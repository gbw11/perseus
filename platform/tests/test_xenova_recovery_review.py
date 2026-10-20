"""복구 검토 R1–R5. production 판정·파일을 쓰고 Xen/I/O/시계/잠금만 대역으로 둔다."""
import copy
import errno
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
import xenova_execution as execution
import xenova_vm as vm
from xenova_runtime import operation_lock
from first_install_contract import uuid4


class ReviewBoundaryTest(unittest.TestCase):
    setUp = foundation.FoundationTest.setUp
    sleep = foundation.FoundationTest.sleep
    call = foundation.FoundationTest.call
    ok = foundation.FoundationTest.ok
    fails = foundation.FoundationTest.fails
    start = foundation.FoundationTest.start
    stop = foundation.FoundationTest.stop
    state = foundation.FoundationTest.state
    spec = foundation.FoundationTest.spec
    recover = foundation.FoundationTest.recover
    physical = foundation.FoundationTest.physical
    checkpoint = foundation.FoundationTest.checkpoint

    def _stop_commit_cut(self, phase):
        self.ok(self.call("autostart-set", {"vm_id":self.vm_id,"value":"on"}))
        self.r = self.p.records(validate_assets=False)[self.r["id"]]
        self.start()
        if phase == "before-admission":
            original = execution.atomic_write
            def write(path,text,*args):
                if Path(path).parent.name == "boot-admission":
                    raise foundation.Interrupted()
                return original(path,text,*args)
            context = patch.object(execution,"atomic_write",side_effect=write)
        else:
            self.checkpoint(phase)
            context = patch.object(vm.os,"sync")
        with context, self.assertRaises(foundation.Interrupted):
            self.call("stop",key="interrupted-stop")
        self.p.execution_checkpoint = lambda *args:None
        self.assertEqual(self.p.execution().intent(self.r)["desired_state"],"stopped")
        self.xen.rows.clear()  # external disappearance after accepted user stop
        count = len(self.physical("create"))
        self.assertFalse(self.p.execution().boot_decision(self.r)["start"])
        self.p.autostart({self.r["id"]:self.r},[self.r["id"]])
        self.assertEqual(len(self.physical("create")),count)
        self.assertEqual(self.p.execution().admission(self.r)["intent_revision"],
                         self.p.execution().intent(self.r)["intent_revision"])

    def test_R1_stop_commit_before_accepted_journal_never_autostarts(self):
        self._stop_commit_cut("intent-published")

    def test_R1_stop_accepted_before_admission_never_autostarts(self):
        self._stop_commit_cut("intent-accepted")

    def test_R1_stop_admission_write_interruption_never_autostarts(self):
        self._stop_commit_cut("before-admission")

    def test_R1_missing_control_journal_cannot_use_stale_permission_or_null_policy(self):
        self.ok(self.call("autostart-set",{"vm_id":self.vm_id,"value":"on"}))
        self.r=self.p.records(validate_assets=False)[self.r["id"]]
        self.start()
        self.checkpoint("intent-published")
        with self.assertRaises(foundation.Interrupted):
            self.call("stop",key="missing-receipt")
        self.p.execution_checkpoint=lambda *args:None
        intent=self.p.execution().intent(self.r)
        (self.p.data/"control-requests"/(intent["accepted_operation_id"]+".json")).unlink()
        decision=self.p.execution().boot_decision(self.r)
        self.assertFalse(decision["start"])
        self.assertEqual(decision["reason"],"INTENT_RECONCILIATION_REQUIRED")

    def event_failure(self, names):
        original=execution.atomic_write
        def write(path,text,*args):
            if Path(path).parent.name=="execution-events" and json.loads(text)["event"] in names:
                raise OSError(errno.ENOSPC,"injected event sink full")
            return original(path,text,*args)
        return patch.object(execution,"atomic_write",side_effect=write)

    def test_R2_recovery_completed_and_success_replay_event_failure_preserve_terminal(self):
        self.start();spec=self.spec()
        with self.event_failure({"recovery-completed","recovery-replayed"}):
            response=self.recover(spec)
            self.ok(response)
            self.assertTrue(response["data"]["diagnostic_warnings"])
            path=self.p.data/"recovery-attempts"/(spec["recovery_attempt_id"]+".json")
            self.assertEqual(json.loads(path.read_text())["phase"],"completed")
            count=len(self.physical("create"))
            replay=self.recover(spec);self.ok(replay)
            self.assertTrue(replay["replayed"])
            self.assertTrue(replay["data"]["diagnostic_warnings"])
            self.assertEqual(len(self.physical("create")),count)
            self.assertEqual(json.loads(path.read_text())["phase"],"completed")

    def test_R2_control_completed_and_replayed_event_failure_preserve_stopped_success(self):
        self.start()
        with self.event_failure({"control-completed","control-replayed"}):
            response=self.call("stop",key="durable-stop");self.ok(response)
            self.assertTrue(response["data"]["diagnostic_warnings"])
            journal=self.p.data/"control-requests"/(response["operation_id"]+".json")
            self.assertEqual(json.loads(journal.read_text())["phase"],"completed")
            count=len(self.physical("shutdown"))
            replay=self.call("stop",key="durable-stop");self.ok(replay)
            self.assertEqual(len(self.physical("shutdown")),count)
            self.assertEqual(json.loads(journal.read_text())["phase"],"completed")
        self.assertEqual(self.state()["xen_state"],"stopped")

    def test_R2_rejection_event_failure_does_not_hide_primary_error(self):
        self.start();spec=self.spec();self.stop()
        with self.event_failure({"request-rejected","control-rejected"}):
            response=self.recover(spec)
        self.fails(response,"INTENT_MISMATCH")
        self.assertTrue(response["error"]["details"]["diagnostic_warnings"])

    def test_R2_required_recovery_completion_journal_failure_stays_uncertain(self):
        self.start();spec=self.spec();original=execution.atomic_write
        def write(path,text,*args):
            if Path(path).parent.name=="recovery-attempts" and json.loads(text)["phase"]=="completed":
                raise OSError(errno.ENOSPC,"required terminal journal failed")
            return original(path,text,*args)
        with patch.object(execution,"atomic_write",side_effect=write):
            self.fails(self.recover(spec),"RECONCILE_REQUIRED")
        count=len(self.physical("create"))
        self.ok(self.recover(spec))
        self.assertEqual(len(self.physical("create")),count)

    def expire(self, phase, *, replay=True):
        self.start();spec=self.spec();self.checkpoint(phase)
        with self.assertRaises(foundation.Interrupted):
            self.recover(spec)
        self.p.execution_checkpoint=lambda *args:None
        self.ticks+=100
        path=self.p.data/"recovery-attempts"/(spec["recovery_attempt_id"]+".json")
        if replay:
            self.fails(self.recover(spec),"DEADLINE_EXCEEDED")
            self.assertEqual(json.loads(path.read_text())["phase"],"failed")
        mode="start-if-absent" if not self.xen.rows else "graceful-restart"
        fresh=self.spec(mode)
        self.ok(self.recover(fresh))
        old=json.loads(path.read_text())
        self.assertEqual(old["phase"],"failed")
        self.assertEqual(old["request"]["deadline_monotonic_ms"],spec["deadline_monotonic_ms"])

    def test_R3_accepted_cut_expiration_closes_old_attempt_and_allows_new(self):
        self.expire("recovery-accepted")

    def test_R3_removed_cut_expiration_closes_old_attempt_and_allows_new(self):
        self.expire("recovery-removed")

    def test_R3_new_attempt_can_reconcile_expired_prelaunch_owner(self):
        self.expire("recovery-removed",replay=False)

    def test_R3_expiration_plan_does_not_write_terminal_record(self):
        self.start();spec=self.spec();self.checkpoint("recovery-accepted")
        with self.assertRaises(foundation.Interrupted):self.recover(spec)
        self.p.execution_checkpoint=lambda *args:None;self.ticks+=100
        path=self.p.data/"recovery-attempts"/(spec["recovery_attempt_id"]+".json")
        before=path.read_bytes()
        self.fails(self.call("recover_once",spec,execute=False),"DEADLINE_EXCEEDED")
        self.assertEqual(path.read_bytes(),before)

    def test_R3_unresolved_launch_is_reconcile_not_retryable_busy_or_new_create(self):
        self.start();spec=self.spec();self.checkpoint("recovery-launching")
        with self.assertRaises(foundation.Interrupted):self.recover(spec)
        self.p.execution_checkpoint=lambda *args:None;self.ticks+=100
        count=len(self.physical("create"))
        result=self.recover(self.spec("start-if-absent"))
        self.fails(result,"RECONCILE_REQUIRED")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(result["error"]["details"]["blocking_attempt_id"],spec["recovery_attempt_id"])
        self.assertEqual(len(self.physical("create")),count)

    def test_R4_probe_error_and_error_sink_failures_always_return_envelope(self):
        self.start()
        path=self.p.runtime/"observations"/(self.vm_id+".json")
        good=path.read_bytes();command=self.p.command;write=execution.atomic_write
        for cause in ("timeout","malformed","busy"):
            for sink in ("full","denied","corrupt"):
                with self.subTest(cause=cause,sink=sink):
                    path.write_bytes(b"{" if sink=="corrupt" else good)
                    def call(args,**kw):
                        if args[:2]==["xl","list"]:
                            if cause=="malformed":return "broken output"
                            raise vm.PlatformError("injected Xen timeout")
                        return command(args,**kw)
                    def store(file,text,*args):
                        if Path(file).parent.name=="observations":
                            raise OSError(errno.EACCES if sink=="denied" else errno.ENOSPC,"injected snapshot failure")
                        return write(file,text,*args)
                    self.p.command=call
                    with patch.object(execution,"atomic_write",side_effect=store):
                        if cause=="busy":
                            with operation_lock(self.p.runtime):
                                response=self.call("probe",{"vm_id":self.vm_id,"timeout_ms":50})
                        else:
                            response=self.call("probe",{"vm_id":self.vm_id,"timeout_ms":50})
                    self.assertIsInstance(response,dict)
                    self.assertFalse(response["ok"])
                    self.assertIsNone(response["data"])
                    self.assertTrue(response["error"]["details"]["diagnostic_warnings"])
        self.p.command=command

    def hold(self, name):
        ready=threading.Event();release=threading.Event()
        def owner():
            with execution.short_lock(self.p.runtime,name):
                ready.set();release.wait(2)
        thread=threading.Thread(target=owner);thread.start()
        self.assertTrue(ready.wait(2))
        timer=threading.Timer(.4,release.set);timer.start()
        def cleanup():
            release.set();thread.join(2);timer.cancel()
        self.addCleanup(cleanup)
        return release

    def test_R5_observation_lock_uses_probe_deadline_not_holder_release(self):
        self.start();self.p.clock=time.monotonic;self.p.sleep=time.sleep
        release=self.hold("observation-"+self.vm_id)
        started=time.monotonic()
        response=self.call("probe",{"vm_id":self.vm_id,"timeout_ms":20})
        elapsed=time.monotonic()-started
        self.fails(response,"DEADLINE_EXCEEDED")
        self.assertLess(elapsed,.3)
        self.assertFalse(release.is_set())

    def test_R5_admission_lock_rejects_user_start_without_intent_change(self):
        before=self.p.execution().intent(self.r)
        release=self.hold("admission")
        response=self.call("start")
        self.fails(response,"OPERATION_IN_PROGRESS")
        self.assertEqual(self.p.execution().intent(self.r),before)
        self.assertFalse(release.is_set())
        self.assertEqual(self.physical("create"),[])

    def test_R5_host_barrier_lock_is_nonblocking_and_does_not_claim_publication(self):
        release=self.hold("admission")
        with self.assertRaises(execution.ExecutionError) as raised:
            self.p.execution().quiesce("review-test")
        self.assertEqual(raised.exception.error["code"],"OPERATION_IN_PROGRESS")
        self.assertIsNone(self.p.execution().barrier())
        self.assertFalse(release.is_set())

    def test_R3_expiration_observation_failure_keeps_unfinished_owner(self):
        self.start();spec=self.spec();self.checkpoint("recovery-accepted")
        with self.assertRaises(foundation.Interrupted):self.recover(spec)
        self.p.execution_checkpoint=lambda *args:None;self.ticks+=100
        command=self.p.command
        def fail(args,**kw):
            if args[:2]==["xl","list"]:raise vm.PlatformError("injected inaccessible Xen")
            return command(args,**kw)
        self.p.command=fail
        result=self.recover(spec);self.fails(result,"RECONCILE_REQUIRED")
        path=self.p.data/"recovery-attempts"/(spec["recovery_attempt_id"]+".json")
        self.assertEqual(json.loads(path.read_text())["phase"],"accepted")

    def test_R4_successful_xen_probe_with_failed_snapshot_is_structured_failure(self):
        original=execution.atomic_write
        def fail(path,text,*args):
            if Path(path).parent.name=="observations":raise PermissionError("snapshot denied")
            return original(path,text,*args)
        with patch.object(execution,"atomic_write",side_effect=fail):
            result=self.call("probe",{"vm_id":self.vm_id,"timeout_ms":100})
        self.fails(result,"OBSERVATION_UNAVAILABLE")
        self.assertTrue(result["error"]["details"]["diagnostic_warnings"])

    def test_R5_late_lock_acquisition_never_enters_success_region(self):
        real=execution.fcntl.flock
        def late(fd,flags):
            real(fd,flags)
            self.ticks=1
        with patch.object(execution.fcntl,"flock",side_effect=late):
            with self.assertRaises(execution.ExecutionError) as raised:
                with execution.short_lock(self.p.runtime,"late",deadline=.5,
                                          clock=self.p.clock,sleep=self.p.sleep):
                    self.fail("expired critical section entered")
        self.assertEqual(raised.exception.error["code"],"DEADLINE_EXCEEDED")

    def test_R5_busy_probe_error_publisher_does_not_wait_for_observation_lock(self):
        release=self.hold("observation-"+self.vm_id)
        with operation_lock(self.p.runtime):
            response=self.call("probe",{"vm_id":self.vm_id,"timeout_ms":20})
        self.fails(response,"OPERATION_IN_PROGRESS")
        self.assertTrue(response["error"]["details"]["diagnostic_warnings"])
        self.assertFalse(release.is_set())



if __name__=="__main__":
    unittest.main()
