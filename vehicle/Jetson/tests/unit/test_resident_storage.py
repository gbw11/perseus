"""D bootstrap의 핵심 파일/lock·순수 protocol 경계. 실제 D spawn은 통합에서 검사한다."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from xenova_vehicle.runtime.channel import control_pair
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer, LifecycleReceiver
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.resident_storage import (
    StorageBoot, StorageIncompatible, StorageProfile, StorageProtocolError, _Database, _diagnostic,
    binding_payload, discovery_payload, parse_bootstrap_result, parse_storage_bind_ack, storage_binding, storage_main,
)
from xenova_vehicle.runtime.resident_profile import release_sha256
from xenova_vehicle.runtime.resident_transport import ResidentLink, j_us


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


class StorageDiagnosticTests(unittest.TestCase):
    def test_new_nested_worker_directory_preserves_final_report(self):
        with tempfile.TemporaryDirectory(prefix='xenova-storage-diagnostic-') as temporary:
            parent = Path(temporary)
            peer = LifecyclePeer(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 'D', 'a' * 64)
            directory = parent / 'D' / peer.worker_instance
            boot = StorageBoot(peer, j_us() + 15_000_000,
                StorageProfile(str(parent / 'map.sqlite3')), diagnostic_directory=str(directory))
            report = dict(worker_instance=peer.worker_instance, stopped_report_sent=True,
                          database_closed=True, owner_lock_closed=True, error=None)
            self.assertFalse(directory.parent.exists())
            _diagnostic(boot, report)
            target = directory / ('storage-' + peer.worker_instance + '.json')
            self.assertEqual(json.loads(target.read_bytes()), report)
            with self.assertRaises(FileExistsError):
                _diagnostic(boot, dict(report, stopped_report_sent=False))
            self.assertEqual(json.loads(target.read_bytes()), report)

    @unittest.skipUnless(sys.platform == 'linux', '실제 Linux control_pair 종료 경로')
    def test_diagnostic_oserror_cannot_turn_successful_cleanup_into_worker_failure(self):
        with tempfile.TemporaryDirectory(prefix='xenova-diagnostic-failure-') as temporary:
            peer = LifecyclePeer(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 'D', release_sha256())
            parent, child = control_pair()
            link = ResidentLink(parent, peer, 'S', track_sent=True)
            receiver = LifecycleReceiver(peer, 'S')
            boot = StorageBoot(peer, j_us() + 15_000_000,
                StorageProfile(str(Path(temporary) / 'map.sqlite3')), diagnostic_directory=temporary)
            outcome = []

            def run():
                try:
                    storage_main(child, boot)
                    outcome.append('RETURNED')
                except BaseException as error:
                    outcome.append(error)

            def receive(kind):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    for packet in link.poll():
                        message = receiver.receive(packet, now_us=j_us()).message
                        if message is not None and message.kind == kind:
                            return message
                    time.sleep(0.005)
                self.fail('actual control packet was not received: ' + kind)

            worker = threading.Thread(target=run, daemon=True)
            with patch('xenova_vehicle.runtime.resident_storage._diagnostic', side_effect=OSError('controlled diagnostic failure')) as diagnostic:
                worker.start()
                try:
                    self.assertEqual(receive('HELLO').payload['worker_instance'], peer.worker_instance)
                    issued = j_us()
                    stop = link.send('STOP', dict(stop_revision='1', reason='TEST', drain_deadline_j_us=str(issued + 4_000_000)),
                                     issued_us=issued, expires_us=issued + 5_000_000)
                    self.assertIsNotNone(stop)
                    receiver.register_stop_intent(stop, now_us=j_us())
                    sent = link.take_sent()
                    self.assertEqual(len(sent), 1)
                    receiver.record_first_sent(stop, sent[0].started_us, now_us=j_us())
                    stopped = receive('STOPPED')
                    self.assertEqual(stopped.payload['status'], 'STOPPED')
                    self.assertTrue(stopped.payload['leases_released'])
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(outcome, ['RETURNED'])
                    diagnostic.assert_called_once()
                    report = diagnostic.call_args.args[1]
                    self.assertTrue(report['database_closed'] and report['owner_lock_closed'] and report['stopped_report_sent'])
                finally:
                    link.close()
                    worker.join(2)
                    child.close()
