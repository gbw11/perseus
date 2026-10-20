"""heartbeat 루프: 시작 직후 1회, 이후 5초 주기, 지연 보정, 끊김 건너뜀, 발행 실패 무시, 정지."""

from __future__ import annotations

import pytest

from xenova_agent.heartbeat import HeartbeatFailed, HeartbeatSender

VM_ID = "11111111-1111-4111-8111-111111111111"
TOPIC = f"xenova/v1/vms/{VM_ID}/heartbeat"


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class FakeClient:
    def __init__(self, clock: FakeClock, *, publish_cost_s: float = 0.0) -> None:
        self.clock = clock
        self.publish_cost_s = publish_cost_s
        self.connected = True
        self.calls: list[tuple[float, str, str, dict]] = []
        self.attempts = 0
        self.fail_at: set[int] = set()  # n번째 publish 시도에서 예외

    def publish(self, topic, message_type, payload, **kwargs):
        self.clock.now += self.publish_cost_s
        self.attempts += 1
        if self.attempts in self.fail_at:
            raise ValueError("injected publish failure")
        self.calls.append((self.clock.now, topic, message_type, dict(payload)))
        return {"seq": len(self.calls)}


class FakeStop:
    """wait(timeout) 가 가짜 시계를 timeout 만큼 흘리고, stop_at 을 지나면 True 를 돌려준다."""

    def __init__(self, clock: FakeClock, stop_at: float) -> None:
        self.clock = clock
        self.stop_at = stop_at
        self.waits: list[float] = []

    def wait(self, timeout=None):
        self.waits.append(round(timeout, 6))
        self.clock.now += timeout
        return self.clock.now >= self.stop_at


def test_first_heartbeat_is_immediate_then_every_interval():
    clock = FakeClock(100.0)
    client = FakeClient(clock)
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock)
    sent = sender.run(FakeStop(clock, stop_at=100.0 + 20.5))
    assert sent == 5
    assert [c[0] for c in client.calls] == [100.0, 105.0, 110.0, 115.0, 120.0]
    assert all(c[1:] == (TOPIC, "agent.heartbeat", {}) for c in client.calls)


def test_schedule_does_not_drift_when_publish_is_slow():
    clock = FakeClock(100.0)
    client = FakeClient(clock, publish_cost_s=0.7)
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock)
    stop = FakeStop(clock, stop_at=100.0 + 15.5)
    sender.run(stop)
    # 발행에 0.7초가 걸려도 다음 발행은 start+n*5 에 맞춘다 (대기는 4.3초)
    assert [c[0] for c in client.calls] == pytest.approx([100.7, 105.7, 110.7, 115.7])
    assert stop.waits == pytest.approx([4.3, 4.3, 4.3, 4.3])


def test_ticks_are_skipped_while_disconnected_and_resume_after_reconnect():
    clock = FakeClock(100.0)
    client = FakeClient(clock)
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock)

    class Stop(FakeStop):
        def wait(self, timeout=None):
            result = super().wait(timeout)
            client.connected = not (105.0 <= self.clock.now < 115.0)  # 105·110 틱은 끊긴 상태
            return result

    sent = sender.run(Stop(clock, stop_at=100.0 + 20.5))
    assert sent == 3 and sender.skipped == 2
    assert [c[0] for c in client.calls] == [100.0, 115.0, 120.0]


def test_single_publish_error_does_not_stop_the_loop():
    clock = FakeClock(100.0)
    client = FakeClient(clock)
    client.fail_at = {2}
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock)
    sent = sender.run(FakeStop(clock, stop_at=100.0 + 15.5))
    assert sent == 3 and sender.failed == 1 and sender.consecutive_failures == 0
    assert [c[0] for c in client.calls] == [100.0, 110.0, 115.0]


def test_consecutive_publish_errors_fail_the_agent():
    """고정된 오류(봉투·토픽·라이브러리 사용)는 서비스를 active 로 두지 않고 실패로 올린다. 성공이 끼면 연속 횟수는 리셋된다."""
    clock = FakeClock(100.0)
    client = FakeClient(clock)
    client.fail_at = {2, 4, 5, 6}  # 2 실패 → 3 성공(리셋) → 4·5·6 연속 실패
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock, max_consecutive_failures=3)
    with pytest.raises(HeartbeatFailed, match="3 times in a row"):
        sender.run(FakeStop(clock, stop_at=100.0 + 60.0))
    assert sender.sent == 2 and sender.failed == 4
    assert [c[0] for c in client.calls] == [100.0, 110.0]


def test_long_stall_rebases_schedule_instead_of_bursting():
    clock = FakeClock(100.0)
    client = FakeClient(clock)
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock)

    class Stop(FakeStop):
        def wait(self, timeout=None):
            result = super().wait(timeout)
            if len(self.waits) == 1:
                self.clock.now += 60.0  # 첫 대기 뒤 60초 정지(절전 등)
            return result

    stop = Stop(clock, stop_at=100.0 + 5.0 + 60.0 + 10.5)
    sent = sender.run(stop)
    # 100 에 보내고, 165 에 깨어나 보낸 뒤 밀린 12틱을 몰아 보내지 않고 170·175 에 보낸다
    assert [c[0] for c in client.calls] == [100.0, 165.0, 170.0, 175.0]
    assert sent == 4
    assert stop.waits[1:] == pytest.approx([5.0, 5.0, 5.0])


def test_stop_returns_promptly_after_current_wait():
    clock = FakeClock(100.0)
    client = FakeClient(clock)
    sender = HeartbeatSender(client, VM_ID, interval_s=5.0, clock=clock)
    stop = FakeStop(clock, stop_at=102.0)
    assert sender.run(stop) == 1
    assert stop.waits == [5.0]
