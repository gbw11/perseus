"""J 원 source/시각·bounded 큐와 실제 한 thread/소켓 입구를 검사한다.

합성 Pi 왕복은 실장비 clock 수락이 아니다. fake Paho는 broker 완료를 증명하지
않으며 실제 raw socket 시험은 pinned parser의 사전 길이 제한만 검증한다.
"""

from collections import deque
from dataclasses import asdict, replace
from hashlib import sha256
import socket
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from uuid import UUID

from xenova_drive_world import schema, encode, decode
from xenova_drive_world.mapper_clock import (ClockPolicy, MapperClockOwner, build_probe, candidate_body)
from xenova_vehicle.runtime.source_identity import SourcePolicy
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.transport.jio_profile import JetsonJioProfile
from xenova_vehicle.transport.jetson_jio import JetsonJio, _new_paho, PREFIX, VEHICLE_TOPIC, WORLD_TOPIC


def uid(number):
    return '00000000-0000-4000-8000-%012x' % number


def profile():
    clock = ClockPolicy('rc-test-1', 'oak-1', 'test-config', 'a' * 64, 'b' * 64,
        2, 10, 5000, 1_000_000, 500_000, 1_000_000)
    source = SourcePolicy(clock.vehicle, clock.configuration, clock.calibration, clock.primitive, 'c' * 64)
    return JetsonJioProfile(source, clock, clock.device, '127.0.0.1', 1883,
        tof_profiles=((0, 'd' * 64, 1), (1, 'e' * 64, 1)))


def source():
    return dict(vehicle='rc-test-1', device='oak-1', mapper_boot=uid(10), mapper_instance=uid(11),
        clock_epoch=uid(12), map_epoch=uid(13), frame_epoch=uid(14), odom_epoch=uid(3),
        configuration='test-config', calibration='a' * 64, primitive='b' * 64)


def bind(owner, fields=None):
    fields=source() if fields is None else fields
    changed=owner.replace_source(fields, pi_host_boot=uid(1),
        gateway_instance=uid(4), device_session=uid(2), pi_session=uid(5))
    owner.replace_publication(publication(fields))
    return changed


def publication(fields=None, *, revision=1, scene=None):
    fields=source() if fields is None else fields
    return dict(service_instance=uid(50),mapper_worker=uid(51),raw_binding_sha256='c'*64,
        **{key:fields[key] for key in ('mapper_instance','map_epoch','frame_epoch','odom_epoch',
            'configuration','calibration','primitive')},scene_admission_ref=scene,source_binding_revision=str(revision))


def qualify_clock(owner):
    """실제 shared owner가 만든 returned Probe13을 callback으로 받는다. 실물 시계 수락은 아니다."""
    deliver(owner,f'{PREFIX}/mapper/oak-1/clock/request.pb',returned_probe())
    if owner.current_lease is None:raise AssertionError('fixture needs its actual returned clock lease')


def vehicle(sequence=1):
    return schema.VehicleSample(major=1, vehicle='rc-test-1', host_boot=uid(1), device_session=uid(2),
        odom_epoch=uid(3), sequence=sequence, source_sample=sequence, source_us=999900 + sequence,
        pi_sample_us=1000000 + sequence, clock_error_us=10, left_ticks=sequence, right_ticks=sequence,
        odom_pose=schema.Pose(x=.1, y=.2, yaw=.03), velocity_mps=.1, position_error_m=.001,
        heading_error_rad=.001, quality=3, calibration='a' * 64, gateway_instance=uid(4), pi_session=uid(5))


def reply(probe):
    fields = source(); fields.pop('vehicle')
    return schema.MapperClockReply(major=1, pi_host_boot=probe.pi_host_boot,
        pi_client_instance=probe.pi_client_instance, probe_seq=probe.probe_seq, nonce=probe.nonce,
        pi_send_us=probe.pi_send_us, j2=probe.pi_send_us - 100000 + 100,
        j3=probe.pi_send_us - 100000 + 120, **fields)


def returned_probe():
    """실제 shared clock owner의 두 합성 완료 뒤 만들어진 세 번째 원 Probe13이다."""
    owner = MapperClockOwner(profile().clock_policy, uid(1))
    owner.set_vehicle(encode(vehicle()), 1000001)
    for at in (1000001, 1200001):
        plan = owner.plan(at, uid(20)); raw = build_probe(plan, at + 100)
        candidate = candidate_body(raw, encode(reply(decode(raw, schema.MapperClockProbe))), at + 500)
        owner.accept(plan, candidate, at + 500)
    plan = owner.plan(1400001, uid(20))
    return build_probe(plan, 1400101)


class Clock:
    def __init__(self, now=900200): self.now = now
    def __call__(self): return self.now


def deliver(owner, topic, value, *, qos=0, retained=False):
    owner._receive(SimpleNamespace(topic=topic, payload=value if type(value) is bytes else encode(value),
        qos=qos, retain=retained))


def tof(sensor, sequence=1, generation=1):
    from xenova_vehicle.transport.sensor_samples import tof_crc32
    value = schema.TofObservation(major=1, vehicle='rc-test-1', host_boot=uid(1), gateway_instance=uid(4),
        pi_session=uid(5), device_session=uid(2), sensor_id=sensor, source_sample=sequence,
        source_us=1000 + sequence, pi_sample_us=1400000 + sequence, clock_error_us=10,
        sensor_time_error_us=100, calibration=('d' if sensor == 0 else 'e') * 64,
        sensor_config_revision=1, rows=8, cols=8, distance_mm_le=struct.pack('<64H', *([500] * 64)),
        status=bytes(64), quality=bytes([3] * 64), frame_status=3, source_crc32=0,
        clock_epoch=uid(30 + sensor), clock_seq=sequence, complete=True,
        sensor_instance=UUID(uid(40 + sensor + generation * 10)).bytes, sensor_generation=generation)
    value.source_crc32 = tof_crc32(value)
    return value


def world(sequence=1):
    fields = source(); fields.pop('device')
    value = schema.WorldSnapshot(major=1, minor=0, required_features=0, sequence=sequence,
        reference_id='synthetic-reference', reference_revision=1, origin=schema.Pose(x=0., y=0., yaw=0.),
        resolution_m=.125, width=2, height=2, reference_odom_seq=1,
        reference_vehicle_pose=schema.Pose(x=0., y=0., yaw=0.), observed_us=900050,
        completed_us=900060, published_us=900070, observation_seq=1,
        occupancy=bytes([255] * 4), cell_flags=bytes(4), age_le32=b'\xff' * 16,
        complete=True, truncated=False, inflation_policy=0, **fields)
    for name, y in (('reference', 0.), ('left_boundary', .5), ('right_boundary', -.5)):
        for x in (0., 1.): getattr(value, name).add(x=x, y=y)
    return encode(value)


class Info:
    rc = 0
    def __init__(self): self.done = False
    def is_published(self): return self.done


class FakeClient:
    """한 실제 thread의 호출/역압을 기록한다. 네트워크·PUBACK 모조를 실증으로 세지 않는다."""
    def __init__(self):
        self.calls = []; self.published = []; self.incoming = deque(); self.gate = None
        self.entered_loop = threading.Event()
    def connect(self, *args, **kwargs):
        self.calls.append(('connect', threading.get_ident()))
        self.on_connect(self, None, None, 0, None)
    def socket(self): return None
    def subscribe(self, topic, qos): self.calls.append(('subscribe', threading.get_ident(), topic, qos))
    def loop(self, timeout):
        self.entered_loop.set()
        if self.gate is not None: self.gate.wait(1)
        if self.incoming: self.on_message(self, None, self.incoming.popleft())
        return 0
    def publish(self, topic, raw, **kwargs):
        self.calls.append(('publish', threading.get_ident()))
        info = Info();info.done=topic!=WORLD_TOPIC
        self.published.append((topic, raw, info)); return info
    def disconnect(self): self.calls.append(('disconnect', threading.get_ident()))


class JetsonJioTests(unittest.TestCase):
    def test_exact_optional_profile_pins_pair_and_does_not_disclose_password(self):
        p = profile()
        value = dict(version=1, source_policy=asdict(p.source_policy), clock_policy=asdict(p.clock_policy),
            device=p.device, host=p.host, port=p.port, username='private-user', password='private-placeholder',
            tof_profiles=[list(row) for row in p.tof_profiles], tof_max_age_us=p.tof_max_age_us)
        parsed = JetsonJioProfile.from_dict(value)
        self.assertNotIn('private-placeholder', repr(parsed))
        value['device'] = 'another-device'
        with self.assertRaises(ValueError): JetsonJioProfile.from_dict(value)
        value['extra'] = 1
        with self.assertRaises(ValueError): JetsonJioProfile.from_dict(value)

    def test_first_approved_vehicle_is_delivered_before_identity_without_a_clock_lease(self):
        clock = Clock(); owner = JetsonJio(profile(), clock=clock)
        raw = encode(vehicle()); deliver(owner, VEHICLE_TOPIC, raw)
        items = owner.take_inputs()
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].kind, items[0].raw, items[0].received_j_us), ('VEHICLE', raw, clock.now))
        self.assertIsNone(items[0].lease)
        clock.now += 100; deliver(owner, VEHICLE_TOPIC, raw)
        self.assertEqual(owner.take_inputs(), ())
        foreign = vehicle(2); foreign.calibration = 'f' * 64
        deliver(owner, VEHICLE_TOPIC, foreign)
        self.assertEqual(owner.take_inputs(), ())

    def test_bound_identity_answers_initial_probe_without_capture_or_nn_readiness(self):
        clock = Clock(); owner = JetsonJio(profile(), clock=clock)
        owner_pi = MapperClockOwner(profile().clock_policy, uid(1))
        owner_pi.set_vehicle(encode(vehicle()), 1000001)
        raw = build_probe(owner_pi.plan(1000001, uid(20)), 1000101)
        topic = f'{PREFIX}/mapper/oak-1/clock/request.pb'
        deliver(owner, topic, raw)
        self.assertIsNone(owner._probe)
        bind(owner); deliver(owner, topic, raw)
        pending = owner._probe; self.assertIsNone(pending.item.lease)
        clock.now += 37
        result = decode(owner._reply(pending), schema.MapperClockReply)
        original = decode(raw, schema.MapperClockProbe)
        self.assertEqual((result.j2, result.j3), (900200, 900237))
        self.assertEqual((result.probe_seq, result.nonce, result.pi_send_us),
            (original.probe_seq, original.nonce, original.pi_send_us))
        self.assertEqual(result.mapper_instance, source()['mapper_instance'])

    def test_probe13_restores_only_the_actual_prior_lease_and_duplicate_does_not_retime(self):
        clock = Clock(1300200); owner = JetsonJio(profile(), clock=clock); bind(owner)
        raw = returned_probe(); topic = f'{PREFIX}/mapper/oak-1/clock/request.pb'
        deliver(owner, topic, raw); item = owner.take_inputs()[0]
        self.assertEqual(item.kind, 'PROBE'); self.assertIsNotNone(item.lease)
        self.assertIsNone(item.lease.completed_pi_us)
        self.assertEqual(item.lease.raw_probe_bytes, raw)
        self.assertEqual(item.lease.trust_bytes, encode(decode(raw, schema.MapperClockProbe).mapper_trust))
        deadline = item.lease.expires_pi_us
        clock.now += 100; deliver(owner, topic, raw)
        self.assertEqual(owner.take_inputs(), ())
        self.assertEqual(owner._lease.expires_pi_us, deadline)
        self.assertEqual(owner._probe.item.received_j_us, 1300200)

    def test_foreign_expected_source_and_retained_qos_or_oversized_input_are_rejected(self):
        owner = JetsonJio(profile(), clock=Clock(1300200)); bind(owner)
        raw = returned_probe(); probe = decode(raw, schema.MapperClockProbe)
        probe.expected_mapper_instance = uid(99)
        probe.mapper_trust.mapper_instance = uid(99)
        topic = f'{PREFIX}/mapper/oak-1/clock/request.pb'
        deliver(owner, topic, probe)
        deliver(owner, topic, raw, retained=True)
        deliver(owner, topic, raw, qos=1)
        deliver(owner, topic, b'x' * 1025)
        self.assertEqual(owner.take_inputs(), ())
        self.assertIsNone(owner._probe)

    def test_new_probe_without_prior_trust_retires_old_time_lease_without_changing_source(self):
        clock = Clock(1300200); owner = JetsonJio(profile(), clock=clock); bind(owner)
        topic = f'{PREFIX}/mapper/oak-1/clock/request.pb'
        raw = returned_probe(); deliver(owner, topic, raw); owner.take_inputs()
        token = owner._token; self.assertIsNotNone(owner._lease)
        newer = decode(raw, schema.MapperClockProbe); newer.probe_seq += 1
        newer.admission_revision += 1; newer.ClearField('mapper_trust')
        clock.now += 100; deliver(owner, topic, newer)
        self.assertIsNone(owner._lease); self.assertIs(owner._token, token)
        self.assertIsNone(owner.take_inputs()[0].lease)
        deliver(owner, topic, raw)
        self.assertEqual(owner.take_inputs(), ())

    def test_scene_latest_preserves_original_grant_and_requires_current_returned_pi_revision(self):
        clock = Clock(1300200); owner = JetsonJio(profile(), clock=clock); bind(owner)
        deliver(owner, f'{PREFIX}/mapper/oak-1/clock/request.pb', returned_probe()); owner.take_inputs()
        scene = schema.SceneGrant(major=1, vehicle='rc-test-1', pi_host_boot=uid(1), gateway_instance=uid(4),
            source_admission_revision=owner._lease.source_revision, mapper_device='oak-1',
            map_epoch=uid(13), frame_epoch=uid(14), odom_epoch=uid(3), configuration='test-config',
            calibration='a' * 64, primitive='b' * 64, issued_pi_us=1400000, valid_until_pi_us=3000000,
            lifecycle_state=0, request_nonce=bytes([8] * 16), survey_deadline_pi_us=2900000,
            max_observation_stations=24, purpose=0, frozen_survey_revision=0, frozen_hazard_revision=0,
            lifecycle_revision=1, scene=schema.SceneAdmissionRef(admission_id=UUID(uid(70)).bytes,
                admission_revision=1, survey_id=UUID(uid(71)).bytes, survey_revision=1, policy=1,
                coverage_profile_sha256=bytes([9] * 32), allow_restore_after_mapper_restart=True))
        topic = f'{PREFIX}/mapper/oak-1/scene.pb'
        deliver(owner, topic, scene, qos=0)
        self.assertEqual(owner.take_inputs(), ())
        deliver(owner, topic, scene, qos=1)
        first = owner.take_inputs()[0]
        self.assertEqual((first.kind, first.raw, first.received_j_us), ('SCENE', encode(scene), clock.now))
        deliver(owner, topic, scene, qos=1)
        self.assertEqual(owner.take_inputs(), ())
        scene.lifecycle_revision = 2; scene.source_admission_revision += 1
        deliver(owner, topic, scene, qos=1)
        self.assertEqual(owner.take_inputs(), ())

    def test_tof_keeps_two_per_sensor_and_preserves_original_instance_crc_and_callback_time(self):
        clock = Clock(1300200); owner = JetsonJio(profile(), clock=clock); bind(owner)
        for sensor in (0, 1):
            for sequence in (1, 2, 3):
                clock.now += 1
                deliver(owner, f'{PREFIX}/sensors/tof/{sensor}/observation.pb', tof(sensor, sequence))
        items = owner.take_inputs()
        self.assertEqual(len(items), 4)
        self.assertEqual([(x.sensor_id, decode(x.raw, schema.TofObservation).source_sample) for x in items],
            [(0, 2), (0, 3), (1, 2), (1, 3)])
        self.assertTrue(all(x.lease is None for x in items))
        clock.now += 1; deliver(owner, f'{PREFIX}/sensors/tof/1/observation.pb', tof(1, 3))
        self.assertEqual(owner.take_inputs(), ())
        deliver(owner, f'{PREFIX}/sensors/tof/1/observation.pb', tof(1, 4, generation=2))
        self.assertEqual(len(owner.take_inputs()), 1)
        deliver(owner, f'{PREFIX}/sensors/tof/1/observation.pb', tof(1, 5, generation=1))
        self.assertEqual(owner.take_inputs(), ())

    def test_world_latest_preserves_original_bytes_expiry_and_sequence_without_refresh(self):
        clock = Clock(1300200); owner = JetsonJio(profile(), clock=clock); bind(owner);qualify_clock(owner)
        publication_sha=sha256(canonical(publication())).hexdigest()
        raw = world(); self.assertTrue(owner.offer_world(raw, expires_j_us=1400000,publication_sha256=publication_sha))
        self.assertFalse(owner.offer_world(raw, expires_j_us=1500000,publication_sha256=publication_sha))
        self.assertEqual(owner._world.expires_us, 1400000)
        newer = world(2); self.assertTrue(owner.offer_world(newer, expires_j_us=1400000,publication_sha256=publication_sha))
        self.assertEqual(owner._world.raw, newer)
        self.assertFalse(owner.offer_world(raw, expires_j_us=1400000,publication_sha256=publication_sha))
        clock.now = 1400000
        self.assertFalse(owner.offer_world(world(3), expires_j_us=1400000,publication_sha256=publication_sha))

    def test_actual_source_replacement_fences_all_old_refs_and_same_source_renewal_is_noop(self):
        owner = JetsonJio(profile(), clock=Clock(1300200)); bind(owner)
        deliver(owner, f'{PREFIX}/mapper/oak-1/clock/request.pb', returned_probe())
        pending = owner._probe; token = owner._token
        self.assertFalse(bind(owner)); self.assertIs(owner._token, token)
        changed = dict(source(), mapper_instance=uid(90))
        self.assertTrue(bind(owner, changed))
        self.assertIsNone(owner._lease); self.assertIsNone(owner._probe)
        self.assertEqual(owner.take_inputs(), ())
        # 이미 선택한 원 request를 새 source로 다시 encode하지 않는다.
        old = decode(owner._reply(pending), schema.MapperClockReply)
        self.assertEqual(old.mapper_instance, source()['mapper_instance'])

    def test_pinned_paho_rejects_remaining_length_before_the_first_body_recv(self):
        try:
            from importlib.metadata import version
            if version('paho-mqtt') != '2.1.0': self.skipTest('pinned Paho2.1 raw parser 시험 필요')
        except ImportError:
            self.skipTest('Paho 없음')
        except Exception as error:
            if type(error).__name__ == 'PackageNotFoundError': self.skipTest('Paho 없음')
            raise
        left, right = socket.socketpair(); left.setblocking(False)
        self.addCleanup(left.close); self.addCleanup(right.close)
        calls = []; oversized = []
        class Spy:
            def recv(self, count): calls.append(count); return left.recv(count)
            def __getattr__(self, name): return getattr(left, name)
        client = _new_paho(profile(), lambda: oversized.append(True)); client._sock = Spy()
        right.sendall(b'\x30\x81\x0a')  # remaining-length1281. body를 보내지 않아도 먼저 거절한다.
        self.assertNotEqual(int(client._packet_read()), 0)
        self.assertEqual(calls, [1, 1, 1])
        self.assertEqual(oversized, [True]); self.assertEqual(len(client._in_packet['packet']), 0)
        client._sock = None

    def wait_for(self, predicate):
        until = time.monotonic() + 1
        while not predicate() and time.monotonic() < until: time.sleep(.002)
        self.assertTrue(predicate(), 'bounded fake-network event did not occur')

    def test_non_daemon_owner_has_one_actual_inflight_and_latest_world_while_backpressured(self):
        fake = FakeClient(); owner_ids = []
        def factory(*args): owner_ids.append(threading.get_ident()); return fake
        owner = JetsonJio(profile(), clock=Clock(1300200), client_factory=factory); bind(owner);qualify_clock(owner)
        publication_sha=sha256(canonical(publication())).hexdigest()
        owner.offer_world(world(), expires_j_us=1400000,publication_sha256=publication_sha); owner.start()
        self.addCleanup(lambda: (owner.stop(), owner.join(1)))
        worlds=lambda:[row for row in fake.published if row[0]==WORLD_TOPIC]
        self.wait_for(lambda: len(worlds()) == 1)
        self.assertFalse(owner._thread.daemon)
        owner.offer_world(world(2), expires_j_us=1400000,publication_sha256=publication_sha)
        owner.offer_world(world(3), expires_j_us=1400000,publication_sha256=publication_sha)
        time.sleep(.015); self.assertEqual(len(worlds()), 1)
        worlds()[0][2].done = True
        self.wait_for(lambda: len(worlds()) == 2)
        self.assertEqual((worlds()[1][0], worlds()[1][1]), (WORLD_TOPIC, world(3)))
        owner.stop(); self.assertTrue(owner.join(1))
        self.assertEqual(set(row[1] for row in fake.calls), set(owner_ids))
        self.assertNotEqual(owner_ids[0], threading.get_ident())

    def test_expired_queued_world_is_dropped_after_network_delay_without_timestamp_refresh(self):
        fake = FakeClient(); clock = Clock(1300200); fake.gate = threading.Event()
        owner = JetsonJio(profile(), clock=clock, client_factory=lambda *args: fake); bind(owner);qualify_clock(owner)
        owner.offer_world(world(), expires_j_us=1300300,publication_sha256=sha256(canonical(publication())).hexdigest()); owner.start()
        self.addCleanup(lambda: (fake.gate.set(), owner.stop(), owner.join(1)))
        self.wait_for(fake.entered_loop.is_set)
        clock.now = 1300300; fake.gate.set()
        self.wait_for(lambda: owner.snapshot()['counts']['expired'] == 1)
        self.assertEqual([row for row in fake.published if row[0]==WORLD_TOPIC], [])
        owner.stop(); self.assertTrue(owner.join(1))

    def test_join_reports_an_actual_still_alive_owner_instead_of_claiming_closed(self):
        fake = FakeClient(); fake.gate = threading.Event()
        owner = JetsonJio(profile(), clock=Clock(), client_factory=lambda *args: fake); owner.start()
        self.addCleanup(lambda: (fake.gate.set(), owner.stop(), owner.join(1)))
        self.wait_for(fake.entered_loop.is_set); owner.stop()
        self.assertFalse(owner.join(.001)); self.assertTrue(owner.snapshot()['alive'])
        fake.gate.set(); self.assertTrue(owner.join(1)); self.assertFalse(owner.snapshot()['alive'])


if __name__ == '__main__':
    unittest.main()
