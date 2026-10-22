"""OpenCV 색 기반 인식. webcam·video 입력에서만 쓴다 (extra `camera`).

장애물: HSV 빨강·진한 주황(교통 콘) 마스크 → 덩어리(연결 요소) → 박스 아랫변을 지면에 투영.
차선:   흰색·노란색 마스크 (수평선 아래만) → 픽셀을 지면에 투영 → 왼쪽(y>0)·오른쪽(y<0) 점에 직선 y = a + b·x 맞춤
        → 두 선의 가운데(한쪽만 보이면 차로 폭/2 만큼 옮김)를 차선 중심으로 본다.
        lane_offset_m = −(중심의 x=0 에서 y), heading_err_rad = atan(b). 선이 안 보이면 둘 다 0.

학습 모델이 아니라 색 임계값이라 조명·배경에 약하다. 시연할 때는 빨간 콘·흰 테이프처럼 색이 분명한 것을 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .config import PerceptionSettings
from .geometry import Lane, Obstacle, Projector, Result, make_result, obstacle_from_box


@dataclass(frozen=True)
class ColorRanges:
    # OpenCV HSV: H 0~179, S·V 0~255. 빨강은 H 가 0 근처와 179 근처 두 구간에 걸친다
    obstacle: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
        ((0, 120, 70), (15, 255, 255)),
        ((165, 120, 70), (179, 255, 255)),
    )
    # 노랑은 주황 콘(H ≤ 15)과 겹치지 않게 20 부터
    lane: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
        ((0, 0, 200), (179, 40, 255)),     # 흰색
        ((20, 80, 120), (35, 255, 255)),   # 노란색
    )


COLORS = ColorRanges()
MIN_OBSTACLE_AREA = 0.001   # 화면 넓이 대비. 작은 점은 잡음으로 버린다
LANE_ROWS_STEP = 4          # 차선 픽셀은 이 간격으로만 뽑아 맞춘다
LANE_MIN_POINTS = 30
LANE_X_RANGE = (0.15, 2.5)  # 이 전방 거리 안의 차선 점만 쓴다 (멀리는 투영 오차가 크다)


def _mask(hsv: np.ndarray, ranges) -> np.ndarray:
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for low, high in ranges:
        mask |= cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
    return mask


def find_obstacles(hsv: np.ndarray, projector: Projector) -> tuple[list[Obstacle], list[tuple[int, int, int, int]]]:
    """(장애물, 화면 박스 (x, y, w, h)). 박스는 지면에 닿지 않아(수평선 위) 버린 것도 포함한다."""
    mask = _mask(hsv, COLORS.obstacle)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area = MIN_OBSTACLE_AREA * mask.shape[0] * mask.shape[1]
    obstacles: list[Obstacle] = []
    boxes: list[tuple[int, int, int, int]] = []
    for i in range(1, count):  # 0 은 배경
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < min_area:
            continue
        boxes.append((x, y, w, h))
        obstacle = obstacle_from_box(projector, x, x + w, y + h)
        if obstacle is not None:
            obstacles.append(obstacle)
    return obstacles, boxes


def _fit(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float] | None:
    """y = a + b·x 최소제곱. 점이 적거나 x 가 한 곳에 몰리면 None."""
    if len(xs) < LANE_MIN_POINTS or float(np.ptp(xs)) < 0.1:
        return None
    b, a = np.polyfit(xs, ys, 1)
    return float(a), float(b)


def find_lane(hsv: np.ndarray, projector: Projector, lane_width_m: float) -> Lane | None:
    """차선 추정. 못 찾으면 None (payload 에는 0 으로 나간다)."""
    mask = _mask(hsv, COLORS.lane)
    top = max(0, int(math.ceil(projector.horizon_v())) + 1)
    if top >= mask.shape[0]:
        return None
    vs, us = np.nonzero(mask[top::LANE_ROWS_STEP, :])
    if len(vs) == 0:
        return None
    vs = vs * LANE_ROWS_STEP + top
    xs, ys, valid = projector.to_ground_many(us.astype(float), vs.astype(float))
    keep = valid & (xs >= LANE_X_RANGE[0]) & (xs <= LANE_X_RANGE[1])
    xs, ys = xs[keep], ys[keep]
    left = _fit(xs[ys > 0], ys[ys > 0])
    right = _fit(xs[ys < 0], ys[ys < 0])
    if left and right:
        a, b = (left[0] + right[0]) / 2, (left[1] + right[1]) / 2
    elif left:
        a, b = left[0] - lane_width_m / 2, left[1]
    elif right:
        a, b = right[0] + lane_width_m / 2, right[1]
    else:
        return None
    return Lane(offset_m=-a, heading_err_rad=math.atan(b))


class Detector:
    def __init__(self, settings: PerceptionSettings) -> None:
        self._settings = settings
        self._projector: Projector | None = None

    def projector(self, width: int, height: int) -> Projector:
        p = self._projector
        if p is None or (p.width, p.height) != (width, height):
            p = self._projector = Projector(self._settings.camera, width, height)
        return p

    def detect(self, frame_bgr: np.ndarray, at: float) -> Result:
        height, width = frame_bgr.shape[:2]
        projector = self.projector(width, height)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        obstacles, boxes = find_obstacles(hsv, projector)
        lane = find_lane(hsv, projector, self._settings.lane_width_m)
        return make_result(
            lane or Lane(), obstacles, self._settings.stop_zone, at,
            limit=self._settings.max_obstacles, boxes=boxes, lane_found=lane is not None,
        )


def draw(frame_bgr: np.ndarray, result: Result, projector: Projector, settings: PerceptionSettings) -> np.ndarray:
    """미리보기: 탐지 박스, 정지 영역, 수평선, 결과 글자."""
    out = frame_bgr.copy()
    for x, y, w, h in result.extra.get("boxes", []):
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 255), 2)
    zone = settings.stop_zone
    corners = [(0.05, zone.half_width_m), (zone.distance_m, zone.half_width_m),
               (zone.distance_m, -zone.half_width_m), (0.05, -zone.half_width_m)]
    pts = [projector.to_image(x, y) for x, y in corners]
    if all(p is not None for p in pts):
        poly = np.array([[int(u), int(v)] for u, v in pts], dtype=np.int32)
        cv2.polylines(out, [poly], True, (0, 0, 255) if result.stop else (0, 200, 0), 2)
    hv = int(projector.horizon_v())
    if 0 <= hv < out.shape[0]:
        cv2.line(out, (0, hv), (out.shape[1], hv), (255, 128, 0), 1)
    lane = "lane " + (f"{result.lane.offset_m:+.2f}m {math.degrees(result.lane.heading_err_rad):+.0f}deg"
                      if result.extra.get("lane_found") else "-")
    lines = ["STOP" if result.stop else "go", lane] + [
        f"obs x={o.x:.2f} y={o.y:+.2f} w={o.w:.2f}" for o in result.obstacles[:4]
    ]
    for i, text in enumerate(lines):
        cv2.putText(out, text, (8, 22 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(out, text, (8, 22 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return out
