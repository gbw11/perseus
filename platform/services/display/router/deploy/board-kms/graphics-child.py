"""Private mount/PID namespace: Xorg and Openbox only; no VM control."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(sys.argv[1])
ready = root / "run/xenova-display-graphics-ready.json"
stop = False
processes = []
logs = []
def stopping(*_):
    global stop
    stop = True
signal.signal(signal.SIGTERM, stopping)
signal.signal(signal.SIGINT, stopping)
env = {**os.environ, "DISPLAY": ":5", "XAUTHORITY": "/run/xenova-display.Xauthority", "HOME": "/home/kiosk", "XDG_RUNTIME_DIR": "/run/user/1000", "LANG": "C.UTF-8"}
def command(args):
    return ["chroot", str(root), *args]
try:
    for args in [["mount", "--rbind", "/dev", str(root / "dev")], ["mount", "--make-rslave", str(root / "dev")], ["mount", "-t", "proc", "proc", str(root / "proc")], ["mount", "--bind", "/sys", str(root / "sys")]]:
        subprocess.run(args, check=True)
    state = root / "run/xenova-kiosk"
    state.mkdir(exist_ok=True)
    os.chown(state, 1000, 1000)
    state.chmod(0o700)
    (state / "display-runtime.json").unlink(missing_ok=True)
    authority = root / "run/xenova-display.Xauthority"
    subprocess.run(command(["/usr/bin/xauth", "-f", "/run/xenova-display.Xauthority", "add", ":5", ".", __import__("secrets").token_hex(16)]), check=True, capture_output=True)
    os.chown(authority, 1000, 1000)
    authority.chmod(0o600)
    log = (root / "run/xenova-display-Xorg.log").open("w")
    logs.append(log)
    server = subprocess.Popen(command(["/usr/lib/xorg/Xorg", ":5", "vt2", "-config", "/run/xenova-display-Xorg.conf", "-logfile", "/run/xenova-display-Xorg-internal.log", "-nolisten", "tcp", "-noreset", "-auth", "/run/xenova-display.Xauthority"]), stdout=log, stderr=log, env=env, start_new_session=True)
    processes.append(server)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not stop:
        if server.poll() is not None:
            raise RuntimeError("Xorg exited during startup")
        if (root / "tmp/.X11-unix/X5").exists():
            break
        time.sleep(.2)
    else:
        raise RuntimeError("Xorg startup stopped or timed out")
    log = (root / "run/xenova-display-openbox.log").open("w")
    logs.append(log)
    wm = subprocess.Popen(command(["/usr/bin/setpriv", "--reuid=1000", "--regid=1000", "--clear-groups", "/usr/bin/openbox", "--sm-disable"]), stdout=log, stderr=log, env=env, start_new_session=True)
    processes.append(wm)
    time.sleep(1)
    ready.write_text(json.dumps({"pid": os.getpid(), "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}))
    while not stop:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("graphics session process exited")
        time.sleep(.2)
finally:
    ready.unlink(missing_ok=True)
    for p in reversed(processes):
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=3)
            except ProcessLookupError:
                pass
    for log in logs:
        log.close()
