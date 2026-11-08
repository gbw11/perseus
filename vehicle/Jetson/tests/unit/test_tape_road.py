"""합성 white-band와 독립 polyline에서 tape/도로의 양성·모호함·상한을 검사한다.

GT는 여기서 영상을 만드는 oracle에만 사용한다. 실제 테이프 색/보정/주행 성능의
수락 시험이 아니며 NumPy/OpenCV가 없는 환경에서는 영상 시험을 명시 skip한다.
"""

from dataclasses import replace
import math
import unittest

from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.road import RoadProfile, infer_road
from xenova_vehicle.perception.tape import TapeGraph, TapeProfile, TapeStrip, extract_tape


def strip(number, points, *, error=.001, start='observed_paint_end', end='observed_paint_end'):
    return TapeStrip(number, tuple(tuple(float(v) for v in p) for p in points), (.02, .03), error, start, end)


def straight_graph(ys=(.3, 0., -.3)):
    return TapeGraph(tuple(strip(i, ((.2, y), (1.4, y))) for i, y in enumerate(ys)),
                     source_key=('synthetic', 'rgb', 'simulation-clock', 1))


class RoadHypothesisTests(unittest.TestCase):
    def test_three_observed_strips_form_two_common_station_midpoints(self):
        graph = straight_graph()
        result = infer_road(graph)
        self.assertEqual(result.status, 'VALID')
        self.assertTrue(result.complete)
        self.assertEqual(len(result.hypotheses), 1)
        hypothesis = result.hypotheses[0]
        self.assertEqual(hypothesis.strip_ids, ((0,), (1,), (2,)))
        self.assertFalse(any(hypothesis.inferred))
        self.assertLessEqual(len(hypothesis.station_s), 32)
        for divider, left, right in zip(hypothesis.divider, hypothesis.left_center, hypothesis.right_center):
            self.assertAlmostEqual(left[0], divider[0])
            self.assertAlmostEqual(right[0], divider[0])
            self.assertAlmostEqual(left[1], .15)
            self.assertAlmostEqual(right[1], -.15)
        self.assertEqual(result.source_key, graph.source_key)
        self.assertFalse(result.calibration_qualified)
        self.assertEqual(hypothesis.boundary_reference, 'tape_center')

    def test_curved_unequal_point_sequences_use_normal_intersections_not_index_average(self):
        def arc(radius, count, exponent):
            return tuple((radius * math.cos(theta), 2 + radius * math.sin(theta))
                for theta in (-1.47 + .65 * (i / (count - 1)) ** exponent for i in range(count)))

        graph = TapeGraph((strip(0, arc(1.7, 13, 1.6)), strip(1, arc(2., 33, 1.)),
                           strip(2, arc(2.3, 21, .8))))
        result = infer_road(graph)
        self.assertEqual(result.status, 'VALID')
        hypothesis = result.hypotheses[0]
        self.assertGreater(len(hypothesis.divider), 2)
        for divider, outer, center in zip(hypothesis.divider, hypothesis.left_outer, hypothesis.left_center):
            self.assertAlmostEqual(center[0], (divider[0] + outer[0]) / 2)
            self.assertAlmostEqual(center[1], (divider[1] + outer[1]) / 2)
            self.assertAlmostEqual(math.hypot(center[0], center[1] - 2), 1.85, delta=.015)
        for divider, outer, center in zip(hypothesis.divider, hypothesis.right_outer, hypothesis.right_center):
            self.assertAlmostEqual(2 * center[0] - divider[0], outer[0])
            self.assertAlmostEqual(2 * center[1] - divider[1], outer[1])
            self.assertAlmostEqual(math.hypot(center[0], center[1] - 2), 2.15, delta=.015)

    def test_parking_apron_edge_is_not_averaged_into_the_lane_center(self):
        graph = straight_graph((.3, 0., -.3, .95))
        result = infer_road(graph)
        self.assertEqual(result.status, 'VALID')
        self.assertEqual(result.hypotheses[0].strip_ids, ((0,), (1,), (2,)))
        self.assertAlmostEqual(result.hypotheses[0].left_center[0][1], .15)

    def test_four_plausible_parallel_lines_remain_ambiguous(self):
        result = infer_road(straight_graph((.6, .3, 0., -.3)))
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertTrue(result.complete)
        self.assertGreaterEqual(len(result.hypotheses), 2)

    def test_missing_outer_is_not_measured_and_two_possible_roles_are_not_selected_by_score(self):
        graph = straight_graph((.3, 0.))
        result = infer_road(graph)
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertEqual(len(result.hypotheses), 2)
        known_side = RoadProfile(lane_width_min_m=.28, lane_width_max_m=.32,
                                 missing_outer_side='RIGHT', max_geometry_error_m=.2)
        partial = infer_road(graph, known_side)
        self.assertEqual(partial.status, 'PARTIAL')
        self.assertTrue(all(partial.hypotheses[0].inferred))
        self.assertEqual(partial.hypotheses[0].strip_ids[2], ())
        self.assertGreater(partial.hypotheses[0].error_m, graph.strips[0].error_m)

    def test_unique_dashed_chain_marks_unobserved_gap_stations_as_inferred(self):
        graph = TapeGraph((strip(0, ((.2, .3), (1.4, .3))), strip(1, ((.2, -.3), (1.4, -.3))),
                           strip(2, ((.2, 0.), (.65, 0.))), strip(3, ((.75, 0.), (1.4, 0.)))))
        result = infer_road(graph, RoadProfile(max_curvature_1pm=2.))
        self.assertEqual(result.status, 'PARTIAL')
        hypothesis = result.hypotheses[0]
        self.assertEqual(hypothesis.strip_ids[1], (2, 3))
        self.assertTrue(any(hypothesis.inferred))
        self.assertFalse(all(hypothesis.inferred))

    def test_ambiguous_dash_connections_do_not_silently_choose_one_chain(self):
        graph = TapeGraph((strip(0, ((.2, .3), (1.4, .3))), strip(1, ((.2, -.3), (1.4, -.3))),
            strip(2, ((.2, 0.), (.65, 0.))), strip(3, ((.75, .003), (1.4, .003))),
            strip(4, ((.75, -.003), (1.4, -.003)))))
        result = infer_road(graph)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)
        self.assertIn('DASH_CHAIN_AMBIGUOUS', result.reasons)

    def test_32_choose_3_does_not_become_unique_after_only_4096_combinations(self):
        graph = straight_graph(tuple(-.05 + i * .003 for i in range(32)))
        result = infer_road(graph)
        self.assertEqual(result.work['triples_total'], 4960)
        self.assertEqual(result.work['triples_checked'], 4096)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)
        self.assertIn('ROAD_HYPOTHESIS_CAP', result.reasons)

    def test_intersection_work_limit_keeps_unexamined_geometry_unknown(self):
        result = infer_road(straight_graph(), RoadProfile(intersection_work_cap=1))
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)
        self.assertIn('ROAD_WORK_CAP', result.reasons)

    def test_graph_cap_failure_is_not_upgraded_by_a_plausible_road(self):
        graph = replace(straight_graph(), status='UNKNOWN', complete=False, reasons=('STRIP_OR_POINT_CAP',))
        result = infer_road(graph)
        self.assertEqual(len(result.hypotheses), 1)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)

    def test_longitudinal_projection_error_does_not_become_short_segment_yaw_error(self):
        strips = []
        for i, y in enumerate((.3, 0., -.3)):
            points = ((.2, y), (.21, y + .003), (.8, y - .001), (1.4, y))
            strips.append(replace(strip(i, points, error=.085),
                point_error_xy=((.08, .002),) * 4, segment_error_xy=((.08, .005),) * 3))
        result = infer_road(TapeGraph(tuple(strips)))
        self.assertEqual(result.status, 'VALID')
        hypothesis = result.hypotheses[0]
        self.assertLess(hypothesis.yaw_error_rad, .01)
        self.assertGreaterEqual(hypothesis.error_m, .085)
        self.assertEqual(hypothesis.heading_reference, 'bounded_observed_polyline')
        uncertain_both_axes = TapeGraph(tuple(replace(s, point_error_xy=(), segment_error_xy=()) for s in strips))
        self.assertEqual(infer_road(uncertain_both_axes).status, 'UNKNOWN')

    def test_missing_outer_alternatives_survive_an_unrelated_extra_paint_line(self):
        graph = straight_graph((.3, 0., 1.2))
        result = infer_road(graph)
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertEqual(len(result.hypotheses), 2)
        self.assertTrue(all(all(h.inferred) for h in result.hypotheses))
        self.assertTrue(all(2 not in ids for h in result.hypotheses for ids in h.strip_ids))
        explicit_prior = RoadProfile(lane_width_min_m=.28, lane_width_max_m=.32,
                                     missing_outer_side='RIGHT', max_geometry_error_m=.2)
        partial = infer_road(graph, explicit_prior)
        self.assertEqual(partial.status, 'PARTIAL')
        self.assertEqual(len(partial.hypotheses), 1)

    def test_occluded_three_line_overlap_is_not_fabricated_but_two_line_priors_remain(self):
        graph = TapeGraph((
            strip(0, ((.475, -.2), (1.39, -.2)), start='FOV_clip', end='occluded'),
            strip(1, ((.765, .2), (1.025, .2))),
            strip(2, ((1.07, .6), (1.57, .6)), start='FOV_clip', end='occluded'),
            strip(3, ((1.275, .2), (1.495, .2)), start='occluded', end='occluded')))
        result = infer_road(graph)
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertTrue(result.hypotheses)
        self.assertTrue(all(any(not ids for ids in h.strip_ids) for h in result.hypotheses))
        self.assertTrue(all(all(h.inferred) for h in result.hypotheses))


class TapeImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy as np
            import cv2
        except ImportError as error:
            raise unittest.SkipTest('영상 수치 시험에는 기존 NumPy/OpenCV가 필요함') from error
        cls.np, cls.cv2 = np, cv2
        from xenova_vehicle.perception.calibration import CameraModel, Plane
        cls.camera = CameraModel(256, 256, ((100., 0., 128.), (0., 100., 128.), (0., 0., 1.)),
            ((0., -1., 0.), (-1., 0., 0.), (0., 0., -1.)), (.8, 0., 1.), pixel_error=0.)
        cls.plane = Plane()
        cls.profile = TapeProfile(x_min_m=.2, x_max_m=1.4, y_min_m=-.7, y_max_m=.7,
                                  extraction_error_m=.002, simplify_error_m=.002)

    def image(self):
        return self.np.full((256, 256, 3), 30, dtype=self.np.uint8)

    def pixels(self, points):
        # 위에서 아래로 보는 독립 pinhole oracle: u=128-100y, v=128-100(x-.8).
        return self.np.rint([(128 - 100 * y, 128 - 100 * (x - .8)) for x, y in points]).astype(self.np.int32)

    def draw(self, image, points, thickness=3):
        self.cv2.polylines(image, [self.pixels(points)], False, (245, 245, 245), thickness)

    def extract(self, image, **kwargs):
        stamp = FrameStamp('synthetic', 'rgb', 1, 'simulation-clock', (100, 100), 100)
        return extract_tape(image, self.camera, self.plane, self.profile, stamp=stamp, **kwargs)

    def test_white_bands_have_one_strip_each_and_two_lane_positive_without_nn(self):
        image = self.image()
        for y in (-.3, 0., .3):
            self.draw(image, ((.3, y), (1.3, y)))
        graph = self.extract(image)
        self.assertEqual(graph.status, 'VALID')
        self.assertEqual(len(graph.strips), 3)
        self.assertFalse(graph.calibration_qualified)
        self.assertEqual(infer_road(graph).status, 'VALID')
        self.assertFalse(graph.mask.flags.writeable)
        self.assertFalse(graph.skeleton.flags.writeable)
        self.assertLessEqual(graph.work['edge_tests'], self.profile.edge_work_cap)
        self.assertTrue(all(all(v is not None and v >= s.error_m for v in s.endpoint_error_m)
                            for s in graph.strips))

    def test_t_and_x_junctions_are_split_instead_of_fit_as_one_line(self):
        for cross in (False, True):
            with self.subTest(cross=cross):
                image = self.image()
                self.draw(image, ((.3, 0.), (1.3, 0.)))
                self.draw(image, ((.8, -.4 if cross else 0.), (.8, .4)))
                graph = self.extract(image)
                self.assertGreaterEqual(len(graph.junctions), 1)
                self.assertGreaterEqual(len(graph.strips), 3 if not cross else 4)
                self.assertTrue(any(j.degree >= 3 for j in graph.junctions))

    def test_fov_clipped_tip_is_not_observed_paint_end(self):
        image = self.image()
        self.draw(image, ((.05, 0.), (1.7, 0.)))
        graph = self.extract(image)
        self.assertGreaterEqual(len(graph.strips), 1)
        self.assertTrue(all(s.start_kind == 'FOV_clip' and s.end_kind == 'FOV_clip' for s in graph.strips))
        self.assertTrue(all(s.endpoint_error_m == (None, None) for s in graph.strips))

    def test_white_box_and_explicit_occlusion_do_not_create_a_divider_through_the_box(self):
        image = self.image()
        self.draw(image, ((.3, 0.), (1.3, 0.)))
        polygon = self.pixels(((.65, -.12), (.95, -.12), (.95, .12), (.65, .12)))
        self.cv2.fillPoly(image, [polygon], (245, 245, 245))
        occlusion = self.np.zeros(image.shape[:2], dtype=self.np.uint8)
        self.cv2.fillPoly(occlusion, [polygon], 1)
        graph = self.extract(image, occlusion_mask=occlusion.astype(self.np.bool_))
        byte_mask_graph = self.extract(image, occlusion_mask=occlusion)
        self.assertEqual(byte_mask_graph.strips, graph.strips)
        self.assertGreaterEqual(len(graph.strips), 1)
        self.assertTrue(any('occluded' in (s.start_kind, s.end_kind) for s in graph.strips))
        self.assertFalse(any(min(p[0] for p in s.points_xy) < .6 and max(p[0] for p in s.points_xy) > 1.
                             for s in graph.strips))
        without_depth = self.extract(image)
        self.assertIn('WIDE_WHITE_SURFACE_CENSORED', without_depth.reasons)

    def test_nn_mask_is_retained_only_as_auxiliary_and_does_not_remove_white_tape(self):
        image = self.image()
        for y in (-.3, 0., .3):
            self.draw(image, ((.3, y), (1.3, y)))
        without = self.extract(image)
        candidates = object()
        with_auxiliary = self.extract(image, nn_candidates=candidates)
        self.assertIs(with_auxiliary.nn_candidates, candidates)
        self.assertEqual(without.strips, with_auxiliary.strips)
        self.assertTrue(self.np.array_equal(without.mask, with_auxiliary.mask))

    def test_reusing_static_camera_projection_does_not_reuse_the_previous_image(self):
        image = self.image()
        for y in (-.3, 0., .3):
            self.draw(image, ((.3, y), (1.3, y)))
        observed = self.extract(image)
        empty = self.extract(self.image())
        observed_again = self.extract(image)
        self.assertEqual(empty.status, 'EMPTY')
        self.assertFalse(empty.strips)
        self.assertEqual(observed.strips, observed_again.strips)
        self.assertEqual(observed.work['projection_batches'], 1)
        self.assertEqual(empty.work['projection_batches'], 0)

    def traced_raster(self, boundary=None, internal=False):
        # 제어한 raster로 edge 탐색 분류만 검사한다. renderer/실물 영상의 양성 주장은 아니다.
        from xenova_vehicle.perception.tape import _trace
        np = self.np
        shape = (140, 120)
        skeleton, band = np.zeros(shape, dtype=np.bool_), np.zeros(shape, dtype=np.bool_)
        skeleton[20:111, 60] = True
        band[19:112, 58:63] = True
        valid, occluded = np.ones(shape, dtype=np.bool_), np.zeros(shape, dtype=np.bool_)
        if boundary == 'FOV_clip':
            valid[100:, 62:] = False
        elif boundary == 'occluded':
            occluded[100:, 62:] = True
        if internal:
            band[60:66, :] = True
        work = dict(edge_tests=0)
        return _trace(np, self.cv2, skeleton, valid, occluded, band, np.zeros(shape),
                      np.zeros(shape + (2,)), self.profile, work), work

    def test_boundary_censored_cross_sections_do_not_claim_width_or_poison_the_whole_graph(self):
        for boundary in ('FOV_clip', 'occluded'):
            with self.subTest(boundary=boundary):
                (strips, _, complete), work = self.traced_raster(boundary)
                self.assertTrue(complete)
                self.assertEqual(len(strips), 1)
                self.assertFalse(strips[0].width_complete)
                self.assertGreater(strips[0].censored_cross_sections, 0)
                self.assertIn(boundary, (strips[0].start_kind, strips[0].end_kind))
                self.assertEqual(work['edge_pair_internal'], 0)
                self.assertEqual(work['edge_pair_missing'], work['edge_pair_censored'])

    def test_unresolved_interior_edges_still_make_the_geometry_incomplete(self):
        (strips, _, complete), work = self.traced_raster(internal=True)
        self.assertFalse(complete)
        self.assertEqual(len(strips), 1)
        self.assertFalse(strips[0].width_complete)
        self.assertGreater(work['edge_pair_internal'], 0)
        self.assertEqual(work['edge_pair_censored'], 0)

    def test_thinning_cap_does_not_claim_a_complete_graph(self):
        image = self.image()
        for y in (-.3, 0., .3):
            self.draw(image, ((.3, y), (1.3, y)), thickness=5)
        graph = extract_tape(image, self.camera, self.plane, replace(self.profile, max_thin_iterations=1))
        self.assertEqual(graph.status, 'UNKNOWN')
        self.assertFalse(graph.complete)
        self.assertIn('THINNING_CAP', graph.reasons)


if __name__ == '__main__':
    unittest.main()
