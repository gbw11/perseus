"""공통 잠금·혼합 registry·Zephyr 생명주기·중단 복구. 명령 대역은 실제 Xen 성공 증거가 아니다."""
import ast
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, FILES, Board, assemble, contract, vm, platform_root
from test_xenova_platform import record, fixture, domain, FakeXen
from test_xenova_zephyr import bundle, refresh
import xenova_runtime as runtime
import xenova_zephyr as zephyr


def snapshot(directory):
    return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in directory.rglob('*') if p.is_file() and not p.is_symlink()}


def application_bundle(source, target):
    shutil.copytree(source, target)
    (target / 'template.json').unlink()
    binary = target / 'zephyr.bin'
    binary.chmod(0o644)
    binary.write_bytes(binary.read_bytes() + b'app-version-2')
    binary.chmod(0o444)
    (target / 'zephyr.version.h').write_text('#define KERNEL_VERSION_STRING "4.4.0"\n')
    (target / 'zephyr.version.h').chmod(0o444)
    meta = {'schema': 1, 'guest_kind': 'zephyr', 'version': 'app-2',
            'source': {'zephyr_version': '4.4.0', 'app': 'test-app', 'build_id': 'test-build',
                       'source_revision': 'test-revision'},
            'hardware_profile': zephyr.PROFILE, 'capabilities': zephyr.CAPABILITIES,
            'files': {name: {'sha256': zephyr.sha(target / name), 'bytes': (target / name).stat().st_size}
                      for name in zephyr.PAYLOADS | {'zephyr.version.h'}}}
    (target / 'firmware.json').write_text(json.dumps(meta))
    (target / 'firmware.json').chmod(0o444)
    return target


class Xen(FakeXen):
    def __call__(self, args, **kwargs):
        if args[0] == 'timedatectl':
            self.calls.append(args)
            return 'NTPSynchronized=no\nNTP=yes\nTimezone=UTC\n'
        result = super().__call__(args, **kwargs)
        if args[:2] == ['xl', 'create']:
            for row in self.rows:
                if row['config']['c_info']['uuid'] in [r['uuid'] for r in self.records.values() if vm.guest_kind(r) == 'zephyr']:
                    row['config']['disks'] = []
        return result


class LockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / 'absent/run/xenova'

    def test_missing_parent_created_and_lock_inode_survives_release_restart(self):
        with runtime.operation_lock(self.run):
            lock = self.run / 'locks/platform.lock'
            inode = lock.stat().st_ino
            (self.run / 'data-device').symlink_to('/dev/fixture')
        with runtime.operation_lock(self.run):
            self.assertEqual(lock.stat().st_ino, inode)
            self.assertTrue((self.run / 'data-device').is_symlink())
        self.assertTrue(lock.exists())

    def test_concurrent_process_refused_then_can_retry_after_holder_exits(self):
        code = ("import sys; from pathlib import Path; from xenova_runtime import operation_lock; "
                "ctx=operation_lock(Path(sys.argv[1])); ctx.__enter__(); print('locked',flush=True); "
                "sys.stdin.readline(); ctx.__exit__(None,None,None)")
        env = {**os.environ, 'PYTHONPATH': str(FILES), 'PYTHONDONTWRITEBYTECODE': '1'}
        holder = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.run)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'locked')
            with self.assertRaisesRegex(vm.PlatformError, 'another platform operation'):
                with runtime.operation_lock(self.run):
                    self.fail('second process acquired live lock')
            holder.communicate('\n', timeout=5)
            self.assertEqual(holder.returncode, 0)
            with runtime.operation_lock(self.run):
                pass
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.communicate()

    def test_interrupted_operation_releases_lock_without_deleting_runtime(self):
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with runtime.operation_lock(self.run):
                (self.run / 'boot-storage.json').write_text('{}')
                raise RuntimeError('interrupted')
        with runtime.operation_lock(self.run):
            self.assertTrue((self.run / 'boot-storage.json').exists())

    def test_symlinked_lock_or_runtime_refused(self):
        self.run.parent.mkdir(parents=True)
        other = self.run.parent / 'other'; other.mkdir()
        self.run.symlink_to(other, target_is_directory=True)
        with self.assertRaises(vm.PlatformError):
            with runtime.operation_lock(self.run):
                pass

    def test_early_and_cli_use_same_lock_without_tmpfiles_or_lifetime_cleanup(self):
        for name in ('xenova_first_boot.py', 'xenova_vm.py'):
            source = (FILES / name).read_text()
            self.assertIn('with operation_lock(', source)
            self.assertNotIn('/run/lock', source)
        for name in ('xenova-data-prepare.service', 'xenova-first-boot.service', 'xenova-vms.service'):
            source = (FILES / name).read_text()
            self.assertNotIn('RuntimeDirectory=', source)
            self.assertNotIn('systemd-tmpfiles-setup.service', source)


class ManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ('instances', 'templates', 'rtos-templates'):
            (self.base / name).mkdir()
        self.template = bundle(self.base / 'rtos-templates/zephyr-4.4.0-xenvm')
        self.original = snapshot(self.template)
        self.z = zephyr.new_instance(self.base, 'zephyr-dev', self.template, {})
        self.directory = self.base / 'instances/zephyr-dev'
        self.records = {self.z['id']: self.z}
        self.xen = Xen(self.base, self.records)
        self.ticks = 0
        self.p = vm.Platform(self.base, self.base / 'run/xenova', self.xen,
                             self.sleep, lambda: self.ticks)

    def sleep(self, seconds):
        self.ticks += seconds

    def add_linux(self):
        r = record()
        fixture(self.base, r)
        self.records[r['id']] = r
        return r

    def test_independent_assets_no_linux_identity_or_root_disk(self):
        vm.validate_record(self.z, self.directory)
        self.assertFalse((self.directory / 'root.raw').exists())
        for field in ('hostname', 'mac', 'ipv4', 'kernel_release'):
            self.assertNotIn(field, self.z)
        active = zephyr.active_directory(self.directory, self.z)
        for name in zephyr.PAYLOADS | {'template.json'}:
            self.assertNotEqual((active / name).stat().st_ino, (self.template / name).stat().st_ino)
        self.assertEqual(snapshot(self.template), self.original)
        self.assertFalse(self.z['capabilities']['guest_writable_persistent_storage'])

    def test_create_without_ip_uses_fixed_resources_and_does_not_start(self):
        from argparse import Namespace
        args = Namespace(id='zephyr-two', template=self.template.name, ipv4=None, memory_mib=None,
                         vcpus=None, autostart=False, execute=True)
        zephyr.new_instance(self.base, args.id, self.template, self.records)
        created = json.loads((self.base / 'instances/zephyr-two/instance.json').read_text())
        self.assertEqual((created['vcpus'], created['memory_mib']), (1, 16))
        self.assertNotEqual(created['uuid'], self.z['uuid'])
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_mixed_registry_reads_schema1_without_migration(self):
        linux = self.add_linux()
        path = self.base / 'instances' / linux['id'] / 'instance.json'
        before = path.read_bytes()
        records = vm.load_records(self.base)
        self.assertEqual(set(records), {'ubuntu-dev', 'zephyr-dev'})
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn('guest_kind', records['ubuntu-dev'])

    def test_duplicate_uuid_across_kinds_rejected(self):
        linux = self.add_linux()
        self.z['uuid'] = linux['uuid']
        (self.directory / 'instance.json').write_text(json.dumps(self.z))
        with self.assertRaisesRegex(vm.PlatformError, 'duplicate VM uuid'):
            vm.load_records(self.base)

    def test_duplicate_instance_id_or_generated_uuid_cannot_publish(self):
        with self.assertRaises(vm.PlatformError):
            zephyr.new_instance(self.base, 'zephyr-dev', self.template, self.records)
        with patch.object(zephyr, 'uuid4', return_value=self.z['uuid']):
            with self.assertRaisesRegex(vm.PlatformError, 'UUID/name'):
                zephyr.new_instance(self.base, 'other', self.template, self.records)
        self.assertFalse((self.base / 'instances/other').exists())

    def test_stopped_status_distinguishes_registered_domain_and_app(self):
        row = self.p.status(self.records)['instances'][0]
        self.assertTrue(row['registered'])
        self.assertEqual(row['xen_state'], 'stopped')
        self.assertEqual(row['app_readiness'], 'unknown')
        self.assertIsNone(row['domid'])
        self.assertFalse(self.p.status(self.records)['time']['synchronized'])

    def test_zephyr_config_has_no_linux_devices_or_pvh_management_kind(self):
        tree = ast.parse(self.p.config(self.z))
        assignments = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body}
        self.assertEqual(assignments['gic_version'], 'v2')
        self.assertEqual(assignments['memory'], 16)
        for field in ('disk', 'vif', 'ramdisk', 'extra', 'type'):
            self.assertNotIn(field, assignments)

    def test_linux_generated_disk_has_target_last(self):
        linux = self.add_linux()
        tree = ast.parse(self.p.config(linux))
        settings = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body}
        disk = settings['disk'][0]
        self.assertEqual(disk.split(',')[-1], 'target=' + str(self.base / 'instances/ubuntu-dev/root.raw'))
        self.assertTrue(disk.startswith('format=raw,vdev=xvda,access=rw,backendtype=qdisk,'))

    def test_start_twice_retains_uuid_firmware_policy_without_network(self):
        before = snapshot(self.directory)
        with patch.object(self.p, 'prepare_dhcp', side_effect=AssertionError('Zephyr requires no DHCP')):
            self.p.start(self.z)
            self.p.start(self.z)
        self.assertEqual(snapshot(self.directory), before)
        self.assertEqual(sum(c[:2] == ['xl', 'create'] for c in self.xen.calls), 1)
        self.assertEqual(snapshot(self.template), self.original)

    def test_same_live_name_different_uuid_refused_for_zephyr(self):
        other = {**self.z, 'uuid': 'bbbb0000-1111-4222-8333-123456789abc'}
        self.xen.rows = [domain(other, self.directory / 'unused')]
        with self.assertRaisesRegex(vm.PlatformError, 'different UUID'):
            self.p.start(self.z)

    def test_stop_without_force_refuses_without_shutdown_or_destroy(self):
        self.p.start(self.z)
        with self.assertRaisesRegex(vm.PlatformError, 'explicit --force'):
            self.p.stop_many([self.z])
        self.assertFalse(any(c[:2] in (['xl', 'shutdown'], ['xl', 'destroy']) for c in self.xen.calls))
        self.assertEqual(self.ticks, 0)

    def test_force_stop_and_reboot_preserve_assets_and_report_force(self):
        self.p.start(self.z)
        before = snapshot(self.directory)
        with self.assertRaisesRegex(vm.PlatformError, 'use reboot --force'):
            self.p.reboot(self.z)
        self.p.reboot(self.z, force=True)
        self.assertEqual(snapshot(self.directory), before)
        result = self.p.stop_many([self.z], force=True)
        self.assertEqual(result['forced'], ['zephyr-dev'])
        self.assertEqual(result['graceful_shutdown_requested'], [])
        self.assertEqual(snapshot(self.directory), before)

    def test_board_shutdown_preflight_does_not_partially_stop_linux(self):
        linux = self.add_linux()
        self.p.start(linux); self.p.start(self.z)
        with self.assertRaisesRegex(vm.PlatformError, 'explicit --force'):
            self.p.prepare_board_shutdown(list(self.records.values()), 'poweroff')
        self.assertFalse(any(c[:2] == ['xl', 'shutdown'] for c in self.xen.calls))
        self.assertFalse(any(c == ['systemctl', 'poweroff'] for c in self.xen.calls))
        self.assertFalse((self.p.runtime / 'shutdown-requested').exists())

    def test_board_shutdown_refuses_unknown_domain_even_when_force_requested(self):
        unknown = record('debian-dev')
        self.xen.rows = [domain(unknown, self.base / 'unknown')]
        with self.assertRaisesRegex(vm.PlatformError, 'unregistered'):
            self.p.prepare_board_shutdown([self.z], 'poweroff', force=True)
        self.assertFalse((self.p.runtime / 'shutdown-requested').exists())

    def test_explicit_board_force_records_policy_before_os_request(self):
        self.p.start(self.z)
        self.p.prepare_board_shutdown([self.z], 'poweroff', force=True)
        result = json.loads((self.p.runtime / 'board-shutdown-result.json').read_text())
        self.assertEqual(result['forced'], ['zephyr-dev'])
        self.assertEqual(result['board_action'], 'poweroff')
        self.assertFalse(any(c == ['systemctl', 'poweroff'] for c in self.xen.calls))

    def test_autostart_partial_failure_does_not_block_zephyr_or_hide_linux_failure(self):
        linux = self.add_linux()
        self.z['autostart'] = True
        with patch.object(self.p, 'prepare_dhcp', side_effect=vm.PlatformError('DHCP unavailable')):
            with self.assertRaisesRegex(vm.PlatformError, 'ubuntu-dev.*DHCP unavailable'):
                self.p.autostart(self.records, [])
        result = json.loads((self.p.runtime / 'autostart-results.json').read_text())
        self.assertEqual(result['zephyr-dev']['result'], 'running')
        self.assertEqual(result['ubuntu-dev']['result'], 'failed')
        self.assertTrue(self.p.matching(self.z, self.p.domains()))

    def test_dhcp_reservations_ignore_zephyr_fields(self):
        linux = self.add_linux()
        with patch.object(self.p, 'check_network'):
            self.p.prepare_dhcp(self.records)
        hosts = (self.p.runtime / 'dhcp-hosts').read_text()
        self.assertIn(linux['mac'], hosts)
        self.assertNotIn('zephyr', hosts)

    def test_default_autostart_off_and_no_automatic_restart_after_user_stop(self):
        with patch.object(self.p, 'prepare_dhcp', side_effect=AssertionError('not used')):
            self.p.autostart(self.records, [])
        self.assertFalse(self.xen.rows)
        self.p.start(self.z)
        self.p.stop_many([self.z], force=True)
        self.assertFalse(self.xen.rows)
        self.assertEqual(sum(c[:2] == ['xl', 'create'] for c in self.xen.calls), 1)

    def test_create_interruption_has_no_partial_visible_registration_and_can_retry(self):
        before = snapshot(self.template)
        with patch.object(zephyr.shutil, 'copyfile', side_effect=OSError('interrupted copy')):
            with self.assertRaisesRegex(OSError, 'interrupted copy'):
                zephyr.new_instance(self.base, 'retry', self.template, self.records)
        self.assertFalse((self.base / 'instances/retry').exists())
        zephyr.new_instance(self.base, 'retry', self.template, self.records)
        self.assertEqual(snapshot(self.template), before)
        vm.load_records(self.base)

    def test_firmware_replacement_switches_current_only_and_keeps_origin_uuid_policy(self):
        incoming = application_bundle(self.template, self.base / 'incoming')
        old = copy.deepcopy(self.z)
        new = zephyr.replace_firmware(self.directory, self.z, incoming, zephyr.sha(incoming / 'firmware.json'))
        for field in ('uuid', 'autostart', 'creation_origin', 'template'):
            self.assertEqual(new[field], old[field])
        self.assertNotEqual(new['active_firmware'], old['active_firmware'])
        self.assertTrue(zephyr.active_directory(self.directory, old).exists())
        self.assertEqual(vm.load_records(self.base)['zephyr-dev']['active_firmware'], new['active_firmware'])
        self.assertEqual(snapshot(self.template), self.original)

    def test_replacement_requires_stopped_instance_and_explicit_hash(self):
        incoming = application_bundle(self.template, self.base / 'incoming')
        self.p.start(self.z)
        with self.assertRaisesRegex(vm.PlatformError, 'stopped'):
            self.p.replace_firmware(self.z, incoming, zephyr.sha(incoming / 'firmware.json'), execute=True)
        self.p.stop_many([self.z], force=True)
        with self.assertRaises(vm.PlatformError):
            self.p.replace_firmware(self.z, incoming, None, execute=True)

    def test_failed_hash_or_interrupted_copy_preserves_active_firmware(self):
        incoming = application_bundle(self.template, self.base / 'incoming')
        before = (self.directory / 'instance.json').read_bytes()
        with self.assertRaisesRegex(vm.PlatformError, 'manifest hash'):
            zephyr.replace_firmware(self.directory, self.z, incoming, '0' * 64)
        with patch.object(zephyr.shutil, 'copyfile', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                zephyr.replace_firmware(self.directory, self.z, incoming, zephyr.sha(incoming / 'firmware.json'))
        self.assertEqual((self.directory / 'instance.json').read_bytes(), before)
        vm.validate_record(self.z, self.directory)

    def test_interruption_before_atomic_metadata_switch_preserves_old_and_retry_succeeds(self):
        incoming = application_bundle(self.template, self.base / 'incoming')
        before = (self.directory / 'instance.json').read_bytes()
        digest = zephyr.sha(incoming / 'firmware.json')
        with patch.object(zephyr, 'atomic_write', side_effect=OSError('interrupted switch')):
            with self.assertRaises(OSError):
                zephyr.replace_firmware(self.directory, self.z, incoming, digest)
        self.assertEqual((self.directory / 'instance.json').read_bytes(), before)
        updated = zephyr.replace_firmware(self.directory, self.z, incoming, digest)
        vm.validate_record(updated, self.directory)

    def test_hardware_irq_flags_mismatch_rejected_even_after_hash_refresh(self):
        path = self.template / 'zephyr.dts'
        path.chmod(0o644)
        path.write_text(path.read_text().replace('1 15 4 160', '1 15 2 160'))
        path.chmod(0o444)
        refresh(self.template)
        with self.assertRaisesRegex(vm.PlatformError, 'hardware profile mismatch'):
            zephyr.bundle_info(self.template)

    def test_delete_refuses_running_then_retires_only_selected_instance(self):
        linux = self.add_linux()
        before = snapshot(self.base / 'instances/ubuntu-dev')
        self.p.start(self.z)
        with patch.object(vm,'storage_check'),patch('xenova_management.Manager._usage',lambda *args:None):
            result=self.p.delete(self.z,execute=True,idempotency_key='retire-test')
        self.assertEqual(result['error']['code'],'VM_NOT_STOPPED',result)
        self.p.stop_many([self.z], force=True)
        with patch.object(vm,'storage_check'),patch('xenova_management.Manager._usage',lambda *args:None):
            result=self.p.delete(self.z,execute=True,idempotency_key='retire-test')
        self.assertTrue(result['ok'],result)
        self.assertFalse(self.directory.exists())
        self.assertTrue(vm.retired_instance(self.base, self.z['id'], self.z['uuid']))
        self.assertEqual(snapshot(self.base / 'instances/ubuntu-dev'), before)
        self.assertEqual(snapshot(self.template), self.original)

    def test_stopped_zephyr_stop_is_noop_without_claiming_graceful_request(self):
        result = self.p.stop_many([self.z])
        self.assertEqual(result['already_stopped'], ['zephyr-dev'])
        self.assertEqual(result['graceful_shutdown_requested'], [])
        self.assertEqual(result['forced'], [])

    def test_autostart_interruption_preserves_previously_completed_result(self):
        linux = self.add_linux()
        self.z['autostart'] = True
        def start(r):
            if r['id'] == 'ubuntu-dev':
                raise KeyboardInterrupt('injected service interruption')
            return 'running'
        with patch.object(self.p, 'start', side_effect=start), patch.object(self.p, 'prepare_dhcp'):
            with self.assertRaises(KeyboardInterrupt):
                self.p.autostart(self.records, [])
        results = json.loads((self.p.runtime / 'autostart-results.json').read_text())
        self.assertEqual(results['zephyr-dev']['result'], 'running')
        self.assertEqual(results['ubuntu-dev']['result'], 'starting')

    def test_cli_policy_and_config_persist_without_starting_or_requiring_network(self):
        with patch.object(vm, 'Platform', return_value=self.p), patch.object(self.p, 'records', return_value=self.records):
            vm.main(['autostart-set', 'zephyr-dev', 'on'])
            vm.main(['config', 'zephyr-dev'])
        current = json.loads((self.directory / 'instance.json').read_text())
        self.assertTrue(current['autostart'])
        self.assertEqual(current['uuid'], self.z['uuid'])
        self.assertTrue((self.p.runtime / 'zephyr-dev.cfg').exists())
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_cli_console_uses_verified_pv_domid_and_releases_operation_lock(self):
        self.p.start(self.z)
        calls = []
        def console(binary, args):
            with runtime.operation_lock(self.p.runtime):
                calls.append((binary, args))
        with patch.object(vm, 'Platform', return_value=self.p), patch.object(self.p, 'records', return_value=self.records), patch.object(vm.os, 'execvp', side_effect=console):
            vm.main(['console', 'zephyr-dev'])
        self.assertEqual(calls, [('xl', ['xl', 'console', '-t', 'pv', '1'])])

    def test_cli_start_zephyr_skips_dhcp_and_stop_requires_explicit_force(self):
        with patch.object(vm, 'Platform', return_value=self.p), patch.object(self.p, 'records', return_value=self.records), patch.object(self.p, 'check_xen'), patch.object(self.p, 'prepare_dhcp', side_effect=AssertionError('not used')):
            vm.main(['start', 'zephyr-dev'])
            with self.assertRaisesRegex(vm.PlatformError, 'explicit --force'):
                vm.main(['stop', 'zephyr-dev'])
            vm.main(['stop', 'zephyr-dev', '--force'])
        self.assertFalse(self.xen.rows)

    def test_autostart_bad_firmware_is_individual_failure_and_linux_still_runs(self):
        linux = self.add_linux()
        self.z['autostart'] = True
        (self.directory / 'instance.json').write_text(json.dumps(self.z))
        binary = zephyr.active_directory(self.directory, self.z) / 'zephyr.bin'
        binary.chmod(0o644); binary.write_bytes(b'corrupt'); binary.chmod(0o444)
        records = vm.load_records(self.base, validate_assets=False)
        with patch.object(self.p, 'prepare_dhcp'):
            with self.assertRaisesRegex(vm.PlatformError, 'zephyr-dev'):
                self.p.autostart(records, [])
        result = json.loads((self.p.runtime / 'autostart-results.json').read_text())
        self.assertEqual(result['ubuntu-dev']['result'], 'running')
        self.assertEqual(result['zephyr-dev']['result'], 'failed')
        self.assertTrue(self.p.matching(linux, self.p.domains()))

    def test_missing_default_is_reported_without_preventing_other_guests(self):
        self.z['autostart'] = True
        with self.assertRaisesRegex(vm.PlatformError, 'not provisioned'):
            self.p.autostart(self.records, ['zephyr-dev', 'missing'])
        results = json.loads((self.p.runtime / 'autostart-results.json').read_text())
        self.assertEqual(results['missing']['result'], 'failed')
        self.assertEqual(results['zephyr-dev']['result'], 'running')


class FactoryAndPackagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def board(self, firmware=True):
        b = Board(self.base / 'board')
        b.grow_mount()
        factory = b.factory()
        if firmware:
            factory['features'] = contract.FEATURES
            template = bundle(self.base / 'source-template')
            entry = assemble.rtos.info(template, assemble.sha(template / 'template.json'))
            factory['rtos_templates'] = assemble.copy_rtos_templates(b.data, [entry])
            seed = zephyr.new_instance(b.data, 'zephyr-dev', b.data / 'rtos-templates' / entry['id'], {},
                                       registry='seeds', seed=True, autostart=False, board_shutdown_policy='refuse')
            factory['firmware_instances'] = [{'record': seed}]
            (b.data / '.xenova-install.json').write_text(json.dumps(factory))
        return b

    def test_new_factory_registers_three_and_rerun_preserves_policy_and_assets(self):
        b = self.board()
        b.p.register()
        path = b.data / 'instances/zephyr-dev/instance.json'
        r = json.loads(path.read_text())
        self.assertFalse(r['autostart'])
        r['autostart'] = True; path.write_text(json.dumps(r))
        before = snapshot(b.data / 'instances')
        b.p.register()
        self.assertEqual(snapshot(b.data / 'instances'), before)
        self.assertEqual(set(vm.load_records(b.data)), {'ubuntu-dev', 'debian-dev', 'zephyr-dev'})

    def test_completed_factory_asset_error_is_recorded_without_blocking_other_instances(self):
        b = self.board(); b.p.register()
        path = b.data / 'instances/zephyr-dev/instance.json'
        r = json.loads(path.read_text())
        binary = zephyr.active_directory(path.parent, r) / 'zephyr.bin'
        binary.chmod(0o644); binary.write_bytes(b'broken'); binary.chmod(0o444)
        b.p.register()
        ready = json.loads((b.p.run / 'install-ready.json').read_text())
        self.assertIn('zephyr-dev', ready['asset_errors'])
        records = vm.load_records(b.data, validate_assets=False)
        self.assertEqual(set(records), {'ubuntu-dev', 'debian-dev', 'zephyr-dev'})
        with self.assertRaises(vm.PlatformError):
            vm.validate_record(r, path.parent)

    def test_old_factory_features_do_not_add_zephyr_or_reinitialize_linux(self):
        b = self.board(firmware=False)
        factory = json.loads((b.data / '.xenova-install.json').read_text())
        factory['features'] = contract.LEGACY_FEATURES
        (b.data / '.xenova-install.json').write_text(json.dumps(factory))
        b.p.register()
        before = snapshot(b.data / 'instances')
        b.p.register()
        self.assertEqual(snapshot(b.data / 'instances'), before)
        self.assertFalse((b.data / 'instances/zephyr-dev').exists())

    def test_existing_linux_install_can_add_zephyr_without_reinitialization_on_next_boot(self):
        b = self.board(firmware=False); b.p.register()
        before = snapshot(b.data / 'instances')
        template = bundle(self.base / 'source-template')
        entry = assemble.rtos.info(template, assemble.sha(template / 'template.json'))
        assemble.copy_rtos_templates(b.data, [entry])
        r = zephyr.new_instance(b.data, 'zephyr-team', b.data / 'rtos-templates' / entry['id'],
                                vm.load_records(b.data), autostart=True)
        b.p.register()
        after = snapshot(b.data / 'instances')
        for key, value in before.items():
            self.assertEqual(after[key], value)
        current = json.loads((b.data / 'instances/zephyr-team/instance.json').read_text())
        self.assertEqual(current, r)
        self.assertTrue(current['autostart'])

    def test_new_factory_cannot_claim_zephyr_support_without_seed(self):
        b = self.board(firmware=False)
        path = b.data / '.xenova-install.json'
        factory = json.loads(path.read_text()); factory['features'] = contract.FEATURES
        path.write_text(json.dumps(factory))
        with self.assertRaisesRegex(ValueError, 'requires Zephyr firmware seeds'):
            b.p.register()
        self.assertFalse((b.data / '.first-install-state.json').exists())

    def test_new_factory_registration_interruption_retries_planned_uuids(self):
        b = self.board()
        save = b.p.save
        planned = {}
        def interrupted(path, value):
            save(path, value)
            if path.name == '.first-install-state.json' and value['phase'] == 'identities-planned':
                planned.update(value['identities'])
                raise OSError('interrupted identity save')
        with patch.object(b.p, 'save', side_effect=interrupted):
            with self.assertRaises(OSError):
                b.p.register()
        b.p.register()
        for key, identity in planned.items():
            r = json.loads((b.data / 'instances' / key / 'instance.json').read_text())
            self.assertEqual(r['uuid'], identity['uuid'])

    def test_factory_verifier_accepts_firmware_replacement_without_resetting_origin(self):
        b = self.board(); b.p.register()
        directory = b.data / 'instances/zephyr-dev'
        r = json.loads((directory / 'instance.json').read_text())
        template = b.data / 'rtos-templates/zephyr-4.4.0-xenvm'
        incoming = application_bundle(template, self.base / 'incoming')
        updated = zephyr.replace_firmware(directory, r, incoming, zephyr.sha(incoming / 'firmware.json'))
        b.p.register()
        self.assertEqual(json.loads((directory / 'instance.json').read_text()), updated)

    def test_factory_does_not_recreate_explicitly_retired_default(self):
        b = self.board(); b.p.register()
        r = json.loads((b.data / 'instances/zephyr-dev/instance.json').read_text())
        p = vm.Platform(b.data, b.p.run, Xen(b.data, {r['id']: r}))
        with patch.object(vm,'storage_check'),patch('xenova_management.Manager._usage',lambda *args:None):
            result=p.delete(r,execute=True,idempotency_key='factory-retire')
        self.assertTrue(result['ok'],result)
        b.p.register()
        self.assertFalse((b.data / 'instances/zephyr-dev').exists())
        self.assertEqual(len(vm.load_records(b.data)), 2)

    def test_old_or_changed_module_and_qemu_configuration_are_rejected(self):
        root = platform_root(self.base / 'root')
        assemble.platform_root_ok(root)
        module = root / 'usr/lib/xenova/xenova_zephyr.py'
        saved = module.read_bytes()
        module.write_bytes(b'old version')
        with self.assertRaisesRegex(ValueError, 'source/installed contract mismatch'):
            assemble.platform_root_ok(root)
        module.write_bytes(saved)
        (root / 'usr/share/xenova/qemu-config-host.h').write_text('#undef CONFIG_VNC\n#define CONFIG_XEN_BACKEND 1\n')
        with self.assertRaisesRegex(ValueError, 'built QEMU'):
            assemble.platform_root_ok(root)

    def test_new_runtime_dependencies_and_single_autostart_owner_are_declared(self):
        recipe = (FILES.parent / 'xenova-platform_1.0.bb').read_text()
        for module in ('xenova_runtime.py', 'xenova_zephyr.py'):
            self.assertIn('file://' + module, recipe)
            self.assertIn(module, recipe)
        for package in ('python3-crypt', 'iproute2-ss', 'util-linux-lsblk'):
            self.assertIn(package, recipe)
        service = (FILES / 'xenova-vms.service').read_text()
        self.assertNotIn('xenova-dhcp.service', service)
        self.assertIn('Restart=no', service)

    def test_unsynchronized_or_unavailable_time_does_not_block_lifecycle(self):
        p = vm.Platform(command=lambda *a, **k: 'NTPSynchronized=no\n')
        self.assertFalse(p.time_status()['synchronized'])
        p.command = lambda *a, **k: (_ for _ in ()).throw(vm.PlatformError('no time service'))
        self.assertIsNone(p.time_status()['synchronized'])
        self.assertEqual(p.time_status()['interval_clock'], 'monotonic')


class FirmwareToolsTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        def module(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            result = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(result)
            return result
        self.package = module('package_firmware_test', ROOT / 'scripts/package-xenova-firmware.py')
        self.verify = module('verify_runtime_test', ROOT / 'scripts/verify-xenova-runtime-artifacts.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.template = bundle(self.base / 'template')

    def build(self):
        build = self.base / 'build'
        shutil.copytree(self.template, build)
        (build / 'zephyr.config').rename(build / '.config')
        header = build / 'include/generated/zephyr/version.h'
        header.parent.mkdir(parents=True)
        header.write_text('#define KERNEL_VERSION_STRING "4.4.0"\n')
        return build

    def args(self, build):
        return ['--build-dir', str(build), '--license', str(self.template / 'LICENSE'),
                '--version', 'app-2', '--app', 'test-app', '--source-revision', 'test-revision',
                '--build-id', 'test-build', '--output', str(self.base / 'output')]

    def test_application_package_plan_is_read_only_and_execute_creates_new_bundle(self):
        build = self.build()
        before = snapshot(build)
        self.package.main(self.args(build))
        self.assertFalse((self.base / 'output').exists())
        self.assertEqual(snapshot(build), before)
        self.package.main(self.args(build) + ['--execute'])
        result = zephyr.bundle_info(self.base / 'output')
        self.assertEqual(result['version'], 'app-2')
        self.assertEqual(result['manifest'], 'firmware.json')
        self.assertEqual(snapshot(build), before)
        with self.assertRaises(ValueError):
            self.package.main(self.args(build) + ['--execute'])

    def test_wrong_build_version_cannot_be_packaged(self):
        build = self.build()
        (build / 'include/generated/zephyr/version.h').write_text('#define KERNEL_VERSION_STRING "4.3.0"\n')
        with self.assertRaisesRegex(ValueError, 'Zephyr 4.4.0'):
            self.package.main(self.args(build) + ['--execute'])
        self.assertFalse((self.base / 'output').exists())

    def test_xl_parsed_disk_contract_rejects_absorbed_path_or_wrong_backend(self):
        r = record()
        directory = self.base / 'instances' / r['id']
        parsed = {'c_info': {'uuid': r['uuid'], 'name': r['name']},
                  'b_info': {'target_memkb': r['memory_mib'] * 1024, 'max_vcpus': r['vcpus']},
                  'disks': [{'pdev_path': str(directory / 'root.raw'), 'format': 'raw',
                             'vdev': 'xvda', 'readwrite': 1, 'backend': 'qdisk'}],
                  'nics': [{'mac': r['mac']}]}
        self.assertTrue(self.verify.parsed_config(r, parsed, directory))
        for field, value in [('pdev_path', str(directory / 'root.raw') + ',format=raw,vdev=xvda'),
                             ('backend', 'phy'), ('vdev', ''), ('readwrite', 0)]:
            changed = copy.deepcopy(parsed); changed['disks'][0][field] = value
            with self.assertRaises(vm.PlatformError):
                self.verify.parsed_config(r, changed, directory)

    def test_verifier_uses_unmodified_file_and_never_claims_guest_boot(self):
        parsed = {'c_info': {}, 'disks': [], 'nics': []}
        manifest = self.base / 'packages.manifest'
        manifest.write_text('xen-tools-xl cortexa72 4.21+stable0+1c72306b1f\nqemu-system-i386 cortexa72 10.2.0\n')
        r = record(); instance = self.base / 'instance.json'; instance.write_text(json.dumps(r))
        config = self.base / 'generated.cfg'; config.write_text('fixture, must pass unchanged')
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            return json.dumps(parsed)
        with patch.object(self.verify, 'run', side_effect=command), patch.object(self.verify, 'parsed_config'), patch.object(self.verify, 'qemu_checks', return_value={'integration_boot_tested': False}), patch.object(self.verify.shutil, 'which', return_value=str(config)):
            output = io.StringIO()
            with redirect_stdout(output):
                self.verify.main(['--config', str(config), '--instance-json', str(instance), '--package-manifest', str(manifest)])
        self.assertEqual(calls, [['xl', '-N', 'create', str(config)]])
        result = json.loads(output.getvalue())
        self.assertTrue(result['xl_parser_passed'])
        self.assertIn('guest DTB generation', result['not_tested'])
        self.assertFalse(result['qemu_checks']['integration_boot_tested'])

    def test_new_bundle_profile_cannot_claim_guest_storage_or_graceful_shutdown(self):
        incoming = application_bundle(self.template, self.base / 'incoming')
        path = incoming / 'firmware.json'
        meta = json.loads(path.read_text()); meta['capabilities']['graceful_shutdown'] = True
        path.chmod(0o644); path.write_text(json.dumps(meta)); path.chmod(0o444)
        with self.assertRaisesRegex(vm.PlatformError, 'profile/capabilities'):
            zephyr.bundle_info(incoming)



if __name__ == '__main__':
    unittest.main()
