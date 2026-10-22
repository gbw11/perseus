"""Opt-in real loopback MQTT -> display feed REST/WS contract acceptance.

Synthetic controller and Xen observations. No real controller, board, VM changes or ACL proof.
Requires the existing test runtime's amqtt, paho, httpx, websockets and display dependencies.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import secrets
import socket
import time
from uuid import UUID

from amqtt.broker import Broker
import httpx
import paho.mqtt.client as mqtt
import uvicorn
from websockets.asyncio.client import connect

from dom0_manager.drivers.base import Domain
from dom0_manager.models import VmState
from xenova_display.config import Binding,DISPLAY_DRIVE_TOPIC,DISPLAY_DRIVE_TYPE,FeedSettings
from xenova_display.main import create_app
from xenova_mqtt.envelope import Identity,build


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


async def until(test,timeout=15):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        if await test(): return
        await asyncio.sleep(.1)
    raise TimeoutError("display control contract condition not reached")


async def run(output):
    output.parent.mkdir(parents=True,exist_ok=True)
    port=free_port()
    broker=Broker({"listeners":{"default":{"type":"tcp","bind":f"127.0.0.1:{port}"}},
                   "plugins":{"amqtt.plugins.authentication.AnonymousAuthPlugin":{"allow_anonymous":True}}})
    await broker.start()
    publisher=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="display-contract-test-"+secrets.token_hex(6))
    server=task=None
    evidence={"scope":"real loopback broker and HTTP/WS; synthetic controller/Xen; not physical board or production ACL", "cases":[]}
    try:
        publisher.connect("127.0.0.1",port);publisher.loop_start()
        async def publish(topic,env,retain=False):
            info=publisher.publish(topic,json.dumps(env),qos=1,retain=retain)
            await asyncio.to_thread(info.wait_for_publish,3)
            assert info.is_published()
        bindings=tuple(Binding.model_validate(row) for row in json.loads((Path(__file__).parents[1]/"examples/qemu-bindings.json").read_text()))
        vm1,vm2,_=[str(b.vm_id) for b in bindings]
        identity=Identity(source_id="dom0-gateway")
        def report(seq,active=vm1,mode="MANUAL"):
            return build(identity,DISPLAY_DRIVE_TYPE,{"active_vm_id":active,"mode":mode},seq)
        # A retained old snapshot must not bootstrap current driving claims.
        await publish(DISPLAY_DRIVE_TOPIC,report(1),retain=True)
        class Driver:
            def list_domains(self):
                return [Domain(UUID(str(b.vm_id)),VmState.running,1,16,domid=i+1) for i,b in enumerate(bindings)]
        token=secrets.token_urlsafe(32)
        app=create_app(FeedSettings(bindings,token,mqtt_port=port),driver=Driver())
        sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(128)
        feed_port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level="warning",timeout_graceful_shutdown=2))
        task=asyncio.create_task(server.serve(sockets=[sock]))
        async def subscribed(): return server.started and app.state.store.mqtt_connected
        await until(subscribed)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{feed_port}",headers={"Authorization":f"Bearer {token}"}) as client:
            async def state():
                response=await client.get("/api/v1/display/state");response.raise_for_status()
                return response.json()
            await asyncio.sleep(.4)
            assert (await state())["drive"]["mode"]=="unknown"
            evidence["cases"].append("retained_bootstrap_is_not_live")
            old=build(identity,"vehicle.control",{"active_vm_id":vm2,"mode":"AUTO"},2)
            await publish("xenova/v1/vehicle/control",old)
            await asyncio.sleep(.3)
            assert (await state())["drive"]["active_vm_id"] is None
            evidence["cases"].append("legacy_vehicle_control_is_ignored")
            for seq in range(2,7):
                await publish(DISPLAY_DRIVE_TOPIC,report(seq))
                async def current(seq=seq):
                    drive=(await state())["drive"]
                    return drive["mode"]=="MANUAL" and drive["source"].get("sequence")==seq
                await until(current)
                await asyncio.sleep(1)
                assert (await state())["drive"]["mode"]=="MANUAL"
            evidence["cases"].append("one_second_unchanged_reports_keep_rest_live_with_increasing_seq")
            async with connect(f"ws://127.0.0.1:{feed_port}/ws/v1/display/state",additional_headers={"Authorization":f"Bearer {token}"}) as ws:
                received=json.loads(await asyncio.wait_for(ws.recv(),3))
                assert received["drive"]["active_vm_id"]==vm1
                evidence["cases"].append("same_confirmed_drive_state_reaches_authenticated_websocket")
            await publish(DISPLAY_DRIVE_TOPIC,report(6))
            async def stale():
                drive=(await state())["drive"]
                return drive["mode"]=="unknown" and drive["source"]["status"]=="stale" and drive["active_vm_id"] is None
            await until(stale)
            evidence["cases"].append("duplicate_does_not_refresh_and_three_second_silence_expires")
            await publish(DISPLAY_DRIVE_TOPIC,report(7,vm2,"MANUAL"))
            async def backup(): return (await state())["drive"]["active_vm_id"]==vm2
            await until(backup)
            p=(await client.get("/api/v1/display/presentation")).json()
            assert p["outputs"][1]["vm_id"]==vm1
            response=await client.post("/api/v1/display/selection",json={"policy_epoch":p["policy_epoch"],"revision":p["revision"],"selection":"follow_active"})
            response.raise_for_status()
            assert response.json()["outputs"][1]["vm_id"]==vm2
            assert response.json()["state"]["drive"]["mode"]=="MANUAL"
            evidence["cases"].append("fresh_backup_report_changes_detail_only_after_explicit_follow_active")
        evidence.update(passed=True,completed_at=datetime.now(timezone.utc).isoformat())
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(evidence))
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
    if not args.execute: parser.error("--execute required for isolated loopback test")
    asyncio.run(run(args.output))
