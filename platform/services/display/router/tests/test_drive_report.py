from copy import deepcopy
from types import SimpleNamespace

import pytest

from xenova_display.config import DISPLAY_DRIVE_TOPIC, DISPLAY_DRIVE_TYPE
from xenova_display.mqtt import MqttSource
from test_feed import VM1, VM2, VM3, domains, message, settings, store  # noqa: F401


def report(**kwargs):
    env=message(kind=DISPLAY_DRIVE_TYPE,mode="MANUAL",active_vm_id=VM1,**kwargs)
    env.pop("vm_id");env["source_id"]="dom0-gateway"
    return env


def test_only_display_topic_is_subscribed_and_vehicle_commands_are_ignored(store):
    calls=[]
    source=MqttSource(store)
    def subscribe(topic,**kwargs):
        calls.append((topic,kwargs))
        return 0,len(calls)
    source.on_connect(SimpleNamespace(subscribe=subscribe),None,None,
                      SimpleNamespace(is_failure=False),None)
    assert (DISPLAY_DRIVE_TOPIC,{"qos":1}) in calls
    assert not any(topic=="xenova/v1/vehicle/control" for topic,_ in calls)
    env=report();env["type"]="vehicle.control"
    assert not store.ingest("xenova/v1/vehicle/control",env)
    assert store.snapshot()["drive"]["mode"]=="unknown"


@pytest.mark.parametrize("change",["retained","source","type","vm_identity","interaction_vm","invalid_mode","missing_vm"])
def test_rejected_report_does_not_establish_liveness(store,change):
    env=report()
    if change=="source": env["source_id"]="untrusted-gateway"
    if change=="type": env["type"]="vehicle.control"
    if change=="vm_identity": env["vm_id"]=VM1
    if change=="interaction_vm": env["payload"]["active_vm_id"]=VM3
    if change=="invalid_mode": env["payload"]["mode"]="INVALID"
    if change=="missing_vm": env["payload"].pop("active_vm_id")
    assert not store.ingest(DISPLAY_DRIVE_TOPIC,env,retained=change=="retained")
    assert store.snapshot()["drive"]["active_vm_id"] is None


def test_unchanged_state_requires_new_sequence_each_second_and_expires_at_three(store):
    for seq in range(1,6):
        env=report(seq=seq)
        assert store.ingest(DISPLAY_DRIVE_TOPIC,env)
        store.now[0]+=1
        store.poll_success(domains())
        assert store.snapshot()["drive"]["mode"]=="MANUAL"
    assert not store.ingest(DISPLAY_DRIVE_TOPIC,env)
    store.now[0]+=2
    expired=store.snapshot()["drive"]
    assert expired["source"]["status"]=="stale"
    assert expired["mode"]=="unknown" and expired["active_vm_id"] is None
    fresh=report(seq=6);fresh["payload"]["active_vm_id"]=VM2
    assert store.ingest(DISPLAY_DRIVE_TOPIC,fresh)
    assert store.snapshot()["drive"]["active_vm_id"]==VM2


def test_retired_publisher_cannot_restore_old_active_vm(store):
    old=report()
    assert store.ingest(DISPLAY_DRIVE_TOPIC,old)
    replacement=deepcopy(old);replacement["agent_instance_id"]="new-publisher"
    replacement["payload"]["active_vm_id"]=VM2
    assert store.ingest(DISPLAY_DRIVE_TOPIC,replacement)
    old["seq"]=100
    assert not store.ingest(DISPLAY_DRIVE_TOPIC,old)
    assert store.snapshot()["drive"]["active_vm_id"]==VM2
