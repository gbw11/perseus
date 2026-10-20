"""MQTT-01 heartbeat 송신 루프.

- 시작 직후 한 번, 그 뒤 interval_s 마다 payload `{}` 를 발행한다. 봉투(seq·boot_id·agent_instance_id·sent_at)는 클라이언트가 붙인다.
- 발행 시각은 단조 시계 기준 start + n*interval 로 잡아 발행에 걸린 시간이 누적되지 않는다.
- 브로커와 끊긴 동안은 보내지 않는다. 쌓아 두었다가 재연결 때 몰아 보내면 받는 쪽이 옛 heartbeat 를 한꺼번에 받는다.
  재연결은 클라이언트가 하고, 연결되면 다음 틱부터 다시 보낸다.
- 에이전트는 상태를 선언하지 않는다. 살아 있는지는 받는 쪽(백엔드·dom0-manager)이 수신 시각으로 판정한다.
- publish 가 예외를 내면 봉투·토픽·정책·라이브러리 사용 오류 같은 고정된 문제일 가능성이 크다. 연결 끊김은 예외가 아니라
  connected 로 드러나므로, 예외가 연속 max_consecutive_failures 번 이어지면 프로세스를 실패시켜 systemd 로그와 재시작 정책으로 드러낸다.
  heartbeat 를 한 번도 못 보내면서 서비스만 active 로 남는 상태를 두지 않는다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from perseus_mqtt import topics

log = logging.getLogger("xenova_agent.heartbeat")


class Publisher(Protocol):
    """perseus_mqtt.PerseusClient 의 쓰는 부분."""

    @property
    def connected(self) -> bool: ...

    def publish(self, topic: str, message_type: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...


class StopSignal(Protocol):
    """threading.Event 와 같은 모양. wait 가 True 를 돌려주면 멈춘다."""

    def wait(self, timeout: float | None = None) -> bool: ...


class HeartbeatFailed(RuntimeError):
    """publish 예외가 연속으로 이어져 에이전트가 더 돌 이유가 없다. 프로세스를 끝내고 systemd 가 로그·재시작으로 드러낸다."""


class HeartbeatSender:
    def __init__(
        self,
        client: Publisher,
        vm_id: str,
        *,
        interval_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        max_consecutive_failures: int = 3,
    ) -> None:
        self._client = client
        self._topic = topics.heartbeat(vm_id)
        self._interval = float(interval_s)
        self._clock = clock
        self._max_consecutive_failures = max_consecutive_failures
        self.sent = 0
        self.skipped = 0  # 연결이 없어 보내지 않은 틱
        self.failed = 0  # publish 가 예외를 낸 틱
        self.consecutive_failures = 0

    def send_once(self) -> bool:
        if not self._client.connected:
            self.skipped += 1
            log.debug("heartbeat 건너뜀: 브로커 연결 없음 (%d)", self.skipped)
            return False
        try:
            self._client.publish(self._topic, "agent.heartbeat", {})
        except Exception as exc:  # noqa: BLE001 - 어떤 예외든 세고, 연속되면 아래서 실패로 올린다
            self.failed += 1
            self.consecutive_failures += 1
            log.warning("heartbeat 발행 실패 (%d, 연속 %d): %s", self.failed, self.consecutive_failures, exc)
            if self.consecutive_failures >= self._max_consecutive_failures:
                raise HeartbeatFailed(
                    f"heartbeat publish failed {self.consecutive_failures} times in a row; last error: {exc}"
                ) from exc
            return False
        self.sent += 1
        self.consecutive_failures = 0
        return True

    def run(self, stop: StopSignal) -> int:
        """stop.wait() 가 True 를 돌려줄 때까지 돈다. 보낸 횟수를 돌려준다."""
        started = self._clock()
        tick = 0
        while True:
            self.send_once()
            tick += 1
            due = started + tick * self._interval
            now = self._clock()
            if now - due >= self._interval:
                # 절전·정지 등으로 크게 밀렸다. 밀린 틱을 몰아 보내지 않고, 방금 보낸 것을 새 기준(0번째)으로 다시 센다
                log.info("heartbeat 주기 재설정: %.1f초 밀림", now - due)
                started, tick = now, 1
                due = now + self._interval
            while True:
                remaining = due - self._clock()
                if remaining <= 0:
                    break
                if stop.wait(remaining):
                    return self.sent
