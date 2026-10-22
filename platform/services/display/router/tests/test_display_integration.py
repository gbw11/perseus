"""Deterministic source -> policy -> authenticated REST/WS transition checks.

Synthetic source observations, not evidence of real vehicle takeover or HDMI.
"""
from dataclasses import replace
import subprocess

from fastapi.testclient import TestClient

from dom0_manager.models import VmState
from xenova_display.main import create_app
from xenova_display.config import DISPLAY_DRIVE_TOPIC, DISPLAY_DRIVE_TYPE
from xenova_display.outputs import X11Layout
from test_feed import VM1, VM2, VM3, domains, message, settings, store  # noqa: F401
from test_outputs import config


def test_control_failover_and_driving_mode_through_rest_and_ws(settings, store):
    class Driver:
        rows=domains()
        def list_domains(self): return self.rows
    class NoMqtt:
        def __init__(self,s): pass
        def start(self): pass
        def stop(self): pass
    driver=Driver()
    headers={"Authorization":"Bearer test-only-token"}
    def control(sequence,active,mode):
        env=message(kind=DISPLAY_DRIVE_TYPE,seq=sequence,active_vm_id=active,mode=mode)
        env.pop("vm_id");env["source_id"]="dom0-gateway"
        assert store.ingest(DISPLAY_DRIVE_TOPIC,env)
    with TestClient(create_app(settings,store=store,driver=driver,mqtt_factory=NoMqtt)) as client:
        def current():
            r=client.get("/api/v1/display/presentation",headers=headers)
            assert r.status_code==200
            return r.json()
        control(1,VM1,"AUTO")
        initial=current()
        assert initial["outputs"][1]["vm_id"]==VM1
        select={"policy_epoch":initial["policy_epoch"],"revision":initial["revision"],"selection":"follow_active"}
        assert client.post("/api/v1/display/selection",headers=headers,json=select).status_code==200
        # Recognition failure's confirmed MANUAL mode does not itself change VM.
        control(2,VM1,"MANUAL")
        manual=current()
        assert manual["outputs"][1]["vm_id"]==VM1
        assert manual["state"]["drive"]["mode"]=="MANUAL"
        assert manual["state"]["drive"]["ai_status"]=="unknown"
        # A VM crash is only a display observation until the controller confirms takeover.
        driver.rows=[replace(d,state=VmState.crashed) if str(d.vm_id)==VM1 else d for d in domains()]
        store.poll_success(driver.rows)
        crashed=current()
        assert crashed["decisions"][VM1]["kind"]=="crashed"
        assert crashed["outputs"][1]["vm_id"]==VM1
        control(3,VM2,"MANUAL")
        takeover=current()
        assert takeover["outputs"][1]["vm_id"]==VM2
        assert takeover["state"]["drive"]["mode"]=="MANUAL"
        assert [v["os"]["expected"] for v in takeover["state"]["vms"]]==["zephyr","zephyr","debian"]
        obsolete={k:manual[k] for k in ("policy_epoch","revision","render_revision")}
        assert client.post("/api/v1/display/outputs/monitor-2/ack",headers=headers,json=obsolete).status_code==409
        with client.websocket_connect("/ws/v1/display/state",headers=headers) as ws:
            assert ws.receive_json()["drive"]["active_vm_id"]==VM2
        # Control expiry removes live driving claims, retains last confirmed detail target.
        store.now[0]+=3
        expired=current()
        assert expired["state"]["drive"]["mode"]=="unknown"
        assert expired["state"]["drive"]["active_vm_id"] is None
        assert expired["outputs"][1]["vm_id"]==VM2


def test_new_domain_clears_ready_app_and_old_render_ack(settings,store):
    from xenova_display.policy import DisplayPolicy,RenderAck
    from test_feed import app
    policy=DisplayPolicy(settings,store.epoch,clock=store.clock)
    assert app(store)
    ready=policy.presentation(store.snapshot())
    assert ready["decisions"][VM1]["kind"]=="status"
    ack=RenderAck(**{k:ready[k] for k in ("policy_epoch","revision","render_revision")})
    policy.acknowledge("monitor-2",ack,store.snapshot())
    store.poll_success(domains(primary_domid=19))
    new=policy.presentation(store.snapshot())
    assert new["outputs"][1]["renderer_ack"] is None
    assert new["decisions"][VM1]["kind"]=="app_waiting"
    assert not app(store,seq=2)
    assert app(store,boot="new-boot")
    assert policy.presentation(store.snapshot())["decisions"][VM1]["kind"]=="status"


def test_xrandr_reconnect_reapplies_only_returning_connector():
    import hashlib
    cfg=config("x11")
    edids=["00"*16,"11"*16]
    for output,edid in zip(cfg.outputs,edids):
        output.edid_sha256=hashlib.sha256(bytes.fromhex(edid)).hexdigest()
    attached=[True,True];commands=[]
    def run(cmd,**kwargs):
        commands.append(cmd)
        text=""
        for i in range(2):
            text+=(f"HDMI-{i+1} connected 1280x720+{i*1280}+0\n\tEDID:\n\t\t{edids[i]}\n   1280x720 60.00*\n"
                   if attached[i] else f"HDMI-{i+1} disconnected\n")
        return subprocess.CompletedProcess(cmd,0,text,"")
    layout=X11Layout(cfg,runner=run)
    assert all(v["physical_verified"] for v in layout.reconcile().values())
    attached[0]=False
    missing=layout.reconcile()
    assert missing["monitor-1"]["status"]=="disconnected"
    assert missing["monitor-2"]["physical_verified"]
    attached[0]=True;commands.clear()
    assert all(v["physical_verified"] for v in layout.reconcile().values())
    changes=[cmd for cmd in commands if "--output" in cmd]
    assert len(changes)==1
    assert "HDMI-1" in changes[0] and "HDMI-2" not in changes[0]
