"""Factory config uses actual source declarations and existing credential paths."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT
import xenova_broker
import xenova_provisioning
import xenova_vm

sys.path.insert(0, str(ROOT/'platform/services/dom0-manager'))
from dom0_manager import recovery_host_config as config

IDS = ('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222')
ROLES = ('zephyr-drive-a', 'zephyr-drive-b')
ACCOUNTS = {'profiles': {'dom0-server': 'service', 'gateway': 'service'},
            'secrets': {'dom0-server': 'worker-fixture', 'gateway': 'gateway-fixture'}}
PROFILE = {'schema_version': '1.0', 'source': 'verified-drive-source', 'channels': ['drive', 'telemetry'],
           'signals': {'agent_heartbeat': None, 'app_heartbeat': True, 'app_progress': True, 'telemetry_v2': True},
           'apps': {'autonomous': {'model': 'stateful', 'standby_progress': 'idle'},
                    'manual': {'model': 'stateless', 'standby_progress': 'idle'}}}


@unittest.skipUnless(os.geteuid() == 0, 'verify actual root-owned private files')
class RecoveryHostConfigTest(unittest.TestCase):
    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        root = Path(base.name)
        self.data, self.runtime, self.etc = (root/name for name in ('data', 'run', 'etc'))
        for directory in (self.data, self.etc):
            directory.mkdir(mode=0o700)
        self.records = {role: {'uuid': identity, 'guest_kind': 'zephyr', 'provisioning_profile': PROFILE}
                        for role, identity in zip(ROLES, IDS)}
        state = self.data/'.first-install-state.json'
        state.write_text(json.dumps({'schema': 1, 'phase': 'complete',
                                     'identities': {role: {'uuid': identity} for role, identity in zip(ROLES, IDS)}}))
        state.chmod(0o600)
        for identity in IDS:
            home = xenova_provisioning.home(self.data, identity, create=True)
            xenova_provisioning.put(home/'status.json', json.dumps({'vm_id': identity, 'channels': ['drive', 'telemetry']}))
            for channel in ('drive', 'telemetry'):
                xenova_provisioning.put(home/(channel+'.json'), 'owned fixture credential')
        for target in (patch.object(xenova_vm, 'storage_check'), patch.object(xenova_vm, 'load_records', return_value=self.records),
                       patch.object(xenova_broker, 'inspect', return_value=ACCOUNTS)):
            target.start()
            self.addCleanup(target.stop)

    def prepare(self):
        return config.configure(self.data, self.runtime, self.etc)

    def test_factory_uuid_pair_and_mock_gateway_reuse_private_credentials(self):
        result = self.prepare()
        self.assertEqual([result['XENOVA_GATEWAY_VM_A'], result['XENOVA_GATEWAY_VM_B']], list(IDS))
        self.assertEqual(result['XENOVA_GATEWAY_SINK'], 'mock')
        self.assertEqual((self.etc/'recovery-worker.env').stat().st_mode & 0o777, 0o600)
        self.assertIn('XENOVA_RECOVERY_BROKER_USERNAME="dom0-server"', (self.etc/'recovery-worker.env').read_text())
        drive = json.loads((self.runtime/'drive-rejoin-config.json').read_text())
        self.assertEqual(drive['app_state_models'], {'autonomous': 'stateful', 'manual': 'stateless'})
        for identity in IDS:
            for channel in ('drive', 'telemetry'):
                credential = Path(drive['credentials'][identity][channel])
                self.assertEqual(credential, self.data/'provisioning'/identity/(channel+'.json'))
                self.assertEqual(credential.read_text(), 'owned fixture credential')
        self.assertFalse((self.data/'drive-control').exists())

    def test_existing_override_and_absent_factory_role(self):
        env = self.etc/'recovery-worker.env'
        xenova_provisioning.put(env, 'approved override')
        self.prepare()
        self.assertEqual(env.read_text(), 'approved override')
        self.records.pop(ROLES[0])
        self.assertIsNone(self.prepare())
        self.assertFalse((self.runtime/'gateway-factory.env').exists())
        self.assertFalse((self.runtime/'recovery-drive.env').exists())
        self.assertEqual(env.read_text(), 'approved override')

    def test_fresh_registration_grants_at_most_one_initialization(self):
        self.prepare()
        state_path = self.data/'.first-install-state.json'
        state = json.loads(state_path.read_text())
        state.update(factory_id='fixture-factory', dom0_machine_id='fixture-machine')
        xenova_provisioning.put(state_path, json.dumps(state))
        ready = self.runtime/'install-ready.json'
        xenova_provisioning.put(ready, json.dumps({'factory_id': state['factory_id'],
                                                 'dom0_machine_id': state['dom0_machine_id'],
                                                 'fresh_registration': False}))
        with patch.object(Path, 'is_file', return_value=True), patch.object(config.subprocess, 'run') as commands:
            config.start_factory_gateway({}, self.data, self.runtime, self.etc)
            commands.assert_not_called()
            xenova_provisioning.put(ready, json.dumps({'factory_id': state['factory_id'],
                                                     'dom0_machine_id': state['dom0_machine_id'],
                                                     'fresh_registration': True}))
            config.start_factory_gateway({}, self.data, self.runtime, self.etc)
            self.assertEqual(commands.call_args_list[0].args[0][-1], '--initialize-control-state')
            self.assertEqual(commands.call_args_list[1].args[0][-1], 'xenova-gateway.service')
            with self.assertRaises(FileExistsError):
                config.start_factory_gateway({}, self.data, self.runtime, self.etc)
            self.assertEqual(commands.call_count, 2)

    def test_recovery_export_uses_only_the_worker_account(self):
        target = self.etc/'worker-export.env'
        self.assertTrue(xenova_broker.export(ACCOUNTS, 'dom0-server', 'recovery-env', target, True)['exported'])
        self.assertIn('XENOVA_RECOVERY_BROKER_USERNAME="dom0-server"', target.read_text())
        with self.assertRaises(xenova_broker.SetupError):
            xenova_broker.export(ACCOUNTS, 'gateway', 'recovery-env', self.etc/'invalid.env', True)


if __name__ == '__main__':
    unittest.main()
