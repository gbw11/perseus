"""대칭 VM 두 대의 실행 관측과 출력 선택. AUTO/MANUAL 정책은 기존 기계를 재사용한다.

각 VM은 두 계산을 수행하고 현재 선택 모드의 후보만 보낸다. 후보의 세대,
신선도, 실제 계산 heartbeat의 연속 준비를 함께 확인한다. Gateway가 확인하는
동기화는 이 범위까지이며 앱 메모리 복제나 Xen 실행 신원 검증은 대신하지 않는다.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID
import math

from .machine import (ACTIVE_APP, AUTONOMOUS, MANUAL_APP, DRIVE_APPS, Action,
                      AppTrack, ControlMachine, ForwardDrive, ForwardStop,
                      Measure, Mode, PublishControl, PublishEvent, Timing, valid_command)


def vm_uuid(value: str) -> str:
    parsed = UUID(value)
    if str(parsed) != value or not parsed.int:
        raise ValueError("canonical nonzero VM UUID required")
    return value


class RedundantControlMachine(ControlMachine):
    """UUID별 관측. 최초 활성 UUID는 역할이 아니며 승계 뒤 자동 복귀하지 않는다."""

    def __init__(self, *, vm_ids: tuple[str, str], initial_active_vm: str | None = None,
                 generation: int = 1, start_mode: Mode = Mode.AUTO,
                 timing: Timing = Timing()) -> None:
        pair = tuple(vm_uuid(v) for v in vm_ids)
        if len(pair) != 2 or pair[0] == pair[1]:
            raise ValueError("two distinct VM UUIDs required")
        self.active_vm = initial_active_vm or pair[0]
        if self.active_vm not in pair:
            raise ValueError("initial active VM must belong to configured pair")
        self.nodes = {vm: {app: AppTrack(app, vm_id=vm) for app in DRIVE_APPS} for vm in pair}
        self._healthy_since: dict[tuple[str, str], float] = {}
        self._command_generation: dict[tuple[str, str], int] = {}
        self._stream_last: dict[tuple[str, str], float] = {}
        self._sessions: dict[str, tuple[str, str]] = {}
        self._retired: dict[str, set[tuple[str, str]]] = {vm: set() for vm in pair}
        self._handover_until = 0.0
        self._confirmed_generation = False
        super().__init__(generation=generation, start_mode=start_mode, timing=timing)
        self.apps = self.nodes[self.active_vm]

    def accept_session(self, vm_id: str, boot: Any, instance: Any) -> bool:
        """이전 실행의 지연 명령을 재합류로 오인하지 않는다. runner가 검증된 봉투를 전달한다."""
        if vm_id not in self.nodes or not isinstance(boot, str) or not isinstance(instance, str):
            return False
        try:
            session = (vm_uuid(boot), vm_uuid(instance))
        except (ValueError, AttributeError):
            return False
        if session in self._retired[vm_id]:
            return False
        previous = self._sessions.get(vm_id)
        if previous != session:
            # Fail closed rather than evicting retired sessions and accepting old bytes.
            if len(self._retired[vm_id]) >= 64:
                return False
            if previous is not None:
                self._retired[vm_id].add(previous)
            self._sessions[vm_id] = session
            self.nodes[vm_id] = {app: AppTrack(app, vm_id=vm_id) for app in DRIVE_APPS}
            for app in DRIVE_APPS:
                self._healthy_since.pop((vm_id, app), None)
                self._command_generation.pop((vm_id, app), None)
                self._stream_last.pop((vm_id, app), None)
            self.apps = self.nodes[self.active_vm]
        return True

    def on_command(self, app_id: str, vm_id: str | None, payload: Any, t: float) -> bool:
        if vm_id not in self.nodes or app_id not in DRIVE_APPS:
            return False
        command = valid_command(payload)
        track = self.nodes[vm_id][app_id]
        if command is None:
            return False
        if command[0] > self.generation:
            return False
        key = (vm_id, app_id)
        self._stream_last[key] = max(t, self._stream_last.get(key, t))
        if command[0] != self.generation:
            track.dropped_stale_generation += 1
            return False
        if track.last_command_t is not None and t < track.last_command_t:
            return False
        track.last_command_t = t
        track.faulted = False
        track.pending = (*command, t)
        self._command_generation[vm_id, app_id] = command[0]
        return True

    def on_heartbeat(self, app_id: str, vm_id: str | None, payload: Any, t: float) -> None:
        if vm_id not in self.nodes or app_id not in DRIVE_APPS or not isinstance(payload, dict):
            return
        state = payload.get("state")
        uptime = payload.get("uptime_s")
        if (state not in ("active", "standby", "idle", "error") or
            isinstance(uptime, bool) or not isinstance(uptime, (int, float)) or
            not math.isfinite(uptime) or uptime < 0):
            return
        track = self.nodes[vm_id][app_id]
        if track.last_heartbeat_t is not None and t < track.last_heartbeat_t:
            return
        key = (vm_id, app_id)
        if state in ("idle", "error"):
            self._healthy_since.pop(key, None)
        elif (track.last_heartbeat_t is None or
              t-track.last_heartbeat_t > self.timing.heartbeat_timeout_s or
              track.heartbeat_state in ("idle", "error")):
            self._healthy_since[key] = t
        track.last_heartbeat_t, track.heartbeat_state = t, state

    def _healthy(self, vm: str, app: str, now: float, *, held: bool = False) -> bool:
        track = self.nodes[vm][app]
        since = self._healthy_since.get((vm, app))
        return (since is not None and track.last_heartbeat_t is not None and
                0 <= now-track.last_heartbeat_t <= self.timing.heartbeat_timeout_s and
                (not held or now-since >= self.timing.ready_hold_s))

    def _fresh(self, vm: str, app: str, now: float) -> bool:
        t = self.nodes[vm][app].last_command_t
        return (self._command_generation.get((vm, app)) == self.generation and
                t is not None and 0 <= now-t <= self.timing.command_timeout_s)

    def _stream_fresh(self, vm: str, app: str, now: float) -> bool:
        """현재 실행의 계산/송신 생존. 출력·대기 준비의 세대 일치와 별개다."""
        t = self._stream_last.get((vm, app))
        return t is not None and 0 <= now-t <= self.timing.command_timeout_s

    def node_status(self, vm: str, now: float) -> str:
        """로컬 진단용. current-mode 후보와 연속 계산 준비 없이는 standby가 아니다."""
        app = self.active_app
        if app is None or not self._fresh(vm, app, now) or not self._healthy(vm, app, now, held=True):
            return "unready"
        return "active" if vm == self.active_vm else "standby"

    def control_payload(self, reason: str) -> dict[str, Any]:
        return {"generation": self.generation, "mode": self.mode.value,
                "active_app": self.active_app,
                "active_vm_id": self.active_vm if self.active_app else None, "reason": reason}

    def start(self, now: float) -> list[Action]:
        self._handover_until = now+self.timing.command_timeout_s
        return [ForwardStop(self.generation)] + super().start(now)

    def _reset_handover(self, now: float) -> None:
        self._handover_until = now+self.timing.command_timeout_s
        self._confirmed_generation = False
        self._last_stop_t = now
        for tracks in self.nodes.values():
            for track in tracks.values():
                track.pending = None

    def _transition(self, to: Mode, reason: str, now: float) -> list[Action]:
        actions = super()._transition(to, reason, now)
        self._reset_handover(now)
        if not actions or not isinstance(actions[0], ForwardStop):
            actions.insert(0, ForwardStop(self.generation))
        return actions

    def _promote(self, vm: str, now: float) -> list[Action]:
        previous = self.active_vm
        cancelled = self._cancel_countdown("command_timeout", now)
        self.active_vm, self.apps = vm, self.nodes[vm]
        self.generation += 1
        self.transitions += 1
        self._resume_requested.clear()
        self._reset_handover(now)
        return [ForwardStop(self.generation), *cancelled,
                PublishControl(self.control_payload("command_timeout")),
                Measure("vm_failover", {"from_vm": previous, "to_vm": vm,
                        "mode": self.mode.value, "gen": self.generation, "t": now})]

    def _output(self, now: float) -> list[Action]:
        app = self.active_app
        if app is None:
            return super()._output(now)
        track = self.apps[app]
        pending = track.pending
        for tracks in self.nodes.values():
            for item in tracks.values():
                item.pending = None
        if pending and self._fresh(self.active_vm, app, now) and self._healthy(self.active_vm, app, now):
            generation, v, steer, _ = pending
            self._confirmed_generation = True
            self.forwarded += 1
            return [ForwardDrive(app, generation, v, steer), *self._first_output(now)]
        if not self._confirmed_generation and (self._last_stop_t is None or now-self._last_stop_t >= self.timing.stop_resend_s):
            self._last_stop_t = now
            return [ForwardStop(self.generation)]
        return []

    def step(self, now: float) -> list[Action]:
        if not self._started:
            raise RuntimeError("start() required")
        vm, app = self.active_vm, self.active_app
        actions: list[Action] = []
        faults: list[Action] = []
        if app is not None:
            for node, tracks in self.nodes.items():
                track = tracks[app]
                if (track.last_command_t is not None and not track.faulted and
                    not any(self._stream_fresh(node, candidate, now) for candidate in DRIVE_APPS)):
                    track.faulted = True
                    faults.append(PublishEvent("drive.fault.detected", {"app_id": app,
                        "vm_id": node, "reason": "command_timeout", "detected_at": None}))
        other = next(v for v in self.nodes if v != vm)
        # An already-running command stream with failed perception is a mode
        # failure; a missing stream is a VM/output-path failure.
        if app is not None:
            # A control round-trip may overlap an already-submitted old frame.
            # Preserve the existing generation-independent liveness rule, but
            # never use it for output, standby readiness or promotion. While
            # awaiting current context the encoder emits STOP, not an old frame.
            live = self._fresh(vm, app, now) if self._confirmed_generation else any(
                self._stream_fresh(vm, candidate, now) for candidate in DRIVE_APPS)
            if (self.mode is Mode.AUTO and self._fresh(vm, app, now) and
                self.apps[AUTONOMOUS].heartbeat_state == "error" and self._healthy(vm, MANUAL_APP, now)):
                actions = self._transition(Mode.MANUAL, "autonomous_lost", now)
            elif (not live or not self._healthy(vm, app, now)):
                if now >= self._handover_until:
                    if self.node_status(other, now) == "standby":
                        actions = self._promote(other, now)
                    else:
                        actions = self._transition(Mode.SAFE_STOP, "no_fresh_standby", now)
            elif not self._fresh(vm, app, now):
                pass  # live but current control not confirmed: keep STOP
            elif self.mode is Mode.MANUAL and self._healthy(vm, AUTONOMOUS, now, held=True):
                actions = self._transition(Mode.AUTO_READY, "autonomous_ready", now)
            elif self.mode is Mode.AUTO_READY:
                if not self._healthy(vm, AUTONOMOUS, now, held=True):
                    actions = self._transition(Mode.MANUAL, "autonomous_lost", now)
                elif self._countdown and now-self._countdown.start_t >= self.timing.resume_countdown_s:
                    actions = self._transition(Mode.AUTO, "resume_requested", now)
        elif self._healthy(vm, MANUAL_APP, now, held=True):
            actions = self._transition(Mode.MANUAL, "manual_fresh", now)
        # SAFE_STOP never elects an unconfirmed standby using heartbeat alone.
        if not actions:
            actions += self._resume(now)
        return actions + faults + self._output(now)
