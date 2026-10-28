"""앱 생존 신호 `app.heartbeat` (MQTT 계약 6.1) 발행 도우미. Linux 앱이 Zephyr 앱과 같은 신호를 내게 한다.

앱의 실제 처리 루프에서 매 반복 `beat(state)` 를 부른다. 별도 스레드에서 부르면 루프가 멈춰도 신호가 계속 나가므로
"프로세스는 있는데 일을 안 한다" 를 잡지 못한다.

- MQTT: 계약 주기 1초로 고정이다(설정으로 바꾸지 않는다). 마지막 발행 뒤 1초가 지났을 때만 내고, 브로커와 끊겨 있으면 내지 않고
  쌓지도 않는다(QoS 0 실시간 신호). 다시 붙으면 다음 `beat()` 에서 바로 낸다
- systemd watchdog: MQTT 와 따로, 유닛의 `WatchdogSec` 절반마다 같은 `beat()` 에서 `WATCHDOG=1` 을 보낸다(systemd 권장).
  주기는 systemd 가 넘겨 주는 WATCHDOG_USEC 에서 읽으므로 유닛과 어긋날 수 없다. 유닛에 WatchdogSec 이 없으면 보내지 않는다.
  브로커 연결과 무관하게 보낸다(브로커가 죽었다고 멀쩡한 앱을 systemd 가 죽이면 안 된다). 루프가 멈추면 통지도 멈춰 systemd 가
  앱을 재시작한다. 루프 한 바퀴가 WatchdogSec 의 절반보다 길면 정상이어도 재시작될 수 있으니 WatchdogSec 은 그보다 넉넉히 잡는다
- 상태값은 계약의 active·standby·idle·error 만. 주행과 무관한 앱의 평상시는 idle 이다
- 이 신호는 앱 상태 표시용이다. dom0 Recovery Worker 는 Linux VM 을 이 신호로 재시작하지 않는다(VM 생존은 agent.heartbeat)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from . import systemd, topics

APP_STATES = ("active", "standby", "idle", "error")
PERIOD_S = 1.0  # 계약 6.1 의 발행 주기
_FROM_ENV = object()


class Publisher(Protocol):
    @property
    def connected(self) -> bool: ...

    def publish(self, topic: str, message_type: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...


class AppHeartbeat:
    def __init__(
        self,
        client: Publisher,
        app_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        watchdog: Callable[[], bool] = systemd.watchdog,
        watchdog_interval_s: Any = _FROM_ENV,
    ) -> None:
        """watchdog_interval_s 는 시험용이다. 운영에서는 생략해 유닛의 WatchdogSec(WATCHDOG_USEC)을 따른다. None 이면 통지하지 않는다."""
        interval = systemd.watchdog_interval_s() if watchdog_interval_s is _FROM_ENV else watchdog_interval_s
        if interval is not None and not interval > 0:
            raise ValueError(f"watchdog interval must be positive, got {interval!r}")
        self._client = client
        self._topic = topics.app_events(app_id)
        self._clock = clock
        self._watchdog = watchdog
        self._watchdog_every = None if interval is None else interval / 2
        self._started = clock()
        self._last_sent: float | None = None
        self._last_watchdog: float | None = None
        self._last_skipped: float | None = None
        self.sent = 0
        self.skipped = 0  # 연결이 없어 내지 못한 발행 주기 수(1초에 최대 1). beat() 호출 수가 아니다

    @property
    def watchdog_every_s(self) -> float | None:
        """watchdog 통지 간격(WatchdogSec 의 절반). 유닛에 WatchdogSec 이 없으면 None."""
        return self._watchdog_every

    def beat(self, state: str) -> bool:
        """처리 루프에서 매 반복 부른다. 이번에 MQTT 로 발행했으면 True."""
        if state not in APP_STATES:
            raise ValueError(f"app.heartbeat state must be one of {APP_STATES}, got {state!r}")
        now = self._clock()
        if self._watchdog_every is not None and (self._last_watchdog is None or now - self._last_watchdog >= self._watchdog_every):
            self._watchdog()
            self._last_watchdog = now
        if self._last_sent is not None and now - self._last_sent < PERIOD_S:
            return False
        if not self._client.connected:
            # _last_sent 는 그대로 두어 재연결 직후 바로 낸다. 못 낸 주기는 1초에 한 번만 센다
            if self._last_skipped is None or now - self._last_skipped >= PERIOD_S:
                self.skipped += 1
                self._last_skipped = now
            return False
        self._client.publish(self._topic, "app.heartbeat", {"state": state, "uptime_s": round(now - self._started, 3)})
        self._last_sent = now
        self.sent += 1
        return True
