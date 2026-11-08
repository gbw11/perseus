"""S broker의 실제 예약 선행·원 draft·교체와 늦은 cleanup 경계를 검증한다.

token은 호출을 기록하는 합성 대역이다. 실제 Linux semaphore/SHM 동작은 통합 검증한다.
"""

from copy import deepcopy
import unittest
from unittest.mock import patch

from raw_lease_fixtures import identifier
from slot_message_fixtures import limits, packet, reader_context, slot_fields, writer_context
from xenova_vehicle.runtime.raw_leases import LeaseStatus
from xenova_vehicle.runtime.resident_broker import BrokerError, ResidentRawBroker
from xenova_vehicle.runtime.slot_cleanup import CleanupTimeLimits
from xenova_vehicle.runtime.slot_messages import SlotMessage


class Token:
    def __init__(self, value):
        self.value = value
        self.acquires = self.posts = 0

    def acquire(self, blocking):
        if blocking is not False:
            raise AssertionError('broker token operation must be nonblocking')
        self.acquires += 1
        if self.value == 0:
            return False
        self.value -= 1
        return True

    def release(self):
        self.posts += 1
        self.value += 1


class Link:
    def __init__(self, context):
        self.context = context
        self.busy = False
        self.attempts = []
        self.sent = []

    def send(self, kind, payload, **fields):
        self.attempts.append((deepcopy(payload), dict(fields)))
        if self.busy:
            return None
        peer = self.context.bind.context.peer
        value = dict(version=1, service_instance=peer.service_instance, channel_epoch=peer.channel_epoch,
            sender_instance=peer.service_instance, message_sequence=str(len(self.sent) + 1),
            kind=kind, binding_domain=fields['domain'], binding_revision=str(fields['revision']),
            request_id=fields['request_id'], issued_j_us=str(fields['issued_us']),
            expires_j_us=str(fields['expires_us']), payload=payload)
        result = packet(value)
        self.sent.append(result)
        return result


class ResidentBrokerTests(unittest.TestCase):
    def setUp(self):
        self.writer = writer_context()
        self.reader = reader_context(self.writer)
        self.tokens = tuple((Token(1), Token(0)) for _ in range(2))
        self.selected_limits = limits()
        self.broker = ResidentRawBroker(self.writer.bind, self.writer.scope, self.reader.reader, self.tokens,
                                       limits=self.selected_limits, cleanup_limits=CleanupTimeLimits(50))
        self.a = Link(self.writer)
        self.m = Link(self.reader)

    def pump(self, now):
        with patch('xenova_vehicle.runtime.resident_broker.j_us', return_value=now + 3):
            return self.broker.pump(self.a, self.m, now_us=now)

    def request(self, number, now):
        value = slot_fields('REQUEST', self.writer, request_number=number, issued=now,
                            expires=now + 1000, sequence=number)
        result = packet(value)
        self.broker.receive('A', result, now_us=now)
        return SlotMessage(result, self.writer, limits=self.selected_limits)

    def fill(self, request, now):
        grant = SlotMessage(self.a.sent[-1], self.writer, request, limits=self.selected_limits)
        writable, ready = self.tokens[grant.record.key.slot]
        self.assertTrue(writable.acquire(False))
        ready.release()
        value = grant.fields
        value.update(sender_instance=self.writer.bind.context.peer.worker_instance,
                     message_sequence=str(10000 + len(self.a.sent)), issued_j_us=str(now))
        value['payload'].update(op='FILLED', payload_length=960000, descriptor_sha256='c' * 64)
        result = packet(value)
        self.broker.receive('A', result, now_us=now)
        return grant

    def write(self, number, now):
        request = self.request(number, now)
        self.pump(now + 1)
        self.fill(request, now + 10)
        return request

    def test_busy_grant_keeps_original_reservation_and_request_expiry(self):
        request = self.request(501, 10000000)
        self.a.busy = True
        self.pump(10000001)
        first = deepcopy(self.a.attempts[-1])
        self.assertEqual(self.broker.snapshot()['slots'][0]['state'], 'WRITING')
        self.pump(10000050)
        self.a.busy = False
        self.pump(10000100)
        self.assertEqual(self.a.attempts[-1], first)
        self.assertEqual(len(self.a.sent), 1)
        grant = SlotMessage(self.a.sent[0], self.writer, request, limits=self.selected_limits)
        self.assertEqual(grant.issued_us, 10000001)
        self.assertEqual(grant.expires_us, 10001000)
        self.assertEqual(self.tokens[0][0].acquires, 0)

    def test_m_import_delay_allows_two_ready_and_actual_unclaimed_replacement(self):
        self.write(501, 10000000)
        self.write(502, 10000030)
        first_posts = self.tokens[0][0].posts
        request = self.request(503, 10000060)
        self.pump(10000061)
        grant = SlotMessage(self.a.sent[-1], self.writer, request, limits=self.selected_limits)
        self.assertEqual((grant.record.key.slot, grant.record.key.generation), (0, 2))
        self.assertEqual(self.tokens[0][0].posts, first_posts + 1)
        self.assertEqual(self.tokens[0][1].value, 0)
        self.assertEqual(self.tokens[0][0].value, 1)
        self.assertFalse(self.broker.snapshot()['reader_bound'])
        self.assertEqual(self.m.sent, [])

    def test_selected_offer_is_protected_while_other_ready_keeps_replacing(self):
        self.write(501, 10000000)
        self.write(502, 10000030)
        self.broker.bind_reader(self.reader.bind)
        self.pump(10000045)
        offered = SlotMessage(self.m.sent[-1], self.reader, limits=self.selected_limits)
        self.assertEqual(offered.record.content.key.slot, 1)
        self.write(503, 10000060)
        request = self.request(504, 10000090)
        self.pump(10000091)
        grant = SlotMessage(self.a.sent[-1], self.writer, request, limits=self.selected_limits)
        self.assertEqual(grant.record.key.slot, 0)
        self.assertEqual(grant.record.key.generation, 3)
        self.assertEqual(self.tokens[1][1].value, 1)
        self.assertEqual(len(self.m.sent), 1)

    def test_busy_unselected_offer_also_protects_its_ready_slot(self):
        self.write(501, 10000000)
        self.write(502, 10000030)
        self.broker.bind_reader(self.reader.bind)
        self.m.busy = True
        self.pump(10000045)
        original = deepcopy(self.m.attempts[-1])
        request = self.request(503, 10000060)
        self.pump(10000061)
        grant = SlotMessage(self.a.sent[-1], self.writer, request, limits=self.selected_limits)
        self.assertEqual(grant.record.key.slot, 0)
        self.m.busy = False
        self.pump(10000070)
        self.assertEqual(self.m.attempts[-1], original)
        self.assertEqual(self.m.sent[-1].fields['issued_j_us'], '10000040')
        self.assertEqual(self.m.sent[-1].fields['expires_j_us'], '10000540')

    def test_read_reserved_before_busy_acquired_and_late_fenced_release_never_posts(self):
        self.write(501, 10000000)
        self.broker.bind_reader(self.reader.bind)
        self.pump(10000012)
        offer = SlotMessage(self.m.sent[-1], self.reader, limits=self.selected_limits)
        value = offer.fields
        value.update(sender_instance=self.reader.reader.worker_instance, request_id=identifier(601),
                     message_sequence='600', issued_j_us='10000020', expires_j_us='10000170')
        value['payload'].update(op='ACQUIRE', purpose='PARSER_WARMUP')
        acquire_packet = packet(value)
        acquire = SlotMessage(acquire_packet, self.reader, offer, limits=self.selected_limits)
        self.broker.receive('M', acquire_packet, now_us=10000021)
        self.m.busy = True
        self.pump(10000022)
        original = deepcopy(self.m.attempts[-1])
        self.assertEqual(self.broker.snapshot()['slots'][0]['state'], 'IN_USE')
        self.pump(10000030)
        self.m.busy = False
        self.pump(10000040)
        self.assertEqual(self.m.attempts[-1], original)
        acquired = SlotMessage(self.m.sent[-1], self.reader, acquire, limits=self.selected_limits)
        self.assertEqual(acquired.issued_us, 10000021)
        writable, ready = self.tokens[0]
        self.assertTrue(ready.acquire(False))
        writable.release()
        posts = writable.posts
        self.broker.fence(10000170)
        release_fields = acquired.fields
        release_fields.update(sender_instance=self.reader.reader.worker_instance, message_sequence='601',
                              issued_j_us='10000200', expires_j_us='10000250')
        release_fields['payload']['op'] = 'RELEASE'
        released = packet(release_fields)
        result = self.broker.receive('M', released, now_us=10000300)
        self.assertIs(result.status, LeaseStatus.APPLIED)
        self.assertEqual(self.broker.snapshot()['slots'][0]['state'], 'FREE')
        again = self.broker.receive('M', released, now_us=10000301)
        self.assertIs(again.status, LeaseStatus.DUPLICATE_NO_EFFECT)
        self.assertEqual(writable.posts, posts)
        self.assertEqual(self.pump(10000302), [])

    def test_expired_reserved_draft_fences_without_renewing_or_selecting_a_sequence(self):
        self.request(501, 10000000)
        self.a.busy = True
        self.pump(10000001)
        original = deepcopy(self.a.attempts[-1])
        with self.assertRaises(BrokerError):
            self.pump(10001000)
        self.assertTrue(self.broker.snapshot()['fenced'])
        self.assertEqual(self.a.sent, [])
        self.assertEqual(self.a.attempts, [original])
        self.assertEqual(self.broker.snapshot()['slots'][0]['state'], 'WRITING')

    def test_partial_token_replacement_fences_and_never_retries_the_post(self):
        self.write(501, 10000000)
        self.write(502, 10000030)
        # 시험 전용 fault: 논리 READY와 실제 ready token을 불일치시킨다.
        self.tokens[0][1].value = 0
        self.request(503, 10000060)
        with self.assertRaises(BrokerError):
            self.pump(10000061)
        state = self.broker.snapshot()
        self.assertTrue(state['fenced'])
        self.assertEqual(state['fault'], 'PARTIAL')
        self.assertTrue(state['slots'][0]['replacement'])
        calls = self.tokens[0][1].acquires
        self.assertEqual(self.tokens[0][0].posts, 0)
        self.assertEqual(self.pump(10000070), [])
        self.assertEqual(self.tokens[0][1].acquires, calls)

    def test_old_ready_completion_is_not_renewed_when_m_binds_late(self):
        self.write(501, 10000000)
        self.broker.bind_reader(self.reader.bind)
        self.pump(10000510)
        self.assertEqual(self.m.sent, [])
        self.write(502, 10000600)
        self.pump(10000612)
        offer = SlotMessage(self.m.sent[-1], self.reader, limits=self.selected_limits)
        self.assertEqual(offer.issued_us, 10000610)
        self.assertEqual(offer.expires_us, 10001110)

    def test_old_acquire_after_new_generation_offer_is_stale_without_reserving_or_touching_tokens(self):
        self.write(501, 10000000)
        self.broker.bind_reader(self.reader.bind)
        self.pump(10000012)
        old_offer = SlotMessage(self.m.sent[-1], self.reader, limits=self.selected_limits)
        delayed = old_offer.fields
        delayed.update(sender_instance=self.reader.reader.worker_instance, request_id=identifier(601),
                       message_sequence='600', issued_j_us='10000509', expires_j_us='10000659')
        delayed['payload'].update(op='ACQUIRE', purpose='PARSER_WARMUP')
        delayed_packet = packet(delayed)
        # 다른 READY를 채운 뒤 원 OFFER 만료 때 slot0을 교체하고 새 OFFER를 선택한다.
        self.write(502, 10000030)
        third = self.request(503, 10000511)
        self.pump(10000512)
        self.fill(third, 10000522)
        self.pump(10000541)
        current = SlotMessage(self.m.sent[-1], self.reader, limits=self.selected_limits)
        self.assertEqual(current.record.content.key.slot, old_offer.record.content.key.slot)
        self.assertEqual(current.record.content.key.generation, 2)
        before = self.broker.snapshot()
        token_calls = [(token.acquires, token.posts) for pair in self.tokens for token in pair]
        sent_counts = len(self.a.sent), len(self.m.sent)
        self.assertIsNone(self.broker.receive('M', delayed_packet, now_us=10000542))
        after = self.broker.snapshot()
        self.assertEqual(after['slots'], before['slots'])
        self.assertEqual(after['reader_draft'], before['reader_draft'])
        self.assertEqual(after['counts'].get('reads_reserved', 0), before['counts'].get('reads_reserved', 0))
        self.assertEqual(after['counts']['stale_acquire'], before['counts'].get('stale_acquire', 0) + 1)
        self.assertEqual([(token.acquires, token.posts) for pair in self.tokens for token in pair], token_calls)
        self.assertEqual((len(self.a.sent), len(self.m.sent)), sent_counts)


if __name__ == '__main__':
    unittest.main()
