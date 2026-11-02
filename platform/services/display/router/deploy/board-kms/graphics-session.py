"""Notify systemd only after Xorg readiness; restore console on normal stop."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

root = Path(os.environ["XENOVA_GRAPHICS_ROOT"])
ready = root / "run/xenova-display-graphics-ready.json"
ready.unlink(missing_ok=True)
stop = False
child = None
def stopping(*_):
    global stop
    stop = True
signal.signal(signal.SIGTERM, stopping)
signal.signal(signal.SIGINT, stopping)
def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect("\0" + address[1:] if address.startswith("@") else address)
            sock.sendall(message.encode())
fd = os.open("/dev/tty0", os.O_RDONLY)
vt = bytearray(6)
fcntl.ioctl(fd, 0x5603, vt, True)
os.close(fd)
prior_vt = struct.unpack("3H", vt)[0]
if prior_vt == 2:
    raise RuntimeError("VT2 already active; refusing takeover")
fb = os.open("/dev/fb0", os.O_RDWR)
fixed, variable = bytearray(80), bytearray(160)
fcntl.ioctl(fb, 0x4602, fixed, True)
fcntl.ioctl(fb, 0x4600, variable, True)
geometry = struct.unpack_from("4I", variable, 0)
length = struct.unpack_from("I", fixed, 24)[0]
if not 0 < length <= 64 * 1024 * 1024:
    raise RuntimeError("unexpected console framebuffer size")
original = os.pread(fb, length, 0)
if len(original) != length:
    raise RuntimeError("incomplete console snapshot")
try:
    lib = root / "usr/lib/aarch64-linux-gnu"
    child = subprocess.Popen([str(lib / "ld-linux-aarch64.so.1"), "--library-path", str(lib), str(root / "usr/bin/unshare"), "--mount", "--propagation", "private", "--pid", "--fork", "--kill-child=TERM", "/usr/bin/python3", str(Path(__file__).with_name("graphics-child.py")), str(root)], start_new_session=True)
    deadline = time.monotonic() + 40
    while not stop and not ready.exists():
        if child.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("graphics session did not become ready")
        time.sleep(.2)
    if not stop:
        notify("READY=1\nSTATUS=Dedicated X11 session ready")
    while not stop:
        if child.poll() is not None:
            raise RuntimeError("graphics namespace exited")
        time.sleep(.2)
finally:
    if child and child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
    # Restore through the host VT API; the isolated graphics bundle need not
    # contain the optional kbd/chvt executable.
    tty = os.open("/dev/tty0", os.O_RDONLY)
    try:
        fcntl.ioctl(tty, 0x5606, prior_vt)  # VT_ACTIVATE
        deadline = time.monotonic() + 5
        while True:
            state = bytearray(6)
            fcntl.ioctl(tty, 0x5603, state, True)  # VT_GETSTATE
            if struct.unpack("3H", state)[0] == prior_vt:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("console VT restoration timed out")
            time.sleep(.05)
    finally:
        os.close(tty)
    current = bytearray(160)
    fcntl.ioctl(fb, 0x4600, current, True)
    restored = False
    if struct.unpack_from("4I", current, 0) == geometry:
        offset = 0
        while offset < len(original):
            offset += os.pwrite(fb, original[offset:], offset)
        restored = os.pread(fb, length, 0) == original
    os.close(fb)
    Path("/run/xenova-display/graphics-stop.json").write_text(json.dumps({"active_vt_restored": prior_vt, "framebuffer_exact_restore": restored}))
