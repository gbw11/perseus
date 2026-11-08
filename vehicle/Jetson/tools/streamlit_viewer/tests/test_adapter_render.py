"""실제 기록 형식에 맞춘 declared offline fixtures/표시 codec 시험."""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapter import OfflineDataset
from snapshot import Media,SnapshotError
from render import preview,map_svg,encoded_dimensions


def line(value):return json.dumps(value,separators=(',',':')).encode()+b'\n'


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'frames').mkdir();self.epoch='11111111-1111-4111-8111-111111111111'
        self.header=dict(version=1,scope='synthetic-rgb-right-first-surface',calibration_id='b'*64,effects_sha256='a'*64)
        (self.root/'manifest.json').write_bytes(line(self.header))
        self.row=dict(ordinal=0,calibration_id='b'*64)
        for name,width,height,encoding,payload in (
            ('rgb',640,360,'NV12',bytes([16])*(640*360)+bytes([128])*(640*360//2)),
            ('right',640,480,'RAW16_LE',struct.pack('<H',1000)*(640*480))):
            path=f'frames/000000-{name}.bin';(self.root/path).write_bytes(payload)
            stamp=dict(source_id='sim:'+self.epoch+':'+'a'*16,stream_id='rgb_video' if name=='rgb' else 'depth_right',
                clock_id='simulation:'+self.epoch,sequence=1,captured_us=[100,100],available_us=110)
            self.row[name]=dict(stamp=stamp,width=width,height=height,encoding=encoding,exposure_us=0,path=path,
                payload_length=len(payload),sha256=sha256(payload).hexdigest())
        self.save([self.row]);self.reader=OfflineDataset(self.root)

    def save(self,rows,complete=True):
        raw=b''.join(line(x) for x in rows);(self.root/'snapshots.jsonl').write_bytes(raw)
        (self.root/'result.json').write_bytes(line(dict(version=1,complete=complete,count=len(rows),index_sha256=sha256(raw).hexdigest())))

    def test_actual_writer_shape_retains_original_raw_pair_and_no_oracle_access(self):
        (self.root/'oracle.jsonl').write_text('must never be parsed')
        import adapter
        with patch.object(adapter,'read_file',wraps=adapter.read_file) as reads:result=self.reader.read()
        self.assertEqual(result.state,'HISTORICAL');self.assertEqual(result.snapshot.raw_payload,line(self.row))
        self.assertEqual({x.descriptor['panel'] for x in result.snapshot.media},{'rgb','depth'})
        self.assertIsNone(result.snapshot.body['operation']);self.assertEqual(result.snapshot.body['map']['layers'],[])
        self.assertFalse(any(call.args[1]=='oracle.jsonl' for call in reads.call_args_list))
        self.assertEqual(result.snapshot.media[0].descriptor['original_stamp'],self.row['rgb']['stamp'])

    def test_incomplete_index_receipt_and_payload_corruption_are_invalid(self):
        self.save([self.row],complete=False);self.assertEqual(self.reader.read().state,'INVALID')
        self.save([self.row]);(self.root/'snapshots.jsonl').write_bytes(b'{')
        self.assertEqual(self.reader.read().state,'INVALID')
        self.save([self.row]);(self.root/self.row['right']['path']).write_bytes(b'wrong')
        self.assertEqual(self.reader.read().state,'INVALID')

    def test_unpaired_original_stamp_or_reversed_index_is_not_retimestamped(self):
        self.row['right']['stamp']['sequence']=2;self.save([self.row])
        self.assertEqual(self.reader.read().state,'INVALID')
        self.row['right']['stamp']['sequence']=1
        second=json.loads(json.dumps(self.row));second['ordinal']=1
        for name in ('rgb','right'):second[name]['path']=f'frames/000001-{name}.bin'
        self.save([self.row,second]);self.assertIn('SEQUENCE_REVERSED',self.reader.read().reason)


class RenderTests(unittest.TestCase):
    def test_nv12_and_depth_zero_are_bounded_display_png_not_new_sensor_data(self):
        nv=Media(dict(width=2,height=2,encoding='NV12'),bytes([16])*4+bytes([128])*2)
        png=preview(nv);self.assertEqual(encoded_dimensions(png,'PNG'),(2,2))
        # Inspect this display encoder's IDAT only; original NV12 stays unchanged.
        length=int.from_bytes(png[33:37],'big');rows=zlib.decompress(png[41:41+length])
        self.assertEqual(rows,bytes(14));self.assertEqual(nv.raw,bytes([16])*4+bytes([128])*2)
        depth=Media(dict(width=2,height=2,encoding='RAW16_LE',depth_unit='mm'),bytes(8))
        png=preview(depth);length=int.from_bytes(png[33:37],'big')
        self.assertEqual(zlib.decompress(png[41:41+length]),bytes(14))

    def test_gt_is_explicit_and_unmapped_frames_never_overlay(self):
        body=dict(map=dict(layers=[dict(kind='GT',frame='<frame>',points=[[0,0],[1,1]]),
                                    dict(kind='PATH',frame='<frame>',points=[[0,1],[1,0]])]))
        self.assertNotIn('stroke-dasharray',map_svg(body));self.assertIn('stroke-dasharray',map_svg(body,show_gt=True))
        self.assertIn('&lt;frame&gt;',map_svg(body));self.assertNotIn('<frame>',map_svg(body))
        body['map']['layers'][0]['frame']='foreign'
        with self.assertRaises(SnapshotError):map_svg(body,show_gt=True)

    def test_encoded_media_dimensions_must_match_declared_bounds(self):
        item=Media(dict(width=2,height=2,encoding='NV12'),bytes([16])*4+bytes([128])*2)
        png=preview(item)
        with self.assertRaises(SnapshotError):preview(Media(dict(width=1,height=2,encoding='PNG'),png))


if __name__=='__main__':unittest.main()
