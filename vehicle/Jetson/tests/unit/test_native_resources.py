"""정상 raw 게시·owned 반환의 작은 통합 단위. fake token은 OS fence 증거가 아니다."""

import unittest
from unittest.mock import patch

from raw_slot_fixtures import bundle, identifier
from resource_guard_fixtures import manifest
from xenova_vehicle.runtime import native_resources as native
from xenova_vehicle.runtime.raw_leases import (Endpoint, LeaseRequest, Offer, ReadKey,
                                               ReadLease, ReadRequest, WriteGrant)
from xenova_vehicle.runtime.raw_slot import ExpectedSlot, encode_owned
from xenova_vehicle.runtime.resource_guard import LoadOutcome
from xenova_vehicle.runtime.resource_store import StoreAck, StoreLoad, StoreProposal


class Token:
    def __init__(self, count):
        self.count = count
        self.posts = self.acquires = 0

    def acquire(self, blocking):
        assert blocking is False
        self.acquires += 1
        if not self.count:
            return False
        self.count -= 1
        return True

    def release(self):
        self.count += 1
        self.posts += 1


class Buffer:
    def __init__(self):
        self.data = bytearray(native.ARENA_BYTES)
        self.buf = memoryview(self.data)


def attachment(cls, scope, tokens, buffer):
    # public attach의 실제 OS I/O는 root Nano 정상 흐름에서 별도로 확인한다.
    result = object.__new__(cls)
    result._scope, result._tokens, result._shm = scope, tokens, buffer
    result._closed = False
    result._seen = [None, None]
    return result


class NativeResourceTests(unittest.TestCase):
    def setUp(self):
        self.context, descriptor, body = bundle()
        self.encoding = encode_owned(self.context, descriptor, body)
        self.tokens = tuple((Token(1), Token(0)) for _ in range(2))
        self.buffer = Buffer()
        self.writer = attachment(native.RawArenaWriter, self.context.write_key.scope,
                                 self.tokens, self.buffer)
        self.grant = WriteGrant(self.context.write_key, LeaseRequest(identifier(500), 3000, 10000), 3500)

    def publish(self):
        with patch.object(native, '_now', return_value=4000):
            return self.writer.publish(self.grant, self.encoding)

    def lease(self, info):
        reader = Endpoint(identifier(510), identifier(511))
        request = ReadRequest(Offer(info), reader, LeaseRequest(identifier(512), 4100, 10000))
        return ReadLease(ReadKey(info.key, reader, identifier(513)), request, 4200)

    def test_publish_copy_return_preserves_owned_bytes_and_posts_once(self):
        info = self.publish()
        reader = attachment(native.RawArenaReader, self.context.write_key.scope,
                            self.tokens, self.buffer)
        lease = self.lease(info)
        with patch.object(native, '_now', return_value=4500):
            owned, fact = reader.copy_owned(lease, ExpectedSlot(self.context, info))
        self.assertEqual(owned.raw, self.encoding.raw)
        self.assertEqual((fact.views_ended_us, fact.writable_posted_us), (4500, 4500))
        self.assertEqual([(x.count, y.count) for x, y in self.tokens], [(1, 0), (1, 0)])
        self.buffer.data[:len(owned.raw)] = bytes(len(owned.raw))
        self.assertEqual(owned.raw, self.encoding.raw)

    def test_duplicate_publish_returns_original_fact_without_second_token_use(self):
        first = self.publish()
        second = self.publish()
        self.assertIs(first, second)
        self.assertEqual((self.tokens[0][0].acquires, self.tokens[0][1].posts), (1, 1))

    def test_missing_writable_does_not_copy_or_post_ready(self):
        self.tokens[0][0].count = 0
        with self.assertRaises(native.NativeResourceError):
            self.publish()
        self.assertFalse(any(self.buffer.data))
        self.assertEqual(self.tokens[0][1].posts, 0)

    def test_expired_write_does_not_take_token(self):
        with patch.object(native, '_now', return_value=10000):
            with self.assertRaises(ValueError):
                self.writer.publish(self.grant, self.encoding)
        self.assertEqual(self.tokens[0][0].acquires, 0)

    def test_corrupt_copy_returns_token_once_and_cannot_repeat_lease(self):
        info = self.publish()
        self.buffer.data[4096] = 1
        reader = attachment(native.RawArenaReader, self.context.write_key.scope,
                            self.tokens, self.buffer)
        lease = self.lease(info)
        with patch.object(native, '_now', return_value=4500):
            with self.assertRaises(ValueError):
                reader.copy_owned(lease, ExpectedSlot(self.context, info))
            with self.assertRaises(native.NativeResourceError):
                reader.copy_owned(lease, ExpectedSlot(self.context, info))
        self.assertEqual((self.tokens[0][0].posts, self.tokens[0][1].acquires), (1, 1))

    def test_no_intent_ack_cannot_reach_native_constructor(self):
        m = manifest()
        load = StoreLoad(identifier(600), LoadOutcome(identifier(601), 'MISSING'))
        proposal = StoreProposal(identifier(602), load.lock_nonce, load, m,
                                 m.raw_sha256, m.canonical_bytes, m.ref)
        ack = StoreAck(proposal, 'REPLACED')
        ctx = type('Context', (), {'get_start_method': lambda self: 'spawn'})()
        resources = native.NativeResources(ctx)
        with patch.object(native, '_creator_profile') as creator:
            with self.assertRaises(native.NativeResourceError):
                resources.create_one(ack)
        creator.assert_not_called()

    def test_fixed_arena_sizes_reject_other_shapes(self):
        key = identifier(700)
        self.assertEqual(native.ArenaSpec(key, '/xnv_raw_' + key, 4194304, 'RAW').size_bytes, 4194304)
        self.assertEqual(native.ArenaSpec(key, '/xnv_db_' + key, native.DB_COPY_BYTES, 'DB_COPY').role, 'DB_COPY')
        with self.assertRaises(native.NativeResourceError):
            native.ArenaSpec(key, '/xnv_raw_' + key, True, 'RAW')

    def test_cleanup_failure_remains_failed_on_second_call_without_retry(self):
        class FailedShm:
            def __init__(self):
                self.closes = self.unlinks = 0
                self.failure = OSError('injected close failure')

            def close(self):
                self.closes += 1
                raise self.failure

            def unlink(self):
                self.unlinks += 1

        ctx = type('Context', (), {'get_start_method': lambda self: 'spawn'})()
        resources = native.NativeResources(ctx)
        shm = FailedShm()
        resources._shms['RAW_CURRENT'] = shm
        for _ in range(2):
            with self.assertRaises(native.NativeResourceError) as caught:
                resources.close_after_children()
            self.assertIs(caught.exception.__cause__, shm.failure)
        self.assertEqual((shm.closes, shm.unlinks), (1, 1))


if __name__ == '__main__':
    unittest.main()
