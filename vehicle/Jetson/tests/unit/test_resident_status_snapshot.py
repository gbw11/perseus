"""S가 검산한 원 lifecycle/RAW 진술에서 진단 snapshot을 만드는 경계.

actual codec/receiver를 사용하되 worker 기동·장치·모델·물리 send 성공은 시험하지
않는다. 설치 metadata와 RAW ACK는 명시 소프트웨어 fixture이며 metric 권한은 없다.
"""

from collections import deque
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import raw_binding_fixtures as raw_fixture
import lifecycle_message_fixtures as lifecycle_fixture
from xenova_drive_world.mapper_clock import ClockPolicy
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer,LifecycleReceiver
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.raw_binding_messages import RawBindAck,RawBindContext
from xenova_vehicle.runtime.resident_runtime import _Runtime,_Worker
from xenova_vehicle.runtime.runtime_status import RuntimeStatus
from xenova_vehicle.runtime.source_identity import SourcePolicy
from xenova_vehicle.transport.jio_profile import JetsonJioProfile


NOW=2_000_000


class Recovery:
    def __init__(self):self.active=None
    def suppressed(self,role,host):
        return self.active is not None and self.active.old.get(role) is host


class ResidentStatusSnapshotTests(unittest.TestCase):
    def runtime(self):
        runtime=object.__new__(_Runtime)
        clock=ClockPolicy('rc-status','oak-status','test-config','a'*64,'b'*64,
            2,10,5000,1_000_000,500_000,1_000_000)
        policy=SourcePolicy(clock.vehicle,clock.configuration,clock.calibration,clock.primitive,'c'*64)
        runtime.jio_profile=JetsonJioProfile(policy,clock,clock.device,'127.0.0.1',1883)
        runtime.service=raw_fixture.identifier(1)
        runtime.clock_epoch=raw_fixture.identifier(204)
        runtime.manifest=SimpleNamespace(runtime=SimpleNamespace(boot_id=raw_fixture.identifier(205)))
        runtime.artifacts=dict(release_sha256='f'*64)
        runtime.binding=raw_fixture.bind_message(raw_fixture.context('A')).binding
        runtime.raw_profile=raw_fixture.profile()
        runtime.phase='WORKERS'
        runtime.workers={};runtime.recovery=Recovery()
        runtime.capture_ready_us=runtime.nn_ready_us=runtime.storage_ready_us=None
        runtime.last_capture_us=runtime.last_fresh_parser_us=None
        runtime.parser_deadline=20_000_000
        runtime.progress={};runtime.events=deque(maxlen=64)
        runtime.resident_started_us=1_900_000
        runtime.stopping=False;runtime.failure=None
        runtime.native_closed=runtime.candidate_written=False
        runtime.log=SimpleNamespace(is_alive=True,terminal_error=None)
        runtime.a_phase=None;runtime.a_phase_deadlines={}
        runtime.latest_runtime_status=None;runtime.runtime_status_error=None;runtime._next_runtime_status_us=0
        self.sequences={}
        for role in ('A','M','D'):
            self.worker(runtime,role)
        self.progress(runtime,'A','ASSET_VALID',3_000,state='DEVICE_BOOT')
        self.progress(runtime,'A','GRAPH_STARTED',4_000,state='WARMUP')
        self.progress(runtime,'A','CAPTURE_READY',1_700_000,state='STREAMING',sequence=30)
        self.progress(runtime,'A','CAPTURE_PAIR',1_900_000,state='STREAMING',sequence=31)
        self.progress(runtime,'M','NN_READY',1_800_000,state='WAIT_CLOCK',sequence=5)
        self.progress(runtime,'M','PARSER_COMPLETED',1_950_000,state='RUNNING',sequence=6)
        for role,state in (('A','STREAMING'),('M','RUNNING'),('D','IDLE')):
            self.heartbeat(runtime,role,state,1_990_000,progress=('88','1950000'))
        return runtime

    def worker(self,runtime,role,*,replacement=False):
        if role in ('A','M'):
            ctx=raw_fixture.context(role)
            if replacement:
                peer=replace(ctx.peer,worker_instance=raw_fixture.identifier(500),channel_epoch=raw_fixture.identifier(501))
                ctx=RawBindContext(peer,ctx.acquisition,ctx.profile)
            peer=ctx.peer
        else:
            peer=LifecyclePeer(runtime.service,raw_fixture.identifier(9),raw_fixture.identifier(10),'D','f'*64)
        host=_Worker(peer,object(),object(),LifecycleReceiver(peer,'S'),100)
        host.admitted=True
        runtime.workers[role]=host
        self.sequences[role]=1
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=500):
            runtime._dispatch(role,lifecycle_fixture.packet('HELLO',current=peer,sequence=1,issued=500,expires=10_000_000))
        if role in ('A','M'):
            host.bind=raw_fixture.bind_message(ctx,binding=runtime.binding,issued=1_000,expires=5_001_000)
            host.bind_ack=RawBindAck(raw_fixture.ack_packet(host.bind,issued=2_000),host.bind)
        return host

    def heartbeat(self,runtime,role,state,issued,*,expires=None,progress=(None,None)):
        host=runtime.workers[role]
        self.sequences[role]+=1
        packet=lifecycle_fixture.packet('HEARTBEAT',current=host.peer,sequence=self.sequences[role],
            issued=issued,expires=issued+2_000_000 if expires is None else expires,
            payload=dict(worker_state=state,last_progress_sequence=progress[0],last_progress_j_us=progress[1]))
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=issued+1):
            runtime._dispatch(role,packet)
        return packet

    def progress(self,runtime,role,stage,completed,*,state,sequence=None,reason=None):
        host=runtime.workers[role]
        self.sequences[role]+=1
        fields=lifecycle_fixture.message_fields('PROGRESS',current=host.peer,
            sequence=self.sequences[role],issued=completed,expires=completed+1_000_000,
            payload=dict(stage=stage,source_sequence=None if sequence is None else str(sequence),
                completed_j_us=str(completed),state=state,reason=reason))
        if role=='M':
            # 실제 parser-completion 원 start가 별도로 수락되어 있었다는 소프트웨어 경계다.
            host.parse_started_us=completed-100;host.parse_sequence=sequence
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=completed):
            runtime._dispatch(role,encode_structure(fields))

    def test_actual_admitted_heartbeat_original_progress_and_session_capture_are_projected(self):
        runtime=self.runtime()
        result=runtime.runtime_status(NOW)
        self.assertIs(type(result),RuntimeStatus)
        fields=result.fields
        self.assertEqual(fields['runtime_state'],'RUNNING')
        self.assertTrue(fields['capture_ready'])
        self.assertFalse(fields['metric_ready'])
        self.assertEqual(fields['reason'],'METRIC_UNASSESSED')
        self.assertEqual([row['role'] for row in fields['workers']],['A','M','D'])
        self.assertEqual(fields['workers'][0]['last_progress_sequence'],'88')
        self.assertEqual(fields['workers'][0]['last_progress_j_us'],'1950000')
        self.assertEqual(len(runtime.workers['A'].phase_observation),4)
        self.assertEqual(runtime.workers['A'].heartbeat_progress,('88','1950000'))
        self.assertEqual(fields['model']['residency_evidence'],'PAIRED_OUTPUT')
        self.assertEqual(fields['model']['parser_state'],'VALID')
        self.assertEqual(fields['model']['inference_state'],'UNAVAILABLE')
        self.assertIsNone(fields['model']['last_input_j_us'])
        self.assertIsNone(fields['model']['last_result_j_us'])
        self.assertIsNone(fields['mapper_instance'])
        self.assertIsNone(fields['source_binding_revision'])
        host=runtime.workers['A'];original=host.stage_observations
        for stage in ('CAPTURE_READY','CAPTURE_PAIR'):
            host.stage_observations=tuple(row for row in original if row.stage!=stage)
            self.assertFalse(runtime.runtime_status(NOW).fields['capture_ready'])
        for stage in ('ASSET_VALID','GRAPH_STARTED'):
            host.stage_observations=tuple(row for row in original if row.stage!=stage)
            self.assertEqual(runtime.runtime_status(NOW).fields['model']['residency_evidence'],'NONE')
        host.stage_observations=original

    def test_capture_is_independent_of_mapper_clock_publication_tof_and_mode_metadata(self):
        runtime=self.runtime()
        for absent in ('M','D'):
            runtime.workers.pop(absent)
        # 현재 scope에는 JIO owner/ClockLease/Vehicle/ToF/publication/운영 모드도 없다.
        for mode in (None,'WAIT','MANUAL','AUTO'):
            runtime.test_only_vehicle_mode=mode
            fields=runtime.runtime_status(NOW).fields
            self.assertTrue(fields['capture_ready'])
            self.assertFalse(fields['metric_ready'])
            self.assertEqual(fields['model']['residency_evidence'],'GRAPH_STARTED')
            self.assertEqual(fields['model']['parser_state'],'UNAVAILABLE')

    def test_replaced_worker_and_global_old_progress_cannot_mint_new_capture_or_graph_evidence(self):
        runtime=self.runtime()
        original=runtime.workers['A']
        self.assertTrue(runtime.runtime_status(NOW).fields['capture_ready'])
        # 같은 등록 peer로 만들어도 새 worker 객체에는 과거 stage 진술이 없다.
        fresh=self.worker(runtime,'A')
        self.heartbeat(runtime,'A','STREAMING',1_990_000)
        self.assertTrue(runtime.progress)
        fields=runtime.runtime_status(NOW).fields
        self.assertFalse(fields['capture_ready'])
        self.assertEqual(fields['model']['residency_evidence'],'NONE')
        self.assertEqual(fresh.stage_observations,())
        self.assertTrue(original.stage_observations)
        runtime.workers['A']=original
        for flag,value in (('eof',True),('reaped',True),('exited_us',NOW),('admitted',False),('hello',False)):
            previous=getattr(original,flag);setattr(original,flag,value)
            self.assertFalse(runtime.runtime_status(NOW).fields['capture_ready'])
            setattr(original,flag,previous)
        original.bind_ack=runtime.workers['M'].bind_ack
        self.assertFalse(runtime.runtime_status(NOW).fields['capture_ready'])
        original.bind_ack=RawBindAck(raw_fixture.ack_packet(original.bind,issued=2_000),original.bind)
        self.progress(runtime,'A','SESSION_CLOSED',1_995_000,state='CLOSED')
        self.assertFalse(runtime.runtime_status(NOW).fields['capture_ready'])
        self.assertEqual(runtime.runtime_status(NOW).fields['model']['residency_evidence'],'NONE')
        self.assertEqual(runtime.runtime_status(NOW).fields['runtime_state'],'DEGRADED')

    def test_new_snapshot_time_does_not_renew_capture_pair_or_heartbeat_original_expiry(self):
        runtime=self.runtime()
        original=runtime.workers['A'].stage_observations
        self.assertTrue(runtime.runtime_status(2_399_999).fields['capture_ready'])
        fields=runtime.runtime_status(2_400_000).fields
        self.assertFalse(fields['capture_ready'])
        self.assertEqual(fields['snapshot_j_us'],'2400000')
        self.assertIs(runtime.workers['A'].stage_observations,original)
        self.progress(runtime,'A','CAPTURE_PAIR',2_900_000,state='STREAMING',sequence=32)
        self.assertTrue(runtime.runtime_status(2_989_999).fields['capture_ready'])
        self.assertFalse(runtime.runtime_status(2_990_000).fields['capture_ready'])
        self.heartbeat(runtime,'A','STREAMING',3_000_000,expires=3_000_010)
        self.progress(runtime,'A','CAPTURE_PAIR',3_000_002,state='STREAMING',sequence=33)
        self.assertTrue(runtime.runtime_status(3_000_009).fields['capture_ready'])
        self.assertFalse(runtime.runtime_status(3_000_010).fields['capture_ready'])

    def test_m_only_recovery_and_new_m_do_not_destroy_healthy_a_or_reuse_old_parser_proof(self):
        runtime=self.runtime()
        old=runtime.workers['M']
        runtime.recovery.active=SimpleNamespace(old={'M':old})
        fields=runtime.runtime_status(NOW).fields
        self.assertTrue(fields['capture_ready'])
        self.assertEqual(fields['runtime_state'],'RECOVERING')
        self.assertNotEqual(fields['model']['parser_state'],'VALID')
        fresh=self.worker(runtime,'M',replacement=True)
        self.heartbeat(runtime,'M','RUNNING',1_990_000)
        fields=runtime.runtime_status(NOW).fields
        self.assertTrue(fields['capture_ready'])
        self.assertEqual(fields['model']['parser_state'],'WARMUP')
        self.assertEqual(fields['model']['residency_evidence'],'GRAPH_STARTED')
        self.assertIsNone(fresh.parser_fresh_observation)
        self.assertIsNotNone(runtime.nn_ready_us)
        self.assertIsNotNone(runtime.last_fresh_parser_us)

    def test_parser_five_second_original_completion_age_is_not_the_active_job_budget(self):
        runtime=self.runtime()
        original=runtime.workers['M'].parser_fresh_observation
        self.heartbeat(runtime,'M','WAIT_INPUT',2_600_000)
        self.assertEqual(runtime.runtime_status(2_600_100).fields['model']['parser_state'],'VALID')
        self.progress(runtime,'M','PARSER_COMPLETED',2_700_000,state='WAIT_INPUT',sequence=7,reason='STALE_INPUT')
        self.assertIs(runtime.workers['M'].parser_fresh_observation,original)
        self.heartbeat(runtime,'M','WAIT_INPUT',6_949_900)
        self.assertEqual(runtime.runtime_status(6_949_999).fields['model']['parser_state'],'VALID')
        self.assertEqual(runtime.runtime_status(6_950_000).fields['model']['parser_state'],'STALE')
        self.assertEqual(original.completed_us,1_950_000)
        self.assertEqual(runtime.runtime_status(6_950_000).fields['model']['last_result_j_us'],None)

    def test_s_lifecycle_projection_and_stop_recovery_fences_do_not_relabel_internal_phase(self):
        runtime=self.runtime()
        for phase in ('PREPARE','ALLOCATING','HANDLES','WORKERS'):
            runtime.phase=phase;runtime.resident_started_us=None
            self.assertEqual(runtime.runtime_status(NOW).fields['runtime_state'],'STARTING')
        runtime.resident_started_us=1_900_000
        runtime.workers['D'].eof=True
        self.assertEqual(runtime.runtime_status(NOW).fields['runtime_state'],'DEGRADED')
        runtime.stopping=True
        fields=runtime.runtime_status(NOW).fields
        self.assertEqual(fields['runtime_state'],'QUIESCING')
        self.assertFalse(fields['capture_ready'])
        self.assertEqual(fields['model']['parser_state'],'UNAVAILABLE')
        runtime.failure='actual owner failed'
        self.assertEqual(runtime.runtime_status(NOW).fields['runtime_state'],'FAILED')
        runtime.failure=None;runtime.log.terminal_error='ResourceLogError'
        self.assertEqual(runtime.runtime_status(NOW).fields['runtime_state'],'FAILED')

    def test_latest_one_hz_snapshot_keeps_original_bytes_and_status_failure_has_no_io_or_ack_effect(self):
        runtime=self.runtime()
        with patch('builtins.open',side_effect=AssertionError('snapshot must not do I/O')):
            runtime._refresh_runtime_status(NOW)
            original=runtime.latest_runtime_status
            runtime._refresh_runtime_status(NOW+999_999)
            self.assertIs(runtime.latest_runtime_status,original)
            runtime._refresh_runtime_status(NOW+1_000_000)
        self.assertEqual(original.fields['snapshot_j_us'],str(NOW))
        self.assertFalse(runtime.latest_runtime_status.fields['capture_ready'])
        self.assertEqual(runtime.latest_runtime_status.fields['snapshot_j_us'],str(NOW+1_000_000))
        with patch.object(runtime,'runtime_status',side_effect=ValueError('bad diagnostic only')):
            runtime._refresh_runtime_status(NOW+1_000_001,force=True)
        self.assertIsNone(runtime.latest_runtime_status)
        self.assertEqual(runtime.runtime_status_error,'ValueError')
        self.assertIsNone(runtime.failure)
        runtime.jio_profile=None
        self.assertIsNone(runtime.runtime_status(NOW))
        runtime._refresh_runtime_status(NOW+2_000_000,force=True)
        self.assertIsNone(runtime.latest_runtime_status)
        self.assertIsNone(runtime.runtime_status_error)
