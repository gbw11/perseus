"""Debian display domU 템플릿의 전용 계정 정책 회귀."""
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform/guests/common"))
import guest_display_template as display_guest
import template_recipe as recipe


class DisplayGuestAccountTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-tmp"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "etc/xenova").mkdir(parents=True)
        (self.root / "etc/xenova/template-prepared").write_text("1\n")
        (self.root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        (self.root / "etc/group").write_text("root:x:0:\n")
        (self.root / "etc/shadow").write_text("root:!:19000:0:99999:7:::\n")

    def useradd(self, argv, **kwargs):
        self.assertEqual(argv[:3], ["chroot", str(self.root), "/usr/sbin/useradd"])
        self.assertIn("--system", argv)
        self.assertIn("--no-create-home", argv)
        self.assertEqual(kwargs, {"check": True, "timeout": 60})
        with (self.root / "etc/passwd").open("a") as stream:
            stream.write("xenova-display:x:998:998::/nonexistent:/usr/sbin/nologin\n")
        with (self.root / "etc/group").open("a") as stream:
            stream.write("xenova-display:x:998:\n")
        with (self.root / "etc/shadow").open("a") as stream:
            stream.write("xenova-display:!:20766::::::\n")

    def test_prepare_creates_locked_system_account_without_home(self):
        result = display_guest.prepare(self.root, {"account": "xenova-display"}, command=self.useradd)
        self.assertEqual(result, {"account": "xenova-display", "uid": 998, "gid": 998,
                                  "home": "/nonexistent", "shell": "/usr/sbin/nologin",
                                  "password_locked": True})
        self.assertFalse((self.root / "nonexistent").exists())

    def test_existing_account_or_instance_is_rejected(self):
        self.useradd(["chroot", str(self.root), "/usr/sbin/useradd", "--system", "--user-group",
                      "--home-dir", "/nonexistent", "--no-create-home", "--shell",
                      "/usr/sbin/nologin", "--password", "!", "xenova-display"],
                     check=True, timeout=60)
        with self.assertRaisesRegex(ValueError, "already exists"):
            display_guest.prepare(self.root, {"account": "xenova-display"}, command=self.useradd)
        (self.root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        (self.root / "etc/group").write_text("root:x:0:\n")
        (self.root / "etc/shadow").write_text("root:!:19000:0:99999:7:::\n")
        (self.root / "etc/xenova/instance-id").write_text("fixture\n")
        with self.assertRaisesRegex(ValueError, "new prepared template"):
            display_guest.prepare(self.root, {"account": "xenova-display"}, command=self.useradd)

    def test_recipe_policy_is_source_locked(self):
        source = self.root / "recipe"
        (source / "files").mkdir(parents=True)
        (source / "files/motd").write_text("fixture\n")
        meta = {"schema": 1, "source_id": "debian-13-20261015", "version": "1.0.6",
                "files": [{"source": "files/motd", "target": "etc/motd", "mode": 420}],
                "guest_display": {"account": "xenova-display"}}
        path = source / "recipe.json"
        path.write_text(json.dumps(meta))
        info = recipe.source_info(path.resolve(), meta["source_id"])
        self.assertEqual(info["recipe"]["guest_display"], {"account": "xenova-display"})
        self.assertIn("platform/guests/common/guest_display_template.py",
                      {Path(item).as_posix() for item in info["preparer_files"]})
        meta["guest_display"]["account"] = "root"
        path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "display guest account policy"):
            recipe.source_info(path.resolve(), meta["source_id"])


if __name__ == "__main__":
    unittest.main()
