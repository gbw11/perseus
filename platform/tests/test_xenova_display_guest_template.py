"""Debian display domU 템플릿의 전용 계정 정책 회귀."""
import http.client
import importlib
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
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
        self.web = self.root / "web-input"
        (self.web / "assets").mkdir(parents=True)
        (self.web / "index.html").write_text('<div id="root"></div>')
        (self.web / "assets/index-test.js").write_text('const route="/display/mobile";')
        self.enabled = []
        self.owned = []

    def enable_unit(self, root, unit):
        self.assertEqual(Path(root), self.root)
        self.assertEqual(unit, "xenova-guest-display.target")
        self.assertTrue((self.root / "usr/lib/systemd/system" / unit).is_file())
        self.enabled.append(unit)

    def set_owner(self, path):
        self.owned.append("/" + Path(path).relative_to(self.root).as_posix())

    @staticmethod
    def mode_of(path):
        return 0o755 if Path(path).is_dir() else 0o644

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
        result = display_guest.prepare(self.root, display_guest.POLICY,
                                       web_assets=self.web.resolve(), web_revision="abcdef1",
                                       command=self.useradd, enable_unit=self.enable_unit,
                                       set_owner=self.set_owner, mode_of=self.mode_of)
        self.assertEqual(result["account"], {"account": "xenova-display", "uid": 998, "gid": 998,
                                             "home": "/nonexistent", "shell": "/usr/sbin/nologin",
                                             "password_locked": True})
        self.assertEqual(result["python_root"], "/opt/xenova/display-guest")
        self.assertEqual(tuple(result["python_modules"]), display_guest.MODULES)
        target = self.root / "opt/xenova/display-guest/xenova_display"
        for name in display_guest.MODULES:
            source = ROOT / "platform/services/display/router/xenova_display" / name
            self.assertEqual((target / name).read_bytes(), source.read_bytes())
            self.assertEqual(result["python_modules"][name]["sha256"],
                             hashlib.sha256(source.read_bytes()).hexdigest())
        installed_web = self.root / "opt/xenova/display-web"
        self.assertEqual((installed_web / "index.html").read_text(), '<div id="root"></div>')
        self.assertEqual(result["web"]["root"], "/opt/xenova/display-web")
        self.assertEqual(result["web"]["revision"], "abcdef1")
        self.assertEqual(set(result["web"]["files"]), {"index.html", "assets/index-test.js"})
        self.assertRegex(result["web"]["tree_sha256"], r"^[0-9a-f]{64}$")
        config = self.root / "etc/xenova/guest-display-domu.json"
        self.assertEqual(json.loads(config.read_text()), {
            "web_root": "/opt/xenova/display-web", "bind": "192.168.77.20", "app_port": 8086,
            "allowed_clients": ["192.168.77.1"], "data_host": "192.168.77.1", "data_port": 8085,
            "data_token_file": "/etc/xenova/guest-display/guest-data.token", "page": "/display/mobile",
        })
        self.assertEqual(result["config"]["path"], "/etc/xenova/guest-display-domu.json")
        self.assertEqual(result["config"]["mode"], "0644")
        unit = self.root / "usr/lib/systemd/system/xenova-guest-display-app.service"
        directives = unit.read_text().splitlines()
        self.assertIn("User=xenova-display", directives)
        self.assertIn("Environment=PYTHONPATH=/opt/xenova/display-guest", directives)
        self.assertIn("Restart=on-failure", directives)
        self.assertEqual(result["service"]["path"],
                         "/usr/lib/systemd/system/xenova-guest-display-app.service")
        target = self.root / "usr/lib/systemd/system/xenova-guest-display.target"
        self.assertIn("Wants=xenova-guest-display-app.service", target.read_text().splitlines())
        self.assertEqual(self.enabled, ["xenova-guest-display.target"])
        self.assertEqual(result["target"]["enabled_by"],
                         "/etc/systemd/system/multi-user.target.wants/xenova-guest-display.target")
        permissions = result["permissions"]
        self.assertEqual(permissions["owner"], "root:root")
        self.assertEqual(permissions["token"], "deferred-to-credential-provisioning")
        self.assertEqual(sorted(self.owned), permissions["checked"])
        self.assertEqual(permissions["directory_mode"], "0755")
        self.assertEqual(permissions["file_mode"], "0644")
        self.assertFalse((self.root / "nonexistent").exists())

    def test_existing_account_or_instance_is_rejected(self):
        self.useradd(["chroot", str(self.root), "/usr/sbin/useradd", "--system", "--user-group",
                      "--home-dir", "/nonexistent", "--no-create-home", "--shell",
                      "/usr/sbin/nologin", "--password", "!", "xenova-display"],
                     check=True, timeout=60)
        with self.assertRaisesRegex(ValueError, "already exists"):
            display_guest.prepare(self.root, display_guest.POLICY,
                                   web_assets=self.web.resolve(), web_revision="abcdef1",
                                   command=self.useradd)
        (self.root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        (self.root / "etc/group").write_text("root:x:0:\n")
        (self.root / "etc/shadow").write_text("root:!:19000:0:99999:7:::\n")
        (self.root / "etc/xenova/instance-id").write_text("fixture\n")
        with self.assertRaisesRegex(ValueError, "new prepared template"):
            display_guest.prepare(self.root, display_guest.POLICY,
                                   web_assets=self.web.resolve(), web_revision="abcdef1",
                                   command=self.useradd)

    def test_recipe_policy_is_source_locked(self):
        source = self.root / "recipe"
        (source / "files").mkdir(parents=True)
        (source / "files/motd").write_text("fixture\n")
        meta = {"schema": 1, "source_id": "debian-13-20261015", "version": "1.0.6",
                "files": [{"source": "files/motd", "target": "etc/motd", "mode": 420}],
                "guest_display": {"account": "xenova-display",
                                  "python_modules": list(display_guest.MODULES),
                                  "web_assets": "external-vm-screens-dist",
                                  "config": "/etc/xenova/guest-display-domu.json",
                                  "service": "xenova-guest-display-app.service",
                                  "target": "xenova-guest-display.target"}}
        path = source / "recipe.json"
        path.write_text(json.dumps(meta))
        info = recipe.source_info(path.resolve(), meta["source_id"])
        self.assertEqual(info["recipe"]["guest_display"], display_guest.POLICY)
        self.assertIn("platform/guests/common/guest_display_template.py",
                      {Path(item).as_posix() for item in info["preparer_files"]})
        locked = {Path(item).as_posix() for item in info["preparer_files"]}
        for name in display_guest.MODULES:
            self.assertIn("platform/services/display/router/xenova_display/" + name, locked)
        self.assertIn("platform/services/display/router/deploy/guest-display/domu/guest-display-domu.json.example",
                      locked)
        self.assertIn("platform/services/display/router/deploy/guest-display/domu/xenova-guest-display-app.service",
                      locked)
        self.assertIn("platform/services/display/router/deploy/guest-display/domu/xenova-guest-display.target",
                      locked)
        meta["guest_display"]["account"] = "root"
        path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "display guest policy"):
            recipe.source_info(path.resolve(), meta["source_id"])

    def test_web_assets_reject_missing_build_or_unpinned_revision(self):
        with self.assertRaisesRegex(ValueError, "canonical dist directory"):
            display_guest.install_web_assets(self.root, None, None)
        with self.assertRaisesRegex(ValueError, "canonical dist directory"):
            display_guest.install_web_assets(self.root, self.web.resolve(), "branch-name")
        (self.web / "index.html").unlink()
        with self.assertRaisesRegex(ValueError, "index.html"):
            display_guest.install_web_assets(self.root, self.web.resolve(), "abcdef1")

    def test_installed_guest_app_serves_screen_assets_and_dom0_data(self):
        display_guest.prepare(self.root, display_guest.POLICY,
                              web_assets=self.web.resolve(), web_revision="abcdef1",
                              command=self.useradd, enable_unit=self.enable_unit,
                              set_owner=self.set_owner, mode_of=self.mode_of)
        installed = self.root / "opt/xenova/display-guest"
        sys.path.insert(0, str(installed))
        try:
            app = importlib.import_module("xenova_display.guest_app")
            calls = []

            class Response:
                status = 200

                @staticmethod
                def read(_limit):
                    return json.dumps({"schema": 1, "mode": "AUTONOMOUS"}).encode()

                @staticmethod
                def getheader(name, _default=""):
                    return "application/json" if name == "Content-Type" else _default

            class Dom0:
                def __init__(self, host, port, timeout):
                    calls.append(("connect", host, port, timeout))

                def request(self, method, path, headers):
                    calls.append(("request", method, path, headers))

                @staticmethod
                def getresponse():
                    return Response()

                @staticmethod
                def close():
                    pass

            config = {
                "web_root": self.root / "opt/xenova/display-web",
                "bind": "127.0.0.1", "allowed_clients": ["127.0.0.1"], "app_port": 0,
                "data_host": "127.0.0.1", "data_port": 18085,
            }
            server = app.serve(config, "fixture-token", connect=Dom0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/display/mobile")
                screen = connection.getresponse()
                self.assertEqual((screen.status, screen.read()), (200, b'<div id="root"></div>'))
                connection.request("GET", "/assets/index-test.js")
                asset = connection.getresponse()
                self.assertEqual(asset.status, 200)
                self.assertIn(b'/display/mobile', asset.read())
                connection.request("GET", "/api/v1/display/presentation")
                data = connection.getresponse()
                self.assertEqual(data.status, 200)
                self.assertEqual(json.loads(data.read()), {"schema": 1, "mode": "AUTONOMOUS"})
                connection.request("GET", "/secret")
                self.assertEqual(connection.getresponse().status, 404)
                connection.request("POST", "/api/v1/display/presentation")
                self.assertEqual(connection.getresponse().status, 405)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            self.assertIn(("connect", "127.0.0.1", 18085, 2), calls)
            self.assertIn(("request", "GET", "/api/v1/display/presentation",
                           {"Authorization": "Bearer fixture-token"}), calls)
        finally:
            sys.path.remove(str(installed))
            for name in list(sys.modules):
                if name == "xenova_display" or name.startswith("xenova_display."):
                    sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
