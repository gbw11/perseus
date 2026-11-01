"""OS 공통 정적 배치·정책 버전 호환. mask 기대값은 렌더러와 독립적인 상수다."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_xenova_cpu as fixtures
import test_xenova_linux_template_paths as linux
import test_xenova_spring_followup as preparation
import test_xenova_recovery_foundation as foundation
from test_xenova_management import Interrupted
from first_install_contract import uuid4
import xenova_cpu as cpu
import xenova_vm as vm
import xenova_zephyr as zephyr
from xenova_execution import fingerprint


class PinningTest(unittest.TestCase):
    def case(self):
        case=fixtures.CPUTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        return case

    def spec(self, revision=None, *, shared=None, placements=None):
        return {'expected_policy_revision':revision,'shared_pcpus':[0,3] if shared is None else shared,
                'placements':[] if placements is None else placements}

    def evidence(self,name,value):
        folder=os.environ.get('XENOVA_STAGE4_EVIDENCE')
        if folder:
            with (Path(folder)/(name+'.json')).open('x') as stream:json.dump(value,stream,indent=2)

    def test_actual_debian_and_ubuntu_selection_full_vcpu_pinning_reboot_recovery_and_unpin(self):
        for distribution in ('debian','ubuntu'):
            with self.subTest(os=distribution):
                c=self.case();c.spec={k:v for k,v in c.f.spec.items() if k!='ipv4'}
                linux.DefaultLinuxTemplatePathTest.select_default(c,distribution)
                c.spec.update(vm_id=uuid4(),name='pin-'+distribution,vcpus=4)
                with preparation.FollowupFixture.linux_boundaries(c):
                    created=c.ok(c.m.call('create',c.spec,execute=True))
                record=next(r for r in c.p.records(validate_assets=False).values() if r['uuid']==c.spec['vm_id'])
                c.xen.records[record['id']]=record
                for i,guest in enumerate((c.z,c.z2)):
                    c.ok(c.m.call('retire',{'vm_id':guest['uuid']},execute=True,idempotency_key='no-zephyr-'+str(i)))
                request=self.spec(placements=[{'vm_id':record['uuid'],'mode':'shared','pcpus':[3]}])
                saved=c.ok(c.policy(request));self.assertEqual(saved['policy']['schema_version'],'2.0')
                c.ok(c.prepare());c.ok(c.control(record,key='pin-start'))
                rendered=(c.p.runtime/(record['id']+'.cfg')).read_text()
                self.assertIn("cpus = '3'",rendered)
                observed=c.ok(c.layout(vm_id=record['uuid']))['vms'][0]
                self.assertEqual(record['vcpus'],4)
                self.assertEqual(observed['observed']['hard_affinity'],[{'vcpu':i,'pcpus':[3]} for i in range(4)])
                self.assertTrue(observed['affinity_matches']);self.assertIsNone(observed['exclusive_verified'])
                pins=[call for call in c.xen.calls if call[:2]==['xl','vcpu-pin']]
                c.fails(c.policy(self.spec(1),key='live-unpin'),'VM_NOT_STOPPED')
                self.assertEqual(pins,[call for call in c.xen.calls if call[:2]==['xl','vcpu-pin']])
                c.cooperative_removes=False
                c.ok(c.control(record,'reboot',key='pin-reboot'))
                c.vm_id=record['uuid']
                recovery=foundation.FoundationTest.spec(c)
                with patch.object(vm.os,'sync'):
                    c.ok(c.m.call('recover_once',recovery,execute=True))
                c.ok(c.control(record,'stop',key='pin-stop'))
                c.ok(c.policy(self.spec(1),key='unpin'));c.ok(c.prepare())
                c.ok(c.control(record,key='default-start'))
                current=c.ok(c.layout(vm_id=record['uuid']))['vms'][0]
                self.assertEqual(current['observed']['hard_affinity'],[{'vcpu':i,'pcpus':[0,3]} for i in range(4)])
                self.evidence('C-T01-'+distribution,{'create':created,'pinned':observed,'unpinned':current,'rendered':rendered})

    def test_shared_overlap_exclusive_masks_and_every_vcpu_drift(self):
        c=self.case()
        record=c.linux
        spec=self.spec(placements=[{'vm_id':record['uuid'],'mode':'exclusive','pcpus':[1,2]},
                                   {'vm_id':c.zid,'mode':'shared','pcpus':[3]},
                                   {'vm_id':c.z2['uuid'],'mode':'shared','pcpus':[3]}])
        c.ok(c.policy(spec));c.ok(c.prepare())
        state=c.state()
        c.ok(c.m.call('update-resources',{'vm_id':record['uuid'],'vcpus':4,'memory_mib':512,
             'expected_lifecycle_generation':state['generation'],'expected_configuration_sha256':state['configuration_sha256']},
             execute=True,idempotency_key='cpu-resources'))
        record=c.p.records(validate_assets=False)[record['id']];c.xen.records[record['id']]=record
        c.ok(c.control(record));c.ok(c.control(c.z));c.ok(c.control(c.z2))
        observed=c.ok(c.layout(vm_id=record['uuid']))['vms'][0]
        self.assertEqual(observed['observed']['hard_affinity'],[{'vcpu':i,'pcpus':[1,2]} for i in range(4)])
        self.assertTrue(observed['exclusive_verified'])
        c.affinities[observed['observed']['domid']][3]['pcpus']=[0]
        c.fails(c.control(record),'CPU_APPLY_INCOMPLETE')
        c.ok(c.control(record,'stop'))
        self.assertFalse(any(call[:2] in (['xl','sched-credit2'],['xl','cpupool-migrate']) for call in c.xen.calls))

    def test_three_exclusive_reservations_and_additional_shared_zephyr(self):
        c=self.case()
        spec={'expected_policy_revision':None,'shared_pcpus':[0],
              'exclusive':[{'vm_id':r['uuid'],'pcpu':core} for r,core in ((c.linux,1),(c.z,2),(c.z2,3))]}
        c.ok(c.policy(spec));c.ok(c.prepare())
        additional=zephyr.new_instance(c.p.data,'extra-shared',c.legacy,{})
        c.p.execution().initialize(additional,'stopped','create');c.xen.records[additional['id']]=additional
        c.ok(c.control(additional))
        value=c.ok(c.layout(vm_id=additional['uuid']))['vms'][0]
        self.assertEqual(value['observed']['hard_affinity'],[{'vcpu':0,'pcpus':[0]}])
        self.assertEqual(len(c.p.cpu().policy()['placements']),3)

    def test_retired_and_purged_reservation_can_be_explicitly_released_and_reassigned(self):
        c=self.case();c.activate()
        c.ok(c.m.call('retire',{'vm_id':c.zid},execute=True,idempotency_key='cpu-retire'))
        c.ok(c.m.call('purge',{'vm_id':c.zid,'confirm_vm_id':c.zid},execute=True,idempotency_key='cpu-purge'))
        spec=self.spec(1,placements=[{'vm_id':c.linux['uuid'],'mode':'exclusive','pcpus':[1]},
                                    {'vm_id':c.z2['uuid'],'mode':'exclusive','pcpus':[2]}])
        c.ok(c.policy(spec,key='transfer'));c.ok(c.prepare());c.ok(c.control(c.linux))
        current=c.ok(c.layout())
        self.assertNotIn(c.zid,[v['vm_id'] for v in current['reservations']])
        self.assertTrue(next(v for v in current['vms'] if v['vm_id']==c.linux['uuid'])['exclusive_verified'])
        c.ok(c.control(c.linux,'stop'))
        # Removing both factory VMs does not make the common platform require A/B.
        c.ok(c.m.call('retire',{'vm_id':c.z2['uuid']},execute=True,idempotency_key='cpu-retire2'))
        (c.p.data/'.first-install-state.json').write_text(json.dumps({'storage_uuid':uuid4(),
            'identities':{'a':{'uuid':c.zid},'b':{'uuid':c.z2['uuid']}},'cpu_policy_activation':c.spec()}))
        c.ok(c.policy(self.spec(2),key='linux-only'));c.ok(c.prepare());c.ok(c.control(c.linux))
        self.assertEqual([v['vm_id'] for v in c.ok(c.layout())['vms']],[c.linux['uuid']])

    def legacy(self,c,phase='completed'):
        request=c.spec();opid=uuid4()
        candidate={'schema_version':'1.0','operation_id':opid,'policy_revision':1,
                   'pool_name':'Pool-0','shared_pcpus':[0,3],
                   'exclusive':sorted(request['exclusive'],key=lambda item:item['vm_id'])}
        request['exclusive']=candidate['exclusive']
        op={'schema_version':'1.0','operation_id':opid,'idempotency_key':'old-writer',
            'request':request,'base_revision':None,'candidate':candidate,'phase':phase}
        cpu=c.p.cpu();cpu.e.write(cpu.journals/(opid+'.json'),op)
        if phase=='completed':
            cpu.e.write(cpu.anchor,{'schema_version':'1.0','activation_operation_id':opid})
            cpu.e.write(cpu.policy_path,candidate)
        return request,candidate,op

    def test_legacy_hash_and_completed_replay_survive_new_policy_without_rewrite(self):
        c=self.case();request,legacy,op=self.legacy(c)
        path=c.p.cpu().journals/(op['operation_id']+'.json');before=path.read_bytes()
        self.assertEqual(c.p.cpu().context(c.z)['policy_sha256'],fingerprint(legacy))
        c.ok(c.policy(self.spec(1),key='new-writer'))
        replay=c.ok(c.policy(request,key='old-writer'))
        self.assertEqual(replay['policy'],legacy);self.assertEqual(replay['current_policy']['schema_version'],'2.0')
        self.assertEqual(path.read_bytes(),before)
        cold=vm.Platform(c.p.data,c.p.runtime,command=c.p.command,defaults_path=c.p.defaults_path)
        self.assertEqual(cold.cpu().policy(),replay['current_policy'])

    def test_legacy_prepared_publication_resumes_in_its_original_format(self):
        c=self.case();request,legacy,op=self.legacy(c,'prepared')
        resumed=c.ok(c.policy(request,key='old-writer'))
        self.assertEqual(resumed['policy'],legacy)
        self.assertEqual(c.p.cpu().operations()[0]['phase'],'completed')
        c.ok(c.prepare());c.ok(c.control(c.linux))

    def test_new_publication_failure_and_unknown_versions_fail_closed(self):
        c=self.case();request=self.spec(placements=[{'vm_id':c.linux['uuid'],'mode':'shared','pcpus':[3]}])
        def interrupted(phase,value):
            if phase=='cpu-policy-published':raise Interrupted()
        c.p.execution_checkpoint=interrupted
        with self.assertRaises(Interrupted):c.policy(request)
        c.p.execution_checkpoint=lambda *args:None
        c.ok(c.policy(request));c.ok(c.prepare());c.ok(c.control(c.linux))
        c.ok(c.control(c.linux,'stop'))
        value=c.p.cpu().policy();value['schema_version']='3.0'
        c.p.cpu().e.write(c.p.cpu().policy_path,value)
        c.fails(c.control(c.linux),'CPU_POLICY_INVALID')

    def test_invalid_masks_modes_and_mixed_input_do_not_publish(self):
        c=self.case()
        for item in ({'vm_id':c.linux['uuid'],'mode':'shared','pcpus':[1]},
                     {'vm_id':c.linux['uuid'],'mode':'exclusive','pcpus':[0]},
                     {'vm_id':c.linux['uuid'],'mode':'live','pcpus':[1]},
                     {'vm_id':c.linux['uuid'],'mode':'exclusive','pcpus':[]}):
            c.fails(c.policy(self.spec(placements=[item])),'INVALID_SPEC')
        c.fails(c.policy(self.spec()|{'exclusive':[]}),'INVALID_SPEC')
        self.assertFalse(c.p.cpu().policy_path.exists())
