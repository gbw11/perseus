"""Opt-in candidate app MQTT -> feed REST/WS -> HDMI presentation acceptance.

Real isolated loopback transport, synthetic publishers/Xen observations only.
Does not contact, start, stop, install into, or replace any guest VM.
"""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import socket
import time

from amqtt.broker import Broker
import httpx
import paho.mqtt.client as mqtt
import uvicorn
from websockets.asyncio.client import connect

from dom0_manager.drivers.base import Domain
from dom0_manager.models import VmState
from xenova_display.config import FeedSettings, load_bindings
from xenova_display.main import create_app
from xenova_mqtt.envelope import Identity, build


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def until(test, timeout=15):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if await test(): return
        await asyncio.sleep(.1)
    raise TimeoutError("display contract condition not reached")


async def run(output):
    bindings, scope = load_bindings(Path(__file__).parents[1]/"examples/integration-candidate-bindings.json", allow_candidate=True)
    token = secrets.token_urlsafe(32)
    port = free_port()
    broker = Broker({"listeners":{"default":{"type":"tcp","bind":f"127.0.0.1:{port}"}},
                    "plugins":{"amqtt.plugins.authentication.AnonymousAuthPlugin":{"allow_anonymous":True}}})
    class Driver:
        rows = [Domain(b.vm_id,VmState.running,1,16,domid=i+1) for i,b in enumerate(bindings)]
        def list_domains(self): return self.rows
    driver=Driver()
    app=create_app(FeedSettings(bindings,token,mqtt_port=port,binding_scope=scope),driver=driver)
    publisher=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="display-source-test-"+secrets.token_hex(6))
    server=task=None
    evidence={"scope":"real loopback MQTT/HTTP/WebSocket and policy; candidate UUIDs, synthetic app/Xen; no guest or production ACL test",
              "cases":[],"observations":[]}
    try:
        await broker.start()
        publisher.connect("127.0.0.1",port);publisher.loop_start()
        sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(128)
        feed_port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level="warning",timeout_graceful_shutdown=2))
        task=asyncio.create_task(server.serve(sockets=[sock]))
        async def subscribed():
            return server.started and app.state.store.mqtt_connected and bool(app.state.store.subscriptions) and all(
                status=="granted" for status in app.state.store.subscriptions.values())
        await until(subscribed)
        evidence["cases"].append("all_configured_topics_receive_real_suback_before_reports")
        b=bindings[1]
        identity=Identity(vm_id=str(b.vm_id),app_id=b.app_id)
        def report(seq,state="active"):
            return build(identity,"app.heartbeat",{"state":state,"uptime_s":seq},seq)
        async def publish(env):
            info=publisher.publish("xenova/v1/apps/manual/events",json.dumps(env),qos=0,retain=False)
            await asyncio.to_thread(info.wait_for_publish,3)
            assert info.is_published()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{feed_port}",headers={"Authorization":f"Bearer {token}"}) as client:
            async def presentation():
                response=await client.get("/api/v1/display/presentation")
                response.raise_for_status()
                return response.json()
            await publish(report(1))
            async def normal():
                p=await presentation()
                return p["decisions"][str(b.vm_id)]["kind"]=="status"
            await until(normal)
            p=await presentation()
            assert p["state"]["binding_scope"]=="candidate"
            assert p["state"]["vms"][0]["app_state"]=="unknown"
            assert p["state"]["vms"][1]["role"]=="backup_drive"
            assert p["state"]["drive"]["mode"]=="unknown"
            assert all(v["os"]["observed"] is None for v in p["state"]["vms"])
            evidence["cases"].append("manual_report_reaches_vm2_only_without_inventing_mode_or_os")
            bad=report(99);bad["vm_id"]=str(bindings[0].vm_id)
            await publish(bad)
            await asyncio.sleep(.3)
            p=await presentation()
            assert p["state"]["vms"][0]["app_state"]=="unknown"
            assert p["state"]["vms"][1]["sources"]["app"]["sequence"]==1
            evidence["cases"].append("wrong_vm_on_correct_app_topic_is_rejected")
            async with connect(f"ws://127.0.0.1:{feed_port}/ws/v1/display/state",additional_headers={"Authorization":f"Bearer {token}"}) as ws:
                state=json.loads(await asyncio.wait_for(ws.recv(),3))
                assert state["vms"][1]["app_id"]=="manual" and state["vms"][1]["reported_app_state"]=="active"
            evidence["cases"].append("authenticated_websocket_exposes_same_uuid_and_report")
            await publish(report(2,"error"))
            async def error(): return (await presentation())["decisions"][str(b.vm_id)]["kind"]=="app_error"
            await until(error)
            p=await presentation()
            evidence["observations"].append({"xen":"running","report":"error","decision":p["decisions"][str(b.vm_id)]["kind"]})
            evidence["cases"].append("app_error_selects_error_panel_while_vm_stays_running")
            driver.rows=[replace(row,state=VmState.paused) if row.vm_id==b.vm_id else row for row in driver.rows]
            async def paused(): return (await presentation())["decisions"][str(b.vm_id)]["kind"]=="paused"
            await until(paused)
            evidence["observations"].append({"xen":"paused","report":"previous error","decision":"paused"})
            evidence["cases"].append("xen_paused_has_priority_over_previous_app_report")
            driver.rows=[replace(row,state=VmState.running) for row in driver.rows]
            async def running(): return (await presentation())["state"]["vms"][1]["xen_state"]=="running"
            await until(running)
            await publish(report(3,"standby"))
            await until(normal)
            p=await presentation()
            assert p["state"]["vms"][1]["reported_app_state"]=="standby"
            evidence["observations"].append({"xen":"running","report":"standby","decision":"status"})
            evidence["cases"].append("fresh_report_restores_normal_panel_without_vm_control_commands")
        evidence.update(passed=True,completed_at=datetime.now(timezone.utc).isoformat())
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(evidence,ensure_ascii=False))
    finally:
        if server:
            server.should_exit=True
            if task: await asyncio.wait_for(task,10)
        publisher.disconnect();publisher.loop_stop()
        await broker.shutdown()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if not args.execute: parser.error("--execute required for isolated loopback transport test")
    asyncio.run(run(args.output))
