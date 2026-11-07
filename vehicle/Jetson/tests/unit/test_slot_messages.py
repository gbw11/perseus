"""SLOT 6종의 정적 문법·등록 문맥·원 parent 보존을 검사한다.

합성 packet과 c1 record는 송신·실예약·token·현재성의 증거가 아니다. 시간 cap의
상세 경계는 별도 시험에 맡기고 여기서는 형식·소유권·문맥 혼합을 확인한다.
"""

from copy import deepcopy
from dataclasses import replace
import unittest

from xenova_vehicle.runtime import raw_leases
from xenova_vehicle.runtime import slot_messages as slots
from xenova_vehicle.runtime.local_envelope import EnvelopeStructureError, StructuralPacket
from xenova_vehicle.runtime.raw_binding_messages import RawBindContext
from raw_binding_fixtures import bind_message, binding_fields
from raw_lease_fixtures import identifier
from slot_message_fixtures import chain, make, packet, reader_context, slot_fields, writer_context


OPS = ('REQUEST', 'GRANT', 'FILLED', 'OFFER', 'ACQUIRE', 'ACQUIRED')
PAYLOAD_KEYS = {'op', 'arena_id', 'slot', 'generation', 'write_token', 'read_token',
                'producer', 'consumer', 'raw_binding_sha256', 'publication_binding_sha256',
                'purpose', 'payload_length', 'descriptor_sha256'}
UNUSED = {
    'REQUEST': ('slot', 'generation', 'write_token', 'read_token', 'consumer',
                'publication_binding_sha256', 'purpose', 'payload_length', 'descriptor_sha256'),
    'GRANT': ('read_token', 'consumer', 'publication_binding_sha256', 'purpose',
              'payload_length', 'descriptor_sha256'),
    'FILLED': ('read_token', 'consumer', 'publication_binding_sha256', 'purpose'),
    'OFFER': ('read_token', 'publication_binding_sha256', 'purpose'),
    'ACQUIRE': ('read_token', 'publication_binding_sha256'),
    'ACQUIRED': ('publication_binding_sha256',),
}


class SlotMessageTests(unittest.TestCase):
    def setUp(self):
        self.messages = chain()

    def parse(self, fields, original, *, parent=None, context=None, time_limits=None):
        return slots.SlotMessage(packet(fields), original.context if context is None else context,
                                 original.parent if parent is None else parent,
                                 limits=original.limits if time_limits is None else time_limits)

    def rejected_fields(self, fields, original, error=slots.SlotMessageError):
        encoded = packet(fields)
        for constructor in (slots.SlotMessage, slots.parse_slot_message):
            with self.subTest(api=constructor.__name__):
                with self.assertRaises(error):
                    constructor(encoded, original.context, original.parent, limits=original.limits)

    def test_six_public_variants_build_the_expected_c1_record_types(self):
        types = (raw_leases.LeaseRequest, raw_leases.WriteGrant, raw_leases.FilledInfo,
                 raw_leases.Offer, raw_leases.ReadRequest, raw_leases.ReadLease)
        self.assertEqual(set(self.messages), set(OPS))
        for op, record_type in zip(OPS, types):
            with self.subTest(op=op):
                message = self.messages[op]
                self.assertIs(type(message.record), record_type)
                self.assertEqual(message.op, op)
                self.assertEqual(set(message.payload), PAYLOAD_KEYS)
                self.assertIs(message.packet.raw_bytes, message.raw)
                self.assertEqual(message.fields, message.packet.fields)
                self.assertEqual(message.request_id, message.fields['request_id'])
                self.assertEqual(message.issued_us, int(message.fields['issued_j_us']))
                self.assertEqual(message.expires_us, int(message.fields['expires_j_us']))

    def test_parent_records_preserve_original_write_and_read_requests(self):
        request, grant, filled, offer, acquire, acquired = (self.messages[op] for op in OPS)
        self.assertIsNone(request.parent)
        self.assertIsNone(offer.parent)
        for child, parent in ((grant, request), (filled, grant), (acquire, offer), (acquired, acquire)):
            self.assertIs(child.parent, parent)
        self.assertIs(grant.record.request, request.record)
        self.assertEqual(grant.record.granted_us, grant.issued_us)
        self.assertEqual(filled.record.key, grant.record.key)
        self.assertEqual(filled.record.completed_us, filled.issued_us)
        self.assertEqual(offer.record.content, filled.record)
        self.assertIs(acquire.record.offer, offer.record)
        self.assertIs(acquired.record.read_request, acquire.record)
        self.assertEqual(acquired.record.reserved_us, acquired.issued_us)
        self.assertEqual(acquired.record.key.write_key, filled.record.key)
        self.assertEqual(acquired.record.key.reader, acquired.context.reader)

    def test_stable_binding_survives_its_original_handshake_transport_expiry(self):
        writer = self.messages['REQUEST'].context
        reader = self.messages['OFFER'].context
        self.assertGreater(self.messages['REQUEST'].issued_us, writer.bind.expires_us)
        self.assertGreater(self.messages['OFFER'].issued_us, reader.bind.expires_us)
        for message in self.messages.values():
            self.assertEqual(message.record, self.parse(message.fields, message).record)

    def test_writer_can_start_without_mapper_binding(self):
        writer = writer_context()
        request = make('REQUEST', writer)
        grant = make('GRANT', writer, request)
        filled = make('FILLED', writer, grant)
        self.assertIs(request.context, writer)
        self.assertIs(grant.context, writer)
        self.assertIs(filled.context, writer)
        self.assertEqual(filled.record.key.scope, writer.scope)

    def test_each_variant_rejects_missing_or_unknown_payload_fields(self):
        for op, message in self.messages.items():
            for name in PAYLOAD_KEYS:
                with self.subTest(op=op, missing=name):
                    fields = message.fields
                    del fields['payload'][name]
                    self.rejected_fields(fields, message)
            with self.subTest(op=op, unknown=True):
                fields = message.fields
                fields['payload']['unknown'] = None
                self.rejected_fields(fields, message)

    def test_unused_fields_are_explicit_null_not_defaulted_from_context(self):
        replacements = dict(slot=0, generation='1', write_token=identifier(900),
                            read_token=identifier(901), consumer=identifier(902),
                            publication_binding_sha256='f' * 64, purpose='PARSER_WARMUP',
                            payload_length=960000, descriptor_sha256='e' * 64)
        for op, names in UNUSED.items():
            message = self.messages[op]
            for name in names:
                with self.subTest(op=op, name=name):
                    self.assertIsNone(message.payload[name])
                    fields = message.fields
                    fields['payload'][name] = replacements[name]
                    self.rejected_fields(fields, message)

    def test_required_values_are_not_filled_in_when_null(self):
        for op, message in self.messages.items():
            required = PAYLOAD_KEYS - {'op'} - set(UNUSED[op])
            for name in required:
                with self.subTest(op=op, name=name):
                    fields = message.fields
                    fields['payload'][name] = None
                    self.rejected_fields(fields, message)

    def test_publication_metric_and_cleanup_variants_are_explicitly_unsupported(self):
        for op, message in self.messages.items():
            with self.subTest(op=op, publication=True):
                fields = message.fields
                fields['payload']['publication_binding_sha256'] = 'f' * 64
                self.rejected_fields(fields, message, slots.UnsupportedSlotOperation)
        for op in ('ACQUIRE', 'ACQUIRED'):
            with self.subTest(op=op, metric=True):
                message = self.messages[op]
                fields = message.fields
                fields['payload']['purpose'] = 'METRIC_MAP'
                self.rejected_fields(fields, message, slots.UnsupportedSlotOperation)
        for op in ('RELEASE', 'REVOKE'):
            with self.subTest(op=op):
                message = self.messages['ACQUIRED']
                fields = message.fields
                fields['payload']['op'] = op
                self.rejected_fields(fields, message, slots.UnsupportedSlotOperation)

    def test_op_requires_an_exact_supported_spelling(self):
        original = self.messages['REQUEST']
        for wrong in ('request', 'UNKNOWN', None, True, 1, [], {}):
            with self.subTest(wrong=wrong):
                fields = original.fields
                fields['payload']['op'] = wrong
                self.rejected_fields(fields, original)

    def test_outer_kind_domain_revision_service_channel_and_sender_are_bound(self):
        changes = dict(kind='HELLO', binding_domain='LIFECYCLE', binding_revision='2',
                       service_instance=identifier(910), channel_epoch=identifier(911),
                       sender_instance=identifier(912))
        for op, message in self.messages.items():
            for name, wrong in changes.items():
                with self.subTest(op=op, name=name):
                    fields = message.fields
                    fields[name] = wrong
                    self.rejected_fields(fields, message)

    def test_directions_use_registered_a_or_m_and_service_as_supervisor_sender(self):
        for op, message in self.messages.items():
            writer = message.context if op in OPS[:3] else message.context.writer
            service = writer.scope.service_instance
            a = writer.scope.writer
            endpoint = a if op in OPS[:3] else message.context.reader
            self.assertEqual(message.fields['channel_epoch'], endpoint.channel_epoch)
            self.assertEqual(message.fields['sender_instance'],
                             endpoint.worker_instance if op in ('REQUEST', 'FILLED', 'ACQUIRE') else service)
            self.assertEqual(message.payload['producer'], a.worker_instance)
            self.assertEqual(message.payload['consumer'], None if op in OPS[:3] else endpoint.worker_instance)

    def test_full_writer_scope_and_registered_consumer_are_exact(self):
        for op, message in self.messages.items():
            for name, wrong in (('arena_id', identifier(913)), ('producer', identifier(914)),
                                ('raw_binding_sha256', 'f' * 64)):
                with self.subTest(op=op, name=name):
                    fields = message.fields
                    fields['payload'][name] = wrong
                    self.rejected_fields(fields, message)
            if op in OPS[3:]:
                with self.subTest(op=op, name='consumer'):
                    fields = message.fields
                    fields['payload']['consumer'] = identifier(915)
                    self.rejected_fields(fields, message)

    def test_slot_generation_tokens_length_and_sha_have_exact_types(self):
        for op in OPS[1:]:
            message = self.messages[op]
            for name, wrong_values in (
                    ('slot', (True, False, '0', -1, 2)),
                    ('generation', (1, True, '0', '01', str(2**64))),
                    ('write_token', (None, '', '01234567-89AB-CDEF-8123-456789ABCDEF'))):
                for wrong in wrong_values:
                    with self.subTest(op=op, name=name, wrong=wrong):
                        fields = message.fields
                        fields['payload'][name] = wrong
                        self.rejected_fields(fields, message)
        for op in OPS[2:]:
            message = self.messages[op]
            for name, wrong_values in (('payload_length', (True, False, '960000', 0, 2093057)),
                                       ('descriptor_sha256', (None, 'F' * 64, 'g' * 64, 'f' * 63))):
                for wrong in wrong_values:
                    with self.subTest(op=op, name=name, wrong=wrong):
                        fields = message.fields
                        fields['payload'][name] = wrong
                        self.rejected_fields(fields, message)

    def test_offer_length_uses_c1_body_extent_bounds_without_claiming_c2_pixels(self):
        original = self.messages['OFFER']
        for length in (1, 2093056):
            with self.subTest(length=length):
                fields = original.fields
                fields['payload']['payload_length'] = length
                result = self.parse(fields, original)
                self.assertEqual(result.record.content.payload_length, length)

    def test_full_writer_key_can_describe_slot_one_at_max_generation(self):
        original = self.messages['OFFER']
        fields = original.fields
        fields['payload']['slot'] = 1
        fields['payload']['generation'] = str(2**64 - 1)
        result = self.parse(fields, original)
        self.assertEqual(result.record.content.key.slot, 1)
        self.assertEqual(result.record.content.key.generation, 2**64 - 1)

    def test_acquired_read_token_requires_a_canonical_uuid(self):
        original = self.messages['ACQUIRED']
        for wrong in (True, 1, '', '01234567-89AB-CDEF-8123-456789ABCDEF'):
            with self.subTest(wrong=wrong):
                fields = original.fields
                fields['payload']['read_token'] = wrong
                self.rejected_fields(fields, original)

    def test_request_and_offer_have_no_parent_and_other_four_require_one(self):
        for op, message in self.messages.items():
            with self.subTest(op=op):
                parent = self.messages['REQUEST'] if op in ('REQUEST', 'OFFER') else None
                with self.assertRaises(slots.SlotMessageError):
                    slots.SlotMessage(message.packet, message.context, parent, limits=message.limits)

    def test_parent_variant_and_native_record_substitution_are_rejected(self):
        wrong_parents = {'GRANT': 'FILLED', 'FILLED': 'REQUEST',
                         'ACQUIRE': 'ACQUIRED', 'ACQUIRED': 'OFFER'}
        for op, wrong_op in wrong_parents.items():
            message = self.messages[op]
            for parent in (self.messages[wrong_op], message.parent.record):
                with self.subTest(op=op, parent_type=type(parent).__name__):
                    with self.assertRaises(slots.SlotMessageError):
                        slots.SlotMessage(message.packet, message.context, parent, limits=message.limits)

    def test_writer_and_reader_operation_contexts_are_not_interchangeable(self):
        writer = self.messages['REQUEST'].context
        reader = self.messages['OFFER'].context
        for op, message in self.messages.items():
            with self.subTest(op=op):
                wrong = reader if op in OPS[:3] else writer
                with self.assertRaises(slots.SlotMessageError):
                    slots.SlotMessage(message.packet, wrong, message.parent, limits=message.limits)

    def test_parent_request_and_full_content_cannot_be_substituted(self):
        for op in ('GRANT', 'FILLED', 'ACQUIRED'):
            with self.subTest(op=op, changed='request'):
                message = self.messages[op]
                fields = message.fields
                fields['request_id'] = identifier(916)
                self.rejected_fields(fields, message)
        for op in ('FILLED', 'ACQUIRE', 'ACQUIRED'):
            for name, wrong in (('slot', 1), ('generation', '2'), ('write_token', identifier(917))):
                with self.subTest(op=op, changed=name):
                    message = self.messages[op]
                    fields = message.fields
                    fields['payload'][name] = wrong
                    self.rejected_fields(fields, message)
        for op in ('ACQUIRE', 'ACQUIRED'):
            for name, wrong in (('payload_length', 960001), ('descriptor_sha256', 'e' * 64)):
                with self.subTest(op=op, changed=name):
                    message = self.messages[op]
                    fields = message.fields
                    fields['payload'][name] = wrong
                    self.rejected_fields(fields, message)

    def test_equal_context_and_limits_values_preserve_original_parent_reference(self):
        original = self.messages['GRANT']
        writer = original.context
        same_context = slots.WriterSlotContext(writer.bind, writer.scope)
        same_limits = slots.SlotTimeLimits(original.limits.copy_budget_us,
                                           original.limits.offer_budget_us,
                                           original.limits.read_budget_us)
        result = slots.SlotMessage(original.packet, same_context, original.parent, limits=same_limits)
        self.assertIs(result.parent, original.parent)
        self.assertIs(result.record.request, original.parent.record)

    def test_parent_requires_the_whole_limits_record_not_only_current_stage_budget(self):
        original = self.messages['GRANT']
        wrong = slots.SlotTimeLimits(original.limits.copy_budget_us,
                                    original.limits.offer_budget_us + 1,
                                    original.limits.read_budget_us)
        with self.assertRaises(slots.SlotMessageError):
            slots.SlotMessage(original.packet, original.context, original.parent, limits=wrong)

    def test_writer_context_rejects_mapper_bind_and_foreign_scope(self):
        writer = self.messages['REQUEST'].context
        mapper_bind = self.messages['OFFER'].context.bind
        with self.assertRaises(slots.SlotMessageError):
            slots.WriterSlotContext(mapper_bind, writer.scope)
        for changed in (replace(writer.scope, service_instance=identifier(918)),
                        replace(writer.scope, raw_binding_sha256='e' * 64),
                        replace(writer.scope, writer=replace(writer.scope.writer, channel_epoch=identifier(919))),
                        replace(writer.scope, writer=replace(writer.scope.writer, worker_instance=identifier(920)))):
            with self.subTest(scope=changed):
                with self.assertRaises(slots.SlotMessageError):
                    slots.WriterSlotContext(writer.bind, changed)

    def test_scope_name_128_is_external_context_not_raw_binding_ascii64(self):
        writer = self.messages['REQUEST'].context
        scope = replace(writer.scope, arena_name='n' * 128)
        result = slots.WriterSlotContext(writer.bind, scope)
        self.assertIs(result.scope, scope)
        self.assertEqual(result.scope.arena_size_bytes, 4 * 1024 * 1024)
        self.assertEqual(result.scope.arena_role, 'RAW')

    def test_reader_context_rejects_a_bind_and_revision_or_raw_body_mismatch(self):
        reader = self.messages['OFFER'].context
        writer = reader.writer
        with self.assertRaises(slots.SlotMessageError):
            slots.ReaderSlotContext(writer, writer.bind)
        changed_revision = bind_message(reader.bind.context, revision=2)
        changed_fields = binding_fields()
        changed_fields['capture_epoch'] = identifier(921)
        changed_body = bind_message(reader.bind.context, binding=changed_fields)
        for original in (changed_revision, changed_body):
            with self.subTest(revision=original.binding_revision):
                with self.assertRaises(slots.SlotMessageError):
                    slots.ReaderSlotContext(writer, original)

    def test_same_raw_hash_and_a_worker_do_not_hide_a_channel_mismatch(self):
        reader = self.messages['OFFER'].context
        original = reader.bind.context
        changed_a = replace(original.acquisition, channel_epoch=identifier(922))
        candidate_context = RawBindContext(original.peer, changed_a, original.profile)
        candidate = bind_message(candidate_context)
        self.assertEqual(candidate.binding.sha256, reader.bind.binding.sha256)
        self.assertEqual(candidate.context.acquisition.worker_instance,
                         reader.writer.scope.writer.worker_instance)
        with self.assertRaises(slots.SlotMessageError):
            slots.ReaderSlotContext(reader.writer, candidate)

    def test_reader_binds_can_differ_in_handshake_request_sequence_and_times(self):
        reader = self.messages['OFFER'].context
        changed = bind_message(reader.bind.context, request_number=923, sequence=99,
                               issued=300, expires=5000300)
        result = slots.ReaderSlotContext(reader.writer, changed)
        self.assertEqual(result.reader, reader.reader)
        self.assertEqual(result.writer.bind.binding.sha256, changed.binding.sha256)

    def test_new_mapper_keeps_writer_scope_but_cannot_inherit_old_mapper_parent(self):
        old = self.messages['OFFER'].context
        new = reader_context(old.writer, mapper_number=924, channel_number=925)
        self.assertIs(new.writer, old.writer)
        self.assertEqual(new.writer.scope, old.writer.scope)
        self.assertEqual(new.bind.binding.sha256, old.bind.binding.sha256)
        self.assertNotEqual(new.reader, old.reader)
        old_offer = self.messages['OFFER']
        fields = slot_fields('ACQUIRE', new)
        with self.assertRaises(slots.SlotMessageError):
            slots.SlotMessage(packet(fields), new, old_offer, limits=old_offer.limits)
        own_offer = make('OFFER', new)
        own_acquire = make('ACQUIRE', new, own_offer)
        own_acquired = make('ACQUIRED', new, own_acquire)
        self.assertEqual(own_acquired.record.key.reader, new.reader)
        self.assertEqual(own_acquired.record.key.write_key.scope.writer, old.writer.scope.writer)

    def test_parent_with_different_scope_is_rejected_even_when_raw_hash_matches(self):
        original = self.messages['GRANT']
        writer = original.context
        changed = slots.WriterSlotContext(writer.bind, replace(writer.scope, arena_id=identifier(926)))
        fields = slot_fields('GRANT', changed)
        with self.assertRaises(slots.SlotMessageError):
            slots.SlotMessage(packet(fields), changed, original.parent, limits=original.limits)

    def test_parent_context_is_not_replaced_by_another_bind_with_the_same_raw_body(self):
        original = self.messages['GRANT']
        writer = original.context
        another_bind = bind_message(writer.bind.context, request_number=929, sequence=99)
        changed = slots.WriterSlotContext(another_bind, writer.scope)
        self.assertEqual(changed.bind.binding.sha256, writer.bind.binding.sha256)
        self.assertEqual(changed.scope, writer.scope)
        self.assertNotEqual(changed, writer)
        fields = slot_fields('GRANT', changed)
        with self.assertRaises(slots.SlotMessageError):
            slots.SlotMessage(packet(fields), changed, original.parent, limits=original.limits)

    def test_public_fields_payload_and_native_input_are_independent_copies(self):
        original = self.messages['REQUEST']
        source = original.fields
        encoded = packet(source)
        message = slots.SlotMessage(encoded, original.context, limits=original.limits)
        before = deepcopy(message.fields)
        source['payload']['producer'] = identifier(927)
        fields = message.fields
        fields['payload']['arena_id'] = identifier(928)
        message.payload.clear()
        self.assertEqual(message.fields, before)
        self.assertEqual(message.raw, encoded.raw_bytes)
        self.assertIs(message.packet, encoded)
        for name in ('packet', 'context', 'limits', 'parent', 'record', 'issued_us', 'expires_us'):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    setattr(message, name, None)

    def test_public_helper_and_constructor_validate_and_preserve_the_same_parent(self):
        for original in self.messages.values():
            with self.subTest(op=original.op):
                direct = slots.SlotMessage(original.packet, original.context, original.parent, limits=original.limits)
                parsed = slots.parse_slot_message(original.packet, original.context, original.parent,
                                                  limits=original.limits)
                self.assertEqual(direct.record, parsed.record)
                self.assertEqual(direct.fields, parsed.fields)
                self.assertIs(parsed.parent, original.parent)
                self.assertIs(parsed.packet, original.packet)
                with self.assertRaises(TypeError):
                    slots.SlotMessage(original.packet, original.context, original.parent,
                                      limits=original.limits, validated=True)

    def test_bounded_raw_packet_keeps_internal_whitespace_and_rejects_4097_before_slot_parsing(self):
        original = self.messages['REQUEST']
        padded = original.raw[:-1] + b' ' * (4096 - len(original.raw)) + b'}'
        encoded = StructuralPacket(padded)
        result = slots.SlotMessage(encoded, original.context, limits=original.limits)
        self.assertEqual(result.raw, padded)
        self.assertEqual(result.record, original.record)
        with self.assertRaises(EnvelopeStructureError):
            StructuralPacket(padded[:-1] + b' }')

    def test_static_reparse_has_no_shared_sequence_or_request_admission_state(self):
        original = self.messages['ACQUIRED']
        for _ in range(3):
            result = slots.SlotMessage(original.packet, original.context, original.parent, limits=original.limits)
            self.assertEqual(result.record, original.record)
            self.assertIs(result.parent, original.parent)

    def test_packet_subclass_is_rejected_before_custom_fields_getter(self):
        calls = []

        class DerivedPacket(StructuralPacket):
            @property
            def fields(self):
                calls.append('subclass fields')
                raise AssertionError('custom packet getter must not run')

        original = self.messages['REQUEST']
        candidate = DerivedPacket(original.raw)
        with self.assertRaises(slots.SlotMessageError):
            slots.SlotMessage(candidate, original.context, limits=original.limits)
        self.assertEqual(calls, [])

    def test_native_parameters_are_rejected_before_custom_callbacks(self):
        calls = []

        class TrapMeta(type):
            def __eq__(cls, other):
                calls.append('metaclass equality')
                raise AssertionError('custom type equality must not run')

            def __hash__(cls):
                calls.append('metaclass hash')
                raise AssertionError('custom type hash must not run')

        class Trap(metaclass=TrapMeta):
            def __getattr__(self, name):
                calls.append('attribute access')
                raise AssertionError('custom attribute must not run')

            def __iter__(self):
                calls.append('iterator')
                raise AssertionError('custom iterator must not run')

        original = self.messages['GRANT']
        for which in ('packet', 'context', 'parent', 'limits'):
            values = dict(packet=original.packet, context=original.context,
                          parent=original.parent, limits=original.limits)
            values[which] = Trap()
            with self.subTest(which=which):
                with self.assertRaises(slots.SlotMessageError):
                    slots.SlotMessage(values['packet'], values['context'], values['parent'],
                                      limits=values['limits'])
        writer = self.messages['REQUEST'].context
        reader = self.messages['OFFER'].context
        for constructor, arguments in (
                (slots.WriterSlotContext, (Trap(), writer.scope)),
                (slots.WriterSlotContext, (writer.bind, Trap())),
                (slots.ReaderSlotContext, (Trap(), reader.bind)),
                (slots.ReaderSlotContext, (writer, Trap()))):
            with self.subTest(constructor=constructor.__name__):
                with self.assertRaises(slots.SlotMessageError):
                    constructor(*arguments)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
