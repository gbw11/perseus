"""실제 pipe/process 종료와 native 진입점의 부분 시작 실패. VT/Xorg는 외부 경계다."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from xenova_display import native_session as native


@contextmanager
def writer(payload):
    read, write = os.pipe()
    program = "import os,signal,sys; " + (f"os.write(int(sys.argv[1]),{payload!r}); " if payload is not None else "") + "signal.pause()"
    process = subprocess.Popen([sys.executable, "-c", program, str(write)],
                               pass_fds=(write,), start_new_session=True)
    os.close(write)
    try:
        yield process, read
    finally:
        os.close(read)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)


def test_actual_displayfd_required_and_process_group_is_reaped():
    with writer(b"5\n") as (child, read):
        assert native.wait_ready(child, read, threading.Event(), time.monotonic() + 2)
        native.stop_children([(child, True)], time.monotonic() + 2)
        assert child.poll() is not None


@pytest.mark.parametrize("payload", [b"6\n", b"x" * 32 + b"\n", b"5\nextra"])
def test_wrong_or_malformed_displayfd_is_rejected(payload):
    with writer(payload) as (child, read):
        with pytest.raises(RuntimeError):
            native.wait_ready(child, read, threading.Event(), time.monotonic() + 2)


def test_no_ready_and_preexisting_file_do_not_extend_deadline(tmp_path):
    (tmp_path / "graphics-ready.json").write_text("old successful run")
    with writer(None) as (child, read):
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            native.wait_ready(child, read, threading.Event(), start + .05)
        assert time.monotonic() - start < 1


def test_stop_before_ready_does_not_claim_success():
    with writer(None) as (child, read):
        stop = threading.Event()
        stop.set()
        assert native.wait_ready(child, read, stop, time.monotonic() + 2) is False


def test_ready_after_deadline_is_rejected():
    ticks = iter([0, 2])
    with writer(b"5\n") as (child, read):
        with pytest.raises(TimeoutError):
            native.wait_ready(child, read, threading.Event(), 1, clock=lambda: next(ticks))


def test_only_renderer_gets_feed_token(monkeypatch):
    monkeypatch.setenv("XENOVA_DISPLAY_TOKEN", "test-only")
    monkeypatch.setenv("XENOVA_DISPLAY_MQTT_PASSWORD", "not-for-graphics")
    monkeypatch.setenv("NOTIFY_SOCKET", "/not-for-children")
    account = SimpleNamespace(pw_name="xenova-display")
    plain = native.environment(account)
    renderer = native.environment(account, renderer=True)
    assert not any(k.startswith("XENOVA_") for k in plain)
    assert renderer["XENOVA_DISPLAY_TOKEN"] == "test-only"
    assert "XENOVA_DISPLAY_MQTT_PASSWORD" not in renderer
    assert "NOTIFY_SOCKET" not in renderer


def test_stop_budget_is_shared_and_reverse_order():
    events, clock = [], [0]
    class Child:
        def __init__(self, name): self.name, self.dead = name, False
        def poll(self): return 0 if self.dead else None
        def terminate(self): events.append(("term", self.name))
        def kill(self): self.dead = True; events.append(("kill", self.name))
        def wait(self, timeout):
            if not self.dead:
                clock[0] += timeout
                raise subprocess.TimeoutExpired(self.name, timeout)
            return 0
    children = [(Child(name), False) for name in ["xorg", "wm", "kiosk"]]
    native.stop_children(children, 5, clock=lambda: clock[0])
    assert [name for action, name in events if action == "term"] == ["kiosk", "wm", "xorg"]
    assert clock[0] == 5


@pytest.mark.parametrize("failure", ["xorg", "ready", "wm", "kiosk", None])
def test_main_cleans_only_started_children(monkeypatch, failure):
    events = []
    class Child:
        def __init__(self, name): self.name = name
    class Console:
        started = False
        def close(self): events.append("console-close")
    monkeypatch.setattr(native, "configuration", lambda *_: None)
    monkeypatch.setattr(native.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native.pwd, "getpwnam", lambda _: SimpleNamespace(
        pw_uid=123, pw_gid=123, pw_dir=str(native.HOME), pw_name="xenova-display"))
    monkeypatch.setattr(native, "prepare", lambda _: events.append("prepare"))
    monkeypatch.setattr(native, "Console", Console)
    monkeypatch.setattr(native.signal, "signal", lambda *_: None)
    monkeypatch.setenv("XENOVA_DISPLAY_TOKEN", "test-only")
    def spawn(args, **kwargs):
        name = "xorg" if args[0].endswith("Xorg") else "wm" if "/usr/bin/openbox" in args else "kiosk"
        if failure == name: raise OSError("injected child start failure")
        assert "--no-sandbox" not in args and "-ac" not in args
        if name != "xorg":
            assert args[0] == "/usr/bin/setpriv" and "--clear-groups" in args
        events.append("start-" + name)
        return Child(name)
    def ready(*_):
        if failure == "ready": raise TimeoutError("injected Xorg timeout")
        events.append("ready")
        return True
    monkeypatch.setattr(native.subprocess, "Popen", spawn)
    monkeypatch.setattr(native, "wait_ready", ready)
    monkeypatch.setattr(native, "supervise", lambda *_: None)
    monkeypatch.setattr(native, "notify", lambda _: events.append("notify"))
    monkeypatch.setattr(native, "stop_children",
                        lambda children, _: events.extend("stop-" + c.name for c, _ in reversed(children)))
    if failure:
        with pytest.raises((OSError, TimeoutError)): native.main([])
    else:
        native.main([])
        assert events.index("ready") < events.index("start-wm") < events.index("start-kiosk") < events.index("notify")
    assert events[-1] == "console-close"
    started = [x[6:] for x in events if x.startswith("start-")]
    stopped = [x[5:] for x in events if x.startswith("stop-")]
    assert stopped == list(reversed(started))


@pytest.mark.parametrize("active,expect_restore", [(7, True), (1, False), (3, False)])
def test_console_does_not_undo_an_operator_switch(monkeypatch, active, expect_restore):
    fd = os.open("/dev/null", os.O_RDONLY)
    console = native.Console.__new__(native.Console)
    console.fd, console.previous, console.started = fd, 1, True
    current, changes = [active], []
    monkeypatch.setattr(console, "active", lambda: current[0])
    def ioctl(_, op, value):
        changes.append((op, value))
        current[0] = value
    monkeypatch.setattr(native.fcntl, "ioctl", ioctl)
    console.close()
    assert bool(changes) is expect_restore


def test_config_reuses_physical_mapping_validation(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    config = json.loads((base / "examples/x11-runtime.json").read_text())
    config["display"] = ":5"
    path = tmp_path / "runtime.json"
    xorg = tmp_path / "xorg.conf"
    xorg.write_text("test external Xorg configuration")
    monkeypatch.setattr(native, "root_file", lambda p: Path(p))
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="EDID"): native.configuration(path, xorg)
    for row in config["outputs"]: row["edid_sha256"] = "1" * 64
    path.write_text(json.dumps(config))
    assert native.configuration(path, xorg).display == ":5"
    config["display"] = ":0";path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=":5"): native.configuration(path, xorg)


def test_root_config_rejects_links_and_writable_files(tmp_path):
    path = tmp_path / "config";path.write_text("{}");path.chmod(0o666)
    with pytest.raises(ValueError): native.root_file(path)
    link = tmp_path / "linked";link.symlink_to(path)
    with pytest.raises(ValueError): native.root_file(link)
