from __future__ import annotations

import pytest

from xenova_manual.keymap import STOP, Target, to_target

LIMITS = {"max_speed_mps": 1.0, "max_reverse_mps": 0.5, "max_steer_rad": 0.4}


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ([], STOP),
        (["up"], Target(1.0, 0.0)),
        (["down"], Target(-0.5, 0.0)),
        (["left"], Target(0.0, 0.4)),
        (["right"], Target(0.0, -0.4)),
        (["up", "left"], Target(1.0, 0.4)),
        (["up", "down"], Target(0.0, 0.0)),
        (["left", "right"], Target(0.0, 0.0)),
        (["up", "brake", "right"], Target(0.0, -0.4)),
        (["up", "up"], Target(1.0, 0.0)),
    ],
)
def test_keys(keys, expected):
    assert to_target(keys, **LIMITS) == expected


def test_unknown_and_non_string_keys_are_ignored():
    assert to_target(["ArrowUp", "w", 3, None, {"k": 1}], **LIMITS) == STOP
    assert to_target(["ArrowUp", "up"], **LIMITS) == Target(1.0, 0.0)
