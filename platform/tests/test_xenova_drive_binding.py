"""Actual Execution observation and intent locks; Xen is the external fake."""
import unittest
import json
import subprocess
import sys
import textwrap
from unittest.mock import patch
import test_xenova_recovery_foundation as foundation
from xenova_vm import PlatformError
from xenova_runtime import operation_lock
from xenova_drive_binding import observe
from xenova_execution import Execution,ExecutionError,ReadChanged,short_lock
from xenova_management import Manager,ReadContext

class DriveBindingTest(unittest.TestCase):
    def setUp(self):
        self.f=foundation.FoundationTest('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_observes_actual_execution_despite_unrelated_operation_lock(self):
        f=self.f;view=f.start()
        with operation_lock(f.p.runtime), patch.object(f.p, "domains", side_effect=AssertionError("full config read")):
            row=observe(f.p,f.vm_id)
        self.assertEqual(row['execution_id'],view['execution_id'])
        self.assertEqual(row['domid'],view['domid'])
        f.stop();self.assertEqual(observe(f.p,f.vm_id)['status'],'stopped')
    def test_reuses_publication_inputs_but_keeps_two_full_registry_and_lifecycle_rounds(self):
        f=self.f;f.start();read_tombs=Manager._tombs
        with (patch.object(f.p,'records',wraps=f.p.records) as records,
              patch.object(Manager,'_tombs',autospec=True,side_effect=read_tombs) as tombs,
              patch.object(Execution,'lifecycle',side_effect=AssertionError('third full lifecycle scan'))):
            row=observe(f.p,f.vm_id)
        self.assertEqual(row['status'],'observed')
        self.assertEqual(records.call_count,2)
        self.assertEqual(tombs.call_count,2)
        self.assertTrue(all(call.kwargs['deadline']==2 for call in tombs.call_args_list))

    def test_admission_reuses_only_the_files_from_each_current_validation(self):
        f=self.f;f.start()
        with (patch.object(ReadContext,'optional',autospec=True,side_effect=ReadContext.optional) as optional,
              patch.object(Execution,'intent',autospec=True,side_effect=Execution.intent) as intent,
              patch.object(Execution,'run_record',autospec=True,side_effect=Execution.run_record) as run,
              patch.object(Execution,'admission',autospec=True,side_effect=Execution.admission) as admission,
              patch.object(Execution,'gate',autospec=True,side_effect=Execution.gate) as gate):
            self.assertEqual(observe(f.p,f.vm_id)['status'],'observed')
        paths=[call.args[1] for call in optional.call_args_list]
        # Initial admission, observation publication and final admission each
        # validate fresh files; neither admission section reads them again.
        self.assertEqual(paths.count(f.directory/'instance.json'),3)
        self.assertEqual(paths.count(f.p.data/'tombstones'/(f.vm_id+'.json')),3)
        self.assertEqual(paths.count(f.p.data/'operations'/(f.r['creation_operation_id']+'.json')),3)
        self.assertEqual(intent.call_count,5)
        self.assertEqual(run.call_count,5)
        self.assertEqual(admission.call_count,4)
        self.assertEqual(gate.call_count,1)

    def test_admission_reuse_keeps_strict_record_and_run_json_types(self):
        f=self.f;f.start();validate=ReadContext.validate_one
        paths=((f.directory/'instance.json','vcpus'),
               (f.p.runtime/'executions'/(f.vm_id+'.json'),'domid'))
        for path,field in paths:
            with self.subTest(field=field):
                saved=path.read_bytes();changed=[]
                def change(context,identity):
                    if not changed:
                        changed.append(True)
                        value=json.loads(saved);value[field]=float(value[field])
                        path.write_text(json.dumps(value))
                    return validate(context,identity)
                try:
                    with patch.object(ReadContext,'validate_one',change):
                        with self.assertRaisesRegex(PlatformError,'binding changed'):
                            observe(f.p,f.vm_id)
                finally:path.write_bytes(saved)

    def test_admission_reuse_still_rejects_current_boot_admission_change(self):
        f=self.f;f.start();original=Execution.observe
        def change(execution,record,deadline,**kwargs):
            result=original(execution,record,deadline,**kwargs)
            path=f.p.runtime/'boot-admission'/(f.vm_id+'.json')
            value=json.loads(path.read_text());value['allowed']=not value['allowed']
            path.write_text(json.dumps(value))
            return result
        with patch.object(Execution,'observe',change):
            with self.assertRaises(ReadChanged):observe(f.p,f.vm_id)

    def test_registry_deadline_and_previous_platform_budget_are_preserved(self):
        f=self.f;f.start();f.p.response_deadline=.25
        original=f.p.records;commands=list(f.xen.calls)
        def slow(**kwargs):
            self.assertEqual(f.p.response_deadline,.25)
            result=original(**kwargs);f.ticks=.3;return result
        with patch.object(f.p,'records',side_effect=slow):
            with self.assertRaises(PlatformError) as raised:observe(f.p,f.vm_id)
        self.assertEqual(raised.exception.error['code'],'DEADLINE_EXCEEDED')
        self.assertEqual(f.p.response_deadline,.25)
        self.assertEqual(f.xen.calls,commands)

    def test_full_registry_and_lifecycle_scans_do_not_hold_admission(self):
        f=self.f;f.start();original_records=f.p.records;original_tombs=Manager._tombs
        def records(**kwargs):
            with short_lock(f.p.runtime,'admission'):pass
            return original_records(**kwargs)
        def tombs(manager,**kwargs):
            with short_lock(f.p.runtime,'admission'):pass
            return original_tombs(manager,**kwargs)
        with patch.object(f.p,'records',side_effect=records),patch.object(Manager,'_tombs',tombs):
            self.assertEqual(observe(f.p,f.vm_id)['status'],'observed')

    def test_late_metadata_does_not_refresh_the_verified_xen_timestamp(self):
        f=self.f;f.start();original=f.p.records;reads=[]
        def slow(**kwargs):
            value=original(**kwargs);reads.append(True)
            if len(reads)==2:f.ticks+=.3
            return value
        with patch.object(f.p,'records',side_effect=slow):row=observe(f.p,f.vm_id)
        stored=json.loads((f.p.runtime/'observations'/(f.vm_id+'.json')).read_text())
        self.assertEqual(row['observed_monotonic_ms'],stored['last_success']['monotonic_ms'])
        self.assertLess(row['observed_monotonic_ms'],int(f.ticks*1000))

    def test_newer_probe_error_cannot_be_cleared_by_an_older_binding_observation(self):
        f=self.f;f.start();original=Execution.publish_observation
        def newer(execution,record,row,unique,deadline,**kwargs):
            f.ticks+=.1;execution.probe_error(f.vm_id,'OBSERVATION_UNAVAILABLE')
            return original(execution,record,row,unique,deadline,**kwargs)
        with patch.object(Execution,'publish_observation',newer):
            with self.assertRaises(ReadChanged):observe(f.p,f.vm_id)
        stored=json.loads((f.p.runtime/'observations'/(f.vm_id+'.json')).read_text())
        self.assertEqual(stored['last_error']['code'],'OBSERVATION_UNAVAILABLE')

    def test_host_change_before_publication_refuses_binding_and_restores_deadline(self):
        f=self.f;f.start();f.p.response_deadline=42;previous=f.p.response_deadline;original=f.p.removal_snapshot
        def changed(deadline):
            result=original(deadline);f.host=foundation.HOST_B;return result
        with patch.object(f.p,'removal_snapshot',side_effect=changed):
            with self.assertRaises(ExecutionError):observe(f.p,f.vm_id)
        self.assertEqual(f.p.response_deadline,previous)

    def test_binding_rechecks_an_unscheduled_sample_without_accepting_it(self):
        f = self.f
        view = f.start()
        original = f.p.execution().observe

        def transient(record, deadline, **kwargs):
            result = original(record, deadline, **kwargs)
            if observed.call_count == 1:
                result.update(xen_state='unknown', xen_state_flags='------')
            return result

        with patch('xenova_execution.Execution.observe', side_effect=transient) as observed:
            row = observe(f.p, f.vm_id)
        self.assertEqual(observed.call_count, 2)
        self.assertEqual(row['execution_id'], view['execution_id'])

    def test_provisioning_waits_for_live_flags_of_the_same_execution(self):
        f=self.f;view=f.start();original=f.p.execution().observe
        def transient(record,deadline,**kwargs):
            result=original(record,deadline,**kwargs)
            if observed.call_count==1:
                result.update(xen_state='unknown',xen_state_flags='------')
            return result
        with patch('xenova_execution.Execution.observe',side_effect=transient) as observed:
            row=observe(f.p,f.vm_id,wait_for_live=True)
        self.assertEqual(observed.call_count,2)
        self.assertEqual(row['execution_id'],view['execution_id'])

    def test_provisioning_reuses_live_sample_only_for_that_incarnation(self):
        f=self.f;f.start();previous=observe(f.p,f.vm_id);original=f.p.execution().observe

        def unscheduled(record,deadline,**kwargs):
            return {**original(record,deadline,**kwargs),'xen_state':'unknown','xen_state_flags':'------'}

        with patch('xenova_execution.Execution.observe',side_effect=unscheduled) as observed:
            row=observe(f.p,f.vm_id,wait_for_live=True,previous_live=previous)
            self.assertEqual(row['execution_id'],previous['execution_id'])
            self.assertEqual(observed.call_count,1)
            with self.assertRaisesRegex(PlatformError,'managed live execution required'):
                observe(f.p,f.vm_id,timeout_s=0.05,wait_for_live=True,
                        previous_live={**previous,'xen_unique_id':'different'})
            with self.assertRaisesRegex(PlatformError,'managed live execution required'):
                observe(f.p,f.vm_id)

    def test_provisioning_does_not_wait_for_a_paused_execution(self):
        f=self.f;f.start();original=f.p.execution().observe
        def paused(record,deadline,**kwargs):
            return {**original(record,deadline,**kwargs),'xen_state':'paused','xen_state_flags':'--p---'}
        with patch('xenova_execution.Execution.observe',side_effect=paused) as observed:
            with self.assertRaisesRegex(PlatformError,'managed live execution required'):
                observe(f.p,f.vm_id,wait_for_live=True)
        self.assertEqual(observed.call_count,1)

    def test_intent_change_during_observation_refuses_old_binding(self):
        f=self.f;f.start();original=f.p.execution().observe
        def changed(record,deadline,**kwargs):
            result=original(record,deadline,**kwargs)
            intent=f.p.execution().intent(record);intent['intent_revision']+=1
            f.p.execution().write(f.p.data/'control'/(f.vm_id+'.json'),intent)
            return result
        with patch('xenova_execution.Execution.observe',side_effect=changed):
            with self.assertRaisesRegex(PlatformError,'binding changed'):observe(f.p,f.vm_id)


class BindingColdImportTest(unittest.TestCase):
    def test_read_layer_loads_before_the_call_budget_in_each_cold_import_order(self):
        code=textwrap.dedent('''
            import builtins
            import json
            import sys
            sys.path[:0]=json.loads(sys.argv[1])
            assert 'xenova_management' not in sys.modules
            original_import=builtins.__import__
            cold_cost=[0.0]
            def importing(name,*args,**kwargs):
                if name=='xenova_management' and name not in sys.modules:
                    cold_cost[0]+=2.4
                return original_import(name,*args,**kwargs)
            builtins.__import__=importing
            for name in sys.argv[2].split(','):__import__(name)
            import xenova_drive_binding as binding
            # No wall-clock sleep: charge only a virtual cold import cost.
            assert cold_cost[0]==2.4
            assert binding.Manager is sys.modules['xenova_management'].Manager
            assert binding.ReadContext is sys.modules['xenova_management'].ReadContext
            builtins.__import__=original_import

            import test_xenova_recovery_foundation as foundation
            from xenova_vm import PlatformError
            f=foundation.FoundationTest('runTest')
            f.setUp()
            try:
                expected=f.start()
                f.ticks=cold_cost[0]
                caller_deadline=f.ticks+.1
                f.p.response_deadline=caller_deadline
                row=binding.observe(f.p,f.vm_id,timeout_s=1)
                assert row['status']=='observed'
                assert row['execution_id']==expected['execution_id']
                assert f.p.response_deadline==caller_deadline

                records=f.p.records
                def delayed(**kwargs):
                    value=records(**kwargs)
                    f.ticks+=.2
                    return value
                f.p.records=delayed
                commands=list(f.xen.calls)
                try:binding.observe(f.p,f.vm_id,timeout_s=1)
                except PlatformError as error:
                    assert error.error['code']=='DEADLINE_EXCEEDED'
                else:raise AssertionError('expired caller budget was renewed')
                assert f.p.response_deadline==caller_deadline
                assert f.xen.calls==commands
            finally:f.doCleanups()
        ''')
        orders=('xenova_drive_binding',
                'xenova_management,xenova_drive_binding',
                'xenova_vm,xenova_drive_binding')
        for order in orders:
            with self.subTest(order=order):
                result=subprocess.run([sys.executable,'-B','-c',code,json.dumps(sys.path),order],
                                      capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
