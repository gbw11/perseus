"""원 수신 간격의 재생 시계. 원 프레임 시각·순번·live 권한은 변경하지 않는다."""

import math
import time


class ReplayCancelled(Exception):
    """호출자가 재생 취소를 요청했다. 다음 프레임을 전달하지 않는다."""


class ReplayTimeline:
    """첫 입력에 고정한 상대 목표까지 기다린다. 늦은 프레임도 버리지 않는다.

    source_received_j_us는 원 capture의 단조시각이다. clock/sleeper는 재생용
    초 단위 함수이며, 검증용으로 교체할 수 있다. speed=0은 간격 없이 읽는다.
    """

    def __init__(self, speed: float = 1.0, *, clock=time.monotonic,
                 sleeper=time.sleep, cancelled=lambda: False):
        if type(speed) not in (int, float) or not 0 <= speed <= 16 or not math.isfinite(speed):
            raise ValueError("replay speed must be finite and between 0 and 16")
        if not all(callable(value) for value in (clock, sleeper, cancelled)):
            raise TypeError("replay clock, sleeper, and cancelled must be callable")
        self.speed = float(speed)
        self._speed_ratio = self.speed.as_integer_ratio()
        self._clock = clock
        self._sleep = sleeper
        self._cancelled = cancelled
        self._first_source: int | None = None
        self._last_source: int | None = None
        self._started: float | int | None = None
        self._last_clock: float | int | None = None

    def _now(self) -> float | int:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("replay clock must return finite seconds")
        if self._last_clock is not None and value < self._last_clock:
            raise ValueError("replay clock moved backwards")
        self._last_clock = value
        return value

    def _check_cancelled(self) -> None:
        if self._cancelled():
            raise ReplayCancelled("replay cancelled")

    def wait(self, source_received_j_us: int) -> dict:
        """전달 목표·실제 경과·지연을 us로 반환한다. 원 시각은 반환값으로 대체하지 않는다.

        원 시각 역전이나 잘못된 값은 ValueError, 취소는 ReplayCancelled다.
        양수 speed에서 분수us 목표는 올림하며, 무대기 모드는 목표/지연을0으로 둔다.
        """
        if type(source_received_j_us) is not int or not 0 <= source_received_j_us < 2**64:
            raise ValueError("source received timestamp must be an unsigned 64-bit integer")
        if self._last_source is not None and source_received_j_us < self._last_source:
            raise ValueError("source received timestamp moved backwards")
        self._check_cancelled()
        if self._first_source is None:
            started = self._now()
            self._first_source = source_received_j_us
            self._started = started
        self._last_source = source_received_j_us

        if self.speed:
            numerator, denominator = self._speed_ratio
            delta = source_received_j_us - self._first_source
            # 큰 원 epoch를 float로 바꾸지 않고 상대 정수 차이만 배율에 적용한다.
            target_us = (delta * denominator + numerator - 1) // numerator
        else:
            target_us = 0

        while True:
            self._check_cancelled()
            elapsed = self._now() - self._started
            if not math.isfinite(elapsed):
                raise ValueError("replay clock interval is not finite")
            numerator, denominator = elapsed.as_integer_ratio()
            actual_us = numerator * 1_000_000 // denominator
            remaining_us = target_us - actual_us
            if not self.speed or remaining_us <= 0:
                return {"target_elapsed_us": target_us, "actual_elapsed_us": actual_us,
                        "lateness_us": max(0, -remaining_us) if self.speed else 0}
            self._sleep(min(remaining_us, 50_000) / 1_000_000)
