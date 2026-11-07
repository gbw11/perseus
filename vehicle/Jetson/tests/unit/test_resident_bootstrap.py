"""명시 설치·원 receipt·내구 publish의 정상 연결과 설치 우회 금지를 검사한다."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from xenova_vehicle.runtime.process_observation import ProcessObserver
from xenova_vehicle.runtime.resource_store import ResourceStore, StoreUsageError
from xenova_vehicle.runtime.runtime_bootstrap import (
    BootstrapUnavailable, MARKER, consume_first_install, initialize_runtime_state,
)


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux store/procfs 설치 흐름 시험')
class RuntimeBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xenova-bootstrap-test-')
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.parent.chmod(0o700)

    def installed_store(self):
        directory = self.parent / str(uuid.uuid4())
        initialized = initialize_runtime_state(directory)
        self.assertEqual(initialized['state'], 'READY')
        store = ResourceStore(str(directory))
        self.addCleanup(store.close)
        observer = ProcessObserver(store, str(uuid.uuid4()))
        return directory, store, observer

    def test_explicit_install_is_consumed_before_original_proposal_publish(self):
        directory, store, observer = self.installed_store()
        observed = observer.observe()
        self.assertEqual(observed.original_load.outcome.kind, 'MISSING')
        self.assertEqual(observed.latest_load.outcome.kind, 'MISSING')
        admission = consume_first_install(store, observed, resource_epoch=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
        marker_raw = (directory / MARKER).read_bytes()
        marker = json.loads(marker_raw)
        self.assertEqual(marker['state'], 'CONSUMED')
        self.assertFalse((directory / ResourceStore.DATA_NAME).exists())
        self.assertIs(admission.proposal.expected, observed.latest_load)
        self.assertEqual(marker['target_ref']['raw_sha256'], admission.proposal.target_ref.raw_sha256)
        self.assertEqual(admission.decision.kind, 'FIRST_INSTALL_ELIGIBLE')
        result = store.publish(admission.proposal)
        self.assertEqual(result.kind, 'DURABLE')
        self.assertIs(result.ack.proposal, admission.proposal)
        loaded = store.load()
        self.assertEqual(loaded.outcome.kind, 'VALID')
        self.assertEqual(loaded.outcome.manifest.raw_bytes, admission.proposal.target_bytes)
        self.assertEqual(loaded.outcome.manifest.fields['state'], 'DIRTY')
        with self.assertRaises(BootstrapUnavailable):
            consume_first_install(store, observer.observe(), resource_epoch=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
        self.assertEqual((directory / MARKER).read_bytes(), marker_raw)

    def test_closed_store_or_superseded_latest_does_not_consume_ready_marker(self):
        for condition in ('closed', 'superseded'):
            with self.subTest(condition=condition):
                directory, store, observer = self.installed_store()
                observed = observer.observe()
                marker_raw = (directory / MARKER).read_bytes()
                if condition == 'closed':
                    store.close()
                else:
                    newer = store.load()
                    self.assertIsNot(newer, observed.latest_load)
                with self.assertRaises(StoreUsageError):
                    consume_first_install(store, observed, resource_epoch=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
                self.assertEqual((directory / MARKER).read_bytes(), marker_raw)
                self.assertEqual(json.loads(marker_raw)['state'], 'READY')
                self.assertFalse((directory / ResourceStore.DATA_NAME).exists())

    def test_plain_missing_directory_is_not_installation_and_creates_no_ipc(self):
        directory = self.parent / 'uninitialized'
        directory.mkdir(mode=0o700)
        store = ResourceStore(str(directory))
        self.addCleanup(store.close)
        observed = ProcessObserver(store, str(uuid.uuid4())).observe()
        self.assertEqual(observed.latest_load.outcome.kind, 'MISSING')
        with patch('multiprocessing.shared_memory.SharedMemory', side_effect=AssertionError('unexpected SHM creation')) as shared_memory, \
                patch('multiprocessing.context.BaseContext.Semaphore', side_effect=AssertionError('unexpected token creation')) as semaphore:
            with self.assertRaises(FileNotFoundError):
                consume_first_install(store, observed, resource_epoch=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
            shared_memory.assert_not_called()
            semaphore.assert_not_called()
        self.assertFalse((directory / MARKER).exists())
        self.assertFalse((directory / ResourceStore.DATA_NAME).exists())
