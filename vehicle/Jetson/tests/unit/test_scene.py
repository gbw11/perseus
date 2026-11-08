"""실제 임시 SQLite commit/LOAD와 합성 sensor·정합 값의 scene lifecycle.

값 fixture는 실물 접근 통제/first-surface/정지 수락이 아니다. 실제 worker 재시작이나
MQTT 적용을 실행하지 않으며 original receipt·source·시각·negative fence를 검사한다.
"""

from dataclasses import replace
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from uuid import UUID

from xenova_drive_world import schema, encode, decode
from xenova_drive_world.mapper_clock import ClockLease
from xenova_vehicle.runtime.scene import MapperScene, SurveyProfile, SurveyObservation, SceneError, SceneEvidenceScope
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.resident_storage import _Database, StorageProfile
from xenova_vehicle.mapping.odometry import PoseSample, PoseEstimate
from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.registration import RegistrationResult, PoseBox
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.calibration import synthetic_calibration, CalibrationProfile
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin
from xenova_vehicle.storage.contracts import StorageContext, StorageJob, canonical
from xenova_vehicle.storage.records import (ContextRecord, ProvenanceRecord, EvidenceTable, TileVersion,
    CELL, PROVENANCE)
from xenova_vehicle.storage.repository import PersistenceStore
from xenova_vehicle.storage.contracts import LoadRequest


def uid(number): return '00000000-0000-4000-8000-'+f'{number:012x}'


def source():
    return dict(vehicle='rc-test-1',device='oak-1',mapper_boot=uid(10),mapper_instance=uid(11),clock_epoch=uid(12),
        map_epoch=uid(5),frame_epoch=uid(6),odom_epoch=uid(3),configuration='scene-lab',calibration='a'*64,primitive='b'*64)


def clock():
    src=source(); probe=schema.MapperClockProbe(major=1,vehicle=src['vehicle'],device=src['device'],
        pi_host_boot=uid(1),pi_client_instance=uid(13),probe_seq=1,nonce=UUID(uid(14)).bytes,
        pi_send_us=1_000_000,admission_revision=1)
    trust=schema.WorldTrust(**{key:value for key,value in src.items() if key!='device'},
        lower_offset_us=0,upper_offset_us=0,expires_pi_us=2_000_000,max_error_us=100)
    return ClockLease(encode(trust),1,1,1_010_000,1_000_000,1_000_000,0,2_000_000,100,encode(probe),1_000_000,1_000_000)


def grant():
    src=source()
    value=schema.SceneGrant(major=1,vehicle=src['vehicle'],pi_host_boot=uid(1),gateway_instance=uid(4),
        source_admission_revision=1,mapper_device=src['device'],issued_pi_us=1_000_000,
        valid_until_pi_us=1_000_000+14_400_000_000,lifecycle_state=0,request_nonce=UUID(uid(30)).bytes,
        survey_deadline_pi_us=181_000_000,max_observation_stations=24,purpose=0,
        frozen_survey_revision=0,frozen_hazard_revision=0,lifecycle_revision=1,
        **{name:src[name] for name in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')})
    value.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(uid(29)).bytes,admission_revision=1,
        survey_id=UUID(uid(31)).bytes,survey_revision=1,policy=1,coverage_profile_sha256=bytes.fromhex('d'*64),
        allow_restore_after_mapper_restart=True))
    return value


def observation(sequence=1,at=1_010_000,hazard=1,*,calibration_id):
    pose=PoseSample(uid(3),uid(12),calibration_id,at+1,.1,.2,0.,.001,.001,'SYNTHETIC_ODOMETRY')
    stamp=FrameStamp(uid(61),uid(64),sequence,uid(12),(at,at+2),at+10)
    estimate=PoseEstimate(pose,stamp.captured_us,(at-10,at+10))
    raw=replace(estimate,sample=replace(pose,x=0.,y=0.,position_error_m=.000001,yaw_error_rad=.000001))
    result=RegistrationResult('BOUNDED',(.1,.2,0.),.001,.001,
        (PoseBox((.099,.101),(.199,.201),(-.001,.001)),),sequence-1,1,1,True)
    alignment=MapOdomAlignment.from_match(raw,estimate,observed_j_interval=raw.captured_us,
        registration_revision=sequence,valid_for_us=500_000,position_drift_mps=.001,yaw_drift_rps=.001)
    return SurveyObservation(stamp,estimate,result,sequence,hazard,'SYNTHETIC',raw,alignment)


@unittest.skipUnless(os.name=='posix','원 D SQLite writer는 POSIX에 연결한다')
class MapperSceneTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        db=_Database(StorageProfile(str(Path(tmp.name)/'scene.sqlite3'),'CREATE_IF_ABSENT'),time.monotonic_ns()//1000+5_000_000)
        self.addCleanup(db.close); self.now=1_010_030
        fields=synthetic_calibration().to_dict(); fields['vehicle_bundle_sha256']='a'*64
        self.calibration=CalibrationProfile.from_dict(fields)
        self.raw=RawBinding(dict(service_instance=uid(60),acquisition_worker=uid(61),pipeline_instance=uid(62),
            capture_epoch=uid(63),stream_instances=dict(rgb_video=uid(64),depth_right=uid(65)),sdk_bridge_epoch=uid(66),
            device_id='oak-1',model_id=None,parser_id=None,graph_profile_sha256='e'*64,
            calibration_bundle=self.calibration.sha256,format_table_sha256='f'*64,clock_epoch=uid(12)))
        publication=dict(service_instance=uid(60),mapper_worker=uid(20),mapper_instance=uid(11),raw_binding_sha256=self.raw.sha256,
            map_epoch=uid(5),frame_epoch=uid(6),odom_epoch=uid(3),configuration='scene-lab',calibration='a'*64,
            primitive='b'*64,scene_admission_ref=None,source_binding_revision='1')
        origin=PiGeometryOrigin('rc-test-1',uid(1),uid(4),uid(2),uid(7),uid(3),'a'*64)
        self.context=StorageContext(db.metadata['database_uuid'],uid(20),uid(11),uid(5),uid(6),uid(3),uid(1),
            'scene-lab','a'*64,'b'*64,canonical(publication),geometry_origin=canonical(origin.fields))
        self.scope=SceneEvidenceScope(self.context,self.raw,self.calibration,'e'*64)
        self.store=PersistenceStore(db.connection,self.context.database_uuid,expires_us=6_000_000,clock_us=lambda:self.now)
        self.profile=SurveyProfile('d'*64,1,4096,5000,.1,.01,.01,20000,.05,.05,500000,'SYNTHETIC')
        self.mapper=MapperScene(self.context,source(),self.profile,gateway_instance=uid(4),evidence_scope=self.scope)
        self.clock=clock(); self.grant=grant(); self.mapper.receive_grant(encode(self.grant),self.clock,now_j_us=1_000_001)
        self.obs=observation(calibration_id=self.calibration.sha256); self.mapper.observe(self.obs,now_j_us=self.now)
        self.station(self.mapper,self.obs)
        range_job=StorageJob(self.context,uid(21),self.now,self.now+5_000_000)
        allocated=self.store.reserve_provenance(range_job,uid(22),block_count=8,current_context_sha256=self.context.sha256)
        self.identity=allocated.start_inclusive

    def station(self, mapper, obs):
        last=obs.pose.sample; first=replace(last,at_us=last.at_us-10000)
        self.assertTrue(mapper.station((first,last),now_j_us=obs.stamp.available_us+20))

    def copies(self, *, lower=1010000, upper=1010002):
        context=self.scope.context_record('RAW')
        raw=struct.pack('<16sQQ',UUID(self.raw.fields['stream_instances']['depth_right']).bytes,1,1010000)+bytes(96)
        record=ProvenanceRecord(self.identity,PROVENANCE.pack(bytes.fromhex(context.sha256),2,2,1,0,
            UUID(uid(1)).bytes,lower,upper,1,0,0,0,0,raw,0,bytes(16)))
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        cell=CELL.pack(0,1,2,0.,0.,.001,self.identity,lower,upper,1,1,0)
        tile=TileVersion(uid(5),0,0,1,cell+unknown*4095)
        return (tile,),EvidenceTable((record,),(context,))

    def freeze(self, *, lower=1010000, mapper_state=None, state_pages=()):
        value=schema.SceneGrant(); value.CopyFrom(self.grant)
        value.lifecycle_state=1; value.lifecycle_revision=2; value.request_nonce=UUID(uid(32)).bytes
        value.survey_deadline_pi_us=1_500_000
        self.mapper.receive_grant(encode(value),self.clock,now_j_us=self.now)
        job=StorageJob(self.context,uid(23),self.now,1_500_000,uid(32))
        tiles,evidence=self.copies(lower=lower)
        if state_pages:
            evidence=replace(evidence,contexts=(*evidence.contexts,*state_pages))
        return value,self.mapper.prepare_freeze(job,tiles,evidence,
            current_tile_references=tuple(sorted(tile.reference for tile in tiles)),current_hazard_revision=1,now_j_us=self.now,
            mapper_state=mapper_state)

    def test_actual_freeze_commit_load_retains_exact_referenced_mapper_state(self):
        from xenova_vehicle.storage.mapper_state import encode_mapper_state,decode_mapper_state
        from xenova_vehicle.storage.contracts import json_bytes
        state=dict(profile='explicit-survey-metadata-fixture',original=[1,2,3])
        reference,pages=encode_mapper_state(state)
        _,request=self.freeze(mapper_state=reference,state_pages=pages)
        self.store.commit(request.job,request.payload,current_context_sha256=self.context.sha256)
        selected=self.store.latest_complete(expires_us=self.now+1_000_000)
        load=LoadRequest(StorageJob(self.context,uid(50),self.now,self.now+5_000_000),
            canonical(selected),canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=self.store.load(load,current_context_sha256=self.context.sha256)
        original_scene=json_bytes(request.payload.manifest,16384)['scene']
        actual_scene=json_bytes(loaded.payload.manifest,16384)['scene']
        self.assertEqual(actual_scene['mapper_state'],reference)
        self.assertEqual(actual_scene,original_scene)
        self.assertEqual(decode_mapper_state(actual_scene,loaded.payload.evidence.contexts),(state,pages))
        self.assertFalse(self.mapper.eligible)

    def committed(self):
        value,request=self.freeze(); self.now+=20
        receipt=self.store.commit(request.job,request.payload,current_context_sha256=self.context.sha256)
        self.assertTrue(self.mapper.complete_freeze(request,receipt,now_j_us=self.now+10))
        return value,request,receipt

    def test_actual_d_commit_precedes_frozen_and_exact_grant_precedes_static_eligibility(self):
        value,request,receipt=self.committed()
        frozen=decode(self.mapper.summary_bytes(now_j_us=self.now+10),schema.SurveySummary)
        self.assertEqual((frozen.status,frozen.positive_cell_count,frozen.observation_station_count),(1,1,1))
        self.assertFalse(self.mapper.eligible); self.assertEqual(frozen.evidence_sha256.hex(),request.payload.sha256)
        value.lifecycle_state=2; value.lifecycle_revision=3; value.frozen_evidence_sha256=frozen.evidence_sha256
        value.frozen_survey_revision=frozen.survey_revision; value.frozen_hazard_revision=frozen.hazard_revision
        self.assertTrue(self.mapper.receive_grant(encode(value),self.clock,now_j_us=self.now+20))
        self.assertTrue(self.mapper.eligible)
        self.assertEqual(decode(self.mapper.summary_bytes(now_j_us=self.now+20),schema.SurveySummary).status,2)
        self.assertEqual(receipt.job_digest,request.digest)

    def test_hash_only_or_old_provenance_does_not_make_frozen(self):
        with self.assertRaises(SceneError): self.freeze(lower=999999)
        self.assertFalse(self.mapper.eligible)
        self.assertIsNone(self.mapper.summary_bytes(now_j_us=self.now))

    def test_wrong_receipt_and_new_hazard_cannot_complete_original_freeze(self):
        _,request=self.freeze(); self.now+=10
        receipt=self.store.commit(request.job,request.payload,current_context_sha256=self.context.sha256)
        self.assertFalse(self.mapper.complete_freeze(request,replace(receipt,job_id=uid(99)),now_j_us=self.now))
        self.mapper.hazard(2)
        self.assertFalse(self.mapper.complete_freeze(request,receipt,now_j_us=self.now))
        self.assertFalse(self.mapper.eligible); self.assertEqual(self.mapper.blocked_reason,'HAZARD')

    def test_missing_station_window_and_ambiguous_registration_never_count_frames_as_stations(self):
        last=self.obs.pose.sample
        with self.assertRaises(SceneError): self.mapper.station((replace(last,at_us=last.at_us-1),last),now_j_us=self.now)
        bad=replace(observation(2,1010100,calibration_id=self.calibration.sha256),
            registration=replace(self.obs.registration,status='AMBIGUOUS_OR_UNOBSERVABLE'))
        with self.assertRaises(SceneError): self.mapper.observe(bad,now_j_us=1010120)
        self.assertFalse(self.mapper.eligible)

    def test_actual_load_and_original_receipt_restore_without_renewing_cell_times(self):
        frozen,request,receipt=self.committed(); reference=self.store.latest_complete(expires_us=self.now+1_000_000)
        load_job=StorageJob(self.context,uid(50),self.now,self.now+5_000_000)
        load=LoadRequest(load_job,canonical(reference),canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=self.store.load(load,current_context_sha256=self.context.sha256)
        value=schema.SceneGrant(); value.CopyFrom(frozen)
        value.lifecycle_revision=3; value.purpose=1; value.request_nonce=UUID(uid(51)).bytes
        value.frozen_evidence_sha256=bytes.fromhex(request.payload.sha256); value.frozen_survey_revision=1
        value.frozen_hazard_revision=1; value.restore_checkpoint_sha256=bytes.fromhex(loaded.payload.sha256)
        mapper=MapperScene(self.context,source(),self.profile,gateway_instance=uid(4),evidence_scope=self.scope)
        mapper.receive_grant(encode(value),self.clock,now_j_us=self.now+20)
        current=observation(2,1020200,calibration_id=self.calibration.sha256)
        mapper.observe(current,now_j_us=1020220); self.station(mapper,current)
        raw=mapper.restore_ready(loaded,receipt,current,now_j_us=1020240)
        restored=decode(raw,schema.SurveySummary)
        self.assertEqual((restored.status,restored.evidence_flags),(4,63)); self.assertFalse(mapper.eligible)
        self.assertEqual(loaded.payload.tiles[0].payload,request.payload.tiles[0].payload)
        self.assertEqual(CELL.unpack_from(loaded.payload.tiles[0].payload)[7:9],(1010000,1010002))

    def test_normal_clock_renewal_preserves_scene_and_foreign_pi_clock_fences(self):
        before=self.mapper.grant_bytes; refreshed=replace(self.clock,time_lease_revision=2,completed_pi_us=1020000)
        self.mapper.renew_clock(refreshed,now_j_us=self.now)
        self.assertEqual(self.mapper.grant_bytes,before)
        self.assertEqual(self.mapper.source,source())
        changed=self.mapper.source; changed['calibration']='f'*64
        self.assertEqual(self.mapper.source,source())
        probe=decode(self.clock.raw_probe_bytes,schema.MapperClockProbe); probe.pi_host_boot=uid(999)
        foreign=replace(self.clock,raw_probe_bytes=encode(probe))
        with self.assertRaises(SceneError): self.mapper.renew_clock(foreign,now_j_us=self.now)
        self.assertFalse(self.mapper.eligible); self.assertEqual(self.mapper.blocked_reason,'SOURCE')

    def test_original_expiry_and_restore_permission_are_not_renewed_by_grant_retry(self):
        _,request,receipt=self.committed()
        original=self.mapper.grant_bytes
        self.mapper.receive_grant(original,self.clock,now_j_us=self.now+1)
        self.assertEqual(self.mapper.grant_bytes,original)
        with self.assertRaises(SceneError): self.mapper.summary_bytes(now_j_us=2_000_000)
        self.assertFalse(self.mapper.eligible)

    def test_live_only_mapper_has_no_scene_reference_or_static_permission(self):
        mapper=MapperScene(self.context,source(),self.profile,gateway_instance=uid(4))
        self.assertIsNone(mapper.grant_bytes); self.assertFalse(mapper.eligible)
        with self.assertRaises(SceneError):
            SceneEvidenceScope(replace(self.context,geometry_origin=None),self.raw,self.calibration,'e'*64)

    def test_same_boot_and_survey_time_with_foreign_raw_origin_or_leaf_cannot_freeze(self):
        # 모든 손상 context를 새 SHA로 다시 만들어 형식/해시가 맞아도 의미가 달라짐을 검사한다.
        from xenova_vehicle.storage.contracts import json_bytes
        tiles,evidence=self.copies(); original=evidence.contexts[0]
        value=schema.SceneGrant(); value.CopyFrom(self.grant); value.lifecycle_state=1
        value.lifecycle_revision=2; value.request_nonce=UUID(uid(32)).bytes; value.survey_deadline_pi_us=1500000
        self.mapper.receive_grant(encode(value),self.clock,now_j_us=self.now)
        for variant in ('RAW','ORIGIN','LEAF'):
            fields=json_bytes(original.payload,4096)
            if variant=='RAW': fields['body']['raw_binding']['pipeline_instance']=uid(999)
            elif variant=='ORIGIN': fields['body']['storage_context']['geometry_origin']['gateway_instance']=uid(999)
            else:
                fields['kind']='TOF'; fields['body']['tof']=dict(sensor_id=0,leaf_sha256='9'*64)
            context=ContextRecord(canonical(fields)); data=list(PROVENANCE.unpack(evidence.provenance[0].payload))
            data[0]=bytes.fromhex(context.sha256)
            changed=ProvenanceRecord(self.identity,PROVENANCE.pack(*data))
            table=EvidenceTable((changed,),(context,))
            job=StorageJob(self.context,uid(90),self.now,1_500_000,uid(32))
            with self.subTest(variant=variant),self.assertRaises(SceneError):
                self.mapper.prepare_freeze(job,tiles,table,current_tile_references=tuple(t.reference for t in tiles),
                    current_hazard_revision=1,now_j_us=self.now)
        self.assertFalse(self.mapper.eligible)

    def test_scene_only_context_change_uses_geometry_scope_not_full_context_sha(self):
        from xenova_vehicle.storage.contracts import json_bytes
        publication=json_bytes(self.context.source_binding,2048)
        publication['source_binding_revision']='2'; publication['scene_admission_ref']=dict(policy=1)
        other=replace(self.context,source_binding=canonical(publication))
        self.assertNotEqual(other.sha256,self.context.sha256)
        changed_scope=SceneEvidenceScope(other,self.raw,self.calibration,'e'*64)
        context=changed_scope.context_record('RAW')
        original=self.scope.context_record('RAW')
        self.assertNotEqual(original.sha256,context.sha256)
        _,evidence=self.copies(); data=list(PROVENANCE.unpack(evidence.provenance[0].payload))
        data[0]=bytes.fromhex(context.sha256)
        record=ProvenanceRecord(self.identity,PROVENANCE.pack(*data))
        self.assertEqual(self.scope.record_sources(record,context),(2,1))
