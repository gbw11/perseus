"""제어권 판정 상태 기계 (MQTT 계약 v1.2 5·6장, RC카 시나리오 4장).

입출력이 없는 순수 객체다. 시각은 호출하는 쪽이 넘긴다(단조 시계, 초). 단위 시험은 시각과 메시지를 직접 넣는다.

- 신선도는 받은 시각으로만 잰다. `sent_at` 은 보지 않는다 (계약 1장).
- 앱이 살아 있는지는 세대와 상관없이 유효한 `drive.command` 수신으로 본다. 전환 직후 새 활성 앱은 잠깐 옛 세대로 보내기 때문이다.
- ESP32 로 전달하는 것은 활성 앱의, 현재 세대와 같은 명령뿐이다 (계약 5.5 1·2).
- step 한 번에 모드 전환은 최대 한 번이다. 다음 조건은 다음 step 에서 본다.
- 재개는 바로 바꾸지 않는다 (계약 v1.2 6장). 받아들이면 `drive.resume.accepted` 를 내고 3초 뒤 step 에서 `AUTO` 로 바꾼다.
  그 사이 `AUTO_READY` 를 벗어나면 `drive.resume.cancelled` 를 낸다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("xenova_gateway")

AUTONOMOUS = "autonomous"
MANUAL_APP = "manual"
DRIVE_APPS = (AUTONOMOUS, MANUAL_APP)


class Mode(str, Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"
    AUTO_READY = "AUTO_READY"
    SAFE_STOP = "SAFE_STOP"


ACTIVE_APP: dict[Mode, str | None] = {
    Mode.AUTO: AUTONOMOUS,
    Mode.MANUAL: MANUAL_APP,
    Mode.AUTO_READY: MANUAL_APP,
    Mode.SAFE_STOP: None,
}

# vehicle.control / drive.mode.changed 의 reason. 앞의 넷은 계약 5.4 값, 뒤의 셋은 계약에 없어 새로 둔 값 (v1.1 반영 요청)
REASON_START = "start"
REASON_COMMAND_TIMEOUT = "command_timeout"
REASON_RESUME = "resume_requested"
REASON_OPERATOR_CANCELLED = "operator_cancelled"
REASON_NO_FRESH_STANDBY = "no_fresh_standby"
REASON_MANUAL_FRESH = "manual_fresh"            # SAFE_STOP → MANUAL
REASON_AUTONOMOUS_READY = "autonomous_ready"    # MANUAL → AUTO_READY
REASON_AUTONOMOUS_LOST = "autonomous_lost"      # AUTO_READY → MANUAL


@dataclass(frozen=True)
class Timing:
    """계약 5·6.1장 값. 실보드 측정 후 확정한다 (계약 10장)."""

    command_timeout_s: float = 0.100   # 활성 앱 명령 끊김 → 전환
    ready_hold_s: float = 3.0          # 복구된 자율 명령이 이만큼 연속 신선 → AUTO_READY
    heartbeat_timeout_s: float = 3.0   # app.heartbeat 를 이만큼 못 받으면 끊김 (6.1)
    stop_resend_s: float = 0.020       # SAFE_STOP 동안 정지 프레임 반복 주기 (명령 주기와 같게)
    resume_countdown_s: float = 3.0    # 재개 수락 → AUTO 전환 (계약 v1.2 6장 switch_in_ms)


# --- 출력 -----------------------------------------------------------------------


@dataclass(frozen=True)
class PublishControl:
    payload: dict[str, Any]


@dataclass(frozen=True)
class PublishEvent:
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ForwardDrive:
    app_id: str
    generation: int
    v_mps: float
    steer_rad: float
    source: dict[str, Any] | None = None


@dataclass(frozen=True)
class ForwardStop:
    generation: int


@dataclass(frozen=True)
class Measure:
    """측정 로그 한 줄 (-87·-94). 시각은 모두 같은 단조 시계."""

    event: str
    fields: dict[str, Any]


Action = PublishControl | PublishEvent | ForwardDrive | ForwardStop | Measure


# --- 앱별 관찰값 -------------------------------------------------------------------


@dataclass
class AppTrack:
    app_id: str
    vm_id: str | None = None
    last_command_t: float | None = None
    streak_start_t: float | None = None     # 끊김 없이 이어진 명령의 첫 수신 시각
    pending: tuple[int, float, float, float] | None = None   # 아직 안 보낸 최신 명령 (generation, v, steer, t)
    last_heartbeat_t: float | None = None
    heartbeat_state: str | None = None
    faulted: bool = False
    dropped_stale_generation: int = 0


def valid_command(payload: Any) -> tuple[int, float, float] | None:
    """계약 5.4 drive.command payload 를 검사한다. 어긋나면 None (그 명령은 생존 신호로도 치지 않는다)."""
    if not isinstance(payload, dict):
        return None
    generation = payload.get("generation")
    v = payload.get("v_mps")
    steer = payload.get("steer_rad")
    if isinstance(generation, bool) or not isinstance(generation, int) or not 0 <= generation <= 0xFFFFFFFF:
        return None
    for value in (v, steer):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            if not math.isfinite(value):return None
        except OverflowError:return None
    return generation, float(v), float(steer)


@dataclass(frozen=True)
class _Countdown:
    """받아들인 재개 요청. AUTO_READY 에서만 있다."""

    start_t: float
    generation: int
    requested_by: str | None


@dataclass
class _Pending:
    """전환 뒤 새 세대 첫 출력까지 재기 위한 기록."""

    generation: int
    reason: str
    last_active_t: float | None
    detected_t: float


class ControlMachine:
    def __init__(
        self,
        *,
        generation: int = 1,
        start_mode: Mode = Mode.AUTO,
        timing: Timing = Timing(),
        vm_ids: dict[str, str] | None = None,
    ) -> None:
        self.timing = timing
        self.mode = start_mode
        self.generation = generation
        self.apps = {app: AppTrack(app, vm_id=(vm_ids or {}).get(app)) for app in DRIVE_APPS}
        self._resume_requested: list[tuple[float, str | None]] = []
        self._countdown: _Countdown | None = None
        # 운영자 취소 요청 (계약 v1.7). 다음 step 에서 카운트다운 중이면 operator_cancelled 로 끝낸다
        self._cancel_requested: tuple[float, str | None] | None = None
        self._last_stop_t: float | None = None
        self._measure: _Pending | None = None
        self._started = False
        self.transitions = 0
        self.forwarded = 0

    # --- 입력 -----------------------------------------------------------------

    def on_command(self, app_id: str, vm_id: str | None, payload: Any, t: float) -> bool:
        """drive.command 수신. t 는 받은 시각. 형식이 틀리면 False."""
        app = self.apps.get(app_id)
        command = valid_command(payload)
        if app is None or command is None:
            return False
        if vm_id:
            app.vm_id = vm_id
        if app.last_command_t is None or t - app.last_command_t > self.timing.command_timeout_s:
            app.streak_start_t = t
        app.last_command_t = t
        app.pending = (*command, t)
        if app.faulted:
            app.faulted = False
            log.info("%s 명령 재개", app_id)
        return True

    def on_heartbeat(self, app_id: str, vm_id: str | None, payload: Any, t: float) -> None:
        app = self.apps.get(app_id)
        if app is None:
            return
        if vm_id:
            app.vm_id = vm_id
        app.last_heartbeat_t = t
        state = payload.get("state") if isinstance(payload, dict) else None
        app.heartbeat_state = state if isinstance(state, str) else None

    def on_resume(self, t: float, requested_by: str | None = None) -> None:
        self._resume_requested.append((t, requested_by))

    def on_cancel_resume(self, t: float, requested_by: str | None = None) -> bool:
        """drive.resume.cancel_requested (계약 v1.7). 카운트다운 중일 때만 받고, 아니면 이벤트 없이 무시한다."""
        if self._countdown is None:
            log.info("재개 취소 요청 무시: 카운트다운 없음")
            return False
        self._cancel_requested = (t, requested_by)
        return True

    @property
    def counting_down(self) -> bool:
        return self._countdown is not None

    # --- 판정 -----------------------------------------------------------------

    def fresh(self, app_id: str, now: float) -> bool:
        last = self.apps[app_id].last_command_t
        return last is not None and now - last <= self.timing.command_timeout_s

    def ready(self, app_id: str, now: float) -> bool:
        """AUTO_READY 조건: 명령이 ready_hold_s 동안 끊김 없이 신선 + app.heartbeat 신선 (시나리오 D8)."""
        app = self.apps[app_id]
        return (
            self.fresh(app_id, now)
            and app.streak_start_t is not None
            and now - app.streak_start_t >= self.timing.ready_hold_s
            and app.last_heartbeat_t is not None
            and now - app.last_heartbeat_t <= self.timing.heartbeat_timeout_s
            and app.heartbeat_state != "error"
        )

    @property
    def active_app(self) -> str | None:
        return ACTIVE_APP[self.mode]

    def control_payload(self, reason: str) -> dict[str, Any]:
        active = self.active_app
        return {
            "generation": self.generation,
            "mode": self.mode.value,
            "active_app": active,
            "active_vm_id": self.apps[active].vm_id if active else None,
            "reason": reason,
        }

    def start(self, now: float) -> list[Action]:
        """시작 상태를 알린다. 시작 전에 받아 둔 명령은 on_command 로 먼저 넣는다."""
        self._started = True
        actions: list[Action] = [
            PublishControl(self.control_payload(REASON_START)),
            PublishEvent("drive.mode.changed", {
                "from": None, "to": self.mode.value, "generation": self.generation, "reason": REASON_START,
            }),
            Measure("start", {"mode": self.mode.value, "gen": self.generation, "t": now}),
        ]
        self._measure = _Pending(self.generation, REASON_START, None, now)
        if self.mode is Mode.SAFE_STOP:
            actions += self._stop(now)
        return actions

    def step(self, now: float) -> list[Action]:
        if not self._started:
            raise RuntimeError("start() 를 먼저 부른다")
        actions: list[Action] = []
        actions += self._operator_cancel(now)
        actions += self._faults(now)
        transition = self._decide(now)
        if transition is not None:
            actions += self._transition(*transition, now)
        else:
            actions += self._resume(now)
        actions += self._output(now)
        return actions

    def _faults(self, now: float) -> list[Action]:
        """한 번이라도 명령을 낸 주행 앱이 끊기면 drive.fault.detected 를 한 번 낸다 (활성·대기 모두)."""
        actions: list[Action] = []
        for app in self.apps.values():
            if app.last_command_t is not None and not app.faulted and not self.fresh(app.app_id, now):
                app.faulted = True
                actions.append(PublishEvent("drive.fault.detected", {
                    "app_id": app.app_id,
                    "vm_id": app.vm_id,
                    "reason": REASON_COMMAND_TIMEOUT,
                    "detected_at": None,   # 발행 직전에 UTC 로 채운다 (runner)
                }))
                log.warning("%s 명령 끊김 %.1f ms", app.app_id, (now - app.last_command_t) * 1000)
        return actions

    def _decide(self, now: float) -> tuple[Mode, str] | None:
        active = self.active_app
        if active is not None and not self.fresh(active, now):
            # 잘못된 승격보다 정지. AUTO 에서만 대기 중인 수동으로 넘긴다 (계약 5.1·5.2)
            if self.mode is Mode.AUTO and self.fresh(MANUAL_APP, now):
                return Mode.MANUAL, REASON_COMMAND_TIMEOUT
            return Mode.SAFE_STOP, REASON_NO_FRESH_STANDBY
        if self.mode is Mode.SAFE_STOP and self.fresh(MANUAL_APP, now):
            return Mode.MANUAL, REASON_MANUAL_FRESH
        if self.mode is Mode.MANUAL and self.ready(AUTONOMOUS, now):
            return Mode.AUTO_READY, REASON_AUTONOMOUS_READY
        if self.mode is Mode.AUTO_READY and not self.ready(AUTONOMOUS, now):
            return Mode.MANUAL, REASON_AUTONOMOUS_LOST
        # 여기까지 왔으면 AUTO_READY 조건이 아직 맞다
        countdown = self._countdown
        if countdown is not None and now - countdown.start_t >= self.timing.resume_countdown_s:
            return Mode.AUTO, REASON_RESUME
        return None

    def _resume(self, now: float) -> list[Action]:
        """drive.resume.requested 는 AUTO_READY 이고 카운트다운 중이 아닐 때만 받는다 (계약 v1.2 6장).

        다른 모드·카운트다운 중에는 무시하고 이벤트도 내지 않는다. 받으면 accepted 를 내고 now 부터 센다.
        같은 step 에 여러 건이면 첫 건만 받고 나머지는 카운트다운 중 재요청으로 본다.
        """
        if not self._resume_requested:
            return []
        _, requested_by = self._resume_requested[0]
        self._resume_requested.clear()
        if self.mode is not Mode.AUTO_READY or self._countdown is not None:
            why = "countdown" if self._countdown is not None else self.mode.value
            log.info("재개 요청 무시: %s", why)
            return [Measure("resume_ignored", {"mode": self.mode.value, "countdown": self._countdown is not None, "t": now})]
        self._countdown = _Countdown(now, self.generation, requested_by)
        switch_in_ms = round(self.timing.resume_countdown_s * 1000)
        log.info("재개 수락 gen=%d requested_by=%s, %d ms 뒤 AUTO", self.generation, requested_by, switch_in_ms)
        return [
            PublishEvent("drive.resume.accepted", {
                "switch_in_ms": switch_in_ms, "generation": self.generation, "requested_by": requested_by,
            }),
            Measure("resume_accepted", {"gen": self.generation, "t": now}),
        ]

    def _operator_cancel(self, now: float) -> list[Action]:
        """받아 둔 운영자 취소를 처리한다. 만료·모드 판정보다 먼저 봐서, 같은 step 에 만료돼도 취소가 이긴다."""
        request = self._cancel_requested
        self._cancel_requested = None
        if request is None or self._countdown is None:
            return []
        return self._cancel_countdown(REASON_OPERATOR_CANCELLED, now, requested_by=request[1])

    def _cancel_countdown(self, reason: str, now: float, *, requested_by: str | None = None) -> list[Action]:
        """카운트다운을 끝내고 cancelled 를 낸다. reason 은 그 전환의 reason 또는 operator_cancelled(모드 유지)."""
        countdown = self._countdown
        self._cancel_requested = None
        if countdown is None:
            return []
        self._countdown = None
        log.info("재개 취소 gen=%d reason=%s", countdown.generation, reason)
        payload: dict[str, Any] = {"reason": reason, "generation": countdown.generation}
        if reason == REASON_OPERATOR_CANCELLED:
            payload["requested_by"] = requested_by
        return [
            PublishEvent("drive.resume.cancelled", payload),
            Measure("resume_cancelled", {"gen": countdown.generation, "reason": reason,
                                         "since_accept_ms": round((now - countdown.start_t) * 1000, 3)}),
        ]

    def _transition(self, to: Mode, reason: str, now: float) -> list[Action]:
        previous_mode = self.mode
        previous_active = self.active_app
        last_active_t = self.apps[previous_active].last_command_t if previous_active else None
        # 세대를 올리기 전에: cancelled 의 generation 은 accepted 와 같은 세대
        cancelled = self._cancel_countdown(reason, now) if to is not Mode.AUTO else []
        self._countdown = None
        self.mode = to
        self.generation += 1
        self.transitions += 1
        self._resume_requested.clear()
        self._last_stop_t = None
        for app in self.apps.values():
            app.pending = None   # 옛 세대 명령은 어차피 전달하지 않는다
        gap_ms = (now - last_active_t) * 1000 if last_active_t is not None else None
        log.info("모드 %s → %s gen=%d reason=%s", previous_mode.value, to.value, self.generation, reason)
        self._measure = _Pending(self.generation, reason, last_active_t, now)
        stop = self._stop(now) if to is Mode.SAFE_STOP else []
        # 정지 프레임이 cancelled·control·이벤트보다 먼저. 측정 기록(first_output)은 맨 뒤
        actions: list[Action] = stop[:1]
        actions += cancelled
        actions += [
            PublishControl(self.control_payload(reason)),
            PublishEvent("drive.mode.changed", {
                "from": previous_mode.value, "to": to.value, "generation": self.generation, "reason": reason,
            }),
            Measure("transition", {
                "from": previous_mode.value, "to": to.value, "gen": self.generation, "reason": reason,
                "last_active_t": last_active_t, "detected_t": now,
                "gap_ms": round(gap_ms, 3) if gap_ms is not None else None,
            }),
        ]
        return actions + stop[1:]

    def _stop(self, now: float) -> list[Action]:
        self._last_stop_t = now
        actions: list[Action] = [ForwardStop(self.generation)]
        actions += self._first_output(now)
        return actions

    def _output(self, now: float) -> list[Action]:
        if self.mode is Mode.SAFE_STOP:
            last, period = self._last_stop_t, self.timing.stop_resend_s
            if last is None or now - last >= 2 * period:
                return self._stop(now)
            if now - last >= period:
                # 깨어난 시각이 아니라 정해진 격자에 맞춰 평균 주기를 지킨다
                actions = self._stop(now)
                self._last_stop_t = last + period
                return actions
            return []
        actions: list[Action] = []
        for app in self.apps.values():
            if app.pending is None:
                continue
            generation, v, steer, _ = app.pending
            app.pending = None
            if app.app_id != self.active_app:
                continue   # 대기 앱 명령은 신선도 확인에만 쓴다 (5.2)
            if generation != self.generation:
                app.dropped_stale_generation += 1
                continue   # 5.5-2: 세대가 다르면 버린다
            self.forwarded += 1
            actions.append(ForwardDrive(app.app_id, generation, v, steer))
            actions += self._first_output(now)
        return actions

    def _first_output(self, now: float) -> list[Action]:
        pending = self._measure
        if pending is None or pending.generation != self.generation:
            return []
        self._measure = None
        fields: dict[str, Any] = {"gen": self.generation, "mode": self.mode.value, "reason": pending.reason, "t": now,
                                  "since_detect_ms": round((now - pending.detected_t) * 1000, 3)}
        if pending.last_active_t is not None:
            fields["since_last_active_ms"] = round((now - pending.last_active_t) * 1000, 3)
        return [Measure("first_output", fields)]
