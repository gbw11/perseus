"""실제 Mosquitto 로 도는 통합 테스트. 브로커가 없으면 건너뛴다.

    docker run --rm -d --name xenova-mq-test -p 18830:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
    XENOVA_MQTT_TEST_BROKER=127.0.0.1:18830 XENOVA_MQTT_TEST_CONTAINER=xenova-mq-test python -m pytest
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import time
import uuid

import pytest

from xenova_mqtt import Identity, XenovaClient, topics
from xenova_mqtt import envelope as env

BROKER = os.environ.get("XENOVA_MQTT_TEST_BROKER", "")
CONTAINER = os.environ.get("XENOVA_MQTT_TEST_CONTAINER", "")

pytestmark = pytest.mark.skipif(not BROKER, reason="XENOVA_MQTT_TEST_BROKER 미설정")


def _client(**identity) -> XenovaClient:
    host, port = BROKER.rsplit(":", 1)
    client = XenovaClient(Identity(**identity), host, int(port))
    assert client.start(wait_s=5), "브로커 연결 실패"
    return client


@pytest.fixture
def vm_id():
    # 테스트마다 다른 토픽을 써서 retained·이전 메시지가 섞이지 않게 한다
    return str(uuid.uuid4())


@pytest.fixture
def clients():
    created: list[XenovaClient] = []

    def make(**identity) -> XenovaClient:
        client = _client(**identity)
        created.append(client)
        return client

    yield make
    for client in created:
        client.stop()


def _subscribe(client: XenovaClient, topic: str) -> queue.Queue:
    received: queue.Queue = queue.Queue()
    client.subscribe(topic, received.put)
    time.sleep(0.3)  # SUBACK 대기
    return received


def test_publish_subscribe_roundtrip(clients, vm_id):
    receiver = clients(source_id="test-receiver")
    received = _subscribe(receiver, "xenova/v1/vms/+/heartbeat")
    sender = clients(vm_id=vm_id)

    sent = sender.publish(topics.heartbeat(vm_id), "agent.heartbeat", {})

    message = received.get(timeout=5)
    assert message.envelope == sent
    assert message.envelope["seq"] == 1
    assert receiver.latest(topics.heartbeat(vm_id)) == message
    assert message.age_ms() < 5000


def test_duplicate_and_invalid_messages_are_dropped(clients, vm_id):
    receiver = clients(source_id="test-receiver")
    topic = topics.heartbeat(vm_id)
    received = _subscribe(receiver, topic)
    sender = clients(vm_id=vm_id)

    raw = json.dumps(env.build(sender.identity, "agent.heartbeat", {}, 7))
    sender._paho.publish(topic, raw, qos=1)
    sender._paho.publish(topic, raw, qos=1)  # 같은 seq 재전송
    mismatch = env.build(Identity(vm_id="someone-else"), "agent.heartbeat", {}, 1)
    sender._paho.publish(topic, json.dumps(mismatch), qos=1)  # VM_ID_MISMATCH
    sender._paho.publish(topic, b"not json", qos=1)
    sender.publish(topic, "agent.heartbeat", {})  # 라이브러리 seq 1 < 7 → 역전으로 버려짐

    assert received.get(timeout=5).envelope["seq"] == 7
    with pytest.raises(queue.Empty):
        received.get(timeout=1)


def test_inventory_is_retained_for_late_subscriber(clients, vm_id):
    sender = clients(vm_id=vm_id)
    payload = {"os": {"id": "debian"}, "kernel_release": "6.12", "hostname": "vm"}
    sender.publish(topics.inventory(vm_id), "agent.inventory", payload)
    time.sleep(0.5)

    late = clients(source_id="test-late")
    received = _subscribe(late, topics.inventory(vm_id))
    assert received.get(timeout=5).payload == payload

    sender.clear_retained(topics.inventory(vm_id))


@pytest.mark.skipif(not CONTAINER, reason="XENOVA_MQTT_TEST_CONTAINER 미설정 (브로커 재시작 테스트)")
def test_reconnects_and_resubscribes_after_broker_restart(clients, vm_id):
    receiver = clients(source_id="test-receiver")
    received = _subscribe(receiver, topics.heartbeat(vm_id))
    sender = clients(vm_id=vm_id)

    subprocess.run(["docker", "restart", CONTAINER], check=True, capture_output=True)
    # 브로커가 소켓을 닫으므로 끊김을 먼저 감지해야 한다. 그래야 아래 대기가 재연결을 확인한다.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (receiver.connected or sender.connected):
        time.sleep(0.1)
    assert not receiver.connected and not sender.connected, "끊김을 감지하지 못함"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (receiver.connected and sender.connected):
        time.sleep(0.2)
    assert receiver.connected and sender.connected, "30초 안에 재연결되지 않음"
    time.sleep(0.5)  # 재구독 SUBACK 대기

    sender.publish(topics.heartbeat(vm_id), "agent.heartbeat", {})
    assert received.get(timeout=5).envelope["vm_id"] == vm_id
