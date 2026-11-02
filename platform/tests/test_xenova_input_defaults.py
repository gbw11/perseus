"""설정 생략만 해석하고 원 제출·확정값·현재 관측을 분리하는 공통 create 수락."""
import copy
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_xenova_management as fixtures
import test_xenova_creation_reconcile as linux
from first_install_contract import uuid4
import xenova_catalog as catalog
import xenova_management_cli as cli
from test_xenova_zephyr import bundle
from test_xenova_lifecycle import application_bundle


class InputDefaultsTest(unittest.TestCase):
    def case(self,real=False):
        c=(linux.CreationReconcileTest if real else fixtures.ManagementTest)('runTest')
        c.setUp();self.addCleanup(c.doCleanups);return c

    def partial(self,c):return {k:v for k,v in c.spec.items() if k not in ('vcpus','memory_mib')}

    def ok(self,response):self.assertTrue(response['ok'],response);return response['data']

    def test_minimum_manager_uses_catalogue_and_preserves_submitted_presence(self):
        c=self.case();request=self.partial(c)
        created=self.ok(c.m.call('create',request,execute=True))
        op=c.m._operations()[0]
        self.assertEqual(op['request'],request);self.assertEqual(op['schema_version'],'1.2')
        self.assertEqual(op['initial_spec']['vcpus'],1);self.assertEqual(op['initial_spec']['memory_mib'],512)
        self.assertEqual(created['initial_spec'],op['initial_spec'])
        self.assertEqual(op['template']['default_resources'],{'vcpus':1,'memory_mib':512})

    def test_cli_presence_is_not_null_or_a_parser_default(self):
        manager=SimpleNamespace(call=Mock(return_value={'ok':True,'data':{},'error':None}))
        args=['create','--vm-id',uuid4(),'--name','optional','--template-id','ubuntu-24.04-arm64',
              '--template-version','1.0.0','--json']
        with redirect_stdout(io.StringIO()):cli.main(args,manager)
        request=manager.call.call_args.args[1]
        self.assertNotIn('vcpus',request);self.assertNotIn('memory_mib',request)
        with redirect_stdout(io.StringIO()):cli.main(args+['--vcpus','0','--memory-mib','16'],manager)
        self.assertEqual(manager.call.call_args.args[1]['vcpus'],0)
        self.assertEqual(manager.call.call_args.args[1]['memory_mib'],16)

    def test_explicit_invalid_is_rejected_before_catalogue_or_reservation(self):
        c=self.case();request=self.partial(c)
        for key,values in (('vcpus',(None,0,-1,False,'1',[],float('nan'))),
                           ('memory_mib',(None,0,-1,False,'512',{},float('inf'))),
                           ('ipv4',(None,'',0,False,'192.168.77.1'))):
            for value in values:
                with self.subTest(key=key,value=value),patch.object(catalog,'entries',side_effect=AssertionError('late validation')):
                    result=c.m.call('create',request|{key:value},execute=True)
                    self.assertFalse(result['ok'],result);self.assertEqual(result['error']['code'],'INVALID_SPEC')
        self.assertFalse((c.data/'operations').exists());self.assertEqual(c.copies,0)

    def test_replay_never_selects_current_defaults_and_preserves_legacy_bytes(self):
        for partial in (False,True):
            with self.subTest(partial=partial):
                c=self.case();request=self.partial(c) if partial else c.spec
                first=c.m.call('create',request,execute=True);self.ok(first)
                op=c.m._operations()[0];path=c.data/'operations'/(op['operation_id']+'.json');before=path.read_bytes()
                with patch.object(catalog,'entries',side_effect=AssertionError('replay reselected defaults')):
                    replay=c.m.fork().call('create',request,execute=True)
                self.ok(replay);self.assertTrue(replay['replayed'])
                self.assertEqual(first['operation_id'],replay['operation_id']);self.assertEqual(path.read_bytes(),before)
                self.assertEqual(c.copies,1)
                if partial:
                    equivalent=c.m.call('create',request|{'vcpus':1,'memory_mib':512},execute=True)
                    self.ok(equivalent);self.assertEqual(equivalent['operation_id'],op['operation_id'])
                    conflict=c.m.call('create',request|{'vcpus':2},execute=True)
                    self.assertEqual(conflict['error']['code'],'IDEMPOTENCY_CONFLICT')

    def test_plan_is_not_acceptance_and_explicit_plan_values_survive_new_defaults(self):
        c=self.case();request=self.partial(c);plan=self.ok(c.m.call('create',request))
        self.assertEqual(plan['request'],request);self.assertEqual(plan['spec']['memory_mib'],512)
        self.assertFalse((c.data/'operations').exists())
        describe=catalog.describe
        def changed(*args,**kwargs):
            selected=describe(*args,**kwargs)
            return {**selected,'default_resources':{'vcpus':2,'memory_mib':768}}
        with patch.object(catalog,'describe',changed):
            saved=self.ok(c.m.call('create',plan['spec'],execute=True))
            another=request|{'vm_id':uuid4(),'name':'new-default'};another.pop('ipv4',None)
            newer=self.ok(c.m.call('create',another,execute=True))
        self.assertEqual(saved['configuration']['memory_mib'],512)
        self.assertEqual(newer['configuration']['memory_mib'],768)

    def test_actual_debian_ubuntu_omission_uses_single_verified_copy(self):
        import prepare_instance as preparation
        for distribution in ('debian','ubuntu'):
            with self.subTest(os=distribution):
                c=self.case(True);c.select(distribution);request=self.partial(c)
                with c.linux_boundaries(),patch.object(preparation,'_copy_verified',wraps=preparation._copy_verified) as copies:
                    if distribution=='ubuntu':
                        args=['create','--vm-id',request['vm_id'],'--name',request['name'],
                              '--template-id',request['template_id'],'--template-version',request['template_version'],
                              '--execute','--json']
                        with redirect_stdout(io.StringIO()) as output:response=cli.main(args,c.m)
                        self.assertEqual(json.loads(output.getvalue()),response);created=self.ok(response)
                    else:created=self.ok(c.m.call('create',request,execute=True))
                self.assertEqual(created['configuration']['vcpus'],1)
                self.assertEqual(created['configuration']['memory_mib'],512)
                self.assertEqual(sum(call.args[0].name=='root.raw' for call in copies.call_args_list),1)

    def test_each_resource_can_be_omitted_without_replacing_the_other(self):
        for omitted,explicit in (('vcpus',{'memory_mib':768}),('memory_mib',{'vcpus':2})):
            with self.subTest(omitted=omitted):
                c=self.case();request=self.partial(c)|explicit
                result=self.ok(c.m.call('create',request,execute=True))
                self.assertEqual(result['configuration'][omitted],{'vcpus':1,'memory_mib':512}[omitted])
                for key,value in explicit.items():self.assertEqual(result['configuration'][key],value)
                self.assertEqual(c.m._operations()[0]['request'],request)

    def test_stored_input_and_resolved_spec_mismatch_is_not_reinterpreted(self):
        c=self.case();self.ok(c.m.call('create',self.partial(c),execute=True));original=c.m._operations()[0]
        path=c.data/'operations'/(original['operation_id']+'.json')
        for kind in ('input','source','capabilities'):
            op=copy.deepcopy(original)
            if kind=='input':op['request']['vcpus']=2
            elif kind=='source':op['template']=None
            else:op['template']['capabilities']=None
            with self.subTest(kind=kind):
                path.write_text(json.dumps(op))
                with patch.object(catalog,'entries',side_effect=AssertionError('damaged journal reselected defaults')):
                    response=c.m.call('create',self.partial(c),execute=True)
                self.assertFalse(response['ok']);self.assertEqual(response['error']['code'],'RECOVERY_REQUIRED')

    def test_corrupt_resource_defaults_are_platform_errors_not_silent_fallback(self):
        c=self.case();describe=catalog.describe
        def damaged(*args,**kwargs):return {**describe(*args,**kwargs),'default_resources':{'vcpus':False,'memory_mib':0}}
        with patch.object(catalog,'describe',damaged):result=c.m.call('create',self.partial(c),execute=True)
        self.assertFalse(result['ok'],result);self.assertEqual(result['error']['code'],'ARTIFACT_INVALID')
        self.assertEqual(c.copies,0);self.assertFalse((c.data/'operations').exists())

    def publish_zephyr(self,c):
        source=application_bundle(bundle(c.data/'old'),c.data/'rtos-template')
        manifest=source/'firmware.json';value=json.loads(manifest.read_text())
        value.update(id='starter-artifact',kind='zephyr-xen-firmware',version='1.0.0')
        manifest.chmod(0o644);manifest.write_text(json.dumps(value));manifest.chmod(0o444)
        (c.data/'rtos-templates').mkdir();source.rename(c.data/'rtos-templates/starter-artifact')
        descriptor={'schema_version':'1.0','template_id':'zephyr-xenvm-starter-arm64','template_version':'1.0.0',
            'guest_kind':'zephyr','asset_directory':'rtos-templates/starter-artifact','source_template_id':'starter-artifact',
            'manifest_sha256':catalog.sha(c.data/'rtos-templates/starter-artifact/firmware.json')}
        target=c.data/'catalog'/descriptor['template_id'];target.mkdir();(target/'1.0.0.json').write_text(json.dumps(descriptor))
        return descriptor

    def test_actual_zephyr_cli_omission_uses_fixed_profile_without_linux_defaults(self):
        c=self.case();descriptor=self.publish_zephyr(c)
        c.m.preparer=c.m._prepare
        args=['create','--vm-id',uuid4(),'--name','rtos-default','--template-id',descriptor['template_id'],
              '--template-version','1.0.0','--execute','--json']
        with redirect_stdout(io.StringIO()) as output:created=cli.main(args,c.m)
        self.ok(created);self.assertEqual(json.loads(output.getvalue()),created)
        record=created['data']['configuration'];self.assertEqual((record['vcpus'],record['memory_mib']),(1,16))
        self.assertNotIn('ipv4',record);self.assertFalse((c.data/'instances'/record['id']/'root.raw').exists())
        op=c.m._operations()[0];self.assertNotIn('memory_mib',op['request']);self.assertEqual(op['schema_version'],'1.2')

    def cold_replay(self,c,request,incoming):
        first=c.m.call('create',request,execute=True);self.ok(first)
        op=c.m._operations()[0];path=c.data/'operations'/(op['operation_id']+'.json');before=path.read_bytes()
        source=c.data/op['template']['asset_directory'];source.rename(source.with_name(source.name+'-unavailable'))
        code='''
import json,sys
from pathlib import Path
from unittest.mock import patch
from first_install_fixture import FILES
from test_xenova_platform import FakeXen
import xenova_vm as vm
from xenova_management import Manager
import xenova_catalog as catalog
data,run=map(Path,sys.argv[1:]);p=vm.Platform(data,run,command=FakeXen(data,{}),defaults_path=FILES/'defaults.json')
with patch.object(vm,'storage_check'),patch.object(catalog,'entries',side_effect=AssertionError('cold replay selected defaults')):
 print(json.dumps(Manager(p).call('create',json.loads(sys.stdin.read()),execute=True)))
'''
        child=subprocess.run([sys.executable,'-B','-c',code,str(c.data),str(c.run)],input=json.dumps(incoming),
                             text=True,capture_output=True,timeout=15)
        self.assertEqual(child.returncode,0,child.stderr);replay=json.loads(child.stdout);self.ok(replay)
        self.assertEqual(replay['operation_id'],first['operation_id']);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(replay['data']['original_result']['initial_spec'],op['initial_spec'])

    def test_completed_partial_request_replays_in_cold_process_with_no_template(self):
        for explicit in (False,True):
            with self.subTest(explicit=explicit):
                c=self.case()
                request=c.spec|{'vcpus':2,'memory_mib':768} if explicit else self.partial(c)
                incoming=self.partial(c) if explicit else c.spec
                self.cold_replay(c,request,incoming)

    def test_acceptance_failure_never_claims_success_or_rewrites_stored_input(self):
        import errno
        c=self.case();request=self.partial(c);write=c.m._write
        def ambiguous(path,value):
            write(path,value)
            if path.parent==c.data/'operations':raise OSError(errno.EIO,'owned acceptance response lost')
        with patch.object(c.m,'_write',ambiguous):result=c.m.call('create',request,execute=True)
        self.assertFalse(result['ok'],result);self.assertEqual(c.copies,0)
        op=c.m._operations()[0];self.assertEqual(op['request'],request)
        self.assertEqual(op['initial_spec']['memory_mib'],512)
        with patch.object(catalog,'entries',side_effect=AssertionError('accepted request reselected defaults')):
            resumed=c.m.call('create',request,execute=True)
        self.ok(resumed);self.assertEqual(resumed['operation_id'],result['operation_id'])
        self.assertEqual(c.copies,1)

    def test_same_key_before_acceptance_rechecks_defaults_after_slot_acquisition(self):
        import threading
        import xenova_preparation as ownership
        c=self.case();request=self.partial(c);entered,release=threading.Event(),threading.Event();rows=[]
        enter=ownership.LinuxSlot.__enter__;describe=catalog.describe;revision=[0]
        def defaults(*args,**kwargs):
            selected=describe(*args,**kwargs)
            return {**selected,'default_resources':{'vcpus':2,'memory_mib':768} if revision[0] else {'vcpus':1,'memory_mib':512}}
        def slot(owner):
            result=enter(owner)
            if threading.current_thread().name=='defaults-before-accept':
                entered.set()
                if not release.wait(10):raise RuntimeError('owned preaccept slot not released')
            return result
        with patch.object(catalog,'describe',defaults),patch.object(ownership.LinuxSlot,'__enter__',slot):
            worker=threading.Thread(name='defaults-before-accept',target=lambda:rows.append(c.m.fork().call('create',request,execute=True)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3));self.assertFalse((c.data/'operations').exists());revision[0]=1
                competing=c.m.fork().call('create',request|{'vcpus':2,'memory_mib':768},execute=True)
                self.assertEqual(competing['error']['code'],'OPERATION_IN_PROGRESS')
                self.assertFalse(competing['error']['details']['accepted'])
                self.assertEqual(c.copies,0)
            finally:release.set();worker.join(10)
        self.assertFalse(worker.is_alive());self.ok(rows[0]);self.assertEqual(c.copies,1)
        operations=c.m._operations();self.assertEqual(len(operations),1)
        self.assertEqual(operations[0]['request'],request);self.assertEqual(operations[0]['initial_spec']['memory_mib'],768)
        with patch.object(catalog,'entries',side_effect=AssertionError('replay selected defaults after acceptance')):
            replay=c.m.call('create',request|{'vcpus':2,'memory_mib':768},execute=True)
            self.ok(replay);self.assertEqual(replay['operation_id'],operations[0]['operation_id'])

    def test_same_key_default_change_error_burst_and_slow_sink_preserve_real_other_stop(self):
        import logging
        import threading
        from contextlib import ExitStack
        import test_xenova_read_progress as reads
        from xenova_gateway.diagnostics import GatewayDiagnostics
        c=reads.ReadProgressTest('runTest');c.setUp();self.addCleanup(c.doCleanups)
        select=c.select
        def omitted(distribution):
            result=select(distribution);c.spec.pop('vcpus');c.spec.pop('memory_mib');return result
        c.select=omitted;entered,release=threading.Event(),threading.Event()
        class Slow(logging.Handler):
            def emit(self,record):
                entered.set()
                if not release.wait(10):raise RuntimeError('owned auxiliary sink not released')
        logger=logging.getLogger('xenova_gateway');handlers=list(logger.handlers);level=logger.level;prop=logger.propagate
        logger.handlers=[Slow()];logger.setLevel(logging.INFO);channel=GatewayDiagnostics(logger)
        try:
            logger.warning('controlled sink barrier');self.assertTrue(entered.wait(3))
            with c.blocked('debian') as state:
                original=copy.deepcopy(state['op']);describe=catalog.describe
                def changed(*args,**kwargs):return {**describe(*args,**kwargs),'default_resources':{'vcpus':2,'memory_mib':768}}
                with patch.object(catalog,'describe',changed):
                    for _ in range(64):
                        rejected=c.m.fork().call('create',c.spec|{'vcpus':False},execute=True)
                        self.assertEqual(rejected['error']['code'],'INVALID_SPEC')
                        logger.warning('controlled input rejection code=INVALID_SPEC')
                    same=c.m.fork().call('create',c.spec|{k:original['initial_spec'][k] for k in ('vcpus','memory_mib')},execute=True)
                    self.ok(same);self.assertEqual(same['data']['effective_status'],'RUNNING')
                    self.assertEqual(same['data']['operation']['initial_spec'],original['initial_spec'])
                    stopped=c.m.fork().call('stop',{'vm_id':state['other']},idempotency_key='DE-T14-stop',budget_ms=2000)
                    self.ok(stopped);self.assertEqual(stopped['data']['original_result']['observation']['presence'],'absent')
                    self.assertFalse(state['release'].is_set());self.assertTrue(state['thread'].is_alive())
                    self.assertEqual(len([o for o in c.m._operations() if o['vm_id']==c.spec['vm_id']]),1)
            self.assertEqual(state['response']['data']['initial_spec'],original['initial_spec'])
            self.assertEqual(state['response']['operation_id'],original['operation_id'])
        finally:
            release.set();channel.close();channel.worker.join(5)
            logger.handlers=handlers;logger.setLevel(level);logger.propagate=prop
        self.assertFalse(channel.worker.is_alive())
