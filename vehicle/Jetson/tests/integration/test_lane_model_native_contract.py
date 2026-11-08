"""명시 lane literal을 공용 Python 검사와 실제 C decoder로 교차 검사한다.

software geometry 계약 시험이며 물리 관측·GT·권한·성능 수락이 아니다. 기존 cc와
nanopb 제품만 사용한다. 선택 XENOVA_LANE_NATIVE_EVIDENCE_DIR에는 원 입력,
명령, stdout/stderr와 소스 SHA를 보존하며 설치나 pytest fixture는 사용하지 않는다.
"""

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[4]
DRIVE = REPOSITORY / 'platform/apps/drive'
STATIONS = (
    0., .043478260869565216, .08695652173913043, .13043478260869565,
    .17391304347826086, .21739130434782608, .2608695652173913,
    .30434782608695654, .34782608695652173, .391304347826087,
    .43478260869565216, .4782608695652174, .5217391304347826,
    .5652173913043478, .6086956521739131, .6521739130434783,
    .6956521739130435, .7391304347826086, .782608695652174,
    .8260869565217391, .8695652173913043, .9130434782608695,
    .9565217391304348, 1.,
)
LANE_IDS = (101, 202)
LANE_REVISIONS = (9, 11)
MODEL_REVISION = 7


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _near_f32(value, ulps):
    bits = struct.unpack('<I', struct.pack('<f', value))[0]
    return struct.unpack('<f', struct.pack('<I', bits + ulps))[0]


class LaneModelNativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'linux':
            raise AssertionError('actual Linux cc/nanopb test required; not a skipped native result')
        compiler = shutil.which('cc')
        if compiler is None:
            raise AssertionError('existing cc is missing; installation/fallback/skip is not supported')
        cls.temporary = tempfile.TemporaryDirectory(prefix='lane-model-native-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.build = Path(cls.temporary.name)
        cls.evidence = None
        destination = os.environ.get('XENOVA_LANE_NATIVE_EVIDENCE_DIR')
        if destination:
            root = Path(destination)
            root.mkdir(parents=True, exist_ok=True)
            cls.evidence = Path(tempfile.mkdtemp(prefix='lane-model-native-', dir=root))
        world = DRIVE / 'world'
        cls.sources = [world / 'world.c', world / 'generated/world.pb.c',
            *(world / 'nanopb' / name for name in ('pb_common.c', 'pb_decode.c', 'pb_encode.c')),
            DRIVE / 'tests/world_probe.c']
        headers = [world / 'world.h', world / 'generated/world.pb.h',
            *(world / 'nanopb' / name for name in ('pb.h', 'pb_common.h', 'pb_decode.h', 'pb_encode.h'))]
        cls.fixture_path = DRIVE / 'examples/world_fixture.py'
        cls.primitive_path = DRIVE / 'planner/generated/primitive-manifest.json'
        cls.pin_paths = (*cls.sources, *headers, cls.fixture_path, cls.primitive_path)
        missing = [str(path) for path in cls.pin_paths if not path.is_file()]
        if missing:
            raise AssertionError('required original native inputs missing: ' + ', '.join(missing))
        cls.pins = cls._source_pins()
        cls._write_json('source-before.json', cls.pins)
        spec = importlib.util.spec_from_file_location('lane_native_original_world_fixture', cls.fixture_path)
        if spec is None or spec.loader is None:
            raise AssertionError('original world fixture loader unavailable')
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)
        from xenova_drive_world import decode, encode, schema, validate
        from xenova_vehicle.perception.road import LaneHypothesis, RoadResult
        from xenova_vehicle.transport.world_adapter import WorldBuildError, lane_model_from_road
        cls.decode, cls.encode, cls.schema, cls.validate = staticmethod(decode), staticmethod(encode), schema, staticmethod(validate)
        cls.LaneHypothesis, cls.RoadResult = LaneHypothesis, RoadResult
        cls.WorldBuildError, cls.convert = WorldBuildError, staticmethod(lane_model_from_road)
        primitive = json.loads(cls.primitive_path.read_text(encoding='utf-8'))['asset_sha256']
        if cls.fixture.PRIMITIVE != primitive:
            raise AssertionError('fixture primitive differs from the original manifest')
        cls.binary = cls.build / 'world-probe'
        command = [compiler, '-std=c11', '-g', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-DTEST_PRIMITIVE_SHA="' + primitive + '"',
            '-I' + str(world), '-I' + str(world / 'nanopb'), '-I' + str(world / 'generated'),
            *(str(path) for path in cls.sources), '-lm', '-o', str(cls.binary)]
        cls._command('compile', command, timeout=30)
        cls._check_sources()
        cls.corpus = []

    @classmethod
    def _source_pins(cls):
        result = []
        for path in cls.pin_paths:
            raw = path.read_bytes()
            result.append(dict(path=path.relative_to(REPOSITORY).as_posix(), bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest()))
        return result

    @classmethod
    def _check_sources(cls):
        after = cls._source_pins()
        cls._write_json('source-after.json', after)
        if after != cls.pins:
            raise AssertionError('original native source/header/fixture changed during the test')

    @classmethod
    def _write(cls, name, raw):
        (cls.build / name).write_bytes(raw)
        if cls.evidence is not None:
            (cls.evidence / name).write_bytes(raw)

    @classmethod
    def _write_json(cls, name, value):
        cls._write(name, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8'))

    @classmethod
    def _command(cls, label, command, *, timeout):
        cls._write_json(label + '.command.json', dict(argv=command, timeout_s=timeout))
        environment = os.environ.copy()
        environment.setdefault('ASAN_OPTIONS', 'detect_leaks=0')
        environment.setdefault('UBSAN_OPTIONS', 'halt_on_error=1:print_stacktrace=1')
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout, env=environment)
        except subprocess.TimeoutExpired as error:
            cls._write(label + '.stdout', error.stdout or b'')
            cls._write(label + '.stderr', error.stderr or b'')
            cls._write_json(label + '.result.json', dict(status='TIMEOUT'))
            raise AssertionError('original native command timed out: ' + label) from error
        cls._write(label + '.stdout', result.stdout)
        cls._write(label + '.stderr', result.stderr)
        cls._write_json(label + '.result.json', dict(returncode=result.returncode))
        if result.returncode != 0:
            raise AssertionError('native command failed: ' + label + '; rc=' + str(result.returncode)
                + '; stderr=' + result.stderr.decode('utf-8', errors='replace')[-2000:])
        return result

    def _product(self, label, wire):
        self._check_sources()
        self._write(label + '.pb', wire)
        self._write_json(label + '.wire.json', dict(bytes=len(wire), sha256=hashlib.sha256(wire).hexdigest()))
        result = self._command(label, [str(self.binary), str(self.build / (label + '.pb'))], timeout=5)
        value = json.loads(result.stdout)
        self.assertEqual(value['bytes'], len(wire))
        self._check_sources()
        return value

    def _road(self, *, length=1., x_offset=0., y_offset=0., bend=0.):
        # 독립 literal의 조밀한 공통 station이다. 추출기/정합/시뮬레이터를 호출하지 않는다.
        divider = tuple((x_offset + length * s, y_offset + 1.6 + 4 * bend * s * (1 - s)) for s in STATIONS)
        offsets = lambda delta: tuple((x, y + delta) for x, y in divider)
        stations = [0.]
        for a, b in zip(divider, divider[1:]):
            stations.append(stations[-1] + math.dist(a, b))
        hypothesis = self.LaneHypothesis(0, ((1,), (2,), (3,)), tuple(stations), divider,
            offsets(.4), offsets(-.4), offsets(.2), offsets(-.2), (False,) * 24,
            ((.39, .41),) * 24, .035, .01)
        return self.RoadResult('VALID', (hypothesis,), True, (), ('NATIVE_LANE_LITERAL', 'rgb', self.fixture.CLOCK, 2), True)

    def _world(self, model, *, x_offset=0., y_offset=0.):
        value = self.fixture.world(sequence=2)
        # 기존 parking_world의 features11 공통 필드만 사용한다. parking/Scene을 채우지 않는다.
        value.minor = 2
        value.required_features = 11
        count = value.width * value.height
        value.cell_flags = bytes([9]) * count
        value.drivable_permission = bytes([255]) * (count // 8)
        value.lane_model.CopyFrom(model)
        value.localization.CopyFrom(self.schema.MapLocalization(registration_revision=1,
            anchor_position_bound_m=.01, anchor_heading_bound_rad=.01,
            geometry_position_bound_m=.01, geometry_heading_bound_rad=.01,
            observed_mapper_us=1_000_000, valid_for_us=300_000, position_drift_bound_mps=.01,
            heading_drift_bound_rps=.01, quality=1, timestamp_error_us=1000))
        if x_offset or y_offset:
            for point in (value.origin, value.reference_vehicle_pose, *value.reference,
                    *value.left_boundary, *value.right_boundary):
                point.x += x_offset
                point.y += y_offset
        return value

    def test_original_dense_24_direct_model_is_rejected_by_python_and_actual_c(self):
        road = self._road()
        hypothesis = road.hypotheses[0]
        model = self.schema.LaneModel(revision=MODEL_REVISION, divider_position_bound_m=hypothesis.error_m,
            divider_heading_bound_rad=hypothesis.yaw_error_rad, divider_state=bytes(24), crossing=bytes(23))
        for x, y in hypothesis.divider:
            model.divider.add(x=x, y=y)
        for index, points in enumerate((hypothesis.left_center, hypothesis.right_center)):
            lane = model.lanes.add(lane_id=LANE_IDS[index], adjacent_lane_id=LANE_IDS[1-index],
                revision=LANE_REVISIONS[index], travel_direction=1, position_bound_m=hypothesis.error_m,
                heading_bound_rad=hypothesis.yaw_error_rad, point_state=bytes(24))
            for x, y in points:
                lane.centerline.add(x=x, y=y)
        value = self._world(model)
        with self.assertRaisesRegex(ValueError, '^unbounded reconstructed lane heading$'):
            self.validate(value)
        # 검증 encoder를 우회해 원 음성 wire를 그대로 C에 준다. converter는 이 시험에서 호출하지 않는다.
        wire = value.SerializeToString(deterministic=True)
        self.assertEqual(self._product('dense-24-direct-negative', wire)['result'], 'GEOMETRY')

    def test_actual_subset_conversion_preserves_bounds_ids_span_and_c_accepts_every_result(self):
        # 6*error는 nominal 설명용 경계다. 좌표/오차 FP32 연산의 실제 판정은 C에 맡긴다.
        boundary = _f32(6 * _f32(.035))
        cases = (
            ('canonical', dict()),
            ('boundary-below', dict(length=_near_f32(boundary, -1))),
            ('boundary-above', dict(length=_near_f32(boundary, 1))),
            ('boundary-above-16ulp', dict(length=_near_f32(boundary, 16))),
            ('small-curve', dict(bend=.002)),
            ('large-coordinates', dict(x_offset=256., y_offset=128.)),
            ('large-boundary', dict(length=_near_f32(boundary, 16), x_offset=256., y_offset=128.)),
        )
        canonical_accepted = False
        for name, arguments in cases:
            with self.subTest(case=name):
                road = self._road(**arguments)
                hypothesis = road.hypotheses[0]
                row = dict(case=name, input=arguments, original_points=24, position_bound_m=.035, heading_bound_rad=.01)
                try:
                    model = self.convert(road, lane_ids=LANE_IDS, model_revision=MODEL_REVISION,
                        lane_revisions=LANE_REVISIONS, crossing=bytes(23))
                except self.WorldBuildError as error:
                    row.update(status='CONSERVATIVE_REJECTION', reason=str(error))
                    self.corpus.append(row)
                    self._write_json('conversion-corpus.json', self.corpus)
                    self.assertNotEqual(name, 'canonical', 'canonical 1m/.035m positive is mandatory')
                    continue
                row.update(status='RETURNED', output_points=len(model.divider))
                self.corpus.append(row)
                self._write_json('conversion-corpus.json', self.corpus)
                value = self._world(model, x_offset=arguments.get('x_offset', 0.), y_offset=arguments.get('y_offset', 0.))
                # 반환된 model은 다른 assertion보다 먼저 실제 C에 전달해 원 판정을 남긴다.
                wire = value.SerializeToString(deterministic=True)
                actual = self._product('subset-' + name, wire)
                row.update(c_result=actual['result'], wire_bytes=len(wire), wire_sha256=hashlib.sha256(wire).hexdigest())
                self._write_json('conversion-corpus.json', self.corpus)
                self.assertEqual(actual['result'], 'OK')
                self.assertLess(len(model.divider), 24)
                self.assertGreaterEqual(len(model.divider), 2)
                self.assertEqual(model.revision, MODEL_REVISION)
                self.assertGreaterEqual(model.divider_position_bound_m, hypothesis.error_m)
                self.assertGreaterEqual(model.divider_heading_bound_rad, hypothesis.yaw_error_rad)
                self.assertEqual(tuple(lane.lane_id for lane in model.lanes), LANE_IDS)
                self.assertEqual(tuple(lane.revision for lane in model.lanes), LANE_REVISIONS)
                selected = [point.x for point in model.divider]
                self.assertTrue(all(a < b for a, b in zip(selected, selected[1:])))
                self.assertTrue(set(selected).issubset({_f32(point[0]) for point in hypothesis.divider}))
                for points, original in ((model.divider, hypothesis.divider),
                        (model.lanes[0].centerline, hypothesis.left_center),
                        (model.lanes[1].centerline, hypothesis.right_center)):
                    self.assertEqual((points[0].x, points[0].y), tuple(map(_f32, original[0])))
                    self.assertEqual((points[-1].x, points[-1].y), tuple(map(_f32, original[-1])))
                    self.assertEqual([point.x for point in points], selected)
                for index, lane in enumerate(model.lanes):
                    self.assertEqual(lane.adjacent_lane_id, LANE_IDS[1-index])
                    self.assertGreaterEqual(lane.position_bound_m, hypothesis.error_m)
                    self.assertGreaterEqual(lane.heading_bound_rad, hypothesis.yaw_error_rad)
                    self.assertEqual(lane.point_state, bytes(len(selected)))
                self.assertEqual(model.divider_state, bytes(len(selected)))
                self.assertEqual(model.crossing, bytes(len(selected)-1))
                self.validate(value)
                self.assertEqual(self.encode(value), wire)
                self.assertEqual(self.decode(wire, self.schema.WorldSnapshot), value)
                if name == 'canonical':
                    canonical_accepted = True
        self.assertTrue(canonical_accepted, 'canonical positive must pass Python and actual C')
        with self.subTest(case='coordinate-domain'):
            for name, x_offset in (('original-outside', 1_000_000.), ('rounds-outside', 999_998.99)):
                with self.subTest(variant=name):
                    road = self._road(x_offset=x_offset)
                    row = dict(case='coordinate-domain-' + name, x_offset=x_offset)
                    if name == 'rounds-outside':
                        self.assertLess(road.hypotheses[0].divider[-1][0], 1_000_000.)
                        self.assertEqual(_f32(road.hypotheses[0].divider[-1][0]), 1_000_000.)
                    try:
                        model = self.convert(road, lane_ids=LANE_IDS, model_revision=MODEL_REVISION,
                            lane_revisions=LANE_REVISIONS, crossing=bytes(23))
                    except self.WorldBuildError as error:
                        row.update(status='REQUIRED_REJECTION', reason=str(error))
                    else:
                        wire = self._world(model).SerializeToString(deterministic=True)
                        actual = self._product('domain-' + name, wire)
                        row.update(status='UNEXPECTED_RETURN', c_result=actual['result'])
                    self.corpus.append(row)
                    self._write_json('conversion-corpus.json', self.corpus)
                    self.assertEqual(row['status'], 'REQUIRED_REJECTION', 'C coordinate domain must be rejected by converter')
        with self.subTest(case='two-station-rounded-outer-tube'):
            divider = ((0., 8191.89990234375), (1., 8191.89990234375))
            left = ((0., 8192.0498046875), (1., 8192.0498046875))
            right = ((0., 8191.75), (1., 8191.75))
            outer_left = ((0., 8192.19970703125), (1., 8192.19970703125))
            outer_right = ((0., 8191.60009765625), (1., 8191.60009765625))
            hypothesis = self.LaneHypothesis(0, ((1,), (2,), (3,)), (0., 1.), divider,
                outer_left, outer_right, left, right, (False, False), ((.2998046875, .2998046875),) * 2, 0., 0.)
            road = self.RoadResult('VALID', (hypothesis,), True, (), ('NATIVE_OUTER_LITERAL', 'rgb', self.fixture.CLOCK, 2), True)
            row = dict(case='two-station-rounded-outer-tube', original_points=2, position_bound_m=0., heading_bound_rad=0.)
            try:
                model = self.convert(road, lane_ids=LANE_IDS, model_revision=MODEL_REVISION,
                    lane_revisions=LANE_REVISIONS, crossing=b'\0')
            except self.WorldBuildError as error:
                row.update(status='CONSERVATIVE_REJECTION', reason=str(error))
            else:
                value = self._world(model, y_offset=divider[0][1] - 1.6)
                wire = value.SerializeToString(deterministic=True)
                actual = self._product('two-station-rounded-outer-tube', wire)
                row.update(status='RETURNED', c_result=actual['result'], wire_bytes=len(wire),
                    wire_sha256=hashlib.sha256(wire).hexdigest())
                self.corpus.append(row)
                self._write_json('conversion-corpus.json', self.corpus)
                self.assertEqual(actual['result'], 'OK')
                self.assertEqual(len(model.divider), 2)
                self.assertEqual(model.revision, MODEL_REVISION)
                self.assertEqual(tuple(lane.lane_id for lane in model.lanes), LANE_IDS)
                self.assertEqual(tuple(lane.revision for lane in model.lanes), LANE_REVISIONS)
                self.assertEqual([(p.x, p.y) for p in model.divider], list(divider))
                margins = []
                for lane, centers, original in zip(model.lanes, (left, right), (outer_left, outer_right)):
                    self.assertEqual([(p.x, p.y) for p in lane.centerline], list(centers))
                    # world.c의 2*center-divider와 E=2*lane_bound+divider_bound를 같은 FP32 순서로 읽는다.
                    error = _f32(_f32(2 * lane.position_bound_m) + model.divider_position_bound_m)
                    for center, d, source_outer in zip(lane.centerline, model.divider, original):
                        actual_outer = tuple(_f32(_f32(2 * c) - v) for c, v in zip((center.x, center.y), (d.x, d.y)))
                        distance = math.dist(source_outer, actual_outer)
                        margins.append(dict(original_outer=source_outer, actual_c_outer=actual_outer,
                            distance_m=distance, c_error_m=error))
                        row['outer_tube_checks'] = margins
                        self._write_json('conversion-corpus.json', self.corpus)
                        self.assertGreaterEqual(error, hypothesis.error_m + distance)
                row['outer_tube_checks'] = margins
                self._write_json('conversion-corpus.json', self.corpus)
                self.validate(value)
                self.assertEqual(self.encode(value), wire)
                self.assertEqual(self.decode(wire, self.schema.WorldSnapshot), value)
            if row['status'] == 'CONSERVATIVE_REJECTION':
                self.corpus.append(row)
                self._write_json('conversion-corpus.json', self.corpus)


if __name__ == '__main__':
    unittest.main()
