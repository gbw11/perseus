"""Linux 임시 파일의 원 제안·CAS·내구 ACK 경계. 설치/자원 생성/SD 전원차단 시험은 아니다."""

from copy import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from resource_guard_fixtures import identifier, manifest, minimal_fields
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
from xenova_vehicle.runtime.resource_store import ResourceStore, StoreUsageError


def _source(revision=2, *, pretty=False, attempts=0):
    value = minimal_fields()
    value["revision"] = str(revision)
    value["recovery_episode"].update(service_attempts=attempts, role_attempts=dict(A=attempts, M=attempts, D=attempts))
    if pretty:
        raw = (json.dumps(value, indent=2, sort_keys=False) + "\n").encode("ascii")
    else:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    return ResourceManifest(raw)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux native 임시 디렉터리의 publish 시험")
class ResourceStorePublishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xnv-store-publish-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = self.temporary.name
        self.data = Path(self.directory) / "resource-set.json"

    def _write_raw(self, raw):
        """시험만 소유한 경로에서 비협조 writer/저장 전 원문을 모사한다."""
        descriptor = os.open(self.data, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)

    def _proposal(self, store, *, expected=None, source=None, request=1001):
        expected = store.load() if expected is None else expected
        source = _source() if source is None else source
        return store.propose(expected, source, identifier(request))

    def assert_unconfirmed(self, result):
        self.assertEqual(result.kind, "UNCONFIRMED")
        self.assertIsNone(result.ack)

    def assert_durable(self, result, proposal, *, method="REPLACED"):
        self.assertEqual(result.kind, "DURABLE")
        ack = result.ack
        self.assertIsNotNone(ack)
        self.assertIs(ack.proposal, proposal)
        self.assertEqual(ack.method, method)
        self.assertEqual(ack.request_id, proposal.request_id)
        self.assertEqual(ack.lock_nonce, proposal.lock_nonce)
        self.assertEqual(ack.target_ref, proposal.target_ref)
        expected = proposal.expected.outcome.manifest
        self.assertEqual(ack.expected_ref, None if expected is None else expected.ref)
        return ack

    def _fail_directory_fsync(self, store, proposal):
        real_fsync = os.fsync
        seen = []

        def fault(descriptor):
            is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
            seen.append("directory" if is_directory else "file")
            if is_directory:
                raise OSError(errno.EIO, "injected directory fsync failure")
            return real_fsync(descriptor)

        with patch.object(os, "fsync", side_effect=fault):
            result = store.publish(proposal)
        self.assertEqual(seen, ["file", "directory"])
        self.assert_unconfirmed(result)
        self.assertEqual(self.data.read_bytes(), proposal.target_bytes)
        return result

    def test_missing_publish_ack_names_actual_canonical_bytes_not_source_raw_sha(self):
        source = _source(pretty=True, attempts=5)
        self.assertNotEqual(source.raw_sha256, source.canonical_sha256)
        with ResourceStore(self.directory) as store:
            receipt = store.load()
            self.assertEqual(receipt.outcome.kind, "MISSING")
            proposal = self._proposal(store, expected=receipt, source=source)
            self.assertIs(proposal.expected, receipt)
            self.assertIs(proposal.manifest, source)
            self.assertEqual(proposal.lock_nonce, store.lock_nonce)
            self.assertEqual(proposal.source_raw_sha256, source.raw_sha256)
            self.assertEqual(proposal.target_bytes, source.canonical_bytes)
            published = ResourceManifest(proposal.target_bytes)
            self.assertEqual(proposal.target_ref, published.ref)
            self.assertNotEqual(proposal.target_ref.raw_sha256, proposal.source_raw_sha256)
            ack = self.assert_durable(store.publish(proposal), proposal)
            self.assertEqual(self.data.read_bytes(), source.canonical_bytes)
            self.assertEqual(ack.target_ref.raw_sha256, hashlib.sha256(self.data.read_bytes()).hexdigest())
            current = store.load().outcome.manifest
            self.assertEqual((current.revision, current.service_instance, current.resource_epoch, current.recovery_episode),
                             (source.revision, source.service_instance, source.resource_epoch, source.recovery_episode))

    def test_valid_cas_keeps_original_pretty_raw_ref_separate_from_target_ref(self):
        old = _source(1, pretty=True)
        self._write_raw(old.raw_bytes)
        with ResourceStore(self.directory) as store:
            receipt = store.load()
            self.assertEqual(receipt.outcome.manifest.raw_bytes, old.raw_bytes)
            self.assertNotEqual(old.raw_sha256, old.canonical_sha256)
            proposal = self._proposal(store, expected=receipt, source=_source(2, pretty=True))
            ack = self.assert_durable(store.publish(proposal), proposal)
            self.assertEqual(ack.expected_ref, old.ref)
            self.assertNotEqual(ack.expected_ref.raw_sha256, old.canonical_sha256)
            self.assertEqual(self.data.read_bytes(), proposal.target_bytes)

    def test_semantically_equal_but_different_current_raw_cannot_satisfy_original_cas(self):
        old = _source(1, pretty=True)
        self._write_raw(old.raw_bytes)
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            self._write_raw(old.canonical_bytes)
            with patch.object(os, "replace") as replace_call, patch.object(os, "fsync") as fsync_call:
                for entry in (store.publish, store.reconcile):
                    result = entry(proposal)
                    self.assertEqual(result.kind, "CAS_MISMATCH")
                    self.assertIsNone(result.ack)
            replace_call.assert_not_called()
            fsync_call.assert_not_called()
            self.assertEqual(self.data.read_bytes(), old.canonical_bytes)

    def test_current_target_before_first_attempt_is_not_historical_cas_or_ack(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            self._write_raw(proposal.target_bytes)
            result = store.publish(proposal)
            self.assertEqual(result.kind, "CAS_MISMATCH")
            self.assertIsNone(result.ack)
            retry = store.reconcile(proposal)
            self.assertNotEqual(retry.kind, "DURABLE")
            self.assertIsNone(retry.ack)

    def test_partial_write_repeats_remaining_bytes_before_file_and_directory_sync(self):
        real_write = os.write
        real_fsync = os.fsync
        real_replace = os.replace
        writes = []
        order = []

        def short_write(descriptor, body):
            count = max(1, len(body) // 3)
            written = real_write(descriptor, body[:count])
            writes.append(written)
            order.append("write")
            return written

        def sync(descriptor):
            kind = "directory-fsync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file-fsync"
            order.append(kind)
            return real_fsync(descriptor)

        def replace(*args, **kwargs):
            order.append("replace")
            return real_replace(*args, **kwargs)

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store, source=_source(pretty=True))
            with patch.object(os, "write", side_effect=short_write), \
                    patch.object(os, "fsync", side_effect=sync), patch.object(os, "replace", side_effect=replace):
                result = store.publish(proposal)
            self.assert_durable(result, proposal)
            self.assertGreater(len(writes), 1)
            self.assertEqual(sum(writes), len(proposal.target_bytes))
            self.assertEqual(order, ["write"] * len(writes) + ["file-fsync", "replace", "directory-fsync"])
            self.assertEqual(self.data.read_bytes(), proposal.target_bytes)

    def test_zero_progress_write_is_not_complete_and_original_proposal_can_retry(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "write", return_value=0):
                self.assert_unconfirmed(store.publish(proposal))
            self.assertFalse(self.data.exists())
            self.assert_durable(store.reconcile(proposal), proposal)
            self.assertEqual(self.data.read_bytes(), proposal.target_bytes)

    def test_write_error_after_partial_bytes_does_not_publish_incomplete_temp(self):
        real_write = os.write
        calls = []

        def fault(descriptor, body):
            calls.append(len(body))
            if len(calls) == 1:
                return real_write(descriptor, body[:7])
            raise OSError(errno.ENOSPC, "injected partial write failure")

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "write", side_effect=fault):
                self.assert_unconfirmed(store.publish(proposal))
            self.assertEqual(len(calls), 2)
            self.assertFalse(self.data.exists())
            self.assert_durable(store.publish(proposal), proposal)

    def test_file_fsync_failure_before_replace_keeps_expected_and_allows_same_retry(self):
        old = _source(1)
        self._write_raw(old.raw_bytes)
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "fsync", side_effect=OSError(errno.EIO, "injected file fsync failure")), \
                    patch.object(os, "replace") as replace_call:
                self.assert_unconfirmed(store.publish(proposal))
            replace_call.assert_not_called()
            self.assertEqual(self.data.read_bytes(), old.raw_bytes)
            self.assert_durable(store.reconcile(proposal), proposal)

    def test_external_target_after_pre_replace_failure_cannot_be_promoted_by_readback(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "fsync", side_effect=OSError(errno.EIO, "injected before replace")):
                self.assert_unconfirmed(store.publish(proposal))
            self._write_raw(proposal.target_bytes)
            with patch.object(os, "fsync") as fsync_call:
                result = store.reconcile(proposal)
            self.assertNotEqual(result.kind, "DURABLE")
            self.assertIsNone(result.ack)
            fsync_call.assert_not_called()

    def test_replace_failure_with_unchanged_expected_can_retry_without_new_revision(self):
        old = _source(1)
        self._write_raw(old.raw_bytes)
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store, source=_source(2, attempts=5))
            with patch.object(os, "replace", side_effect=OSError(errno.EIO, "injected replace entry failure")):
                self.assert_unconfirmed(store.publish(proposal))
            self.assertEqual(self.data.read_bytes(), old.raw_bytes)
            self.assert_durable(store.reconcile(proposal), proposal)
            self.assertEqual(ResourceManifest(self.data.read_bytes()).recovery_episode, proposal.manifest.recovery_episode)

    def test_replace_effect_then_error_uses_original_attempt_and_reconfirmed_ack(self):
        real_replace = os.replace

        def effect_then_error(*args, **kwargs):
            real_replace(*args, **kwargs)
            raise OSError(errno.EIO, "injected lost replace completion")

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "replace", side_effect=effect_then_error):
                self.assert_unconfirmed(store.publish(proposal))
            self.assertEqual(self.data.read_bytes(), proposal.target_bytes)
            self.assert_durable(store.reconcile(proposal), proposal, method="RECONFIRMED")

    def test_directory_fsync_failure_keeps_visible_target_unconfirmed_then_reflushes(self):
        # 교체된 이름의 target을 다시 열어야 한다. 원 expected의 옛 inode는 재확인 대상이 아니다.
        self._write_raw(_source(1).raw_bytes)
        real_fsync = os.fsync
        seen = []

        def observe(descriptor):
            seen.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
            return real_fsync(descriptor)

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            self._fail_directory_fsync(store, proposal)
            with patch.object(os, "fsync", side_effect=observe), patch.object(os, "replace") as replace_call:
                result = store.reconcile(proposal)
            self.assertEqual(seen, ["file", "directory"])
            replace_call.assert_not_called()
            self.assert_durable(result, proposal, method="RECONFIRMED")

    def test_reconfirmation_fsync_error_never_caches_a_durable_ack(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            self._fail_directory_fsync(store, proposal)
            with patch.object(os, "fsync", side_effect=OSError(errno.EIO, "injected reconfirm failure")):
                self.assert_unconfirmed(store.reconcile(proposal))
            self.assert_durable(store.publish(proposal), proposal, method="RECONFIRMED")

    def test_observed_replace_success_then_original_expected_reappearance_is_not_retryable(self):
        old = _source(1)
        self._write_raw(old.raw_bytes)
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            self._fail_directory_fsync(store, proposal)
            self._write_raw(old.raw_bytes)
            with patch.object(os, "replace") as replace_call:
                result = store.reconcile(proposal)
            self.assertEqual(result.kind, "CAS_MISMATCH")
            self.assertIsNone(result.ack)
            replace_call.assert_not_called()
            self.assertEqual(self.data.read_bytes(), old.raw_bytes)

    def test_other_or_corrupt_current_after_replace_does_not_trigger_rollback(self):
        for body in (_source(9).raw_bytes, b"not a manifest\n"):
            with self.subTest(body=body[:24]), tempfile.TemporaryDirectory(prefix="xnv-store-publish-sub-") as directory:
                previous_directory, previous_data = self.directory, self.data
                self.directory, self.data = directory, Path(directory) / "resource-set.json"
                try:
                    with ResourceStore(directory) as store:
                        proposal = self._proposal(store)
                        self._fail_directory_fsync(store, proposal)
                        self._write_raw(body)
                        result = store.reconcile(proposal)
                        self.assertNotEqual(result.kind, "DURABLE")
                        self.assertIsNone(result.ack)
                        self.assertEqual(self.data.read_bytes(), body)
                finally:
                    self.directory, self.data = previous_directory, previous_data

    def test_unresolved_pending_cannot_be_replaced_even_by_identical_propose_arguments(self):
        with ResourceStore(self.directory) as store:
            receipt = store.load()
            source = _source(pretty=True)
            proposal = self._proposal(store, expected=receipt, source=source)
            variants = ((receipt, source, proposal.request_id),
                        (receipt, source, identifier(1002)),
                        (receipt, ResourceManifest(source.canonical_bytes), proposal.request_id),
                        (store.load(), _source(3), identifier(1003)))
            for expected, replacement, request in variants:
                with self.subTest(request=request), self.assertRaises(StoreUsageError):
                    store.propose(expected, replacement, request)
            self.assert_durable(store.publish(proposal), proposal)

    def test_unknown_pending_blocks_new_request_after_failure_or_cas_mismatch(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "write", side_effect=OSError(errno.ENOSPC, "injected pending failure")):
                self.assert_unconfirmed(store.publish(proposal))
            with self.assertRaises(StoreUsageError):
                self._proposal(store, request=1002, source=_source(3))
            self._write_raw(_source(9).raw_bytes)
            self.assertEqual(store.reconcile(proposal).kind, "CAS_MISMATCH")
            with self.assertRaises(StoreUsageError):
                self._proposal(store, request=1003, source=_source(4))

    def test_only_original_pending_object_is_publishable_not_equivalent_shallow_copy(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            clone = copy(proposal)
            self.assertIsNot(clone, proposal)
            for entry in (store.publish, store.reconcile):
                with self.assertRaises(StoreUsageError):
                    entry(clone)
            self.assert_durable(store.publish(proposal), proposal)

    def test_lost_ack_retry_returns_same_original_ack_not_new_completion(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            original = self.assert_durable(store.publish(proposal), proposal)
            with patch.object(os, "fsync") as fsync_call, patch.object(os, "replace") as replace_call:
                for entry in (store.reconcile, store.publish, store.reconcile):
                    result = entry(proposal)
                    self.assertIs(self.assert_durable(result, proposal), original)
            fsync_call.assert_not_called()
            replace_call.assert_not_called()

    def test_cached_ack_does_not_claim_current_file_still_matches_or_repair_external_change(self):
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            original = self.assert_durable(store.publish(proposal), proposal)
            other = _source(9).raw_bytes
            self._write_raw(other)
            result = store.reconcile(proposal)
            self.assertIs(result.ack, original)
            self.assertEqual(self.data.read_bytes(), other)
            self.assertNotEqual(ResourceManifest(other).ref, result.ack.target_ref)

    def test_completed_proposal_requires_fresh_load_new_request_and_retires_old_cache(self):
        with ResourceStore(self.directory) as store:
            receipt = store.load()
            proposal = self._proposal(store, expected=receipt)
            self.assert_durable(store.publish(proposal), proposal)
            with self.assertRaises(StoreUsageError):
                self._proposal(store, expected=receipt, source=_source(3), request=1002)
            fresh = store.load()
            with self.assertRaises(StoreUsageError):
                store.propose(fresh, _source(3), proposal.request_id)
            next_proposal = self._proposal(store, expected=fresh, source=_source(3), request=1002)
            for entry in (store.publish, store.reconcile):
                with self.assertRaises(StoreUsageError):
                    entry(proposal)
            self.assert_durable(store.publish(next_proposal), next_proposal)

    def test_same_request_different_source_raw_is_not_alias_for_completed_ack(self):
        with ResourceStore(self.directory) as store:
            source = _source(pretty=True)
            proposal = self._proposal(store, source=source)
            self.assert_durable(store.publish(proposal), proposal)
            self._write_raw(_source(9).raw_bytes)
            fresh = store.load()
            alias = ResourceManifest(source.canonical_bytes)
            self.assertEqual(alias.canonical_bytes, source.canonical_bytes)
            self.assertNotEqual(alias.raw_sha256, source.raw_sha256)
            with self.assertRaises(StoreUsageError):
                store.propose(fresh, alias, proposal.request_id)
            replacement = self._proposal(store, expected=fresh, source=alias, request=1002)
            self.assert_durable(store.publish(replacement), replacement)

    def test_noop_exact_raw_target_is_rejected_but_pretty_to_canonical_is_real_change(self):
        canonical = manifest()
        self._write_raw(canonical.canonical_bytes)
        with ResourceStore(self.directory) as store:
            receipt = store.load()
            with self.assertRaises(StoreUsageError):
                self._proposal(store, expected=receipt, source=canonical)
        pretty = _source(1, pretty=True)
        self._write_raw(pretty.raw_bytes)
        with ResourceStore(self.directory) as store:
            receipt = store.load()
            proposal = self._proposal(store, expected=receipt, source=receipt.outcome.manifest)
            self.assert_durable(store.publish(proposal), proposal)
            self.assertEqual(ResourceManifest(self.data.read_bytes()).revision, 1)

    def test_proposal_and_ack_are_immutable_without_mutating_original_manifest(self):
        source = _source(pretty=True)
        source_raw = source.raw_bytes
        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store, source=source)
            exported = source.fields
            exported["revision"] = "999"
            with self.assertRaises(AttributeError):
                proposal.target_bytes = b"forged"
            ack = self.assert_durable(store.publish(proposal), proposal)
            with self.assertRaises(AttributeError):
                ack.target_ref = None
            self.assertEqual(source.raw_bytes, source_raw)
            self.assertEqual(ResourceManifest(self.data.read_bytes()).revision, 2)

    def test_replace_success_does_not_unlink_new_file_reappearing_at_old_temp_name(self):
        real_replace = os.replace
        foreign_name = []
        sentinel = b"new unrelated inode after atomic replace\n"

        def replace_and_recreate(source, target, *args, **kwargs):
            result = real_replace(source, target, *args, **kwargs)
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                                 dir_fd=kwargs.get("src_dir_fd"))
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(sentinel)
            foreign_name.append(Path(source) if os.path.isabs(source) else Path(self.directory) / source)
            return result

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "replace", side_effect=replace_and_recreate):
                self.assert_durable(store.publish(proposal), proposal)
            self.assertEqual(len(foreign_name), 1)
            self.assertEqual(foreign_name[0].read_bytes(), sentinel)

    def test_replace_effect_then_exception_does_not_delete_reappeared_foreign_temp(self):
        real_replace = os.replace
        foreign_name = []
        sentinel = b"foreign inode after ambiguous replace\n"

        def effect_recreate_then_error(source, target, *args, **kwargs):
            real_replace(source, target, *args, **kwargs)
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                                 dir_fd=kwargs.get("src_dir_fd"))
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(sentinel)
            foreign_name.append(Path(source) if os.path.isabs(source) else Path(self.directory) / source)
            raise OSError(errno.EIO, "injected ambiguous replace with name reuse")

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "replace", side_effect=effect_recreate_then_error):
                self.assert_unconfirmed(store.publish(proposal))
            self.assertEqual(len(foreign_name), 1)
            self.assertEqual(foreign_name[0].read_bytes(), sentinel)
            self.assert_durable(store.reconcile(proposal), proposal, method="RECONFIRMED")
            self.assertEqual(foreign_name[0].read_bytes(), sentinel)

    def test_temp_open_denied_without_created_leaf_allows_same_original_proposal_retry(self):
        real_open = os.open
        attempted = []

        def deny_exclusive_temp(path, flags, *args, **kwargs):
            if flags & os.O_CREAT and flags & os.O_EXCL:
                attempted.append(Path(path) if os.path.isabs(path) else Path(self.directory) / path)
                raise PermissionError(errno.EACCES, "injected temp creation denied without side effect")
            return real_open(path, flags, *args, **kwargs)

        with ResourceStore(self.directory) as store:
            proposal = self._proposal(store)
            with patch.object(os, "open", side_effect=deny_exclusive_temp):
                self.assert_unconfirmed(store.publish(proposal))
            self.assertTrue(attempted)
            self.assertTrue(all(not path.exists() for path in attempted))
            self.assertFalse(self.data.exists())
            self.assert_durable(store.publish(proposal), proposal)
            self.assertEqual(self.data.read_bytes(), proposal.target_bytes)

    def test_data_mutation_during_directory_sync_cannot_ack_target_or_overwrite_third_raw(self):
        real_fsync = os.fsync
        # 다른 길이의 유효 원문은 filesystem 시각 양자화와 무관하게 내용 변경을 드러낸다.
        third_raw = _source(9, pretty=True).raw_bytes
        for mode in ("same_inode_content", "replacement_inode"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="xnv-store-sync-race-") as directory:
                data = Path(directory) / "resource-set.json"
                mutations = []

                def mutate_during_directory_sync(descriptor):
                    if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                        if mode == "same_inode_content":
                            with data.open("wb") as stream:
                                stream.write(third_raw)
                        else:
                            alternate = Path(directory) / "third-owned-file.json"
                            temporary_fd = os.open(alternate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(temporary_fd, "wb") as stream:
                                stream.write(third_raw)
                            os.replace(alternate, data)
                        mutations.append(mode)
                    return real_fsync(descriptor)

                with ResourceStore(directory) as store:
                    proposal = self._proposal(store)
                    self.assertNotEqual(len(proposal.target_bytes), len(third_raw))
                    with patch.object(os, "fsync", side_effect=mutate_during_directory_sync):
                        result = store.publish(proposal)
                    self.assertEqual(mutations, [mode])
                    self.assertNotEqual(result.kind, "DURABLE")
                    self.assertIsNone(result.ack)
                    self.assertEqual(data.read_bytes(), third_raw)
                    with patch.object(os, "replace") as replace_call:
                        retry = store.reconcile(proposal)
                    self.assertNotEqual(retry.kind, "DURABLE")
                    self.assertIsNone(retry.ack)
                    replace_call.assert_not_called()
                    self.assertEqual(data.read_bytes(), third_raw)


if __name__ == "__main__":
    unittest.main()
