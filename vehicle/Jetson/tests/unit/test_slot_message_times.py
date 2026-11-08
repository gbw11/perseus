"""합성 SLOT의 원 시각·parent·local cap 결합. 실제 예약/수신 now/전송·소유권 증명은 아니다."""

from dataclasses import FrozenInstanceError, replace
import unittest

from slot_message_fixtures import (
    chain, limits, make, packet, reader_context, slot_fields, writer_context,
)
from xenova_vehicle.runtime.slot_messages import (
    ReaderSlotContext, SlotMessage, SlotMessageError, SlotTimeLimits, WriterSlotContext,
    parse_slot_message,
)


_MAX = 2**64 - 1
_IW = 10000000
_G = _IW + 20
_P = _IW + 40
_EW = _IW + 1000
_EO = _P + 500
_IR = _IW + 50
_ER = _IW + 200
_GR = _IW + 60


class SlotMessageTimeTests(unittest.TestCase):
    def assert_rejected(self, op, ctx, parent=None, *, selected=None, cap_us=None, **wire):
        selected = limits() if selected is None else selected
        source = packet(slot_fields(op, ctx, **wire))
        for entry in (parse_slot_message, SlotMessage):
            with self.subTest(entry=entry.__name__), self.assertRaises(SlotMessageError):
                entry(source, ctx, parent, limits=selected, cap_us=cap_us)

    def test_six_stage_chain_preserves_each_original_event_and_c1_parent_record(self):
        stages = chain()
        request, grant, filled = (stages[name] for name in ("REQUEST", "GRANT", "FILLED"))
        offer, acquire, acquired = (stages[name] for name in ("OFFER", "ACQUIRE", "ACQUIRED"))
        expected = {"REQUEST": (_IW, _EW), "GRANT": (_G, _EW), "FILLED": (_P, _EW),
                    "OFFER": (_P, _EO), "ACQUIRE": (_IR, _ER), "ACQUIRED": (_GR, _ER)}
        for op, value in stages.items():
            self.assertEqual((value.op, value.issued_us, value.expires_us), (op, *expected[op]))
            self.assertEqual(value.raw, value.packet.raw_bytes)
            self.assertFalse(any(hasattr(value, name) for name in (
                "now_us", "current", "authorized", "nn_ready", "actual_reservation", "highwater")))
        self.assertIsNone(request.parent)
        self.assertIsNone(offer.parent)
        for child, parent in ((grant, request), (filled, grant), (acquire, offer), (acquired, acquire)):
            self.assertIs(child.parent, parent)
        self.assertEqual(request.record.issued_us, _IW)
        self.assertEqual(grant.record.request, request.record)
        self.assertEqual(grant.record.granted_us, _G)
        self.assertEqual(filled.record.completed_us, _P)
        self.assertEqual(offer.record.content, filled.record)
        self.assertEqual(acquire.record.offer, offer.record)
        self.assertEqual(acquire.record.request.issued_us, _IR)
        self.assertEqual(acquired.record.read_request, acquire.record)
        self.assertEqual(acquired.record.reserved_us, _GR)
        self.assertEqual(acquired.record.key.write_key, grant.record.key)

    def test_limits_are_mandatory_exact_positive_u64_with_read_maximum_150000(self):
        for copy, offer, read in ((1, 1, 1), (1000, 500, 150000), (_MAX, _MAX, 150000)):
            value = SlotTimeLimits(copy, offer, read)
            self.assertEqual((value.copy_budget_us, value.offer_budget_us, value.read_budget_us),
                             (copy, offer, read))
            with self.assertRaises(FrozenInstanceError):
                value.read_budget_us = 1
        for field in ("copy_budget_us", "offer_budget_us", "read_budget_us"):
            for bad in (0, -1, True, False, _MAX + 1, 1.0, "1", None, object()):
                original = dict(copy_budget_us=1000, offer_budget_us=500, read_budget_us=150)
                original[field] = bad
                with self.subTest(field=field, bad_type=type(bad).__name__), self.assertRaises(SlotMessageError):
                    SlotTimeLimits(**original)
        with self.assertRaises(SlotMessageError):
            SlotTimeLimits(1000, 500, 150001)
        ctx = writer_context()
        source = packet(slot_fields("REQUEST", ctx))
        for entry in (parse_slot_message, SlotMessage):
            with self.assertRaises(TypeError):
                entry(source, ctx)
            for bad in (None, {}, (1000, 500, 150), object()):
                with self.assertRaises(SlotMessageError):
                    entry(source, ctx, limits=bad)

    def test_slot_operation_times_are_not_limited_by_expired_bind_handshake(self):
        stages = chain()
        writer = stages["REQUEST"].context
        reader = stages["OFFER"].context
        self.assertGreater(_IW, writer.bind.expires_us)
        self.assertGreater(_P, reader.bind.expires_us)
        self.assertEqual(stages["REQUEST"].record.issued_us, _IW)
        self.assertEqual(stages["ACQUIRED"].record.reserved_us, _GR)

    def test_explicit_custom_limits_drive_all_windows_without_fixture_default_values(self):
        writer = writer_context()
        reader = reader_context(writer)
        selected = SlotTimeLimits(17, 23, 150000)
        issued, write_end = 20000000, 20000017
        completed = issued + 2
        notice_end = completed + 23
        read_start, read_end = completed + 1, completed + 1 + 150000
        request = make("REQUEST", writer, limits=selected, issued=issued, expires=write_end)
        grant = make("GRANT", writer, request, limits=selected, issued=issued + 1, expires=write_end)
        filled = make("FILLED", writer, grant, limits=selected, issued=completed, expires=write_end)
        offer = make("OFFER", reader, limits=selected, issued=completed, expires=notice_end)
        acquire = make("ACQUIRE", reader, offer, limits=selected, issued=read_start, expires=read_end)
        acquired = make("ACQUIRED", reader, acquire, limits=selected, issued=read_start + 1, expires=read_end)
        self.assertEqual(filled.record.completed_us, completed)
        self.assertEqual(acquired.record.read_request.request.expires_us - read_start, 150000)
        self.assertGreater(acquired.expires_us, offer.expires_us)
        self.assert_rejected("REQUEST", writer, selected=selected, issued=issued, expires=write_end + 1)
        self.assert_rejected("OFFER", reader, selected=selected, issued=completed, expires=notice_end + 1)
        self.assert_rejected("ACQUIRE", reader, offer, selected=selected, issued=read_start, expires=read_end + 1)
        self.assert_rejected("ACQUIRED", reader, acquire, selected=selected, issued=notice_end, expires=read_end)

    def test_request_original_window_copy_budget_and_external_cap_are_separate(self):
        ctx = writer_context()
        chosen = SlotTimeLimits(1000, 500, 150)
        for issued, expires in ((0, 1), (_IW, _EW), (_MAX - 1000, _MAX)):
            value = make("REQUEST", ctx, limits=chosen, issued=issued, expires=expires, cap_us=expires)
            self.assertEqual((value.issued_us, value.expires_us, value.cap_us), (issued, expires, expires))
            self.assertEqual(value.record.cap_us, expires)
        for issued, expires in ((_IW, _IW), (_IW + 1, _IW), (_IW, _EW + 1)):
            self.assert_rejected("REQUEST", ctx, selected=chosen, issued=issued, expires=expires)
        self.assert_rejected("REQUEST", ctx, selected=chosen, cap_us=_EW - 1)
        for cap in (True, False, -1, _MAX + 1, 1.0, "10001000", object()):
            self.assert_rejected("REQUEST", ctx, selected=chosen, cap_us=cap)

    def test_grant_preserves_qw_ew_cap_and_allows_only_iw_le_g_lt_ew(self):
        ctx = writer_context()
        original = make("REQUEST", ctx, cap_us=_EW + 100)
        for stamp in (_IW, _EW - 1):
            result = make("GRANT", ctx, original, issued=stamp, expires=_EW)
            self.assertEqual((result.request_id, result.expires_us, result.cap_us),
                             (original.request_id, _EW, _EW + 100))
            self.assertEqual(result.record.request, original.record)
            self.assertEqual(result.record.granted_us, stamp)
        for stamp in (_IW - 1, _EW):
            self.assert_rejected("GRANT", ctx, original, issued=stamp, expires=_EW)
        for expiry in (_EW - 1, _EW + 1):
            self.assert_rejected("GRANT", ctx, original, expires=expiry)
        self.assert_rejected("GRANT", ctx, original, request_number=999)

    def test_filled_preserves_original_grant_and_cannot_rebase_completion_or_expiry(self):
        stages = chain()
        grant = stages["GRANT"]
        for stamp in (_G, _EW - 1):
            result = make("FILLED", grant.context, grant, issued=stamp, expires=_EW)
            self.assertEqual(result.record.key, grant.record.key)
            self.assertEqual(result.record.completed_us, stamp)
            self.assertEqual(result.request_id, grant.request_id)
        for stamp in (_G - 1, _EW):
            self.assert_rejected("FILLED", grant.context, grant, issued=stamp, expires=_EW)
        for expiry in (_EW - 1, _EW + 1):
            self.assert_rejected("FILLED", grant.context, grant, expires=expiry)
        self.assert_rejected("FILLED", grant.context, grant, request_number=999)

    def test_offer_expiry_is_exact_original_completion_plus_pinned_budget(self):
        ctx = reader_context()
        for completion, expiry in ((0, 500), (_P, _EO), (_MAX - 500, _MAX)):
            value = make("OFFER", ctx, limits=limits(), issued=completion, expires=expiry)
            self.assertEqual(value.record.content.completed_us, completion)
            self.assertEqual((value.issued_us, value.expires_us), (completion, expiry))
            self.assertIsNone(value.parent)
            self.assertIsNone(value.cap_us)
        for expiry in (_EO - 1, _EO + 1):
            self.assert_rejected("OFFER", ctx, issued=_P, expires=expiry)
        # wire expiry는 유효 u64지만 원 합산이 MAX+1이면 clamp하지 않고 거절한다.
        self.assert_rejected("OFFER", ctx, issued=_MAX - 499, expires=_MAX)
        self.assert_rejected("OFFER", ctx, issued=_MAX, expires=_MAX)

    def test_offer_and_read_do_not_inherit_completed_copy_cap_or_deadline(self):
        writer = writer_context()
        reader = reader_context(writer)
        short = _IW + 100
        request = make("REQUEST", writer, issued=_IW, expires=short, cap_us=short)
        grant = make("GRANT", writer, request, issued=_G, expires=short)
        filled = make("FILLED", writer, grant, issued=_P, expires=short)
        self.assertEqual(filled.cap_us, short)
        offer = make("OFFER", reader, issued=_P, expires=_EO)
        acquire = make("ACQUIRE", reader, offer, issued=_IW + 500, expires=_IW + 650,
                       cap_us=_IW + 700)
        acquired = make("ACQUIRED", reader, acquire, issued=_IW + 510, expires=_IW + 650)
        self.assertGreater(offer.expires_us, filled.expires_us)
        self.assertIsNone(offer.cap_us)
        self.assertEqual(acquired.cap_us, _IW + 700)
        self.assertGreater(acquired.expires_us, filled.expires_us)

    def test_acquire_is_inside_notice_at_issue_with_independent_bounded_read_window(self):
        offer = chain()["OFFER"]
        for issued in (_P, _EO - 1):
            result = make("ACQUIRE", offer.context, offer, issued=issued, expires=issued + 150,
                          cap_us=issued + 160)
            self.assertEqual((result.record.request.issued_us, result.record.request.expires_us,
                              result.record.request.cap_us), (issued, issued + 150, issued + 160))
        for issued, expires in ((_P - 1, _P + 100), (_EO, _EO + 100), (_IR, _IR),
                                (_IR, _IR - 1), (_IR, _IR + 151)):
            self.assert_rejected("ACQUIRE", offer.context, offer, issued=issued, expires=expires)
        self.assert_rejected("ACQUIRE", offer.context, offer, cap_us=_ER - 1)
        for cap in (True, False, -1, _MAX + 1, 1.0, "10000200", object()):
            self.assert_rejected("ACQUIRE", offer.context, offer, cap_us=cap)

    def test_acquire_request_must_differ_from_its_known_notice_request(self):
        offer = chain()["OFFER"]
        bad = slot_fields("ACQUIRE", offer.context)
        bad["request_id"] = offer.request_id
        for entry in (parse_slot_message, SlotMessage):
            with self.assertRaises(SlotMessageError):
                entry(packet(bad), offer.context, offer, limits=offer.limits)
        value = make("ACQUIRE", offer.context, offer)
        self.assertNotEqual(value.request_id, offer.request_id)

    def test_acquired_reservation_lower_bound_and_both_strict_upper_bounds(self):
        stages = chain()
        acquire = stages["ACQUIRE"]
        for stamp in (_IR, _ER - 1):
            result = make("ACQUIRED", acquire.context, acquire, issued=stamp, expires=_ER)
            self.assertEqual(result.record.reserved_us, stamp)
            self.assertEqual(result.record.read_request, acquire.record)
        for stamp in (_IR - 1, _ER):
            self.assert_rejected("ACQUIRED", acquire.context, acquire, issued=stamp, expires=_ER)
        for expiry in (_ER - 1, _ER + 1):
            self.assert_rejected("ACQUIRED", acquire.context, acquire, expires=expiry)
        self.assert_rejected("ACQUIRED", acquire.context, acquire, request_number=999)
        late_read = make("ACQUIRE", stages["OFFER"].context, stages["OFFER"],
                         issued=_EO - 1, expires=_EO + 149)
        self.assertEqual(make("ACQUIRED", late_read.context, late_read, issued=_EO - 1,
                              expires=_EO + 149).record.reserved_us, _EO - 1)
        self.assert_rejected("ACQUIRED", late_read.context, late_read, issued=_EO, expires=_EO + 149)

    def test_read_expiry_after_notice_is_normal_and_parent_survives_notice_deadline(self):
        offer = chain()["OFFER"]
        acquire = make("ACQUIRE", offer.context, offer, issued=_EO - 1, expires=_EO + 149)
        acquired = make("ACQUIRED", acquire.context, acquire, issued=_EO - 1, expires=_EO + 149)
        self.assertGreater(acquired.expires_us, offer.expires_us)
        self.assertIs(acquired.parent, acquire)
        self.assertIs(acquired.parent.parent, offer)
        self.assertEqual(acquired.record.read_request.offer.content.completed_us, _P)
        # 원 안내가 만료된 뒤의 시각도 독립 read request의 원 구간에는 포함될 수 있다.
        acquired.record.read_request.request.check(_EO + 1)
        again = SlotMessage(acquired.packet, acquired.context, acquire, limits=acquired.limits)
        self.assertEqual(again.record, acquired.record)

    def test_each_child_pins_all_limit_values_but_equal_immutable_limits_are_allowed(self):
        stages = chain()
        for op, parent_op in (("GRANT", "REQUEST"), ("FILLED", "GRANT"),
                              ("ACQUIRE", "OFFER"), ("ACQUIRED", "ACQUIRE")):
            value, parent = stages[op], stages[parent_op]
            equal = SlotTimeLimits(1000, 500, 150)
            reconstructed = parse_slot_message(value.packet, value.context, parent, limits=equal)
            self.assertIs(reconstructed.parent, parent)
            self.assertIs(reconstructed.context, parent.context)
            self.assertIs(reconstructed.limits, parent.limits)
            self.assertEqual(reconstructed.limits, parent.limits)
            for selected in (SlotTimeLimits(1001, 500, 150), SlotTimeLimits(1000, 501, 150),
                             SlotTimeLimits(1000, 500, 151)):
                with self.subTest(op=op), self.assertRaises(SlotMessageError):
                    SlotMessage(value.packet, value.context, parent, limits=selected)

    def test_only_first_request_and_acquire_accept_new_local_cap(self):
        stages = chain()
        writer = stages["REQUEST"].context
        original = make("REQUEST", writer, cap_us=_EW + 10)
        grant = make("GRANT", writer, original)
        filled = make("FILLED", writer, grant)
        self.assertEqual((original.cap_us, grant.cap_us, filled.cap_us), (_EW + 10,) * 3)
        self.assertEqual(grant.record.request.cap_us, _EW + 10)
        offer = stages["OFFER"]
        acquire = make("ACQUIRE", offer.context, offer, cap_us=_ER + 10)
        acquired = make("ACQUIRED", acquire.context, acquire)
        self.assertEqual(acquired.cap_us, _ER + 10)
        self.assertEqual(acquired.record.read_request.request.cap_us, _ER + 10)
        for value, parent in ((grant, original), (filled, grant), (offer, None), (acquired, acquire)):
            for cap in (0, _MAX, value.cap_us if value.cap_us is not None else _EO):
                with self.subTest(op=value.op, cap=cap), self.assertRaises(SlotMessageError):
                    SlotMessage(value.packet, value.context, parent, limits=value.limits, cap_us=cap)
            self.assertEqual(SlotMessage(value.packet, value.context, parent, limits=value.limits,
                                         cap_us=None).cap_us, value.cap_us)

    def test_required_parent_ops_and_parentless_ops_are_not_interchangeable(self):
        stages = chain()
        allowed = {"GRANT": "REQUEST", "FILLED": "GRANT", "ACQUIRE": "OFFER", "ACQUIRED": "ACQUIRE"}
        for op, value in stages.items():
            if op in ("REQUEST", "OFFER"):
                for parent in (stages["REQUEST"], stages["OFFER"], object()):
                    with self.assertRaises(SlotMessageError):
                        SlotMessage(value.packet, value.context, parent, limits=value.limits)
                continue
            for parent in (None, object(), value.record, *stages.values()):
                if parent is stages[allowed[op]]:
                    continue
                with self.subTest(op=op, parent_type=type(parent).__name__), self.assertRaises(SlotMessageError):
                    parse_slot_message(value.packet, value.context, parent, limits=value.limits)

    def test_parent_context_equality_is_full_and_new_m_cannot_adopt_old_reader_parent(self):
        stages = chain()
        writer = stages["REQUEST"].context
        same_writer = WriterSlotContext(writer.bind, writer.scope)
        grant = stages["GRANT"]
        result = SlotMessage(grant.packet, same_writer, stages["REQUEST"], limits=grant.limits)
        self.assertIs(result.parent, stages["REQUEST"])
        changed_scope = replace(writer.scope, arena_name=writer.scope.arena_name + "-other")
        changed_writer = WriterSlotContext(writer.bind, changed_scope)
        with self.assertRaises(SlotMessageError):
            SlotMessage(grant.packet, changed_writer, stages["REQUEST"], limits=grant.limits)
        old_reader = stages["OFFER"].context
        equal_reader = ReaderSlotContext(old_reader.writer, old_reader.bind)
        request = stages["ACQUIRE"]
        self.assertIs(parse_slot_message(request.packet, equal_reader, stages["OFFER"],
                                         limits=request.limits).parent, stages["OFFER"])
        newer = reader_context(writer, mapper_number=6, channel_number=7)
        foreign = slot_fields("ACQUIRE", newer)
        with self.assertRaises(SlotMessageError):
            SlotMessage(packet(foreign), newer, stages["OFFER"], limits=request.limits)

    def test_same_read_request_can_have_equal_c1_projection_but_keeps_full_notice_parent(self):
        offer = chain()["OFFER"]
        other = make("OFFER", offer.context, request_number=413, issued=_P, expires=_EO)
        first = make("ACQUIRE", offer.context, offer)
        second = SlotMessage(first.packet, first.context, other, limits=first.limits)
        # wire에는 Qo가 없다. static wrapper는 외부 원 parent를 보관하며 실제 선택은 S gate다.
        self.assertEqual(first.raw, second.raw)
        self.assertEqual(first.record, second.record)
        self.assertIs(first.parent, offer)
        self.assertIs(second.parent, other)
        self.assertNotEqual(first.parent.request_id, second.parent.request_id)
        first_response = make("ACQUIRED", first.context, first)
        second_response = SlotMessage(first_response.packet, second.context, second, limits=second.limits)
        self.assertIs(first_response.parent.parent, offer)
        self.assertIs(second_response.parent.parent, other)

    def test_no_now_or_transport_completion_is_accepted_as_static_time_input(self):
        value = chain()["FILLED"]
        for entry in (parse_slot_message, SlotMessage):
            for keyword in ("now_us", "received_us", "completed_us", "first_sent_us"):
                with self.assertRaises(TypeError):
                    entry(value.packet, value.context, value.parent, limits=value.limits, **{keyword: _MAX})
        self.assertEqual(value.record.completed_us, value.issued_us)


if __name__ == "__main__":
    unittest.main()
