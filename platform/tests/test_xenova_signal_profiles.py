"""자산 선언·실제 loader capture·현재 프로필 조회. Xen/guest/Store만 기존 fixture다."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_xenova_network_zephyr as network_fixture
import test_xenova_provisioning as provision_fixture
from xenova_guest_profile import from_manifest,validate
import xenova_zephyr as zephyr
import xenova_provisioning as provision


class SignalProfileTest(unittest.TestCase):
    def setUp(self):
        self.f=network_fixture.NetworkPlatformTest('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_loader_captures_verified_current_profile_and_never_uses_next_asset_for_old_run(self):
        f=self.f
        initial=f.upgrade_start();run=f.p.execution().run_record(f.z)
        self.assertIsNone(run['signal_profile']['signals']['app_heartbeat'])
        before=json.loads(json.dumps(run))
        record={**f.z,'provisioning_profile':from_manifest({'source':{'app':'xenova-starter'}})}
        path=f.zdir/'instance.json';path.write_text(json.dumps(record));f.xen.records[f.z['id']]=record
        view=f.m.call('provisioning',{'vm_id':f.zid})
        self.assertTrue(view['ok'],view)
        self.assertIs(view['data']['profile']['signals']['app_heartbeat'],False)
        self.assertIsNone(view['data']['current_profile']['profile']['signals']['app_heartbeat'])
        self.assertEqual(f.p.execution().run_record(record),before)

    def test_actual_start_calls_bootstrap_after_binding_with_captured_profile(self):
        f=self.f;directory=f.fw;manifest=directory/'firmware.json';meta=json.loads(manifest.read_text())
        config=directory/'zephyr.config';config.chmod(0o644)
        config.write_text(config.read_text()+'\nCONFIG_XENOVA_TELEMETRY=y\nCONFIG_XENOVA_DRIVE_WARM=y\nCONFIG_XENOVA_DRIVE_LATTICE=y\n');config.chmod(0o444)
        meta['source']['app']='xenova-drive';meta['files']['zephyr.config']={'sha256':zephyr.sha(config),'bytes':config.stat().st_size}
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        f.ok(f.transition());calls=[];actual=provision.bootstrap_execution
        class Store:
            quiescent=True
            def __init__(self,channel):self.channel=channel
            def apply(self,context,payload,previous):calls.append((self.channel,context,payload))
            def close(self):pass
        # Supply is real; native hashing is covered by provisioning tests. Use its isolated source only.
        provider=provision_fixture.ProvisioningHooksTest('runTest');provider.setUp();self.addCleanup(provider.doCleanups)
        provision.ensure(f.p.data,f.z)
        def bootstrap(p,record,**kwargs):return actual(p,record,store_factory=Store,**kwargs)
        with patch.object(provision,'bootstrap_execution',bootstrap):f.ok(f.control('start'))
        self.assertEqual([c[0] for c in calls],['telemetry','drive'])
        self.assertEqual(calls[0][2]['boot_id'],calls[1][2]['boot_id'])
        view=f.ok(f.m.call('provisioning',{'vm_id':f.zid}))
        self.assertEqual(view['current_profile']['profile']['apps']['autonomous']['model'],'stateful')
        self.assertEqual(view['current_profile']['profile']['apps']['manual']['model'],'stateless')
        self.assertEqual(view['supply']['authentication'],'unobserved')
        self.assertEqual(view['current_profile']['profile']['apps']['autonomous']['standby_progress'],'idle')
        original=provision.credential_file(f.p.data,f.zid,'drive').read_bytes()
        with patch.object(provision,'bootstrap_execution',bootstrap):
            f.ok(f.control('stop'));second=f.ok(f.control('start'))['vm']
            request={'vm_id':f.zid,'recovery_attempt_id':'70000000-1111-4222-8333-123456789abc',
                'expected_host_boot_id':f.host,'expected_lifecycle_generation':0,
                'expected_intent_revision':second['intent_revision'],
                'expected_execution':{'execution_id':second['execution_id']},
                'mode':'graceful-restart','deadline_monotonic_ms':int((f.ticks+90)*1000)}
            result=f.ok(f.m.call('recover_once',request,execute=True))
        self.assertEqual(result['original_result']['app_readiness'],'unknown')
        self.assertEqual(len({row[1]['execution_id'] for row in calls}),3)
        self.assertEqual(len({row[2]['boot_id'] for row in calls}),3)
        self.assertTrue(provision.credential_file(f.p.data,f.zid,'drive').read_bytes()==original)

    def test_malformed_declaration_does_not_become_unsupported_or_stateless(self):
        value=from_manifest({'source':{}})
        self.assertIsNone(value['signals']['app_heartbeat']);self.assertEqual(value['apps'],{})
        with self.assertRaises(ValueError):validate({**value,'channels':[{}]})
        with self.assertRaises(ValueError):from_manifest({'provisioning_profile':None})
        with self.assertRaises(ValueError):from_manifest({'source':{'distribution':'debian'},
            'provisioning_profile':{**value,'channels':['telemetry']}})
        with self.assertRaises(ValueError):validate({**value,'channels':['drive']})


if __name__=='__main__':unittest.main()
