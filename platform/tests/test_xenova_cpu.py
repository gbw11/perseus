"""Real Manager/Platform/Execution journals; only Xen, clock and small assets are fixtures."""
import copy
import io
import json
import re
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
import test_xenova_network_zephyr as network
from test_xenova_management import Interrupted
from first_install_contract import uuid4
import xenova_vm as vm
import xenova_zephyr as zephyr
from xenova_cpu import factory_activation
from xenova_execution import ExecutionError, fingerprint
from xenova_runtime import operation_lock


class CPUTest(unittest.TestCase):
    ok = foundation.FoundationTest.ok
    fails = foundation.FoundationTest.fails
    sleep = foundation.FoundationTest.sleep

    def setUp(self):
        network.NetworkPlatformTest.setUp(self)
        self.z2 = zephyr.new_instance(self.p.data, 'second-zephyr', self.legacy, {})
        self.p.execution().initialize(self.z2, 'stopped', 'create')
        self.xen.records[self.z2['id']] = self.z2
        self.hardware = [0,1,2,3]
        self.scheduler = 'credit2'
        self.affinities = {0:[{'vcpu':i,'pcpus':[0,1,2,3]} for i in range(4)]}
        self.delegate = self.p.command
        self.command_hook = None
        self.fail_pin = None
        self.p.command = self.command

    def command(self, args, **kwargs):
        if self.command_hook:
            result = self.command_hook(args, kwargs)
            if result is not None:
                return result
        if args[:2] == ['xl','info']:
            return 'nr_cpus : '+str(len(self.hardware))+'\nmax_cpu_id : 3\n'
        if args[:2] == ['xl','cpupool-list']:
            return ('Name CPU list\nPool-0 '+','.join(map(str,self.hardware))+'\n' if '-c' in args else
                    'Name CPUs Sched Active Domain count\nPool-0 '+str(len(self.hardware))+' '+self.scheduler+' y '+str(len(self.xen.rows)+1)+'\n')
        if args[:2] == ['xl','vcpu-pin']:
            self.xen.calls.append(args)
            if self.fail_pin == int(args[3]):
                raise vm.PlatformError('pin failure')
            self.affinities[int(args[2])][int(args[3])]['pcpus'] = list(map(int,args[4].split(',')))
            return ''
        if args[:3] == ['xenova-domain-identity','--domid','0']:
            return json.dumps({'domid':0,'xen_unique_id':'500','state_bits':1})
        if args[:3] == ['xl','list','-c'] or args[:2] == ['xl','vcpu-list']:
            rows = [{'name':'Domain-0','domid':0,'vcpus':4}]
            for row in self.xen.rows:
                name = row['config']['c_info']['name'];record=self.xen.records[name]
                rows.append({'name':name,'domid':row['domid'],'vcpus':record['vcpus']})
                self.affinities.setdefault(row['domid'],[{'vcpu':i,'pcpus':[0,1,2,3]} for i in range(record['vcpus'])])
            if args[1] == 'list':
                return 'Name ID Mem VCPUs State Time(s) Cpupool\n'+''.join(
                    f"{r['name']} {r['domid']} 16 {r['vcpus']} -b---- 1.0 Pool-0\n" for r in rows)
            return 'Name ID VCPU CPU State Time(s) Affinity (Hard / Soft)\n'+''.join(
                f"{r['name']} {r['domid']} {a['vcpu']} 2 -b- 0.0 {','.join(map(str,a['pcpus'])) or 'none'} / all\n"
                for r in rows for a in self.affinities[r['domid']])
        old_masks = {r['config']['c_info']['name']:copy.deepcopy(self.affinities.get(r['domid'])) for r in self.xen.rows}
        result = self.delegate(args, **kwargs)
        if args[:2] == ['xl','create']:
            text = __import__('pathlib').Path(args[2]).read_text()
            match = re.search(r"^cpus = '([0-9,]+)'$", text, re.M)
            for row in self.xen.rows[-1:]:
                count = self.xen.records[row['config']['c_info']['name']]['vcpus']
                self.affinities[row['domid']] = [{'vcpu':i,'pcpus':list(map(int,match[1].split(','))) if match else [0,1,2,3]} for i in range(count)]
        if args[:2] == ['xl','reboot']:
            for row in self.xen.rows:
                if row['domid'] not in self.affinities and old_masks.get(row['config']['c_info']['name']):
                    self.affinities[row['domid']] = old_masks[row['config']['c_info']['name']]
        return result

    def spec(self, revision=None, swap=False):
        return {'expected_policy_revision':revision,'shared_pcpus':[0,3],
                'exclusive':[{'vm_id':self.zid,'pcpu':2 if swap else 1},{'vm_id':self.z2['uuid'],'pcpu':1 if swap else 2}]}

    def policy(self, spec=None, execute=True, key='cpu-v1'):
        return self.m.call('cpu-policy-set', self.spec() if spec is None else spec, execute=execute,idempotency_key=key)

    def prepare(self, **spec):
        return self.m.call('cpu-host-prepare',spec,execute=True)

    def layout(self, **spec):
        return self.m.call('cpu-layout',spec)

    def control(self, record, action='start', key=None):
        with patch.object(vm.os,'sync'):
            return self.m.call(action,{'vm_id':record['uuid']},execute=True,idempotency_key=key)

    def activate(self):
        self.ok(self.policy());self.ok(self.prepare())

    def test_policy_validation_uses_manager_entry_deadline_after_registry_delay(self):
        original=self.p.records
        def delayed(*args,**kwargs):
            self.ticks=14
            return original(*args,**kwargs)
        budgets=[]
        self.command_hook=lambda args,kwargs:budgets.append(kwargs.get('timeout'))
        with patch.object(self.p,'records',side_effect=delayed):
            self.ok(self.policy(execute=False))
        self.assertTrue(budgets)
        self.assertTrue(all(0 < seconds <= 1 for seconds in budgets))
        self.assertFalse(self.p.cpu().policy_path.exists())

    def test_factory_policy_observation_keeps_short_host_prepare_budget(self):
        state={'storage_uuid':uuid4(),'identities':{
            r['id']:{'uuid':r['uuid']} for r in (self.z,self.z2)},
            'cpu_policy_activation':self.spec()}
        (self.p.data/'.first-install-state.json').write_text(json.dumps(state))
        def delayed(args,kwargs):
            self.ticks=2
            return None
        self.command_hook=delayed
        self.fails(self.prepare(timeout_ms=1000),'DEADLINE_EXCEEDED')
        self.assertFalse(self.p.cpu().policy_path.exists())
        self.assertFalse(self.p.cpu().receipt_path.exists())
        self.assertFalse(any(c[:2]==['xl','vcpu-pin'] for c in self.xen.calls))

    def test_policy_expiry_before_publication_preserves_policy_and_journals(self):
        cpu=self.p.cpu();check=cpu.check_unknown
        def delayed(*args):
            check(*args);self.ticks=16
        with patch.object(cpu,'check_unknown',side_effect=delayed),patch.object(self.p,'cpu',return_value=cpu):
            self.fails(self.policy(),'DEADLINE_EXCEEDED')
        self.assertFalse(cpu.policy_path.exists())
        self.assertFalse(cpu.anchor.exists())
        self.assertEqual(cpu.operations(),[])

    def test_plan_has_no_files_pins_or_identity_changes_and_revision_retry(self):
        before = {p.relative_to(self.p.data):p.read_bytes() for p in self.p.data.rglob('*.json')}
        planned = self.ok(self.policy(execute=False))
        self.assertTrue(planned['plan']);self.assertFalse(self.p.cpu().policy_path.exists())
        self.assertEqual(before,{p.relative_to(self.p.data):p.read_bytes() for p in self.p.data.rglob('*.json')})
        self.ok(self.policy());self.assertTrue(self.policy()['replayed'])
        self.fails(self.policy(self.spec(7),key='stale'),'TARGET_CHANGED')
        self.fails(self.policy(self.spec(1,True)),'IDEMPOTENCY_CONFLICT')
        self.assertFalse(any(c[:2]==['xl','vcpu-pin'] for c in self.xen.calls))

    def test_invalid_reservations_cores_uuid_profile_offline(self):
        for change,code in [({'shared_pcpus':[0,4]},'INVALID_SPEC'),({'shared_pcpus':[0,1,3]},'INVALID_SPEC'),
                            ({'exclusive':[{'vm_id':self.zid,'pcpu':1},{'vm_id':self.z2['uuid'],'pcpu':1}]},'INVALID_SPEC'),
                            ({'exclusive':[{'vm_id':'bad','pcpu':1}]},'INVALID_SPEC'),
                            ({'exclusive':[{'vm_id':uuid4(),'pcpu':1}]},'VM_NOT_FOUND'),
                            ({'exclusive':[{'vm_id':self.linux['uuid'],'pcpu':1}]},'RESOURCE_UNSUPPORTED')]:
            self.fails(self.policy(self.spec()|change),code)
        self.hardware=[0,2,3];self.fails(self.policy(),'RESOURCE_UNSUPPORTED')
        self.scheduler='credit';self.fails(self.layout(),'RESOURCE_UNSUPPORTED')

    def test_unconfigured_and_absent_are_not_compliance_success(self):
        result=self.ok(self.layout());self.assertFalse(result['configured']);self.assertFalse(result['host']['ready'])
        for v in result['vms']:
            self.assertIsNone(v['affinity_matches']);self.assertIsNone(v['exclusive_verified'])
            self.assertIsNone(v['observed']['hard_affinity'])
        self.activate();result=self.ok(self.layout(vm_id=self.zid))
        self.assertTrue(result['host']['ready']);self.assertEqual(len(result['vms']),1)
        self.assertEqual(result['vms'][0]['observed']['presence'],'absent')
        self.assertIsNone(result['vms'][0]['affinity_matches'])

    def test_preset_and_swapped_policy_keep_vcpu_assets_and_firmware(self):
        records=copy.deepcopy(self.p.records(validate_assets=False))
        firmware=(self.zdir/'instance.json').read_bytes()
        self.activate()
        self.ok(self.policy(self.spec(1,True),key='swap'));self.ok(self.prepare())
        for r,core in ((self.z,2),(self.z2,1)):
            self.ok(self.control(r));v=self.ok(self.layout(vm_id=r['uuid']))['vms'][0]
            self.assertEqual(v['observed']['hard_affinity'],[{'vcpu':0,'pcpus':[core]}])
            self.assertTrue(v['affinity_matches']);self.assertTrue(v['exclusive_verified']);self.assertIsNotNone(v['execution_id'])
        self.assertEqual(records,self.p.records(validate_assets=False));self.assertEqual(firmware,(self.zdir/'instance.json').read_bytes())
        self.assertEqual(len(self.affinities[0]),4)

    def test_all_linux_vcpus_share_mask_after_resource_update(self):
        self.activate()
        state=self.ok(self.m.call('get',{'vm_id':self.linux['uuid']}))['vm']
        spec={'vm_id':self.linux['uuid'],'vcpus':4,'memory_mib':512,
              'expected_lifecycle_generation':state['generation'],'expected_configuration_sha256':state['configuration_sha256']}
        self.ok(self.m.call('update-resources',spec,execute=True,idempotency_key='more-vcpus'))
        self.linux=self.p.records(validate_assets=False)[self.linux['id']];self.xen.records[self.linux['id']]=self.linux
        self.ok(self.control(self.linux));v=self.ok(self.layout(vm_id=self.linux['uuid']))['vms'][0]
        self.assertEqual(v['observed']['hard_affinity'],[{'vcpu':i,'pcpus':[0,3]} for i in range(4)])
        self.assertIsNone(v['exclusive_verified'])

    def test_missing_ready_and_changed_boot_block_start_without_mutating_dom0(self):
        self.ok(self.policy());self.fails(self.control(self.linux),'CPU_NOT_READY')
        self.ok(self.prepare());self.host=foundation.HOST_B
        before=copy.deepcopy(self.affinities)
        self.fails(self.control(self.linux),'CPU_NOT_READY');self.assertEqual(before,self.affinities)

    def test_already_running_drift_is_observed_and_stop_remains_available(self):
        self.activate();self.ok(self.control(self.linux))
        domid=self.ok(self.layout(vm_id=self.linux['uuid']))['vms'][0]['observed']['domid']
        self.affinities[domid][0]['pcpus']=[0,1,3]
        result=self.ok(self.layout());self.assertTrue(result['issues'])
        count=len([c for c in self.xen.calls if c[:2]==['xl','create']])
        self.fails(self.control(self.linux),'CPU_APPLY_INCOMPLETE')
        self.p.cpu().policy_path.write_text('broken')
        self.ok(self.control(self.linux,'stop'))
        self.assertEqual(count,len([c for c in self.xen.calls if c[:2]==['xl','create']]))

    def test_unmanaged_paused_domain_conflict_and_observation_failure(self):
        self.activate();self.ok(self.control(self.z))
        from test_xenova_platform import domain
        unknown=copy.deepcopy(self.linux);unknown.update(id='outside',name='outside',uuid=uuid4())
        self.xen.records['outside']=unknown
        row=domain(unknown,self.p.data/'instances'/'outside'/'root.raw',90);self.xen.rows.append(row)
        result=self.ok(self.layout(vm_id=self.zid));self.assertFalse(result['vms'][0]['exclusive_verified'])
        self.assertEqual(result['vms'][0]['issues'][0]['conflicts'][0]['domid'],90)
        self.command_hook=lambda a,k:'bad output' if a[:2]==['xl','vcpu-list'] else None
        self.fails(self.layout(),'OBSERVATION_UNAVAILABLE')

    def test_domain_set_incarnation_and_deadline_change_are_errors(self):
        calls=0
        def hook(a,k):
            nonlocal calls
            if a[:3]==['xenova-domain-identity','--domid','0']:
                calls+=1
                return json.dumps({'domid':0,'xen_unique_id':str(500+calls),'state_bits':1})
        self.command_hook=hook;self.fails(self.layout(),'TARGET_CHANGED')
        def timeout(a,k):
            if a[:2]==['xl','info']:raise subprocess.TimeoutExpired(a,k['timeout'])
        self.command_hook=timeout;self.fails(self.layout(),'DEADLINE_EXCEEDED')
        self.fails(self.layout(timeout_ms=0),'INVALID_SPEC')

    def test_interrupted_policy_publication_and_missing_policy_fail_closed(self):
        def checkpoint(phase,value):
            if phase=='cpu-policy-prepared':raise Interrupted()
        self.p.execution_checkpoint=checkpoint
        with self.assertRaises(Interrupted):self.policy()
        self.fails(self.control(self.linux),'CPU_POLICY_INVALID')
        self.p.execution_checkpoint=lambda *args:None;self.ok(self.policy());self.ok(self.prepare())
        self.p.cpu().policy_path.unlink();self.fails(self.layout(),'CPU_POLICY_INVALID')

    def test_partial_dom0_apply_retry_restore_and_no_implicit_repins(self):
        self.ok(self.policy());self.fail_pin=2
        self.fails(self.prepare(),'OBSERVATION_UNAVAILABLE')
        receipt=self.p.cpu().read(self.p.cpu().receipt_path)
        self.assertEqual(receipt['phase'],'failed');self.assertEqual(receipt['completed_vcpus'],[0,1])
        self.fails(self.policy(self.spec(1,True),key='swap'),'RECONCILE_REQUIRED')
        self.fail_pin=None;self.ok(self.prepare());self.ok(self.prepare(restore=True))
        self.assertFalse(self.ok(self.layout())['host']['ready'])
        self.assertEqual(self.affinities[0],[{'vcpu':i,'pcpus':[0,1,2,3]} for i in range(4)])
        self.ok(self.prepare())

    def test_policy_change_refuses_running_or_uncertain_launch(self):
        self.activate();self.ok(self.control(self.linux))
        self.fails(self.policy(self.spec(1,True),key='swap'),'VM_NOT_STOPPED')
        self.ok(self.control(self.linux,'stop'))
        self.p.execution().prepare_launch(self.linux)
        self.fails(self.policy(self.spec(1,True),key='swap'),'RECONCILE_REQUIRED')

    def test_cpu_query_lock_is_bounded_and_has_null_operation(self):
        with operation_lock(self.p.runtime):self.fails(self.layout(),'OPERATION_IN_PROGRESS')
        response=self.layout();self.ok(response);self.assertIsNone(response['operation_id'])
        self.assertFalse(response['replayed']);self.assertFalse(response['data']['configured'])

    def test_linux_toolstack_reboot_binds_new_identity_with_cpu_context(self):
        self.activate();self.ok(self.control(self.linux))
        old=self.p.execution().run_record(self.linux)
        self.cooperative_removes=False
        self.ok(self.control(self.linux,'reboot'))
        new=self.p.execution().run_record(self.linux)
        self.assertNotEqual(old['execution_id'],new['execution_id']);self.assertEqual(old['cpu_policy'],new['cpu_policy'])
        self.assertEqual(new['cpu_verification'],'complete')

    def test_created_domain_with_bad_affinity_is_incomplete_not_absent_and_not_repeated(self):
        self.activate()
        def corrupt(phase,value):
            if phase=='launch-observed':self.affinities[value['domid']][0]['pcpus']=[0,1,2,3]
        self.p.execution_checkpoint=corrupt
        self.fails(self.control(self.linux,key='launch-once'),'CPU_APPLY_INCOMPLETE')
        run=self.p.execution().run_record(self.linux)
        self.assertEqual(run['phase'],'observed');self.assertEqual(run['cpu_verification'],'incomplete')
        self.assertIsNotNone(run['xen_unique_id'])
        self.p.execution_checkpoint=lambda *args:None;count=len(self.xen.rows)
        self.fails(self.control(self.linux,key='launch-once'),'CPU_APPLY_INCOMPLETE')
        self.assertEqual(count,len(self.xen.rows));self.ok(self.control(self.linux,'stop'))

    def test_autostart_recovery_and_zephyr_cooperative_reboot_use_same_layout(self):
        # Upgrade only this fixture Zephyr, then activate the policy before starting.
        self.vm_id=self.zid
        self.ok(network.NetworkPlatformTest.transition(self, network.NetworkPlatformTest.spec(self)))
        self.vm_id=self.linux['uuid']
        self.activate()
        record=self.p.records(validate_assets=False)[self.z['id']];self.z=record;self.xen.records[record['id']]=record
        self.ok(self.m.call('autostart-set',{'vm_id':record['uuid'],'value':'on'},execute=True))
        record=self.p.records(validate_assets=False)[record['id']];self.xen.records[record['id']]=record
        self.p.execution().accept(record,{'vm_id':record['uuid'],'action':'start','force':False},'boot-start',source='boot-policy')
        with redirect_stdout(io.StringIO()):self.p.autostart({record['id']:record},[])
        self.ok(self.control(record,'reboot'))
        self.assertTrue(self.ok(self.layout(vm_id=self.zid))['vms'][0]['exclusive_verified'])
        self.ok(self.control(self.linux))
        self.vm_id=self.linux['uuid']
        spec=foundation.FoundationTest.spec(self)
        with patch.object(vm.os,'sync'):
            result=self.m.call('recover_once',spec,execute=True)
        self.ok(result);self.assertEqual(result['data']['attempt']['cpu_policy']['policy_revision'],1)
        self.assertTrue(self.ok(self.layout(vm_id=self.vm_id))['vms'][0]['affinity_matches'])

    def state(self):
        return self.ok(self.m.call('get',{'vm_id':getattr(self,'vm_id',self.linux['uuid'])}))['vm']

    def transition(self,*args,**kwargs):
        return network.NetworkPlatformTest.transition(self,*args,**kwargs)

    def test_factory_preset_resolves_identity_plan_and_no_role_or_domid(self):
        path=self.p.data/'preset.json'
        path.write_text(json.dumps({'shared_pcpus':[0,3],'exclusive_instances':[
            {'instance_id':'a','pcpu':2},{'instance_id':'b','pcpu':1}]}))
        identities={'a':{'uuid':self.zid},'b':{'uuid':self.z2['uuid']}}
        spec=factory_activation(identities,path)
        self.assertEqual(spec['exclusive'],sorted(self.spec(swap=True)['exclusive'],key=lambda v:v['vm_id']))
        path.unlink();self.assertIsNone(factory_activation(identities,path))


    def test_published_policy_interruption_retry_closes_without_republish(self):
        def checkpoint(phase,value):
            if phase=='cpu-policy-published':raise Interrupted()
        self.p.execution_checkpoint=checkpoint
        with self.assertRaises(Interrupted):self.policy()
        before=self.p.cpu().policy_path.read_bytes()
        self.fails(self.prepare(),'RECONCILE_REQUIRED')
        self.p.execution_checkpoint=lambda *args:None
        self.assertTrue(self.policy()['replayed'])
        self.assertEqual(before,self.p.cpu().policy_path.read_bytes());self.ok(self.prepare())

    def test_incomplete_control_and_recovery_guard_cpu_policy_change(self):
        self.activate()
        self.p.execution().accept(self.linux,{'vm_id':self.linux['uuid'],'action':'stop','force':False},'unfinished-stop')
        self.fails(self.policy(self.spec(1,True),key='swap'),'RECONCILE_REQUIRED')

    def test_stop_shutdown_remain_usable_when_cpu_readiness_is_lost(self):
        self.activate();self.ok(self.control(self.linux))
        self.p.cpu().receipt_path.write_text('unreadable receipt')
        self.fails(self.control(self.z),'CPU_POLICY_INVALID')
        with patch.object(vm.os,'sync'),redirect_stdout(io.StringIO()):
            self.p.stop_many([self.linux])
        self.assertEqual(self.xen.rows,[])

    def test_retire_restore_purge_do_not_release_uuid_reservation(self):
        self.activate();before=self.p.cpu().policy_path.read_bytes()
        self.ok(self.m.call('retire',{'vm_id':self.zid},execute=True,idempotency_key='retire-z'))
        self.assertIn(self.zid,[v['vm_id'] for v in self.ok(self.layout())['reservations']])
        self.ok(self.m.call('restore',{'vm_id':self.zid},execute=True,idempotency_key='restore-z'))
        self.ok(self.control(self.z))
        self.ok(self.m.call('stop',{'vm_id':self.zid,'force':True},execute=True))
        self.ok(self.m.call('retire',{'vm_id':self.zid},execute=True,idempotency_key='retire-z-again'))
        self.ok(self.m.call('purge',{'vm_id':self.zid,'confirm_vm_id':self.zid},execute=True,idempotency_key='purge-z'))
        self.assertEqual(before,self.p.cpu().policy_path.read_bytes())
        self.assertIn(self.zid,[v['vm_id'] for v in self.ok(self.layout())['reservations']])

    def test_linux_reboot_wrong_recreated_mask_keeps_identity_and_rejects_completion(self):
        self.activate();self.ok(self.control(self.linux));self.cooperative_removes=False
        def corrupt(phase,value):
            if phase=='launch-observed':self.affinities[value['domid']][0]['pcpus']=[1]
        self.p.execution_checkpoint=corrupt
        self.fails(self.control(self.linux,'reboot'),'CPU_APPLY_INCOMPLETE')
        self.p.execution_checkpoint=lambda *args:None
        self.assertEqual(self.p.execution().run_record(self.linux)['cpu_verification'],'incomplete')
        self.ok(self.control(self.linux,'stop'))

    def test_identity_helper_timeout_is_not_empty_affinity_success(self):
        def timeout(args,kwargs):
            if args[0]=='xenova-domain-identity':raise subprocess.TimeoutExpired(args,1)
        self.command_hook=timeout;self.fails(self.layout(),'DEADLINE_EXCEEDED')

    def test_cli_query_and_policy_plan_emit_one_envelope(self):
        import xenova_management_cli as cli
        from pathlib import Path
        path=self.p.data/'request.json';path.write_text(json.dumps(self.spec()))
        for argv in (['cpu-layout','--json'],['cpu-policy-set','--spec-file',str(path),'--idempotency-key','cli-plan','--json']):
            out=io.StringIO()
            with redirect_stdout(out):cli.main(argv,manager=self.m)
            value=json.loads(out.getvalue());self.assertTrue(value['ok']);self.assertIsNone(value['operation_id'])
        self.assertFalse(self.p.cpu().policy_path.exists())

    def test_dom0_identity_cli_is_explicit_and_guest_default_stays_restricted(self):
        import xenova_domain_identity as helper
        with self.assertRaises(ValueError):helper.identity(0)
        with patch.object(helper,'identity',return_value={'domid':0}),redirect_stdout(io.StringIO()):
            helper.main(['--domid','0','--allow-dom0'])
            helper.identity.assert_called_once_with(0,allow_dom0=True)

    def test_active_policy_corruption_read_error_and_zero_uuid_fail_closed(self):
        self.activate()
        path=self.p.cpu().policy_path
        original=type(path).read_text
        def denied(p,*args,**kwargs):
            if p==path:raise PermissionError('denied')
            return original(p,*args,**kwargs)
        with patch.object(type(path),'read_text',new=denied):
            self.fails(self.control(self.linux),'CPU_POLICY_INVALID')
        value=json.loads(path.read_text());value['shared_pcpus']=[0,1,3];path.write_text(json.dumps(value))
        self.fails(self.control(self.linux),'CPU_POLICY_INVALID')
        self.fails(self.policy(self.spec(1)|{'exclusive':[{'vm_id':'00000000-0000-0000-0000-000000000000','pcpu':1}]}),'INVALID_SPEC')

    def test_first_boot_activation_is_attached_once_to_durable_identity_plan(self):
        import test_xenova_lifecycle as life
        f=life.FactoryAndPackagingTest();f.setUp();self.addCleanup(f.doCleanups)
        board=f.board();preset=board.root/'etc/xenova/cpu-policy-preset.json';preset.parent.mkdir()
        preset.write_text(json.dumps({'shared_pcpus':[0,3],'exclusive_instances':[{'instance_id':'zephyr-dev','pcpu':2}]}))
        board.p.register();path=board.data/'.first-install-state.json';before=json.loads(path.read_text())
        self.assertEqual(before['cpu_policy_activation']['exclusive'],[{'vm_id':before['identities']['zephyr-dev']['uuid'],'pcpu':2}])
        preset.write_text(json.dumps({'shared_pcpus':[0,3],'exclusive_instances':[{'instance_id':'zephyr-dev','pcpu':1}]}))
        board.p.register();self.assertEqual(before,json.loads(path.read_text()))

    def test_existing_factory_plan_does_not_get_policy_on_upgrade(self):
        import test_xenova_lifecycle as life
        f=life.FactoryAndPackagingTest();f.setUp();self.addCleanup(f.doCleanups)
        board=f.board();board.p.register();path=board.data/'.first-install-state.json';before=path.read_bytes()
        preset=board.root/'etc/xenova/cpu-policy-preset.json';preset.parent.mkdir()
        preset.write_text(json.dumps({'shared_pcpus':[0,3],'exclusive_instances':[{'instance_id':'zephyr-dev','pcpu':2}]}))
        board.p.register();self.assertEqual(before,path.read_bytes());self.assertNotIn('cpu_policy_activation',json.loads(before))

    def test_interrupted_policy_revision_blocks_new_launch_until_original_retry(self):
        self.activate()
        def checkpoint(phase,value):
            if phase=='cpu-policy-prepared':raise Interrupted()
        self.p.execution_checkpoint=checkpoint
        with self.assertRaises(Interrupted):self.policy(self.spec(1,True),key='swap')
        self.assertFalse(self.ok(self.layout())['host']['ready'])
        self.fails(self.control(self.linux),'RECONCILE_REQUIRED')
        self.assertEqual(self.xen.rows,[])
        self.p.execution_checkpoint=lambda *args:None
        self.ok(self.policy(self.spec(1,True),key='swap'));self.ok(self.prepare())
        self.ok(self.control(self.z))
        self.assertEqual(self.ok(self.layout(vm_id=self.zid))['vms'][0]['observed']['hard_affinity'],[{'vcpu':0,'pcpus':[2]}])

    def test_factory_publication_retries_after_postpublish_interruption(self):
        state={'schema':1,'storage_uuid':uuid4(),'identities':{'a':{'uuid':self.zid},'b':{'uuid':self.z2['uuid']}},
               'cpu_policy_activation':self.spec()}
        (self.p.data/'.first-install-state.json').write_text(json.dumps(state))
        def checkpoint(phase,value):
            if phase=='cpu-policy-published':raise Interrupted()
        self.p.execution_checkpoint=checkpoint
        with self.assertRaises(Interrupted):self.prepare()
        self.p.execution_checkpoint=lambda *args:None
        self.ok(self.prepare());self.assertTrue(self.ok(self.layout())['host']['ready'])
        self.assertEqual(len(self.p.cpu().operations()),1)

    def test_handoff_envelope_examples_preserve_null_and_mismatch_semantics(self):
        from first_install_fixture import ROOT
        values=json.loads((ROOT/'platform/tests/fixtures/cpu-layout-responses.json').read_text())
        envelope={'schema_version','ok','request_id','operation_id','replayed','data','error'}
        for value in values.values():
            self.assertEqual(set(value),envelope);self.assertIsNone(value['operation_id'])
        self.assertTrue(values['running']['data']['vms'][0]['exclusive_verified'])
        self.assertIsNone(values['stopped']['data']['vms'][0]['affinity_matches'])
        self.assertFalse(values['unconfigured']['data']['configured'])
        self.assertTrue(values['mismatch']['ok']);self.assertFalse(values['mismatch']['data']['vms'][0]['exclusive_verified'])
        self.assertEqual(values['observation_failure']['error']['code'],'OBSERVATION_UNAVAILABLE')


    def test_exclusive_conflicts_report_actual_widened_masks_not_only_reserved_core(self):
        self.activate();self.ok(self.control(self.z))
        domid=self.ok(self.layout(vm_id=self.zid))['vms'][0]['observed']['domid']
        self.affinities[domid][0]['pcpus']=[0,1,3]
        result=self.ok(self.layout(vm_id=self.zid))['vms'][0]
        self.assertFalse(result['affinity_matches']);self.assertFalse(result['exclusive_verified'])
        conflicts=next(i for i in result['issues'] if i['code']=='EXCLUSIVE_CONFLICT')['conflicts']
        self.assertEqual(len(conflicts),4)
        self.assertTrue(all(v['domid']==0 and v['pcpus']==[0,3] for v in conflicts))


    def test_restoring_old_policy_file_is_not_a_valid_revision_rollback(self):
        self.activate();old=self.p.cpu().policy_path.read_bytes()
        self.ok(self.policy(self.spec(1,True),key='swap'))
        self.p.cpu().policy_path.write_bytes(old)
        self.fails(self.layout(),'CPU_POLICY_INVALID')
        self.fails(self.control(self.linux),'CPU_POLICY_INVALID')
        self.assertEqual(self.xen.rows,[])

    def test_cpu_layout_execution_id_uses_existing_lifecycle_generation_contract(self):
        self.activate();self.ok(self.control(self.linux))
        e=self.p.execution();record=e.run_record(self.linux);record['lifecycle_generation']+=1
        e.write(e._path(self.p.runtime,'executions',self.linux['uuid']),record)
        value=self.ok(self.layout(vm_id=self.linux['uuid']))['vms'][0]
        self.assertIsNone(value['execution_id']);self.assertTrue(value['affinity_matches'])

    def test_static_unit_orders_prepare_without_restart_or_stop_propagation(self):
        import configparser
        from first_install_fixture import FILES,ROOT
        def load(name):
            p=configparser.ConfigParser();p.read(FILES/name);return p
        unit=load('xenova-cpu-prepare.service');vms=load('xenova-vms.service')
        self.assertIn('xenova-cpu-prepare.service',vms['Unit']['Wants'].split())
        self.assertIn('xenova-cpu-prepare.service',vms['Unit']['After'].split())
        for name in ('Requires','PartOf','BindsTo'):
            self.assertNotIn('xenova-cpu-prepare.service',vms['Unit'].get(name,'').split())
        self.assertNotIn('ExecStop',unit['Service']);self.assertNotIn('Install',unit)
        from first_install_fixture import platform_root
        import assemble
        target=self.p.data/'assembled-root';platform_root(target)
        assemble.platform_root_ok(target)
        (target/'usr/lib/xenova/xenova_cpu.py').write_text('stale')
        with self.assertRaisesRegex(ValueError,'xenova_cpu.py'):assemble.platform_root_ok(target)


if __name__=='__main__':unittest.main()
