from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dom0_manager.drivers.base import Domain
from dom0_manager.models import VmState
from xenova_display.config import FeedSettings, load_bindings
from xenova_display.mqtt import MqttSource
from xenova_display.policy import DisplayPolicy
from xenova_display.state import StateStore
from xenova_mqtt.envelope import Identity, build

CANDIDATE = Path(__file__).parents[1]/"examples/integration-candidate-bindings.json"


@pytest.fixture
def candidate():
    bindings, scope = load_bindings(CANDIDATE, allow_candidate=True)
    store = StateStore(FeedSettings(bindings, "test-only-token", binding_scope=scope), clock=lambda: 100)
    store.mqtt_connected = True
    store.poll_success([Domain(b.vm_id, VmState.running, 1, 16, domid=i+1) for i,b in enumerate(bindings)])
    return store


def heartbeat(store, index, *, state="active", seq=1):
    b=store.settings.bindings[index]
    return build(Identity(vm_id=str(b.vm_id), app_id=b.app_id, boot_id="boot", agent_instance_id="publisher"),
                 "app.heartbeat", {"state":state,"uptime_s":1}, seq)


class Subscriber:
    def __init__(self): self.mids={}
    def subscribe(self, topic, **kwargs):
        mid=len(self.mids)+1
        self.mids[mid]=topic
        return 0,mid


def connection(store):
    source=MqttSource(store)
    client=Subscriber()
    source.on_connect(client,None,None,SimpleNamespace(is_failure=False),None)
    return source,client


def acknowledge(source,client,*,denied=()):
    for mid,topic in client.mids.items():
        source.on_subscribe(client,None,mid,[128 if topic in denied else 1],None)


def test_candidate_needs_explicit_opt_in_and_does_not_claim_registry_verification(monkeypatch):
    monkeypatch.setenv("XENOVA_DISPLAY_BINDINGS",str(CANDIDATE))
    monkeypatch.setenv("XENOVA_DISPLAY_TOKEN","test-only-token")
    monkeypatch.delenv("XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS",raising=False)
    with pytest.raises(ValueError,match="candidate bindings require"):
        FeedSettings.from_env()
    monkeypatch.setenv("XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS","1")
    settings=FeedSettings.from_env()
    assert settings.binding_scope=="candidate"
    assert [b.app_id for b in settings.bindings]==["autonomous","manual","interaction"]


@pytest.mark.parametrize("change",["version","boolean_version","duplicate_vm","duplicate_app","wrong_os","unknown_field"])
def test_profile_rejects_ambiguous_or_incompatible_mapping(tmp_path,change):
    raw=json.loads(CANDIDATE.read_text())
    if change=="version": raw["schema_version"]=2
    if change=="boolean_version": raw["schema_version"]=True
    if change=="duplicate_vm": raw["bindings"][1]["vm_id"]=raw["bindings"][0]["vm_id"]
    if change=="duplicate_app": raw["bindings"][1]["app_id"]="autonomous"
    if change=="wrong_os": raw["bindings"][2]["expected_os"]="zephyr"
    if change=="unknown_field": raw["production_verified"]=True
    path=tmp_path/"bindings.json";path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        bindings,scope=load_bindings(path,allow_candidate=True)
        FeedSettings(bindings,"test-only-token",binding_scope=scope)


def test_existing_list_configuration_remains_supported_but_is_labelled_legacy():
    bindings,scope=load_bindings(CANDIDATE.parent/"qemu-bindings.json")
    assert len(bindings)==3 and scope=="legacy"


@pytest.mark.parametrize("bad",["wrong_vm","wrong_app","wrong_topic","unregistered_vm","ambiguous_identity"])
def test_reports_cannot_cross_configured_vm_and_app(candidate,bad):
    env=heartbeat(candidate,1)
    topic="xenova/v1/apps/manual/events"
    if bad=="wrong_vm": env["vm_id"]=str(candidate.settings.bindings[0].vm_id)
    if bad=="wrong_app": env["app_id"]="autonomous"
    if bad=="wrong_topic": topic="xenova/v1/apps/autonomous/events"
    if bad=="unregistered_vm": env["vm_id"]="44444444-4444-4444-8444-444444444444"
    if bad=="ambiguous_identity": env["source_id"]="also-a-source"
    assert not candidate.ingest(topic,env)
    assert all(vm["app_state"]=="unknown" for vm in candidate.snapshot()["vms"])


def test_app_name_and_active_report_do_not_change_vm_role_or_driving_mode(candidate):
    env=heartbeat(candidate,1)
    assert candidate.ingest("xenova/v1/apps/manual/events",env)
    snapshot=candidate.snapshot()
    assert snapshot["vms"][1]["role"]=="backup_drive"
    assert snapshot["vms"][0]["app_state"]=="unknown"
    assert snapshot["vms"][1]["reported_app_state"]=="active"
    assert snapshot["drive"]["mode"]=="unknown" and snapshot["drive"]["active_vm_id"] is None
    assert snapshot["binding_scope"]=="candidate"
    assert all(v["os"]["observed"] is None for v in snapshot["vms"])


def test_each_topic_needs_suback_not_just_a_broker_connection(candidate):
    source,client=connection(candidate)
    topic="xenova/v1/apps/manual/events"
    assert candidate.mqtt_connected
    assert not candidate.ingest(topic,heartbeat(candidate,1))
    acknowledge(source,client,denied=(topic,))
    assert not candidate.ingest(topic,heartbeat(candidate,1))
    # A denied app channel does not poison a different VM or the Debian Agent channel.
    assert candidate.ingest("xenova/v1/apps/autonomous/events",heartbeat(candidate,0))
    snapshot=candidate.snapshot()
    assert snapshot["vms"][0]["app_state"]=="ready"
    assert snapshot["vms"][1]["sources"]["app"]["subscription_status"]=="denied"
    p=DisplayPolicy(candidate.settings,candidate.epoch).presentation(snapshot)
    assert p["decisions"][str(candidate.settings.bindings[1].vm_id)]["kind"]=="app_source_unavailable"
    assert p["state"]["vms"][1]["xen_state"]=="running"


def test_debian_heartbeat_is_agent_liveness_not_app_or_os_evidence(candidate):
    source,client=connection(candidate);acknowledge(source,client)
    b=candidate.settings.bindings[2]
    env=build(Identity(vm_id=str(b.vm_id),boot_id="linux-boot",agent_instance_id="agent"),
              "agent.heartbeat",{},1)
    assert candidate.ingest(f"xenova/v1/vms/{b.vm_id}/heartbeat",env)
    vm=candidate.snapshot()["vms"][2]
    assert vm["sources"]["agent"]["status"]=="fresh"
    assert vm["app_state"]=="unknown" and vm["os"]["observed"] is None
    assert not vm["os"]["current_run_verified"]
    assert candidate.snapshot()["vms"][0]["sources"]["agent"]["subscription_status"]=="unsupported"


def test_subscribe_call_error_is_not_app_failure(candidate):
    source=MqttSource(candidate)
    client=SimpleNamespace(subscribe=lambda *args,**kwargs:(4,None))
    source.on_connect(client,None,None,SimpleNamespace(is_failure=False),None)
    p=DisplayPolicy(candidate.settings,candidate.epoch).presentation(candidate.snapshot())
    assert all(status=="error" for status in candidate.subscriptions.values())
    assert all(v["xen_state"]=="running" and v["app_state"]=="unknown" for v in p["state"]["vms"])
    assert all(d["kind"]=="app_source_unavailable" for d in p["decisions"].values())


def test_reconnect_needs_new_suback_and_new_report_even_if_cached_report_is_recent(candidate):
    source,client=connection(candidate);acknowledge(source,client)
    topic="xenova/v1/apps/manual/events"
    assert candidate.ingest(topic,heartbeat(candidate,1))
    source.on_disconnect(client,None,None,None,None)
    assert candidate.snapshot()["vms"][1]["app_state"]=="unknown"
    replacement=Subscriber()
    source.on_connect(replacement,None,None,SimpleNamespace(is_failure=False),None)
    assert not candidate.ingest(topic,heartbeat(candidate,1,seq=2))
    acknowledge(source,replacement)
    assert candidate.snapshot()["vms"][1]["app_state"]=="unknown"
    assert not candidate.ingest(topic,heartbeat(candidate,1,seq=1))
    assert candidate.ingest(topic,heartbeat(candidate,1,seq=2))
    assert candidate.snapshot()["vms"][1]["app_state"]=="ready"


def test_early_suback_is_kept_until_mid_is_registered(candidate):
    source=MqttSource(candidate)
    class Early(Subscriber):
        def subscribe(self,topic,**kwargs):
            result,mid=super().subscribe(topic,**kwargs)
            source.on_subscribe(self,None,mid,[SimpleNamespace(value=1)],None)
            return result,mid
    client=Early()
    source.on_connect(client,None,None,SimpleNamespace(is_failure=False),None)
    assert all(status=="granted" for status in candidate.subscriptions.values())
    assert not source.pending and not source.early


def test_disconnect_during_subscribe_cannot_register_an_old_request(candidate):
    source=MqttSource(candidate)
    class Disconnect(Subscriber):
        def subscribe(self,topic,**kwargs):
            source.on_disconnect(self,None,None,None,None)
            return 0,41
    source.on_connect(Disconnect(),None,None,SimpleNamespace(is_failure=False),None)
    source.on_subscribe(None,None,41,[1],None)
    assert not source.pending and not candidate.mqtt_connected
    assert all(status=="unavailable" for status in candidate.subscriptions.values())


@pytest.mark.parametrize("state",["stopped","stopping","paused","crashed"])
@pytest.mark.parametrize("app_status",["active","error"])
def test_xen_state_has_priority_over_previous_app_report(candidate,state,app_status):
    assert candidate.ingest("xenova/v1/apps/manual/events",heartbeat(candidate,1,state=app_status))
    rows=[Domain(b.vm_id,VmState.running,1,16,domid=i+1) for i,b in enumerate(candidate.settings.bindings)]
    vm_id=str(rows[1].vm_id)
    if state=="stopped": rows.pop(1)  # Xen represents stopped as domain absence, not an enum.
    else: rows[1]=replace(rows[1],state=VmState(state))
    candidate.poll_success(rows)
    p=DisplayPolicy(candidate.settings,candidate.epoch).presentation(candidate.snapshot())
    assert p["decisions"][vm_id]["kind"]==state


@pytest.mark.parametrize("index,app_id", [(1,"manual"), (2,"interaction")])
def test_live_app_error_and_return_to_normal_choose_distinct_screens(candidate,index,app_id):
    policy=DisplayPolicy(candidate.settings,candidate.epoch)
    topic=f"xenova/v1/apps/{app_id}/events"
    assert candidate.ingest(topic,heartbeat(candidate,index,state="error"))
    assert policy.presentation(candidate.snapshot())["decisions"][str(candidate.settings.bindings[index].vm_id)]["kind"]=="app_error"
    assert candidate.ingest(topic,heartbeat(candidate,index,state="standby",seq=2))
    assert policy.presentation(candidate.snapshot())["decisions"][str(candidate.settings.bindings[index].vm_id)]["kind"]=="status"
