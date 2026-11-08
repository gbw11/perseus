"""공용 실제 codec roundtrip과 내부 grid 변환의 양성/거절. 운영 승인 시험은 아니다."""

from dataclasses import replace
import struct
import unittest

from xenova_drive_world import schema, decode
from xenova_vehicle.mapping.local_map import GridSpec
from xenova_vehicle.perception.road import LaneHypothesis, RoadResult
from xenova_vehicle.transport.world_adapter import WorldBuildError, WorldSource, build_world, lane_model_from_road


BOOT = '11111111-1111-4111-8111-111111111111'
INSTANCE = '22222222-2222-4222-8222-222222222222'
FRAME = '33333333-3333-4333-8333-333333333333'
CLOCK = '44444444-4444-4444-8444-444444444444'
CALIBRATION = 'a' * 64


def source():
    return WorldSource('SYNTHETIC', 'frame:original-capture', FRAME, CLOCK, CALIBRATION,
                       (1_000_000, 1_000_010), 1_100_000)


def road():
    divider = ((0., 0.), (1., 0.))
    left, right = ((0., .3), (1., .3)), ((0., -.3), (1., -.3))
    center_left, center_right = ((0., .15), (1., .15)), ((0., -.15), (1., -.15))
    h = LaneHypothesis(0, ((0,), (1,), (2,)), (0., 1.), divider, left, right,
        center_left, center_right, (False, False), ((.29, .31), (.29, .31)), .001, .002)
    return RoadResult('VALID', (h,), True, (), ('synthetic', 'rgb', CLOCK, 1), True)


def snapshot():
    import numpy as np
    state = np.asarray([[0, 1, 2], [2, 0, 1], [1, 2, 0]], dtype=np.uint8)
    age = np.asarray([[0xffffffff, 10, 20], [30, 0xffffffff, 40], [50, 60, 0xffffffff]], dtype=np.uint32)
    support = np.ones((3, 3), dtype=bool)
    support[2, 2] = False
    permission = np.asarray([[True, True, False], [True, False, True], [True, False, True]], dtype=bool)
    return dict(kind='SYNTHETIC', frame_id='frame:original-capture', calibration_id=CALIBRATION,
        clock_id=CLOCK, revision=1, spec=GridSpec(3, 3, .125, (0., -.125)),
        arrays=dict(state=state, age_ms=age, support=support, permission=permission),
        world_valid=False, live_authority=False)


def template(features=11):
    value = schema.WorldSnapshot(major=1, minor=2 if features in (11, 15) else 0,
        required_features=features, vehicle='synthetic-adapter-test', mapper_boot=BOOT,
        mapper_instance=INSTANCE, map_epoch=FRAME, frame_epoch=FRAME, odom_epoch=FRAME,
        sequence=1, configuration='explicit-test-profile', calibration=CALIBRATION,
        primitive='b' * 64, reference_id='explicit-test-reference', reference_revision=1,
        reference_odom_seq=1, observed_us=1_000_005, completed_us=1_100_100,
        published_us=1_100_200, observation_seq=1, clock_epoch=CLOCK,
        complete=True, truncated=False, inflation_policy=0)
    value.origin.CopyFrom(schema.Pose(x=0., y=-.125, yaw=0.))
    value.reference_vehicle_pose.CopyFrom(schema.Pose(x=0., y=0., yaw=0.))
    for x in (0., 1.):
        value.reference.add(x=x, y=0.)
        value.left_boundary.add(x=x, y=.5)
        value.right_boundary.add(x=x, y=-.5)
    if features in (11, 15):
        value.lane_model.CopyFrom(lane_model_from_road(road(), lane_ids=(101, 102), model_revision=7,
            lane_revisions=(9, 10), crossing=b'\x00'))
        value.localization.CopyFrom(schema.MapLocalization(registration_revision=1,
            anchor_position_bound_m=.001, anchor_heading_bound_rad=.001,
            geometry_position_bound_m=.001, geometry_heading_bound_rad=.001,
            observed_mapper_us=1_000_005, valid_for_us=250_000, position_drift_bound_mps=0.,
            heading_drift_bound_rps=0., quality=1, timestamp_error_us=5))
    return value


def preferred():
    import numpy as np
    value = np.zeros((3, 3), dtype=bool)
    value[0, 1] = True
    return value


class WorldAdapterTests(unittest.TestCase):
    def test_declared_vehicle_bundle_is_wire_identity_while_local_profile_remains_grid_identity(self):
        from test_resident_perception import provisional
        from xenova_vehicle.perception.calibration import CalibrationProfile
        fields=provisional().to_dict();fields['vehicle_bundle_sha256']='c'*64
        calibration=CalibrationProfile.from_dict(fields)
        original=snapshot();original['calibration_id']=calibration.sha256
        header=template();header.calibration='c'*64
        original['kind']='REPLAY'
        bound=WorldSource.from_calibration('REPLAY',original['frame_id'],FRAME,CLOCK,calibration,
            (1_000_000,1_000_010),1_100_000)
        built=build_world(original,header,source=bound,preferred_road=preferred())
        self.assertEqual(built.message().calibration,'c'*64)
        self.assertEqual(built.source.calibration_id,calibration.sha256)
        self.assertNotEqual(built.source.calibration_id,built.message().calibration)
        header.calibration=calibration.sha256
        with self.assertRaises(WorldBuildError):build_world(original,header,source=bound,preferred_road=preferred())

    def test_shared_roundtrip_explicit_occupancy_permission_and_original_age(self):
        import numpy as np
        original, header = snapshot(), template()
        state_before = original['arrays']['state'].copy()
        retained, expired = np.zeros((3, 3), dtype=bool), np.zeros((3, 3), dtype=bool)
        retained[1, 0], expired[2, 1] = True, True
        built = build_world(original, header, source=source(), preferred_road=preferred(),
                            static_retained=retained, expired=expired)
        message = decode(built.payload)
        self.assertEqual(message, built.message())
        self.assertEqual(message.occupancy, bytes((255, 0, 100, 100, 255, 0, 0, 100, 255)))
        self.assertEqual(message.cell_flags, bytes((0, 9, 1, 3, 0, 1, 1, 5, 0)))
        self.assertEqual(message.drivable_permission, bytes((0x6b, 0)))
        self.assertEqual(struct.unpack('<9I', message.age_le32),
                         (0xffffffff, 10, 20, 30, 0xffffffff, 40, 50, 60, 0xffffffff))
        self.assertFalse(header.HasField('occupancy'))
        self.assertTrue(np.array_equal(original['arrays']['state'], state_before))
        self.assertEqual(built.input_kind, 'SYNTHETIC')
        self.assertFalse(built.world_valid or built.metric_ready or built.live_authority)
        changed = built.message()
        changed.sequence = 999
        self.assertEqual(built.message().sequence, 1)
        unknown = schema.WorldSnapshot()
        unknown.ParseFromString(built.payload + b'\xf8\x07\x01')
        filtered = build_world(original, unknown, source=source(), preferred_road=preferred(),
                               static_retained=retained, expired=expired)
        self.assertEqual(filtered.payload, built.payload)

    def test_legacy_exact_grid_does_not_invent_permission_or_new_features(self):
        instant = replace(source(), captured_us=(1_000_005, 1_000_005))
        built = build_world(snapshot(), template(0), source=instant)
        value = built.message()
        self.assertEqual(value.required_features, 0)
        self.assertFalse(value.HasField('drivable_permission'))
        self.assertFalse(value.HasField('lane_model'))

    def test_missing_new_geometry_or_parking_inventory_is_rejected(self):
        for field in ('lane_model', 'localization'):
            header = template()
            header.ClearField(field)
            with self.subTest(field=field), self.assertRaises(WorldBuildError):
                build_world(snapshot(), header, source=source(), preferred_road=preferred())
        with self.assertRaises(WorldBuildError):
            build_world(snapshot(), template(15), source=source(), preferred_road=preferred())

    def test_original_clock_calibration_kind_and_frame_are_required(self):
        for key, value in (('clock_id', INSTANCE), ('calibration_id', 'c' * 64),
                           ('kind', 'LIVE'), ('frame_id', 'frame:other')):
            original = snapshot()
            original[key] = value
            with self.subTest(key=key), self.assertRaises(WorldBuildError):
                build_world(original, template(), source=source(), preferred_road=preferred())
        for field, value in (('clock_epoch', INSTANCE), ('calibration', 'd' * 64), ('frame_epoch', INSTANCE)):
            header = template()
            setattr(header, field, value)
            with self.subTest(field=field), self.assertRaises(WorldBuildError):
                build_world(snapshot(), header, source=source(), preferred_road=preferred())

    def test_source_time_is_not_replaced_by_receive_or_publish_time(self):
        for field, value in (('observed_us', 1_100_000), ('completed_us', 1_000_009), ('published_us', 1_100_099)):
            header = template()
            setattr(header, field, value)
            with self.subTest(field=field), self.assertRaises(WorldBuildError):
                build_world(snapshot(), header, source=source(), preferred_road=preferred())
        header = template()
        header.localization.timestamp_error_us = 4
        with self.assertRaises(WorldBuildError):
            build_world(snapshot(), header, source=source(), preferred_road=preferred())
        for update in (dict(captured_us=None), dict(snapshot_us=True), dict(snapshot_us=1 << 63),
                       dict(captured_us=(1_100_001, 1_100_002))):
            with self.subTest(update=update), self.assertRaises(WorldBuildError):
                replace(source(), **update)

    def test_state_dtype_shape_and_original_large_age_are_preserved(self):
        import numpy as np
        for name, value in (('state', np.zeros((2, 2), dtype=np.uint8)),
                            ('age_ms', np.zeros((3, 3), dtype=np.uint64)),
                            ('support', np.zeros((3, 3), dtype=np.uint8))):
            original = snapshot()
            original['arrays'][name] = value
            with self.subTest(name=name), self.assertRaises(WorldBuildError):
                build_world(original, template(), source=source(), preferred_road=preferred())
        original = snapshot()
        original['arrays']['state'][0, 1] = 3
        with self.assertRaises(WorldBuildError):
            build_world(original, template(), source=source(), preferred_road=preferred())
        # 보존 occupied의 원 나이는 현재 boot uptime보다 클 수 있다. source time으로 재작성하지 않는다.
        original = snapshot()
        original['arrays']['age_ms'][1, 0] = 3_600_000
        retained = np.zeros((3, 3), dtype=bool)
        retained[1, 0] = True
        built = build_world(original, template(), source=source(), preferred_road=preferred(), static_retained=retained)
        self.assertEqual(struct.unpack('<9I', built.message().age_le32)[3], 3_600_000)

    def test_retained_free_needs_scene_and_road_bit_needs_its_own_policy(self):
        import numpy as np
        retained = np.zeros((3, 3), dtype=bool)
        retained[0, 1] = True
        with self.assertRaises(WorldBuildError):
            build_world(snapshot(), template(), source=source(), preferred_road=preferred(), static_retained=retained)
        with self.assertRaises(WorldBuildError):
            build_world(snapshot(), template(0), source=replace(source(), captured_us=(1_000_005, 1_000_005)),
                        preferred_road=preferred())
        # whole-cell permission을 road bit로 대체하지 않는다. 명시 road 없이 feature11은 실패다.
        with self.assertRaises(WorldBuildError):
            build_world(snapshot(), template(), source=source())

    def test_float32_grid_error_needs_existing_localization_bound(self):
        original = snapshot()
        original['spec'] = GridSpec(3, 3, .05, (0., -.125))
        with self.assertRaises(WorldBuildError):
            build_world(original, template(0), source=replace(source(), captured_us=(1_000_005, 1_000_005)))
        header = template()
        header.localization.geometry_position_bound_m = 0.
        with self.assertRaises(WorldBuildError):
            build_world(original, header, source=source(), preferred_road=preferred())
        built = build_world(original, template(), source=source(), preferred_road=preferred())
        self.assertEqual(built.message().resolution_m, struct.unpack('<f', struct.pack('<f', .05))[0])

    def test_template_conflicts_and_authority_claims_do_not_get_overwritten(self):
        header = template()
        header.occupancy = bytes(9)
        with self.assertRaises(WorldBuildError):
            build_world(snapshot(), header, source=source(), preferred_road=preferred())
        for name in ('world_valid', 'live_authority'):
            original = snapshot()
            original[name] = True
            with self.subTest(name=name), self.assertRaises(WorldBuildError):
                build_world(original, template(), source=source(), preferred_road=preferred())

    def test_lane_conversion_uses_external_ids_revisions_and_actual_common_midpoints(self):
        original = road()
        value = lane_model_from_road(original, lane_ids=(101, 102), model_revision=7,
                                     lane_revisions=(9, 10), crossing=b'\x01')
        self.assertEqual([lane.lane_id for lane in value.lanes], [101, 102])
        self.assertEqual([lane.adjacent_lane_id for lane in value.lanes], [102, 101])
        self.assertEqual([lane.revision for lane in value.lanes], [9, 10])
        self.assertEqual(value.revision, 7)
        self.assertEqual(value.crossing, b'\x01')
        for i, divider in enumerate(value.divider):
            self.assertAlmostEqual(value.lanes[0].centerline[i].y, .15)
            self.assertAlmostEqual(value.lanes[1].centerline[i].y, -.15)
            self.assertEqual(divider.y, 0.)
        self.assertGreaterEqual(value.lanes[0].position_bound_m, original.hypotheses[0].error_m)

    def test_ambiguous_incomplete_or_nonmidpoint_lane_is_not_selected_or_repaired(self):
        original = road()
        bad_midpoint = replace(original.hypotheses[0], left_center=((0., .17), (1., .17)))
        for value in (replace(original, hypotheses=original.hypotheses * 2, status='AMBIGUOUS'),
                      replace(original, complete=False), replace(original, hypotheses=(bad_midpoint,))):
            with self.subTest(status=value.status), self.assertRaises(WorldBuildError):
                lane_model_from_road(value, lane_ids=(101, 102), model_revision=7, lane_revisions=(9, 10), crossing=b'\0')
        for updates in (dict(lane_ids=(101, 101)), dict(model_revision=0), dict(lane_revisions=(9, True)), dict(crossing=b'')):
            arguments = dict(lane_ids=(101, 102), model_revision=7, lane_revisions=(9, 10), crossing=b'\0')
            arguments.update(updates)
            with self.subTest(updates=updates), self.assertRaises(WorldBuildError):
                lane_model_from_road(original, **arguments)

    def test_original_long_clock_label_requires_explicit_wire_binding_without_relabeling(self):
        label = 'simulation:' + BOOT
        self.assertEqual(len(label), 47)
        with self.assertRaises(WorldBuildError):
            replace(source(), clock_id=label)
        original = snapshot()
        original['clock_id'] = label
        binding = replace(source(), clock_id=label, wire_clock_epoch=CLOCK)
        built = build_world(original, template(), source=binding, preferred_road=preferred())
        self.assertEqual(built.source.clock_id, label)
        self.assertEqual(built.source.wire_clock_epoch, CLOCK)
        self.assertEqual(built.message().clock_epoch, CLOCK)
        self.assertEqual(built.input_kind, 'SYNTHETIC')
        self.assertEqual(original['clock_id'], label)
        for bad in ('J', 'simulation:' + BOOT, 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'):
            with self.subTest(bad=bad), self.assertRaises(WorldBuildError):
                replace(binding, wire_clock_epoch=bad)

    def test_legacy_rejects_capture_width_and_new_profile_rounds_half_width_up(self):
        for width in (1, 3, 5):
            binding = replace(source(), captured_us=(1_000_000, 1_000_000 + width))
            midpoint, expected_error = 1_000_000 + width // 2, (width + 1) // 2
            for features in (0, 1):
                header = template(features)
                header.observed_us = midpoint
                with self.subTest(width=width, features=features), self.assertRaises(WorldBuildError):
                    build_world(snapshot(), header, source=binding,
                                preferred_road=preferred() if features else None)
            header = template()
            header.observed_us = header.localization.observed_mapper_us = midpoint
            header.localization.timestamp_error_us = expected_error - 1
            with self.subTest(width=width), self.assertRaises(WorldBuildError):
                build_world(snapshot(), header, source=binding, preferred_road=preferred())
            header.localization.timestamp_error_us = expected_error
            built = build_world(snapshot(), header, source=binding, preferred_road=preferred())
            self.assertEqual(built.message().observed_us, midpoint)
            self.assertEqual(built.message().localization.timestamp_error_us, expected_error)

    def test_actual_synthetic_pipeline_snapshot_reaches_builder_with_named_synthetic_clock_binding(self):
        import numpy as np
        from xenova_vehicle.perception.calibration import synthetic_calibration
        from xenova_vehicle.perception.depth import DepthConfig
        from xenova_vehicle.perception.pipeline import PerceptionPipeline, PipelineProfile
        from xenova_vehicle.simulation import SyntheticCamera, SceneSnapshot, demo_scene

        camera = SyntheticCamera(synthetic_calibration())
        scene = demo_scene()
        observation = camera.capture(SceneSnapshot(BOOT, 0, 1_000_000,
            tuple(scene.fields['track']['layout']['start_pose']), scene))
        profile = PipelineProfile(grid=GridSpec(8, 8, .125, (0., -.5)),
                                  depth=DepthConfig(max_patches=0, max_hit_boxes=1024))
        # GT/pose는 renderer에만 있다. core는 원 RGB/native RIGHT만 받는다.
        result = PerceptionPipeline(camera.calibration, profile, targets=()).process(observation)
        original = result.map_snapshot
        binding = WorldSource('SYNTHETIC', original['frame_id'], FRAME, original['clock_id'],
            original['calibration_id'], result.summary['captured_us'], result.summary['now_us'],
            wire_clock_epoch=CLOCK)
        self.assertEqual(binding.clock_id, 'simulation:' + BOOT)
        header = template()
        header.calibration = camera.calibration.sha256
        header.origin.y = -.5
        header.observed_us = header.completed_us = header.published_us = result.summary['now_us']
        header.localization.observed_mapper_us = result.summary['now_us']
        header.localization.timestamp_error_us = 0
        # 외부 template은 명시 진단 fixture다. 새 정합/권위의 수락으로 포장하지 않는다.
        header.localization.quality = 0
        header.localization.valid_for_us = 0
        road_priority = np.zeros((8, 8), dtype=bool)
        road_priority[:, 3] = True
        built = build_world(original, header, source=binding, preferred_road=road_priority)
        decoded = decode(built.payload)
        self.assertEqual((decoded.width, decoded.height), (8, 8))
        self.assertEqual(decoded.clock_epoch, CLOCK)
        self.assertEqual(decoded.calibration, camera.calibration.sha256)
        self.assertEqual(built.source.clock_id, observation.rgb.stamp.clock_id)
        self.assertEqual(built.source.captured_us, observation.capture_interval)
        self.assertEqual(built.input_kind, 'SYNTHETIC')
        self.assertFalse(decoded.localization.quality)
        self.assertFalse(built.world_valid or built.metric_ready or built.live_authority)
        self.assertFalse(result.summary['world_valid'] or result.summary['live_authority'])
        self.assertIsNone(result.summary['input_odom_origin'])
        self.assertEqual(original['clock_id'], observation.rgb.stamp.clock_id)


if __name__ == '__main__':
    unittest.main()
