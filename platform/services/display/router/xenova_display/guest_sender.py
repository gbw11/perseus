"""410 Plan B, display-domU side: serve this guest's own rendered screen to dom0.

The display domU runs the vehicle UI and renders the pixels itself. This
worker captures that screen (X11) and serves it as ONE authenticated
latest-frame MJPEG stream on the guest's Xen network address. dom0 pulls it
with `guest_frames.GuestFrameReceiver`; dom0 does not render the UI here.

Standard library only (plus the ffmpeg binary), so it runs in a minimal
Debian guest without pip. `testsrc` is a synthetic pattern for path checks
only and is always reported as `synthetic: true`.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .mjpeg import JpegParser, jpeg_dimensions

SOURCES = ("x11", "testsrc")
STREAM_PATH = "/frames.mjpeg"
STATUS_PATH = "/status"
MAX_STREAM_CLIENTS = 2  # dom0 relay (+1 spare during reconnect); not a public fan-out
STALE_SECONDS = 2.0


def read_token(path):
    """Root/owner-only token file; the value never appears in argv, logs or responses."""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 512:
        raise ValueError("token file must be a private regular file (mode 0600, <=512 bytes)")
    token = path.read_text(encoding="utf-8").strip()
    if not token or any(c in token for c in "\r\n\0"):
        raise ValueError("token file is empty or malformed")
    return token


def bind_address(host, *, allow_loopback=False):
    """Bind to one explicit guest IPv4 address, never a wildcard."""
    address = ipaddress.IPv4Address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("bind to the guest's Xen network address, not a wildcard")
    if address.is_loopback and not allow_loopback:
        raise ValueError("loopback bind is for local tests only")
    if not (address.is_private or address.is_loopback):
        raise ValueError("display frames stay on the private Xen network")
    return str(address)


def capture_command(source, *, display=":0", width=1280, height=720, fps=5, quality=7):
    if source not in SOURCES:
        raise ValueError("unknown capture source")
    if not (640 <= width <= 1920 and 480 <= height <= 1080 and 1 <= fps <= 15 and 2 <= quality <= 20):
        raise ValueError("capture size, rate or quality out of range")
    if source == "x11":
        if not display.startswith(":") or not display[1:].replace(".", "").isdigit():
            raise ValueError("local X11 display required")
        source_args = ["-f", "x11grab", "-draw_mouse", "0", "-framerate", str(fps),
                       "-video_size", f"{width}x{height}", "-i", f"{display}+0,0"]
    else:
        # Synthetic moving pattern; -re keeps it at the requested rate.
        source_args = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}"]
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *source_args, "-an",
            "-vf", "format=yuvj420p", "-c:v", "mjpeg", "-q:v", str(quality), "-threads", "1",
            "-flush_packets", "1", "-f", "mjpeg", "pipe:1"]


class LatestFrame:
    """Single latest frame; no queue, no history."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.condition = threading.Condition()
        self.frame, self.sequence, self.captured = None, 0, 0.0
        self.error = "waiting"

    def publish(self, frame):
        with self.condition:
            self.frame, self.captured = frame, self.clock()
            self.sequence += 1
            self.error = None
            self.condition.notify_all()

    def fail(self, code):
        with self.condition:
            self.frame, self.error = None, code
            self.condition.notify_all()

    def current(self):
        with self.condition:
            fresh = self.frame is not None and self.clock() - self.captured < STALE_SECONDS
            return self.sequence, (self.frame if fresh else None)

    def wait_newer(self, sequence, timeout):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != sequence, timeout)
        return self.current()

    def status(self):
        with self.condition:
            age = (self.clock()-self.captured)*1000 if self.sequence else None
            live = self.frame is not None and age is not None and age < STALE_SECONDS*1000
            return {"frame_sequence": self.sequence,
                    "frame_age_ms": int(age) if age is not None else None,
                    "status": "live" if live else "unavailable",
                    "error": None if live else self.error or "frame_stale"}


class CaptureWorker:
    """Run the capture encoder, keep only the latest valid frame, restart on exit."""
    def __init__(self, command, latest, *, width, height, spawn=subprocess.Popen):
        self.command, self.latest, self.spawn = command, latest, spawn
        self.width, self.height = width, height
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="guest-capture")
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            parser = JpegParser()
            try:
                self.process = self.spawn(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                stdout = self.process.stdout
                while not self.stop_event.is_set():
                    if not select.select([stdout], [], [], 1)[0]:
                        continue
                    data = stdout.read1(65536)
                    if not data:
                        break
                    for frame in parser.push(data):
                        if jpeg_dimensions(frame) == (self.width, self.height):
                            self.latest.publish(frame)
                self.latest.fail("encoder_stopped")
            except OSError:
                self.latest.fail("encoder_unavailable")
            finally:
                process, self.process = self.process, None
                if process:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                    if process.stdout: process.stdout.close()
            self.stop_event.wait(1)

    def stop(self):
        self.stop_event.set()
        process = self.process
        if process:
            try: process.terminate()
            except ProcessLookupError: pass
        if self.thread: self.thread.join(timeout=5)


def make_handler(token, latest, *, source, stopping):
    expected = f"Bearer {token}".encode()
    slots = threading.BoundedSemaphore(MAX_STREAM_CLIENTS)

    class Handler(BaseHTTPRequestHandler):
        server_version = "xenova-guest-display"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):  # no request logs with paths/addresses
            pass

        def authorised(self):
            return hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected)

        def plain(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.authorised():
                return self.plain(401, {"code": "Unauthorized"})
            if self.path == STATUS_PATH:
                return self.plain(200, {**latest.status(), "source": source,
                                        "synthetic": source == "testsrc", "rendered_by": "domU"})
            if self.path != STREAM_PATH:
                return self.plain(404, {"code": "NotFound"})
            if not slots.acquire(blocking=False):
                return self.plain(503, {"code": "StreamBusy"})
            try:
                self.connection.settimeout(5)  # a stuck consumer cannot pin this worker
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                last = None
                while not stopping.is_set():
                    sequence, frame = latest.wait_newer(last, 0.5)
                    if frame is None or sequence == last:
                        continue
                    last = sequence
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     + f"Content-Length: {len(frame)}\r\n\r\n".encode() + frame + b"\r\n")
                    self.wfile.flush()
            except OSError:
                pass  # consumer went away or stalled
            finally:
                self.close_connection = True
                slots.release()

        def do_POST(self): self.plain(405, {"code": "MethodNotAllowed"})
        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


class GuestDisplayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host, port, token, latest, *, source):
    stopping = threading.Event()
    server = GuestDisplayServer((host, port), make_handler(token, latest, source=source, stopping=stopping))
    server.stopping = stopping
    return server


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", required=True, help="the display domU's own Xen network IPv4 address")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--token-file", required=True)
    p.add_argument("--source", choices=SOURCES, default="x11")
    p.add_argument("--display", default=":0")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=5)
    p.add_argument("--quality", type=int, default=7)
    p.add_argument("--allow-loopback", action="store_true", help="local tests only")
    args = p.parse_args(argv)
    try:
        host = bind_address(args.bind, allow_loopback=args.allow_loopback)
        token = read_token(args.token_file)
        command = capture_command(args.source, display=args.display, width=args.width,
                                  height=args.height, fps=args.fps, quality=args.quality)
    except (OSError, ValueError) as error:
        print(json.dumps({"code": "GUEST_DISPLAY_CONFIG_INVALID", "detail": type(error).__name__}))
        return 2
    latest = LatestFrame()
    worker = CaptureWorker(command, latest, width=args.width, height=args.height)
    server = serve(host, args.port, token, latest, source=args.source)
    def stop(*_):
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.stopping.set()
        server.server_close()
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
