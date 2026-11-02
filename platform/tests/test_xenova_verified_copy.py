"""실제 파일의 검증 결합 복사와 Manager 준비 경계. 장치·chroot·Xen만 대체한다."""
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import unittest
from unittest.mock import patch

import prepare_instance as preparation
from test_xenova_spring_followup import FollowupFixture
import xenova_catalog as catalog
import xenova_vm as vm


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VerifiedCopyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root / 'source', self.root / 'copy'
        self.source.write_bytes(b'nonzero template data' * 100)
        self.expected = sha(self.source)

    def copy(self, **kwargs):
        return preparation._copy_verified(self.source, self.target, self.expected, **kwargs)

    def test_dense_copy_is_private_independent_and_has_the_expected_bytes(self):
        identity = self.copy(expected_bytes=self.source.stat().st_size)
        self.assertEqual(sha(self.target), self.expected)
        self.assertNotEqual(self.source.stat().st_ino, self.target.stat().st_ino)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        preparation._unchanged(self.source, identity)

    def test_sparse_holes_and_trailing_hole_keep_logical_bytes_and_size(self):
        with self.source.open('wb') as stream:
            stream.write(b'begin')
            stream.seek(3 * 1024 ** 2)
            stream.write(b'middle')
            stream.truncate(8 * 1024 ** 2)
            stream.flush()
            os.fsync(stream.fileno())
        self.expected = sha(self.source)
        original_read = os.read
        read_bytes = []

        def read(fd, length):
            data = original_read(fd, length)
            read_bytes.append(len(data))
            return data

        with patch.object(preparation.os, 'read', side_effect=read):
            self.copy()
        self.assertEqual(self.target.stat().st_size, self.source.stat().st_size)
        self.assertEqual(sha(self.target), self.expected)
        self.assertLess(self.target.stat().st_blocks * 512, self.target.stat().st_size)
        with self.source.open('rb') as stream:
            holes = any(not data for _, _, data in preparation._ranges(stream.fileno(), self.source.stat().st_size))
        if holes:
            self.assertLess(sum(read_bytes), self.source.stat().st_size)

    def test_all_hole_file_is_hashed_as_zero_bytes(self):
        with self.source.open('wb') as stream:
            stream.truncate(3 * 1024 ** 2 + 17)
        self.expected = sha(self.source)
        self.copy()
        self.assertEqual(self.target.stat().st_size, self.source.stat().st_size)
        self.assertEqual(sha(self.target), self.expected)
        self.assertLess(self.target.stat().st_blocks * 512, self.target.stat().st_size)

    def test_unsupported_extent_queries_use_a_verified_stream(self):
        original_seek = os.lseek

        def seek(fd, offset, kind):
            if kind in (os.SEEK_DATA, os.SEEK_HOLE):
                raise OSError(errno.EINVAL, 'fixture: no extent support')
            return original_seek(fd, offset, kind)

        with patch.object(preparation.os, 'lseek', side_effect=seek):
            self.copy()
        self.assertEqual(sha(self.target), self.expected)

    def test_short_reads_and_short_writes_are_completed(self):
        original_read, original_write = os.read, os.write
        with patch.object(preparation.os, 'read', side_effect=lambda fd, n: original_read(fd, min(n, 13))), \
                patch.object(preparation.os, 'write', side_effect=lambda fd, data: original_write(fd, data[:7])):
            self.copy()
        self.assertEqual(sha(self.target), self.expected)

    def test_zero_write_is_an_error_and_partial_output_is_retained(self):
        with patch.object(preparation.os, 'write', return_value=0), self.assertRaises(OSError):
            self.copy()
        self.assertTrue(self.target.exists())
        self.assertEqual(sha(self.source), self.expected)

    def test_premature_eof_does_not_confirm_a_copy(self):
        with patch.object(preparation.os, 'read', return_value=b''), self.assertRaisesRegex(ValueError, 'ended during copy'):
            self.copy()
        self.assertTrue(self.target.exists())

    def test_read_write_and_fsync_errors_preserve_the_source_and_partial_copy(self):
        for function, code in (('read', errno.EIO), ('write', errno.ENOSPC), ('fsync', errno.EIO)):
            with self.subTest(function=function):
                self.target = self.root / ('copy-' + function)
                with patch.object(preparation.os, function, side_effect=OSError(code, 'injected')), self.assertRaises(OSError):
                    self.copy()
                self.assertTrue(self.target.exists())
                self.assertEqual(sha(self.source), self.expected)

    def test_bad_digest_retains_the_unpublished_copy(self):
        self.expected = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.copy()
        self.assertTrue(self.target.exists())

    def test_wrong_size_is_rejected_before_output_allocation(self):
        with self.assertRaisesRegex(ValueError, 'byte size mismatch'):
            self.copy(expected_bytes=self.source.stat().st_size + 1)
        self.assertFalse(self.target.exists())

    def test_existing_output_and_output_symlink_are_never_overwritten(self):
        self.target.write_bytes(b'keep existing')
        with self.assertRaises(FileExistsError):
            self.copy()
        self.assertEqual(self.target.read_bytes(), b'keep existing')
        outside = self.root / 'outside'
        outside.write_bytes(b'keep outside')
        self.target = self.root / 'link'
        self.target.symlink_to(outside)
        with self.assertRaises(FileExistsError):
            self.copy()
        self.assertEqual(outside.read_bytes(), b'keep outside')

    def test_source_symlink_hardlink_and_fifo_are_rejected(self):
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                path = self.root / kind
                if kind == 'symlink': path.symlink_to(self.source)
                elif kind == 'hardlink': os.link(self.source, path)
                else: os.mkfifo(path)
                with self.assertRaises((ValueError, OSError)):
                    preparation._copy_verified(path, self.root / ('out-' + kind), self.expected)

    def test_same_inode_rewrite_even_with_restored_mtime_is_rejected(self):
        original_read = os.read
        before = self.source.stat()
        changed = False

        def read(fd, length):
            nonlocal changed
            data = original_read(fd, length)
            if not changed:
                changed = True
                with self.source.open('r+b') as stream:
                    stream.write(b'X')
                os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
            return data

        with patch.object(preparation.os, 'read', side_effect=read), self.assertRaisesRegex(ValueError, 'template input changed'):
            self.copy()
        self.assertTrue(self.target.exists())

    def test_source_replacement_during_copy_is_rejected(self):
        original_read = os.read
        changed = False

        def read(fd, length):
            nonlocal changed
            data = original_read(fd, length)
            if not changed:
                changed = True
                replacement = self.root / 'replacement'
                replacement.write_bytes(self.source.read_bytes())
                os.replace(replacement, self.source)
            return data

        with patch.object(preparation.os, 'read', side_effect=read), self.assertRaises(ValueError):
            self.copy()

    def test_nonadvancing_extent_is_rejected(self):
        original_seek = os.lseek

        def seek(fd, offset, kind):
            return 0 if kind in (os.SEEK_DATA, os.SEEK_HOLE) else original_seek(fd, offset, kind)

        with patch.object(preparation.os, 'lseek', side_effect=seek), self.assertRaisesRegex(ValueError, 'did not advance'):
            self.copy()


class PreparationCopyBoundaryTest(FollowupFixture):
    def direct_preparation(self, *, seed=False, legacy=False):
        from argparse import Namespace
        from first_install_fixture import FILES
        import test_xenova_management as fixtures
        source = self.f.data / 'templates/source'
        if legacy:
            manifest = source / 'template.json'
            meta = json.loads(manifest.read_text())
            meta.pop('files')
            manifest.chmod(0o644)
            manifest.write_text(json.dumps(meta))
            manifest.chmod(0o444)
        record = fixtures.record()
        record.update(template='source', template_id=self.spec['template_id'], template_version='1.0.0')
        if seed:
            (self.f.data / 'seeds').mkdir()
        args = Namespace(data_root=self.f.data, id=record['id'], deployment_seed=seed, spec=None)
        work = self.f.data / 'instances/.direct-preparation'
        with self.linux_boundaries(), patch.object(preparation, 'seed_store_check'), \
                patch.object(preparation, 'prepare_deployment_seed') as seed_config:
            preparation.make_instance(args, vm=vm, defaults_path=FILES / 'defaults.json',
                record_override=record, workspace_path=work, template_directory=source)
        target = self.f.data / ('seeds' if seed else 'instances') / record['id']
        for name in ('root.raw', 'Image', 'initrd', 'boot.json', 'kernel.config'):
            self.assertEqual(sha(target / name), sha(source / name))
        self.assertNotEqual((target / 'root.raw').stat().st_ino, (source / 'root.raw').stat().st_ino)
        return json.loads((target / 'instance.json').read_text()), seed_config.call_count

    def test_factory_seed_keeps_pending_identity_and_uses_the_seed_adapter(self):
        record, calls = self.direct_preparation(seed=True)
        self.assertEqual(calls, 1)
        self.assertEqual(record['identity_state'], 'pending')
        self.assertEqual(record['uuid'], '00000000-0000-4000-8000-000000000000')

    def test_direct_legacy_manifest_without_files_keeps_root_and_boot_checks(self):
        record, calls = self.direct_preparation(legacy=True)
        self.assertEqual(calls, 0)
        self.assertEqual(record['template_version'], '1.0.0')
        self.assertNotEqual(record['uuid'], '00000000-0000-4000-8000-000000000000')

    def test_real_create_uses_only_one_verified_copy_per_payload(self):
        source = self.f.data / 'templates/source'
        before = {p.name: sha(p) for p in source.iterdir()}
        with self.linux_boundaries() as commands, \
                patch.object(catalog, 'sha', wraps=catalog.sha) as hashes, \
                patch.object(preparation, '_copy_verified', wraps=preparation._copy_verified) as copies, \
                patch.object(preparation, 'digest', side_effect=AssertionError('separate digest pass forbidden')):
            result = self.m.call('create', self.spec, execute=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(sum(Path(call.args[0]).name == 'root.raw' for call in hashes.call_args_list), 0)
        self.assertEqual([Path(call.args[0]).name for call in copies.call_args_list],
            ['root.raw', 'Image', 'initrd', 'boot.json', 'kernel.config'])
        self.assertFalse(any(c[0] == 'cp' for c in commands))
        target = self.f.data / 'instances' / self.f.key
        self.assertNotEqual((target / 'root.raw').stat().st_ino, (source / 'root.raw').stat().st_ino)
        for name in ('root.raw', 'Image', 'initrd', 'boot.json', 'kernel.config'):
            self.assertEqual(sha(target / name), before[name])
        self.assertEqual({p.name: sha(p) for p in source.iterdir()}, before)

    def test_manifest_expectation_is_not_overwritten_in_direct_preparation(self):
        from argparse import Namespace
        from first_install_fixture import FILES
        record = dict(self.f.spec, id='custom', name='custom', hostname='custom', template='source',
                      uuid=self.spec['vm_id'], mac='02:00:00:00:00:99', ipv4='192.168.77.31',
                      template_manifest_sha256='0' * 64)
        work = self.f.data / 'instances/.expected-manifest'
        args = Namespace(data_root=self.f.data, id='custom', deployment_seed=False, spec=None)
        with self.linux_boundaries(), self.assertRaisesRegex(ValueError, 'selected template manifest'):
            preparation.make_instance(args, vm=vm,
                defaults_path=FILES / 'defaults.json', record_override=record,
                workspace_path=work, template_directory=self.f.data / 'templates/source')
        self.assertFalse(work.exists())

    def test_copy_failure_preserves_original_operation_and_never_publishes(self):
        with self.linux_boundaries(), patch.object(preparation.os, 'write', side_effect=OSError(errno.ENOSPC, 'injected')):
            result = self.m.call('create', self.spec, execute=True)
        self.error(result, 'RECOVERY_REQUIRED')
        operations = self.m._operations()
        self.assertEqual(len(operations), 1)
        original = operations[0]
        stage = self.f.data / original['owned_workspace'] / self.f.key
        self.assertTrue((stage / 'root.raw').exists())
        self.assertFalse((self.f.data / 'instances' / self.f.key).exists())
        retry = self.m.call('create', self.spec, execute=True)
        self.error(retry, 'RECOVERY_REQUIRED')
        self.assertEqual(retry['operation_id'], original['operation_id'])
        self.assertTrue(retry['replayed'])
        self.assertTrue((stage / 'root.raw').exists())

    def test_source_changed_during_guest_configuration_is_not_published(self):
        source = self.f.data / 'templates/source/root.raw'

        def configure(*args, **kwargs):
            source.chmod(0o644)
            with source.open('r+b') as stream: stream.write(b'X')
            source.chmod(0o444)

        with self.linux_boundaries(), patch.object(preparation, 'prepare_instance', side_effect=configure):
            result = self.m.call('create', self.spec, execute=True)
        self.error(result, 'RECOVERY_REQUIRED')
        self.assertFalse((self.f.data / 'instances' / self.f.key).exists())
        original = self.m._operations()[0]
        self.assertTrue((self.f.data / original['owned_workspace'] / self.f.key / 'root.raw').is_file())

    def test_payload_changed_after_admission_is_rejected_before_guest_configuration(self):
        source = self.f.data / 'templates/source/root.raw'
        prepare = self.m._prepare

        def replace(op):
            source.chmod(0o644)
            with source.open('r+b') as stream: stream.write(b'X')
            source.chmod(0o444)
            prepare(op)

        with self.linux_boundaries(), patch.object(preparation, 'prepare_instance') as configure:
            self.m.preparer = replace
            result = self.m.call('create', self.spec, execute=True)
        self.error(result, 'RECOVERY_REQUIRED')
        configure.assert_not_called()
        self.assertFalse((self.f.data / 'instances' / self.f.key).exists())
        original = self.m._operations()[0]
        self.assertTrue((self.f.data / original['owned_workspace'] / self.f.key / 'root.raw').is_file())

    def test_copy_process_kill_retains_partial_workspace_and_same_operation(self):
        pid = os.fork()
        if pid == 0:
            original_write = os.write

            def write(fd, data):
                count = original_write(fd, data)
                os.kill(os.getpid(), signal.SIGKILL)
                return count

            try:
                with self.linux_boundaries(), patch.object(preparation.os, 'write', side_effect=write):
                    self.m.call('create', self.spec, execute=True)
            finally:
                os._exit(9)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        operations = self.m._operations()
        self.assertEqual(len(operations), 1)
        original = operations[0]
        path = self.f.data / original['owned_workspace'] / self.f.key / 'root.raw'
        self.assertTrue(path.is_file())
        before = (path.stat().st_ino, path.read_bytes())
        fresh = self.m.__class__(self.p, defaults_path=self.m.defaults_path)
        retry = fresh.call('create', self.spec, execute=True)
        self.error(retry, 'RECOVERY_REQUIRED')
        self.assertEqual(retry['operation_id'], original['operation_id'])
        self.assertEqual((path.stat().st_ino, path.read_bytes()), before)
        self.assertEqual(len(self.m._operations()), 1)


if __name__ == '__main__':
    unittest.main()
