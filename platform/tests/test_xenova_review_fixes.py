"""독립 검토의 패키지 소유권·실제 Xen sysfs·게스트 링크 결함 회귀 검사."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from xenova_sysfs_fixture import XenSysfs
import test_xenova_platform as contracts
from test_xenova_platform import FILES, ROOT, FakeXen, fixture, guest, record, vm


class UnitOwnershipTest(unittest.TestCase):
    def test_all_installed_units_have_package_owner_without_enabling_static_unit(self):
        recipe = (FILES.parent / 'xenova-platform_1.0.bb').read_text()
        installed = set(re.search(r'for unit in ([^;]+); do', recipe).group(1).split())
        units = {p.name for p in FILES.iterdir() if p.suffix in ('.service', '.mount')}
        self.assertEqual(installed, units)
        enabled = set(re.search(r'SYSTEMD_SERVICE:\$\{PN\} = "([^"]+)"', recipe).group(1).split())
        shipped = re.search(r'FILES:\$\{PN\} \+= "([^"]+)"', recipe, re.S).group(1).replace('\\\n', ' ').split()
        explicit = {p.rsplit('/', 1)[-1] for p in shipped if p.startswith('${systemd_system_unitdir}/')}
        # 고정 systemd.bbclass는 SYSTEMD_SERVICE 목록을 소유하지만 Requires는 추적하지 않는다.
        self.assertFalse(installed - enabled - explicit, f'unowned installed units: {sorted(installed - enabled - explicit)}')
        self.assertIn('${systemd_system_unitdir}/xenova-xen-ready.service', shipped)
        self.assertNotIn('xenova-xen-ready.service', enabled)
        self.assertNotIn('[Install]', (FILES / 'xenova-xen-ready.service').read_text())
        self.assertTrue(all('[Install]' in (FILES / unit).read_text() for unit in enabled))
        self.assertNotRegex(recipe, r'INSANE_SKIP[^\n]*installed-vs-shipped')


class NetbackIdentityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='xenova-netback-sysfs-')
        self.addCleanup(temp.cleanup)
        self.fs = XenSysfs(Path(temp.name))
        self.p = vm.Platform(command=lambda args: json.dumps([{'addr_info': [{'local': '192.168.77.1', 'prefixlen': 24}]}]))

    def test_real_netback_parent_device_is_accepted(self):
        node = self.fs.add_port('vif42.0')
        self.assertTrue((node / 'device').is_symlink())
        self.assertEqual((node / 'device/subsystem').resolve().name, 'xen-backend')
        self.p.check_network(self.fs.net)

    def test_renamed_physical_port_is_rejected(self):
        self.fs.add_port('vif42.0', kind='physical')
        with self.assertRaisesRegex(vm.PlatformError, 'DHCP refused'):
            self.p.check_network(self.fs.net)

    def test_xen_frontend_is_not_a_backend_port(self):
        self.fs.add_port('vif42.0', kind='netfront')
        with self.assertRaisesRegex(vm.PlatformError, 'DHCP refused'):
            self.p.check_network(self.fs.net)

    def test_unbound_or_missing_backend_device_is_rejected(self):
        node = self.fs.add_port('vif42.0')
        (node / 'device/driver').unlink()
        with self.assertRaisesRegex(vm.PlatformError, 'DHCP refused'):
            self.p.check_network(self.fs.net)
        (node / 'device').unlink()
        with self.assertRaisesRegex(vm.PlatformError, 'DHCP refused'):
            self.p.check_network(self.fs.net)

    def test_unexpected_port_is_rejected_even_with_backend_links(self):
        self.fs.add_port('tap0')
        with self.assertRaisesRegex(vm.PlatformError, 'DHCP refused'):
            self.p.check_network(self.fs.net)


class NetworkLifecycleRegressionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='xenova-live-vif-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.fs = XenSysfs(self.base)
        self.records = {r['id']: r for r in [record(), record('debian-dev')]}
        for r in self.records.values():
            fixture(self.base, r)
        (self.base / '.xenova-storage.json').write_text(json.dumps(
            {'schema':1, 'uuid':'12345678-1234-4234-8234-123456789abc'}))
        mountinfo = self.base / 'mountinfo'
        mountinfo.write_text(f'1 0 8:3 / {self.base} rw - ext4 /dev/fixture rw\n')
        real_storage_check = vm.storage_check
        storage = patch.object(vm, 'storage_check', side_effect=lambda data: real_storage_check(data, mountinfo))
        storage.start()
        self.addCleanup(storage.stop)
        self.xen = FakeXen(self.base, self.records)
        self.p = vm.Platform(self.base, self.base / 'run', self.command)
        check = self.p.check_network
        self.network_patch = patch.object(self.p, 'check_network', side_effect=lambda: check(self.fs.net))
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)

    def command(self, args, **kwargs):
        if args[0] == 'ip':
            return json.dumps([{'addr_info': [{'local': '192.168.77.1', 'prefixlen': 24}]}])
        result = self.xen(args, **kwargs)
        if args[:2] == ['xl', 'create']:
            self.fs.add_port(f"vif{self.xen.rows[-1]['domid']}.0")
        return result

    def start(self, key):
        self.p.prepare_dhcp(self.records)
        self.p.start(self.records[key])

    def test_second_vm_starts_after_first_vm_attaches_real_vif(self):
        self.start('ubuntu-dev')
        self.start('debian-dev')
        self.assertEqual(len(self.xen.rows), 2)

    def test_existing_vm_start_rechecks_network_without_duplicate_create(self):
        disk = self.base / 'instances/ubuntu-dev/root.raw'
        before = disk.read_bytes()
        self.start('ubuntu-dev')
        self.start('ubuntu-dev')
        self.assertEqual(sum(c[:2] == ['xl', 'create'] for c in self.xen.calls), 1)
        self.assertEqual(disk.read_bytes(), before)

    def test_dhcp_restart_precheck_accepts_existing_vifs_and_keeps_reservations(self):
        self.start('ubuntu-dev')
        hosts = self.p.runtime / 'dhcp-hosts'
        before = hosts.read_bytes()
        service = (ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-bringup/files/xenova-dhcp.service').read_text()
        self.assertIn('ExecStartPre=/usr/sbin/xenova-vm check-network', service)
        # DHCP restart의 실제 ExecStartPre 진입점을 호출한다. systemd/dnsmasq는 실행하지 않는다.
        with patch.object(vm, 'Platform', return_value=self.p), patch.object(vm, 'RUN', self.p.runtime):
            vm.main(['check-network'])
        self.assertEqual(hosts.read_bytes(), before)
        self.assertEqual(len(self.xen.rows), 1)


class GuestSymlinkRegressionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='xenova-guest-symlinks-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'root'
        contracts.GuestPreparationTest().root(self.root)

    def wants(self):
        path = self.root / 'etc/systemd/system/multi-user.target.wants'
        path.mkdir(exist_ok=True)
        return path

    def test_absolute_existing_service_links_can_be_replaced(self):
        wants = self.wants()
        (wants / 'ssh.service').symlink_to('/usr/lib/systemd/system/ssh.service')
        guest.prepare_template(self.root)
        self.assertEqual(os.readlink(wants / 'ssh.service'), '/usr/lib/systemd/system/ssh.service')
        self.assertTrue((self.root / 'etc/xenova/template-prepared').exists())

    def test_relative_existing_service_links_can_be_replaced(self):
        wants = self.wants()
        (wants / 'ssh.service').symlink_to('../../../../usr/lib/systemd/system/ssh.service')
        (wants / 'systemd-networkd.service').symlink_to('../../../../usr/lib/systemd/system/systemd-networkd.service')
        guest.prepare_template(self.root)
        self.assertEqual(os.readlink(wants / 'ssh.service'), '/usr/lib/systemd/system/ssh.service')

    def test_absolute_parent_link_stays_in_guest(self):
        parent = self.root / 'etc/systemd/system/multi-user.target.wants'
        parent.symlink_to('/var/lib/xenova/wants')
        guest.enable(self.root, 'ssh.service')
        link = self.root / 'var/lib/xenova/wants/ssh.service'
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), '/usr/lib/systemd/system/ssh.service')

    def test_relative_parent_link_stays_in_guest(self):
        parent = self.root / 'etc/systemd/system/multi-user.target.wants'
        parent.symlink_to('../../../var/lib/xenova/wants')
        guest.enable(self.root, 'ssh.service')
        self.assertTrue((self.root / 'var/lib/xenova/wants/ssh.service').is_symlink())

    def test_vendor_service_absolute_alias_and_usrmerge_are_supported(self):
        (self.root / 'lib').symlink_to('usr/lib')
        vendor = self.root / 'usr/lib/systemd/system'
        (vendor / 'ssh.service').unlink()
        (vendor / 'ssh.service').symlink_to('/lib/systemd/system/sshd.service')
        (vendor / 'sshd.service').write_text('[Service]\n')
        guest.enable(self.root, 'ssh.service')
        self.assertTrue((self.wants() / 'ssh.service').is_symlink())

    def test_replacing_leaf_link_never_changes_its_host_target(self):
        outside = self.base / 'host-service'
        outside.write_text('HOST MUST STAY')
        link = self.wants() / 'ssh.service'
        link.symlink_to(outside)
        guest.enable(self.root, 'ssh.service')
        self.assertEqual(outside.read_text(), 'HOST MUST STAY')
        self.assertEqual(os.readlink(link), '/usr/lib/systemd/system/ssh.service')

    def test_absolute_file_and_parent_targets_are_rooted_in_guest(self):
        outside = self.base / 'host-directory'
        outside.mkdir()
        (outside / 'keep').write_text('HOST MUST STAY')
        (self.root / 'etc/alias').symlink_to(outside)
        guest.write(self.root, 'etc/alias/keep', 'GUEST ONLY')
        self.assertEqual((outside / 'keep').read_text(), 'HOST MUST STAY')
        self.assertEqual((self.root / str(outside).lstrip('/') / 'keep').read_text(), 'GUEST ONLY')
        (self.root / 'etc/file-link').symlink_to('/etc/guest-target')
        guest.write(self.root, 'etc/file-link', 'guest file')
        self.assertEqual((self.root / 'etc/guest-target').read_text(), 'guest file')

    def test_relative_escape_and_link_cycles_are_rejected(self):
        (self.root / 'etc/escape').symlink_to('../../outside')
        (self.root / 'etc/loop').symlink_to('/etc/loop')
        for relative in ('../outside', '/etc/shadow', 'etc/escape/keep', 'etc/loop/keep'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                guest.write(self.root, relative, 'FORBIDDEN')
        self.assertFalse((self.base / 'outside').exists())

    def test_mask_getty_and_dbus_links_use_guest_parent_resolution(self):
        (self.root / 'etc/systemd/system/getty.target.wants').symlink_to('/var/lib/xenova/getty')
        dbus = self.root / 'var/lib/dbus'
        dbus.parent.mkdir(parents=True)
        dbus.symlink_to('/etc/xenova/dbus')
        guest.prepare_template(self.root)
        self.assertTrue((self.root / 'var/lib/xenova/getty/serial-getty@hvc0.service').is_symlink())
        self.assertEqual(os.readlink(self.root / 'etc/xenova/dbus/machine-id'), '/etc/machine-id')


if __name__ == '__main__':
    unittest.main()
