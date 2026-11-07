"""RELEASE의 원 lease·완료 진술·독립 transport 수명을 정적으로 검사한다.

합성 packet 수락은 실제 view 종료·writable post·현재 peer 승인이나 FREE가 아니다.
M/S의 native cap은 wire에서 복원하지 않으며 parent가 보유한 값을 그대로 따른다.
"""

from copy import deepcopy
import unittest

from xenova_vehicle.runtime import raw_leases
from xenova_vehicle.runtime import slot_cleanup as cleanup
from xenova_vehicle.runtime.slot_messages import SlotMessage
from xenova_vehicle.runtime.local_envelope import EnvelopeStructureError, StructuralPacket
from slot_cleanup_fixtures import acquired, cleanup_limits, make, packet, release_fields
from slot_message_fixtures import chain
from raw_lease_fixtures import identifier


MAX = 2**64 - 1
PAYLOAD_KEYS = {'op', 'arena_id', 'slot', 'generation', 'write_token', 'read_token',
                'producer', 'consumer', 'raw_binding_sha256', 'publication_binding_sha256',
                'purpose', 'payload_length', 'descriptor_sha256'}


class ReleaseMessageTests(unittest.TestCase):
    def setUp(self):
        self.parent = acquired()
        self.limits = cleanup_limits()

    def parse(self, fields, *, parent=None, selected=None):
        return cleanup.ReleaseMessage(packet(fields), self.parent if parent is None else parent,
                                      limits=self.limits if selected is None else selected)

    def rejected(self, fields):
        encoded = packet(fields)
        for constructor in (cleanup.ReleaseMessage, cleanup.parse_release_message):
            with self.subTest(api=constructor.__name__):
                with self.assertRaises(cleanup.SlotCleanupError):
                    constructor(encoded, self.parent, limits=self.limits)

    def test_valid_release_preserves_exact_parent_and_remote_statement(self):
        fields = release_fields(self.parent)
        encoded = packet(fields)
        message = cleanup.ReleaseMessage(encoded, self.parent, limits=self.limits)
        self.assertIs(message.packet, encoded)
        self.assertIs(message.parent, self.parent)
        self.assertIs(message.context, self.parent.context)
        self.assertIs(message.limits, self.limits)
        self.assertIs(type(message.record), raw_leases.RemoteReturnStatement)
        self.assertIs(message.record.lease, self.parent.record)
        self.assertEqual(message.record.completed_us, int(fields['issued_j_us']))
        self.assertEqual(message.issued_us, message.record.completed_us)
        self.assertEqual(message.expires_us, message.issued_us + self.limits.transport_budget_us)
        self.assertEqual(message.request_id, self.parent.request_id)
        self.assertIs(message.raw, encoded.raw_bytes)
        self.assertEqual(message.fields, fields)

    def test_remote_statement_does_not_invent_two_local_view_return_times(self):
        message = make(self.parent)
        self.assertIsNot(type(message.record), raw_leases.ViewReturnFact)
        self.assertFalse(hasattr(message.record, 'views_ended_us'))
        self.assertFalse(hasattr(message.record, 'writable_posted_us'))
        self.assertIs(message.record.lease.read_request, self.parent.record.read_request)
        self.assertEqual(message.record.lease.key, self.parent.record.key)

    def test_payload_is_exactly_original_acquired_with_only_op_changed(self):
        message = make(self.parent)
        expected = self.parent.payload
        expected['op'] = 'RELEASE'
        self.assertEqual(set(message.payload), PAYLOAD_KEYS)
        self.assertEqual(message.payload, expected)
        self.assertIsNone(message.payload['publication_binding_sha256'])
        self.assertEqual(message.payload['purpose'], 'PARSER_WARMUP')

    def test_each_missing_or_unknown_payload_field_is_rejected(self):
        base = release_fields(self.parent)
        for name in PAYLOAD_KEYS:
            with self.subTest(missing=name):
                fields = deepcopy(base)
                del fields['payload'][name]
                self.rejected(fields)
        fields = deepcopy(base)
        fields['payload']['unknown'] = None
        self.rejected(fields)

    def test_release_is_the_only_supported_operation(self):
        base = release_fields(self.parent)
        for op in ('REQUEST', 'GRANT', 'FILLED', 'OFFER', 'ACQUIRE', 'ACQUIRED',
                   'REVOKE', 'STOPPED', 'release', None, True):
            with self.subTest(op=op):
                fields = deepcopy(base)
                fields['payload']['op'] = op
                self.rejected(fields)

    def test_all_original_payload_values_and_types_are_preserved(self):
        changes = dict(arena_id=identifier(901), slot=1, generation='2',
                       write_token=identifier(902), read_token=identifier(903),
                       producer=identifier(904), consumer=identifier(905),
                       raw_binding_sha256='e' * 64, publication_binding_sha256='f' * 64,
                       purpose='METRIC_MAP', payload_length=960001, descriptor_sha256='e' * 64)
        base = release_fields(self.parent)
        for name, wrong in changes.items():
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields['payload'][name] = wrong
                self.rejected(fields)
        for name, wrong in (('slot', False), ('slot', '0'), ('generation', 1),
                            ('generation', True), ('payload_length', True),
                            ('payload_length', '960000'), ('publication_binding_sha256', False)):
            with self.subTest(name=name, wrong=wrong):
                fields = deepcopy(base)
                fields['payload'][name] = wrong
                self.rejected(fields)

    def test_required_payload_values_cannot_be_defaulted_when_null(self):
        base = release_fields(self.parent)
        for name in PAYLOAD_KEYS - {'op', 'publication_binding_sha256'}:
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields['payload'][name] = None
                self.rejected(fields)

    def test_outer_original_identity_and_direction_are_exact(self):
        base = release_fields(self.parent)
        changes = dict(kind='BIND', binding_domain='LIFECYCLE', binding_revision='2',
                       service_instance=identifier(906), channel_epoch=identifier(907),
                       sender_instance=self.parent.fields['sender_instance'], request_id=identifier(908))
        for name, wrong in changes.items():
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields[name] = wrong
                self.rejected(fields)

    def test_m_sender_channel_does_not_replace_registered_a_endpoint(self):
        message = make(self.parent)
        writer = message.context.writer.scope.writer
        reader = message.record.lease.key.reader
        self.assertEqual(message.fields['sender_instance'], reader.worker_instance)
        self.assertEqual(message.fields['channel_epoch'], reader.channel_epoch)
        self.assertEqual(message.payload['producer'], writer.worker_instance)
        self.assertNotEqual(reader.channel_epoch, writer.channel_epoch)
        self.assertEqual(message.record.lease.key.write_key.scope.writer, writer)

    def test_parent_must_be_exact_acquired_variant_not_a_native_lease(self):
        originals = chain()
        encoded = packet(release_fields(self.parent))
        for name in ('REQUEST', 'GRANT', 'FILLED', 'OFFER', 'ACQUIRE'):
            with self.subTest(op=name):
                with self.assertRaises(cleanup.SlotCleanupError):
                    cleanup.ReleaseMessage(encoded, originals[name], limits=self.limits)
        for wrong in (None, self.parent.record, self.parent.fields):
            with self.subTest(type_name=type(wrong).__name__):
                with self.assertRaises(cleanup.SlotCleanupError):
                    cleanup.ReleaseMessage(encoded, wrong, limits=self.limits)

    def test_exact_parent_also_requires_exact_read_lease_record(self):
        parent = acquired()
        encoded = packet(release_fields(parent))
        # 시험 전용 변조로 record 경계만 확인한다. 정상 공개 생성 경로는 이 값을 만들지 않는다.
        object.__setattr__(parent, 'record', chain()['ACQUIRE'].record)
        with self.assertRaises(cleanup.SlotCleanupError):
            cleanup.ReleaseMessage(encoded, parent, limits=self.limits)

    def test_different_sender_sequences_are_not_numerically_ordered(self):
        fields = self.parent.fields
        fields['message_sequence'] = '100'
        parent = SlotMessage(packet(fields), self.parent.context, self.parent.parent,
                             limits=self.parent.limits)
        release = release_fields(parent, sequence=1)
        message = self.parse(release, parent=parent)
        self.assertEqual(message.fields['message_sequence'], '1')
        self.assertEqual(message.parent.fields['message_sequence'], '100')

    def test_transport_budget_is_mandatory_and_exact_positive_u64(self):
        encoded = packet(release_fields(self.parent))
        with self.assertRaises(TypeError):
            cleanup.ReleaseMessage(encoded, self.parent)
        with self.assertRaises(TypeError):
            cleanup.CleanupTimeLimits()
        for wrong in (0, -1, True, False, 1.0, '50', None, MAX + 1):
            with self.subTest(wrong=wrong):
                with self.assertRaises(cleanup.SlotCleanupError):
                    cleanup.CleanupTimeLimits(wrong)
        for valid in (1, MAX):
            with self.subTest(valid=valid):
                selected = cleanup.CleanupTimeLimits(valid)
                self.assertEqual(selected.transport_budget_us, valid)
                with self.assertRaises(AttributeError):
                    selected.transport_budget_us = 0

    def test_completion_may_equal_reserved_but_not_precede_it(self):
        reserved = self.parent.record.reserved_us
        message = self.parse(release_fields(self.parent, completed=reserved))
        self.assertEqual(message.record.completed_us, reserved)
        self.rejected(release_fields(self.parent, completed=reserved - 1))

    def test_completion_and_transport_can_cross_original_read_expiry(self):
        for completed in (self.parent.expires_us - 1, self.parent.expires_us,
                          self.parent.expires_us + 1, self.parent.expires_us + 1000000):
            with self.subTest(completed=completed):
                fields = release_fields(self.parent, completed=completed)
                message = self.parse(fields)
                self.assertEqual(message.record.completed_us, completed)
                self.assertEqual(message.record.lease.read_request.request.expires_us, self.parent.expires_us)
                self.assertEqual(message.expires_us, completed + self.limits.transport_budget_us)

    def test_expiry_is_exact_completion_plus_budget_not_a_shorter_or_longer_window(self):
        base = release_fields(self.parent)
        end = int(base['expires_j_us'])
        for changed in (end - 1, end + 1):
            with self.subTest(expires=changed):
                fields = deepcopy(base)
                fields['expires_j_us'] = str(changed)
                self.rejected(fields)

    def test_final_u64_sum_accepts_max_and_rejects_clamped_overflow(self):
        completed = MAX - self.limits.transport_budget_us
        valid = self.parse(release_fields(self.parent, completed=completed))
        self.assertEqual(valid.expires_us, MAX)
        fields = release_fields(self.parent, completed=completed)
        fields['issued_j_us'] = str(completed + 1)
        fields['expires_j_us'] = str(MAX)
        self.rejected(fields)

    def test_declared_transport_budget_must_match_the_wire(self):
        fields = release_fields(self.parent, budget=50)
        encoded = packet(fields)
        for budget in (49, 51):
            with self.subTest(budget=budget):
                with self.assertRaises(cleanup.SlotCleanupError):
                    cleanup.ReleaseMessage(encoded, self.parent, limits=cleanup_limits(budget=budget))

    def test_m_and_s_native_caps_are_independent_while_release_wire_is_the_same(self):
        original = chain()
        request = original['ACQUIRE']
        raw_acquired = original['ACQUIRED']
        caps = (request.expires_us, request.expires_us + 100)
        parents = []
        for cap in caps:
            acquire = SlotMessage(request.packet, request.context, request.parent,
                                  limits=request.limits, cap_us=cap)
            parents.append(SlotMessage(raw_acquired.packet, raw_acquired.context, acquire,
                                       limits=raw_acquired.limits))
        self.assertEqual(parents[0].raw, parents[1].raw)
        completed = request.expires_us + 1000
        encoded = packet(release_fields(parents[0], completed=completed))
        reports = [cleanup.ReleaseMessage(encoded, parent, limits=self.limits) for parent in parents]
        for report, parent, cap in zip(reports, parents, caps):
            self.assertIs(report.parent, parent)
            self.assertIs(report.record.lease, parent.record)
            self.assertEqual(report.record.lease.read_request.request.cap_us, cap)
            self.assertEqual(report.record.completed_us, completed)
            self.assertGreater(completed, cap)
        self.assertEqual(reports[0].raw, reports[1].raw)

    def test_no_now_context_cap_or_unchecked_argument_can_replace_original_parent_facts(self):
        encoded = packet(release_fields(self.parent))
        for constructor in (cleanup.ReleaseMessage, cleanup.parse_release_message):
            for name, value in (('now_us', 0), ('context', self.parent.context),
                                ('cap_us', self.parent.expires_us), ('validated', True)):
                with self.subTest(api=constructor.__name__, argument=name):
                    with self.assertRaises(TypeError):
                        constructor(encoded, self.parent, limits=self.limits, **{name: value})

    def test_public_helper_and_constructor_preserve_same_packet_and_record(self):
        encoded = packet(release_fields(self.parent))
        direct = cleanup.ReleaseMessage(encoded, self.parent, limits=self.limits)
        parsed = cleanup.parse_release_message(encoded, self.parent, limits=self.limits)
        self.assertIs(parsed.packet, encoded)
        self.assertIs(parsed.parent, self.parent)
        self.assertEqual(parsed.record, direct.record)
        self.assertEqual(parsed.fields, direct.fields)

    def test_fields_payload_and_native_source_are_independent_copies(self):
        fields = release_fields(self.parent)
        encoded = packet(fields)
        message = cleanup.ReleaseMessage(encoded, self.parent, limits=self.limits)
        original = deepcopy(message.fields)
        fields['payload']['read_token'] = identifier(909)
        visible = message.fields
        visible['payload']['producer'] = identifier(910)
        message.payload.clear()
        self.assertEqual(message.fields, original)
        self.assertEqual(message.raw, encoded.raw_bytes)
        for name in ('packet', 'parent', 'context', 'limits', 'record', 'request_id',
                     'issued_us', 'expires_us'):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    setattr(message, name, None)

    def test_internal_whitespace_packet_4096_is_preserved_and_4097_is_b1_rejected(self):
        encoded = packet(release_fields(self.parent))
        padded = encoded.raw_bytes[:-1] + b' ' * (4096 - len(encoded.raw_bytes)) + b'}'
        bounded = StructuralPacket(padded)
        message = cleanup.ReleaseMessage(bounded, self.parent, limits=self.limits)
        self.assertEqual(message.raw, padded)
        with self.assertRaises(EnvelopeStructureError):
            StructuralPacket(padded[:-1] + b' }')

    def test_static_reparse_does_not_reissue_c_or_run_a_cleanup_ledger(self):
        fields = release_fields(self.parent, completed=self.parent.expires_us + 1000)
        encoded = packet(fields)
        for _ in range(3):
            message = cleanup.ReleaseMessage(encoded, self.parent, limits=self.limits)
            self.assertEqual(message.raw, encoded.raw_bytes)
            self.assertEqual(message.record.completed_us, int(fields['issued_j_us']))
            self.assertEqual(message.record.lease, self.parent.record)

    def test_public_inputs_reject_custom_types_without_hooks(self):
        calls = []

        class TrapMeta(type):
            def __eq__(cls, other):
                calls.append('metaclass equality')
                raise AssertionError('custom equality must not run')

            def __hash__(cls):
                calls.append('metaclass hash')
                raise AssertionError('custom hash must not run')

        class Trap(metaclass=TrapMeta):
            def __getattr__(self, name):
                calls.append('attribute access')
                raise AssertionError('custom attribute must not run')

            def __index__(self):
                calls.append('numeric index')
                raise AssertionError('numeric coercion must not run')

        encoded = packet(release_fields(self.parent))
        for which in ('packet', 'parent', 'limits'):
            arguments = dict(packet=encoded, parent=self.parent, limits=self.limits)
            arguments[which] = Trap()
            with self.subTest(which=which):
                with self.assertRaises(cleanup.SlotCleanupError):
                    cleanup.ReleaseMessage(arguments['packet'], arguments['parent'], limits=arguments['limits'])
        with self.assertRaises(cleanup.SlotCleanupError):
            cleanup.CleanupTimeLimits(Trap())
        self.assertEqual(calls, [])

    def test_packet_and_parent_subclasses_are_rejected_before_custom_getters(self):
        calls = []

        class DerivedPacket(StructuralPacket):
            @property
            def fields(self):
                calls.append('packet fields getter')
                raise AssertionError('custom packet getter must not run')

        class DerivedParent(SlotMessage):
            @property
            def payload(self):
                calls.append('parent payload getter')
                raise AssertionError('custom parent getter must not run')

        encoded = packet(release_fields(self.parent))
        derived = DerivedParent(self.parent.packet, self.parent.context, self.parent.parent,
                                limits=self.parent.limits)
        for candidate_packet, parent in ((DerivedPacket(encoded.raw_bytes), self.parent), (encoded, derived)):
            with self.subTest(packet_type=type(candidate_packet).__name__, parent_type=type(parent).__name__):
                with self.assertRaises(cleanup.SlotCleanupError):
                    cleanup.ReleaseMessage(candidate_packet, parent, limits=self.limits)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
