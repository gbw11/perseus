import base64
import socket
import threading
import time

import pytest
import uvicorn

from xenova_display.stream import create_stream_app
from test_outputs import config

playwright = pytest.importorskip("playwright.sync_api")


def test_viewer_reconnects_when_live_stream_stops_delivering(tmp_path):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except playwright.Error:
            try: browser = p.chromium.launch()
            except playwright.Error as error: pytest.skip(f"no Chromium available: {error}")
        page = browser.new_page()
        frame = base64.b64decode(page.evaluate(
            "()=>{const c=document.createElement('canvas');c.width=c.height=8;"
            "return c.toDataURL('image/jpeg').split(',')[1]}"))

        class SilentCapture:
            """Reports live but repeats one frame identity, so an open stream goes quiet."""
            def __init__(self, _config, output, _status):
                self.output_id = output.output_id
            def start(self): pass
            def stop(self): pass
            def snapshot(self):
                return {"output_id": self.output_id, "status": "live",
                        "capture_epoch": "epoch", "frame_sequence": 1}, frame

        app = create_stream_app(config(), tmp_path/"unused", "test-key",
                                allow_virtual=True, capture_factory=SilentCapture)
        connections = {"monitor-1": 0, "monitor-2": 0}

        async def counted(scope, receive, send):
            path = scope.get("path", "")
            if path.startswith("/streams/"):
                connections[path.removeprefix("/streams/").removesuffix(".mjpeg")] += 1
            await app(scope, receive, send)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(counted, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+10
            while not server.started and time.monotonic() < deadline: time.sleep(.05)
            page.goto(f"http://127.0.0.1:{port}/demo")
            page.locator("#key").fill("test-key")
            page.get_by_role("button", name="화면 연결").click()
            page.get_by_text("두 화면 연결됨", exact=True).wait_for(timeout=5000)
            assert connections == {"monitor-1": 1, "monitor-2": 1}
            # The open responses send nothing further; the viewer must notice and reopen both.
            page.get_by_text("화면 수신 중단 · 마지막 프레임").first.wait_for(timeout=6000)
            deadline = time.monotonic()+12
            while min(connections.values()) < 2 and time.monotonic() < deadline: time.sleep(.1)
            assert min(connections.values()) >= 2
            page.get_by_text("두 화면 연결됨", exact=True).wait_for(timeout=5000)
        finally:
            browser.close()
            app.state.stopping.set()
            server.should_exit = True
            thread.join(timeout=10)
