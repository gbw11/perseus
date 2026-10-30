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
        with operation_lock(f.p.runtime):row=observe(f.p,f.vm_id)
        self.assertEqual(row['execution_id'],view['execution_id'])
        self.assertEqual(row['domid'],view['domid'])
        f.stop();self.assertEqual(observe(f.p,f.vm_id)['status'],'stopped')
    def test_intent_change_during_observation_refuses_old_binding(self):
        f=self.f;f.start();original=f.p.execution().observe
        def changed(record,deadline):
            result=original(record,deadline)
            intent=f.p.execution().intent(record);intent['intent_revision']+=1
            f.p.execution().write(f.p.data/'control'/(f.vm_id+'.json'),intent)
            return result
        with patch('xenova_execution.Execution.observe',side_effect=changed):
            with self.assertRaisesRegex(PlatformError,'binding changed'):observe(f.p,f.vm_id)
