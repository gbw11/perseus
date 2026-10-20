"""MQTT-01 heartbeat 송신 루프.

- 시작 직후 한 번, 그 뒤 interval_s 마다 payload `{}` 를 발행한다. 봉투(seq·boot_id·agent_instance_id·sent_at)는 클라이언트가 붙인다.
- 발행 시각은 단조 시계 기준 start + n*interval 로 잡아 발행에 걸린 시간이 누적되지 않는다.
- 브로커와 끊긴 동안은 보내지 않는다. 쌓아 두었다가 재연결 때 몰아 보내면 받는 쪽이 옛 heartbeat 를 한꺼번에 받는다.
  재연결은 클라이언트가 하고, 연결되면 다음 틱부터 다시 보낸다.
- 에이전트는 상태를 선언하지 않는다. 살아 있는지는 받는 쪽(백엔드·dom0-manager)이 수신 시각으로 판정한다.
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


class HeartbeatSender:
    def __init__(
        self,
        client: Publisher,
        vm_id: str,
        *,
        interval_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._topic = topics.heartbeat(vm_id)
        self._interval = float(interval_s)
        self._clock = clock
        self.sent = 0
        self.skipped = 0  # 연결이 없어 보내지 않은 틱
        self.failed = 0  # publish 가 예외를 낸 틱

    def send_once(self) -> bool:
        if not self._client.connected:
            self.skipped += 1
            log.debug("heartbeat 건너뜀: 브로커 연결 없음 (%d)", self.skipped)
            return False
        try:
            self._client.publish(self._topic, "agent.heartbeat", {})
        except Exception as exc:  # noqa: BLE001 - 봉투·크기 오류 같은 프로그래밍 오류도 루프를 죽이면 안 된다
            self.failed += 1
            log.warning("heartbeat 발행 실패 (%d): %s", self.failed, exc)
            return False
        self.sent += 1
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
