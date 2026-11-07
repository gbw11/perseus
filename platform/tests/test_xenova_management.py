"""작은 실제 파일/영구 JSON으로 신원·중단·수명주기를 검증한다. Xen/mount/대용량 준비만 대역이다."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from first_install_fixture import ROOT, FILES
from test_xenova_platform import fixture, record, FakeXen, domain
from test_xenova_zephyr import bundle
from test_xenova_lifecycle import application_bundle
import xenova_vm as vm
import xenova_catalog as catalog
import xenova_management as management
import xenova_management_cli as cli
import xenova_zephyr as zephyr
from artifact_io import publish


class Interrupted(BaseException):
    pass


class ManagementTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name)/'data';self.data.mkdir()
        (self.data/'instances').mkdir()
        self.run=Path(self.tmp.name)/'run'
        self.xen=FakeXen(self.data,{})
        self.p=vm.Platform(self.data,self.run,command=self.xen,defaults_path=FILES/'defaults.json')
        # 명시적 로컬 시간 프로필. 생성 운영 기본값으로 설치하지 않는다.
        self.p.creation_policy_path=self.data/'creation-policy.json'
        self.p.creation_policy_path.write_text(json.dumps({'schema_version':'1.0','call_ms':3600000,
                                                         'execution_ms':3500000,'cleanup_ms':5000}))
        self.storage=patch.object(vm,'storage_check');self.storage.start();self.addCleanup(self.storage.stop)
        self.m=management.Manager(self.p,preparer=self.prepare,usage_check=lambda *args:None, defaults_path=FILES/'defaults.json')
        network=patch.object(self.p,'check_network');network.start();self.addCleanup(network.stop)
        self.copies=0
        source=self.data/'templates/source';source.mkdir(parents=True)
        for name in ('root.raw','Image','initrd','kernel.config'):
            (source/name).write_bytes(b'fixture')
        with (source/'root.raw').open('r+b') as stream:
            stream.seek(1080);stream.write(b'\x53\xef')
        h=catalog.sha(source/'Image')
        boot={'source':{'distribution':'ubuntu'},'kernel_release':'test','root_device':'/dev/xvda',
              'kernel_sha256':h,'initrd_sha256':h,'config_sha256':h}
        (source/'boot.json').write_text(json.dumps(boot))
        meta={'id':'source','version':'1.0.0','source':boot['source'],'root_sha256':catalog.sha(source/'root.raw'),
              'files':{p.name:{'sha256':catalog.sha(p),'bytes':p.stat().st_size} for p in source.iterdir()}}
        (source/'template.json').write_text(json.dumps(meta))
        for path in source.iterdir():path.chmod(0o444)
        self.descriptor={'schema_version':'1.0','template_id':'ubuntu-24.04-arm64','template_version':'1.0.0',
                         'guest_kind':'linux','asset_directory':'templates/source','source_template_id':'source',
                         'manifest_sha256':catalog.sha(source/'template.json')}
        d=self.data/'catalog/ubuntu-24.04-arm64';d.mkdir(parents=True)
        (d/'1.0.0.json').write_text(json.dumps(self.descriptor))
        self.spec={'vm_id':'550e8400-e29b-41d4-a716-446655440000','name':'custom','template_id':'ubuntu-24.04-arm64','template_version':'1.0.0',
                   'vcpus':1,'memory_mib':512,'ipv4':'192.168.77.30'}

        self.key=management.internal_name(self.spec['vm_id'])

    def test_starter_heartbeat_capability_does_not_rewrite_firmware_manifest(self):
        source=application_bundle(bundle(self.data/"legacy"),self.data/"rtos-templates"/"capability-fixture")
        manifest=source/"firmware.json"
        meta=json.loads(manifest.read_text())
        meta.update(id="capability-fixture",kind="zephyr-xen-firmware",template_version="1.0.0")
        descriptor={"schema_version":"1.0","template_id":"capability-fixture",
                    "template_version":catalog.deployment_version(meta),"guest_kind":"zephyr",
                    "asset_directory":"rtos-templates/capability-fixture",
                    "source_template_id":meta["id"],"manifest_sha256":""}
        for app,expected in (("xenova-starter",False),("xenova-network-starter",False),("custom-app",None)):
            with self.subTest(app=app):
                meta["source"]["app"]=app
                manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
                descriptor["manifest_sha256"]=catalog.sha(manifest)
                original=manifest.read_bytes()
                view=catalog.describe(self.data,descriptor)
                self.assertIs(view["capabilities"].get("app_heartbeat"),expected)
                self.assertEqual(manifest.read_bytes(),original)

    def prepare(self,op):
        self.copies+=1
        spec=op['initial_spec'];r=record();name=op['assigned']['internal_name']
        r.update(guest_kind='linux',id=name,name=name,hostname=name,display_name=spec['name'],uuid=op['vm_id'],
                 mac=op['assigned']['mac'],ipv4=op['assigned']['ipv4'],memory_mib=spec['memory_mib'],vcpus=spec['vcpus'],
                 autostart=False,creation_spec=spec,creation_operation_id=op['operation_id'],
                 template_manifest_sha256=op['template']['manifest_sha256'],
                 template_id=spec['template_id'],template_version=spec['template_version'])
        directory=fixture(self.data,r)
        with (directory/'root.raw').open('wb') as stream:
            stream.seek(1080);stream.write(b'\x53\xef')
        (directory/'saved-user-file').write_text('keep me')
        return r

    def call(self,action,spec=None,key=None,execute=True):
        return self.m.call(action,spec,execute=execute,idempotency_key=key or action)

    def create(self):
        result=self.call('create',self.spec)
        self.assertTrue(result['ok'],result)
        return result['data']['vm_id']

    def test_create_registration_waits_for_admission_in_the_existing_call_budget(self):
        import xenova_execution as execution
        now=[1000.0];holder=[None];waits=[]
        self.p.clock=lambda:now[0]
        def checkpoint(phase,op):
            if phase=='dhcp-applied' and holder[0] is None:
                holder[0]=execution.short_lock(self.p.runtime,'admission');holder[0].__enter__()
        def sleep(delay):
            waits.append(delay);now[0]+=delay
            from xenova_runtime import operation_lock
            with operation_lock(self.run):pass  # admission wait holds no common lock
            self.assertFalse((self.data/'control'/(self.spec['vm_id']+'.json')).exists())
            holder[0].__exit__(None,None,None);holder[0]=None
        self.m.checkpoint=checkpoint;self.p.sleep=sleep
        try:answer=self.m.call('create',self.spec,execute=True,idempotency_key='admission-create',budget_ms=6000)
        finally:
            if holder[0] is not None:holder[0].__exit__(None,None,None)
        self.assertTrue(answer['ok'],answer);self.assertEqual(len(waits),1)
        self.assertEqual(self.copies,1)
        intent=json.loads((self.data/'control'/(self.spec['vm_id']+'.json')).read_text())
        self.assertEqual(intent['desired_state'],'stopped')
        self.assertEqual(intent['accepted_operation_id'],answer['operation_id'])

    def test_registration_budget_expiry_preserves_original_create_for_same_key_recovery(self):
        import xenova_execution as execution
        now=[1000.0];holder=[None]
        self.p.clock=lambda:now[0];self.p.sleep=lambda delay:now.__setitem__(0,now[0]+delay)
        def checkpoint(phase,op):
            if phase=='dhcp-applied' and holder[0] is None:
                holder[0]=execution.short_lock(self.p.runtime,'admission');holder[0].__enter__()
        self.m.checkpoint=checkpoint
        try:first=self.m.call('create',self.spec,execute=True,idempotency_key='admission-expiry',budget_ms=6000)
        finally:
            if holder[0] is not None:holder[0].__exit__(None,None,None)
        self.assertFalse(first['ok']);self.assertEqual(first['error']['code'],'DEADLINE_EXCEEDED')
        self.assertFalse((self.data/'control'/(self.spec['vm_id']+'.json')).exists())
        path=self.data/'operations'/(first['operation_id']+'.json');before=json.loads(path.read_text())
        self.assertEqual(before['status'],'RECOVERY_REQUIRED')
        self.m.checkpoint=lambda *args:None
        replay=self.m.call('create',self.spec,execute=True,idempotency_key='admission-expiry',budget_ms=6000)
        self.assertTrue(replay['ok'],replay);self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_id'],first['operation_id']);self.assertEqual(self.copies,1)
        after=json.loads(path.read_text())
        for key in ('request','initial_spec','spec_hash','vm_id','creation'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(after['first_error']['code'],'DEADLINE_EXCEEDED')

    def test_registration_wait_rechecks_host_operation_lifecycle_and_configuration(self):
        import xenova_execution as execution
        for boundary in ('host','operation','lifecycle','configuration'):
            with self.subTest(boundary=boundary):
                f=ManagementTest('runTest');f.setUp();holder=[];original=[];now=[1000.0]
                try:
                    f.p.clock=lambda:now[0]
                    def checkpoint(phase,op):
                        if phase=='dhcp-applied':
                            original.append(json.loads(json.dumps(op)))
                            lock=execution.short_lock(f.p.runtime,'admission');lock.__enter__();holder.append(lock)
                    def changed(delay):
                        now[0]+=delay
                        if boundary=='host':f.p.boot_id_reader=lambda:'11111111-1111-4111-8111-111111111111'
                        else:
                            path=(f.data/'operations'/(original[0]['operation_id']+'.json') if boundary=='operation'
                                  else f.data/'tombstones'/(f.spec['vm_id']+'.json') if boundary=='lifecycle'
                                  else f.data/'instances'/f.key/'instance.json')
                            value=json.loads(path.read_text())
                            field={'operation':'phase','lifecycle':'generation','configuration':'memory_mib'}[boundary]
                            value[field]='external-change' if boundary=='operation' else value[field]+1
                            path.write_text(json.dumps(value))  # external writer fixture under its own admission
                        holder.pop().__exit__(None,None,None)
                    f.m.checkpoint=checkpoint;f.p.sleep=changed
                    response=f.m.call('create',f.spec,execute=True,idempotency_key='guard-'+boundary,budget_ms=6000)
                    self.assertFalse(response['ok']);self.assertEqual(response['error']['code'],'TARGET_CHANGED',response)
                    self.assertFalse((f.data/'control'/(f.spec['vm_id']+'.json')).exists())
                    self.assertEqual(f.copies,1)
                    after=json.loads((f.data/'operations'/(original[0]['operation_id']+'.json')).read_text())
                    self.assertEqual(after['spec_hash'],original[0]['spec_hash'])
                    self.assertEqual(after['status'],'RUNNING')  # another writer's result is not rewritten as failure
                finally:
                    if holder:holder.pop().__exit__(None,None,None)
                    f.doCleanups()

    def state(self,identity):
        result=self.call('get',{'vm_id':identity})
        self.assertTrue(result['ok'],result)
        return result['data']['vm']

    def cli_output(self,args):
        output=io.StringIO()
        with patch.object(cli,'Manager',return_value=self.m),redirect_stdout(output):
            result=vm.main(args)
        return result,output.getvalue()

    def test_cli_short_list_shows_display_and_internal_names_actual_state_and_full_identity(self):
        self.spec['name']='시험 개발 VM'
        identity=self.create()
        debian=record('debian-dev');debian['display_name']='Debian Interaction'
        directory=fixture(self.data,debian)
        self.xen.rows=[domain(debian,directory/'root.raw')]
        self.xen.states={1:'-b----'}
        result,output=self.cli_output(['list','--short'])
        self.assertTrue(result['ok'],result)
        self.assertEqual(output.splitlines(),[
            'NAME | INTERNAL_NAME | UUID | STATE | IP',
            f"Debian Interaction | debian-dev | {debian['uuid']} | blocked | {debian['ipv4']}",
            f'시험 개발 VM | {self.key} | {identity} | stopped | 192.168.77.30'])

    def test_cli_short_list_empty_prints_only_header(self):
        result,output=self.cli_output(['list','--short'])
        self.assertEqual(result['data']['vms'],[])
        self.assertEqual(output,'NAME | INTERNAL_NAME | UUID | STATE | IP\n')

    def test_cli_short_list_null_missing_fields_and_configuration_id_mapping(self):
        response=self.call('list',{})
        response['data']['vms']=[{},
            {'name':None,'configuration':None,'vm_id':None,'xen_state':None,'ipv4':None},
            {'name':'표시 이름','configuration':{'id':'registered-name','name':'not-the-cli-target'},
             'vm_id':self.spec['vm_id'],'xen_state':None,'desired_state':'running',
             'lifecycle':'REGISTERED','ipv4':'192.168.123.113'}]
        with patch.object(self.m,'call',return_value=response):
            result,output=self.cli_output(['list','--short'])
        self.assertIs(result,response)
        self.assertEqual(output.splitlines(),[
            'NAME | INTERNAL_NAME | UUID | STATE | IP',
            '- | - | - | - | -',
            '- | - | - | - | -',
            f"표시 이름 | registered-name | {self.spec['vm_id']} | - | 192.168.123.113"])

    def test_cli_list_keeps_existing_pretty_and_compact_json_output(self):
        self.spec['name']='시험 VM'
        self.create()
        for options,indent in (([],2),(['--json'],None)):
            with self.subTest(options=options):
                result,output=self.cli_output(['list',*options])
                self.assertTrue(result['ok'],result)
                self.assertEqual(output,json.dumps(result,ensure_ascii=False,indent=indent)+'\n')
                self.assertIn('configuration',result['data']['vms'][0])

    def test_cli_short_list_preserves_lifecycle_filter(self):
        identity=self.create()
        self.assertTrue(self.call('retire',{'vm_id':identity})['ok'])
        result,output=self.cli_output(['list','--short'])
        self.assertEqual(result['data']['vms'],[])
        self.assertEqual(output.count('\n'),1)
        for lifecycle in ('all','RETIRED'):
            with self.subTest(lifecycle=lifecycle):
                result,output=self.cli_output(['list','--short','--lifecycle',lifecycle])
                self.assertEqual(len(result['data']['vms']),1)
                self.assertEqual(result['data']['vms'][0]['lifecycle'],'RETIRED')
                self.assertIn(f"custom | {self.key} | {identity} |",output)

    def test_cli_short_option_conflicts_and_other_commands_fail_before_manager_call(self):
        for args in (['list','--short','--json'],['list','--json','--short'],
                     ['get',self.spec['vm_id'],'--short'],['templates','--short'],
                     ['start',self.spec['vm_id'],'--short','--json'],['status','--short','--json']):
            with self.subTest(args=args):
                output=io.StringIO()
                with patch.object(cli,'Manager',return_value=self.m),\
                     patch.object(self.m,'call') as call,redirect_stdout(output),\
                     self.assertRaises(SystemExit) as caught:
                    vm.main(args)
                self.assertEqual(caught.exception.code,2)
                self.assertEqual(json.loads(output.getvalue())['error']['code'],'INVALID_SPEC')
                call.assert_not_called()

    def test_cli_short_list_query_error_keeps_json_error_and_exit_code(self):
        self.create()
        errors=[]
        for options in ([],['--short'],['--json']):
            with self.subTest(options=options):
                output=io.StringIO()
                with patch.object(cli,'Manager',return_value=self.m),\
                     patch.object(cli,'uuid4',return_value=self.spec['vm_id']),\
                     patch.object(self.p,'command',side_effect=vm.PlatformError('injected Xen query failure')),\
                     redirect_stdout(output),self.assertRaises(SystemExit) as caught:
                    vm.main(['list',*options])
                self.assertEqual(caught.exception.code,4)
                result=json.loads(output.getvalue())
                self.assertFalse(result['ok'])
                self.assertIsNone(result['data'])
                self.assertNotIn('NAME |',output.getvalue())
                errors.append(result)
        self.assertEqual(errors[0],errors[1])
        self.assertEqual(errors[0],errors[2])

    def test_cli_list_help_documents_short_option(self):
        output=io.StringIO()
        with redirect_stdout(output),self.assertRaises(SystemExit) as caught:
            vm.main(['list','--help'])
        self.assertEqual(caught.exception.code,0)
        self.assertIn('--short',output.getvalue())
        self.assertIn('INTERNAL_NAME',output.getvalue())

    def test_plan_does_not_reserve_identity_key_name_ip_or_write_operations(self):
        result=self.call('create',self.spec,execute=False)
        self.assertTrue(result['ok'],result)
        self.assertIsNone(result['operation_id'])
        self.assertFalse((self.data/'operations').exists())
        self.assertEqual(list((self.data/'instances').iterdir()),[])
        self.create()

    def test_uuid_is_vm_id_same_key_replays_without_resetting_current_configuration(self):
        identity=self.create()
        path=self.data/f'instances/{self.key}/instance.json'
        r=json.loads(path.read_text());r['memory_mib']=768;r['autostart']=True;path.write_text(json.dumps(r))
        again=self.call('create',dict(reversed(list(self.spec.items()))))
        self.assertTrue(again['ok'],again);self.assertTrue(again['replayed'])
        self.assertEqual(again['data']['current']['configuration']['memory_mib'],768)
        self.assertEqual(self.copies,1)
        self.assertEqual(self.state(identity)['initial_spec']['memory_mib'],512)

    def test_idempotency_conflicts_across_spec_and_action_and_other_key_name(self):
        identity=self.create()
        conflict=self.call('create',{**self.spec,'memory_mib':768})
        self.assertEqual(conflict['error']['code'],'IDEMPOTENCY_CONFLICT')
        conflict=self.call('retire',{'vm_id':identity},key='create:'+self.spec['vm_id'])
        self.assertEqual(conflict['error']['code'],'IDEMPOTENCY_CONFLICT')
        conflict=self.call('create',self.spec,key='other')
        self.assertTrue(conflict['ok'],conflict);self.assertTrue(conflict['replayed'])

    def test_explicit_version_and_ip_and_resource_validation(self):
        for spec,code in [
            ({**self.spec,'template_version':'latest'},'INVALID_SPEC'),
            ({**self.spec,'template_version':'2.0.0'},'TEMPLATE_VERSION_NOT_FOUND'),
            ({**self.spec,'template_id':'unknown'},'TEMPLATE_NOT_FOUND'),
            ({**self.spec,'template_id':'../escape'},'INVALID_SPEC'),
            ({**self.spec,'vcpus':True},'INVALID_SPEC'),
            ({**self.spec,'memory_mib':1},'RESOURCE_UNSUPPORTED'),
            ({k:v for k,v in self.spec.items() if k!='vm_id'},'INVALID_SPEC')]:
            result=self.call('create',spec)
            self.assertFalse(result['ok'],result);self.assertEqual(result['error']['code'],code,result)
        self.create()
        result=self.call('create',{**self.spec,'vm_id':'750e8400-e29b-41d4-a716-446655440000','name':'another'},key='other')
        self.assertEqual(result['error']['code'],'IP_CONFLICT')

    def test_manifest_content_and_parent_links_cannot_select_another_artifact(self):
        source=self.data/'templates/source/template.json';source.chmod(0o644);source.write_bytes(b'changed');source.chmod(0o444)
        result=self.call('create',self.spec)
        self.assertEqual(result['error']['code'],'ARTIFACT_INVALID')
        self.assertFalse((self.data/'operations').exists())

    def test_interruption_after_identity_journal_reuses_same_allocated_uuid(self):
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=='recorded' else None
        with self.assertRaises(Interrupted):self.call('create',self.spec)
        op=json.loads(next((self.data/'operations').glob('*.json')).read_text())
        self.assertEqual(self.copies,0)
        self.m.checkpoint=lambda *args:None
        identity=self.create()
        self.assertEqual(identity,op['vm_id']);self.assertEqual(self.copies,1)

    def test_partial_copy_is_retained_and_not_reinitialized(self):
        def partial(op):
            path=self.data/op['owned_workspace'];path.mkdir();(path/'partial-disk').write_text('owned')
            raise Interrupted()
        self.m.preparer=partial
        with self.assertRaises(Interrupted):self.call('create',self.spec)
        self.m.preparer=self.prepare
        result=self.call('create',self.spec)
        self.assertEqual(result['error']['code'],'RECOVERY_REQUIRED')
        self.assertEqual(self.copies,0)
        self.assertEqual(next((self.data/'instances').glob('.operation-*/partial-disk')).read_text(),'owned')

    def test_published_before_success_response_recovers_without_copy(self):
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=='published' else None
        with self.assertRaises(Interrupted):self.call('create',self.spec)
        before=json.loads((self.data/f'instances/{self.key}/instance.json').read_text())['uuid']
        self.m.checkpoint=lambda *args:None
        self.assertEqual(self.create(),before);self.assertEqual(self.copies,1)

    def test_retire_restore_preserve_assets_and_old_retire_does_not_mutate_again(self):
        identity=self.create();directory=self.data/f'instances/{self.key}'
        before={p.name:p.read_bytes() for p in directory.iterdir()}
        retired=self.call('retire',{'vm_id':identity})
        self.assertTrue(retired['ok'],retired);self.assertFalse(directory.exists())
        self.assertEqual(self.state(identity)['lifecycle'],'RETIRED')
        result=self.call('restore',{'vm_id':identity})
        self.assertTrue(result['ok'],result)
        self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir()},before)
        again=self.call('retire',{'vm_id':identity})
        self.assertTrue(again['ok'],again)
        self.assertEqual(again['data']['current']['lifecycle'],'REGISTERED')
        self.assertEqual([a for a in self.xen.calls if a[:2]==['xl','create']],[])

    def test_retire_move_interruption_and_restore_move_interruption_recover(self):
        identity=self.create()
        for action in ('retire','restore'):
            self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=='moved' else None
            with self.assertRaises(Interrupted):self.call(action,{'vm_id':identity})
            self.m.checkpoint=lambda *args:None
            result=self.call(action,{'vm_id':identity})
            self.assertTrue(result['ok'],result)

    def test_running_vm_cannot_retire_and_identity_mismatch_is_not_a_stopped_vm(self):
        identity=self.create();r=json.loads((self.data/f'instances/{self.key}/instance.json').read_text())
        self.xen.rows=[domain(r,self.data/f'instances/{self.key}/root.raw')]
        self.assertEqual(self.call('retire',{'vm_id':identity})['error']['code'],'VM_NOT_STOPPED')
        self.xen.rows[0]['config']['c_info']['uuid']='bbbb0000-1111-4222-8333-123456789abc'
        self.assertEqual(self.call('retire',{'vm_id':identity})['error']['code'],'TARGET_CHANGED')

    def test_restore_ip_conflict_and_missing_assets_never_create_blank_disk(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        r=record('debian-dev');r['ipv4']=self.spec['ipv4'];fixture(self.data,r)
        result=self.call('restore',{'vm_id':identity})
        self.assertEqual(result['error']['code'],'RESTORE_CONFLICT')
        self.assertEqual(self.copies,1)

    def test_purge_requires_retired_and_exact_uuid_then_keeps_tombstone_and_operation(self):
        identity=self.create()
        self.assertEqual(self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})['error']['code'],'INVALID_SPEC')
        self.call('retire',{'vm_id':identity})
        self.assertEqual(self.call('purge',{'vm_id':identity,'confirm_vm_id':'wrong'})['error']['code'],'INVALID_SPEC')
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.state(identity)['lifecycle'],'PURGED')
        self.assertTrue((self.data/'tombstones'/(identity+'.json')).is_file())
        self.assertGreater(result['data']['space']['logical_bytes'],0)
        self.assertEqual(self.call('create',self.spec)['error']['code'],'VM_PURGED')
        self.assertEqual(self.call('create',self.spec,key='new')['error']['code'],'VM_PURGED')

    def test_partial_purge_resume_blocks_restore_and_reuses_inventory(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=='asset-deleted' else None
        with self.assertRaises(Interrupted):self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        result=self.call('restore',{'vm_id':identity})
        self.assertFalse(result['ok'])
        self.m.checkpoint=lambda *args:None
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertTrue(result['ok'],result)

    def test_unexpected_hardlink_or_parent_symlink_prevents_purge_and_external_file_is_unchanged(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        tomb=json.loads((self.data/'tombstones'/(identity+'.json')).read_text())
        archive=self.data/tomb['archive']
        outside=self.data/'outside';outside.write_text('keep outside')
        os.link(outside,archive/'unexpected')
        self.m.usage_check=self.m._usage
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertEqual(result['error']['code'],'ASSET_IN_USE',result)
        self.assertEqual(outside.read_text(),'keep outside')

    def test_actual_no_replace_publication_preserves_existing_empty_directory(self):
        source=self.data/'stage';source.mkdir();(source/'owned').write_text('new')
        target=self.data/'target';target.mkdir()
        with self.assertRaises(ValueError):publish(source,target)
        self.assertEqual(list(target.iterdir()),[])

    def test_cli_real_main_records_path_returns_single_json_for_success_and_invalid_input(self):
        with patch.object(cli,'Manager',side_effect=lambda: management.Manager(self.p, defaults_path=FILES/'defaults.json')):
            output=io.StringIO()
            with redirect_stdout(output):
                vm.main(['create','custom','--vm-id',self.spec['vm_id'],'--template-id','ubuntu-24.04-arm64','--template-version','1.0.0',
                         '--vcpus','1','--memory-mib','512','--ipv4','192.168.77.30','--idempotency-key','create:'+self.spec['vm_id'],'--json'])
            result=json.loads(output.getvalue());self.assertTrue(result['ok'],result)
            self.assertFalse((self.data/'operations').exists())
            output=io.StringIO()
            with redirect_stdout(output),self.assertRaises(SystemExit) as caught:
                vm.main(['create','custom','--memory-mib','wrong','--json'])
            self.assertEqual(caught.exception.code,2)
            self.assertEqual(json.loads(output.getvalue())['error']['code'],'INVALID_SPEC')

    def test_zephyr_real_independent_creation_has_no_ip_disk_and_fixed_profile(self):
        legacy=bundle(self.data/'old')
        source=application_bundle(legacy,self.data/'rtos-temp')
        manifest=source/'firmware.json';meta=json.loads(manifest.read_text())
        meta.update(id='starter-artifact',kind='zephyr-xen-firmware',version='1.0.0')
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        (self.data/'rtos-templates').mkdir();source.rename(self.data/'rtos-templates/starter-artifact')
        descriptor={'schema_version':'1.0','template_id':'zephyr-xenvm-starter-arm64','template_version':'1.0.0',
                    'guest_kind':'zephyr','asset_directory':'rtos-templates/starter-artifact','source_template_id':'starter-artifact',
                    'manifest_sha256':catalog.sha(self.data/'rtos-templates/starter-artifact/firmware.json')}
        d=self.data/'catalog/zephyr-xenvm-starter-arm64';d.mkdir();(d/'1.0.0.json').write_text(json.dumps(descriptor))
        spec={'vm_id':'650e8400-e29b-41d4-a716-446655440000','name':'rtos-app','template_id':descriptor['template_id'],'template_version':'1.0.0','vcpus':1,'memory_mib':16}
        self.m.preparer=self.m._prepare
        import xenova_execution as execution
        holder=[];waits=[];sleep=self.p.sleep
        save_tomb=self.m._save_tomb
        def saved(*args,**kwargs):
            result=save_tomb(*args,**kwargs)
            lock=execution.short_lock(self.p.runtime,'admission');lock.__enter__();holder.append(lock)
            return result
        def release(delay):
            waits.append(delay)
            if holder:holder.pop().__exit__(None,None,None)
            sleep(delay)
        self.p.sleep=release
        try:
            with patch.object(self.m,'_save_tomb',side_effect=saved):result=self.call('create',spec,key='zephyr')
        finally:
            if holder:holder.pop().__exit__(None,None,None)
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(waits),1)
        r=result['data']['configuration']
        self.assertEqual(r['board_shutdown_policy'],'refuse');self.assertFalse(r['autostart'])
        self.assertNotIn('ipv4',r);self.assertFalse((self.data/('instances/'+management.internal_name(spec['vm_id'])+'/root.raw')).exists())
        self.assertEqual(self.call('create',spec,key='zephyr')['data']['current']['vm_id'],r['uuid'])


    def test_purge_io_failure_records_remaining_assets_and_can_resume(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        original=os.unlink
        called=[]
        def broken(path,*args,**kwargs):
            if kwargs.get('dir_fd') is not None and not called:
                called.append(path);raise OSError('injected I/O failure')
            return original(path,*args,**kwargs)
        with patch.object(management.os,'unlink',side_effect=broken):
            result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertEqual(result['error']['code'],'CLEANUP_INCOMPLETE',result)
        op=json.loads((self.data/'operations'/(result['operation_id']+'.json')).read_text())
        self.assertTrue(op['remaining_assets'])
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertTrue(result['ok'],result)

    def test_old_purge_cannot_resume_after_a_newer_lifecycle_generation(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=='recorded' else None
        with self.assertRaises(Interrupted):self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.m.checkpoint=lambda *args:None
        tombpath=self.data/'tombstones'/(identity+'.json')
        tomb=json.loads(tombpath.read_text());tomb['generation']+=2;tomb['lifecycle']='REGISTERED'
        archive=self.data/tomb['archive'];archive.rename(self.data/f'instances/{self.key}')
        tomb['archive']=None;tombpath.write_text(json.dumps(tomb))
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertEqual(result['error']['code'],'TARGET_CHANGED',result)
        self.assertEqual((self.data/f'instances/{self.key}/saved-user-file').read_text(),'keep me')

    def test_global_lock_concurrent_invocation_does_not_generate_another_identity(self):
        from xenova_runtime import operation_lock
        with operation_lock(self.run):
            result=self.call('create',self.spec)
        self.assertEqual(result['error']['code'],'OPERATION_IN_PROGRESS',result)
        self.assertFalse((self.data/'operations').exists())
        self.create()

    def test_factory_reservation_survives_purge_and_restore_clears_retired_state(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        self.assertTrue(vm.retired_instance(self.data,self.key,identity))
        self.call('restore',{'vm_id':identity})
        self.assertFalse(vm.retired_instance(self.data,self.key,identity))
        self.call('retire',{'vm_id':identity},key='retire2')
        self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertTrue(vm.retired_instance(self.data,self.key,identity))

    def test_subprocess_failure_is_one_error_envelope_and_does_not_remove_published_assets(self):
        import subprocess
        def failed(op):
            self.prepare(op)
            raise subprocess.CalledProcessError(1,['private-tool'])
        self.m.preparer=failed
        result=self.call('create',self.spec)
        self.assertFalse(result['ok'],result)
        self.assertEqual(result['error']['code'],'RECOVERY_REQUIRED')
        self.assertTrue((self.data/f'instances/{self.key}/saved-user-file').is_file())
        self.m.preparer=self.prepare
        self.assertTrue(self.call('create',self.spec)['ok'])
        self.assertEqual(self.copies,1)

    def test_injected_archive_symlink_never_removes_outside_files(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        tomb=json.loads((self.data/'tombstones'/(identity+'.json')).read_text())
        archive=self.data/tomb['archive']
        actual=self.data/'retained-assets';archive.rename(actual)
        outside=self.data/'outside';outside.mkdir();(outside/'keep').write_text('outside')
        archive.symlink_to(outside,target_is_directory=True)
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertFalse(result['ok'],result)
        self.assertEqual((outside/'keep').read_text(),'outside')
        self.assertTrue((actual/'saved-user-file').is_file())

    def test_restore_writable_disk_does_not_compare_template_hash_or_recreate_missing_disk(self):
        identity=self.create();disk=self.data/f'instances/{self.key}/root.raw'
        with disk.open('ab') as stream:stream.write(b'user writes')
        retired=self.call('retire',{'vm_id':identity});self.assertTrue(retired['ok'],retired)
        result=self.call('restore',{'vm_id':identity});self.assertTrue(result['ok'],result)
        self.assertTrue(disk.read_bytes().endswith(b'user writes'))
        self.call('retire',{'vm_id':identity},key='retire2')
        tomb=json.loads((self.data/'tombstones'/(identity+'.json')).read_text())
        (self.data/tomb['archive']/'root.raw').unlink()
        result=self.call('restore',{'vm_id':identity},key='restore2')
        self.assertFalse(result['ok'],result);self.assertEqual(self.copies,1)


    def test_same_version_different_manifest_is_rejected_without_overwriting_catalog(self):
        from test_xenova_platform import load
        tool=load('xenova_catalog_publish_test',ROOT/'platform/scripts/publish-xenova-template-catalog.py')
        original=(self.data/'catalog/ubuntu-24.04-arm64/1.0.0.json').read_bytes()
        result=tool.register(self.data,self.descriptor,execute=True,runtime=self.run)
        self.assertTrue(result['replayed'])
        alternate=self.data/'templates/alternate'
        shutil.copytree(self.data/'templates/source',alternate)
        meta=json.loads((alternate/'template.json').read_text())
        meta['provenance_note']='different content'
        (alternate/'template.json').chmod(0o644);(alternate/'template.json').write_text(json.dumps(meta));(alternate/'template.json').chmod(0o444)
        descriptor={**self.descriptor,'asset_directory':'templates/alternate','manifest_sha256':catalog.sha(alternate/'template.json')}
        with self.assertRaisesRegex(ValueError,'same template ID/version'):
            tool.register(self.data,descriptor,execute=True,runtime=self.run)
        self.assertEqual((self.data/'catalog/ubuntu-24.04-arm64/1.0.0.json').read_bytes(),original)

    def test_operation_lookup_does_not_claim_a_stale_running_operation_is_active(self):
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=='recorded' else None
        with self.assertRaises(Interrupted):self.call('create',self.spec)
        self.m.checkpoint=lambda *args:None
        result=self.call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})
        self.assertTrue(result['ok']);self.assertTrue(result['data']['recovery_required'])
        self.assertEqual(result['data']['effective_status'],'RECOVERY_REQUIRED')
        self.assertEqual(self.copies,0)

    def test_purge_rejects_xen_disk_reference_and_unverifiable_loop_inventory(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        tomb=json.loads((self.data/'tombstones'/(identity+'.json')).read_text())
        archive=self.data/tomb['archive']
        self.m.usage_check=self.m._usage
        other=record('debian-dev')
        self.xen.rows=[domain(other,archive/'root.raw')]
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertEqual(result['error']['code'],'ASSET_IN_USE',result)
        self.xen.rows=[]
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertFalse(result['ok'],result)  # FakeXen's empty stdout is not valid loop JSON.
        self.assertTrue((archive/'root.raw').is_file())

    def test_manager_itself_has_no_stdout_and_cli_execute_is_exactly_one_json(self):
        from xenova_preparation import diagnostic, run
        original=self.m.preparer
        def noisy(op):
            diagnostic('lower Python tool log')
            run(['python3','-c','print("lower process log")'],check=True)
            return original(op)
        self.m.preparer=noisy
        out=io.StringIO()
        with redirect_stdout(out):
            result=cli.main(['create','custom','--vm-id',self.spec['vm_id'],'--template-id','ubuntu-24.04-arm64','--template-version','1.0.0',
                         '--vcpus','1','--memory-mib','512','--ipv4','192.168.77.30','--idempotency-key','create:'+self.spec['vm_id'],
                         '--execute','--json'],manager=self.m)
        self.assertEqual(json.loads(out.getvalue()),result)
        self.assertTrue(result['ok'],result)
        self.assertNotIn('lower',out.getvalue())
        self.assertIn(b'lower Python tool log',self.m.last_output['bytes'])
        self.assertIn(b'lower process log',self.m.last_output['bytes'])


    def test_uuid_collision_with_existing_vm_is_rejected_before_journal(self):
        old=record();directory=fixture(self.data,old)
        result=self.call('create',{**self.spec,'vm_id':old['uuid']})
        self.assertEqual(result['error']['code'],'IDEMPOTENCY_CONFLICT',result)
        self.assertFalse((self.data/'operations').exists())
        self.assertTrue((directory/'instance.json').is_file())


    def test_string_false_cannot_authorize_execution_or_forced_shutdown(self):
        result=self.m.call('create',self.spec,execute='false',idempotency_key='invalid')
        self.assertEqual(result['error']['code'],'INVALID_SPEC')
        self.assertFalse((self.data/'operations').exists())
        identity=self.create()
        result=self.m.call('stop',{'vm_id':identity,'force':'false'})
        self.assertEqual(result['error']['code'],'INVALID_SPEC')
        self.assertFalse(any(a[:2] in (['xl','destroy'],['xl','shutdown']) for a in self.xen.calls))


    def test_restore_requires_readonly_ext4_check_after_usage_preflight(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        order=[]
        self.m.usage_check=lambda *args:order.append('usage')
        original=self.p.command
        def command(args,**kwargs):
            if args[0]=='e2fsck':
                self.assertEqual(args[1],'-fn')
                self.assertIn('usage',order)
                raise vm.PlatformError('read-only filesystem check failed')
            return original(args,**kwargs)
        self.p.command=command
        result=self.call('restore',{'vm_id':identity})
        self.assertFalse(result['ok'],result)
        self.assertFalse((self.data/f'instances/{self.key}').exists())
        self.assertEqual(self.copies,1)

    def test_purge_can_remove_corrupted_owned_boot_asset_without_repair_or_template_copy(self):
        identity=self.create();self.call('retire',{'vm_id':identity})
        tomb=json.loads((self.data/'tombstones'/(identity+'.json')).read_text())
        archive=self.data/tomb['archive']
        (archive/'Image').unlink()
        result=self.call('purge',{'vm_id':identity,'confirm_vm_id':identity})
        self.assertTrue(result['ok'],result)
        self.assertFalse(archive.exists())
        self.assertEqual(self.copies,1)


    def test_malformed_catalog_or_operation_json_still_returns_one_error_envelope(self):
        path=self.data/'catalog/ubuntu-24.04-arm64/1.0.0.json'
        original=path.read_bytes();path.write_text('[]')
        result=self.call('templates',{})
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'ARTIFACT_INVALID')
        path.write_bytes(original)
        identity=self.create()
        op=next((self.data/'operations').glob('*.json'));op.write_text('[]')
        result=self.call('operation',{'operation_id':op.stem})
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'ARTIFACT_INVALID')
