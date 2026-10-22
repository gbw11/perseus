"""OpenCV 가 있을 때만: numpy 로 그린 합성 프레임에서 실제 탐지 경로를 시험한다."""

from __future__ import annotations

import math

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from xenova_perception.config import PerceptionSettings  # noqa: E402
from xenova_perception.detect import Detector, draw  # noqa: E402

W, H = 640, 480
SETTINGS = PerceptionSettings()


def blank() -> np.ndarray:
    frame = np.full((H, W, 3), 60, dtype=np.uint8)  # 회색 바닥
    frame[: H // 3] = (120, 90, 40)                  # 위쪽은 하늘색 계열 배경
    return frame


def red_box(frame: np.ndarray, x: float, y: float, w: float = 0.2, height_px: int = 40) -> np.ndarray:
    """지면 (x, y) 에 폭 w 의 빨간 물체를 그린다 (아랫변을 투영값에 맞춤)."""
    p = Detector(SETTINGS).projector(W, H)
    ul, vb = p.to_image(x, y + w / 2)
    ur, _ = p.to_image(x, y - w / 2)
    cv2.rectangle(frame, (int(round(ul)), int(round(vb)) - height_px), (int(round(ur)), int(round(vb))), (0, 0, 255), -1)
    return frame


def white_line(frame: np.ndarray, y: float, x0: float = 0.2, x1: float = 2.0, offset_heading: float = 0.0) -> np.ndarray:
    p = Detector(SETTINGS).projector(W, H)
    a = p.to_image(x0, y + math.tan(offset_heading) * x0)
    b = p.to_image(x1, y + math.tan(offset_heading) * x1)
    cv2.line(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (255, 255, 255), 6)
    return frame


def test_red_box_ahead_is_detected_and_stops():
    r = Detector(SETTINGS).detect(red_box(blank(), 0.5, 0.0), at=1.0)
    assert len(r.obstacles) == 1
    o = r.obstacles[0]
    assert o.x == pytest.approx(0.5, abs=0.03)
    assert o.y == pytest.approx(0.0, abs=0.02)
    assert o.w == pytest.approx(0.2, abs=0.03)
    assert r.stop is True


def test_left_and_right_boxes_have_correct_sign():
    frame = red_box(red_box(blank(), 1.2, 0.35), 1.2, -0.35)
    r = Detector(SETTINGS).detect(frame, at=1.0)
    ys = sorted(o.y for o in r.obstacles)
    assert len(ys) == 2
    assert ys[0] == pytest.approx(-0.35, abs=0.03) and ys[1] == pytest.approx(0.35, abs=0.03)
    assert r.stop is False


def test_empty_frame_has_no_obstacle_and_zero_lane():
    r = Detector(SETTINGS).detect(blank(), at=1.0)
    assert r.obstacles == () and r.stop is False
    assert (r.lane.offset_m, r.lane.heading_err_rad) == (0.0, 0.0)
    assert r.extra["lane_found"] is False


def test_lane_offset_from_two_white_lines():
    # 차선 중심이 차 기준 왼쪽 0.05 m → 차는 중심보다 오른쪽 → lane_offset_m = -0.05
    frame = white_line(white_line(blank(), 0.225), -0.125)
    r = Detector(SETTINGS).detect(frame, at=1.0)
    assert r.extra["lane_found"] is True
    assert r.lane.offset_m == pytest.approx(-0.05, abs=0.03)
    assert r.lane.heading_err_rad == pytest.approx(0.0, abs=0.05)


def test_draw_returns_same_size_frame():
    frame = red_box(blank(), 0.5, 0.0)
    detector = Detector(SETTINGS)
    r = detector.detect(frame, at=1.0)
    out = draw(frame, r, detector.projector(W, H), SETTINGS)
    assert out.shape == frame.shape
