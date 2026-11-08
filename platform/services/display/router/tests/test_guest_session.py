"""413: display-domU screen session — Xvfb readiness, Chromium kiosk, fail-together supervision."""
import os
import threading
import time

import pytest

from xenova_display.guest_session import chromium_command, run, wait_ready, xvfb_command

CONFIG = {"app_port": 8086, "page": "/display/mobile", "display": ":0", "width": 1280, "height": 720,
          "chromium": "/usr/bin/chromium"}


def test_commands_are_local_and_kiosk():
    x = xvfb_command(":0", 1280, 720, 9)
    assert x[:2] == ["Xvfb", ":0"] and "1280x720x24" in x and ["-nolisten", "tcp"] == x[5:7]
    assert "-nocursor" in x and x[-2:] == ["-displayfd", "9"]
    c = chromium_command("/usr/bin/chromium", "http://127.0.0.1:8086/display/mobile", 1280, 720, "/tmp/p")
    assert c[0] == "/usr/bin/chromium" and "--kiosk" in c and "--window-size=1280,720" in c
    assert c[-1] == "http://127.0.0.1:8086/display/mobile" and "--user-data-dir=/tmp/p" in c


class Child:
    def __init__(self, name, log, env=None, exit_after=None):
        self.name, self.log, self.env, self.code = name, log, env, None
        self.exit_at = None if exit_after is None else time.monotonic() + exit_after
    def poll(self):
        if self.code is None and self.exit_at and time.monotonic() >= self.exit_at:
            self.code = 1
        return self.code
    def terminate(self):
        self.log.append(("terminate", self.name)); self.code = 0
    def wait(self, timeout=None): return self.code
    def kill(self): self.code = -9


def spawner(log, *, ready=b"0\n", browser_exit=None):
    def spawn(command, **kwargs):
        if command[0] == "Xvfb":
            fd = int(command[command.index("-displayfd")+1])
            assert fd in kwargs["pass_fds"]
            if ready: os.write(fd, ready)
            log.append(("start", "xvfb"))
            return Child("xvfb", log)
        log.append(("start", "chromium"))
        child = Child("chromium", log, env=kwargs["env"], exit_after=browser_exit)
        spawn.browser = child
        return child
    return spawn


def test_session_starts_x_then_browser_and_stops_browser_first(monkeypatch):
    monkeypatch.setenv("XENOVA_GUEST_DATA_TOKEN", "must-not-leak")
    log, stop = [], threading.Event()
    spawn = spawner(log)
    threading.Timer(.3, stop.set).start()
    run(CONFIG, stop, spawn=spawn)
    assert log == [("start", "xvfb"), ("start", "chromium"), ("terminate", "chromium"), ("terminate", "xvfb")]
    assert spawn.browser.env["DISPLAY"] == ":0"
    assert not any("TOKEN" in k for k in spawn.browser.env)


def test_browser_exit_fails_the_whole_session():
    log, stop = [], threading.Event()
    with pytest.raises(RuntimeError, match="child exited"):
        run(CONFIG, stop, spawn=spawner(log, browser_exit=.2))
    assert ("terminate", "xvfb") in log


def test_wrong_display_number_is_not_ready():
    log, stop = [], threading.Event()
    with pytest.raises(RuntimeError, match="display number"):
        run(CONFIG, stop, spawn=spawner(log, ready=b"5\n"))
    assert ("start", "chromium") not in log


def test_readiness_times_out_and_detects_early_exit():
    read, write = os.pipe()
    try:
        with pytest.raises(TimeoutError):
            wait_ready(Child("x", []), read, ":0", threading.Event(), time.monotonic()+.3)
        dead = Child("x", []); dead.code = 1
        with pytest.raises(RuntimeError, match="before readiness"):
            wait_ready(dead, read, ":0", threading.Event(), time.monotonic()+1)
    finally:
        os.close(read); os.close(write)
