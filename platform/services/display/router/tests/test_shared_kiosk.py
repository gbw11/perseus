"""The optional shared process must keep each output's context and close isolated."""
import asyncio

from xenova_display.kiosk import Kiosk
from xenova_display.outputs import RuntimeConfig


def test_shared_browser_keeps_output_authentication_and_shutdown_isolated(monkeypatch):
    monkeypatch.setenv("XENOVA_DISPLAY_TOKEN", "must-not-reach-chromium")
    config = RuntimeConfig.model_validate({"backend": "virtual_x11", "outputs": [
        {"output_id": "monitor-1", "connector": "left", "x": 0},
        {"output_id": "monitor-2", "connector": "right", "x": 1280}]})

    class Page:
        closed = False
        async def goto(self, url, **kwargs): self.url = url
        async def close(self): self.closed = True

    class Cdp:
        async def send(self, method, values=None): return {"windowId": 1}
        async def detach(self): pass

    class Request:
        async def post(self, url, **kwargs):
            self.authorization = kwargs["headers"]["Authorization"]
            return type("Reply", (), {"status": 204})()

    class Context:
        def __init__(self): self.pages = []; self.request = Request()
        async def new_page(self):
            page = Page(); self.pages.append(page); return page
        async def new_cdp_session(self, page): return Cdp()
        async def close(self):
            for page in self.pages: await page.close()

    class Browser:
        connected = True
        def is_connected(self): return self.connected
        async def new_context(self, **kwargs): return Context()

    class BrowserType:
        launches = 0
        async def launch(self, **kwargs):
            self.launches += 1
            assert kwargs["chromium_sandbox"] is True
            assert "XENOVA_DISPLAY_TOKEN" not in kwargs["env"]
            assert not any("test-secret" in arg for arg in kwargs["args"])
            return Browser()

    async def exercise():
        kiosk = Kiosk(config, "test-secret", "/unused", allow_virtual=True, shared_browser=True)
        browser_type = BrowserType()
        for output in config.outputs: await kiosk.start_output(browser_type, output)
        assert browser_type.launches == 1
        left, right = kiosk.contexts.values()
        assert left is not right
        assert left.request.authorization == right.request.authorization == "Bearer test-secret"
        right_page = kiosk.pages["monitor-2"]
        await kiosk.close_output("monitor-1")
        assert not right_page.closed
        assert kiosk.browser.is_connected()
        await kiosk.start_output(browser_type, config.outputs[0])
        assert browser_type.launches == 1
        assert kiosk.contexts["monitor-1"] is not left
        assert kiosk.pages["monitor-2"] is right_page
        for identity in list(kiosk.contexts): await kiosk.close_output(identity)

    asyncio.run(exercise())
