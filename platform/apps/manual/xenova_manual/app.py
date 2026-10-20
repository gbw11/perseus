"""수동주행 앱 본체: 최신 drive.input 과 vehicle.control 로 drive.command 를 50 Hz 발행한다 (MQTT 계약 v1 5·7장).

- 활성이든 대기든 계속 발행한다. 게이트웨이가 대기 앱 명령의 신선도로 승격 여부를 정한다 (5.2).
- 입력이 input_timeout_ms 동안 없으면 키를 뗀 것으로 보고 정지 명령을 낸다.
- vehicle/control 을 아직 못 받았으면 generation 0 으로 낸다. ESP32 는 세대가 다르면 버리므로 차는 움직이지 않는다.
- 1초마다 app.heartbeat 로 프로세스 생존과 상태를 알린다 (6.1). 주행 루프 안에서 보내므로 루프가 멈추면 같이 멈춘다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from xenova_mqtt import topics

from .config import APP_ID, ManualSettings
from .keymap import STOP, Target, to_target

log = logging.getLogger("xenova_manual")

COMMAND_TOPIC = topics.app_events(APP_ID)
# MQTT 계약 v1 6.1: 1초 주기, 받는 쪽은 3초 못 받으면 끊김
HEARTBEAT_PERIOD_S = 1.0


def _generation(control: Any) -> int | None:
    if control is None:
        return None
    value = control.payload.get("generation")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class ManualDriveApp:
    def __init__(self, client: Any, settings: ManualSettings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock
        self._warned_no_control = False
        self._last_mode: tuple[Any, Any] | None = None
        self._started = clock()
        self._last_heartbeat: float | None = None
        self.sent = 0

    def subscribe(self) -> None:
        # 핸들러 없이 구독해 latest-only 로 읽는다. vehicle/control 은 retained 라 연결 직후 현재 세대가 온다
        self._client.subscribe(topics.vehicle_control(), qos=1)
        self._client.subscribe(topics.manual_input(), qos=0)

    def target(self) -> Target:
        message = self._client.latest(topics.manual_input())
        if message is None or message.type != "drive.input" or message.age_ms() > self._settings.input_timeout_ms:
            return STOP
        keys = message.payload.get("keys")
        if not isinstance(keys, list):
            return STOP
        s = self._settings
        return to_target(keys, max_speed_mps=s.max_speed_mps, max_reverse_mps=s.max_reverse_mps, max_steer_rad=s.max_steer_rad)

    def generation(self) -> int:
        control = self._client.latest(topics.vehicle_control())
        generation = _generation(control)
        if generation is None:
            if not self._warned_no_control:
                log.warning("vehicle/control 없음: generation 0 으로 발행 (게이트웨이 대기)")
                self._warned_no_control = True
            return 0
        mode = (control.payload.get("mode"), control.payload.get("active_app"))
        if mode != self._last_mode:
            log.info("vehicle/control generation=%d mode=%s active_app=%s", generation, *mode)
            self._last_mode = mode
        return generation

    def state(self) -> str:
        """app.heartbeat 의 state. 활성 여부는 게이트웨이가 정한 vehicle/control 의 active_app 으로만 판단한다."""
        control = self._client.latest(topics.vehicle_control())
        if _generation(control) is None:
            return "idle"
        return "active" if control.payload.get("active_app") == APP_ID else "standby"

    def heartbeat(self) -> dict[str, Any] | None:
        now = self._clock()
        if self._last_heartbeat is not None and now - self._last_heartbeat < HEARTBEAT_PERIOD_S:
            return None
        self._last_heartbeat = now
        payload = {"state": self.state(), "uptime_s": round(now - self._started, 3)}
        self._client.publish(COMMAND_TOPIC, "app.heartbeat", payload)
        return payload

    def tick(self) -> dict[str, Any]:
        target = self.target()
        payload = {"generation": self.generation(), "v_mps": target.v_mps, "steer_rad": target.steer_rad}
        self._client.publish(COMMAND_TOPIC, "drive.command", payload)
        self.sent += 1
        self.heartbeat()
        return payload

    def run(self, stop: threading.Event) -> None:
        """고정 주기로 tick 한다. 마감 시각을 누적해 sleep 오차가 쌓이지 않게 하고, 한 주기 넘게 밀리면 다시 맞춘다."""
        period = self._settings.period_s
        deadline = self._clock()
        while not stop.is_set():
            self.tick()
            deadline += period
            delay = deadline - self._clock()
            if delay < -period:
                log.debug("주기 밀림 %.1f ms, 다시 맞춤", -delay * 1000)
                deadline = self._clock()
                delay = 0.0
            if delay > 0:
                # Event.wait 는 Windows 에서 15.6 ms 단위라 50 Hz 가 깨진다. 한 주기(20 ms) 이하라 멈춤 반응도 충분하다
                time.sleep(delay)
