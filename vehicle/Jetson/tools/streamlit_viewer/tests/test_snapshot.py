"""새 PC viewer 파일경계 의미시험. Streamlit/제품/카메라를 import하지 않는다."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from snapshot import SnapshotReader,SnapshotError,age,SCHEMA,SNAPSHOT_CAP,MEDIA_CAP


def document(sequence=1):
    return dict(schema=SCHEMA,source=dict(id='declared-test-source',epoch='test-epoch',sequence=str(sequence),kind='DIAGNOSTIC',
        stamp=dict(domain='jetson-j',epoch='declared-j-epoch',us='100')),operation=dict(auto_ready=True,mode='AUTO'),map=dict(layers=[]),media=[])


def packed(value):return json.dumps(value,separators=(',',':')).encode()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.now=10.;self.reader=SnapshotReader(self.root,clock=lambda:self.now,stale_after=2)
        self.original_product_modules={name for name in sys.modules if name.startswith('xenova_vehicle')}

    def write(self,body):
        raw=packed(body);(self.root/'snapshot.json').write_bytes(raw);return raw

    def test_same_file_keeps_original_receive_time_sequence_and_raw_bytes(self):
        raw=self.write(document());first=self.reader.read();self.assertEqual(first.state,'VALID')
        self.now=13.;os.utime(self.root/'snapshot.json',None);later=self.reader.read()
        self.assertEqual(later.state,'STALE');self.assertEqual(later.snapshot.first_received,first.snapshot.first_received)
        self.assertEqual(later.snapshot.raw,raw);self.assertEqual(later.snapshot.body['source']['sequence'],'1')
        self.assertTrue(later.snapshot.body['operation']['auto_ready'])  # Original report retained; no current READY is manufactured.

    def test_reversed_and_same_sequence_different_bytes_are_invalid_and_error_survives_recovery(self):
        original=self.write(document(2));self.reader.read();self.now=11
        self.write(document(1));result=self.reader.read();self.assertEqual(result.state,'INVALID')
        self.assertIn('SEQUENCE_REVERSED',result.reason);self.assertIsNone(result.snapshot)
        changed=document(2);changed['operation']['mode']='MANUAL';self.write(changed)
        self.assertIn('SAME_SEQUENCE_DIFFERENT_BYTES',self.reader.read().reason)
        (self.root/'snapshot.json').write_bytes(original);self.now=15;recovered=self.reader.read()
        self.assertEqual(recovered.state,'STALE');self.assertIsNotNone(recovered.last_error)
        self.write(document(3));self.assertEqual(self.reader.read().state,'VALID')

    def test_partial_duplicate_nonfinite_and_excessive_files_are_not_normal(self):
        for raw in (b'{',b'{"schema":1,"schema":2}',b'{"x":NaN}',b'{"x":1e9999}',b' '*(SNAPSHOT_CAP+1)):
            with self.subTest(raw=raw[:30]):
                (self.root/'snapshot.json').write_bytes(raw);result=self.reader.read()
                self.assertEqual(result.state,'INVALID');self.assertIsNotNone(result.last_error)

    def media(self,path='depth.bin',raw=b'\0\0'*4):
        (self.root/'depth.bin').write_bytes(raw)
        return dict(panel='depth',path=path,sha256=sha256(raw).hexdigest(),byte_length=len(raw),width=2,height=2,
                    encoding='RAW16_LE',stamp=None,source_sequence='1')

    def test_media_hash_is_rechecked_on_same_snapshot_and_old_receipt_is_not_renewed(self):
        body=document();body['media']=[self.media()];self.write(body);first=self.reader.read()
        self.assertEqual(first.state,'VALID');(self.root/'depth.bin').write_bytes(b'\1\0'*4)
        self.assertIn('MEDIA_HASH_OR_LENGTH',self.reader.read().reason)
        (self.root/'depth.bin').write_bytes(b'\0\0'*4);self.now=13;again=self.reader.read()
        self.assertEqual(again.state,'STALE');self.assertEqual(again.snapshot.first_received,10)

    def test_paths_and_symlinks_do_not_escape_explicit_root(self):
        for path in ('../outside.bin','/absolute.bin','C:\\private.bin','frames/../../secret'):
            body=document();body['media']=[self.media(path)];self.write(body)
            self.assertEqual(self.reader.read().state,'INVALID',path)
        outside=self.root.parent/(self.root.name+'-outside');outside.write_bytes(b'\0\0'*4)
        self.addCleanup(lambda:outside.unlink(missing_ok=True))
        try:(self.root/'link.bin').symlink_to(outside)
        except OSError:self.skipTest('OS does not permit test symlink creation')
        body=document();body['media']=[self.media('link.bin')];self.write(body)
        self.assertIn('SYMLINK',self.reader.read().reason)

    def test_media_caps_layout_and_regular_file_are_enforced(self):
        body=document();entry=self.media();entry['byte_length']=MEDIA_CAP+1;body['media']=[entry];self.write(body)
        self.assertEqual(self.reader.read().state,'INVALID')
        body['media']=[self.media()]*5;self.write(body);self.assertEqual(self.reader.read().state,'INVALID')
        body['media']=[self.media()];body['media'][0]['encoding']='NV12';self.write(body)
        self.assertEqual(self.reader.read().state,'INVALID')
        if hasattr(os,'mkfifo'):
            os.mkfifo(self.root/'pipe');body['media']=[self.media('pipe')];self.write(body)
            self.assertEqual(self.reader.read().state,'INVALID')  # Must reject before blocking open/read.

    def test_clock_domains_epochs_and_future_are_distinct_from_viewer_reception(self):
        original=dict(domain='J',epoch='one',us='100')
        self.assertEqual(age(original,dict(domain='PI',epoch='one',us='500'))['state'],'UNMAPPED')
        self.assertEqual(age(original,dict(domain='J',epoch='two',us='500'))['state'],'UNMAPPED')
        self.assertEqual(age(original,dict(domain='J',epoch='one',us='500'))['age_us'],400)
        self.assertEqual(age(original,dict(domain='J',epoch='one',us='99'))['state'],'INVALID_FUTURE')
        self.assertEqual(age(None,None)['state'],'UNMAPPED')

    def test_close_has_no_producer_or_file_side_effect_and_keeps_caller_descriptor(self):
        raw=self.write(document())
        with (self.root/'snapshot.json').open('rb') as caller:
            self.reader.read();self.reader.close();self.assertEqual(caller.read(),raw)
        self.assertEqual((self.root/'snapshot.json').read_bytes(),raw)
        import snapshot
        with patch.object(snapshot,'read_file',side_effect=AssertionError('closed viewer must not read')):
            self.assertEqual(self.reader.read().state,'UNAVAILABLE')
        with self.assertRaises(SnapshotError):self.reader.accept(raw)
        self.assertEqual({name for name in sys.modules if name.startswith('xenova_vehicle')},self.original_product_modules)

    def test_raw_payload_is_preserved_and_missing_source_is_unavailable(self):
        self.assertEqual(self.reader.read().state,'UNAVAILABLE')
        raw=b' {"original_order":1, "other":2}\n';(self.root/'receipt.json').write_bytes(raw)
        body=document();body['raw_payload']=dict(path='receipt.json',byte_length=len(raw),sha256=sha256(raw).hexdigest());self.write(body)
        self.assertEqual(self.reader.read().snapshot.raw_payload,raw)

    def test_metadata_change_and_fd_to_path_replacement_are_still_rejected(self):
        import snapshot
        self.write(document());actual_fstat=os.fstat;actual_regular=snapshot._regular
        fields=('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns','st_birthtime_ns','st_file_attributes')
        def changed(value,field):
            values={name:getattr(value,name,None) for name in fields};values[field]+=1
            return SimpleNamespace(**values)
        for fault in ('fd-mtime','fd-ctime','path-inode','path-mtime'):
            with self.subTest(fault=fault):
                calls=dict(fd=0,path=0)
                def fstat(fd):
                    value=actual_fstat(fd);calls['fd']+=1
                    return changed(value,'st_mtime_ns' if fault=='fd-mtime' else 'st_ctime_ns') if fault.startswith('fd-') and calls['fd']==2 else value
                def regular(path):
                    value=actual_regular(path);calls['path']+=1
                    return changed(value,'st_ino' if fault=='path-inode' else 'st_mtime_ns') if fault.startswith('path-') and calls['path']==3 else value
                with patch.object(os,'fstat',fstat),patch.object(snapshot,'_regular',regular):result=self.reader.read()
                self.assertEqual(result.state,'INVALID');self.assertIn('FILE_CHANGED_DURING_READ',result.reason)


if __name__=='__main__':unittest.main()
