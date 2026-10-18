"""고정 Xen의 생략/상태 규칙과 실제 CLI→registry→대상 검증 범위를 재현한다.

Xen·mountinfo·콘솔 exec만 fixture로 대체하며 records/load_records/자산 검증은 실행한다.
실제 Xen 파서·게스트 부팅 시험을 대신하지 않는다.
"""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import FILES, vm
from test_zenova_platform import record, fixture, domain, FakeXen
from test_zenova_zephyr import bundle
import zenova_zephyr as zephyr


class SnapshotXen(FakeXen):
    """xl -l은 설정만, 짧은 목록은 고정 Xen의 여섯 상태 열을 제공한다."""
    def __init__(self, base, records):
        super().__init__(base, records)
        self.states = {}
        self.omit_disks = False
        self.omit_config = False
        self.short_name = None

    def __call__(self, args, **kwargs):
        if args == ['xl', 'list']:
            self.calls.append(args)
            return 'Name ID Mem VCPUs State Time(s)\nDomain-0 0 1024 4 r----- 0\n' + ''.join(
                f"{self.short_name or r['config']['c_info']['name']} {r['domid']} 16 1 {self.states.get(r['domid'], 'r-----')} 0\n"
                for r in self.rows)
        if args == ['xl', 'list', '-l']:
            self.calls.append(args)
            rows = copy.deepcopy(self.rows)
            if self.omit_config:
                return '[]'
            for row in rows:
                if self.records.get(row['config']['c_info'].get('name'), {}).get('guest_kind') == 'zephyr':
                    if self.omit_disks:
                        row['config'].pop('disks', None)
                    else:
                        row['config']['disks'] = []
            return json.dumps(rows)
        return super().__call__(args, **kwargs)


class DomainReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zenova-domain-review-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.linux = record()
        self.directory = fixture(self.base, self.linux)
        (self.base / '.zenova-storage.json').write_text(json.dumps(
            {'schema': 1, 'uuid': '12345678-1234-4234-8234-123456789abc'}))
        self.mountinfo = self.base / 'mountinfo'
        self.mountinfo.write_text(f'1 0 8:3 / {self.base} rw - ext4 /dev/fixture rw\n')
        self.records = {self.linux['id']: self.linux}
        self.xen = SnapshotXen(self.base, self.records)
        self.p = vm.Platform(self.base, self.base / 'run/zenova', self.xen)
        self.output = io.StringIO()

    def cli(self, *args):
        # 실제 마운트 입력만 대체하고 storage_check의 검증 로직도 실행한다.
        real_storage_check = vm.storage_check
        with patch.object(vm, 'Platform', return_value=self.p), \
             patch.object(vm, 'storage_check', side_effect=lambda data: real_storage_check(data, self.mountinfo)), \
             patch.object(self.p, 'check_xen'), patch.object(self.p, 'check_network'), \
             patch.object(vm.os, 'execvp') as console, redirect_stdout(self.output):
            vm.main(list(args))
        return console

    def add_linux(self):
        other = record('debian-dev')
        directory = fixture(self.base, other)
        self.records[other['id']] = other
        return other, directory

    def add_zephyr(self):
        template = bundle(self.base / 'template')
        z = zephyr.new_instance(self.base, 'zephyr-dev', template, self.records)
        self.records[z['id']] = z
        return z

    def live(self, r, domid=1, flags='r-----'):
        self.xen.rows.append(domain(r, self.base / 'instances' / r['id'] / 'root.raw', domid))
        self.xen.states[domid] = flags

    def test_diskless_omitted_and_empty_array_fixtures(self):
        for omitted in (True, False):
            with self.subTest(omitted=omitted):
                source = Path(__file__).parent / 'fixtures/xen-4.21'
                detail = (source / ('diskless-omitted.json' if omitted else 'diskless-empty.json')).read_text()
                short = (source / 'diskless-list.txt').read_text()
                with patch.object(self.p, 'command', side_effect=lambda args: detail if args[-1] == '-l' else short):
                    rows = self.p.domains()
                self.assertEqual(rows[0]['config']['disks'], [])
                self.assertEqual(rows[0]['config']['c_info']['uuid'], 'cccc0000-1111-4222-8333-123456789abc')

    def test_config_retrieval_omission_is_not_diskless(self):
        self.live(self.linux)
        self.xen.omit_config = True
        with self.assertRaisesRegex(vm.PlatformError, 'omitted'):
            self.p.domains()

    def test_invalid_identity_disks_and_short_name_refused(self):
        self.live(self.linux)
        original = copy.deepcopy(self.xen.rows)
        for mutate in (
            lambda row: row['config']['c_info'].pop('uuid'),
            lambda row: row['config']['c_info'].update(uuid='invalid'),
            lambda row: row['config']['c_info'].pop('name'),
            lambda row: row['config'].update(disks=None),
            lambda row: row['config'].update(disks={}),
        ):
            self.xen.rows = copy.deepcopy(original)
            mutate(self.xen.rows[0])
            # 짧은 목록은 변경 전의 정상 이름을 반환한다.
            self.xen.short_name = self.linux['name']
            with self.subTest(row=self.xen.rows), self.assertRaises(vm.PlatformError):
                self.p.domains()
        self.xen.rows = original
        self.xen.short_name = 'different-name'
        with self.assertRaises(vm.PlatformError):
            self.p.domains()

    def test_duplicate_domain_id_and_live_uuid_refused(self):
        self.live(self.linux)
        self.xen.rows.append(copy.deepcopy(self.xen.rows[0]))
        with self.assertRaises(vm.PlatformError):
            self.p.domains()
        self.xen.rows[-1]['domid'] = 2
        with self.assertRaises(vm.PlatformError):
            self.p.domains()

    def test_linux_disk_guard_survives_diskless_zephyr(self):
        z = self.add_zephyr()
        self.live(z)
        self.xen.omit_disks = True
        self.xen.rows.append(domain(record('debian-dev'), self.directory / 'root.raw', 2))
        with self.assertRaisesRegex(vm.PlatformError, 'already attached'):
            self.cli('start', self.linux['id'])
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_main_unrelated_missing_and_corrupt_assets_do_not_block_target_actions(self):
        other, directory = self.add_linux()
        image = directory / 'Image'
        original = image.read_bytes()
        for corruption in ('missing', 'digest'):
            if corruption == 'missing':
                image.unlink()
            else:
                image.write_bytes(b'corrupted kernel')
            for action in ('start', 'stop', 'console'):
                with self.subTest(corruption=corruption, action=action):
                    self.xen.rows = []
                    if action != 'start':
                        self.live(self.linux)
                    console = self.cli(action, self.linux['id'])
                    if action == 'console':
                        console.assert_called_once_with('xl', ['xl', 'console', '-t', 'pv', '1'])
                    if action == 'stop':
                        self.assertFalse(self.xen.rows)
            image.write_bytes(original)

    def test_main_unrelated_firmware_hash_error_does_not_block_target_start(self):
        z = self.add_zephyr()
        binary = zephyr.active_directory(self.base / 'instances' / z['id'], z) / 'zephyr.bin'
        binary.chmod(0o644)
        binary.write_bytes(b'corrupted firmware')
        for action in ('start', 'stop', 'console'):
            with self.subTest(action=action):
                self.xen.rows = []
                if action != 'start':
                    self.live(self.linux)
                self.cli(action, self.linux['id'])
        with self.assertRaises(vm.PlatformError):
            self.cli('start', z['id'])

    def test_main_missing_unrelated_linux_disk_does_not_block_target(self):
        other, directory = self.add_linux()
        (directory / 'root.raw').unlink()
        self.cli('start', self.linux['id'])
        self.cli('stop', self.linux['id'])
        with self.assertRaises(vm.PlatformError):
            self.cli('start', other['id'])

    def test_main_target_boot_assets_required_for_start_config_reboot(self):
        (self.directory / 'Image').unlink()
        self.live(self.linux)
        for action in ('start', 'config', 'reboot'):
            with self.subTest(action=action), self.assertRaises(vm.PlatformError):
                self.cli(action, self.linux['id'])
        self.assertFalse(any(c[:2] in (['xl', 'create'], ['xl', 'reboot'], ['xl', 'destroy']) for c in self.xen.calls))

    def test_main_broken_target_can_be_stopped_or_inspected_on_console(self):
        (self.directory / 'Image').unlink()
        self.live(self.linux)
        self.cli('console', self.linux['id'])
        self.cli('stop', self.linux['id'])
        self.assertFalse(self.xen.rows)

    def test_main_broken_zephyr_firmware_allows_console_and_explicit_force_stop(self):
        z = self.add_zephyr()
        binary = zephyr.active_directory(self.base / 'instances' / z['id'], z) / 'zephyr.bin'
        binary.chmod(0o644)
        binary.write_bytes(b'corrupted firmware')
        self.live(z)
        self.xen.omit_disks = True
        self.cli('console', z['id'])
        with self.assertRaisesRegex(vm.PlatformError, 'explicit --force'):
            self.cli('stop', z['id'])
        self.cli('stop', z['id'], '--force')
        self.assertFalse(self.xen.rows)

    def test_main_board_shutdown_handles_broken_boot_assets_before_os_request(self):
        other, directory = self.add_linux()
        (directory / 'Image').unlink()
        self.live(self.linux, 1)
        self.live(other, 2)
        self.cli('poweroff')
        shutdowns = [c for c in self.xen.calls if c[:2] == ['xl', 'shutdown']]
        self.assertEqual({c[2] for c in shutdowns}, {'1', '2'})
        self.assertEqual(self.xen.calls[-1], ['systemctl', 'poweroff'])
        self.assertFalse(self.xen.rows)

    def test_main_broken_zephyr_assets_do_not_bypass_board_force_policy(self):
        z = self.add_zephyr()
        binary = zephyr.active_directory(self.base / 'instances' / z['id'], z) / 'zephyr.bin'
        binary.chmod(0o644)
        binary.write_bytes(b'corrupted firmware')
        (self.directory / 'Image').unlink()
        self.live(self.linux, 1)
        self.live(z, 2)
        self.xen.omit_disks = True
        with self.assertRaisesRegex(vm.PlatformError, 'explicit --force'):
            self.cli('poweroff')
        self.assertFalse(any(c[:2] in (['xl', 'shutdown'], ['xl', 'destroy']) for c in self.xen.calls))
        self.cli('poweroff', '--force')
        self.assertEqual(self.xen.calls[-1], ['systemctl', 'poweroff'])

    def test_main_registry_only_retains_duplicate_identity_and_disk_protection(self):
        other, directory = self.add_linux()
        metadata = directory / 'instance.json'
        original = metadata.read_bytes()
        for field in ('uuid', 'name', 'mac', 'ipv4'):
            bad = {**other, field: self.linux[field]}
            metadata.write_text(json.dumps(bad))
            with self.subTest(field=field), self.assertRaisesRegex(vm.PlatformError, 'duplicate'):
                self.cli('stop', self.linux['id'])
        metadata.write_bytes(original)
        disk = directory / 'root.raw'
        disk.unlink()
        os.link(self.directory / 'root.raw', disk)
        with self.assertRaisesRegex(vm.PlatformError, 'same writable disk'):
            self.cli('stop', self.linux['id'])

    def test_main_registry_only_rejects_unsafe_paths_and_template_disk_alias(self):
        other, directory = self.add_linux()
        disk = directory / 'root.raw'
        disk.unlink()
        disk.symlink_to(self.directory / 'root.raw')
        with self.assertRaises(vm.PlatformError):
            self.cli('stop', self.linux['id'])
        disk.unlink()
        disk.write_bytes(b'own disk')
        template = self.base / 'templates' / other['template']
        template.mkdir(parents=True)
        os.link(disk, template / 'root.raw')
        with self.assertRaisesRegex(vm.PlatformError, 'immutable template'):
            self.cli('stop', self.linux['id'])
        (template / 'root.raw').unlink()
        registry = self.base / 'instances'
        registry.rename(self.base / 'actual-instances')
        registry.symlink_to(self.base / 'actual-instances', target_is_directory=True)
        with self.assertRaises(vm.PlatformError):
            self.cli('stop', self.linux['id'])

    def test_main_status_reports_actual_states_and_unknown_app_readiness(self):
        states = {'r-----': 'running', '-b----': 'blocked', '--p---': 'paused',
                  '---s--': 'shutdown', '---sc-': 'crashed', '-----d': 'dying',
                  '------': 'unknown', '---sw-': 'shutdown', '--p--d': 'dying'}
        for flags, expected in states.items():
            with self.subTest(flags=flags):
                self.xen.rows = []
                self.live(self.linux, flags=flags)
                self.output.seek(0); self.output.truncate()
                self.cli('status')
                row = json.loads(self.output.getvalue())['instances'][0]
                self.assertEqual(row['xen_state'], expected)
                self.assertEqual(row['xen_state_flags'], flags)
                self.assertEqual(row['app_readiness'], 'unknown')
        self.xen.rows = []
        self.output.seek(0); self.output.truncate()
        self.cli('status')
        self.assertEqual(json.loads(self.output.getvalue())['instances'][0]['xen_state'], 'stopped')

    def test_existing_blocked_domain_is_healthy_without_duplicate_create(self):
        self.live(self.linux, flags='-b----')
        self.cli('start', self.linux['id'])
        self.assertIn('blocked', self.output.getvalue())
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_existing_unhealthy_domain_refuses_start_without_recovery(self):
        for flags, state in (('--p---', 'paused'), ('---s--', 'shutdown'),
                             ('---sc-', 'crashed'), ('-----d', 'dying'), ('------', 'unknown')):
            self.xen.rows = []
            self.live(self.linux, flags=flags)
            with self.subTest(flags=flags), self.assertRaisesRegex(vm.PlatformError, state):
                self.cli('start', self.linux['id'])
        self.assertFalse(any(c[:2] in (['xl', 'create'], ['xl', 'destroy'], ['xl', 'unpause']) for c in self.xen.calls))

    def test_autostart_main_preserves_blocked_and_failed_actual_states(self):
        other, directory = self.add_linux()
        self.live(self.linux, 1, '-b----')
        self.live(other, 2, '---sc-')
        real_read = Path.read_text
        def read(path, *args, **kwargs):
            if path == Path('/etc/zenova/defaults.json'):
                return (FILES / 'defaults.json').read_text()
            return real_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), self.assertRaisesRegex(vm.PlatformError, 'crashed'):
            self.cli('autostart')
        results = json.loads((self.p.runtime / 'autostart-results.json').read_text())
        self.assertEqual(results[self.linux['id']]['result'], 'blocked')
        self.assertEqual(results[self.linux['id']]['xen_state'], 'blocked')
        self.assertEqual(results[other['id']]['result'], 'failed')
        self.assertEqual(results[other['id']]['xen_state'], 'crashed')
        self.assertEqual(results[self.linux['id']]['app_readiness'], 'unknown')
        self.assertFalse(any(c[:2] in (['xl', 'create'], ['xl', 'destroy']) for c in self.xen.calls))

    def test_create_result_is_checked_for_paused_domain_without_recovery(self):
        self.xen.states[1] = '--p---'
        with self.assertRaisesRegex(vm.PlatformError, 'paused'):
            self.cli('start', self.linux['id'])
        self.assertEqual(len(self.xen.rows), 1)
        self.assertFalse(any(c[:2] in (['xl', 'destroy'], ['xl', 'unpause']) for c in self.xen.calls))


if __name__ == '__main__':
    unittest.main()
