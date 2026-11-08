"""Warm supervision is separate from calculation, Xen identity and authority."""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from xenova_display.config import Binding
from xenova_display.mqtt import MqttSource
from xenova_display.state import StateStore
from xenova_display.policy import DisplayPolicy
from test_dual_drive_reports import rig, domains, report, vm  # noqa: F401
from test_source_bindings import Subscriber, acknowledge


@pytest.fixture
def warm(rig):
    old, _, _ = rig
    bindings = tuple(Binding.model_validate({**b.model_dump(), "report_profile": "warm-drive-1"})
                     if b.app_topic_scope == "vm_scoped" else b for b in old.settings.bindings)
    store = StateStore(replace(old.settings, bindings=bindings), clock=lambda: old.now[0])
    store.now = old.now
    store.poll_success(domains(store))
    source = MqttSource(store)
    subscriber = Subscriber()
    source.on_connect(subscriber, None, None, SimpleNamespace(is_failure=False), None)
    acknowledge(source, subscriber)
    return store, source, subscriber


def lifecycle(warm, *, app="autonomous", index=0, seq=1, check="1", progress="0",
              generation=1, phase="WAITING", readiness="QUALIFIED", reason=None,
              instance="publisher", boot="boot", **extra):
    store, _, _ = warm
    payload = dict(profile="warm-drive-1", applied_generation=generation, check_seq=check,
                   progress_seq=progress, phase=phase, readiness=readiness, reason=reason)
    payload.update(extra)
    return report(store, index, app, "app.lifecycle", seq=seq, instance=instance, boot=boot, **payload)


def put(warm, **kwargs):
    topic, envelope = lifecycle(warm, **kwargs)
    return warm[0].ingest(topic, envelope, qos=0), topic, envelope


def test_lifecycle_only_reports_start_each_app_but_never_claim_calculation_or_driving(warm):
    store, source, _ = warm
    for index in (0, 1):
        for app in ("autonomous", "manual"):
            topic, envelope = lifecycle(warm, index=index, app=app)
            source.on_message(None, None, SimpleNamespace(topic=topic, payload=json.dumps(envelope).encode(), retain=False, qos=0))
        row = vm(store, index)
        assert row["app_state"] == "unknown"
        for value in row["apps"].values():
            assert value["lifecycle"]["current"]["readiness"] == "QUALIFIED"
            assert value["lifecycle"]["source"]["status"] == "fresh"
            assert not value["lifecycle"]["execution_verified"] and not value["lifecycle"]["authority_verified"]
            assert value["readiness"]["current"] is None
        assert DisplayPolicy(store.settings, store.epoch).presentation(store.snapshot())["decisions"][row["vm_id"]]["kind"] == "app_standby"
    assert store.snapshot()["drive"]["active_vm_id"] is None


def test_input_waiting_is_reported_as_waiting_not_as_healthy_or_failed(warm):
    for app in ("autonomous", "manual"):
        assert put(warm, app=app, readiness="NOT_READY", reason="INPUT_UNAVAILABLE")[0]
    store = warm[0]
    decision = DisplayPolicy(store.settings, store.epoch).presentation(store.snapshot())["decisions"][vm(store)["vm_id"]]
    assert decision["kind"] == "app_preparing" and decision["tone"] == "warning"


def test_verified_profile_does_not_silently_accept_warm_profile(rig):
    topic, envelope = lifecycle(rig)
    assert not rig[0].ingest(topic, envelope)


def test_warm_profile_never_accepts_legacy_ready_as_warm_qualification(warm):
    store = warm[0]
    topic, envelope = report(store)
    assert store.ingest(topic, envelope)
    topic, envelope = report(store, kind="app.readiness")
    assert not store.ingest(topic, envelope)
    assert vm(store)["app_state"] == "unknown"


@pytest.mark.parametrize("change", [dict(profile="other"), dict(check_seq="01"), dict(check_seq="0"),
    dict(check_seq=str(2**64)), dict(progress_seq="-1"), dict(applied_generation=True),
    dict(phase="EXECUTING"), dict(readiness="READY"), dict(reason="INPUT_STALE"), dict(extra=True)])
def test_shared_contract_validation_rejects_invalid_payload(warm, change):
    topic, envelope = lifecycle(warm)
    envelope["payload"].update(change)
    assert not warm[0].ingest(topic, envelope, qos=0)


@pytest.mark.parametrize("bad", ["retained", "qos", "oversized", "wrong-vm", "wrong-app"])
def test_transport_and_identity_checks_are_not_relaxed(warm, bad):
    topic, envelope = lifecycle(warm)
    kwargs = dict(qos=0)
    if bad == "retained": kwargs["retained"] = True
    elif bad == "qos": kwargs["qos"] = 1
    elif bad == "oversized": kwargs["wire_size"] = 1025
    elif bad == "wrong-vm": envelope["vm_id"] = str(warm[0].settings.bindings[1].vm_id)
    else: envelope["app_id"] = "manual"
    assert not warm[0].ingest(topic, envelope, **kwargs)


def test_reversed_check_and_progress_do_not_refresh_source(warm):
    assert put(warm, check="10", progress="5")[0]
    for seq, check, progress, generation in ((2, "10", "5", 1), (3, "11", "4", 1), (4, "11", "5", 0)):
        warm[0].now[0] += .1
        assert not put(warm, seq=seq, check=check, progress=progress, generation=generation)[0]
    assert vm(warm[0])["apps"]["autonomous"]["lifecycle"]["source"]["sequence"] == 1


def test_new_supervision_checks_do_not_renew_calculation_lease(warm):
    store = warm[0]
    def ready(seq, check, progress="7", generation=1):
        return put(warm, seq=seq, check=check, progress=progress, generation=generation,
                   phase="EXECUTING", readiness="READY")[0]
    assert ready(1, "1")
    topic, envelope = report(store, seq=2)
    assert store.ingest(topic, envelope)
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["current"] is not None
    store.now[0] += .2
    assert ready(3, "2")
    store.now[0] += .11
    assert ready(4, "3")
    value = vm(store)["apps"]["autonomous"]
    assert value["lifecycle"]["source"]["status"] == "fresh"
    assert value["lifecycle"]["calculation_source"]["status"] == "stale"
    assert value["lifecycle"]["current"] is None and value["app_state"] == "unknown"
    assert ready(5, "4", "8")
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["current"]["readiness"] == "READY"
    assert ready(6, "5", "8", generation=2)
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["current"] is None


def test_app_restart_invalidates_only_that_app_and_rejects_old_instance(warm):
    assert put(warm)[0] and put(warm, app="manual")[0]
    assert put(warm, instance="new-instance", seq=10)[0]
    assert not put(warm, seq=11, check="2")[0]
    assert vm(warm[0])["apps"]["manual"]["lifecycle"]["current"] is not None


def test_disconnect_staleness_reconnect_and_original_observation_time(warm):
    store, source, subscriber = warm
    assert put(warm)[0]
    store.now[0] += .31
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["current"] is None
    source.on_disconnect(subscriber, None, None, None, None)
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["source"]["status"] == "disconnected"
    source.on_connect(subscriber, None, None, SimpleNamespace(is_failure=False), None)
    acknowledge(source, subscriber)
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["source"]["status"] != "fresh"
    assert put(warm, seq=2, check="2")[0]
    assert vm(store)["apps"]["autonomous"]["lifecycle"]["source"]["status"] == "fresh"


def test_supervision_counter_alone_does_not_churn_render_revision(warm):
    store = warm[0]
    for app in ("autonomous", "manual"): assert put(warm, app=app)[0]
    policy = DisplayPolicy(store.settings, store.epoch)
    before = policy.presentation(store.snapshot())["render_revision"]
    store.now[0] += .1
    for app in ("autonomous", "manual"): assert put(warm, app=app, seq=2, check="2")[0]
    assert policy.presentation(store.snapshot())["render_revision"] == before
    assert put(warm, seq=3, check="3", readiness="NOT_READY", reason="INPUT_STALE")[0]
    assert policy.presentation(store.snapshot())["render_revision"] != before


def test_warm_profile_is_not_allowed_on_legacy_binding(warm):
    data=warm[0].settings.bindings[2].model_dump()
    data["report_profile"]="warm-drive-1"
    with pytest.raises(ValueError): Binding.model_validate(data)


def test_warm_evidence_reaches_existing_rest_policy_and_read_only_websocket(warm):
    from fastapi.testclient import TestClient
    from xenova_display.main import create_app

    store = warm[0]
    for index in (0, 1):
        for app in ("autonomous", "manual"):
            assert put(warm, index=index, app=app, readiness="NOT_READY", reason="INPUT_UNAVAILABLE")[0]

    class ExistingSubscription:
        def __init__(self, store): pass
        def start(self): pass
        def stop(self): pass

    app = create_app(store.settings, store=store,
                     driver=SimpleNamespace(list_domains=lambda: domains(store)),
                     mqtt_factory=ExistingSubscription)
    headers = {"Authorization": f"Bearer {store.settings.token}"}
    with TestClient(app) as client:
        presentation = client.get("/api/v1/display/presentation", headers=headers).json()
        for row in presentation["state"]["vms"][:2]:
            assert presentation["decisions"][row["vm_id"]]["kind"] == "app_preparing"
        with client.websocket_connect("/ws/v1/display/state", headers=headers) as socket:
            value = socket.receive_json()
            assert value["drive"]["active_vm_id"] is None
            assert all(a["lifecycle"]["current"]["reason"] == "INPUT_UNAVAILABLE"
                       for v in value["vms"][:2] for a in v["apps"].values())
