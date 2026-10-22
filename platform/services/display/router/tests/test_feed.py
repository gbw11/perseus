from dataclasses import replace
import json
from pathlib import Path
from uuid import UUID

from dom0_manager.drivers.base import Domain
from dom0_manager.models import VmState
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from xenova_display.config import Binding, FeedSettings
from xenova_display.main import create_app
from xenova_display.state import StateStore

ROWS = json.loads((Path(__file__).parents[1] / "examples/qemu-bindings.json").read_text())
VM1, VM2, VM3 = [r["vm_id"] for r in ROWS]


@pytest.fixture
def settings():
    return FeedSettings(tuple(Binding.model_validate(r) for r in ROWS), "test-only-token")


@pytest.fixture
def store(settings):
    now = [100.0]
    s = StateStore(settings, clock=lambda: now[0])
    s.now = now
    s.mqtt_connected = True
    s.poll_success(domains())
    return s


def domains(primary_domid=18):
    return [Domain(UUID(vm), VmState.running, 1, 16, domid=domid)
            for vm, domid in zip((VM1, VM2, VM3), (primary_domid, 17, 13))]


def message(vm=VM1, kind="app.heartbeat", seq=1, boot="boot-1", instance="publisher-1", **payload):
    app = {VM1: "primary-drive", VM2: "backup-drive", VM3: "message-board"}[vm]
    return {"schema_version": "1.0", "message_id": f"m-{seq}", "vm_id": vm,
            "boot_id": boot, "agent_instance_id": instance, "type": kind, "seq": seq,
            "app_id": app, "sent_at": "2099-01-01T00:00:00Z", "payload": payload}


def app(store, vm=VM1, **kwargs):
    env = message(vm, state="active", **kwargs)
    return store.ingest(f"xenova/v1/apps/{env['app_id']}/events", env)


def test_observed_xen_does_not_invent_os_or_app_readiness(store):
    snapshot = store.snapshot()
    assert [v["role"] for v in snapshot["vms"]] == ["primary_drive", "backup_drive", "interaction"]
    assert [v["os"]["expected"] for v in snapshot["vms"]] == ["zephyr", "zephyr", "debian"]
    assert all(v["os"]["observed"] is None and v["app_state"] == "unknown" for v in snapshot["vms"])
    assert snapshot["drive"]["active_vm_id"] is None


def test_app_dedup_wrong_vm_and_retired_process(store):
    assert app(store)
    assert not app(store)
    assert app(store, instance="publisher-2")
    assert not app(store, seq=99)  # Old process cannot replace the new one.
    wrong = message(vm=VM2, state="active")
    assert not store.ingest("xenova/v1/apps/primary-drive/events", wrong)


def test_freshness_uses_local_clock_and_separates_xen_app(store):
    assert app(store)
    store.now[0] += 3
    store.poll_success(domains())
    vm = store.snapshot()["vms"][0]
    assert vm["sources"]["xen"]["status"] == "fresh"
    assert vm["sources"]["app"]["status"] == "stale"
    assert vm["app_state"] == "unknown"  # Future sent_at does not keep it fresh.


def test_domain_replacement_clears_prior_boot_and_rejects_delayed_old_data(store):
    assert app(store)
    store.poll_success(domains(primary_domid=19))
    assert store.snapshot()["vms"][0]["app_state"] == "unknown"
    assert not app(store, seq=2)
    assert app(store, boot="boot-2")
    assert not app(store, seq=99)


def test_inventory_cannot_establish_liveness_and_is_marked_unverified(store):
    topic = f"xenova/v1/vms/{VM3}/inventory"
    inv = message(VM3, kind="agent.inventory", os={"id": "debian"})
    assert not store.ingest(topic, inv, retained=True)
    hb = message(VM3, kind="agent.heartbeat")
    assert store.ingest(f"xenova/v1/vms/{VM3}/heartbeat", hb)
    assert store.ingest(topic, inv, retained=True)
    vm = store.snapshot()["vms"][2]
    assert vm["os"]["observed"] == "debian"
    assert vm["os"]["match"] is True
    assert vm["os"]["current_run_verified"] is False
    assert vm["sources"]["inventory"]["retained"] is True
    assert vm["app_state"] == "unknown"


def test_xen_failure_never_becomes_vm_stop_or_retains_ready_status(store):
    assert app(store)
    store.poll_failure()
    vm = store.snapshot()["vms"][0]
    assert vm["xen_state"] == "running"  # last observation, explicitly disconnected
    assert vm["sources"]["xen"]["status"] == "disconnected"
    assert vm["app_state"] == "unknown"
    assert not app(store, seq=2)


def test_mqtt_disconnect_does_not_change_xen_state(store):
    assert app(store)
    store.mqtt_connected = False
    vm = store.snapshot()["vms"][0]
    assert vm["xen_state"] == "running"
    assert vm["sources"]["app"]["status"] == "disconnected"
    assert vm["app_state"] == "unknown"


def test_mode_and_active_vm_are_independent_and_retained_control_is_not_live(store):
    control = message(kind="vehicle.control", mode="MANUAL", active_vm_id=VM1)
    control.pop("vm_id")
    control["source_id"] = "dom0-gateway"
    topic = "xenova/v1/vehicle/control"
    assert not store.ingest(topic, control, retained=True)
    assert store.ingest(topic, control)
    assert store.snapshot()["drive"]["active_vm_id"] == VM1
    assert store.snapshot()["drive"]["mode"] == "MANUAL"
    assert not store.ingest(topic, control)
    control.update(seq=2)
    control["payload"]["active_vm_id"] = VM2
    assert store.ingest(topic, control)
    assert store.snapshot()["drive"]["mode"] == "MANUAL"
    store.now[0] += 3
    assert store.snapshot()["drive"]["mode"] == "unknown"


@pytest.mark.parametrize("override", [{"vm_id": str(UUID(int=0))}, {"role": "manual"}, {"app_id": "#"}])
def test_bindings_reject_dom0_and_ambiguous_roles(override):
    with pytest.raises(ValueError):
        Binding.model_validate({**ROWS[0], **override})


def test_service_token_required(settings):
    with pytest.raises(ValueError):
        replace(settings, token="")


def test_real_http_and_ws_auth_full_snapshots_and_read_only(settings, store):
    class Driver:
        def list_domains(self): return domains()
    class NoMqtt:
        def __init__(self, s): pass
        def start(self): pass
        def stop(self): pass
    with TestClient(create_app(settings, driver=Driver(), store=store, mqtt_factory=NoMqtt)) as client:
        assert client.get("/api/v1/display/state").status_code == 401
        headers = {"Authorization": "Bearer test-only-token"}
        assert client.get("/api/v1/display/state", headers=headers).json()["vms"][0]["vm_id"] == VM1
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/v1/display/state"): pass
        with client.websocket_connect("/ws/v1/display/state", headers=headers) as ws:
            assert ws.receive_json()["schema_version"] == 1
            ws.send_json({"type": "input", "keys": ["w"]})
            with pytest.raises(WebSocketDisconnect) as closed: ws.receive_json()
            assert closed.value.code == 1008


def test_malformed_and_unregistered_sources_do_not_change_state(store):
    assert not store.ingest("anything", [])
    command = message(kind="agent.command")
    command.pop("boot_id")
    assert not store.ingest(f"xenova/v1/vms/{VM1}/command", command)
    env = message(state=["active"])
    assert not store.ingest("xenova/v1/apps/primary-drive/events", env)
    env = message(kind="vehicle.control", mode=["AUTO"])
    assert not store.ingest("xenova/v1/vehicle/control", env)
    assert store.snapshot()["vms"][0]["app_state"] == "unknown"
