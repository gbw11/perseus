"""실제 recipe 설치·조립 source gate·dom0 그래프의 API/MQTT 패키징 회귀."""
import json
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from first_install_fixture import ROOT, assemble, platform_root
import deployment_payloads as payloads
from dom0_ssh_fixture import extract, install_policy, verify


class DeploymentPayloadTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="xenova-payloads-")
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.root=self.base/"root"

    def install_recipe(self,name,project,package):
        recipe=payloads.RECIPES/name/(name+"_0.1.0.bb")
        source=self.base/("unpack-"+name);source.mkdir()
        text=recipe.read_text()
        extra=re.search(r'FILESEXTRAPATHS:prepend := "([^"]+)"',text).group(1)
        extra=extra.replace(chr(36)+"{THISDIR}",str(recipe.parent))
        search=[Path(p) for p in extra.split(":") if p]+[recipe.parent/"files"]
        selected={}
        for name in re.findall(r"file://([^ ;\"]+)",re.search(r'SRC_URI = "([^"]+)"',text).group(1)):
            matches=[p/name for p in search if (p/name).exists()]
            self.assertTrue(matches,"recipe fetch source missing: "+name)
            selected[name]=matches[0]
            if matches[0].is_dir():
                shutil.copytree(matches[0],source/name)
            else:
                shutil.copyfile(matches[0],source/name)
        self.assertEqual(selected[package].resolve(),(project/package).resolve())
        body=text.split("do_install() {\n",1)[1].split("\n}",1)[0]
        for key,value in {"D":str(self.root),"S":str(source),
                          "PYTHON_SITEPACKAGES_DIR":"/usr/lib/python3.14/site-packages",
                          "systemd_system_unitdir":"/usr/lib/systemd/system",
                          "sysconfdir":"/etc","datadir":"/usr/share"}.items():
            body=body.replace("${"+key+"}",value)
        self.assertNotIn("${",body)
        subprocess.run(["bash","-eu","-c",body],check=True,capture_output=True,text=True)

    def install_apps(self):
        self.install_recipe("xenova-dom0-manager",payloads.API,"dom0_manager")
        self.install_recipe("xenova-mqtt-client",payloads.MQTT,"xenova_mqtt")

    def test_actual_recipe_installs_both_source_trees_and_safe_server_config(self):
        self.install_apps()
        for relative,source in payloads.source_files("usr/lib/python3.14/site-packages").items():
            self.assertEqual((self.root/relative).read_bytes(),source.read_bytes(),relative)
        env=(self.root/"etc/xenova/dom0-manager.env").read_text()
        for setting in ("XENOVA_DRIVER=platform","XENOVA_HOST=127.0.0.1","XENOVA_PORT=8081"):
            self.assertIn(setting,env)
        self.assertNotIn("XENOVA_SERVICE_TOKEN=",env)
        self.assertEqual(stat.S_IMODE((self.root/"etc/xenova/dom0-manager.env").stat().st_mode),0o600)
        self.assertIn("dom0_manager.service_token",(self.root/"usr/lib/systemd/system/xenova-dom0-manager.service").read_text())
        self.assertFalse(list(self.root.rglob("test_*.py")))
        self.assertTrue((self.root/"usr/lib/python3.14/site-packages/xenova_mqtt/__init__.py").is_file())
        self.assertFalse((self.root/"usr/lib/python3.14/site-packages/perseus_mqtt").exists())

    def test_actual_recipe_install_preserves_existing_vm_keys_and_disk(self):
        protected={}
        for relative in ("srv/xenova/instances/example/root.raw","etc/machine-id",
                         "etc/shadow","etc/ssh/ssh_host_ed25519_key"):
            p=self.root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b"owned test sentinel")
            protected[relative]=(p.read_bytes(),p.stat().st_ino)
        self.install_apps()
        for relative,before in protected.items():
            p=self.root/relative;self.assertEqual((p.read_bytes(),p.stat().st_ino),before)

    def test_current_root_gate_checks_sources_and_includes_them_in_manifest(self):
        platform_root(self.root)
        expected=assemble.platform_root_ok(self.root)
        for relative in payloads.source_files("usr/lib/python3.14/site-packages"):
            self.assertIn(relative,expected)
        inputs=assemble.source_fingerprints()
        for source in payloads.source_files("usr/lib/python3/site-packages").values():
            self.assertIn(str(source.relative_to(ROOT)),inputs)

    def test_gate_rejects_missing_api_or_mqtt_without_accepting_marker_alone(self):
        for package in ("dom0_manager","xenova_mqtt"):
            with self.subTest(package=package):
                root=self.base/package;platform_root(root)
                (root/"usr/lib/python3.14/site-packages"/package/"__init__.py").unlink()
                with self.assertRaises(ValueError):assemble.platform_root_ok(root)

    def test_gate_rejects_modified_api_and_mqtt_source(self):
        for relative in ("dom0_manager/api/vms.py","xenova_mqtt/client.py"):
            with self.subTest(relative=relative):
                root=self.base/relative.split("/")[0];platform_root(root)
                (root/"usr/lib/python3.14/site-packages"/relative).write_text("# stale payload\n")
                with self.assertRaisesRegex(ValueError,"source/installed"):assemble.platform_root_ok(root)

    def test_source_contract_refuses_missing_source_package(self):
        for field in ("API","MQTT"):
            with self.subTest(field=field),patch.object(payloads,field,self.base/"missing-source"):
                with self.assertRaisesRegex(ValueError,"source package missing"):
                    payloads.source_files("usr/lib/python3.14/site-packages")

    def test_gate_rejects_legacy_mqtt_package_name(self):
        platform_root(self.root)
        site=self.root/"usr/lib/python3.14/site-packages"
        (site/"xenova_mqtt").rename(site/"perseus_mqtt")
        with self.assertRaisesRegex(ValueError,"payload missing"):
            assemble.platform_root_ok(self.root)

    def test_gate_rejects_each_missing_runtime_dependency(self):
        for index,relative in enumerate(("fastapi/__init__.py","uvicorn/__init__.py","paho/mqtt/client.py",
                                         "pydantic/__init__.py","starlette/__init__.py","anyio/__init__.py")):
            with self.subTest(relative=relative):
                root=self.base/str(index);platform_root(root)
                (root/"usr/lib/python3.14/site-packages"/relative).unlink()
                with self.assertRaisesRegex(ValueError,"dependency"):assemble.platform_root_ok(root)

    def test_gate_rejects_unenabled_service_and_old_capabilities(self):
        platform_root(self.root)
        link=self.root/"etc/systemd/system/multi-user.target.wants/xenova-dom0-manager.service";link.unlink()
        with self.assertRaisesRegex(ValueError,"not enabled"):assemble.platform_root_ok(self.root)
        link.symlink_to("/usr/lib/systemd/system/xenova-dom0-manager.service")
        cap=self.root/"usr/share/xenova/first-install-capabilities.json"
        value=json.loads(cap.read_text());value.pop("dom0_api");cap.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"capabilities"):assemble.platform_root_ok(self.root)

    def test_gate_rejects_missing_worker_enable_and_shared_secret(self):
        platform_root(self.root)
        link=self.root/"etc/systemd/system/multi-user.target.wants/xenova-recovery-worker.service"
        link.unlink()
        with self.assertRaisesRegex(ValueError,"not enabled"):assemble.platform_root_ok(self.root)
        link.symlink_to("/usr/lib/systemd/system/xenova-recovery-worker.service")
        (self.root/"etc/xenova/dom0-manager-token.env").write_text("private board token sentinel")
        with self.assertRaisesRegex(ValueError,"board service token"):assemble.platform_root_ok(self.root)

    def test_gate_rejects_world_readable_environment(self):
        platform_root(self.root)
        (self.root/"etc/xenova/dom0-manager.env").chmod(0o644)
        with self.assertRaisesRegex(ValueError,"root-only"):assemble.platform_root_ok(self.root)

    def test_real_259_loader_accepts_service_and_detects_injected_cycle(self):
        self.root.mkdir();extract(self.root);install_policy(self.root,replay_recipe=True)
        unit=self.root/"usr/lib/systemd/system/xenova-dom0-manager.service"
        unit.write_bytes((payloads.RECIPES/"xenova-dom0-manager/files"/unit.name).read_bytes())
        python=self.root/"usr/bin/python3";python.write_text("#!/bin/sh\nexit 0\n");python.chmod(0o755)
        worker=self.root/"usr/lib/systemd/system/xenova-recovery-worker.service"
        worker.write_bytes((payloads.API/"deploy"/worker.name).read_bytes())
        self.assertNotIn("network-online.target",worker.read_text())
        self.assertIn("network.target",worker.read_text())
        self.assertIn("ReadWritePaths=/srv/xenova /run/xenova /var/lib/xen",worker.read_text())
        for source in (payloads.RECIPES/"xenova-platform/files").glob("*.service"):
            (self.root/"usr/lib/systemd/system"/source.name).write_bytes(source.read_bytes())
        normal=verify(self.root,unit.name,worker.name)
        self.assertEqual(normal.returncode,0,normal.stdout+normal.stderr)
        for text in ("Found ordering cycle","deleted to break ordering cycle","Ordering cycle found, skipping"):
            self.assertNotIn(text,normal.stdout+normal.stderr)
        unit.write_text(unit.read_text().replace("[Unit]\n","[Unit]\nBefore=basic.target\n",1))
        bad=verify(self.root,unit.name)
        self.assertIn("Found ordering cycle",bad.stdout+bad.stderr)


if __name__=="__main__":unittest.main()
