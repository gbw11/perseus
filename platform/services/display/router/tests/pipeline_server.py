"""Opt-in Spring/HDMI consumer fixture. Real MQTT/feed/browser, synthetic guests.

Started only by DisplayPipelineIntegrationTest. No guest, console or hardware access.
Private token arrives on stdin; fault endpoints are not installed in the product.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import secrets
import socket
import sys

ROOT = Path(__file__).resolve().parents[5]
for source in ("platform/services/display/router", "platform/services/dom0-manager",
               "platform/services/vm-agent/mqtt-client"):
    sys.path.insert(0, str(ROOT / source))

from amqtt.broker import Broker
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import paho.mqtt.client as mqtt
from playwright.async_api import async_playwright
import uvicorn

from dom0_manager.drivers.base import Domain
from dom0_manager.models import VmState
from xenova_display.config import FeedSettings, load_bindings
from xenova_display.main import create_app
from xenova_display.state import StateStore
from xenova_mqtt.envelope import Identity, build


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


async def until(predicate, seconds=12):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.05)


async def main():
    private = json.loads(sys.stdin.readline())
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    bindings, scope = load_bindings(Path(__file__).parents[1] / "examples/integration-candidate-bindings.json",
                                    allow_candidate=True)
    target = bindings[1]
    vm_id = str(target.vm_id)
    broker_socket = listener()
    broker_port = broker_socket.getsockname()[1]
    broker_socket.close()
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{broker_port}"}},
                     "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
    clock = [100.0]  # Deterministic source expiry; network and UI polling remain real.
    settings = FeedSettings(bindings, private["token"], mqtt_port=broker_port, binding_scope=scope)
    store = StateStore(settings, clock=lambda: clock[0])

    class Driver:
        rows = [Domain(b.vm_id, VmState.running, 1, 16, domid=i + 1) for i, b in enumerate(bindings)]
        failed = False

        def list_domains(self):
            if self.failed:
                raise ConnectionError("injected Xen observation failure")
            return self.rows

    driver = Driver()
    app = create_app(settings, driver=driver, store=store,
                     web_root=ROOT / "frontend/apps/vm-screens/dist")
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pipeline-" + secrets.token_hex(6))
    identity = Identity(vm_id=vm_id, app_id=target.app_id, boot_id="synthetic-" + secrets.token_hex(16))
    debian = Identity(vm_id=str(bindings[2].vm_id))
    sequence = 0
    outage = False
    browser = context = playwright = None
    pages = []
    browser_errors = []
    observations = []

    async def publish(who, topic, kind, payload):
        nonlocal sequence
        sequence += 1
        info = publisher.publish(topic, json.dumps(build(who, kind, payload, sequence)), qos=1, retain=False)
        await asyncio.to_thread(info.wait_for_publish, 5)
        assert info.is_published()
        return sequence

    @app.middleware("http")
    async def upstream_failure(request, call_next):
        if outage and request.url.path == "/api/v1/display/state":
            return JSONResponse({"code": "injected_upstream_unavailable"}, status_code=503)
        return await call_next(request)

    @app.post("/test/scenario/{name}")
    async def scenario(name: str):
        nonlocal identity, outage
        if name not in {"healthy", "error", "stale", "recovered", "paused", "stopped", "xen-unavailable", "upstream-down", "upstream-up"}:
            raise HTTPException(400)
        if name.startswith("upstream-"):
            outage = name == "upstream-down"
            return {"scenario": name}
        if name in {"healthy", "recovered"}:
            if not any(row.vm_id == target.vm_id for row in driver.rows):
                identity = Identity(vm_id=vm_id, app_id=target.app_id, boot_id="synthetic-" + secrets.token_hex(16))
            driver.failed = False
            driver.rows = [Domain(b.vm_id, VmState.running, 1, 16, domid=i + 1) for i, b in enumerate(bindings)]
        elif name == "paused":
            driver.rows = [replace(row, state=VmState.paused) if row.vm_id == target.vm_id else row for row in driver.rows]
        elif name == "stopped":
            # Xen represents stopped guests by absence, not a synthetic xl state.
            driver.rows = [row for row in driver.rows if row.vm_id != target.vm_id]
        elif name == "xen-unavailable":
            driver.failed = True
        if driver.failed:
            store.poll_failure()
        else:
            store.poll_success(driver.rows)
        if name == "stale":
            clock[0] += 4
            store.poll_success(driver.rows)
        if name in {"healthy", "recovered", "error"}:
            seq = await publish(identity, "xenova/v1/apps/manual/events", "app.heartbeat",
                                {"state": "error" if name == "error" else "standby", "uptime_s": sequence})
            await until(lambda: store.snapshot()["vms"][1]["sources"]["app"].get("sequence") == seq)
            agent_seq = await publish(debian, f"xenova/v1/vms/{bindings[2].vm_id}/heartbeat",
                                      "agent.heartbeat", {"uptime_s": sequence})
            await until(lambda: store.snapshot()["vms"][2]["sources"]["agent"].get("sequence") == agent_seq)
        return {"scenario": name, "vm_id": vm_id, "synthetic_sources": True}

    @app.post("/test/browser/{kind}")
    async def browser_check(kind: str):
        nonlocal browser, context, playwright, pages
        if browser is None:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(viewport={"width": 1280, "height": 720})
            response = await context.request.post(base + "/api/v1/display/session",
                                                   headers={"Authorization": "Bearer " + settings.token})
            assert response.status == 204
            # Cookie permits presentation/ACK only, never service state or selection.
            assert (await context.request.get(base + "/api/v1/display/state")).status == 401
            assert (await context.request.post(base + "/api/v1/display/selection", data={})).status == 401
            for monitor in (1, 2):
                page = await context.new_page()
                page.on("pageerror", lambda error: browser_errors.append(str(error)))
                pages.append(page)
                await page.goto(base + f"/display/monitor-{monitor}")
        layouts = []
        for page in pages:
            await page.locator(f'[data-testid="card-VM2"][data-kind="{kind}"]').wait_for(timeout=10000)
            assert settings.token not in await page.content()
            layouts.append(await page.evaluate("({width: innerWidth, height: innerHeight, scroll_width: document.documentElement.scrollWidth, scroll_height: document.documentElement.scrollHeight})"))
        await pages[0].get_by_text("VM 전체 상태", exact=True).wait_for()
        await pages[1].get_by_text("VM 상세 화면", exact=True).wait_for()
        assert await pages[0].locator("[data-role]").count() == 3
        p = app.state.policy.presentation(store.snapshot())
        async with asyncio.timeout(10):
            while not all(row["renderer_ack"] and row["renderer_ack"]["stage"] == "dom_rendered" for row in p["outputs"]):
                await asyncio.sleep(.1)
                p = app.state.policy.presentation(store.snapshot())
        assert not browser_errors, browser_errors
        for index, page in enumerate(pages, 1):
            await page.screenshot(path=str(output / f"{len(observations):02d}-{kind}-monitor-{index}.png"), full_page=True)
        record = {"kind": kind, "render_revision": p["render_revision"], "vm_id": vm_id,
                  "dom_rendered": [row["output_id"] for row in p["outputs"]], "browser_errors": list(browser_errors),
                  "physical_hdmi_verified": False, "layouts": layouts, "state": p["state"],
                  "fits_720p": all(row["scroll_height"] <= row["height"] and row["scroll_width"] <= row["width"] for row in layouts)}
        observations.append(record)
        (output / "browser-evidence.json").write_text(json.dumps(observations, indent=2) + "\n")
        return record

    sock = listener()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=3))
    task = None
    try:
        await broker.start()
        publisher.connect("127.0.0.1", broker_port)
        publisher.loop_start()
        task = asyncio.create_task(server.serve(sockets=[sock]))
        await until(lambda: server.started and bool(store.subscriptions) and
                    all(value == "granted" for value in store.subscriptions.values()))
        initial = app.state.policy.presentation(store.snapshot())
        from xenova_display.policy import Selection
        app.state.policy.select(Selection(policy_epoch=initial["policy_epoch"], revision=initial["revision"],
                                          selection="pinned", vm_id=target.vm_id))
        print(json.dumps({"base_url": base, "vm_id": vm_id}), flush=True)
        await asyncio.to_thread(sys.stdin.read)
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        server.should_exit = True
        if task:
            await asyncio.wait_for(task, 10)
        else:
            sock.close()
        publisher.disconnect()
        publisher.loop_stop()
        await broker.shutdown()
        (output / "cleanup-evidence.json").write_text(json.dumps({
            "feed_task_finished": task is None or task.done(),
            "browser_closed": browser is None or not browser.is_connected(),
            "mqtt_publisher_stopped": True, "broker_shutdown_completed": True,
            "guest_accessed": False,
        }, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
