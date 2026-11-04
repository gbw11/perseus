"""Opt-in USB-only phone entry point. Existing feed stays on loopback.

Pairing uses a root-visible, single-use code (120s/5 attempts) and one read-only
browser session (8h). No VM commands, new feed, HDMI, MQTT or Gateway ownership.
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
import stat
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .mobile_network import resolve, bind
from .mobile_upstream import LocalFeed, FeedUnavailable, allowed_path

COOKIE = "xenova_mobile"
RUNTIME = Path("/run/xenova-display-mobile")
CODE_SECONDS, SESSION_SECONDS, ATTEMPTS = 120, 8 * 3600, 5
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

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if (not request.client or request.client.host != endpoint.phone_ip
                or request.headers.get("host") != endpoint.host):
            response = JSONResponse({"code": "PHONE_LINK_REQUIRED"}, status_code=403)
        elif request.method not in ("GET", "POST"):
            response = Response(status_code=405)
        elif request.method == "POST" and request.headers.get("origin") != endpoint.origin:
            response = JSONResponse({"code": "PHONE_ORIGIN_REQUIRED"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/xenova/display-mobile.json"))
    args = parser.parse_args(argv)
    sock = None
    lock = None
    owned = False
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
        (RUNTIME / "endpoint.json").write_text(json.dumps({"url": endpoint.origin + "/display/mobile",
            "interface": endpoint.interface, "pairing_ttl_s": CODE_SECONDS, "session_ttl_s": SESSION_SECONDS}) + "\n")
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(create_app(endpoint, feed, pairing, rotate_signal=True), proxy_headers=False,
            access_log=False, log_level="warning", workers=1, limit_concurrency=16))
        server.run(sockets=[sock])
        return 0 if server.started else 1
    except Exception:
        # Never log config values, credentials, pairing code, raw HTTP or traceback.
        print("Mobile display startup failed; verify USB interface, private configuration and existing feed.")
        return 1
    finally:
        if sock is not None:
            sock.close()
        if owned:
            with suppress(OSError):
                (RUNTIME / "pairing-code").unlink(missing_ok=True)
                (RUNTIME / "endpoint.json").unlink(missing_ok=True)
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
