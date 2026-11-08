"""합성 raw lease record와 두 슬롯 원장의 정상·중복 경계. 실제 메모리/토큰/exit 증명은 아니다."""

from dataclasses import FrozenInstanceError, fields, replace
import unittest
from unittest.mock import patch

from raw_lease_fixtures import identifier, ledger, read_lease, reader, request, scope, write_ready
from xenova_vehicle.runtime import raw_leases
from xenova_vehicle.runtime.raw_leases import (
    ARENA_BYTES, METADATA_BYTES, SLOT_BYTES, CleanupSummary, Endpoint, FilledInfo,
    LedgerSnapshot, LeaseLedgerError, LeaseRequest, LeaseResult, LeaseStatus, Offer,
    PendingReplacement, RawLeaseLedger, RawScope, ReadKey, ReadLease, ReadRequest,
    ReplacementFault, ReplacementSyncFact, ReplacementTicket, SlotSnapshot, SlotState,
    UnsupportedPurpose, ViewReturnFact, WriteGrant, WriteKey,
)


_MAX = 2**64 - 1


def _return(lease, ended=160, posted=161):
    """실 view 종료가 아닌, 원 lease에 결박한 합성 lower 완료 진술이다."""
    return ViewReturnFact(lease, ended, posted)


def _read_request(value, *, slot=0, number=30, issued=140, expires=300, cap=None,
                  endpoint=None, publication=None, purpose="PARSER_WARMUP", now=150):
    return ReadRequest(value.offer(slot, now_us=now), reader() if endpoint is None else endpoint,
                       request(number, issued=issued, expires=expires, cap=cap), purpose, publication)


def _retarget(lease, *, key=None, endpoint=None, token=None, original=None):
    """각 record 자체는 일관되게 유지하며 원장과 다른 full key/원 요청을 만든다."""
    key = lease.key.write_key if key is None else key
    endpoint = lease.key.reader if endpoint is None else endpoint
    content = replace(lease.read_request.offer.content, key=key)
    selected = replace(lease.read_request, offer=Offer(content), reader=endpoint,
                       request=lease.read_request.request if original is None else original)
    read_key = ReadKey(key, endpoint, lease.key.read_token if token is None else token)
    return ReadLease(read_key, selected, lease.reserved_us)


class RawLeaseRecordTests(unittest.TestCase):
    def assert_bad(self, factory):
        with self.assertRaises(LeaseLedgerError):
            factory()

    def assert_unchanged_error(self, value, operation, error=LeaseLedgerError):
        before = value.snapshot
        with self.assertRaises(error):
            operation()
        self.assertEqual(value.snapshot, before)

    def test_scope_endpoint_and_two_slot_initial_metadata_are_exact_not_os_evidence(self):
        value = ledger()
        snapshot = value.snapshot
        self.assertEqual((snapshot.scope, snapshot.reader, snapshot.publication_sha256), (scope(), reader(), None))
        self.assertEqual(snapshot.scope.arena_size_bytes, 4 * 1024 * 1024)
        self.assertEqual((ARENA_BYTES, SLOT_BYTES, METADATA_BYTES), (4194304, 2097152, 4096))
        self.assertEqual(tuple((slot.state, slot.generation) for slot in snapshot.slots),
                         ((SlotState.FREE, 0), (SlotState.FREE, 0)))
        self.assertIs(type(snapshot.slots), tuple)
        self.assertFalse(snapshot.fenced)
        self.assertFalse(snapshot.retired)
        self.assertIsNone(snapshot.fault)
        self.assertFalse(any(hasattr(snapshot, name) for name in (
            "shm", "semaphore", "native_view", "actual_exit", "metric_ready", "nn_ready", "authorized")))
        # 이름 profile은 아직 printable ASCII뿐이며 OS 이름·자원 존재를 승인하지 않는다.
        self.assertEqual(replace(scope(), arena_name=" ").arena_name, " ")

    def test_uuid_sha_name_size_role_and_slot_native_ranges_are_constructor_checked(self):
        current = scope()
        malformed = (None, True, 1, "", "x" * 36, identifier(1).replace("-", ""),
                     "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "x" * 1_000_000)
        for name in ("channel_epoch", "worker_instance"):
            for bad in malformed:
                self.assert_bad(lambda name=name, bad=bad: replace(reader(), **{name: bad}))
        for name in ("service_instance", "arena_id"):
            for bad in malformed:
                self.assert_bad(lambda name=name, bad=bad: replace(current, **{name: bad}))
        for bad in (None, True, 1, "a" * 63, "a" * 65, "A" * 64, "g" * 64):
            self.assert_bad(lambda bad=bad: replace(current, raw_binding_sha256=bad))
        for bad in (None, True, 1, "", "a" * 129, "\x00", "\n", "\x7f", "장치"):
            self.assert_bad(lambda bad=bad: replace(current, arena_name=bad))
        for bad in (None, True, 1, 2 * 1024 * 1024, 4194304.0):
            self.assert_bad(lambda bad=bad: replace(current, arena_size_bytes=bad))
        for bad in (None, True, "raw", "COPY", "RAW "):
            self.assert_bad(lambda bad=bad: replace(current, arena_role=bad))
        self.assert_bad(lambda: replace(current, writer={}))
        for bad in (-1, 2, True, False, 0.0, "0", None):
            self.assert_bad(lambda bad=bad: WriteKey(current, bad, 1, identifier(20)))
        for slot in (0, 1):
            for generation in (1, _MAX):
                self.assertEqual(WriteKey(current, slot, generation, identifier(20)).generation, generation)
        for bad in (0, -1, _MAX + 1, True, 1.0, "1", None):
            self.assert_bad(lambda bad=bad: WriteKey(current, 0, bad, identifier(20)))
        key = WriteKey(current, 0, 1, identifier(20))
        for bad in malformed:
            self.assert_bad(lambda bad=bad: replace(key, write_token=bad))
            self.assert_bad(lambda bad=bad: ReadKey(key, reader(), bad))
        self.assert_bad(lambda: replace(key, scope={}))
        self.assert_bad(lambda: ReadKey(key, {}, identifier(40)))

    def test_original_request_has_half_open_u64_window_and_independent_cap(self):
        for issued, expires, cap in ((0, 1, None), (100, 200, 200), (100, 200, 250),
                                     (_MAX - 1, _MAX, _MAX)):
            original = request(issued=issued, expires=expires, cap=cap)
            original.check(issued)
            original.check(expires - 1)
            self.assertEqual((original.issued_us, original.expires_us, original.cap_us), (issued, expires, cap))
            self.assert_bad(lambda original=original, expires=expires: original.check(expires))
        for name in ("issued_us", "expires_us", "cap_us"):
            for bad in (False, True, -1, _MAX + 1, 1.0, "100", object()):
                self.assert_bad(lambda name=name, bad=bad: replace(request(), **{name: bad}))
        for issued, expires, cap in ((100, 100, None), (200, 100, None), (100, 200, 199)):
            self.assert_bad(lambda issued=issued, expires=expires, cap=cap:
                            request(issued=issued, expires=expires, cap=cap))
        self.assert_bad(lambda: request().check(99))
        self.assert_bad(lambda: replace(request(), request_id="not-a-request"))

    def test_large_native_scalars_are_rejected_before_uuid_regex_or_integer_conversion(self):
        huge = "x" * 1_000_000
        current = scope()
        with patch.object(raw_leases, "UUID", side_effect=AssertionError("UUID length guard must run first")):
            self.assert_bad(lambda: Endpoint(huge, identifier(5)))
            self.assert_bad(lambda: LeaseRequest(huge, 100, 200))
        with patch.object(raw_leases.re, "fullmatch", side_effect=AssertionError("SHA length guard must run first")):
            self.assert_bad(lambda: replace(current, raw_binding_sha256=huge))
        self.assert_bad(lambda: replace(current, arena_name=huge))
        self.assert_bad(lambda: LeaseRequest(identifier(10), 1 << 1_000_000, 200))

    def test_filled_and_offer_are_bounded_summaries_without_payload_or_read_token(self):
        key = WriteKey(scope(), 0, 1, identifier(20))
        for size in (1, SLOT_BYTES - METADATA_BYTES):
            info = FilledInfo(key, size, "b" * 64, 120)
            offer = Offer(info)
            self.assertEqual(offer.content, info)
            self.assertFalse(hasattr(offer, "read_token"))
            self.assertFalse(hasattr(info, "payload"))
        for bad in (0, -1, SLOT_BYTES - METADATA_BYTES + 1, True, 1.0, "1"):
            self.assert_bad(lambda bad=bad: FilledInfo(key, bad, "b" * 64, 120))
        for bad in (None, "B" * 64, "b" * 63):
            self.assert_bad(lambda bad=bad: FilledInfo(key, 960000, bad, 120))
        self.assert_bad(lambda: FilledInfo(key, 960000, "b" * 64, -1))
        self.assert_bad(lambda: Offer({"content": "unvalidated"}))

    def test_all_public_records_are_frozen_and_snapshot_tuples_have_no_mutable_alias(self):
        value = ledger()
        grant, info = write_ready(value)
        selected = _read_request(value)
        lease = value.acquire(selected, identifier(40), now_us=150).value
        returned = _return(lease)
        planned = replace(grant.key, generation=2, write_token=identifier(21))
        ticket = ReplacementTicket(info, request(11, issued=140, expires=300), planned, 150)
        sync = ReplacementSyncFact(ticket, 151, 152, 153)
        records = (scope(), reader(), grant.key, lease.key, request(), grant, info, Offer(info),
                   selected, lease, returned, ticket, sync, PendingReplacement(ticket),
                   CleanupSummary(returned), value.snapshot.slots[0], value.snapshot,
                   LeaseResult(LeaseStatus.APPLIED, grant))
        for record in records:
            name = fields(record)[0].name
            with self.subTest(record=type(record).__name__), self.assertRaises(FrozenInstanceError):
                setattr(record, name, None)
        before = value.snapshot
        exported_slots = list(before.slots)
        exported_slots[0] = SlotSnapshot()
        external = replace(before, slots=(SlotSnapshot(), SlotSnapshot()))
        self.assertEqual(value.snapshot, before)
        self.assertNotEqual(external, value.snapshot)
        self.assert_bad(lambda: replace(before, slots=list(before.slots)))
        self.assert_bad(lambda: replace(before, slots=(before.slots[0],)))

    def test_native_custom_types_and_metaclasses_are_rejected_without_hooks(self):
        calls = []

        def trap(name):
            calls.append(name)
            raise AssertionError("native hook must not execute: " + name)

        class StringTrap(str):
            def __len__(self):
                return trap("str.len")

            def __eq__(self, other):
                return trap("str.eq")

            def __hash__(self):
                return trap("str.hash")

        class IntegerTrap(int):
            def __le__(self, other):
                return trap("int.le")

            def __ge__(self, other):
                return trap("int.ge")

        class TypeTrap(type):
            def __eq__(self, other):
                return trap("type.eq")

            def __hash__(self):
                return trap("type.hash")

        class ObjectTrap(metaclass=TypeTrap):
            def __eq__(self, other):
                return trap("object.eq")

            def __str__(self):
                return trap("object.str")

            def __deepcopy__(self, memo):
                return trap("object.deepcopy")

        class EndpointSubclass(Endpoint):
            pass

        value = ledger()
        grant, info = write_ready(value)
        selected = _read_request(value)
        lease = value.acquire(selected, identifier(40), now_us=150).value
        ticket = ReplacementTicket(info, request(11, issued=140, expires=300),
                                   replace(grant.key, generation=2), 150)
        bomb = ObjectTrap()
        factories = (lambda: Endpoint(StringTrap(identifier(4)), identifier(5)),
                     lambda: replace(scope(), arena_name=StringTrap("raw")),
                     lambda: replace(scope(), raw_binding_sha256=StringTrap("a" * 64)),
                     lambda: replace(scope(), writer=bomb),
                     lambda: replace(scope(), writer=EndpointSubclass(identifier(2), identifier(3))),
                     lambda: replace(scope(), arena_size_bytes=IntegerTrap(ARENA_BYTES)),
                     lambda: WriteKey(scope(), IntegerTrap(0), 1, identifier(20)),
                     lambda: replace(grant.key, generation=IntegerTrap(1)),
                     lambda: LeaseRequest(identifier(10), IntegerTrap(100), 200),
                     lambda: replace(grant, request=bomb), lambda: replace(info, key=bomb),
                     lambda: Offer(bomb), lambda: replace(selected, reader=bomb),
                     lambda: replace(selected, purpose=StringTrap("PARSER_WARMUP")),
                     lambda: replace(lease, key=bomb), lambda: ViewReturnFact(bomb, 160, 161),
                     lambda: replace(ticket, request=bomb), lambda: ReplacementSyncFact(bomb, None, None, 150),
                     lambda: SlotSnapshot(state=bomb), lambda: LeaseResult(LeaseStatus.APPLIED, bomb),
                     lambda: RawLeaseLedger(bomb, reader()), lambda: RawLeaseLedger(scope(), bomb))
        for index, factory in enumerate(factories):
            with self.subTest(index=index):
                self.assert_bad(factory)
        self.assertEqual(calls, [])

    def test_record_constructors_reject_mismatched_nested_identity_and_event_order(self):
        value = ledger()
        grant, info = write_ready(value)
        selected = _read_request(value)
        lease = value.acquire(selected, identifier(40), now_us=150).value
        self.assert_bad(lambda: replace(grant, granted_us=99))
        self.assert_bad(lambda: replace(grant, granted_us=200))
        self.assert_bad(lambda: replace(lease, key=replace(lease.key, reader=Endpoint(identifier(70), identifier(71)))))
        self.assert_bad(lambda: replace(lease, key=replace(lease.key, write_key=replace(grant.key, generation=2))))
        self.assert_bad(lambda: replace(lease, reserved_us=119))
        self.assert_bad(lambda: replace(lease, reserved_us=300))
        self.assert_bad(lambda: ViewReturnFact(lease, 149, 151))
        self.assert_bad(lambda: ViewReturnFact(lease, 160, 159))
        self.assert_bad(lambda: replace(value.snapshot.slots[0], generation=2))
        self.assert_bad(lambda: SlotSnapshot(state=SlotState.WRITING))
        self.assert_bad(lambda: SlotSnapshot(state=SlotState.READY))
        self.assert_bad(lambda: SlotSnapshot(state=SlotState.IN_USE))
        self.assert_bad(lambda: replace(value.snapshot, reader=Endpoint(identifier(70), identifier(71))))
        self.assert_bad(lambda: replace(value.snapshot, fenced=True))
        self.assert_bad(lambda: replace(value.snapshot, retired=True))
        self.assert_bad(lambda: replace(value.snapshot, fault=ReplacementFault.PARTIAL))
        self.assert_bad(lambda: replace(value.snapshot, slots=(value.snapshot.slots[1], value.snapshot.slots[0])))

    def test_normal_state_chain_has_separate_writer_and_reader_channels_and_no_offer_grant(self):
        value = ledger()
        result = value.reserve_write(0, request(), identifier(20), now_us=110)
        self.assertIs(result.status, LeaseStatus.APPLIED)
        grant = result.value
        self.assertEqual((grant.key.slot, grant.key.generation, grant.granted_us), (0, 1, 110))
        self.assertEqual(grant.key.scope.writer, scope().writer)
        self.assertIs(value.snapshot.slots[0].state, SlotState.WRITING)
        self.assertIs(value.filled(FilledInfo(grant.key, 960000, "b" * 64, 120), now_us=130).status,
                      LeaseStatus.APPLIED)
        offered = value.offer(0, now_us=140)
        self.assertIs(value.snapshot.slots[0].state, SlotState.READY)
        self.assertIsNone(value.snapshot.slots[0].read)
        self.assertFalse(hasattr(offered, "read_token"))
        selected = ReadRequest(offered, reader(), request(30, issued=140, expires=300))
        self.assertFalse(hasattr(selected, "read_token"))
        acquired = value.acquire(selected, identifier(40), now_us=150)
        self.assertIs(acquired.status, LeaseStatus.APPLIED)
        lease = acquired.value
        self.assertIs(value.snapshot.slots[0].state, SlotState.IN_USE)
        self.assertEqual(lease.key.write_key, grant.key)
        self.assertEqual(lease.key.reader, reader())
        self.assertNotEqual(lease.key.reader.channel_epoch, grant.key.scope.writer.channel_epoch)
        self.assertTrue(value.read_is_current(lease, now_us=150))
        self.assertIs(value.release(_return(lease), now_us=162).status, LeaseStatus.APPLIED)
        self.assertIs(value.snapshot.slots[0].state, SlotState.FREE)
        self.assertFalse(value.read_is_current(lease, now_us=162))

    def test_one_writer_marker_survives_dequeue_assumptions_and_invalid_filled(self):
        value = ledger()
        grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
        before = value.snapshot
        duplicate = value.reserve_write(0, request(), identifier(20), now_us=111)
        self.assertIs(duplicate.status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(duplicate.value, grant)
        self.assertEqual(value.snapshot, before)
        self.assert_unchanged_error(value, lambda: value.reserve_write(1, request(11), identifier(21), now_us=112))
        self.assert_unchanged_error(value, lambda: value.filled(FilledInfo(grant.key, 960000, "b" * 64, 109), now_us=113))
        self.assert_unchanged_error(value, lambda: value.filled(FilledInfo(grant.key, 960000, "b" * 64, 190), now_us=200))
        self.assertIs(value.snapshot.slots[0].state, SlotState.WRITING)
        value.filled(FilledInfo(grant.key, 960000, "b" * 64, 120), now_us=130)
        second = value.reserve_write(1, request(11), identifier(21), now_us=140)
        self.assertIs(second.status, LeaseStatus.APPLIED)
        self.assertEqual(second.value.key.generation, 1)

    def test_write_current_uses_grant_lower_bound_original_expiry_and_writing_state(self):
        value = ledger()
        grant = value.reserve_write(0, request(), identifier(20), now_us=110).value
        for now, expected in ((109, False), (110, True), (199, True), (200, False)):
            with self.subTest(now=now):
                self.assertIs(value.write_is_current(grant, now_us=now), expected)
        forged = replace(grant, request=replace(grant.request, request_id=identifier(99)))
        self.assertFalse(value.write_is_current(forged, now_us=120))
        value.filled(FilledInfo(grant.key, 960000, "b" * 64, 120), now_us=130)
        self.assertFalse(value.write_is_current(grant, now_us=140))
        self.assertIs(value.snapshot.slots[0].state, SlotState.READY)

    def test_held_write_request_conflicts_pin_slot_token_times_and_original_cap(self):
        value = ledger()
        value.reserve_write(0, request(), identifier(20), now_us=110)
        originals = (request(issued=101), request(expires=201), request(cap=200))
        for changed in originals:
            self.assert_unchanged_error(value, lambda changed=changed:
                                        value.reserve_write(0, changed, identifier(20), now_us=111))
        self.assert_unchanged_error(value, lambda: value.reserve_write(1, request(), identifier(20), now_us=111))
        self.assert_unchanged_error(value, lambda: value.reserve_write(0, request(), identifier(21), now_us=111))

    def test_filled_duplicate_is_original_summary_not_new_completion_or_state_reset(self):
        value = ledger()
        grant, info = write_ready(value)
        lease = read_lease(value)
        before = value.snapshot
        self.assertIs(value.filled(info, now_us=500).status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        for changed in (replace(info, payload_length=960001), replace(info, descriptor_sha256="c" * 64),
                        replace(info, completed_us=121)):
            self.assert_unchanged_error(value, lambda changed=changed: value.filled(changed, now_us=500))
        self.assertEqual(value.snapshot.slots[0].read, lease)
        self.assertIs(value.snapshot.slots[0].state, SlotState.IN_USE)

    def test_each_write_key_component_is_checked_before_accepting_filled_or_return(self):
        value = ledger()
        grant, info = write_ready(value)
        lease = read_lease(value)
        original = grant.key
        variants = (replace(original, scope=replace(scope(), service_instance=identifier(70))),
                    replace(original, scope=replace(scope(), arena_id=identifier(71))),
                    replace(original, scope=replace(scope(), writer=replace(scope().writer, channel_epoch=identifier(72)))),
                    replace(original, scope=replace(scope(), writer=replace(scope().writer, worker_instance=identifier(73)))),
                    replace(original, scope=replace(scope(), raw_binding_sha256="c" * 64)),
                    replace(original, scope=replace(scope(), arena_name="another-raw")),
                    replace(original, slot=1), replace(original, generation=2),
                    replace(original, write_token=identifier(74)))
        for index, key in enumerate(variants):
            with self.subTest(index=index):
                self.assert_unchanged_error(value, lambda key=key: value.filled(replace(info, key=key), now_us=160))
                before = value.snapshot
                foreign = _retarget(lease, key=key)
                try:
                    result = value.release(_return(foreign), now_us=162)
                except LeaseLedgerError:
                    pass
                else:
                    self.assertIs(result.status, LeaseStatus.UNKNOWN_NO_EFFECT)
                self.assertEqual(value.snapshot, before)
        for endpoint in (replace(reader(), channel_epoch=identifier(80)),
                         replace(reader(), worker_instance=identifier(81))):
            foreign = _retarget(lease, endpoint=endpoint)
            self.assertIs(value.release(_return(foreign), now_us=162).status, LeaseStatus.UNKNOWN_NO_EFFECT)
        foreign = _retarget(lease, token=identifier(82))
        self.assertIs(value.release(_return(foreign), now_us=162).status, LeaseStatus.UNKNOWN_NO_EFFECT)
        self.assertTrue(value.read_is_current(lease, now_us=170))

    def test_acquire_validates_offer_summary_and_registered_reader_before_reserving(self):
        for changed in ("payload", "sha", "completed", "reader_channel", "reader_worker"):
            value = ledger()
            write_ready(value)
            selected = _read_request(value)
            if changed in ("payload", "sha", "completed"):
                updates = {"payload": {"payload_length": 960001}, "sha": {"descriptor_sha256": "c" * 64},
                           "completed": {"completed_us": 121}}[changed]
                selected = replace(selected, offer=Offer(replace(selected.offer.content, **updates)))
            else:
                name = "channel_epoch" if changed == "reader_channel" else "worker_instance"
                selected = replace(selected, reader=replace(reader(), **{name: identifier(80)}))
            self.assert_unchanged_error(value, lambda: value.acquire(selected, identifier(40), now_us=150))

    def test_acquire_duplicate_pins_request_content_reader_purpose_publication_and_token(self):
        value = ledger(publication="c" * 64)
        write_ready(value)
        selected = _read_request(value, publication="c" * 64)
        lease = value.acquire(selected, identifier(40), now_us=150).value
        before = value.snapshot
        duplicate = value.acquire(selected, identifier(40), now_us=160)
        self.assertIs(duplicate.status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertIs(duplicate.value, lease)
        self.assertEqual(value.snapshot, before)
        for changed in (replace(selected, request=replace(selected.request, issued_us=141)),
                        replace(selected, request=replace(selected.request, expires_us=301)),
                        replace(selected, request=replace(selected.request, cap_us=300)),
                        replace(selected, publication_sha256=None)):
            self.assert_unchanged_error(value, lambda changed=changed:
                                        value.acquire(changed, identifier(40), now_us=160))
        self.assert_unchanged_error(value, lambda: value.acquire(selected, identifier(41), now_us=160))
        self.assert_unchanged_error(value, lambda: value.acquire(selected, identifier(40), now_us=300))

    def test_parser_publication_is_optional_but_supplied_value_must_match_registered_context(self):
        for registered, supplied in ((None, None), ("c" * 64, None), ("c" * 64, "c" * 64)):
            value = ledger(publication=registered)
            write_ready(value)
            result = value.acquire(_read_request(value, publication=supplied), identifier(40), now_us=150)
            self.assertIs(result.status, LeaseStatus.APPLIED)
            self.assertEqual(result.value.read_request.publication_sha256, supplied)
        for registered, supplied in ((None, "c" * 64), ("c" * 64, "d" * 64)):
            value = ledger(publication=registered)
            write_ready(value)
            selected = _read_request(value, publication=supplied)
            self.assert_unchanged_error(value, lambda: value.acquire(selected, identifier(40), now_us=150))

    def test_metric_map_remains_explicitly_unsupported_without_state_change(self):
        value = ledger(publication="c" * 64)
        write_ready(value)
        selected = _read_request(value, publication="c" * 64, purpose="METRIC_MAP")
        self.assert_unchanged_error(value, lambda: value.acquire(selected, identifier(40), now_us=150), UnsupportedPurpose)
        self.assertIs(value.snapshot.slots[0].state, SlotState.READY)
        self.assertIsNone(value.snapshot.slots[0].read)
        self.assert_bad(lambda: replace(selected, purpose="METRIC_MAP "))

    def test_completed_write_and_new_parser_read_have_independent_original_deadlines(self):
        value = ledger()
        grant, _ = write_ready(value)
        selected = _read_request(value, issued=210, expires=300, cap=310, now=220)
        lease = value.acquire(selected, identifier(40), now_us=220).value
        self.assertEqual(grant.request.expires_us, 200)
        self.assertEqual(lease.read_request.request, request(30, issued=210, expires=300, cap=310))
        self.assertTrue(value.read_is_current(lease, now_us=299))
        self.assertFalse(value.read_is_current(lease, now_us=300))
        self.assertIs(value.snapshot.slots[0].state, SlotState.IN_USE)
        self.assertIs(value.release(_return(lease, 301, 302), now_us=303).status, LeaseStatus.APPLIED)

    def test_known_read_request_tombstone_remains_distinct_from_new_active_read(self):
        value = ledger()
        write_ready(value)
        first_request = _read_request(value, expires=500)
        first = value.acquire(first_request, identifier(40), now_us=150).value
        value.release(_return(first), now_us=162)
        write_ready(value, slot=1, request_number=11, token_number=21, issued=170, expires=250,
                    granted=180, completed=190, received=200)
        second_request = _read_request(value, slot=1, number=31, issued=210, expires=500, now=220)
        second = value.acquire(second_request, identifier(41), now_us=220).value
        before = value.snapshot
        replay = value.acquire(first_request, identifier(40), now_us=230)
        self.assertIs(replay.status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(replay.value, first)
        self.assertEqual(value.snapshot, before)
        self.assertTrue(value.read_is_current(second, now_us=230))
        changed = replace(first_request, request=replace(first_request.request, expires_us=501))
        self.assert_unchanged_error(value, lambda: value.acquire(changed, identifier(40), now_us=230))

    def test_same_read_key_with_changed_original_request_or_return_fact_is_conflict(self):
        value = ledger()
        write_ready(value)
        lease = read_lease(value)
        forged = _retarget(lease, original=replace(lease.read_request.request, request_id=identifier(99)))
        self.assert_unchanged_error(value, lambda: value.release(_return(forged), now_us=162))
        original = _return(lease)
        value.release(original, now_us=162)
        before = value.snapshot
        self.assertIs(value.release(original, now_us=500).status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        self.assert_unchanged_error(value, lambda: value.release(_return(lease, 163, 164), now_us=500))

    def test_one_active_reader_and_unconfirmed_or_expired_input_never_frees_lease(self):
        value = ledger()
        write_ready(value)
        write_ready(value, slot=1, request_number=11, token_number=21,
                    granted=131, completed=132, received=133)
        selected = _read_request(value)
        lease = value.acquire(selected, identifier(40), now_us=150).value
        other = _read_request(value, slot=1, number=31)
        self.assert_unchanged_error(value, lambda: value.acquire(other, identifier(41), now_us=151))
        self.assert_unchanged_error(value, lambda: value.reserve_write(0, request(12), identifier(22), now_us=152))
        self.assertFalse(value.read_is_current(lease, now_us=300))
        self.assertIs(value.snapshot.slots[0].state, SlotState.IN_USE)
        self.assert_unchanged_error(value, lambda: value.release(_return(lease), now_us=160))
        self.assert_bad(lambda: value.release({"lease": lease, "views_ended": True}, now_us=162))

    def test_recent_write_summary_is_not_reissued_as_new_grant_after_slot_becomes_free(self):
        value = ledger()
        grant, _ = write_ready(value)
        lease = read_lease(value)
        value.release(_return(lease), now_us=162)
        before = value.snapshot
        same = value.reserve_write(0, request(), identifier(20), now_us=170)
        self.assertIs(same.status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(same.value, grant)
        self.assertEqual(value.snapshot, before)
        self.assertIs(value.snapshot.slots[0].state, SlotState.FREE)
        self.assert_unchanged_error(value, lambda: value.reserve_write(1, request(), identifier(20), now_us=170))
        self.assert_unchanged_error(value, lambda: value.reserve_write(0, request(expires=201), identifier(20), now_us=170))

    def test_many_logical_jobs_keep_two_slots_one_cleanup_each_and_no_full_history_promise(self):
        value = ledger()
        earliest = None
        for number in range(60):
            stamp, slot = 100 + 100 * number, number % 2
            grant, _ = write_ready(value, slot=slot, request_number=1000 + number,
                                  token_number=2000 + number, issued=stamp, expires=stamp + 40,
                                  granted=stamp + 1, completed=stamp + 2, received=stamp + 3)
            selected = _read_request(value, slot=slot, number=3000 + number, issued=stamp + 4,
                                     expires=stamp + 80, now=stamp + 5)
            lease = value.acquire(selected, identifier(4000 + number), now_us=stamp + 5).value
            fact = _return(lease, stamp + 6, stamp + 7)
            value.release(fact, now_us=stamp + 8)
            earliest = fact if earliest is None else earliest
            snapshot = value.snapshot
            self.assertEqual(len(snapshot.slots), 2)
            self.assertTrue(all(item.read is None and item.pending is None for item in snapshot.slots))
            self.assertLessEqual(sum(item.cleanup is not None for item in snapshot.slots), 2)
            self.assertEqual(grant.key.generation, number // 2 + 1)
        before = value.snapshot
        self.assertIs(value.release(earliest, now_us=6200).status, LeaseStatus.UNKNOWN_NO_EFFECT)
        self.assertEqual(value.snapshot, before)
        self.assertEqual(tuple(item.generation for item in before.slots), (30, 30))


if __name__ == "__main__":
    unittest.main()
