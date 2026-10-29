"""수정 가능한 원본과 불변 게시본의 경계·버전·실제 payload 검증 회귀."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from first_install_fixture import ROOT, assemble
from test_xenova_platform import load
from test_xenova_lifecycle import FirmwareToolsTest

recipe = load('xenova_template_recipe_test', ROOT / 'platform/guests/common/template_recipe.py')
resolver = load('xenova_default_template_resolver_test', ROOT / 'platform/scripts/resolve-xenova-templates.py')


class TemplateRecipeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'editable'
        (self.source / 'files').mkdir(parents=True)
        self.meta = {'schema': 1, 'source_id': 'ubuntu-24.04-20260926', 'version': '1.0.0',
                     'files': [{'source': 'files/motd', 'target': 'etc/motd', 'mode': 420}]}
        self.path = self.source / 'recipe.json'
        self.save()
        (self.source / 'files/motd').write_text('Xenova custom template\n')
        self.root = self.base / 'root'; self.root.mkdir()

    def save(self):
        self.path.write_text(json.dumps(self.meta))

    def test_source_changes_produce_new_preparation_digest_and_apply_without_editing_source(self):
        before = recipe.source_info(self.path, self.meta['source_id'])
        recipe.apply(self.root, self.path, self.meta['source_id'])
        self.assertEqual((self.root / 'etc/motd').read_text(), 'Xenova custom template\n')
        self.assertEqual(recipe.source_info(self.path, self.meta['source_id']), before)
        (self.source / 'files/motd').write_text('next application defaults')
        self.meta['version'] = '1.1.0'; self.save()
        after = recipe.source_info(self.path, self.meta['source_id'])
        self.assertNotEqual(before['source_sha256'], after['source_sha256'])
        self.assertEqual(after['recipe']['version'], '1.1.0')

    def test_reserved_identity_ssh_and_escaping_paths_are_rejected_before_writes(self):
        for target in ('../outside', '/etc/motd', 'etc/ssh/sshd_config', 'etc/machine-id', 'usr/lib/xenova/guest_identity.py'):
            with self.subTest(target=target):
                self.meta['files'][0]['target'] = target; self.save()
                with self.assertRaises(ValueError):
                    recipe.apply(self.root, self.path, self.meta['source_id'])
                self.assertFalse(list(self.root.iterdir()))

    def test_external_overlay_source_link_is_not_followed(self):
        source = self.source / 'files/motd'
        source.unlink()
        outside = self.base / 'outside'; outside.write_text('external unchanged')
        source.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'without parent links'):
            recipe.apply(self.root, self.path, self.meta['source_id'])
        self.assertEqual(outside.read_text(), 'external unchanged')

    def test_current_default_selection_has_three_unique_ids_and_common_drive(self):
        entries = json.loads((ROOT / 'platform/guests/default-templates.lock.json').read_text())['templates']
        self.assertEqual(len(entries), 3)
        self.assertEqual(len({e['template_id'] for e in entries}), 3)
        selected = next(e for e in entries if e['guest_kind'] == 'zephyr')
        self.assertEqual(selected['id'], 'xenova-zephyr-drive-common')
        self.assertRegex(selected['version'], r'^1\.2\.0-drive-[0-9a-f]{12}$')
        self.assertTrue((ROOT / 'platform/guests/zephyr/starter.lock.json').is_file())

    def test_default_catalog_rejects_legacy_duplicate_or_escaping_selection(self):
        with self.assertRaises(ValueError):
            resolver.resolve(self.base, {'schema': 1, 'templates': []})
        entries = json.loads((ROOT / 'platform/guests/default-templates.lock.json').read_text())['templates']
        entries[0]['directory'] = '../old'
        with self.assertRaisesRegex(ValueError, 'escapes'):
            resolver.resolve(self.base, {'schema': 1, 'templates': entries})
        entries[2]['template_id'] = entries[0]['template_id']
        with self.assertRaisesRegex(ValueError, 'one current default'):
            resolver.resolve(self.base, {'schema': 1, 'templates': entries})



class StarterPublicationTest(unittest.TestCase):
    setUp = FirmwareToolsTest.setUp
    build = FirmwareToolsTest.build

    def catalog(self, *, banner=b'Xenova / Zephyr / Xen'):
        build = self.build()
        lock = json.loads((ROOT / 'platform/guests/zephyr/starter.lock.json').read_text())
        for name in ('zephyr.bin', 'zephyr.elf'):
            (build / name).chmod(0o600)
            with (build / name).open('ab') as stream: stream.write(banner)
        app = ROOT / 'platform/guests/zephyr/starter-app'
        (build.parent / 'CMakeCache.txt').write_text('CMAKE_HOME_DIRECTORY:INTERNAL=' + str(app) +
            '\nZEPHYR_BASE:PATH=/fixed-zephyr\n')
        receipt = {key: lock[key] for key in ('id','version','app_files','app_source_sha256','zephyr_commit','sdk_version')}
        receipt.update(schema=1, payloads={name: {'sha256': hashlib.sha256((build / name).read_bytes()).hexdigest(),
            'bytes': (build / name).stat().st_size} for name in (
            'zephyr.bin','zephyr.elf','zephyr.dts','.config','include/generated/zephyr/version.h')})
        (build.parent / 'xenova-build-receipt.json').write_text(json.dumps(receipt))
        args = ['--build-dir', str(build), '--license', str(self.template / 'LICENSE'),
                '--version', lock['version'], '--app', lock['app'],
                '--source-revision', 'sha256:' + lock['app_source_sha256'], '--build-id', 'synthetic-test-only',
                '--app-source', str(app), '--template-id', lock['id'], '--output', str(self.base / 'output')]
        return build, lock, args

    def test_current_source_receipt_and_payload_can_be_published(self):
        build, lock, args = self.catalog()
        with patch.object(self.package.subprocess, 'check_output', return_value=lock['zephyr_commit']), redirect_stdout(io.StringIO()):
            self.package.main(args + ['--execute'])
        self.assertTrue((self.base / 'output/firmware.json').is_file())

    def test_changed_binary_after_build_receipt_is_rejected_without_publication(self):
        build, lock, args = self.catalog()
        with (build / 'zephyr.bin').open('ab') as stream: stream.write(b'changed')
        with patch.object(self.package.subprocess, 'check_output', return_value=lock['zephyr_commit']):
            with self.assertRaisesRegex(ValueError, 'build receipt'):
                self.package.main(args + ['--execute'])
        self.assertFalse((self.base / 'output').exists())

    def test_previous_perseus_binary_cannot_be_wrapped_as_xenova(self):
        build, lock, args = self.catalog(banner=b'Perseus / Zephyr / Xen')
        with patch.object(self.package.subprocess, 'check_output', return_value=lock['zephyr_commit']):
            with self.assertRaisesRegex(ValueError, 'Xenova banner'):
                self.package.main(args + ['--execute'])
        self.assertFalse((self.base / 'output').exists())



class StarterLockRefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Path(self.tmp.name) / 'starter-app'; self.app.mkdir()
        (self.app / 'main.c').write_text('initial application')
        self.tool = load('xenova_source_lock_refresh_test', ROOT / 'platform/scripts/refresh-xenova-starter-lock.py')
        self.old = json.loads((ROOT / 'platform/guests/zephyr/starter.lock.json').read_text())
        self.old['app_files'] = self.tool.builder.fingerprint(self.app)
        self.path = self.app.parent / 'starter.lock.json'
        self.path.write_text(json.dumps(self.old))

    def test_changed_source_cannot_reuse_the_previous_application_version(self):
        before = self.path.read_bytes()
        (self.app / 'main.c').write_text('new application')
        with self.assertRaisesRegex(ValueError, 'new application version'):
            self.tool.updated_lock(self.app, self.old['version'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_version_recomputes_hashes_and_preserves_upstream_pins_without_writing(self):
        before = self.path.read_bytes()
        (self.app / 'main.c').write_text('new application')
        meta = self.tool.updated_lock(self.app, '1.1.0')
        self.assertEqual(meta['id'], 'xenova-zephyr-starter-1.1.0')
        self.assertEqual(meta['app_files']['main.c']['sha256'], hashlib.sha256(b'new application').hexdigest())
        self.assertEqual(meta['zephyr_commit'], self.old['zephyr_commit'])
        self.assertEqual(meta['sdk_version'], self.old['sdk_version'])
        self.assertEqual(self.path.read_bytes(), before)


del FirmwareToolsTest
