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
    assert run(settings, client_factory=lambda s: client, stop=stop, install_signals=False) == 0
    assert client.started_with == [0.05]  # 첫 heartbeat 전에 한 주기만큼 연결을 기다린다
    assert client.published == [(f"perseus/v1/vms/{VM_ID}/heartbeat", "agent.heartbeat", {})]
    assert client.stopped is True


def test_run_exits_nonzero_when_publish_keeps_failing():
    """고정된 발행 오류는 프로세스 실패(종료 코드 1)로 드러낸다. 클라이언트는 그래도 닫는다."""
    stop = threading.Event()
    client = FakeClient(stop)

    def broken_publish(topic, message_type, payload, **kwargs):
        raise ValueError("bad envelope")

    client.publish = broken_publish
    settings = AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.01)
    assert run(settings, client_factory=lambda s: client, stop=stop, install_signals=False) == 1
    assert client.stopped is True
