"""원 admission→불변 frame snapshot의 closure·membership·fence 계약 회귀다.

기존 software wire/clock fixture를 실제 소비자에 넣는다. 새 Pi clock 승인, 실제
카메라·물리 FREE 또는 thread scheduling의 수락 증거를 만들지 않는다.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError,fields,is_dataclass,replace
from hashlib import sha256
import unittest
from unittest.mock import patch
from uuid import UUID

import test_mapper_sensor_state as state_fixtures
from xenova_drive_world import decode,encode,schema
from xenova_vehicle.mapping.odometry import PoseBuffer
from xenova_vehicle.runtime import mapper_sensor_state as module
from xenova_vehicle.runtime.mapper_sensor_state import SensorFrameSnapshot,SensorSnapshotUnavailable
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.transport.sensor_samples import FrozenSensorSamples,SensorSamples


class SensorAdmissionOwnerTests(unittest.TestCase):
    def setUp(self):
        self.f=state_fixtures.MapperSensorStateTests('test_full_capture_uses_original_vehicle_anchor_interval_and_callback_metadata')
        self.f.setUp()
        self.job_id=str(UUID(int=803))
        self.key=('rgb_video',12,401000,999000)

    def snapshot(self,*,now=None):
        f=self.f
        return f.state.frame_snapshot(f.stamp,now_us=f.now if now is None else now,
            expires_us=f.now+100000,job_id=self.job_id,source_generation=f.raw.sha256,source_key=self.key)

    def ingest(self,value=None,*,now=None):
        f=self.f
        return f.state.ingest(canonical(f.snapshot() if value is None else value),now_us=f.now if now is None else now)

    def test_snapshot_owns_an_immutable_closure_and_independent_source_properties(self):
        f=self.f;admitted=self.ingest();snapshot=self.snapshot();inputs=snapshot.metric_inputs
        self.assertIsNone(admitted.pose);self.assertIsNotNone(inputs.pose)
        self.assertIs(type(snapshot.sensors),FrozenSensorSamples)
        self.assertIs(snapshot.calibration,f.calibration)
        self.assertIs(snapshot.sensors.context,f.state.sensors.context)
        self.assertIs(snapshot.metric_inputs.vehicle,admitted.vehicle)
        self.assertEqual(snapshot.source_context_bytes,canonical(f.snapshot()))
        self.assertEqual(inputs.pose.captured_us,f.stamp.captured_us)
        self.assertIs(deepcopy(snapshot),snapshot);self.assertIs(deepcopy(snapshot.sensors),snapshot.sensors)
        source=inputs.source;source['vehicle']='foreign'
        self.assertEqual(snapshot.metric_inputs.source,f.fixture.source)
        pose_args=snapshot.pose_args;pose_args['capacity']=2
        self.assertEqual(snapshot.pose_args['capacity'],16)
        provenance=snapshot.calibration.provenance;provenance['measured']=True
        self.assertFalse(snapshot.calibration.provenance['measured'])
        context_source=snapshot.sensors.context.source;context_source['vehicle']='foreign'
        self.assertEqual(snapshot.sensors.context.source,f.fixture.source)
        seen=set()
        def inspect(value):
            if id(value) in seen:return
            seen.add(id(value))
            self.assertNotIsInstance(value,(dict,list,set,SensorSamples,PoseBuffer,module.MapperSensorState))
            if isinstance(value,tuple):
                for child in value:inspect(child)
            elif is_dataclass(value):
                self.assertTrue(value.__dataclass_params__.frozen)
                for field in fields(value):inspect(getattr(value,field.name))
        inspect(snapshot)
        with self.assertRaises(FrozenInstanceError):snapshot.fence_revision=90
        with self.assertRaises(FrozenInstanceError):snapshot.sensors.latest_vehicle=None

    def test_factory_only_and_original_record_identity_cannot_be_replaced_by_equality(self):
        self.ingest();snapshot=self.snapshot();inputs=snapshot.metric_inputs
        with self.assertRaises(TypeError):SensorFrameSnapshot()
        with self.assertRaises(TypeError):FrozenSensorSamples()
        bracket=snapshot.original_pose_bracket(inputs.pose)
        for row in bracket.originals:
            self.assertIs(snapshot.original_vehicle_admission(row.evidence),row)
            self.assertIs(snapshot.validate_vehicle_admission(row),row.evidence)
        row=bracket.originals[0]
        with self.assertRaises(ValueError):snapshot.validate_vehicle_admission(replace(row))
        with self.assertRaises(ValueError):snapshot.original_vehicle_admission(replace(row.evidence))
        with self.assertRaises(ValueError):snapshot.original_pose_bracket(replace(inputs.pose))
        self.assertTrue(snapshot.sensors.current_vehicle(inputs.vehicle,self.f.now))
        self.assertFalse(snapshot.sensors.current_vehicle(replace(inputs.vehicle),self.f.now))
        self.assertFalse(snapshot.sensors.current_tof(replace(inputs.tof[0]),self.f.now))

    def test_frame_metadata_and_semantic_context_match_without_rejecting_delivery_progress(self):
        f=self.f;self.ingest();snapshot=self.snapshot()
        args=dict(job_id=self.job_id,source_generation=f.raw.sha256,source_key=self.key,
            expires_us=f.now+100000,capture_stamp=f.stamp,now_us=f.now,fence_revision=0)
        self.assertIs(snapshot.validate_frame(**args,context_bytes=canonical(f.snapshot())),snapshot)
        progress=f.snapshot();progress['vehicles'][0]['received_j_us']='1020000'
        progress['active_probe_sha256']=None;progress['probes']={};progress['tof']={}
        self.assertIs(snapshot.validate_frame(**args,context_bytes=canonical(progress)),snapshot)
        for name,value in (('job_id',str(UUID(int=804))),('source_generation','foreign'),
                ('source_key',('rgb_video',True,401000,999000)),('expires_us',f.now+100001),
                ('capture_stamp',replace(f.stamp,captured_us=(999900,1000100))),
                ('now_us',f.now+100000),('fence_revision',1)):
            changed=dict(args);changed[name]=value
            with self.subTest(name=name),self.assertRaises(ValueError):snapshot.validate_frame(**changed)
        publication=f.snapshot();publication['publication']['source_binding_revision']='2'
        f.update_context(publication,source_binding=publication['publication'])
        with self.assertRaises(ValueError):snapshot.validate_frame(**args,context_bytes=canonical(publication))

    def test_returned_clock_bytes_hashes_and_original_expiry_are_preserved(self):
        f=self.f;self.ingest();snapshot=self.snapshot();inputs=snapshot.metric_inputs
        self.assertIsNone(snapshot.p4_evidence)
        proof=next(value for value in snapshot.clock_proofs if value[1]==f.probe_raw)
        self.assertEqual(proof,(sha256(f.probe_raw).hexdigest(),f.probe_raw,
            sha256(inputs.clock_lease.trust_bytes).hexdigest(),inputs.clock_lease.trust_bytes))
        for row in snapshot.vehicle_admissions:
            self.assertEqual(row.admitted_j_us,f.now)
            self.assertLessEqual(row.received_j_us,row.admitted_j_us)
            self.assertEqual(row.evidence.clock_lease.pi_interval_to_j(row.evidence.pi_interval,row.admitted_j_us),
                row.evidence.j_interval)
        self.assertEqual(snapshot.expires_us,f.now+100000)
        with self.assertRaises(SensorSnapshotUnavailable):self.snapshot(now=f.now+100000)
        history=f.state.vehicle_admissions
        expired=self.ingest(now=1250000)
        self.assertIsNotNone(expired.clock_lease);self.assertIsNone(expired.vehicle)
        self.assertEqual(f.state.fence_revision,1)
        self.ingest(now=1250001);self.assertEqual(f.state.fence_revision,1)
        lost=self.ingest(now=1900000)
        self.assertIsNone(lost.clock_lease);self.assertEqual(f.state.fence_revision,2)
        self.ingest(now=1900001);self.assertEqual(f.state.fence_revision,2)
        self.assertEqual(f.state.vehicle_admissions,history)

    def test_clockless_tof_reset_fences_once_without_erasing_pose_history(self):
        f=self.f;self.ingest();snapshot=self.snapshot();buffer=f.state.poses
        reset=f.fixture.tof(source_sample=1,sensor_generation=2,sensor_instance=UUID(int=805).bytes)
        value=f.snapshot();value['tof']={'0':f.row(encode(reset))}
        probe=decode(f.probe_raw,schema.MapperClockProbe);probe.ClearField('mapper_trust')
        raw=encode(probe);key=sha256(raw).hexdigest()
        value['probes']={key:f.row(raw)};value['active_probe_sha256']=key
        changed=self.ingest(value,now=f.now+1)
        # ToF reset과 active clock 상실은 서로 다른 두 철회이며 반복 None은 새 사건이 아니다.
        self.assertEqual(f.state.fence_revision,2);self.assertEqual(len(changed.sensor_resets),1)
        self.assertIs(f.state.poses,buffer)
        self.assertEqual(len(f.state.vehicle_admissions),len(snapshot.vehicle_admissions))
        self.assertIsNone(f.state.sensors.latest_tof(0))
        self.assertIsNotNone(f.state.sensors.latest_tof(1))
        again=self.ingest(value,now=f.now+2)
        self.assertEqual(again.sensor_resets,());self.assertEqual(f.state.fence_revision,2)
        with self.assertRaises(ValueError):snapshot.validate_frame(job_id=self.job_id,
            source_generation=f.raw.sha256,source_key=self.key,expires_us=snapshot.expires_us,fence_revision=1)
        self.assertEqual(snapshot.fence_revision,0)

    def test_exact_logical_byte_cap_rejects_whole_snapshot_without_proof_omission(self):
        f=self.f;self.ingest();snapshot=self.snapshot()
        self.assertEqual(module.MAX_SENSOR_SNAPSHOT_BYTES,512*1024)
        self.assertLessEqual(snapshot.byte_length,module.MAX_SENSOR_SNAPSHOT_BYTES)
        records=f.state.vehicle_admissions
        with patch.object(module,'MAX_SENSOR_SNAPSHOT_BYTES',snapshot.byte_length-1):
            with self.assertRaises(SensorSnapshotUnavailable):self.snapshot()
        self.assertEqual(f.state.vehicle_admissions,records)
        with patch.object(module,'MAX_SENSOR_SNAPSHOT_BYTES',snapshot.byte_length):
            exact=self.snapshot()
        self.assertEqual(exact.byte_length,snapshot.byte_length)
        self.assertTrue(all(a is b for a,b in zip(exact.vehicle_admissions,records)))


if __name__=='__main__':unittest.main()
