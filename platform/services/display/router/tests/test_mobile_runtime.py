"""351: read-only runtime checks, real local HTTP and offline deployment install."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from xenova_display import mobile_preflight as mobile
from test_feed import settings, store  # noqa: F401 - shared fixtures
from xenova_display.policy import DisplayPolicy
from xenova_display.main import create_app
from fastapi.testclient import TestClient
from test_feed import domains

ROOT = Path(__file__).resolve().parents[5]
DEPLOY = ROOT / "platform/services/display/router/deploy/mobile"
TOKEN = "test-only-do-not-print"


@pytest.fixture
def environment():
    return {"XENOVA_DISPLAY_TOKEN": TOKEN, "XENOVA_OBSERVATIONS_PATH": "/run/xenova/vm-observations.json",
            "XENOVA_DISPLAY_DRIVE_TRANSPORT": "gateway"}


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "marker"
    path.touch(mode=0o644)
    original = Path.lstat

    def metadata(p):
        value = original(p)
        # The fixture caller need not be root. Simulate installation ownership only.
        return SimpleNamespace(st_uid=0, st_mode=value.st_mode, st_size=value.st_size)
    monkeypatch.setattr(Path, "lstat", metadata)
    return path


def unit_info():
    values = {u: {"Id": u, "LoadState": "loaded", "ActiveState": "inactive"} for u in mobile.UNITS}
    values[mobile.UNITS[0]].update(ActiveState="active", PartOf="",
        ExecStart="{ argv[]=/usr/bin/python3 -m uvicorn xenova_display.main:create_app --factory --host 127.0.0.1 --port 8082 --workers 1 ; }")
    values[mobile.UNITS[1]].update(ExecStart="/usr/bin/python3 -m xenova_display.native_session",
        DropInPaths="/usr/lib/systemd/system/xenova-display-hdmi.service.d/50-mobile-first.conf")
    for u in mobile.UNITS[3:]:
        values[u]["LoadState"] = "not-found"
    return values


def inspect(settings, store, marker, environment, *, info=None, get=None):
    p = DisplayPolicy(settings, store.epoch).presentation(store.snapshot())
    calls = []

    def response(port, path, token):
        calls.append((port, path, token))
        return ("text/html", b'<html><div id="root"></div></html>') if path == "/display/mobile" else (
            "application/json", json.dumps(p).encode())
    result = mobile.inspect(environment=environment, marker=marker, query_units=lambda: info or unit_info(),
                            get=get or response, check_guard=lambda _: True)
    return result, calls


def test_local_readiness_is_not_physical_or_fresh_vehicle_acceptance(settings, store, marker, environment):
    store.poll_failure()
    result, calls = inspect(settings, store, marker, environment)
    assert result["local_runtime_ready"]
    assert not result["physical_phone_verified"] and not result["usb_verified"]
    assert result["sources"]["drive_source"] == "unknown"
    assert set(result["sources"]["vm_xen_sources"].values()) == {"disconnected"}
    assert [x[1] for x in calls] == ["/display/mobile", "/api/v1/display/presentation"]
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("change,check", [
    ("partof", "native_shared_feed"), ("duplicate-workers", "native_shared_feed"),
    ("ten-workers", "native_shared_feed"), ("other-address", "native_shared_feed"),
    ("legacy-feed", "native_shared_feed"), ("active-hdmi", "hdmi_inactive"),
    ("starting-hdmi", "hdmi_inactive"), ("legacy-graphics", "legacy_graphics_inactive"),
    ("feed-down", "feed_active"),
])
def test_unsafe_or_unsettled_deployments_are_not_ready(settings, store, marker, environment, change, check):
    info = unit_info()
    if change == "partof": info[mobile.UNITS[0]]["PartOf"] = "xenova-display.target"
    if change == "duplicate-workers": info[mobile.UNITS[0]]["ExecStart"] = info[mobile.UNITS[0]]["ExecStart"].replace("--workers 1", "--workers 2")
    if change == "ten-workers": info[mobile.UNITS[0]]["ExecStart"] = info[mobile.UNITS[0]]["ExecStart"].replace("--workers 1", "--workers 10")
    if change == "other-address": info[mobile.UNITS[0]]["ExecStart"] = info[mobile.UNITS[0]]["ExecStart"].replace("127.0.0.1", "127.0.0.10")
    if change == "legacy-feed": info[mobile.UNITS[0]]["ExecStart"] = "/opt/xenova-display/current/bin/feed.py"
    if change == "active-hdmi": info[mobile.UNITS[1]]["ActiveState"] = "active"
    if change == "starting-hdmi": info[mobile.UNITS[1]]["ActiveState"] = "activating"
    if change == "legacy-graphics": info[mobile.UNITS[3]].update(LoadState="loaded", ActiveState="active")
    if change == "feed-down": info[mobile.UNITS[0]]["ActiveState"] = "failed"
    result, _ = inspect(settings, store, marker, environment, info=info)
    assert not result["checks"][check] and not result["local_runtime_ready"]


@pytest.mark.parametrize("change", ["missing", "nonempty", "writable", "symlink"])
def test_marker_must_be_the_owned_empty_file(settings, store, marker, environment, change):
    if change == "missing": marker.unlink()
    if change == "nonempty": marker.write_text("someone else's settings")
    if change == "writable": marker.chmod(0o666)
    if change == "symlink":
        target = marker.with_suffix(".real")
        marker.rename(target)
        marker.symlink_to(target)
    result, _ = inspect(settings, store, marker, environment)
    assert not result["checks"]["phone_first_marker"] and not result["local_runtime_ready"]


def test_missing_token_does_not_make_requests(settings, store, marker, environment):
    environment.pop("XENOVA_DISPLAY_TOKEN")
    result, calls = inspect(settings, store, marker, environment)
    assert not calls and not result["local_runtime_ready"]


def test_unavailable_sources_and_raw_exceptions_never_dump_credentials(marker, environment):
    def failure(*a): raise RuntimeError("private error " + TOKEN)
    result = mobile.inspect(environment=environment, marker=marker, query_units=failure, get=failure)
    assert not result["local_runtime_ready"]
    assert TOKEN not in json.dumps(result) and "private error" not in json.dumps(result)


@pytest.mark.parametrize("authorized", [True, False])
def test_preflight_uses_existing_feed_auth_and_presentation_without_a_second_collector(
        settings, store, marker, environment, tmp_path, authorized):
    class Driver:
        def list_domains(self): return domains()
    class NoMqtt:
        def __init__(self, _): pass
        def start(self): pass
        def stop(self): pass
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<html><div id="root"></div></html>')
    service = create_app(settings, driver=Driver(), store=store, mqtt_factory=NoMqtt, web_root=tmp_path)
    environment["XENOVA_DISPLAY_TOKEN"] = settings.token if authorized else TOKEN
    requests = []
    with TestClient(service) as client:
        def get(port, path, token):
            requests.append(path)
            response = client.get(path, headers={"Authorization": f"Bearer {token}"})
            if response.status_code != 200:
                raise ValueError("rejected")
            return response.headers.get("Content-Type", ""), response.content
        result, _ = inspect(settings, store, marker, environment, get=get)
    assert result["local_runtime_ready"] is authorized
    assert requests == ["/display/mobile", "/api/v1/display/presentation"]
    assert service.state.store is store
    assert settings.token not in json.dumps(result)


@contextmanager
def local_server(status, body=b"response"):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path, self.headers.get("Authorization")))
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Location", "/should-not-follow")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("status", [401, 302, 503])
def test_http_failures_do_not_redirect_or_use_control_requests(status):
    with local_server(status) as (port, requests):
        with pytest.raises(ValueError): mobile.local_get(port, "/display/mobile", TOKEN)
    assert requests == [("GET", "/display/mobile", f"Bearer {TOKEN}")]


def test_local_http_is_fixed_loopback_and_bounds_response_size(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://invalid-proxy:1")
    with local_server(200, b'<div id="root"></div>') as (port, requests):
        content_type, body = mobile.local_get(port, "/display/mobile", TOKEN)
        assert "text/html" in content_type and b'id="root"' in body
    with local_server(200, b"x" * (mobile.LIMIT + 1)) as (port, _):
        with pytest.raises(ValueError): mobile.local_get(port, "/display/mobile", TOKEN)


def test_systemctl_show_only_and_not_found_units_are_accepted():
    calls = []
    payload = "\n\n".join("\n".join(f"{k}={v}" for k, v in row.items()) for row in unit_info().values())
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=1, stdout=payload, stderr="private systemd error " + TOKEN)
    result = mobile.units(run=run)
    assert result[mobile.UNITS[0]]["ActiveState"] == "active"
    assert calls[0][0][:2] == ["systemctl", "show"]
    assert calls[0][1]["timeout"] == 4 and not calls[0][1].get("shell", False)


def test_guard_requires_exact_negative_condition(marker):
    folder = marker.parent / "xenova-display-hdmi.service.d"
    folder.mkdir()
    guard = folder / "50-mobile-first.conf"
    guard.write_text((DEPLOY / "mobile-first.conf").read_text())
    guard.chmod(0o644)
    assert mobile.guard_installed(str(guard))
    guard.write_text(f"[Unit]\nConditionPathExists={mobile.MARKER}\n")
    assert not mobile.guard_installed(str(guard))


def test_packaging_fragment_installs_support_without_changing_boot_defaults(tmp_path):
    append = ROOT / "platform/yocto/meta-xenova/recipes-core/xenova-display/xenova-display_0.1.0.bbappend"
    recipe = append.read_text()
    extra = re.search(r'FILESEXTRAPATHS:prepend := "([^\"]+)"', recipe).group(1)
    resolved = Path(extra.replace("${THISDIR}", str(append.parent)).rstrip(":")).resolve()
    assert resolved == DEPLOY
    body = re.search(r"do_install:append\(\) \{(.*?)\n\}", recipe, re.S).group(1)
    stage = tmp_path / "root"
    subprocess.run(["sh", "-eu", "-c", body], check=True, timeout=5, env={**os.environ,
        "D": str(stage), "S": str(DEPLOY), "systemd_system_unitdir": "/usr/lib/systemd/system", "datadir": "/usr/share"})
    assert (stage / "usr/lib/systemd/system/xenova-display-hdmi.service.d/50-mobile-first.conf").read_text() == (DEPLOY / "mobile-first.conf").read_text()
    assert (stage / "usr/share/xenova/display/mobile/mobile-first.README.md").is_file()
    assert not (stage / "etc/xenova/display-mobile-first").exists()
    assert not list(stage.rglob("*.wants"))


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze required")
def test_real_systemd_condition_blocks_only_when_marker_exists(tmp_path):
    marker = tmp_path / "mobile-first"
    condition = f"ConditionPathExists=!{marker}"
    absent = subprocess.run(["systemd-analyze", "condition", condition], capture_output=True, timeout=4)
    marker.touch()
    present = subprocess.run(["systemd-analyze", "condition", condition], capture_output=True, timeout=4)
    marker.unlink()
    restored = subprocess.run(["systemd-analyze", "condition", condition], capture_output=True, timeout=4)
    assert absent.returncode == restored.returncode == 0
    assert present.returncode != 0
