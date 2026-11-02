"""MQTT 와 상태 기계, ESP32 출력을 잇는 판정 루프.

- MQTT 콜백(paho 스레드)은 받은 시각을 찍어 큐에 넣기만 한다. 상태는 루프 스레드 하나만 바꾼다.
- 루프는 메시지가 오면 바로, 없으면 TICK_S 마다 깨어 판정한다.
- 출력 순서: 정지 프레임 → vehicle/control → 이벤트 (상태 기계가 낸 순서 그대로).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from xenova_mqtt import topics
from xenova_mqtt.envelope import utc_now

from .config import EVENT_APP_ID
from .frame import STOP_SAFE_STOP, STOP_SHUTDOWN, FrameEncoder
from .machine import (
    AUTONOMOUS,
    DRIVE_APPS,
    MANUAL_APP,
    ControlMachine,
    ForwardDrive,
    ForwardStop,
    Measure,
    Mode,
    PublishControl,
    PublishEvent,
    Timing,
)
from .sink import Sink
from .redundancy import RedundantControlMachine

log = logging.getLogger("xenova_gateway")

# 판정 주기 상한. 100 ms 끊김을 이 값 안팎의 오차로 잡는다
TICK_S = 0.005
# 기본 시계. 단조 증가하고 해상도가 높다 (Linux CLOCK_MONOTONIC). Windows 의 time.monotonic 은 15.6 ms 단위라 쓰지 않는다
QUEUE_MAX = 1024
MESSAGE_BOARD = "message-board"
# MQTT-13 운영자 콘솔 재개 (백엔드가 POST /api/v1/drive/resume 을 받아 발행, 계약 v1.2). xenova_mqtt.topics 에 아직 빌더가 없다
CONSOLE_RESUME = f"{topics.PREFIX}/console/resume"
RESUME_REQUESTED = "drive.resume.requested"


def _generation(payload: Any) -> int | None:
    value = payload.get("generation") if isinstance(payload, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 0xFFFFFFFF:
        return None
    return value


class GatewayRunner:
    def __init__(
        self,
        client: Any,
        sink: Sink,
        *,
        start_mode: Mode = Mode.AUTO,
        timing: Timing = Timing(),
        retained_wait_s: float = 1.0,
        clock: Callable[[], float] = time.perf_counter,
        vm_ids: tuple[str, str] | None = None,
        initial_active_vm: str | None = None,
    ) -> None:
        if vm_ids is not None or initial_active_vm is not None:
            raise ValueError("paired firmware requires VerifiedGatewayRunner with authority and trusted bindings")
        self._client = client
        self._sink = sink
        self._start_mode = start_mode
        self._timing = timing
        self._retained_wait_s = retained_wait_s
        self._clock = clock
        self._vm_ids = vm_ids
        self._initial_active_vm = initial_active_vm
        self._queue: queue.Queue[tuple[float, Any]] = queue.Queue(maxsize=QUEUE_MAX)
        self._encoder = FrameEncoder()
        self.machine: ControlMachine | None = None
        self.dropped_queue_full = 0
        self.rejected_commands = 0

    # --- 입력 -----------------------------------------------------------------

    def subscribe(self) -> None:
        # 주행 명령은 QoS 0 (계약 5.3). 재개 요청은 QoS 1. control 은 시작 시 이전 세대를 읽는 데만 쓴다
        for app in DRIVE_APPS:
            self._client.subscribe(topics.app_events(app), self._enqueue, qos=0)
        self._client.subscribe(topics.app_events(MESSAGE_BOARD), self._enqueue, qos=1)
        self._client.subscribe(CONSOLE_RESUME, self._enqueue, qos=1)
        self._client.subscribe(topics.vehicle_control(), qos=1)

    def _enqueue(self, message: Any) -> None:
        """paho 스레드. 수신 시각만 찍고 넘긴다."""
        try:
            self._queue.put_nowait((self._clock(), message))
        except queue.Full:
            self.dropped_queue_full += 1
            if self.dropped_queue_full & (self.dropped_queue_full-1)==0:
                log.warning("입력 큐 가득 참, 누적 버림=%d", self.dropped_queue_full)

    def _feed(self, t: float, message: Any) -> None:
        machine = self.machine
        assert machine is not None
        app_id = topics.parse(message.topic).app_id
        vm_id = message.envelope.get("vm_id")
        if app_id in DRIVE_APPS:
            if isinstance(machine, RedundantControlMachine):
                if getattr(message, "retained", False) or message.type not in ("drive.command", "app.heartbeat") or not machine.accept_session(
                    vm_id, message.envelope.get("boot_id"), message.envelope.get("agent_instance_id")
                ):
                    return
            if message.type == "drive.command":
                if not machine.on_command(app_id, vm_id, message.payload, t):
                    self.rejected_commands+=1
                    if self.rejected_commands & (self.rejected_commands-1)==0:
                        log.warning("버림 %s drive.command: invalid payload 누적=%d", app_id, self.rejected_commands)
            elif message.type == "app.heartbeat":
                machine.on_heartbeat(app_id, vm_id, message.payload, t)
        elif message.type == RESUME_REQUESTED and (app_id == MESSAGE_BOARD or message.topic == CONSOLE_RESUME):
            # 메시지 보드(MQTT-06)와 운영자 콘솔(MQTT-13) 어느 쪽에서 와도 같다
            requested_by = message.payload.get("requested_by")
            requested_by = requested_by if isinstance(requested_by, str) else None
            log.info("재개 요청 수신 requested_by=%s", requested_by[:96] if requested_by else None)
            machine.on_resume(t, requested_by)

    def _drain(self, first: tuple[float, Any] | None = None) -> None:
        item = first
        while True:
            if item is None:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    return
            self._feed(*item)
            item = None

    # --- 출력 -----------------------------------------------------------------

    def _emit(self, actions: list[Any]) -> None:
        for action in actions:
            if isinstance(action, ForwardDrive):
                frame = self._encoder.drive(action.generation, action.v_mps, action.steer_rad)
                self._sink.write(frame, self._clock())
            elif isinstance(action, ForwardStop):
                self._sink.write(self._encoder.stop(action.generation, STOP_SAFE_STOP), self._clock())
            elif isinstance(action, PublishControl):
                self._client.publish(topics.vehicle_control(), "vehicle.control", action.payload)
            elif isinstance(action, PublishEvent):
                payload = dict(action.payload)
                if action.type == "drive.fault.detected" and payload.get("detected_at") is None:
                    payload["detected_at"] = utc_now()
                self._client.publish(topics.app_events(EVENT_APP_ID), action.type, payload)
            elif isinstance(action, Measure):
                if log.isEnabledFor(logging.INFO):
                    log.info("MEASURE event=%s %s", action.event, " ".join(
                        f"{k}={v:.6f}" if isinstance(v, float) and k.endswith("t") else f"{k}={v}"
                        for k, v in action.fields.items()))

    # --- 루프 -----------------------------------------------------------------

    def previous_generation(self) -> int | None:
        """retained vehicle/control 의 세대. 게이트웨이를 다시 띄워도 세대를 되돌리지 않기 위해 읽는다."""
        message = self._client.latest(topics.vehicle_control())
        return _generation(message.payload) if message is not None else None

    def begin(self) -> None:
        previous = self.previous_generation()
        generation = previous + 1 if previous is not None else 1
        if self._vm_ids is not None:
            initial, mode = self._initial_active_vm, self._start_mode
            retained = self._client.latest(topics.vehicle_control())
            if retained is not None:
                control = retained.payload
                value = control.get("generation")
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 0xFFFFFFFF:
                    raise ValueError("retained control generation invalid or exhausted")
                generation = value + 1
                try:
                    previous_mode = Mode(control["mode"])
                except (ValueError, KeyError):
                    raise ValueError("retained control mode invalid") from None
                from .machine import ACTIVE_APP
                if control.get("active_app") != ACTIVE_APP[previous_mode]:
                    raise ValueError("retained control app/mode conflict")
                selected = control.get("active_vm_id")
                if previous_mode is not Mode.SAFE_STOP:
                    if selected not in self._vm_ids:
                        raise ValueError("retained active VM outside configured pair")
                    initial = selected
                elif selected is not None:
                    raise ValueError("SAFE_STOP retained active VM must be null")
                # SAFE_STOP explicitly requested by the operator still wins.
                mode = Mode.SAFE_STOP if self._start_mode is Mode.SAFE_STOP else previous_mode
            self.machine = RedundantControlMachine(vm_ids=self._vm_ids, initial_active_vm=initial,
                generation=generation, start_mode=mode, timing=self._timing)
        else:
            self.machine = ControlMachine(generation=generation, start_mode=self._start_mode, timing=self._timing)
        self._drain()   # 시작 전에 받은 명령으로 신선도를 채운다
        log.info("시작 모드=%s generation=%d (이전 %s)", self._start_mode.value, generation, previous)
        self._emit(self.machine.start(self._clock()))

    def run_once(self, timeout_s: float = TICK_S) -> None:
        assert self.machine is not None
        try:
            first = self._queue.get(timeout=timeout_s) if timeout_s > 0 else self._queue.get_nowait()
        except queue.Empty:
            first = None
        self._drain(first)
        self._emit(self.machine.step(self._clock()))

    def shutdown(self) -> None:
        if self.machine is not None:
            self._sink.write(self._encoder.stop(self.machine.generation, STOP_SHUTDOWN), self._clock())
        self._sink.close()

    def run(self, stop: threading.Event, *, connect_wait_s: float = 5.0) -> None:
        self.subscribe()
        if not self._client.start(wait_s=connect_wait_s):
            log.warning("브로커에 아직 연결되지 않음, 뒤에서 재시도")
        deadline = self._clock() + self._retained_wait_s
        while self._clock() < deadline and self.previous_generation() is None and not stop.is_set():
            time.sleep(0.01)
        self.begin()
        try:
            while not stop.is_set():
                self.run_once()
        finally:
            self.shutdown()
            assert self.machine is not None
            m = self.machine
            log.info("종료 모드=%s generation=%d 전환 %d 전달 %d 옛세대 버림 autonomous=%d manual=%d",
                     m.mode.value, m.generation, m.transitions, m.forwarded,
                     m.apps[AUTONOMOUS].dropped_stale_generation, m.apps[MANUAL_APP].dropped_stale_generation)
