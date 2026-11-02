"""원 create 확정값의 의미 비교. 원장/Manager/CLI와 변경 없는 HTTP 생산자를 실행한다."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch,Mock
from uuid import UUID
from contextlib import redirect_stdout

import test_xenova_input_defaults as defaults
import test_xenova_read_progress as reads
import xenova_catalog as catalog
import xenova_management_cli as cli
import xenova_preparation as ownership
from first_install_contract import uuid4


class CreateCompatibilityTest(unittest.TestCase):
    case=defaults.InputDefaultsTest.case
    partial=defaults.InputDefaultsTest.partial
    ok=defaults.InputDefaultsTest.ok

    def raw(self,c,op):return (c.m.data/'operations'/(op['operation_id']+'.json')).read_bytes()

    def test_resource_presence_matrix_both_directions_preserves_origin(self):
        resources=('vcpus','memory_mib')
        for origin_mask in range(4):
            c=self.case();request=self.partial(c)
            for bit,key in enumerate(resources):
                if origin_mask & (1<<bit):request[key]={'vcpus':2,'memory_mib':768}[key]
            first=c.m.call('create',request,execute=True);self.ok(first)
            op=c.m._operations()[0];before=self.raw(c,op)
            for replay_mask in range(4):
                incoming=self.partial(c)
                for bit,key in enumerate(resources):
                    if replay_mask & (1<<bit):incoming[key]=op['initial_spec'][key]
                incoming=dict(reversed(list(incoming.items())))
                with self.subTest(origin=origin_mask,replay=replay_mask), \
                     patch.object(catalog,'entries',side_effect=AssertionError('replay selected current defaults')):
                    result=c.m.fork().call('create',incoming,execute=True)
                    self.ok(result);self.assertTrue(result['replayed'])
                    self.assertEqual(result['operation_id'],op['operation_id'])
                    self.assertEqual(result['data']['original_result']['initial_spec'],op['initial_spec'])
                    self.assertEqual(self.raw(c,op),before);self.assertEqual(c.copies,1)

    def test_real_differences_and_invalid_explicit_values_stay_rejected(self):
        c=self.case();request=self.partial(c);self.ok(c.m.call('create',request,execute=True))
        op=c.m._operations()[0];before=self.raw(c,op)
        different=({'vcpus':2},{'memory_mib':768},{'name':'different'},
                   {'template_id':'debian-13-arm64'},{'template_version':'9.9.9'})
        for change in different:
            with self.subTest(change=change),patch.object(catalog,'entries',side_effect=AssertionError('conflict selected catalog')):
                result=c.m.call('create',request|change,execute=True)
                self.assertEqual(result['error']['code'],'IDEMPOTENCY_CONFLICT')
        no_ip=dict(request);no_ip.pop('ipv4')
        self.assertEqual(c.m.call('create',no_ip,execute=True)['error']['code'],'IDEMPOTENCY_CONFLICT')
        for key in ('vcpus','memory_mib'):
            for value in (None,True,False,0,-1,'1',[],{}):
                with self.subTest(key=key,value=value):
                    result=c.m.call('create',request|{key:value},execute=True)
                    self.assertEqual(result['error']['code'],'INVALID_SPEC')
        self.assertEqual(self.raw(c,op),before);self.assertEqual(c.copies,1)

    def test_complete_cli_then_omitted_cli_keeps_nondefault_resources(self):
        c=self.case();args=['create','--vm-id',c.spec['vm_id'],'--name',c.spec['name'],
            '--template-id',c.spec['template_id'],'--template-version',c.spec['template_version'],
            '--ipv4',c.spec['ipv4'],'--execute','--json']
        with redirect_stdout(io.StringIO()) as stream:
            first=cli.main(args+['--vcpus','2','--memory-mib','768'],c.m)
        self.ok(first);self.assertEqual(json.loads(stream.getvalue()),first)
        with redirect_stdout(io.StringIO()) as stream:replay=cli.main(args,c.m)
        self.ok(replay);self.assertEqual(json.loads(stream.getvalue()),replay)
        self.assertEqual(replay['operation_id'],first['operation_id'])
        self.assertEqual(replay['data']['initial_spec']['memory_mib'],768);self.assertEqual(c.copies,1)

    def test_current_configuration_is_not_original_creation(self):
        c=self.case();request=copy.deepcopy(c.spec);first=c.m.call('create',request,execute=True);self.ok(first)
        op=c.m._operations()[0];raw=self.raw(c,op)
        view=self.ok(c.m.call('get',{'vm_id':request['vm_id']}))['vm']
        resized=c.m.call('update-resources',{'vm_id':request['vm_id'],'vcpus':2,'memory_mib':768,
            'expected_lifecycle_generation':view['generation'],
            'expected_configuration_sha256':view['configuration_sha256']},execute=True,idempotency_key='compat-resize')
        self.ok(resized)
        for incoming in (self.partial(c),request):
            result=c.m.call('create',incoming,execute=True);self.ok(result)
            self.assertEqual(result['data']['initial_spec']['memory_mib'],512)
            self.assertEqual(result['data']['configuration']['memory_mib'],768)
            self.assertEqual(result['operation_id'],first['operation_id']);self.assertEqual(self.raw(c,op),raw)
        self.assertEqual(c.m.call('create',request|{'memory_mib':768},execute=True)['error']['code'],'IDEMPOTENCY_CONFLICT')

    def test_preacceptance_race_uses_winning_operation_not_transient_defaults(self):
        c=self.case();omitted=self.partial(c);entered,release=threading.Event(),threading.Event();rows=[]
        enter=ownership.LinuxSlot.__enter__
        def slot(owner):
            if threading.current_thread().name=='compat-omitted':
                entered.set()
                if not release.wait(10):raise RuntimeError('owned admission barrier not released')
            return enter(owner)
        with patch.object(ownership.LinuxSlot,'__enter__',slot):
            worker=threading.Thread(name='compat-omitted',target=lambda:rows.append(c.m.fork().call('create',omitted,execute=True)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3));self.assertFalse((c.data/'operations').exists())
                winner=c.m.fork().call('create',omitted|{'vcpus':2,'memory_mib':768},execute=True);self.ok(winner)
                op=c.m._operations()[0];before=self.raw(c,op)
                with patch.object(catalog,'entries',side_effect=AssertionError('loser selected newer defaults')):
                    release.set();worker.join(10)
            finally:release.set();worker.join(10)
        self.assertFalse(worker.is_alive());self.ok(rows[0])
        self.assertEqual(rows[0]['operation_id'],winner['operation_id']);self.assertTrue(rows[0]['replayed'])
        self.assertEqual(self.raw(c,op),before);self.assertEqual(len(c.m._operations()),1);self.assertEqual(c.copies,1)

    def test_failed_and_settled_origins_never_recreate_under_equivalent_input(self):
        c=self.case(True);op=c.fail_copy();request={k:v for k,v in op['request'].items() if k not in ('vcpus','memory_mib')}
        for settled in (False,True):
            if settled:self.ok(c.m.call('operation-reconcile',c.request(op),execute=True))
            current=c.m._operations()[0];before=self.raw(c,current)
            with patch.object(catalog,'entries',side_effect=AssertionError('failed origin selected defaults')), \
                 patch.object(c.m,'preparer',side_effect=AssertionError('failed origin created again')):
                result=c.m.call('create',request,execute=True)
            self.assertFalse(result['ok']);self.assertNotEqual(result['error']['code'],'IDEMPOTENCY_CONFLICT')
            self.assertEqual(result['operation_id'],op['operation_id']);self.assertTrue(result['replayed'])
            self.assertEqual(self.raw(c,current),before)
            if settled:self.assertFalse(result['error']['details']['original_create_succeeded'])

    def test_active_nondefault_full_origin_accepts_omitted_replay(self):
        c=reads.ReadProgressTest('runTest');c.setUp();self.addCleanup(c.doCleanups)
        select=c.select
        def nondefault(distribution):
            result=select(distribution);c.spec.update(vcpus=2,memory_mib=768);return result
        c.select=nondefault
        with c.blocked('ubuntu') as state:
            original=copy.deepcopy(state['op']);incoming={k:v for k,v in c.spec.items() if k not in ('vcpus','memory_mib')}
            with patch.object(catalog,'entries',side_effect=AssertionError('active replay selected defaults')):
                replay=c.m.fork().call('create',incoming,execute=True)
            data=self.ok(replay);self.assertEqual(data['effective_status'],'RUNNING')
            self.assertEqual(replay['operation_id'],original['operation_id'])
            for key in ('request','initial_spec','spec_hash','creation','assigned'):
                self.assertEqual(data['operation'][key],original[key])
            self.assertFalse(state['release'].is_set());self.assertTrue(state['thread'].is_alive())
            self.assertEqual(len([o for o in c.m._operations() if o['vm_id']==c.spec['vm_id']]),1)
        self.assertEqual(state['response']['operation_id'],original['operation_id'])

    def test_supported_mixed_journals_replay_without_conversion(self):
        c=self.case();self.ok(c.m.call('create',c.spec,execute=True))
        partial=self.partial(c)|{'vm_id':uuid4(),'name':'recent'};partial.pop('ipv4')
        self.ok(c.m.call('create',partial,execute=True))
        descriptor=defaults.InputDefaultsTest.publish_zephyr(self,c);c.m.preparer=c.m._prepare
        zephyr={'vm_id':uuid4(),'name':'legacy-rtos','template_id':descriptor['template_id'],
            'template_version':descriptor['template_version'],'vcpus':1,'memory_mib':16}
        self.ok(c.m.call('create',zephyr,execute=True))
        operations=c.m._operations();self.assertEqual({o['schema_version'] for o in operations},{'1.0','1.1','1.2'})
        for op in operations:
            incoming={k:v for k,v in op['request'].items() if k not in ('vcpus','memory_mib')}
            if op['schema_version']=='1.2':incoming.update({k:op['initial_spec'][k] for k in ('vcpus','memory_mib')})
            before=self.raw(c,op)
            with patch.object(catalog,'entries',side_effect=AssertionError('mixed replay selected catalog')):
                replay=c.m.call('create',incoming,execute=True)
            self.ok(replay);self.assertEqual(replay['operation_id'],op['operation_id']);self.assertEqual(self.raw(c,op),before)

    @unittest.skipUnless(importlib.util.find_spec('fastapi'),'FastAPI/Pydantic/httpx not installed in existing host; actual producer tested separately')
    def test_unmodified_http_full_request_recovers_accepted_partial_origin(self):
        # 기존 제품/fixture를 그대로 불러온다. 새 축약 adapter나 FakeManager를 만들지 않는다.
        folder=Path(__file__).resolve().parents[1]/'services/dom0-manager/tests'
        with patch.object(sys,'path',[str(folder),*sys.path]):
            source=importlib.util.spec_from_file_location('compat_http_fixture',folder/'test_platform_integration.py')
            http=importlib.util.module_from_spec(source);source.loader.exec_module(http)
        with patch.object(http.vm,'storage_check'),patch.object(http.vm.Platform,'check_network'),patch.object(http.vm.Platform,'check_xen'):
            b=http.Board()
            try:
                manager=b.manager;prepare=Mock(wraps=manager.preparer);manager.preparer=prepare
                request={'vm_id':str(http.NEW_VM),'name':'unchanged HTTP','template_id':http.TEMPLATE_ID,
                         'template_version':http.TEMPLATE_VERSION,'memory_mib':256}
                write=manager._write
                def lost(path,value):
                    write(path,value)
                    if path.parent==b.data/'operations':raise OSError(5,'owned acceptance response lost')
                with patch.object(manager,'_write',lost):accepted=manager.call('create',request,execute=True)
                self.assertFalse(accepted['ok']);op=manager._operations()[0];self.assertEqual(prepare.call_count,0)
                wrapped=http.SerializedManager(manager)
                app=http.create_app(http.Settings(driver='platform',log_level='WARNING'),
                    driver=http.PlatformDriver(wrapped,http.Xl(http.FakeRunner({('xl','info'):http.XL_INFO}))),
                    provisioner=http.PlatformProvisioner(wrapped))
                client=http.TestClient(app)
                try:
                    response=client.post('/api/v1/vms',json=request|{'vcpus':1})
                    self.assertEqual(response.status_code,201,response.text)
                    self.assertEqual(set(response.json()),{'vm_id','state','vcpus','memory_mib'})
                    self.assertEqual(response.json()['memory_mib'],256)
                    self.assertEqual(client.post('/api/v1/vms',json=request|{'vcpus':1}).status_code,409)
                finally:client.close()
                final=manager._operations()[0]
                self.assertEqual(final['operation_id'],op['operation_id']);self.assertEqual(final['request'],op['request'])
                self.assertEqual(final['spec_hash'],op['spec_hash']);self.assertEqual(final['creation'],op['creation'])
                self.assertEqual(prepare.call_count,1)
            finally:b.close()
