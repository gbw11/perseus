"""판정 루프 시험: 가짜 MQTT 클라이언트와 주입 시계로 큐 → 상태 기계 → 출력 경로를 본다."""

from __future__ import annotations

from typing import Any

from xenova_mqtt import Identity, topics
from xenova_mqtt import envelope as env
from xenova_mqtt.client import Message

from xenova_gateway.frame import DriveFrame, StopFrame
from xenova_gateway.machine import Mode
from xenova_gateway.runner import GatewayRunner
from xenova_gateway.sink import MockSink

AUTO_VM = "11111111-1111-4111-8111-111111111111"


class FakeClient:
    def __init__(self, retained_generation: int | None = None) -> None:
        self.handlers: dict[str, Any] = {}
        self.published: list[tuple[str, str, dict]] = []
        self.retained = retained_generation

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.handlers[topic_filter] = handler

    def latest(self, topic):
        if topic == topics.vehicle_control() and self.retained is not None:
            return Message(topic, {"type": "vehicle.control", "payload": {"generation": self.retained}})
        return None

    def publish(self, topic, message_type, payload, **_):
        self.published.append((topic, message_type, payload))

    def deliver(self, topic: str, message_type: str, payload: dict, vm_id: str | None = AUTO_VM, seq: int = 1):
        app = topics.parse(topic).app_id
        identity = Identity(vm_id=vm_id, app_id=app)
        self.handlers[topic](Message(topic, env.build(identity, message_type, payload, seq)))


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make(retained: int | None = None, start_mode: Mode = Mode.AUTO):
    client, clock, sink = FakeClient(retained), Clock(), MockSink()
    runner = GatewayRunner(client, sink, clock=clock, start_mode=start_mode)
    runner.subscribe()
    return runner, client, clock, sink


def test_generation_continues_from_retained_control():
    runner, client, clock, _ = make(retained=41)
    runner.begin()
    topic, message_type, payload = client.published[0]
    assert (topic, message_type) == (topics.vehicle_control(), "vehicle.control")
    assert payload["generation"] == 42


def test_messages_before_begin_count_for_freshness_and_forward_after():
    runner, client, clock, sink = make()
    client.deliver(topics.app_events("autonomous"), "drive.command", {"generation": 0, "v_mps": 0.0, "steer_rad": 0.0})
    runner.begin()
    clock.t += 0.02
    runner.run_once(0)
    assert runner.machine.mode is Mode.AUTO
    client.deliver(topics.app_events("autonomous"), "drive.command", {"generation": 1, "v_mps": 0.4, "steer_rad": 0.1}, seq=2)
    clock.t += 0.02
    runner.run_once(0)
    frames = [f for _, f, _ in sink.frames]
    assert frames == [DriveFrame(1, 1, 0.4, 0.1)]


def test_callback_timestamp_is_receive_time_not_loop_time():
    runner, client, clock, _ = make()
    runner.begin()
    client.deliver(topics.app_events("autonomous"), "drive.command", {"generation": 1, "v_mps": 0.0, "steer_rad": 0.0})
    clock.t += 0.5     # 루프가 늦게 돌아도 수신 시각은 enqueue 시각
    runner.run_once(0)
    assert runner.machine.apps["autonomous"].last_command_t == 100.0
    assert runner.machine.mode is Mode.SAFE_STOP


def test_events_and_fault_published_with_detected_at():
    runner, client, clock, sink = make()
    client.deliver(topics.app_events("autonomous"), "drive.command", {"generation": 0, "v_mps": 0.0, "steer_rad": 0.0})
    runner.begin()
    clock.t += 0.2
    runner.run_once(0)
    types = [(t, m) for t, m, _ in client.published]
    assert (topics.app_events("vehicle"), "drive.fault.detected") in types
    fault = next(p for _, m, p in client.published if m == "drive.fault.detected")
    assert fault["app_id"] == "autonomous" and fault["vm_id"] == AUTO_VM and fault["detected_at"].endswith("Z")
    assert isinstance(sink.frames[-1][1], StopFrame)


def test_resume_from_message_board_is_fed():
    runner, client, clock, _ = make()
    runner.begin()
    client.deliver(topics.app_events("message-board"), "drive.resume.requested", {"requested_by": "operator"},
                   vm_id="33333333-3333-4333-8333-333333333333")
    runner.run_once(0)
    # SAFE_STOP 이라 무시된다 — 모드 이벤트는 시작·SAFE_STOP 두 개뿐
    assert [p["to"] for _, m, p in client.published if m == "drive.mode.changed"] == ["AUTO", "SAFE_STOP"]


def test_shutdown_sends_stop_frame():
    runner, client, clock, sink = make()
    runner.begin()
    runner.shutdown()
    assert isinstance(sink.frames[-1][1], StopFrame) and sink.frames[-1][1].reason == 2
