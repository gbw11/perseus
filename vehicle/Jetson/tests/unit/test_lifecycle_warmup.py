"""개별 synthetic 사실의 사건 순서·문맥·원 기한 검사. 실기 READY 증거가 아니다."""

from dataclasses import replace
import unittest

from xenova_vehicle.runtime.lifecycle_policy import (
    AbsoluteDeadline, BindWatch, CapturePairEvidence, CaptureScope, CaptureWarmup,
    ParserContext, ParserEvidence, ParserWarmup, SourceStamp,
)


def identity(number):
    return f"00000000-0000-0000-0000-{number:012d}"


def scope(number=1, *, nn=True):
    start = number * 10
    return CaptureScope(f"{number:064x}", identity(start), identity(start + 1), identity(start + 2),
                        identity(start + 3) if nn else None, identity(start + 4) if nn else None)


def capture_tracker(current=None, *, expires=15_000_000):
    current = scope() if current is None else current
    return CaptureWarmup(current, 100, 50_000, AbsoluteDeadline(0, expires), now_us=100)


def pair(current, ordinal, *, completed=None, **changes):
    received = 100 + ordinal * 100
    values = dict(scope=current,
                  rgb=SourceStamp(current.rgb_stream, 1000 + ordinal, 2000 + ordinal * 10,
                                  50_000 + ordinal * 10, received),
                  depth=SourceStamp(current.depth_stream, 9000 + ordinal * 2, 8000 + ordinal * 10,
                                    50_001 + ordinal * 10, received + 1),
                  completed_us=received + 2 if completed is None else completed,
                  checks_passed=True)
    values.update(changes)
    return CapturePairEvidence(**values)


def capture_ready(current=None):
    tracker = capture_tracker(current)
    for ordinal in range(1, 31):
        evidence = pair(tracker.scope, ordinal)
        tracker.observe(evidence, now_us=evidence.completed_us)
    return tracker


def pair_at(current, ordinal, completed_us, *, queue_us=100):
    """경계 직전 원 수신/완료를 가진 합성 사실. 서로 다른 clock을 직접 비교하지 않는다."""
    received = completed_us - 2
    sdk = 50_000 + received - queue_us
    return CapturePairEvidence(current,
        SourceStamp(current.rgb_stream, 1000 + ordinal, received + 2000, sdk, received),
        SourceStamp(current.depth_stream, 9000 + ordinal * 2, received + 3000, sdk + 1, received + 1),
        completed_us, True)


def context(current=None, *, worker=100, request=101, model="asset-v1", parser="parser-v1"):
    return ParserContext(scope() if current is None else current, identity(worker), identity(request), model, parser)


def acknowledged(current, ack_us):
    watch = BindWatch("M", current.mapper_worker, current.capture_scope.raw_binding_sha256,
                      current.bind_request_id, ack_us - 100, now_us=ack_us - 100)
    watch.sent(ack_us - 1, now_us=ack_us - 1)
    watch.ack(current.mapper_worker, current.capture_scope.raw_binding_sha256, current.bind_request_id,
              ack_us, now_us=ack_us)
    return watch


def parser_tracker(*, ack_us=5000, cap_us=None, current=None):
    current = context() if current is None else current
    tracker = ParserWarmup(current, "REQUIRED", cap_us=cap_us)
    capture = capture_ready(current.capture_scope)
    binding = acknowledged(current, ack_us)
    tracker.admit_capture(capture, now_us=max(capture.ready_us, ack_us))
    tracker.admit_binding(binding, now_us=max(capture.ready_us, ack_us))
    return tracker


def parsed(current, ordinal, stage_start=5000, **changes):
    received = stage_start + ordinal * 10
    values = dict(context=current,
                  nn_input=SourceStamp(current.capture_scope.nn_input_stream, ordinal, 100_000 + ordinal,
                                       2_000_000 + ordinal * 10, received),
                  nn_output=SourceStamp(current.capture_scope.nn_output_stream, ordinal, 100_000 + ordinal,
                                        2_000_000 + ordinal * 10, received + 1),
                  completed_us=received + 2, pairing_and_freshness_validated=True, parser_ok=True)
    values.update(changes)
    return ParserEvidence(**values)


class CaptureWarmupTests(unittest.TestCase):
    def test_loose_parent90s_does_not_extend_capture_original15s(self):
        for event_us, now_us, expected in ((14_999_999, 14_999_999, True),
                                           (14_999_999, 15_000_000, False),
                                           (15_000_000, 15_000_000, False)):
            with self.subTest(event_us=event_us, now_us=now_us):
                tracker = capture_tracker(expires=90_000_000)
                self.assertEqual((tracker.deadline.started_us, tracker.deadline.expires_us), (0, 15_000_000))
                for ordinal in range(1, 30):
                    evidence = pair_at(tracker.scope, ordinal, 100 + ordinal * 100)
                    tracker.observe(evidence, now_us=evidence.completed_us)
                final = pair_at(tracker.scope, 30, event_us)
                self.assertEqual(tracker.observe(final, now_us=now_us), expected)
                self.assertEqual(tracker.ready, expected)
                self.assertEqual(tracker.count, 30 if expected else 29)
                self.assertEqual(tracker.ready_us, event_us if expected else None)
                self.assertEqual(tracker.failed, not expected)

    def test_capture_preserves_a_shorter_parent_cap_half_open(self):
        for final_us, expected in ((3999, True), (4000, False)):
            with self.subTest(final_us=final_us):
                tracker = capture_tracker(expires=4000)
                self.assertEqual((tracker.deadline.started_us, tracker.deadline.expires_us), (0, 4000))
                for ordinal in range(1, 30):
                    evidence = pair_at(tracker.scope, ordinal, 100 + ordinal * 100)
                    tracker.observe(evidence, now_us=evidence.completed_us)
                self.assertEqual(tracker.observe(pair_at(tracker.scope, 30, final_us), now_us=final_us), expected)
                self.assertEqual(tracker.ready, expected)
                self.assertEqual(tracker.deadline.expires_us, 4000)

    def test_late_queue_setup_never_moves_original_warmup_start(self):
        current = scope()
        for queue_us in (5_000_000, 17_000_000):
            with self.subTest(queue_us=queue_us):
                tracker = CaptureWarmup(current, queue_us, 50_000,
                                        AbsoluteDeadline(1_000_000, 91_000_000), now_us=queue_us)
                self.assertEqual((tracker.deadline.started_us, tracker.deadline.expires_us),
                                 (1_000_000, 16_000_000))
                if queue_us < 16_000_000:
                    for ordinal in range(1, 30):
                        evidence = pair_at(current, ordinal, queue_us + ordinal * 100, queue_us=queue_us)
                        tracker.observe(evidence, now_us=evidence.completed_us)
                    final = pair_at(current, 30, 15_999_999, queue_us=queue_us)
                    self.assertTrue(tracker.observe(final, now_us=15_999_999))
                    self.assertEqual(tracker.ready_us, 15_999_999)
                else:
                    evidence = pair_at(current, 1, queue_us + 100, queue_us=queue_us)
                    self.assertFalse(tracker.observe(evidence, now_us=evidence.completed_us))
                    self.assertTrue(tracker.failed)
                    self.assertFalse(tracker.ready)

    def test_only_thirtieth_distinct_pair_is_ready_without_external_dependencies(self):
        tracker = capture_tracker()
        for ordinal in range(1, 30):
            evidence = pair(tracker.scope, ordinal)
            self.assertTrue(tracker.observe(evidence, now_us=evidence.completed_us))
        self.assertEqual(tracker.count, 29)
        self.assertFalse(tracker.ready)
        final = pair(tracker.scope, 30)
        self.assertNotEqual(final.rgb.sequence, final.depth.sequence)
        self.assertTrue(tracker.observe(final, now_us=final.completed_us))
        self.assertEqual((tracker.count, tracker.ready_us), (30, final.completed_us))
        self.assertTrue(tracker.ready)
        self.assertIsNone(tracker.__dict__.get("payload"))
        self.assertFalse(hasattr(tracker, "metric_ready") or hasattr(tracker, "world_valid"))

    def test_duplicate_old_prequeue_failed_replay_and_foreign_do_not_count(self):
        tracker = capture_tracker()
        first = pair(tracker.scope, 1)
        tracker.observe(first, now_us=first.completed_us)
        old = pair(tracker.scope, 0)
        prequeue_sdk = replace(first, rgb=replace(first.rgb, sdk_us=49_999))
        foreign = pair(scope(2), 10)
        variants = (first, old, prequeue_sdk, foreign, replace(first, checks_passed=False),
                    replace(first, input_kind="REPLAY"), replace(first, rgb=replace(first.rgb, stream_instance=identity(999))))
        for evidence in variants:
            with self.subTest(evidence=evidence):
                self.assertFalse(tracker.observe(evidence, now_us=2000))
                self.assertEqual(tracker.count, 1)
                self.assertFalse(tracker.failed or tracker.ready)

    def test_a_bad_pair_does_not_reset_valid_capture_count(self):
        tracker = capture_tracker()
        one = pair(tracker.scope, 1)
        bad = pair(tracker.scope, 2, checks_passed=False)
        three = pair(tracker.scope, 3)
        tracker.observe(one, now_us=one.completed_us)
        tracker.observe(bad, now_us=bad.completed_us)
        tracker.observe(three, now_us=three.completed_us)
        self.assertEqual(tracker.count, 2)

    def test_half_open_expiry_and_late_original_completion_do_not_reopen(self):
        tracker = capture_tracker(expires=4000)
        for ordinal in range(1, 30):
            evidence = pair(tracker.scope, ordinal)
            tracker.observe(evidence, now_us=evidence.completed_us)
        self.assertFalse(tracker.poll(3999))
        final = pair(tracker.scope, 30)
        self.assertFalse(tracker.observe(final, now_us=4000))
        self.assertTrue(tracker.failed)
        self.assertFalse(tracker.ready)
        self.assertEqual(tracker.count, 29)
        self.assertFalse(tracker.observe(pair(tracker.scope, 31), now_us=5000))
        self.assertEqual(tracker.count, 29)

    def test_foreign_late_evidence_does_not_expire_current_tracker(self):
        tracker = capture_tracker(expires=4000)
        self.assertFalse(tracker.observe(pair(scope(2), 1), now_us=5000))
        self.assertFalse(tracker.failed)
        self.assertEqual(tracker.count, 0)
        self.assertTrue(tracker.poll(5000))

    def test_capture_scope_replacement_starts_new_count_but_same_object_is_stable(self):
        original = capture_ready()
        same_scope = replace(original.scope)
        self.assertEqual(same_scope, original.scope)
        self.assertTrue(original.ready)
        replacement = capture_tracker(scope(2))
        self.assertEqual(replacement.count, 0)
        self.assertFalse(replacement.observe(pair(original.scope, 100), now_us=15000))
        self.assertEqual(replacement.count, 0)
        self.assertEqual(original.count, 30)

    def test_original_receive_before_queue_cannot_be_repackaged_as_new_completion(self):
        tracker = capture_tracker(expires=4000)
        original = pair(tracker.scope, 1)
        prequeue = replace(original, rgb=replace(original.rgb, received_j_us=50),
                           depth=replace(original.depth, received_j_us=51))
        self.assertFalse(tracker.observe(prequeue, now_us=5000))
        self.assertEqual(tracker.count, 0)
        self.assertFalse(tracker.failed)
        self.assertTrue(tracker.poll(5000))
        equal = capture_tracker()
        original = pair(equal.scope, 1)
        same_us = replace(original, rgb=replace(original.rgb, received_j_us=100),
                          depth=replace(original.depth, received_j_us=100), completed_us=102)
        self.assertTrue(equal.observe(same_us, now_us=102))
        self.assertEqual(equal.count, 1)

    def test_completion_cannot_reverse_and_same_us_completions_are_accepted(self):
        tracker = capture_tracker()
        first = pair(tracker.scope, 1, completed=10000)
        tracker.observe(first, now_us=10000)
        self.assertFalse(tracker.observe(pair(tracker.scope, 2), now_us=10000))
        self.assertEqual((tracker.count, tracker.last_rgb.sequence, tracker.last_completed_us),
                         (1, first.rgb.sequence, 10000))
        for ordinal in range(2, 31):
            self.assertTrue(tracker.observe(pair(tracker.scope, ordinal, completed=10000), now_us=10000))
        self.assertEqual((tracker.count, tracker.ready_us), (30, 10000))

    def test_invalid_old_capture_event_does_not_poll_current_deadline(self):
        for kind in ("duplicate", "old_sdk", "reversed_completion"):
            with self.subTest(kind=kind):
                tracker = capture_tracker(expires=20000)
                first = pair(tracker.scope, 1, completed=10000)
                tracker.observe(first, now_us=10000)
                if kind == "duplicate":
                    invalid = first
                elif kind == "old_sdk":
                    candidate = pair(tracker.scope, 2, completed=10000)
                    invalid = replace(candidate, rgb=replace(candidate.rgb, sdk_us=49999))
                else:
                    invalid = pair(tracker.scope, 2)
                self.assertFalse(tracker.observe(invalid, now_us=20001))
                self.assertEqual(tracker.count, 1)
                self.assertFalse(tracker.failed)
                self.assertTrue(tracker.poll(20001))

    def test_future_completed_time_and_invalid_fact_times_raise_without_progress(self):
        tracker = capture_tracker()
        evidence = pair(tracker.scope, 1)
        with self.assertRaises(ValueError):
            tracker.observe(evidence, now_us=evidence.completed_us - 1)
        self.assertEqual(tracker.count, 0)
        for changes in ({"sequence": True}, {"device_us": -1}, {"sdk_us": 1.0},
                        {"received_j_us": 2**64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(evidence.rgb, **changes)
        for input_kind in (None, True, 1, "live", "UNKNOWN", ["LIVE"]):
            with self.subTest(input_kind=input_kind), self.assertRaises(ValueError):
                replace(evidence, input_kind=input_kind)
        with self.assertRaises(ValueError):
            replace(evidence, completed_us=evidence.depth.received_j_us - 1)


class ParserWarmupTests(unittest.TestCase):
    def test_nn_last_prerequisite_uses_original_capture_ready_not_queue_or_delivery(self):
        capture = capture_tracker(expires=90_000_000)
        for ordinal in range(1, 31):
            completed = 100 + ordinal * 100 if ordinal < 30 else 14_999_999
            evidence = pair_at(capture.scope, ordinal, completed)
            capture.observe(evidence, now_us=completed)
        self.assertEqual(capture.ready_us, 14_999_999)
        self.assertEqual(capture.deadline.expires_us, 15_000_000)
        current = context(capture.scope)
        binding = acknowledged(current, 10_000_000)
        for delivery_us, failed in ((20_000_000, False), (35_000_000, True)):
            with self.subTest(delivery_us=delivery_us):
                tracker = ParserWarmup(current, "REQUIRED")
                tracker.admit_binding(binding, now_us=delivery_us)
                tracker.admit_capture(capture, now_us=delivery_us)
                self.assertEqual((tracker.deadline.started_us, tracker.deadline.expires_us),
                                 (14_999_999, 29_999_999))
                self.assertEqual(tracker.failed, failed)
                self.assertFalse(tracker.nn_ready)
                self.assertTrue(capture.ready)
                self.assertFalse(capture.failed)
                if not failed:
                    self.assertFalse(tracker.poll(29_999_998))
                    self.assertTrue(tracker.poll(29_999_999))

    def test_prerequisite_event_and_delivery_orders_use_original_second_event(self):
        for ack_us in (1000, 5000):
            for delivery in ("binding_first", "capture_first"):
                with self.subTest(ack_us=ack_us, delivery=delivery):
                    current = context()
                    capture = capture_ready(current.capture_scope)
                    binding = acknowledged(current, ack_us)
                    tracker = ParserWarmup(current, "REQUIRED")
                    if delivery == "binding_first":
                        tracker.admit_binding(binding, now_us=10000)
                        tracker.admit_capture(capture, now_us=10000)
                    else:
                        tracker.admit_capture(capture, now_us=10000)
                        tracker.admit_binding(binding, now_us=10000)
                    start = max(capture.ready_us, ack_us)
                    self.assertEqual((tracker.deadline.started_us, tracker.deadline.expires_us),
                                     (start, start + 15_000_000))
                    original_deadline = tracker.deadline
                    self.assertFalse(tracker.admit_capture(capture, now_us=20000))
                    self.assertFalse(tracker.admit_binding(binding, now_us=20000))
                    self.assertIs(tracker.deadline, original_deadline)
                    self.assertFalse(tracker.nn_ready)

    def test_output_zero_expires_at_original_deadline_without_first_output(self):
        tracker = parser_tracker()
        self.assertEqual(tracker.status, "PENDING")
        self.assertEqual(tracker.streak, 0)
        self.assertFalse(tracker.poll(15_004_999))
        self.assertTrue(tracker.poll(15_005_000))
        self.assertEqual(tracker.status, "FAILED")
        for ordinal in range(1, 6):
            self.assertFalse(tracker.observe(parsed(tracker.context, ordinal), now_us=16_000_000))
        self.assertFalse(tracker.nn_ready)

    def test_late_prerequisite_assembly_cannot_rebase_stage_at_delivery_now(self):
        current = context()
        tracker = ParserWarmup(current, "REQUIRED")
        tracker.admit_binding(acknowledged(current, 5000), now_us=20_000_000)
        tracker.admit_capture(capture_ready(current.capture_scope), now_us=20_000_000)
        self.assertEqual(tracker.deadline.expires_us, 15_005_000)
        self.assertTrue(tracker.failed)
        self.assertFalse(tracker.nn_ready)

    def test_five_successful_empty_parse_facts_can_prepare_while_external_inputs_wait(self):
        tracker = parser_tracker()
        for ordinal in range(1, 5):
            evidence = parsed(tracker.context, ordinal)
            self.assertTrue(tracker.observe(evidence, now_us=evidence.completed_us))
        self.assertEqual(tracker.streak, 4)
        self.assertFalse(tracker.nn_ready)
        final = parsed(tracker.context, 5)
        tracker.observe(final, now_us=final.completed_us)
        self.assertTrue(tracker.nn_ready)
        self.assertEqual(tracker.ready_us, final.completed_us)
        self.assertEqual(tracker.status, "READY")
        self.assertFalse(hasattr(tracker, "metric_ready") or hasattr(tracker, "drive_permitted"))

    def test_current_failure_resets_streak_and_its_input_cannot_be_retried_as_success(self):
        tracker = parser_tracker()
        for ordinal in range(1, 5):
            evidence = parsed(tracker.context, ordinal)
            tracker.observe(evidence, now_us=evidence.completed_us)
        original_deadline = tracker.deadline
        failure = parsed(tracker.context, 5, parser_ok=False)
        self.assertTrue(tracker.observe(failure, now_us=failure.completed_us))
        self.assertEqual(tracker.streak, 0)
        self.assertFalse(tracker.observe(replace(failure, parser_ok=True), now_us=failure.completed_us + 1))
        self.assertEqual(tracker.streak, 0)
        self.assertIs(tracker.deadline, original_deadline)
        for ordinal in range(6, 11):
            evidence = parsed(tracker.context, ordinal)
            tracker.observe(evidence, now_us=evidence.completed_us)
        self.assertTrue(tracker.nn_ready)

    def test_parser_failure_completion_is_remembered_and_equal_us_successes_can_follow(self):
        tracker = parser_tracker()
        first = parsed(tracker.context, 1)
        tracker.observe(first, now_us=first.completed_us)
        failure = parsed(tracker.context, 2, completed_us=10000, parser_ok=False)
        self.assertTrue(tracker.observe(failure, now_us=10000))
        self.assertEqual((tracker.streak, tracker.last_input.sequence, tracker.last_completed_us), (0, 2, 10000))
        self.assertFalse(tracker.observe(parsed(tracker.context, 3), now_us=10000))
        self.assertEqual((tracker.streak, tracker.last_input.sequence, tracker.last_completed_us), (0, 2, 10000))
        for ordinal in range(3, 8):
            same_us = parsed(tracker.context, ordinal, completed_us=10000)
            self.assertTrue(tracker.observe(same_us, now_us=10000))
        self.assertTrue(tracker.nn_ready)
        self.assertEqual(tracker.ready_us, 10000)

    def test_invalid_old_parser_event_does_not_expire_or_modify_current_streak(self):
        for kind in ("duplicate", "reversed_completion"):
            with self.subTest(kind=kind):
                tracker = parser_tracker()
                first = parsed(tracker.context, 1, completed_us=10000)
                tracker.observe(first, now_us=10000)
                invalid = first if kind == "duplicate" else parsed(tracker.context, 2)
                self.assertFalse(tracker.observe(invalid, now_us=16_000_000))
                self.assertEqual(tracker.streak, 1)
                self.assertFalse(tracker.failed)
                self.assertTrue(tracker.poll(16_000_000))

    def test_duplicate_foreign_model_worker_scope_and_replay_never_count_or_revoke(self):
        tracker = parser_tracker()
        first = parsed(tracker.context, 1)
        tracker.observe(first, now_us=first.completed_us)
        bad_contexts = (replace(tracker.context, model_id="asset-old"),
                        replace(tracker.context, parser_id="parser-old"),
                        replace(tracker.context, mapper_worker=identity(999)), context(scope(2)))
        for other in bad_contexts:
            evidence = parsed(other, 2, parser_ok=False)
            self.assertFalse(tracker.observe(evidence, now_us=20_000_000))
            self.assertEqual(tracker.streak, 1)
            self.assertFalse(tracker.failed)
        for proof in (first, replace(first, parser_ok=False),
                      replace(parsed(tracker.context, 2), input_kind="REPLAY"),
                      replace(parsed(tracker.context, 2), pairing_and_freshness_validated=False)):
            self.assertFalse(tracker.observe(proof, now_us=6000))
            self.assertEqual(tracker.streak, 1)
        tracker.poll(20_000_000)
        self.assertTrue(tracker.failed)

    def test_m_only_replacement_preserves_a_but_requires_new_ack_and_five_facts(self):
        capture = capture_ready()
        old = parser_tracker(current=context(capture.scope))
        for ordinal in range(1, 6):
            proof = parsed(old.context, ordinal)
            old.observe(proof, now_us=proof.completed_us)
        self.assertTrue(old.nn_ready)
        current = context(capture.scope, worker=200, request=201)
        fresh = ParserWarmup(current, "REQUIRED")
        fresh.admit_capture(capture, now_us=1_000_000)
        self.assertIsNone(fresh.deadline)
        self.assertEqual(fresh.streak, 0)
        self.assertFalse(fresh.admit_binding(acknowledged(old.context, 5000), now_us=1_000_000))
        binding = acknowledged(current, 1_000_100)
        fresh.admit_binding(binding, now_us=1_000_100)
        self.assertEqual(fresh.deadline.expires_us, 16_000_100)
        self.assertFalse(fresh.observe(parsed(old.context, 6), now_us=1_000_200))
        self.assertTrue(capture.ready)
        self.assertEqual(capture.count, 30)
        self.assertFalse(fresh.nn_ready)
        for ordinal in range(1, 6):
            proof = parsed(current, ordinal, stage_start=1_000_100)
            fresh.observe(proof, now_us=proof.completed_us)
        self.assertTrue(fresh.nn_ready)

    def test_long_healthy_a_lifetime_does_not_cap_new_mapper_stage_to_old_cold90s(self):
        capture = capture_ready()
        current = context(capture.scope, worker=200, request=201)
        tracker = ParserWarmup(current, "REQUIRED")
        tracker.admit_capture(capture, now_us=100_000_000)
        tracker.admit_binding(acknowledged(current, 100_000_100), now_us=100_000_100)
        self.assertEqual(tracker.deadline.expires_us, 115_000_100)
        self.assertFalse(tracker.failed)

    def test_explicit_shorter_stage_cap_is_not_extended_by_new_input(self):
        tracker = parser_tracker(cap_us=5500)
        self.assertEqual(tracker.deadline.expires_us, 5500)
        evidence = parsed(tracker.context, 1)
        tracker.observe(evidence, now_us=5499)
        self.assertTrue(tracker.poll(5500))
        self.assertFalse(tracker.observe(parsed(tracker.context, 2), now_us=5501))
        self.assertEqual(tracker.deadline.expires_us, 5500)

    def test_unpaired_foreign_stream_prestage_and_future_proofs_do_not_prepare(self):
        tracker = parser_tracker()
        original = parsed(tracker.context, 1)
        mismatched = replace(original, nn_output=replace(original.nn_output, sequence=99))
        wrong_stream = replace(original, nn_output=replace(original.nn_output, stream_instance=identity(999)))
        prestage = parsed(tracker.context, 1, stage_start=4000)
        for evidence in (mismatched, wrong_stream, prestage):
            self.assertFalse(tracker.observe(evidence, now_us=6000))
            self.assertEqual(tracker.streak, 0)
        with self.assertRaises(ValueError):
            tracker.observe(original, now_us=original.completed_us - 1)
        self.assertEqual(tracker.streak, 0)

    def test_absent_and_optional_do_not_create_timer_or_fake_nn_readiness(self):
        absent_context = context(scope(2, nn=False), model="absent")
        absent = ParserWarmup(absent_context, "ABSENT")
        absent.admit_capture(capture_ready(absent_context.capture_scope), now_us=5000)
        absent.admit_binding(acknowledged(absent_context, 5000), now_us=5000)
        self.assertEqual(absent.status, "ABSENT")
        self.assertIsNone(absent.deadline)
        self.assertFalse(absent.poll(2**63) or absent.nn_ready or absent.classical_policy_allowed)
        for approved in (False, True):
            optional = ParserWarmup(context(), "OPTIONAL", classical_fallback_allowed=approved)
            optional.admit_capture(capture_ready(optional.context.capture_scope), now_us=5000)
            optional.admit_binding(acknowledged(optional.context, 5000), now_us=5000)
            self.assertEqual(optional.status, "OPTIONAL_PARSER_NOT_IMPLEMENTED")
            self.assertIsNone(optional.deadline)
            self.assertEqual(optional.classical_policy_allowed, approved)
            self.assertFalse(optional.nn_ready or optional.poll(2**63))

    def test_unknown_requirement_invalid_kinds_and_timestamps_fail(self):
        for requirement in ("required", "UNKNOWN", None, True, 1):
            with self.subTest(requirement=requirement), self.assertRaises(ValueError):
                ParserWarmup(context(), requirement)
        with self.assertRaises(ValueError):
            ParserWarmup(context(scope(2, nn=False)), "REQUIRED")
        with self.assertRaises(ValueError):
            ParserWarmup(context(), "ABSENT")
        for bad in (None, True, "LIVE ", "live", 1, ["LIVE"]):
            with self.subTest(kind=bad), self.assertRaises(ValueError):
                replace(parsed(context(), 1), input_kind=bad)
        for bad in (True, -1, 1.5, 2**64):
            with self.subTest(now=bad), self.assertRaises(ValueError):
                parser_tracker().poll(bad)
        with self.assertRaises(ValueError):
            replace(parsed(context(), 1), completed_us=-1)


if __name__ == "__main__":
    unittest.main()
