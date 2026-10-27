"""Inventory is provisional until a fresh heartbeat of the exact Agent session."""
from dataclasses import replace

import pytest

from test_feed import VM3, domains, message, settings, store

TOPIC = f"xenova/v1/vms/{VM3}/inventory"
HEARTBEAT = f"xenova/v1/vms/{VM3}/heartbeat"


def inventory(store, **kwargs):
    return store.ingest(TOPIC, message(VM3, kind="agent.inventory", os={"id": "debian"}, **kwargs))


def heartbeat(store, **kwargs):
    return store.ingest(HEARTBEAT, message(VM3, kind="agent.heartbeat", **kwargs))


def vm(store):
    return store.snapshot()["vms"][2]


def reconnect(store):
    store.mqtt_connection(True, [TOPIC, HEARTBEAT])
    store.subscription(TOPIC, "granted")
    store.subscription(HEARTBEAT, "granted")


def test_inventory_before_heartbeat_is_deferred_without_claiming_os_or_liveness(store):
    assert not inventory(store)
    assert vm(store)["os"]["observed"] is None
    assert vm(store)["guest_boot_id"] is None
    assert vm(store)["sources"]["agent"]["status"] == "unknown"
    store.now[0] += 2
    store.poll_success(domains())
    assert heartbeat(store)
    row = vm(store)
    assert row["os"]["observed"] == "debian"
    assert row["sources"]["inventory"]["age_ms"] == 2000
    assert not row["os"]["current_run_verified"]


@pytest.mark.parametrize("change", ["boot", "instance", "expired", "disconnect", "xen_replaced", "inventory_denied"])
def test_pending_inventory_cannot_cross_session_or_transport_or_execution(store, change):
    reconnect(store)
    assert not inventory(store)
    kwargs = {}
    if change == "boot": kwargs["boot"] = "new-boot"
    if change == "instance": kwargs["instance"] = "new-instance"
    if change == "expired":
        store.now[0] += 15
        store.poll_success(domains())
    if change == "disconnect":
        store.mqtt_connection(False)
        reconnect(store)
    if change == "xen_replaced":
        store.poll_success([replace(d, domid=25) if str(d.vm_id) == VM3 else d for d in domains()])
    if change == "inventory_denied": store.subscription(TOPIC, "denied")
    assert heartbeat(store, **kwargs)
    assert vm(store)["os"]["observed"] is None


def test_only_latest_pending_sequence_is_kept(store):
    assert not inventory(store, seq=5)
    assert not inventory(store, seq=4)
    assert not inventory(store, seq=5)
    assert heartbeat(store)
    assert vm(store)["sources"]["inventory"]["sequence"] == 5


def test_retired_agent_inventory_cannot_replace_current_os(store):
    assert heartbeat(store)
    assert inventory(store)
    assert heartbeat(store, instance="new-agent")
    assert vm(store)["os"]["observed"] is None
    assert not inventory(store, seq=100)
    assert inventory(store, instance="new-agent")
    assert vm(store)["os"]["observed"] == "debian"
    assert not heartbeat(store, seq=100)


def test_reconnect_requires_new_agent_report_before_new_inventory_is_shown_fresh(store):
    reconnect(store)
    assert heartbeat(store)
    assert inventory(store)
    store.mqtt_connection(False)
    assert vm(store)["sources"]["inventory"]["status"] == "disconnected"
    reconnect(store)
    assert not inventory(store, seq=2)
    assert vm(store)["sources"]["inventory"]["status"] == "disconnected"
    assert heartbeat(store, seq=2)
    assert vm(store)["sources"]["inventory"]["status"] == "fresh"
    assert vm(store)["sources"]["agent"]["status"] == "fresh"


def test_provisional_buffer_is_bounded_and_never_displays_without_matching_heartbeat(store):
    for i in range(10):
        assert not inventory(store, instance=f"agent-{i}")
    assert len(store.guests[VM3]["pending_inventory"]) == 4
    assert vm(store)["os"]["observed"] is None
    assert heartbeat(store, instance="agent-3")
    assert vm(store)["os"]["observed"] == "debian"
    assert not store.guests[VM3]["pending_inventory"]
