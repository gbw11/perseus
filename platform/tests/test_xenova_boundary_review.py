"""독립 재검토: 제출 뒤 timeout을 미실행 실패로 확정하지 않는지 검증한다."""
import unittest
from unittest.mock import patch

import test_xenova_cpu as cpu_fixture
import test_xenova_recovery_foundation as foundation
import xenova_vm as vm
from first_install_contract import uuid4
from xenova_execution import Execution
from xenova_trace import Trace
from test_xenova_management import Interrupted


class BoundaryReviewTest(unittest.TestCase):
    def setUp(self):
        self.t=cpu_fixture.CPUTest('runTest');self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.p,self.m,self.xen=self.t.p,self.t.m,self.t.xen
        self.record=self.t.linux
        self.t.activate()

    def operation(self,key):
        return next(o for o in self.p.execution().requests() if o['idempotency_key']==key)

    def test_create_effect_then_transport_timeout_keeps_uncertain_owner_and_deadline(self):
        original=self.p.command
        def timeout(argv,**kwargs):
            value=original(argv,**kwargs)
            if argv[:2]==['xl','create']:
                raise vm.PlatformError('xl command transport timed out after submission')
            return value
        with patch.object(self.p,'command',timeout):
            result=self.t.control(self.record,key='review-create-timeout')
        self.assertFalse(result['ok'])
        operation=self.operation('review-create-timeout')
        self.assertEqual(operation['phase'],'uncertain',operation)
        self.assertEqual(operation['error']['code'],'RECONCILE_REQUIRED')
        self.assertEqual(operation['deadline_monotonic_ms'],90000)
        self.assertTrue(self.xen.rows)
        before=len([a for a in self.xen.calls if a[:2]==['xl','create']])
        self.t.ticks=100
        replay=self.t.control(self.record,key='review-create-timeout')
        self.assertFalse(replay['ok'])
        self.assertEqual(before,len([a for a in self.xen.calls if a[:2]==['xl','create']]))
        self.assertEqual(self.operation('review-create-timeout')['deadline_monotonic_ms'],90000)

    def test_shutdown_effect_then_transport_timeout_can_only_reconcile_same_original(self):
        self.t.ok(self.t.control(self.record,key='review-start-for-stop'))
        original=self.p.command
        def timeout(argv,**kwargs):
            value=original(argv,**kwargs)
            if argv[:2]==['xl','shutdown']:
                raise vm.PlatformError('xl command transport timed out after submission')
            return value
        with patch.object(self.p,'command',timeout):
            result=self.t.control(self.record,'stop',key='review-stop-timeout')
        self.assertFalse(result['ok'])
        operation=self.operation('review-stop-timeout')
        self.assertEqual(operation['phase'],'uncertain',operation)
        self.assertEqual(operation['error']['code'],'RECONCILE_REQUIRED')
        self.assertFalse(self.xen.rows)
        before=len([a for a in self.xen.calls if a[:2]==['xl','shutdown']])
        self.t.ticks=100
        replay=self.t.control(self.record,'stop',key='review-stop-timeout')
        self.t.ok(replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(before,len([a for a in self.xen.calls if a[:2]==['xl','shutdown']]))
        self.assertEqual(self.operation('review-stop-timeout')['phase'],'completed')
        self.assertEqual(self.operation('review-stop-timeout')['deadline_monotonic_ms'],90000)

    def test_recovery_shutdown_timeout_retains_unresolved_original_attempt(self):
        self.t.ok(self.t.control(self.record,key='review-recovery-start'))
        current=self.t.ok(self.m.call('get',{'vm_id':self.record['uuid']}))['vm']
        spec={'vm_id':self.record['uuid'],'recovery_attempt_id':uuid4(),
              'expected_host_boot_id':self.t.host,'expected_lifecycle_generation':current['generation'],
              'expected_intent_revision':current['intent_revision'],
              'expected_execution':{'execution_id':current['execution_id']},
              'mode':'graceful-restart','deadline_monotonic_ms':90000}
        original=self.p.command
        def timeout(argv,**kwargs):
            value=original(argv,**kwargs)
            if argv[:2]==['xl','shutdown']:
                raise vm.PlatformError('xl command transport timed out after submission')
            return value
        with patch.object(self.p,'command',timeout),patch.object(vm.os,'sync'):
            result=self.m.call('recover_once',spec,execute=True)
        self.assertFalse(result['ok'])
        operation=self.t.ok(self.m.call('recovery-operation',{'recovery_attempt_id':spec['recovery_attempt_id']}))['attempt']
        self.assertEqual(operation['phase'],'uncertain',operation)
        before=len([a for a in self.xen.calls if a[:2] in (['xl','create'],['xl','shutdown'])])
        self.t.ticks=100
        with patch.object(vm.os,'sync'):
            replay=self.m.call('recover_once',spec,execute=True)
        self.assertFalse(replay['ok'])
        self.assertEqual(before,len([a for a in self.xen.calls if a[:2] in (['xl','create'],['xl','shutdown'])]))

    def test_interrupted_recovery_binding_must_revalidate_real_cpu_before_finalizing(self):
        self.t.ok(self.t.control(self.record,key='review-recovery-cpu-initial'))
        current=self.t.ok(self.m.call('get',{'vm_id':self.record['uuid']}))['vm']
        spec={'vm_id':self.record['uuid'],'recovery_attempt_id':uuid4(),
              'expected_host_boot_id':self.t.host,'expected_lifecycle_generation':current['generation'],
              'expected_intent_revision':current['intent_revision'],
              'expected_execution':{'execution_id':current['execution_id']},
              'mode':'graceful-restart','deadline_monotonic_ms':90000}
        def interrupt(phase,value):
            if phase=='launch-observed':
                raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with patch.object(vm.os,'sync'),self.assertRaises(Interrupted):
            self.m.call('recover_once',spec,execute=True)
        self.p.execution_checkpoint=lambda *args:None
        run=self.p.execution().run_record(self.record)
        self.assertNotEqual(run.get('cpu_verification'),'complete')
        self.t.affinities[run['domid']][0]['pcpus']=[0,1,2,3]
        commands={kind:len([a for a in self.xen.calls if a[:2]==['xl',kind]])
                  for kind in ('create','shutdown','vcpu-pin')}
        with patch.object(vm.os,'sync'):
            failed=self.m.call('recover_once',spec,execute=True)
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['error']['code'],'CPU_APPLY_INCOMPLETE')
        self.assertNotEqual(self.p.execution().run_record(self.record).get('cpu_verification'),'complete')
        attempt=self.t.ok(self.m.call('recovery-operation',{'recovery_attempt_id':spec['recovery_attempt_id']}))['attempt']
        self.assertIn(attempt['phase'],('launching','observed','uncertain'))
        self.assertEqual(commands,{kind:len([a for a in self.xen.calls if a[:2]==['xl',kind]]) for kind in commands})
        self.t.affinities[run['domid']][0]['pcpus']=[0,3]
        with patch.object(vm.os,'sync'):
            completed=self.m.call('recover_once',spec,execute=True)
        self.t.ok(completed)
        self.assertTrue(completed['replayed'])
        self.assertEqual(self.p.execution().run_record(self.record)['cpu_verification'],'complete')
        self.assertEqual(commands,{kind:len([a for a in self.xen.calls if a[:2]==['xl',kind]]) for kind in commands})

    def test_batch_publication_failure_never_returns_partial_views_or_clean_snapshot(self):
        original=Execution.write
        observed=[]
        def fail_second(execution,path,value):
            if path.parent.name=='observations':
                observed.append(path.stem)
                if len(observed)==2:
                    raise OSError('second observation durability unavailable')
            return original(execution,path,value)
        with patch.object(Execution,'write',fail_second):
            result=self.m.call('list')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'],'OBSERVATION_UNAVAILABLE')
        self.assertIsNone(result['data'])
        self.assertGreaterEqual(len(observed),2)
        snapshot=self.p.execution().snapshot(observed[0],1000)
        self.assertTrue(snapshot['unknown'])
        self.assertIsNone(snapshot['current'])

    def test_existing_start_checks_actual_cpu_again_after_admission(self):
        self.t.ok(self.t.control(self.record,key='review-noop-initial'))
        run=self.p.execution().run_record(self.record)
        before=len([a for a in self.xen.calls if a[:2]==['xl','create']])
        def drift(phase,value):
            if phase=='intent-executing' and value.get('idempotency_key')=='review-noop-drift':
                self.t.affinities[run['domid']][0]['pcpus']=[0,1,2,3]
        self.p.execution_checkpoint=drift
        result=self.t.control(self.record,key='review-noop-drift')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'],'CPU_APPLY_INCOMPLETE')
        self.assertEqual(before,len([a for a in self.xen.calls if a[:2]==['xl','create']]))
        self.assertEqual(self.t.affinities[run['domid']][0]['pcpus'],[0,1,2,3])

    def test_optional_trace_bind_failure_does_not_change_actual_start_completion(self):
        with patch.object(Trace,'bind',side_effect=OSError('optional trace identity unavailable')):
            result=self.t.control(self.record,key='review-optional-bind')
        self.t.ok(result)
        self.assertEqual(self.operation('review-optional-bind')['phase'],'completed')
        self.assertIsNotNone(result['data']['vm']['execution_id'])

    def test_existing_start_rechecks_running_state_after_admission(self):
        self.t.ok(self.t.control(self.record,key='review-noop-before-pause'))
        run=self.p.execution().run_record(self.record)
        before=len([a for a in self.xen.calls if a[:2]==['xl','create']])
        def pause(phase,value):
            if phase=='intent-executing' and value.get('idempotency_key')=='review-noop-paused':
                self.xen.states={run['domid']:'--p---'}
        self.p.execution_checkpoint=pause
        result=self.t.control(self.record,key='review-noop-paused')
        self.assertFalse(result['ok'])
        self.assertIn('paused',result['error']['message'])
        self.assertEqual(before,len([a for a in self.xen.calls if a[:2]==['xl','create']]))
        self.assertNotEqual(self.operation('review-noop-paused')['phase'],'completed')

    def test_legacy_status_uses_one_batch_and_original_total_deadline(self):
        records=self.p.records(validate_assets=False)
        original_domains=self.p.domains
        def costly_domains(**kwargs):
            self.t.ticks+=2
            return original_domains(**kwargs)
        observe_many=Execution.observe_many
        with patch.object(self.p,'domains',side_effect=costly_domains), \
             patch.object(Execution,'observe',side_effect=AssertionError('status must batch')), \
             patch.object(Execution,'observe_many',autospec=True,side_effect=observe_many) as batch, \
             patch.object(self.p,'time_status',wraps=self.p.time_status) as timing:
            result=self.p.status(records)
        self.assertEqual(len(result['instances']),len(records))
        self.assertEqual(batch.call_count,1)
        self.assertEqual(batch.call_args.args[2],15)
        self.assertEqual(timing.call_args.kwargs['deadline'],15)


class UnconfiguredBindReviewTest(unittest.TestCase):
    setUp=foundation.FoundationTest.setUp
    sleep=foundation.FoundationTest.sleep

    def test_binding_rechecks_incarnation_after_observed_checkpoint_without_cpu_policy(self):
        def replace_incarnation(phase,value):
            if phase=='launch-observed':
                row,token=self.xen.incarnations[value['domid']]
                self.xen.incarnations[value['domid']]=(row,token+100)
        self.p.execution_checkpoint=replace_incarnation
        with patch.object(vm.os,'sync'):
            result=self.m.call('start',{'vm_id':self.vm_id},execute=True,idempotency_key='review-unconfigured-incarnation')
        self.assertFalse(result['ok'],result)
        self.assertEqual(len([a for a in self.xen.calls if a[:2]==['xl','create']]),1)
        operation=next(o for o in self.p.execution().requests() if o['idempotency_key']=='review-unconfigured-incarnation')
        self.assertNotEqual(operation['phase'],'completed')

    def test_binding_cannot_complete_under_superseded_launch_intent(self):
        def change_intent(phase,value):
            if phase=='launch-observed':
                execution=self.p.execution();intent=execution.intent(self.r)
                execution.write(self.p.data/'control'/(self.vm_id+'.json'),
                    {**intent,'desired_state':'stopped','intent_revision':intent['intent_revision']+1,
                     'accepted_operation_id':uuid4()})
        self.p.execution_checkpoint=change_intent
        result=self.m.call('start',{'vm_id':self.vm_id},execute=True,idempotency_key='review-superseded-launch')
        self.assertFalse(result['ok'],result)
        operation=next(o for o in self.p.execution().requests() if o['idempotency_key']=='review-superseded-launch')
        self.assertNotEqual(operation['phase'],'completed')
        self.assertEqual(self.p.execution().intent(self.r)['desired_state'],'stopped')


if __name__=='__main__':
    unittest.main()
