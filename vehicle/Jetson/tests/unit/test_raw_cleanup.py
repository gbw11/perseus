"""합성 local/remote 반환과 마지막 cleanup의 유한 보충. 실제 view/token/OS 종료 시험은 아니다."""

from dataclasses import FrozenInstanceError, replace
import unittest

from raw_lease_fixtures import identifier, ledger, read_lease, reader, request, write_ready
from xenova_vehicle.runtime.raw_leases import (
    CleanupSummary, Endpoint, LeaseLedgerError, LeaseResult, LeaseStatus, PendingReplacement, ReadKey, ReadLease, ReadRequest,
    RemoteReturnStatement, ReplacementFault, ReplacementSyncFact, SlotState, ViewReturnFact,
)


_MAX = 2**64 - 1


def _active(*, expires=300):
    value = ledger()
    grant, info = write_ready(value)
    lease = read_lease(value, expires=expires)
    return value, grant, info, lease


def _local(lease, *, ended=390, posted=400):
    return ViewReturnFact(lease, ended, posted)


def _remote(lease, *, completed=401):
    return RemoteReturnStatement(lease, completed)


def _apply(value, fact, *, now=500):
    return (value.release(fact, now_us=now) if type(fact) is ViewReturnFact
            else value.release_remote(fact, now_us=now))


def _alter_lease(lease, *, request_changes=None, reserved=None, purpose=None, publication=None,
                 content_changes=None, key=None, endpoint=None):
    """형식상 일관된 c1 record를 만들되 보유한 원 lease와의 차이를 보존한다."""
    original = lease.read_request
    read_request = original
    if request_changes is not None:
        read_request = replace(read_request, request=replace(original.request, **request_changes))
    if purpose is not None:
        read_request = replace(read_request, purpose=purpose)
    if publication is not None:
        read_request = replace(read_request, publication_sha256=publication)
    content = original.offer.content
    if content_changes is not None:
        content = replace(content, **content_changes)
    if key is not None:
        content = replace(content, key=key)
    read_request = replace(read_request, offer=replace(original.offer, content=content))
    if endpoint is not None:
        read_request = replace(read_request, reader=endpoint)
    read_key = ReadKey(content.key, read_request.reader, lease.key.read_token)
    return ReadLease(read_key, read_request, lease.reserved_us if reserved is None else reserved)


def _next_generation(value, *, state):
    original = request(11, issued=500, expires=1000)
    grant = value.reserve_write(0, original, identifier(21), now_us=510).value
    if state == "WRITING":
        return grant
    from xenova_vehicle.runtime.raw_leases import FilledInfo
    info = FilledInfo(grant.key, 960000, "c" * 64, 520)
    value.filled(info, now_us=530)
    if state == "IN_USE":
        return read_lease(value, request_number=31, token_number=41,
                          issued=540, expires=900, reserved=550)
    return value.begin_replace(0, grant.key, request(12, issued=540, expires=900),
                               identifier(22), now_us=550).value


class RawCleanupTests(unittest.TestCase):
    def assert_atomic_error(self, value, operation):
        before = value.snapshot
        with self.assertRaises(LeaseLedgerError):
            operation()
        self.assertEqual(value.snapshot, before)

    def test_remote_record_is_exact_immutable_report_without_fabricated_local_stamps(self):
        _, _, _, lease = _active()
        for completed in (lease.reserved_us, 401, _MAX):
            report = RemoteReturnStatement(lease, completed)
            self.assertIs(report.lease, lease)
            self.assertEqual(report.completed_us, completed)
            self.assertFalse(hasattr(report, "views_ended_us"))
            self.assertFalse(hasattr(report, "writable_posted_us"))
            with self.assertRaises(FrozenInstanceError):
                report.completed_us = 0
        for bad in (149, -1, _MAX + 1, True, False, 401.0, "401", None):
            with self.assertRaises(LeaseLedgerError):
                RemoteReturnStatement(lease, bad)
        with self.assertRaises(LeaseLedgerError):
            RemoteReturnStatement({}, 401)

    def test_local_first_and_remote_first_have_one_applied_one_corroboration_then_exact_duplicates(self):
        for first_kind in ("local", "remote"):
            value, _, _, lease = _active()
            local, remote = _local(lease), _remote(lease)
            primary, opposite = (local, remote) if first_kind == "local" else (remote, local)
            initial = _apply(value, primary)
            self.assertIs(initial.status, LeaseStatus.APPLIED)
            self.assertIsNone(initial.value)
            before = value.snapshot
            summary = before.slots[0].cleanup
            self.assertIs(summary.fact, primary)
            self.assertIsNone(summary.opposite)
            result = _apply(value, opposite)
            self.assertIs(result.status, LeaseStatus.CLEANUP_CORROBORATED)
            self.assertIsNone(result.value)
            after = value.snapshot
            self.assertIs(after.slots[0].state, SlotState.FREE)
            self.assertIsNone(after.slots[0].read)
            self.assertIs(after.slots[0].cleanup.fact, primary)
            self.assertIs(after.slots[0].cleanup.opposite, opposite)
            self.assertNotEqual(after, before)
            self.assertEqual(after, replace(before, slots=(replace(before.slots[0], cleanup=after.slots[0].cleanup), before.slots[1])))
            for duplicate in (primary, opposite, primary, opposite):
                self.assertIs(_apply(value, duplicate, now=600).status, LeaseStatus.DUPLICATE_NO_EFFECT)
                self.assertEqual(value.snapshot, after)

    def test_public_summary_preserves_primary_and_accepts_equal_post_report_boundary(self):
        _, _, _, lease = _active()
        local, remote = _local(lease), _remote(lease, completed=400)
        for primary, opposite in ((local, remote), (remote, local)):
            summary = CleanupSummary(primary, opposite)
            self.assertIs(summary.fact, primary)
            self.assertIs(summary.opposite, opposite)
            with self.assertRaises(FrozenInstanceError):
                summary.opposite = None
        for first, second in ((local, local), (remote, remote), (local, _remote(lease, completed=399)),
                              (_remote(lease, completed=399), local)):
            with self.assertRaises(LeaseLedgerError):
                CleanupSummary(first, second)

    def test_corroboration_result_requires_none_without_changing_other_status_value_contract(self):
        value, grant, _, lease = _active()
        other = ledger()
        _, info = write_ready(other)
        ticket = other.begin_replace(0, info.key, request(11, issued=140, expires=300), identifier(21), now_us=150).value
        result = LeaseResult(LeaseStatus.CLEANUP_CORROBORATED)
        self.assertIsNone(result.value)
        for retained in (grant, lease, ticket, PendingReplacement(ticket)):
            with self.assertRaises(LeaseLedgerError):
                LeaseResult(LeaseStatus.CLEANUP_CORROBORATED, retained)
        self.assertIs(LeaseResult(LeaseStatus.APPLIED, grant).value, grant)
        self.assertIs(LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT, lease).value, lease)

    def test_replacement_summary_cannot_hold_return_opposite_or_change_existing_meaning(self):
        value = ledger()
        _, info = write_ready(value)
        ticket = value.begin_replace(0, info.key, request(11, issued=140, expires=300), identifier(21), now_us=150).value
        sync = ReplacementSyncFact(ticket, 151, 152, 153)
        _, _, _, lease = _active()
        self.assertIs(CleanupSummary(sync).fact, sync)
        self.assertIsNone(CleanupSummary(sync).opposite)
        for opposite in (_local(lease), _remote(lease), sync):
            with self.assertRaises(LeaseLedgerError):
                CleanupSummary(sync, opposite)
        for primary in (_local(lease), _remote(lease)):
            with self.assertRaises(LeaseLedgerError):
                CleanupSummary(primary, sync)

    def test_summary_requires_same_full_lease_not_only_same_read_key(self):
        _, _, _, lease = _active()
        local = _local(lease)
        foreign = _alter_lease(lease, request_changes={"cap_us": 300})
        self.assertEqual(foreign.key, lease.key)
        self.assertNotEqual(foreign, lease)
        for first, second in ((local, _remote(foreign)), (_remote(foreign), local)):
            with self.assertRaises(LeaseLedgerError):
                CleanupSummary(first, second)

    def test_same_variant_changed_metadata_conflicts_before_and_after_opposite(self):
        for first_kind in ("local", "remote"):
            value, _, _, lease = _active()
            local, remote = _local(lease), _remote(lease)
            primary, opposite = (local, remote) if first_kind == "local" else (remote, local)
            _apply(value, primary)
            changed_primary = _local(lease, ended=391) if first_kind == "local" else _remote(lease, completed=402)
            self.assert_atomic_error(value, lambda: _apply(value, changed_primary))
            _apply(value, opposite)
            for changed in (_local(lease, ended=391), _local(lease, posted=399), _remote(lease, completed=402)):
                self.assert_atomic_error(value, lambda changed=changed: _apply(value, changed))

    def test_post_after_remote_report_conflicts_in_both_arrival_orders_without_rollback(self):
        for first_kind in ("local", "remote"):
            value, _, _, lease = _active()
            local, remote = _local(lease, posted=410), _remote(lease, completed=400)
            primary, opposite = (local, remote) if first_kind == "local" else (remote, local)
            self.assertIs(_apply(value, primary).status, LeaseStatus.APPLIED)
            before = value.snapshot
            self.assert_atomic_error(value, lambda: _apply(value, opposite))
            self.assertIs(value.snapshot.slots[0].state, SlotState.FREE)
            self.assertEqual(value.snapshot, before)

    def test_changed_original_request_cap_purpose_pub_content_and_reservation_are_conflicts(self):
        value, _, _, lease = _active()
        variants = (
            _alter_lease(lease, request_changes={"request_id": identifier(99)}),
            _alter_lease(lease, request_changes={"issued_us": 141}),
            _alter_lease(lease, request_changes={"expires_us": 301}),
            _alter_lease(lease, request_changes={"cap_us": 300}),
            _alter_lease(lease, purpose="METRIC_MAP"),
            _alter_lease(lease, publication="c" * 64), _alter_lease(lease, reserved=151),
            _alter_lease(lease, content_changes={"payload_length": 960001}),
            _alter_lease(lease, content_changes={"descriptor_sha256": "c" * 64}),
        )
        for retained in (False, True):
            if retained:
                value.release_remote(_remote(lease), now_us=500)
            for index, altered in enumerate(variants):
                self.assertEqual(altered.key, lease.key)
                with self.subTest(retained=retained, index=index):
                    self.assert_atomic_error(value, lambda altered=altered: value.release_remote(_remote(altered), now_us=500))
                    self.assert_atomic_error(value, lambda altered=altered: value.release(_local(altered), now_us=500))

    def test_future_event_is_rejected_before_active_duplicate_and_unknown_paths(self):
        value, _, _, lease = _active()
        remote, local = _remote(lease), _local(lease)
        self.assert_atomic_error(value, lambda: value.release_remote(remote, now_us=400))
        self.assert_atomic_error(value, lambda: value.release(local, now_us=399))
        value.release_remote(remote, now_us=500)
        self.assert_atomic_error(value, lambda: value.release_remote(remote, now_us=400))
        value.release(local, now_us=500)
        self.assert_atomic_error(value, lambda: value.release(local, now_us=399))
        other_key = replace(lease.key.write_key, generation=99)
        unknown = _alter_lease(lease, key=other_key)
        self.assert_atomic_error(value, lambda: value.release_remote(_remote(unknown, completed=700), now_us=699))
        self.assert_atomic_error(value, lambda: value.release(_local(unknown, ended=690, posted=700), now_us=699))

    def test_read_expiry_fence_and_retirement_allow_only_original_cleanup(self):
        for retired in (False, True):
            value, _, _, lease = _active()
            value.fence(now_us=350, retired=retired)
            before = value.snapshot
            self.assertFalse(value.read_is_current(lease, now_us=400))
            self.assertIs(value.release_remote(_remote(lease), now_us=500).status, LeaseStatus.APPLIED)
            self.assertIs(value.release(_local(lease), now_us=500).status, LeaseStatus.CLEANUP_CORROBORATED)
            self.assertTrue(value.snapshot.fenced)
            self.assertEqual(value.snapshot.retired, retired)
            self.assertEqual(value.snapshot.fenced_us, before.fenced_us)
            self.assertFalse(value.read_is_current(lease, now_us=500))
            self.assertIs(value.snapshot.slots[0].state, SlotState.FREE)

    def test_old_opposite_preserves_gen2_writing_inuse_and_replacement_pending(self):
        for state in ("WRITING", "IN_USE", "PENDING"):
            for first_kind in ("local", "remote"):
                for fenced in (False, True):
                    value, _, _, lease = _active()
                    local, remote = _local(lease), _remote(lease)
                    primary, opposite = (local, remote) if first_kind == "local" else (remote, local)
                    _apply(value, primary)
                    _next_generation(value, state=state)
                    if fenced:
                        value.fence(now_us=560, retired=True)
                    before = value.snapshot
                    invalid = _remote(lease, completed=399) if first_kind == "local" else _local(lease, posted=402)
                    with self.subTest(state=state, first_kind=first_kind, fenced=fenced):
                        self.assert_atomic_error(value, lambda: _apply(value, invalid, now=600))
                        result = _apply(value, opposite, now=600)
                        self.assertIs(result.status, LeaseStatus.CLEANUP_CORROBORATED)
                        after = value.snapshot
                        expected = replace(before, slots=(replace(before.slots[0], cleanup=after.slots[0].cleanup), before.slots[1]))
                        self.assertEqual(after, expected)
                        self.assertEqual(after.slots[0].generation, 2)
                        self.assertEqual(after.slots[0].cleanup, CleanupSummary(primary, opposite))

    def test_opposite_preserves_nonnull_fault_and_other_slot_replacement_records(self):
        value, _, _, lease = _active()
        value.release_remote(_remote(lease), now_us=500)
        _next_generation(value, state="IN_USE")
        _, info = write_ready(value, slot=1, request_number=20, token_number=30, issued=560,
                             expires=800, granted=570, completed=580, received=590)
        ticket = value.begin_replace(1, info.key, request(21, issued=600, expires=800),
                                     identifier(31), now_us=610).value
        failed = ReplacementSyncFact(ticket, None, None, 620)
        self.assertIs(value.complete_replace(ticket, failed, now_us=620).status, LeaseStatus.REPLACEMENT_FAILED)
        before = value.snapshot
        self.assertIs(before.fault, ReplacementFault.PARTIAL)
        self.assertIs(value.release(_local(lease), now_us=650).status, LeaseStatus.CLEANUP_CORROBORATED)
        after = value.snapshot
        self.assertEqual(after, replace(before, slots=(replace(before.slots[0], cleanup=after.slots[0].cleanup), before.slots[1])))
        self.assertTrue(after.fenced)
        self.assertIs(after.fault, ReplacementFault.PARTIAL)

    def test_remote_primary_retains_qr_fingerprint_and_does_not_block_new_qr(self):
        value, _, _, old = _active(expires=1000)
        value.release_remote(_remote(old), now_us=500)
        grant, info = write_ready(value, request_number=11, token_number=21, issued=510,
                                 expires=800, granted=520, completed=530, received=540)
        offered = value.offer(0, now_us=550)
        reused = ReadRequest(offered, reader(), request(30, issued=550, expires=900))
        self.assert_atomic_error(value, lambda: value.acquire(reused, identifier(41), now_us=560))
        fresh = replace(reused, request=request(31, issued=550, expires=900))
        foreign = replace(fresh, reader=Endpoint(identifier(90), identifier(91)))
        self.assert_atomic_error(value, lambda: value.acquire(foreign, identifier(41), now_us=560))
        new = value.acquire(fresh, identifier(41), now_us=560).value
        self.assertEqual(new.key.write_key, grant.key)
        self.assertTrue(value.read_is_current(new, now_us=570))
        before = value.snapshot
        result = value.acquire(old.read_request, old.key.read_token, now_us=570)
        self.assertIs(result.status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(result.value, old)
        self.assertEqual(value.snapshot, before)
        changed = replace(old.read_request, request=replace(old.read_request.request, cap_us=1000))
        self.assert_atomic_error(value, lambda: value.acquire(changed, old.key.read_token, now_us=570))
        self.assert_atomic_error(value, lambda: value.acquire(old.read_request, identifier(99), now_us=570))
        self.assert_atomic_error(value, lambda: value.acquire(old.read_request, old.key.read_token, now_us=1000))
        value.fence(now_us=600)
        self.assert_atomic_error(value, lambda: value.acquire(old.read_request, old.key.read_token, now_us=601))

    def test_remote_primary_with_opposite_counts_one_retained_lease_and_preserves_one_reader_guard(self):
        value, _, _, old = _active(expires=1000)
        value.release_remote(_remote(old), now_us=500)
        value.release(_local(old), now_us=500)
        write_ready(value, request_number=11, token_number=21, issued=510,
                    expires=800, granted=520, completed=530, received=540)
        new = read_lease(value, request_number=31, token_number=41, issued=550, expires=900, reserved=560)
        _, second = write_ready(value, slot=1, request_number=12, token_number=22, issued=570,
                                expires=900, granted=580, completed=590, received=600)
        other = ReadRequest(value.offer(1, now_us=610), reader(), request(32, issued=600, expires=900))
        self.assert_atomic_error(value, lambda: value.acquire(other, identifier(42), now_us=610))
        self.assertEqual(value.snapshot.slots[0].read, new)
        self.assertEqual(value.snapshot.slots[0].cleanup, CleanupSummary(_remote(old), _local(old)))

    def test_new_terminal_cleanup_evicts_old_summary_without_recreating_history(self):
        value, _, _, old = _active()
        value.release_remote(_remote(old), now_us=500)
        new = _next_generation(value, state="IN_USE")
        newest = _local(new, ended=590, posted=600)
        value.release(newest, now_us=610)
        before = value.snapshot
        for fact in (_local(old), _remote(old)):
            self.assertIs(_apply(value, fact, now=700).status, LeaseStatus.UNKNOWN_NO_EFFECT)
            self.assertEqual(value.snapshot, before)
        self.assertEqual(value.snapshot.slots[0].cleanup, CleanupSummary(newest))
        self.assertEqual(sum(slot.cleanup is not None for slot in before.slots), 1)

    def test_replacement_cleanup_evicts_old_return_and_rejects_opposite_attachment(self):
        value, _, _, old = _active()
        value.release_remote(_remote(old), now_us=500)
        ticket = _next_generation(value, state="PENDING")
        sync = ReplacementSyncFact(ticket, 551, 552, 553)
        value.complete_replace(ticket, sync, now_us=554)
        before = value.snapshot
        for fact in (_local(old), _remote(old)):
            self.assertIs(_apply(value, fact, now=600).status, LeaseStatus.UNKNOWN_NO_EFFECT)
            self.assertEqual(value.snapshot, before)
        self.assertEqual(before.slots[0].cleanup, CleanupSummary(sync))
        self.assertIs(before.slots[0].state, SlotState.WRITING)
        self.assertEqual(before.slots[0].generation, 3)

    def test_unknown_other_key_and_reader_do_not_store_cleanup_or_free_current_lease(self):
        value, _, _, lease = _active()
        other = _alter_lease(lease, key=replace(lease.key.write_key, generation=2))
        foreign_reader = _alter_lease(lease, endpoint=Endpoint(identifier(91), identifier(92)))
        before = value.snapshot
        for changed in (other, foreign_reader):
            for fact in (_local(changed), _remote(changed)):
                self.assertIs(_apply(value, fact).status, LeaseStatus.UNKNOWN_NO_EFFECT)
                self.assertEqual(value.snapshot, before)
        self.assertIs(value.snapshot.slots[0].state, SlotState.IN_USE)
        self.assertIsNone(value.snapshot.slots[0].cleanup)

    def test_remote_and_local_public_methods_keep_exact_types_and_no_custom_hooks(self):
        value, _, _, lease = _active()
        local, remote = _local(lease), _remote(lease)
        self.assert_atomic_error(value, lambda: value.release(remote, now_us=500))
        self.assert_atomic_error(value, lambda: value.release_remote(local, now_us=500))
        calls = []

        class TypeTrap(type):
            def __eq__(self, other):
                calls.append("type.eq")
                raise AssertionError("native metaclass comparison must not run")

            def __hash__(self):
                calls.append("type.hash")
                raise AssertionError("native metaclass hash must not run")

        class ObjectTrap(metaclass=TypeTrap):
            def __str__(self):
                calls.append("object.str")
                raise AssertionError("native string conversion must not run")

        class RemoteSubclass(RemoteReturnStatement):
            pass

        bomb = ObjectTrap()
        for operation in (lambda: RemoteReturnStatement(bomb, 401),
                          lambda: RemoteReturnStatement(lease, bomb),
                          lambda: CleanupSummary(bomb), lambda: CleanupSummary(remote, bomb),
                          lambda: value.release_remote(bomb, now_us=500),
                          lambda: value.release_remote(RemoteSubclass(lease, 401), now_us=500)):
            self.assert_atomic_error(value, operation)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
