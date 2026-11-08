"""표시 export의 stdlib 의미시험. 제품/renderer/SDK/inference/Streamlit 서버는 열지 않는다."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import sim_observation_export as exporting
from sim_observation_export import SimObservationExporter,ObservationExportError


SOURCE='sim-wall:00000000-0000-4000-8000-000000000001'
CLOCK='jetson-monotonic:00000000-0000-4000-8000-000000000002'
ODOM='00000000-0000-4000-8000-000000000003'
CALIBRATION='a'*64


def original(sequence=1,*,capture=None):
    """Exact read source shape의 명시 최소 software leaf. actual Rig 실행 증거는 아니다."""
    lower,upper=(100*sequence,100*sequence+10) if capture is None else capture
    def image(stream,encoding,payload):
        stamp=SimpleNamespace(source_id=SOURCE,stream_id=stream,sequence=sequence,clock_id=CLOCK,
            captured_us=(lower,upper),available_us=upper+10)
        return SimpleNamespace(stamp=stamp,width=2,height=2,encoding=encoding,payload=payload,exposure_us=0)
    rgb=image('rgb_video','NV12',b'\x10'*4+b'\x80\x80')
    depth=image('depth_right','RAW16_LE',b'\0\0\xe8\3\xe8\3\xe8\3')
    observation=SimpleNamespace(kind='SYNTHETIC',calibration_id=CALIBRATION,rgb=rgb,depth=depth,nn=None,nn_key=None)
    fields=dict(profile='sim-mapper-motion-observation-1',scope='ACTUAL_FD_MQTT_RETURNED_TRUST_SYNTHETIC_ODOMETRY_DIAGNOSTIC',
        capture=dict(input_kind='SYNTHETIC',source_id=SOURCE,clock_id=CLOCK,sequence=str(sequence),
            captured_j_us=list(map(str,(lower,upper))),completed_j_us=str(upper+10),expires_j_us=str(lower+1000),
            rgb_sha256=sha256(rgb.payload).hexdigest(),depth_sha256=sha256(depth.payload).hexdigest(),
            calibration_id=CALIBRATION,profile_sha256='b'*64,timestamp_basis='original_host_pose_sample_not_OAK_exposure',live_authority=False),
        pose=dict(sample=dict(epoch=ODOM,clock_id=CLOCK,calibration_id=CALIBRATION,at_us=(lower+upper)//2,
            x=.1*sequence,y=.2,yaw=.03,position_error_m=.02,yaw_error_rad=.01,origin='SYNTHETIC_ODOMETRY'),
            captured_us=[lower,upper],bracket_us=[lower-10,upper+20]),
        checked_j_us=str(upper+20),fd=dict(pi_session='original-pi',device_session='original-device',odom_epoch=ODOM,
            clock_epoch='different-pi-clock-domain',clock_sequence='7',clock_valid_until_pi_us='1'),
        nn_executed=False,free_positive=False,authority=False,physical_acceptance=False,closed_loop=False,
        context_sha256='c'*64,reasons=[])
    return fields,observation


def raw(fields):return (json.dumps(fields,ensure_ascii=True,indent=1)+'\n').encode('ascii')


class ObservationExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'viewer'

    def writer(self):
        value=SimObservationExporter(self.root);self.addCleanup(value.close);return value

    def body(self):return json.loads((self.root/'snapshot.json').read_bytes())

    def test_original_fields_media_stamp_and_unknown_panels_match_actual_viewer_contract(self):
        modules=set(sys.modules);fields,observation=original();original_bytes=raw(fields)
        result=self.writer().export(original_bytes,observation);body=self.body()
        self.assertEqual(result.status,'WRITTEN');self.assertEqual(body['source']['sequence'],'1')
        self.assertEqual(body['source']['kind'],'OFFLINE_SYNTHETIC')
        self.assertEqual(body['source']['stamp'],dict(domain=CLOCK,epoch=SOURCE,us='100'))
        self.assertIsNone(body['operation']);self.assertIsNone(body['metrics'])
        self.assertEqual([x['panel'] for x in body['media']],['rgb','depth'])
        self.assertEqual([x['kind'] for x in body['map']['layers']],['ESTIMATE'])
        self.assertEqual(body['map']['layers'][0]['frame'],'ODOMETRY:'+ODOM)
        self.assertEqual(body['map']['layers'][0]['points'],[[.1,.2]])
        for item,frame in zip(body['media'],(observation.rgb,observation.depth)):
            self.assertEqual((self.root/item['path']).read_bytes(),frame.payload)
            self.assertEqual(item['original_stamp']['captured_us'],[100,110])
            self.assertEqual(item['original_stamp']['available_us'],120)
        self.assertEqual((self.root/body['raw_payload']['path']).read_bytes(),original_bytes)
        viewer=Path(__file__).resolve().parents[2]/'tools/streamlit_viewer'
        sys.path.insert(0,str(viewer))
        from snapshot import SnapshotReader,age
        reader=SnapshotReader(self.root);self.addCleanup(reader.close)
        view=reader.read();self.assertEqual(view.state,'VALID')
        self.assertEqual(view.snapshot.raw_payload,original_bytes)
        self.assertEqual(age(body['source']['stamp'],dict(domain='viewer-monotonic-us',epoch='PC',us='999'))['state'],'UNMAPPED')
        self.assertFalse(any(name.startswith(('xenova_vehicle','cv2','numpy','depthai','paho')) for name in set(sys.modules)-modules))

    def test_same_original_does_not_touch_files_or_renew_sequence_and_survives_writer_restart(self):
        fields,observation=original();wire=raw(fields);writer=self.writer();writer.export(wire,observation)
        before={p.relative_to(self.root).as_posix():(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file() and p.name!='.export-owner.lock'}
        self.assertEqual(writer.export(wire,observation).status,'UNCHANGED')
        after={p.relative_to(self.root).as_posix():(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file() and p.name!='.export-owner.lock'}
        self.assertEqual(before,after);writer.close()
        replacement=self.writer();self.assertEqual(replacement.export(wire,observation).status,'UNCHANGED')
        self.assertEqual((self.root/'snapshot.json').read_bytes(),before['snapshot.json'][0])

    def test_same_sequence_changed_bytes_reverse_and_overlapping_capture_are_rejected(self):
        writer=self.writer();fields,observation=original(2);writer.export(raw(fields),observation);before=(self.root/'snapshot.json').read_bytes()
        changed=deepcopy(fields);changed['reasons']=['same-seq-different-original-json']
        with self.assertRaisesRegex(ObservationExportError,'SAME_SEQUENCE_DIFFERENT_BYTES'):writer.export(raw(changed),observation)
        reinterpreted=deepcopy(observation);reinterpreted.depth.width=4;reinterpreted.depth.height=1
        with self.assertRaisesRegex(ObservationExportError,'SAME_SEQUENCE_DIFFERENT_BYTES'):writer.export(raw(fields),reinterpreted)
        older,old_image=original(1)
        with self.assertRaisesRegex(ObservationExportError,'SEQUENCE_REVERSED'):writer.export(raw(older),old_image)
        overlap,image=original(3,capture=(210,220))
        with self.assertRaisesRegex(ObservationExportError,'CAPTURE_REVERSED_OR_OVERLAPPED'):writer.export(raw(overlap),image)
        self.assertEqual((self.root/'snapshot.json').read_bytes(),before)

    def test_receipt_pair_hash_calibration_and_oracle_pose_cannot_be_relabelled(self):
        for fault in ('hash','pair','calibration','oracle','authority','clock'):
            with self.subTest(fault=fault):
                fields,image=original()
                if fault=='hash':fields['capture']['rgb_sha256']='d'*64
                elif fault=='pair':image.depth.stamp.sequence=2
                elif fault=='calibration':image.calibration_id='e'*64
                elif fault=='oracle':fields['pose']['sample']['origin']='ORACLE_SIMULATION'
                elif fault=='authority':fields['authority']=True
                else:fields['pose']['sample']['clock_id']='pi-monotonic:other'
                with SimObservationExporter(self.root) as writer:
                    with self.assertRaises(ObservationExportError):writer.export(raw(fields),image)
                self.assertFalse((self.root/'snapshot.json').exists())

    def test_json_media_caps_partial_json_duplicate_and_nonfinite_are_rejected_before_publication(self):
        writer=self.writer();fields,image=original()
        for wire in (b'{',b'{"profile":1,"profile":2}',b'{"value":NaN}',b'{"value":1e9999}',b' '*(exporting.JSON_CAP+1)):
            with self.subTest(wire=wire[:40]):
                with self.assertRaises(ObservationExportError):writer.export(wire,image)
        for fault in ('media-length','media-cap','pixel-cap','encoding'):
            candidate=deepcopy(image)
            if fault=='media-length':candidate.rgb.payload=b'partial'
            elif fault=='media-cap':candidate.depth.payload=b'\0'*(exporting.MEDIA_CAP+1)
            elif fault=='pixel-cap':candidate.depth.width=candidate.depth.height=4096
            else:candidate.rgb.encoding='BGR8'
            with self.assertRaises(ObservationExportError):writer.export(raw(fields),candidate)
        self.assertFalse((self.root/'snapshot.json').exists())

    def test_newest_only_slots_are_bounded_and_manifest_is_replaced_last(self):
        writer=self.writer();replacements=[];actual=os.replace
        def replace(source,target):replacements.append(Path(target).name);return actual(source,target)
        for sequence in range(1,8):
            fields,image=original(sequence)
            with patch.object(exporting.os,'replace',replace):writer.export(raw(fields),image)
            self.assertEqual(replacements[-4:],['rgb.nv12','depth.raw16','observation.json','snapshot.json'])
            self.assertLessEqual(sum(p.is_file() for p in self.root.rglob('*')),8)
            self.assertFalse(list(self.root.rglob('*.tmp')))
        self.assertEqual(self.body()['source']['sequence'],'7')

    def test_partial_write_keeps_previous_manifest_and_active_media_without_advancing_hwm(self):
        writer=self.writer();first,image=original();writer.export(raw(first),image);before=(self.root/'snapshot.json').read_bytes();body=self.body()
        original_media=[(self.root/x['path']).read_bytes() for x in body['media']]
        second,image=original(2);actual=os.write
        def partial(fd,payload):return actual(fd,payload[:max(1,len(payload)//2)])
        with patch.object(exporting.os,'write',partial):
            with self.assertRaisesRegex(ObservationExportError,'PARTIAL_FILE_WRITE'):writer.export(raw(second),image)
        self.assertEqual((self.root/'snapshot.json').read_bytes(),before)
        self.assertEqual([(self.root/x['path']).read_bytes() for x in body['media']],original_media)
        self.assertFalse(list(self.root.rglob('*.tmp')))
        self.assertEqual(writer.export(raw(second),image).status,'WRITTEN')

    def test_manifest_replace_failure_is_not_a_success_or_original_time_reissue(self):
        writer=self.writer();first,image=original();writer.export(raw(first),image);before=(self.root/'snapshot.json').read_bytes()
        second,image=original(2);wire=raw(second);actual=os.replace
        def fail(source,target):
            if Path(target).name=='snapshot.json':raise OSError('declared manifest replacement failure')
            return actual(source,target)
        with patch.object(exporting.os,'replace',fail):
            with self.assertRaises(OSError):writer.export(wire,image)
        self.assertEqual((self.root/'snapshot.json').read_bytes(),before)
        writer.export(wire,image);body=self.body()
        self.assertEqual(body['source']['stamp']['us'],'200')
        self.assertEqual((self.root/body['raw_payload']['path']).read_bytes(),wire)

    def test_gt_is_explicit_display_only_and_absence_never_reads_an_oracle(self):
        fields,image=original()
        class NoOracle(SimpleNamespace):
            @property
            def oracle(self):raise AssertionError('export must not access oracle')
        image=NoOracle(**vars(image));writer=self.writer();writer.export(raw(fields),image)
        self.assertFalse(any(x['kind']=='GT' for x in self.body()['map']['layers']))
        writer.close();self.root=Path(self.temp.name)/'explicit-gt'
        layer=dict(kind='GT',frame='ODOMETRY:'+ODOM,points=[[0.,0.],[.2,0.]],closed=False)
        self.writer().export(raw(fields),image,display_gt_layers=[layer])
        self.assertEqual(self.body()['map']['layers'][-1],layer)
        self.assertIsNone(self.body()['operation'])

    def test_dedicated_single_writer_source_epoch_and_existing_file_integrity_are_preserved(self):
        writer=self.writer();fields,image=original();writer.export(raw(fields),image)
        with self.assertRaises((ValueError,OSError)):SimObservationExporter(self.root)
        different=deepcopy(fields);different['capture']['source_id']=SOURCE+'-new'
        other=deepcopy(image)
        for frame in (other.rgb,other.depth):frame.stamp.source_id=SOURCE+'-new'
        with self.assertRaisesRegex(ObservationExportError,'SOURCE_CHANGED'):writer.export(raw(different),other)
        path=self.root/self.body()['media'][0]['path'];path.write_bytes(b'broken')
        with self.assertRaisesRegex(ObservationExportError,'PARTIAL_EXPORT_MEDIA'):writer.export(raw(fields),image)
        self.assertEqual(path.read_bytes(),b'broken')  # Never repairs/relabels a changed source silently.

    def test_foreign_files_and_symlink_slots_are_never_overwritten(self):
        self.root.mkdir();foreign=self.root/'user-data';foreign.write_bytes(b'keep')
        with self.assertRaises(ObservationExportError):SimObservationExporter(self.root)
        self.assertEqual(foreign.read_bytes(),b'keep')
        self.root=Path(self.temp.name)/'symlink-root';self.root.mkdir();outside=Path(self.temp.name)/'outside';outside.mkdir()
        try:(self.root/'slot-0').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('OS does not permit declared symlink fixture')
        with self.assertRaisesRegex(ObservationExportError,'SYMLINK_OR_REPARSE'):SimObservationExporter(self.root)
        self.assertEqual(list(outside.iterdir()),[])

    def test_empty_pose_is_unknown_and_close_has_no_product_or_media_side_effect(self):
        fields,image=original();fields['pose']=None;writer=self.writer();writer.export(raw(fields),image);before=self.body()
        self.assertEqual(before['map']['layers'],[]);self.assertIsNone(before['identities']['pose_frame'])
        raw_snapshot=(self.root/'snapshot.json').read_bytes();writer.close()
        self.assertEqual((self.root/'snapshot.json').read_bytes(),raw_snapshot)
        self.assertFalse((self.root/'.export-owner.lock').exists())
        with self.assertRaisesRegex(ObservationExportError,'SINGLE_DIAGNOSTIC_OWNER'):writer.export(raw(fields),image)


if __name__=='__main__':unittest.main()
