"""A 전용 firmware/usage/DHCP 지연 중 B의 조회·수락·실제 stop 경계를 분리한다."""
import json
import os
from pathlib import Path
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import test_xenova_read_progress as reads
import test_xenova_network_zephyr as network
import xenova_zephyr as zephyr
from xenova_execution import short_lock
from xenova_runtime import PlatformError
import xenova_vm as vm


class CrossBoundaryTest(unittest.TestCase):
    def fixture(self, firmware=False):
        case=(network.NetworkPlatformTest if firmware else reads.ReadProgressTest)('runTest')
        case.setUp();self.addCleanup(case.doCleanups)
        return case

    def evidence(self,name,value):
        folder=os.environ.get('XENOVA_STAGE4_EVIDENCE')
        if folder:
            with (Path(folder)/(name+'.json')).open('x') as stream:json.dump(value,stream,indent=2)

    def barrier(self,case,request,action,spec,other,key,install):
        entered,release=threading.Event(),threading.Event();results=[]
        def hold():
            entered.set()
            if not release.wait(12):raise RuntimeError('owned A-only barrier not released')
        with install(hold):
            worker=threading.Thread(target=lambda:results.append(request.call(action,spec,execute=True,idempotency_key=key)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5),key)
                target=spec.get('vm_id')
                if target:
                    conflict=case.m.fork().call('start',{'vm_id':target},idempotency_key=key+'-same-target')
                    self.assertFalse(conflict['ok'],conflict)
                    self.assertEqual(conflict['error']['code'],'OPERATION_IN_PROGRESS')
                    duplicate=case.m.fork().call(action,spec,execute=True,idempotency_key=key)
                    if duplicate['ok']:
                        self.assertEqual(duplicate['data']['effective_status'],'RUNNING')
                    else:self.assertEqual(duplicate['error']['code'],'OPERATION_IN_PROGRESS')
                queried=case.m.fork().call('get',{'vm_id':other},budget_ms=2000)
                original=case.m.fork().call('operation',{'idempotency_key':'create:'+other},budget_ms=2000)
                admission=True
                try:
                    with short_lock(case.p.runtime,'admission'):pass
                except PlatformError:admission=False
                stopped=case.m.fork().call('stop',{'vm_id':other},idempotency_key=key+'-stop',budget_ms=2000)
                control=case.m.fork().call('control-operation',{'idempotency_key':key+'-stop'},budget_ms=2000)
                value={'a_action':action,'point':key,'a_released':False,'get':queried,'original':original,
                       'admission_available':admission,'stop':stopped,'control':control,
                       'physical_stop_completed':bool(control['ok'] and control['data']['operation']['phase']=='completed')}
                self.evidence(key,value)
                self.assertTrue(worker.is_alive());self.assertFalse(release.is_set())
                self.assertTrue(queried['ok'],queried);self.assertTrue(original['ok'],original)
                self.assertTrue(admission,value)
                self.assertTrue(stopped['ok'],value)
                self.assertTrue(value['physical_stop_completed'],value)
            finally:release.set();worker.join(15)
        self.assertFalse(worker.is_alive());self.assertTrue(results[0]['ok'],results)
        self.assertTrue(case.m.fork().call('stop',{'vm_id':other},idempotency_key=key+'-stop')['ok'])

    def firmware(self,point):
        case=self.fixture(True)
        case.ok(case.m.call('start',{'vm_id':case.linux['uuid']},idempotency_key='start-B'))
        spec=case.spec();request=case.m.fork()
        original=getattr(zephyr,point)
        def install(hold):
            def delayed(source,*args,**kwargs):
                if Path(source)==case.fw:hold()
                return original(source,*args,**kwargs)
            return patch.object(zephyr,point,delayed)
        self.barrier(case,request,'firmware-transition',spec,case.linux['uuid'],'X-DWAIT-'+point,install)

    def test_firmware_validation_does_not_block_other_stop(self):self.firmware('bundle_info')
    def test_firmware_copy_does_not_block_other_stop(self):self.firmware('copy_revision')

    def test_resource_usage_does_not_block_other_stop(self):
        case=self.fixture();first,other=case.pair()
        self.assertTrue(case.m.call('stop',{'vm_id':first},idempotency_key='stop-A')['ok'])
        view=case.m.call('get',{'vm_id':first})['data']['vm']
        spec={'vm_id':first,'vcpus':2,'memory_mib':512,'expected_lifecycle_generation':view['generation'],
              'expected_configuration_sha256':view['configuration_sha256']}
        request=case.m.fork();original=request.usage_check
        def install(hold):
            def delayed(*args):hold();return original(*args)
            return patch.object(request,'usage_check',delayed)
        self.barrier(case,request,'update-resources',spec,other,'X-DWAIT-resource-usage',install)

    def dhcp(self,action,point='reload'):
        case=self.fixture();first,other=case.pair()
        self.assertTrue(case.m.call('stop',{'vm_id':first},idempotency_key='stop-A')['ok'])
        if action=='restore':
            self.assertTrue(case.m.call('retire',{'vm_id':first},execute=True,idempotency_key='retire-A')['ok'])
        request=case.m.fork();original=request.p.command
        def install(hold):
            def delayed(args,**kwargs):
                if args[:2]==['systemctl',point]:hold()
                return original(args,**kwargs)
            return patch.object(request.p,'command',delayed)
        self.barrier(case,request,action,{'vm_id':first},other,'X-DWAIT-'+action+'-dhcp-'+point,install)

    def test_retire_dhcp_does_not_block_other_stop(self):self.dhcp('retire')
    def test_restore_dhcp_does_not_block_other_stop(self):self.dhcp('restore')
    def test_retire_dhcp_start_does_not_block_other_stop(self):self.dhcp('retire','start')
    def test_restore_dhcp_start_does_not_block_other_stop(self):self.dhcp('restore','start')

    def test_dhcp_service_wait_cannot_overwrite_a_new_registered_reservation(self):
        case=self.fixture();first,_=case.pair()
        retired_mac=next(r['mac'] for r in case.p.records(validate_assets=False).values() if r['uuid']==first)
        self.assertTrue(case.m.call('stop',{'vm_id':first},idempotency_key='dhcp-stop-A')['ok'])
        request=case.m.fork();command=request.p.command
        entered,release=threading.Event(),threading.Event();out=[]
        def delayed(args,**kwargs):
            if args[:2]==['systemctl','start']:
                entered.set()
                if not release.wait(10):raise RuntimeError('DHCP start barrier not released')
            return command(args,**kwargs)
        with patch.object(request.p,'command',delayed):
            worker=threading.Thread(target=lambda:out.append(request.call('retire',{'vm_id':first},
                execute=True,idempotency_key='dhcp-retire-A')));worker.start()
            try:
                self.assertTrue(entered.wait(5))
                spec={**case.f.spec,'vm_id':'650e8400-e29b-41d4-a716-446655440005','name':'during-dhcp','ipv4':'192.168.77.45'}
                result=case.m.fork().call('create',spec,execute=True)
                self.assertTrue(result['ok'],result)
                record=next(r for r in case.p.records(validate_assets=False).values() if r['uuid']==spec['vm_id'])
            finally:release.set();worker.join(15)
        self.assertFalse(worker.is_alive());self.assertTrue(out[0]['ok'],out)
        hosts=(case.p.runtime/'dhcp-hosts').read_text()
        self.assertIn(record['mac']+','+record['ipv4']+',',hosts)
        self.assertNotIn(retired_mac+',',hosts)

    def test_legacy_firmware_replace_does_not_block_other_stop(self):
        case=self.fixture(True);request=case.m.fork();source=case.legacy
        case.ok(case.m.call('start',{'vm_id':case.linux['uuid']},idempotency_key='legacy-start-B'))
        original=zephyr.copy_revision
        def call(*args,**kwargs):
            vm.main(['firmware-replace',case.z['id'],'--bundle',str(source),
                     '--sha256',zephyr.sha(source/'template.json'),'--execute'])
            return {'ok':True}
        def install(hold):
            stack=ExitStack()
            def delayed(path,*args,**kwargs):
                if Path(path)==source:hold()
                return original(path,*args,**kwargs)
            stack.enter_context(patch.object(vm,'Platform',return_value=request.p))
            stack.enter_context(patch.object(zephyr,'copy_revision',delayed))
            return stack
        self.barrier(case,SimpleNamespace(call=call),'firmware-replace',{},case.linux['uuid'],
                     'X-DWAIT-legacy-firmware',install)
