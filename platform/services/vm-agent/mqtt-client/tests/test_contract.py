"""브로커 없이 도는 단위 테스트: 토픽, QoS 정책, 봉투, seq 중복 처리."""

from __future__ import annotations

import re

import pytest

from perseus_mqtt import EnvelopeError, Identity, SeqTracker, policy, topics, validate
from perseus_mqtt import envelope as env

VM_ID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"

# 백엔드 MqttAgentSubscriber 가 구독하는 형식
BACKEND_TOPIC = re.compile(r"^perseus/v1/vms/([^/]+)/(heartbeat|inventory|metrics)$")


def test_agent_topics_match_backend_subscription():
    for build in (topics.heartbeat, topics.inventory, topics.metrics):
        assert BACKEND_TOPIC.match(build(VM_ID)).group(1) == VM_ID


def test_contract_topics():
    assert topics.command_result(VM_ID) == f"perseus/v1/vms/{VM_ID}/command-result"
    assert topics.app_events("autonomous") == "perseus/v1/apps/autonomous/events"
    assert topics.manual_input() == "perseus/v1/apps/manual/input"
    assert topics.vehicle_control() == "perseus/v1/vehicle/control"
    assert topics.vehicle_status() == "perseus/v1/vehicle/status"
    assert topics.probe_request("b") == "perseus/v1/probes/b/request"
    assert topics.probe_reply("a") == "perseus/v1/probes/a/reply"


@pytest.mark.parametrize("bad", ["", "a/b", "+", "#", "a b"])
def test_topic_segment_rejects_wildcards(bad):
    with pytest.raises(ValueError):
        topics.heartbeat(bad)


def test_parse():
    assert topics.parse(topics.metrics(VM_ID)).vm_id == VM_ID
    assert topics.parse(topics.app_events("manual")).app_id == "manual"
    assert topics.parse(topics.manual_input()).app_id is None
    assert topics.parse(topics.vehicle_control()) == topics.TopicInfo()


@pytest.mark.parametrize(
    "subscription, topic, expected",
    [
        ("perseus/v1/vms/+/heartbeat", f"perseus/v1/vms/{VM_ID}/heartbeat", True),
        ("perseus/v1/vms/+/heartbeat", f"perseus/v1/vms/{VM_ID}/metrics", False),
        ("perseus/#", "perseus/v1/vehicle/status", True),
        ("perseus/v1/vehicle/control", "perseus/v1/vehicle/control", True),
        ("perseus/v1/vehicle/+", "perseus/v1/vehicle", False),
    ],
)
def test_wildcard_match(subscription, topic, expected):
    assert topics.matches(subscription, topic) is expected


def test_policy_follows_contract_tables():
    # 3장: heartbeat 1, inventory 1 retained, metrics 0 (백엔드 QOS = {1, 1, 0})
    assert [policy.for_type(t).qos for t in ("agent.heartbeat", "agent.inventory", "agent.metrics")] == [1, 1, 0]
    assert policy.for_type("agent.inventory").retain
    # 5장: 주행 토픽 QoS 0, control 만 QoS 1 retained, 1 KB
    assert policy.for_type("drive.command") == policy.Policy(qos=0, driving=True)
    assert policy.for_type("vehicle.control") == policy.Policy(qos=1, retain=True, driving=True)
    assert policy.for_type("drive.command").max_bytes == 1024
    # 같은 토픽의 다른 type: apps/autonomous/events 의 perception.event 는 QoS 1
    assert policy.for_type("perception.event").qos == 1
    assert policy.for_type("agent.metrics").max_bytes == 64 * 1024
    assert policy.for_type("unknown.type") == policy.DEFAULT


def _envelope(**overrides):
    identity = Identity(vm_id=VM_ID, boot_id="boot-1", agent_instance_id="agent-1")
    data = env.build(identity, "agent.heartbeat", {}, 1)
    data.update(overrides)
    return data


def test_build_has_backend_envelope_fields():
    data = _envelope()
    assert set(data) == {
        "schema_version", "message_id", "vm_id", "boot_id", "agent_instance_id", "type", "seq", "sent_at", "payload",
    }
    assert data["schema_version"] == "1.0"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", data["sent_at"])
    validate(topics.heartbeat(VM_ID), data)


def test_build_for_non_vm_publisher_uses_source_id():
    identity = Identity(source_id="dom0-gateway", app_id="vehicle")
    data = env.build(identity, "drive.mode.changed", {"from": "AUTO", "to": "MANUAL"}, 3, correlation_id="c-1")
    assert "vm_id" not in data
    assert data["source_id"] == "dom0-gateway"
    assert data["app_id"] == "vehicle"
    assert data["correlation_id"] == "c-1"
    validate(topics.app_events("vehicle"), data)


def test_identity_requires_exactly_one_publisher():
    with pytest.raises(ValueError):
        Identity()
    with pytest.raises(ValueError):
        Identity(vm_id="a", source_id="b")


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"schema_version": "2.0"}, "SCHEMA_VERSION_UNSUPPORTED"),
        ({"vm_id": "other"}, "VM_ID_MISMATCH"),
        ({"seq": 0}, "PAYLOAD_INVALID"),
        ({"seq": True}, "PAYLOAD_INVALID"),
        ({"boot_id": ""}, "PAYLOAD_INVALID"),
        ({"payload": []}, "PAYLOAD_INVALID"),
    ],
)
def test_validate_rejects(overrides, code):
    with pytest.raises(EnvelopeError) as raised:
        validate(topics.heartbeat(VM_ID), _envelope(**overrides))
    assert raised.value.code == code


def test_validate_app_id_must_match_topic():
    data = env.build(Identity(vm_id=VM_ID, app_id="manual"), "drive.command", {"generation": 1}, 1)
    with pytest.raises(EnvelopeError):
        validate(topics.app_events("autonomous"), data)


def test_command_envelope_has_no_seq():
    data = {
        "schema_version": "1.0", "message_id": "m", "vm_id": VM_ID, "type": "agent.command",
        "sent_at": "2026-10-19T00:00:00.000Z", "payload": {"command_id": "c"},
    }
    validate(topics.command(VM_ID), data)
    assert SeqTracker().accept(topics.command(VM_ID), data)


def test_seq_tracker_drops_duplicates_and_resets_on_restart():
    tracker = SeqTracker()
    topic = topics.heartbeat(VM_ID)
    assert tracker.accept(topic, _envelope(seq=1))
    assert tracker.accept(topic, _envelope(seq=2))
    assert not tracker.accept(topic, _envelope(seq=2))  # 중복
    assert not tracker.accept(topic, _envelope(seq=1))  # 역전
    assert tracker.accept(topic, _envelope(seq=1, agent_instance_id="agent-2"))  # 프로세스 재시작
    assert tracker.accept(topic, _envelope(seq=1, boot_id="boot-2"))  # VM 재부팅
    # 토픽이 다르면 따로 센다
    assert tracker.accept(topics.metrics(VM_ID), _envelope(seq=1, boot_id="boot-2"))
