"""Actual Execution observation and intent locks; Xen is the external fake."""
import unittest
from unittest.mock import patch
import test_xenova_recovery_foundation as foundation
from xenova_vm import PlatformError
from xenova_runtime import operation_lock
from xenova_drive_binding import observe

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
