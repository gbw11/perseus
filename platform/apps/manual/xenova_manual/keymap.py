"""drive.input 의 눌린 키 목록을 목표 속도·조향각으로 바꾼다 (MQTT 계약 v1 7장 키 이름).

부호는 ROS(REP-103)와 같다: 전진 +, 후진 -, 왼쪽 조향 +, 오른쪽 조향 -.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

KEYS = frozenset({"up", "down", "left", "right", "brake"})


@dataclass(frozen=True)
class Target:
    v_mps: float = 0.0
    steer_rad: float = 0.0


STOP = Target()


def to_target(keys: Iterable[object], *, max_speed_mps: float, max_reverse_mps: float, max_steer_rad: float) -> Target:
    """반대 방향 키가 같이 눌리면 그 축은 0. `brake` 가 있으면 속도는 0 (조향은 유지)."""
    pressed = {key for key in keys if isinstance(key, str) and key in KEYS}
    if "brake" in pressed:
        v = 0.0
    else:
        v = (max_speed_mps if "up" in pressed else 0.0) - (max_reverse_mps if "down" in pressed else 0.0)
        if "up" in pressed and "down" in pressed:
            v = 0.0
    steer = (max_steer_rad if "left" in pressed else 0.0) - (max_steer_rad if "right" in pressed else 0.0)
    return Target(v_mps=v, steer_rad=steer)
