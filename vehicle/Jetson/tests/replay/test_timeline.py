"""재생 시간만 fake clock으로 검사한다. 카메라나 실제 대기를 사용하지 않는다."""

import unittest

from xenova_vehicle.recording.timeline import ReplayCancelled, ReplayTimeline


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now
        self.sleeps = []

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ReplayTimelineTests(unittest.TestCase):
    def timeline(self, speed=1, **options):
        clock = FakeClock()
        return clock, ReplayTimeline(speed, clock=clock.read, sleeper=clock.sleep, **options)

    def test_speed_one_replays_relative_interval_with_bounded_sleeps(self):
        clock, timeline = self.timeline()
        first = timeline.wait(7_000_000)
        second = timeline.wait(7_125_000)
        self.assertEqual(first, {"target_elapsed_us": 0, "actual_elapsed_us": 0, "lateness_us": 0})
        self.assertEqual(second["target_elapsed_us"], 125_000)
        self.assertGreaterEqual(second["actual_elapsed_us"], 125_000)
        self.assertLessEqual(second["lateness_us"], 1)
        self.assertTrue(clock.sleeps)
        self.assertTrue(all(0 < value <= .05 for value in clock.sleeps))

    def test_speed_two_halves_the_gap(self):
        clock, timeline = self.timeline(2)
        timeline.wait(50)
        timing = timeline.wait(200_050)
        self.assertEqual(timing["target_elapsed_us"], 100_000)
        self.assertGreaterEqual(timing["actual_elapsed_us"], 100_000)
        self.assertLessEqual(sum(clock.sleeps), .100002)

    def test_speed_zero_keeps_order_without_waiting_or_claiming_lateness(self):
        clock, timeline = self.timeline(0)
        timeline.wait(10)
        clock.now += .2
        timing = timeline.wait(2**64 - 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(timing["target_elapsed_us"], 0)
        self.assertEqual(timing["lateness_us"], 0)
        self.assertGreaterEqual(timing["actual_elapsed_us"], 199_999)
        with self.assertRaises(ValueError):
            timeline.wait(10)

    def test_equal_source_time_does_not_add_an_interval(self):
        clock, timeline = self.timeline()
        timeline.wait(900)
        timing = timeline.wait(900)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(timing["target_elapsed_us"], 0)

    def test_large_old_epoch_keeps_one_microsecond_difference(self):
        _, timeline = self.timeline(2)
        base = 2**64 - 10
        timeline.wait(base)
        timing = timeline.wait(base + 1)
        self.assertEqual(timing["target_elapsed_us"], 1)
        self.assertGreaterEqual(timing["actual_elapsed_us"], 1)

    def test_processing_lateness_does_not_reanchor_later_targets(self):
        clock, timeline = self.timeline()
        timeline.wait(1_000_000)
        clock.now += .25
        late = timeline.wait(1_100_000)
        self.assertEqual(late["target_elapsed_us"], 100_000)
        self.assertGreaterEqual(late["lateness_us"], 150_000)
        self.assertEqual(clock.sleeps, [])
        recovered = timeline.wait(1_300_000)
        self.assertEqual(recovered["target_elapsed_us"], 300_000)
        self.assertLessEqual(sum(clock.sleeps), .050002)
        self.assertGreaterEqual(recovered["actual_elapsed_us"], 300_000)

    def test_invalid_speed_or_timestamp_is_rejected(self):
        for speed in (float("nan"), float("inf"), -1, 17, True, "1"):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                ReplayTimeline(speed)
        for stamp in (-1, 2**64, True, 1.0, "1", None):
            _, timeline = self.timeline()
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                timeline.wait(stamp)

    def test_cancel_before_first_frame_and_while_sleeping(self):
        _, timeline = self.timeline(cancelled=lambda: True)
        with self.assertRaises(ReplayCancelled):
            timeline.wait(0)
        clock = FakeClock()
        cancelled = False

        def sleep_and_cancel(seconds):
            nonlocal cancelled
            clock.sleep(seconds)
            cancelled = True

        timeline = ReplayTimeline(clock=clock.read, sleeper=sleep_and_cancel,
                                  cancelled=lambda: cancelled)
        timeline.wait(0)
        with self.assertRaises(ReplayCancelled):
            timeline.wait(10_000_000)
        self.assertEqual(len(clock.sleeps), 1)
        self.assertEqual(clock.sleeps[0], .05)

    def test_tiny_positive_speed_still_has_bounded_cancellable_sleep(self):
        clock = FakeClock()
        timeline = ReplayTimeline(5e-324, clock=clock.read, sleeper=clock.sleep,
                                  cancelled=lambda: bool(clock.sleeps))
        timeline.wait(0)
        with self.assertRaises(ReplayCancelled):
            timeline.wait(1)
        self.assertEqual(clock.sleeps, [.05])

    def test_nonfinite_or_reversed_local_clock_is_rejected(self):
        for value in (float("nan"), float("inf"), True):
            timeline = ReplayTimeline(clock=lambda: value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                timeline.wait(0)
        clock, timeline = self.timeline()
        timeline.wait(0)
        clock.now -= 1
        with self.assertRaises(ValueError):
            timeline.wait(1)
