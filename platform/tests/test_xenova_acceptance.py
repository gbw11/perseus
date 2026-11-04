"""실제 main→records 경로의 domid 신원 재검사와 VM별 종료 정책. Xen 실행 대역이다."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from first_install_fixture import FILES, ROOT, vm, assemble
from test_xenova_domain_review import DomainReviewTest
from test_xenova_platform import domain, record
from test_xenova_lifecycle import application_bundle, snapshot
from test_xenova_zephyr import bundle
import xenova_runtime as runtime
import xenova_zephyr as zephyr


class AcceptanceTest(unittest.TestCase):
    setUp = DomainReviewTest.setUp
    add_zephyr = DomainReviewTest.add_zephyr
    add_linux = DomainReviewTest.add_linux
    live = DomainReviewTest.live

    def cli(self, *args, console=None):
        real_storage = vm.storage_check
        output = io.StringIO()
        with patch.object(vm, 'Platform', return_value=self.p), \
             patch.object(vm, 'storage_check', side_effect=lambda data: real_storage(data, self.mountinfo)), \
             patch.object(self.p, 'check_xen'), patch.object(self.p, 'check_network'), \
             patch.object(vm.os, 'execvp', side_effect=console) as execution, redirect_stdout(output):
            try:
                vm.main(list(args))
            except SystemExit as exc:
                # Public CLI errors are JSON envelopes plus a failure exit.
                # Keep the physical-command assertions below on the real entry.
                self.assertNotEqual(exc.code, 0)
                documents, remaining = [], output.getvalue().lstrip()
                while remaining:
                    document, end = json.JSONDecoder().raw_decode(remaining)
                    documents.append(document)
                    remaining = remaining[end:].lstrip()
                # Legacy board shutdown also emits its per-VM result first.
                result = documents[-1]
                self.assertFalse(result['ok'])
                self.assertIsNotNone(result['error']['code'])
                raise vm.PlatformError(result['error']['message']) from exc
        return execution

    def save(self, r):
        (self.base / 'instances' / r['id'] / 'instance.json').write_text(json.dumps(r))

    def controls(self):
        return [c for c in self.xen.calls if c[:2] in (['xl', 'shutdown'], ['xl', 'destroy'], ['xl', 'reboot'])]

    def test_mock_rejects_uuid_and_cli_console_uses_domid_without_holding_lock(self):
        self.live(self.linux, 7)
        with self.assertRaisesRegex(vm.PlatformError, 'invalid domain identifier'):
            self.xen(['xl', 'shutdown', self.linux['uuid']])
        def console(binary, args):
            self.assertEqual((binary, args), ('xl', ['xl', 'console', '-t', 'pv', '7']))
            with runtime.operation_lock(self.p.runtime):
                pass
        self.cli('console', self.linux['id'], console=console)

    def test_cli_linux_shutdown_reboot_and_explicit_destroy_use_current_domid(self):
        for action, suffix in (('stop', []), ('reboot', []), ('destroy', ['--force'])):
            with self.subTest(action=action):
                self.xen.rows = []; self.xen.calls = []
                self.live(self.linux, 9)
                before = (self.directory / 'root.raw').read_bytes()
                self.cli(action, self.linux['id'], *suffix)
                self.assertEqual(self.controls()[0][2], '9')
                self.assertEqual((self.directory / 'root.raw').read_bytes(), before)
                self.assertEqual(json.loads((self.directory / 'instance.json').read_text())['uuid'], self.linux['uuid'])

    def test_identity_or_domid_change_disappearance_and_reuse_refuse_all_control_actions(self):
        for action, suffix in (('stop', []), ('reboot', []), ('destroy', ['--force']), ('console', [])):
            for mutation in ('id', 'gone', 'uuid', 'name', 'reuse', 'duplicate'):
                with self.subTest(action=action, mutation=mutation):
                    self.xen.rows = []; self.xen.calls = []
                    self.live(self.linux, 11)
                    calls = 0
                    def command(args, **kw):
                        nonlocal calls
                        if args == ['xl', 'list', '-l']:
                            calls += 1
                            if calls == 2:
                                row = self.xen.rows[0]
                                if mutation == 'id': row['domid'] = 12
                                elif mutation == 'gone': self.xen.rows = []
                                elif mutation == 'uuid': row['config']['c_info']['uuid'] = record('debian-dev')['uuid']
                                elif mutation == 'name': row['config']['c_info']['name'] = 'replacement'
                                elif mutation == 'reuse':
                                    row['config']['c_info'] = {'uuid': record('debian-dev')['uuid'], 'name': 'replacement'}
                                elif mutation == 'duplicate':
                                    other = copy.deepcopy(row); other['domid'] = 12
                                    self.xen.rows.append(other)
                        return self.xen(args, **kw)
                    self.p.command = command
                    with self.assertRaises(vm.PlatformError):
                        self.cli(action, self.linux['id'], *suffix)
                    self.assertFalse(self.controls())

    def test_same_name_other_uuid_and_duplicate_name_fail_before_console(self):
        self.live(self.linux, 5)
        self.xen.rows[0]['config']['c_info']['uuid'] = record('debian-dev')['uuid']
        with self.assertRaises(vm.PlatformError): self.cli('console', self.linux['id'])
        self.live(self.linux, 6)
        with self.assertRaises(vm.PlatformError): self.cli('console', self.linux['id'])

    def test_autostart_on_does_not_approve_board_force_and_preflight_stops_nothing(self):
        z = self.add_zephyr(); self.live(self.linux, 1); self.live(z, 2)
        self.cli('autostart-set', z['id'], 'on')
        self.assertEqual(vm.board_shutdown_policy(vm.load_records(self.base, validate_assets=False)[z['id']]), 'refuse')
        for action in ('poweroff', 'reboot-board', 'shutdown-all'):
            with self.assertRaisesRegex(vm.PlatformError, 'board force-allowed'):
                self.cli(action)
        self.assertFalse(self.controls())
        self.assertEqual(len(self.xen.rows), 2)

    def test_board_policy_is_persistent_explicit_and_preserves_linux_and_firmware_assets(self):
        z = self.add_zephyr()
        before = snapshot(self.base / 'instances')
        self.cli('board-shutdown-set', z['id'], 'force-allowed')
        current = json.loads((self.base / 'instances' / z['id'] / 'instance.json').read_text())
        self.assertEqual(current, {**z, 'board_shutdown_policy': 'force-allowed'})
        after = snapshot(self.base / 'instances')
        for path in before:
            if path != z['id'] + '/instance.json': self.assertEqual(before[path], after[path])
        with self.assertRaises(vm.PlatformError): self.cli('board-shutdown-set', self.linux['id'], 'force-allowed')

    def test_mixed_board_shutdown_graceful_linux_and_only_approved_zephyr_forced(self):
        z = self.add_zephyr(); z['board_shutdown_policy'] = 'force-allowed'; self.save(z)
        self.live(self.linux, 4); self.live(z, 6, '-b----')
        self.cli('poweroff')
        controls = self.controls()
        self.assertIn(['xl', 'shutdown', '4'], controls)
        self.assertIn(['xl', 'destroy', '6'], controls)
        self.assertNotIn(['xl', 'destroy', '4'], controls)
        result = json.loads((self.p.runtime / 'board-shutdown-result.json').read_text())
        self.assertEqual(result['instances'][self.linux['id']]['result'], 'graceful')
        self.assertEqual(result['instances'][z['id']]['result'], 'forced')
        self.assertFalse(result['global_force'])
        self.assertEqual(self.xen.calls[-1], ['systemctl', 'poweroff'])

    def test_individual_zephyr_stop_reboot_still_require_force_despite_board_policy(self):
        z = self.add_zephyr(); z['board_shutdown_policy'] = 'force-allowed'; self.save(z); self.live(z, 8)
        for action in ('stop', 'reboot'):
            with self.assertRaisesRegex(vm.PlatformError, '--force'): self.cli(action, z['id'])
        self.assertFalse(self.controls())
        self.cli('reboot', z['id'], '--force')
        self.assertIn(['xl', 'destroy', '8'], self.controls())
        current = json.loads((self.base / 'instances' / z['id'] / 'instance.json').read_text())
        self.assertEqual(current, z)

    def test_unregistered_domain_preflight_refuses_all_registered_guests(self):
        self.live(self.linux, 3)
        other = record('debian-dev'); self.xen.rows.append(domain(other, self.directory / 'root.raw', 7))
        with self.assertRaisesRegex(vm.PlatformError, 'unregistered'): self.cli('shutdown-all', '--force')
        self.assertFalse(self.controls())

    def test_request_return_is_not_domain_removal_and_partial_timeout_is_recorded(self):
        z = self.add_zephyr(); z['board_shutdown_policy'] = 'force-allowed'; self.save(z)
        self.live(self.linux, 1); self.live(z, 2); self.xen.hang_shutdown = True
        ticks = [0]
        self.p.clock = lambda: ticks[0]
        self.p.sleep = lambda seconds: ticks.__setitem__(0, ticks[0] + seconds)
        with self.assertRaisesRegex(vm.PlatformError, 'graceful shutdown timed out'): self.cli('reboot-board')
        result = json.loads((self.p.runtime / 'board-shutdown-result.json').read_text())
        self.assertEqual(result['instances'][self.linux['id']]['stage'], 'removal-timeout')
        self.assertEqual(result['instances'][z['id']]['stage'], 'domain-removed')
        self.assertNotIn(['xl', 'destroy', '1'], self.controls())
        self.assertFalse(any(c == ['systemctl', 'reboot'] for c in self.xen.calls))
        self.assertFalse((self.p.runtime / 'shutdown-requested').exists())

    def test_shutdown_command_error_does_not_hide_other_guest_result(self):
        other, directory = self.add_linux(); self.live(self.linux, 1); self.live(other, 2)
        def command(args, **kw):
            if args == ['xl', 'shutdown', '1']: raise vm.PlatformError('injected guest request error')
            return self.xen(args, **kw)
        self.p.command = command
        with self.assertRaisesRegex(vm.PlatformError, 'injected guest'): self.cli('shutdown-all')
        result = json.loads((self.p.runtime / 'last-stop.json').read_text())
        self.assertEqual(result['instances'][self.linux['id']]['result'], 'failed')
        self.assertEqual(result['instances'][other['id']]['result'], 'graceful')

    def test_replacement_during_shutdown_is_not_destroyed_and_blocks_os_request(self):
        self.live(self.linux, 1)
        def command(args, **kw):
            result = self.xen(args, **kw)
            if args == ['xl', 'shutdown', '1']: self.live(self.linux, 22)
            return result
        self.p.command = command
        with self.assertRaisesRegex(vm.PlatformError, 'replacement domain'): self.cli('poweroff')
        self.assertEqual(self.controls(), [['xl', 'shutdown', '1']])

    def test_starter_defaults_and_diagnostic_template_have_separate_policies(self):
        defaults = json.loads((FILES / 'defaults.json').read_text())
        self.assertEqual({z['id'] for z in defaults['firmware_instances']}, {'zephyr-drive-a','zephyr-drive-b'})
        for z in defaults['firmware_instances']:
            self.assertTrue(z['autostart'])
            self.assertEqual(z['template'], 'xenova-zephyr-drive-lattice-lab-1.0.0')
            self.assertEqual(z['board_shutdown_policy'], 'graceful')
            self.assertEqual(z['template_id'], 'zephyr-drive-lattice-lab-arm64')
            selected = json.loads((ROOT / 'platform/guests/default-templates.lock.json').read_text())['templates']
            self.assertEqual(z['template_version'], next(e['version'] for e in selected if e['guest_kind'] == 'zephyr'))
        self.assertTrue(all(row['autostart'] for row in defaults['instances']))
        self.assertFalse(zephyr.CAPABILITIES['graceful_shutdown'])
        old = self.add_zephyr()
        self.assertFalse(old['autostart']); self.assertEqual(vm.board_shutdown_policy(old), 'refuse')

    def test_new_starter_catalog_seed_and_firmware_replacement_preserve_user_policies(self):
        template = bundle(self.base / 'diagnostic')
        app = application_bundle(template, self.base / 'starter')
        manifest = app / 'firmware.json'; meta = json.loads(manifest.read_text())
        lock = json.loads((ROOT / 'platform/guests/zephyr/starter.lock.json').read_text())
        meta.update(id=lock['id'], kind='zephyr-xen-firmware', version=lock['version'])
        meta['source'].update({k: lock[k] for k in ('app', 'app_files', 'app_source_sha256', 'zephyr_commit', 'sdk_version')})
        meta['source']['source_revision'] = 'sha256:' + lock['app_source_sha256']
        manifest.chmod(0o644); manifest.write_text(json.dumps(meta)); manifest.chmod(0o444)
        info = assemble.rtos_info({'rtos_templates': [{'template_dir': str(app), 'template_manifest_sha256': zephyr.sha(manifest)}]})
        (self.base / 'seeds').mkdir()
        assemble.copy_rtos_templates(self.base, info)
        seed = assemble.copy_rtos_seeds(self.base, info)[0]['record']
        self.assertTrue(seed['autostart']); self.assertEqual(seed['board_shutdown_policy'], 'force-allowed')
        self.assertEqual(seed['active_firmware']['version'], '1.0.0')
        old = self.add_zephyr(); old['board_shutdown_policy'] = 'force-allowed'; self.save(old)
        self.cli('firmware-replace', old['id'], '--bundle', str(app), '--sha256', zephyr.sha(manifest), '--execute')
        current = vm.load_records(self.base)[old['id']]
        self.assertEqual(current['uuid'], old['uuid']); self.assertEqual(current['autostart'], old['autostart'])
        self.assertEqual(current['board_shutdown_policy'], old['board_shutdown_policy'])
        self.assertEqual(current['creation_origin'], old['creation_origin'])
        self.assertTrue(zephyr.active_directory(self.base / 'instances' / old['id'], old).is_dir())

    def test_shutdown_deadline_also_bounds_each_xl_query_before_any_mutation(self):
        self.live(self.linux, 1)
        ticks = [0]; timeouts = []
        self.p.clock = lambda: ticks[0]
        def command(args, **kwargs):
            timeouts.append(kwargs.get('timeout'))
            result = self.xen(args, **kwargs)
            if args[:2] == ['xl', 'list']:
                ticks[0] += 1
            return result
        self.p.command = command
        with self.assertRaisesRegex(vm.PlatformError, 'deadline exceeded'), redirect_stdout(io.StringIO()):
            self.p.stop_many([self.linux], timeout=2)
        self.assertEqual(timeouts, [2, 1])
        self.assertFalse(self.controls())

    def test_starter_main_calls_banner_once_then_forever_sleep_without_application_thread(self):
        import subprocess
        source = ROOT / 'platform/guests/zephyr/starter-app/src/main.c'
        headers = self.base / 'headers/zephyr'
        (headers / 'sys').mkdir(parents=True)
        (headers / 'kernel.h').write_text('#define K_FOREVER (-1LL)\nint k_sleep(long long timeout);\n')
        (headers / 'sys/printk.h').write_text('int printk(const char *format, ...);\n')
        code = self.base / 'starter-test.c'
        code.write_text(
            '#include <assert.h>\n#include <stdarg.h>\n#include <stdio.h>\n#include <string.h>\n'
            'static int prints, sleeps; static char message[512];\n'
            'int printk(const char *format, ...) { prints++; va_list a; va_start(a,format); '
            'vsnprintf(message,sizeof(message),format,a); va_end(a); return 0; }\n'
            'int k_sleep(long long timeout) { assert(timeout == -1); sleeps++; return 0; }\n'
            '#define main starter_main\n#include "' + str(source) + '"\n#undef main\n'
            'int main(void) { starter_main(); assert(prints==1 && sleeps==1); '
            'assert(strstr(message,"Xenova / Zephyr / Xen")); return 0; }\n')
        compiled = subprocess.run(['cc', '-I', str(headers.parent), str(code), '-o', str(self.base/'starter-test')],
                                  text=True, capture_output=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertEqual(subprocess.run([str(self.base/'starter-test')], timeout=3).returncode, 0)
        self.assertNotIn('K_THREAD_DEFINE', source.read_text())


del DomainReviewTest
