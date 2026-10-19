"""완료 산출물의 고립·잠금 수명·읽기 전용 계획 회귀. 수백 바이트 일반 파일만 사용한다."""
import fcntl
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import assemble, contract, mbr
import artifact_io
import completed_artifacts as releases


class CompletedArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.build = self.base / 'build'
        self.deploy = self.build / 'tmp/deploy/images/raspberrypi4-64'
        self.deploy.mkdir(parents=True)
        self.image = self.deploy / 'platform-20261016.wic'
        self.image.write_bytes(b'non-OS fixture, not a disk')
        self.record = self.base / 'build.json'
        self.body = {'source_commit': 'a' * 40, 'source_worktree_clean': False, 'build_exit_code': 0,
                     'finished_at': '2026-10-16T00:00:00Z', 'build_directory': str(self.build),
                     'wic': {'path': str(self.image), 'bytes': self.image.stat().st_size, 'sha256': artifact_io.sha(self.image)}}
        self.save_record()
        self.output = self.base / 'completed-release'

    def save_record(self):
        self.record.write_text(json.dumps(self.body))
        self.digest = artifact_io.sha(self.record)

    def execute(self):
        result = releases.publish_completed(self.record, self.digest, 'wic', self.output)
        # 0555 공개 디렉터리도 fixture 소유자가 테스트 종료 시 정리할 수 있도록 한다.
        self.addCleanup(lambda: self.output.chmod(0o755) if self.output.exists() else None)
        return {'platform': result['platform']}

    def snapshot(self):
        return {str(p.relative_to(self.base)): (p.lstat().st_mode, p.read_bytes() if p.is_file() and not p.is_symlink() else str(p.readlink()) if p.is_symlink() else None)
                for p in self.base.rglob('*')}

    def test_publication_plan_never_creates_files_locks_or_reads_image_bytes(self):
        before = self.snapshot()
        original = artifact_io.sha
        with patch('artifact_io.sha', side_effect=lambda p: self.fail('plan hashed live image') if Path(p) == self.image else original(p)), patch.object(releases, 'publication_lock', side_effect=AssertionError('plan locked')):
            result = releases.publication_plan(self.record, self.digest, 'wic', self.output)
        self.assertFalse(result['source_image_verified'])
        self.assertEqual(self.snapshot(), before)

    def test_normal_bitbake_shutdown_without_lock_publishes_and_consumes(self):
        self.assertFalse((self.build / 'bitbake.lock').exists())
        config = self.execute()
        self.assertFalse((self.build / 'bitbake.lock').exists())
        with assemble.completed_input(config) as (image, record):
            self.assertEqual(image.read_bytes(), self.image.read_bytes())
            self.assertNotEqual(image.stat().st_ino, self.image.stat().st_ino)
            self.assertEqual(record, self.body)
            self.assertEqual(image.stat().st_mode & 0o222, 0)

    def test_running_build_blocks_publication_without_output(self):
        lock = self.build / 'bitbake.lock'
        lock.write_text('existing BitBake PID\n')
        with lock.open('rb') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'still running'):
                self.execute()
        self.assertFalse(self.output.exists())
        self.assertEqual(lock.read_text(), 'existing BitBake PID\n')

    def test_new_bitbake_cannot_start_while_copy_is_being_published(self):
        original = releases.publish
        def concurrent(stage, output):
            with (self.build / 'bitbake.lock').open('a+') as fd:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return original(stage, output)
        with patch.object(releases, 'publish', side_effect=concurrent):
            self.execute()

    def test_idle_preexisting_lock_is_preserved(self):
        lock = self.build / 'bitbake.lock'
        lock.write_text('old pid')
        inode = lock.stat().st_ino
        self.execute()
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.read_text(), 'old pid')

    def test_lock_replaced_during_acquisition_is_retried_and_not_deleted(self):
        lock = self.build / 'bitbake.lock'
        flock = releases.fcntl.flock
        changed = False
        def race(fd, operation):
            nonlocal changed
            flock(fd, operation)
            if operation == fcntl.LOCK_EX | fcntl.LOCK_NB and not changed:
                changed = True
                lock.unlink()
                lock.write_text('replacement')
        with patch.object(releases.fcntl, 'flock', side_effect=race):
            self.execute()
        self.assertTrue(changed)
        self.assertEqual(lock.read_text(), 'replacement')

    def test_linked_lock_does_not_touch_external_file(self):
        external = self.base / 'keep'
        external.write_text('preserve')
        (self.build / 'bitbake.lock').symlink_to(external)
        with self.assertRaises(OSError):
            self.execute()
        self.assertEqual(external.read_text(), 'preserve')
        self.assertFalse(self.output.exists())

    def test_published_copy_remains_usable_after_original_build_removed(self):
        config = self.execute()
        shutil.rmtree(self.build)
        with releases.completed_input(config) as (image, _):
            self.assertEqual(image.read_bytes(), b'non-OS fixture, not a disk')
        self.assertFalse(self.build.exists())

    def test_assembly_input_ignores_new_running_build_and_original_changes(self):
        config = self.execute()
        self.image.write_bytes(b'new running build output')
        with (self.build / 'bitbake.lock').open('a+') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with releases.completed_input(config) as (image, _):
                self.assertEqual(image.read_bytes(), b'non-OS fixture, not a disk')

    def test_input_reader_does_not_access_original_build_paths(self):
        config = self.execute()
        original = Path.stat
        def guard(path, *args, **kwargs):
            if path == self.build or path.is_relative_to(self.build):
                self.fail('reader accessed original build path: '+str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', guard):
            with releases.completed_input(config): pass

    def test_read_only_input_and_assembly_plan_do_not_write(self):
        config = self.execute()
        before = self.snapshot()
        guests = [{'logical_bytes': 8 * contract.GIB, 'allocated_bytes': 1024, 'boot_bytes': 1024} for _ in range(2)]
        with releases.completed_input(config) as (image, record):
            firmware = [{'id': 'zephyr-4.4.0-xenvm', 'logical_bytes': 1024, 'template': str(self.base / 'firmware')}]
            with patch.object(assemble, 'read_layout', return_value=(mbr(), 4096, contract.layout(mbr()))), patch.object(assemble, 'guest_info', return_value=guests), patch.object(assemble, 'rtos_info', return_value=firmware):
                plan = assemble.plan({'schema': 1, **config}, image, record, self.base / 'out', self.base / 'work', 64_000_000_000)
        self.assertEqual(plan['action'], 'plan')
        self.assertEqual(self.snapshot(), before)

    def test_failed_or_incomplete_record_never_publishes(self):
        for key, value in [('build_exit_code', 1), ('finished_at', '')]:
            with self.subTest(key=key):
                body = dict(self.body)
                self.body[key] = value; self.save_record()
                with self.assertRaisesRegex(ValueError, 'completed successful'):
                    self.execute()
                self.body = body; self.save_record()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.build / 'bitbake.lock').exists())

    def test_wrong_record_or_image_digest_refuses_publication(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            releases.publish_completed(self.record, '0' * 64, 'wic', self.output)
        self.image.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.execute()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.build / 'bitbake.lock').exists())

    def test_existing_or_build_internal_output_is_refused(self):
        self.output.mkdir(); (self.output / 'keep').write_text('preserve')
        with self.assertRaises(ValueError): self.execute()
        with self.assertRaisesRegex(ValueError, 'outside the build'):
            releases.publication_plan(self.record, self.digest, 'wic', self.build / 'new-release')
        self.assertEqual((self.output / 'keep').read_text(), 'preserve')

    def test_copy_failure_leaves_no_complete_release_and_unlocks(self):
        with patch.object(releases, 'write_file', side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError, 'interrupted'): self.execute()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.build / 'bitbake.lock').exists())
        self.assertEqual(self.image.read_bytes(), b'non-OS fixture, not a disk')

    def test_source_changed_during_copy_is_not_published(self):
        original = releases.signature
        calls = 0
        def changed(path):
            nonlocal calls
            calls += 1
            if calls == 2: self.image.write_bytes(b'changed during copy')
            return original(path)
        with patch.object(releases, 'signature', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed during publication'): self.execute()
        self.assertFalse(self.output.exists())

    def test_missing_or_direct_live_input_is_refused_without_writes(self):
        before = self.snapshot()
        for config in ({'platform': {}}, {'platform': {'image': str(self.image), 'build_record': str(self.record)}}):
            with self.assertRaisesRegex(ValueError, 'published completed'):
                with releases.completed_input(config): pass
        self.assertEqual(self.snapshot(), before)

    def test_published_image_corruption_or_missing_manifest_is_refused(self):
        config = self.execute()
        image = Path(config['platform']['image'])
        image.chmod(0o644); image.write_bytes(b'corrupt'); image.chmod(0o444)
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            with releases.completed_input(config): pass
        config['platform']['release_manifest_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            with releases.completed_input(config): pass

    def test_published_input_changed_while_in_use_is_rejected(self):
        config = self.execute()
        with self.assertRaisesRegex(ValueError, 'changed while being used'):
            with releases.completed_input(config) as (image, _):
                image.chmod(0o644); image.write_bytes(b'modified'); image.chmod(0o444)

    def test_raw_source_cannot_be_substituted_for_published_image(self):
        config = self.execute()
        config['platform']['image'] = str(self.image)
        with self.assertRaisesRegex(ValueError, 'independent published'):
            with releases.completed_input(config): pass

    def test_output_race_does_not_replace_existing_directory(self):
        real = releases.publish
        def race(stage, output):
            output.mkdir(); (output / 'keep').write_text('racing user data')
            return real(stage, output)
        with patch.object(releases, 'publish', side_effect=race):
            with self.assertRaises(ValueError): self.execute()
        self.assertEqual((self.output / 'keep').read_text(), 'racing user data')

    def test_unpublished_stage_with_finished_files_is_never_accepted(self):
        with patch.object(releases, 'publish', side_effect=OSError('interrupted before rename')):
            with self.assertRaisesRegex(OSError, 'interrupted before rename'): self.execute()
        stages = list(self.base.glob('.xenova-publishing-*'))
        self.assertEqual(len(stages), 1)
        stages[0].chmod(0o755)
        self.addCleanup(lambda: stages[0].chmod(0o755) if stages[0].exists() else None)
        config = releases.platform_config(stages[0])
        stages[0].chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'incomplete platform publication'):
            with releases.completed_input(config): pass

    def test_writable_or_hardlinked_release_is_refused(self):
        config = self.execute()
        image = Path(config['platform']['image'])
        image.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'read-only independent'):
            with releases.completed_input(config): pass
        image.chmod(0o444)
        (self.base / 'linked-image').hardlink_to(image)
        with self.assertRaisesRegex(ValueError, 'read-only independent'):
            with releases.completed_input(config): pass

    def test_altered_provenance_even_with_new_manifest_hash_is_rejected(self):
        config = self.execute()
        manifest_path = Path(config['platform']['release_manifest'])
        manifest = json.loads(manifest_path.read_text())
        manifest['source_commit'] = 'b' * 40
        manifest_path.chmod(0o644)
        manifest_path.write_text(json.dumps(manifest))
        manifest_path.chmod(0o444)
        config['platform']['release_manifest_sha256'] = artifact_io.sha(manifest_path)
        with self.assertRaisesRegex(ValueError, 'provenance differs'):
            with releases.completed_input(config): pass

    def test_rpi_sdimg_completed_artifact_uses_same_handoff_contract(self):
        source = self.image.with_name('platform-20261016.rpi-sdimg')
        self.image.rename(source)
        self.body['rpi_sdimg'] = {**self.body.pop('wic'), 'path': str(source)}
        self.save_record()
        result = releases.publish_completed(self.record, self.digest, 'rpi_sdimg', self.output)
        self.addCleanup(lambda: self.output.chmod(0o755))
        with releases.completed_input(result) as (image, _):
            self.assertEqual(image.name, source.name)
            self.assertEqual(image.read_bytes(), source.read_bytes())

    def test_writable_release_directory_and_corrupt_checksum_list_rejected(self):
        config = self.execute()
        self.output.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'directory must be canonical and read-only'):
            with releases.completed_input(config): pass
        self.output.chmod(0o555)
        checksums = self.output / 'SHA256SUMS'
        checksums.chmod(0o644); checksums.write_text('invalid'); checksums.chmod(0o444)
        with self.assertRaisesRegex(ValueError, 'checksum list mismatch'):
            with releases.completed_input(config): pass
