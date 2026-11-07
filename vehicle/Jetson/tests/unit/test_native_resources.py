"""정상 raw 게시·owned 반환의 작은 통합 단위. fake token은 OS fence 증거가 아니다."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from raw_slot_fixtures import bundle, identifier
from resource_guard_fixtures import encode, manifest, minimal_fields
from xenova_vehicle.runtime import native_resources as native
from xenova_vehicle.runtime.raw_leases import (Endpoint, LeaseRequest, Offer, RawScope,
                                               RawOwnerExitBarrier, RawOwnerExitEvidence,
                                               RawRecoveryBarrier, ReadKey, ReadLease,
                                               ReadRequest, WriteGrant)
from xenova_vehicle.runtime.raw_slot import ExpectedSlot, encode_owned
from xenova_vehicle.runtime.resource_guard import LoadOutcome
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
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


def ack(target, original=None, number=800):
    target = ResourceManifest(target.canonical_bytes)
    source = manifest() if original is None else original
    load = StoreLoad(identifier(number), LoadOutcome(identifier(number + 1), 'VALID', source))
    proposal = StoreProposal(identifier(number + 2), load.lock_nonce, load, target,
                             target.raw_sha256, target.raw_bytes, target.ref)
    return StoreAck(proposal, 'REPLACED')


def recovery_resources():
    fields = minimal_fields()
    fields['revision'] = '2'
    raw_id, db_id = identifier(810), identifier(811)
    for slot, key, size in (('RAW_CURRENT', raw_id, native.ARENA_BYTES),
                            ('DB_COPY_0', db_id, native.DB_COPY_BYTES)):
        fields['resources'][slot] = dict(state='KNOWN', name=('/xnv_raw_' if slot.startswith('RAW')
                                                              else '/xnv_db_') + key,
                                        object_id=key, size_bytes=str(size))
    for i, slot in enumerate(native.TOKEN_SLOTS):
        fields['resources'][slot] = dict(state='KNOWN', name='/mp-fixture_' + str(i),
                                        object_id=None, size_bytes=None)
    m = ResourceManifest(encode(fields))
    ctx = type('Context', (), {'get_start_method': lambda self: 'spawn'})()
    resources = native.NativeResources(ctx)
    resources._created = {r.slot: r for r in m.known_resources}
    resources._tokens = {s: Token(1 if s.endswith('WRITABLE') else 0) for s in native.TOKEN_SLOTS}
    resources._raw_attempt = (raw_id, 1)
    scope = RawScope(m.service_instance, raw_id, Endpoint(identifier(812), identifier(813)),
                     'a' * 64, '/xnv_raw_' + raw_id)
    return resources, m, scope


class RetirableShm:
    def __init__(self, name, size, fail=False):
        self.name, self.size, self._fd = name, size, 14
        self.closes = self.unlinks = 0
        self.fail = fail

    def close(self):
        self.closes += 1
        if self.fail:
            raise OSError('injected retirement close failure')

    def unlink(self):
        self.unlinks += 1


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

    def normalized(self, resources, m, scope, *, owner_exit=False):
        barrier = (RawOwnerExitBarrier(scope, 2500, 3000) if owner_exit else
                   RawRecoveryBarrier(scope, Endpoint(identifier(820), identifier(821)),
                                      identifier(822), 'b' * 64, 3000, 2500))
        with patch.object(resources, '_root', return_value=22), \
                patch.object(resources, '_check_root'), patch.object(resources, '_close_root'), \
                patch.object(native, '_now', return_value=4000):
            result = resources.normalize_raw(ack(m), identifier(823), barrier)
        return barrier, result

    def test_normalization_is_once_per_original_operation_and_preserves_db_pair(self):
        resources, m, scope = recovery_resources()
        resources._tokens['RAW_0_WRITABLE'].count = 0
        resources._tokens['RAW_1_WRITABLE'].count = 0
        resources._tokens['RAW_1_READY'].count = 1
        db_before = tuple((resources._tokens[s].count, resources._tokens[s].acquires,
                           resources._tokens[s].posts) for s in native.TOKEN_SLOTS[4:])
        barrier, first = self.normalized(resources, m, scope)
        before = tuple((t.count, t.acquires, t.posts) for t in resources._tokens.values())
        second = resources.normalize_raw(ack(m), identifier(823), barrier)
        self.assertIs(first, second)
        self.assertEqual(before, tuple((t.count, t.acquires, t.posts) for t in resources._tokens.values()))
        self.assertEqual([resources._tokens[s].count for s in native.TOKEN_SLOTS[:4]], [1, 0, 1, 0])
        self.assertEqual(db_before, tuple((resources._tokens[s].count, resources._tokens[s].acquires,
                                          resources._tokens[s].posts) for s in native.TOKEN_SLOTS[4:]))
        self.assertEqual(first.normalized_us, 4000)

    def test_duplicate_token_is_sticky_and_never_repaired_by_retry(self):
        resources, m, scope = recovery_resources()
        resources._tokens['RAW_0_READY'].count = 2
        barrier = RawOwnerExitBarrier(scope, 2500, 3000)
        with patch.object(resources, '_root', return_value=22), patch.object(resources, '_close_root'), \
                patch.object(native, '_now', return_value=4000):
            with self.assertRaises(native.NativeResourceError):
                resources.normalize_raw(ack(m), identifier(823), barrier)
        counts = tuple((t.count, t.acquires, t.posts) for t in resources._tokens.values())
        with self.assertRaises(native.NativeResourceError):
            resources.normalize_raw(ack(m), identifier(823), barrier)
        self.assertEqual(counts, tuple((t.count, t.acquires, t.posts) for t in resources._tokens.values()))
        self.assertTrue(all(t.posts == 0 for t in resources._tokens.values()))

    def retired_ack(self, resources, m):
        fields = m.fields
        fields['revision'] = '3'
        fields['resources']['RAW_RETIRED'] = fields['resources']['RAW_CURRENT']
        fields['resources']['RAW_CURRENT'] = dict(state='NOT_ATTEMPTED', name=None,
                                                  object_id=None, size_bytes=None)
        target = ResourceManifest(encode(fields))
        return target, ack(target, m, number=830)

    def test_owner_exit_retire_and_new_physical_raw_reuse_fixed_tokens(self):
        resources, m, scope = recovery_resources()
        _, normalized = self.normalized(resources, m, scope, owner_exit=True)
        self.assertIs(type(normalized), RawOwnerExitEvidence)
        old = RetirableShm(scope.arena_name[1:], native.ARENA_BYTES)
        resources._shms['RAW_CURRENT'] = old
        target, retired_ack = self.retired_ack(resources, m)
        entry = SimpleNamespace(st_dev=1, st_ino=2)
        tokens = tuple(resources._tokens.values())
        with patch.object(resources, '_root', return_value=22), \
                patch.object(resources, '_check_root'), patch.object(resources, '_close_root'), \
                patch.object(native.os, 'stat', side_effect=[entry, FileNotFoundError()]), \
                patch.object(native.os, 'fstat', return_value=entry), \
                patch.object(native, '_now', return_value=4500):
            first = resources.retire_raw(retired_ack, identifier(840))
        self.assertIs(resources.retire_raw(retired_ack, identifier(840)), first)
        self.assertEqual((old.closes, old.unlinks), (1, 1))
        self.assertNotIn('RAW_RETIRED', resources._shms)
        fields = target.fields
        fields['revision'] = '4'
        fields['resources']['RAW_RETIRED'] = dict(state='NOT_ATTEMPTED', name=None,
                                                  object_id=None, size_bytes=None)
        fields['resources']['RAW_CURRENT'] = dict(state='INTENT', name=None,
                                                  object_id=None, size_bytes=None)
        new_id = identifier(841)
        fields['intents'] = [dict(intent_id=identifier(842), slot='RAW_CURRENT', operation='CREATE',
                                  planned_name='/xnv_raw_' + new_id, planned_object_id=new_id,
                                  planned_size_bytes=str(native.ARENA_BYTES))]
        planned = ResourceManifest(encode(fields))
        new_shm = RetirableShm('xnv_raw_' + new_id, native.ARENA_BYTES)
        entry = SimpleNamespace(st_dev=1, st_ino=3, st_mode=0o100600, st_uid=1000,
                                st_nlink=1, st_size=native.ARENA_BYTES)
        with patch.object(native, '_creator_profile'), patch.object(resources, '_root', return_value=22), \
                patch.object(resources, '_check_root'), patch.object(resources, '_close_root'), \
                patch.object(native.os, 'stat', side_effect=[FileNotFoundError(), entry]), \
                patch.object(native.os, 'fstat', return_value=entry), \
                patch.object(native.os, 'geteuid', return_value=1000), \
                patch.object(native, '_shared_memory_module', return_value=SimpleNamespace(
                    SharedMemory=lambda **kwargs: new_shm)):
            result = resources.create_one(ack(planned, target, number=850))
        self.assertEqual(result.object_id, new_id)
        self.assertEqual(tuple(resources._tokens.values()), tokens)

    def test_retirement_failure_blocks_repeat_and_next_allocation(self):
        resources, m, scope = recovery_resources()
        self.normalized(resources, m, scope, owner_exit=True)
        old = RetirableShm(scope.arena_name[1:], native.ARENA_BYTES, fail=True)
        resources._shms['RAW_CURRENT'] = old
        _, retired_ack = self.retired_ack(resources, m)
        entry = SimpleNamespace(st_dev=1, st_ino=2)
        with patch.object(resources, '_root', return_value=22), patch.object(resources, '_close_root'), \
                patch.object(native.os, 'stat', return_value=entry), \
                patch.object(native.os, 'fstat', return_value=entry):
            with self.assertRaises(native.NativeResourceError):
                resources.retire_raw(retired_ack, identifier(840))
        with self.assertRaises(native.NativeResourceError):
            resources.retire_raw(retired_ack, identifier(840))
        with self.assertRaises(native.NativeResourceError):
            resources.create_one(retired_ack)
        self.assertEqual((old.closes, old.unlinks), (1, 1))


if __name__ == '__main__':
    unittest.main()
