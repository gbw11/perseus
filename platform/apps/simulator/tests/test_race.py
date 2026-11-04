"""출발 신호·랩 시간 (계약 §12 sim.pose.race, 시나리오 P7). 관리 콘솔 mock(mocks/sim.ts newRace·advance)과 같은 규칙."""

from __future__ import annotations

import pytest

from xenova_simulator.race import Race

N, S0 = 400, 100


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


def lights_at(race, clock, t, **kw):
    clock.now = 10.0 + t
    race.update(S0, S0, N, manual=kw.get("manual", False), forward=kw.get("forward", False))
    return race.payload()


def test_lights_one_per_second_then_out_after_random_hold(clock):
    race = Race(clock, rng=lambda: 0.5)   # 꺼지는 순간 = 0.7 + 4 + 0.2 + 0.5 × 2.8 = 6.3 s
    assert race.payload() == {"phase": "lights", "lights_on": 0, "lap": 0, "lap_ms": 0,
                              "last_lap_ms": None, "best_lap_ms": None, "jump_start": False}
    assert [lights_at(race, clock, t)["lights_on"] for t in (0.69, 0.7, 1.7, 2.7, 3.7, 4.7, 6.29)] == [0, 1, 2, 3, 4, 5, 5]
    assert race.holds(manual=False) and not race.holds(manual=True)
    p = lights_at(race, clock, 6.3)
    assert p["phase"] == "go" and p["lights_on"] == 0 and p["lap_ms"] == 0
    clock.now += 1.234
    assert race.payload()["lap_ms"] == 1234
    assert not race.holds(manual=False)


@pytest.mark.parametrize("r, out_s", [(0.0, 4.9), (1.0, 7.7)])
def test_lights_out_between_0_2_and_3_0_s_after_fifth(clock, r, out_s):
    race = Race(clock, rng=lambda: r)
    assert lights_at(race, clock, out_s - 0.001)["phase"] == "lights"
    assert lights_at(race, clock, out_s)["phase"] == "go"


def run_lap(race, clock, seconds, start=0):
    """출발선 → 반 바퀴 → 출발선을 seconds 동안 지나간다 (중심선 점을 고르게 밟는다)."""
    steps = 200
    for k in range(1, steps + 1):
        clock.now += seconds / steps
        race.update((S0 + start + k * N // steps) % N, S0, N, manual=False, forward=True)


def test_lap_time_from_start_line_last_and_best(clock):
    race = Race(clock, lights=False)
    assert race.phase == "go"
    run_lap(race, clock, 60.0)
    p = race.payload()
    assert p["lap"] == 1 and p["last_lap_ms"] == 60000 and p["best_lap_ms"] == 60000
    run_lap(race, clock, 50.0)
    run_lap(race, clock, 55.0)
    p = race.payload()
    assert p["lap"] == 3 and p["last_lap_ms"] == 55000 and p["best_lap_ms"] == 50000


def test_crossing_start_without_half_lap_is_not_a_lap(clock):
    race = Race(clock, lights=False)
    for idx in (S0 - 5, S0 - 1, S0 + 1, S0 - 2, S0 + 3):   # 출발선 앞뒤로 왔다 갔다
        clock.now += 1.0
        race.update(idx % N, S0, N, manual=False, forward=True)
    assert race.payload()["lap"] == 0


def test_manual_forward_while_lights_on_is_jump_start_plus_5s_first_lap(clock):
    race = Race(clock, rng=lambda: 0.0)
    lights_at(race, clock, 0.5, manual=True, forward=True)       # 아직 불이 하나도 안 켜짐 → 부정 출발 아님
    assert race.payload()["jump_start"] is False
    lights_at(race, clock, 1.0, manual=False, forward=True)      # 자율(잡혀 있음)은 부정 출발이 아니다
    assert race.payload()["jump_start"] is False
    lights_at(race, clock, 2.0, manual=True, forward=True)
    assert race.payload()["jump_start"] is True
    lights_at(race, clock, 4.9)
    run_lap(race, clock, 40.0)
    run_lap(race, clock, 40.0)
    p = race.payload()
    assert p["last_lap_ms"] == 40000 and p["best_lap_ms"] == 40000      # 둘째 바퀴는 벌칙 없음
    assert race.last == pytest.approx(40.0) and p["jump_start"] is True


def test_jump_penalty_on_first_lap(clock):
    race = Race(clock, rng=lambda: 0.0)
    lights_at(race, clock, 1.0, manual=True, forward=True)
    lights_at(race, clock, 4.9)
    run_lap(race, clock, 40.0)
    assert race.payload()["last_lap_ms"] == 45000


def test_restart_clears_laps_and_jump(clock):
    race = Race(clock, rng=lambda: 0.0)
    lights_at(race, clock, 1.0, manual=True, forward=True)
    lights_at(race, clock, 4.9)
    run_lap(race, clock, 40.0)
    race.restart()
    p = race.payload()
    assert p == {"phase": "lights", "lights_on": 0, "lap": 0, "lap_ms": 0,
                 "last_lap_ms": None, "best_lap_ms": None, "jump_start": False}
