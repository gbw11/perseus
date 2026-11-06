"""15 Hz 고정 주기 발행.

프레임 속도와 발행 주기를 떼어 놓는다:
- 프레임 쪽(webcam·video)은 처리가 끝날 때마다 최신 결과 하나만 바꿔 끼운다 (LatestResult).
- 발행 쪽은 1/15 s 마다 그 최신 결과를 낸다. 새 프레임이 아직 없으면 **같은 결과를 다시** 낸다 (새 seq).
  받는 쪽(자율주행 VM)은 latest-only 라 같은 값이 한 번 더 와도 해가 없고, 주기가 일정해야 끊김 판정이 쉽다.
- 최신 결과가 stale_s(기본 0.2 s)보다 오래됐으면 **내지 않는다.** 카메라가 멈췄는데 옛 결과를 계속 내면
  자율주행 VM 이 젯슨 끊김(시나리오 F6 → 정지)을 알아채지 못한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from xenova_mqtt import topics

from .config import APP_ID, MESSAGE_TYPE
from .geometry import Result, payload

log = logging.getLogger("xenova_perception")

TOPIC = topics.app_events(APP_ID)


class ResultSource(Protocol):
    def current(self, now: float) -> Result | None: ...


class LatestResult:
    """프레임 루프가 쓰고 발행 루프가 읽는 최신 결과 한 칸."""

    def __init__(self, stale_s: float) -> None:
        self._stale_s = stale_s
        self._lock = threading.Lock()
        self._result: Result | None = None

    def put(self, result: Result) -> None:
        with self._lock:
            self._result = result

    def current(self, now: float) -> Result | None:
        with self._lock:
            result = self._result
        if result is None or now - result.at > self._stale_s:
            return None
        return result


class Publisher:
    def __init__(
        self,
        client: Any,
        source: ResultSource,
        rate_hz: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._source = source
        self._period = 1.0 / rate_hz
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None
        self.published = 0
        self.skipped = 0
        self.last_payload: dict[str, Any] | None = None
        self._last_stop: bool | None = None

    def tick(self) -> dict[str, Any] | None:
        """발행 시각이 됐으면 한 건 낸다. 낸 payload 또는 None."""
        now = self._clock()
        # 마감 시각을 누적한다 ("지난 발행 후 1/15 s" 로 재면 sleep 오차가 쌓여 14 Hz 가 된다).
        # 한 주기 넘게 밀리면 따라잡으려 몰아 내지 않고 지금부터 다시 센다
        if self._next is None or now - self._next > self._period:
            self._next = now
        if now < self._next - 1e-9:
            return None
        self._next += self._period
        result = self._source.current(now)
        if result is None:
            self.skipped += 1
            if self.skipped == 1 or self.skipped % 75 == 0:  # 5 초에 한 번
                log.warning("인식 결과가 없거나 오래돼 발행하지 않음 (누적 %d 주기)", self.skipped)
            return None
        body = payload(result)
        self._client.publish(TOPIC, MESSAGE_TYPE, body, qos=0, retain=False)
        self.published += 1
        self.last_payload = body
        if result.stop != self._last_stop:
            log.info("stop=%s obstacles=%d", result.stop, len(result.obstacles))
            self._last_stop = result.stop
        return body

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.tick()
            if self._next is not None:
                delay = self._next - self._clock()
                if delay > 0:
                    self._sleep(min(delay, self._period))
