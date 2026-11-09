"""Opt-in USB-only phone entry point. Existing feed stays on loopback.

Pairing uses a root-visible, single-use code (120s/5 attempts) and one read-only
browser session (8h). No VM commands, new feed, HDMI, MQTT or Gateway ownership.
The listener follows the phone link across cable re-plugs (393): see PhoneLink.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .mobile_network import resolve, bind
from .mobile_upstream import LocalFeed, FeedUnavailable, allowed_path

COOKIE = "xenova_mobile"
RUNTIME = Path("/run/xenova-display-mobile")
CODE_SECONDS, SESSION_SECONDS, ATTEMPTS = 120, 8 * 3600, 5
# Link following: kernel notifications wake the check at once; the slow re-check also
# catches a missed notification or an edited phone address in the root config.
RECHECK_SECONDS, SETTLE_SECONDS, GRACE_SECONDS = 30, 0.5, 3
RTMGRP_LINK, RTMGRP_IPV4_IFADDR = 0x1, 0x10
PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Xenova 차량 화면 연결</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;padding:24px;background:#0e141d;color:#f5f1e8;font:16px system-ui,sans-serif}
main{width:min(100%,380px);padding:28px;border:1px solid #354353;border-radius:18px;background:#19232f}small{color:#c0e1e1;letter-spacing:.12em}h1{font-size:24px}p{color:#a9b3c1;font-size:14px;line-height:1.6}label{display:block;margin:24px 0 8px}input{width:100%;padding:14px;border:1px solid #354353;border-radius:10px;background:#0e141d;color:#f5f1e8;font:28px monospace;letter-spacing:.2em}button{width:100%;margin-top:16px;padding:14px;border:0;border-radius:10px;background:#c0e1e1;color:#0e141d;font:600 16px system-ui}a{color:#c0e1e1}
</style><main><small>XENOVA / VEHICLE DISPLAY</small><h1>차량 화면 연결</h1>
<p>관리 터미널에서 확인한 6자리 연결번호를 입력해 주세요. 번호는 2분 동안 한 번만 사용할 수 있어요.</p>
<form method="post" action="/connect"><label for="code">연결번호</label>
<input id="code" name="code" inputmode="numeric" pattern="[0-9]{6}" minlength="6" maxlength="6" autocomplete="one-time-code" required>
<button type="submit">화면 연결</button></form></main></html>"""
PENDING_PAGE = (PAGE.split("<p>", 1)[0].replace("<h1>차량 화면 연결</h1>", "<h1>화면 준비 중</h1>")
    + '<p>연결은 확인됐지만 표시 서비스에 아직 연결할 수 없어요. 잠시 후 다시 열어 주세요. 계속되면 운영자에게 알려 주세요.</p>'
      '<a href="/display/mobile">다시 열기</a></main></html>')


class Pairing:
    def __init__(self, *, clock=time.monotonic, publish=lambda _: None):
        self.clock, self.publish = clock, publish
        self.code, self.code_until, self.tries = "", 0, 0
        self.session, self.session_until = "", 0

    def rotate(self):
        # Operator rotation also revokes the previous phone session.
        self.code, self.code_until, self.tries = f"{secrets.randbelow(1000000):06d}", self.clock() + CODE_SECONDS, 0
        self.session, self.session_until = "", 0
        self.publish(self.code)

    def valid(self, value):
        return bool(self.session and self.clock() < self.session_until
                    and hmac.compare_digest(value.encode(), self.session.encode()))

    def pair(self, code):
        self.tries += 1
        if (not self.code or self.clock() >= self.code_until or self.tries > ATTEMPTS
                or not re.fullmatch(r"[0-9]{6}", code)
                or not hmac.compare_digest(code, self.code)):
            return None
        self.code = ""
        self.publish(None)
        self.session, self.session_until = secrets.token_urlsafe(32), self.clock() + SESSION_SECONDS
        return self.session


def create_app(endpoint, feed, pairing, *, rotate_signal=False):
    @asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        if rotate_signal:
            # Schedule rotation between requests, never re-enter pairing from a Python signal handler.
            loop.add_signal_handler(signal.SIGUSR1, pairing.rotate)
        try:
            yield
        finally:
            if rotate_signal:
                loop.remove_signal_handler(signal.SIGUSR1)

    app = FastAPI(title="Xenova phone display access", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    # PhoneLink replaces this when the USB address changes; requests always see the current one.
    app.state.endpoint = endpoint

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        endpoint = app.state.endpoint
        if (not request.client or request.client.host != endpoint.phone_ip
                or request.headers.get("host") != endpoint.host):
            response = JSONResponse({"code": "PHONE_LINK_REQUIRED"}, status_code=403)
        elif request.method not in ("GET", "POST"):
            response = Response(status_code=405)
        elif request.method == "POST" and request.headers.get("origin") != endpoint.origin:
            response = JSONResponse({"code": "PHONE_ORIGIN_REQUIRED"}, status_code=403)
        else:
            response = await call_next(request)
        # Native form navigation under no-referrer sends Origin: null in Chrome.
        # Keep the exact Origin check; allow referrers only within this origin
        # on the pairing document so its form supplies the real same-origin value.
        referrer_policy = "same-origin" if request.url.path == "/connect" else "no-referrer"
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": referrer_policy,
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.get("/connect")
    def connect_page(request: Request):
        if pairing.valid(request.cookies.get(COOKIE, "")):
            return RedirectResponse("/display/mobile", status_code=303)
        return HTMLResponse(PAGE)

    @app.post("/connect")
    async def connect_phone(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            return Response(status_code=415)
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > 64:
                return Response(status_code=413)
            body.extend(part)
        try:
            form = parse_qs(body.decode("ascii"), strict_parsing=True, max_num_fields=1)
            code = form["code"][0] if set(form) == {"code"} and len(form["code"]) == 1 else ""
        except (ValueError, KeyError, UnicodeError):
            code = ""
        session = pairing.pair(code)
        if not session:
            return HTMLResponse(PAGE.replace('<form method=',
                '<p role="alert">번호가 다르거나 만료됐어요. 5회 실패했거나 2분이 지났다면 새 번호를 발급받아 주세요.</p><form method='), status_code=401)
        response = RedirectResponse("/display/mobile", status_code=303)
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict", secure=False,
                            max_age=SESSION_SECONDS, path="/")
        return response

    @app.get("/{path:path}")
    def read(request: Request, path: str):
        path = "/" + path
        if path == "/":
            return RedirectResponse("/display/mobile", status_code=303)
        # Scope is checked before auth; do not expose any command/state/internal path.
        if not allowed_path(path) or request.url.query or "%" in request.scope.get("raw_path", b"").decode("latin1"):
            return Response(status_code=404)
        if not pairing.valid(request.cookies.get(COOKIE, "")):
            if path == "/display/mobile":
                return RedirectResponse("/connect", status_code=303)
            return JSONResponse({"code": "PHONE_SESSION_REQUIRED"}, status_code=401)
        try:
            upstream = feed.get(path)
            if path == "/display/mobile" and upstream.status != 200:
                return HTMLResponse(PENDING_PAGE, status_code=503)
            return Response(upstream.body, status_code=upstream.status, headers={"Content-Type": upstream.content_type})
        except FeedUnavailable as error:
            if path == "/display/mobile":
                return HTMLResponse(PENDING_PAGE, status_code=503)
            return JSONResponse({"code": error.code}, status_code=503)

    return app


def root_config(path):
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_mode & 0o077 or metadata.st_size > 4096):
        raise ValueError("root-only mobile configuration required")
    return json.loads(path.read_text())


def publish_code(code):
    path = RUNTIME / "pairing-code"
    if code is None:
        path.unlink(missing_ok=True)
        return
    temporary = RUNTIME / "pairing-code.new"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(code + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_endpoint(endpoint):
    """Show the current phone URL to the operator; no file while there is no usable link."""
    path = RUNTIME / "endpoint.json"
    if endpoint is None:
        path.unlink(missing_ok=True)
        return
    temporary = RUNTIME / "endpoint.json.new"
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps({"url": endpoint.origin + "/display/mobile", "interface": endpoint.interface,
                "pairing_ttl_s": CODE_SECONDS, "session_ttl_s": SESSION_SECONDS}) + "\n")
        # Readers (display_mode, operators) never see a half-written file.
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_link(config_path):
    """The verified endpoint now, or None while the cable is out or the link is unusable."""
    try:
        return resolve(root_config(config_path))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def follow(previous, listening, found):
    """How the listener follows the phone link after it is read again.

    previous: the endpoint last served. found: the endpoint now, or None (no usable link).
    """
    if found is None:
        return "pause" if listening else "wait"
    if listening and found == previous:
        return "keep"
    if (found.local_ip, found.phone_ip, found.port) == (previous.local_ip, previous.phone_ip, previous.port):
        # Same URL on a re-plugged (new) device: the phone's cookie still matches, keep its session.
        return "rebind"
    # A new URL. The browser cookie belongs to the old address, so a new code is needed.
    return "new_address"


def make_server(app):
    import uvicorn
    return uvicorn.Server(uvicorn.Config(app, proxy_headers=False, access_log=False, log_level="warning",
        workers=1, limit_concurrency=16, timeout_graceful_shutdown=GRACE_SECONDS))


def link_events():
    """Kernel link/IPv4-address notifications. Only "something changed" is used, never their content."""
    events = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW | socket.SOCK_NONBLOCK, socket.NETLINK_ROUTE)
    try:
        events.bind((0, RTMGRP_LINK | RTMGRP_IPV4_IFADDR))
        return events
    except Exception:
        events.close()
        raise


def say(message):
    # Fixed messages only: never an address, code, config value or traceback.
    print(message, flush=True)


class PhoneLink:
    """Keep the phone listener on the verified USB link across cable re-plugs (393).

    - Cable out or no usable address: stop listening, remove endpoint.json, wait.
    - Back on the same address (a new kernel device): listen again; the paired session stays.
    - Back on a new address: listen there, publish the new URL and a new pairing code.
    The trust rules do not change: same root config, USB device check, phone IP, Host/Origin.
    """

    def __init__(self, app, pairing, endpoint, sock, *, read, listen=bind, publish=publish_endpoint,
                 server=make_server, recheck=RECHECK_SECONDS, settle=SETTLE_SECONDS):
        self.app, self.pairing, self.endpoint, self.sock = app, pairing, endpoint, sock
        self.read, self.listen, self.publish, self.server = read, listen, publish, server
        self.recheck, self.settle = recheck, settle
        self.changed = None

    def _drain(self, events):
        with suppress(OSError):
            while events.recv(65536):
                pass
        self.changed.set()

    async def _next_change(self):
        with suppress(TimeoutError):
            await asyncio.wait_for(self.changed.wait(), self.recheck)
        await asyncio.sleep(self.settle)  # a re-plug is a burst: link up, then the DHCP address
        self.changed.clear()

    async def run(self, events=None):
        loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        # One handler for the whole run, also while there is no listener (and no app lifespan).
        loop.add_signal_handler(signal.SIGUSR1, self.pairing.rotate)
        if events is not None:
            loop.add_reader(events.fileno(), self._drain, events)
        server = task = None
        try:
            while True:
                if self.sock is not None and task is None:
                    server = self.server(self.app)
                    task = asyncio.create_task(server.serve(sockets=[self.sock]))
                waiter = asyncio.create_task(self._next_change())
                done, _ = await asyncio.wait({t for t in (task, waiter) if t is not None},
                                             return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    waiter.cancel()
                    # uvicorn stopped by itself: a stop signal or a failed start.
                    return 0 if server.started else 1
                found = await asyncio.to_thread(self.read)
                action = follow(self.endpoint, task is not None, found)
                if action in ("keep", "wait"):
                    continue
                if task is not None:
                    server.should_exit = True
                    await task  # closes the listener on the old device
                    server = task = self.sock = None
                if action == "pause":
                    self.publish(None)
                    say("Phone link lost; waiting for the USB address.")
                    continue
                try:
                    self.sock = self.listen(found)
                except OSError:
                    self.publish(None)
                    say("Phone link is back but the listener could not bind yet; retrying.")
                    continue
                self.app.state.endpoint = self.endpoint = found
                if action == "new_address":
                    self.pairing.rotate()
                self.publish(found)
                say("Phone link back on the same address; the paired session continues." if action == "rebind"
                    else "Phone link moved to a new address; a new URL and pairing code were issued.")
        finally:
            if events is not None:
                loop.remove_reader(events.fileno())
            loop.remove_signal_handler(signal.SIGUSR1)
            if task is not None and not task.done():
                task.cancel()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/xenova/display-mobile.json"))
    args = parser.parse_args(argv)
    sock = None
    lock = None
    owned = False
    link = events = None
    try:
        if os.geteuid() != 0:
            raise ValueError("trusted service launcher required")
        import fcntl
        info = RUNTIME.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("private runtime directory required")
        lock = os.open(RUNTIME / "access.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        endpoint = resolve(root_config(args.config))
        feed = LocalFeed(os.environ.get("XENOVA_DISPLAY_TOKEN", ""))
        sock = bind(endpoint)
        pairing = Pairing(publish=publish_code)
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
        pairing.rotate()
        owned = True
        publish_endpoint(endpoint)
        link = PhoneLink(create_app(endpoint, feed, pairing), pairing, endpoint, sock,
                         read=lambda: read_link(args.config))
        sock = None  # PhoneLink owns the listener from here on
        try:
            events = link_events()
        except (OSError, AttributeError):
            events = None  # still follows the link through the slow re-check
        return asyncio.run(link.run(events))
    except Exception:
        # Never log config values, credentials, pairing code, raw HTTP or traceback.
        print("Mobile display startup failed; verify USB interface, private configuration and existing feed.")
        return 1
    finally:
        if sock is not None:
            sock.close()
        if link is not None and link.sock is not None:
            with suppress(OSError):
                link.sock.close()
        if events is not None:
            events.close()
        if owned:
            with suppress(OSError):
                (RUNTIME / "pairing-code").unlink(missing_ok=True)
                (RUNTIME / "endpoint.json").unlink(missing_ok=True)
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
