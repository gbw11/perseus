"""Independent latest-frame X11 capture and authenticated MJPEG demo receiver."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import threading
import time
from uuid import uuid4
import uvicorn

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .outputs import RuntimeConfig


MAX_JPEG = 2 * 1024 * 1024


class FrameSignal:
    """Broadcast a new latest frame without a frame queue or per-client polling."""
    def __init__(self, loop):
        self.loop = loop
        self.event = asyncio.Event()

    def notify(self):
        try:
            self.loop.call_soon_threadsafe(self._publish)
        except RuntimeError:
            # A late capture notification must not revive a closed event loop.
            pass

    def _publish(self):
        previous, self.event = self.event, asyncio.Event()
        previous.set()


class JpegParser:
    def __init__(self): self.buffer = bytearray()
    def push(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start == -1:
                self.buffer = self.buffer[-1:]
                break
            if start: del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end == -1:
                if len(self.buffer) > MAX_JPEG: self.buffer.clear()
                break
            if end+2 <= MAX_JPEG: frames.append(bytes(self.buffer[:end+2]))
            del self.buffer[:end+2]
        return frames


def capture_command(config, output):
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "x11grab", "-draw_mouse", "0", "-framerate", str(config.fps),
        "-video_size", f"{output.width}x{output.height}",
        "-i", f"{config.display}+{output.x},{output.y}", "-an",
        "-filter_threads", "1", "-vf",
        f"scale={config.stream_width}:{config.stream_height}:flags=fast_bilinear:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={config.stream_width}:{config.stream_height}:(ow-iw)/2:(oh-ih)/2,format=yuvj420p",
        "-c:v", "mjpeg", "-q:v", str(config.jpeg_quality), "-threads", "1",
        "-flush_packets", "1", "-f", "mjpeg", "pipe:1"]


class Capture:
    def __init__(self, config, output, status_path):
        self.config, self.output, self.status_path = config, output, Path(status_path)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.sequence = 0
        self.bytes_total = 0
        self.captured_mono = 0
        self.capture_epoch = str(uuid4())
        self.error = "waiting"
        self.process = None
        self.thread = None
        self.changed = None

    def layout(self):
        try:
            status = json.loads(self.status_path.read_text())
            if not 0 <= time.monotonic() - status["updated_monotonic"] < 6:
                return None
            output = status["outputs"][self.output.output_id]
            if output["status"] != "mapped" or not output["renderer_alive"]:
                return None
            if output["rectangle"] != [self.output.x,self.output.y,self.output.width,self.output.height]:
                return None
            if output["connector"] != self.output.connector or status["backend"] != self.config.backend:
                return None
            return {**output, "layout_epoch": status["layout_epoch"]}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def start(self):
        try: self.changed = FrameSignal(asyncio.get_running_loop())
        except RuntimeError: pass  # Existing synchronous diagnostic callers.
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"capture-{self.output.output_id}")
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            layout = self.layout()
            if layout is None:
                self.error = "output_unavailable"
                self.stop_event.wait(0.5)
                continue
            self.capture_epoch = str(uuid4())
            parser = JpegParser()
            try:
                self.process = subprocess.Popen(capture_command(self.config,self.output), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                while not self.stop_event.is_set():
                    current = self.layout()
                    if current is None or current["layout_epoch"] != layout["layout_epoch"]:
                        self.error = "output_unavailable"
                        break
                    if not select.select([self.process.stdout], [], [], 1)[0]:
                        continue
                    # read1 returns available pipe data rather than waiting for a whole 64KiB block.
                    data = self.process.stdout.read1(65536)
                    if not data: break
                    current = self.layout()
                    if current is None or current["layout_epoch"] != layout["layout_epoch"]:
                        self.error = "output_unavailable"
                        break
                    for frame in parser.push(data):
                        with self.lock:
                            self.frame, self.captured_mono = frame, time.monotonic()
                            self.sequence += 1
                            self.bytes_total += len(frame)
                            self.error = None
                        if self.changed: self.changed.notify()
            except OSError:
                self.error = "encoder_unavailable"
            finally:
                process, self.process = self.process, None
                if process:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                    if process.stdout: process.stdout.close()
                with self.lock:
                    self.frame = None
                    if self.error is None: self.error = "encoder_stopped"
                if self.changed: self.changed.notify()
            self.stop_event.wait(1)

    def stop(self):
        self.stop_event.set()
        process = self.process
        if process:
            try: process.terminate()
            except ProcessLookupError: pass
        if self.thread: self.thread.join(timeout=5)

    def snapshot(self):
        layout = self.layout()
        with self.lock:
            age = (time.monotonic()-self.captured_mono)*1000 if self.sequence else None
            ready = layout is not None and self.error is None and age is not None and age < 2000
            metadata = {"stream_id": self.output.output_id, "output_id": self.output.output_id,
                "capture_source_id": f"x11-root:{self.output.connector}", "capture_epoch": self.capture_epoch,
                "rectangle": [self.output.x,self.output.y,self.output.width,self.output.height],
                "width": self.config.stream_width, "height": self.config.stream_height, "codec": "mjpeg", "fps": self.config.fps,
                "frame_sequence": self.sequence, "frame_age_ms": int(age) if age is not None else None,
                "status": "live" if ready else "unavailable", "error": None if ready else self.error or "frame_stale",
                "bytes_total": self.bytes_total, "backend": self.config.backend,
                "physical_verified": bool(layout and layout.get("physical_verified")),
                "render_revision": None, "render_correlation": "unverified",
                "reported_dom_revision": layout.get("render_revision") if layout else None}
            # DOM revisions cannot be proven to correspond exactly to a particular encoded X11 frame.
            return metadata, self.frame if ready else None


class Login(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def create_stream_app(config=None, status_path=None, token=None, *, allow_virtual=False, capture_factory=Capture):
    config = config or RuntimeConfig.load(os.environ["XENOVA_DISPLAY_RUNTIME_CONFIG"])
    status_path = status_path or os.environ.get("XENOVA_DISPLAY_RUNTIME_STATUS", "/run/xenova/display-runtime.json")
    token = token or os.environ.get("XENOVA_STREAM_TOKEN", "")
    if not token: raise ValueError("XENOVA_STREAM_TOKEN is required")
    if config.backend == "virtual_x11" and not (allow_virtual or os.environ.get("XENOVA_STREAM_ALLOW_VIRTUAL") == "1"):
        raise ValueError("virtual capture must be explicitly enabled for testing")
    captures = {o.output_id: capture_factory(config,o,status_path) for o in config.outputs}
    sessions = {}

    @asynccontextmanager
    async def lifespan(app):
        for capture in captures.values(): capture.start()
        try: yield
        finally:
            for capture in captures.values(): await asyncio.to_thread(capture.stop)

    app = FastAPI(title="Perseus demo screen relay", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.captures = captures
    app.state.stopping = threading.Event()

    def authenticated(request):
        bearer = hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {token}".encode())
        cookie = sessions.get(request.cookies.get("xenova_stream", ""), 0) > time.monotonic()
        return bearer or cookie

    @app.middleware("http")
    async def auth(request, call_next):
        public = request.method == "GET" and request.url.path == "/demo"
        login = request.method == "POST" and request.url.path == "/auth"
        if app.state.stopping.is_set(): return JSONResponse({"code":"Stopping"},status_code=503)
        if not (public or login or authenticated(request)): return JSONResponse({"code":"Unauthorized"}, status_code=401)
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
        response.set_cookie("xenova_stream", key, httponly=True, samesite="strict", max_age=3600,
                            secure=request.url.scheme == "https")
        return response

    @app.get("/api/v1/streams")
    def statuses(): return {"streams": [c.snapshot()[0] for c in captures.values()]}

    @app.get("/streams/{output_id}.mjpeg")
    async def frames(output_id: str, request: Request):
        if output_id not in captures: raise HTTPException(404)
        capture = captures[output_id]
        async def latest():
            last = None
            while not app.state.stopping.is_set() and not await request.is_disconnected():
                if not authenticated(request): return
                signal = getattr(capture, "changed", None)
                # Subscribe before reading the frame: publishing between these
                # operations then cannot leave the receiver waiting on a new event.
                pending = signal.event if signal is not None else None
                metadata, frame = capture.snapshot()
                identity = (metadata["capture_epoch"],metadata["frame_sequence"])
                if frame is not None and identity != last:
                    last = identity
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n" +
                           f"Content-Length: {len(frame)}\r\nX-Frame-Sequence: {metadata['frame_sequence']}\r\n\r\n".encode() + frame + b"\r\n")
                if pending is None:
                    await asyncio.sleep(0.05)
                else:
                    # Even without new frames, recheck auth, disconnect and output
                    # freshness periodically. A notification does not bypass checks.
                    try: await asyncio.wait_for(pending.wait(), timeout=0.5)
                    except asyncio.TimeoutError: pass
        return StreamingResponse(latest(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"X-Accel-Buffering":"no"})

    @app.get("/demo", response_class=HTMLResponse)
    def viewer(): return (Path(__file__).parent / "viewer.html").read_text(encoding="utf-8")
    return app


class StreamServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        # Close infinite multipart responses before Uvicorn waits for active requests.
        self.config.app.state.stopping.set()
        await super().shutdown(sockets)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8083)
    args = parser.parse_args()
    app = create_stream_app()
    server = StreamServer(uvicorn.Config(app,host=args.host,port=args.port,workers=1,
                                       log_level="info",access_log=False,timeout_graceful_shutdown=5))
    server.run()
