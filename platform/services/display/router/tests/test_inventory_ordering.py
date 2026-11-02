"""Inventory is provisional until a fresh heartbeat of the exact Agent session."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from test_feed import VM3, domains, message, settings, store
from test_source_bindings import Subscriber, acknowledge
from xenova_display.mqtt import MqttSource

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


@pytest.mark.parametrize('same_session', [True, False])
def test_retained_inventory_waits_for_matching_live_agent_and_preserves_provenance(store, same_session):
    retained = message(VM3, kind='agent.inventory', os={'id': 'debian'})
    assert not store.ingest(TOPIC, retained, retained=True)
    assert vm(store)['os']['observed'] is None
    store.now[0] += 2
    store.poll_success(domains())
    assert heartbeat(store, instance='publisher-1' if same_session else 'different-agent')
    row = vm(store)
    if same_session:
        assert row['os']['observed'] == 'debian'
        assert row['sources']['inventory']['retained'] is True
        assert row['sources']['inventory']['age_ms'] == 2000
        assert row['os']['current_run_verified'] is False
    else:
        assert row['os']['observed'] is None


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


def test_retained_inventory_replayed_once_after_first_verified_live_agent(store):
    source, client = MqttSource(store), Subscriber()
    source.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
    acknowledge(source, client)
    # MQTT is ready before the slow initial Xen query. Do not accept that OS
    # report as evidence of a current running VM.
    store.poll_failure()
    report = message(VM3, kind='agent.inventory', os={'id': 'debian'})
    source.on_message(client, None, SimpleNamespace(topic=TOPIC, payload=json.dumps(report).encode(), retain=True))
    assert vm(store)['os']['observed'] is None
    original_count = len(client.mids)
    store.poll_success(domains())
    hb = message(VM3, kind='agent.heartbeat')
    source.on_message(client, None, SimpleNamespace(topic=HEARTBEAT, payload=json.dumps(hb).encode(), retain=False))
    assert len(client.mids) == original_count + 1
    assert client.mids[max(client.mids)] == TOPIC
    acknowledge(source, client)
    source.on_message(client, None, SimpleNamespace(topic=TOPIC, payload=json.dumps(report).encode(), retain=True))
    assert vm(store)['os']['observed'] == 'debian'
    assert vm(store)['sources']['inventory']['retained'] is True
    hb['seq'] += 1
    source.on_message(client, None, SimpleNamespace(topic=HEARTBEAT, payload=json.dumps(hb).encode(), retain=False))
    assert len(client.mids) == original_count + 1  # no per-heartbeat resubscribe load


def test_manager_restart_replays_inventory_without_rebooting_guest(store):
    rows = [replace(d, observation_id=f'producer-a:identity-{d.domid}') for d in domains()]
    store.poll_success(rows)
    source, client = MqttSource(store), Subscriber()
    source.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
    acknowledge(source, client)
    def deliver(topic, envelope, retained=False):
        source.on_message(client, None, SimpleNamespace(topic=topic,
            payload=json.dumps(envelope).encode(), retain=retained))
    deliver(HEARTBEAT, message(VM3, kind='agent.heartbeat'))
    acknowledge(source, client)
    inv = message(VM3, kind='agent.inventory', os={'id': 'debian'})
    deliver(TOPIC, inv, True)
    assert vm(store)['os']['observed'] == 'debian'
    count = len(client.mids)
    store.poll_success([replace(d, observation_id=f'producer-b:identity-{d.domid}') for d in rows])
    assert vm(store)['os']['observed'] is None
    deliver(HEARTBEAT, message(VM3, kind='agent.heartbeat', seq=2))
    assert len(client.mids) == count + 1
    acknowledge(source, client)
    deliver(TOPIC, inv, True)
    assert vm(store)['os']['observed'] == 'debian'
