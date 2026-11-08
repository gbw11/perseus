"""411 Plan B local probe: guest X11 -> guest_sender -> dom0 relay -> kiosk(guest_frames) -> "HDMI" X11.

Two Xvfb servers stand in for the display domU screen (:97) and dom0's HDMI
X server (:98, two 1280x720 outputs side by side). The real `kiosk` process
runs with renderer=guest_frames and spawns `frame_presenter` per output.
Local only: NOT Xen, NOT the board, NOT a physical HDMI monitor, and the
"guest UI" is solid colours drawn with xsetroot.

Requires Xvfb, xsetroot, xrandr, ffmpeg. Uses the ffmpeg fallback decoder
unless --library points at a TurboJPEG library. Changes nothing outside a temp dir.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

import uvicorn

ROUTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROUTER))
from xenova_display.guest_frames import GuestFrameReceiver, GuestSource, create_relay_app  # noqa: E402

GUEST, HDMI = ":97", ":98"
SAMPLE = {"monitor-1": (640, 600), "monitor-2": (1920, 600)}
BLUE, RED, NOTICE = (0x1a, 0x73, 0xe8), (0xd9, 0x30, 0x25), (0x20, 0x21, 0x24)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def xvfb(display, size):
    process = subprocess.Popen(["Xvfb", display, "-screen", "0", f"{size}x24", "-nolisten", "tcp",
                                "-nolisten", "unix", "-noreset"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic()+10
    while subprocess.run(["xsetroot", "-display", display, "-solid", "black"], stderr=subprocess.DEVNULL).returncode:
        if time.monotonic() > deadline or process.poll() is not None:
            raise RuntimeError(f"Xvfb {display} did not start")
        time.sleep(.1)
    return process


def pixel(x, y):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0",
                          "-video_size", "2560x720", "-i", f"{HDMI}+0,0", "-frames:v", "1",
                          "-vf", f"crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True, timeout=10, check=True).stdout
    return tuple(out[:3])


def near(rgb, target, tolerance=24):
    return all(abs(a-b) <= tolerance for a, b in zip(rgb, target))


def wait_for(target, seconds):
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        if all(near(pixel(*xy), target) for xy in SAMPLE.values()):
            return int((time.monotonic()-start)*1000)
        time.sleep(.1)
    return None


def screenshot(path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", "0",
                    "-video_size", "2560x720", "-i", f"{HDMI}+0,0", "-frames:v", "1", path],
                   check=True, timeout=10)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library", help="TurboJPEG library; default uses the ffmpeg fallback decoder")
    p.add_argument("--save-dir", help="write live/unavailable screenshots of the HDMI stand-in here")
    args = p.parse_args()
    summary = {"environment": "local Xvfb stand-ins (not Xen, not board, not physical HDMI)",
               "decoder": "turbojpeg" if args.library else "ffmpeg (fallback, one-frame lag)"}
    procs, server, receiver = [], None, None
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        guest_token, relay_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        (tmp/"guest.token").write_text(guest_token); (tmp/"guest.token").chmod(0o600)
        (tmp/"relay.token").write_text(relay_token); (tmp/"relay.token").chmod(0o640)
        try:
            procs.append(xvfb(GUEST, "1280x720"))
            procs.append(xvfb(HDMI, "2560x720"))
            subprocess.run(["xsetroot", "-display", GUEST, "-solid", "#1a73e8"], check=True)
            guest_port, relay_port = free_port(), free_port()
            procs.append(subprocess.Popen([sys.executable, "-m", "xenova_display.guest_sender", "--bind", "127.0.0.1",
                "--allow-loopback", "--port", str(guest_port), "--token-file", str(tmp/"guest.token"),
                "--source", "x11", "--display", GUEST, "--fps", "5"], cwd=ROUTER,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            sender = procs[-1]
            receiver = GuestFrameReceiver(GuestSource(host="127.0.0.1", port=guest_port, allow_loopback=True), guest_token)
            server = uvicorn.Server(uvicorn.Config(create_relay_app(receiver, relay_token), host="127.0.0.1",
                                                   port=relay_port, log_level="error"))
            threading.Thread(target=server.run, daemon=True).start()
            runtime = {"backend": "virtual_x11", "display": HDMI, "renderer": "guest_frames",
                       "guest_relay_port": relay_port, "guest_relay_token_file": str(tmp/"relay.token"),
                       "frame_decoder": "turbojpeg" if args.library else "ffmpeg",
                       "native_jpeg_library": args.library,
                       "outputs": [{"output_id": "monitor-1", "connector": "HDMI-1", "x": 0},
                                   {"output_id": "monitor-2", "connector": "HDMI-2", "x": 1280}]}
            (tmp/"runtime.json").write_text(json.dumps(runtime))
            env = {**os.environ, "XENOVA_DISPLAY_TOKEN": "local-probe-only", "DISPLAY": HDMI}
            kiosk_log = open(tmp/"kiosk.log", "wb")
            procs.append(subprocess.Popen([sys.executable, "-m", "xenova_display.kiosk", "--config", str(tmp/"runtime.json"),
                "--status", str(tmp/"status.json"), "--allow-virtual"], cwd=ROUTER, env=env,
                stdout=subprocess.DEVNULL, stderr=kiosk_log))
            kiosk = procs[-1]
            summary["blue_on_both_outputs_after_ms"] = wait_for(BLUE, 20)
            time.sleep(1.5)  # let the 1 s supervisor write a status that includes the presenter reports
            status = json.loads((tmp/"status.json").read_text())
            summary["status_age_ms"] = int((time.monotonic()-status["updated_monotonic"])*1000)
            summary["status_live"] = {k: {f: v.get(f) for f in ("status", "renderer_alive", "renderer_kind",
                                                                   "content_status", "physical_verified")}
                                      for k, v in status["outputs"].items()}
            if args.save_dir: screenshot(str(Path(args.save_dir)/"hdmi-live.png"))
            subprocess.run(["xsetroot", "-display", GUEST, "-solid", "#d93025"], check=True)
            summary["red_after_guest_change_ms"] = wait_for(RED, 10)
            sender.terminate(); sender.wait(timeout=10)
            stopped = time.monotonic()
            summary["notice_after_guest_stop_ms"] = wait_for(NOTICE, 10)
            timeline = []
            while time.monotonic() - stopped < 6:
                status = json.loads((tmp/"status.json").read_text())
                one = status["outputs"]["monitor-1"]
                timeline.append([int((time.monotonic()-stopped)*1000), int((time.monotonic()-status["updated_monotonic"])*1000),
                                 one.get("content_status"), one.get("content_error")])
                time.sleep(.5)
            summary["status_timeline_after_stop"] = timeline
            summary["status_after_stop"] = {k: {f: v.get(f) for f in ("renderer_alive", "content_status", "content_error")}
                                            for k, v in status["outputs"].items()}
            if args.save_dir: screenshot(str(Path(args.save_dir)/"hdmi-unavailable.png"))
            summary["kiosk_running"] = kiosk.poll() is None
            kiosk_log.flush()
            summary["kiosk_stderr_tail"] = (tmp/"kiosk.log").read_text(errors="replace")[-600:] or None
            text = json.dumps(status)
            summary["tokens_in_status"] = guest_token in text or relay_token in text
        finally:
            if server is not None: server.should_exit = True
            if receiver is not None: receiver.stop()
            for proc in reversed(procs):
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=10)
                    except subprocess.TimeoutExpired: proc.kill()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = (summary.get("blue_on_both_outputs_after_ms") is not None and summary.get("red_after_guest_change_ms") is not None
          and summary.get("notice_after_guest_stop_ms") is not None and summary.get("kiosk_running")
          and not summary.get("tokens_in_status")
          and all(v["renderer_alive"] and v["content_status"] == "live" for v in summary["status_live"].values())
          and all(v["renderer_alive"] and v["content_status"] == "unavailable" for v in summary["status_after_stop"].values()))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
