"""생성 전 용량 거절과 불변 payload의 단계 내 해시 재사용을 검증한다.

실제 Manager·catalog·용량 계산을 사용한다. Xen과 게스트 준비는 기존 fixture로
대체하며, 이 시험은 장기 작업의 전역 잠금 분리나 실보드 성능 수락을 뜻하지 않는다.
"""
from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_xenova_management as fixtures
import xenova_catalog as catalog
import xenova_vm as vm


class TemplatePreflightTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManagementTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.source = self.f.data / 'templates/source'

    def free(self, size):
        return patch.object(vm.os, 'statvfs', return_value=SimpleNamespace(f_bavail=size, f_frsize=1))

    def change_manifest(self, change):
        path = self.source / 'template.json'
        meta = json.loads(path.read_text())
        change(meta)
        path.chmod(0o644)
        path.write_text(json.dumps(meta))
        path.chmod(0o444)
        self.f.descriptor['manifest_sha256'] = catalog.sha(path)
        descriptor = self.f.data / 'catalog/ubuntu-24.04-arm64/1.0.0.json'
        descriptor.write_text(json.dumps(self.f.descriptor))

    def assert_no_creation(self):
        self.assertEqual(self.f.copies, 0)
        self.assertEqual(self.f.m._operations(), [])
        self.assertEqual(list((self.f.data / 'instances').iterdir()), [])
        self.assertFalse(any(call[:2] == ['xl', 'create'] for call in self.f.xen.calls))

    def test_insufficient_space_does_not_hash_payload_or_record_a_creation(self):
        for execute in (False, True):
            with self.subTest(execute=execute), self.free(0), patch.object(catalog, 'sha', wraps=catalog.sha) as sha:
                result = self.f.call('create', self.f.spec, execute=execute)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['error']['code'], 'INSUFFICIENT_STORAGE', result)
            self.assertIsNone(result['operation_id'])
            self.assertFalse(any(Path(call.args[0]).name != 'template.json' for call in sha.call_args_list))
            self.assert_no_creation()

    def test_full_description_hashes_each_payload_once(self):
        with patch.object(catalog, 'sha', wraps=catalog.sha) as sha:
            catalog.describe(self.f.data, self.f.descriptor, full=True)
        counts = Counter(Path(call.args[0]).name for call in sha.call_args_list)
        self.assertEqual(counts, Counter({name: 1 for name in (
            'template.json', 'root.raw', 'Image', 'initrd', 'kernel.config', 'boot.json')}))

    def test_metadata_description_does_not_read_payload(self):
        with patch.object(catalog, 'sha', wraps=catalog.sha) as sha:
            catalog.describe(self.f.data, self.f.descriptor)
        self.assertEqual([Path(call.args[0]).name for call in sha.call_args_list], ['template.json'])

    def test_root_digest_must_match_both_manifest_fields(self):
        self.change_manifest(lambda meta: meta.update(root_sha256='0' * 64))
        with self.assertRaisesRegex(vm.PlatformError, 'Linux root digest mismatch'):
            catalog.describe(self.f.data, self.f.descriptor, full=True)

    def test_inconsistent_root_and_boot_metadata_is_rejected_without_payload_reads(self):
        for field in ('root', 'boot'):
            with self.subTest(field=field):
                if field == 'root':
                    self.change_manifest(lambda meta: meta.update(root_sha256='0' * 64))
                else:
                    self.change_manifest(lambda meta: meta.update(root_sha256=meta['files']['root.raw']['sha256']))
                    self.change_manifest(lambda meta: meta['files']['Image'].update(sha256='0' * 64))
                with patch.object(catalog, 'sha', wraps=catalog.sha) as hashes:
                    result = self.f.call('create', self.f.spec)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error']['code'], 'ARTIFACT_INVALID')
                self.assertTrue(all(Path(call.args[0]).name == 'template.json' for call in hashes.call_args_list))
                self.assert_no_creation()

    def test_linux_plan_defers_payload_validation_without_recording_work(self):
        root = self.source / 'root.raw'
        root.chmod(0o644)
        content = bytearray(root.read_bytes())
        content[0] ^= 1
        root.write_bytes(content)
        root.chmod(0o444)
        with self.free(32 * 1024 ** 3), patch.object(catalog, 'sha', wraps=catalog.sha) as hashes:
            result = self.f.call('create', self.f.spec, execute=False)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['data']['plan'])
        self.assertTrue(all(Path(call.args[0]).name=='template.json' for call in hashes.call_args_list))
        self.assert_no_creation()

    def test_invalid_metadata_is_rejected_before_capacity_check(self):
        self.change_manifest(lambda meta: meta['files'].pop('Image'))
        with self.free(0), patch.object(self.f.m, '_reserve_space', wraps=self.f.m._reserve_space) as reserve:
            result = self.f.call('create', self.f.spec)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'ARTIFACT_INVALID', result)
        reserve.assert_not_called()
        self.assert_no_creation()

    def test_linux_plan_checks_capacity_without_reading_payload(self):
        events = []
        original_sha, original_reserve = catalog.sha, self.f.m._reserve_space

        def sha(path):
            if Path(path).name == 'root.raw':
                events.append('root-hash')
            return original_sha(path)

        def reserve(*args, **kwargs):
            events.append('capacity')
            return original_reserve(*args, **kwargs)

        with self.free(32 * 1024 ** 3), patch.object(catalog, 'sha', side_effect=sha), \
                patch.object(self.f.m, '_reserve_space', side_effect=reserve):
            result = self.f.call('create', self.f.spec, execute=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual(events, ['capacity'])
        self.assert_no_creation()

    def test_successful_create_replay_does_not_revalidate_or_recopy_payload(self):
        self.f.create()
        with self.free(0), patch.object(catalog, 'sha', wraps=catalog.sha) as sha:
            result = self.f.call('create', self.f.spec)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['replayed'])
        self.assertEqual(self.f.copies, 1)
        self.assertFalse(any(Path(call.args[0]).name == 'root.raw' for call in sha.call_args_list))


if __name__ == '__main__':
    unittest.main()
