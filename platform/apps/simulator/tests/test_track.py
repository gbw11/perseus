"""서킷 트랙: 관리 콘솔 mock(simEngine.ts buildTrack)과 같은 계산인지, 계약 v1.4 §11 sim.world.track 형식인지."""

from __future__ import annotations

import json
import math

import pytest

from xenova_simulator.track import CURB_M, LANE_WIDTH_M, ROAD_M, load, sources, track_ids

T = load("spielberg")


def test_ten_tracks_from_the_console_mock():
    assert track_ids() == ["spielberg", "monza", "silverstone", "spa", "melbourne",
                           "hungaroring", "catalunya", "mexico", "bahrain", "interlagos"]
    assert len(sources()["spa"].centerline) == 681  # 계약 v1.4: 가장 긴 스파 681점


def test_unknown_track_is_rejected():
    with pytest.raises(ValueError):
        load("suzuka")


def test_start_pose_is_on_centreline_facing_forward():
    x, y, heading = T.start_pose()
    i, off, _ = T.frame(x, y)
    assert i == T.start_index and off == pytest.approx(0.0)
    tx, ty = T.tan[i]
    assert heading == pytest.approx(math.atan2(ty, tx))
    assert not T.corner[i]  # 출발선은 가장 긴 직선 위


def test_offset_is_positive_to_the_left():
    x, y, heading = T.start_pose()
    left = (x - 0.2 * math.sin(heading), y + 0.2 * math.cos(heading))
    _, off, _ = T.frame(*left, hint=T.start_index)
    assert off == pytest.approx(0.2, abs=1e-6)


def test_walls_are_outside_the_road_and_start_straight_has_no_grass():
    for wl, wr in zip(T.wall_left_m, T.wall_right_m):
        assert ROAD_M < wl <= ROAD_M + 0.68 + 1e-9 and ROAD_M < wr <= ROAD_M + 0.68 + 1e-9
    assert T.wall_left_m[T.start_index] == pytest.approx(ROAD_M + 0.03, abs=0.01)


def test_surface_road_grass_dirt():
    i = max(range(T.N), key=lambda k: abs(T.turn[k]))  # 가장 급한 코너
    outside = -1 if T.turn[i] > 0 else 1
    assert T.surface(i, 0.0) == "road" and T.surface(i, ROAD_M) == "road"
    assert T.surface(i, outside * (ROAD_M + 0.1)) == "dirt"
    assert T.surface(i, -outside * (ROAD_M + 0.1)) == "grass"


def test_progress_wraps_once_per_lap():
    x, y, _ = T.start_pose()
    assert T.progress(x, y) == pytest.approx(0.0, abs=1e-6)
    j = (T.start_index + T.N // 2) % T.N
    assert 0 < T.progress(*T.C[j]) < T.lap_m


def test_world_track_payload_matches_contract_v14():
    p = T.payload()
    assert p["shape"] == "centerline" and p["id"] == "spielberg" and p["real_m"] == 4315
    assert (p["lane_width_m"], p["curb_m"]) == (LANE_WIDTH_M, CURB_M) == (0.6, 0.06)
    assert len(p["centerline"]) == len(p["wall_left_m"]) == len(p["wall_right_m"]) == T.N
    assert 0 <= p["start_index"] < T.N
    assert len(json.dumps(load("spa").payload())) < 64 * 1024  # sim.world 1장 64 KB 이하
