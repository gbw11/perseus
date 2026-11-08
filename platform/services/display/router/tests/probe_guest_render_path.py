"""410 Plan B local path probe: X11 pixels -> guest_sender -> dom0 receiver/relay -> consumer.

Local only (Xvfb on a workstation/WSL). It stands in for the display domU's
screen; it is NOT a Xen guest, NOT the board and NOT the real vehicle UI.
It proves that pixels drawn on the "guest" X server reach a dom0-side
consumer through the authenticated relay, that a screen change propagates,
and that a stopped guest is reported unavailable.

Requires: Xvfb, xsetroot, ffmpeg (x11grab). Changes nothing outside a temp dir.
"""
import argparse
import http.client
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
from xenova_display.mjpeg import JpegParser, jpeg_dimensions  # noqa: E402

DISPLAY = ":97"
WIDTH, HEIGHT = 1280, 720


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sample_rgb(jpeg):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "pipe:0",
                          # Convert first: a 1x1 crop is invalid on 4:2:0 chroma.
                          "-vf", "format=rgb24,crop=1:1:100:100", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         input=jpeg, capture_output=True, timeout=10, check=True).stdout
    return list(out[:3])


def close_to(rgb, target, tolerance=24):
    return all(abs(a-b) <= tolerance for a, b in zip(rgb, target))


def collect(port, token, seconds):
    """Read the relay's MJPEG for a while; return (frames, elapsed)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/streams/display.mjpeg", headers={"Authorization": f"Bearer {token}"})
    response = connection.getresponse()
    assert response.status == 200, response.status
    parser, frames, start = JpegParser(), [], time.monotonic()
    while time.monotonic() - start < seconds:
        frames += parser.push(response.read1(65536))
    connection.close()
    return frames, time.monotonic() - start


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=float, default=6)
    p.add_argument("--save", help="write the last relayed frame here (JPEG)")
    args = p.parse_args()
    summary = {"environment": "local Xvfb stand-in for the display domU (not Xen, not board)",
               "synthetic_pixels": "xsetroot solid colours", "width": WIDTH, "height": HEIGHT}
    with tempfile.TemporaryDirectory() as tmp:
        guest_token, relay_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        token_file = Path(tmp)/"guest-token"
        token_file.write_text(guest_token)
        token_file.chmod(0o600)
        # Abstract socket only (WSLg mounts /tmp/.X11-unix read-only); -noreset keeps the
        # root colour after xsetroot disconnects.
        xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{WIDTH}x{HEIGHT}x24",
                                 "-nolisten", "tcp", "-nolisten", "unix", "-noreset"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs, server, receiver = [xvfb], None, None
        try:
            deadline = time.monotonic()+10
            while subprocess.run(["xsetroot", "-display", DISPLAY, "-solid", "#1a73e8"],
                                 stderr=subprocess.DEVNULL).returncode:
                if time.monotonic() > deadline or xvfb.poll() is not None: raise RuntimeError("Xvfb did not start")
                time.sleep(.1)
            guest_port, relay_port = free_port(), free_port()
            sender = subprocess.Popen([sys.executable, "-m", "xenova_display.guest_sender", "--bind", "127.0.0.1",
                                       "--allow-loopback", "--port", str(guest_port), "--token-file", str(token_file),
                                       "--source", "x11", "--display", DISPLAY, "--fps", "5"],
                                      cwd=ROUTER, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            procs.append(sender)
            receiver = GuestFrameReceiver(GuestSource(host="127.0.0.1", port=guest_port, allow_loopback=True,
                                                      width=WIDTH, height=HEIGHT, max_fps=5), guest_token)
            app = create_relay_app(receiver, relay_token)
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=relay_port, log_level="error"))
            threading.Thread(target=server.run, daemon=True).start()
            deadline = time.monotonic()+20
            while receiver.snapshot()[0]["status"] != "live":
                if time.monotonic() > deadline:
                    raise RuntimeError(f"no live guest frame: {receiver.snapshot()[0]['error']}")
                time.sleep(.1)
            frames, elapsed = collect(relay_port, relay_token, args.seconds)
            summary.update(relayed_frames=len(frames), seconds=round(elapsed, 2),
                           relayed_fps=round(len(frames)/elapsed, 2),
                           jpeg_bytes_avg=int(sum(map(len, frames))/max(len(frames), 1)),
                           dimensions_ok=all(jpeg_dimensions(f) == (WIDTH, HEIGHT) for f in frames),
                           sample_rgb_blue=sample_rgb(frames[-1]))
            summary["blue_seen"] = close_to(summary["sample_rgb_blue"], [0x1a, 0x73, 0xe8])
            # The "guest UI" changes: dom0 must show the new pixels, not a cached frame.
            subprocess.run(["xsetroot", "-display", DISPLAY, "-solid", "#d93025"], check=True)
            changed_at, red = time.monotonic(), None
            while time.monotonic() - changed_at < 5:
                frames, _ = collect(relay_port, relay_token, 0.6)
                if frames and close_to(sample_rgb(frames[-1]), [0xd9, 0x30, 0x25]):
                    red = frames[-1]
                    break
            summary["red_seen_after_ms"] = int((time.monotonic()-changed_at)*1000) if red else None
            if args.save and (red or frames):
                Path(args.save).write_bytes(red or frames[-1])
                summary["saved"] = args.save
            # The guest stops: the relay must stop presenting the last frame as live.
            sender.terminate(); sender.wait(timeout=10)
            stopped_at = time.monotonic()
            while receiver.snapshot()[0]["status"] == "live" and time.monotonic() - stopped_at < 6:
                time.sleep(.05)
            metadata = receiver.snapshot()[0]
            summary.update(unavailable_after_stop_ms=int((time.monotonic()-stopped_at)*1000),
                           status_after_stop=metadata["status"], error_after_stop=metadata["error"],
                           frames_rejected=metadata["frames_rejected"], frames_dropped=metadata["frames_dropped"])
            summary["tokens_in_status"] = guest_token in json.dumps(metadata) or relay_token in json.dumps(metadata)
        finally:
            if server is not None: server.should_exit = True
            if receiver is not None: receiver.stop()
            for proc in reversed(procs):
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = (summary.get("dimensions_ok") and summary.get("blue_seen") and summary.get("red_seen_after_ms") is not None
          and summary.get("status_after_stop") == "unavailable" and not summary.get("tokens_in_status"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
