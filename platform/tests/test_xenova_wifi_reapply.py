"""소유한 supplicant 명령과 설정 소비를 모델링한다. RF/AP 접속 시험은 아니다."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from first_install_fixture import ROOT
import xenova_wifi as wifi
from xenova_runtime import PlatformError


class SupplicantModel:
    def __init__(self, root, active):
        self.root, self.active = root, active
        self.loaded = (root / 'etc/xenova/wifi/wlan0.conf').read_bytes() if active else None
        self.calls = []
        self.fail = None

    def __call__(self, args):
        self.calls.append(args)
        if self.fail and args[:2] == self.fail:
            raise PlatformError('synthetic secret must not reach output')
        if args[:2] == ['systemctl', 'show']:
            return 'active\n' if self.active else 'inactive\n'
        if args[:2] in (['systemctl', 'start'], ['systemctl', 'restart']) or args[:3] == ['systemctl', 'enable', '--now'] and not self.active:
            self.active = True
            self.loaded = (self.root / 'etc/xenova/wifi/wlan0.conf').read_bytes()
        if args[0] == 'ip':
            return '[{"addr_info":[]}]'
        if args[0] == 'wpa_cli':
            return 'wpa_state=SCANNING\n'
        return ''


class WifiReapplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'sys/class/net/wlan0/wireless').mkdir(parents=True)
        self.wifi = wifi.Wifi(self.root)
        self.wifi.configure('wlan0', 'old-fixture', 'old-fixture-password', 'KR')

    def activate_cli(self, model):
        self.wifi.run = model
        output = io.StringIO()
        with patch.object(wifi, 'Wifi', return_value=self.wifi), patch.object(wifi, 'command', side_effect=model), redirect_stdout(output):
            code = wifi.main(['activate', '--interface', 'wlan0'])
        return code, json.loads(output.getvalue())

    def test_active_service_reads_changed_credentials_and_only_owned_service_restarts(self):
        model = SupplicantModel(self.root, True)
        before = model.loaded
        self.wifi.configure('wlan0', 'new-fixture', 'new-fixture-password', 'KR')
        code, result = self.activate_cli(model)
        self.assertNotEqual(model.loaded, before)
        self.assertEqual(model.loaded, (self.root / 'etc/xenova/wifi/wlan0.conf').read_bytes())
        self.assertIn(b'\ndisable_scan_offload=1\n',model.loaded)
        self.assertEqual(code, 0)
        self.assertEqual(result['activation']['action'], 'restart')
        self.assertEqual(result['activation']['result'], 'requested')
        self.assertEqual(result['authentication'], 'SCANNING')
        self.assertEqual([a for a in model.calls if a[:2] == ['systemctl', 'restart']],
                         [['systemctl', 'restart', 'xenova-wifi@wlan0.service']])
        self.assertFalse(any('xenova-dhcp' in ' '.join(a) or 'xenbr0' in a for a in model.calls))
        self.assertNotIn('new-fixture', json.dumps(result))

    def test_inactive_service_starts_once_without_restart(self):
        model = SupplicantModel(self.root, False)
        code, result = self.activate_cli(model)
        self.assertEqual(code, 0)
        self.assertEqual(result['activation']['action'], 'start')
        self.assertEqual([a for a in model.calls if a[:2] == ['systemctl', 'start']],
                         [['systemctl', 'start', 'xenova-wifi@wlan0.service']])
        self.assertFalse(any(a[:2] == ['systemctl', 'restart'] for a in model.calls))

    def test_reapply_failure_keeps_saved_config_reports_phase_and_skips_network_changes(self):
        model = SupplicantModel(self.root, True)
        previous = model.loaded
        self.wifi.configure('wlan0', 'new-fixture', 'new-fixture-password', 'KR')
        before = (self.root / 'etc/xenova/wifi/wlan0.conf').read_bytes()
        model.fail = ['systemctl', 'restart']
        code, result = self.activate_cli(model)
        self.assertEqual(code, 1)
        self.assertEqual(result['activation']['action'], 'restart')
        self.assertEqual(result['activation']['result'], 'failed')
        self.assertEqual(result['activation']['stage'], 'supplicant')
        self.assertEqual(model.loaded, previous)
        self.assertEqual((self.root / 'etc/xenova/wifi/wlan0.conf').read_bytes(), before)
        self.assertFalse(any(a[0] == 'networkctl' for a in model.calls))
        self.assertNotIn('synthetic secret', json.dumps(result))
        self.assertNotIn('new-fixture', json.dumps(result))

    def test_network_reconfiguration_failure_is_not_connection_success(self):
        model = SupplicantModel(self.root, True)
        model.fail = ['networkctl', 'reconfigure']
        code, result = self.activate_cli(model)
        self.assertEqual(code, 1)
        self.assertEqual(result['activation']['stage'], 'network-reconfigure')
        self.assertEqual(result['activation']['result'], 'failed')

    def test_activation_query_failure_does_not_start_or_restart(self):
        model = SupplicantModel(self.root, True)
        model.fail = ['systemctl', 'show']
        code, result = self.activate_cli(model)
        self.assertEqual(code, 1)
        self.assertEqual(result['activation']['stage'], 'state-query')
        self.assertFalse(any(a[:2] in (['systemctl', 'start'], ['systemctl', 'restart']) for a in model.calls))

    def test_first_start_failure_is_distinct_from_reapply_failure(self):
        model = SupplicantModel(self.root, False)
        model.fail = ['systemctl', 'start']
        code, result = self.activate_cli(model)
        self.assertEqual(code, 1)
        self.assertEqual(result['activation'], {'action': 'start', 'result': 'failed', 'stage': 'supplicant'})
        self.assertFalse(any(a[0] == 'networkctl' for a in model.calls))

    def test_transitional_or_malformed_service_state_never_starts_second_supplicant(self):
        for state in ('activating', 'deactivating', '', 'invalid'):
            with self.subTest(state=state):
                model = SupplicantModel(self.root, True)
                def run(args):
                    if args[:2] == ['systemctl', 'show']:
                        model.calls.append(args)
                        return state
                    return model(args)
                self.wifi.run = run
                with self.assertRaises(wifi.ActivationError) as caught:
                    self.wifi.activate('wlan0')
                self.assertEqual(caught.exception.activation['stage'], 'service-state')
                self.assertFalse(any(a[:2] in (['systemctl', 'start'], ['systemctl', 'restart']) for a in model.calls))

    def test_networkd_configuration_is_readable_and_credentials_remain_private(self):
        network = self.root / 'etc/systemd/network/25-xenova-wifi.network'
        credentials = self.root / 'etc/xenova/wifi/wlan0.conf'
        self.assertEqual(network.stat().st_mode & 0o777, 0o644)
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)
        self.assertEqual(credentials.parent.stat().st_mode & 0o777, 0o700)
        settings = network.read_text()
        for expected in ('RequiredForOnline=routable', 'DHCP=ipv4',
                         'IPv6AcceptRA=no', 'RouteMetric=600'):
            self.assertIn(expected, settings)

    def configure_with_private_umask(self):
        previous = os.umask(0o077)
        try:
            self.wifi.configure('wlan0', 'private-umask-fixture', 'private-umask-password', 'KR')
        finally:
            os.umask(previous)

    def test_private_caller_umask_does_not_make_public_network_file_unreadable(self):
        self.configure_with_private_umask()
        self.test_networkd_configuration_is_readable_and_credentials_remain_private()

    @unittest.skipUnless(os.geteuid() == 0, 'actual consumer UID test requires isolated root invocation')
    def test_actual_unprivileged_consumer_reads_network_file_but_not_psk(self):
        import pwd
        self.configure_with_private_umask()
        consumer = pwd.getpwnam('systemd-network')
        self.root.chmod(0o755)
        network = self.root / 'etc/systemd/network/25-xenova-wifi.network'
        credentials = self.root / 'etc/xenova/wifi/wlan0.conf'
        script = """import os,sys
os.setgroups([])
os.setgid(int(sys.argv[1]))
os.setuid(int(sys.argv[2]))
assert '[Match]' in open(sys.argv[3]).read()
try:
    open(sys.argv[4]).read()
except PermissionError:
    pass
else:
    raise AssertionError('PSK readable by network consumer')
"""
        result = subprocess.run([sys.executable, '-c', script, str(consumer.pw_gid), str(consumer.pw_uid),
                                 str(network), str(credentials)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
