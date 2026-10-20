"""실제 의도/journal/판정을 실행한다. Xen·시각·작은 자산·I/O 중단만 외부 경계다."""
import copy
import ctypes
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from first_install_fixture import ROOT, FILES
import test_xenova_management as management_fixture
from test_xenova_management import Interrupted
from test_xenova_platform import domain, fixture, record
import xenova_management as management
import xenova_vm as vm
import xenova_execution as execution
import xenova_domain_identity as identity
from first_install_contract import uuid4
from xenova_runtime import operation_lock, OperationBusy

HOST_A = "10000000-1111-4222-8333-123456789abc"
HOST_B = "20000000-1111-4222-8333-123456789abc"


class FoundationTest(unittest.TestCase):
    def setUp(self):
        self.f = management_fixture.ManagementTest("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.p, self.m, self.xen = self.f.p, self.f.m, self.f.xen
        self.host, self.ticks = HOST_A, 0
        self.p.boot_id_reader = lambda: self.host
        self.p.clock = lambda: self.ticks
        self.p.sleep = self.sleep
        self.addCleanup(patch.stopall)
        patch.object(self.p, "check_xen").start()
        self.vm_id = self.f.create()
        self.r = self.p.records(validate_assets=False)[self.f.key]
        self.xen.records = {self.r["id"]:self.r}
        self.directory = self.p.data/"instances"/self.r["id"]

    def sleep(self, seconds):
        self.ticks += seconds

    def call(self, action, spec=None, key=None, execute=True):
        return self.m.call(action, spec or {"vm_id":self.vm_id}, execute=execute, idempotency_key=key)

    def ok(self, response):
        self.assertTrue(response["ok"], response)
        return response["data"]

    def fails(self, response, code):
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"]["code"], code, response)
        return response

    def start(self, key=None):
        return self.ok(self.call("start", key=key))["vm"]

    def stop(self, key=None):
        with patch.object(vm.os, "sync"):
            return self.ok(self.call("stop", key=key))["vm"]

    def state(self):
        return self.ok(self.call("get"))["vm"]

    def spec(self, mode="graceful-restart"):
        current = self.state()
        expected = ({"absent_observation_revision":current["observation_revision"]}
                    if mode == "start-if-absent" else {"execution_id":current["execution_id"]})
        return {"vm_id":self.vm_id, "recovery_attempt_id":uuid4(),
                "expected_host_boot_id":self.host, "expected_lifecycle_generation":current["generation"],
                "expected_intent_revision":current["intent_revision"], "expected_execution":expected,
                "mode":mode, "deadline_monotonic_ms":int((self.ticks+90)*1000)}

    def recover(self, spec):
        with patch.object(vm.os, "sync"):
            return self.call("recover_once", spec)

    def physical(self, action):
        return [c for c in self.xen.calls if c[:2] == ["xl", action]]

    def checkpoint(self, phase):
        self.p.execution_checkpoint = lambda current, value: (
            (_ for _ in ()).throw(Interrupted()) if current == phase else None)

    def test_P01_name_cli_and_uuid_manager_share_intent_source(self):
        self.start()
        before = self.state()["intent_revision"]
        with patch.object(vm, "Platform", return_value=self.p), patch.object(vm.os, "sync"), redirect_stdout(io.StringIO()):
            vm.main(["stop", self.r["id"]])
        self.assertEqual(self.state()["desired_state"], "stopped")
        self.assertEqual(self.state()["intent_revision"], before+1)
        self.start()
        self.assertEqual(self.state()["intent_revision"], before+2)

    def test_P02_duplicate_start_preserves_execution_and_key_replay_revision(self):
        first = self.start("start-once")
        count = len(self.physical("create"))
        replay = self.call("start", key="start-once")
        self.ok(replay)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.state()["intent_revision"], first["intent_revision"])
        self.assertEqual(self.state()["execution_id"], first["execution_id"])
        second = self.start()
        self.assertEqual(second["intent_revision"], first["intent_revision"]+1)
        self.assertEqual(second["execution_id"], first["execution_id"])
        self.assertEqual(len(self.physical("create")), count)

    def test_P03_absent_stop_records_intent_without_shutdown(self):
        before = self.state()["intent_revision"]
        result = self.stop("stop-absent")
        self.assertEqual(result["desired_state"], "stopped")
        self.assertEqual(result["intent_revision"], before+1)
        self.assertEqual(self.physical("shutdown"), [])
        self.stop("stop-absent")
        self.assertEqual(self.state()["intent_revision"], before+1)

    def test_control_key_conflict_never_changes_intent(self):
        self.start("same")
        before = self.state()["intent_revision"]
        self.fails(self.call("stop", key="same"), "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.state()["intent_revision"], before)

    def test_P04_rejected_method_or_missing_asset_preserves_revision(self):
        before = self.state()["intent_revision"]
        (self.directory/"Image").unlink()
        self.fails(self.call("start"), "ARTIFACT_INVALID")
        self.assertEqual(self.p.execution().intent(self.r)["intent_revision"], before)
        self.assertEqual(self.physical("create"), [])

    def test_P05_accepted_stop_timeout_keeps_stopped_intent_and_never_forces(self):
        self.start()
        before = self.state()["intent_revision"]
        self.xen.hang_shutdown = True
        result = self.call("stop")
        self.fails(result, "XEN_EXECUTION_FAILED")
        self.assertTrue(result["error"]["details"]["accepted"])
        self.assertEqual(self.state()["desired_state"], "stopped")
        self.assertEqual(self.state()["intent_revision"], before+1)
        self.assertEqual(self.physical("destroy"), [])

    def test_P06_intent_write_failure_has_journal_and_no_physical_execution(self):
        original = execution.atomic_write
        target = self.p.data/"control"/(self.vm_id+".json")
        before = target.read_bytes()
        def fail(path, text, *args):
            if Path(path) == target:
                raise OSError("injected persistent write failure")
            return original(path, text, *args)
        with patch.object(execution, "atomic_write", side_effect=fail):
            result = self.call("start", key="write-failure")
        self.fails(result, "STORAGE_UNAVAILABLE")
        self.assertIsNotNone(result["operation_id"])
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.physical("create"), [])
        self.start("write-failure")
        self.assertEqual(self.state()["intent_revision"], 2)

    def test_intent_journal_before_commit_crash_reuses_one_revision(self):
        self.checkpoint("intent-prepared")
        with self.assertRaises(Interrupted):
            self.call("start", key="cut")
        self.assertEqual(self.p.execution().intent(self.r)["intent_revision"], 1)
        self.p.execution_checkpoint = lambda *args:None
        self.start("cut")
        self.assertEqual(self.state()["intent_revision"], 2)

    def test_intent_commit_before_accepted_journal_crash_is_reconciled(self):
        self.checkpoint("intent-published")
        with self.assertRaises(Interrupted):
            self.call("start", key="cut")
        self.assertEqual(self.p.execution().intent(self.r)["intent_revision"], 2)
        self.assertEqual(self.physical("create"), [])
        self.p.execution_checkpoint = lambda *args:None
        self.start("cut")
        self.assertEqual(self.state()["intent_revision"], 2)

    def test_intent_executor_crash_does_not_blindly_repeat_create(self):
        self.checkpoint("intent-executing")
        with self.assertRaises(Interrupted):
            self.call("start", key="cut")
        self.p.execution_checkpoint = lambda *args:None
        self.fails(self.call("start", key="cut"), "RECONCILE_REQUIRED")
        self.assertEqual(self.physical("create"), [])

    def test_P07_recovery_keeps_running_intent_and_all_vm_assets(self):
        first = self.start()
        before = {f.name:f.read_bytes() for f in self.directory.iterdir()}
        result = self.ok(self.recover(self.spec()))["original_result"]
        after = self.state()
        self.assertEqual(after["desired_state"], "running")
        self.assertEqual(after["intent_revision"], first["intent_revision"])
        self.assertNotEqual(result["execution_id"], first["execution_id"])
        self.assertEqual({f.name:f.read_bytes() for f in self.directory.iterdir()}, before)
        self.assertEqual(self.physical("destroy"), [])

    def test_P08_pending_recovery_is_rejected_after_user_stop(self):
        self.start()
        spec = self.spec()
        self.stop()
        count = len(self.physical("create"))
        self.fails(self.recover(spec), "INTENT_MISMATCH")
        self.assertEqual(len(self.physical("create")), count)

    def test_P09_stop_start_running_again_does_not_revalidate_old_revision(self):
        self.start()
        spec = self.spec()
        self.stop()
        self.start()
        self.fails(self.recover(spec), "INTENT_MISMATCH")

    def test_P10_same_attempt_response_loss_replays_without_restart(self):
        self.start()
        spec = self.spec()
        original = self.ok(self.recover(spec))["original_result"]
        count = len(self.physical("create"))
        again = self.recover(spec)
        self.ok(again)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["data"]["original_result"], original)
        self.assertEqual(len(self.physical("create")), count)
        self.fails(self.recover({**spec,"mode":"force-restart"}), "IDEMPOTENCY_CONFLICT")

    def test_completed_recovery_returns_original_and_current_separately(self):
        self.start()
        spec = self.spec()
        original = self.ok(self.recover(spec))["original_result"]
        self.stop()
        self.start()
        replay = self.ok(self.recover(spec))
        self.assertEqual(replay["original_result"], original)
        self.assertNotEqual(replay["current_observation"]["execution_id"], original["execution_id"])

    def test_P11_same_uuid_domid_reuse_has_different_xen_incarnation(self):
        self.start()
        spec = self.spec()
        self.xen.rows = [copy.deepcopy(self.xen.rows[0])]
        current = self.state()
        self.assertIsNone(current["execution_id"])
        count = len(self.physical("shutdown"))
        self.fails(self.recover(spec), "TARGET_CHANGED")
        self.assertEqual(len(self.physical("shutdown")), count)

    def test_guest_internal_reboot_is_not_automatically_adopted(self):
        self.start()
        old = self.state()["execution_id"]
        self.xen.rows[0]["domid"] += 100
        state = self.state()
        self.assertIsNone(state["execution_id"])
        self.assertEqual(self.p.execution().run_record(self.r)["execution_id"], old)

    def test_P12_previous_host_request_rejected_and_snapshot_marked_stale(self):
        self.start()
        spec = self.spec()
        self.host = HOST_B
        self.fails(self.recover(spec), "TARGET_CHANGED")
        snap = self.ok(self.call("snapshot", {"vm_id":self.vm_id,"max_age_ms":1000}))["snapshot"]
        self.assertTrue(snap["stale"])
        self.assertTrue(snap["unknown"])
        self.assertIsNone(self.p.execution().admission(self.r))

    def test_P13_removed_checkpoint_resumes_with_same_attempt_without_second_stop(self):
        self.start()
        spec = self.spec()
        self.checkpoint("recovery-removed")
        with self.assertRaises(Interrupted):
            self.recover(spec)
        count = len(self.physical("shutdown"))
        self.p.execution_checkpoint = lambda *args:None
        self.ok(self.recover(spec))
        self.assertEqual(len(self.physical("shutdown")), count)

    def test_launching_before_physical_create_is_uncertain_and_not_repeated(self):
        self.start()
        spec = self.spec()
        self.checkpoint("recovery-launching")
        with self.assertRaises(Interrupted):
            self.recover(spec)
        count = len(self.physical("create"))
        self.p.execution_checkpoint = lambda *args:None
        self.fails(self.recover(spec), "RECONCILE_REQUIRED")
        self.assertEqual(len(self.physical("create")), count)

    def test_new_domain_before_binding_is_uncertain_and_preserved(self):
        self.start()
        spec = self.spec()
        original = self.xen
        def cut_after_create(args, **kwargs):
            result = original(args, **kwargs)
            if args[:2] == ["xl","create"]:
                raise Interrupted()
            return result
        self.p.command = cut_after_create
        with self.assertRaises(Interrupted):
            self.recover(spec)
        self.p.command = original
        count = len(self.physical("create"))
        self.fails(self.recover(spec), "RECONCILE_REQUIRED")
        self.assertEqual(len(self.physical("create")), count)
        self.assertEqual(len(self.xen.rows), 1)

    def test_new_binding_before_attempt_completion_can_be_finalized(self):
        self.start()
        spec = self.spec()
        self.checkpoint("launch-observed")
        with self.assertRaises(Interrupted):
            self.recover(spec)
        count = len(self.physical("create"))
        self.p.execution_checkpoint = lambda *args:None
        self.ok(self.recover(spec))
        self.assertEqual(len(self.physical("create")), count)

    def test_P14_probe_failure_keeps_last_success_and_does_not_mean_absent(self):
        self.start()
        original = self.p.command
        def broken(args, **kwargs):
            if args == ["xl","list","-l"]:
                raise vm.PlatformError("injected Xen access failure")
            return original(args, **kwargs)
        self.p.command = broken
        result = self.call("probe", {"vm_id":self.vm_id,"timeout_ms":1000})
        self.assertFalse(result["ok"])
        snap = self.ok(self.call("snapshot", {"vm_id":self.vm_id,"max_age_ms":1000}))["snapshot"]
        self.assertTrue(snap["unknown"])
        self.assertEqual(snap["last_success"]["value"]["presence"], "present")
        self.assertIsNotNone(snap["last_error"])
        self.p.command = original

    def test_broken_xl_output_is_not_absence(self):
        self.start()
        original = self.p.command
        self.p.command = lambda args, **kw: "not-json" if args == ["xl","list","-l"] else original(args, **kw)
        self.assertFalse(self.call("probe", {"vm_id":self.vm_id,"timeout_ms":1000})["ok"])

    def test_empty_xl_outputs_are_not_confirmed_absence(self):
        original = self.p.command
        self.p.command = lambda args, **kw: "[]" if args == ["xl","list","-l"] else "" if args[:2] == ["xl","list"] else original(args,**kw)
        self.fails(self.call("probe",{"vm_id":self.vm_id,"timeout_ms":1000}),"OBSERVATION_UNAVAILABLE")
        before=self.p.execution().intent(self.r)
        self.assertFalse(self.call("stop")["ok"])
        self.assertEqual(self.p.execution().intent(self.r),before)

    def test_P15_barrier_blocks_start_and_recovery_but_not_completed_replay(self):
        self.start("done")
        spec = self.spec()
        barrier = self.p.execution().quiesce("reboot")
        self.fails(self.call("start"), "HOST_QUIESCING")
        self.fails(self.recover(spec), "HOST_QUIESCING")
        self.ok(self.call("start", key="done"))
        self.fails(self.call("host-resume", {"barrier_id":uuid4()}), "TARGET_CHANGED")
        self.ok(self.call("host-resume", {"barrier_id":barrier["barrier_id"]}))
        self.assertEqual(self.p.execution().barrier()["state"], "open")

    def test_P16_manager_restart_preserves_admission_and_systemd_quiesce_closes_it(self):
        self.start()
        before = self.p.execution().admission(self.r)
        restarted = management.Manager(self.p)
        self.ok(restarted.call("get", {"vm_id":self.vm_id}))
        self.assertEqual(self.p.execution().admission(self.r), before)
        with patch.object(vm, "Platform", return_value=self.p), redirect_stdout(io.StringIO()):
            vm.main(["host-quiesce"])
        self.assertEqual(self.p.execution().barrier()["state"], "quiescing")
        self.p.execution().initialize_host()
        self.assertEqual(self.p.execution().barrier()["state"], "quiescing")

    def test_P17_restore_does_not_roll_back_intent_revision_or_create_a_policy(self):
        self.start()
        self.stop()
        before = self.p.execution().intent(self.r)
        self.ok(self.call("retire", key="retire"))
        self.ok(self.call("restore", key="restore"))
        after = self.state()
        self.assertEqual(self.p.execution().intent(self.r), before)
        self.assertFalse(after["intent_applicable"])
        self.assertEqual(after["desired_state"], "stopped")
        self.start()
        self.assertEqual(self.state()["intent_revision"], before["intent_revision"]+1)

    def test_incomplete_asset_operation_blocks_recovery_without_destroy(self):
        self.start()
        spec = self.spec()
        self.stop()
        self.m.checkpoint = lambda phase, op: (_ for _ in ()).throw(Interrupted()) if phase == "recorded" else None
        with self.assertRaises(Interrupted):
            self.call("retire", key="retire-cut")
        self.m.checkpoint = lambda *args:None
        # Same applicable intent is re-established; the unresolved asset journal still owns it.
        self.start()
        spec = self.spec()
        self.fails(self.recover(spec), "OPERATION_IN_PROGRESS")

    def test_P18_snapshot_does_not_take_operation_lock_or_invoke_xen(self):
        self.start()
        with operation_lock(self.p.runtime), patch.object(self.p, "records", side_effect=AssertionError("registry called")), patch.object(self.p, "domains", side_effect=AssertionError("Xen called")):
            snap = self.ok(self.call("snapshot", {"vm_id":self.vm_id,"max_age_ms":1000}))["snapshot"]
        self.assertFalse(snap["unknown"])

    def test_P19_snapshot_stale_is_unknown_and_not_a_mutation_token(self):
        self.start()
        self.ticks += 10
        snap = self.ok(self.call("snapshot", {"vm_id":self.vm_id,"max_age_ms":1}))["snapshot"]
        self.assertTrue(snap["stale"])
        self.assertIsNone(snap["current"])

    def test_P20_new_create_stopped_legacy_unset_and_initialization_idempotence(self):
        self.assertEqual(self.state()["desired_state"], "stopped")
        self.p.execution().initialize(self.r, "running", "factory")
        self.assertEqual(self.state()["desired_state"], "stopped")
        legacy = record("debian-dev")
        fixture(self.p.data, legacy)
        state = self.ok(self.call("get", {"vm_id":legacy["uuid"]}))["vm"]
        self.assertEqual(state["desired_state"], "unknown")
        self.assertIsNone(state["intent_revision"])

    def test_P21_unapproved_conflicts_keep_legacy_boot_action_but_deny_recovery(self):
        self.r["autostart"] = True
        decision = self.p.execution().boot_decision(self.r)
        self.assertTrue(decision["start"])
        self.assertEqual(decision["reason"], "BOOT_POLICY_PENDING")
        self.assertFalse(decision["admission"]["allowed"])
        self.start()
        self.host = HOST_B
        self.r["autostart"] = False
        decision = self.p.execution().boot_decision(self.r)
        self.assertFalse(decision["start"])
        self.assertEqual(decision["reason"], "BOOT_POLICY_PENDING")
        self.assertFalse(decision["admission"]["allowed"])

    def test_approved_boot_choices_are_explicit_and_cover_all_combinations(self):
        policy = {"schema_version":"1.0","autostart_stopped":"keep-stopped",
                  "manual_running":"start","force_restart_vm_ids":[]}
        path = Path(self.f.tmp.name)/"policy.json"
        path.write_text(json.dumps(policy))
        self.p.execution_policy_path = path
        self.r["autostart"] = True
        self.assertFalse(self.p.execution().boot_decision(self.r)["start"])
        policy["autostart_stopped"] = "start"
        path.write_text(json.dumps(policy))
        self.assertTrue(self.p.execution().boot_decision(self.r)["start"])
        self.assertEqual(self.p.execution().intent(self.r)["desired_state"], "running")
        self.assertTrue(self.p.execution().boot_decision(self.r)["admission"]["allowed"])
        self.r["autostart"] = False
        self.assertTrue(self.p.execution().boot_decision(self.r)["start"])
        policy["manual_running"] = "keep-stopped"
        path.write_text(json.dumps(policy))
        self.assertFalse(self.p.execution().boot_decision(self.r)["start"])

    def test_P22_same_host_new_manager_keeps_execution(self):
        state = self.start()
        self.m = management.Manager(self.p)
        again = self.state()
        self.assertEqual(again["execution_id"], state["execution_id"])
        self.assertEqual(again["intent_revision"], state["intent_revision"])

    def test_P23_completion_does_not_create_app_readiness_or_vehicle_authority(self):
        self.start()
        result = self.ok(self.recover(self.spec()))["original_result"]
        self.assertEqual(result["app_readiness"], "unknown")
        for forbidden in ("APP_READY","STANDBY_READY","ACTIVE","fenced"):
            self.assertNotIn(forbidden, json.dumps(result))

    def test_recovery_deadline_is_not_reset_on_retransmission(self):
        self.start()
        spec = self.spec()
        self.checkpoint("recovery-removed")
        with self.assertRaises(Interrupted):
            self.recover(spec)
        self.p.execution_checkpoint = lambda *args:None
        self.ticks += 100
        self.fails(self.recover(spec), "DEADLINE_EXCEEDED")
        self.assertEqual(len(self.physical("create")), 1)

    def test_start_if_absent_requires_observation_revision_and_running_intent(self):
        self.start()
        self.xen.rows.clear()  # external failure; the desired state is still running
        spec = self.spec("start-if-absent")
        result = self.ok(self.recover(spec))["original_result"]
        self.assertIsNotNone(result["execution_id"])
        self.assertEqual(self.state()["desired_state"], "running")

    def test_false_absent_and_arbitrary_safety_flags_are_rejected(self):
        self.start()
        spec = self.spec()
        for bad in ({**spec,"fenced":True}, {**spec,"expected_execution":{"absent":True}},
                    {**spec,"deadline_monotonic_ms":True}):
            self.fails(self.recover(bad), "INVALID_SPEC")

    def test_force_recovery_requires_separate_per_vm_approval(self):
        self.start()
        spec = self.spec("force-restart")
        self.fails(self.recover(spec), "RECOVERY_MODE_UNSUPPORTED")
        policy = {"schema_version":"1.0","autostart_stopped":None,"manual_running":None,
                  "force_restart_vm_ids":[self.vm_id]}
        path = Path(self.f.tmp.name)/"policy.json";path.write_text(json.dumps(policy))
        self.p.execution_policy_path = path
        self.ok(self.recover(spec))
        self.assertEqual(len(self.physical("destroy")), 1)

    def test_recovery_plan_has_no_attempt_or_intent_writes(self):
        self.start()
        before = self.p.execution().intent(self.r)
        result = self.call("recover_once", self.spec(), execute=False)
        self.assertTrue(self.ok(result)["plan"])
        self.assertFalse((self.p.data/"recovery-attempts").exists())
        self.assertEqual(self.p.execution().intent(self.r), before)

    def test_busy_request_never_accepts_or_borrows_another_operation_id(self):
        before = self.p.execution().intent(self.r)
        with operation_lock(self.p.runtime):
            result = self.call("start", key="busy")
        self.fails(result, "OPERATION_IN_PROGRESS")
        self.assertIsNone(result["operation_id"])
        self.assertEqual(self.p.execution().intent(self.r), before)

    def test_board_cleanup_preserves_user_intent_and_barrier_on_failure(self):
        self.start()
        before = self.p.execution().intent(self.r)
        self.xen.hang_shutdown = True
        with patch.object(vm.os, "sync"), self.assertRaises(vm.PlatformError):
            self.p.prepare_board_shutdown([self.r], "reboot", deadline=self.p.clock()+2)
        self.assertEqual(self.p.execution().intent(self.r), before)
        self.assertEqual(self.p.execution().barrier()["state"], "quiescing")

    def test_host_barrier_published_before_bounded_operation_drain(self):
        with operation_lock(self.p.runtime), patch.object(vm, "Platform", return_value=self.p), self.assertRaises(OperationBusy):
            vm.main(["shutdown-all"])
        self.assertEqual(self.p.execution().barrier()["state"], "quiescing")
        self.assertEqual(self.physical("shutdown"), [])
        self.assertGreaterEqual(self.ticks, 90)

    def test_json_cli_snapshot_and_recovery_operation_share_the_envelope(self):
        self.start()
        import xenova_management_cli as cli
        output = io.StringIO()
        with redirect_stdout(output):
            result = cli.main(["snapshot", self.vm_id, "--max-age-ms","1000","--json"], manager=self.m)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertEqual(result["schema_version"], "1.0")

    def test_recover_json_cli_emits_exactly_one_json_document(self):
        self.start()
        import xenova_management_cli as cli
        path = Path(self.f.tmp.name)/"recovery.json"
        path.write_text(json.dumps(self.spec()))
        output = io.StringIO()
        with redirect_stdout(output), patch.object(vm.os, "sync"):
            cli.main(["recover-once","--spec-file",str(path),"--execute","--json"], manager=self.m)
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_P10_P15_P18_concurrent_attempt_barrier_and_other_vm_snapshot(self):
        self.start()
        second = {**self.f.spec, "vm_id":uuid4(), "name":"other", "ipv4":"192.168.77.31"}
        other = self.ok(self.call("create",second))["vm_id"]
        self.xen.records = self.p.records(validate_assets=False)
        self.ok(self.call("start",{"vm_id":other}))
        spec = self.spec()
        entered, release = threading.Event(), threading.Event()
        result = []
        def checkpoint(phase, value):
            if phase == "recovery-stopping":
                entered.set()
                if not release.wait(5):
                    raise AssertionError("test did not release external boundary")
        self.p.execution_checkpoint = checkpoint
        thread = threading.Thread(target=lambda:result.append(self.recover(spec)))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            before = len(self.xen.calls)
            snap = self.ok(self.call("snapshot",{"vm_id":other,"max_age_ms":1000}))["snapshot"]
            self.assertFalse(snap["unknown"])
            self.assertEqual(len(self.xen.calls),before)
            busy = self.recover(spec)
            self.fails(busy,"OPERATION_IN_PROGRESS")
            self.assertIsNone(busy["operation_id"])
            self.p.execution().quiesce("reboot")
        finally:
            release.set();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.fails(result[0],"HOST_QUIESCING")
        self.assertEqual(len(self.physical("create")),2)
        self.assertEqual(self.state()["desired_state"],"running")

    def test_busy_probe_is_unknown_and_keeps_last_success(self):
        self.start()
        with operation_lock(self.p.runtime):
            result = self.call("probe",{"vm_id":self.vm_id,"timeout_ms":1000})
        self.fails(result,"OPERATION_IN_PROGRESS")
        snap = self.ok(self.call("snapshot",{"vm_id":self.vm_id,"max_age_ms":1000}))["snapshot"]
        self.assertTrue(snap["unknown"])
        self.assertEqual(snap["last_error"]["code"],"OPERATION_IN_PROGRESS")
        self.assertEqual(snap["last_success"]["value"]["presence"],"present")

    def test_destroy_means_stop_intent_and_does_not_retire_assets(self):
        self.start()
        before = self.state()["intent_revision"]
        self.ok(self.call("destroy",{"vm_id":self.vm_id,"force":True}))
        self.assertEqual(self.state()["intent_revision"],before+1)
        self.assertEqual(self.state()["desired_state"],"stopped")
        self.assertTrue(self.directory.is_dir())
        self.assertEqual(self.state()["lifecycle"],"REGISTERED")

    def test_user_reboot_keeps_running_intent_and_changes_execution_only(self):
        before = self.start()
        self.ok(self.call("reboot",key="reboot"))
        after = self.state()
        self.assertEqual(after["desired_state"],"running")
        self.assertEqual(after["intent_revision"],before["intent_revision"]+1)
        self.assertNotEqual(after["execution_id"],before["execution_id"])
        self.assertEqual(self.physical("shutdown"),[])

    def test_autostart_set_changes_no_current_intent(self):
        self.start()
        before = self.p.execution().intent(self.r)
        self.ok(self.call("autostart-set",{"vm_id":self.vm_id,"value":"off"}))
        self.assertEqual(self.p.execution().intent(self.r),before)

    def test_control_request_from_previous_lifecycle_cannot_resume(self):
        self.checkpoint("intent-prepared")
        with self.assertRaises(Interrupted):
            self.call("start",key="old-generation")
        self.p.execution_checkpoint = lambda *args:None
        self.ok(self.call("retire",key="retire"))
        self.ok(self.call("restore",key="restore"))
        self.fails(self.call("start",key="old-generation"),"TARGET_CHANGED")
        self.assertEqual(self.physical("create"),[])

    def test_read_only_execution_context_matches_observed_binding(self):
        before = self.start()
        view = self.ok(self.call("execution-context"))["vm"]
        self.assertEqual(view["execution_context"]["execution_id"],before["execution_id"])
        self.assertEqual(view["execution_context"]["vm_id"],self.vm_id)
        self.assertNotIn("guest_boot_id",view["execution_context"])
        self.assertNotIn("app_session_id",view["execution_context"])

    def test_unknown_existing_live_domain_start_does_not_adopt_execution(self):
        self.xen.rows = [domain(self.r,self.directory/"root.raw")]
        state = self.start()
        self.assertIsNone(state["execution_id"])
        self.assertIsNone(self.p.execution().run_record(self.r))
        self.assertEqual(self.physical("create"),[])

    def test_second_incomplete_attempt_cannot_take_over_same_intent(self):
        self.start()
        original = self.spec()
        self.checkpoint("recovery-accepted")
        with self.assertRaises(Interrupted):
            self.recover(original)
        self.p.execution_checkpoint = lambda *args:None
        self.fails(self.recover({**original,"recovery_attempt_id":uuid4()}),"OPERATION_IN_PROGRESS")
        self.ok(self.recover(original))


    def test_status_uses_current_observation_after_earlier_list_became_stale(self):
        self.start()
        sampled=False
        def command(args,**kw):
            nonlocal sampled
            if args==["xl","list","-v"] and not sampled:
                sampled=True
                self.xen.rows=[]
            if args[0]=="timedatectl":
                return "NTPSynchronized=no"
            return self.xen(args,**kw)
        self.p.command=command
        status=self.p.status({self.r["id"]:self.r})["instances"][0]
        self.assertEqual(status["xen_state"],"stopped")
        self.assertIsNone(status["domid"])
        self.assertIsNone(status["xen_state_flags"])
        self.assertIsNone(status["execution_id"])
        self.assertEqual(status["desired_state"],"running")

    def test_absent_stop_does_not_clear_unfinished_launch_or_allow_new_create(self):
        self.checkpoint("launch-prepared")
        with self.assertRaises(Interrupted):
            self.call("start",key="unfinished")
        self.p.execution_checkpoint=lambda *args:None
        self.stop()
        intent=self.p.execution().intent(self.r)
        self.assertEqual(intent["desired_state"],"stopped")
        self.assertEqual(self.p.execution().run_record(self.r)["phase"],"uncertain")
        self.fails(self.call("start",key="new-after-stop"),"RECONCILE_REQUIRED")
        self.assertEqual(self.p.execution().intent(self.r),intent)
        self.assertEqual(self.physical("create"),[])

    def test_configuration_change_invalidates_previous_absent_observation(self):
        self.start()
        self.xen.rows=[]
        spec=self.spec("start-if-absent")
        updated={**self.r,"memory_mib":self.r["memory_mib"]+128}
        (self.directory/"instance.json").write_text(json.dumps(updated))
        before=len(self.physical("create"))
        self.fails(self.recover(spec),"TARGET_CHANGED")
        self.assertEqual(len(self.physical("create")),before)


    def test_reboot_interruption_without_launch_record_requires_reconciliation(self):
        self.start()
        self.checkpoint("intent-executing")
        with self.assertRaises(Interrupted):
            self.call("reboot", key="reboot-cut")
        (self.p.runtime/"executions"/(self.vm_id+".json")).unlink()
        self.p.execution_checkpoint = lambda *args:None
        before=len(self.physical("reboot"))
        self.fails(self.call("reboot", key="reboot-cut"), "RECONCILE_REQUIRED")
        self.assertEqual(len(self.physical("reboot")),before)

    def test_rejected_recovery_is_correlated_without_logging_request_content(self):
        self.start()
        spec=self.spec()
        self.stop()
        self.fails(self.recover(spec),"INTENT_MISMATCH")
        events=[json.loads(p.read_text()) for p in (self.p.runtime/"execution-events").glob("*.json")]
        rejected=[e for e in events if e["event"]=="request-rejected" and e["details"].get("action")=="recover_once"]
        self.assertEqual(rejected[-1]["details"]["recovery_attempt_id"],spec["recovery_attempt_id"])
        self.assertEqual(rejected[-1]["details"]["error_code"],"INTENT_MISMATCH")

    def test_accepted_control_finalization_write_failure_keeps_operation_identity(self):
        self.start()
        original=execution.atomic_write
        def fail(path,text,*args):
            if Path(path).parent.name=="control-requests" and json.loads(text)["phase"] in ("completed","failed"):
                raise OSError("injected journal finalization failure")
            return original(path,text,*args)
        with patch.object(execution,"atomic_write",side_effect=fail), patch.object(vm.os,"sync"):
            result=self.call("stop",key="finish-failure")
        self.fails(result,"RECONCILE_REQUIRED")
        self.assertIsNotNone(result["operation_id"])
        self.assertEqual(self.state()["desired_state"],"stopped")
        count=len(self.physical("shutdown"))
        self.ok(self.call("stop",key="finish-failure"))
        self.assertEqual(len(self.physical("shutdown")),count)


class FactoryIntentTest(unittest.TestCase):
    def board(self):
        import test_xenova_default_deployment as deployment
        helper = deployment.DefaultDeploymentTest("runTest")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        board, _ = helper.current_board()
        board.p.register()
        return board

    def test_new_three_factory_intents_running_and_registration_rerun_preserves_stop(self):
        board = self.board()
        from test_xenova_lifecycle import Xen
        records = vm.load_records(board.data)
        p = vm.Platform(board.data,board.root/"run/xenova",Xen(board.data,records),defaults_path=FILES/"defaults.json")
        for record in records.values():
            self.assertEqual(p.execution().intent(record)["desired_state"],"running")
        target = records["zephyr-drive-a"]
        with patch.object(vm.os,"sync"), redirect_stdout(io.StringIO()):
            p.request_control("stop",target,idempotency_key="factory-explicit-stop")
        before = p.execution().intent(target)
        board.p.register()
        self.assertEqual(p.execution().intent(target),before)
        self.assertEqual(before["desired_state"],"stopped")

    def test_completed_legacy_registration_does_not_infer_intent(self):
        board = self.board()
        records = vm.load_records(board.data)
        for record in records.values():
            (board.data/"control"/(record["uuid"]+".json")).unlink()
        board.p.register()
        p = vm.Platform(board.data,board.root/"run/xenova")
        self.assertTrue(all(p.execution().intent(record) is None for record in records.values()))

    def test_zephyr_board_force_policy_does_not_approve_recovery_or_individual_stop(self):
        board = self.board()
        from test_xenova_lifecycle import Xen
        records = vm.load_records(board.data)
        p = vm.Platform(board.data,board.root/"run/xenova",Xen(board.data,records),defaults_path=FILES/"defaults.json")
        m = management.Manager(p)
        target = records["zephyr-drive-a"]
        with patch.object(vm,"storage_check"), patch.object(p,"check_xen"):
            started = m.call("start",{"vm_id":target["uuid"]})
            self.assertTrue(started["ok"],started)
            state=started["data"]["vm"]
            refused=m.call("stop",{"vm_id":target["uuid"]})
            self.assertFalse(refused["ok"])
            self.assertEqual(p.execution().intent(target)["intent_revision"],state["intent_revision"])
            spec={"vm_id":target["uuid"],"recovery_attempt_id":uuid4(),"expected_host_boot_id":p.boot_id_reader(),
                  "expected_lifecycle_generation":0,"expected_intent_revision":state["intent_revision"],
                  "expected_execution":{"execution_id":state["execution_id"]},"mode":"force-restart",
                  "deadline_monotonic_ms":int((p.clock()+90)*1000)}
            denied=m.call("recover_once",spec,execute=True)
            self.assertEqual(denied["error"]["code"],"RECOVERY_MODE_UNSUPPORTED",denied)
            spec["mode"]="graceful-restart"
            denied=m.call("recover_once",spec,execute=True)
            self.assertEqual(denied["error"]["code"],"RECOVERY_MODE_UNSUPPORTED",denied)
            with patch.object(vm.os,"sync"):
                stopped=m.call("stop",{"vm_id":target["uuid"],"force":True})
            self.assertTrue(stopped["ok"],stopped)
            self.assertTrue((board.data/"instances"/target["id"]).is_dir())



class PackagingAndUnitsTest(unittest.TestCase):
    def installed(self, root):
        from dom0_ssh_fixture import extract, install_policy
        extract(root)
        install_policy(root,replay_recipe=True)
        wants = root/"etc/systemd/system/multi-user.target.wants"
        link = wants/"xenova-host-admission.service"
        link.symlink_to("/usr/lib/systemd/system/xenova-host-admission.service")

    def test_actual_recipe_installs_runtime_policy_entrypoint_and_real_barrier_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.installed(root)
            for name in ("xenova_execution.py","xenova_recovery.py","xenova_domain_identity.py"):
                file=root/"usr/lib/xenova"/name
                self.assertEqual(file.read_bytes(),(FILES/name).read_bytes())
                self.assertEqual(file.stat().st_mode & 0o777,0o644)
            entry=root/"usr/sbin/xenova-domain-identity"
            self.assertEqual(entry.read_bytes(),(FILES/"xenova-domain-identity").read_bytes())
            self.assertEqual(entry.stat().st_mode & 0o777,0o755)
            policy=json.loads((root/"etc/xenova/execution-policy.json").read_text())
            self.assertIsNone(policy["autostart_stopped"])
            self.assertIsNone(policy["manual_running"])
            self.assertEqual(policy["force_restart_vm_ids"],[])
            recipe=(FILES.parent/"xenova-platform_1.0.bb").read_text()
            self.assertIn("xen-tools-libxenmanage",recipe)
            self.assertIn("python3-ctypes",recipe)
            unit=(root/"usr/lib/systemd/system/xenova-host-admission.service").read_text()
            self.assertIn("ExecStop=/usr/sbin/xenova-vm host-quiesce",unit)

    def test_actual_dom0_259_graph_accepts_barrier_and_rejects_injected_cycle(self):
        from dom0_ssh_fixture import verify
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.installed(root)
            result=verify(root,"xenova-host-admission.service","xenova-vms.service","xenova-vm-shutdown.service")
            text=result.stdout+result.stderr
            self.assertEqual(result.returncode,0,text)
            for diagnostic in ("Found ordering cycle","deleted to break ordering cycle","Ordering cycle found, skipping"):
                self.assertNotIn(diagnostic,text)
            drop=root/"etc/systemd/system/xenova-host-admission.service.d"
            drop.mkdir(parents=True)
            (drop/"injected.conf").write_text("[Unit]\nBefore=xenova-vms.service\n")
            broken=verify(root,"xenova-host-admission.service","xenova-vms.service")
            self.assertIn("Found ordering cycle",broken.stdout+broken.stderr)



class DomainIdentityABITest(unittest.TestCase):
    def test_real_ctypes_boundary_preserves_64bit_and_closes_on_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);source = root/"peer.c";shared=root/"peer.so"
            source.write_text("""
#include <stdint.h>
#include <errno.h>
static int closes;
void *xenmanage_open(void *logger,unsigned int flags) { (void)logger;(void)flags;return (void *)1; }
int xenmanage_close(void *handle) { (void)handle;closes++;return 0; }
int close_count(void) { return closes; }
int xenmanage_get_domain_info(void *h,unsigned int dom,unsigned int *state,unsigned int *caps,uint64_t *id) {
 (void)h;if(dom!=42){errno=EACCES;return -1;}*state=1;*caps=0;*id=9007199254740993ULL;return 0;
}
""")
            cp=subprocess.run(["cc","-shared","-fPIC",str(source),"-o",str(shared)],capture_output=True,text=True)
            self.assertEqual(cp.returncode,0,cp.stderr)
            lib=ctypes.CDLL(str(shared),use_errno=True)
            self.assertEqual(identity.identity(42,library=lib)["xen_unique_id"],"9007199254740993")
            self.assertEqual(lib.close_count(),1)
            with self.assertRaises(OSError):
                identity.identity(43,library=lib)
            self.assertEqual(lib.close_count(),2)

    def test_invalid_guest_domid_never_loads_library(self):
        for domid in (0, -1, 0x7ff0, True, "42"):
            with self.assertRaises(ValueError):
                identity.identity(domid)


if __name__ == "__main__":
    unittest.main()
