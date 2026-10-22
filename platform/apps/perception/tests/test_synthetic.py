from __future__ import annotations

import pytest

from xenova_perception.config import PerceptionSettings, StopZone
from xenova_perception.geometry import payload
from xenova_perception.synthetic import SCENARIOS, SyntheticSource


def phases(scenario: str, step: float = 0.1) -> list[str]:
    """시나리오 한 주기를 훑어 상태가 바뀌는 순서를 돌려준다: clear / far / stop."""
    source = SyntheticSource(PerceptionSettings(scenario=scenario), clock=lambda: 0.0)
    period = SCENARIOS[scenario][0]
    seen: list[str] = []
    for i in range(round(period / step)):
        r = source.at(i * step)
        state = "stop" if r.stop else ("far" if r.obstacles else "clear")
        if not seen or seen[-1] != state:
            seen.append(state)
    return seen


def test_approach_order():
    assert phases("approach") == ["clear", "far", "stop", "clear"]


def test_cut_in_order():
    assert phases("cut-in") == ["clear", "far", "stop", "clear"]


def test_clear_never_stops():
    assert phases("clear") == ["clear"]


def test_approach_stop_starts_when_obstacle_enters_zone():
    source = SyntheticSource(PerceptionSettings(), clock=lambda: 0.0)
    before, after = source.at(6.3), source.at(6.5)
    assert before.obstacles[0].x > 0.8 and before.stop is False
    assert after.obstacles[0].x < 0.8 and after.stop is True


def test_scenario_repeats():
    source = SyntheticSource(PerceptionSettings(), clock=lambda: 0.0)
    assert payload(source.at(3.0)) == payload(source.at(15.0))


def test_current_uses_elapsed_time_from_start():
    now = [100.0]
    source = SyntheticSource(PerceptionSettings(), clock=lambda: now[0])
    assert source.current(101.0).obstacles == ()      # 1 s: 빈 길
    assert source.current(107.0).stop is True          # 7 s: 0.5 m 앞
    assert source.current(107.0).at == 107.0


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        SyntheticSource(PerceptionSettings(scenario="nope"))


def test_stop_zone_setting_changes_stop():
    near_zone = PerceptionSettings(stop_zone=StopZone(distance_m=0.3))
    source = SyntheticSource(near_zone, clock=lambda: 0.0)
    assert source.at(7.0).stop is False  # 0.5 m 앞이지만 정지 거리 0.3 m
