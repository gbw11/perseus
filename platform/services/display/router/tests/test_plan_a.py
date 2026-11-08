"""414 Plan A: VM3 serves the web UI, dom0 Chromium renders it on the single HDMI."""
import asyncio
import http.client
import json
from pathlib import Path
import threading
import time

import pytest

from xenova_display.guest_app import load_config, serve
from xenova_display.guest_frames import GuestRelayConfig
from xenova_display.kiosk import Kiosk
from xenova_display.outputs import RuntimeConfig

ROUTER = Path(__file__).resolve().parents[1]


def plan_a(**changes):
    raw = json.loads((ROUTER/"examples/single-hdmi-plan-a-runtime.json").read_text())
    raw.update(backend="virtual_x11", display=":99", **changes)
    return RuntimeConfig.model_validate(raw)


def test_example_and_ui_origin_rules():
    c = plan_a()
    assert c.ui_origin == "http://192.168.77.20:8086" and c.renderer == "chromium"
    assert c.page_url(c.outputs[0]) == "http://192.168.77.20:8086/display/mobile"
    assert plan_a(ui_origin=None).page_url(plan_a().outputs[0]) == "http://127.0.0.1:8082/display/mobile"
    for bad in ["https://192.168.77.20:8086", "http://192.168.77.20", "http://8.8.8.8:8086", "http://0.0.0.0:8086",
                "http://vm3.local:8086", "http://user:pw@192.168.77.20:8086", "http://192.168.77.20:8086/x",
                "http://192.168.77.20:8086?token=1"]:
        with pytest.raises(ValueError): plan_a(ui_origin=bad)
    with pytest.raises(ValueError):
        plan_a(renderer="guest_frames", guest_relay_token_file="/t", frame_decoder="ffmpeg")


class Page:
    def __init__(self): self.urls = []
    async def goto(self, url, timeout): self.urls.append(url)
    def is_closed(self): return False


class CDP:
    def __init__(self, output): self.output = output
    async def send(self, method, params=None):
        o = self.output
        return {"windowId": 1, "bounds": dict(left=o.x, top=o.y, width=o.width, height=o.height)}
    async def detach(self): pass


class Context:
    def __init__(self, output):
        self.output, self.posts, self.gets, self.pages = output, [], [], [Page()]
        self.request = self
    async def post(self, url, headers, timeout):
        self.posts.append(url)
        return type("R", (), {"status": 204})()
    async def get(self, url, timeout):
        self.gets.append(url)
        return type("R", (), {"status": 503})()
    async def new_cdp_session(self, page): return CDP(self.output)


class BrowserType:
    def __init__(self): self.launches = []
    async def launch_persistent_context(self, profile, **kwargs):
        self.launches.append(kwargs["args"])
        self.context = Context(self.output)
        return self.context


@pytest.mark.parametrize("ui_origin,session", [("http://192.168.77.20:8086", False), (None, True)])
def test_kiosk_opens_vm3_ui_without_a_dom0_feed_session(tmp_path, ui_origin, session):
    async def case():
        kiosk = Kiosk(plan_a(ui_origin=ui_origin), "service-token", tmp_path/"status.json", allow_virtual=True)
        output = kiosk.config.outputs[0]
        browser = BrowserType(); browser.output = output
        await kiosk.start_output(browser, output)
        url = kiosk.config.page_url(output)
        assert f"--app={url}" in browser.launches[0]
        assert browser.context.pages[0].urls == [url]
        assert bool(browser.context.posts) is session  # feed session cookie only for dom0-served pages
        mapping = {"status": "mapped", "physical_verified": True}
        await kiosk._update_output(browser, output, mapping)
        state = kiosk.states["monitor-1"]
        assert state["status"] == "mapped" and state["renderer_alive"]
        if ui_origin:
            assert state["feed_status"] == "not_used" and browser.context.gets == []
        else:
            assert browser.context.gets == ["http://127.0.0.1:8082/api/v1/display/presentation"]
        for identity in list(kiosk.profiles): kiosk.profiles.pop(identity).cleanup()
    asyncio.run(case())


def test_guest_app_bind_and_client_rules(tmp_path):
    root = tmp_path/"web"; root.mkdir(); (root/"index.html").write_text("ui")
    def config(**changes):
        value = {"web_root": str(root), "data_host": "192.168.77.1", "data_token_file": "/etc/t", **changes}
        (tmp_path/"c.json").write_text(json.dumps(value))
        return load_config(tmp_path/"c.json")
    assert (config()["bind"], config()["allowed_clients"]) == ("127.0.0.1", ["127.0.0.1"])
    example = json.loads((ROUTER/"deploy/guest-display/domu/guest-display-domu.json.example").read_text())
    example["web_root"] = str(root)
    c = config(**{k: v for k, v in example.items() if k != "web_root"})
    assert (c["bind"], c["allowed_clients"]) == ("192.168.77.20", ["192.168.77.1"])
    for bad in [dict(bind="0.0.0.0"), dict(bind="8.8.8.8"), dict(allowed_clients=[]),
                dict(allowed_clients=["0.0.0.0"]), dict(allowed_clients="192.168.77.1"),
                dict(allowed_clients=["8.8.8.8"])]:
        with pytest.raises(ValueError): config(**bad)


def test_guest_app_refuses_clients_other_than_dom0(tmp_path):
    root = tmp_path/"web"; root.mkdir(); (root/"index.html").write_text("ui")
    server = serve({"web_root": root, "app_port": 0, "data_host": "192.168.77.1", "data_port": 8085,
                    "bind": "127.0.0.1", "allowed_clients": ["127.0.0.2"]}, "token")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        connection.request("GET", "/display/mobile")
        response = connection.getresponse()
        assert response.status == 403 and json.loads(response.read()) == {"code": "Forbidden"}
    finally:
        server.shutdown(); server.server_close()


def test_dom0_data_only_mode_needs_one_token():
    example = json.loads((ROUTER/"deploy/guest-display/guest-display-data-only.json.example").read_text())
    c = GuestRelayConfig.model_validate(example)
    assert c.guest is None and c.relay_token_file is None and str(c.data_bind) == "192.168.77.1"
    with pytest.raises(ValueError):  # the Plan B relay still needs its stream/relay tokens
        GuestRelayConfig.model_validate({**example, "guest": {"host": "192.168.77.20"}})
