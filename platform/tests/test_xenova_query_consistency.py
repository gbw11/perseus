"""공개 CLI→Manager→실제 metadata/관측 reader를 확인한다. Xen/작은 OS 자산만 대역이다."""
from contextlib import redirect_stdout
import io
from argparse import Namespace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from first_install_fixture import Board
import test_xenova_recovery_foundation as foundation
from test_xenova_platform import fixture, record
import xenova_vm as vm
import xenova_management as management
import xenova_management_cli as cli
import xenova_catalog as catalog
from test_xenova_zephyr import bundle
from test_xenova_lifecycle import application_bundle
from xenova_runtime import operation_lock
from xenova_execution import Execution


class QueryConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.f=foundation.FoundationTest('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.m,self.p,self.r=self.f.m,self.f.p,self.f.r

    def invoke(self,args,*,manager=None):
        out=io.StringIO();code=0
        with redirect_stdout(out):
            try:result=cli.main(args,manager=manager or self.m)
            except SystemExit as error:code=error.code;result=json.loads(out.getvalue())
        return code,result,out.getvalue()

    def files(self):
        return {str(p.relative_to(self.p.data)):p.read_bytes() for p in self.p.data.rglob('*') if p.is_file()}

    def test_intents_reads_no_execution_host_xen_assets_or_cache_and_preserves_unset(self):
        other=record('debian-dev');fixture(self.p.data,other)
        before=self.files();runtime=list(self.p.runtime.rglob('*'));calls=len(self.f.xen.calls)
        with (patch.object(self.p,'command',side_effect=AssertionError('no Xen')),
             patch.object(self.p,'execution',side_effect=AssertionError('no execution context')),
             patch.object(self.p,'boot_id_reader',side_effect=AssertionError('no execution host')) as host,
             patch.object(vm,'validate_record',side_effect=AssertionError('no boot hash'))):
            result=self.f.ok(self.m.call('intents'))['vms']
            single=self.f.ok(self.m.call('intents',{'vm_id':other['uuid']}))['vm']
            host.assert_not_called()
        self.assertEqual({v['vm_id'] for v in result},{self.f.vm_id,other['uuid']})
        self.assertEqual(single['desired_state'],'unknown');self.assertIsNone(single['intent_revision'])
        self.assertFalse(single['intent_applicable']);self.assertTrue(single['autostart'])
        self.assertNotIn('xen_state',single);self.assertNotIn('execution_id',single)
        self.assertEqual(before,self.files());self.assertEqual(runtime,list(self.p.runtime.rglob('*')))
        self.assertEqual(calls,len(self.f.xen.calls))

    def test_intents_keeps_past_generation_and_retired_filter_without_repair(self):
        self.f.start();self.f.stop()
        self.f.ok(self.m.call('retire',{'vm_id':self.f.vm_id},execute=True,idempotency_key='intent-retire'))
        before=self.files()
        row=self.f.ok(self.m.call('intents',{'vm_id':self.f.vm_id}))['vm']
        self.assertEqual(row['lifecycle'],'RETIRED');self.assertFalse(row['intent_applicable'])
        self.assertEqual(row['desired_state'],'stopped');self.assertGreater(row['generation'],row['intent_lifecycle_generation'])
        self.assertEqual(self.f.ok(self.m.call('intents'))['vms'],[])
        self.assertEqual(self.f.ok(self.m.call('intents',{'lifecycle':'all'}))['vms'],[row])
        self.assertEqual(before,self.files())

    def test_intents_corruption_is_not_empty_and_recovery_does_not_initialize(self):
        path=self.p.data/'control'/(self.f.vm_id+'.json');saved=path.read_bytes()
        path.write_text('{broken')
        self.f.fails(self.m.call('intents'),'RECONCILE_REQUIRED')
        self.assertEqual(path.read_text(),'{broken')
        path.write_bytes(saved)
        self.assertTrue(self.m.call('intents')['ok'])

    def test_zephyr_regular_create_uses_same_intent_query_without_xen(self):
        source=application_bundle(bundle(self.p.data/'query-legacy'),self.p.data/'rtos-templates/query-firmware')
        manifest=source/'firmware.json';meta=json.loads(manifest.read_text())
        meta.update(id='query-firmware',kind='zephyr-xen-firmware',version='1.0.0')
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        descriptor={'schema_version':'1.0','template_id':'query-zephyr','template_version':'1.0.0',
                    'guest_kind':'zephyr','asset_directory':'rtos-templates/query-firmware','source_template_id':'query-firmware',
                    'manifest_sha256':catalog.sha(manifest)}
        directory=self.p.data/'catalog/query-zephyr';directory.mkdir()
        (directory/'1.0.0.json').write_text(json.dumps(descriptor))
        manager=management.Manager(self.p,defaults_path=self.m.defaults_path)
        made=self.f.ok(manager.call('create',{'vm_id':foundation.HOST_B,'name':'zephyr-query',
            'template_id':'query-zephyr','template_version':'1.0.0'},execute=True))
        with patch.object(self.p,'command',side_effect=AssertionError('no Xen')):
            view=self.f.ok(manager.call('intents',{'vm_id':foundation.HOST_B}))['vm']
        self.assertEqual(view['guest_kind'],'zephyr');self.assertEqual(view['desired_state'],'stopped')
        self.assertEqual(view['intent_revision'],1);self.assertTrue(view['intent_applicable'])

    def test_intent_change_retries_once_and_continuous_change_refuses(self):
        original=management.ReadContext.validate_one;changes=[]
        def validate(context,identity):
            if not changes:
                changes.append(True)
                self.p.execution().accept(self.r,{'vm_id':identity,'action':'stop','force':False},'query-stop')
            return original(context,identity)
        with patch.object(management.ReadContext,'validate_one',validate):
            view=self.f.ok(self.m.call('intents',{'vm_id':self.f.vm_id}))['vm']
        self.assertEqual(view['intent_revision'],2);self.assertEqual(view['desired_state'],'stopped')
        def changing(context,identity):
            path=self.p.data/'control'/(identity+'.json');value=json.loads(path.read_text())
            value['intent_revision']+=1;path.write_text(json.dumps(value));return original(context,identity)
        with patch.object(management.ReadContext,'validate_one',changing):
            self.f.fails(self.m.call('intents'),'TARGET_CHANGED')
        self.assertTrue(self.m.call('intents')['ok'])

    def test_lifecycle_change_during_intent_round_returns_latest_set_not_old_registered_vm(self):
        original=management.ReadContext.validate_one;changed=[]
        def validate(context,identity):
            if not changed:
                changed.append(True)
                self.f.ok(self.m.fork().call('retire',{'vm_id':identity},execute=True,idempotency_key='race-retire'))
            return original(context,identity)
        with patch.object(management.ReadContext,'validate_one',validate):
            result=self.f.ok(self.m.call('intents'))
        self.assertEqual(result['vms'],[])
        self.assertEqual(self.f.ok(self.m.call('intents',{'lifecycle':'all'}))['vms'][0]['lifecycle'],'RETIRED')

    def test_same_internal_name_registered_with_new_uuid_does_not_inherit_old_intent(self):
        original=management.ReadContext.validate_one;changed=[]
        def validate(context,identity):
            if not changed:
                changed.append(True)
                self.f.ok(self.m.fork().call('retire',{'vm_id':identity},execute=True,idempotency_key='name-retire'))
                replacement={**record(),'id':self.r['id'],'name':self.r['name'],'uuid':foundation.HOST_B}
                fixture(self.p.data,replacement)
            return original(context,identity)
        with patch.object(management.ReadContext,'validate_one',validate):
            rows=self.f.ok(self.m.call('intents'))['vms']
        self.assertEqual([r['vm_id'] for r in rows],[foundation.HOST_B])
        self.assertEqual(rows[0]['desired_state'],'unknown');self.assertIsNone(rows[0]['intent_revision'])

    def test_query_family_and_actual_stop_progress_while_common_lock_is_held(self):
        self.f.start()
        with operation_lock(self.p.runtime):
            for action,spec in (('intents',{}),('get',{'vm_id':self.f.vm_id}),
                                ('probe',{'vm_id':self.f.vm_id,'timeout_ms':1000}),
                                ('execution-context',{'vm_id':self.f.vm_id}),('templates',{})):
                self.f.ok(self.m.call(action,spec))
            pending=self.m.call('stop',{'vm_id':self.f.vm_id},idempotency_key='original key 한글')
            self.assertTrue(pending['error']['details']['accepted'])
            code,result,_=self.invoke(['control-operation','--idempotency-key','original key 한글','--vm-id',self.f.vm_id])
            self.assertEqual(code,0);self.assertEqual(result['data']['operation']['operation_id'],pending['operation_id'])
        self.f.ok(self.m.call('stop',{'vm_id':self.f.vm_id},idempotency_key='original key 한글'))
        self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_probe_context_and_get_preserve_return_shapes_and_cache(self):
        self.f.start()
        a=self.f.ok(self.m.call('get',{'vm_id':self.f.vm_id}))['vm']
        b=self.f.ok(self.m.call('execution-context',{'vm_id':self.f.vm_id}))['vm']
        c=self.f.ok(self.m.call('probe',{'vm_id':self.f.vm_id,'timeout_ms':1000}))['observation']
        self.assertEqual(a,b);self.assertEqual(a['execution_id'],c['execution_id'])
        self.assertIn('configuration',b);self.assertNotIn('configuration',c)
        self.assertTrue((self.p.runtime/'observations'/(self.f.vm_id+'.json')).is_file())

    def test_json_pretty_and_short_use_same_manager_action_and_no_renderer_observation(self):
        self.f.start()
        samples=[]
        for options in ([],['--json'],['--short']):
            with patch.object(self.m,'call',wraps=self.m.call) as call:
                code,result,out=self.invoke(['status',*options])
            self.assertEqual(code,0);self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.args[0],'list');samples.append(result['data'])
            self.assertIn('NAME |',out) if options==['--short'] else self.assertEqual(json.loads(out),result)
        self.assertEqual(samples[0],samples[1]);self.assertEqual(samples[0],samples[2])

    def test_explicit_options_apply_or_refuse_before_effect_and_names_do_not_override(self):
        for action in ('get','list','intents','probe','execution-context'):
            args=[action,*([self.f.vm_id] if action not in ('list','intents') else []),'--timeout-ms=1000']
            with patch.object(self.m,'call',wraps=self.m.call) as call:
                self.assertEqual(self.invoke(args)[0],0)
            self.assertEqual(call.call_args.args[1]['timeout_ms'],1000)
        bad=(['get',self.f.vm_id,'--force'],['list','--vm-id',self.f.vm_id],['list','--restore'],
             ['stop',self.f.vm_id,'--execute'],['get',self.f.vm_id,'extra'],['get',self.f.vm_id,'--lifecycle','all'],
             ['get',self.f.vm_id,'--vm-id',foundation.HOST_B],['create','name','--autostart'],['get','--timeout-ms','0'])
        for args in bad:
            with self.subTest(args=args),patch.object(self.m,'call',wraps=self.m.call) as call:
                code,result,_=self.invoke(args);self.assertEqual(code,2,result);call.assert_not_called()
        self.assertEqual(self.invoke(['get',self.r['id'],'--vm-id',self.f.vm_id])[0],0)
        self.assertEqual(self.invoke(['get','--vm-id',self.f.vm_id,'--vm-id',foundation.HOST_B])[0],2)
        self.assertEqual(self.invoke(['get','--vm-id',self.f.vm_id,'--vm-id',self.f.vm_id])[0],0)

    def test_status_diagnostics_remain_explicit_and_format_does_not_choose_handler(self):
        for options in ([],['--json']):
            with patch.object(self.p,'status',wraps=self.p.status) as status:
                code,result,_=self.invoke(['diagnose','--details',*options])
            self.assertEqual(code,0);self.assertIn('instances',result);self.assertIn('time',result)
            self.assertEqual(status.call_count,1)
        self.assertEqual(self.invoke(['console',self.r['id'],'--json'])[0],2)

    def test_argv_separator_equals_and_literal_json_key_keep_the_original_meaning(self):
        for args in (['get','--json','--',self.f.vm_id],['get',self.f.vm_id,'--timeout-ms=1000']):
            self.assertEqual(self.invoke(args)[0],0)
        self.f.start('--json')
        code,result,_=self.invoke(['control-operation','--idempotency-key=--json'])
        self.assertEqual(code,0)
        self.assertEqual(result['data']['operation']['idempotency_key'],'--json')
        self.assertEqual(self.invoke(['status','--lifecycle=all'])[0],0)

    def test_slow_renderer_holds_no_control_lock_and_other_stop_really_finishes(self):
        self.f.start()
        entered,release=threading.Event(),threading.Event()
        class Slow(io.StringIO):
            def write(self,value):
                entered.set()
                if not release.wait(5):raise AssertionError('renderer not released')
                return super().write(value)
        out=Slow();results=[]
        def query():results.append(cli.main(['get',self.f.vm_id],manager=self.m.fork()))
        with redirect_stdout(out):
            thread=threading.Thread(target=query);thread.start()
            try:
                self.assertTrue(entered.wait(3))
                stopped=self.f.ok(self.m.call('stop',{'vm_id':self.f.vm_id},idempotency_key='slow-render-stop'))
                self.assertEqual(len(self.f.physical('shutdown')),1)
                self.assertTrue(thread.is_alive())
            finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertTrue(results[0]['ok'])
        self.assertEqual(json.loads(out.getvalue()),results[0])

    def test_name_lookup_and_parser_time_are_charged_to_call_budget(self):
        original=vm.load_records
        def delayed(*args,**kwargs):self.f.ticks+=.2;return original(*args,**kwargs)
        with patch.object(vm,'load_records',delayed):
            code,result,_=self.invoke(['get',self.r['id'],'--budget-ms=100'])
        self.assertEqual(code,4);self.assertEqual(result['error']['code'],'DEADLINE_EXCEEDED')
        self.assertEqual(self.invoke(['get',self.f.vm_id,'--budget-ms=1000'])[0],0)

    def test_original_key_cli_works_retired_without_live_get_or_deadline_renewal(self):
        self.f.start('key original');self.f.stop()
        op=self.f.ok(self.m.call('control-operation',{'idempotency_key':'key original'}))['operation']
        self.f.ok(self.m.call('retire',{'vm_id':self.f.vm_id},execute=True,idempotency_key='retire-key'))
        with patch.object(self.p,'command',side_effect=AssertionError('no live observation')):
            for args in (['--idempotency-key','key original'],['--operation-id',op['operation_id']]):
                code,row,_=self.invoke(['control-operation',*args,'--vm-id',self.f.vm_id,'--json'])
                self.assertEqual(code,0);self.assertEqual(row['data']['operation'],op)
        for args in ([],['--operation-id',op['operation_id'],'--idempotency-key','key original']):
            self.assertEqual(self.invoke(['control-operation',*args])[0],2)

    def test_valid_original_control_and_recovery_records_survive_current_host_read_failure(self):
        self.f.start('historical-key')
        spec=self.f.spec();self.f.ok(self.f.recover(spec))
        before=self.files()
        for unavailable in (OSError('fixture boot identity unreadable'),None):
            option={'side_effect':unavailable} if isinstance(unavailable,Exception) else {'return_value':'invalid'}
            with patch.object(self.p,'boot_id_reader',**option):
                control=self.f.ok(self.m.call('control-operation',{'idempotency_key':'historical-key'}))
                recovered=self.f.ok(self.m.call('recovery-operation',{'recovery_attempt_id':spec['recovery_attempt_id']}))
            self.assertIsNone(control['deadline_observation']['original_host_matches'])
            self.assertIsNone(control['deadline_observation']['execution_deadline_expired'])
            self.assertEqual(control['diagnostic_warnings'][0]['cause_type'],
                             'OSError' if isinstance(unavailable,Exception) else 'InvalidHostIdentity')
            self.assertEqual(recovered['attempt']['request'],spec)
        self.assertEqual(before,self.files())

    def test_snapshot_uuid_has_no_registry_or_fresh_observation_and_keeps_clock(self):
        self.f.start();self.f.ticks+=.1
        before=(self.p.runtime/'observations'/(self.f.vm_id+'.json')).read_bytes()
        with (patch.object(vm,'load_records',side_effect=AssertionError('no registry')),
              patch.object(self.p,'command',side_effect=AssertionError('no Xen'))):
            code,result,_=self.invoke(['snapshot',self.f.vm_id,'--max-age-ms=1000'])
        self.assertEqual(code,0);self.assertEqual(before,(self.p.runtime/'observations'/(self.f.vm_id+'.json')).read_bytes())
        self.assertEqual(result['data']['snapshot']['age_ms'],100)

    def test_bad_json_and_io_are_distinct_and_help_needs_no_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'spec.json'
            for raw in (b'{bad',b'{"a":1,"a":2}',b'{"a":NaN}',b'\xff'):
                path.write_bytes(raw)
                code,result,_=self.invoke(['recover-once','--spec-file',str(path)])
                self.assertEqual(result['error']['code'],'INVALID_SPEC')
            path.unlink()
            self.assertEqual(self.invoke(['recover-once','--spec-file',str(path)])[1]['error']['code'],'STORAGE_UNAVAILABLE')
        with patch.object(cli,'Manager',side_effect=AssertionError('no constructor')),redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:vm.main(['--help'])
        self.assertEqual(caught.exception.code,0)

    def test_legacy_python_create_omission_uses_same_defaults_but_explicit_null_is_invalid(self):
        args=Namespace(id='next-vm',vm_id=foundation.HOST_B,template_id='ubuntu-24.04-arm64',
                       template_version='1.0.0',execute=False)
        planned=self.f.ok(self.p.create(args))
        self.assertEqual((planned['spec']['vcpus'],planned['spec']['memory_mib']),(1,512))
        args.vcpus=None
        self.f.fails(self.p.create(args),'INVALID_SPEC')

    def test_broken_output_does_not_repeat_or_cancel_completed_stop(self):
        self.f.start()
        class Broken(io.StringIO):
            def write(self,value):raise BrokenPipeError('test reader closed')
        with redirect_stdout(Broken()),self.assertRaises(SystemExit):
            cli.main(['stop',self.f.vm_id,'--idempotency-key','pipe-stop'],manager=self.m)
        op=self.f.ok(self.m.call('control-operation',{'idempotency_key':'pipe-stop'}))['operation']
        self.assertEqual(op['phase'],'completed');self.assertEqual(len(self.f.physical('shutdown')),1)
        self.assertEqual(self.invoke(['stop',self.f.vm_id,'--idempotency-key','pipe-stop'])[0],0)
        self.assertEqual(len(self.f.physical('shutdown')),1)


class FactoryQueryTest(unittest.TestCase):
    def test_actual_firstboot_registration_without_create_journal_is_in_intents(self):
        with tempfile.TemporaryDirectory() as temp:
            board=Board(Path(temp));board.grow_mount();board.factory();board.p.register()
            p=vm.Platform(board.data,board.p.run,command=lambda *a,**k:(_ for _ in ()).throw(AssertionError('no Xen')))
            with patch.object(vm,'storage_check'):
                result=management.Manager(p).call('intents')
            self.assertTrue(result['ok'],result)
            self.assertEqual({r['name'] for r in result['data']['vms']},{'ubuntu-dev','debian-dev'})
            self.assertTrue(all(r['intent_source']=='factory' and r['desired_state']=='running' for r in result['data']['vms']))
            self.assertFalse((board.data/'operations').exists())


if __name__=='__main__':unittest.main()
