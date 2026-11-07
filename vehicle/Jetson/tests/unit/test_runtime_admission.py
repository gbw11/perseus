"""실제 Store/procfs로 설치·봉쇄·boot-reset producer 연결을 검사한다.

old-boot case는 자기 임시 catalogue에 이전 boot UUID를 넣은 통제 fixture다.
실제 OS 재부팅이나 systemd 실행자 검증을 대신하지 않으며 IPC 생성은 하지 않는다.
"""

from dataclasses import asdict
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from xenova_vehicle.runtime.native_resources import NativeResources
from xenova_vehicle.runtime.process_observation import ProcessObserver
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
from xenova_vehicle.runtime.resource_store import ResourceStore
from xenova_vehicle.runtime.runtime_admission import _record_manifest, consume_runtime_state, record_runtime_publication
from xenova_vehicle.runtime.runtime_bootstrap import (
    BootstrapUnavailable, EXECUTION_RECORD, consume_first_install, initialize_runtime_state,
)


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux Store/procfs admission 시험')
class RuntimeAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xenova-admission-')
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name)
        parent.chmod(0o700)
        self.directory = parent / 'state'
        initialize_runtime_state(self.directory)
        self.store = ResourceStore(str(self.directory))
        self.addCleanup(self.store.close)
        self.native = NativeResources(multiprocessing.get_context('spawn'))
        self.observer = ProcessObserver(self.store, str(uuid.uuid4()))

    def consume(self, observation):
        issued_us = time.monotonic_ns() // 1000
        return consume_runtime_state(self.store, observation, resource_epoch=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()), native_owner=self.native, issued_us=issued_us,
            expires_us=issued_us + 5_000_000)

    def publish(self, admission):
        result = self.store.publish(admission.proposal)
        self.assertEqual(result.kind, 'DURABLE')
        receipt = self.store.load()
        record_runtime_publication(self.store, result.ack, receipt)
        return receipt

    def test_install_preparation_precedes_publish_and_record_uses_actual_ack(self):
        observed = self.observer.observe()
        admission = self.consume(observed)
        record_path = self.directory / EXECUTION_RECORD
        prepared = json.loads(record_path.read_bytes())
        self.assertEqual(prepared['status'], 'PREPARED')
        self.assertIs(admission.proposal.expected, observed.latest_load)
        self.assertFalse((self.directory / ResourceStore.DATA_NAME).exists())
        receipt = self.publish(admission)
        published = json.loads(record_path.read_bytes())
        self.assertEqual(published['status'], 'PUBLISHED')
        self.assertEqual(published['source_ref'], asdict(receipt.outcome.manifest.ref))
        self.assertEqual(published['recovery_episode']['service_attempts'], 1)
        self.assertFalse(self.native.creation_attempted)

    def test_same_boot_incomplete_or_unmanaged_history_is_kept_fenced(self):
        first = self.consume(self.observer.observe())
        self.publish(first)
        record = (self.directory / EXECUTION_RECORD).read_bytes()
        catalogue = (self.directory / ResourceStore.DATA_NAME).read_bytes()
        with self.assertRaisesRegex(BootstrapUnavailable, 'same-boot legacy or incomplete'):
            self.consume(self.observer.observe())
        self.assertEqual((self.directory / EXECUTION_RECORD).read_bytes(), record)
        self.assertEqual((self.directory / ResourceStore.DATA_NAME).read_bytes(), catalogue)
        self.assertFalse(self.native.creation_attempted)

    def test_published_anchor_preserves_durable_counter_source_without_manager_or_sidecar_write(self):
        first = self.consume(self.observer.observe())
        receipt = self.publish(first)
        anchor_path = self.directory / EXECUTION_RECORD
        anchor_raw = anchor_path.read_bytes()
        anchor = json.loads(anchor_raw)
        episode_id = receipt.outcome.manifest.recovery_episode.episode_id
        # 임시 Store에 counter 진행/healthy reset을 직접 제안한다. 300초 건강 증거는 S 통합 시험의 책임이다.
        with patch('xenova_vehicle.runtime.service_scope._manager', side_effect=AssertionError('unexpected manager helper')) as manager, \
                patch('xenova_vehicle.runtime.runtime_admission.write_execution_record', side_effect=AssertionError('unexpected anchor mirror')) as writer:
            for count in (5, 0):
                fields = receipt.outcome.manifest.fields
                fields['revision'] = str(receipt.outcome.manifest.revision + 1)
                if count == 0:
                    episode_id = str(uuid.uuid4())
                fields['recovery_episode'] = dict(episode_id=episode_id, service_attempts=count,
                                                  role_attempts=dict(A=count, M=count, D=count))
                proposal = self.store.propose(receipt, ResourceManifest(_canonical(fields)), str(uuid.uuid4()))
                result = self.store.publish(proposal)
                self.assertEqual(result.kind, 'DURABLE')
                receipt = self.store.load()
                record_runtime_publication(self.store, result.ack, receipt)
                self.assertEqual(anchor_path.read_bytes(), anchor_raw)
                self.assertEqual(receipt.outcome.manifest.recovery_episode.service_attempts, count)
            manager.assert_not_called()
            writer.assert_not_called()
        current = self.store.load().outcome.manifest
        self.assertEqual(current.recovery_episode.episode_id, episode_id)
        self.assertEqual(current.recovery_episode.role_attempts, (0, 0, 0))
        self.assertEqual(anchor['recovery_episode']['service_attempts'], 1)
        _record_manifest(anchor, current)
        future = dict(anchor, source_ref=dict(anchor['source_ref'], revision=current.revision + 1))
        with self.assertRaises(BootstrapUnavailable):
            _record_manifest(future, current)
        changed_same_revision = dict(anchor, source_ref=dict(asdict(current.ref), raw_sha256='f' * 64))
        with self.assertRaises(BootstrapUnavailable):
            _record_manifest(changed_same_revision, current)

    def test_controlled_old_boot_uses_actual_current_scope_and_preserves_attempts(self):
        legacy = consume_first_install(self.store, self.observer.observe(),
                    resource_epoch=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
        self.assertEqual(self.store.publish(legacy.proposal).kind, 'DURABLE')
        receipt = self.store.load()
        fields = receipt.outcome.manifest.fields
        fields['runtime']['boot_id'] = str(uuid.uuid4())
        fields['revision'] = '2'
        fixture = ResourceManifest(_canonical(fields))
        proposal = self.store.propose(receipt, fixture, str(uuid.uuid4()))
        self.assertEqual(self.store.publish(proposal).kind, 'DURABLE')
        observed = self.observer.observe()
        self.assertNotEqual(observed.guard.runtime.boot_id, fixture.runtime.boot_id)
        admission = self.consume(observed)
        self.assertEqual(admission.decision.kind, 'FRESH_RESET_ELIGIBLE')
        self.assertIs(admission.proposal.expected, admission.name_observation.latest_load)
        self.assertEqual(admission.decision.predecessor.ref, fixture.ref)
        self.assertEqual(admission.manifest.runtime, observed.guard.runtime)
        self.assertEqual(admission.manifest.recovery_episode.episode_id, fixture.recovery_episode.episode_id)
        self.assertEqual(admission.manifest.recovery_episode.service_attempts, 2)
        self.assertEqual(admission.manifest.recovery_episode.role_attempts, (2, 2, 2))
        self.assertTrue(all(owner.outcome == 'UNKNOWN' for owner in admission.name_observation.after.owners))
        self.publish(admission)
        self.assertFalse(self.native.creation_attempted)
