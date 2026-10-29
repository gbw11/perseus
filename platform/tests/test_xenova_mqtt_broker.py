"""MQTT 공통 정책·CLI·마운트·부팅 계약. 저장/재시작 회귀는 test_xenova_broker_accounts에 둔다."""
import importlib.util
import io
from contextlib import redirect_stderr
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'platform/deploy/mosquitto/scripts/xenova_broker.py'
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-connectivity/xenova-mqtt-broker/files'
spec = importlib.util.spec_from_file_location('mqtt_contract', MODULE)
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)
A = '11111111-1111-4111-8111-111111111111'


class Contracts(unittest.TestCase):
    def test_mount_rejection_never_creates_accounts_or_lock(self):
        if os.geteuid() != 0:
            self.skipTest('root ownership requires the isolated Linux fixture')
        with tempfile.TemporaryDirectory(prefix='xenova-mqtt-mount-') as temporary:
            data = Path(temporary)
            mountinfo = data / 'mountinfo'
            device_id = data.stat().st_dev
            device = SimpleNamespace(stat=lambda: SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=device_id))
            for row in ('', f'1 0 {os.major(device_id)}:{os.minor(device_id)} / {data} ro - ext4 /dev/fixture ro\n',
                        f'1 0 {os.major(device_id)}:{os.minor(device_id)} / {data} rw - tmpfs fixture rw\n'):
                mountinfo.write_text(row)
                with self.assertRaises(broker.SetupError):
                    broker.storage(data, device=device, mountinfo=mountinfo)
                self.assertFalse((data / 'mqtt').exists())
                self.assertFalse((data / '.mqtt-initialize.lock').exists())

    def test_invalid_duplicate_noncanonical_and_unknown_accounts_are_rejected(self):
        for values in [['unknown'], [A, A], ['00000000-0000-0000-0000-000000000000'], [A.replace('-', '')]]:
            with self.assertRaises(broker.SetupError):
                broker.vm_ids(values)
        for name in ['arbitrary-new-service', 'telemetry-invalid', '../backend', 'backend\nuser evil']:
            with self.assertRaises(broker.SetupError):
                broker.profile(name, broker.policy())

    def test_development_outputs_are_generated_from_same_policy_as_image(self):
        result = subprocess.run([sys.executable, '-B', str(MODULE.parent / 'render-policy.py'), '--check'],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = broker.default_accounts(broker.policy())
        self.assertIn('interaction', records)
        self.assertNotIn(A, records)
        self.assertNotIn('message-board', records)

    def test_service_and_recipe_do_not_wait_for_tailnet_or_add_reverse_vm_dependency(self):
        text = (FILES / '20-xenova-broker.conf').read_text()
        for required in ['ConditionPathExists=/srv/xenova/mqtt', 'RequiresMountsFor=/srv/xenova', 'check --runtime']:
            self.assertIn(required, text)
        for forbidden in ['network-online.target', 'xenova-vms.service', 'BindsTo=', 'tailscaled']:
            self.assertNotIn(forbidden, text)
        config = (FILES / 'mosquitto.conf').read_text()
        self.assertIn('include_dir /srv/xenova/mqtt/current/config.d', config)
        self.assertIn('listener 1883 127.0.0.1', config)
        self.assertIn('listener 1883 192.168.77.1', config)
        self.assertNotIn('0.0.0.0', config)
        base = (FILES / 'mosquitto.service').read_text()
        for original in ['User=mosquitto', 'Type=notify', 'WatchdogSec=3min', 'NotifyAccess=main',
                         'ExecReload=/bin/kill -HUP $MAINPID', 'Restart=on-failure']:
            self.assertIn(original, base)
        recipe = (FILES.parent / 'xenova-mqtt-broker_1.0.bb').read_text()
        self.assertIn('deploy/mosquitto/scripts', recipe)
        self.assertIn('file://policy.json', recipe)
        self.assertIn('file://xenova_broker.py', recipe)

    def test_old_cli_and_team_entrypoints_delegate_to_single_writer(self):
        for name in ('xenova-broker', 'xenova-mqtt-init'):
            result = subprocess.run([sys.executable, '-B', str(FILES / name), '--help'], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'add-account', result.stdout)
        script = (MODULE.parent / 'dom0-broker.sh').read_text()
        self.assertNotIn('mosquitto_passwd', script)
        self.assertNotIn('sleep ', script)
        self.assertIn('/usr/sbin/xenova-broker', script)

    def test_reload_request_is_never_called_verified_authentication(self):
        results = [SimpleNamespace(returncode=0, stdout='active\n'), SimpleNamespace(returncode=0)]
        with patch.object(broker.subprocess, 'run', side_effect=results) as run:
            self.assertEqual(broker.reload_broker(), 'reload_requested')
            self.assertEqual(run.call_args.args[0], ['systemctl', 'reload', 'mosquitto.service'])
        with patch.object(broker.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='inactive\n')):
            self.assertEqual(broker.reload_broker(), 'next_start')
        with patch.object(broker.subprocess, 'run', side_effect=[results[0], SimpleNamespace(returncode=1)]):
            with self.assertRaises(broker.SetupError):
                broker.reload_broker()

    def test_reload_failure_preserves_stored_result_and_unknown_write_is_not_empty(self):
        for stored in (True, None):
            errors = io.StringIO()
            response = {'stored': True, 'configured': True} if stored else OSError('injected storage failure')
            with patch.object(broker.os, 'geteuid', return_value=0), \
                 patch.object(broker, 'change', side_effect=response if stored is None else None,
                              return_value=response), patch.object(broker, 'reload_broker',
                              side_effect=broker.SetupError('stored; reload unconfirmed')), redirect_stderr(errors):
                self.assertEqual(broker.main(['add-account', A, '--execute']), 1)
            value = json.loads(errors.getvalue())
            self.assertEqual(value['stored'], stored)
            self.assertEqual(value['configured'], stored)

    def test_unprivileged_request_is_rejected_before_writer_and_reload(self):
        errors = io.StringIO()
        with patch.object(broker.os, 'geteuid', return_value=1000), \
             patch.object(broker, 'change') as writer, \
             patch.object(broker, 'reload_broker') as reload, redirect_stderr(errors):
            self.assertEqual(broker.main(['add-account', A, '--execute']), 1)
        writer.assert_not_called()
        reload.assert_not_called()
        value = json.loads(errors.getvalue())
        self.assertIsNone(value['stored'])
        self.assertIsNone(value['configured'])

    def test_account_state_does_not_change_factory_uuid_or_assets(self):
        from first_install_fixture import Board
        with tempfile.TemporaryDirectory(prefix='xenova-mqtt-registration-') as temporary:
            board = Board(Path(temporary))
            board.grow_mount()
            board.factory()
            mqtt = board.data / 'mqtt'
            mqtt.mkdir(mode=0o750)
            (mqtt / 'unrelated-account-state').write_text('synthetic private state')
            board.p.register()
            records = {p.name: (p / 'instance.json').read_bytes() for p in (board.data / 'instances').iterdir()}
            board.p.register()
            self.assertEqual(records, {p.name: (p / 'instance.json').read_bytes() for p in (board.data / 'instances').iterdir()})
            self.assertEqual((mqtt / 'unrelated-account-state').read_text(), 'synthetic private state')

    def test_tailnet_access_does_not_relax_ssh_forwarding(self):
        path = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova-auth.conf'
        settings = dict(line.split(None, 1) for line in path.read_text().splitlines() if line.strip() and not line.startswith('#'))
        self.assertEqual(settings['AllowTcpForwarding'], 'local')
        self.assertEqual(settings['GatewayPorts'], 'no')
        self.assertEqual(set(settings['PermitOpen'].split()), {f'192.168.77.{host}:22' for host in range(2, 100)})


if __name__ == '__main__':
    unittest.main()
