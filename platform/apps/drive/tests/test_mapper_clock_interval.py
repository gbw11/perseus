"""원 Probe13 lease의 양방향 구간 환산과 지원 window 경계.

offset과 승인 값은 명시 software fixture다. 실제 왕복·물리 drift 수락을
생성하지 않으며 현재 source 판정은 restore_returned_probe caller 경계다.
"""

from dataclasses import asdict
import unittest

from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import (ClockPolicy, MapperClockError, U63_MAX,
    restore_returned_probe)


def uid(n):
    return '70000000-0000-4000-8000-%012x' % n


def fixture(*, lower=99_900, upper=100_100, basis=1_000_000, now=1_050_000):
    source = dict(vehicle='rc-car', device='oak-front', mapper_boot=uid(1), mapper_instance=uid(2),
        clock_epoch=uid(3), map_epoch=uid(4), frame_epoch=uid(5), odom_epoch=uid(6),
        configuration='explicit-clock-test', calibration='a'*64, primitive='b'*64)
    policy = ClockPolicy(source['vehicle'], source['device'], source['configuration'],
        source['calibration'], source['primitive'], timestamp_error_us=2, drift_ppm=50,
        max_offset_width_us=2000, lease_us=1_000_000, vehicle_max_age_us=200_000,
        lookback_us=500_000)
    trust = schema.WorldTrust(**{key: value for key, value in source.items() if key != 'device'},
        lower_offset_us=lower, upper_offset_us=upper, expires_pi_us=basis+policy.lease_us,
        max_error_us=policy.max_offset_width_us)
    probe = schema.MapperClockProbe(major=1, vehicle=source['vehicle'], device=source['device'],
        pi_host_boot=uid(7), pi_client_instance=uid(8), probe_seq=4, nonce=b'n'*16,
        pi_send_us=basis+100_000, admission_revision=7, expected_mapper_boot=source['mapper_boot'],
        expected_mapper_instance=source['mapper_instance'], expected_clock_epoch=source['clock_epoch'])
    probe.mapper_trust.CopyFrom(trust)
    raw = encode(probe)
    lease = restore_returned_probe(raw, policy, source, probe.pi_host_boot, now)
    return lease, raw, policy, source


class MapperClockIntervalTests(unittest.TestCase):
    def test_original_offset_hull_and_roundtrip_cover_every_j_endpoint_without_new_drift(self):
        for lower, upper, now in ((99_900, 100_100, 1_050_000),
                (-100_100, -99_900, 1_250_000), (-100, 100, 1_150_000)):
            with self.subTest(offset=(lower, upper)):
                lease, raw, policy, source = fixture(lower=lower, upper=upper, now=now)
                original = (999_800, 1_000_200)
                expected = original[0]+lower, original[1]+upper
                actual = lease.j_interval_to_pi(original, now)
                self.assertEqual(actual, expected)
                back = lease.pi_interval_to_j(actual, now)
                self.assertLessEqual(back[0], original[0])
                self.assertGreaterEqual(back[1], original[1])
                self.assertEqual(back, (original[0]-(upper-lower), original[1]+(upper-lower)))
                self.assertEqual(lease.j_interval_to_pi(list(original), now), actual)
                self.assertEqual(lease.raw_probe_bytes, raw)
                self.assertEqual(lease.source, source)
                self.assertEqual(policy.drift_ppm, 50)

    def test_later_conversion_does_not_refresh_bounds_probe_or_original_expiry(self):
        lease, raw, _, _ = fixture()
        frozen = asdict(lease)
        original = (1_000_000, 1_000_010)
        first = lease.j_interval_to_pi(original, 1_050_000)
        later = lease.j_interval_to_pi(original, 1_500_000)
        self.assertEqual(first, later)
        self.assertEqual(asdict(lease), frozen)
        self.assertEqual(lease.raw_probe_bytes, raw)
        self.assertEqual(lease.expires_pi_us, 2_000_000)
        self.assertEqual(lease.pi_support_lower_us, 500_000)
        self.assertIsNone(lease.completed_pi_us)
        self.assertIsNone(lease.time_lease_revision)

    def test_support_lower_boundary_is_inclusive_and_original_expiry_is_exclusive(self):
        lease, _, _, _ = fixture()
        low = lease.pi_support_lower_us-99_900
        self.assertEqual(lease.j_interval_to_pi((low, low), 1_050_000), (500_000, 500_200))
        with self.assertRaises(MapperClockError):
            lease.j_interval_to_pi((low-1, low), 1_050_000)
        high = lease.expires_pi_us-100_100-1
        self.assertEqual(lease.j_interval_to_pi((high, high), high), (1_999_799, 1_999_999))
        with self.assertRaises(MapperClockError):
            lease.j_interval_to_pi((high, high+1), high)

    def test_actual_now_must_follow_original_probe_and_precede_original_lease_edge(self):
        lease, _, _, _ = fixture()
        first_now = lease.probe_pi_send_us-100_100
        interval = (999_800, 1_000_200)
        lease.j_interval_to_pi(interval, first_now)
        with self.assertRaises(MapperClockError):
            lease.j_interval_to_pi(interval, first_now-1)
        last_now = lease.expires_pi_us-100_100
        lease.j_interval_to_pi(interval, last_now-1)
        for now in (last_now, last_now+1):
            with self.subTest(now=now), self.assertRaises(MapperClockError):
                lease.j_interval_to_pi(interval, now)

    def test_interval_and_now_require_exact_ordered_bounded_integers(self):
        class IntegerSubclass(int):
            pass
        lease, _, _, _ = fixture()
        for interval in (None, 1, (), (1,), (1, 2, 3), (1_000_001, 1_000_000),
                (True, 1_000_000), (1_000_000, False), (999_999.0, 1_000_000),
                (IntegerSubclass(999_999), 1_000_000), (-1, 1_000_000),
                (1_000_000, U63_MAX+1), iter((999_999, 1_000_000))):
            with self.subTest(interval_type=type(interval).__name__), self.assertRaises(MapperClockError):
                lease.j_interval_to_pi(interval, 1_050_000)
        for now in (True, 1_050_000.0, IntegerSubclass(1_050_000), -1, U63_MAX+1):
            with self.subTest(now=repr(now)), self.assertRaises(MapperClockError):
                lease.j_interval_to_pi((999_999, 1_000_000), now)

    def test_negative_pi_result_or_unrepresentable_roundtrip_is_rejected(self):
        lease, _, _, _ = fixture(lower=-100_100, upper=-99_900, now=1_250_000)
        with self.assertRaises(MapperClockError):
            lease.j_interval_to_pi((100_000, 100_001), 1_250_000)
        # Pi hull는 양수여도 원 pi_interval_to_j의 J lower가 음수인 경우다.
        edge, _, _, _ = fixture(basis=100, now=1)
        with self.assertRaises(MapperClockError):
            edge.j_interval_to_pi((0, 0), 1)
        self.assertEqual(edge.expires_pi_us, 1_000_100)

    def test_pi_overflow_and_reverse_j_overflow_are_rejected_without_wrapping(self):
        positive, _, _, _ = fixture(basis=U63_MAX-1_000_000, now=U63_MAX-500_000)
        with self.assertRaises(MapperClockError):
            positive.j_interval_to_pi((U63_MAX, U63_MAX), U63_MAX-500_000)
        negative, _, _, _ = fixture(lower=-100_100, upper=-99_900,
            basis=U63_MAX-1_000_000, now=U63_MAX-100_000)
        with self.assertRaises(MapperClockError):
            negative.j_interval_to_pi((U63_MAX-100, U63_MAX-100), U63_MAX-100_000)
        self.assertEqual(positive.expires_pi_us, U63_MAX)
        self.assertEqual(negative.expires_pi_us, U63_MAX)

    def test_source_replacement_is_rejected_at_original_returned_probe_admission(self):
        lease, raw, policy, source = fixture()
        for name in source:
            expected = dict(source)
            expected[name] = ('e'*64 if name in ('calibration', 'primitive') else
                'foreign' if name in ('vehicle', 'device', 'configuration') else uid(99))
            with self.subTest(source_field=name), self.assertRaises(MapperClockError):
                restore_returned_probe(raw, policy, expected, uid(7), 1_050_000)
        with self.assertRaises(MapperClockError):
            restore_returned_probe(raw, policy, source, uid(99), 1_050_000)
        exposed = lease.source
        exposed['mapper_instance'] = uid(99)
        self.assertEqual(lease.source, source)
        self.assertEqual(decode(raw, schema.MapperClockProbe).mapper_trust.expires_pi_us, lease.expires_pi_us)
        self.assertEqual(lease.j_interval_to_pi((1_000_000, 1_000_010), 1_050_000),
            (1_099_900, 1_100_110))


if __name__ == '__main__':
    unittest.main()
