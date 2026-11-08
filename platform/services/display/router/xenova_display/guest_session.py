"""413 Plan B, display-domU side: one virtual screen with the vehicle screen app in Chromium kiosk.

Starts Xvfb (the display domU's screen), waits until it is really ready
(-displayfd), then starts Chromium in kiosk mode on the local screen app
(guest_app). `guest_sender` captures this screen for dom0. Either child
exiting is a session failure, so systemd restarts the whole session.

Standard library only. Runs as the unprivileged display account.
"""
from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import tempfile
import threading
import time

from .guest_app import load_config

START_SECONDS = 20
STOP_SECONDS = 10


def xvfb_command(display, width, height, displayfd):
    # No TCP listener; -noreset keeps the screen when clients reconnect; no pointer on the screen.
    return ["Xvfb", display, "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp",
            "-noreset", "-nocursor", "-displayfd", str(displayfd)]


def chromium_command(chromium, url, width, height, profile):
    return [chromium, "--kiosk", "--no-first-run", "--no-default-browser-check", "--noerrdialogs",
            "--disable-session-crashed-bubble", "--disable-translate", "--disable-features=Translate",
            "--lang=ko-KR", "--password-store=basic", "--ozone-platform=x11", "--disable-gpu",
            "--force-device-scale-factor=1", "--window-position=0,0", f"--window-size={width},{height}",
            f"--user-data-dir={profile}", url]


def wait_ready(child, descriptor, display, stop, deadline, *, clock=time.monotonic):
    """Accept only the X server's own readiness report for the expected display."""
    data = bytearray()
    expected = display[1:].encode() + b"\n"
    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_READ)
        while not stop.is_set():
            if child.poll() is not None:
                raise RuntimeError("Xvfb exited before readiness")
            left = deadline - clock()
            if left <= 0:
                raise TimeoutError("Xvfb readiness deadline expired")
            if not selector.select(min(left, .2)):
                continue
            chunk = os.read(descriptor, 16)
            if not chunk:
                raise RuntimeError("Xvfb closed its readiness pipe")
            data.extend(chunk)
            if b"\n" in data:
                if bytes(data) != expected:
                    raise RuntimeError("unexpected X display number")
                return True
    return False


def supervise(children, stop):
    """Any child exiting ends the session; the unit restarts it as a whole."""
    while not stop.wait(.5):
        for child in children:
            if child.poll() is not None:
                raise RuntimeError("display session child exited")


def stop_children(children, *, budget=STOP_SECONDS):
    deadline = time.monotonic() + budget
    for child in reversed(children):  # Chromium first, then the X server
        if child.poll() is not None:
            continue
        child.terminate()
        try:
            child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def run(config, stop, *, spawn=subprocess.Popen):
    url = f"http://127.0.0.1:{config['app_port']}{config['page']}"
    env = {k: v for k, v in os.environ.items() if "TOKEN" not in k and "PASSWORD" not in k}
    env["DISPLAY"] = config["display"]
    children = []
    read, write = os.pipe()
    try:
        with tempfile.TemporaryDirectory(prefix="xenova-guest-chromium-") as profile:
            xvfb = spawn(xvfb_command(config["display"], config["width"], config["height"], write),
                         pass_fds=(write,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.append(xvfb)
            os.close(write); write = None
            if not wait_ready(xvfb, read, config["display"], stop, time.monotonic() + START_SECONDS):
                return
            browser = spawn(chromium_command(config["chromium"], url, config["width"], config["height"], profile),
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
            children.append(browser)
            supervise(children, stop)
    finally:
        for fd in (read, write):
            if fd is not None:
                os.close(fd)
        stop_children(children)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="/etc/xenova/guest-display-domu.json")
    args = p.parse_args(argv)
    if os.geteuid() == 0:
        print(json.dumps({"code": "RUN_AS_DISPLAY_ACCOUNT"}))
        return 2
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        print(json.dumps({"code": "GUEST_DISPLAY_SESSION_CONFIG_INVALID", "detail": type(error).__name__}))
        return 2
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        run(config, stop)
    except (OSError, RuntimeError, TimeoutError) as error:
        print(json.dumps({"code": "GUEST_DISPLAY_SESSION_FAILED", "detail": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
