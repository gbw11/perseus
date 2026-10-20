"""수동주행 앱 본체: 최신 drive.input 과 vehicle.control 로 drive.command 를 50 Hz 발행한다 (MQTT 계약 v1 5·7장).

- 활성이든 대기든 계속 발행한다. 게이트웨이가 대기 앱 명령의 신선도로 승격 여부를 정한다 (5.2).
- 입력이 input_timeout_ms 동안 없으면 키를 뗀 것으로 보고 정지 명령을 낸다.
- vehicle/control 을 아직 못 받았으면 generation 0 으로 낸다. ESP32 는 세대가 다르면 버리므로 차는 움직이지 않는다.
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

    def tick(self) -> dict[str, Any]:
        target = self.target()
        payload = {"generation": self.generation(), "v_mps": target.v_mps, "steer_rad": target.steer_rad}
        self._client.publish(COMMAND_TOPIC, "drive.command", payload)
        self.sent += 1
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
