"""Yocto 전용 X11 세션. 기존 kiosk를 재사용하며 VM·인증 설정은 변경하지 않는다.

Xorg displayfd 준비와 실제 renderer/HDMI 상태는 별개다.
직접 자식의 정상 종료를 먼저 요청하고 잔존 하위 프로세스는 systemd cgroup이 정리한다.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import selectors
import signal
import socket
import stat
import struct
import subprocess
import threading
import time

DISPLAY = ":5"
VT = 7  # 기본 자동 getty 범위 밖. tty-fail이 이미 소유된 터미널을 거절한다.
RUNTIME = Path("/run/xenova-display-graphics")
HOME = Path("/var/lib/xenova-display")
START_SECONDS = 30
STOP_SECONDS = 15


def root_file(path):
    path = Path(path)
    st = path.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022
            or path.resolve() != path):
        raise ValueError("root-owned, non-writable regular configuration required")
    return path


def configuration(path, xorg):
    from .outputs import RuntimeConfig
    config = RuntimeConfig.load(root_file(path))
    root_file(xorg)
    if config.backend != "x11" or config.display != DISPLAY:
        raise ValueError("native session requires physical x11 on :5")
    if any(o.edid_sha256 == "0" * 64 for o in config.outputs):
        raise ValueError("replace example EDID hashes before starting HDMI")
    return config


def notify(text):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect("\0" + address[1:] if address.startswith("@") else address)
            sock.sendall(text.encode())


def wait_ready(child, descriptor, stop, deadline, *, clock=time.monotonic):
    """실제 Xorg displayfd만 수락한다. 예전 socket/ready 파일은 근거가 아니다."""
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_READ)
        while not stop.is_set():
            if child.poll() is not None:
                raise RuntimeError("Xorg exited before readiness")
            left = deadline - clock()
            if left <= 0:
                raise TimeoutError("Xorg readiness deadline expired")
            if not selector.select(min(left, .2)):
                continue
            chunk = os.read(descriptor, 64)
            if not chunk:
                raise RuntimeError("Xorg closed readiness pipe")
            data.extend(chunk)
            if len(data) > 16:
                raise RuntimeError("invalid Xorg readiness response")
            if b"\n" in data:
                if data != b"5\n":
                    raise RuntimeError("unexpected Xorg display number")
                if clock() >= deadline:
                    raise TimeoutError("Xorg became ready after deadline")
                return not stop.is_set()
    return False


def supervise(children, stop):
    """자식 사망은 세션 실패다. renderer의 물리 출력 성공을 추측하지 않는다."""
    while not stop.wait(.2):
        if any(child.poll() is not None for child, _ in children):
            raise RuntimeError("display child exited")


def stop_children(children, deadline, *, clock=time.monotonic):
    """kiosk → WM → Xorg. 모든 단계가 하나의 종료 예산을 사용한다."""
    remaining = []
    for child, group in reversed(children):
        if child.poll() is not None:
            continue
        try:
            if group:
                os.killpg(child.pid, signal.SIGTERM)
            else:
                child.terminate()
            child.wait(timeout=min(4, max(0, deadline - clock())))
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            try:
                if group:
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                child.wait(timeout=max(0, deadline - clock()))
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                remaining.append(child.pid)
    if remaining:
        raise RuntimeError("display children did not exit within the stop budget")


class Console:
    def __init__(self):
        self.fd = os.open("/dev/tty0", os.O_RDWR | os.O_NOCTTY)
        self.started = False
        try:
            self.previous = self.active()
            if self.previous == VT:
                raise RuntimeError("reserved display VT is already active")
        except BaseException:
            os.close(self.fd)
            raise

    def active(self):
        state = bytearray(6)
        fcntl.ioctl(self.fd, 0x5603, state, True)  # VT_GETSTATE
        return struct.unpack("3H", state)[0]

    def close(self):
        try:
            # Xorg normally restores the original VT. Preserve a user's
            # intervening switch to a third VT.
            if self.started and self.active() == VT:
                fcntl.ioctl(self.fd, 0x5606, self.previous)  # VT_ACTIVATE
                end = time.monotonic() + 3
                while self.active() != self.previous:
                    if time.monotonic() >= end:
                        raise RuntimeError("console VT restoration timed out")
                    time.sleep(.05)
        finally:
            os.close(self.fd)


def environment(account, *, renderer=False):
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
           "DISPLAY": DISPLAY, "XAUTHORITY": str(RUNTIME / "Xauthority"),
           "HOME": str(HOME), "USER": account.pw_name, "LOGNAME": account.pw_name,
           "XDG_RUNTIME_DIR": str(RUNTIME / "client")}
    if renderer:
        token = os.environ.get("XENOVA_DISPLAY_TOKEN", "")
        if not token:
            raise ValueError("XENOVA_DISPLAY_TOKEN is required")
        env["XENOVA_DISPLAY_TOKEN"] = token
    return env


def prepare(account):
    st = RUNTIME.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0
            or st.st_gid != account.pw_gid or st.st_mode & 0o007):
        raise ValueError("invalid graphics runtime directory")
    client = RUNTIME / "client"
    if client.exists() or client.is_symlink():
        st = client.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != account.pw_uid or st.st_mode & 0o077:
            raise ValueError("invalid client runtime directory")
    else:
        client.mkdir(mode=0o700)
        os.chown(client, account.pw_uid, account.pw_gid)
    old = client / "display-runtime.json"
    if old.exists() or old.is_symlink():
        st = old.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != account.pw_uid:
            raise ValueError("invalid previous renderer status")
        old.unlink()
    authority = RUNTIME / "Xauthority"
    if authority.exists() or authority.is_symlink():
        st = authority.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
            raise ValueError("invalid previous X11 authority")
        authority.unlink()
    fd = os.open(authority, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    import secrets
    # Cookie stays out of argv, journal and public status.
    subprocess.run(["/usr/bin/xauth", "-f", str(authority), "-q"],
                   input=f"add {DISPLAY} . {secrets.token_hex(16)}\n", text=True,
                   check=True, capture_output=True, timeout=5)
    os.chown(authority, 0, account.pw_gid)
    authority.chmod(0o640)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/xenova/display-runtime.json")
    parser.add_argument("--xorg-config", default="/etc/xenova/display-Xorg.conf")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run through xenova-display-hdmi.service")
    configuration(args.config, args.xorg_config)
    account = pwd.getpwnam("xenova-display")
    if account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != str(HOME):
        raise ValueError("invalid display service account")
    env = environment(account)
    renderer_env = environment(account, renderer=True)
    prepare(account)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    console = Console()
    children = []
    read, write = None, None
    try:
        read, write = os.pipe2(os.O_CLOEXEC)
        xorg = subprocess.Popen(["/usr/bin/Xorg", DISPLAY, f"vt{VT}", "-keeptty",
            "-nolisten", "tcp", "-noreset", "-displayfd", str(write),
            "-auth", str(RUNTIME / "Xauthority"), "-config", args.xorg_config,
            "-logfile", str(RUNTIME / "Xorg.log")], env=env, pass_fds=(write,))
        children.append((xorg, False))
        console.started = True
        os.close(write)
        write = None
        if not wait_ready(xorg, read, stop, time.monotonic() + START_SECONDS) or stop.is_set():
            return
        lower = ["/usr/bin/setpriv", f"--reuid={account.pw_uid}",
                 f"--regid={account.pw_gid}", "--clear-groups"]
        wm = subprocess.Popen([*lower, "--no-new-privs", "/usr/bin/openbox", "--sm-disable"],
                              env=env, start_new_session=True)
        children.append((wm, True))
        if stop.is_set():
            return
        kiosk = subprocess.Popen([*lower, "/usr/bin/python3", "-m", "xenova_display.kiosk",
            "--config", args.config, "--status", str(RUNTIME / "client/display-runtime.json"),
            "--chromium", "/usr/lib/chromium/chromium-bin", "--prevent-idle-blank"],
            env=renderer_env, start_new_session=True)
        children.append((kiosk, True))
        if stop.is_set():
            return
        notify("READY=1\nSTATUS=X11 ready; inspect renderer status for actual HDMI output")
        supervise(children, stop)
    finally:
        for fd in (read, write):
            if fd is not None:
                os.close(fd)
        try:
            stop_children(children, time.monotonic() + STOP_SECONDS)
        finally:
            console.close()


if __name__ == "__main__":
    main()
