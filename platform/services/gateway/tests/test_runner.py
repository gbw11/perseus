"""판정 루프 시험: 가짜 MQTT 클라이언트와 주입 시계로 큐 → 상태 기계 → 출력 경로를 본다."""

from __future__ import annotations

from typing import Any

from xenova_mqtt import Identity, topics
from xenova_mqtt import envelope as env
from xenova_mqtt.client import Message

from xenova_gateway.frame import DriveFrame, StopFrame
from xenova_gateway.machine import Mode
from xenova_gateway.runner import CONSOLE_RESUME, GatewayRunner
from xenova_gateway.sink import MockSink

AUTO_VM = "11111111-1111-4111-8111-111111111111"
MANUAL_VM = "22222222-2222-4222-8222-222222222222"


class FakeClient:
    def __init__(self, retained_generation: int | None = None) -> None:
        self.handlers: dict[str, Any] = {}
        self.qos: dict[str, int] = {}
        self.published: list[tuple[str, str, dict]] = []
        self.retained = retained_generation

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.handlers[topic_filter] = handler
        self.qos[topic_filter] = qos

    def latest(self, topic):
        if topic == topics.vehicle_control() and self.retained is not None:
            return Message(topic, {"type": "vehicle.control", "payload": {"generation": self.retained}})
        return None

    def publish(self, topic, message_type, payload, **_):
        self.published.append((topic, message_type, payload))

    def deliver(self, topic: str, message_type: str, payload: dict, vm_id: str | None = AUTO_VM, seq: int = 1,
                source_id: str | None = None):
        app = topics.parse(topic).app_id
        identity = Identity(source_id=source_id, app_id=app) if source_id else Identity(vm_id=vm_id, app_id=app)
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


def test_resume_from_interaction_is_fed():
    runner, client, clock, _ = make()
    runner.begin()
    client.deliver(topics.app_events("interaction"), "drive.resume.requested", {"requested_by": "operator"},
                   vm_id="33333333-3333-4333-8333-333333333333")
    runner.run_once(0)
    # SAFE_STOP 이라 무시된다 — 모드 이벤트는 시작·SAFE_STOP 두 개뿐
    assert [p["to"] for _, m, p in client.published if m == "drive.mode.changed"] == ["AUTO", "SAFE_STOP"]


def test_shutdown_sends_stop_frame():
    runner, client, clock, sink = make()
    runner.begin()
    runner.shutdown()
    assert isinstance(sink.frames[-1][1], StopFrame) and sink.frames[-1][1].reason == 2


def drive(runner, client, clock, seconds: float, apps=("autonomous", "manual"), heartbeat: bool = False):
    """seconds 동안 20 ms 마다 apps 가 현재 세대로 명령을 내고(heartbeat 면 자율 heartbeat 도) 루프를 한 번 돈다."""
    for _ in range(round(seconds / 0.02)):
        clock.t = round(clock.t + 0.02, 9)
        generation = runner.machine.generation
        for app in apps:
            vm = AUTO_VM if app == "autonomous" else MANUAL_VM
            client.deliver(topics.app_events(app), "drive.command",
                           {"generation": generation, "v_mps": 0.0, "steer_rad": 0.0}, vm_id=vm)
        if heartbeat:
            client.deliver(topics.app_events("autonomous"), "app.heartbeat", {"state": "standby", "uptime_s": 1.0})
        runner.run_once(0)


def auto_ready_runner():
    runner, client, clock, sink = make()
    for app, vm in (("autonomous", AUTO_VM), ("manual", MANUAL_VM)):
        client.deliver(topics.app_events(app), "drive.command", {"generation": 0, "v_mps": 0.0, "steer_rad": 0.0}, vm_id=vm)
    runner.begin()
    drive(runner, client, clock, 0.2)
    drive(runner, client, clock, 0.2, apps=("manual",))
    assert runner.machine.mode is Mode.MANUAL
    drive(runner, client, clock, 3.1, heartbeat=True)
    assert runner.machine.mode is Mode.AUTO_READY
    return runner, client, clock, sink


def test_subscribes_console_resume_qos1():
    runner, client, clock, _ = make()
    assert client.handlers[CONSOLE_RESUME] is not None and client.qos[CONSOLE_RESUME] == 1
    assert CONSOLE_RESUME == "xenova/v1/console/resume"


def test_resume_from_console_counts_down_then_auto():
    runner, client, clock, _ = auto_ready_runner()
    gen = runner.machine.generation
    client.published.clear()
    # 백엔드(운영자 콘솔)는 VM 이 아니라 source_id dom0-server 로 발행한다 (계약 2장)
    client.deliver(CONSOLE_RESUME, "drive.resume.requested", {"requested_by": "admin"}, source_id="dom0-server")
    drive(runner, client, clock, 0.02, heartbeat=True)
    assert [(t, p) for t, m, p in client.published if m == "drive.resume.accepted"] == [
        (topics.app_events("vehicle"), {"switch_in_ms": 3000, "generation": gen, "requested_by": "admin"})]
    assert runner.machine.mode is Mode.AUTO_READY
    drive(runner, client, clock, 3.0, heartbeat=True)
    assert runner.machine.mode is Mode.AUTO
    changed = [(t, p) for t, m, p in client.published if m == "drive.mode.changed"]
    assert changed == [(topics.app_events("vehicle"),
                        {"from": "AUTO_READY", "to": "AUTO", "generation": gen + 1, "reason": "resume_requested"})]


def test_resume_from_interaction_cancelled_when_autonomous_lost():
    runner, client, clock, _ = auto_ready_runner()
    gen = runner.machine.generation
    client.published.clear()
    client.deliver(topics.app_events("interaction"), "drive.resume.requested", {"requested_by": "operator"},
                   vm_id="33333333-3333-4333-8333-333333333333")
    drive(runner, client, clock, 1.0, heartbeat=True)
    drive(runner, client, clock, 0.2, apps=("manual",))
    assert runner.machine.mode is Mode.MANUAL
    vehicle = [(m, p) for t, m, p in client.published if t == topics.app_events("vehicle") and m != "drive.fault.detected"]
    assert vehicle == [
        ("drive.resume.accepted", {"switch_in_ms": 3000, "generation": gen, "requested_by": "operator"}),
        ("drive.resume.cancelled", {"reason": "autonomous_lost", "generation": gen}),
        ("drive.mode.changed", {"from": "AUTO_READY", "to": "MANUAL", "generation": gen + 1, "reason": "autonomous_lost"}),
    ]


def test_resume_on_other_topic_is_not_fed():
    runner, client, clock, _ = auto_ready_runner()
    client.published.clear()
    # 주행 앱 토픽에 실린 재개 요청은 받지 않는다 (요청 경로는 인터랙션 VM·콘솔 둘)
    client.deliver(topics.app_events("manual"), "drive.resume.requested", {"requested_by": "x"}, vm_id=MANUAL_VM)
    drive(runner, client, clock, 0.02, heartbeat=True)
    assert not [m for _, m, _ in client.published if m == "drive.resume.accepted"]


def test_legacy_runner_cannot_fall_back_to_unbound_paired_control():
    import pytest
    client, clock, sink = FakeClient(), Clock(), MockSink()
    with pytest.raises(ValueError, match="VerifiedGatewayRunner"):
        GatewayRunner(client, sink, clock=clock, vm_ids=(AUTO_VM, MANUAL_VM))

def test_cancel_from_console_keeps_auto_ready():
    """운영자 취소 (계약 v1.7, S15P31E110-280). legacy 는 causation 검사 없이 같은 토픽에서 받는다."""
    runner, client, clock, _ = auto_ready_runner()
    gen = runner.machine.generation
    client.published.clear()
    client.deliver(CONSOLE_RESUME, "drive.resume.requested", {"requested_by": "admin"}, source_id="dom0-server")
    drive(runner, client, clock, 1.0, heartbeat=True)
    client.deliver(CONSOLE_RESUME, "drive.resume.cancel_requested", {"requested_by": "admin"}, source_id="dom0-server")
    drive(runner, client, clock, 3.0, heartbeat=True)
    assert runner.machine.mode is Mode.AUTO_READY and runner.machine.generation == gen
    vehicle = [(m, p) for t, m, p in client.published if t == topics.app_events("vehicle")]
    assert vehicle == [
        ("drive.resume.accepted", {"switch_in_ms": 3000, "generation": gen, "requested_by": "admin"}),
        ("drive.resume.cancelled", {"reason": "operator_cancelled", "generation": gen, "requested_by": "admin"}),
    ]
