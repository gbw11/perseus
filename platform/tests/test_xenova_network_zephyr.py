"""Production profile transition/control/recovery; small firmware and Xen process boundaries are fixtures."""
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import test_xenova_recovery_foundation as foundation
from test_xenova_zephyr import bundle
from test_xenova_lifecycle import application_bundle
from test_xenova_management import Interrupted
import xenova_zephyr as zephyr
import xenova_vm as vm
import xenova_management as management
from xenova_execution import fingerprint


def network_bundle(legacy,target):
    application_bundle(legacy,target)
    cfg=target/"zephyr.config"
    cfg.chmod(0o644)
    cfg.write_text(cfg.read_text()+"\n".join(["CONFIG_XENOVA_RUNTIME=y","CONFIG_XENOVA_NETFRONT=y",
        "CONFIG_NETWORKING=y","CONFIG_NET_L2_ETHERNET=y","CONFIG_NET_IPV4=y","CONFIG_NET_ARP=y",
        "CONFIG_NET_DHCPV4=y","# CONFIG_XEN_GRANT_TABLE is not set"])+"\n")
    cfg.chmod(0o444)
    dts=target/"zephyr.dts";dts.chmod(0o644)
    dts.write_text(dts.read_text().replace("0xf 0x4 0xa0","0xf 0x2 0xa0").replace("15 4 160","15 2 160"))
    dts.chmod(0o444)
    for name in ("zephyr.map","XEN-ABI-LICENSE"):
        (target/name).write_text("fixture");(target/name).chmod(0o444)
    manifest=target/"firmware.json";meta=json.loads(manifest.read_text())
    meta.update(hardware_profile=zephyr.NET_PROFILE,capabilities=zephyr.NET_CAPABILITIES)
    meta["files"]={p.name:{"sha256":zephyr.sha(p),"bytes":p.stat().st_size}
                   for p in target.iterdir() if p.name!="firmware.json"}
    manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
    return target


class NetworkPlatformTest(unittest.TestCase):
    sleep=foundation.FoundationTest.sleep
    ok=foundation.FoundationTest.ok
    fails=foundation.FoundationTest.fails
    def setUp(self):
        foundation.FoundationTest.setUp(self)
        self.addCleanup(patch.stopall)
        self.linux=self.r
        self.legacy=bundle(self.p.data/"legacy-starter")
        self.fw=network_bundle(self.legacy,self.p.data/"network-app")
        self.z=zephyr.new_instance(self.p.data,"zephyr-drive-test",self.legacy,{})
        self.p.execution().initialize(self.z,"stopped","create")
        self.zid=self.z["uuid"];self.zdir=self.p.data/"instances"/self.z["id"]
        self.xen.records[self.z["id"]]=self.z
        self.old_command=self.p.command
        def command(args,**kwargs):
            if args[0]=="xenstore-read":
                self.xen.calls.append(args);return self.ready
            if args[:2]==["xl","reboot"] and self.cooperative_removes:
                self.xen.calls.append(args)
                self.xen.rows=[r for r in self.xen.rows if str(r["domid"])!=args[2]]
                return ""
            result=self.old_command(args,**kwargs)
            for row in self.xen.rows:
                if row["config"]["c_info"]["uuid"]==self.zid:row["config"]["disks"]=[]
            return result
        self.ready="1";self.cooperative_removes=True;self.p.command=command
        patch.object(self.p,"reserve_space").start()
    def state(self):
        return self.ok(self.m.call("get",{"vm_id":self.zid}))["vm"]
    def spec(self,bundle_path=None,policy="graceful"):
        current=self.state();path=bundle_path or self.fw
        return {"vm_id":self.zid,"bundle":str(path),"manifest_sha256":zephyr.sha(path/("firmware.json" if (path/"firmware.json").exists() else "template.json")),
                "expected_lifecycle_generation":current["generation"],
                "expected_configuration_sha256":current["configuration_sha256"],"board_shutdown_policy":policy}
    def transition(self,spec=None,key="upgrade",execute=True):
        result=self.m.call("firmware-transition",spec or self.spec(),execute=execute,idempotency_key=key)
        if result["ok"] and execute:
            self.z=self.p.records(validate_assets=False)[self.z["id"]]
            self.xen.records[self.z["id"]]=self.z
        return result
    def control(self,action,key=None):
        with patch.object(vm.os,"sync"):
            return self.m.call(action,{"vm_id":self.zid},execute=True,idempotency_key=key)
    def physical(self,action):
        return [q for q in self.xen.calls if q[:2]==["xl",action]]
    def upgrade_start(self):
        self.ok(self.transition());return self.ok(self.control("start"))["vm"]

    def test_profile_bundle_requires_real_network_config_and_level_irq(self):
        zephyr.bundle_info(self.fw)
        cfg=self.fw/"zephyr.config";cfg.chmod(0o644);cfg.write_text(cfg.read_text().replace("CONFIG_XENOVA_NETFRONT=y",""))
        meta=self.fw/"firmware.json";m=json.loads(meta.read_text());m["files"][cfg.name]={"sha256":zephyr.sha(cfg),"bytes":cfg.stat().st_size}
        cfg.chmod(0o444);meta.chmod(0o644);meta.write_text(json.dumps(m));meta.chmod(0o444)
        self.fails(self.transition(),"ARTIFACT_INVALID")
    def test_legacy_remains_supported_and_has_no_vif_or_graceful_claim(self):
        vm.validate_record(self.z,self.zdir);self.assertNotIn("vif =",self.p.config(self.z))
        self.assertFalse(vm.cooperative(self.z))
    def test_transition_preserves_uuid_origin_intent_and_immutable_old_firmware(self):
        before=self.state();old=zephyr.active_directory(self.zdir,self.z)
        hashes={p.name:zephyr.sha(p) for p in old.iterdir()}
        self.ok(self.transition());after=self.state()
        for field in ("vm_id","generation","intent_revision","desired_state"):
            self.assertEqual(after[field],before[field])
        self.assertEqual(after["configuration"]["creation_origin"],before["configuration"]["creation_origin"])
        self.assertEqual(hashes,{p.name:zephyr.sha(p) for p in old.iterdir()})
        self.assertNotEqual(after["ipv4"],self.linux["ipv4"])
        self.assertIn("bridge=xenbr0",self.p.config(self.z))
        self.assertEqual((self.p.runtime/"dhcp-hosts").stat().st_mode&0o777,0o644)
        self.assertEqual(self.physical("create"),[])
    def test_runtime_template_listing_keeps_validated_profile_capabilities(self):
        import shutil
        for index,(source,capabilities,profile) in enumerate((
                (self.legacy,zephyr.CAPABILITIES,zephyr.PROFILE),
                (self.fw,zephyr.NET_CAPABILITIES,zephyr.NET_PROFILE))):
            data=self.p.data/("list-profile-"+str(index))
            (data/"templates").mkdir(parents=True)
            (data/"rtos-templates").mkdir()
            manifest="firmware.json" if (source/"firmware.json").exists() else "template.json"
            meta=json.loads((source/manifest).read_text())
            name=meta.get("id","network-catalog-fixture")
            target=data/"rtos-templates"/name
            shutil.copytree(source,target)
            if "id" not in meta:
                meta.update(id=name,kind="zephyr-xen-firmware")
                (target/manifest).chmod(0o644)
                (target/manifest).write_text(json.dumps(meta))
                (target/manifest).chmod(0o444)
            with self.subTest(profile=profile["id"]),patch.object(self.p,"data",data):
                rows=vm.Platform.templates(self.p)
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]["capabilities"],capabilities)
                self.assertEqual(rows[0]["hardware_profile"],profile)

    def test_plan_does_not_reserve_or_publish(self):
        before={str(p):p.read_bytes() for p in self.p.data.rglob("*") if p.is_file()}
        self.ok(self.transition(execute=False))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.p.data.rglob("*") if p.is_file()})
    def test_rollback_keeps_new_address_reserved_and_old_profile(self):
        self.ok(self.transition());address=self.z["ipv4"];mac=self.z["mac"]
        self.ok(self.transition(self.spec(self.legacy,"force-allowed"),key="rollback"))
        self.assertEqual(self.z["ipv4"],address);self.assertEqual(self.z["mac"],mac)
        self.assertFalse(vm.has_network(self.z));self.assertNotIn("vif =",self.p.config(self.z))
        pool,held=self.m._ip_inventory(self.p.records(validate_assets=False))
        self.assertIn(address,held)
    def test_profile_publish_interruption_reuses_same_address_and_firmware(self):
        spec=self.spec()
        def interrupt(phase,op):
            if phase=="profile-published":raise Interrupted()
        self.m.checkpoint=interrupt
        with self.assertRaises(Interrupted):self.transition(spec)
        published=json.loads((self.zdir/"instance.json").read_text())
        self.m.checkpoint=lambda *a:None
        self.ok(self.transition(spec))
        self.assertEqual(self.z,published)
    def test_transition_refuses_running_intent_even_if_domain_is_absent(self):
        self.ok(self.control("start"))
        self.xen.rows=[]
        self.fails(self.transition(),"INTENT_MISMATCH")
    def test_cooperative_stop_and_reboot_change_execution_not_assets(self):
        first=self.upgrade_start();configuration=first["configuration"]
        result=self.ok(self.control("reboot","reboot"))
        after=self.state()
        self.assertNotEqual(first["execution_id"],after["execution_id"])
        self.assertEqual(after["intent_revision"],first["intent_revision"]+1)
        self.assertEqual(after["configuration"],configuration)
        count=len(self.physical("create"));self.ok(self.control("reboot","reboot"))
        self.assertEqual(len(self.physical("create")),count)
        self.assertEqual(self.state()["execution_id"],after["execution_id"])
        self.ok(self.control("stop"))
        self.assertEqual(self.state()["desired_state"],"stopped")
        self.assertEqual(self.physical("destroy"),[])
        self.assertIn("shutdown",[q[1] for q in self.xen.calls if q[0]=="xl"])
    def test_reboot_removal_race_requires_new_successful_absence_observations(self):
        self.upgrade_start()
        record=self.z
        old=copy.deepcopy(self.xen.rows[0])
        old["xen_unique_id"]=self.p.execution().unique(old,100)
        snapshot={old["domid"]:{"domid":old["domid"],"uuid":record["uuid"],"name":record["name"]}}
        with patch.object(self.p,"removal_snapshot",side_effect=[snapshot,snapshot,{},{}]) as observe, \
                patch("xenova_execution.Execution.unique",side_effect=vm.PlatformError("identity process vanished")):
            self.p.wait_reboot_removed(record,old,100)
        self.assertEqual(observe.call_count,4)
        for followup in ([snapshot,snapshot],[{},snapshot]):
            with self.subTest(followup=followup), \
                    patch.object(self.p,"removal_snapshot",side_effect=[snapshot,snapshot,*followup]), \
                    patch("xenova_execution.Execution.unique",side_effect=vm.PlatformError("lookup unavailable")):
                with self.assertRaisesRegex(vm.PlatformError,"lookup unavailable"):
                    self.p.wait_reboot_removed(record,old,100)
        with patch.object(self.p,"removal_snapshot",side_effect=[snapshot,snapshot,vm.PlatformError("broken list")]), \
                patch("xenova_execution.Execution.unique",side_effect=vm.PlatformError("lookup unavailable")):
            with self.assertRaisesRegex(vm.PlatformError,"broken list"):
                self.p.wait_reboot_removed(record,old,100)
        with patch.object(self.p,"removal_snapshot",return_value=snapshot), \
                patch("xenova_execution.Execution.unique",return_value="999999"):
            with self.assertRaises(vm.DomainIdentityError):
                self.p.wait_reboot_removed(record,old,100)

    def test_expired_submitted_reboot_closes_removed_target_without_launch(self):
        self.upgrade_start()
        self.cooperative_removes=False
        def interrupt(phase,op):
            if phase=="intent-shutdown-requested":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","expired-submitted")
        self.p.execution_checkpoint=lambda *a:None
        self.xen.rows=[]
        self.ticks=100
        self.fails(self.control("reboot","expired-submitted"),"DEADLINE_EXCEEDED")
        op=next(q for q in self.p.execution().requests() if q["idempotency_key"]=="expired-submitted")
        self.assertEqual(op["phase"],"failed")
        self.assertEqual(op["reboot_stage"],"old-removed")
        self.assertEqual(len(self.physical("create")),1)
        self.assertEqual(self.physical("destroy"),[])

    def test_missing_runtime_handler_rejects_before_acceptance(self):
        first=self.upgrade_start();self.ready="0"
        self.fails(self.control("reboot"),"RECOVERY_MODE_UNSUPPORTED")
        self.assertEqual(first["intent_revision"],self.state()["intent_revision"])
        self.assertEqual(self.physical("reboot"),[])
        self.fails(self.control("stop"),"RECOVERY_MODE_UNSUPPORTED")
    def test_old_removed_interruption_can_resume_once(self):
        first=self.upgrade_start()
        def interrupt(phase,op):
            if phase=="intent-old-removed":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","cut-removed")
        self.assertEqual(self.xen.rows,[])
        self.p.execution_checkpoint=lambda *a:None
        self.ok(self.control("reboot","cut-removed"))
        self.assertEqual(len(self.physical("reboot")),1)
        self.assertEqual(len(self.physical("create")),2)
    def test_new_launching_interruption_never_blindly_creates(self):
        self.upgrade_start()
        def interrupt(phase,op):
            if phase=="intent-new-launching":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","cut-launch")
        self.p.execution_checkpoint=lambda *a:None
        self.fails(self.control("reboot","cut-launch"),"RECONCILE_REQUIRED")
        self.assertEqual(len(self.physical("create")),1)
    def test_new_observed_interruption_finalizes_without_duplicate(self):
        self.upgrade_start()
        def interrupt(phase,op):
            if phase=="intent-new-observed":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","cut-observed")
        self.p.execution_checkpoint=lambda *a:None
        self.ok(self.control("reboot","cut-observed"))
        self.assertEqual(len(self.physical("create")),2)
    def test_timeout_does_not_force_or_recreate(self):
        self.upgrade_start();self.cooperative_removes=False
        response=self.control("reboot")
        self.assertFalse(response["ok"])
        self.assertEqual(len(self.physical("create")),1)
        self.assertEqual(self.physical("destroy"),[])
    def test_graceful_recovery_reuses_assets_and_does_not_increment_intent(self):
        first=self.upgrade_start()
        spec={"vm_id":self.zid,"recovery_attempt_id":"70000000-1111-4222-8333-123456789abc",
            "expected_host_boot_id":self.host,"expected_lifecycle_generation":0,
            "expected_intent_revision":first["intent_revision"],
            "expected_execution":{"execution_id":first["execution_id"]},
            "mode":"graceful-restart","deadline_monotonic_ms":90000}
        with patch.object(vm.os,"sync"):
            result=self.ok(self.m.call("recover_once",spec,execute=True))
        self.assertEqual(self.state()["intent_revision"],first["intent_revision"])
        self.assertNotEqual(self.state()["execution_id"],first["execution_id"])
        self.assertEqual(result["original_result"]["app_readiness"],"unknown")
        self.assertEqual(self.physical("destroy"),[])

    def test_new_catalog_create_allocates_network_identity_without_start(self):
        target=self.p.data/"rtos-templates"/"xenova-zephyr-network-starter-1.1.0"
        target.parent.mkdir()
        import shutil
        shutil.copytree(self.fw,target)
        manifest=target/"firmware.json";meta=json.loads(manifest.read_text())
        meta.update(id=target.name,kind="zephyr-xen-firmware",template_id="zephyr-xenvm-starter-arm64",
                    template_version="1.1.0",version="1.1.0")
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        descriptor={"schema_version":"1.0","template_id":"zephyr-xenvm-starter-arm64","template_version":"1.1.0",
                    "guest_kind":"zephyr","asset_directory":"rtos-templates/"+target.name,
                    "source_template_id":target.name,"manifest_sha256":zephyr.sha(manifest)}
        parent=self.p.data/"catalog"/descriptor["template_id"];parent.mkdir()
        (parent/"1.1.0.json").write_text(json.dumps(descriptor))
        spec={"vm_id":"70000000-2222-4222-8333-123456789abc","name":"Network trial",
              "template_id":descriptor["template_id"],"template_version":"1.1.0","vcpus":1,"memory_mib":16}
        with patch.object(self.m,"preparer",self.m._prepare):
            created=self.ok(self.m.call("create",spec,execute=True))
        self.assertTrue(vm.has_network(created["configuration"]))
        self.assertEqual(created["desired_state"],"stopped")
        self.assertNotEqual(created["ipv4"],self.linux["ipv4"])
        self.assertEqual(self.physical("create"),[])
        self.assertIn(created["ipv4"],(self.p.runtime/"dhcp-hosts").read_text())

    def test_new_factory_seed_assigns_unique_mac_after_registration_and_preserves_rerun(self):
        import shutil
        import test_xenova_default_deployment as deployment
        from first_install_fixture import assemble
        f=deployment.DefaultDeploymentTest("runTest");f.setUp();self.addCleanup(f.doCleanups)
        board,factory=f.current_board()
        for entry in factory["firmware_instances"]:
            shutil.rmtree(board.data/"seeds"/entry["record"]["id"])
        source=f.base/"network-catalog"
        network_bundle(f.base/"diagnostic",source)
        manifest=source/"firmware.json";meta=json.loads(manifest.read_text())
        lock=json.loads((Path(__file__).resolve().parents[1]/"guests/zephyr/network-starter.lock.json").read_text())
        meta.update(id=lock["id"],kind="zephyr-xen-firmware",version=lock["version"],template_version="1.1.0")
        meta["source"].update({k:lock[k] for k in ("app","app_files","app_source_sha256","zephyr_commit","sdk_version")})
        meta["source"]["source_revision"]="sha256:"+lock["app_source_sha256"]
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        entry=assemble.rtos.info(source,zephyr.sha(manifest))
        # Existing immutable legacy firmware remains alongside the additional template.
        shutil.copytree(source,board.data/"rtos-templates"/lock["id"])
        factory["firmware_instances"]=assemble.copy_rtos_seeds(board.data,[entry])
        (board.data/".xenova-install.json").write_text(json.dumps(factory))
        board.p.register()
        records=vm.load_records(board.data)
        a,b=records["zephyr-drive-a"],records["zephyr-drive-b"]
        self.assertNotEqual(a["mac"],b["mac"]);self.assertNotEqual(a["ipv4"],b["ipv4"])
        self.assertTrue(all(vm.has_network(r) for r in (a,b)))
        saved={k:dict(r) for k,r in records.items()}
        board.p.register()
        self.assertEqual(saved,vm.load_records(board.data))

    def test_observed_reboot_finalizes_after_original_deadline_without_new_execution(self):
        self.upgrade_start()
        def interrupt(phase,op):
            if phase=="intent-new-observed":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","observed-deadline")
        before=self.state()["execution_id"];self.ticks+=200
        self.p.execution_checkpoint=lambda *a:None
        self.ok(self.control("reboot","observed-deadline"))
        self.assertEqual(self.state()["execution_id"],before)
        self.assertEqual(len(self.physical("create")),2)

    def test_old_removed_expiration_closes_without_launch(self):
        self.upgrade_start()
        def interrupt(phase,op):
            if phase=="intent-old-removed":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","removed-expiry")
        self.p.execution_checkpoint=lambda *a:None;self.ticks+=200
        self.fails(self.control("reboot","removed-expiry"),"DEADLINE_EXCEEDED")
        self.assertEqual(len(self.physical("create")),1)
        self.assertEqual(self.xen.rows,[])

    def test_durable_observed_reboot_replays_historical_result_after_later_user_stop(self):
        self.upgrade_start()
        def interrupt(phase,op):
            if phase=="intent-new-observed":raise Interrupted()
        self.p.execution_checkpoint=interrupt
        with self.assertRaises(Interrupted):self.control("reboot","observed-stopped")
        born=self.state()["execution_id"]
        self.p.execution_checkpoint=lambda *a:None
        self.ok(self.control("stop"))
        revision=self.state()["intent_revision"]
        self.ok(self.control("reboot","observed-stopped"))
        self.assertEqual(self.state()["desired_state"],"stopped")
        self.assertEqual(self.state()["intent_revision"],revision)
        op=next(q for q in self.p.execution().requests() if q["idempotency_key"]=="observed-stopped")
        self.assertEqual(op["result"]["execution_id"],born)
        self.assertEqual(len(self.physical("create")),2)


if __name__=="__main__":unittest.main()
