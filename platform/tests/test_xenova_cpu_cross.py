"""정적 CPU 배치와 자원·수습·host·lifecycle의 교차를 실제 원장으로 확인한다."""
import copy
import errno
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_xenova_cpu as fixtures
import test_xenova_recovery_foundation as foundation
from first_install_contract import uuid4
from xenova_runtime import PlatformError
import xenova_creation as creation
from xenova_cpu import policy_candidate
from xenova_execution import ExecutionError


class CPUCrossTest(unittest.TestCase):
    def case(self):
        c=fixtures.CPUTest('runTest');c.setUp();self.addCleanup(c.doCleanups)
        return c

    def spec(self,c,revision=None,pinned=False):
        return {'expected_policy_revision':revision,'shared_pcpus':[0,3],
                'placements':[{'vm_id':c.linux['uuid'],'mode':'shared','pcpus':[3]}] if pinned else []}

    def evidence(self,name,value):
        folder=os.environ.get('XENOVA_STAGE4_EVIDENCE')
        if folder:
            with (Path(folder)/(name+'.json')).open('x') as stream:json.dump(value,stream,indent=2)

    def resize(self,c,count,key):
        view=c.ok(c.m.call('get',{'vm_id':c.linux['uuid']}))['vm']
        c.ok(c.m.call('update-resources',{'vm_id':c.linux['uuid'],'vcpus':count,'memory_mib':512,
            'expected_lifecycle_generation':view['generation'],
            'expected_configuration_sha256':view['configuration_sha256']},execute=True,idempotency_key=key))
        c.linux=c.p.records(validate_assets=False)[c.linux['id']]
        c.xen.records[c.linux['id']]=c.linux

    def test_pinned_linux_resize_2_4_2_then_unpin(self):
        c=self.case();self.resize(c,2,'initial-two')
        c.ok(c.policy(self.spec(c,pinned=True)));c.ok(c.prepare());observations=[]
        for index,count in enumerate((2,4,2)):
            if index:self.resize(c,count,'resize-'+str(index))
            c.ok(c.control(c.linux,key='resize-start-'+str(index)))
            row=c.ok(c.layout(vm_id=c.linux['uuid']))['vms'][0]
            self.assertEqual(row['observed']['hard_affinity'],[{'vcpu':v,'pcpus':[3]} for v in range(count)])
            self.assertIn("cpus = '3'",(c.p.runtime/(c.linux['id']+'.cfg')).read_text())
            observations.append(row);c.ok(c.control(c.linux,'stop',key='resize-stop-'+str(index)))
        c.ok(c.policy(self.spec(c,1),key='resize-unpin'));c.ok(c.prepare())
        c.ok(c.control(c.linux,key='resize-default'))
        row=c.ok(c.layout(vm_id=c.linux['uuid']))['vms'][0]
        self.assertEqual(row['observed']['hard_affinity'],[{'vcpu':v,'pcpus':[0,3]} for v in range(2)])
        self.evidence('X-CPU-resize',{'pinned':observations,'unpinned':row})

    def test_policy_or_receipt_error_blocks_launch_but_exact_manager_stop_and_recovery_work(self):
        for kind in ('policy-unreadable','receipt-unreadable','receipt-applying'):
            with self.subTest(kind=kind):
                c=self.case();c.ok(c.policy(self.spec(c,pinned=True)));c.ok(c.prepare())
                c.ok(c.control(c.linux,key='before-error'));cpu=c.p.cpu()
                recovery=foundation.FoundationTest.spec(c)
                path=cpu.policy_path if kind=='policy-unreadable' else cpu.receipt_path
                original=path.read_bytes();effects=copy.deepcopy(c.xen.calls)
                if kind=='receipt-applying':
                    value=json.loads(original);value['phase']='applying';path.write_text(json.dumps(value))
                else:path.write_text('unreadable fixture bytes')
                rejected_recovery=c.m.call('recover_once',recovery,execute=True)
                self.assertFalse(rejected_recovery['ok'],rejected_recovery)
                self.assertIn(rejected_recovery['error']['code'],('CPU_POLICY_INVALID','CPU_NOT_READY'))
                rejected=c.control(c.linux,key='blocked-start')
                self.assertFalse(rejected['ok'],rejected)
                self.assertIn(rejected['error']['code'],('CPU_POLICY_INVALID','CPU_NOT_READY'))
                stopped=c.control(c.linux,'stop',key='exact-stop')
                c.ok(stopped)
                self.assertFalse(c.xen.rows)
                for action in ('create','vcpu-pin'):
                    self.assertEqual([v for v in effects if v[:2]==['xl',action]],
                                     [v for v in c.xen.calls if v[:2]==['xl',action]])
                path.write_bytes(original);c.ok(c.prepare())
                c.ok(c.control(c.linux,key='after-recovery'))
                self.evidence('X-CPU-stop-'+kind,{'rejected':rejected,'stop':stopped,
                    'recovery_rejected':rejected_recovery,
                    'recovered':c.ok(c.layout(vm_id=c.linux['uuid']))})

    def test_empty_placements_new_host_keeps_old_factory_ticket_inactive(self):
        c=self.case();c.activate()
        (c.p.data/'.first-install-state.json').write_text(json.dumps({'storage_uuid':uuid4(),
            'identities':{r['id']:{'uuid':r['uuid']} for r in (c.z,c.z2)},'cpu_policy_activation':c.spec()}))
        c.ok(c.policy(self.spec(c,1),key='all-unpinned'));c.ok(c.prepare())
        cpu=c.p.cpu();policy=cpu.policy_path.read_bytes();history=cpu.operations()
        c.host=foundation.HOST_B;c.p.execution().initialize_host()
        c.ok(c.prepare());self.assertEqual(cpu.policy_path.read_bytes(),policy)
        self.assertEqual(cpu.operations(),history);self.assertEqual(cpu.policy()['placements'],[])
        c.ok(c.control(c.linux,key='new-host-default'))
        observed=c.ok(c.layout(vm_id=c.linux['uuid']))
        self.assertEqual(observed['host_boot_id'],foundation.HOST_B)
        self.assertEqual(observed['reservations'],[])
        self.assertEqual(observed['vms'][0]['observed']['hard_affinity'],[{'vcpu':0,'pcpus':[0,3]}])
        self.evidence('X-CPU-empty-new-host',observed)

    def test_policy_and_receipt_read_EIO_do_not_require_cpu_repair_before_exact_stop(self):
        for kind in ('policy','receipt'):
            with self.subTest(kind=kind):
                c=self.case();c.activate();c.ok(c.control(c.linux,key='before-EIO'))
                recovery=foundation.FoundationTest.spec(c);cpu=c.p.cpu()
                target=cpu.policy_path if kind=='policy' else cpu.receipt_path
                before=target.read_bytes();read=Path.read_text
                def failed(path,*args,**kwargs):
                    if path==target:raise OSError(errno.EIO,'owned CPU read fault')
                    return read(path,*args,**kwargs)
                with patch.object(Path,'read_text',failed):
                    recovered=c.m.call('recover_once',recovery,execute=True)
                    c.fails(recovered,'CPU_POLICY_INVALID')
                    c.fails(c.control(c.linux,key='EIO-blocked-start'),'CPU_POLICY_INVALID')
                    stopped=c.control(c.linux,'stop',key='EIO-exact-stop');c.ok(stopped)
                    self.assertFalse(c.xen.rows)
                self.assertEqual(target.read_bytes(),before)
                c.ok(c.prepare());c.ok(c.control(c.linux,key='EIO-recovered-start'))
                self.evidence('X-CPU-read-EIO-'+kind,{'stop':stopped,'recovery_rejected':recovered})

    def test_failed_create_settlement_then_actual_policy_change_and_normal_start(self):
        c=self.case();spec={k:v for k,v in c.f.spec.items() if k!='ipv4'}
        spec.update(vm_id=uuid4(),name='failed-before-workspace')
        def fail(*args,**kwargs):raise PlatformError('owned pre-workspace failure')
        with patch.object(c.m,'preparer',fail):
            failed=c.m.call('create',spec,execute=True)
        self.assertFalse(failed['ok'],failed)
        op=next(v for v in c.m._operations() if v['vm_id']==spec['vm_id'])
        c.fails(c.policy(self.spec(c),key='after-failure'),'OPERATION_IN_PROGRESS')
        request={'operation_id':op['operation_id'],'vm_id':op['vm_id'],
                 'expected_spec_sha256':op['spec_hash'],'purpose':'discard-private'}
        command=c.p.command;iterate=Path.iterdir
        def external(args,**kwargs):
            if args[:2]==['losetup','--json']:return '{"loopdevices":[]}'
            return command(args,**kwargs)
        def processes(path):
            if path==Path('/proc'):yield Path('/proc')/str(os.getpid())
            else:yield from iterate(path)
        with patch.object(c.p,'command',external),patch.object(Path,'iterdir',processes):
            diagnostic=c.ok(c.m.call('operation-reconcile',request))
            settled=c.ok(c.m.call('operation-reconcile',request|{'expected_context_sha256':diagnostic['context_sha256'],
                'reconciliation_id':uuid4()},execute=True))
        after=next(v for v in c.m._operations() if v['vm_id']==spec['vm_id'])
        self.assertFalse(creation.holds(c.m,after));self.assertEqual(after['error'],op['error'])
        self.assertEqual(after['status'],op['status'])
        c.ok(c.policy(self.spec(c),key='after-failure'));c.ok(c.prepare())
        c.ok(c.control(c.linux,key='settled-normal-start'))
        self.evidence('X-CPU-settlement',{'failed':failed,'settled':settled,'original':after,
            'normal':c.ok(c.layout(vm_id=c.linux['uuid']))})

    def test_prejournal_restore_lease_blocks_policy_writer_then_releases(self):
        c=self.case();c.activate()
        c.ok(c.m.call('retire',{'vm_id':c.linux['uuid']},execute=True,idempotency_key='lease-retire'))
        entered,release=threading.Event(),threading.Event();out=[];request=c.m.fork();command=request.p.command
        def delayed(args,**kwargs):
            if args[0]=='e2fsck':
                entered.set()
                if not release.wait(10):raise RuntimeError('restore barrier not released')
            return command(args,**kwargs)
        with patch.object(request.p,'command',delayed):
            worker=threading.Thread(target=lambda:out.append(request.call('restore',{'vm_id':c.linux['uuid']},
                execute=True,idempotency_key='prejournal-restore')));worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(any(v['idempotency_key']=='prejournal-restore' for v in c.m._operations()))
                c.fails(c.policy(self.spec(c,1),key='lease-policy'),'OPERATION_IN_PROGRESS')
            finally:release.set();worker.join(15)
        self.assertFalse(worker.is_alive());c.ok(out[0])
        c.ok(c.policy(self.spec(c,1),key='lease-policy'));c.ok(c.prepare())
        c.ok(c.control(c.linux,key='after-lease'))

    def test_require_scans_history_only_at_entry_and_after_observation(self):
        c=self.case();c.activate();cpu=c.p.cpu()
        with patch.object(cpu,'operations',wraps=cpu.operations) as scans,patch.object(cpu,'observe',wraps=cpu.observe) as observed:
            context=cpu.require(c.linux,c.p.clock()+15)
        self.assertTrue(context['configured']);self.assertEqual(scans.call_count,2)
        self.assertEqual(observed.call_count,1)

    def test_representative_requests_keep_metadata_and_observation_cost_bounds(self):
        from benchmark_cpu_cost import one
        for action,policy,journals,observations in (('start',18,18,2),('get',0,0,0),('recover_once',20,20,3)):
            with self.subTest(action=action):
                row=one(action,4,True)
                self.assertLessEqual(row['cpu_files'].get('policy.parse',0),policy)
                self.assertLessEqual(row['counts'].get('history_scans',0),journals)
                self.assertEqual(row['counts'].get('full_cpu_observations',0),observations)
                self.assertTrue(row['ok'])

    def test_prepared_journal_after_observation_invalidates_unchanged_policy(self):
        c=self.case();c.activate();cpu=c.p.cpu();observe=cpu.observe;opid=uuid4()
        request=self.spec(c,1)
        def changed(deadline):
            value=observe(deadline)
            cpu.e.write(cpu.journals/(opid+'.json'),{'schema_version':'2.0','operation_id':opid,
                'idempotency_key':'late-prepared','request':request,'base_revision':1,
                'candidate':policy_candidate(request,opid,'Pool-0'),'phase':'prepared'})
            return value
        with patch.object(cpu,'observe',changed):
            with self.assertRaises(ExecutionError) as raised:cpu.require(c.linux,c.p.clock()+15)
        self.assertEqual(raised.exception.error['code'],'CPU_NOT_READY')
        self.assertEqual(cpu.policy()['policy_revision'],1)
        current=c.ok(c.layout());self.assertFalse(current['host']['ready'])
        self.assertIn({'code':'POLICY_PUBLICATION_PENDING','operation_id':opid},current['issues'])
