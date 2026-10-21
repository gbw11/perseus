"""상태 기계 단위 시험. 시각과 메시지를 직접 넣으므로 sleep 이 없다."""

from __future__ import annotations

import pytest

from xenova_gateway.machine import (
    AUTONOMOUS,
    MANUAL_APP,
    ControlMachine,
    ForwardDrive,
    ForwardStop,
    Measure,
    Mode,
    PublishControl,
    PublishEvent,
)

AUTO_VM = "11111111-1111-4111-8111-111111111111"
MANUAL_VM = "22222222-2222-4222-8222-222222222222"
PERIOD = 0.020  # 50 Hz


def of(actions, kind):
    return [a for a in actions if isinstance(a, kind)]


def events(actions, type_):
    return [a.payload for a in of(actions, PublishEvent) if a.type == type_]


class Harness:
    """두 주행 앱의 50 Hz 명령을 시각 순서대로 넣고 매 명령 뒤 step 한다."""

    def __init__(self, start_mode: Mode = Mode.AUTO, generation: int = 1) -> None:
        self.m = ControlMachine(generation=generation, start_mode=start_mode)
        self.now = 0.0
        self.actions: list = []

    def start(self):
        self.actions += self.m.start(self.now)
        return self

    def cmd(self, app: str, generation: int | None = None, v: float = 0.0, t: float | None = None):
        vm = AUTO_VM if app == AUTONOMOUS else MANUAL_VM
        g = self.m.generation if generation is None else generation
        self.m.on_command(app, vm, {"generation": g, "v_mps": v, "steer_rad": 0.0}, self.now if t is None else t)

    def hb(self, app: str, state: str = "standby"):
        vm = AUTO_VM if app == AUTONOMOUS else MANUAL_VM
        self.m.on_heartbeat(app, vm, {"state": state, "uptime_s": 1.0}, self.now)

    def step(self):
        out = self.m.step(self.now)
        self.actions += out
        return out

    def run(self, seconds: float, apps=(AUTONOMOUS, MANUAL_APP), heartbeat=(), tick: float = PERIOD):
        """seconds 동안 tick 마다 apps 가 현재 세대로 명령을 내고 step 한다. heartbeat 앱은 1초마다."""
        end = self.now + seconds
        out = []
        next_hb = self.now
        while self.now < end - 1e-9:
            self.now = round(self.now + tick, 9)
            for app in apps:
                self.cmd(app)
            if heartbeat and self.now >= next_hb:
                for app in heartbeat:
                    self.hb(app)
                next_hb += 1.0
            out += self.step()
        return out


def running_auto() -> Harness:
    h = Harness()
    h.cmd(AUTONOMOUS)
    h.cmd(MANUAL_APP)
    h.start()
    h.run(0.2)
    assert h.m.mode is Mode.AUTO
    return h


def test_start_publishes_retained_control_and_mode_event():
    h = Harness(generation=5).start()
    control = of(h.actions, PublishControl)[0].payload
    assert control == {"generation": 5, "mode": "AUTO", "active_app": "autonomous", "active_vm_id": None, "reason": "start"}
    assert events(h.actions, "drive.mode.changed") == [{"from": None, "to": "AUTO", "generation": 5, "reason": "start"}]


def test_start_without_any_app_goes_safe_stop():
    h = Harness().start()
    out = h.step()
    assert h.m.mode is Mode.SAFE_STOP
    assert of(out, PublishControl)[0].payload["reason"] == "no_fresh_standby"
    assert of(out, ForwardStop)
    assert not events(out, "drive.fault.detected")   # 한 번도 명령을 낸 적 없는 앱은 장애로 알리지 않는다


def test_timeout_promotes_manual_after_100ms_silence():
    h = running_auto()
    gen = h.m.generation
    last_auto = h.now
    # 수동만 계속, 자율은 끊김. 판정 경계를 1 ms 단위로 확인
    h.now = round(last_auto + 0.100, 9)
    h.cmd(MANUAL_APP)
    h.step()
    assert h.m.mode is Mode.AUTO, "100 ms 까지는 신선"
    h.now = round(last_auto + 0.101, 9)
    out = h.step()
    assert h.m.mode is Mode.MANUAL
    assert h.m.generation == gen + 1
    control = of(out, PublishControl)[0].payload
    assert control["active_app"] == "manual" and control["active_vm_id"] == MANUAL_VM
    assert control["reason"] == "command_timeout"
    fault = events(out, "drive.fault.detected")
    assert fault and fault[0]["app_id"] == "autonomous" and fault[0]["vm_id"] == AUTO_VM
    assert events(out, "drive.mode.changed") == [{"from": "AUTO", "to": "MANUAL", "generation": gen + 1, "reason": "command_timeout"}]
    measure = [a for a in of(out, Measure) if a.event == "transition"][0]
    assert measure.fields["gap_ms"] == pytest.approx(101.0)


def test_timeout_promotes_manual_then_forwards_only_new_generation():
    h = running_auto()
    h.run(0.12, apps=(MANUAL_APP,))
    assert h.m.mode is Mode.MANUAL
    gen = h.m.generation
    # 수동 앱이 아직 옛 세대로 보낸 명령은 버린다
    h.now += PERIOD
    h.cmd(MANUAL_APP, generation=gen - 1, v=0.3)
    assert not of(h.step(), ForwardDrive)
    h.now += PERIOD
    h.cmd(MANUAL_APP, generation=gen, v=0.3)
    out = h.step()
    assert of(out, ForwardDrive) == [ForwardDrive("manual", gen, 0.3, 0.0)]
    first = [a for a in of(out, Measure) if a.event == "first_output"]
    assert first and first[0].fields["gen"] == gen and "since_last_active_ms" in first[0].fields


def test_both_silent_goes_safe_stop_and_repeats_stop_frames():
    h = running_auto()
    last = h.now
    h.now = round(last + 0.101, 9)
    out = h.step()
    assert h.m.mode is Mode.SAFE_STOP
    assert of(out, PublishControl)[0].payload == {
        "generation": h.m.generation, "mode": "SAFE_STOP", "active_app": None, "active_vm_id": None,
        "reason": "no_fresh_standby",
    }
    # 정지 프레임이 control 보다 먼저
    assert isinstance(out[out.index(of(out, ForwardStop)[0]) + 1], (Measure, PublishControl))
    assert out.index(of(out, ForwardStop)[0]) < out.index(of(out, PublishControl)[0])
    assert {e["app_id"] for e in events(out, "drive.fault.detected")} == {"autonomous", "manual"}
    stops = 0
    for _ in range(10):
        h.now = round(h.now + 0.005, 9)
        stops += len(of(h.step(), ForwardStop))
    assert stops == 2   # 50 ms 동안 20 ms 주기


def test_manual_silent_in_manual_goes_safe_stop_even_if_autonomous_fresh():
    h = running_auto()
    h.run(0.12, apps=(MANUAL_APP,))
    assert h.m.mode is Mode.MANUAL
    h.run(0.12, apps=(AUTONOMOUS,))
    assert h.m.mode is Mode.SAFE_STOP   # AUTO 는 재개로만 들어간다


def test_safe_stop_to_manual_when_manual_fresh_again():
    h = running_auto()
    h.now += 0.2
    h.step()
    assert h.m.mode is Mode.SAFE_STOP
    gen = h.m.generation
    h.now += PERIOD
    h.cmd(MANUAL_APP)
    out = h.step()
    assert h.m.mode is Mode.MANUAL and h.m.generation == gen + 1
    assert of(out, PublishControl)[0].payload["reason"] == "manual_fresh"
    # 자율만 돌아와도 SAFE_STOP 은 풀리지 않는다
    h2 = running_auto()
    h2.now += 0.2
    h2.step()
    h2.run(0.5, apps=(AUTONOMOUS,))
    assert h2.m.mode is Mode.SAFE_STOP


def failed_over() -> Harness:
    h = running_auto()
    h.run(0.5, apps=(MANUAL_APP,))
    assert h.m.mode is Mode.MANUAL
    return h


def test_auto_ready_requires_3s_continuous_commands_and_heartbeat():
    h = failed_over()
    # 명령만 3 s 넘게, heartbeat 없음 → MANUAL 유지
    h.run(3.2, apps=(AUTONOMOUS, MANUAL_APP))
    assert h.m.mode is Mode.MANUAL
    # heartbeat 가 오면 바로 조건 충족 (명령은 이미 3 s 넘게 연속)
    h.hb(AUTONOMOUS)
    h.run(PERIOD)
    assert h.m.mode is Mode.AUTO_READY


def test_auto_ready_after_3s_not_before():
    h = failed_over()
    gen = h.m.generation
    h.run(2.98, heartbeat=(AUTONOMOUS,))   # 첫 자율 명령은 +20 ms, 2.96 s 연속
    assert h.m.mode is Mode.MANUAL
    out = h.run(0.06, heartbeat=(AUTONOMOUS,))
    assert h.m.mode is Mode.AUTO_READY
    assert h.m.generation == gen + 1
    control = of(out, PublishControl)[0].payload
    assert control["mode"] == "AUTO_READY" and control["active_app"] == "manual" and control["reason"] == "autonomous_ready"


def test_auto_ready_streak_restarts_after_gap():
    h = failed_over()
    h.run(2.0, heartbeat=(AUTONOMOUS,))
    h.run(0.2, apps=(MANUAL_APP,))          # 자율 200 ms 끊김 → 연속 기록 리셋
    h.run(2.0, heartbeat=(AUTONOMOUS,))
    assert h.m.mode is Mode.MANUAL
    h.run(1.1, heartbeat=(AUTONOMOUS,))
    assert h.m.mode is Mode.AUTO_READY


def test_auto_ready_falls_back_to_manual_when_autonomous_lost():
    h = failed_over()
    h.run(3.1, heartbeat=(AUTONOMOUS,))
    assert h.m.mode is Mode.AUTO_READY
    gen = h.m.generation
    out = h.run(0.12, apps=(MANUAL_APP,))
    assert h.m.mode is Mode.MANUAL and h.m.generation == gen + 1
    assert of(out, PublishControl)[0].payload["reason"] == "autonomous_lost"


def test_resume_in_auto_ready_goes_auto():
    h = failed_over()
    h.run(3.1, heartbeat=(AUTONOMOUS,))
    gen = h.m.generation
    h.m.on_resume(h.now)
    out = h.run(PERIOD)
    assert h.m.mode is Mode.AUTO and h.m.generation == gen + 1
    assert of(out, PublishControl)[0].payload["active_app"] == "autonomous"
    assert events(out, "drive.mode.changed") == [{"from": "AUTO_READY", "to": "AUTO", "generation": gen + 1, "reason": "resume_requested"}]
    # 다음 자율 명령(새 세대)이 전달된다
    out = h.run(PERIOD)
    assert [f.app_id for f in of(out, ForwardDrive)] == ["autonomous"]


def test_resume_ignored_outside_auto_ready():
    h = failed_over()
    gen = h.m.generation
    h.m.on_resume(h.now)
    out = h.run(PERIOD)
    assert h.m.mode is Mode.MANUAL and h.m.generation == gen
    assert not events(out, "drive.mode.changed")


def test_stale_generation_and_standby_commands_are_not_forwarded():
    h = running_auto()
    gen = h.m.generation
    h.now += PERIOD
    h.cmd(AUTONOMOUS, generation=gen - 1, v=1.0)
    h.cmd(MANUAL_APP, generation=gen, v=0.7)        # 대기 앱
    assert not of(h.step(), ForwardDrive)
    assert h.m.apps[AUTONOMOUS].dropped_stale_generation == 1
    h.now += PERIOD
    h.cmd(AUTONOMOUS, generation=gen, v=0.5)
    assert of(h.step(), ForwardDrive) == [ForwardDrive("autonomous", gen, 0.5, 0.0)]


def test_stale_generation_still_counts_as_alive():
    """세대가 달라도 유효한 명령이면 앱은 살아 있다 (전환 직후 새 활성 앱이 바로 끊김 판정되지 않게)."""
    h = running_auto()
    h.run(0.12, apps=(MANUAL_APP,))
    assert h.m.mode is Mode.MANUAL
    old = h.m.generation - 1
    for _ in range(10):
        h.now += PERIOD
        h.cmd(MANUAL_APP, generation=old)
        h.step()
    assert h.m.mode is Mode.MANUAL


def test_latest_only_forwards_one_command_per_step():
    h = running_auto()
    gen = h.m.generation
    h.now += PERIOD
    for v in (0.1, 0.2, 0.3):
        h.cmd(AUTONOMOUS, v=v)
    assert of(h.step(), ForwardDrive) == [ForwardDrive("autonomous", gen, 0.3, 0.0)]


@pytest.mark.parametrize("payload", [
    {"generation": 1, "v_mps": "1", "steer_rad": 0.0},
    {"generation": True, "v_mps": 0.0, "steer_rad": 0.0},
    {"generation": 1, "v_mps": float("nan"), "steer_rad": 0.0},
    {"v_mps": 0.0, "steer_rad": 0.0},
    [],
])
def test_invalid_command_is_not_alive(payload):
    m = ControlMachine()
    assert m.on_command(AUTONOMOUS, AUTO_VM, payload, 0.0) is False
    assert m.apps[AUTONOMOUS].last_command_t is None


def test_safe_stop_start_mode_sends_stop_and_waits_for_manual():
    h = Harness(start_mode=Mode.SAFE_STOP)
    h.cmd(AUTONOMOUS)
    h.start()
    assert of(h.actions, ForwardStop)
    h.run(0.5, apps=(AUTONOMOUS,))
    assert h.m.mode is Mode.SAFE_STOP   # 자동 출발 없음
    h.run(PERIOD, apps=(AUTONOMOUS, MANUAL_APP))
    assert h.m.mode is Mode.MANUAL
