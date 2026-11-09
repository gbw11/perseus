"""PC 관측기만 실행한다. 합성 fixture와 원 OAK 기록 검산을 구분한다."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import representative_adapter as adapter
from representative_adapter import RepresentativeDataset,MANIFEST
from render import preview
from snapshot import SnapshotError,age,validate


def packed(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


class RecordingFixture:
    """제품 NN/renderer를 쓰지 않는 명시 표시 fixture. 네 색은 GT 누출 음성용이다."""
    trial='check/trials/s23-representative-fixture'
    names=('P01-straight-empty','P07-depth-none','P06-vehicle-asset-unsupported')

    def __init__(self,root):
        self.root=root;self.data={};self.cases=[]
        self.source=dict(input_kind='SYNTHETIC',live_authority=False,metric_ready=False,
            device_mxid='DECLARED_FIXTURE_NOT_OAK',model_id='declared-yolop',nn_context_key='a'*64,
            profile=dict(input_kind='SYNTHETIC',camera_nodes=0,source_id='sim:fixture',clock_id='host:fixture',calibration_id='b'*64),
            graph_declaration=dict(input_kind='SYNTHETIC',camera_nodes=0,asset=dict(asset_id='declared-yolop',blob_sha256='c'*64)),
            model_asset=dict(asset_id='declared-yolop',blob_sha256='c'*64))
        png=Image.new('RGB',(1280,720));png.paste((255,0,0),(0,0,640,360));png.paste((0,255,0),(640,0,1280,360))
        png.paste((0,0,255),(0,360,640,720));png.paste((255,0,255),(640,360,1280,720));buffer=BytesIO();png.save(buffer,format='PNG')
        self.png=buffer.getvalue()
        for ordinal,name in enumerate(self.names):
            case=dict(name=name,live_authority=False,status='INCOMPLETE',geometry_expired=True,
                geometry_expires_j_us=500,nn_expires_j_us=1000)
            self.put(ordinal,'profile.json',packed(dict(calibration_id='b'*64)))
            self.put(ordinal,'evaluation-only.json',b'NEVER READ EVALUATION')
            if name.startswith('P06-'):
                case.update(status='NOT_EXECUTED',actual_nn=False,geometry_expires_j_us=None,nn_expires_j_us=None,
                    reason='UNSUPPORTED_SCENE_ASSET_VEHICLE_VISUAL_MODEL')
            else:
                sequence=2 if ordinal==0 else 18
                stamp=dict(source_id='sim:fixture',clock_id='host:fixture',stream_id='rgb_video',sequence=sequence,
                    captured_us=[100,110],available_us=200)
                rgb=bytes([16])*(640*360)+bytes([128])*(640*360//2)
                depth=struct.pack('<H',1000)*(640*480)
                descriptor=dict(bytes=len(rgb),sha256=sha256(rgb).hexdigest(),width=640,height=360,encoding='NV12',stamp=stamp)
                right=dict(bytes=len(depth),sha256=sha256(depth).hexdigest(),width=640,height=480,encoding='RAW16_LE',
                    stamp={**stamp,'stream_id':'depth_right'})
                self.put(ordinal,'rgb.nv12',rgb);self.put(ordinal,'rendered-depth.raw16le',depth)
                self.put(ordinal,'input.json',packed(dict(kind='SYNTHETIC',calibration_id='b'*64,
                    depth_fault='NONE' if ordinal==1 else 'NATIVE',rgb=descriptor,depth=None if ordinal==1 else right)))
                if ordinal==0:self.put(ordinal,'input-depth.raw16le',depth)
                self.put(ordinal,'capture-receipt.json',packed(dict(calibration_id='b'*64,input_kind='SYNTHETIC',
                    source_id='sim:fixture',clock_id='host:fixture',sequence=str(sequence),captured_j_us=['100','110'],
                    completed_j_us='200',expires_j_us='500',live_authority=False,diagnostic_only=True,expired=False,
                    timestamp_basis='original_host_pose_sample_not_OAK_exposure',rgb_sha256=sha256(rgb).hexdigest(),
                    depth_sha256=sha256(depth).hexdigest(),profile_sha256=sha256(self.get(ordinal,'profile.json')).hexdigest())))
                self.put(ordinal,'metrics.json',packed(dict(actual_nn=True,live_authority=False,status=case['status'],geometry_expired=True,
                    geometry_expires_j_us='500',nn_expires_j_us='1000',nn_vehicle_detections=0,
                    completed_j_us='900',nn_input_received_j_us=300,nn_output_received_j_us=310,nn_decode_completed_j_us=400,
                    evaluation_time_basis='actual_monotonic_no_restamp')))
                self.put(ordinal,'core-summary.json',packed(dict(kind='SYNTHETIC',calibration_id='b'*64,live_authority=False,
                    rgb_captured_us=[100,110],road_status='UNKNOWN',map_counts=dict(free=0,occupied=4,unknown=100),
                    neural_layer=dict(counts=dict(road_source_positive_pixels=0,road_positive_cells=0)))))
                self.put(ordinal,'detections.json',packed(dict(nn=[],source=[],counts=dict(source_detections=0))))
                self.put(ordinal,'nn-packet.json',packed(dict(context_key='a'*64,sequence=str(sequence),stream='nn_output',
                    received_j_us='310',device_timestamp_us='205',sdk_timestamp_us='205',payload_length=16)))
                self.put(ordinal,'nn-buffer.bin',b'NEVER READ RAW NN')
                self.put(ordinal,'four-panels.png',self.png)
            self.cases.append(case)
        self.seal()

    def path(self,ordinal,name):return self.trial+'/'+self.names[ordinal]+'/'+name
    def put(self,ordinal,name,raw):self.data[self.path(ordinal,name)]=raw
    def get(self,ordinal,name):return self.data[self.path(ordinal,name)]
    def change(self,ordinal,name,mutate):
        value=json.loads(self.get(ordinal,name));mutate(value);self.put(ordinal,name,packed(value));self.seal()

    def seal(self):
        for ordinal,case in enumerate(self.cases):
            prefix=self.path(ordinal,'')
            case['files']=[dict(path=name[len(prefix):],bytes=len(raw),sha256=sha256(raw).hexdigest())
                for name,raw in self.data.items() if name.startswith(prefix) and not name.endswith('/result.json')]
            self.put(ordinal,'result.json',packed(case))
        self.data[self.trial+'/nn-source-receipt.json']=packed(self.source)
        self.data[self.trial+'/result.json']=packed(dict(profile='sim-representative-diagnostics-1',actual_nn_required=True,
            live_authority=False,closed_loop=False,performance_accepted=False,physical_acceptance=False,
            case_count=len(self.cases),cases=self.cases,status='INCOMPLETE',geometry_expired_cases=2))
        manifest=dict(schema_version=1,files=[dict(path=name,bytes=len(raw),sha256=sha256(raw).hexdigest()) for name,raw in self.data.items()])
        for name,raw in {**self.data,MANIFEST:packed(manifest)}.items():
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)


class RepresentativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.f=RecordingFixture(self.root);self.now=1.
        self.reader=RepresentativeDataset(self.root,clock=lambda:self.now);self.addCleanup(self.reader.close)

    def assert_invalid(self,result,reason):
        self.assertIn(result.state,('INVALID','UNAVAILABLE'));self.assertIsNone(result.snapshot);self.assertIn(reason,result.reason)

    def test_original_selected_media_payload_stamp_and_unknown_current_state(self):
        with patch.object(adapter,'read_file',wraps=adapter.read_file) as reads:result=self.reader.read(0)
        self.assertEqual(result.state,'HISTORICAL');snapshot=result.snapshot;body=snapshot.body
        self.assertEqual(snapshot.raw_payload,self.f.get(0,'result.json'));self.assertEqual(body['source']['sequence'],'2')
        self.assertEqual(body['source']['stamp'],dict(domain='host:fixture',epoch='sim:fixture',us=100))
        self.assertIsNone(body['operation']);self.assertEqual(body['map']['layers'],[])
        self.assertEqual(age(body['source']['stamp'],dict(domain='viewer',epoch='private',us=999))['state'],'UNMAPPED')
        self.assertEqual({x.descriptor['panel'] for x in snapshot.media},{'rgb','depth','yolop','grid'})
        self.assertEqual(body['metrics']['neural_counts']['road_source_positive_pixels'],0)
        self.assertEqual(body['metrics']['frame_local_map_counts']['occupied'],4)
        self.assertFalse(any(any(word in c.args[1] for word in ('evaluation-only','oracle','nn-buffer','rendered-depth')) for c in reads.call_args_list))
        self.assertIn('RAW_NN_BUFFER_NOT_READ_OR_VERIFIED',body['limitations'])
        self.assertLess(len(body['verified_artifacts']),len(self.f.data))

    def test_fixed_left_crops_preserve_png_and_never_show_ground_truth_quadrant(self):
        snapshot=self.reader.read(0).snapshot;outputs={}
        for item in snapshot.media:
            if item.descriptor['panel'] not in ('yolop','grid'):continue
            self.assertEqual(item.raw,self.f.png)
            raw=preview(item)
            with Image.open(BytesIO(raw)) as image:
                self.assertEqual(image.size,(640,360));outputs[item.descriptor['panel']]=image.getpixel((639,359))
        self.assertEqual(outputs,dict(yolop=(255,0,0),grid=(0,0,255)))
        roles={x.descriptor.get('display_crop') for x in snapshot.media if x.descriptor['encoding']=='PNG'}
        self.assertEqual(len(roles),2)

    def test_depth_none_is_absent_input_even_when_rendered_depth_exists(self):
        with patch.object(adapter,'read_file',wraps=adapter.read_file) as reads:result=self.reader.read(1)
        self.assertEqual(result.state,'HISTORICAL')
        self.assertEqual({x.descriptor['panel'] for x in result.snapshot.media},{'rgb','yolop','grid'})
        self.assertFalse(any('depth.raw16le' in c.args[1] for c in reads.call_args_list))

    def test_zero_input_preserves_render_capture_receipt_and_displays_unknown_not_free(self):
        zero=bytes(640*480*2);self.f.put(0,'input-depth.raw16le',zero)
        def fault(value):
            value['depth_fault']='ZERO';value['depth']['sha256']=sha256(zero).hexdigest()
        self.f.change(0,'input.json',fault)
        result=self.reader.read(0);self.assertEqual(result.state,'HISTORICAL',result.reason)
        depth=next(x for x in result.snapshot.media if x.descriptor['panel']=='depth')
        self.assertEqual(depth.raw,zero);self.assertEqual(result.snapshot.body['metrics']['depth_input_mode'],'ZERO')
        with Image.open(BytesIO(preview(depth))) as image:self.assertEqual(image.getpixel((0,0)),(0,0,0))
        self.f.change(0,'input.json',lambda x:x.update(depth_fault='NATIVE'))
        self.assert_invalid(RepresentativeDataset(self.root).read(0),'CAPTURE_DEPTH_HASH')

    def test_p06_unexecuted_has_no_fabricated_stamp_sequence_nn_or_media(self):
        result=self.reader.read(2);self.assertEqual(result.state,'HISTORICAL');body=result.snapshot.body
        self.assertEqual(body['recording']['status'],'NOT_EXECUTED');self.assertIsNone(body['source']['sequence'])
        self.assertIsNone(body['source']['stamp']);self.assertEqual(result.snapshot.media,());self.assertIsNone(body['metrics']['pipeline'])
        self.assertEqual(result.snapshot.raw_payload,self.f.get(2,'result.json'))

    def test_repoll_navigation_touch_recovery_and_close_preserve_first_receipt_and_source(self):
        before={p:sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        first=self.reader.read(0).snapshot.first_received;self.now=9.;self.reader.read(1)
        os.utime(self.root/self.f.path(0,'rgb.nv12'),None)
        result=self.reader.read(0);self.assertEqual(result.snapshot.first_received,first);self.assertEqual(result.received_age_s,8.)
        path=self.root/self.f.path(0,'rgb.nv12');raw=path.read_bytes();path.write_bytes(raw[:-1])
        self.assert_invalid(self.reader.read(0),'HASH_OR_LENGTH');path.write_bytes(raw);self.now=20.
        result=self.reader.read(0);self.assertEqual(result.snapshot.first_received,first);self.assertIsNotNone(result.last_error)
        self.reader.close();self.assertEqual(self.reader.read().state,'UNAVAILABLE')
        self.assertEqual(before,{p:sha256(p.read_bytes()).hexdigest() for p in before})

    def test_manifest_shape_duplicate_path_partial_and_cap_are_rejected(self):
        path=self.root/MANIFEST;original=path.read_bytes();value=json.loads(original)
        variants=(b'{',b'{"schema_version":1,"schema_version":1,"files":[]}',
            packed({**value,'schema_version':True}),packed({**value,'files':value['files']+[value['files'][0]]}),
            packed({**value,'files':[dict(path='../outside',bytes=1,sha256='a'*64)]}),
            packed({**value,'files':[value['files'][0]]*513}),b' '*262145)
        for raw in variants:
            with self.subTest(prefix=raw[:60]):
                path.write_bytes(raw);self.assert_invalid(self.reader.read(0),'')
        path.write_bytes(original);self.assertEqual(self.reader.read(0).state,'HISTORICAL')

    def test_missing_partial_and_changed_media_clear_current_without_losing_error(self):
        self.assertEqual(self.reader.read(0).state,'HISTORICAL')
        target=self.root/self.f.path(0,'four-panels.png');raw=target.read_bytes();target.unlink()
        self.assert_invalid(self.reader.read(0),'four-panels.png');target.write_bytes(raw[:-1])
        self.assert_invalid(self.reader.read(0),'HASH_OR_LENGTH');target.write_bytes(b'x'+raw[1:])
        self.assert_invalid(self.reader.read(0),'HASH_OR_LENGTH');target.write_bytes(raw)
        self.assertIsNotNone(self.reader.read(0).last_error)

    def test_manifest_change_and_case_receipt_mismatch_are_rejected(self):
        self.assertEqual(self.reader.read(0).state,'HISTORICAL')
        path=self.root/MANIFEST;path.write_bytes(path.read_bytes()+b'\n')
        self.assert_invalid(self.reader.read(0),'MANIFEST_CHANGED')
        self.f.seal();self.reader=RepresentativeDataset(self.root)
        self.f.data[self.f.path(0,'result.json')]=packed({**self.f.cases[0],'name':'another'})
        # Rebuild only the manifest; top/case disagreement has valid bytes/SHA.
        target=self.root/self.f.path(0,'result.json');target.write_bytes(self.f.data[self.f.path(0,'result.json')])
        manifest=json.loads((self.root/MANIFEST).read_bytes())
        for row in manifest['files']:
            if row['path']==self.f.path(0,'result.json'):row.update(bytes=target.stat().st_size,sha256=sha256(target.read_bytes()).hexdigest())
        (self.root/MANIFEST).write_bytes(packed(manifest));self.assert_invalid(self.reader.read(0),'CASE_RECEIPT')

    def test_source_model_context_and_calibration_mismatches_are_rejected(self):
        baseline=deepcopy(self.f.source)
        for mutate,reason in ((lambda x:x['profile'].update(camera_nodes=1),'SOURCE_SCOPE'),
                (lambda x:x['profile'].update(source_id='foreign'),'CAPTURE_SOURCE'),
                (lambda x:x['profile'].update(calibration_id='d'*64),'CAPTURE_SOURCE'),
                (lambda x:x.update(nn_context_key='d'*64),'NN_SOURCE'),
                (lambda x:x['graph_declaration']['asset'].update(blob_sha256='d'*64),'MODEL_SOURCE')):
            with self.subTest(reason=reason):
                self.f.source=deepcopy(baseline);mutate(self.f.source);self.f.seal()
                self.assert_invalid(RepresentativeDataset(self.root).read(0),reason)

    def test_original_time_expiry_sequence_and_depth_pair_are_not_restamped(self):
        baseline=dict(self.f.data)
        for name,mutate,reason in (
                ('capture-receipt.json',lambda x:x.update(completed_j_us='201'),'STAMP_MISMATCH'),
                ('capture-receipt.json',lambda x:x.update(expires_j_us='501'),'TIME_OR_EXPIRY'),
                ('capture-receipt.json',lambda x:x.update(profile_sha256='d'*64),'PROFILE_HASH'),
                ('nn-packet.json',lambda x:x.update(sequence='3'),'NN_SOURCE'),
                ('metrics.json',lambda x:x.update(geometry_expired=False),'METRIC_SOURCE'),
                ('metrics.json',lambda x:x.update(nn_output_received_j_us=901),'TIME_OR_EXPIRY'),
                ('input.json',lambda x:x['depth']['stamp'].update(available_us=201),'DEPTH_STAMP')):
            with self.subTest(file=name,reason=reason):
                self.f.data=dict(baseline);self.f.change(0,name,mutate)
                self.assert_invalid(RepresentativeDataset(self.root).read(0),reason)

    def test_same_source_sequence_with_other_case_bytes_is_rejected(self):
        self.f.change(1,'input.json',lambda x:x['rgb']['stamp'].update(sequence=2))
        self.f.change(1,'capture-receipt.json',lambda x:x.update(sequence='2'))
        self.f.change(1,'nn-packet.json',lambda x:x.update(sequence='2'))
        self.assertEqual(self.reader.read(0).state,'HISTORICAL')
        self.assert_invalid(self.reader.read(1),'SEQUENCE_CONFLICT')

    def test_missing_wrong_or_arbitrary_crop_metadata_is_rejected_before_display(self):
        body=self.reader.read(0).snapshot.body
        for role in (None,'GT_BOTTOM_RIGHT','REPRESENTATIVE_GRID_BOTTOM_LEFT',[0,0,640,360]):
            with self.subTest(role=role):
                changed=deepcopy(body);item=next(x for x in changed['media'] if x['panel']=='yolop')
                item['display_crop']=role
                with self.assertRaises(SnapshotError):validate(changed)
        changed=deepcopy(body);next(x for x in changed['media'] if x['panel']=='yolop')['width']=640
        with self.assertRaises(SnapshotError):validate(changed)

    def test_original_archive_all_twelve_selected_recordings_without_oracle_or_nn_buffer(self):
        recording=os.environ.get('OAK_REPRESENTATIVE_RECORDING_ROOT')
        if not recording:self.skipTest('explicit original archive root not provided')
        reader=RepresentativeDataset(recording);self.addCleanup(reader.close);executed=expired=0;receipts=[]
        with patch.object(adapter,'read_file',wraps=adapter.read_file) as reads:
            for ordinal in range(12):
                result=reader.read(ordinal);self.assertEqual(result.state,'HISTORICAL',result.reason)
                body=result.snapshot.body;self.assertEqual(body['recording']['ordinal'],ordinal);self.assertIsNone(body['operation'])
                self.assertEqual(body['map']['layers'],[])
                receipts.append(dict(recording=body['recording'],source=body['source'],metrics=body['metrics'],
                    identities=body['identities'],verified_artifacts=body['verified_artifacts'],limitations=body['limitations'],
                    media=[item.descriptor for item in result.snapshot.media]))
                if body['recording']['status']=='NOT_EXECUTED':
                    self.assertTrue(body['recording']['case_name'].startswith('P06-'));self.assertIsNone(body['source']['sequence']);continue
                executed+=1;expired+=body['metrics']['pipeline']['geometry_expired']
                self.assertEqual(body['metrics']['neural_counts']['road_source_positive_pixels'],0)
                self.assertEqual(body['metrics']['pipeline']['nn_vehicle_detections'],0)
                self.assertEqual(body['metrics']['frame_local_map_counts']['free'],0)
                panels={x.descriptor['panel']:x for x in result.snapshot.media}
                if body['recording']['case_name']=='P07-depth-none':self.assertNotIn('depth',panels)
                for name in ('yolop','grid'):
                    with Image.open(BytesIO(preview(panels[name]))) as image:self.assertEqual(image.size,(640,360))
        self.assertEqual((executed,expired),(11,11))
        self.assertFalse(any(any(word in c.args[1] for word in ('oracle','evaluation-only','nn-buffer','rendered-depth')) for c in reads.call_args_list))
        output=os.environ.get('OAK_REPRESENTATIVE_VERIFIED_RECEIPT')
        if output:Path(output).write_bytes(packed(dict(scope='SELECTED_ARTIFACTS_ONLY',executed=executed,geometry_expired=expired,cases=receipts)))


if __name__=='__main__':unittest.main()
