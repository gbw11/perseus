"""진입점: 클라이언트를 띄우고 heartbeat 를 보내다가 정지 신호에 클라이언트를 닫는다."""

from __future__ import annotations

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
