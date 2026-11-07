"""브로커 없이 실제 코스 데이터의 탑뷰와 인지용 카메라 PNG를 생성한다.

정지한 두 가상 관측 자세를 렌더링한다. 주행·회피·주차 성공을 재생하는 도구는 아니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
for package in (ROOT / "platform/apps/simulator", ROOT / "platform/services/vm-agent/mqtt-client",
                ROOT / "platform/services/gateway"):
    sys.path.insert(0, str(package))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from xenova_simulator.camera import ASPHALT, LINE, OBSTACLE, Renderer  # noqa: E402
from xenova_simulator.config import SimSettings  # noqa: E402
from xenova_simulator.sim import Simulator  # noqa: E402
from xenova_simulator.simtopics import SimTopics  # noqa: E402
from xenova_simulator.track import track_ids  # noqa: E402


def top_view(sim: Simulator) -> np.ndarray:
    """생성된 코스 geometry와 실제 world 장애물·rear-axle pose를 내려다본다."""
    track, layout = sim.track, sim.track.layout
    x0, y0, x1, y1 = layout["bounds"]
    width, height, pad = 960, 800, 48
    scale = min((width - 2 * pad) / (x1 - x0), (height - 120) / (y1 - y0))
    image = np.full((height, width, 3), (24, 26, 29), np.uint8)

    def pixel(point):
        return (round((point[0] - x0) * scale + pad), round(70 + (y1 - point[1]) * scale))

    def points(polygon):
        return np.asarray([pixel(p) for p in polygon], np.int32)

    half = track.lane_width_m / 2
    for i in range(track.N):
        j = (i + 1) % track.N
        quad = [track.at(i, half), track.at(j, half), track.at(j, -half), track.at(i, -half)]
        cv2.fillPoly(image, [points(quad)], ASPHALT)
    stroke = max(2, round(.025 * scale))
    for side in (half, -half):
        cv2.polylines(image, [points([track.at(i, side) for i in range(track.N)])], True, LINE, stroke)
    for polygon in layout["paved_areas"]:
        cv2.fillPoly(image, [points(polygon)], ASPHALT)
    for line in layout["paint_lines"]:
        cv2.polylines(image, [points(line)], False, LINE, stroke)
    for i in range(track.N):
        if track.SL[i] % .5 < .25:
            cv2.line(image, pixel(track.C[i]), pixel(track.C[(i + 1) % track.N]), LINE, stroke)
    si = track.start_index
    cv2.line(image, pixel(track.at(si, half)), pixel(track.at(si, -half)), (80, 220, 245), stroke)
    for obstacle in sim.vehicle.obstacles:
        x, y, radius = obstacle.x, obstacle.y, obstacle.w / 2
        cv2.fillPoly(image, [points([(x-radius,y-radius),(x+radius,y-radius),
                                    (x+radius,y+radius),(x-radius,y+radius)])], OBSTACLE[obstacle.kind][0])
        cv2.putText(image, obstacle.id, pixel((x, y + radius + .04)), cv2.FONT_HERSHEY_SIMPLEX,
                    .55, LINE, 1, cv2.LINE_AA)
    vehicle, pose = layout["vehicle"], sim.vehicle
    direction = np.asarray([math.cos(pose.heading_rad), math.sin(pose.heading_rad)])
    normal = np.asarray([-direction[1], direction[0]])
    center = np.asarray([pose.x, pose.y]) + direction * vehicle["wheelbase_m"] / 2
    body = [center + a * direction * vehicle["length_m"] / 2 + b * normal * vehicle["width_m"] / 2
            for a, b in ((1,1),(1,-1),(-1,-1),(-1,1))]
    cv2.fillPoly(image, [points(body)], (225, 150, 60))
    cv2.arrowedLine(image, pixel(center), pixel(center + direction * .25), LINE, 2, tipLength=.3)
    cv2.putText(image, track.id + " | clockwise | two 0.40m lanes", (pad, 30),
                cv2.FONT_HERSHEY_SIMPLEX, .72, LINE, 1, cv2.LINE_AA)
    cv2.putText(image, "Vehicle Rmin 0.55m (assumed) | static preview, not a driving result", (pad, height-20),
                cv2.FONT_HERSHEY_SIMPLEX, .53, LINE, 1, cv2.LINE_AA)
    return image


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", default="jetson-demo", choices=[t for t in track_ids() if t.startswith("jetson-")])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sim = Simulator(object(), SimSettings(track_id=args.track, camera_listen=None, start_lights=False))
    renderer = Renderer(sim.settings.camera)
    layout = sim.track.layout
    # 오른쪽 칸 정면을 보는 별도 가상 자세다. 출발점에서 이 자세까지의 경로를 주장하지 않는다.
    slot = layout["parking_slots"][1]["outline"]
    survey = (sum(p[0] for p in slot) / len(slot), .4, math.pi / 2)
    images = {"top.png": top_view(sim),
              "start.png": renderer.render(sim.track, *sim.track.start_pose(), sim.vehicle.obstacles),
              "parking.png": renderer.render(sim.track, *survey, sim.vehicle.obstacles)}
    for filename, frame in images.items():
        if not cv2.imwrite(str(output / filename), frame):
            raise RuntimeError(f"PNG 기록 실패: {filename}")
    world = SimTopics(sim, object()).world()
    (output / "world.json").write_text(json.dumps(world, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = {"track": args.track, "static_preview_only": True, "start_pose": sim.track.start_pose(),
              "parking_survey_pose": survey, "assumptions": layout["assumptions"],
              "numpy": np.__version__, "opencv": cv2.__version__,
              "images": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in images}}
    (output / "preview.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "images": list(images), "track": args.track}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
