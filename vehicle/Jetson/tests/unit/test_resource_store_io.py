"""Linux manifest store의 파일 읽기·실제 lock·process/thread 소유권을 검사한다.

모든 파일은 전용 임시 디렉터리에 만든다. publish/CAS/fsync의 단계 검증은 별도
시험이며, 여기의 process·FD 관측은 실제 앱·guard 증거 생산 수락이 아니다.
"""

import copy
import json
import os
from pathlib import Path
import pickle
import select
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from xenova_vehicle.runtime import resource_store as stores
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
from resource_guard_fixtures import encode, identifier, minimal_fields


PROBE = r'''
import json
import os
import sys
from xenova_vehicle.runtime.resource_store import ResourceStore, StoreBusyError, StoreError
if sys.argv[2] == 'strict-umask':
    os.umask(0o777)
try:
    with ResourceStore(sys.argv[1]) as store:
        if sys.argv[2] == 'load':
            receipt = store.load()
            result = {'kind': receipt.outcome.kind, 'manifest': receipt.outcome.manifest is not None}
        else:
            result = {'kind': 'ACQUIRED'}
            if sys.argv[2] == 'strict-umask':
                result['lock_mode'] = os.stat(sys.argv[1] + '/resource-set.lock').st_mode & 0o777
except StoreBusyError:
    result = {'kind': 'BUSY'}
except StoreError as error:
    result = {'kind': 'ERROR', 'error': type(error).__name__}
print(json.dumps(result), flush=True)
'''

HOLDER = r'''
import sys
from xenova_vehicle.runtime.resource_store import ResourceStore
store = ResourceStore(sys.argv[1])
print('READY', flush=True)
sys.stdin.read(1)
store.close()
print('CLOSED', flush=True)
'''


@unittest.skipUnless(sys.platform.startswith('linux'), '실제 Linux FS/flock 시험이며 다른 OS에서 흉내 내지 않는다')
class ResourceStoreIoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xenova-store-io-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'state'
        self.directory.mkdir(mode=0o700)
        self.data = self.directory / 'resource-set.json'
        self.lock = self.directory / 'resource-set.lock'
        self.source = str(Path(__file__).resolve().parents[2] / 'src')

    def put(self, raw, *, path=None, mode=0o600):
        path = self.data if path is None else path
        path.write_bytes(raw)
        path.chmod(mode)
        return path

    def open_store(self):
        store = stores.ResourceStore(str(self.directory))

        def close_owned():
            try:
                store.close()
            except stores.StoreUsageError:
                pass

        self.addCleanup(close_owned)
        return store

    def child(self, operation='probe', *, directory=None):
        env = os.environ.copy()
        env['PYTHONPATH'] = self.source
        result = subprocess.run([sys.executable, '-B', '-S', '-c', PROBE,
                                 str(self.directory if directory is None else directory), operation],
                                capture_output=True, text=True, env=env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def start_holder(self):
        env = os.environ.copy()
        env['PYTHONPATH'] = self.source
        process = subprocess.Popen([sys.executable, '-B', '-S', '-c', HOLDER, str(self.directory)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env)

        def reap():
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            else:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()

        self.addCleanup(reap)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, 'holder did not report lock acquisition')
        self.assertEqual(process.stdout.readline().strip(), 'READY')
        return process

    def test_existing_directory_required_and_non_string_or_relative_input_rejected(self):
        for wrong in (self.directory, None, 1, True, '', 'relative-state'):
            with self.subTest(type_name=type(wrong).__name__):
                with self.assertRaises(stores.StoreUsageError):
                    stores.ResourceStore(wrong)
        missing = self.directory / 'not-created'
        with self.assertRaises(stores.StoreError):
            stores.ResourceStore(str(missing))
        self.assertFalse(missing.exists())

    def test_directory_is_not_subject_to_regular_file_link_count_one(self):
        (self.directory / 'child').mkdir(mode=0o700)
        self.assertGreaterEqual(self.directory.stat().st_nlink, 2)
        store = self.open_store()
        self.assertEqual(store.load().outcome.kind, 'MISSING')

    def test_directory_suffix_or_dot_components_are_rejected_without_normalizing(self):
        alias = self.root / 'directory-alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        candidates = (str(self.directory) + '/', str(self.directory) + '/.',
                      str(self.directory) + '/../state', str(alias) + '/', str(alias) + '/.')
        for directory in candidates:
            with self.subTest(directory=directory):
                with self.assertRaises(stores.StoreUsageError):
                    stores.ResourceStore(directory)
        self.assertTrue(alias.is_symlink())
        self.assertFalse(self.lock.exists())

    def test_directory_allows_read_execute_permissions_but_never_group_or_other_write(self):
        for mode in (0o700, 0o750, 0o755):
            with self.subTest(mode=oct(mode)):
                self.directory.chmod(mode)
                store = stores.ResourceStore(str(self.directory))
                store.close()
        for mode in (0o770, 0o707):
            with self.subTest(mode=oct(mode)):
                self.directory.chmod(mode)
                with self.assertRaises(stores.StoreError):
                    stores.ResourceStore(str(self.directory))
                self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), mode)
        self.directory.chmod(0o700)

    def test_final_directory_symlink_or_regular_file_is_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        regular = self.put(b'file', path=self.root / 'regular')
        for directory in (alias, regular):
            with self.subTest(type_name=directory.name):
                self.assertEqual(self.child(directory=directory)['kind'], 'ERROR')
        self.assertTrue(alias.is_symlink())
        self.assertEqual(regular.read_bytes(), b'file')

    def test_missing_valid_empty_and_corrupt_load_results_remain_distinct(self):
        store = self.open_store()
        missing = store.load()
        self.assertEqual(missing.outcome.kind, 'MISSING')
        self.assertIsNone(missing.outcome.manifest)
        self.assertFalse(self.data.exists())
        raw = encode(minimal_fields())
        self.put(raw)
        valid = store.load()
        self.assertEqual(valid.outcome.kind, 'VALID')
        self.assertEqual(valid.lock_nonce, store.lock_nonce)
        self.assertEqual(valid.outcome.manifest.raw_bytes, raw)
        for malformed in (b'', b'not-json'):
            with self.subTest(length=len(malformed)):
                self.put(malformed)
                result = store.load()
                self.assertEqual(result.outcome.kind, 'CORRUPT')
                self.assertIsNone(result.outcome.manifest)
                self.assertEqual(self.data.read_bytes(), malformed)

    def test_load_receipt_is_immutable_and_keeps_original_noncanonical_raw(self):
        fields = minimal_fields()
        raw = json.dumps(fields, ensure_ascii=True, indent=1).encode('ascii')
        self.put(raw)
        store = self.open_store()
        receipt = store.load()
        self.assertEqual(receipt.outcome.kind, 'VALID')
        manifest = receipt.outcome.manifest
        self.assertEqual(manifest.raw_bytes, raw)
        self.assertNotEqual(manifest.ref.raw_sha256, manifest.canonical_sha256)
        with self.assertRaises(AttributeError):
            receipt.lock_nonce = 'changed'
        with self.assertRaises(AttributeError):
            receipt.outcome = None

    def test_load_8192_is_valid_and_8193_is_corrupt_without_rewrite(self):
        canonical = encode(minimal_fields())
        raw = canonical[:-2] + b' ' * (8192 - len(canonical)) + b'}\n'
        self.put(raw)
        store = self.open_store()
        result = store.load()
        self.assertEqual(result.outcome.kind, 'VALID')
        self.assertEqual(result.outcome.manifest.raw_bytes, raw)
        too_large = raw[:-2] + b' }\n'
        self.put(too_large)
        self.assertEqual(store.load().outcome.kind, 'CORRUPT')
        self.assertEqual(self.data.read_bytes(), too_large)

    def test_partial_reads_are_accumulated_until_real_eof(self):
        raw = encode(minimal_fields())
        self.put(raw)
        store = self.open_store()
        actual_read = os.read
        sizes = []

        def fragment(fd, maximum):
            result = actual_read(fd, min(maximum, 7))
            sizes.append(len(result))
            return result

        with patch('os.read', side_effect=fragment):
            receipt = store.load()
        self.assertGreater(len(sizes), 1)
        self.assertEqual(receipt.outcome.kind, 'VALID')
        self.assertEqual(receipt.outcome.manifest.raw_bytes, raw)

    def test_overlarge_file_never_requests_or_accumulates_more_than_8193_bytes(self):
        self.put(b'x' * 16384)
        store = self.open_store()
        actual_read = os.read
        consumed = 0

        def bounded(fd, maximum):
            nonlocal consumed
            self.assertGreater(maximum, 0)
            self.assertLessEqual(maximum, 8193 - consumed)
            data = actual_read(fd, maximum)
            consumed += len(data)
            return data

        with patch('os.read', side_effect=bounded):
            receipt = store.load()
        self.assertEqual(receipt.outcome.kind, 'CORRUPT')
        self.assertLessEqual(consumed, 8193)
        self.assertEqual(self.data.stat().st_size, 16384)

    def test_read_failure_is_not_missing_or_corrupt_and_does_not_change_source(self):
        raw = encode(minimal_fields())
        self.put(raw)
        store = self.open_store()
        with patch('os.read', side_effect=OSError('synthetic read failure')):
            receipt = store.load()
        self.assertEqual(receipt.outcome.kind, 'IO_ERROR')
        self.assertIsNone(receipt.outcome.manifest)
        self.assertEqual(self.data.read_bytes(), raw)

    def test_file_truncated_during_read_is_not_a_valid_mixed_snapshot(self):
        self.put(encode(minimal_fields()))
        store = self.open_store()
        actual_read = os.read
        first = True

        def truncate_after_fragment(fd, maximum):
            nonlocal first
            data = actual_read(fd, min(maximum, 7))
            if first:
                first = False
                self.data.write_bytes(b'')
            return data

        with patch('os.read', side_effect=truncate_after_fragment):
            receipt = store.load()
        self.assertEqual(receipt.outcome.kind, 'IO_ERROR')
        self.assertIsNone(receipt.outcome.manifest)

    def test_file_entry_replaced_while_open_does_not_become_valid_old_fd_receipt(self):
        raw = encode(minimal_fields())
        self.put(raw)
        store = self.open_store()
        replacement = self.put(raw, path=self.directory / 'replacement.json')
        actual_read = os.read
        first = True

        def replace_after_fragment(fd, maximum):
            nonlocal first
            result = actual_read(fd, min(maximum, 7))
            if first:
                first = False
                os.replace(replacement, self.data)
            return result

        with patch('os.read', side_effect=replace_after_fragment):
            receipt = store.load()
        self.assertEqual(receipt.outcome.kind, 'IO_ERROR')

    def test_data_symlink_hardlink_and_bad_mode_are_never_missing_or_repaired(self):
        target = self.put(encode(minimal_fields()), path=self.root / 'outside.json')
        self.data.symlink_to(target)
        self.assertIn(self.child('load')['kind'], ('ERROR', 'IO_ERROR'))
        self.assertTrue(self.data.is_symlink())
        self.data.unlink()
        os.link(target, self.data)
        self.assertIn(self.child('load')['kind'], ('ERROR', 'IO_ERROR'))
        self.assertEqual(target.stat().st_nlink, 2)
        self.data.unlink()
        self.put(encode(minimal_fields()), mode=0o640)
        self.assertIn(self.child('load')['kind'], ('ERROR', 'IO_ERROR'))
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o640)
        self.assertEqual(target.read_bytes(), encode(minimal_fields()))

    def test_special_data_nodes_are_rejected_in_a_bounded_subprocess(self):
        os.mkfifo(self.data, mode=0o600)
        self.assertIn(self.child('load')['kind'], ('ERROR', 'IO_ERROR'))
        self.assertTrue(stat.S_ISFIFO(self.data.lstat().st_mode))
        self.data.unlink()
        self.data.mkdir(mode=0o700)
        self.assertIn(self.child('load')['kind'], ('ERROR', 'IO_ERROR'))
        self.assertTrue(self.data.is_dir())

    def test_lock_symlink_hardlink_fifo_directory_and_wrong_mode_are_not_repaired(self):
        target = self.put(b'keep', path=self.root / 'lock-target')
        for kind in ('symlink', 'hardlink', 'fifo', 'directory', 'mode'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    self.lock.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(target, self.lock)
                elif kind == 'fifo':
                    os.mkfifo(self.lock, mode=0o600)
                elif kind == 'directory':
                    self.lock.mkdir(mode=0o700)
                else:
                    self.put(b'keep', path=self.lock, mode=0o644)
                before = self.lock.lstat()
                self.assertEqual(self.child()['kind'], 'ERROR')
                after = self.lock.lstat()
                self.assertEqual((after.st_dev, after.st_ino, after.st_mode),
                                 (before.st_dev, before.st_ino, before.st_mode))
                if kind == 'directory':
                    self.lock.rmdir()
                else:
                    self.lock.unlink()
        self.assertEqual(target.read_bytes(), b'keep')

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0,
                         '다른 UID의 실제 파일 소유권 시험에는 root 권한이 필요하다')
    def test_foreign_uid_directory_lock_and_data_are_rejected_without_chown_repair(self):
        self.put(b'lock', path=self.lock)
        self.put(encode(minimal_fields()))
        for path, operation in ((self.directory, 'probe'), (self.lock, 'probe'), (self.data, 'load')):
            with self.subTest(path=path.name):
                os.chown(path, 1, -1)
                try:
                    self.assertIn(self.child(operation)['kind'], ('ERROR', 'IO_ERROR'))
                    self.assertEqual(path.stat().st_uid, 1)
                finally:
                    os.chown(path, 0, -1)

    def test_lock_inode_and_contents_survive_load_close_and_reacquire(self):
        self.put(b'original-lock', path=self.lock)
        original = self.lock.stat()
        store = self.open_store()
        first_nonce = store.lock_nonce
        self.assertEqual(self.child()['kind'], 'BUSY')
        store.load()
        store.close()
        self.assertEqual(self.lock.read_bytes(), b'original-lock')
        self.assertEqual(self.lock.stat().st_ino, original.st_ino)
        reopened = self.open_store()
        self.assertNotEqual(reopened.lock_nonce, first_nonce)
        self.assertEqual(self.lock.stat().st_ino, original.st_ino)
        with self.assertRaises(stores.StoreUsageError):
            store.load()

    def test_own_new_lock_mode_is_exact_even_under_restrictive_child_umask(self):
        result = self.child('strict-umask')
        self.assertEqual(result['kind'], 'ACQUIRED')
        self.assertEqual(result['lock_mode'], 0o600)
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)

    def test_replaced_lock_entry_blocks_existing_store_without_deleting_replacement(self):
        store = self.open_store()
        old = self.directory / 'original-lock'
        os.rename(self.lock, old)
        self.put(b'replacement', path=self.lock)
        replacement_identity = (self.lock.stat().st_dev, self.lock.stat().st_ino)
        try:
            with self.assertRaises(stores.StoreError):
                store.load()
            self.assertEqual((self.lock.stat().st_dev, self.lock.stat().st_ino), replacement_identity)
            self.assertEqual(self.lock.read_bytes(), b'replacement')
        finally:
            self.lock.unlink()
            os.rename(old, self.lock)

    def test_independent_writer_stays_busy_across_manifest_inode_replacement(self):
        self.put(encode(minimal_fields()))
        store = self.open_store()
        lock_inode = self.lock.stat().st_ino
        before = self.data.stat().st_ino
        replacement = self.put(encode(minimal_fields()), path=self.directory / 'new-manifest')
        os.replace(replacement, self.data)
        self.assertNotEqual(self.data.stat().st_ino, before)
        self.assertEqual(store.load().outcome.kind, 'VALID')
        self.assertEqual(self.child()['kind'], 'BUSY')
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        store.close()
        self.assertEqual(self.child()['kind'], 'ACQUIRED')
        self.assertEqual(self.lock.stat().st_ino, lock_inode)

    def test_actual_owner_process_death_releases_fd_lock_without_unlink(self):
        holder = self.start_holder()
        inode = self.lock.stat().st_ino
        self.assertEqual(self.child()['kind'], 'BUSY')
        holder.kill()
        holder.communicate(timeout=5)
        self.assertIsNotNone(holder.returncode)
        self.assertEqual(self.child()['kind'], 'ACQUIRED')
        self.assertEqual(self.lock.stat().st_ino, inode)

    def test_busy_constructor_attempts_do_not_leak_local_file_descriptors(self):
        self.open_store()
        before = set(os.listdir('/proc/self/fd'))
        for _ in range(3):
            with self.assertRaises(stores.StoreBusyError):
                stores.ResourceStore(str(self.directory))
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_foreign_thread_cannot_load_propose_publish_reconcile_or_unlock(self):
        original = encode(minimal_fields())
        self.put(original)
        store = self.open_store()
        receipt = store.load()
        fields = minimal_fields()
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        preparation = []

        def foreign_prepare():
            try:
                store.propose(receipt, target, identifier(800))
            except stores.StoreUsageError:
                preparation.append('rejected')
            except BaseException as error:
                preparation.append(type(error).__name__)
            else:
                preparation.append('accepted')

        first_thread = threading.Thread(target=foreign_prepare, name='foreign-empty-proposal-owner')
        first_thread.start()
        first_thread.join(timeout=5)
        self.assertFalse(first_thread.is_alive(), 'foreign proposal entry did not return')
        self.assertEqual(preparation, ['rejected'])
        # 빈 pending에서도 먼저 거절해야 하며 기존 pending 충돌에 가려져서는 안 된다.
        proposal = store.propose(receipt, target, identifier(800))
        observed = []

        def foreign_calls():
            actions = (('load', store.load), ('propose', lambda: store.propose(receipt, target, identifier(801))),
                       ('publish', lambda: store.publish(proposal)),
                       ('reconcile', lambda: store.reconcile(proposal)), ('close', store.close))
            for name, action in actions:
                try:
                    action()
                except stores.StoreUsageError:
                    observed.append((name, 'rejected'))
                except BaseException as error:
                    observed.append((name, type(error).__name__))
                else:
                    observed.append((name, 'accepted'))

        thread = threading.Thread(target=foreign_calls, name='foreign-store-owner')
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), 'foreign-thread store entry did not return')
        self.assertEqual(observed, [(name, 'rejected') for name in ('load', 'propose', 'publish', 'reconcile', 'close')])
        self.assertEqual(self.data.read_bytes(), original)
        self.assertEqual(self.child()['kind'], 'BUSY')

    def test_same_thread_recursive_operation_is_rejected_without_losing_outer_load(self):
        raw = encode(minimal_fields())
        self.put(raw)
        store = self.open_store()
        actual_read = os.read
        attempted = False
        rejected = []

        def reenter_once(fd, maximum):
            nonlocal attempted
            if not attempted:
                attempted = True
                try:
                    store.load()
                except stores.StoreUsageError:
                    rejected.append(True)
                else:
                    rejected.append(False)
            return actual_read(fd, maximum)

        with patch('os.read', side_effect=reenter_once):
            receipt = store.load()
        self.assertEqual(rejected, [True])
        self.assertEqual(receipt.outcome.kind, 'VALID')
        self.assertEqual(receipt.outcome.manifest.raw_bytes, raw)

    def test_copy_deepcopy_and_pickle_reject_transfer_without_releasing_original_lock(self):
        store = self.open_store()
        nonce = store.lock_nonce
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(stores.StoreUsageError):
                    operation(store)
                self.assertEqual(store.load().outcome.kind, 'MISSING')
                self.assertEqual(store.lock_nonce, nonce)
                self.assertEqual(self.child()['kind'], 'BUSY')

    def test_context_enter_reentry_during_namespace_check_does_not_enter_twice(self):
        store = self.open_store()
        actual_fstat = os.fstat
        attempted = False
        rejected = []

        def reenter_once(descriptor):
            nonlocal attempted
            if not attempted:
                attempted = True
                try:
                    store.__enter__()
                except stores.StoreUsageError:
                    rejected.append(True)
                else:
                    rejected.append(False)
            return actual_fstat(descriptor)

        with patch('os.fstat', side_effect=reenter_once):
            entered = store.__enter__()
        self.assertIs(entered, store)
        self.assertEqual(rejected, [True])
        self.assertEqual(store.load().outcome.kind, 'MISSING')
        self.assertEqual(self.child()['kind'], 'BUSY')
        store.__exit__(None, None, None)
        self.assertEqual(self.child()['kind'], 'ACQUIRED')

    def test_foreign_context_exit_preserves_owner_entered_state_and_lock(self):
        store = self.open_store()
        self.assertIs(store.__enter__(), store)
        observed = []

        def foreign_exit():
            try:
                store.__exit__(None, None, None)
            except stores.StoreUsageError:
                observed.append('rejected')
            except BaseException as error:
                observed.append(type(error).__name__)
            else:
                observed.append('accepted')

        thread = threading.Thread(target=foreign_exit, name='foreign-context-exit')
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), 'foreign context exit did not return')
        self.assertEqual(observed, ['rejected'])
        with self.assertRaises(stores.StoreUsageError):
            store.__enter__()
        self.assertEqual(store.load().outcome.kind, 'MISSING')
        self.assertEqual(self.child()['kind'], 'BUSY')
        store.__exit__(None, None, None)
        self.assertEqual(self.child()['kind'], 'ACQUIRED')

    def test_close_reentry_is_rejected_and_each_held_descriptor_is_closed_once(self):
        store = self.open_store()
        actual_close = os.close
        attempted = False
        rejected = []
        closed_descriptors = []

        def reenter_once(descriptor):
            nonlocal attempted
            if not attempted:
                attempted = True
                try:
                    store.close()
                except stores.StoreUsageError:
                    rejected.append(True)
                else:
                    rejected.append(False)
            closed_descriptors.append(descriptor)
            return actual_close(descriptor)

        with patch('os.close', side_effect=reenter_once):
            store.close()
        self.assertEqual(rejected, [True])
        self.assertTrue(closed_descriptors)
        self.assertEqual(len(closed_descriptors), len(set(closed_descriptors)))
        with self.assertRaises(stores.StoreUsageError):
            store.load()
        self.assertEqual(self.child()['kind'], 'ACQUIRED')

    @unittest.skipUnless(hasattr(os, 'fork'), '실제 fork 상속 경계는 Linux fork가 필요하다')
    def test_fork_child_bad_entry_and_close_do_not_unlock_parent_description(self):
        store = self.open_store()
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            result = []
            for name, action in (('load', store.load), ('close', store.close)):
                try:
                    action()
                except stores.StoreUsageError:
                    result.append((name, 'rejected'))
                except BaseException as error:
                    result.append((name, type(error).__name__))
                else:
                    result.append((name, 'accepted'))
            os.write(write_fd, json.dumps(result).encode('ascii'))
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child, 9)
                self.fail('fork child store misuse did not return')
            result = json.loads(os.read(read_fd, 4096))
        finally:
            os.close(read_fd)
            _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(result, [['load', 'rejected'], ['close', 'rejected']])
        self.assertEqual(self.child()['kind'], 'BUSY')
        store.close()
        self.assertEqual(self.child()['kind'], 'ACQUIRED')


@unittest.skipIf(sys.platform.startswith('linux'), 'Linux에서 미지원 platform 입구 시험은 해당하지 않는다')
class ResourceStoreUnsupportedPlatformTests(unittest.TestCase):
    def test_native_store_reports_unsupported_platform_without_fake_fcntl(self):
        with self.assertRaises(stores.StoreError):
            stores.ResourceStore(str(Path(__file__).resolve().parent))


if __name__ == '__main__':
    unittest.main()
