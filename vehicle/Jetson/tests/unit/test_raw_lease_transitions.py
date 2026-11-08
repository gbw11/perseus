"""raw 원장의 사건 순서와 유한 정리를 검사한다. 사실 입력은 모두 합성이다.

실제 token 작업, view 종료, 송신 성공, owner 종료를 관측하지 않는다. 비공개 상태
주입은 generation 고갈과 local commit 예외의 시험 경계에만 사용한다.
"""

from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import patch

from xenova_vehicle.runtime import raw_leases as raw
from raw_lease_fixtures import identifier, ledger, read_lease, reader, request, write_ready


class LeaseTransitionsTests(unittest.TestCase):
    def unchanged_error(self, value, call, error=raw.LeaseLedgerError):
        before = value.snapshot
        with self.assertRaises(error):
            call()
        self.assertEqual(value.snapshot, before)

    def prepared(self):
        value = ledger()
        grant, info = write_ready(value)
        ticket = value.begin_replace(0, grant.key, request(50, issued=130),
                                     identifier(60), now_us=140).value
        return value, grant, info, ticket

    def sync_fact(self, ticket, *, consumed=150, posted=160, finished=170):
        return raw.ReplacementSyncFact(ticket, consumed, posted, finished)

    def assert_failed_replacement(self, value, info, ticket, fact, cause):
        snapshot = value.snapshot
        slot = snapshot.slots[0]
        self.assertTrue(snapshot.fenced)
        self.assertIs(snapshot.fault, cause)
        self.assertIs(slot.state, raw.SlotState.READY)
        self.assertEqual(slot.generation, info.key.generation)
        self.assertEqual(slot.filled, info)
        self.assertEqual(slot.write.key, info.key)
        self.assertIs(slot.pending.ticket, ticket)
        self.assertIs(slot.pending.fact, fact)
        self.assertIs(slot.cleanup.fact, fact)
        self.assertIsNone(slot.read)
        self.unchanged_error(value, lambda: value.offer(0, now_us=190))
        self.unchanged_error(value, lambda: value.reserve_write(
            1, request(70, issued=130, expires=300), identifier(71), now_us=190))

    def test_full_cycle_keeps_writer_and_reader_channels_separate(self):
        value = ledger()
        self.assertEqual(len(value.snapshot.slots), 2)
        self.assertTrue(all(s.state is raw.SlotState.FREE and s.generation == 0
                            for s in value.snapshot.slots))
        grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.WRITING)
        self.assertEqual(grant.key.generation, 1)
        info = raw.FilledInfo(grant.key, 960000, 'b' * 64, 120)
        self.assertIs(value.filled(info, now_us=130).status, raw.LeaseStatus.APPLIED)
        offer = value.offer(0, now_us=140)
        self.assertIs(type(offer), raw.Offer)
        self.assertFalse(hasattr(offer, 'read_token'))
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.READY)
        lease = read_lease(value)
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.IN_USE)
        self.assertEqual(lease.key.write_key, grant.key)
        self.assertEqual(lease.key.reader, reader())
        self.assertNotEqual(lease.key.reader, grant.key.scope.writer)
        fact = raw.ViewReturnFact(lease, 160, 170)
        self.assertIs(value.release(fact, now_us=180).status, raw.LeaseStatus.APPLIED)
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.FREE)
        self.assertEqual(value.snapshot.slots[0].filled, info)

    def test_acquire_reserves_before_any_acquired_send(self):
        value = ledger()
        grant, _ = write_ready(value)
        lease = read_lease(value)
        before = value.snapshot
        # 송신 API를 호출하지 않아도 예약은 유지된다. EAGAIN은 별도 사실이다.
        for _ in range(20):
            self.assertIs(value.snapshot.slots[0].read, lease)
            self.assertEqual(value.snapshot, before)
        self.unchanged_error(value, lambda: value.begin_replace(
            0, grant.key, request(50, issued=140), identifier(60), now_us=160))
        self.unchanged_error(value, lambda: value.reserve_write(
            0, request(51, issued=140), identifier(61), now_us=160))

    def test_one_producer_marker_spans_both_slots_until_filled(self):
        value = ledger()
        grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
        self.unchanged_error(value, lambda: value.reserve_write(
            1, request(11), identifier(21), now_us=115))
        self.unchanged_error(value, lambda: value.reserve_write(
            1, request(11, issued=210, expires=400), identifier(21), now_us=220))
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.WRITING)
        value.filled(raw.FilledInfo(grant.key, 1, 'b' * 64, 120), now_us=130)
        second = value.reserve_write(1, request(11), identifier(21), now_us=140).value
        self.assertEqual(second.key.slot, 1)
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.READY)

    def test_write_request_fingerprint_duplicate_and_conflicts(self):
        value = ledger()
        original = request()
        grant = value.reserve_write(0, original, identifier(20), now_us=110).value
        result = value.reserve_write(0, original, identifier(20), now_us=120)
        self.assertIs(result.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(result.value, grant)
        for slot, original, token in (
                (1, request(), identifier(20)),
                (0, request(), identifier(21)),
                (0, request(expires=201), identifier(20)),
                (0, request(cap=200), identifier(20))):
            with self.subTest(slot=slot, original=original, token=token):
                self.unchanged_error(value, lambda: value.reserve_write(
                    slot, original, token, now_us=120))

    def test_reader_marker_spans_both_slots_and_request_is_immutable(self):
        value = ledger()
        write_ready(value)
        write_ready(value, slot=1, request_number=11, token_number=21,
                    granted=131, completed=132, received=133)
        lease = read_lease(value)
        result = value.acquire(lease.read_request, lease.key.read_token, now_us=160)
        self.assertIs(result.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(result.value, lease)
        second = raw.ReadRequest(value.offer(1, now_us=160), reader(), request(31, issued=140))
        self.unchanged_error(value, lambda: value.acquire(second, identifier(41), now_us=160))
        for changed, token in (
                (lease.read_request, identifier(41)),
                (replace(lease.read_request, request=request(30, issued=140, expires=301)),
                 lease.key.read_token),
                (replace(lease.read_request, offer=second.offer), lease.key.read_token)):
            with self.subTest(changed=changed, token=token):
                self.unchanged_error(value, lambda: value.acquire(changed, token, now_us=160))

    def test_parser_null_and_registered_publication_do_not_require_metric_job(self):
        for registered, supplied in ((None, None), ('c' * 64, None), ('c' * 64, 'c' * 64)):
            with self.subTest(registered=registered, supplied=supplied):
                value = ledger(publication=registered)
                write_ready(value)
                lease = read_lease(value, publication=supplied)
                self.assertEqual(lease.read_request.publication_sha256, supplied)
                self.assertEqual(lease.read_request.purpose, 'PARSER_WARMUP')
        value = ledger(publication='c' * 64)
        write_ready(value)
        wrong = raw.ReadRequest(value.offer(0, now_us=150), reader(),
                                request(30, issued=140), publication_sha256='d' * 64)
        self.unchanged_error(value, lambda: value.acquire(wrong, identifier(40), now_us=150))

    def test_metric_and_foreign_reader_are_explicitly_rejected(self):
        value = ledger(publication='c' * 64)
        write_ready(value)
        normal = raw.ReadRequest(value.offer(0, now_us=150), reader(), request(30, issued=140),
                                 publication_sha256='c' * 64)
        self.unchanged_error(value, lambda: value.acquire(
            replace(normal, purpose='METRIC_MAP'), identifier(40), now_us=150), raw.UnsupportedPurpose)
        other = raw.Endpoint(identifier(80), identifier(81))
        self.unchanged_error(value, lambda: value.acquire(
            replace(normal, reader=other), identifier(40), now_us=150))

    def test_completed_write_deadline_does_not_cap_independent_read_job(self):
        value = ledger()
        grant, info = write_ready(value)
        self.assertEqual(grant.request.expires_us, 200)
        self.assertEqual(value.offer(0, now_us=250).content, info)
        lease = read_lease(value, issued=240, expires=400, reserved=250)
        for now, expected in ((249, False), (250, True), (399, True), (400, False), (401, False)):
            with self.subTest(now=now):
                self.assertIs(value.read_is_current(lease, now_us=now), expected)
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.IN_USE)

    def test_first_late_filled_is_rejected_without_free_or_ready(self):
        for now in (200, 201):
            with self.subTest(now=now):
                value = ledger()
                grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
                info = raw.FilledInfo(grant.key, 1, 'b' * 64, 120)
                self.unchanged_error(value, lambda: value.filled(info, now_us=now))
                self.assertIs(value.snapshot.slots[0].state, raw.SlotState.WRITING)
                self.assertEqual(value.snapshot.slots[0].write, grant)

    def test_filled_uses_original_grant_lower_bound_and_rejects_future(self):
        value = ledger()
        grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
        for completed, now in ((109, 130), (131, 130)):
            with self.subTest(completed=completed):
                info = raw.FilledInfo(grant.key, 1, 'b' * 64, completed)
                self.unchanged_error(value, lambda: value.filled(info, now_us=now))
        self.assertIs(value.filled(raw.FilledInfo(grant.key, 1, 'b' * 64, 110),
                                   now_us=110).status, raw.LeaseStatus.APPLIED)

    def test_duplicate_filled_never_restores_ready_from_in_use_or_free(self):
        value = ledger()
        _, info = write_ready(value)
        lease = read_lease(value)
        for state in (raw.SlotState.IN_USE, raw.SlotState.FREE):
            with self.subTest(state=state):
                if state is raw.SlotState.FREE:
                    value.release(raw.ViewReturnFact(lease, 160, 170), now_us=180)
                before = value.snapshot
                self.assertIs(value.filled(info, now_us=500).status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
                self.assertEqual(value.snapshot, before)
                self.assertIs(value.snapshot.slots[0].state, state)
        value.fence(now_us=510)
        before = value.snapshot
        self.assertIs(value.filled(info, now_us=520).status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(value.snapshot, before)

    def test_same_filled_key_with_changed_summary_is_conflict(self):
        value = ledger()
        _, info = write_ready(value)
        read_lease(value)
        for changed in (replace(info, payload_length=1), replace(info, descriptor_sha256='c' * 64),
                        replace(info, completed_us=121)):
            with self.subTest(changed=changed):
                self.unchanged_error(value, lambda: value.filled(changed, now_us=500))

    def test_begin_replace_keeps_ready_but_blocks_claim_and_other_producer(self):
        value, _, info, ticket = self.prepared()
        slot = value.snapshot.slots[0]
        self.assertIs(slot.state, raw.SlotState.READY)
        self.assertEqual(slot.write.key, info.key)
        self.assertEqual(slot.generation, 1)
        self.assertEqual(ticket.planned_key.generation, 2)
        self.assertIs(type(ticket), raw.ReplacementTicket)
        self.assertIsNone(slot.pending.fact)
        original = raw.ReadRequest(raw.Offer(info), reader(), request(30, issued=140))
        self.unchanged_error(value, lambda: value.acquire(original, identifier(40), now_us=150))
        self.unchanged_error(value, lambda: value.reserve_write(
            1, request(51, issued=130), identifier(61), now_us=150))
        result = value.begin_replace(0, info.key, ticket.request, ticket.planned_key.write_token, now_us=150)
        self.assertIs(result.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(result.value, ticket)

    def test_acquire_winning_first_prevents_replacement(self):
        value = ledger()
        grant, _ = write_ready(value)
        lease = read_lease(value)
        self.unchanged_error(value, lambda: value.begin_replace(
            0, grant.key, request(50, issued=140), identifier(60), now_us=160))
        self.assertIs(value.snapshot.slots[0].read, lease)

    def test_successful_external_fact_commits_one_new_grant(self):
        value, _, _, ticket = self.prepared()
        fact = self.sync_fact(ticket)
        result = value.complete_replace(ticket, fact, now_us=180)
        self.assertIs(result.status, raw.LeaseStatus.APPLIED)
        grant = result.value
        self.assertIs(type(grant), raw.WriteGrant)
        self.assertEqual(grant.key, ticket.planned_key)
        self.assertIs(grant.request, ticket.request)
        self.assertEqual(grant.granted_us, 180)
        slot = value.snapshot.slots[0]
        self.assertIs(slot.state, raw.SlotState.WRITING)
        self.assertIsNone(slot.filled)
        self.assertIsNone(slot.pending)
        self.assertIs(slot.cleanup.fact, fact)
        before = value.snapshot
        duplicate = value.complete_replace(ticket, fact, now_us=500)
        self.assertIs(duplicate.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        begin = value.begin_replace(0, ticket.old_content.key, ticket.request,
                                    ticket.planned_key.write_token, now_us=190)
        self.assertIs(begin.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(begin.value, ticket)

    def test_replacement_write_current_begins_at_commit_not_external_finish(self):
        value, _, _, ticket = self.prepared()
        grant = value.complete_replace(ticket, self.sync_fact(ticket), now_us=180).value
        for now, expected in ((179, False), (180, True), (199, True), (200, False), (201, False)):
            with self.subTest(now=now):
                self.assertIs(value.write_is_current(grant, now_us=now), expected)
        value.filled(raw.FilledInfo(grant.key, 2, 'c' * 64, 185), now_us=190)
        self.assertFalse(value.write_is_current(grant, now_us=190))

    def test_replacement_request_id_cannot_change_held_fingerprint(self):
        value, _, info, ticket = self.prepared()
        for original, token, expected in (
                (replace(ticket.request, expires_us=201), ticket.planned_key.write_token, info.key),
                (ticket.request, identifier(61), info.key),
                (ticket.request, ticket.planned_key.write_token, replace(info.key, write_token=identifier(22)))):
            with self.subTest(original=original, token=token):
                self.unchanged_error(value, lambda: value.begin_replace(
                    0, expected, original, token, now_us=150))
        self.unchanged_error(value, lambda: value.reserve_write(
            1, ticket.request, ticket.planned_key.write_token, now_us=150))
        value.complete_replace(ticket, self.sync_fact(ticket), now_us=180)
        self.unchanged_error(value, lambda: value.reserve_write(
            1, ticket.request, ticket.planned_key.write_token, now_us=190))

    def test_ticket_identity_is_required_even_for_equal_copy(self):
        value, _, _, ticket = self.prepared()
        copied = replace(ticket)
        self.assertEqual(copied, ticket)
        self.assertIsNot(copied, ticket)
        copied_fact = self.sync_fact(copied)
        self.unchanged_error(value, lambda: value.complete_replace(ticket, copied_fact, now_us=180))
        before = value.snapshot
        result = value.complete_replace(copied, copied_fact, now_us=180)
        self.assertIs(result.status, raw.LeaseStatus.UNKNOWN_NO_EFFECT)
        self.assertEqual(value.snapshot, before)

    def test_future_replacement_fact_is_rejected_before_fence(self):
        value, _, _, ticket = self.prepared()
        fact = self.sync_fact(ticket, finished=181)
        self.unchanged_error(value, lambda: value.complete_replace(ticket, fact, now_us=180))
        self.assertFalse(value.snapshot.fenced)
        for consumed, posted, finished in ((None, 160, 170), (150, 149, 170), (139, 160, 170),
                                           (150, 171, 170), (None, None, 139)):
            with self.subTest(consumed=consumed, posted=posted, finished=finished):
                self.unchanged_error(value, lambda: self.sync_fact(
                    ticket, consumed=consumed, posted=posted, finished=finished))

    def test_partial_external_attempt_retains_original_content_and_fences(self):
        for consumed in (None, 150):
            with self.subTest(consumed=consumed):
                value, _, info, ticket = self.prepared()
                fact = self.sync_fact(ticket, consumed=consumed, posted=None)
                result = value.complete_replace(ticket, fact, now_us=180)
                self.assertIs(result.status, raw.LeaseStatus.REPLACEMENT_FAILED)
                self.assertIs(result.value, value.snapshot.slots[0].pending)
                self.assert_failed_replacement(value, info, ticket, fact, raw.ReplacementFault.PARTIAL)
                before = value.snapshot
                self.assertIs(value.complete_replace(ticket, fact, now_us=500).status,
                              raw.LeaseStatus.DUPLICATE_NO_EFFECT)
                self.assertEqual(value.snapshot, before)
                self.unchanged_error(value, lambda: value.complete_replace(
                    ticket, self.sync_fact(ticket), now_us=500))

    def test_late_external_success_cannot_issue_new_write_or_deadline(self):
        for now in (200, 201):
            with self.subTest(now=now):
                value, _, info, ticket = self.prepared()
                fact = self.sync_fact(ticket)
                result = value.complete_replace(ticket, fact, now_us=now)
                self.assertIs(result.status, raw.LeaseStatus.REPLACEMENT_FAILED)
                self.assert_failed_replacement(value, info, ticket, fact, raw.ReplacementFault.EXPIRED)
                self.assertEqual(value.snapshot.fenced_us, now)
                self.assertEqual(value.snapshot.slots[0].pending.ticket.request.expires_us, 200)

    def test_fence_before_external_completion_preserves_first_fence_and_ticket(self):
        value, _, info, ticket = self.prepared()
        self.assertTrue(value.fence(now_us=145))
        self.assertFalse(value.fence(now_us=146))
        self.assertTrue(value.fence(now_us=147, retired=True))
        fact = self.sync_fact(ticket)
        result = value.complete_replace(ticket, fact, now_us=180)
        self.assertIs(result.status, raw.LeaseStatus.REPLACEMENT_FAILED)
        self.assert_failed_replacement(value, info, ticket, fact, raw.ReplacementFault.FENCED)
        self.assertEqual(value.snapshot.fenced_us, 145)
        self.assertTrue(value.snapshot.retired)

    def test_commit_exception_before_or_after_mutation_keeps_old_fenced_evidence(self):
        for fail_after_commit in (False, True):
            with self.subTest(fail_after_commit=fail_after_commit):
                value, _, info, ticket = self.prepared()
                fact = self.sync_fact(ticket)
                actual_commit = value._commit_slot
                calls = []

                def faulting_commit(index, slot):
                    # 시험 전용 주입: 외부 fact 뒤 첫 local commit의 양쪽 경계만 실패시킨다.
                    calls.append(slot)
                    if len(calls) == 1:
                        if fail_after_commit:
                            actual_commit(index, slot)
                        raise RuntimeError('synthetic local commit failure')
                    actual_commit(index, slot)

                with patch.object(value, '_commit_slot', side_effect=faulting_commit):
                    with self.assertRaisesRegex(RuntimeError, 'synthetic local commit failure'):
                        value.complete_replace(ticket, fact, now_us=180)
                self.assertEqual(len(calls), 2)
                self.assertIs(calls[0].state, raw.SlotState.WRITING)
                self.assert_failed_replacement(value, info, ticket, fact,
                                               raw.ReplacementFault.LOCAL_COMMIT_FAILED)
                before = value.snapshot
                self.assertIs(value.complete_replace(ticket, fact, now_us=190).status,
                              raw.LeaseStatus.DUPLICATE_NO_EFFECT)
                self.assertEqual(value.snapshot, before)

    def test_successful_replacement_fact_cannot_be_changed_after_commit(self):
        value, _, _, ticket = self.prepared()
        fact = self.sync_fact(ticket)
        value.complete_replace(ticket, fact, now_us=180)
        self.unchanged_error(value, lambda: value.complete_replace(
            ticket, replace(fact, finished_us=171), now_us=190))

    def test_expiry_and_fence_keep_writing_ownership_without_recovery(self):
        value = ledger()
        grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
        self.assertFalse(value.write_is_current(grant, now_us=200))
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.WRITING)
        value.fence(now_us=210, retired=True)
        self.assertIs(value.snapshot.slots[0].write, grant)
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.WRITING)
        self.unchanged_error(value, lambda: value.reserve_write(
            1, request(11, issued=220, expires=400), identifier(21), now_us=230))

    def test_exact_expired_retired_return_cleans_once_without_new_authority(self):
        value = ledger()
        write_ready(value)
        lease = read_lease(value)
        self.assertFalse(value.read_is_current(lease, now_us=300))
        self.assertIs(value.snapshot.slots[0].read, lease)
        value.fence(now_us=310, retired=True)
        self.assertFalse(value.read_is_current(lease, now_us=160))
        fact = raw.ViewReturnFact(lease, 320, 330)
        self.assertIs(value.release(fact, now_us=340).status, raw.LeaseStatus.APPLIED)
        slot = value.snapshot.slots[0]
        self.assertIs(slot.state, raw.SlotState.FREE)
        self.assertTrue(value.snapshot.fenced)
        self.assertTrue(value.snapshot.retired)
        before = value.snapshot
        self.assertIs(value.release(fact, now_us=350).status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        self.unchanged_error(value, lambda: value.reserve_write(
            0, request(70, issued=350, expires=500), identifier(71), now_us=360))

    def test_return_requires_ordered_fact_not_future_or_bare_lease(self):
        value = ledger()
        write_ready(value)
        lease = read_lease(value)
        self.unchanged_error(value, lambda: value.release(lease, now_us=180))
        self.unchanged_error(value, lambda: raw.ViewReturnFact(lease, 149, 170))
        self.unchanged_error(value, lambda: raw.ViewReturnFact(lease, 170, 169))
        fact = raw.ViewReturnFact(lease, 160, 181)
        self.unchanged_error(value, lambda: value.release(fact, now_us=180))

    def test_known_read_key_changed_request_is_conflict_before_and_after_release(self):
        value = ledger(publication='c' * 64)
        write_ready(value)
        lease = read_lease(value)
        fact = raw.ViewReturnFact(lease, 160, 170)
        changed_leases = (
            replace(lease, read_request=replace(lease.read_request, request=request(30, issued=140, expires=301))),
            replace(lease, read_request=replace(lease.read_request, publication_sha256='c' * 64)),
            replace(lease, reserved_us=151),
        )
        for returned in (False, True):
            with self.subTest(returned=returned):
                if returned:
                    value.release(fact, now_us=180)
                for changed in changed_leases:
                    self.unchanged_error(value, lambda: value.release(
                        raw.ViewReturnFact(changed, 160, 170), now_us=190))
                if returned:
                    self.unchanged_error(value, lambda: value.release(
                        replace(fact, writable_posted_us=171), now_us=190))

    def test_other_read_token_or_channel_does_not_release_current_lease(self):
        value = ledger()
        write_ready(value)
        lease = read_lease(value)
        other_reader = raw.Endpoint(identifier(80), identifier(81))
        foreign = (
            replace(lease, key=replace(lease.key, read_token=identifier(41))),
            replace(lease, key=replace(lease.key, reader=other_reader),
                    read_request=replace(lease.read_request, reader=other_reader)),
        )
        for changed in foreign:
            with self.subTest(changed=changed):
                before = value.snapshot
                result = value.release(raw.ViewReturnFact(changed, 160, 170), now_us=180)
                self.assertIs(result.status, raw.LeaseStatus.UNKNOWN_NO_EFFECT)
                self.assertEqual(value.snapshot, before)

    def test_same_reader_old_generation_return_cannot_release_new_lease(self):
        value = ledger()
        write_ready(value)
        first = read_lease(value)
        first_fact = raw.ViewReturnFact(first, 160, 170)
        value.release(first_fact, now_us=180)
        write_ready(value, request_number=11, token_number=21, issued=200, expires=400,
                    granted=210, completed=220, received=230)
        second = read_lease(value, request_number=31, token_number=41, issued=240,
                            expires=500, reserved=250)
        self.assertEqual(second.key.reader, first.key.reader)
        self.assertEqual(second.key.write_key.generation, first.key.write_key.generation + 1)
        self.assertNotEqual(second.key.read_token, first.key.read_token)
        before = value.snapshot
        self.assertIs(value.release(first_fact, now_us=260).status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        second_fact = raw.ViewReturnFact(second, 270, 280)
        value.release(second_fact, now_us=290)
        write_ready(value, request_number=12, token_number=22, issued=300, expires=500,
                    granted=310, completed=320, received=330)
        third = read_lease(value, request_number=32, token_number=42, issued=340,
                           expires=600, reserved=350)
        before = value.snapshot
        self.assertIs(value.release(first_fact, now_us=360).status, raw.LeaseStatus.UNKNOWN_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        self.assertIs(value.snapshot.slots[0].read, third)
        self.assertIs(value.snapshot.slots[0].cleanup.fact, second_fact)

    def test_recent_read_request_summary_still_checks_full_fingerprint(self):
        value = ledger()
        write_ready(value)
        first = read_lease(value)
        value.release(raw.ViewReturnFact(first, 160, 170), now_us=180)
        write_ready(value, request_number=11, token_number=21, issued=200, expires=400,
                    granted=210, completed=220, received=230)
        current = raw.ReadRequest(value.offer(0, now_us=250), reader(),
                                  request(30, issued=240, expires=500))
        self.unchanged_error(value, lambda: value.acquire(current, identifier(41), now_us=250))
        exact = value.acquire(first.read_request, first.key.read_token, now_us=250)
        self.assertIs(exact.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(exact.value, first)
        self.assertIs(value.snapshot.slots[0].state, raw.SlotState.READY)

    def test_current_read_does_not_hide_previous_cleanup_request(self):
        value = ledger()
        write_ready(value)
        first = read_lease(value)
        value.release(raw.ViewReturnFact(first, 160, 170), now_us=180)
        write_ready(value, request_number=11, token_number=21, issued=200, expires=400,
                    granted=210, completed=220, received=230)
        current = read_lease(value, request_number=31, token_number=41, issued=240,
                             expires=500, reserved=250)
        before = value.snapshot
        exact = value.acquire(first.read_request, first.key.read_token, now_us=260)
        self.assertIs(exact.status, raw.LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(exact.value, first)
        self.assertEqual(value.snapshot, before)
        changed = replace(first.read_request, request=request(30, issued=140, expires=301))
        self.unchanged_error(value, lambda: value.acquire(changed, first.key.read_token, now_us=260))
        self.assertIs(value.snapshot.slots[0].read, current)

    def test_generation_limit_is_rejected_before_replacement_ticket(self):
        value = ledger()
        grant, info = write_ready(value)
        max_key = replace(grant.key, generation=raw.U64_MAX)
        max_grant = replace(grant, key=max_key)
        max_info = replace(info, key=max_key)
        # 시험 전용 주입이며 production seed/reset 경로를 추가하지 않는다.
        value._slots = (raw.SlotSnapshot(raw.U64_MAX, raw.SlotState.READY,
                                        max_grant, max_info), value.snapshot.slots[1])
        self.unchanged_error(value, lambda: value.begin_replace(
            0, max_key, request(50, issued=130), identifier(60), now_us=140), raw.GenerationExhausted)
        self.assertIsNone(value.snapshot.slots[0].pending)
        self.assertFalse(value.snapshot.fenced)

    def test_free_generation_limit_does_not_wrap_or_claim_other_slot(self):
        value = ledger()
        # 시험 전용 주입: MAX FREE의 산술 경계만 재현한다.
        value._slots = (raw.SlotSnapshot(generation=raw.U64_MAX), value.snapshot.slots[1])
        self.unchanged_error(value, lambda: value.reserve_write(
            0, request(), identifier(20), now_us=110), raw.GenerationExhausted)
        self.assertEqual(raw._next_generation(raw.U64_MAX - 1), raw.U64_MAX)
        self.assertEqual(raw._next_generation(0), 1)
        self.assertIs(value.snapshot.slots[1].state, raw.SlotState.FREE)

    def test_snapshot_and_held_records_cannot_be_mutated_by_caller(self):
        value, grant, _, ticket = self.prepared()
        snapshot = value.snapshot
        for target, field, changed in ((snapshot, 'fenced', True),
                                       (snapshot.slots[0], 'generation', 99),
                                       (grant, 'granted_us', 0),
                                       (ticket.request, 'expires_us', 999),
                                       (ticket, 'begun_us', 0)):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(target, field, changed)
        self.assertIs(type(snapshot.slots), tuple)
        self.assertEqual(value.snapshot, snapshot)


if __name__ == '__main__':
    unittest.main()
