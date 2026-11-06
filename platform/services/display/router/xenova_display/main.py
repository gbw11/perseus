from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import logging
import os
import secrets
import time
from pathlib import Path

from dom0_manager.observations import ObservationFile
from dom0_manager.config import Settings
from dom0_manager.drivers import build_driver
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import FeedSettings
from .mqtt import MqttSource
from .state import StateStore
from .policy import DisplayPolicy, RenderAck, Selection
from .gateway import GatewayReader
from .shared import SharedFollower

log = logging.getLogger(__name__)


def create_app(settings=None, *, driver=None, store=None, mqtt_factory=MqttSource, web_root=None,
               gateway_factory=GatewayReader):
    settings = settings or FeedSettings.from_env()
    if driver is None:
        shared_path = os.environ.get("XENOVA_OBSERVATIONS_PATH")
        driver = ObservationFile(shared_path) if shared_path else build_driver(Settings.from_env())
    store = store or StateStore(settings)
    policy = DisplayPolicy(settings, store.epoch, clock=store.clock)
    sessions = {}

    async def poll_gateway():
        reader = None
        unavailable = False
        while True:
            started = time.monotonic()
            try:
                if reader is None:
                    # Built lazily and retried: a missing Gateway client or host boot id
                    # (e.g. a development PC) leaves driving state unknown instead of
                    # failing startup. Without a reader there is no observation to expire.
                    reader = gateway_factory(settings)
                value, observed = await asyncio.to_thread(reader.read)
                store.gateway_success(value, observed_mono=observed)
                unavailable = False
            except Exception as error:
                if reader is not None:
                    store.gateway_failure()
                if not unavailable:
                    log.warning("Gateway display context unavailable (%s)", type(error).__name__)
                unavailable = True
            # One outstanding bounded call at a time; skip catch-up polls.
            await asyncio.sleep(max(0, 1 - (time.monotonic() - started)))

    async def poll():
        unavailable = False
        follower = SharedFollower(driver) if isinstance(driver, ObservationFile) else None
        interval = .25 if follower is not None else 1
        while True:
            started = time.monotonic()
            try:
                if follower is not None:
                    update = await asyncio.to_thread(follower.read_changed)
                    if update is not None:
                        domains, observed = update
                        store.poll_success(domains, observed_mono=observed)
                else:
                    # Explicitly injected test/probe adapters only.
                    domains = await asyncio.to_thread(driver.list_domains)
                    store.poll_success(domains)
                unavailable = False
            except Exception as error:
                if follower is not None:
                    follower.invalidate()
                store.poll_failure()
                if not unavailable:
                    log.warning("Shared VM display observation unavailable (%s)", type(error).__name__)
                unavailable = True
            await asyncio.sleep(max(0, interval - (time.monotonic() - started)))

    @asynccontextmanager
    async def lifespan(app):
        source = mqtt_factory(store)
        # Driving state is read only from the Gateway context; an unavailable
        # Gateway is reported as unknown, never replaced by MQTT/retained or raw
        # Authority-file reads.
        tasks = [asyncio.create_task(poll()), asyncio.create_task(poll_gateway())]
        try:
            source.start()
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await asyncio.to_thread(source.stop)

    app = FastAPI(title="Xenova read-only display feed", lifespan=lifespan)
    app.state.store = store
    app.state.policy = policy

    def authorized(value):
        return hmac.compare_digest(value.encode(), f"Bearer {settings.token}".encode())

    @app.middleware("http")
    async def service_auth(request, call_next):
        path = request.url.path
        read_screen = request.method == "GET" and (
            path in {"/display/monitor-1", "/display/monitor-2", "/display/mobile", "/api/v1/display/presentation"}
            or path.startswith("/assets/"))
        ack_screen = request.method == "POST" and path in {
            "/api/v1/display/outputs/monitor-1/ack", "/api/v1/display/outputs/monitor-2/ack"}
        cookie_ok = sessions.get(request.cookies.get("xenova_display", ""), 0) > time.monotonic()
        if not authorized(request.headers.get("authorization", "")) and not (cookie_ok and (read_screen or ack_screen)):
            return JSONResponse({"code": "Unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.post("/api/v1/display/session", status_code=204)
    async def browser_session(request: Request):
        # Called by a trusted launcher/browser automation context, never by page JS with a service token.
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key] <= now:
                sessions.pop(key)
        if len(sessions) >= 32:
            sessions.pop(next(iter(sessions)))
        key = secrets.token_urlsafe(32)
        sessions[key] = now + 8 * 3600
        response = Response(status_code=204)
        response.set_cookie("xenova_display", key, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=8 * 3600, path="/")
        return response

    @app.get("/api/v1/display/presentation")
    def presentation():
        return policy.presentation(store.snapshot())

    @app.post("/api/v1/display/selection")
    def selection(request: Selection):
        try:
            policy.select(request)
            return policy.presentation(store.snapshot())
        except LookupError as error:
            raise HTTPException(400, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/v1/display/outputs/{output_id}/ack", status_code=204)
    def render_ack(output_id: str, request: RenderAck):
        try:
            policy.acknowledge(output_id, request, store.snapshot())
            return Response(status_code=204)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    if web_root is None:
        web_root = os.environ.get("XENOVA_DISPLAY_WEB_ROOT")
    if web_root:
        root = Path(web_root).resolve()
        if not (root / "index.html").is_file():
            raise ValueError("XENOVA_DISPLAY_WEB_ROOT must contain a built index.html")
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="display-assets")

        @app.get("/display/{output_id}", include_in_schema=False)
        def monitor_page(output_id: str):
            if output_id not in {"monitor-1", "monitor-2", "mobile"}:
                raise HTTPException(404)
            return FileResponse(root / "index.html")

    @app.get("/api/v1/display/state")
    def state():
        return store.snapshot()

    @app.websocket("/ws/v1/display/state")
    async def state_stream(websocket: WebSocket):
        if not authorized(websocket.headers.get("authorization", "")):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            while True:
                await asyncio.wait_for(websocket.send_json(store.snapshot()), timeout=5)
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=1)
                    if message["type"] == "websocket.disconnect":
                        return
                    await websocket.close(code=1008, reason="read-only state stream")
                    return
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
            return

    return app
