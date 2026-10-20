"""실제 소스·캐시를 건드리지 않는 setup-yocto.sh 안전 동작 회귀 검사.

작은 로컬 Git fixture만 사용한다. BitBake 파싱·이미지 빌드 검증은 별도로 수행한다.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
REPOSITORIES = (
    "openembedded-core", "bitbake", "meta-yocto",
    "meta-raspberrypi", "meta-virtualization", "meta-openembedded",
)


class SetupSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xenova-yocto-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.workspace = self.base / "workspace"
        (self.project / "platform/scripts").mkdir(parents=True)
        layer = self.project / "platform/yocto/meta-xenova/conf"
        layer.mkdir(parents=True)
        (layer / "layer.conf").write_text("# fixture\n")
        config = self.project / "platform/yocto/config"
        config.mkdir()
        (config / "local.conf.fragment").write_text("# fixture\n")
        self.script = self.project / "platform/scripts/setup-yocto.sh"
        shutil.copy2(ROOT / "platform/scripts/setup-yocto.sh", self.script)
        prototype = self.base / "prototype"
        self.git("init", "-q", str(prototype))
        self.git("-C", str(prototype), "-c", "user.name=Fixture",
                 "-c", "user.email=fixture@example.invalid",
                 "commit", "-q", "--allow-empty", "-m", "fixture")
        sha = self.git("-C", str(prototype), "rev-parse", "HEAD").strip()
        entries = []
        for name in REPOSITORIES:
            dest = self.workspace / "layers" / name
            shutil.copytree(prototype, dest)
            url = f"https://example.org/{name}"
            self.git("-C", str(dest), "remote", "add", "origin", url)
            entries.append(f"{name} {url} {sha}\n")
        self.lock = self.project / "platform/yocto/layers.lock"
        self.lock.write_text("".join(entries))
        self.environment = {
            **os.environ, "YOCTO_WORKSPACE": str(self.workspace),
            "YOCTO_BUILD_DIR": str(self.workspace / "build-test"),
        }

    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE)

    def run_setup(self, *args, **environment):
        return subprocess.run(
            ["bash", str(self.script), *args], text=True, capture_output=True,
            env={**self.environment, **environment},
        )

    def test_clean_check_creates_no_build(self):
        result = self.run_setup("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[OK]"), 6)
        self.assertFalse((self.workspace / "build-test").exists())

    def test_missing_check_creates_no_workspace(self):
        missing = self.base / "absent"
        result = self.run_setup("--check", YOCTO_WORKSPACE=str(missing))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())

    def test_dirty_checkout_is_preserved(self):
        change = self.workspace / "layers/bitbake/local-change"
        change.write_text("keep this\n")
        result = self.run_setup("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local changes", result.stderr)
        self.assertEqual(change.read_text(), "keep this\n")

    def test_wrong_commit_is_preserved(self):
        repo = self.workspace / "layers/bitbake"
        self.git("-C", str(repo), "-c", "user.name=Fixture",
                 "-c", "user.email=fixture@example.invalid",
                 "commit", "-q", "--allow-empty", "-m", "different")
        before = self.git("-C", str(repo), "rev-parse", "HEAD")
        result = self.run_setup("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to change", result.stderr)
        self.assertEqual(self.git("-C", str(repo), "rev-parse", "HEAD"), before)

    def test_remote_mismatch_does_not_print_url(self):
        wrong = "https://example.org/private-checkout"
        self.git("-C", str(self.workspace / "layers/bitbake"),
                 "remote", "set-url", "origin", wrong)
        result = self.run_setup("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin differs", result.stderr)
        self.assertNotIn(wrong, result.stdout + result.stderr)

    def test_duplicate_lock_rejected_before_clone(self):
        self.lock.write_text(self.lock.read_text() + self.lock.read_text().splitlines()[0] + "\n")
        result = self.run_setup("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr)

    def test_unmanaged_build_preserves_conf(self):
        build = self.workspace / "build-test"
        (build / "conf").mkdir(parents=True)
        conf = build / "conf/local.conf"
        conf.write_text('MACHINE = "original"\n')
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not owned", result.stderr)
        self.assertEqual(conf.read_text(), 'MACHINE = "original"\n')

    def test_extra_argument_rejected(self):
        result = self.run_setup("--check", "unexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at most one option", result.stderr)


if __name__ == "__main__":
    unittest.main()
