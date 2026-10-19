"""G1 production C 구현과 사용자 시험 도구의 경계를 실제 임시 파일로 검사한다."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT/"platform/guests/zephyr/xen-store"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = load("g1_lab", ROOT/"scripts/xenova-xenstore-g1-lab.py")
builder = load("g1_builder", ROOT/"scripts/build-xenova-xenstore-g1.py")
FILES = ROOT/"platform/yocto/meta-xenova/recipes-core/xenova-platform/files"
lab.platform(FILES)
from xenova_vm import Platform

IDENTITY = "cccc0000-1111-4222-8333-123456789abc"
MAC = "02:16:3e:aa:bb:cc"


class NativeCoreTest(unittest.TestCase):
    def run_core(self, sanitize):
        compiler = shutil.which("cc")
        if not compiler:
            self.fail("G1 regression requires the existing host C compiler")
        with tempfile.TemporaryDirectory(prefix="xenova-xs-host-") as temp:
            binary = Path(temp)/"test-store"
            command = [compiler,"-std=c11","-D_POSIX_C_SOURCE=200809L",
                       "-Wall","-Wextra","-Werror","-g","-I"+str(MODULE/"include"),
                       "-I"+str(MODULE/"tests/host")]
            if sanitize:
                command += ["-fsanitize=address,undefined","-fno-omit-frame-pointer","-no-pie"]
            command += [str(MODULE/"src"/name) for name in
                        ("xenstore.c","xenbus_vif.c","xenstore_link.c")]
            command += [str(MODULE/"tests/test_store.c"),"-o",str(binary)]
            compile_result = subprocess.run(command, text=True, capture_output=True, timeout=60)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout+compile_result.stderr)
            result = subprocess.run([str(binary)],text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn("PASS 61 production-code cases",result.stdout)
            if os.environ.get("XENOVA_TEST_LOG_DIR"):
                directory=Path(os.environ["XENOVA_TEST_LOG_DIR"])
                directory.mkdir(parents=True,exist_ok=True)
                suffix="sanitized" if sanitize else "native"
                (directory/f"xenstore-g1-{suffix}.log").write_text(result.stdout+result.stderr)
            return result.stdout

    def test_production_core(self):
        self.run_core(False)

    def test_production_core_asan_ubsan(self):
        self.run_core(True)

    def test_production_adapter_pending_replay_native_and_sanitized(self):
        for sanitize in (False, True):
            with self.subTest(sanitize=sanitize), tempfile.TemporaryDirectory() as temp:
                binary=Path(temp)/"adapter"
                command=[shutil.which("cc"),"-std=c11","-D_POSIX_C_SOURCE=200809L","-Wall","-Wextra","-Werror","-g",
                         "-I"+str(MODULE/"tests/host"),"-I"+str(MODULE/"include")]
                if sanitize:
                    command += ["-fsanitize=address,undefined","-fno-omit-frame-pointer","-no-pie"]
                command += [str(MODULE/"src/xenstore.c"),str(MODULE/"src/xenstore_link.c"),
                            str(MODULE/"tests/test_adapter.c"),"-o",str(binary)]
                compile_result=subprocess.run(command,text=True,capture_output=True,timeout=60)
                self.assertEqual(compile_result.returncode,0,compile_result.stdout+compile_result.stderr)
                result=subprocess.run([str(binary)],text=True,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn("PASS masked pending replay and second event",result.stdout)
                if os.environ.get("XENOVA_TEST_LOG_DIR"):
                    directory=Path(os.environ["XENOVA_TEST_LOG_DIR"])
                    directory.mkdir(parents=True,exist_ok=True)
                    (directory/("adapter-sanitized.log" if sanitize else "adapter-native.log")).write_text(result.stdout+result.stderr)

    def test_frozen_public_abi(self):
        lock=json.loads((MODULE/"references.lock.json").read_text())
        self.assertEqual(lock["xen"]["revision"],"1c72306b1f0b4dc533954f678361044f8083dccc")
        source=json.loads((ROOT/"platform/guests/zephyr/source.lock.json").read_text())
        self.assertEqual(lock["zephyr"]["revision"],source["commit"])
        self.assertEqual(lock["sdk"]["version"],source["sdk_version"])
        for name, entry in lock["xen"]["abi_files"].items():
            content=(MODULE/"include/xenova/abi"/name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(),entry["sha256"])
            self.assertIn(b"SPDX-License-Identifier: MIT",content)

    def test_target_verifier_rejects_non_elf(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/"bad.elf";file.write_bytes(b"not elf")
            with self.assertRaises(ValueError):
                builder.verify_elf(file)


class CommandLogTest(unittest.TestCase):
    def run_failure(self, timeout):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);script=root/"peer.py"
            ready=root/"ready"
            script.write_text("import sys,time,pathlib\nprint('STDOUT-MARKER',flush=True)\n"
                              "sys.stderr.write('E'*6000+'STDERR-END');sys.stderr.flush()\n"+
                              ("pathlib.Path(sys.argv[1]).write_text('ready')\ntime.sleep(10)\n"
                               if timeout else "sys.exit(7)\n"))
            argv=[__import__("sys").executable,"-u",str(script),str(ready)]
            # Process startup is outside this log-preservation boundary. Trigger the real
            # TimeoutExpired only after both output streams have been flushed.
            def ready_then_timeout(args, **kwargs):
                with subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as child:
                    deadline=time.monotonic()+5
                    while not ready.exists() and child.poll() is None and time.monotonic()<deadline:
                        time.sleep(.005)
                    if not ready.exists():
                        child.kill();child.communicate()
                        self.fail("marker peer did not become ready")
                    try:
                        child.communicate(timeout=kwargs["timeout"])
                    except subprocess.TimeoutExpired as error:
                        child.kill()
                        error.output,error.stderr=child.communicate()
                        raise
                    self.fail("timeout peer unexpectedly exited")
            boundary=patch.object(lab.subprocess,"run",side_effect=ready_then_timeout) if timeout else contextlib.nullcontext()
            with boundary, self.assertRaises(RuntimeError) as caught:
                lab.run(argv,timeout=0.3 if timeout else 5,log_directory=root/"logs")
            records=list((root/"logs").iterdir());self.assertEqual(len(records),1)
            record=records[0]
            self.assertEqual((record/"stdout").read_bytes(),b"STDOUT-MARKER\n")
            self.assertEqual((record/"stderr").read_bytes(),b"E"*6000+b"STDERR-END")
            metadata=json.loads((record/"result.json").read_text())
            self.assertEqual(metadata["argv"],argv)
            self.assertEqual(metadata["timed_out"],timeout)
            self.assertEqual(metadata["returncode"],None if timeout else 7)
            self.assertIn(str(record),str(caught.exception))
            self.assertEqual(record.stat().st_mode & 0o777,0o700)
            for file in record.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777,0o600)

    def test_nonzero_full_stdout_stderr_preserved(self):
        self.run_failure(False)

    def test_timeout_partial_stdout_stderr_preserved(self):
        self.run_failure(True)

    def test_success_output_and_returncode_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(lab.run([__import__("sys").executable,"-c","print('ok')"],
                                     log_directory=root/"logs"),"ok")
            record=next((root/"logs").iterdir())
            self.assertEqual(json.loads((record/"result.json").read_text())["returncode"],0)
            self.assertEqual((record/"stdout").read_bytes(),b"ok\n")

    def test_log_parent_symlink_blocks_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);alias=root/"alias";alias.symlink_to(root,target_is_directory=True)
            with patch.object(lab.subprocess,"run") as command,self.assertRaises(ValueError):
                lab.run(["xl","info"],log_directory=alias/"logs")
            command.assert_not_called()


class XenBoundary(Platform):
    def __init__(self, root):
        super().__init__(root/"data",root/"run")
        self.rows=[]
        (self.data/"instances").mkdir(parents=True);self.runtime.mkdir()
        self.snapshot_override=None

    def check_xen(self):
        pass

    def check_network(self):
        pass

    def domains(self):
        return self.rows

    def removal_snapshot(self, deadline):
        if self.snapshot_override is not None:
            return self.snapshot_override
        return {r["domid"]:{"domid":r["domid"],"name":r["config"]["c_info"]["name"],
                           "uuid":r["config"]["c_info"]["uuid"]} for r in self.rows}

    def removal_observation(self, expected, deadline):
        return [self.removal_snapshot(deadline),self.removal_snapshot(deadline)]


class LabTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="xenova-g1-lab-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.p=XenBoundary(self.root)
        self.firmware=self.root/"firmware";self.firmware.mkdir()
        files={}
        for name in ("zephyr.bin","zephyr.elf","zephyr.map","zephyr.config","zephyr.dts",
                     "ZEPHYR-LICENSE","XEN-ABI-LICENSE"):
            file=self.firmware/name;file.write_bytes(b"small fixture")
            files[name]={"sha256":hashlib.sha256(file.read_bytes()).hexdigest(),"bytes":file.stat().st_size}
        (self.firmware/"g1-build.json").write_text(json.dumps(
            {"app":"xenova-xenstore-g1","files":files}))
        self.args=type("Args",(),{"firmware":self.firmware,"directory":self.root/"lab",
                                "name":"xg1-test","uuid":IDENTITY,"mac":MAC})()

    def prepare(self):
        return lab.prepare(self.p,self.args)

    def running(self):
        self.p.rows=[{"domid":42,"config":{"c_info":{"name":"xg1-test","uuid":IDENTITY},
                                          "nics":[{"mac":MAC}]}}]

    def invoke(self, *args):
        output=io.StringIO()
        with patch.object(lab,"platform",return_value=self.p),contextlib.redirect_stdout(output):
            lab.main(list(args))
        return output.getvalue()

    def test_actual_platform_constructor_and_dhcp_inventory(self):
        self.assertIsInstance(self.p,Platform)
        (self.p.runtime/"dhcp-hosts").write_text(MAC+",fixture,192.168.77.30\n")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.args.directory.exists())

    def test_platform_loader_fresh_process_with_published_dependency_layout(self):
        runtime=self.root/"runtime";runtime.mkdir()
        for name in ("xenova_vm.py","xenova_runtime.py","xenova_zephyr.py"):
            shutil.copyfile(FILES/name,runtime/name)
        for name in ("first_install_contract.py","artifact_io.py"):
            shutil.copyfile(ROOT/"platform/first-install"/name,runtime/name)
        script=self.root/"standalone.py"
        shutil.copyfile(ROOT/"scripts/xenova-xenstore-g1-lab.py",script)
        code=("import importlib.util;from pathlib import Path;"
              f"s=importlib.util.spec_from_file_location('lab',{str(script)!r});"
              "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
              f"p=m.platform(Path({str(runtime)!r}));"
              "assert p.command is m.run;assert str(p.runtime)=='/run/xenova';assert str(p.data)=='/srv/xenova'")
        result=subprocess.run([__import__("sys").executable,"-B","-c",code],
                              env={**os.environ,"PYTHONPATH":""},
                              text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_prepare_one_vif_cfg_and_no_registry_write(self):
        self.prepare()
        text=(self.args.directory/"test.cfg").read_text()
        self.assertIn('vif = ["mac='+MAC+',bridge=xenbr0"]',text)
        self.assertIn("memory = 16",text)
        for forbidden in ("disk =", "ip=", "tx-ring-ref", "rx-ring-ref", "state="):
            self.assertNotIn(forbidden,text)
        self.assertEqual(list((self.p.data/"instances").iterdir()),[])
        self.assertEqual(lab.load(self.args.directory)["uuid"],IDENTITY)

    def test_reject_live_name_uuid_mac_collision(self):
        self.running()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.args.directory.exists())

    def test_retired_registry_mac_is_reserved(self):
        directory=self.p.data/"instances/.retired-vm-g1";directory.mkdir()
        record={"name":"old","uuid":"dddd0000-1111-4222-8333-123456789abc","mac":MAC}
        (directory/"instance.json").write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(json.loads((directory/"instance.json").read_text()),record)

    def test_firmware_changed_rejected_before_cfg(self):
        (self.firmware/"zephyr.bin").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.args.directory.exists())

    def test_parent_symlink_rejected(self):
        alias=self.root/"alias";alias.symlink_to(self.firmware,target_is_directory=True)
        self.args.firmware=alias
        with self.assertRaises(ValueError):
            self.prepare()

    def test_invalid_name_mac_uuid(self):
        for name,identity,mac in [("zephyr-drive-a",IDENTITY,MAC),
                                  ("xg1-test",IDENTITY,"ff:ff:ff:ff:ff:ff"),
                                  ("xg1-test","../other",MAC)]:
            with self.assertRaises(ValueError):
                lab.identities(name,identity,mac)

    def test_tampered_cfg_rejected(self):
        self.prepare()
        with (self.args.directory/"test.cfg").open("a") as stream:
            stream.write('\ndisk = ["unsafe"]\n')
        with self.assertRaises(ValueError):
            lab.load(self.args.directory)

    def test_reused_domid_prevents_control(self):
        metadata=self.prepare();self.running()
        self.p.snapshot_override={42:{"domid":42,"name":"other","uuid":IDENTITY}}
        with patch.object(lab,"run") as command,self.assertRaises(ValueError):
            lab.target(self.p,metadata)
        command.assert_not_called()

    def test_registered_vm_refused_by_lab_control(self):
        metadata=self.prepare();self.running()
        directory=self.p.data/"instances/vm-owned";directory.mkdir()
        (directory/"instance.json").write_text(json.dumps(
            {"name":"vm-owned","uuid":IDENTITY,"mac":MAC}))
        with self.assertRaises(ValueError):
            lab.target(self.p,metadata)

    def test_signal_cannot_write_vif_or_another_domain(self):
        self.prepare();self.running()
        for path in ("/local/domain/42/device/vif/0/state",
                     "/local/domain/99/data/xenova-g1-ab/control",
                     "/local/domain/42/data/xenova-g1-ab/../state"):
            with patch.object(lab,"run") as command,self.assertRaises(ValueError):
                self.invoke("signal","--directory",str(self.args.directory),"--execute",
                            "--path",path,"--value","dom0-1")
            command.assert_not_called()

    def test_signal_only_owned_test_node(self):
        self.prepare();self.running()
        with patch.object(lab,"run",return_value="fixture") as command:
            self.invoke("signal","--directory",str(self.args.directory),"--execute",
                        "--path","/local/domain/42/data/xenova-g1-ab/control","--value","dom0-1")
        self.assertEqual(command.call_args_list[1].args[0],
                         ["xenstore-write","/local/domain/42/data/xenova-g1-ab/control","dom0-1"])

    def test_cleanup_confirms_uuid_and_preserves_firmware(self):
        self.prepare();self.running()
        before={p.name:p.read_bytes() for p in self.firmware.iterdir()}
        def command(argv, **kwargs):
            self.assertEqual(argv,["xl","destroy","42"]);self.p.rows=[];return ""
        with patch.object(lab,"run",side_effect=command):
            self.invoke("cleanup","--directory",str(self.args.directory),"--execute",
                        "--confirm-uuid",IDENTITY,"--remove-config")
        self.assertFalse(self.args.directory.exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.firmware.iterdir()})

    def test_cleanup_wrong_confirmation_does_nothing(self):
        self.prepare();self.running()
        with patch.object(lab,"run") as command,self.assertRaises(ValueError):
            self.invoke("cleanup","--directory",str(self.args.directory),"--execute",
                        "--confirm-uuid","wrong")
        command.assert_not_called()
        self.assertTrue((self.args.directory/"test.cfg").exists())


if __name__ == "__main__":
    unittest.main()
