"""410 Plan B, dom0 side: receive frames that the display domU already rendered.

dom0 does not build or render the vehicle UI on this path. It pulls one
authenticated MJPEG stream from the display domU (`guest_sender`), validates
every frame, keeps only the latest one and republishes it to the dom0-local
HDMI frame output. (The phone option renders the UI on the phone itself and
does not use this path.)

The domU is untrusted: frame size, JPEG structure, dimensions and rate are
bounded, and a stalled, closed or misbehaving guest is reported as
`unavailable` with a fixed error code, never as live.

The guest gets display data through `create_guest_data_app`: one read-only
path, its own guest token, proxied to the existing loopback feed. The feed's
service token stays in dom0.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import http.client
import ipaddress
import secrets
import threading
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .mjpeg import JpegParser, jpeg_dimensions
from .mobile_upstream import FeedUnavailable
from .stream import FrameSignal

STREAM_PATH = "/frames.mjpeg"
GUEST_DATA_PATH = "/api/v1/display/presentation"


class GuestSource(BaseModel):
    """Where dom0 pulls the display domU's frames from (the guest's Xen-network address)."""
    model_config = ConfigDict(extra="forbid")
    host: ipaddress.IPv4Address
    port: int = Field(default=8090, ge=1, le=65535)
    width: int = Field(default=1280, ge=640, le=1920)
    height: int = Field(default=720, ge=480, le=1080)
    max_fps: int = Field(default=5, ge=1, le=15)
    stale_ms: int = Field(default=2000, ge=500, le=5000)
    allow_loopback: bool = False  # local tests only

    @model_validator(mode="after")
    def private_guest(self):
        if self.host.is_unspecified or self.host.is_multicast:
            raise ValueError("a concrete guest address is required")
        if self.host.is_loopback and not self.allow_loopback:
            raise ValueError("loopback guest source is test-only")
        if not (self.host.is_private or self.host.is_loopback):
            raise ValueError("guest frames must come from the private Xen network")
        return self


def valid_token(token):
    if not isinstance(token, str) or not token.strip() or len(token) > 512 or any(c in token for c in "\r\n\0"):
        raise ValueError("valid token required")
    return token


class GuestFrameReceiver:
    """Pull the display domU's MJPEG stream and keep only the latest valid frame.

    Same start/stop/snapshot/changed surface as `stream.Capture`, so the
    latest-frame consumers do not care whether pixels came from dom0's X11
    or from the display domU.
    """

    def __init__(self, source, token, *, connect=http.client.HTTPConnection, clock=time.monotonic):
        self.source, self._token = source, valid_token(token)
        self.connect, self.clock = connect, clock
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame, self.received = None, 0.0
        self.sequence = self.bytes_total = self.dropped = self.rejected = 0
        self.epoch = str(uuid4())
        self.error = "waiting"
        self.connection = None
        self.thread = None
        self.changed = None

    def start(self):
        try: self.changed = FrameSignal(asyncio.get_running_loop())
        except RuntimeError: pass  # synchronous callers and tests
        self.thread = threading.Thread(target=self.run, daemon=True, name="guest-frames")
        self.thread.start()

    def run(self):
        backoff = 0.5
        while not self.stop_event.is_set():
            received = self.session()
            backoff = 0.5 if received else min(backoff*2, 5.0)
            self.stop_event.wait(backoff)

    def _fail(self, code):
        with self.lock:
            self.frame, self.error = None, code
        if self.changed: self.changed.notify()

    def _accept(self, frame):
        with self.lock:
            self.frame, self.received = frame, self.clock()
            self.sequence += 1
            self.bytes_total += len(frame)
            self.error = None
        if self.changed: self.changed.notify()

    def session(self):
        """One connection to the guest. Returns True when at least one frame was accepted."""
        self.epoch = str(uuid4())
        parser = JpegParser()
        accepted, last = False, None
        min_interval = 0.8 / self.source.max_fps  # tolerate jitter, refuse floods
        connection = None
        try:
            connection = self.connect(str(self.source.host), self.source.port, timeout=3)
            self.connection = connection
            connection.request("GET", STREAM_PATH, headers={"Authorization": f"Bearer {self._token}"})
            response = connection.getresponse()
            if response.status in (401, 403):
                self._fail("guest_auth_failed")
                return False
            if response.status != 200 or not response.getheader("Content-Type", "").startswith("multipart/x-mixed-replace"):
                self._fail("guest_protocol_error")
                return False
            while not self.stop_event.is_set():
                data = response.read1(65536)
                if not data:
                    self._fail("guest_stream_closed")
                    return accepted
                frames = parser.push(data)
                if parser.discarded:
                    with self.lock: self.rejected += parser.discarded
                    parser.discarded = 0
                for frame in frames:
                    if jpeg_dimensions(frame) != (self.source.width, self.source.height):
                        with self.lock: self.rejected += 1
                        self._fail("guest_frame_invalid")
                        continue
                    now = self.clock()
                    if last is not None and now - last < min_interval:
                        with self.lock: self.dropped += 1
                        continue
                    last = now
                    self._accept(frame)
                    accepted = True
            return accepted
        except TimeoutError:
            self._fail("guest_stream_stalled")
        except (OSError, http.client.HTTPException):
            self._fail("guest_stream_closed" if accepted else "guest_unreachable")
        finally:
            self.connection = None
            if connection is not None:
                try: connection.close()
                except Exception: pass
        return accepted

    def stop(self):
        self.stop_event.set()
        connection = self.connection
        if connection is not None:
            try: connection.close()  # unblocks a pending read
            except Exception: pass
        if self.thread: self.thread.join(timeout=5)

    def snapshot(self):
        with self.lock:
            age = (self.clock()-self.received)*1000 if self.sequence else None
            ready = self.frame is not None and age is not None and age < self.source.stale_ms
            metadata = {"stream_id": "display", "rendered_by": "domU", "capture_epoch": self.epoch,
                        "width": self.source.width, "height": self.source.height, "codec": "mjpeg",
                        "max_fps": self.source.max_fps, "frame_sequence": self.sequence,
                        "frame_age_ms": int(age) if age is not None else None,
                        "status": "live" if ready else "unavailable",
                        "error": None if ready else self.error or "frame_stale",
                        "bytes_total": self.bytes_total, "frames_dropped": self.dropped,
                        "frames_rejected": self.rejected}
            return metadata, (self.frame if ready else None)


class Login(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def create_relay_app(receiver, token, *, start_receiver=True):
    """dom0-local consumers (the HDMI frame output) read the latest guest frame here."""
    token = valid_token(token)
    sessions = {}

    @asynccontextmanager
    async def lifespan(app):
        if start_receiver: receiver.start()
        try: yield
        finally:
            if start_receiver: await asyncio.to_thread(receiver.stop)

    app = FastAPI(title="Perseus guest display relay", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.receiver = receiver
    app.state.stopping = threading.Event()

    def authenticated(request):
        bearer = hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {token}".encode())
        cookie = sessions.get(request.cookies.get("xenova_relay", ""), 0) > time.monotonic()
        return bearer or cookie

    @app.middleware("http")
    async def auth(request, call_next):
        login = request.method == "POST" and request.url.path == "/auth"
        if app.state.stopping.is_set(): return JSONResponse({"code": "Stopping"}, status_code=503)
        if not (login or authenticated(request)): return JSONResponse({"code": "Unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/auth", status_code=204)
    def login(body: Login, request: Request):
        if not hmac.compare_digest(body.token.encode(), token.encode()): raise HTTPException(401)
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key] <= now: sessions.pop(key)
        if len(sessions) >= 32: sessions.pop(next(iter(sessions)))
        key = secrets.token_urlsafe(32)
        sessions[key] = now+3600
        response = Response(status_code=204)
        response.set_cookie("xenova_relay", key, httponly=True, samesite="strict", max_age=3600,
                            secure=request.url.scheme == "https")
        return response

    @app.get("/api/v1/guest-display")
    def status(): return receiver.snapshot()[0]

    @app.get("/streams/display.mjpeg")
    async def frames(request: Request):
        async def latest():
            last = None
            while not app.state.stopping.is_set() and not await request.is_disconnected():
                if not authenticated(request): return
                signal = getattr(receiver, "changed", None)
                # Subscribe before reading so a publish in between still wakes us.
                pending = signal.event if signal is not None else None
                metadata, frame = receiver.snapshot()
                identity = (metadata["capture_epoch"], metadata["frame_sequence"])
                if frame is not None and identity != last:
                    last = identity
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n" +
                           f"Content-Length: {len(frame)}\r\nX-Frame-Sequence: {metadata['frame_sequence']}\r\n\r\n".encode()
                           + frame + b"\r\n")
                if pending is None:
                    await asyncio.sleep(0.05)
                else:
                    try: await asyncio.wait_for(pending.wait(), timeout=0.5)
                    except asyncio.TimeoutError: pass
        return StreamingResponse(latest(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"X-Accel-Buffering": "no"})

    return app


def create_guest_data_app(feed, guest_token):
    """Bound to dom0's Xen-bridge address: the display domU may read presentation data only.

    `feed` is a `mobile_upstream.LocalFeed`; it attaches the loopback feed's
    service token itself. The guest token only opens this one read path.
    """
    guest_token = valid_token(guest_token)
    expected = f"Bearer {guest_token}".encode()
    busy = threading.BoundedSemaphore(2)
    app = FastAPI(title="Perseus guest display data", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request, call_next):
        if not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected):
            return JSONResponse({"code": "Unauthorized"}, status_code=401)
        if request.method != "GET" or request.url.path != GUEST_DATA_PATH or request.url.query:
            return JSONResponse({"code": "NotFound"}, status_code=404)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get(GUEST_DATA_PATH)
    def presentation():
        if not busy.acquire(blocking=False):
            return JSONResponse({"code": "GuestDataBusy"}, status_code=503)
        try:
            result = feed.get(GUEST_DATA_PATH)
        except FeedUnavailable as error:
            return JSONResponse({"code": error.code}, status_code=503)
        finally:
            busy.release()
        if result.status != 200 or not result.content_type.startswith("application/json"):
            return JSONResponse({"code": "DISPLAY_FEED_UNAVAILABLE"}, status_code=503)
        # Body only: no upstream headers, cookies or redirects reach the guest.
        return Response(result.body, media_type="application/json")

    return app


class GuestRelayConfig(BaseModel):
    """/etc/xenova/guest-display.json (411). Token values live in separate private files."""
    model_config = ConfigDict(extra="forbid")
    guest: GuestSource
    guest_stream_token_file: str = Field(min_length=2, max_length=4096, pattern=r"^/")
    relay_token_file: str = Field(min_length=2, max_length=4096, pattern=r"^/")
    guest_data_token_file: str = Field(min_length=2, max_length=4096, pattern=r"^/")
    relay_port: int = Field(default=8084, ge=1, le=65535)
    data_bind: ipaddress.IPv4Address = ipaddress.IPv4Address("192.168.77.1")
    data_port: int = Field(default=8085, ge=1, le=65535)
    feed_port: int = Field(default=8082, ge=1, le=65535)

    @model_validator(mode="after")
    def bridge_only(self):
        if not self.data_bind.is_private or self.data_bind.is_unspecified or self.data_bind.is_loopback:
            raise ValueError("guest data path binds to dom0's Xen bridge address only")
        if len({self.relay_port, self.data_port, self.feed_port}) != 3:
            raise ValueError("relay, guest data and feed ports must differ")
        return self


def main(argv=None):
    import argparse
    from contextlib import nullcontext
    import os
    from pathlib import Path
    import signal
    import uvicorn
    from .frame_presenter import read_secret
    from .mobile_upstream import LocalFeed
    from .stream import StreamServer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/xenova/guest-display.json")
    args = parser.parse_args(argv)
    config = GuestRelayConfig.model_validate_json(Path(args.config).read_text(encoding="utf-8"))
    receiver = GuestFrameReceiver(config.guest, read_secret(config.guest_stream_token_file))
    relay = create_relay_app(receiver, read_secret(config.relay_token_file))
    data = create_guest_data_app(LocalFeed(os.environ.get("XENOVA_DISPLAY_TOKEN", ""), port=config.feed_port),
                                 read_secret(config.guest_data_token_file))
    # Relay: dom0-local consumers only. Guest data: dom0's Xen bridge address only.
    relay_server = StreamServer(uvicorn.Config(relay, host="127.0.0.1", port=config.relay_port, workers=1,
                                               log_level="warning", access_log=False, timeout_graceful_shutdown=5))
    data_server = uvicorn.Server(uvicorn.Config(data, host=str(config.data_bind), port=config.data_port, workers=1,
                                                log_level="warning", access_log=False))

    servers = (relay_server, data_server)
    for server in servers:
        # One handler stops both listeners; uvicorn's per-server capture would stop only one.
        server.capture_signals = nullcontext
        server.install_signal_handlers = lambda: None

    async def serve():
        loop = asyncio.get_running_loop()
        def stop():
            for server in servers: server.should_exit = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop)
        await asyncio.gather(*(server.serve() for server in servers))
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
