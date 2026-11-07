"""설계 사건 순서와 원 기한 경계를 검사한다. 합성 fact이며 worker·IPC·실종료 시험이 아니다."""

from dataclasses import FrozenInstanceError
import unittest

from xenova_vehicle.runtime.lifecycle_policy import (
    AbsoluteDeadline, AStageWatch, BindWatch, CapturePairEvidence, CaptureScope, CaptureWarmup,
    LocalSetupWatch, SessionExitEvidence, SourceStamp, active_progress_expired, validate_state,
)


SECOND = 1_000_000
MAX_U64 = 2**64 - 1
A_WORKER = "00000000-0000-4000-8000-000000000001"
M_WORKER = "00000000-0000-4000-8000-000000000006"
D_WORKER = "00000000-0000-4000-8000-000000000007"
REQUEST = "00000000-0000-4000-8000-000000000008"
OTHER = "00000000-0000-4000-8000-000000000009"
BINDING = "a" * 64


def scope(binding=BINDING):
    return CaptureScope(binding, A_WORKER,
                        "00000000-0000-4000-8000-000000000002",
                        "00000000-0000-4000-8000-000000000003",
                        "00000000-0000-4000-8000-000000000004",
                        "00000000-0000-4000-8000-000000000005")


def bind(*, issued=100, now=None, cap=None):
    return BindWatch("M", M_WORKER, BINDING, REQUEST, issued,
                     now_us=issued if now is None else now, cap_us=cap)


def a_at(state, *, attempt=100):
    watch = AStageWatch(scope(), attempt, now_us=attempt)
    for index, step in enumerate(("ASSET_CHECK", "DEVICE_BOOT", "PIPELINE_START", "WARMUP"), 1):
        watch.enter(step, attempt + index, now_us=attempt + index)
        if step == state:
            return watch
    raise AssertionError("unsupported test setup state")


def ready_capture(watch, *, deadline=None):
    """현재 WARMUP 사건 뒤 새30개를 공급하는 합성 producer fact다. 실기 증거가 아니다."""
    deadline = watch.deadline if deadline is None else deadline
    start = deadline.started_us
    capture = CaptureWarmup(watch.scope, start, 1000, deadline, now_us=start)
    for number in range(1, 31):
        rgb = SourceStamp(watch.scope.rgb_stream, number, 500 + number, 1000 + number, start + number)
        depth = SourceStamp(watch.scope.depth_stream, 100 + number, 700 + number, 1000 + number, start + number)
        proof = CapturePairEvidence(watch.scope, rgb, depth, start + number, True)
        capture.observe(proof, now_us=start + number)
    return capture


class AbsoluteDeadlineTests(unittest.TestCase):
    def test_half_open_boundaries_use_original_event_not_delivery(self):
        event = 1_000_000_000_000
        deadline = AbsoluteDeadline.after(event, 15 * SECOND, now_us=event + 4 * SECOND)
        self.assertEqual(deadline.started_us, event)
        self.assertEqual(deadline.expires_us, event + 15 * SECOND)
        self.assertFalse(deadline.expired(event + 15 * SECOND - 1))
        self.assertTrue(deadline.expired(event + 15 * SECOND))
        self.assertTrue(deadline.expired(event + 15 * SECOND + 1))

    def test_shorter_cap_is_preserved_and_larger_cap_does_not_extend(self):
        short = AbsoluteDeadline.after(100, 1000, now_us=200, cap_us=500)
        long = AbsoluteDeadline.after(100, 1000, now_us=200, cap_us=5000)
        self.assertEqual(short.expires_us, 500)
        self.assertEqual(long.expires_us, 1100)
        self.assertFalse(short.expired(499))
        self.assertTrue(short.expired(500))
        expired_cap = AbsoluteDeadline.after(100, 1000, now_us=100, cap_us=99)
        self.assertTrue(expired_cap.expired(100))

    def test_no_wrap_and_exact_u64_endpoint(self):
        with self.assertRaises(ValueError):
            AbsoluteDeadline.after(MAX_U64 - 1, 2, now_us=MAX_U64 - 1)
        deadline = AbsoluteDeadline.after(MAX_U64 - 5, 5, now_us=MAX_U64 - 5)
        self.assertFalse(deadline.expired(MAX_U64 - 1))
        self.assertTrue(deadline.expired(MAX_U64))

    def test_effective_parent_cap_is_applied_before_rejecting_an_intermediate_sum(self):
        for event, duration, cap in ((MAX_U64 - 1, 2, MAX_U64),
                                     (MAX_U64 - 100, MAX_U64, MAX_U64 - 50),
                                     (MAX_U64, MAX_U64, MAX_U64)):
            with self.subTest(event=event, duration=duration, cap=cap):
                deadline = AbsoluteDeadline.after(event, duration, now_us=event, cap_us=cap)
                self.assertEqual(deadline.started_us, event)
                self.assertEqual(deadline.expires_us, cap)
                self.assertTrue(deadline.expired(cap))
                if cap > event:
                    self.assertFalse(deadline.expired(cap - 1))

    def test_a_valid_cap_does_not_sanitize_invalid_input_values_or_uncapped_overflow(self):
        for event, duration, cap in ((MAX_U64 + 1, 1, MAX_U64),
                                     (MAX_U64 - 1, MAX_U64 + 1, MAX_U64),
                                     (MAX_U64 - 1, True, MAX_U64),
                                     (MAX_U64 - 1, 2, MAX_U64 + 1),
                                     (MAX_U64 - 1, 2, None)):
            with self.subTest(event=event, duration=duration, cap=cap), self.assertRaises(ValueError):
                AbsoluteDeadline.after(event, duration, now_us=event, cap_us=cap)

    def test_invalid_time_types_and_future_events_are_rejected(self):
        for value in (True, False, 1.0, -1, MAX_U64 + 1, "100", None):
            for field in ("event", "duration", "now", "cap"):
                if field == "cap" and value is None:
                    continue  # None은 별도 상위 cap이 없다는 명시적인 API 값이다.
                with self.subTest(value=value, field=field), self.assertRaises(ValueError):
                    AbsoluteDeadline.after(value if field == "event" else 100,
                        value if field == "duration" else 10,
                        now_us=value if field == "now" else 100,
                        cap_us=value if field == "cap" else 1000)
        with self.assertRaises(ValueError):
            AbsoluteDeadline.after(101, 1, now_us=100)
        with self.assertRaises(ValueError):
            AbsoluteDeadline.after(100, 1, now_us=100).expired(99)

    def test_expired_delivery_and_zero_duration_cannot_gain_new_time(self):
        late = AbsoluteDeadline.after(100, 10, now_us=1000)
        self.assertEqual(late.expires_us, 110)
        self.assertTrue(late.expired(1000))
        self.assertTrue(AbsoluteDeadline.after(100, 0, now_us=100).expired(100))
        with self.assertRaises(FrozenInstanceError):
            late.expires_us = 2000


class LocalSetupDeadlineTests(unittest.TestCase):
    def test_m_and_d_setup_deadline_has_exact_fifteen_second_boundary(self):
        for role, worker in (("M", M_WORKER), ("D", D_WORKER)):
            with self.subTest(role=role):
                watch = LocalSetupWatch(role, worker, 100, now_us=100)
                self.assertEqual(watch.deadline.expires_us, 100 + 15 * SECOND)
                self.assertFalse(watch.poll(100 + 15 * SECOND - 1))
                self.assertTrue(watch.poll(100 + 15 * SECOND))
                self.assertFalse(watch.hello(worker, 100 + 15 * SECOND - 1,
                                             now_us=100 + 15 * SECOND + 1))
                self.assertTrue(watch.failed)

    def test_hello_finishes_local_timer_before_unbounded_external_wait(self):
        for role, worker in (("M", M_WORKER), ("D", D_WORKER)):
            with self.subTest(role=role):
                watch = LocalSetupWatch(role, worker, 100, now_us=100)
                event = 100 + 15 * SECOND - 1
                self.assertTrue(watch.hello(worker, event, now_us=event))
                self.assertFalse(watch.poll(100 + 600 * SECOND))
                self.assertFalse(watch.hello(worker, event, now_us=100 + 600 * SECOND))
                self.assertEqual(watch.hello_us, event)
                self.assertEqual(watch.deadline.expires_us, 100 + 15 * SECOND)

    def test_foreign_or_prespawn_hello_does_not_finish_or_retime_setup(self):
        watch = LocalSetupWatch("M", M_WORKER, 100, now_us=100)
        expiry = watch.deadline.expires_us
        self.assertFalse(watch.hello(OTHER, 101, now_us=101))
        self.assertFalse(watch.hello(M_WORKER, 99, now_us=101))
        self.assertIsNone(watch.hello_us)
        self.assertEqual(watch.deadline.expires_us, expiry)
        self.assertTrue(watch.hello(M_WORKER, 102, now_us=103))
        self.assertEqual(watch.hello_us, 102)

    def test_local_setup_does_not_share_an_earlier_a_cold_deadline(self):
        watch = LocalSetupWatch("M", M_WORKER, 200 * SECOND, now_us=200 * SECOND)
        self.assertTrue(watch.hello(M_WORKER, 201 * SECOND, now_us=201 * SECOND))
        self.assertFalse(watch.poll(1000 * SECOND))

    def test_bad_role_worker_and_future_hello_are_rejected(self):
        for role in ("A", "S", "m", None):
            with self.subTest(role=role), self.assertRaises(ValueError):
                LocalSetupWatch(role, M_WORKER, 100, now_us=100)
        with self.assertRaises(ValueError):
            LocalSetupWatch("M", "not-a-uuid", 100, now_us=100)
        watch = LocalSetupWatch("M", M_WORKER, 100, now_us=100)
        with self.assertRaises(ValueError):
            watch.hello(M_WORKER, 102, now_us=101)


class BindDeadlineTests(unittest.TestCase):
    def test_eagain_then_send_at_four_seconds_keeps_original_five_second_cap(self):
        watch = bind()
        expiry = 100 + 5 * SECOND
        self.assertFalse(watch.poll(100 + 4 * SECOND))  # EAGAIN에서는 sent를 호출하지 않는다.
        self.assertIsNone(watch.sent_us)
        self.assertTrue(watch.sent(100 + 4 * SECOND, now_us=100 + 4 * SECOND))
        self.assertEqual(watch.issued_us, 100)
        self.assertEqual(watch.deadline.started_us, 100)
        self.assertEqual(watch.deadline.expires_us, expiry)
        self.assertTrue(watch.ack(M_WORKER, BINDING, REQUEST, expiry - 1, now_us=expiry - 1))
        self.assertFalse(watch.poll(expiry + 100 * SECOND))

    def test_unsent_request_expires_and_cannot_be_revived_by_send_or_ack(self):
        watch = bind()
        expiry = 100 + 5 * SECOND
        self.assertFalse(watch.poll(expiry - 1))
        self.assertTrue(watch.poll(expiry))
        self.assertFalse(watch.sent(expiry, now_us=expiry))
        self.assertFalse(watch.ack(M_WORKER, BINDING, REQUEST, expiry, now_us=expiry))
        self.assertIsNone(watch.ack_us)

    def test_send_near_u64_limit_uses_the_representable_original_issue_cap(self):
        issued = MAX_U64 - 5 * SECOND
        watch = bind(issued=issued)
        self.assertEqual(watch.deadline.expires_us, MAX_U64)
        self.assertTrue(watch.sent(issued + 1, now_us=issued + 1))
        self.assertEqual(watch.deadline.started_us, issued)
        self.assertEqual(watch.deadline.expires_us, MAX_U64)
        self.assertTrue(watch.ack(M_WORKER, BINDING, REQUEST, MAX_U64 - 1, now_us=MAX_U64 - 1))
        self.assertFalse(watch.poll(MAX_U64))
        expired = bind(issued=issued)
        self.assertFalse(expired.sent(MAX_U64, now_us=MAX_U64))
        self.assertTrue(expired.failed)

    def test_duplicate_send_and_ack_do_not_renew_request(self):
        watch = bind()
        self.assertTrue(watch.sent(101, now_us=101))
        original = watch.deadline
        self.assertFalse(watch.sent(100 + 4 * SECOND, now_us=100 + 4 * SECOND))
        self.assertEqual(watch.deadline, original)
        self.assertTrue(watch.ack(M_WORKER, BINDING, REQUEST, 102, now_us=103))
        self.assertFalse(watch.ack(M_WORKER, BINDING, REQUEST, 102, now_us=100 + 99 * SECOND))
        self.assertEqual(watch.ack_us, 102)
        self.assertEqual(watch.deadline.expires_us, 100 + 5 * SECOND)

    def test_foreign_worker_binding_request_and_ack_before_send_are_not_success(self):
        watch = bind()
        self.assertFalse(watch.ack(M_WORKER, BINDING, REQUEST, 101, now_us=101))
        self.assertTrue(watch.sent(200, now_us=200))
        for worker, digest, request in ((OTHER, BINDING, REQUEST),
                                         (M_WORKER, "b" * 64, REQUEST), (M_WORKER, BINDING, OTHER)):
            with self.subTest(worker=worker, request=request, binding=digest):
                self.assertFalse(watch.ack(worker, digest, request, 201, now_us=201))
        self.assertFalse(watch.ack(M_WORKER, BINDING, REQUEST, 199, now_us=201))
        self.assertIsNone(watch.ack_us)
        self.assertTrue(watch.ack(M_WORKER, BINDING, REQUEST, 202, now_us=203))

    def test_late_delivered_ack_and_shorter_job_deadline_are_not_retimed(self):
        watch = bind(cap=100 + 2 * SECOND)
        self.assertTrue(watch.sent(100 + SECOND, now_us=100 + SECOND))
        expiry = 100 + 2 * SECOND
        self.assertEqual(watch.deadline.expires_us, expiry)
        self.assertFalse(watch.ack(M_WORKER, BINDING, REQUEST, expiry - 1, now_us=expiry))
        self.assertTrue(watch.failed)
        fresh = bind(cap=expiry)
        self.assertTrue(fresh.sent(101, now_us=101))
        self.assertTrue(fresh.ack(M_WORKER, BINDING, REQUEST, expiry - 1, now_us=expiry - 1))

    def test_constructor_delivery_time_future_events_and_invalid_scope(self):
        watch = bind(now=100 + 4 * SECOND)
        self.assertEqual(watch.deadline.expires_us, 100 + 5 * SECOND)
        with self.assertRaises(ValueError):
            watch.sent(102, now_us=101)
        with self.assertRaises(ValueError):
            watch.ack(M_WORKER, BINDING, REQUEST, 102, now_us=101)
        for args in (("S", M_WORKER, BINDING, REQUEST), ("M", OTHER.upper(), "G" * 64, REQUEST),
                     ("M", M_WORKER, BINDING, "invalid")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                BindWatch(*args, 100, now_us=100)


class RoleAndActiveProgressTests(unittest.TestCase):
    def test_role_state_allowlists_match_the_design_without_inventing_other_graphs(self):
        expected = {
            "S": "DOWN STARTING RUNNING DEGRADED QUIESCING RECOVERING FAILED FAILED_NEEDS_SERVICE STOPPED",
            "A": "STARTING ASSET_CHECK DEVICE_BOOT PIPELINE_START WARMUP STREAMING DEGRADED DRAINING CLOSING CLOSED FAILED FAILED_CONFIG FAILED_NEEDS_SERVICE",
            "M": "STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED QUIESCING RECOVERING FAILED STOPPED",
            "D": "STARTING IDLE ACTIVE_JOB RECOVERING QUIESCING FAILED STOPPED",
        }
        for role, states in expected.items():
            for state in states.split():
                with self.subTest(role=role, state=state):
                    validate_state(role, state)
        for role, state in (("UNKNOWN", "RUNNING"), ("M", "STREAMING"), ("D", "WAIT_CLOCK"),
                            ("A", "STOPPED"), ("S", "BOGUS"), (None, "RUNNING"), ("M", True)):
            with self.subTest(role=role, state=state), self.assertRaises(ValueError):
                validate_state(role, state)

    def test_streaming_a_and_active_m_have_original_completion_half_open_gap(self):
        for role, state, active in (("A", "STREAMING", False), ("M", "RUNNING", True)):
            with self.subTest(role=role):
                self.assertFalse(active_progress_expired(role, state, 100, now_us=500099, active_job=active))
                self.assertTrue(active_progress_expired(role, state, 100, now_us=500100, active_job=active))
                self.assertTrue(active_progress_expired(role, state, 100, now_us=500101, active_job=active))

    def test_normal_wait_boot_and_idle_do_not_become_five_hundred_ms_hangs(self):
        for role, states in (("M", ("WAIT_INPUT", "WAIT_CLOCK", "WAIT_ODOM", "WAIT_STORAGE")),
                             ("A", ("ASSET_CHECK", "DEVICE_BOOT", "PIPELINE_START", "WARMUP")),
                             ("D", ("IDLE",)), ("S", ("RUNNING",))):
            for state in states:
                with self.subTest(role=role, state=state):
                    self.assertFalse(active_progress_expired(role, state, 0, now_us=600 * SECOND))
        self.assertFalse(active_progress_expired("M", "RUNNING", 0, now_us=600 * SECOND))

    def test_parser_only_work_in_wait_state_is_an_active_m_job(self):
        for state in ("WAIT_CLOCK", "WAIT_ODOM", "WAIT_STORAGE"):
            with self.subTest(state=state):
                self.assertFalse(active_progress_expired("M", state, 100, now_us=500099, active_job=True))
                self.assertTrue(active_progress_expired("M", state, 100, now_us=500100, active_job=True))
                self.assertFalse(active_progress_expired("M", state, 100, now_us=500100, active_job=False))

    def test_active_progress_time_and_activity_flag_are_strict(self):
        with self.assertRaises(ValueError):
            active_progress_expired("A", "STREAMING", 101, now_us=100)
        for value in (True, 1.0, -1, MAX_U64 + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                active_progress_expired("M", "RUNNING", value, now_us=100, active_job=True)
        with self.assertRaises(ValueError):
            active_progress_expired("M", "RUNNING", 0, now_us=100, active_job=1)


class AStageDeadlineTests(unittest.TestCase):
    def test_cold_attempt_exact_ninety_seconds_and_late_stage_cannot_reopen(self):
        watch = AStageWatch(scope(), 100, now_us=100)
        expiry = 100 + 90 * SECOND
        self.assertFalse(watch.poll(expiry - 1))
        self.assertTrue(watch.poll(expiry))
        self.assertEqual(watch.state, "FAILED")
        self.assertFalse(watch.enter("ASSET_CHECK", expiry + 1, now_us=expiry + 1))
        self.assertEqual(watch.global_deadline.expires_us, expiry)

    def test_each_explicit_stage_cap_has_plus_minus_one_us_boundary(self):
        for state, duration, next_state in (("ASSET_CHECK", 15, "DEVICE_BOOT"),
                                           ("DEVICE_BOOT", 30, "PIPELINE_START"),
                                           ("PIPELINE_START", 30, "WARMUP"), ("WARMUP", 15, "STREAMING")):
            with self.subTest(state=state):
                watch = a_at(state)
                expiry = watch.entered_us + duration * SECOND
                self.assertEqual(watch.deadline.expires_us, expiry)
                self.assertFalse(watch.poll(expiry - 1))
                self.assertTrue(watch.poll(expiry))
                self.assertFalse(watch.enter(next_state, expiry - 1, now_us=expiry + 1))
                self.assertTrue(watch.failed)

    def test_global_deadline_wins_over_a_late_warmup_stage(self):
        start = 100
        watch = AStageWatch(scope(), start, now_us=start)
        for state, delta in (("ASSET_CHECK", 14), ("DEVICE_BOOT", 28),
                              ("PIPELINE_START", 57), ("WARMUP", 86)):
            event = start + delta * SECOND
            self.assertTrue(watch.enter(state, event, now_us=event))
        self.assertEqual(watch.deadline.started_us, start + 86 * SECOND)
        self.assertEqual(watch.deadline.expires_us, start + 90 * SECOND)
        self.assertFalse(watch.poll(start + 90 * SECOND - 1))
        self.assertTrue(watch.poll(start + 90 * SECOND))

    def test_repeated_stage_event_and_later_delivery_do_not_extend_the_stage(self):
        watch = a_at("ASSET_CHECK")
        original = watch.deadline
        self.assertFalse(watch.enter("ASSET_CHECK", 200, now_us=100 + 14 * SECOND))
        self.assertEqual(watch.deadline, original)
        self.assertTrue(watch.enter("DEVICE_BOOT", 100 + 10 * SECOND, now_us=100 + 11 * SECOND))
        self.assertEqual(watch.deadline.expires_us, 100 + 40 * SECOND)
        self.assertEqual(watch.global_deadline.expires_us, 100 + 90 * SECOND)

    def test_target_stage_already_expired_at_delivery_is_not_started(self):
        watch = AStageWatch(scope(), 100, now_us=100)
        self.assertFalse(watch.enter("ASSET_CHECK", 101, now_us=101 + 15 * SECOND))
        self.assertEqual(watch.state, "FAILED")
        self.assertTrue(watch.failed)
        self.assertFalse(watch.enter("DEVICE_BOOT", 102, now_us=102 + 15 * SECOND))

    def test_skipped_future_unknown_and_reversed_stage_events_are_rejected(self):
        watch = AStageWatch(scope(), 100, now_us=100)
        with self.assertRaises(ValueError):
            watch.enter("WARMUP", 101, now_us=101)
        with self.assertRaises(ValueError):
            watch.enter("UNKNOWN", 101, now_us=101)
        with self.assertRaises(ValueError):
            watch.enter("ASSET_CHECK", 102, now_us=101)
        self.assertTrue(watch.enter("ASSET_CHECK", 200, now_us=200))
        self.assertFalse(watch.enter("DEVICE_BOOT", 199, now_us=201))
        with self.assertRaises(ValueError):
            watch.enter("PIPELINE_START", 201, now_us=201)
        self.assertEqual(watch.state, "ASSET_CHECK")

    def test_failed_config_does_not_automatically_start_a_new_attempt(self):
        watch = a_at("ASSET_CHECK")
        self.assertTrue(watch.enter("FAILED_CONFIG", 200, now_us=200))
        self.assertFalse(watch.enter("ASSET_CHECK", 201, now_us=201))
        self.assertEqual(watch.state, "FAILED_CONFIG")
        self.assertTrue(watch.failed)

    def test_current_capture_proof_finishes_local_startup_before_external_wait(self):
        watch = a_at("WARMUP")
        with self.assertRaises(ValueError):
            watch.enter("STREAMING", watch.entered_us + 1, now_us=watch.entered_us + 1)
        capture = ready_capture(watch)
        self.assertTrue(capture.ready)
        self.assertTrue(watch.enter("STREAMING", capture.ready_us, now_us=capture.ready_us, capture=capture))
        self.assertTrue(watch.startup_completed)
        self.assertIsNone(watch.deadline)
        self.assertFalse(watch.poll(100 + 600 * SECOND))
        self.assertEqual(watch.state, "STREAMING")

    def test_same_scope_ready_proof_cannot_replace_the_current_warmup_origin_or_cap(self):
        for start_delta, expiry_delta in ((-1, 0), (1, 0), (0, 1)):
            with self.subTest(start_delta=start_delta, expiry_delta=expiry_delta):
                if expiry_delta:
                    watch = AStageWatch(scope(), 100, now_us=100)
                    for state, delta in (("ASSET_CHECK", 14), ("DEVICE_BOOT", 28),
                                          ("PIPELINE_START", 57), ("WARMUP", 86)):
                        event = 100 + delta * SECOND
                        watch.enter(state, event, now_us=event)
                else:
                    watch = a_at("WARMUP")
                original = watch.deadline
                different = AbsoluteDeadline(original.started_us + start_delta,
                                             original.expires_us + expiry_delta)
                foreign_stage = ready_capture(watch, deadline=different)
                self.assertEqual(foreign_stage.scope, watch.scope)
                self.assertTrue(foreign_stage.ready)
                if expiry_delta:
                    self.assertGreater(foreign_stage.deadline.expires_us, original.expires_us)
                event = max(watch.entered_us, foreign_stage.ready_us) + 1
                with self.assertRaises(ValueError):
                    watch.enter("STREAMING", event, now_us=event, capture=foreign_stage)
                self.assertEqual(watch.state, "WARMUP")
                self.assertEqual(watch.deadline, original)
                self.assertEqual(watch.warmup_deadline, original)
                self.assertFalse(watch.startup_completed)
                current = ready_capture(watch)
                self.assertTrue(watch.enter("STREAMING", event + 1, now_us=event + 1, capture=current))

    def test_unready_degraded_keeps_the_original_warmup_deadline_and_allows_cleanup(self):
        watch = a_at("WARMUP")
        original = watch.deadline
        capture = ready_capture(watch)
        event = original.started_us + SECOND
        self.assertTrue(watch.enter("DEGRADED", event, now_us=event))
        self.assertFalse(watch.startup_completed)
        self.assertEqual(watch.deadline, original)
        self.assertEqual(watch.warmup_deadline, original)
        self.assertFalse(watch.poll(original.expires_us - 1))
        self.assertTrue(watch.poll(original.expires_us))
        self.assertEqual(watch.state, "FAILED")
        self.assertFalse(watch.enter("STREAMING", original.expires_us + 1,
                                     now_us=original.expires_us + 1, capture=capture))
        self.assertTrue(watch.enter("DRAINING", original.expires_us + 100, now_us=original.expires_us + 100))
        self.assertTrue(watch.enter("CLOSING", original.expires_us + 200, now_us=original.expires_us + 200))
        self.assertTrue(watch.enter("CLOSED", original.expires_us + 500, now_us=original.expires_us + 500,
            exit_evidence=SessionExitEvidence(scope(), original.expires_us + 300, original.expires_us + 400)))
        self.assertTrue(watch.failed)
        self.assertFalse(watch.startup_completed)
        self.assertEqual(watch.state, "CLOSED")

    def test_ready_streaming_degraded_recovery_does_not_reapply_cold_or_warmup_time(self):
        for shorter in (False, True):
            with self.subTest(shorter_capture_cap=shorter):
                watch = a_at("WARMUP")
                original = watch.deadline
                proof_deadline = (AbsoluteDeadline(original.started_us, original.started_us + 5 * SECOND)
                                  if shorter else original)
                capture = ready_capture(watch, deadline=proof_deadline)
                self.assertTrue(watch.enter("STREAMING", capture.ready_us, now_us=capture.ready_us, capture=capture))
                event = watch.global_deadline.expires_us + 100 * SECOND
                self.assertTrue(watch.enter("DEGRADED", event, now_us=event))
                self.assertIsNone(watch.deadline)
                self.assertEqual(watch.warmup_deadline, original)
                self.assertTrue(watch.enter("STREAMING", event + 1, now_us=event + 1, capture=capture))
                self.assertTrue(watch.startup_completed)
                self.assertIsNone(watch.deadline)
                self.assertFalse(watch.poll(event + 1000 * SECOND))

    def test_first_streaming_adoption_honors_the_shorter_capture_deadline(self):
        for delta in (5 * SECOND - 1, 5 * SECOND, 5 * SECOND + 1, 6 * SECOND):
            with self.subTest(delivery_offset_us=delta):
                watch = a_at("WARMUP")
                start = watch.entered_us
                shorter = AbsoluteDeadline(start, start + 5 * SECOND)
                capture = ready_capture(watch, deadline=shorter)
                self.assertTrue(capture.ready)
                accepted = watch.enter("STREAMING", capture.ready_us, now_us=start + delta, capture=capture)
                self.assertEqual(accepted, delta < 5 * SECOND)
                if delta >= 5 * SECOND:
                    self.assertEqual(watch.state, "FAILED")
                    self.assertTrue(watch.failed)
                    self.assertFalse(watch.startup_completed)
                    self.assertFalse(watch.enter("STREAMING", start + delta + 1,
                        now_us=start + delta + 1, capture=capture))

    def test_current_origin_with_a_narrower_capture_cap_and_normal_cleanup_is_valid(self):
        watch = a_at("WARMUP")
        original = watch.deadline
        shorter = AbsoluteDeadline(original.started_us, original.expires_us - 1000)
        capture = ready_capture(watch, deadline=shorter)
        event = capture.ready_us
        self.assertTrue(watch.enter("STREAMING", event, now_us=event, capture=capture))
        self.assertEqual(watch.warmup_deadline, original)
        self.assertTrue(watch.enter("DRAINING", event + 1, now_us=event + 1))
        self.assertTrue(watch.enter("CLOSING", event + 2, now_us=event + 2))
        self.assertTrue(watch.enter("CLOSED", event + 5, now_us=event + 5,
                                    exit_evidence=SessionExitEvidence(scope(), event + 3, event + 4)))
        self.assertEqual(watch.state, "CLOSED")
        self.assertFalse(watch.failed)

    def test_capture_fifteen_second_clamp_preserves_a_parent_cap_near_u64_limit(self):
        start = MAX_U64 - 5 * SECOND
        parent = AbsoluteDeadline(start, MAX_U64)
        capture = CaptureWarmup(scope(), start, 1000, parent, now_us=start)
        self.assertEqual(capture.deadline.started_us, start)
        self.assertEqual(capture.deadline.expires_us, MAX_U64)
        self.assertFalse(capture.poll(MAX_U64 - 1))
        self.assertTrue(capture.poll(MAX_U64))
        self.assertFalse(capture.ready)
        with self.assertRaises(ValueError):
            CaptureWarmup(scope(), start, MAX_U64 + 1, parent, now_us=start)

    def test_closed_requires_current_session_and_actual_exit_evidence(self):
        watch = a_at("ASSET_CHECK")
        self.assertTrue(watch.enter("DRAINING", 200, now_us=200))
        self.assertTrue(watch.enter("CLOSING", 300, now_us=300))
        for evidence in (None, SessionExitEvidence(scope("b" * 64), 400, 500),
                         SessionExitEvidence(scope(), 99, 500), SessionExitEvidence(scope(), 0, 0),
                         SessionExitEvidence(scope(), 500, 400),
                         SessionExitEvidence(scope(), 601, 500), SessionExitEvidence(scope(), 400, 601)):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                watch.enter("CLOSED", 600, now_us=600, exit_evidence=evidence)
        self.assertEqual(watch.state, "CLOSING")
        self.assertTrue(watch.enter("CLOSED", 600, now_us=600,
                                    exit_evidence=SessionExitEvidence(scope(), 400, 500)))
        self.assertEqual(watch.state, "CLOSED")

    def test_expired_attempt_still_accepts_exact_cleanup_without_reopening_startup(self):
        watch = a_at("ASSET_CHECK")
        expiry = watch.deadline.expires_us
        self.assertTrue(watch.poll(expiry))
        self.assertTrue(watch.enter("DRAINING", expiry + 100, now_us=expiry + 100))
        self.assertTrue(watch.enter("CLOSING", expiry + 200, now_us=expiry + 200))
        self.assertTrue(watch.enter("CLOSED", expiry + 500, now_us=expiry + 500,
                                    exit_evidence=SessionExitEvidence(scope(), expiry + 300, expiry + 400)))
        self.assertEqual(watch.state, "CLOSED")
        self.assertIsNone(watch.deadline)
        self.assertTrue(watch.failed)
        self.assertFalse(watch.startup_completed)

    def test_cleanup_records_expiry_identically_with_or_without_an_explicit_poll(self):
        for offset in (-1, 0, 1):
            outcomes = []
            for poll_first in (False, True):
                with self.subTest(expiry_offset_us=offset, poll_first=poll_first):
                    watch = a_at("ASSET_CHECK")
                    event = watch.deadline.expires_us + offset
                    if poll_first:
                        self.assertEqual(watch.poll(event), offset >= 0)
                    self.assertTrue(watch.enter("DRAINING", event, now_us=event))
                    self.assertEqual(watch.failed, offset >= 0)
                    self.assertTrue(watch.enter("CLOSING", event + 1, now_us=event + 1))
                    evidence = SessionExitEvidence(scope(), event + 2, event + 3)
                    self.assertTrue(watch.enter("CLOSED", event + 4, now_us=event + 4, exit_evidence=evidence))
                    outcomes.append((watch.state, watch.failed, watch.startup_completed, watch.deadline))
            self.assertEqual(outcomes[0], outcomes[1])
            self.assertEqual(outcomes[0], ("CLOSED", offset >= 0, False, None))

    def test_starting_and_terminal_failures_allow_only_cleanup_without_restart(self):
        for failure in (None, "FAILED_CONFIG", "FAILED_NEEDS_SERVICE"):
            with self.subTest(failure=failure):
                watch = AStageWatch(scope(), 100, now_us=100)
                if failure is not None:
                    self.assertTrue(watch.enter(failure, 101, now_us=101))
                self.assertTrue(watch.enter("DRAINING", 200, now_us=200))
                self.assertTrue(watch.enter("CLOSING", 300, now_us=300))
                self.assertTrue(watch.enter("CLOSED", 600, now_us=600,
                    exit_evidence=SessionExitEvidence(scope(), 400, 500)))
                self.assertEqual(watch.failed, failure is not None)
                self.assertFalse(watch.startup_completed)
                if failure is not None:
                    self.assertFalse(watch.enter("ASSET_CHECK", 601, now_us=601))


if __name__ == "__main__":
    unittest.main()
