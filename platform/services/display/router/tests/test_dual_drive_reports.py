"""Synthetic VM/app reports through real collector callbacks, policy, REST and WS.

No board, firmware, MQTT socket, control publisher or drive authority is used.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from dom0_manager.drivers.base import Domain
from dom0_manager.models import VmState
from fastapi.testclient import TestClient
import pytest
from xenova_mqtt.envelope import Identity, build

from xenova_display.config import Binding, FeedSettings, load_bindings
from xenova_display.main import create_app
from xenova_display.mqtt import MqttSource
from xenova_display.policy import DisplayPolicy
from xenova_display.state import StateStore
from test_source_bindings import Subscriber, acknowledge

PROFILE = Path(__file__).parents[1] / "examples/dual-drive-candidate-bindings.json"


def domains(store):
    return [Domain(b.vm_id, VmState.running, 1, 16, domid=i+1) for i, b in enumerate(store.settings.bindings)]


@pytest.fixture
def rig():
    bindings, scope = load_bindings(PROFILE, allow_candidate=True)
    now = [100.0]
    store = StateStore(FeedSettings(bindings, "test-only-token", binding_scope=scope), clock=lambda: now[0])
    store.now = now
    store.poll_success(domains(store))
    source = MqttSource(store)
    subscriber = Subscriber()
    source.on_connect(subscriber, None, None, SimpleNamespace(is_failure=False), None)
    acknowledge(source, subscriber)
    return store, source, subscriber


def report(store, index=0, app="autonomous", kind="app.heartbeat", *, seq=None, boot="boot", instance="publisher", **payload):
    binding = store.settings.bindings[index]
    if seq is None:
        seq = 1 if kind == "app.heartbeat" else 2
    if not payload:
        payload = ({"state": "standby", "uptime_s": 1} if kind == "app.heartbeat" else
                   {"applied_generation": 7, "progress_seq": "10", "readiness": "READY", "reason": None})
    return binding.app_topic(app), build(Identity(vm_id=str(binding.vm_id), app_id=app,
        boot_id=boot, agent_instance_id=instance), kind, payload, seq)


def send(rig, index=0, app="autonomous", kind="app.heartbeat", **kwargs):
    store, source, _ = rig
    topic, envelope = report(store, index, app, kind, **kwargs)
    source.on_message(None, None, SimpleNamespace(topic=topic, payload=json.dumps(envelope).encode(), retain=False, qos=0))
    return topic, envelope


def vm(store, index=0):
    return store.snapshot()["vms"][index]


def test_configuration_explicit_opt_in_and_no_legacy_fallback(rig):
    store, source, _ = rig
    with pytest.raises(ValueError): load_bindings(PROFILE)
    assert len(source.topics) == 8
    for index in (0, 1):
        for app in ("autonomous", "manual"):
            assert store.settings.bindings[index].app_topic(app) in source.topics
    assert "xenova/v1/apps/autonomous/events" not in source.topics
    topic, env = report(store)
    assert not store.ingest("xenova/v1/apps/autonomous/events", env)
    assert vm(store)["app_state"] == "unknown"


@pytest.mark.parametrize("change", ["one_app", "duplicate", "wrong_app", "mixed", "implicit", "linux"])
def test_ambiguous_new_bindings_are_rejected(change):
    raw = json.loads(PROFILE.read_text())["bindings"][0]
    if change == "one_app": raw["app_ids"] = ["autonomous"]
    if change == "duplicate": raw["app_ids"] = ["manual", "manual"]
    if change == "wrong_app": raw["app_ids"] = ["autonomous", "primary-drive"]
    if change == "mixed": raw["app_id"] = "autonomous"
    if change == "implicit": raw.pop("app_topic_scope")
    if change == "linux": raw.update(role="interaction", expected_os="debian")
    with pytest.raises(ValueError): Binding.model_validate(raw)


def test_four_heartbeats_are_isolated_even_with_equal_seq_and_publisher_ids(rig):
    store, _, _ = rig
    for index in (0, 1):
        for app in ("autonomous", "manual"): send(rig, index, app)
    state = store.snapshot()
    for row in state["vms"][:2]:
        assert row["app_id"] is None and row["app_state"] == "ready"
        assert set(row["apps"]) == {"autonomous", "manual"}
        assert all(a["source"]["status"] == "fresh" for a in row["apps"].values())
    assert state["vms"][2]["app_state"] == "unknown"
    assert state["drive"]["active_vm_id"] is None and state["drive"]["mode"] == "unknown"


def test_one_missing_app_cannot_be_masked_by_sibling_or_other_vm(rig):
    store, _, _ = rig
    send(rig, 0, "autonomous")
    send(rig, 1, "autonomous")
    send(rig, 1, "manual")
    assert vm(store)["app_state"] == "unknown"
    assert vm(store)["apps"]["manual"]["source"]["status"] == "unknown"
    assert vm(store, 1)["app_state"] == "ready"
    store.now[0] += 3
    store.poll_success(domains(store))
    send(rig, 1, "manual", seq=2)
    assert vm(store, 1)["apps"]["autonomous"]["source"]["status"] == "stale"
    assert vm(store, 1)["app_state"] == "unknown"


@pytest.mark.parametrize("change", ["wrong_vm", "wrong_app", "unknown_vm", "retained", "qos", "source_id", "nan_uptime", "bool_uptime"])
def test_invalid_heartbeat_cannot_pollute_any_app(rig, change):
    store, _, _ = rig
    topic, env = report(store)
    if change == "wrong_vm": env["vm_id"] = str(store.settings.bindings[1].vm_id)
    if change == "wrong_app": env["app_id"] = "manual"
    if change == "unknown_vm": topic = topic.replace(str(store.settings.bindings[0].vm_id), "44444444-4444-4444-8444-444444444444")
    if change == "source_id": env["source_id"] = "gateway"
    if change == "nan_uptime": env["payload"]["uptime_s"] = float("nan")
    if change == "bool_uptime": env["payload"]["uptime_s"] = True
    assert not store.ingest(topic, env, retained=change == "retained", qos=1 if change == "qos" else 0)
    assert all(a["app_state"] == "unknown" for row in store.snapshot()["vms"] for a in row["apps"].values())


def test_reported_ready_is_not_mode_owner_or_trusted_authority(rig):
    store, _, _ = rig
    # READY alone cannot establish a boot or an app session.
    topic, ready = report(store, kind="app.readiness")
    assert not store.ingest(topic, ready)
    send(rig)
    send(rig, kind="app.readiness")
    app = vm(store)["apps"]["autonomous"]
    assert app["readiness"]["current"]["readiness"] == "READY"
    assert not app["readiness"]["execution_verified"] and not app["readiness"]["authority_verified"]
    assert vm(store)["app_state"] == "unknown"  # manual heartbeat still missing
    assert store.snapshot()["drive"]["mode"] == "unknown"


@pytest.mark.parametrize("key,value", [
    ("applied_generation", True), ("applied_generation", -1), ("applied_generation", 2**32),
    ("applied_generation", 0), ("progress_seq", 1), ("progress_seq", "01"),
    ("progress_seq", "0"), ("progress_seq", "-1"), ("progress_seq", "１"),
    ("progress_seq", str(2**64)), ("readiness", "ready"), ("reason", "INPUT_STALE"),
    ("extra", "unsupported"),
])
def test_closed_readiness_contract_rejects_bad_fields(rig, key, value):
    store, _, _ = rig
    send(rig)
    topic, env = report(store, kind="app.readiness")
    env["payload"][key] = value
    assert not store.ingest(topic, env)
    assert vm(store)["apps"]["autonomous"]["readiness"]["reported"] is None


def test_ready_needs_new_progress_and_expires_without_it(rig):
    store, _, _ = rig
    send(rig)
    send(rig, kind="app.readiness")
    store.now[0] += .2
    topic, repeated = report(store, kind="app.readiness", seq=3)
    assert not store.ingest(topic, repeated)
    store.now[0] += .101
    assert vm(store)["apps"]["autonomous"]["readiness"]["current"] is None
    assert vm(store)["apps"]["autonomous"]["source"]["status"] == "fresh"
    stopped = deepcopy(repeated)
    stopped["seq"] = 4
    stopped["payload"].update(readiness="NOT_READY", reason="INPUT_STALE")
    assert store.ingest(topic, stopped)  # same progress may cancel readiness
    assert vm(store)["apps"]["autonomous"]["readiness"]["current"]["readiness"] == "NOT_READY"
    assert not store.ingest(topic, stopped)
    regressed = deepcopy(stopped)
    regressed["seq"] = 5
    regressed["payload"]["progress_seq"] = "9"
    assert not store.ingest(topic, regressed)
    regressed["payload"].update(progress_seq="11", applied_generation=6)
    assert not store.ingest(topic, regressed)


def test_one_publisher_restart_invalidates_only_its_app_and_rejects_old_instance(rig):
    store, _, _ = rig
    for app in ("autonomous", "manual"):
        send(rig, app=app)
        send(rig, app=app, kind="app.readiness")
    send(rig, instance="new-publisher")
    assert vm(store)["apps"]["autonomous"]["readiness"]["current"] is None
    assert vm(store)["apps"]["manual"]["readiness"]["current"] is not None
    topic, old = report(store, seq=99)
    assert not store.ingest(topic, old)
    topic, wrong_session = report(store, app="manual", kind="app.readiness", instance="new-publisher", seq=2)
    assert not store.ingest(topic, wrong_session)
    send(rig, kind="app.readiness", instance="new-publisher", progress_seq="1", applied_generation=7, readiness="READY", reason=None)
    assert vm(store)["apps"]["autonomous"]["readiness"]["current"]["progress_seq"] == "1"


def test_sequence_is_shared_across_report_types_but_not_across_apps(rig):
    store, _, _ = rig
    send(rig)
    send(rig, kind="app.readiness", seq=20)
    topic, delayed = report(store, seq=19)
    assert not store.ingest(topic, delayed)
    assert vm(store)["apps"]["autonomous"]["source"]["sequence"] == 1
    send(rig, app="manual")
    assert vm(store)["apps"]["manual"]["source"]["sequence"] == 1
    topic, fresh = report(store, seq=21)
    assert store.ingest(topic, fresh)
    topic, delayed_ready = report(store, kind="app.readiness", seq=20)
    assert not store.ingest(topic, delayed_ready)
    topic, repeated = report(store, kind="app.readiness", seq=30)
    assert not store.ingest(topic, repeated)  # no new completed work
    topic, delayed = report(store, seq=29)
    assert not store.ingest(topic, delayed)
    assert vm(store)["apps"]["autonomous"]["source"]["sequence"] == 21


def test_uint64_progress_preserved_and_error_does_not_change_control_or_sibling(rig):
    store, _, _ = rig
    for app in ("autonomous", "manual"):
        send(rig, app=app)
        send(rig, app=app, kind="app.readiness", applied_generation=7,
             progress_seq=str(2**64 - 1), readiness="READY", reason=None)
    send(rig, seq=3, state="error", uptime_s=2)
    row = vm(store)
    assert row["app_state"] == "failed"
    assert row["apps"]["autonomous"]["readiness"]["current"] is None
    assert row["apps"]["manual"]["readiness"]["current"]["progress_seq"] == str(2**64 - 1)
    assert row["apps"]["manual"]["app_state"] == "ready"
    assert store.snapshot()["drive"]["active_vm_id"] is None
    assert store.snapshot()["drive"]["mode"] == "unknown"


def test_topic_suback_and_reconnect_require_new_reports_per_app(rig):
    store, source, subscriber = rig
    for app in ("autonomous", "manual"):
        send(rig, app=app)
        send(rig, app=app, kind="app.readiness")
    source.on_disconnect(subscriber, None, None, None, None)
    assert vm(store)["app_state"] == "unknown"
    replacement = Subscriber()
    source.on_connect(replacement, None, None, SimpleNamespace(is_failure=False), None)
    topic, env = report(store, seq=3)
    assert not store.ingest(topic, env)
    acknowledge(source, replacement, denied=(store.settings.bindings[0].app_topic("manual"),))
    assert store.ingest(topic, env)
    assert vm(store)["apps"]["autonomous"]["source"]["status"] == "fresh"
    assert vm(store)["apps"]["autonomous"]["readiness"]["current"] is None
    p = DisplayPolicy(store.settings, store.epoch).presentation(store.snapshot())
    assert p["decisions"][str(store.settings.bindings[0].vm_id)]["kind"] == "app_source_unavailable"


def test_wire_limit_retained_and_qos_checked_before_readiness_is_stored(rig):
    store, source, _ = rig
    send(rig)
    topic, env = report(store, kind="app.readiness")
    raw = json.dumps(env).encode()
    for payload, retained, qos in ((raw + b" " * 1024, False, 0), (raw, True, 0), (raw, False, 1)):
        source.on_message(None, None, SimpleNamespace(topic=topic, payload=payload, retain=retained, qos=qos))
        assert vm(store)["apps"]["autonomous"]["readiness"]["reported"] is None
    env["message_id"] = "x" * 1024
    assert not store.ingest(topic, env)


def test_real_xen_observation_priority_and_replacement_clears_both_apps(rig):
    store, _, _ = rig
    for app in ("autonomous", "manual"):
        send(rig, app=app)
        send(rig, app=app, kind="app.readiness")
    rows = domains(store)
    store.poll_success([replace(d, state=VmState.paused) if i == 0 else d for i, d in enumerate(rows)])
    assert vm(store)["app_state"] == "unknown"
    assert all(a["readiness"]["current"] is None for a in vm(store)["apps"].values())
    store.poll_success([replace(d, domid=10) if i == 0 else d for i, d in enumerate(rows)])
    topic, old = report(store, seq=99)
    assert not store.ingest(topic, old)
    send(rig, boot="new-boot")
    assert vm(store)["apps"]["manual"]["source"]["status"] == "unknown"


def test_synthetic_dual_reports_available_through_authenticated_rest_and_ws(rig):
    store, _, _ = rig
    class Driver:
        def list_domains(self): return domains(store)
    class NoMqtt:
        def __init__(self, store): pass
        def start(self): pass
        def stop(self): pass
    for index in (0, 1):
        for app in ("autonomous", "manual"):
            send(rig, index, app)
            send(rig, index, app, kind="app.readiness")
    headers = {"Authorization": "Bearer test-only-token"}
    service = create_app(store.settings, store=store, driver=Driver(), mqtt_factory=NoMqtt)
    with TestClient(service) as client:
        assert client.get("/api/v1/display/state").status_code == 401
        state = client.get("/api/v1/display/state", headers=headers).json()
        assert all(len(v["apps"]) == 2 for v in state["vms"][:2])
        assert state["drive"]["active_vm_id"] is None
        presentation = client.get("/api/v1/display/presentation", headers=headers).json()
        assert all(presentation["decisions"][str(b.vm_id)]["kind"] == "status" for b in store.settings.bindings[:2])
        with client.websocket_connect("/ws/v1/display/state", headers=headers) as socket:
            frame = socket.receive_json()
            assert frame["vms"][1]["apps"]["manual"]["readiness"]["current"]["readiness"] == "READY"
            assert frame["drive"]["mode"] == "unknown"
