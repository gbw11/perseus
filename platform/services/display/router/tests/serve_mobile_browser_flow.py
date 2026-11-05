"""PC-only manual browser integration fixture; never a USB/board launcher.

Runs the real phone access app and LocalFeed against built vm-screens assets and
synthetic presentation data. Both listeners bind loopback only. Operator actions
are accepted on stdin, never through an extra HTTP control endpoint.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mobile_preview_data import presentation
from xenova_display.mobile_access import CODE_SECONDS, SESSION_SECONDS, Pairing, create_app
from xenova_display.mobile_network import Endpoint
from xenova_display.mobile_upstream import ASSET, LocalFeed


class TestClock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset

    def advance(self, seconds):
        self.offset += seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18754)
    parser.add_argument('--seconds', type=int, default=900)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[5]
    assets = root / 'frontend/apps/vm-screens/dist'
    if not (assets / 'index.html').is_file():
        parser.error('Build @xenova/vm-screens before running this fixture')
    if not 1024 <= args.port <= 65535 or not 1 <= args.seconds <= 1800:
        parser.error('Use an unprivileged loopback port and a 1..1800 second lifetime')
    clock = TestClock()
    pairing = Pairing(clock=clock)
    pairing.rotate()
    state = {'phase': 'preparing', 'feed_available': True, 'sequence': 0}
    audit, feed_audit, operations = Counter(), Counter(), []
    private_token = secrets.token_urlsafe(32)

    class SyntheticFeed(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path
            if self.headers.get('Authorization') != 'Bearer ' + private_token:
                return self.reply(401, b'', 'text/plain')
            feed_audit[path] += 1
            if not state['feed_available']:
                return self.reply(503, b'', 'text/plain')
            if path == '/api/v1/display/presentation':
                value = presentation(state['phase'])
                state['sequence'] += 1
                value['state']['sequence'] = state['sequence']
                body = json.dumps(value).encode()
                return self.reply(200, body, 'application/json')
            if path == '/display/mobile':
                html = (assets / 'index.html').read_text(encoding='utf-8')
                badge = '<div style="position:fixed;bottom:0;right:0;z-index:99999;background:#101821;color:#f8b84e;font:10px sans-serif;padding:2px">PC 합성 시험 · USB/실보드 아님</div>'
                return self.reply(200, html.replace('</body>', badge + '</body>').encode(), 'text/html; charset=utf-8')
            if ASSET.fullmatch(path):
                file = (assets / path.lstrip('/')).resolve()
                if file.is_file() and file.is_relative_to(assets.resolve()):
                    kind = {'.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png',
                            '.svg': 'image/svg+xml', '.woff2': 'font/woff2'}.get(file.suffix, 'application/octet-stream')
                    return self.reply(200, file.read_bytes(), kind)
            return self.reply(404, b'', 'text/plain')

        def reply(self, status, body, kind):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticFeed)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        # Explicit fixture injection replaces USB discovery/binding only here.
        # Production main(), resolve(), bind(), boundaries and TTL constants stay unchanged.
        sock.bind(('127.0.0.1', args.port))
        sock.listen(16)
        sock.setblocking(False)
        endpoint = Endpoint('loopback-test-only', '127.0.0.1', '127.0.0.1', args.port)
        app = create_app(endpoint, LocalFeed(private_token, port=upstream.server_port), pairing, rotate_signal=True)

        @app.middleware('http')
        async def observe(request, call_next):
            if request.method == 'POST':
                origin = request.headers.get('origin')
                kind = 'expected' if origin == endpoint.origin else 'null' if origin == 'null' else 'missing' if origin is None else 'other'
                audit['POST origin ' + kind] += 1
            response = await call_next(request)
            # Never retain code input, request headers, cookies, token or bodies.
            audit[f'{request.method} {request.url.path} {response.status_code}'] += 1
            return response

        server = uvicorn.Server(uvicorn.Config(app, proxy_headers=False, access_log=False, log_level='error'))

        def status(include_code=False):
            value = {'server_started': server.started, 'url': endpoint.origin + '/display/mobile',
                     'synthetic_only': True, 'phase': state['phase'],
                     'feed_available': state['feed_available'], 'sequence': state['sequence'],
                     'test_clock_offset_s': clock.offset,
                     'code_remaining_s': max(0, round(pairing.code_until - clock())),
                     'session_valid': pairing.valid(pairing.session),
                     'requests': dict(audit), 'upstream_requests': dict(feed_audit)}
            if include_code:
                value['ephemeral_test_pairing_code'] = pairing.code
            return value

        def operator():
            for line in sys.stdin:
                action = line.strip()
                if action not in {'status', 'code', 'expire-code', 'expire-session', 'rotate',
                                  'applied', 'fail-feed', 'recover-feed', 'stop'}:
                    print(json.dumps({'error': 'Unknown fixture command'}), flush=True)
                    continue
                if not server.started:
                    print(json.dumps({'error': 'Fixture not ready'}), flush=True)
                    continue
                operations.append(action)
                if action == 'expire-code':
                    clock.advance(CODE_SECONDS + 1)
                elif action == 'expire-session':
                    clock.advance(SESSION_SECONDS + 1)
                elif action == 'rotate':
                    os.kill(os.getpid(), signal.SIGUSR1)
                    # Rotation itself is the product's event-loop signal callback.
                elif action == 'applied':
                    state['phase'] = 'applied'
                elif action == 'fail-feed':
                    state['feed_available'] = False
                elif action == 'recover-feed':
                    state['feed_available'] = True
                elif action == 'stop':
                    server.should_exit = True
                print(json.dumps(status(action == 'code'), ensure_ascii=True), flush=True)
                if action == 'stop':
                    return
            server.should_exit = True

        timer = threading.Timer(args.seconds, lambda: setattr(server, 'should_exit', True))
        timer.daemon = True
        timer.start()
        threading.Thread(target=operator, daemon=True).start()
        print(json.dumps({'fixture_url': endpoint.origin + '/display/mobile',
                          'auto_stop_s': args.seconds, 'synthetic_only': True}), flush=True)
        try:
            server.run(sockets=[sock])
        finally:
            timer.cancel()
            args.report.parent.mkdir(parents=True, exist_ok=True)
            summary = status()
            summary.update({'operations': operations, 'listener_closed': True,
                            'product_code_seconds': CODE_SECONDS, 'product_session_seconds': SESSION_SECONDS})
            args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    finally:
        sock.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(3)


if __name__ == '__main__':
    main()
