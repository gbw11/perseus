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
        self.source = dict(inode=123, mtime_ns=456, bytes=100)
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
