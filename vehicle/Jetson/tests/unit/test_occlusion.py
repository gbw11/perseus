"""batch 가림 mask를 독립 scalar 투영·near 교차·실제 오차 perturbation으로 검산한다.

수락된 센서 정확도나 15Hz 성능 시험이 아니다. 기존 project_point의 각 corner가
mask 안에 남는지 확인하고 복잡 lens/near profile은 보수 fallback인지 검사한다.
"""

from dataclasses import replace
from itertools import product
import math
import unittest

from xenova_vehicle.perception.calibration import CameraModel, IDENTITY, synthetic_calibration
from xenova_vehicle.perception.geometry import PointBounds, project_point
from xenova_vehicle.perception.occlusion import MAX_BOXES, box_occlusion


def corners(box):
    return product(*zip(box.lower, box.upper))


class OcclusionTests(unittest.TestCase):
    def test_all_scalar_visible_corners_are_inside_and_box_cap_does_not_overflow_union(self):
        import numpy as np
        camera = synthetic_calibration().rgb
        angle = .08
        rectification = ((math.cos(angle), -math.sin(angle), 0.),
                         (math.sin(angle), math.cos(angle), 0.), (0., 0., 1.))
        k = camera.k
        camera = replace(camera, rectification=rectification,
                         k=((k[0][0], 4., k[0][2]), k[1], k[2]))
        boxes = (PointBounds((.9, -.1, .01), (1.1, .05, .14), True),
                 PointBounds((1.5, .2, .0), (1.8, .4, .22), True))
        mask = box_occlusion(boxes, camera)
        self.assertEqual(mask.dtype, np.uint8)
        self.assertEqual(mask.shape, (360, 640))
        seen = 0
        for box in boxes:
            for point in corners(box):
                pixel = project_point(point, camera)
                if pixel is not None:
                    u, v = (round(value) for value in pixel)
                    if 0 <= u < camera.width and 0 <= v < camera.height:
                        self.assertEqual(int(mask[v, u]), 1)
                        seen += 1
        self.assertGreater(seen, 0)
        single = box_occlusion(boxes[:1], camera)
        repeated = box_occlusion((boxes[0],) * MAX_BOXES, camera)
        self.assertTrue(np.array_equal(single, repeated))
        self.assertFalse(box_occlusion((), camera).any())
        with self.assertRaises(ValueError):
            box_occlusion((boxes[0],) * (MAX_BOXES + 1), camera)

    def test_z0_crossing_and_unbounded_distortion_occlude_the_full_valid_roi(self):
        import numpy as np
        camera = CameraModel(240, 200, ((100., 0., 120.), (0., 100., 100.), (0., 0., 1.)),
                             IDENTITY, (0., 0., 0.), roi=(20., 30., 200., 180.), pixel_error=0.)
        expected = np.zeros((200, 240), np.uint8)
        expected[30:180, 20:200] = 1
        crossing = PointBounds((-.1, -.1, -.01), (.1, .1, .2), True)
        self.assertTrue(np.array_equal(box_occlusion((crossing,), camera), expected))
        front = PointBounds((-.1, -.1, 1.), (.1, .1, 1.2), True)
        lens = replace(camera, distortion=(.1, 0., 0., 0.))
        self.assertTrue(np.array_equal(box_occlusion((front,), lens), expected))
        behind = PointBounds((-.1, -.1, -1.2), (.1, .1, -1.), True)
        self.assertFalse(box_occlusion((behind,), camera).any())

    def test_error_envelope_expands_and_contains_perturbed_scalar_projections(self):
        import numpy as np
        camera = CameraModel(320, 240, ((200., 0., 160.), (0., 200., 120.), (0., 0., 1.)),
                             IDENTITY, (0., 0., 0.), pixel_error=0.)
        box = PointBounds((-.05, -.04, 1.), (.05, .04, 1.02), True)
        nominal = box_occlusion((box,), camera)
        uncertain = replace(camera, pixel_error=2., translation_error_m=.01,
                            rotation_error_rad=.015, ray_error_rad=.005)
        mask = box_occlusion((box,), uncertain)
        self.assertTrue(np.all(mask >= nominal))
        self.assertGreater(int(mask.sum()), int(nominal.sum()))
        for translation, angle in (((.01, -.01, .01), .015), ((-.01, .01, -.01), -.015)):
            rotation = ((math.cos(angle), -math.sin(angle), 0.),
                        (math.sin(angle), math.cos(angle), 0.), (0., 0., 1.))
            actual = replace(camera, translation=translation, rotation=rotation)
            for point in corners(box):
                u, v = project_point(point, actual)
                for du, dv in product((-2., 2.), repeat=2):
                    self.assertEqual(int(mask[round(v + dv), round(u + du)]), 1)

    def test_visible_fractional_last_column_and_row_remain_inside_integer_mask(self):
        camera = CameraModel(64, 48, ((100., 0., 32.), (0., 100., 24.), (0., 0., 1.)),
                             IDENTITY, (0., 0., 0.), pixel_error=0.)
        box = PointBounds((.311, .231, 1.), (.3149, .2349, 1.), True)
        mask = box_occlusion((box,), camera)
        for point in corners(box):
            # scalar projection은 u=63.1..63.49/v=47.1..47.49를 ROI 안으로 판정한다.
            pixel = project_point(point, camera)
            self.assertIsNotNone(pixel)
            u, v = pixel
            self.assertGreater(u, camera.width - 1)
            self.assertLess(u, camera.width)
            self.assertGreater(v, camera.height - 1)
            self.assertLess(v, camera.height)
            self.assertEqual(int(mask[math.floor(v), math.floor(u)]), 1)
        self.assertEqual(int(mask[-1, -1]), 1)
