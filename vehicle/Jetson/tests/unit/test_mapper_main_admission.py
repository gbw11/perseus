"""M-main의 metadata 수락과 제어 역압 중 진행을 검사한다.

원 wire/clock fixture를 사용한다. 소켓 역압은 실제 Linux seqpacket이며 카메라,
실물 시계·보정·주행 허가를 검증하지 않는다. 앱 실행은 Nano에서만 한다.
"""

from collections import deque
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_mapper_runtime_profile as runtime_fixture
import test_resident_release as release_fixture
from xenova_vehicle.runtime import resident_workers as workers


class MapperMainAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=runtime_fixture.MapperRuntimeProfileTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_main_builds_only_sensor_metadata_and_attaches_original_capture_hull(self):
        fixture=self.fixture
        with patch('xenova_vehicle.runtime.resident_perception.ResidentPerception',
                side_effect=AssertionError('main cannot build CV core')):
            boot=SimpleNamespace(mapper_profile_bytes=fixture.config.canonical_bytes,
                perception_profile_bytes=fixture.calibration_bytes,source_policy=fixture.source_policy,
                clock_policy=fixture.fixture.policy,
                peer=SimpleNamespace(worker_instance=fixture.fixture.mapper_worker),
                mapper_instance=fixture.fixture.mapper_instance)
            config,owner=workers.mapper_sensor_owner(boot,fixture.raw)
            item=fixture.item()
            owner.ingest(item.context_bytes,now_us=fixture.now)
            # 사용자의 실제 실행 경로처럼 같은 frame의 snapshot을 한 번만 결합한다.
            from xenova_vehicle.runtime.mapper_compute import MapperInput
            original=MapperInput(item.job_id,item.source_generation,item.sample,item.expires_us,
                context_bytes=item.context_bytes)
            attached=workers.attach_sensor_snapshot(original,owner,config.capture_timing,now_us=fixture.now)
        self.assertIs(attached.sample.rgb.payload,original.sample.rgb.payload)
        self.assertIs(attached.sample.depth.payload,original.sample.depth.payload)
        self.assertEqual(attached.sensor_snapshot.capture_stamp,item.sensor_snapshot.capture_stamp)
        self.assertEqual((attached.job_id,attached.source_key,attached.expires_us),
            (original.job_id,original.source_key,original.expires_us))
        self.assertIsNotNone(attached.sensor_snapshot.metric_inputs.pose)

    def test_missing_timing_preserves_none_and_foreign_calibration_is_rejected(self):
        item=self.fixture.item(capture_timing=None)
        self.assertIsNone(item.sensor_snapshot.capture_stamp.captured_us)
        self.assertIsNone(item.sensor_snapshot.metric_inputs.pose)
        boot=SimpleNamespace(mapper_profile_bytes=self.fixture.config.canonical_bytes,
            perception_profile_bytes=self.fixture.calibration_bytes,
            source_policy=replace(self.fixture.source_policy,calibration_profile_sha256='f'*64),
            clock_policy=self.fixture.fixture.policy,
            peer=SimpleNamespace(worker_instance=self.fixture.fixture.mapper_worker),
            mapper_instance=self.fixture.fixture.mapper_instance)
        with self.assertRaises(ValueError):workers.mapper_sensor_owner(boot,self.fixture.raw)

    def actor(self):
        actor=object.__new__(workers._Actor)
        actor.link=Mock();actor.stop=None;actor.deferred=deque()
        actor.idle_hook=None;actor._idle_busy=False
        actor.drain_sent=Mock();actor._receive=Mock(return_value=[])
        return actor

    def test_busy_send_services_main_admission_without_reissuing_original_draft(self):
        actor=self.actor();accepted=object();threads=[]
        item=self.fixture.item()
        def admit():
            threads.append(threading.get_ident())
            self.fixture.sensor_owner.ingest(item.context_bytes,now_us=self.fixture.now)
            actor.service_idle()  # 재진입은 새 pump를 추가하지 않는다.
        actor.idle_hook=admit
        actor.link.send.side_effect=[None,None,accepted]
        with patch.object(workers,'j_us',return_value=150),patch.object(workers.time,'sleep'):
            actual=actor.send_required('SLOT',{'op':'RELEASE'},issued_us=100,expires_us=200,
                request_id='00000000-0000-4000-8000-000000000001')
        self.assertIs(actual,accepted)
        self.assertEqual(threads,[threading.get_ident()]*2)
        self.assertEqual(actor.link.send.call_args_list[0],actor.link.send.call_args_list[-1])
        self.assertEqual(self.fixture.sensor_owner.sensors.latest_vehicle.anchor.sequence,2)

    def test_stop_skips_pump_and_hook_exception_stops_original_send(self):
        actor=self.actor();actor.idle_hook=Mock()
        def stop():actor.stop=object();return []
        actor._receive.side_effect=stop;actor.link.send.return_value=None
        with patch.object(workers,'j_us',return_value=150),self.assertRaises(workers._StopWork):
            actor.send_required('SLOT',{},issued_us=100,expires_us=200)
        actor.idle_hook.assert_not_called()
        actor=self.actor();actor.link.send.return_value=None
        actor.idle_hook=Mock(side_effect=ValueError('ORIGINAL_SOURCE_CHANGED'))
        with patch.object(workers,'j_us',return_value=150),self.assertRaisesRegex(ValueError,'ORIGINAL_SOURCE_CHANGED'):
            actor.send_required('SLOT',{},issued_us=100,expires_us=200)
        self.assertFalse(actor._idle_busy)
        self.assertEqual(actor.link.send.call_count,1)


@unittest.skipUnless(release_fixture.sys.platform=='linux','Linux seqpacket actual-send 시험')
class MapperAdmissionReleaseTests(unittest.TestCase):
    def test_actual_release_backpressure_services_same_main_before_full_send(self):
        fixture=release_fixture.ResidentReleaseTests('runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.saturate();packet,_=fixture.release()
        entered=threading.Event();threads=[];errors=[]
        def admit():threads.append(threading.get_ident());entered.set()
        fixture.actor.idle_hook=admit
        def drain():
            try:
                if not entered.wait(1):raise AssertionError('main pump did not progress under actual EAGAIN')
                fixture.drain_raw()
            except BaseException as error:errors.append(error)
        drainer=threading.Thread(target=drain)
        drainer.start()
        try:
            actual=fixture.actor.await_sent(packet)
            self.assertIs(actual.packet,packet)
            self.assertTrue(threads)
            self.assertEqual(set(threads),{threading.get_ident()})
        finally:
            drainer.join(2)
        self.assertFalse(drainer.is_alive())
        self.assertEqual(errors,[])


if __name__=='__main__':unittest.main()
