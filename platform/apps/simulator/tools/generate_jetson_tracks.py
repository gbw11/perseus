"""측정 전 Jetson 데모 코스 6종의 공유 geometry를 생성한다.

좌표는 metre, world XY-up이다. 중심선은 도로 가운데 분리선이며 시계 방향이다.
이 데이터는 주차장 표현용이고 차량 기동 가능성이나 실측 보정을 보증하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE.parents[3] / "frontend/packages/ui/src/sim/jetson-tracks.json"
VARIANTS = (("jetson-demo", .60), ("jetson-r45", .45), ("jetson-r55", .55),
            ("jetson-r65", .65), ("jetson-r75", .75), ("jetson-r80", .80))
SAMPLE_M = .05


def _point(x: float, y: float) -> list[float]:
    return [round(x, 6), round(y, 6)]


def generate(track_id: str, inner_radius: float) -> dict:
    """외곽 4.5×3 m를 고정하고 반경에 맞춰 직선 길이를 조절한다."""
    if not .2 < inner_radius <= .8:
        raise ValueError("4.5×3 m 안의 두 차로 코스는 안쪽 차로 중심 반경이 0.2 m 초과, 0.8 m 이하여야 합니다")
    width, height = 4.3, 2.8
    outer = inner_radius + .6
    radius = inner_radius + .2
    tx, cy = .1 + outer, .1 + outer
    horizontal = round(width - 2 * outer, 9)
    vertical = round(height - 2 * outer, 9)
    rx, top_cy = tx + horizontal, cy + vertical
    # 반경 비교에서 출발 조건이 바뀌지 않도록 기본 코스의 좌표를 공통으로 쓴다.
    start_x = 2.56
    points = [_point(start_x, .5)]

    def line(x: float, y: float) -> None:
        ax, ay = points[-1]
        distance = math.hypot(x - ax, y - ay)
        if distance < 1e-9:  # R80에서는 세로 직선이 사라진다.
            return
        count = max(1, math.ceil(distance / SAMPLE_M))
        points.extend(_point(ax + (x - ax) * i / count, ay + (y - ay) * i / count)
                      for i in range(1, count + 1))

    def arc(cx: float, center_y: float, a: float, b: float) -> None:
        count = max(1, math.ceil(radius * abs(b - a) / SAMPLE_M))
        points.extend(_point(cx + radius * math.cos(a + (b - a) * i / count),
                             center_y + radius * math.sin(a + (b - a) * i / count))
                      for i in range(1, count + 1))

    line(tx, .5)
    arc(tx, cy, -math.pi / 2, -math.pi)
    line(tx - radius, top_cy)
    arc(tx, top_cy, math.pi, math.pi / 2)
    line(rx, top_cy + radius)
    arc(rx, top_cy, math.pi / 2, 0)
    line(rx + radius, cy)
    arc(rx, cy, 0, -math.pi / 2)
    line(start_x, .5)
    points.pop()  # 마지막 점은 첫 점과 같으므로 고리 연결에만 사용한다.
    lap = sum(math.dist(a, points[(i + 1) % len(points)]) for i, a in enumerate(points))
    left, right = 2.7, 3.5
    # 큰 코너에서도 두 칸이 안마당에 들어가도록 10 cm 위로 옮긴다.
    mouth = 1.2 if inner_radius > .65 else 1.1
    back = mouth + .6
    inner_edge = inner_radius - .2
    right_join_y = cy - math.sqrt(inner_edge ** 2 - max(0., right - rx) ** 2)
    slots = []
    for i in range(2):
        bx = left + i * .4
        slots.append({"id": f"P{i + 1}", "outline": [_point(bx, mouth), _point(bx, back),
                      _point(bx + .4, back), _point(bx + .4, mouth)]})
    apron = [_point(left, .885), _point(right, .885), _point(right, mouth), _point(left, mouth)]
    layout = {
        "bounds": [0, 0, round(width + .2, 6), round(height + .2, 6)],
        "lane_count": 2,
        "start_pose": [round(start_x + (.30 + .20) / 2, 6), .7, math.pi],
        "paved_areas": [apron, *[slot["outline"] for slot in slots]],
        "paint_lines": [*[slot["outline"] + [slot["outline"][0]] for slot in slots],
                        [_point(left, mouth), _point(left, .9)],
                        [_point(right, mouth), _point(right, right_join_y)]],
        "parking_slots": slots,
        "obstacle_seeds": [
            {"id": "A", "kind": "cone", "x": 1.92, "y": .7, "w": .18, "h": .18},
            {"id": "B", "kind": "cone", "x": 1.4, "y": .3, "w": .18, "h": .18},
        ],
        "vehicle": {"length_m": .30, "width_m": .22, "wheelbase_m": .20, "min_turn_radius_m": .55},
        "camera": {"height_m": .12, "pitch_rad": math.pi / 12, "hfov_rad": 69 * math.pi / 180,
                   "mount_x_m": .20},
        "assumptions": (f"Illustrative unmeasured geometry; inner lane centre radius {inner_radius:.2f} m; "
                        "both lanes follow the clockwise centreline order. Vehicle and camera values are "
                        "provisional, not measured calibration. Symmetric front/rear overhang is assumed; "
                        "the rear-axle initial pose places the front bumper on the start line. "
                        "All courses share 4.5 by 3 m bounds, the start pose and obstacle coordinates; "
                        "straight lengths change with the corner radius. R0.75 and R0.80 parking bays "
                        "are shifted 0.10 m north to stay inside the infield; "
                        "the parking entrance lies behind the camera at the initial pose. "
                        "Obstacle placement and parking manoeuvres "
                        "are not physically validated; no autonomous parking planner is added."),
    }
    role = "기본" if track_id == "jetson-demo" else "비교"
    name = f"Jetson {role} 코스 (R{round(inner_radius * 100)}cm)"
    return {"id": track_id, "name": name, "short": track_id,
            "real_m": round(lap), "centerline": points,
            "lane_width_m": .8, "curb_m": 0, "layout": layout}


def generated_bytes() -> bytes:
    return (json.dumps([generate(*variant) for variant in VARIANTS], ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="기존 생성 파일과 비교만 한다")
    args = parser.parse_args()
    content = generated_bytes()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != content:
            print(f"생성 파일이 최신이 아닙니다: {TARGET}")
            return 1
    else:
        TARGET.write_bytes(content)
    print(f"{TARGET.name}: {len(VARIANTS)} tracks, {len(content)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
