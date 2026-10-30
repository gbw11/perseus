"""OpenCV 색 기반 인식. webcam·video 입력에서만 쓴다 (extra `camera`).

장애물: HSV 빨강·진한 주황(교통 콘) 마스크 → 덩어리(연결 요소) → 박스 아랫변을 지면에 투영.
차선:   흰색·노란색 마스크 (수평선 아래만) → 선 덩어리(연결 요소)마다 픽셀을 지면에 투영해 y = f(x) (2차) 맞춤
        → 차 앞 0.3 m 에서 가장 가까운 왼쪽(y>0)·오른쪽(y<0) 선의 가운데(한쪽만 보이면 차로 폭/2 만큼 옮김)를 차선 중심 c(x).
        lane_offset_m = −c(0), heading_err_rad = atan((c(0.6) − c(0)) / 0.6). 선이 안 보이면 둘 다 0.

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
LANE_MAX_SAMPLES = 600       # 선 덩어리 하나에서 투영할 최대 픽셀 수 (넘으면 고르게 솎는다)
LANE_MIN_POINTS = 30
LANE_X_RANGE = (0.15, 2.0)   # 이 전방 거리 안의 차선 점만 쓴다 (멀리는 투영 오차가 크다)
LANE_QUADRATIC_SPAN_M = 0.6  # 선이 이만큼 길게 보이면 2차, 짧으면 직선으로 맞춘다
LANE_SIDE_X = 0.3            # 왼쪽·오른쪽 선을 가르는 전방 거리
LANE_LOOKAHEAD_M = 0.6       # heading_err 를 재는 전방 주시 거리


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


def _fit(xs: np.ndarray, ys: np.ndarray) -> np.ndarray | None:
    """y = c0 + c1·x (+ c2·x²) 최소제곱 계수 (np.polyval 순서). 점이 적거나 x 가 한 곳에 몰리면 None.

    곡선 차로를 따라가려면 2차가 필요하지만, 짧게 보이는 선에 2차를 맞추면 끝이 튄다 → x 폭이 좁으면 직선.
    """
    if len(xs) < LANE_MIN_POINTS or float(np.ptp(xs)) < 0.1:
        return None
    degree = 2 if float(np.ptp(xs)) >= LANE_QUADRATIC_SPAN_M else 1
    return np.polyfit(xs, ys, degree)


def lane_lines(mask: np.ndarray, projector: Projector) -> list[np.ndarray]:
    """흰·노란 선 덩어리마다 지면 곡선 하나를 맞춘다. 덩어리로 나누는 이유: 곡선에서는 오른쪽 선이
    차 앞에서 왼쪽(y > 0)으로 넘어가므로 y 부호로 점을 나누면 두 선이 섞인다."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    lines: list[np.ndarray] = []
    for i in range(1, count):  # 0 은 배경
        if stats[i, cv2.CC_STAT_AREA] < LANE_MIN_POINTS:
            continue
        vs, us = np.nonzero(labels == i)
        step = max(1, len(vs) // LANE_MAX_SAMPLES)
        xs, ys, valid = projector.to_ground_many(us[::step].astype(float), vs[::step].astype(float))
        keep = valid & (xs >= LANE_X_RANGE[0]) & (xs <= LANE_X_RANGE[1])
        coeffs = _fit(xs[keep], ys[keep])
        if coeffs is not None:
            lines.append(coeffs)
    return lines


def find_lane(hsv: np.ndarray, projector: Projector, lane_width_m: float) -> Lane | None:
    """차선 추정. 못 찾으면 None (payload 에는 0 으로 나간다).

    차 바로 앞(x = LANE_SIDE_X)에서 y 가 양수인 선 중 가장 가까운 것을 왼쪽, 음수 중 가장 가까운 것을 오른쪽으로 본다.
    차로 중심 c(x) = 두 선의 가운데 (한쪽만 보이면 차로 폭/2 만큼 옮김).
    lane_offset_m = −c(0), heading_err_rad = atan((c(L) − c(0)) / L), L = LANE_LOOKAHEAD_M.
    → 곡선이면 heading 에 앞쪽 굽음이 들어가 자율 앱이 미리 꺾는다.
    """
    mask = _mask(hsv, COLORS.lane)
    top = max(0, int(math.ceil(projector.horizon_v())) + 1)
    if top >= mask.shape[0]:
        return None
    mask[:top] = 0
    left = right = None
    for coeffs in lane_lines(mask, projector):
        y = float(np.polyval(coeffs, LANE_SIDE_X))
        if y > 0 and (left is None or y < left[0]):
            left = (y, coeffs)
        elif y < 0 and (right is None or y > right[0]):
            right = (y, coeffs)
    if left is None and right is None:
        return None

    def centre(x: float) -> float:
        if left and right:
            return (float(np.polyval(left[1], x)) + float(np.polyval(right[1], x))) / 2
        if left:
            return float(np.polyval(left[1], x)) - lane_width_m / 2
        return float(np.polyval(right[1], x)) + lane_width_m / 2

    c0 = centre(0.0)
    return Lane(offset_m=-c0, heading_err_rad=math.atan2(centre(LANE_LOOKAHEAD_M) - c0, LANE_LOOKAHEAD_M))


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
