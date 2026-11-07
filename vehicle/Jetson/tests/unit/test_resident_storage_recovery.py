"""D 재시작의 실제 DB identity/head와 짝지은 수신 검증을 검사한다.

SQLite 변경·소실은 자기 임시 DB fixture에만 주입한다. 실제 old-D reap와 새 D
spawn 순서 및 A/model 유지 여부는 상주 통합 시험의 책임이다. ID 예약 job이나
checkpoint 회복을 실행한 것으로 세지 않는다.
"""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.resident_storage import (
    StorageBoot, StorageIncompatible, StorageProfile, StorageProtocolError, StorageRecovery,
    _Database, discovery_payload, parse_bootstrap_result,
)
from xenova_vehicle.runtime.resident_transport import j_us


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux SQLite/owner-lock 복구 fixture')
class StorageRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xenova-storage-recovery-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o700)

    def admitted(self, *, highwater='1'):
        path = self.directory / (str(uuid.uuid4()) + '.sqlite3')
        profile = StorageProfile(str(path), 'CREATE_IF_ABSENT')
        database = _Database(profile, j_us() + 5_000_000)
        try:
            if highwater != '1':
                database.connection.execute('UPDATE storage_metadata SET allocator_highwater=? WHERE singleton=1', (highwater,))
        finally:
            database.close()
        observed = _Database(profile, j_us() + 5_000_000)
        try:
            metadata = dict(observed.metadata)
        finally:
            observed.close()
        body = dict(discovery_nonce=str(uuid.uuid4()), storage_profile_sha256=profile.sha256,
                    status='EMPTY', **metadata, allocator_ready=True, selected_checkpoint=None)
        return path, profile, body, StorageRecovery(profile, body)

    def packets(self, recovery, metadata):
        peer = LifecyclePeer(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 'D', 'a' * 64)
        request_id = str(uuid.uuid4())
        fields = dict(version=1, service_instance=peer.service_instance, channel_epoch=peer.channel_epoch,
                      sender_instance=peer.service_instance, message_sequence='1', kind='DB_JOB',
                      binding_domain='STORAGE', binding_revision='0', request_id=request_id,
                      issued_j_us='100', expires_j_us='1000')
        request = encode_structure(dict(fields, payload=discovery_payload(recovery.profile, str(uuid.uuid4()))))
        body = dict(discovery_nonce=request.payload['body']['discovery_nonce'], storage_profile_sha256=recovery.profile.sha256,
                    status='EMPTY', **metadata, allocator_ready=True, selected_checkpoint=None)
        reply = encode_structure(dict(fields, sender_instance=peer.worker_instance, issued_j_us='150',
                                      payload=dict(op='BOOTSTRAP_RESULT', body=body)))
        return peer, request, reply

    def test_recovery_reads_same_database_and_does_not_lower_durable_head(self):
        path, previous_profile, previous_body, recovery = self.admitted(highwater='5')
        original_uuid = recovery.database_uuid
        previous_body['database_uuid'] = str(uuid.uuid4())
        self.assertEqual(recovery.database_uuid, original_uuid)
        self.assertEqual(recovery.profile.database_open_policy, 'EXISTING_ONLY')
        self.assertEqual(recovery.profile.database_path, str(path))
        self.assertNotEqual(recovery.profile.sha256, previous_profile.sha256)
        database = _Database(recovery.profile, j_us() + 5_000_000, recovery=recovery)
        try:
            self.assertFalse(database.created)
            self.assertEqual(database.metadata['database_uuid'], original_uuid)
            self.assertEqual(database.metadata['allocator_highwater'], '5')
            peer, request, reply = self.packets(recovery, database.metadata)
            received = parse_bootstrap_result(reply, peer, recovery.profile, request, now_us=200, recovery=recovery)
            self.assertEqual(received['database_uuid'], original_uuid)
            self.assertEqual(received['allocator_highwater'], '5')
        finally:
            database.close()

    def test_missing_database_is_not_recreated_and_create_policy_is_rejected(self):
        path, previous_profile, _, recovery = self.admitted()
        identity = path.stat()
        self.assertEqual((path.stat().st_dev, path.stat().st_ino), (identity.st_dev, identity.st_ino))
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            _Database(recovery.profile, j_us() + 5_000_000, recovery=recovery)
        self.assertFalse(path.exists())
        peer = LifecyclePeer(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 'D', 'a' * 64)
        with self.assertRaises(StorageProtocolError):
            StorageBoot(peer, j_us() + 15_000_000, previous_profile, recovery=recovery)
        with self.assertRaises(StorageProtocolError):
            _Database(previous_profile, j_us() + 5_000_000, recovery=recovery)
        self.assertFalse(path.exists())

    def test_changed_uuid_or_regressed_head_is_rejected_on_both_sides(self):
        for changed_field, changed_value in (('database_uuid', str(uuid.uuid4())), ('allocator_highwater', '4')):
            with self.subTest(changed_field=changed_field):
                path, _, _, recovery = self.admitted(highwater='5')
                fixture = _Database(recovery.profile, j_us() + 5_000_000)
                try:
                    if changed_field == 'database_uuid':
                        fixture.connection.execute('UPDATE storage_metadata SET database_uuid=? WHERE singleton=1', (changed_value,))
                    else:
                        fixture.connection.execute('UPDATE storage_metadata SET allocator_highwater=? WHERE singleton=1', (changed_value,))
                finally:
                    fixture.close()
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(StorageIncompatible):
                    _Database(recovery.profile, j_us() + 5_000_000, recovery=recovery)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
                metadata = dict(database_uuid=recovery.database_uuid, schema_version=1,
                                allocator_highwater=recovery.minimum_allocator_highwater)
                metadata[changed_field] = changed_value
                peer, request, reply = self.packets(recovery, metadata)
                with self.assertRaises(StorageProtocolError):
                    parse_bootstrap_result(reply, peer, recovery.profile, request, now_us=200, recovery=recovery)
