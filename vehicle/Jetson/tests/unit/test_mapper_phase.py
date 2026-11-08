"""M의 현재 결과 상태와 S의 원 heartbeat 진단. 실제 주행/World 수락 시험은 아니다.

control 사건은 결정적 compute 대역으로 주입한다. 원 송신/compute 통합 경계는
test_mapper_integration이 별도로 검사하고, S 진단은 실제 lifecycle codec/receiver를 쓴다.
"""

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from lifecycle_message_fixtures import packet,peer
from xenova_vehicle.runtime.lifecycle_messages import LifecycleReceiver
from xenova_vehicle.runtime.lifecycle_policy import AbsoluteDeadline,validate_state
from xenova_vehicle.runtime.mapper_compute import MapperEvent,MapperResult
from xenova_vehicle.runtime.mapper_control import MapperControl
from xenova_vehicle.runtime.mapper_progress import MapperJobWatch
from xenova_vehicle.runtime.resident_runtime import _Runtime,_Worker


class Compute:
    def __init__(self):
        self.events=[];self.result=None;self.terminal_error=None
    def poll(self):
        result,self.events=self.events,[]
        return result
    def take_result(self):
        result,self.result=self.result,None
        return result
    def acknowledge_parser_start(self,job_id,actual):return True
    def skip_parser(self,job_id):raise AssertionError('fixture sources must advance')


class Actor:
    def __init__(self):
        self.state='WARMUP';self.progresses=[]
        self.binding=SimpleNamespace(sha256='a'*64,
            fields={'stream_instances':{'nn_input0':str(uuid4())}})
    def progress(self,stage,sequence=None,**fields):
        self.progresses.append((stage,sequence,self.state,fields))
        return object()
    def await_sent(self,original,*,cap_us):return original


def controller():
    now=[1000];actor=Actor();compute=Compute()
    control=MapperControl(actor,compute,AbsoluteDeadline(1000,15_001_000),clock=lambda:now[0])
    return control,actor,compute,now


def warmup(control,compute,now,count=5):
    for sequence in range(1,count+1):
        now[0]+=10;job=str(uuid4());started=now[0]-1
        event=MapperEvent('PARSER_START_REQUIRED',job,control.generation,1,(sequence,sequence),
            sequence,started,now[0],now[0]+500_000,False,
            parser_deadline_us=now[0]+500_000,nn_source_key=(sequence,sequence,sequence,started))
        compute.events=[event,replace(event,kind='NN_COMPLETED',accepted=True)]
        control.tick()


def offer(control,compute,now,latest,*,expires=None):
    payload=json.dumps({'processed':1,'latest':latest},allow_nan=False).encode('ascii')
    result=MapperResult(str(uuid4()),control.generation,1,(1,1),now[0]-1,now[0],
        now[0]+500_000 if expires is None else expires,True,payload)
    compute.result=result
    return result


def metric(**changes):
    value=dict(metric_input_reasons=[],source_vehicle_sequence=7,mapping_ready=True,
        mapping_status='IDLE',mapping_reason='READY',localization_configured=True,
        metric_ready=False,world_valid=False,live_authority=False)
    value.update(changes)
    return value


class MapperCurrentPhaseTests(unittest.TestCase):
    def test_original_parser_five_preserved_and_current_processing_is_not_authority(self):
        control,actor,compute,now=controller()
        offer(control,compute,now,metric())
        control.tick()
        self.assertEqual(actor.state,'WARMUP')
        warmup(control,compute,now,4)
        self.assertFalse(control.ready)
        self.assertEqual(actor.state,'WARMUP')
        # 다섯째도 독립 원 source여야 하므로 앞 네 source를 이어서 보낸다.
        now[0]+=10;job=str(uuid4())
        event=MapperEvent('PARSER_START_REQUIRED',job,control.generation,1,(5,5),5,
            now[0]-1,now[0],now[0]+500_000,False,
            parser_deadline_us=now[0]+500_000,nn_source_key=(5,5,5,now[0]-1))
        compute.events=[event,replace(event,kind='NN_COMPLETED',accepted=True)]
        control.tick()
        self.assertTrue(control.ready)
        self.assertEqual((control.streak,actor.state),(5,'RUNNING'))
        self.assertEqual([row[0] for row in actor.progresses].count('NN_READY'),1)
        self.assertFalse(control.latest_summary['latest']['metric_ready'])
        self.assertFalse(control.latest_summary['latest']['world_valid'])
        self.assertFalse(control.latest_summary['latest']['live_authority'])

    def test_final_clock_vehicle_storage_reasons_and_optional_old_row_warnings(self):
        control,actor,compute,now=controller();warmup(control,compute,now)
        self.assertEqual(actor.state,'WAIT_INPUT')
        cases=(
            (metric(metric_input_reasons=['SOURCE_NOT_BOUND'],source_vehicle_sequence=None),'WAIT_INPUT'),
            (metric(metric_input_reasons=['PI_J_CLOCK_UNAVAILABLE'],source_vehicle_sequence=None),'WAIT_CLOCK'),
            (metric(metric_input_reasons=['VEHICLE_CLOCK_UNAVAILABLE','TOF_CLOCK_UNAVAILABLE']),'RUNNING'),
            (metric(metric_input_reasons=['VEHICLE_UNAVAILABLE'],source_vehicle_sequence=None),'WAIT_ODOM'),
            (metric(metric_input_reasons=['FULL_CAPTURE_POSE_UNAVAILABLE']),'WAIT_ODOM'),
            (metric(mapping_ready=False,mapping_reason='PROVENANCE_NOT_READY',mapping_status='WAIT_ORIGINAL_D_JOB'),'WAIT_STORAGE'),
            (metric(mapping_ready=True,mapping_status='WAIT_ORIGINAL_D_JOB'),'RUNNING'),
            (metric(mapping_ready=False,mapping_status='PROFILE_NOT_READY'),'DEGRADED'),
            (metric(quarantined=True),'DEGRADED'),
            (metric(map_restore_issue='MAP_RESTORE_NOT_READY'),'DEGRADED'),
            (metric(),'RUNNING'))
        for latest,expected in cases:
            with self.subTest(expected=expected,latest=latest):
                now[0]+=1;offer(control,compute,now,latest);control.tick()
                validate_state('M',actor.state)
                self.assertEqual(actor.state,expected)
                self.assertEqual(control.latest_summary['latest'],latest)
        self.assertTrue(control.ready)
        self.assertEqual(control.streak,5)

    def test_expired_foreign_and_source_fenced_results_do_not_renew_phase_or_skip_watchdog(self):
        control,actor,compute,now=controller();warmup(control,compute,now)
        original=offer(control,compute,now,metric(),expires=now[0]+50);control.tick()
        accepted=control.latest_summary
        compute.result=replace(original,source_generation='b'*64,expires_us=now[0]+500_000)
        control.tick()
        compute.result=replace(original,accepted=False,payload=None,reason='STOP_OR_SOURCE_FENCE')
        control.tick()
        now[0]=original.expires_us
        compute.result=original
        control.tick()
        self.assertEqual(actor.state,'WAIT_INPUT')
        self.assertIs(control.latest_summary,accepted)
        self.assertEqual(control.result_count,1)
        # 폐기할 결과가 있어도 원 parser 무진전 감시를 건너뛰면 안 된다.
        now[0]=control.last_completion+control.nn_stall_us
        compute.result=replace(original,source_generation='b'*64)
        with self.assertRaisesRegex(TimeoutError,'parser association stalled'):control.tick()

    def test_json_decode_time_consumes_original_result_lifetime(self):
        control,actor,compute,now=controller();warmup(control,compute,now)
        original=offer(control,compute,now,metric(),expires=now[0]+10)
        loads=json.loads
        def decode(raw):
            now[0]=original.expires_us
            return loads(raw)
        with patch('xenova_vehicle.runtime.mapper_control.json.loads',side_effect=decode):control.tick()
        self.assertIsNone(control.latest_summary)
        self.assertEqual((control.result_count,actor.state),(0,'WAIT_INPUT'))

    def test_terminal_states_and_original_first_output_deadline_are_not_overwritten(self):
        control,actor,compute,now=controller();warmup(control,compute,now)
        for terminal in ('QUIESCING','RECOVERING','FAILED','STOPPED'):
            actor.state=terminal
            offer(control,compute,now,metric());control.tick()
            self.assertEqual(actor.state,terminal)
        control,actor,compute,now=controller()
        now[0]=control.deadline.expires_us
        offer(control,compute,now,metric(),expires=now[0])
        with self.assertRaisesRegex(TimeoutError,'first-output deadline'):control.tick()
        self.assertFalse(control.ready)

    def test_actual_cpu_progress_does_not_make_clock_or_world_current(self):
        control,actor,compute,now=controller();warmup(control,compute,now)
        offer(control,compute,now,metric(metric_input_reasons=['PI_J_CLOCK_UNAVAILABLE']))
        control.tick()
        event=MapperEvent('JOB_STARTED',str(uuid4()),control.generation,1,(8,8),None,
            now[0],now[0],now[0]+500_000,False)
        compute.events=[event,replace(event,kind='PROGRESS',reason='DEPTH_COMPLETED')]
        control.tick()
        self.assertEqual(actor.state,'WAIT_CLOCK')
        self.assertEqual(control.result_count,1)
        self.assertEqual(actor.progresses[-1][0],'MAPPER_PROGRESS')


class ResidentPhaseDiagnosticTests(unittest.TestCase):
    def runtime(self):
        runtime=object.__new__(_Runtime);current=peer('M')
        host=_Worker(current,object(),object(),LifecycleReceiver(current,'S'),100)
        host.admitted=True
        runtime.workers={'M':host};runtime.stopping=False
        runtime.recovery=SimpleNamespace(suppressed=lambda role,host:False)
        runtime.event=lambda *args,**fields:None
        runtime.nn_ready_us=900;runtime.last_fresh_parser_us=950
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=1000):
            runtime._dispatch('M',packet('HELLO',current=current,issued=100,expires=5000))
        return runtime,host,current

    def test_admitted_heartbeat_phase_keeps_original_lifetime_and_cpu_readiness_separate(self):
        runtime,host,current=self.runtime()
        heartbeat=packet('HEARTBEAT',current=current,sequence=2,issued=1000,expires=2_000_000,
            payload=dict(worker_state='WAIT_CLOCK',last_progress_sequence=None,last_progress_j_us=None))
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=1100):runtime._dispatch('M',heartbeat)
        host.compute_watch=MapperJobWatch()
        host.compute_watch.observe(str(uuid4()),'MAPPER_STARTED',8,1050,'501050',now_us=1100)
        diagnostic=runtime._mapper_diagnostics(1200)
        self.assertEqual(diagnostic['external_waits'],['PI_J_CLOCK'])
        self.assertEqual(diagnostic['phase']['issued_j_us'],'1000')
        self.assertEqual(diagnostic['phase']['received_j_us'],'1100')
        self.assertIsNotNone(diagnostic['cpu_active'])
        self.assertTrue(diagnostic['parser_warmup_observed'])
        self.assertIsNone(diagnostic['world_current'])
        # heartbeat가 살아 있어도 기존1초 관측 공백 경계 뒤에는 current로 표시하지 않는다.
        stale=runtime._mapper_diagnostics(1_001_000)
        self.assertFalse(stale['phase']['current'])
        self.assertIsNone(stale['external_waits'])
        self.assertEqual(stale['last_observed_waits'],['PI_J_CLOCK'])
        runtime.stopping=True
        self.assertFalse(runtime._mapper_diagnostics(1200)['phase']['current'])
        self.assertIsNone(runtime._mapper_diagnostics(1200)['external_waits'])

    def test_missing_or_rejected_current_peer_heartbeat_does_not_invent_waits(self):
        runtime,host,current=self.runtime()
        self.assertIsNone(runtime._mapper_diagnostics(1100)['external_waits'])
        self.assertIsNone(runtime._mapper_diagnostics(1100)['phase'])
        heartbeat=packet('HEARTBEAT',current=current,sequence=2,issued=1000,expires=1200,
            payload=dict(worker_state='RUNNING',last_progress_sequence=None,last_progress_j_us=None))
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=1200):
            with self.assertRaises(ValueError):runtime._dispatch('M',heartbeat)
        self.assertIsNone(host.phase_observation)
        with patch('xenova_vehicle.runtime.resident_runtime.j_us',return_value=1100):runtime._dispatch('M',heartbeat)
        diagnostic=runtime._mapper_diagnostics(1100)
        self.assertEqual(diagnostic['external_waits'],[])
        self.assertIsNone(diagnostic['world_current'])
        host.eof=True
        self.assertIsNone(runtime._mapper_diagnostics(1100)['external_waits'])


if __name__=='__main__':unittest.main()
