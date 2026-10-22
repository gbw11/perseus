"""Production resource journal and boot decisions; Xen/filesystem usage are external boundaries."""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
import test_xenova_recovery_foundation as foundation
from test_xenova_recovery_foundation import HOST_B
from test_xenova_management import Interrupted
import xenova_execution as execution
import xenova_management_cli as cli
import xenova_vm as vm


class ResourcesTest(unittest.TestCase):
    setUp = foundation.FoundationTest.setUp
    sleep = foundation.FoundationTest.sleep
    call = foundation.FoundationTest.call
    ok = foundation.FoundationTest.ok
    fails = foundation.FoundationTest.fails
    state = foundation.FoundationTest.state
    start = foundation.FoundationTest.start
    stop = foundation.FoundationTest.stop
    physical = foundation.FoundationTest.physical

    def spec(self, **changes):
        v = self.state()
        return {"vm_id":self.vm_id,"vcpus":2,"memory_mib":768,
                "expected_lifecycle_generation":v["generation"],
                "expected_configuration_sha256":v["configuration_sha256"], **changes}

    def update(self, spec=None, key="resize", execute=True):
        return self.call("update-resources", spec or self.spec(), key=key, execute=execute)

    def test_stopped_update_preserves_assets_identity_intent_and_initial_spec(self):
        before=self.state()
        assets={p.name:p.read_bytes() for p in self.directory.iterdir() if p.name!="instance.json"}
        intent=self.p.execution().intent(self.r)
        self.ok(self.update())
        after=self.state()
        self.assertEqual(after["configuration"], {**before["configuration"],"vcpus":2,"memory_mib":768})
        self.assertEqual(after["initial_spec"],before["initial_spec"])
        self.assertEqual(self.p.execution().intent(self.r),intent)
        self.assertEqual(assets,{p.name:p.read_bytes() for p in self.directory.iterdir() if p.name!="instance.json"})
        self.assertEqual(self.physical("create"),[])
        cfg=self.p.config(after["configuration"])
        self.assertIn("vcpus = 2",cfg);self.assertIn("memory = 768",cfg)

    def test_plan_has_no_persistent_writes(self):
        before={str(p):p.read_bytes() for p in self.p.data.rglob("*") if p.is_file()}
        self.ok(self.update(execute=False))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.p.data.rglob("*") if p.is_file()})

    def test_replay_returns_original_and_newer_current_without_rewind(self):
        spec=self.spec();first=self.ok(self.update(spec))
        self.ok(self.update(self.spec(vcpus=3,memory_mib=1024),key="next"))
        again=self.update(spec)
        self.assertTrue(again["replayed"])
        self.assertEqual(self.ok(again)["original_result"],first["original_result"])
        self.assertEqual(self.state()["vcpus"],3)
        self.fails(self.update({**spec,"vcpus":4}),"IDEMPOTENCY_CONFLICT")

    def test_actual_presence_and_running_intent_are_separately_rejected(self):
        self.start()
        self.fails(self.update(),"INTENT_MISMATCH")
        self.xen.rows=[]
        self.fails(self.update(),"INTENT_MISMATCH")

    def test_present_domain_with_stopped_intent_is_not_removed(self):
        self.start();self.xen.hang_shutdown=True
        self.fails(self.call("stop"),"XEN_EXECUTION_FAILED")
        self.fails(self.update(),"VM_NOT_STOPPED")
        self.assertEqual(self.physical("destroy"),[])

    def test_absent_observation_failure_never_changes_resources(self):
        spec=self.spec()
        with patch.object(self.p,"domains",side_effect=vm.PlatformError("bad Xen output")):
            self.fails(self.update(spec),"TARGET_CHANGED")
        self.assertEqual(self.r,json.loads((self.directory/"instance.json").read_text()))

    def test_invalid_values_and_expected_context(self):
        for change,code in [({"vcpus":True},"INVALID_SPEC"),({"memory_mib":True},"INVALID_SPEC"),
              ({"vcpus":0},"RESOURCE_UNSUPPORTED"),({"vcpus":5},"RESOURCE_UNSUPPORTED"),
              ({"memory_mib":127},"RESOURCE_UNSUPPORTED"),({"memory_mib":8193},"RESOURCE_UNSUPPORTED"),
              ({"expected_lifecycle_generation":True},"INVALID_SPEC"),
              ({"expected_lifecycle_generation":1},"TARGET_CHANGED"),
              ({"expected_configuration_sha256":"0"*64},"TARGET_CHANGED")]:
            with self.subTest(change=change):
                self.fails(self.update(self.spec(**change)),code)

    def test_interruption_before_and_after_publication_reconciles_once(self):
        for cut in ("recorded","resources-published"):
            with self.subTest(cut=cut):
                spec=self.spec(vcpus=2 if cut=="recorded" else 3)
                key="cut-"+cut
                def interrupt(phase,op):
                    if phase==cut:raise Interrupted()
                self.m.checkpoint=interrupt
                with self.assertRaises(Interrupted):self.update(spec,key=key)
                self.fails(self.call("start"),"OPERATION_IN_PROGRESS")
                self.m.checkpoint=lambda *args:None
                self.ok(self.update(spec,key=key))
                self.assertEqual(self.state()["vcpus"],spec["vcpus"])

    def test_required_metadata_write_failure_keeps_original_and_replays(self):
        spec=self.spec();original=self.m._write
        def fail(path,value):
            if path==self.directory/"instance.json":raise OSError("injected ENOSPC")
            original(path,value)
        with patch.object(self.m,"_write",side_effect=fail):
            self.fails(self.update(spec),"RECOVERY_REQUIRED")
        self.assertEqual(self.state()["vcpus"],1)
        self.ok(self.update(spec))

    def test_cli_name_and_uuid_use_manager(self):
        for target,cpus in ((self.r["name"],2),(self.vm_id,3)):
            spec=self.spec(vcpus=cpus)
            with redirect_stdout(io.StringIO()):
                result=cli.main(["update-resources",target,"--vcpus",str(cpus),"--memory-mib","768",
                    "--expected-lifecycle-generation","0","--expected-configuration-sha256",
                    spec["expected_configuration_sha256"],"--idempotency-key","cli-"+str(cpus),"--execute","--json"],manager=self.m)
            self.ok(result)
            self.assertEqual(self.state()["vcpus"],cpus)

    def test_required_completion_write_failure_replays_without_republishing_metadata(self):
        spec=self.spec()
        original=self.m._save_op
        def fail(op,phase=None):
            if op["action"]=="update-resources" and phase=="complete":
                raise OSError("injected journal ENOSPC")
            return original(op,phase)
        with patch.object(self.m,"_save_op",side_effect=fail):
            self.fails(self.update(spec),"RECOVERY_REQUIRED")
        inode=(self.directory/"instance.json").stat().st_ino
        self.ok(self.update(spec))
        self.assertEqual((self.directory/"instance.json").stat().st_ino,inode)

    def test_corrupted_after_values_do_not_override_the_recorded_request(self):
        spec=self.spec()
        self.m.checkpoint=lambda phase,op: (_ for _ in ()).throw(Interrupted()) if phase=="recorded" else None
        with self.assertRaises(Interrupted):self.update(spec)
        self.m.checkpoint=lambda *a:None
        op=next(p for p in (self.p.data/"operations").glob("*.json")
                if json.loads(p.read_text())["action"]=="update-resources")
        value=json.loads(op.read_text());value["updated_record"]["vcpus"]=4;op.write_text(json.dumps(value))
        self.fails(self.update(spec),"RECOVERY_REQUIRED")
        self.assertEqual(self.state()["vcpus"],1)


class BootPolicyTest(unittest.TestCase):
    setUp=ResourcesTest.setUp
    sleep=ResourcesTest.sleep
    call=ResourcesTest.call
    ok=ResourcesTest.ok
    fails=ResourcesTest.fails
    state=ResourcesTest.state
    start=ResourcesTest.start
    stop=ResourcesTest.stop

    def test_all_four_combinations_on_new_host_preserve_intent(self):
        for auto in (False,True):
            for desired in ("running","stopped"):
                with self.subTest(auto=auto,desired=desired):
                    self.host=HOST_B
                    self.p.execution().initialize_host()
                    if desired=="running":self.start()
                    else:self.stop()
                    self.r["autostart"]=auto
                    self.p.execution().initialize_host()
                    # Distinct host makes explicit user admission inapplicable.
                    self.host="30000000-1111-4222-8333-123456789abc"
                    before=self.p.execution().intent(self.r)
                    result=self.p.execution().boot_decision(self.r)
                    self.assertEqual(result["start"],auto and desired=="running")
                    self.assertEqual(result["admission"]["allowed"],result["start"])
                    self.assertEqual(self.p.execution().intent(self.r),before)

    def test_explicit_start_of_manual_vm_admits_current_host_only(self):
        self.r["autostart"]=False
        self.start()
        self.assertTrue(self.p.execution().boot_decision(self.r)["admission"]["allowed"])
        self.host=HOST_B
        self.assertFalse(self.p.execution().boot_decision(self.r)["start"])
        self.assertEqual(self.p.execution().intent(self.r)["desired_state"],"running")

    def test_existing_admin_policy_and_null_are_not_overwritten(self):
        path=Path(self.tmp.name) if hasattr(self,"tmp") else self.p.runtime.parent
        policy=path/"admin-policy.json"
        policy.write_text(json.dumps({"schema_version":"1.0","autostart_stopped":"start",
            "manual_running":None,"force_restart_vm_ids":[]}))
        policy.chmod(0o600)
        self.p.execution_policy_path=policy
        self.assertEqual(self.p.execution().policy()["autostart_stopped"],"start")
        self.assertIsNone(self.p.execution().policy()["manual_running"])


if __name__=="__main__":unittest.main()
