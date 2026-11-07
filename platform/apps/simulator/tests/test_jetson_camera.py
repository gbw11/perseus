"""주차 바닥·사각형 흰 선·코스별 카메라가 실제 인지 영상에 반영되는지 검사한다."""

from copy import deepcopy
import math

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from xenova_simulator.camera import ASPHALT, LINE, Renderer  # noqa: E402
from xenova_simulator.config import SimSettings, settings_for_track  # noqa: E402
from xenova_simulator.track import load, track_ids  # noqa: E402


def test_parking_pavement_and_slot_paint_are_in_actual_camera_image():
    track = load("jetson-demo")
    settings = settings_for_track(SimSettings(camera_listen=None), track)
    renderer = Renderer(settings.camera)
    slot = track.layout["parking_slots"][1]["outline"]
    slot_x = (slot[0][0] + slot[2][0]) / 2
    pose = (slot_x, .7, math.pi / 2)
    actual = renderer.render(track, *pose)
    no_paint = deepcopy(track)
    no_paint.id = "jetson-no-slot-paint"
    no_paint.layout["paint_lines"] = []
    blank = renderer.render(no_paint, *pose)
    white = lambda image: np.all(image == LINE, axis=2).sum()
    assert white(actual) > white(blank) + 100
    # 선택 칸 가운데를 보는 픽셀은 흰 선이나 기존 잔디·벽이 아닌 포장이다.
    assert tuple(actual[settings.camera.height // 2, settings.camera.width // 2]) == ASPHALT
    no_pavement = deepcopy(track)
    no_pavement.id = "jetson-no-parking-pavement"
    no_pavement.layout["paved_areas"] = []
    without = renderer.render(no_pavement, *pose)
    assert tuple(without[settings.camera.height // 2, settings.camera.width // 2]) != ASPHALT


def test_course_camera_profile_is_used_and_legacy_renderer_is_restored():
    base = SimSettings(camera_listen=None)
    renderer = Renderer(base.camera)
    legacy, demo = load("spielberg"), load("jetson-demo")
    before = renderer.render(legacy, *legacy.start_pose())
    actual = renderer.render(demo, *demo.start_pose())
    expected = Renderer(settings_for_track(base, demo).camera).render(demo, *demo.start_pose())
    assert np.array_equal(actual, expected)
    assert np.array_equal(renderer.render(legacy, *legacy.start_pose()), before)
    assert renderer.spec == base.camera


def test_parking_entrance_does_not_keep_a_transverse_road_edge_stripe():
    track = load("jetson-demo")
    spec = settings_for_track(SimSettings(camera_listen=None), track).camera
    slot = track.layout["parking_slots"][1]["outline"]
    slot_x = (slot[0][0] + slot[2][0]) / 2
    image = Renderer(spec).render(track, slot_x, .4, math.pi / 2)
    # 도로와 연결 마당이 만나는 구간이다. 주차칸 입구선보다 가까운 지면에는 선이 없어야 한다.
    assert np.all(image[145:165, spec.width // 2] == ASPHALT)


@pytest.mark.parametrize("track_id", [t for t in track_ids() if t.startswith("jetson-")])
def test_parking_is_outside_the_initial_forward_camera_image(track_id):
    track = load(track_id)
    renderer = Renderer(settings_for_track(SimSettings(camera_listen=None), track).camera)
    actual = renderer.render(track, *track.start_pose())
    no_parking = deepcopy(track)
    no_parking.id = "jetson-no-parking"
    for field in ("paved_areas", "paint_lines", "parking_slots"):
        no_parking.layout[field] = []
    assert np.array_equal(actual, renderer.render(no_parking, *track.start_pose()))
