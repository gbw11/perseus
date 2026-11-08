"""M control/compute의 원 송신·취소·채택 경계. camera/실시간 성능 수락은 아니다."""

from dataclasses import replace
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_resident_samples import sample
from test_resident_perception import provisional,binding
from xenova_vehicle.perception.calibration import canonical
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.lifecycle_policy import AbsoluteDeadline
from xenova_vehicle.runtime.mapper_compute import MapperCompute,MapperInput,MapperCancelled
from xenova_vehicle.runtime.mapper_control import MapperControl
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_progress import MapperJobWatch
from xenova_vehicle.runtime.resident_perception import ResidentPerception
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.resident_transport import ResidentLink,j_us


def fresh_sample():
    bound,old=sample()
    now=j_us()
    rgb=replace(old.rgb,received_j_us=now)
    depth=replace(old.depth,received_j_us=now)
    original=replace(old.nn_input,received_j_us=now)
    output=replace(old.nn_output,received_j_us=now)
    clocks={key:dict(bridge_sample=dict(value['bridge_sample']),
        sdk_host_timestamp_j_bracket_us=[str(now),str(now)]) for key,value in old.clocks.items()}
    return bound,CaptureSample(rgb,depth,original,output,clocks),now


class SocketActor:
    def __init__(self,bound):
        self.binding=bound
        self.state='WARMUP'
        self.remote,local=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.remote.setblocking(False)
        peer=LifecyclePeer(str(uuid4()),str(uuid4()),str(uuid4()),'M','a'*64)
        self.link=ResidentLink(local,peer,'M',track_sent=True)
        self.sent=[]

    def progress(self,stage,sequence=None,*,completed=None,reason=None,request_id=None):
        packet=self.link.send('PROGRESS',dict(stage=stage,source_sequence=str(sequence),
            completed_j_us=str(completed),state=self.state,reason=reason),
            issued_us=j_us(),request_id=request_id)
        assert packet is not None
        self.sent.extend(self.link.take_sent())
        self.remote.recv(4096)
        return packet

    def await_sent(self,packet,*,cap_us=None):
        actual=next(value for value in self.sent if value.packet is packet)
        assert actual.completed_us<cap_us
        return actual

    def close(self):
        self.link.close()
        self.remote.close()


class FakeCore:
    def __init__(self):self.processed=3
    def consume(self,original,*,now_us,nn_candidates,checkpoint,completed_clock):
        self.processed+=1
        checkpoint('INPUT_COMPLETED')
        return dict(now_us=completed_clock(),world_valid=False,metric_ready=False)


class MapperIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(socket,'SOCK_SEQPACKET'),'Linux seqpacket required')
    def test_actual_sent_nn_completion_precedes_slow_geometry_and_expiry_does_not_publish(self):
        bound,original,began=fresh_sample()
        actor=SocketActor(bound)
        release=threading.Event()
        parsed=threading.Event()
        committed=[]
        class Processor:
            def parse(self,item,checkpoint):parsed.set();return None
            def geometry(self,item,value,checkpoint):
                release.wait(2)
                checkpoint()
                return b'{"processed":1,"latest":{}}'
            def accepted(self,item):committed.append(item.job_id)
            def cancelled(self,item,reason):pass
        compute=MapperCompute(lambda generation:Processor(),source_generation=bound.sha256)
        controller=MapperControl(actor,compute,AbsoluteDeadline(began,began+15_000_000))
        compute.start()
        try:
            item=MapperInput(str(uuid4()),bound.sha256,original,began+150_000)
            self.assertTrue(compute.submit(item))
            end=time.monotonic()+1
            while controller.last_completion is None and time.monotonic()<end:
                controller.tick();time.sleep(.001)
            self.assertTrue(parsed.is_set())
            self.assertIsNotNone(controller.last_completion)
            stages=[record.packet.payload['stage'] for record in actor.sent]
            self.assertIn('PARSER_STARTED',stages)
            self.assertIn('PARSER_COMPLETED',stages)
            self.assertNotIn('MAPPER_COMPLETED',stages)
            self.assertIsNone(controller.latest_summary)
            # main은 긴 geometry 반환을 기다리지 않고 원 NN 완료를 이미 전달했다.
            while j_us()<item.expires_us+1000:
                controller.tick();time.sleep(.002)
            release.set()
            end=time.monotonic()+1
            while not any(record.packet.payload['stage']=='MAPPER_COMPLETED' for record in actor.sent) and time.monotonic()<end:
                controller.tick();time.sleep(.001)
            self.assertFalse(committed)
            self.assertIsNone(controller.latest_summary)
            self.assertEqual(controller.previous.received_j_us,original.nn_input.received_j_us)
            self.assertLess(controller.last_completion,item.expires_us)
            self.assertTrue(any(record.packet.payload['reason']=='SOURCE_EXPIRED' for record in actor.sent))
        finally:
            release.set();compute.request_stop();compute.join(2);actor.close()

    def test_cancelled_candidate_preserves_accepted_core_and_only_final_hook_adopts(self):
        with patch('xenova_vehicle.runtime.resident_perception.ResidentPerception',return_value=FakeCore()):
            processor=ResidentMapperProcessor(b'profile',object(),clock=lambda:1200)
        item=SimpleNamespace(job_id=str(uuid4()),sample=object())
        payload=processor.geometry(item,None,lambda phase=None:None)
        self.assertEqual(json.loads(payload)['processed'],4)
        self.assertEqual(processor._accepted.processed,3)
        processor.cancelled(item,'SOURCE_EXPIRED')
        self.assertEqual(processor._accepted.processed,3)
        processor.geometry(item,None,lambda phase=None:None)
        processor.accepted(item)
        self.assertEqual(processor._accepted.processed,4)
        def cancel(phase=None):raise MapperCancelled('SOURCE_EXPIRED')
        with self.assertRaises(MapperCancelled):processor.geometry(item,None,cancel)
        self.assertEqual(processor._accepted.processed,4)

    def test_actual_core_snapshot_uses_completion_clock_without_minting_capture_time(self):
        calibration=provisional()
        consumer=ResidentPerception(canonical(calibration.to_dict()),binding(calibration))
        _,original=sample(nn=False)
        phases=[]
        result=consumer.consume(original,now_us=1200,completed_clock=lambda:400_000,
            checkpoint=lambda phase=None:phases.append(phase))
        self.assertEqual(result['now_us'],400_000)
        self.assertIsNone(result['captured_us'])
        self.assertFalse(result['metric_ready'] or result['world_valid'] or result['live_authority'])
        self.assertIn('SNAPSHOT_COMPLETED',phases)
        self.assertEqual(result['rgb_source_key'][-1],original.rgb.sequence)

    def test_expired_input_is_not_immediate_process_hang_and_terminal_does_not_renew_it(self):
        watch=MapperJobWatch();job=str(uuid4())
        watch.observe(job,'MAPPER_STARTED',7,1000,'200000',now_us=1100)
        watch.observe(job,'MAPPER_PROGRESS',7,100000,'DEPTH_COMPLETED',now_us=100100)
        self.assertFalse(watch.stalled(250000))
        self.assertTrue(watch.stalled(600000))
        watch.observe(job,'MAPPER_COMPLETED',7,600000,'SOURCE_EXPIRED',now_us=600001)
        self.assertFalse(watch.stalled(900000))

    def test_watch_rejects_changed_deadline_source_future_and_late_success(self):
        watch=MapperJobWatch();job=str(uuid4())
        watch.observe(job,'MAPPER_STARTED',7,1000,'200000',now_us=1100)
        for args in ((job,'MAPPER_STARTED',7,1000,'300000',1100),
                     (job,'MAPPER_PROGRESS',8,1200,'DEPTH_COMPLETED',1300),
                     (job,'MAPPER_PROGRESS',7,1400,'DEPTH_COMPLETED',1300),
                     (job,'MAPPER_COMPLETED',7,200000,'COMPLETED',200000)):
            with self.subTest(stage=args[1]),self.assertRaises(ValueError):
                watch.observe(*args[:5],now_us=args[5])

    @unittest.skipUnless(hasattr(socket,'SOCK_SEQPACKET'),'Linux seqpacket required')
    def test_actual_supervisor_consumer_keeps_metric_job_separate_from_parser_readiness(self):
        from xenova_vehicle.runtime.resident_runtime import _Runtime
        bound,original,now=fresh_sample()
        actor=SocketActor(bound)
        runtime=object.__new__(_Runtime)
        host=SimpleNamespace(bind_ack=object(),compute_watch=None,parse_started_us=None)
        runtime.workers={'M':host};runtime.stopping=False
        job=str(uuid4())
        try:
            packet=actor.progress('MAPPER_STARTED',original.rgb.sequence,completed=now,
                                  reason=str(now+500_000),request_id=job)
            runtime._progress('M',packet,j_us())
            self.assertEqual(host.compute_watch.active.job_id,job)
            self.assertIsNone(host.parse_started_us)
            packet=actor.progress('MAPPER_COMPLETED',original.rgb.sequence,completed=j_us(),
                                  reason='COMPLETED',request_id=job)
            runtime._progress('M',packet,j_us())
            self.assertIsNone(host.compute_watch.active)
            self.assertIsNone(host.parse_started_us)
        finally:actor.close()


if __name__=='__main__':unittest.main()
