"""원 clock 왕복/바이너리 local framing/직접 source 사본의 유한 계약 시험.

가짜 peer 값은 실제 clock/센서 수락이 아니다. broker/process 통합과 OS 종료 증명은
별도 실행 대상이며 여기서 모조 process의 join은 실제 reap 근거로 보고하지 않는다.
"""

from dataclasses import replace
import io
import json
import struct
from contextlib import redirect_stderr
from types import SimpleNamespace
import unittest

from xenova_drive_world import schema, encode, decode
from xenova_drive_world.mapper_clock import (ClockPolicy, MapperClockOwner, MapperClockError,
    build_probe, candidate_body, parse_candidate, plan_json, parse_plan, restore_returned_probe)
from xenova_gateway.mapper_uplink import UplinkProfile, GatewayMapperUplink, frame, unframe
from xenova_gateway.config import parse


def uid(number):
    return '00000000-0000-4000-8000-' + f'{number:012x}'


def policy():
    return ClockPolicy('rc-test-1', 'oak-1', 'test-config', 'a' * 64, 'b' * 64, 2, 10, 5000, 1000000, 500000, 1000000)


def vehicle(at=1000000, sequence=1):
    return schema.VehicleSample(major=1, vehicle='rc-test-1', host_boot=uid(1), device_session=uid(2),
        odom_epoch=uid(3), sequence=sequence, source_sample=sequence, source_us=at - 100,
        pi_sample_us=at, clock_error_us=10, left_ticks=sequence, right_ticks=sequence,
        odom_pose=schema.Pose(x=.1, y=.2, yaw=.03), velocity_mps=.1,
        position_error_m=.001, heading_error_rad=.001, quality=3, calibration='a' * 64,
        gateway_instance=uid(4), pi_session=uid(5))


def reply_for(plan, raw_probe, *, offset=100000, clock_epoch=None):
    probe = decode(raw_probe, schema.MapperClockProbe)
    return schema.MapperClockReply(major=1, pi_host_boot=plan.pi_host_boot, pi_client_instance=plan.pi_client_instance,
        probe_seq=plan.probe_seq, nonce=plan.nonce, pi_send_us=probe.pi_send_us, device=plan.device,
        mapper_boot=uid(10), mapper_instance=uid(11), clock_epoch=uid(12) if clock_epoch is None else clock_epoch,
        map_epoch=uid(13), frame_epoch=uid(14), odom_epoch=uid(3), configuration='test-config',
        calibration='a' * 64, primitive='b' * 64, j2=probe.pi_send_us - offset + 100,
        j3=probe.pi_send_us - offset + 120)


def exchange(owner, now, *, offset=100000, clock_epoch=None):
    plan = owner.plan(now, uid(20))
    if plan is None:
        raise AssertionError('fixture expected a new original plan')
    wire = build_probe(plan, now + 100)
    reply = reply_for(plan, wire, offset=offset, clock_epoch=clock_epoch)
    body = candidate_body(wire, encode(reply), now + 500)
    return owner.accept(plan, body, now + 500), plan, wire


def owner():
    result = MapperClockOwner(policy(), uid(1))
    result.set_vehicle(encode(vehicle()), 1000000)
    return result


def header(kind='SOURCE_VEHICLE'):
    return dict(host_boot=uid(1), gateway_instance=uid(4), uplink_instance=uid(20), channel_epoch=uid(21),
        message_sequence='1', kind=kind, issued_pi_us='1000000', expires_pi_us='1200000', request_id=uid(30))


class MapperUplinkTests(unittest.TestCase):
    def test_two_consistent_probes_expand_offset_and_preserve_prior_trust_on_next_probe(self):
        clock = owner()
        first, _, _ = exchange(clock, 1000000)
        self.assertIsNone(first)
        lease, _, _ = exchange(clock, 1200000)
        trust = decode(lease.trust_bytes, schema.WorldTrust)
        # RTT400us, one lease1s, per-stamp error2us: pad=2*2+ceil(10*(1000400)/1e6)=15.
        self.assertEqual((trust.lower_offset_us, trust.upper_offset_us), (99885, 100295))
        self.assertEqual(trust.expires_pi_us, 2200100)
        self.assertEqual((lease.source_revision, lease.time_lease_revision), (1, 1))
        plan = clock.plan(1400000, uid(20))
        self.assertEqual(plan.prior_trust_bytes, lease.trust_bytes)
        copied = parse_plan(plan_json(plan), request_id=plan.request_id, issued_us=plan.issued_us, expires_us=plan.expires_us)
        probe = build_probe(copied, 1400100)
        self.assertEqual(encode(decode(probe, schema.MapperClockProbe).mapper_trust), lease.trust_bytes)
        renewed = clock.accept(plan, candidate_body(probe, encode(reply_for(plan, probe)), 1400500), 1400500)
        self.assertEqual((renewed.source_revision, renewed.time_lease_revision), (1, 2))
        self.assertEqual(decode(lease.trust_bytes, schema.WorldTrust).expires_pi_us, 2200100)
        self.assertIsNone(clock.trust(2400100))

    def test_original_nonce_p4_deadline_and_foreign_plan_are_not_reissued(self):
        for mutation in ('nonce', 'p4', 'deadline', 'foreign'):
            with self.subTest(mutation=mutation):
                clock = owner()
                plan = clock.plan(1000000, uid(20))
                raw = build_probe(plan, 1000100)
                reply = reply_for(plan, raw)
                p4, now = 1000500, 1000500
                if mutation == 'nonce':
                    reply.nonce = b'x' * 16
                if mutation == 'p4':
                    p4 = now + 1
                if mutation == 'deadline':
                    now = plan.expires_us
                passed = replace(plan) if mutation == 'foreign' else plan
                with self.assertRaises(ValueError):
                    clock.accept(passed, candidate_body(raw, encode(reply), p4), now)
                self.assertIsNone(clock.trust(now))
                self.assertEqual(clock.time_lease_revision, 0)
        with self.assertRaises(ValueError):
            build_probe(plan, plan.expires_us)

    def test_timeout_breaks_consecutive_warmup_and_lease_does_not_admit_stale_vehicle(self):
        clock = owner()
        self.assertIsNone(exchange(clock, 1000000)[0])
        self.assertIsNotNone(clock.plan(1200000, uid(20)))
        self.assertIsNone(exchange(clock, 1400000)[0])
        self.assertIsNone(clock.plan(1600000, uid(20)))
        clock.set_vehicle(encode(vehicle(1600000, 2)), 1600000)
        self.assertIsNotNone(exchange(clock, 1600000)[0])

    def test_same_epoch_discontinuity_is_blocked_not_normal_time_renewal(self):
        clock = owner()
        exchange(clock, 1000000)
        original = exchange(clock, 1200000)[0]
        with self.assertRaises(MapperClockError):
            exchange(clock, 1400000, offset=200000)
        self.assertIsNone(clock.trust(1400500))
        self.assertGreater(clock.source_revision, original.source_revision)
        self.assertEqual(clock.time_lease_revision, original.time_lease_revision)
        clock.set_vehicle(encode(vehicle(1600000, 2)), 1600000)
        with self.assertRaises(MapperClockError):
            exchange(clock, 1600000, offset=200000)

    def test_identity_profile_and_impossible_offset_never_create_clock_approval(self):
        for name in ('configuration', 'calibration', 'primitive', 'odom_epoch', 'mapper_instance', 'j3'):
            with self.subTest(name=name):
                clock = owner()
                plan = clock.plan(1000000, uid(20))
                raw = build_probe(plan, 1000100)
                reply = reply_for(plan, raw)
                if name == 'j3':
                    reply.j3 = reply.j2 + 50000
                else:
                    setattr(reply, name, 'not-the-approved-profile')
                with self.assertRaises(ValueError):
                    clock.accept(plan, candidate_body(raw, encode(reply), 1000500), 1000500)
                self.assertEqual(clock.time_lease_revision, 0)

    def test_candidate_actual_combined_body_and_exact_nine_field_header_caps(self):
        with self.assertRaises(ValueError):
            candidate_body(bytes(1024), bytes(1024), 1000000)
        maximum = candidate_body(bytes(1024), bytes(1012), 1000000)
        self.assertEqual(len(maximum), 2048)
        self.assertEqual(tuple(map(len, parse_candidate(maximum)[:2])), (1024, 1012))
        packet = frame(header('TRUST_CANDIDATE'), maximum)
        actual, body = unframe(packet)
        self.assertEqual(actual, header('TRUST_CANDIDATE'))
        self.assertEqual(body, maximum)
        with self.assertRaises(ValueError):
            frame(header(), bytes(1025))
        with self.assertRaises(ValueError):
            frame(dict(header(), version=1), b'x')
        with self.assertRaises(ValueError):
            unframe(struct.pack('<II', 2048, 2048) + bytes(4096))
        raw = json.dumps(header(), separators=(',', ':')).encode('utf-16-le')
        with self.assertRaises(ValueError):
            unframe(struct.pack('<II', len(raw), 1) + raw + b'x')

    def test_original_vehicle_fans_out_without_new_source_time_or_fake_approval(self):
        profile = UplinkProfile(policy(), '127.0.0.1', 1883, None, None)
        port = GatewayMapperUplink(profile, uid(1), uid(4), clock=lambda: 1000100)
        raw = encode(vehicle())
        self.assertTrue(port.offer_vehicle(raw))
        self.assertIs(port.pending['vehicle'][1], raw)
        self.assertEqual(encode(port.owner.vehicle), raw)
        self.assertFalse(port.offer_vehicle(raw))
        provider = SimpleNamespace(trust=object(), world_failure=None)
        port.tick(provider)
        self.assertIsNone(provider.trust)
        self.assertEqual(port.owner.vehicle.sequence, 1)
        self.assertEqual(port.owner.vehicle.pi_sample_us, 1000000)
        self.assertIsNone(port.process)

    def test_new_client_instance_changes_probe_context_without_resetting_vehicle_origin(self):
        clock = owner()
        exchange(clock, 1000000)
        lease = exchange(clock, 1200000)[0]
        original_vehicle = encode(clock.vehicle)
        plan = clock.plan(1400000, uid(99))
        self.assertEqual(plan.pi_client_instance, uid(99))
        self.assertEqual(plan.prior_trust_bytes, lease.trust_bytes)
        self.assertEqual(encode(clock.vehicle), original_vehicle)
        self.assertEqual(clock.source_revision, lease.source_revision)

    def test_returned_lease_checks_source_window_original_expiry_and_has_no_invented_p4(self):
        clock = owner()
        exchange(clock, 1000000)
        accepted = exchange(clock, 1200000)[0]
        plan = clock.plan(1400000, uid(20))
        wire = build_probe(plan, 1400100)
        source = dict(device='oak-1', **{key: getattr(decode(accepted.trust_bytes, schema.WorldTrust), key)
            for key in ('vehicle', 'mapper_boot', 'mapper_instance', 'clock_epoch', 'map_epoch', 'frame_epoch',
                        'odom_epoch', 'configuration', 'calibration', 'primitive')})
        returned = restore_returned_probe(wire, policy(), source, uid(1), 1300300)
        self.assertIsNone(returned.completed_pi_us)
        self.assertIsNone(returned.time_lease_revision)
        self.assertEqual(returned.trust_bytes, accepted.trust_bytes)
        self.assertEqual(returned.raw_probe_bytes, wire)
        self.assertEqual(returned.pi_host_boot, uid(1))
        self.assertEqual(returned.source, source)
        converted = returned.pi_interval_to_j((1200000, 1200010), 1300300)
        self.assertEqual(converted, (1099705, 1100125))
        with self.assertRaises(ValueError):
            returned.pi_interval_to_j((returned.pi_support_lower_us - 1, returned.pi_support_lower_us), 1300300)
        with self.assertRaises(ValueError):
            returned.pi_interval_to_j((1200000, 1200010), returned.expires_pi_us - 100295)
        with self.assertRaises(ValueError):
            restore_returned_probe(wire, policy(), dict(source, mapper_instance=uid(99)), uid(1), 1300300)
        with self.assertRaises(ValueError):
            replace(returned, expires_pi_us=returned.expires_pi_us + 1)

    def test_short_lease_high_drift_lookback_counterexample_is_covered_not_retimed(self):
        p = replace(policy(), timestamp_error_us=0, drift_ppm=1000, lease_us=250000,
                    vehicle_max_age_us=1000000, lookback_us=1000000)
        clock = MapperClockOwner(p, uid(1))
        clock.set_vehicle(encode(vehicle(700000, 1)), 1000000)
        lease = None
        for now in (1000000, 1200000):
            plan = clock.plan(now, uid(20))
            wire = build_probe(plan, now)
            reply = reply_for(plan, wire, offset=0)
            reply.j2, reply.j3 = now, now + 999
            lease = clock.accept(plan, candidate_body(wire, encode(reply), now + 1000), now + 1000)
        result = lease.pi_interval_to_j((700000, 700000), 1201000)
        self.assertLessEqual(result[0], 700300)
        self.assertGreaterEqual(result[1], 700300)
        self.assertEqual(lease.expires_pi_us, 1450000)
        self.assertEqual(lease.pi_support_lower_us, 200000)
        with self.assertRaises(ValueError):
            lease.pi_interval_to_j((199999, 199999), 1201000)

    def test_optional_profile_config_does_not_enable_mapper_from_absent_profile(self):
        self.assertIsNone(parse(['--profile', 'legacy'], env={}).mapper_uplink_profile)
        args = ['--profile', 'warm', '--vm-a', uid(40), '--vm-b', uid(41), '--sink', 'serial',
            '--vehicle-limits', '/not-opened/limits.json', '--serial-profile', 'feedback-2',
            '--feedback-timeout-ms', '100', '--odometry-config', '/not-opened/odom.json',
            '--mapper-uplink-profile', '/not-opened/j.json']
        settings = parse(args, env={})
        self.assertTrue(settings.drive_link)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse(args + ['--world-trust', '/not-opened/static.json'], env={})

    def test_stop_send_failure_still_terminates_and_observes_child_exit(self):
        profile = UplinkProfile(policy(), '127.0.0.1', 1883, None, None)
        port = GatewayMapperUplink(profile, uid(1), uid(4), clock=lambda: 1000100)
        class FakeProcess:
            exitcode = None
            alive = True
            def is_alive(self): return self.alive
            def join(self, seconds): pass
            def terminate(self): self.alive, self.exitcode = False, -15
        class FakeLink:
            pending = None
            socket = SimpleNamespace(close=lambda: None)
            def offer(self, *args): raise OSError('synthetic socket failure')
        port.process, port.link = FakeProcess(), FakeLink()
        port.close()
        self.assertEqual(port.process.exitcode, -15)
        self.assertEqual(port.counts['stop_transport_failed'], 1)
        self.assertEqual(port.counts['forced_termination'], 1)
