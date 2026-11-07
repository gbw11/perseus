"""D bootstrap의 핵심 파일/lock·순수 protocol 경계. 실제 D spawn은 통합에서 검사한다."""

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.resident_storage import (
    StorageIncompatible, StorageProfile, StorageProtocolError, _Database,
    binding_payload, discovery_payload, parse_bootstrap_result, parse_storage_bind_ack, storage_binding,
)
from xenova_vehicle.runtime.resident_transport import j_us


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux D backend fixture')
class StorageDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xenova-storage-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o700)
        self.path = self.directory / 'map.sqlite3'

    def open(self, policy='EXISTING_ONLY'):
        return _Database(StorageProfile(str(self.path), policy), j_us() + 5_000_000)

    def test_explicit_create_has_stable_uuid_and_durable_allocator(self):
        database = self.open('CREATE_IF_ABSENT')
        first = dict(database.metadata)
        self.assertTrue(database.created)
        self.assertEqual(first['allocator_highwater'], '1')
        self.assertEqual(database.connection.execute('PRAGMA synchronous').fetchone(), (2,))
        database.close()
        reopened = self.open()
        try:
            self.assertFalse(reopened.created)
            self.assertEqual(reopened.metadata, first)
        finally:
            reopened.close()

    def test_existing_only_does_not_create_missing_database(self):
        with self.assertRaises(FileNotFoundError):
            self.open()
        self.assertFalse(self.path.exists())

    def test_existing_damaged_and_empty_files_are_preserved(self):
        for payload in (b'', b'not a SQLite database\n'):
            with self.subTest(payload=payload):
                self.path.write_bytes(payload)
                self.path.chmod(0o600)
                with self.assertRaises(StorageIncompatible):
                    self.open('CREATE_IF_ABSENT')
                self.assertEqual(self.path.read_bytes(), payload)

    def test_actual_writer_lock_rejects_second_connection(self):
        database = self.open('CREATE_IF_ABSENT')
        try:
            with self.assertRaises(BlockingIOError):
                self.open()
            self.assertEqual(database.metadata['allocator_highwater'], '1')
        finally:
            database.close()

    def test_existing_checkpoint_is_not_reported_as_empty(self):
        database = self.open('CREATE_IF_ABSENT')
        database.connection.execute('INSERT INTO checkpoints VALUES (?,1,?)', (str(uuid.uuid4()), '{}'))
        database.close()
        with self.assertRaises(StorageIncompatible):
            self.open()


class StorageProtocolTests(unittest.TestCase):
    def setUp(self):
        self.peer = LifecyclePeer(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 'D', 'a' * 64)
        self.profile = StorageProfile(str(Path(tempfile.gettempdir()).absolute() / 'xenova-protocol-only.sqlite3'))

    def packet(self, kind, payload, *, child=False, revision=0, request_id=None, issued=100, expires=1000):
        return encode_structure(dict(version=1, service_instance=self.peer.service_instance,
            channel_epoch=self.peer.channel_epoch,
            sender_instance=self.peer.worker_instance if child else self.peer.service_instance,
            message_sequence='1', kind=kind, binding_domain='STORAGE', binding_revision=str(revision),
            request_id=str(uuid.uuid4()) if request_id is None else request_id,
            issued_j_us=str(issued), expires_j_us=str(expires), payload=payload))

    def test_original_nonce_profile_deadline_and_binding_survive_roundtrip(self):
        request = self.packet('DB_JOB', discovery_payload(self.profile, str(uuid.uuid4())))
        body = dict(discovery_nonce=request.payload['body']['discovery_nonce'], storage_profile_sha256=self.profile.sha256,
                    status='EMPTY', database_uuid=str(uuid.uuid4()), schema_version=1, allocator_highwater='1',
                    allocator_ready=True, selected_checkpoint=None)
        reply = self.packet('DB_JOB', dict(op='BOOTSTRAP_RESULT', body=body), child=True,
                            request_id=request.fields['request_id'], issued=150)
        received = parse_bootstrap_result(reply, self.peer, self.profile, request, now_us=200)
        bind = self.packet('BIND', binding_payload(storage_binding(self.peer, self.profile, received)), revision=1, issued=250)
        ack = self.packet('BIND_ACK', dict(binding_revision='1', binding_sha256=bind.payload['binding_sha256'],
                          accepted=True, state='IDLE', reason=None), child=True, revision=1,
                          request_id=bind.fields['request_id'], issued=300)
        self.assertTrue(parse_storage_bind_ack(ack, self.peer, bind, now_us=350)['accepted'])
        changed = deepcopy(body)
        changed['discovery_nonce'] = str(uuid.uuid4())
        wrong = self.packet('DB_JOB', dict(op='BOOTSTRAP_RESULT', body=changed), child=True,
                            request_id=request.fields['request_id'], issued=150)
        with self.assertRaises(StorageProtocolError):
            parse_bootstrap_result(wrong, self.peer, self.profile, request, now_us=200)
        with self.assertRaises(StorageProtocolError):
            parse_bootstrap_result(reply, self.peer, self.profile, request, now_us=1000)
