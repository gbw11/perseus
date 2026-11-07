"""관리 콘솔 서킷 10개와 공유 Jetson 데모 6개를 시뮬레이터 JSON으로 병합한다.

화면 목업과 시뮬레이터가 같은 중심선을 써야 탑뷰·출발선·벽이 맞는다. tracks.ts 를 고치면 이 스크립트를 다시 돌린다.

    python tools/export_tracks.py            # → xenova_simulator/tracks.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[3] / "frontend" / "apps" / "admin-console" / "src" / "mocks" / "tracks.ts"
TARGET = HERE.parent / "xenova_simulator" / "tracks.json"
JETSON_SOURCE = HERE.parents[3] / "frontend/packages/ui/src/sim/jetson-tracks.json"

_ENTRY = re.compile(
    r"id:\s*'(?P<id>[^']+)',\s*name:\s*'(?P<name>[^']+)',\s*short:\s*'(?P<short>[^']+)',\s*real_m:\s*(?P<real>\d+),"
    r".*?centerline:\s*(?P<cl>\[\[.*?\]\])",
    re.S,
)


def parse(text: str) -> list[dict]:
    tracks = []
    for m in _ENTRY.finditer(text):
        centerline = json.loads(m["cl"])
        if len(centerline) < 10 or not all(len(p) == 2 for p in centerline):
            raise ValueError(f"{m['id']}: 중심선 형식")
        tracks.append({"id": m["id"], "name": m["name"], "short": m["short"], "real_m": int(m["real"]),
                       "centerline": centerline})
    return tracks


def main() -> int:
    tracks = parse(SOURCE.read_text(encoding="utf-8"))
    if len(tracks) != 10:
        print(f"트랙 {len(tracks)}개 — tracks.ts 형식이 바뀌었나?", file=sys.stderr)
        return 1
    demo_tracks = json.loads(JETSON_SOURCE.read_text(encoding="utf-8"))
    if len(demo_tracks) != 6 or len({t["id"] for t in tracks + demo_tracks}) != 16:
        raise ValueError("Jetson 데모 6개와 기존 서킷 10개의 id는 중복되지 않아야 합니다")
    tracks.extend(demo_tracks)
    header = {"source": "frontend/apps/admin-console/src/mocks/tracks.ts",
              "additional_source": "frontend/packages/ui/src/sim/jetson-tracks.json",
              "license": "TUM racetrack-database 중심선 (LGPL-3.0, © OpenStreetMap contributors), 1/50 축소"}
    TARGET.write_text(json.dumps({**header, "tracks": tracks}, ensure_ascii=False, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    print(f"{TARGET.name}: " + ", ".join(f"{t['id']}({len(t['centerline'])})" for t in tracks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
