"""잘린 tape 끝을 물리 끝점으로 정합하는 허위 관측성 반례와 정책 회귀다.

literal graph는 의미 단위의 명시 관측이다. 실제 pixels 추출/실물 보정 수락이나
World·채택·LIVE 입력 시험이 아니다. SYNTHETIC 보정과 odom 표시는 그대로 둔다.
등록 profile의 residual/error/work cap은 기본값이며 실제 feature/reference/register를 쓴다.
"""

from dataclasses import asdict, replace
import itertools
import unittest

from xenova_vehicle.mapping.odometry import PoseEstimate, PoseSample
from xenova_vehicle.mapping.registration import PoseBox, RegistrationProfile, register
from xenova_vehicle.perception.calibration import synthetic_calibration
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.tape import ENDPOINT_KINDS, TapeGraph, TapeStrip


PHYSICAL = ('physical_junction', 'observed_paint_end')
CENSORED = ('FOV_clip', 'occluded', 'predicted')
TRUE_DX_M = .10


class RegistrationCensorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.calibration = synthetic_calibration()
        self.pipeline = PerceptionPipeline(self.calibration)
        self.profile = RegistrationProfile()
        self.prior = PoseBox((-.15, .15), (-.0001, .0001), (-.0001, .0001))
        self.a = FrameStamp('semantic-camera', 'rgb', 1, 'semantic-clock', (100_000, 100_000), 100_000)
        self.b = replace(self.a, sequence=2, captured_us=(200_000, 200_000), available_us=200_000)
        sample = PoseSample('semantic-odom', self.a.clock_id, self.calibration.sha256,
            self.a.captured_us[0], 0., 0., 0., 0., 0., origin='SYNTHETIC_ODOMETRY')
        self.pose_a = PoseEstimate(sample, self.a.captured_us, self.a.captured_us)

    @staticmethod
    def strip(number, *, start='FOV_clip', end='FOV_clip', y=0., points=None, **kwargs):
        return TapeStrip(number, ((.3, y), (1.3, y)) if points is None else points,
            (.024, .026), .001, start, end, endpoint_error_m=(.001, .001), **kwargs)

    def graph(self, strips, stamp):
        return TapeGraph(tuple(strips), source_key=stamp.key, calibration_qualified=True)

    def reference(self, graph):
        return self.pipeline._reference_from(graph, 'capture-A', self.pose_a, 1)

    def features(self, graph):
        return self.pipeline._features(graph, 'capture-B')

    def result(self, reference_graph, current_graph):
        reference = self.reference(reference_graph)
        points = self.features(current_graph)
        # 기존 register는 empty points를 거절한다. 정보 부족을 새 성공/status로 포장하지 않는다.
        fitted = (register(points, reference, self.prior, self.profile,
            calibration_id=self.calibration.sha256) if points else None)
        return points, reference, fitted

    @staticmethod
    def contains(fitted, pose=(TRUE_DX_M, 0., 0.)):
        return fitted is not None and any(all(lo <= value <= hi for value, (lo, hi) in
            zip(pose, (box.x, box.y, box.yaw))) for box in fitted.feasible)

    @staticmethod
    def evidence(points, reference, fitted):
        return repr(dict(points=[asdict(point) for point in points],
            reference=asdict(reference), registration=None if fitted is None else asdict(fitted)))

    def assert_excluded(self, strip):
        old = self.graph((strip,), self.a)
        current = self.graph((strip,), self.b)
        points, reference, fitted = self.result(old, current)
        evidence = self.evidence(points, reference, fitted)
        self.assertEqual(points, (), evidence)
        self.assertEqual(reference.segments, (), evidence)

    def test_same_fov_window_cannot_falsely_bound_zero_and_exclude_true_ten_centimetres(self):
        """연속 무한 평행선은 전진 후에도 같은 local FOV 끝에서 잘린다."""
        strips = tuple(self.strip(number, y=y) for number, y in enumerate((-.3, 0., .3)))
        old, current = self.graph(strips, self.a), self.graph(strips, self.b)
        self.assertNotEqual(old.source_key, current.source_key)
        self.assertTrue(self.prior.x[0] <= 0. <= TRUE_DX_M <= self.prior.x[1])
        points, reference, fitted = self.result(old, current)
        evidence = 'true_dx=.10; default_profile=' + repr(asdict(self.profile)) + '; ' + self.evidence(points, reference, fitted)
        false_bound = fitted is not None and fitted.status == 'BOUNDED' and not self.contains(fitted)
        self.assertFalse(false_bound, evidence)
        self.assertEqual(points, (), evidence)
        self.assertEqual(reference.segments, (), evidence)

    def test_both_ends_censored_are_excluded_for_each_original_endpoint_kind(self):
        self.assertEqual(set(ENDPOINT_KINDS), set(PHYSICAL) | set(CENSORED))
        for kind in CENSORED:
            with self.subTest(endpoint=kind):
                self.assert_excluded(self.strip(0, start=kind, end=kind))

    def test_mixed_censored_ends_and_one_censored_end_are_excluded_in_both_metric_paths(self):
        for start, end in itertools.product(sorted(ENDPOINT_KINDS), repeat=2):
            if start not in CENSORED and end not in CENSORED:
                continue
            with self.subTest(start=start, end=end):
                self.assert_excluded(self.strip(0, start=start, end=end))

    def test_closed_strip_has_no_metric_segment_endpoints_even_with_physical_labels(self):
        self.assert_excluded(self.strip(0, start='physical_junction', end='observed_paint_end',
            points=((.3, -.1), (1.3, -.1), (1.3, .1), (.3, -.1)), closed=True))

    def test_unobserved_strip_never_enters_current_features_or_frozen_reference(self):
        for closed in (False, True):
            with self.subTest(closed=closed):
                self.assert_excluded(self.strip(0, start='observed_paint_end', end='physical_junction',
                    observed=False, closed=closed))

    def test_real_finite_physical_ends_remain_and_bound_actual_translation(self):
        for start, end in itertools.product(PHYSICAL, repeat=2):
            with self.subTest(start=start, end=end):
                old_strips = tuple(self.strip(number, start=start, end=end, y=y)
                    for number, y in enumerate((-.3, 0., .3)))
                current_strips = tuple(replace(strip,
                    points_xy=tuple((x - TRUE_DX_M, y) for x, y in strip.points_xy)) for strip in old_strips)
                points, reference, fitted = self.result(self.graph(old_strips, self.a), self.graph(current_strips, self.b))
                evidence = self.evidence(points, reference, fitted)
                self.assertEqual(len(points), 6, evidence)
                self.assertEqual(len(reference.segments), 3, evidence)
                self.assertEqual(fitted.status, 'BOUNDED', evidence)
                self.assertTrue(fitted.complete, evidence)
                self.assertTrue(self.contains(fitted), evidence)

    def test_censored_lines_do_not_delete_or_vote_with_independent_real_finite_features(self):
        clipped = tuple(self.strip(number, y=y) for number, y in enumerate((-.3, 0., .3)))
        finite = (self.strip(3, start='physical_junction', end='observed_paint_end',
            points=((.2, .6), (.8, .6))),
            self.strip(4, start='observed_paint_end', end='physical_junction',
                points=((.2, .6), (.2, .9))))
        shifted = tuple(replace(strip, points_xy=tuple((x - TRUE_DX_M, y)
            for x, y in strip.points_xy)) for strip in finite)
        points, reference, fitted = self.result(self.graph(clipped + finite, self.a),
            self.graph(clipped + shifted, self.b))
        evidence = self.evidence(points, reference, fitted)
        self.assertEqual({point.component for point in points}, {'strip:3', 'strip:4'}, evidence)
        self.assertEqual({segment.component for segment in reference.segments}, {'strip:3', 'strip:4'}, evidence)
        self.assertEqual(fitted.status, 'BOUNDED', evidence)
        self.assertTrue(fitted.complete, evidence)
        self.assertTrue(self.contains(fitted), evidence)
