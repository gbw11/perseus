"""Temporary, synthetic-only phone UI preview; not a production USB/auth server.

Build vm-screens first. Default loopback; explicitly supply --host <PC LAN IP>
for phone review on the same Wi-Fi. Stops automatically after 30 minutes.
Only built assets and fake presentation data are served. No board connections.
"""
import argparse
import ipaddress
import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mobile_preview_data import presentation

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=18750)
parser.add_argument("--seconds", type=int, default=1800)
args = parser.parse_args()
if not ipaddress.ip_address(args.host).is_private or args.host == "0.0.0.0":
    parser.error("Use a specific loopback/private PC interface, never a public/all-interface bind")
root = Path(__file__).resolve().parents[5] / "frontend/apps/vm-screens/dist"
if not (root / "index.html").is_file():
    parser.error("Build @xenova/vm-screens first")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(root), **kw)

    def do_GET(self):
        if self.path == "/api/v1/display/presentation":
            value = presentation("preparing")
            value["state"]["sequence"] = time.monotonic_ns()
            # JS safe integer, monotonic across repeated HTTP requests.
            value["state"]["sequence"] //= 1_000_000
            data = json.dumps(value).encode()
            content_type = "application/json"
        elif self.path.split("?", 1)[0] in ("/", "/display/mobile"):
            html = (root / "index.html").read_text(encoding="utf-8")
            note = '<div style="position:fixed;bottom:0;right:0;z-index:99999;background:#101821;color:#f8b84e;font:11px sans-serif;padding:3px">합성 상태 미리보기 · 실보드/USB 시험 아님</div>'
            data = html.replace("</body>", note + "</body>").encode()
            content_type = "text/html; charset=utf-8"
        elif self.path.startswith("/assets/") and ".." not in self.path:
            return super().do_GET()
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer((args.host, args.port), Handler)
timer = threading.Timer(min(max(args.seconds, 1), 1800), server.shutdown)
timer.daemon = True
timer.start()
print(f"Synthetic phone preview: http://{args.host}:{args.port}/display/mobile", flush=True)
try:
    server.serve_forever()
finally:
    timer.cancel()
    server.server_close()
