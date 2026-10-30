"""실제 Xen/마운트/이미지 생성 없이 영구 VM 계약과 실패 분기를 재현한다.

root.raw 등은 몇 바이트의 일반 파일 fixture다. 기존 디스크나 빌드 환경을 수정하지 않는다.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from xenova_sysfs_fixture import XenSysfs

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vm = load('xenova_vm_test', FILES / 'xenova_vm.py')
sys.path.insert(0, str(ROOT / 'platform/guests/common'))
guest = load('prepare_rootfs_test', ROOT / 'platform/guests/common/prepare_rootfs.py')
prepare = load('prepare_guests_test', ROOT / 'platform/scripts/prepare-xenova-guests.py')


def record(key='ubuntu-dev'):
    rows = json.loads((ROOT / 'platform/tests/fixtures/legacy-defaults.json').read_text())['instances']
    r = next(copy.deepcopy(row) for row in rows if row['id'] == key)
    r.update(schema=1, uuid='aaaa0000-1111-4222-8333-123456789abc', kernel_release='6.8.0-test')
    if key == 'debian-dev':
        r['uuid'] = 'bbbb0000-1111-4222-8333-123456789abc'
    return r


def fixture(base, r):
    d = base / 'instances' / r['id']
    d.mkdir(parents=True)
    for filename in ['root.raw', 'Image', 'initrd']:
        (d / filename).write_bytes(b'fixture: not an image')
    digest = hashlib.sha256((d / 'Image').read_bytes()).hexdigest()
    (d / 'boot.json').write_text(json.dumps({'kernel_release': r['kernel_release'], 'root_device': '/dev/xvda', 'kernel_sha256': digest, 'initrd_sha256': digest}))
    (d / 'instance.json').write_text(json.dumps(r))
    return d


def domain(r, path, domid=1):
    return {'domid': domid, 'config': {'c_info': {'uuid': r['uuid'], 'name': r['name']}, 'disks': [{'pdev_path': str(path)}]}}


class FakeXen:
    def __init__(self, base, records):
        self.base, self.records, self.rows, self.calls = base, records, [], []
        self.hang_shutdown = False
        self.fail_create = False
        self.incarnations = {}
        self.next_incarnation = 1

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ['xenova-domain-identity', '--domid']:
            domid = int(args[2])
            rows = [row for row in self.rows if row['domid'] == domid]
            if len(rows) != 1:
                raise vm.PlatformError('domain incarnation is unavailable')
            previous = self.incarnations.get(domid)
            if previous is None or previous[0] is not rows[0]:
                self.incarnations[domid] = (rows[0], self.next_incarnation)
                self.next_incarnation += 1
            return json.dumps({'domid':domid, 'xen_unique_id':str(self.incarnations[domid][1]), 'state_bits':1})
        if args == ['xl', 'list', '-l']:
            return json.dumps(self.rows)
        if args == ['xl', 'list', '-v']:
            # 고정 xl_info.c의 verbose 출력. UUID를 실행 인자로 허용하지 않는다.
            return ('Name ID Mem VCPUs State Time(s) UUID Reason-Code Security Label\n'
                    'Domain-0 0 1024 4 r----- 0.0 00000000-0000-0000-0000-000000000000 - -\n'
                    + ''.join(f"{r['config']['c_info']['name']} {r['domid']} 512 1 "
                              f"{getattr(self, 'states', {}).get(r['domid'], 'r-----')} 0.0 "
                              f"{r['config']['c_info']['uuid']} - -\n" for r in self.rows))
        if args == ['xl', 'list']:
            return 'Name ID Mem VCPUs State Time(s)\nDomain-0 0 1024 4 r----- 0\n' + ''.join(f"{r['config']['c_info']['name']} {r['domid']} 512 1 r----- 0\n" for r in self.rows)
        if args[:2] == ['xl', 'create']:
            if self.fail_create:
                raise vm.PlatformError('injected xl create failure')
            key = Path(args[2]).stem
            self.rows.append(domain(self.records[key], self.base / 'instances' / key / 'root.raw', len(self.rows) + 1))
        if args[:2] in (['xl', 'shutdown'], ['xl', 'reboot'], ['xl', 'destroy']):
            target = args[2]
            rows = [r for r in self.rows if str(r['domid']) == target or r['config']['c_info']['name'] == target]
            if len(rows) != 1:
                raise vm.PlatformError('invalid domain identifier (UUID is not an xl execution identifier): ' + target)
            row = rows[0]
            if args[1] == 'destroy' or (args[1] == 'shutdown' and not self.hang_shutdown):
                self.rows.remove(row)
            elif args[1] == 'reboot':
                row['domid'] += 10
        return ''


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xenova-contract-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.r = record()
        self.directory = fixture(self.base, self.r)
        (self.base / '.xenova-storage.json').write_text(json.dumps(
            {'schema':1, 'uuid':'12345678-1234-4234-8234-123456789abc'}))
        mountinfo = self.base / 'mountinfo'
        mountinfo.write_text(f'1 0 8:3 / {self.base} rw - ext4 /dev/fixture rw\n')
        # Direct start now rechecks the real registry at its physical guard. Supply
        # the small fixture's mount evidence while retaining storage_check logic.
        real_storage_check = vm.storage_check
        storage = patch.object(vm, 'storage_check', side_effect=lambda data: real_storage_check(data, mountinfo))
        storage.start()
        self.addCleanup(storage.stop)
        self.records = {self.r['id']: self.r}
        self.runner = FakeXen(self.base, self.records)
        self.ticks = 0
        self.p = vm.Platform(self.base, self.base / 'run', self.runner, self.sleep, lambda: self.ticks)

    def sleep(self, seconds):
        self.ticks += seconds

    def test_start_twice_reuses_disk_without_duplicate_domain(self):
        before = (self.directory / 'root.raw').read_bytes()
        self.p.start(self.r)
        self.p.start(self.r)
        self.assertEqual(sum(call[:2] == ['xl', 'create'] for call in self.runner.calls), 1)
        self.assertEqual((self.directory / 'root.raw').read_bytes(), before)
        config = (self.base / 'run/ubuntu-dev.cfg').read_text()
        self.assertIn('backendtype=qdisk', config)
        self.assertIn('net.ifnames=0', config)
        self.assertIn("on_crash = 'preserve'", config)
        self.assertIn("on_poweroff = 'destroy'", config)

    def test_missing_disk_never_created(self):
        disk = self.directory / 'root.raw'
        disk.unlink()
        with patch.object(vm, 'storage_check'):
            with self.assertRaises(vm.PlatformError):
                self.p.records()
        self.assertFalse(disk.exists())

    def test_different_live_uuid_with_same_name_is_rejected(self):
        other = copy.deepcopy(self.r)
        other['uuid'] = record('debian-dev')['uuid']
        self.runner.rows = [domain(other, self.directory / 'root.raw')]
        with self.assertRaisesRegex(vm.PlatformError, 'different UUID'):
            self.p.start(self.r)

    def test_disk_already_attached_to_other_domain_rejected(self):
        self.runner.rows = [domain(record('debian-dev'), self.directory / 'root.raw')]
        with self.assertRaisesRegex(vm.PlatformError, 'already attached'):
            self.p.start(self.r)

    def test_registry_hardlinked_writable_disk_rejected(self):
        other = fixture(self.base, record('debian-dev'))
        (other / 'root.raw').unlink()
        os.link(self.directory / 'root.raw', other / 'root.raw')
        with patch.object(vm, 'storage_check'):
            with self.assertRaisesRegex(vm.PlatformError, 'same writable disk'):
                self.p.records()

    def test_instance_cannot_alias_template_disk(self):
        template = self.base / 'templates' / self.r['template']
        template.mkdir(parents=True)
        os.link(self.directory / 'root.raw', template / 'root.raw')
        with patch.object(vm, 'storage_check'):
            with self.assertRaisesRegex(vm.PlatformError, 'aliases immutable template'):
                self.p.records()

    def test_registry_duplicate_mac_rejected(self):
        other = record('debian-dev')
        other['mac'] = self.r['mac']
        fixture(self.base, other)
        with patch.object(vm, 'storage_check'):
            with self.assertRaisesRegex(vm.PlatformError, 'duplicate VM mac'):
                self.p.records()

    def test_symlink_disk_rejected(self):
        disk = self.directory / 'root.raw'
        disk.unlink()
        disk.symlink_to(self.directory / 'Image')
        with self.assertRaises(vm.PlatformError):
            vm.validate_record(self.r, self.directory)

    def test_changed_kernel_is_rejected(self):
        (self.directory / 'Image').write_bytes(b'changed')
        with self.assertRaisesRegex(vm.PlatformError, 'digest mismatch'):
            vm.validate_record(self.r, self.directory)

    def test_stop_is_graceful_and_does_not_restart(self):
        self.p.start(self.r)
        before = (self.directory / 'root.raw').read_bytes()
        with patch.object(vm.os, 'sync'):
            self.p.stop_many([self.r])
        self.assertEqual(self.runner.rows, [])
        self.assertEqual((self.directory / 'root.raw').read_bytes(), before)
        self.assertFalse(any(c[:2] == ['xl', 'destroy'] for c in self.runner.calls))
        self.assertEqual(sum(c[:2] == ['xl', 'create'] for c in self.runner.calls), 1)

    def test_shutdown_timeout_never_destroys(self):
        self.p.start(self.r)
        self.runner.hang_shutdown = True
        with self.assertRaisesRegex(vm.PlatformError, 'timed out'):
            self.p.stop_many([self.r], timeout=2)
        self.assertEqual(len(self.runner.rows), 1)
        self.assertFalse(any(c[:2] == ['xl', 'destroy'] for c in self.runner.calls))

    def test_reboot_keeps_uuid_and_disk(self):
        self.p.start(self.r)
        before = copy.deepcopy(self.runner.rows[0])
        self.p.reboot(self.r)
        after = self.runner.rows[0]
        self.assertNotEqual(before['domid'], after['domid'])
        self.assertEqual(before['config'], after['config'])

    def test_autostart_false_not_started(self):
        self.r['autostart'] = False
        with patch.object(self.p, 'prepare_dhcp'):
            self.p.autostart(self.records, ['ubuntu-dev'])
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.runner.calls))

    def test_missing_default_reports_failure(self):
        with self.assertRaisesRegex(vm.PlatformError, 'not provisioned'):
            self.p.autostart(self.records, ['ubuntu-dev', 'debian-dev'])

    def test_failed_create_is_not_reported_as_ready(self):
        self.runner.fail_create = True
        with patch.object(self.p, 'prepare_dhcp'):
            with self.assertRaisesRegex(vm.PlatformError, 'injected xl create failure'):
                self.p.autostart(self.records, ['ubuntu-dev'])

    def test_dhcp_not_active_prevents_create(self):
        def failing(args, **kwargs):
            if 'is-active' in args:
                raise vm.PlatformError('DHCP inactive')
            return self.runner(args, **kwargs)
        self.p.command = failing
        with patch.object(self.p, 'check_network'):
            with self.assertRaisesRegex(vm.PlatformError, 'DHCP inactive'):
                self.p.prepare_dhcp(self.records)
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.runner.calls))

    def test_shutdown_marker_blocks_start(self):
        self.p.runtime.mkdir()
        (self.p.runtime / 'shutdown-requested').touch()
        with self.assertRaisesRegex(vm.PlatformError, 'shutdown is in progress'):
            self.p.start(self.r)

    def test_board_shutdown_timeout_does_not_request_poweroff(self):
        self.p.start(self.r)
        self.runner.hang_shutdown = True
        with patch.object(self.p, 'stop_many', side_effect=vm.PlatformError('timeout')):
            with self.assertRaises(vm.PlatformError):
                self.p.prepare_board_shutdown([self.r], 'poweroff')
        self.assertFalse((self.p.runtime / 'shutdown-requested').exists())
        self.assertFalse(any(c == ['systemctl', 'poweroff'] for c in self.runner.calls))

    def test_unmount_failure_never_requests_poweroff(self):
        self.p.prepare_board_shutdown([self.r], 'poweroff')
        def failing(args, **kwargs):
            if args == ['systemctl', 'stop', 'srv-xenova.mount']:
                raise vm.PlatformError('unmount failed')
            return self.runner(args, **kwargs)
        self.p.command = failing
        with self.assertRaises(vm.PlatformError):
            self.p.finish_board_shutdown('poweroff')
        self.assertFalse(any(c == ['systemctl', 'poweroff'] for c in self.runner.calls))

    def test_board_shutdown_orders_storage_before_poweroff(self):
        self.p.prepare_board_shutdown([self.r], 'poweroff')
        self.p.finish_board_shutdown('poweroff')
        calls = self.runner.calls
        self.assertLess(calls.index(['systemctl', 'stop', 'srv-xenova.mount']), calls.index(['systemctl', 'poweroff']))

    def test_non_xen_capability_blocks_start_gate(self):
        with patch.object(vm.Path, 'read_text', return_value=''):
            with self.assertRaisesRegex(vm.PlatformError, 'not Xen dom0'):
                self.p.check_xen()


class StorageAndNetworkTest(unittest.TestCase):
    def test_dhcp_process_exit_is_explicit_failure(self):
        with patch.object(vm.Path, 'exists', return_value=False):
            with self.assertRaisesRegex(vm.PlatformError, 'dnsmasq exited'):
                vm.dhcp_ready(99999)

    def test_missing_mount_does_not_create_any_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            info = base / 'mountinfo'
            info.write_text('20 1 8:1 / / rw - ext4 /dev/sda rw\n')
            with self.assertRaisesRegex(vm.PlatformError, 'not mounted'):
                vm.storage_check(base / 'missing', info)
            self.assertFalse((base / 'missing').exists())

    def test_readonly_storage_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            info = base / 'mountinfo'
            info.write_text(f'20 1 8:1 / {base} ro - ext4 /dev/sda ro\n')
            with self.assertRaisesRegex(vm.PlatformError, 'writable'):
                vm.storage_check(base, info)

    def test_physical_bridge_member_refuses_dhcp(self):
        with tempfile.TemporaryDirectory() as temp:
            net = Path(temp)
            (net / 'xenbr0/bridge').mkdir(parents=True)
            (net / 'xenbr0/brif/eth0').mkdir(parents=True)
            with self.assertRaisesRegex(vm.PlatformError, 'DHCP refused'):
                vm.Platform().check_network(net)

    def test_vif_only_bridge_and_static_ip_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            sysfs = XenSysfs(Path(temp))
            sysfs.add_port('vif42.0')
            cmd = lambda args: json.dumps([{'addr_info': [{'local': '192.168.77.1', 'prefixlen': 24}]}])
            vm.Platform(command=cmd).check_network(sysfs.net)

    def test_wrong_bridge_address_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            net = Path(temp)
            (net / 'xenbr0/bridge').mkdir(parents=True)
            (net / 'xenbr0/brif').mkdir()
            with self.assertRaisesRegex(vm.PlatformError, '192.168.77.1'):
                vm.Platform(command=lambda args: '[]').check_network(net)


class GuestPreparationTest(unittest.TestCase):
    def root(self, base):
        for path in ['etc/ssh', 'etc/cloud', 'etc/systemd/system', 'usr/lib/systemd/system']:
            (base / path).mkdir(parents=True, exist_ok=True)
        (base / 'etc/shadow').write_text('root:!:19000:0:99999:7:::\n')
        (base / 'etc/ssh/sshd_config').write_text('Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication no\n')
        for unit in ['systemd-networkd.service', 'ssh.service']:
            (base / 'usr/lib/systemd/system' / unit).write_text('[Service]\n')

    def test_initial_auth_and_identity_are_not_reapplied(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.root(base)
            guest.prepare_template(base)
            guest.prepare_instance(base, record(), command=lambda *a, **k: None)
            machine = (base / 'etc/machine-id').read_text()
            (base / 'etc/shadow').write_text('root:USER_CHANGED:19001:0:99999:7:::\n')
            with self.assertRaisesRegex(ValueError, 'existing instance'):
                guest.prepare_instance(base, record(), command=lambda *a, **k: None)
            with self.assertRaises(ValueError):
                guest.prepare_template(base)
            self.assertIn('USER_CHANGED', (base / 'etc/shadow').read_text())
            self.assertEqual(machine, (base / 'etc/machine-id').read_text())
            self.assertEqual((base / 'etc/ssh/sshd_config').read_text().splitlines()[0], 'Include /etc/ssh/xenova-auth.conf')
            self.assertIn('PermitEmptyPasswords no', (base / 'etc/ssh/xenova-auth.conf').read_text())

    def test_cloned_instances_get_distinct_identity_and_key_commands(self):
        ids, targets = [], []
        with tempfile.TemporaryDirectory() as temp:
            for index in range(2):
                base = Path(temp) / str(index)
                self.root(base)
                guest.prepare_template(base)
                guest.prepare_instance(base, record(), command=lambda a, **k: targets.append(a[-1]))
                ids.append((base / 'etc/machine-id').read_text())
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(len(set(targets)), 6)

    def test_kernel_contract_rejects_missing_frontend(self):
        with self.assertRaises(ValueError):
            prepare.kernel_config_ok('CONFIG_XEN=y\n')

    def test_plan_does_not_call_any_mutation(self):
        with patch.object(prepare, 'make_template') as mutate:
            prepare.main(['template', '--source', 'ubuntu-24.04-20260926', '--input', '/nonexistent/input', '--output', '/nonexistent/output'])
            mutate.assert_not_called()

    def test_existing_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'existing'
            p.write_text('keep')
            with self.assertRaises(ValueError):
                prepare.require_new_output(p)
            self.assertEqual(p.read_text(), 'keep')

    def test_service_order_and_single_start_owner(self):
        startup = (FILES / 'xenova-vms.service').read_text()
        shutdown = (FILES / 'xenova-vm-shutdown.service').read_text()
        self.assertIn('Restart=no', startup)
        self.assertIn('ExecStop=/usr/sbin/xenova-vm shutdown-all', startup)
        self.assertIn('TimeoutStopSec=110s', startup)
        # Startup owns the libxl/qdisk cgroup; its ExecStop precedes systemd cleanup.
        self.assertNotIn('KillMode=none', startup)
        after=next(line for line in shutdown.splitlines() if line.startswith('After='))
        self.assertIn('xen-qemu-dom0-disk-backend.service',after.split())
        self.assertIn('xenova-vm-shutdown.service', startup)
        self.assertIn('ExecStart=/bin/true', shutdown)
        self.assertIn('ExecStop=/usr/sbin/xenova-vm shutdown-all', shutdown)
        self.assertNotIn('network-online.target', startup + shutdown)
        image = (ROOT / 'platform/yocto/meta-xenova/recipes-core/images/xenova-xen-bringup-image.bb').read_text()
        self.assertIn('ln -snf /dev/null', image)
        self.assertIn('xendomains.service', image)

    def test_default_linux_registry_is_debian_and_keeps_unique_network_identity(self):
        defaults = json.loads((FILES / 'defaults.json').read_text())['instances']
        self.assertEqual({r['id'] for r in defaults}, {'debian-dev'})
        self.assertTrue(all(r['autostart'] for r in defaults))
        self.assertEqual(len({r['mac'] for r in defaults}), 1)
        self.assertEqual(len({r['ipv4'] for r in defaults}), 1)


if __name__ == '__main__':
    unittest.main()
