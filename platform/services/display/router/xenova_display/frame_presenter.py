"""411 Plan B: dom0 HDMI frame output for one output window.

Shows the latest frame that the display domU rendered (via the dom0 relay
from 410) full-size on one X11 output. dom0 does not render the vehicle UI
here: it decodes a finished JPEG and copies it to the screen.

A stale, closed or unauthenticated relay never leaves the last frame on
screen as if it were live: the window switches to an explicit
"unavailable" status screen. The window is refreshed at least once per
second, and every refresh is reported on stdout as one JSON line so the
kiosk supervisor can tell a live output from a hung one.
"""
from __future__ import annotations

import argparse
import ctypes as C
import http.client
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import threading
import time

from .mjpeg import JpegParser, jpeg_dimensions

STREAM_PATH = "/streams/display.mjpeg"
REFRESH_SECONDS = 1.0
REPORT_SECONDS = 0.5


def read_secret(path):
    """Token file readable by the display account only (root-owned or own, not world/group-writable)."""
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o037 or info.st_size > 512
            or path.resolve() != path):
        raise ValueError("relay token file must be a private regular file")
    token = path.read_text(encoding="utf-8").strip()
    if not token or any(c in token for c in "\r\n\0"):
        raise ValueError("relay token file is empty or malformed")
    return token


class RelayFrames:
    """Pull the dom0 relay's latest-frame MJPEG (loopback) and keep only the newest frame."""

    def __init__(self, port, token, *, width, height, connect=http.client.HTTPConnection, clock=time.monotonic):
        self.port, self._token = port, token
        self.width, self.height = width, height
        self.connect, self.clock = connect, clock
        self.stop_event = threading.Event()
        self.changed = threading.Event()
        self.lock = threading.Lock()
        self.frame, self.sequence, self.received = None, 0, 0.0
        self.error = "waiting"
        self.connection = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="relay-frames")
        self.thread.start()

    def run(self):
        backoff = 0.5
        while not self.stop_event.is_set():
            backoff = 0.5 if self.session() else min(backoff*2, 5.0)
            self.stop_event.wait(backoff)

    def _fail(self, code):
        with self.lock:
            self.frame, self.error = None, code
        self.changed.set()

    def session(self):
        parser, accepted, connection = JpegParser(), False, None
        try:
            connection = self.connect("127.0.0.1", self.port, timeout=3)
            self.connection = connection
            connection.request("GET", STREAM_PATH, headers={"Authorization": f"Bearer {self._token}"})
            response = connection.getresponse()
            if response.status in (401, 403):
                self._fail("relay_auth_failed")
                return False
            if response.status != 200 or not response.getheader("Content-Type", "").startswith("multipart/x-mixed-replace"):
                self._fail("relay_protocol_error")
                return False
            while not self.stop_event.is_set():
                data = response.read1(65536)
                if not data:
                    self._fail("relay_stream_closed")
                    return accepted
                for frame in parser.push(data):
                    if jpeg_dimensions(frame) != (self.width, self.height):
                        self._fail("frame_size_mismatch")
                        continue
                    with self.lock:
                        self.frame, self.received, self.error = frame, self.clock(), None
                        self.sequence += 1
                    accepted = True
                    self.changed.set()
            return accepted
        except TimeoutError:
            # The relay keeps the connection open but sends nothing while the guest is down.
            self._fail("relay_stream_stalled")
        except (OSError, http.client.HTTPException):
            self._fail("relay_stream_closed" if accepted else "relay_unreachable")
        finally:
            self.connection = None
            if connection is not None:
                try: connection.close()
                except Exception: pass
        return accepted

    def latest(self):
        with self.lock:
            return self.sequence, self.frame, self.received, self.error

    def stop(self):
        self.stop_event.set()
        self.changed.set()
        connection = self.connection
        if connection is not None:
            try: connection.close()
            except Exception: pass
        if self.thread: self.thread.join(timeout=5)


class TurboDecoder:
    """Decode straight into the presenter's shared buffer (257's verified TurboJPEG)."""
    def __init__(self, library, width, height, *, factory=None):
        if factory is None:
            from .native_jpeg import JPEGDecoder as factory
        self.decoder = factory(library, width, height)
        self.pending = None

    def submit(self, jpeg):
        self.pending = jpeg

    def take_into(self, address, pitch):
        """Decode the submitted frame into the buffer; False when nothing new is pending."""
        jpeg, self.pending = self.pending, None
        if jpeg is None:
            return False
        self.decoder.decode(jpeg, address, pitch)
        return True

    def close(self): self.decoder.close()


class FFmpegDecoder:
    """Fallback without TurboJPEG: one persistent ffmpeg (MJPEG -> BGRX).

    ffmpeg emits frame N only when frame N+1 arrives, so this path shows the
    previous frame (one frame of extra latency) and never the very first one.
    It exists for hosts without the TurboJPEG library and for local tests.
    """
    def __init__(self, width, height, *, spawn=subprocess.Popen):
        self.width, self.height, self.size = width, height, width*height*4
        self.process = spawn(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                              "-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "32",
                              "-analyzeduration", "0", "-fpsprobesize", "0", "-f", "mjpeg", "-c:v", "mjpeg",
                              "-i", "pipe:0", "-fps_mode", "passthrough", "-threads", "1",
                              "-f", "rawvideo", "-pix_fmt", "bgr0", "pipe:1"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.lock = threading.Lock()
        self.raw, self.sequence, self.taken = None, 0, 0
        self.thread = threading.Thread(target=self._read, daemon=True, name="ffmpeg-decode")
        self.thread.start()

    def _read(self):
        buffer = bytearray()
        stdout = self.process.stdout
        while True:
            chunk = stdout.read1(1 << 20)
            if not chunk:
                return
            buffer += chunk
            while len(buffer) >= self.size:
                with self.lock:
                    self.raw, self.sequence = bytes(buffer[:self.size]), self.sequence+1
                del buffer[:self.size]

    def submit(self, jpeg):
        if self.process.poll() is not None:
            raise RuntimeError("ffmpeg decoder exited")
        try:
            self.process.stdin.write(jpeg)
            self.process.stdin.flush()
        except OSError:
            raise RuntimeError("ffmpeg decoder exited") from None

    def take_into(self, address, pitch):
        """Copy the newest decoded frame into the buffer; False when none arrived since last time."""
        with self.lock:
            raw, sequence = self.raw, self.sequence
        if raw is None or sequence == self.taken:
            return False
        self.taken = sequence
        row = self.width*4
        if pitch == row:
            C.memmove(address, raw, self.size)
        else:
            for y in range(self.height):
                C.memmove(address + y*pitch, raw[y*row:(y+1)*row], row)
        return True

    def close(self):
        if self.process.poll() is None:
            try: self.process.stdin.close()
            except OSError: pass
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()


def notice_lines(code):
    return ["GUEST DISPLAY UNAVAILABLE",
            f"reason: {code}",
            "The display domU frame is not arriving. This is not the vehicle screen.",
            "dom0 keeps waiting and shows the frame again as soon as it is fresh."]


class FrameOutput:
    """Single-threaded presenter loop: every X11 call happens on this thread."""

    def __init__(self, relay, decoder, presenter, *, stale_ms=2000, clock=time.monotonic, report=None):
        self.relay, self.decoder, self.presenter = relay, decoder, presenter
        self.stale = stale_ms / 1000
        self.clock = clock
        self.report = report or (lambda line: None)
        self.mode, self.shown, self.submitted, self.code = None, None, None, "waiting"
        self.presented, self.reported, self.reported_mode = 0.0, 0.0, None

    def step(self, now=None):
        now = self.clock() if now is None else now
        sequence, frame, received, error = self.relay.latest()
        live = frame is not None and 0 <= now - received < self.stale
        decoded = False
        if live:
            try:
                if sequence != self.submitted:
                    self.decoder.submit(frame)
                    self.submitted = sequence
                decoded = self.decoder.take_into(self.presenter.address, self.presenter.pitch)
            except (ValueError, RuntimeError):
                self._notice("frame_decode_failed", now)
        if decoded:
            self.presenter.present()
            self.mode, self.shown, self.code, self.presented = "live", self.submitted, None, now
        elif not live and self.mode != "unavailable":
            self._notice(error or "frame_stale", now)
        elif now - self.presented >= REFRESH_SECONDS:
            # Periodic refresh proves the output still works even without new frames.
            if self.mode == "live":
                self.presenter.present()
                self.presented = now
            else:
                self._notice(self.code or error or "frame_stale", now)
        # Report a live/unavailable change at once; otherwise every REPORT_SECONDS.
        if self.mode != self.reported_mode or now - self.reported >= REPORT_SECONDS:
            self.reported, self.reported_mode = now, self.mode
            self.report({"schema_version": 1, "alive": True, "renderer": "guest_frames",
                         "presented_monotonic": self.presented, "content": self.mode or "waiting",
                         "frame_sequence": self.shown,
                         "frame_age_ms": int((now - received)*1000) if frame is not None else None,
                         "error": None if self.mode == "live" else self.code})

    def _notice(self, code, now):
        self.presenter.notice(notice_lines(code))
        self.mode, self.code, self.presented = "unavailable", code, now


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--display", default=os.environ.get("DISPLAY", ":0"))
    p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--relay-port", type=int, default=8084)
    p.add_argument("--relay-token-file", required=True)
    p.add_argument("--decoder", choices=("turbojpeg", "ffmpeg"), default="turbojpeg")
    p.add_argument("--library", help="absolute TurboJPEG library path (turbojpeg decoder)")
    p.add_argument("--stale-ms", type=int, default=2000)
    args = p.parse_args(argv)

    def report(value):
        sys.stdout.write(json.dumps(value) + "\n")
        sys.stdout.flush()

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    relay = decoder = presenter = None
    try:
        if not (1 <= args.relay_port <= 65535 and 500 <= args.stale_ms <= 5000):
            raise ValueError("relay port or stale window out of range")
        token = read_secret(args.relay_token_file)
        from .native_x11 import SharedPresenter
        presenter = SharedPresenter(args.width, args.height, args.x, args.y, display=args.display)
        if args.decoder == "turbojpeg":
            if not args.library:
                raise ValueError("turbojpeg decoder requires --library")
            decoder = TurboDecoder(args.library, args.width, args.height)
        else:
            decoder = FFmpegDecoder(args.width, args.height)
        relay = RelayFrames(args.relay_port, token, width=args.width, height=args.height)
        relay.start()
        output = FrameOutput(relay, decoder, presenter, stale_ms=args.stale_ms, report=report)
        while not stop.is_set():
            relay.changed.wait(0.2)
            relay.changed.clear()
            output.step()
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        report({"schema_version": 1, "alive": False, "renderer": "guest_frames",
                "error": "frame_output_failed", "detail": type(error).__name__})
        return 1
    finally:
        if relay: relay.stop()
        if decoder: decoder.close()
        if presenter: presenter.close()


if __name__ == "__main__":
    raise SystemExit(main())
