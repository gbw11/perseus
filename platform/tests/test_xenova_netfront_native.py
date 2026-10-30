"""Compile production netfront/grant/shutdown/Store code; mock only kernel/transport boundaries."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"platform/guests/zephyr"
RUNTIME=BASE/"xen-runtime"
STORE=BASE/"xen-store"
HOST=ROOT/"platform/tests/native-runtime"


class NativeRuntimeTest(unittest.TestCase):
    def run_case(self,name,sanitize):
        cc=shutil.which("cc")
        self.assertIsNotNone(cc,"existing host C compiler required")
        with tempfile.TemporaryDirectory(prefix="xenova-netfront-") as tmp:
            tmp=Path(tmp)
            cmd=[cc,"-std=c11","-D_POSIX_C_SOURCE=200809L","-Wall","-Wextra","-Werror","-g",
                 "-I"+str(RUNTIME/"include")]
            if sanitize:
                cmd+=["-fsanitize=address,undefined","-fno-omit-frame-pointer","-no-pie"]
            if name=="shared_store":
                peer=(STORE/"tests/test_store.c").read_text()
                self.assertIn("static void basic(void)",peer)
                (tmp/"xenova_peer.inc").write_text(peer.split("static void basic(void)")[0])
                cmd+=["-pthread","-I"+str(tmp),"-I"+str(HOST/"host"),"-I"+str(STORE/"include"),
                      str(RUNTIME/"src/runtime.c"),str(HOST/"test_shared_store.c")]
                cmd += [str(STORE/"src"/n) for n in ("xenstore.c","xenbus_vif.c","xenstore_link.c")]
            else:
                cmd += [str(RUNTIME/"src"/(name+".c")),str(RUNTIME/"tests"/("test_"+name+".c"))]
            cmd += ["-o",str(tmp/"test")]
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(tmp/"test")],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn("PASS",result.stdout)
            if os.environ.get("XENOVA_TEST_LOG_DIR"):
                log=Path(os.environ["XENOVA_TEST_LOG_DIR"]);log.mkdir(parents=True,exist_ok=True)
                (log/(name+("-sanitized" if sanitize else "-native")+".log")).write_text(result.stdout+result.stderr)

    def test_production_native(self):
        for name in ("netfront_ring","grant_pool","shutdown_flow","shared_store"):
            with self.subTest(component=name):self.run_case(name,False)

    def test_production_asan_ubsan(self):
        for name in ("netfront_ring","grant_pool","shutdown_flow","shared_store"):
            with self.subTest(component=name):self.run_case(name,True)
