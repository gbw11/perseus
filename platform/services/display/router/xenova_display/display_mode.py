"""351: one-shot native HDMI/mobile selection. No daemon or vehicle commands.

Only the two display services are started/stopped. Shared feed, VM, Gateway,
MQTT, networking, credentials and unit enablement are never changed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import time

HDMI = "xenova-display-hdmi.service"
MOBILE = "xenova-display-mobile.service"
FEED = "xenova-display-feed.service"
UNITS = (HDMI, MOBILE, FEED)
RUNTIME = Path("/run/xenova-display-mode")
MARKER = RUNTIME / "mobile"
GUARD = Path("/usr/lib/systemd/system/xenova-display-hdmi.service.d/60-display-mode.conf")
LEGACY = Path("/etc/xenova/display-mobile-first")
RENDER_STATUS = Path("/run/xenova-display-graphics/client/display-runtime.json")


class ModeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def regular(path, *, private=False, limit=65536):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & (0o077 if private else 0o022)
            or info.st_size > limit or path.resolve() != path):
        raise ModeError("UNSAFE_CONFIGURATION")
    return path


def present(path):
    # Broken symlinks also count as present and must never be overwritten.
    return path.exists() or path.is_symlink()


class Systemd:
    def query(self):
        result = subprocess.run(["/usr/bin/systemctl", "show", *UNITS,
            "--property=Id,LoadState,ActiveState,ExecStart,DropInPaths"],
            check=True, capture_output=True, text=True, timeout=5)
        return {item["Id"]: item for block in result.stdout.strip().split("\n\n")
                if "Id" in (item := dict(line.split("=", 1) for line in block.splitlines() if "=" in line))}

    def change(self, action, unit):
        if action not in {"start", "stop"} or unit not in {HDMI, MOBILE}:
            raise ModeError("UNSUPPORTED_SERVICE_ACTION")
        subprocess.run(["/usr/bin/systemctl", "--no-block", action, unit],
                       check=True, capture_output=True, timeout=5)


class Selector:
    def __init__(self, system=None, *, runtime=RUNTIME, guard=GUARD, legacy=LEGACY,
                 render_status=RENDER_STATUS, endpoint=Path("/run/xenova-display-mobile/endpoint.json"),
                 clock=time.monotonic, sleep=time.sleep):
        self.system = system or Systemd()
        self.runtime, self.guard, self.legacy = runtime, guard, legacy
        self.marker = runtime / "mobile"
        self.render_status, self.endpoint = render_status, endpoint
        self.clock, self.sleep = clock, sleep

    def selected(self):
        if present(self.marker):
            if regular(self.marker, private=True, limit=7).read_bytes() != b"mobile\n":
                raise ModeError("FOREIGN_MODE_MARKER")
            return "mobile"
        return "hdmi"

    def mark(self, mode):
        old = self.selected()
        if old == mode:
            return
        if mode == "hdmi":
            self.marker.unlink()
        else:
            fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"mobile\n")

    def check(self, info):
        if present(self.legacy):
            # Do not silently remove a previous deployment's persistent policy.
            raise ModeError("LEGACY_MOBILE_FIRST_REQUIRES_MIGRATION")
        guard = regular(self.guard, limit=4096).read_text()
        expected = "ConditionPathExists=!/run/xenova-display-mode/mobile"
        lines = [s.strip() for s in guard.splitlines() if s.strip() and not s.lstrip().startswith("#")]
        if lines != ["[Unit]", expected] or str(self.guard) not in info.get(HDMI, {}).get("DropInPaths", "").split():
            raise ModeError("MODE_GUARD_NOT_LOADED")
        for unit, module in ((HDMI, "xenova_display.native_session"), (MOBILE, "xenova_display.mobile_access")):
            if (info.get(unit, {}).get("LoadState") != "loaded"
                    or module not in info[unit].get("ExecStart", "")):
                raise ModeError("NATIVE_DISPLAY_UNITS_REQUIRED")
            if info[unit].get("ActiveState") not in {"active", "inactive"}:
                raise ModeError("DISPLAY_SERVICE_NOT_SETTLED")
        if info.get(FEED, {}).get("ActiveState") != "active":
            raise ModeError("SHARED_FEED_NOT_ACTIVE")

    def wait(self, unit, state, seconds=50):
        deadline = self.clock() + seconds
        while True:
            info = self.system.query().get(unit, {})
            if info.get("ActiveState") == state:
                return
            if (state == "active" and info.get("ActiveState") == "failed") or self.clock() >= deadline:
                raise ModeError("DISPLAY_SERVICE_TRANSITION_FAILED")
            self.sleep(.25)

    def hdmi_ready(self):
        try:
            path = self.render_status
            meta = path.lstat()
            if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o022 or meta.st_size > 65536 or path.resolve() != path:
                return False
            value = json.loads(path.read_text())
            now = self.clock()
            if (value.get("schema_version") != 1 or value.get("backend") != "x11"
                    or not 0 <= now - value["updated_monotonic"] <= 3):
                return False
            outputs = value["outputs"]
            return set(outputs) == {"monitor-1", "monitor-2"} and all(
                o.get("status") == "mapped" and o.get("renderer_alive") is True
                and o.get("physical_verified") is True
                and 0 <= now - o["renderer_verified_monotonic"] <= 3
                for o in outputs.values())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def wait_renderer(self):
        deadline = self.clock() + 45
        while not self.hdmi_ready():
            if self.system.query().get(HDMI, {}).get("ActiveState") != "active" or self.clock() >= deadline:
                raise ModeError("HDMI_RENDERER_NOT_READY")
            self.sleep(.25)

    def mobile_ready(self):
        # Endpoint existence is launcher readiness, NOT a connected phone proof.
        from urllib.parse import urlsplit
        import ipaddress
        value = json.loads(regular(self.endpoint, private=True, limit=4096).read_text())
        url = urlsplit(value["url"])
        ipaddress.IPv4Address(url.hostname)
        if (url.scheme != "http" or url.path != "/display/mobile" or url.query or url.fragment
                or url.username or url.password or not isinstance(value.get("interface"), str)):
            raise ModeError("MOBILE_ENDPOINT_NOT_READY")

    def wait_mobile(self):
        # Type=simple becomes active before Python has bound USB and published
        # endpoint.json. A missing/half-written startup file is not ready yet.
        deadline = self.clock() + 15
        while True:
            try:
                self.mobile_ready()
                return
            except (FileNotFoundError, json.JSONDecodeError):
                if (self.system.query().get(MOBILE, {}).get("ActiveState") != "active"
                        or self.clock() >= deadline):
                    raise ModeError("MOBILE_ENDPOINT_NOT_READY") from None
                self.sleep(.25)

    def status(self):
        info = self.system.query()
        return {"schema_version": 1, "selected": self.selected(), "boot_default": "hdmi",
                "services": {u: {k: info.get(u, {}).get(k, "unknown") for k in ("LoadState", "ActiveState")} for u in UNITS},
                "hdmi_renderer_ready": self.hdmi_ready(), "physical_display_verified": False,
                "phone_connection_verified": False}

    def switch(self, mode):
        if mode not in {"hdmi", "mobile"}:
            raise ModeError("INVALID_MODE")
        info = self.system.query()
        self.check(info)
        old = self.selected()
        touched = set()
        try:
            if mode == "mobile":
                # Prepare the phone listener before removing the visible HDMI UI.
                if info[MOBILE]["ActiveState"] != "active":
                    touched.add(MOBILE)
                    self.system.change("start", MOBILE)
                self.wait(MOBILE, "active")
                self.wait_mobile()
                self.mark("mobile")
                touched.add(HDMI)
                self.system.change("stop", HDMI)
                self.wait(HDMI, "inactive", 25)
            else:
                self.mark("hdmi")
                touched.add(HDMI)
                self.system.change("start", HDMI)
                self.wait(HDMI, "active")
                self.wait_renderer()
                touched.add(MOBILE)
                self.system.change("stop", MOBILE)
                self.wait(MOBILE, "inactive", 15)
            result = self.status()
            result.update(applied=True, rollback="not_needed")
            return result
        except Exception as error:
            rollback_ok = True
            try:
                self.mark(old)
            except Exception:
                rollback_ok = False
            # Stop a newly started service before restoring the previous one.
            ordered = sorted(touched, key=lambda u: info[u]["ActiveState"] == "active")
            for unit in ordered:
                state = info[unit]["ActiveState"]
                try:
                    self.system.change("start" if state == "active" else "stop", unit)
                    self.wait(unit, state, 50 if state == "active" else 25)
                except Exception:
                    rollback_ok = False
            if not rollback_ok:
                raise ModeError("ROLLBACK_INCOMPLETE_CHECK_DISPLAY_SERVICES") from None
            raise ModeError(error.code if isinstance(error, ModeError) else "DISPLAY_MODE_FAILED_RESTORED") from None


@contextmanager
def locked(runtime=RUNTIME):
    import fcntl
    runtime.mkdir(mode=0o700, exist_ok=True)
    st = runtime.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077 or runtime.resolve() != runtime:
        raise ModeError("UNSAFE_RUNTIME_DIRECTORY")
    fd = os.open(runtime / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        regular(runtime / "lock", private=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ModeError("DISPLAY_MODE_BUSY") from None
        yield
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("status", "hdmi", "mobile"))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print(json.dumps({"code": "ROOT_REQUIRED"}))
        return 1
    try:
        selector = Selector()
        if args.mode == "status":
            result = selector.status()
        else:
            with locked():
                result = selector.switch(args.mode)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"applied": False, "code": error.code if isinstance(error, ModeError) else "DISPLAY_MODE_CHECK_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
