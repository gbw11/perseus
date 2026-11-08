"""413 Plan B, display-domU side: serve the vehicle screen app and forward its one data read.

Chromium in the display domU opens http://127.0.0.1:<app_port><page>. This
server returns the built vm-screens files and forwards exactly one request,
GET /api/v1/display/presentation, to dom0's read-only guest data path (410)
with the guest data token. The page never sees a token. State collection and
screen decisions stay in dom0; the domU only draws.

Standard library only (runs in a minimal Debian guest). Loopback bind only.
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .guest_sender import read_token

PAGES = ("/display/monitor-1", "/display/monitor-2", "/display/combined", "/display/mobile")
DATA_PATH = "/api/v1/display/presentation"
ASSET = re.compile(r"/assets/([A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.(?:js|css|png|jpg|jpeg|webp|svg|woff2?|ttf|ico))")
MAX_BODY = 2 * 1024 * 1024
TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2",
         ".woff": "font/woff", ".ttf": "font/ttf", ".ico": "image/x-icon", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def load_config(path, *, allow_loopback_data=False):
    """/etc/xenova/guest-display-domu.json, shared by guest_app and guest_session."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {"web_root", "app_port", "data_host", "data_port", "data_token_file", "page",
               "display", "width", "height", "chromium"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("unknown guest display setting")
    c = {"app_port": 8086, "data_port": 8085, "page": "/display/mobile", "display": ":0",
         "width": 1280, "height": 720, "chromium": "/usr/bin/chromium", **raw}
    web_root = Path(c["web_root"])
    if not web_root.is_absolute() or not (web_root / "index.html").is_file():
        raise ValueError("web_root must be an absolute vm-screens build with index.html")
    host = ipaddress.IPv4Address(c["data_host"])
    if host.is_unspecified or host.is_multicast or not (host.is_private or host.is_loopback):
        raise ValueError("data_host must be dom0's private Xen bridge address")
    if host.is_loopback and not allow_loopback_data:
        raise ValueError("loopback data_host is for local tests only")
    for key in ("app_port", "data_port"):
        if type(c[key]) is not int or not 1 <= c[key] <= 65535:
            raise ValueError(f"{key} out of range")
    if c["page"] not in PAGES:
        raise ValueError("page must be one of the screens the feed serves")
    if not (isinstance(c["display"], str) and re.fullmatch(r":[0-9]{1,2}", c["display"])):
        raise ValueError("local X11 display required")
    if not (640 <= c["width"] <= 1920 and 480 <= c["height"] <= 1080):
        raise ValueError("screen size out of range")
    if not Path(c["chromium"]).is_absolute() or not Path(c["data_token_file"]).is_absolute():
        raise ValueError("absolute chromium and token paths required")
    c["web_root"], c["data_host"] = web_root.resolve(), str(host)
    return c


class DataSource:
    """The one forwarded read. Fixed path, fixed host, bounded body, no headers passed through."""
    def __init__(self, host, port, token, *, connect=http.client.HTTPConnection):
        self.host, self.port, self._token, self.connect = host, port, token, connect
        self.busy = threading.BoundedSemaphore(2)

    def get(self):
        if not self.busy.acquire(blocking=False):
            return 503, {"code": "GUEST_DATA_BUSY"}, None
        connection = None
        try:
            connection = self.connect(self.host, self.port, timeout=2)
            connection.request("GET", DATA_PATH, headers={"Authorization": f"Bearer {self._token}"})
            response = connection.getresponse()
            if response.status in (401, 403):
                return 503, {"code": "GUEST_DATA_AUTH_FAILED"}, None
            body = response.read(MAX_BODY + 1)
            if (response.status != 200 or len(body) > MAX_BODY
                    or not response.getheader("Content-Type", "").startswith("application/json")):
                return 503, {"code": "DISPLAY_DATA_UNAVAILABLE"}, None
            return 200, None, body
        except (OSError, http.client.HTTPException):
            return 503, {"code": "DISPLAY_DATA_UNAVAILABLE"}, None
        finally:
            self.busy.release()
            if connection is not None:
                try: connection.close()
                except Exception: pass


def make_handler(web_root, data):
    index = web_root / "index.html"
    assets = (web_root / "assets").resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "xenova-guest-display-app"
        sys_version = ""

        def log_message(self, *_):
            pass

        def send(self, status, body, content_type, cache="no-store"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def code(self, status, code):
            self.send(status, json.dumps({"code": code}).encode(), "application/json")

        def do_GET(self):
            path = self.path
            if path in PAGES:
                return self.send(200, index.read_bytes(), "text/html; charset=utf-8")
            if path == DATA_PATH:
                status, error, body = data.get()
                if error is not None:
                    return self.code(status, error["code"])
                return self.send(200, body, "application/json")
            match = ASSET.fullmatch(path)
            if match:
                file = (assets / match[1]).resolve()
                if file.parent == assets and file.is_file():
                    kind = TYPES.get(file.suffix) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                    return self.send(200, file.read_bytes(), kind, cache="max-age=300")
            return self.code(404, "NotFound")

        def do_POST(self):
            # e.g. MonitorPage renderer acks: dom0 policy stays read-only from the guest.
            self.code(405, "MethodNotAllowed")
        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_POST

    return Handler


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(config, token, *, connect=http.client.HTTPConnection):
    data = DataSource(config["data_host"], config["data_port"], token, connect=connect)
    # Loopback only: Chromium in this domU is the single client.
    return AppServer(("127.0.0.1", config["app_port"]), make_handler(config["web_root"], data))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="/etc/xenova/guest-display-domu.json")
    args = p.parse_args(argv)
    try:
        config = load_config(args.config)
        token = read_token(config["data_token_file"])
    except (OSError, ValueError) as error:
        print(json.dumps({"code": "GUEST_DISPLAY_APP_CONFIG_INVALID", "detail": type(error).__name__}))
        return 2
    server = serve(config, token)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
