"""Isolated browser-test server: actual QEMU Xen reads, explicitly synthetic failure/drive cases.

Token arrives on stdin. This is not a production entry point and installs no service.
"""
import asyncio
import json
from pathlib import Path
import socket
import sys

from dom0_manager.drivers.xen import XenDriver
from dom0_manager.drivers.xl import SshRunner, Xl
from fastapi import HTTPException
import uvicorn

from xenova_display.config import Binding, FeedSettings
from xenova_display.main import create_app
from xenova_display.state import StateStore

ROOT = Path(__file__).resolve().parents[5]
config = json.loads(sys.stdin.readline())
settings = FeedSettings(tuple(Binding.model_validate(b) for b in json.loads(
    (Path(__file__).parents[1] / "examples/qemu-bindings.json").read_text())), config["token"])
store = StateStore(settings)
base_snapshot = store.snapshot
scenario = "live"


def snapshot():
    state = base_snapshot()
    if scenario == "live":
        return state
    for v in state["vms"]:
        v["sources"]["xen"] = {**v["sources"]["xen"], "status": "fresh"}
        v["sources"]["app"]["status"] = "fresh"
        v["app_state"] = "ready"
    state["drive"].update(active_vm_id=state["vms"][0]["vm_id"], mode="AUTO", source={"status": "fresh", "age_ms": 0})
    if scenario == "crash":
        state["vms"][0]["xen_state"] = "crashed"
    if scenario == "stale":
        state["vms"][0]["sources"]["xen"]["status"] = "stale"
    if scenario == "manual":
        state["drive"]["mode"] = "MANUAL"
    if scenario == "backup":
        state["drive"]["active_vm_id"] = state["vms"][1]["vm_id"]
    return state


store.snapshot = snapshot


class NoMqtt:
    def __init__(self, store): pass
    def start(self): pass
    def stop(self): pass


driver = XenDriver(Xl(SshRunner("xenova-qemu-dom0", ssh=("ssh", "-F", "/home/ssafy/xenova-qemu-28/keys/ssh_config"))))
app = create_app(settings, driver=driver, store=store, mqtt_factory=NoMqtt,
                 web_root=ROOT / "frontend/apps/vm-screens/dist")


@app.post("/test/scenario/{name}")
def set_scenario(name: str):
    global scenario
    if name not in {"live", "ready", "crash", "stale", "manual", "backup"}:
        raise HTTPException(400)
    scenario = name
    return {"scenario": scenario, "simulation": name != "live"}


async def main():
    sock = socket.socket()
    sock.bind(("0.0.0.0", 0))
    sock.listen(128)
    print(json.dumps({"port": sock.getsockname()[1]}), flush=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=3))

    async def watch():
        await asyncio.to_thread(sys.stdin.read)
        server.should_exit = True
    task = asyncio.create_task(watch())
    try: await server.serve(sockets=[sock])
    finally: task.cancel()


asyncio.run(main())
