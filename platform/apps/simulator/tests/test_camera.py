"""전방 카메라: 그린 화면을 인지 대역(xenova_perception)이 읽으면 실제 위치와 맞아야 한다. OpenCV 가 있을 때만."""

from __future__ import annotations

import math
import threading
import time
import urllib.error
import urllib.request

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from xenova_perception.config import PerceptionSettings  # noqa: E402
from xenova_perception.detect import Detector  # noqa: E402
from xenova_simulator.camera import ASPHALT, BARRIER, CURB, LINE, SKY, CameraService, Renderer  # noqa: E402
from xenova_simulator.config import SimSettings  # noqa: E402
from xenova_simulator.model import Obstacle  # noqa: E402
from xenova_simulator.track import load  # noqa: E402

S = SimSettings()
T = load("spielberg")


@pytest.fixture(scope="module")
def renderer():
    return Renderer(S.camera)


@pytest.fixture(scope="module")
def detector():
    return Detector(PerceptionSettings())


def start():
    return T.start_pose()


def ahead(x, y, h, d, side=0.0):
    """자세 (x, y, h)에서 앞으로 d, 왼쪽으로 side 떨어진 점."""
    return x + d * math.cos(h) - side * math.sin(h), y + d * math.sin(h) + side * math.cos(h)


def test_frame_layers_on_start_straight(renderer):
    x, y, h = start()
    image = renderer.render(T, x, y, h)
    assert image.shape == (S.camera.height, S.camera.width, 3) and image.dtype == np.uint8
    colours = {tuple(int(c) for c in px) for px in image.reshape(-1, 3)}
    assert {SKY, ASPHALT, LINE, CURB, BARRIER} <= colours  # 출발 직선은 잔디 없이 바로 벽
    assert tuple(int(c) for c in image[-1, S.camera.width // 2]) == ASPHALT


def test_centred_car_sees_centred_lane(renderer, detector):
    r = detector.detect(renderer.render(T, *start()), 0.0)
    assert r.extra["lane_found"] is True
    assert r.lane.offset_m == pytest.approx(0.0, abs=0.03)
    assert r.lane.heading_err_rad == pytest.approx(0.0, abs=0.05)


@pytest.mark.parametrize("side", [0.1, -0.1])
def test_lane_offset_sign_follows_car_position(renderer, detector, side):
    x, y, h = start()
    r = detector.detect(renderer.render(T, *ahead(x, y, h, 0.0, side), h), 0.0)
    assert r.lane.offset_m == pytest.approx(side, abs=0.04)


def test_obstacle_distance_matches_front_face(renderer, detector):
    x, y, h = start()
    cam = renderer.camera_position(x, y, h)
    box = Obstacle.of("box", *ahead(*cam, h, 1.5))
    r = detector.detect(renderer.render(T, x, y, h, [box]), 0.0)
    assert len(r.obstacles) == 1
    o = r.obstacles[0]
    assert o.x == pytest.approx(1.5 - box.w / 2, abs=0.1)   # 인지는 바닥에 닿은 앞면까지 잰다
    assert o.y == pytest.approx(0.0, abs=0.03)
    # 상자 바닥은 트랙 좌표축에 맞춰 놓여 비스듬히 보이면 대각선 폭(최대 √2 배)까지 넓어진다
    assert box.w - 0.03 <= o.w <= box.w * math.sqrt(2) + 0.03
    assert r.stop is False


@pytest.mark.parametrize("kind", ["cone", "box", "tires"])
def test_every_obstacle_kind_is_seen_and_close_one_stops(renderer, detector, kind):
    x, y, h = start()
    cam = renderer.camera_position(x, y, h)
    r = detector.detect(renderer.render(T, x, y, h, [Obstacle.of(kind, *ahead(*cam, h, 0.6))]), 0.0)
    assert r.obstacles and r.stop is True


def test_corners_turn_heading_the_right_way(renderer, detector):
    # 급한 왼쪽·오른쪽 코너 한가운데에 중심선 위로 세우면 heading 부호가 굽는 쪽과 같아야 한다
    for sign in (1, -1):
        i = max(range(T.N), key=lambda k: sign * T.turn[k])
        x, y = T.C[i]
        h = math.atan2(T.tan[i][1], T.tan[i][0])
        r = detector.detect(renderer.render(T, x, y, h), 0.0)
        assert r.extra["lane_found"] is True
        assert sign * r.lane.heading_err_rad > 0.05, (sign, r.lane)


def test_obstacle_behind_camera_is_not_drawn(renderer):
    x, y, h = start()
    plain = renderer.render(T, x, y, h)
    assert np.array_equal(plain, renderer.render(T, x, y, h, [Obstacle.of("cone", *ahead(x, y, h, -0.5))]))


def test_every_track_renders_fast_enough(renderer):
    for track_id in ("monza", "spa"):
        track = load(track_id)
        renderer.render(track, *track.start_pose())  # 오프셋 지도 만들기 (처음 한 번)
        t0 = time.perf_counter()
        for _ in range(5):
            renderer.render(track, *track.start_pose())
        assert (time.perf_counter() - t0) / 5 < 1 / S.camera.fps  # 15 fps 안에


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


def test_http_snapshot_and_stream():
    x, y, h = start()
    service = CameraService(S.camera, lambda: (T, x, y, h, []), "127.0.0.1", 0)
    base = "http://%s:%d" % service.address
    service.start()
    try:
        time.sleep(0.5)
        status, ctype, body = _get(base + "/camera/snapshot.jpg")
        assert status == 200 and ctype == "image/jpeg"
        image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        assert image.shape == (S.camera.height, S.camera.width, 3)

        with urllib.request.urlopen(base + "/camera/stream.mjpg", timeout=2) as response:
            assert response.headers.get("Content-Type").startswith("multipart/x-mixed-replace")
            begin, data = time.monotonic(), b""
            while time.monotonic() - begin < 1.0:
                data += response.read1(65536)
        assert data.count(b"--frame\r\n") >= 10   # 15 fps 기준 1 s
        with pytest.raises(urllib.error.HTTPError):
            _get(base + "/nope")
    finally:
        service.stop()


def test_snapshot_without_fresh_frame_is_503():
    """프레임이 없거나 0.5 s 보다 오래되면 멈춘 화면을 실시간처럼 주지 않는다."""
    from http.server import ThreadingHTTPServer

    from xenova_simulator.camera import FrameHub, make_handler

    clock = [0.0]
    hub, stop = FrameHub(lambda: clock[0]), threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, stop))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://%s:%d/camera/snapshot.jpg" % server.server_address[:2]
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(url)
        assert excinfo.value.code == 503
        hub.publish(b"\xff\xd8jpeg")
        assert _get(url)[0] == 200
        clock[0] = 0.6
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(url)
        assert excinfo.value.code == 503
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


def test_perception_stream_source_reads_simulator_camera():
    """인지 대역 --source stream 이 시뮬레이터 MJPEG 를 실제로 열어 결과를 낸다."""
    from xenova_perception.frames import run_frames
    from xenova_perception.publisher import LatestResult

    x, y, h = start()
    cone = Obstacle.of("cone", *ahead(x, y, h, S.camera.mount_x_m + 1.2))
    service = CameraService(S.camera, lambda: (T, x, y, h, [cone]), "127.0.0.1", 0)
    service.start()
    stop = threading.Event()
    latest = LatestResult(0.5)
    try:
        url = "http://%s:%d/camera/stream.mjpg" % service.address
        timer = threading.Timer(2.5, stop.set)
        timer.start()
        frames = run_frames(PerceptionSettings(source="stream", url=url), latest, stop)
        timer.cancel()
    finally:
        stop.set()
        service.stop()
    assert frames >= 10
    result = latest.current(time.monotonic())
    assert result is not None and result.obstacles and result.extra["lane_found"]
