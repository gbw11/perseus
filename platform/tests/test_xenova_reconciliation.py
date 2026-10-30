"""D03 실제 receipt/Manager·잠금·저장 경계. Xen/Store와 실패 시점만 대역이다."""
import copy
import errno
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
import xenova_execution as execution
import xenova_vm as vm
from xenova_runtime import operation_lock
from first_install_contract import uuid4

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / 'platform/scripts/provision-xenova-telemetry.py'


def bootstrap_module():
    spec = importlib.util.spec_from_file_location('d03_bootstrap', BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoreBoundary:
    """메모리 Store 경계. 비교 로직은 실제 XenStore.inspect를 실행한다."""
    channel = 'telemetry'
    quiescent = True

    def __init__(self, module):
        self.module = module
        self.nodes, self.acls, self.writes = {}, {}, 0
        self.fail_after_commit = True

    def apply(self, context, value, previous=None):
        self.writes += 1
        parent = f"/local/domain/{context['domid']}/data/xenova-{self.channel}"
        self.nodes[f"/local/domain/{context['domid']}/vm"] = ('/vm/' + context['vm_id']).encode()
        self.nodes[parent] = b''
        self.nodes[parent + '/bootstrap'] = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        for name in ('', '/bootstrap', '/clock', '/clock/request', '/clock/response'):
            self.nodes.setdefault(parent + name, b'')
            self.acls[parent + name] = [(0, 0), (context['domid'], 3 if name == '/clock/request' else 1)]
        if self.fail_after_commit:
            raise OSError(errno.EIO, 'synthetic commit reply lost')

    def inspect(self, context, value):
        obj = self.module.XenStore.__new__(self.module.XenStore)
        obj.channel, obj.quiescent, obj.handle = self.channel, self.quiescent, None
        nodes, acls = copy.deepcopy(self.nodes), copy.deepcopy(self.acls)
        class Library:
            def xs_transaction_start(inner, handle): return 1
            def xs_transaction_end(inner, handle, tx, abort):
                if abort is not True:
                    raise AssertionError('read observation attempted a commit')
                return True
        obj.lib = Library()
        obj.read = lambda tx, path: nodes.get(path)
        obj.permissions = lambda tx, path: acls[path]
        return obj.inspect(context, value)


class ReceiptReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.api = bootstrap_module()
        self.temp = tempfile.TemporaryDirectory(prefix='xenova-d03-receipt-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / 'receipt.json'
        self.context = dict(vm_id=uuid4(), host_boot_id=uuid4(), execution_id=uuid4(),
                            domid=42, xen_unique_id='18446744073709551615')
        self.credential = dict(vm_id=self.context['vm_id'], broker_ipv4='192.0.2.1', port=18883,
            username=self.context['vm_id'], password='SYNTHETIC-NOT-A-REAL-SECRET', trusted_internal_plaintext=True)
        self.credential_path = self.home / 'legacy-credentials.json'
        self.credential_path.write_text(json.dumps(self.credential))
        self.credential_path.chmod(0o600)
        _, self.source = self.api.private_json(self.credential_path)
        self.store = StoreBoundary(self.api)
        with self.assertRaises(OSError):
            self.api.provision(self.context, self.credential, self.source, self.path,
                               store=self.store, observe=lambda:dict(self.context))
        self.original = json.loads(self.path.read_text())
        self.assertEqual(self.original['phase'], 'uncertain')

    def reconcile(self, **kw):
        return self.api.reconcile(self.context, self.credential, self.source, self.path,
            store=self.store, observe=lambda:dict(self.context), **kw)

    def identities_unchanged(self):
        after = json.loads(self.path.read_text())
        for key in ('context', 'receipt_id', 'boot_id', 'publisher_id', 'credential_source', 'metadata'):
            self.assertEqual(after[key], self.original[key])
        self.assertEqual(self.store.writes, 1)

    def test_T01_T02_diagnostic_is_readonly_then_same_receipt_confirms_without_reinjection(self):
        before = self.path.read_bytes()
        result = self.reconcile()
        self.assertEqual(result['status'], 'confirmable')
        self.assertEqual(self.path.read_bytes(), before)
        result = self.reconcile(execute=True)
        self.assertEqual(result['status'], 'applied')
        self.identities_unchanged()
        self.assertNotIn(self.credential['password'], json.dumps(result) + self.path.read_text())

    def test_legacy_uuid_account_can_only_reconcile_its_original_receipt_without_reinjection(self):
        with self.assertRaises(ValueError):
            self.api.credentials(self.credential_path, self.context['vm_id'], 'telemetry')
        credential, identity = self.api.credentials(self.credential_path, self.context['vm_id'], 'telemetry',
                                                     for_reconciliation=True)
        self.assertEqual(identity, self.original['credential_source'])
        result = self.api.reconcile(self.context, credential, identity, self.path,
            store=self.store, observe=lambda: dict(self.context), execute=True)
        self.assertEqual(result['status'], 'applied')
        self.identities_unchanged()
        self.assertEqual(self.store.writes, 1)

    def test_T03_mismatch_acl_missing_expectation_and_unfinished_call_remain_blocked(self):
        parent = f"/local/domain/{self.context['domid']}/data/xenova-telemetry"
        for kind in ('value', 'permission', 'legacy', 'active-transaction', 'source'):
            with self.subTest(kind=kind):
                nodes, acls = copy.deepcopy(self.store.nodes), copy.deepcopy(self.store.acls)
                source = dict(self.source)
                if kind == 'value': self.store.nodes[parent + '/bootstrap'] = b'{}'
                if kind == 'permission': self.store.acls[parent + '/bootstrap'] = [(0, 0)]
                if kind == 'legacy':
                    self.path.write_text(json.dumps({k:v for k,v in self.original.items() if k != 'store_call_returned'}))
                if kind == 'active-transaction': self.store.quiescent = False
                if kind == 'source': self.source['inode'] += 1
                before = self.path.read_bytes()
                if kind == 'active-transaction':
                    with self.assertRaises(ValueError): self.reconcile(execute=True)
                else:
                    self.assertEqual(self.reconcile(execute=True)['status'], 'blocked')
                self.assertEqual(self.path.read_bytes(), before)
                self.store.nodes, self.store.acls, self.source = nodes, acls, source
                self.store.quiescent = True
                self.path.write_text(json.dumps(self.original))
        self.identities_unchanged()

    def test_T03_partial_observation_and_failure_do_not_confirm(self):
        for effect in (lambda *a:{'bootstrap_matches':True}, OSError(errno.EIO, 'read failure')):
            before = self.path.read_bytes()
            with patch.object(self.store, 'inspect', side_effect=effect):
                with self.assertRaises((ValueError, OSError)): self.reconcile(execute=True)
            self.assertEqual(self.path.read_bytes(), before)

    def test_T04_module_reload_replay_preserves_original_confirmation(self):
        self.reconcile(execute=True)
        before = self.path.read_bytes()
        self.api = bootstrap_module()
        self.store.module = self.api
        self.assertEqual(self.reconcile(execute=True)['status'], 'applied')
        self.assertEqual(self.path.read_bytes(), before)
        self.identities_unchanged()

    def test_T02_drive_confirmation_keeps_shared_boot_and_separate_instance(self):
        self.reconcile(execute=True)
        store = StoreBoundary(self.api);store.channel = 'drive'
        path = self.home/'drive.json'
        with self.assertRaises(OSError):
            self.api.provision(self.context, self.credential, self.source, path, store=store,
                observe=lambda:self.context, channel='drive', guest_boot_receipt=self.path)
        original = json.loads(path.read_text())
        self.assertEqual(original['boot_id'], self.original['boot_id'])
        self.assertNotEqual(original['publisher_id'], self.original['publisher_id'])
        blocked = self.api.reconcile(self.context, self.credential, self.source, path, store=store,
            observe=lambda:self.context, channel='drive')
        self.assertEqual(blocked['status'], 'blocked')
        done = self.api.reconcile(self.context, self.credential, self.source, path, store=store,
            observe=lambda:self.context, channel='drive', guest_boot_receipt=self.path, execute=True)
        self.assertEqual(done['status'], 'applied')
        self.assertEqual(store.writes, 1)
        self.assertEqual(json.loads(path.read_text())['publisher_id'], original['publisher_id'])

    def test_T04_T09_sigkill_before_and_after_record_then_fresh_process_replay(self):
        private = self.home/'inputs.json'
        private.write_text(json.dumps({'context':self.context, 'credential':self.credential, 'source':self.source,
            'nodes':{k:v.hex() for k,v in self.store.nodes.items()}, 'acls':self.store.acls}))
        private.chmod(0o600)
        code = '''
import json,os,signal,sys
from pathlib import Path
from test_xenova_reconciliation import bootstrap_module,StoreBoundary
api=bootstrap_module();home=Path(sys.argv[1]);value=json.loads((home/'inputs.json').read_text())
store=StoreBoundary(api);store.nodes={k:bytes.fromhex(v) for k,v in value['nodes'].items()}
store.acls={k:[tuple(p) for p in v] for k,v in value['acls'].items()}
def checkpoint(phase):
    if phase==sys.argv[2]:os.kill(os.getpid(),signal.SIGKILL)
result=api.reconcile(value['context'],value['credential'],value['source'],home/'receipt.json',
    store=store,observe=lambda:value['context'],execute=True,checkpoint=checkpoint)
assert store.writes==0
print(json.dumps({'status':result['status'],'receipt_id':result['receipt_id']}))
'''
        for phase in ('before-guard', 'recorded'):
            with self.subTest(phase=phase):
                self.path.write_text(json.dumps(self.original))
                result = subprocess.run([sys.executable,'-B','-c',code,str(self.home),phase],
                                        capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode, -9, result.stderr)
                self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain' if phase=='before-guard' else 'applied')
                replay = subprocess.run([sys.executable,'-B','-c',code,str(self.home),'no-kill'],
                                        capture_output=True,text=True,timeout=10)
                self.assertEqual(replay.returncode, 0, replay.stderr)
                self.assertEqual(json.loads(replay.stdout)['receipt_id'], self.original['receipt_id'])
                self.identities_unchanged()

    def test_T08_final_guard_rejects_changed_receipt_or_execution(self):
        before = self.path.read_bytes()
        def change(phase):
            if phase == 'before-guard':
                value = dict(self.original, metadata={'build_id':'changed'})
                self.api.save_receipt(self.path, value)
        with self.assertRaises(ValueError): self.reconcile(execute=True, checkpoint=change)
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')
        self.path.write_bytes(before)
        other = {**self.context, 'execution_id':uuid4()}
        with patch.object(self.api, 'private_json', wraps=self.api.private_json):
            result = self.api.reconcile(other, self.credential, self.source, self.path,
                store=self.store, observe=lambda:other, execute=True)
        self.assertEqual(result['status'], 'blocked')
        self.identities_unchanged()

    def test_T09_save_failures_are_not_confirmed_and_retry_does_not_write_store(self):
        for stage in ('write', 'file-sync', 'replace', 'parent-sync'):
            with self.subTest(stage=stage):
                self.path.write_text(json.dumps(self.original))
                if stage == 'write':
                    target, effect = 'save_receipt', OSError(errno.ENOSPC, 'synthetic full')
                elif stage == 'replace':
                    target, effect = 'os.replace', OSError(errno.EIO, 'synthetic replace')
                else:
                    target = 'os.fsync'
                    original_sync = os.fsync
                    def effect(fd):
                        import stat
                        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                        if directory == (stage == 'parent-sync'):
                            raise OSError(errno.EIO, 'synthetic sync failure')
                        return original_sync(fd)
                if '.' in target:
                    owner, attr = self.api.os, target.split('.')[1]
                else:
                    owner, attr = self.api, target
                with patch.object(owner, attr, side_effect=effect):
                    with self.assertRaises(OSError): self.reconcile(execute=True)
                self.assertEqual(self.reconcile(execute=True)['status'], 'applied')
                self.identities_unchanged()

    def test_T10_real_receipt_lock_does_not_block_another_execution(self):
        with self.api.receipt_lock(self.path):
            with self.assertRaises(BlockingIOError): self.reconcile(execute=True)
            other = self.home / 'other.json'
            with self.api.receipt_lock(other): pass
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def test_T11_deadline_is_shared_across_store_and_final_guard(self):
        now = [1.0]
        inspect = self.store.inspect
        def delayed(*args):
            value = inspect(*args)
            now[0] += .02
            return value
        before = self.path.read_bytes()
        with patch.object(self.store, 'inspect', side_effect=delayed):
            with self.assertRaises(TimeoutError):
                self.reconcile(execute=True, timeout_ms=10, clock=lambda:now[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_reconciliation_cli_observer_rejects_pending_user_stop_before_store(self):
        from unittest.mock import Mock
        import xenova_management as management
        manager = Mock()
        manager.call.return_value = {'ok':True, 'data':{'vm':{
            'guest_kind':'zephyr','shutdown_requested':False,'execution_context':{**self.context,'phase':'observed'},
            'desired_state':'stopped','intent_applicable':True}}}
        with patch.object(management,'Manager',return_value=manager):
            with self.assertRaisesRegex(ValueError,'running intent'):
                self.api.current(self.context['vm_id'], deadline=time.monotonic()+1)
        self.assertEqual(manager.call.call_args.args[0], 'get')
        self.assertEqual(self.store.writes, 1)

    def test_T08_cli_observer_rechecks_intent_revision_even_when_execution_is_unchanged(self):
        from unittest.mock import Mock
        import xenova_management as management
        view = {'guest_kind':'zephyr','shutdown_requested':False,'execution_context':{**self.context,'phase':'observed'},
                'domid':self.context['domid'],'desired_state':'running','intent_applicable':True,
                'lifecycle':'REGISTERED','generation':0,'intent_revision':1}
        manager = Mock();manager.call.side_effect = [
            {'ok':True,'data':{'vm':view}}, {'ok':True,'data':{'vm':{**view,'intent_revision':2}}}]
        identity = json.dumps({key:self.context[key] for key in ('domid','xen_unique_id')})
        guard = {};deadline = time.monotonic()+1
        with patch.object(management,'Manager',return_value=manager), \
             patch.object(self.api.Path,'read_text',return_value=self.context['host_boot_id']), \
             patch.object(self.api.subprocess,'check_output',return_value=identity):
            self.assertEqual(self.api.current(self.context['vm_id'],deadline=deadline,guard=guard),self.context)
            with self.assertRaisesRegex(ValueError,'intent or lifecycle'):
                self.api.current(self.context['vm_id'],deadline=deadline,guard=guard)
        self.assertEqual(self.store.writes,1)

    def test_T12_legitimate_new_execution_accepts_new_receipt_but_not_old_confirmation(self):
        self.reconcile(execute=True)
        new = {**self.context, 'execution_id':uuid4(), 'xen_unique_id':'1'}
        self.store.fail_after_commit = False
        fresh = self.api.provision(new, self.credential, self.source, self.home/'new.json',
                                   store=self.store, observe=lambda:new)
        self.assertEqual(fresh['context'], new)
        self.assertNotEqual(fresh['receipt_id'], self.original['receipt_id'])
        self.assertEqual(self.api.reconcile(new, self.credential, self.source, self.path,
            store=self.store, observe=lambda:new)['status'], 'blocked')


class RecoveryReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.f = foundation.FoundationTest('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.p, self.m, self.r = self.f.p, self.f.m, self.f.r

    def cut(self, checkpoint):
        self.f.start()
        spec = self.f.spec()
        self.f.checkpoint(checkpoint)
        with self.assertRaises(foundation.Interrupted): self.f.recover(spec)
        self.p.execution_checkpoint = lambda *args:None
        self.f.ticks += 100
        return spec

    def reconcile(self, spec, execute=False, **kw):
        return self.m.call('recovery-reconcile', {'recovery_attempt_id':spec['recovery_attempt_id'],
                                                'timeout_ms':kw.pop('timeout_ms',15000)}, execute=execute, **kw)

    def path(self, spec):
        return self.p.data/'recovery-attempts'/(spec['recovery_attempt_id']+'.json')

    def physical(self):
        return [c for c in self.f.xen.calls if len(c)>1 and c[0]=='xl' and c[1] in ('create','shutdown','reboot','destroy')]

    def test_T01_T05_stopping_before_submission_can_fail_original_without_commands(self):
        spec = self.cut('recovery-stopping')
        path = self.path(spec)
        original, commands = path.read_bytes(), self.physical()
        intent = self.p.execution().intent(self.r)
        plan = self.f.ok(self.reconcile(spec))
        self.assertEqual(plan['reconciliation']['outcome'], 'not_submitted')
        self.assertEqual(path.read_bytes(), original)
        done = self.f.ok(self.reconcile(spec, True))
        self.assertEqual(done['attempt']['phase'], 'failed')
        self.assertIsNone(done['original_result'])
        self.assertEqual(done['attempt']['request'], spec)
        self.assertEqual(done['attempt']['request_hash'], execution.fingerprint(spec))
        self.assertEqual(self.physical(), commands)
        self.assertEqual(self.p.execution().intent(self.r), intent)

    def test_T05_stop_returned_removal_closes_without_replacement(self):
        spec = self.cut('recovery-stop-returned')
        original = json.loads(self.path(spec).read_text())
        commands = self.physical()
        done = self.f.ok(self.reconcile(spec, True))
        self.assertEqual(done['reconciliation']['outcome'], 'stopped_without_replacement')
        self.assertEqual(done['attempt']['phase'], 'failed')
        self.assertEqual(done['attempt']['operation_id'], original['operation_id'])
        self.assertEqual(self.physical(), commands)

    def test_T05_uncertain_returned_shutdown_error_is_preserved_after_confirmation(self):
        self.f.start();spec = self.f.spec()
        stop = self.p.stop_many
        def response_lost(*args, **kw):
            stop(*args, **kw)
            raise vm.PlatformError('synthetic stop response lost')
        with patch.object(self.p,'stop_many',side_effect=response_lost):
            self.f.fails(self.f.recover(spec), 'RECONCILE_REQUIRED')
        original = json.loads(self.path(spec).read_text())
        self.assertEqual(original['phase'], 'uncertain')
        self.assertEqual(original['stop_submission']['state'], 'returned')
        self.f.ticks += 100
        commands = self.physical()
        done = self.f.ok(self.reconcile(spec, True))
        self.assertEqual(done['attempt']['reconciliation']['original_error'], original['error'])
        self.assertEqual(done['attempt']['reconciliation']['original_phase'], 'uncertain')
        self.assertEqual(done['attempt']['phase'], 'failed')
        self.assertEqual(self.physical(), commands)
        self.assertEqual(self.f.ok(self.reconcile(spec, True))['reconciliation']['status'], 'recorded')
        self.assertEqual(self.physical(), commands)

    def test_T06_correlated_observed_launch_completes_after_expiry_without_create(self):
        spec = self.cut('recovery-observed')
        commands = self.physical()
        done = self.f.ok(self.reconcile(spec, True))
        self.assertEqual(done['attempt']['phase'], 'completed')
        self.assertEqual(done['original_result']['app_readiness'], 'unknown')
        self.assertEqual(done['attempt']['request'], spec)
        self.assertEqual(self.physical(), commands)

    def test_T07_legacy_stopping_is_not_released_on_absence_or_lock_alone(self):
        spec = self.cut('recovery-stopping')
        path = self.path(spec)
        op = json.loads(path.read_text());op.pop('stop_submission')
        self.p.execution().write(path, op)
        for present in (True, False):
            with self.subTest(present=present):
                if not present: self.f.xen.rows.clear()
                before, commands = path.read_bytes(), self.physical()
                self.assertEqual(self.f.ok(self.reconcile(spec))['reconciliation']['status'], 'blocked')
                self.f.fails(self.reconcile(spec, True), 'RECONCILE_REQUIRED')
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.physical(), commands)

    def test_T07_submitted_but_not_returned_launch_is_not_closed(self):
        spec = self.cut('recovery-launching')
        before, commands = self.path(spec).read_bytes(), self.physical()
        self.f.fails(self.reconcile(spec, True), 'RECONCILE_REQUIRED')
        self.assertEqual(self.path(spec).read_bytes(), before)
        self.assertEqual(self.physical(), commands)

    def test_T08_final_guard_detects_new_user_stop_intent(self):
        spec = self.cut('recovery-stop-returned')
        before, commands = self.path(spec).read_bytes(), self.physical()
        def change(phase, operation):
            if phase == 'recovery-reconcile-before-guard':
                value = self.m.fork().call('stop', {'vm_id':self.r['uuid']}, execute=True, idempotency_key='d03-user-stop')
                self.assertTrue(value['error']['details']['accepted'])
        self.p.execution_checkpoint = change
        self.f.fails(self.reconcile(spec, True), 'TARGET_CHANGED')
        self.assertEqual(self.path(spec).read_bytes(), before)
        self.assertEqual(self.physical(), commands)
        self.assertEqual(self.p.execution().intent(self.r)['desired_state'], 'stopped')

    def test_T09_required_write_failure_and_response_loss_keep_same_owner_ids(self):
        spec = self.cut('recovery-stop-returned')
        before, commands = self.path(spec).read_bytes(), self.physical()
        write = execution.atomic_write
        def full(path, *args):
            if Path(path).parent.name == 'recovery-attempts':
                raise OSError(errno.ENOSPC, 'synthetic full')
            return write(path, *args)
        with patch.object(execution, 'atomic_write', side_effect=full):
            self.assertFalse(self.reconcile(spec, True)['ok'])
        self.assertEqual(self.path(spec).read_bytes(), before)
        def cut(phase, value):
            if phase == 'recovery-failed': raise foundation.Interrupted()
        self.p.execution_checkpoint = cut
        with self.assertRaises(foundation.Interrupted): self.reconcile(spec, True)
        self.p.execution_checkpoint = lambda *args:None
        response = self.m.fork().call('recovery-reconcile', {'recovery_attempt_id':spec['recovery_attempt_id']}, execute=True)
        self.assertEqual(self.f.ok(response)['attempt']['phase'], 'failed')
        self.assertEqual(self.f.ok(response)['attempt']['request'], spec)
        self.assertEqual(self.physical(), commands)

    def test_T09_actual_process_kill_after_original_journal_save_then_replay(self):
        spec = self.cut('recovery-stop-returned')
        before = json.loads(self.path(spec).read_text())
        commands = self.physical()
        code = '''
import os,signal,sys
from pathlib import Path
from unittest.mock import patch
from test_xenova_platform import FakeXen
from test_xenova_recovery_foundation import HOST_A
from first_install_fixture import FILES
from xenova_management import Manager
import xenova_vm as vm
class ObserveOnlyXen(FakeXen):
    def __call__(self,args,**kwargs):
        assert not (args[0]=='xl' and args[1] in ('create','shutdown','reboot','destroy'))
        return super().__call__(args,**kwargs)
data,runtime=map(Path,sys.argv[1:3]);xen=ObserveOnlyXen(data,{})
p=vm.Platform(data,runtime,command=xen,defaults_path=FILES/'defaults.json',clock=lambda:100,
    boot_id_reader=lambda:HOST_A)
def checkpoint(phase,value):
    if phase=='recovery-failed':os.kill(os.getpid(),signal.SIGKILL)
p.execution_checkpoint=checkpoint
with patch.object(vm,'storage_check'):
    xen.records=p.records(validate_assets=False)
    result=Manager(p).call('recovery-reconcile',{'recovery_attempt_id':sys.argv[3]},execute=True)
raise RuntimeError('kill checkpoint not reached: '+str(result.get('error')))
'''
        child = subprocess.run([sys.executable,'-B','-c',code,str(self.p.data),str(self.p.runtime),
                                spec['recovery_attempt_id']],capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode, -9, child.stderr)
        after = self.f.ok(self.reconcile(spec, True))['attempt']
        for key in ('operation_id','request','request_hash','accepted_monotonic_ms'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['phase'], 'failed')
        self.assertEqual(self.physical(), commands)

    def test_T09_journal_file_sync_replace_parent_sync_failures_preserve_original_ids(self):
        import stat
        spec = self.cut('recovery-stop-returned')
        path = self.path(spec)
        original, commands = path.read_bytes(), self.physical()
        sync, replace = os.fsync, os.replace
        for stage in ('file-sync','replace','parent-sync'):
            with self.subTest(stage=stage):
                path.write_bytes(original)
                def failed_sync(fd):
                    name = Path(os.readlink('/proc/self/fd/'+str(fd)))
                    directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                    target = name.name=='recovery-attempts' if directory else name.parent.name=='recovery-attempts'
                    if target and directory == (stage=='parent-sync'):
                        raise OSError(errno.EIO,'synthetic journal fsync')
                    return sync(fd)
                def failed_replace(src, dst):
                    if Path(dst).parent.name=='recovery-attempts':raise OSError(errno.EIO,'synthetic journal replace')
                    return replace(src,dst)
                with patch.object(os,'replace',side_effect=failed_replace) if stage=='replace' else \
                     patch.object(os,'fsync',side_effect=failed_sync):
                    self.f.fails(self.reconcile(spec, True),'RECONCILE_REQUIRED')
                result = self.f.ok(self.reconcile(spec, True))
                self.assertEqual(result['attempt']['request'], spec)
                self.assertEqual(result['attempt']['operation_id'], json.loads(original)['operation_id'])
                self.assertEqual(self.physical(), commands)

    def test_T10_actual_execution_lock_rejects_competing_reconciliation(self):
        spec = self.cut('recovery-stop-returned')
        before = self.path(spec).read_bytes()
        with operation_lock(self.p.runtime):
            self.f.fails(self.reconcile(spec, True), 'OPERATION_IN_PROGRESS')
        self.assertEqual(self.path(spec).read_bytes(), before)

    def test_public_cli_dispatch_and_input_validation_preserve_original_attempt(self):
        spec = self.cut('recovery-stop-returned')
        before = self.path(spec).read_bytes()
        with patch.object(vm, 'Platform', return_value=self.p), redirect_stdout(io.StringIO()) as output:
            value = vm.main(['recovery-reconcile','--recovery-attempt-id',spec['recovery_attempt_id']])
        self.assertTrue(value['ok'])
        self.assertEqual(json.loads(output.getvalue())['data']['reconciliation']['status'], 'confirmable')
        for invalid in ({'recovery_attempt_id':'invalid'},
                        {'recovery_attempt_id':spec['recovery_attempt_id'],'timeout_ms':True},
                        {'recovery_attempt_id':spec['recovery_attempt_id'],'timeout_ms':15001},
                        {'recovery_attempt_id':spec['recovery_attempt_id'],'force':True}):
            self.f.fails(self.m.call('recovery-reconcile',invalid),'INVALID_SPEC')
        self.f.fails(self.m.call('recovery-reconcile',{'recovery_attempt_id':spec['recovery_attempt_id']},
                                idempotency_key='new-key'),'INVALID_SPEC')
        self.assertEqual(self.path(spec).read_bytes(), before)

    def test_T07_active_owned_scope_reaches_budget_without_signal_or_release(self):
        import xenova_guest_process as guest
        spec = self.cut('recovery-stop-returned')
        run = self.p.execution().run_record(self.r)
        self.p.execution().write(self.p.execution()._path(self.p.runtime,'executions',self.r['uuid']),
            {**run,'process_scope':guest.scope_name(self.r['uuid'],run['execution_id'])})
        before, commands = self.path(spec).read_bytes(), self.physical()
        command = self.p.command
        def observe(args, **kw):
            if args[:2] == ['systemctl','show']:
                self.f.xen.calls.append(args)
                prop = args[args.index('-p')+1]
                return {'LoadState':'loaded','ActiveState':'active','Before':'xenova-vm-shutdown.service'}[prop]
            return command(args, **kw)
        with patch.object(self.p,'process_isolation',True), patch.object(self.p,'command',side_effect=observe):
            self.f.fails(self.reconcile(spec, True, timeout_ms=25),'DEADLINE_EXCEEDED')
        self.assertEqual(self.path(spec).read_bytes(), before)
        self.assertEqual(self.physical(), commands)
        self.assertFalse(any(c[:2] == ['systemctl','kill'] for c in self.f.xen.calls))

    def test_T11_host_change_and_small_budget_never_refresh_original_deadline(self):
        spec = self.cut('recovery-stop-returned')
        before = self.path(spec).read_bytes()
        self.f.host = foundation.HOST_B
        self.f.fails(self.reconcile(spec, True), 'TARGET_CHANGED')
        self.f.host = foundation.HOST_A
        command = self.p.command
        def delayed(args, **kwargs):
            self.f.ticks += .002
            return command(args, **kwargs)
        with patch.object(self.p, 'command', side_effect=delayed):
            self.f.fails(self.reconcile(spec, True, budget_ms=1), 'DEADLINE_EXCEEDED')
        self.assertEqual(self.path(spec).read_bytes(), before)

    def test_T12_other_execution_never_completes_original_attempt(self):
        spec = self.cut('recovery-observed')
        run = self.p.execution().run_record(self.r)
        self.p.execution().write(self.p.execution()._path(self.p.runtime,'executions',self.r['uuid']),
                                 {**run,'owner':{'kind':'user','operation_id':uuid4()},'execution_id':uuid4()})
        before, commands = self.path(spec).read_bytes(), self.physical()
        self.f.fails(self.reconcile(spec, True), 'RECONCILE_REQUIRED')
        self.assertEqual(self.path(spec).read_bytes(), before)
        self.assertEqual(self.physical(), commands)

    def test_T07_observation_error_and_residual_process_scope_do_not_release_owner(self):
        spec = self.cut('recovery-stop-returned')
        before = self.path(spec).read_bytes()
        with patch.object(self.p, 'domains', side_effect=vm.PlatformError('synthetic observation failure')):
            self.f.fails(self.reconcile(spec, True), 'OBSERVATION_UNAVAILABLE')
        import xenova_guest_process
        with patch.object(self.p, 'process_isolation', True), \
             patch.object(xenova_guest_process, 'cleanup', side_effect=vm.PlatformError('active scope')):
            # 보존된 실행에 scope 근거가 없다는 것 자체도 성공으로 치환하지 않는다.
            self.f.fails(self.reconcile(spec, True), 'RECONCILE_REQUIRED')
        self.assertEqual(self.path(spec).read_bytes(), before)

    def test_T05_accepted_and_removed_phases_finalize_only_original_attempt(self):
        # 별도 fixture를 써 서로 다른 원 실행/기한을 섞지 않는다.
        for checkpoint in ('recovery-accepted', 'recovery-removed'):
            with self.subTest(checkpoint=checkpoint):
                other = RecoveryReconciliationTest('runTest');other.setUp()
                try:
                    spec = other.cut(checkpoint)
                    commands = other.physical()
                    result = other.f.ok(other.reconcile(spec, True))
                    self.assertEqual(result['attempt']['phase'], 'failed')
                    self.assertEqual(result['attempt']['request'], spec)
                    self.assertEqual(other.physical(), commands)
                finally:other.doCleanups()

    def test_T12_new_legitimate_execution_is_not_changed_by_old_result_recheck(self):
        spec = self.cut('recovery-stop-returned')
        self.f.ok(self.reconcile(spec, True))
        new = self.f.start('d03-new-user-command')
        execution_id = new['execution_id']
        commands = self.physical()
        old = self.f.ok(self.reconcile(spec, True))
        self.assertIsNone(old['original_result'])
        self.assertEqual(self.f.state()['execution_id'], execution_id)
        self.assertEqual(self.physical(), commands)

    def test_T13_real_adapter_can_read_completion_but_final_engine_needs_owner_connection(self):
        from types import SimpleNamespace
        from uuid import UUID
        try:
            from dom0_manager.recovery.engine import RecoveryEngine
            from dom0_manager.recovery.platform_recovery import PlatformRecovery
            from dom0_manager.recovery.policy import RecoveryPolicy
            from dom0_manager.recovery.state import RecoveryRecord, RecoveryStatus, RecoveryStore
        except ImportError:
            self.skipTest('existing isolated dom0-manager integration dependencies required')
        spec = self.cut('recovery-observed')
        rid = UUID(uuid4())
        record = RecoveryRecord(rid, UUID(self.r['uuid']), 'DOMAIN_ABSENT', 'linux', RecoveryStatus.FAILED,
            '2026-10-27T00:00:00+00:00', 0, attempt=1, recovery_attempt_id=UUID(spec['recovery_attempt_id']),
            request=dict(spec), mode=spec['mode'], final=True, closed_monotonic=self.f.ticks,
            last_error={'code':'RECONCILE_REQUIRED','platform_code':'RECONCILE_REQUIRED','message':'unconfirmed'})
        store = RecoveryStore(self.p.runtime/'owned-d03-upper-fixture.json')
        store.save({rid:record})
        adapter = PlatformRecovery(self.m)
        engine = RecoveryEngine(RecoveryPolicy(), adapter, SimpleNamespace(health=lambda _:None),
                                store, clock=lambda:self.f.ticks, inline=True)
        self.addCleanup(engine.shutdown)
        self.f.ok(self.reconcile(spec, True))
        commands = self.physical()
        actual = adapter.attempt(record.recovery_attempt_id)
        self.assertEqual(actual['phase'], 'completed')
        engine.tick()
        self.assertEqual(engine.records()[0].status, RecoveryStatus.FAILED)
        self.assertEqual(engine.completed_executions(), [])
        # 직접적인 기존 조회 분기는 원 결과를 읽을 수 있다. final 소비 연결은 제품에 아직 없다.
        read = engine._task_replay(engine.records()[0])
        self.assertEqual(read['original_result']['execution_id'], actual['result']['execution_id'])
        self.assertEqual(engine.records()[0].attempt, 1)
        self.assertEqual(engine.records()[0].request, spec)
        self.assertEqual(self.physical(), commands)
