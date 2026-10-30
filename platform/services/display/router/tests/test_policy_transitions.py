"""254 acceptance: real collector/policy logic, synthetic observations, no board or broker."""
from copy import deepcopy

import pytest

from xenova_display.config import DISPLAY_DRIVE_TOPIC, DISPLAY_DRIVE_TYPE
from xenova_display.policy import DisplayPolicy, Selection, decide_vm
from xenova_display.state import StateStore
from test_feed import VM1, VM2, VM3, app, domains, message, settings, store  # noqa: F401


def control(store, *, seq, active=VM1, mode="AUTO"):
    env = message(kind=DISPLAY_DRIVE_TYPE, seq=seq, active_vm_id=active, mode=mode)
    env.pop("vm_id")
    env["source_id"] = "dom0-gateway"
    assert store.ingest(DISPLAY_DRIVE_TOPIC, env)


def follow(policy):
    policy.select(Selection(policy_epoch=policy.epoch, revision=policy.revision, selection="follow_active"))


@pytest.mark.parametrize("source", ["unknown", "stale", "disconnected"])
def test_past_app_failure_is_not_a_current_failure(store, source):
    vm = store.snapshot()["vms"][0]
    vm["app_state"] = "failed"
    vm["sources"]["app"]["status"] = source
    assert decide_vm(vm)["kind"] == "app_waiting"


def test_subscription_failure_takes_priority_over_previous_error(store):
    vm = store.snapshot()["vms"][0]
    vm["app_state"] = "failed"
    vm["sources"]["app"].update(status="disconnected", subscription_status="denied")
    assert decide_vm(vm)["kind"] == "app_source_unavailable"


@pytest.mark.parametrize("state,expected", [
    ("running", "status"), ("paused", "paused"), ("stopping", "stopping"),
    ("stopped", "stopped"), ("crashed", "crashed"),
])
def test_vm_state_changes_keep_pinned_detail_and_other_vm_decisions(settings, store, state, expected):
    policy = DisplayPolicy(settings, store.epoch)
    before = store.snapshot()
    for vm in before["vms"]:
        vm["app_state"] = "ready"
        vm["sources"]["app"]["status"] = "fresh"
    original = policy.presentation(before)
    changed = deepcopy(before)
    changed["vms"][0]["xen_state"] = state
    result = policy.presentation(changed)
    assert result["decisions"][VM1]["kind"] == expected
    assert result["outputs"] == original["outputs"]
    assert result["decisions"][VM2] == original["decisions"][VM2]
    assert result["decisions"][VM3] == original["decisions"][VM3]
    assert result["revision"] == original["revision"]
    recovered = policy.presentation(before)
    assert recovered["decisions"][VM1]["kind"] == "status"
    assert recovered["render_revision"] == original["render_revision"]


def test_real_collector_app_error_disconnect_and_recovery(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    topic = "xenova/v1/apps/primary-drive/events"
    store.mqtt_connection(True, (topic,))
    store.subscription(topic, "granted")
    assert app(store)
    assert policy.presentation(store.snapshot())["decisions"][VM1]["kind"] == "status"
    error = message(seq=2, state="error")
    assert store.ingest(topic, error)
    assert policy.presentation(store.snapshot())["decisions"][VM1]["kind"] == "app_error"
    store.mqtt_connection(False, (topic,))
    disconnected = policy.presentation(store.snapshot())
    assert disconnected["decisions"][VM1]["kind"] == "app_source_unavailable"
    assert disconnected["state"]["vms"][0]["xen_state"] == "running"
    store.mqtt_connection(True, (topic,))
    store.subscription(topic, "granted")
    # Reconnection alone is not a new heartbeat.
    assert policy.presentation(store.snapshot())["decisions"][VM1]["kind"] == "app_waiting"
    assert app(store, seq=3)
    assert policy.presentation(store.snapshot())["decisions"][VM1]["kind"] == "status"


def test_collector_loss_never_claims_vm_crash_and_recovers_on_new_observation(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    assert app(store)
    store.poll_failure()
    result = policy.presentation(store.snapshot())
    assert result["decisions"][VM1]["kind"] == "stale"
    assert result["outputs"][1]["vm_id"] == VM1
    store.poll_success(domains())
    assert policy.presentation(store.snapshot())["decisions"][VM1]["kind"] == "status"


def test_follow_changes_only_on_control_report_not_mode_or_fault(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    follow(policy)
    control(store, seq=1)
    first = policy.presentation(store.snapshot())
    control(store, seq=2, mode="MANUAL")
    manual = policy.presentation(store.snapshot())
    assert manual["outputs"] == first["outputs"]
    assert manual["state"]["drive"]["mode"] == "MANUAL"
    fault = store.snapshot()
    fault["vms"][0]["xen_state"] = "crashed"
    assert policy.presentation(fault)["outputs"][1]["vm_id"] == VM1
    control(store, seq=3, active=VM2, mode="MANUAL")
    backup = policy.presentation(store.snapshot())
    assert backup["outputs"][1]["vm_id"] == VM2
    assert backup["outputs"][0] == first["outputs"][0]
    store.now[0] += 3
    expired = policy.presentation(store.snapshot())
    assert expired["outputs"][1]["vm_id"] == VM2
    assert "미확인" in expired["outputs"][1]["reason"]
    assert expired["state"]["drive"]["active_vm_id"] is None
    assert expired["state"]["drive"]["mode"] == "unknown"
    control(store, seq=4, active=VM1, mode="AUTO")
    assert policy.presentation(store.snapshot())["outputs"][1]["vm_id"] == VM1


def test_follow_never_routes_to_interaction_vm_even_for_invalid_snapshot(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    follow(policy)
    state = store.snapshot()
    state["drive"].update(active_vm_id=VM3, source={"status": "fresh"})
    result = policy.presentation(state)
    assert result["outputs"][1]["vm_id"] == VM1
    assert "미확인" in result["outputs"][1]["reason"]


def test_initial_unknown_and_operation_overlay_do_not_become_normal(settings):
    store = StateStore(settings)
    policy = DisplayPolicy(settings, store.epoch)
    initial = policy.presentation(store.snapshot())
    assert all(decision["kind"] == "waiting" for decision in initial["decisions"].values())
    store.poll_success(domains())
    state = store.snapshot()
    state["vms"][0]["operation"] = {"status": "running", "kind": "recovery"}
    assert policy.presentation(state)["decisions"][VM1]["kind"] == "operation"
    state["vms"][0]["xen_state"] = "crashed"
    assert policy.presentation(state)["decisions"][VM1]["kind"] == "crashed"
