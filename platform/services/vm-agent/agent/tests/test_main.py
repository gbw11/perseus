"""진입점: 클라이언트를 띄우고 heartbeat 를 보내다가 정지 신호에 클라이언트를 닫는다."""

from __future__ import annotations

import json
import threading

from xenova_agent.__main__ import run
from xenova_agent.config import AgentSettings

VM_ID = "11111111-1111-4111-8111-111111111111"


class FakeClient:
    def __init__(self, stop: threading.Event) -> None:
        self.stop_event = stop
        self.started_with: list[float | None] = []
        self.stopped = False
        self.published: list[tuple[str, str, dict]] = []
        self.raw: list[tuple[str, bytes]] = []  # v2 telemetry (봉투 없는 원문)

    def publish_raw(self, topic, data, *, qos, retain, wait_s=None):
        self.raw.append((topic, bytes(data)))

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.subscriptions = getattr(self, "subscriptions", []) + [(topic_filter, handler)]

    @property
    def connected(self) -> bool:
        return bool(self.started_with)

    def start(self, wait_s=None):
        self.started_with.append(wait_s)
        return True

    def stop(self):
        self.stopped = True

    def publish(self, topic, message_type, payload, **kwargs):
        self.published.append((topic, message_type, dict(payload)))
        self.stop_event.set()  # 첫 heartbeat 뒤 바로 정지 요청
        return {}


def test_run_sends_heartbeat_and_closes_client_on_stop():
    stop = threading.Event()
    client = FakeClient(stop)
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05)
    assert run(settings, client_factory=lambda s: client, reporter_factory=None, stop=stop, install_signals=False) == 0
    assert client.started_with == [0.05]  # 첫 heartbeat 전에 한 주기만큼 연결을 기다린다
    assert client.published == [(f"xenova/v1/vms/{VM_ID}/heartbeat", "agent.heartbeat", {})]
    assert client.stopped is True


def test_run_exits_nonzero_when_publish_keeps_failing():
    """고정된 발행 오류는 프로세스 실패(종료 코드 1)로 드러낸다. 클라이언트는 그래도 닫는다."""
    stop = threading.Event()
    client = FakeClient(stop)

    def broken_publish(topic, message_type, payload, **kwargs):
        raise ValueError("bad envelope")

    client.publish = broken_publish
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.01)
    assert run(settings, client_factory=lambda s: client, reporter_factory=None, stop=stop, install_signals=False) == 1
    assert client.stopped is True


def test_slow_collection_does_not_delay_heartbeats():
    """reporter 는 별도 스레드다. 수집이 멈춰 있어도 heartbeat 는 주기대로 나가고, 정지 때 reporter 도 멈춘다."""
    import time

    from xenova_agent.processes import ProcessSample
    from xenova_agent.reporter import Reporter

    stop = threading.Event()
    release = threading.Event()
    heartbeats = []

    class Client(FakeClient):
        def publish(self, topic, message_type, payload, **kwargs):
            if message_type == "agent.heartbeat":
                heartbeats.append(time.monotonic())
                if len(heartbeats) == 3:
                    stop.set()
            return {}

    def stuck_inventory():
        release.wait(5)  # 느린 /proc·네트워크 조회 흉내
        return {}

    def factory(client, settings):
        return Reporter(client, settings.vm_id, collect_inventory=stuck_inventory, collect_metrics=list, collect_processes=lambda: ProcessSample("disabled"), interval_s=0.05)

    client = Client(stop)
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05)
    started = time.monotonic()
    assert run(settings, client_factory=lambda s: client, reporter_factory=factory, stop=stop, install_signals=False) == 0
    release.set()
    assert len(heartbeats) == 3 and heartbeats[-1] - started < 1.0


def test_telemetry_thread_publishes_v2_streams_and_a_final_stopping_heartbeat():
    """세 번째 스레드는 Reporter 와 무관하게 돌고, 정지 때 클라이언트를 닫기 전에 stopping heartbeat 를 한 번 보낸다."""
    stop = threading.Event()
    client = FakeClient(stop)  # 첫 v1 heartbeat 뒤 정지
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05)
    assert run(settings, client_factory=lambda s: client, reporter_factory=None, stop=stop, install_signals=False) == 0
    streams = [topic.rsplit("/", 1)[1] for topic, _ in client.raw]
    assert streams[:3] == ["inventory", "heartbeat", "metrics"] and all(t.startswith(f"xenova/v2/vms/{VM_ID}/telemetry/") for t, _ in client.raw)
    first = json.loads(client.raw[1][1])
    assert first["payload"]["publisher_state"] == "degraded"  # Reporter 가 없으면 수집 루프가 없는 것이다
    assert first["payload"]["app_health_asserted"] is False
    last = json.loads(client.raw[-1][1])
    assert last["type"] == "telemetry.heartbeat" and last["payload"]["publisher_state"] == "stopping"
    assert client.stopped is True


def test_telemetry_can_be_disabled_by_settings():
    stop = threading.Event()
    client = FakeClient(stop)
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05, telemetry_enabled=False)
    assert run(settings, client_factory=lambda s: client, reporter_factory=None, stop=stop, install_signals=False) == 0
    assert client.raw == [] and len(client.published) == 1


def test_commands_subscribe_before_connect_and_inventory_lists_them_only_when_enabled():
    from xenova_agent.__main__ import inventory_commands

    stop = threading.Event()
    client = FakeClient(stop)
    order = []
    client.start = lambda wait_s=None: (order.append("start"), client.started_with.append(wait_s))[1] or True
    original = client.subscribe
    client.subscribe = lambda *a, **k: (order.append("subscribe"), original(*a, **k))
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05, telemetry_enabled=False, state_dir=None)
    assert run(settings, client_factory=lambda s: client, reporter_factory=None, stop=stop, install_signals=False) == 0
    assert order == ["subscribe", "start"]
    assert [t for t, _ in client.subscriptions] == [f"xenova/v1/vms/{VM_ID}/command"]
    assert inventory_commands(settings) == ()
    assert inventory_commands(AgentSettings(vm_id=VM_ID, demo_commands=True)) == ("stress", "agent_exit")


def test_agent_exit_command_ends_run_with_exit_code_3():
    """agent_exit 가 PUBACK 확인 뒤 정지를 요청하면 run 은 유닛이 다시 띄우지 않는 종료 코드 3 으로 끝난다."""
    from datetime import datetime, timedelta, timezone

    from xenova_mqtt import Message

    from xenova_agent.commands import EXIT_CODE

    stop = threading.Event()

    class Client(FakeClient):
        identity = type("Identity", (), {"boot_id": "boot-1"})()

        def publish(self, topic, message_type, payload, **kwargs):
            self.published.append((topic, message_type, dict(payload)))
            if message_type == "agent.heartbeat" and len(self.published) == 1:
                now = datetime.now(timezone.utc)
                iso = lambda t: t.isoformat(timespec="milliseconds").replace("+00:00", "Z")  # noqa: E731
                handler = self.subscriptions[0][1]
                handler(Message(f"xenova/v1/vms/{VM_ID}/command", {
                    "schema_version": "1.0", "message_id": "m", "vm_id": VM_ID, "type": "agent.command", "sent_at": iso(now),
                    "payload": {"command_id": "x-1", "operation_id": None, "expected_boot_id": "boot-1",
                                "expires_at": iso(now + timedelta(seconds=60)), "action": "agent_exit", "parameters": {}},
                }))
            return {}

        def publish_tracked(self, topic, message_type, payload):
            self.published.append((topic, message_type, dict(payload)))
            info = type("Info", (), {"is_published": lambda self: True})()
            return type("Ticket", (), {"info": info})()

    client = Client(stop)
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05, telemetry_enabled=False, demo_commands=True, state_dir=None)
    assert run(settings, client_factory=lambda s: client, reporter_factory=None, stop=stop, install_signals=False) == EXIT_CODE
    results = [p for _, kind, p in client.published if kind == "agent.command_result"]
    assert [r["status"] for r in results] == ["ACCEPTED"]
    assert client.stopped is True
