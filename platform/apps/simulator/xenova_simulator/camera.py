"""전방 카메라: 차 앞에 단 카메라에 보일 화면을 그려 HTTP MJPEG 로 내준다.

젯슨(인지)에는 이 화면만 간다. 트랙 모양·차 위치·장애물 좌표는 주지 않는다 (실차와 같은 조건).
- 투영은 평평한 지면·롤 0·정사각 픽셀 핀홀 모델이고, 인지 대역 `xenova_perception.geometry.Projector` 와 같은 식이다.
  카메라 높이·숙인 각·화각이 양쪽에서 같아야 거리가 맞는다 (기본 0.15 m / 15° / 60°).
- 그리는 것 (계약 v1.4 서킷): 하늘, 아스팔트 차선(폭 0.6 m), 차선 양쪽 흰 선, 연석(0.06 m), 벽까지 잔디·코너 바깥 흙,
  벽 밖 콘크리트, 먼 곳 안개, 장애물 상자(콘 주황·박스 빨강·타이어 검붉은색).
  연석은 실제처럼 빨강·흰 줄이 아니라 파랑으로 칠한다 — 색으로 장애물(빨강)·차선(흰색)을 찾는 인지 대역이 헷갈리지 않게.
- endpoint (설계서 8.4 의 이름을 따름): `GET /camera/stream.mjpg` (multipart/x-mixed-replace), `GET /camera/snapshot.jpg`.
  마지막 프레임이 0.5 s 보다 오래되면 snapshot 은 503 이다 (멈춘 화면을 실시간처럼 주지 않는다).

트랙마다 한 번 "오프셋 지도"(2 cm 격자마다 중심선 횡방향 거리·벽 거리·흙 여부)를 만들어 두고, 매 프레임은 픽셀의 지면 좌표로
그 지도를 `cv2.remap` 한다 (서킷 중심선 400~680점을 픽셀마다 찾지 않는다).

numpy·OpenCV 가 필요하다 (`pip install -e .[camera]`). 물리·MQTT 경로는 없이도 돈다.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

from .config import CameraSpec
from .model import Obstacle
from .track import Track

log = logging.getLogger("xenova_simulator")

# BGR
SKY = (235, 206, 150)
FOG = (175, 175, 170)
ASPHALT = (70, 70, 72)
LINE = (245, 245, 245)
CURB = (190, 110, 40)
GRASS = (60, 120, 50)
DIRT = (110, 125, 135)      # 채도를 낮춰 빨강(장애물)·노랑(차선) 색 범위에 안 들게
BARRIER = (120, 120, 125)
OBSTACLE = {"cone": ((0, 90, 255), (60, 140, 255)), "box": ((40, 40, 210), (70, 70, 230)),
            "tires": ((30, 30, 150), (50, 50, 170))}
STALE_S = 0.5
_MIN_DOWN = 1e-3           # 인지 대역 geometry._MIN_DOWN 과 같다
_NEAR_M = 0.05             # 이보다 가까운(또는 뒤의) 꼭짓점이 있으면 그 장애물은 그리지 않는다
_OUTSIDE = 99.0


@dataclass
class OffsetMap:
    """트랙 고정 좌표 격자. off = 중심선 횡방향 거리(+ 왼쪽), wall = 그쪽 벽까지 거리, dirt = 코너 바깥 흙."""

    x0: float
    y0: float
    res: float
    off: np.ndarray
    wall: np.ndarray
    dirt: np.ndarray


def build_offset_map(track: Track, res: float) -> OffsetMap:
    margin = max(max(track.wall_left_m), max(track.wall_right_m)) + 1.0
    x0, y0, x1, y1 = track.bounds(margin)
    w, h = int(math.ceil((x1 - x0) / res)) + 1, int(math.ceil((y1 - y0) / res)) + 1
    # 중심선을 격자 반 칸 간격으로 촘촘히 찍고, 점마다 위치·법선·원래 점 번호를 기억한다
    sx, sy, nx, ny, seg = [], [], [], [], []
    for i in range(track.N):
        j = (i + 1) % track.N
        (ax, ay), (bx, by) = track.C[i], track.C[j]
        (anx, any_), (bnx, bny) = track.nor[i], track.nor[j]
        steps = max(1, int(math.hypot(bx - ax, by - ay) / (res / 2)))
        for k in range(steps):
            t = k / steps
            vx, vy = anx + (bnx - anx) * t, any_ + (bny - any_) * t
            n = math.hypot(vx, vy) or 1.0
            sx.append(ax + (bx - ax) * t)
            sy.append(ay + (by - ay) * t)
            nx.append(vx / n)
            ny.append(vy / n)
            seg.append(i if t < 0.5 else j)
    sx, sy, nx, ny, seg = map(np.array, (sx, sy, nx, ny, seg))
    col = np.clip(np.round((sx - x0) / res).astype(np.int64), 0, w - 1)
    row = np.clip(np.round((sy - y0) / res).astype(np.int64), 0, h - 1)
    mask = np.ones((h, w), np.uint8)
    sample = np.full((h, w), -1, np.int64)
    mask[row, col] = 0
    sample[row, col] = np.arange(len(sx))
    # 칸마다 가장 가까운 중심선 칸: 거리 변환의 픽셀 label → 그 칸에 찍은 점 번호
    _, labels = cv2.distanceTransformWithLabels(mask, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    zero = mask == 0
    lut = np.zeros(int(labels.max()) + 1, np.int64)
    lut[labels[zero]] = sample[zero]
    nearest = lut[labels]
    gy, gx = np.mgrid[0:h, 0:w]
    px, py = x0 + gx * res, y0 + gy * res
    off = (px - sx[nearest]) * nx[nearest] + (py - sy[nearest]) * ny[nearest]
    i = seg[nearest]
    wall = np.where(off > 0, np.array(track.wall_left_m)[i], np.array(track.wall_right_m)[i])
    outside = np.where(np.array(track.turn)[i] > 0, -1.0, 1.0)
    dirt = np.array(track.corner)[i] & (np.sign(off) == outside)
    return OffsetMap(x0, y0, res, off.astype(np.float32), wall.astype(np.float32), dirt.astype(np.uint8))


class Renderer:
    """차 자세 → 카메라 화면 (BGR uint8, height × width × 3). 트랙별 오프셋 지도는 처음 쓸 때 만든다."""

    def __init__(self, spec: CameraSpec) -> None:
        self.spec = spec
        w, h = spec.width, spec.height
        self.f = (w / 2) / math.tan(spec.hfov_rad / 2)
        self.cx, self.cy = w / 2, h / 2
        self._sin, self._cos = math.sin(spec.pitch_rad), math.cos(spec.pitch_rad)
        # 픽셀마다 지면 교점을 카메라 기준 (전방 x, 왼쪽 y) 로 한 번만 구해 둔다
        v, u = np.mgrid[0:h, 0:w].astype(np.float64)
        d = (v - self.cy) / self.f
        down = self._sin + d * self._cos
        self.ground = down > _MIN_DOWN
        t = spec.height_m / np.where(self.ground, down, 1.0)
        self.gx = t * (self._cos - d * self._sin)
        self.gy = -t * (u - self.cx) / self.f
        self.ground &= self.gx <= spec.max_range_m
        self.sky = down <= _MIN_DOWN
        self._maps: dict[str, OffsetMap] = {}
        self._lock = threading.Lock()

    def offset_map(self, track: Track) -> OffsetMap:
        with self._lock:
            m = self._maps.get(track.id)
            if m is None:
                m = self._maps[track.id] = build_offset_map(track, self.spec.raster_m)
            return m

    def camera_position(self, x: float, y: float, heading: float) -> tuple[float, float]:
        """후륜 축 중심 자세 → 카메라 바로 아래 지면 위치."""
        m = self.spec.mount_x_m
        return x + m * math.cos(heading), y + m * math.sin(heading)

    def render(self, track: Track, x: float, y: float, heading: float, obstacles: Sequence[Obstacle] = ()) -> np.ndarray:
        cam_x, cam_y = self.camera_position(x, y, heading)
        c, s = math.cos(heading), math.sin(heading)
        m = self.offset_map(track)
        mx = ((cam_x + self.gx * c - self.gy * s - m.x0) / m.res).astype(np.float32)
        my = ((cam_y + self.gx * s + self.gy * c - m.y0) / m.res).astype(np.float32)
        off = np.abs(cv2.remap(m.off, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=_OUTSIDE))
        wall = cv2.remap(m.wall, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        dirt = cv2.remap(m.dirt, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0

        half, line = track.lane_width_m / 2, self.spec.line_width_m
        g = self.ground
        image = np.empty((self.spec.height, self.spec.width, 3), dtype=np.uint8)
        image[:] = FOG
        image[self.sky] = SKY
        image[g & (off > wall)] = BARRIER
        inside = g & (off <= wall)
        image[inside & ~dirt] = GRASS
        image[inside & dirt] = DIRT
        image[g & (off <= track.road_m)] = CURB
        image[g & (off <= half)] = ASPHALT
        image[g & (off <= half) & (off >= half - line)] = LINE

        # 먼 장애물부터 그려 가까운 것이 가린다
        for o in sorted(obstacles, key=lambda o: -math.hypot(o.x - cam_x, o.y - cam_y)):
            self._draw_box(image, o, cam_x, cam_y, c, s)
        return image

    def _project(self, fx: float, fy: float, z: float) -> tuple[float, float] | None:
        """카메라 기준 (전방, 왼쪽, 높이) → 픽셀. 카메라 앞이 아니면 None."""
        below = self.spec.height_m - z
        depth = fx * self._cos + below * self._sin
        if depth <= _NEAR_M:
            return None
        down = -fx * self._sin + below * self._cos
        return self.cx + self.f * (-fy) / depth, self.cy + self.f * down / depth

    def _draw_box(self, image: np.ndarray, o: Obstacle, cam_x: float, cam_y: float, c: float, s: float) -> None:
        half = o.w / 2
        bottom, top = [], []
        for ox, oy in ((-half, -half), (half, -half), (half, half), (-half, half)):
            dx, dy = o.x + ox - cam_x, o.y + oy - cam_y
            fx, fy = dx * c + dy * s, -dx * s + dy * c
            for z, out in ((0.0, bottom), (o.h, top)):
                p = self._project(fx, fy, z)
                if p is None:
                    return
                out.append(p)
        side, lid = OBSTACLE.get(o.kind, OBSTACLE["box"])
        hull = cv2.convexHull(np.array(bottom + top, dtype=np.float32)).astype(np.int32)
        cv2.fillConvexPoly(image, hull, side, lineType=cv2.LINE_AA)
        cv2.fillConvexPoly(image, cv2.convexHull(np.array(top, dtype=np.float32)).astype(np.int32), lid,
                           lineType=cv2.LINE_AA)


def encode_jpeg(image: np.ndarray, quality: int) -> bytes:
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG 인코딩 실패")
    return data.tobytes()


class FrameHub:
    """마지막 JPEG 한 장과 순번. 시청자마다 다시 그리거나 다시 인코딩하지 않는다."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._at = 0.0
        self.seq = 0

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg, self._at = jpeg, self._clock()
            self.seq += 1
            self._cond.notify_all()

    def current(self) -> tuple[bytes | None, float, int]:
        """(jpeg, 나이 s, 순번)."""
        with self._cond:
            return self._jpeg, self._clock() - self._at, self.seq

    def wait_next(self, after: int, timeout: float) -> tuple[bytes | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self.seq > after, timeout=timeout)
            return self._jpeg if self.seq > after else None, self.seq


def make_handler(hub: FrameHub, stop: threading.Event) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - http.server 이름
            log.debug("camera %s " + fmt, self.address_string(), *args)

        def do_GET(self) -> None:  # noqa: N802 - http.server 이름
            path = self.path.split("?", 1)[0]
            if path == "/camera/snapshot.jpg":
                self._snapshot()
            elif path == "/camera/stream.mjpg":
                self._stream()
            else:
                self.send_error(404)

        def _snapshot(self) -> None:
            jpeg, age, _ = hub.current()
            if jpeg is None or age > STALE_S:
                self.send_error(503, "no fresh frame")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            seq = hub.seq
            try:
                while not stop.is_set():
                    jpeg, seq = hub.wait_next(seq, timeout=1.0)
                    if jpeg is None:
                        continue  # 새 프레임이 없으면 옛 프레임을 다시 보내지 않는다
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # 시청자가 끊었다. 렌더링·주행에는 영향 없음
            self.close_connection = True

    return Handler


class CameraService:
    """카메라 스레드(일정 fps 로 렌더·인코딩) + HTTP 서버."""

    def __init__(
        self,
        spec: CameraSpec,
        pose: Callable[[], tuple[Track, float, float, float, Sequence[Obstacle]]],
        host: str,
        port: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.spec = spec
        self.renderer = Renderer(spec)
        self.hub = FrameHub(clock)
        self._pose = pose
        self._clock = clock
        self._stop = threading.Event()
        self._http = ThreadingHTTPServer((host, port), make_handler(self.hub, self._stop))
        self._http.daemon_threads = True
        self.address = self._http.server_address[:2]
        self._threads = [
            threading.Thread(target=self._render_loop, name="camera-render", daemon=True),
            threading.Thread(target=self._http.serve_forever, kwargs={"poll_interval": 0.2}, name="camera-http", daemon=True),
        ]
        self.rendered = 0

    def start(self) -> None:
        for thread in self._threads:
            thread.start()
        log.info("전방 카메라 http://%s:%d/camera/stream.mjpg (%dx%d %.0f fps)",
                 *self.address, self.spec.width, self.spec.height, self.spec.fps)

    def stop(self) -> None:
        self._stop.set()
        self._http.shutdown()
        self._http.server_close()
        for thread in self._threads:
            thread.join(timeout=1.0)

    def render_once(self) -> bytes:
        track, x, y, heading, obstacles = self._pose()
        jpeg = encode_jpeg(self.renderer.render(track, x, y, heading, obstacles), self.spec.jpeg_quality)
        self.hub.publish(jpeg)
        self.rendered += 1
        return jpeg

    def _render_loop(self) -> None:
        period = 1.0 / self.spec.fps
        deadline = self._clock()
        while not self._stop.is_set():
            try:
                self.render_once()
            except Exception:  # 카메라 오류가 주행 시뮬레이션을 멈추면 안 된다
                log.exception("카메라 프레임 실패")
            deadline += period
            delay = deadline - self._clock()
            if delay < -period:
                deadline = self._clock()  # 늦으면 몰아서 그리지 않는다
            elif delay > 0:
                self._stop.wait(delay)
