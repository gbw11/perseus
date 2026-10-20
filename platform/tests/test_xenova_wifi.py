"""Wi-Fi 비밀 입력·영구 설정·주소 진단·실제 systemd 그래프 회귀. 무선 연결 성공 증거는 아니다."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, FILES, assemble, platform_root
from dom0_ssh_fixture import extract, install_policy, verify
import xenova_wifi as wifi
from xenova_runtime import PlatformError


class WifiTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'sys/class/net/wlan0/wireless').mkdir(parents=True)
        self.wifi=wifi.Wifi(self.root)

    def test_secret_is_derived_config_is_private_and_state_does_not_echo_ssid_psk(self):
        result=self.wifi.configure('wlan0','Synthetic " lab','fixture-password','KR')
        path=self.root/'etc/xenova/wifi/wlan0.conf'
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
        self.assertNotIn('fixture-password',path.read_text())
        self.assertNotIn('Synthetic',path.read_text())
        self.assertNotIn('fixture-password',json.dumps(result))
        self.assertEqual(path.read_text(),wifi.settings('Synthetic " lab','fixture-password','KR'))
        self.assertIn('Type=wlan',(self.root/'etc/systemd/network/25-xenova-wifi.network').read_text())
        self.assertNotIn('xenbr0',(self.root/'etc/systemd/network/25-xenova-wifi.network').read_text())

    def test_invalid_input_rejected_before_existing_configuration_changes(self):
        self.wifi.configure('wlan0','test','fixture-password','KR')
        before=(self.root/'etc/xenova/wifi/wlan0.conf').read_bytes()
        for ssid,password,country in [('bad\nssid','fixture-password','KR'),('x','tiny','KR'),('x','fixture-password','KR\nnetwork={')]:
            with self.assertRaises(PlatformError):self.wifi.configure('wlan0',ssid,password,country)
            self.assertEqual((self.root/'etc/xenova/wifi/wlan0.conf').read_bytes(),before)
        with self.assertRaises(PlatformError):self.wifi.configure('../eth0','x','fixture-password','KR')

    def test_parent_link_and_file_link_preserve_external_assets(self):
        directory=self.root/'etc/xenova'
        directory.mkdir(parents=True)
        outside=self.root/'outside';outside.mkdir()
        (directory/'wifi').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(PlatformError):self.wifi.configure('wlan0','test','fixture-password','KR')
        self.assertEqual(list(outside.iterdir()),[])

    def test_unconfigured_missing_ap_auth_failed_and_no_address_do_not_start_or_stop_vms(self):
        calls=[]
        def run(args):
            calls.append(args)
            if args[0]=='ip':return '[{"addr_info":[]}]'
            return 'wpa_state=SCANNING\nssid=secret-name\n'
        self.wifi.run=run
        self.assertEqual(self.wifi.status('wlan0')['authentication'],'not-configured')
        self.wifi.configure('wlan0','test','fixture-password','KR')
        result=self.wifi.status('wlan0')
        self.assertEqual(result['authentication'],'SCANNING');self.assertEqual(result['ipv4'],[])
        self.assertNotIn('secret-name',json.dumps(result))
        self.assertFalse(any(a[0] in ('xl','systemctl') for a in calls))
        def unavailable(args):
            raise PlatformError('unavailable')
        self.wifi.run=unavailable
        self.assertEqual(self.wifi.status('wlan0')['authentication'],'unavailable')

    def test_subnet_overlap_reported_without_changing_guest_addresses(self):
        self.wifi.run=lambda args: '[{"addr_info":[{"local":"192.168.77.50","prefixlen":24}]}]'
        result=self.wifi.status('wlan0')
        self.assertTrue(result['internal_subnet_overlap'])
        self.assertIn('network is not changed',' '.join(result['errors']))
        self.assertFalse((self.root/'etc/systemd/network').exists())

    def test_real_dom0_units_keep_ssh_key_order_and_vm_boot_independent_of_wifi(self):
        root=self.root/'graph';root.mkdir();extract(root);install_policy(root,replay_recipe=True)
        executable=root/'usr/sbin/wpa_supplicant';executable.write_text('#!/bin/sh\nexit 0\n');executable.chmod(0o755)
        result=verify(root,'sshd.service','xenova-vms.service','xenova-wifi@wlan0.service')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotRegex(result.stderr,r'(?i)ordering cycle|job .*deleted|skipping .*cycle')
        self.assertIn('sshdgenkeys.service',result.stdout+result.stderr)
        source=(FILES/'xenova-vms.service').read_text()
        self.assertNotIn('wifi',source.lower());self.assertNotIn('network-online.target',source)
        self.assertIn('JobTimeoutSec=20s',(FILES/'xenova-wifi@.service').read_text())

    def test_actual_recipe_install_contains_every_new_runtime_import_and_unit(self):
        root=self.root/'package';root.mkdir();extract(root);install_policy(root,replay_recipe=True)
        for module in ('xenova_management','xenova_management_cli','xenova_catalog','xenova_wifi','artifact_io'):
            self.assertTrue((root/'usr/lib/xenova'/(module+'.py')).is_file())
        self.assertTrue((root/'usr/sbin/xenova-wifi').stat().st_mode&0o111)
        self.assertTrue((root/'usr/lib/systemd/system/xenova-wifi@.service').is_file())
        recipe=(FILES.parent/'xenova-platform_1.0.bb').read_text()
        for package in ('python3-ctypes','python3-unixadmin','wpa-supplicant-cli','wireless-regdb-static','linux-firmware-bcm43455'):
            self.assertIn(package,recipe)

    def test_old_image_missing_management_code_is_rejected_by_assembler(self):
        root=platform_root(self.root/'platform')
        path=root/'usr/lib/xenova/xenova_management.py';path.unlink()
        with self.assertRaisesRegex(ValueError,'source/installed contract mismatch'):
            assemble.platform_root_ok(root)
