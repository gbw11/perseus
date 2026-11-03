"""OpenCV 색 기반 인식. webcam·video 입력에서만 쓴다 (extra `camera`).

장애물: HSV 빨강·진한 주황(교통 콘) 마스크 → 덩어리(연결 요소) → 박스 아랫변을 지면에 투영.
차선:   흰색·노란색 마스크 (수평선 아래만) → 선 덩어리(연결 요소)마다 픽셀을 지면에 투영
        → 덩어리마다 왼쪽·오른쪽·버림을 따져 같은 중심의 원호 두 줄(차로 중심 ± 차로 폭/2) 모델을 맞춤 → 차선 중심 c(x).
        lane_offset_m = −c(0), heading_err_rad = atan((c(0.6) − c(0)) / 0.6). 선이 안 보이면 둘 다 0.

학습 모델이 아니라 색 임계값이라 조명·배경에 약하다. 시연할 때는 빨간 콘·흰 테이프처럼 색이 분명한 것을 쓴다.
"""

from __future__ import annotations

import itertools
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
LANE_FIT_SAMPLES = 200       # 선 덩어리 하나에서 투영할 최대 픽셀 수 (넘으면 고르게 솎는다. 가설마다 맞추므로 적게)
LANE_MIN_POINTS = 30
LANE_MIN_SPAN_M = 0.1        # 선 덩어리의 전방 길이가 이보다 짧으면 버린다
LANE_X_RANGE = (0.15, 2.0)   # 이 전방 거리 안의 차선 점만 쓴다 (멀리는 투영 오차가 크다)
LANE_QUADRATIC_SPAN_M = 0.6  # 초기값: 선이 이만큼 길게 보이면 2차, 짧으면 직선으로 굽음을 읽는다
LANE_MAX_LINES = 3           # 왼쪽·오른쪽·버림을 모두 따져 보는 선 덩어리 수 (큰 것부터)
LANE_FIT_TAU_M = 0.03        # 잔차를 이 값에서 자른다 (다른 선·잡음이 모델을 끌지 않게)
LANE_FIT_ITERATIONS = 8
LANE_MAX_PSI_RAD = 1.2
LANE_MAX_CURVATURE = 3.0     # 1/m. 반경 0.33 m 보다 급한 곡선은 없다고 본다
LANE_HALF_RANGE_M = (0.1, 1.0)  # 양쪽 선이 다 보일 때 맞추는 차로 폭/2 의 범위
# 사전 지식 (점 하나당 벌점의 제곱근, 값 1 m 또는 1 rad 또는 1/m 기준): 차는 차로 안에서 차로 방향을 대체로 따라 달린다.
# 선이 차 앞 0.4 m 부터만 보여 c0·psi·kappa 가 서로 맞바뀌는(같은 점을 똑같이 잘 설명하는) 경우 덜 치우친 쪽을 고르게 한다.
# 데이터가 분명히 정하는 값은 몇 % 안쪽으로만 끌린다
LANE_PRIOR = (0.1, 0.05, 0.005)  # (c0, psi, kappa)
LANE_LOOKAHEAD_M = 0.6       # heading_err 를 재는 전방 주시 거리
# 앞 프레임 이어 보기 (S15P31E110-360): 가설을 고를 때만 앞 프레임 c0·psi 와 많이 다른 가설에 벌점을 준다 (맞춘 값은 끌지 않는다).
# 한 프레임(1/15 s, 0.5 m/s) 사이 c0·psi 는 몇 cm·0.05 rad 남짓만 바뀌는데, 머리핀 출구처럼 선이 짧게 보이면 반대쪽 가설이
# 근소하게 이겨 heading 이 0.6 rad 넘게 뒤집히곤 했다 (catalunya 폐루프 이탈). 벌점 상한이 있어 증거가 분명하면 바뀐다
LANE_TRACK_SIGMA = (0.2, 0.25)   # (c0 m, psi rad) — 벌점 = 전체 점 수 × τ² × Σ(차이/σ)²
LANE_TRACK_MAX = 4.0             # 벌점 상한 (전체 점을 네 번 버리는 만큼)
LANE_TRACK_GAP_S = 0.5           # 앞 프레임이 이보다 오래됐으면 이어 보지 않는다


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


def lane_lines(mask: np.ndarray, projector: Projector) -> list[tuple[np.ndarray, np.ndarray]]:
    """흰·노란 선 덩어리마다 지면 점 (x, y) 를 낸다. 큰 덩어리부터 LANE_MAX_LINES 개까지. 덩어리로 나누는 이유:
    곡선에서는 오른쪽 선이 차 앞에서 왼쪽(y > 0)으로 넘어가므로 y 부호로 점을 나누면 두 선이 섞인다."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    lines: list[tuple[np.ndarray, np.ndarray]] = []
    for i in sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA]):  # 0 은 배경
        if stats[i, cv2.CC_STAT_AREA] < LANE_MIN_POINTS or len(lines) == LANE_MAX_LINES:
            break
        vs, us = np.nonzero(labels == i)
        step = max(1, len(vs) // LANE_FIT_SAMPLES)
        xs, ys, valid = projector.to_ground_many(us[::step].astype(float), vs[::step].astype(float))
        keep = valid & (xs >= LANE_X_RANGE[0]) & (xs <= LANE_X_RANGE[1])
        xs, ys = xs[keep], ys[keep]
        if len(xs) >= LANE_MIN_POINTS and float(np.ptp(xs)) >= LANE_MIN_SPAN_M:
            lines.append((xs, ys))
    return lines


@dataclass(frozen=True)
class LaneFit:
    """차로 중심을 원호로 본 모델 (차 기준). x = 0 에서 가로 위치 c0, 방향 psi(왼쪽 +), 곡률 kappa(1/m, 왼쪽 +).
    양쪽 선은 중심에서 ±half 떨어진 같은 중심의 원호다. cost 는 가설끼리 견주는 값 (작을수록 좋다)."""

    c0: float
    psi: float
    kappa: float
    half: float
    cost: float

    def centre(self, x: float) -> float:
        """전방 x 에서 중심선의 가로 위치. 원호가 x 까지 못 가면(반경 < x) 가장 멀리 간 곳의 값."""
        # 원 위의 점 d = (x, dy): n·d − kappa·|d|²/2 = 0, n = (−sin psi, cos psi) → dy 의 2차식. 곡률 0 에서도 안정한 근
        cos, k = math.cos(self.psi), self.kappa
        c = x * math.sin(self.psi) + k * x * x / 2
        return self.c0 + 2 * c / (cos + math.sqrt(max(0.0, cos * cos - 2 * k * c)))


def _fit_arc(xs: np.ndarray, ys: np.ndarray, side: np.ndarray, half: float, free_half: bool, n_prior: int) -> LaneFit:
    """점마다 왼쪽(+1)·오른쪽(−1) 선이라고 정해 두고 원호 모델을 가우스-뉴턴(Huber 가중)으로 맞춘다.

    원호 모델에서 점 d = (x, y − c0) 의 중심선 횡거리 o 는 o(1 − kappa·o/2) = n·d − kappa·|d|²/2 를 만족한다
    (곡률 0 이면 직선). 잔차 = 그 차이 / (1 − kappa·o) ≈ 선까지의 거리. 양쪽 선이 다 있으면 half(차로 폭/2)도 맞춘다.
    n_prior = 사전 지식의 무게 (점 수). 가설끼리 비용을 견줄 수 있게 맞추지 않은 점까지 센 전체 점 수를 준다.
    """
    # 초기값: 점이 많은 쪽 선에 y = a + b·x (+ c·x²) 를 맞춰 x = 0 의 방향·곡률을 읽는다
    main_side = 1.0 if np.count_nonzero(side > 0) >= np.count_nonzero(side < 0) else -1.0
    main = side == main_side
    degree = 2 if float(np.ptp(xs[main])) >= LANE_QUADRATIC_SPAN_M else 1
    coeffs = np.polyfit(xs[main], ys[main], degree)
    c2, b, a = coeffs if degree == 2 else (0.0, *coeffs)
    psi = math.atan(b)
    kappa = float(np.clip(2 * c2 / (1 + b * b) ** 1.5, -LANE_MAX_CURVATURE, LANE_MAX_CURVATURE))
    p = np.array([a - main_side * half / math.cos(psi), psi, kappa, half])
    cols = 4 if free_half else 3
    prior = np.zeros((3, cols))
    prior[:, :3] = np.diag(LANE_PRIOR) * math.sqrt(n_prior)

    def residual(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c0, psi, k, h = p
        o = side * h
        dy = ys - c0
        sin, cos = math.sin(psi), math.cos(psi)
        den = np.maximum(1 - k * o, 0.2)
        r = (-xs * sin + dy * cos - k * (xs * xs + dy * dy) / 2 - o * (1 - k * o / 2)) / den
        jac = np.stack([(-cos + k * dy) / den, (-xs * cos - dy * sin) / den,
                        (o * o - xs * xs - dy * dy) / 2 / den, -side], axis=1)
        return r, jac

    for _ in range(LANE_FIT_ITERATIONS):
        r, jac = residual(p)
        w = np.sqrt(np.minimum(1.0, LANE_FIT_TAU_M / np.maximum(np.abs(r), 1e-9)))
        # 사전 지식은 c0·psi·kappa 를 0 으로 당기는 가짜 관측 세 줄
        rows = np.vstack([jac[:, :cols] * w[:, None], prior])
        rhs = np.concatenate([-r * w, -prior[:, :3] @ p[:3]])
        step = np.linalg.lstsq(rows, rhs, rcond=None)[0]
        p[:cols] += step
        p[1] = float(np.clip(p[1], -LANE_MAX_PSI_RAD, LANE_MAX_PSI_RAD))
        p[2] = float(np.clip(p[2], -LANE_MAX_CURVATURE, LANE_MAX_CURVATURE))
        p[3] = float(np.clip(p[3], *LANE_HALF_RANGE_M))
        if float(np.max(np.abs(step))) < 1e-5:
            break
    r, _ = residual(p)
    cost = float(np.sum(np.minimum(r * r, LANE_FIT_TAU_M ** 2)) + np.sum((prior[:, :3] @ p[:3]) ** 2))
    return LaneFit(float(p[0]), float(p[1]), float(p[2]), float(p[3]), cost)


def fit_lane(lines: list[tuple[np.ndarray, np.ndarray]], lane_width_m: float,
             prev: LaneFit | None = None) -> LaneFit | None:
    """선 덩어리마다 왼쪽·오른쪽·버림을 모두 따져 보고(3^n − 1 가설) 비용이 가장 작은 원호 모델을 고른다.

    비용 = 맞춘 점의 잘린 잔차² + 버린 점마다 τ² + 사전 지식 벌점(LANE_PRIOR). 사전 지식의 c0 항이 한쪽 선만 보일 때
    왼쪽·오른쪽을 가른다: 같은 선을 어느 쪽으로 보든 잔차는 같으니 차가 차로 안(|c0| 작음)에 있는 쪽을 고른다.
    급코너에서 바깥 선 하나만 보여도 선 전체의 굽음으로 방향·곡률을 읽어, 먼 쪽 짧은 선을 2차식으로 차 앞까지 늘리지 않는다.
    prev(앞 프레임 결과)가 있으면 그와 c0·psi 가 많이 다른 가설에 고르기 벌점(LANE_TRACK_*)을 더한다. 반환 cost 에는 넣지 않는다.
    """
    if not lines:
        return None
    total = sum(len(xs) for xs, _ in lines)
    best: LaneFit | None = None
    best_pick = math.inf
    for sides in itertools.product((1, -1, 0), repeat=len(lines)):
        used = [(xs, ys, s) for (xs, ys), s in zip(lines, sides) if s]
        if not used:
            continue
        xs = np.concatenate([u[0] for u in used])
        ys = np.concatenate([u[1] for u in used])
        side = np.concatenate([np.full(len(u[0]), float(u[2])) for u in used])
        fit = _fit_arc(xs, ys, side, lane_width_m / 2, free_half=1 in sides and -1 in sides, n_prior=total)
        cost = fit.cost + (total - len(xs)) * LANE_FIT_TAU_M ** 2
        pick = cost
        if prev is not None:
            jump = ((fit.c0 - prev.c0) / LANE_TRACK_SIGMA[0]) ** 2 + ((fit.psi - prev.psi) / LANE_TRACK_SIGMA[1]) ** 2
            pick += total * LANE_FIT_TAU_M ** 2 * min(LANE_TRACK_MAX, jump)
        if pick < best_pick:
            best_pick = pick
            best = LaneFit(fit.c0, fit.psi, fit.kappa, fit.half, cost)
    return best


def find_lane(hsv: np.ndarray, projector: Projector, lane_width_m: float) -> Lane | None:
    fit = find_lane_fit(hsv, projector, lane_width_m)
    return None if fit is None else lane_of(fit)


def lane_of(fit: LaneFit) -> Lane:
    c0 = fit.centre(0.0)
    return Lane(offset_m=-c0, heading_err_rad=math.atan2(fit.centre(LANE_LOOKAHEAD_M) - c0, LANE_LOOKAHEAD_M))


def find_lane_fit(hsv: np.ndarray, projector: Projector, lane_width_m: float,
                  prev: LaneFit | None = None) -> LaneFit | None:
    """차선 추정. 못 찾으면 None (payload 에는 0 으로 나간다).

    선 덩어리들을 함께 원호 모델로 맞춰(fit_lane) 차로 중심 c(x) 를 얻는다. 한쪽만 보이면 그 선에서 차로 폭/2 안쪽의 원호.
    lane_offset_m = −c(0), heading_err_rad = atan((c(L) − c(0)) / L), L = LANE_LOOKAHEAD_M.
    → 곡선이면 heading 에 앞쪽 굽음이 들어가 자율 앱이 미리 꺾는다.
    """
    mask = _mask(hsv, COLORS.lane)
    top = max(0, int(math.ceil(projector.horizon_v())) + 1)
    if top >= mask.shape[0]:
        return None
    mask[:top] = 0
    return fit_lane(lane_lines(mask, projector), lane_width_m, prev)


class Detector:
    def __init__(self, settings: PerceptionSettings) -> None:
        self._settings = settings
        self._projector: Projector | None = None
        self._prev: tuple[float, LaneFit] | None = None  # (시각, 앞 프레임 차선) — 가설 고르기 이어 보기

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
        prev = self._prev[1] if self._prev and 0 <= at - self._prev[0] <= LANE_TRACK_GAP_S else None
        fit = find_lane_fit(hsv, projector, self._settings.lane_width_m, prev)
        self._prev = None if fit is None else (at, fit)
        lane = None if fit is None else lane_of(fit)
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
