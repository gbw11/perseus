"""배치 hit 소비를 원 scalar 수식·원 지도 전이와 대조한다.

합성 배열의 값 시험이며 센서 수락·실시간 성능 시험이 아니다. oracle는 최적화 전
full-block depth와 cell별 hit 갱신을 보존하고, 새 batch 구현의 helper를 쓰지 않는다.
"""

from dataclasses import FrozenInstanceError
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from xenova_vehicle.mapping.local_map import EvidenceGrid, FREE, OCCUPIED, GridSpec
from xenova_vehicle.perception.calibration import CameraModel, IDENTITY, Plane, synthetic_calibration
from xenova_vehicle.perception.depth import DepthConfig, analyze_depth
from xenova_vehicle.perception.free_space import TargetClass, certify_cell
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.occlusion import box_occlusion
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.point_batch import PointBoundsBatch


def scalar_hits(grid, boxes, stamp, *, view_id, origin_xy):
    """최적화 전 add_hits의 cell별 사건 순서. int 변환으로 원 u64 비교를 보존한다."""
    observed = grid._time(stamp)
    touched = set()
    for box in boxes:
        touched.update(grid.touched(box))
    for col, row in touched:
        grid.conflict[row, col] |= grid.state[row, col] == FREE
        grid.state[row, col] = OCCUPIED
        if observed is None:
            grid.time_known[row, col] = False
            grid.observed_us[row, col] = 0
            grid.unordered_hit[row, col] = True
            grid.hit_available_us[row, col] = max(int(grid.hit_available_us[row, col]), stamp.available_us)
            grid.hit_origin[row, col] = origin_xy
            grid.hit_view[row * grid.spec.width + col] = view_id
        elif grid.unordered_hit[row, col] and observed <= int(grid.hit_available_us[row, col]):
            continue
        elif not grid.time_known[row, col] or observed >= int(grid.observed_us[row, col]):
            grid.observed_us[row, col] = observed
            grid.time_known[row, col] = True
            grid.unordered_hit[row, col] = False
            grid.hit_available_us[row, col] = stamp.available_us
            grid.hit_origin[row, col] = origin_xy
            grid.hit_view[row * grid.spec.width + col] = view_id
    if touched:
        grid.revision += 1
        grid.applied_hits += len(touched)
    return len(touched)


def full_block_depth(depth, camera, plane, config):
    """원 full-block nominal 분류. invalid row도 기하를 계산한 뒤 mask로 제외한다."""
    import numpy as np
    yy, xx = np.indices(depth.shape, dtype=np.float64)
    values = depth.reshape(-1)
    xx, yy = xx.reshape(-1), yy.reshape(-1)
    k = camera.k
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        z = values.astype(np.float64) * .001
        qy = (yy - k[1][2]) / k[1][1]
        qx = (xx - k[0][2] - k[0][1] * qy) / k[0][0]
        q = np.stack((qx, qy, np.ones_like(qx)), axis=-1)
        transform = np.asarray(camera.rectification) @ np.asarray(camera.rotation).T
        points = z[:, None] * (q @ transform) + np.asarray(camera.translation)
        norm = np.sqrt((q * q).sum(axis=-1))
        pixel = math.hypot(camera.pixel_error / k[1][1],
            camera.pixel_error / k[0][0] + abs(k[0][1]) * camera.pixel_error / (k[0][0] * k[1][1]))
        delta = 2 * math.sin(camera.ray_error_rad / 2)
        min_hz = 1 / (norm + pixel) - delta
        q_error = pixel + np.where(min_hz > 0, delta * (1 + norm + pixel) / min_hz, np.inf)
        z_error = config.extra_depth_error_m + config.extra_relative_error * z
        radius = ((z + z_error) * q_error + z_error * norm
            + 2 * math.sin(camera.rotation_error_rad / 2) * (z + z_error) * (norm + q_error)
            + camera.translation_error_m + 1e-9)
        heights = points @ np.asarray(plane.normal) + plane.offset
        h_error = radius * sum(abs(v) for v in plane.normal) + plane.offset_error_m + 1e-9
        h_error += 2 * math.sin(plane.normal_error_rad / 2) * (
            np.sqrt((points * points).sum(axis=-1)) + math.sqrt(3) * radius)
        a, b, c, d = camera.roi
        valid = (np.isfinite(z) & (z > 0) & (values <= 65535) & (z >= camera.near_m)
            & (z <= camera.far_m) & (xx >= a) & (xx < c) & (yy >= b) & (yy < d))
        finite = np.isfinite(radius) & np.isfinite(heights) & np.isfinite(h_error)
        floor = (valid & finite & (heights - h_error >= -config.floor_tolerance_m)
            & (heights + h_error <= config.floor_tolerance_m))
        hits = (valid & finite & (heights + h_error >= config.obstacle_min_height_m)
            & (heights - h_error <= config.obstacle_max_height_m))
    return points, radius, valid, floor, hits


def stamp(value, *, sequence=1, known=True, available=None):
    return FrameStamp('synthetic', 'right', sequence, 'simulation-clock',
        (value, value) if known else None, value + 100 if available is None else available)


def grid():
    import numpy as np
    value = EvidenceGrid(GridSpec(4, 4, .25, (-.5, -.5)), frame_id='base',
        calibration_id='a' * 64, clock_id='simulation-clock', kind='SYNTHETIC')
    value.set_domain(np.ones((4, 4), dtype=bool), np.ones((4, 4), dtype=bool))
    return value


class PointBatchTests(unittest.TestCase):
    def assert_grids_equal(self, left, right):
        import numpy as np
        for name in ('state', 'observed_us', 'time_known', 'conflict', 'unordered_hit',
                     'hit_available_us', 'hit_origin'):
            np.testing.assert_array_equal(getattr(left, name), getattr(right, name), err_msg=name)
        for name in ('hit_view', 'revision', 'applied_hits', 'applied_free'):
            self.assertEqual(getattr(left, name), getattr(right, name), name)

    def test_owned_readonly_rows_and_public_validation(self):
        import numpy as np
        lower = np.array([[0., -.1, .1], [1., .2, .3]])
        upper = lower + .01
        flags = np.array([False, True])
        expected = tuple(PointBounds(tuple(a), tuple(b), bool(q)) for a, b, q in zip(lower, upper, flags))
        batch = PointBoundsBatch(lower, upper, flags, ('source', 'right', 'clock', 7))
        lower[:] = upper[:] = -10
        flags[:] = False
        self.assertEqual(tuple(batch), expected)
        self.assertEqual(batch[-1], expected[-1])
        self.assertEqual(batch[:1], expected[:1])
        for array in (batch.numbers, batch.lower, batch.upper, batch.qualified):
            self.assertFalse(array.flags.writeable)
            with self.assertRaises(ValueError):
                array.setflags(write=True)
        with self.assertRaises(FrozenInstanceError):
            batch.source_key = None
        good = np.zeros((1, 3))
        for lo, hi, q in ((np.full((1, 3), np.nan), good, True),
                          (good + 1, good, True), (good[:, :2], good, True),
                          (good, good, np.array([1], dtype=np.uint8)),
                          (np.zeros((8193, 3)), np.zeros((8193, 3)), True)):
            with self.subTest(shape=lo.shape, qualification=type(q).__name__), self.assertRaises(ValueError):
                PointBoundsBatch(lo, hi, q)
        empty = PointBoundsBatch.from_points(())
        self.assertEqual(tuple(empty), ())
        self.assertEqual(len(empty.transformed_xy((1., 2., .1))), 0)

    def test_array_consumers_do_not_expand_and_first_hazard_keeps_original_order(self):
        import numpy as np
        camera = CameraModel(64, 48, ((100., 0., 32.), (0., 100., 24.), (0., 0., 1.)),
            IDENTITY, (0., 0., 0.), pixel_error=0.)
        outside = PointBounds((2., 2., 1.), (2.1, 2.1, 1.1), True)
        uncertain = PointBounds((-.05, -.04, 1.), (.05, .04, 1.02), False)
        accepted = PointBounds((-.02, -.02, 1.), (.02, .02, 1.02), True)
        profile, target = synthetic_calibration(), TargetClass(.03, .06, .15, qualified=True)
        for scalar in ((outside, uncertain, accepted), (outside, accepted, uncertain)):
            batch = PointBoundsBatch.from_points(scalar)
            expected_mask = box_occlusion(scalar, camera)
            expected = certify_cell((-.01, -.01, .01, .01), (), (target,), profile, hazards=scalar)
            with (patch.object(PointBoundsBatch, '__iter__', side_effect=AssertionError('scalar expansion')),
                  patch.object(PointBoundsBatch, '__getitem__', side_effect=AssertionError('scalar expansion'))):
                actual = certify_cell((-.01, -.01, .01, .01), (), (target,), profile, hazards=batch)
                self.assertEqual(actual, expected)
                self.assertEqual(actual.work, 0)
                np.testing.assert_array_equal(box_occlusion(batch, camera), expected_mask)
                mapped = grid()
                self.assertGreater(mapped.add_hits(batch, stamp(100), view_id='v', origin_xy=(0., 0.)), 0)
                self.assertEqual(len(batch.transformed_xy((.1, .2, .3))), len(scalar))

    def test_pose_transform_matches_unchanged_scalar_corners_and_errors(self):
        import numpy as np
        points = (PointBounds((-.4, -.2, .01), (.3, .7, .2), True),
                  PointBounds((.5, -.6, -.1), (1.7, -.1, .4), False))
        batch = PointBoundsBatch.from_points(points)
        for yaw in (0., .7, -2.9):
            sample = SimpleNamespace(x=.3, y=-.4, yaw=yaw, position_error_m=.013, yaw_error_rad=.07)
            pose = SimpleNamespace(sample=sample)
            expected = np.asarray([PerceptionPipeline._xy_bounds(None, box, pose) for box in points])
            actual = batch.transformed_xy((sample.x, sample.y, yaw),
                position_error_m=sample.position_error_m, yaw_error_rad=sample.yaw_error_rad)
            np.testing.assert_array_equal(np.column_stack((actual.lower[:, :2], actual.upper[:, :2])), expected)
            np.testing.assert_array_equal(actual.lower[:, 2], batch.lower[:, 2])
            np.testing.assert_array_equal(actual.upper[:, 2], batch.upper[:, 2])
            np.testing.assert_array_equal(actual.qualified, batch.qualified)

    def test_closed_touch_union_and_age_order_match_frozen_cell_transitions(self):
        import numpy as np
        boxes = ((-.25, -.25, -.25, -.25), (-.51, -.1, .01, .26), (.25, .25, .5, .5),
                 (2., 2., 3., 3.), (-.25, -.25, -.25, -.25))
        scalar, batched = grid(), grid()
        cells = tuple((col, row) for row in range(4) for col in range(4))
        for value in (scalar, batched):
            value.add_free(cells, stamp(50), view_id='initial', origin_xy=(0., 0.), certificate_complete=True)
        events = ((200, True, 300), (150, True, 250), (300, False, 400),
                  (400, True, 500), (401, True, 501), ((1 << 63) + 99, True, (1 << 63) + 199))
        for index, (observed, known, available) in enumerate(events):
            original = stamp(observed, sequence=index, known=known, available=available)
            origin = (index * .01, index * -.02)
            expected = scalar_hits(scalar, boxes, original, view_id=str(index), origin_xy=origin)
            self.assertEqual(batched.add_hits(boxes, original, view_id=str(index), origin_xy=origin), expected)
            self.assert_grids_equal(scalar, batched)
            left, right = scalar.snapshot(available + 1000, clock_id='simulation-clock'), batched.snapshot(
                available + 1000, clock_id='simulation-clock')
            for name in left['arrays']:
                np.testing.assert_array_equal(left['arrays'][name], right['arrays'][name])
        later = stamp((1 << 63) + 300)
        for origin in ((.05, -.1), (.3, .2)):
            self.assertEqual(scalar.add_free(cells, later, view_id='new', origin_xy=origin, certificate_complete=True),
                batched.add_free(cells, later, view_id='new', origin_xy=origin, certificate_complete=True))
            self.assert_grids_equal(scalar, batched)

    def test_off_grid_huge_boxes_do_not_overflow_or_miss_closed_boundary(self):
        import numpy as np
        value = grid()
        for box in ((1e300, 1e300, 1e300, 1e300), (-1e300, -1e300, -1e300, -1e300),
                    (-.5, -.5, -.5, -.5), (.5, .5, .5, .5), (-.25, -.25, .25, .25)):
            expected = np.zeros((4, 4), dtype=bool)
            for col, row in value.touched(box):
                expected[row, col] = True
            value.add_hits((box,), stamp(100), view_id='v', origin_xy=(0., 0.))
            np.testing.assert_array_equal(value.state == OCCUPIED, expected)
            value = grid()
        self.assertEqual(value.add_hits(((1e308, 1e308, 1e308, 1e308),), stamp(100),
            view_id='v', origin_xy=(0., 0.)), 0)
        self.assertEqual(value.add_hits(((-1e308, -1e308, 1e308, 1e308),), stamp(100),
            view_id='v', origin_xy=(0., 0.)), 16)
        self.assertEqual(value.add_hits((), stamp(100), view_id='v', origin_xy=(0., 0.)), 0)

    def test_valid_first_depth_keeps_full_block_counts_selection_and_bounds(self):
        import numpy as np
        camera = CameraModel(32, 16, ((100., 7., 16.), (0., 110., 8.), (0., 0., 1.)),
            ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (.05, .02, 1.),
            roi=(1.5, .5, 30.5, 15.5), pixel_error=.5, rotation_error_rad=.002,
            ray_error_rad=.001, translation_error_m=.0001, near_m=.05, far_m=3.)
        plane = Plane(normal_error_rad=.001, offset_error_m=.0001)
        config = DepthConfig(max_patches=0, max_hit_boxes=8, extra_depth_error_m=.0005,
                             extra_relative_error=.0001)
        data = np.full((16, 32), 1000., dtype=np.float64)
        data.reshape(-1)[::7] = 600.
        data.reshape(-1)[[0, 1, 33, 100, 111, 200, 300]] = [0., -1., np.nan, np.inf, 65536., 10., 4000.]
        points, radius, valid, floor, hits = full_block_depth(data, camera, plane, config)
        indices = np.flatnonzero(hits)
        chosen = indices[(np.arange(8, dtype=np.int64) * len(indices)) // 8]
        original = stamp(1000)
        with patch.object(PointBounds, '__post_init__', side_effect=AssertionError('eager point construction')):
            actual = analyze_depth(data, camera, plane, stamp=original, config=config, input_kind='SYNTHETIC')
        self.assertIs(actual.stamp, original)
        self.assertIs(type(actual.hit_boxes), PointBoundsBatch)
        self.assertEqual(actual.hit_boxes.source_key, original.key)
        self.assertEqual(actual.valid_count, int(valid.sum()))
        self.assertEqual(actual.floor_count, int(floor.sum()))
        self.assertEqual(actual.invalid_count, data.size - int(valid.sum()))
        self.assertEqual(actual.omitted_hit_count, len(indices) - 8)
        self.assertEqual(actual.unknown_count, data.size - int(floor.sum()) - 8)
        self.assertEqual(actual.omitted_patch_count, int(valid.sum()))
        self.assertFalse(actual.hit_boxes.qualified.any())
        np.testing.assert_array_equal(actual.hit_boxes.lower, np.nextafter(points[chosen] - radius[chosen, None], -np.inf))
        np.testing.assert_array_equal(actual.hit_boxes.upper, np.nextafter(points[chosen] + radius[chosen, None], np.inf))
        empty = analyze_depth(np.zeros_like(data), camera, plane, config=config)
        self.assertEqual((empty.valid_count, empty.invalid_count), (0, data.size))
        self.assertEqual(empty.hit_boxes, ())
        self.assertEqual(empty.patches, ())


if __name__ == '__main__':
    unittest.main()
