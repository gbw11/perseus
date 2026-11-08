"""M의 원 source snapshot을 센서/전체 capture pose로 잇는 계약 회귀.

센서 wire·CRC·시계 offset은 기존 software fixture를 재사용한다. 실제 카메라,
Pi 시계 수락이나 보정 승인을 시험하지 않으며 callback을 측정시각으로 쓰지 않는다.
"""

from base64 import b64encode
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import unittest
from uuid import UUID

import test_sensor_samples as sensor_fixtures
from raw_binding_fixtures import binding_fields
from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import ClockPolicy
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.runtime.mapper_sensor_state import MapperSensorState
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.storage.contracts import StorageContext, canonical
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin


class MapperSensorStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = sensor_fixtures.SensorSampleTests(
            'test_vehicle_bundle_profile_exact_anchor_and_original_interval')
        self.fixture.setUp()
        f = self.fixture
        self.now = f.now
        self.mapper_worker = sensor_fixtures.identity()
        self.mapper_instance = f.source['mapper_instance']
        self.second_leaf = replace(f.leaf, sensor_id=1)
        local = f.calibration.to_dict()
        local['external_leaves']['second-test-leaf'] = self.second_leaf.sha256
        self.calibration = CalibrationProfile.from_dict(local)
        self.leaves = {0: ('front-test-leaf', f.leaf), 1: ('second-test-leaf', self.second_leaf)}
        raw = binding_fields()
        raw.update(clock_epoch=f.source['clock_epoch'], calibration_bundle=self.calibration.sha256)
        self.raw = RawBinding(raw)
        self.policy = ClockPolicy(vehicle=f.source['vehicle'], device=f.source['device'],
            configuration=f.source['configuration'], calibration=f.source['calibration'],
            primitive=f.source['primitive'], timestamp_error_us=0, drift_ppm=0,
            max_offset_width_us=100, lease_us=1_000_000, vehicle_max_age_us=200_000,
            lookback_us=500_000)
        self.binding = dict(service_instance=self.raw.fields['service_instance'],
            mapper_worker=self.mapper_worker, mapper_instance=self.mapper_instance,
            raw_binding_sha256=self.raw.sha256,
            **{key: f.source[key] for key in ('map_epoch', 'frame_epoch', 'odom_epoch',
                'configuration', 'calibration', 'primitive')},
            scene_admission_ref=None, source_binding_revision='1')
        self.origin = dict(pi_host_boot=f.pi_boot, gateway_instance=f.gateway,
            device_session=f.device, pi_session=f.pi_session)
        geometry = PiGeometryOrigin(vehicle=f.source['vehicle'], **self.origin,
            odom_epoch=f.source['odom_epoch'], calibration=f.source['calibration'])
        self.context = StorageContext(sensor_fixtures.identity(), self.mapper_worker,
            self.mapper_instance, f.source['map_epoch'], f.source['frame_epoch'],
            f.source['odom_epoch'], f.pi_boot, f.source['configuration'],
            f.source['calibration'], f.source['primitive'], canonical(self.binding),
            geometry_origin=geometry.fields)
        self.probe_raw = f.lease.raw_probe_bytes
        self.probe_key = sha256(self.probe_raw).hexdigest()
        self.vehicles = (encode(f.vehicle()), encode(f.vehicle(2, 1_110_000, .002)))
        self.tofs = (encode(f.tof()), encode(f.tof(sensor_id=1,
            calibration=self.second_leaf.sha256,
            sensor_instance=UUID(sensor_fixtures.identity()).bytes)))
        self.stamp = FrameStamp(self.raw.fields['acquisition_worker'],
            self.raw.fields['stream_instances']['rgb_video'], 12,
            f.source['clock_epoch'], (999_800, 1_000_200), self.now)
        self.state = self.owner()

    def owner(self, *, raw=None):
        return MapperSensorState(self.calibration, self.raw if raw is None else raw,
            self.policy, tof_leaves=self.leaves, vehicle_max_age_us=200_000,
            tof_max_age_us=200_000, max_speed_mps=.5, max_yaw_rate_rps=2.,
            max_gap_us=100_000, capacity=16, mapper_worker=self.mapper_worker,
            mapper_instance=self.mapper_instance)

    def row(self, wire, *, key=None, received=1_020_000):
        return dict(raw=b64encode(wire).decode('ascii'), received_j_us=str(received),
            clock_probe_sha256=self.probe_key if key is None else key)

    def snapshot(self):
        return dict(version=1, publication=deepcopy(self.binding),
            source=dict(self.fixture.source), origin=dict(self.origin),
            context_sha256=self.context.sha256, storage_context=self.context.fields,
            active_probe_sha256=self.probe_key,
            vehicles=[self.row(wire, received=1_015_000 + index)
                for index, wire in enumerate(self.vehicles)],
            probes={self.probe_key: self.row(self.probe_raw, received=1_010_000)},
            tof={str(index): self.row(wire, received=1_025_000 + index)
                for index, wire in enumerate(self.tofs)}, scene=None)

    @staticmethod
    def update_context(snapshot, **changes):
        fields = deepcopy(snapshot['storage_context'])
        fields.update(changes)
        context = StorageContext.from_fields(fields)
        snapshot['storage_context'] = context.fields
        snapshot['context_sha256'] = context.sha256
        return context

    def read(self, snapshot=None, *, owner=None, stamp=None, now=None):
        return (self.state if owner is None else owner).read(
            canonical(self.snapshot() if snapshot is None else snapshot),
            self.stamp if stamp is None else stamp,
            now_us=self.now if now is None else now)

    def test_full_capture_uses_original_vehicle_anchor_interval_and_callback_metadata(self):
        result = self.read()
        self.assertEqual(result.storage_context, self.context)
        self.assertEqual(result.source, self.fixture.source)
        self.assertEqual(result.clock_lease.raw_probe_bytes, self.probe_raw)
        self.assertIsNotNone(result.pose)
        self.assertEqual(result.pose.captured_us, (999_800, 1_000_200))
        self.assertEqual(result.pose.bracket_us, (990_000, 1_010_000))
        self.assertEqual(result.pose.sample.at_us, 1_000_000)
        self.assertAlmostEqual(result.pose.sample.x, .001, places=8)
        self.assertEqual(result.pose.sample.calibration_id, self.calibration.sha256)
        self.assertEqual(result.pose.sample.origin, 'ODOMETRY')
        vehicle = result.vehicle
        self.assertEqual(vehicle.wire, self.vehicles[1])
        self.assertEqual(vehicle.anchor.sequence, 2)
        self.assertEqual(vehicle.anchor.source_sample, 12)
        self.assertEqual(vehicle.anchor.source_us, 440_000)
        self.assertEqual(vehicle.anchor.pi_sample_us, 1_110_000)
        self.assertEqual(vehicle.anchor.odom_epoch, self.fixture.source['odom_epoch'])
        self.assertAlmostEqual(vehicle.anchor.odom_pose[0], .002, places=8)
        self.assertEqual(vehicle.anchor.calibration_bundle_sha256, 'a' * 64)
        self.assertNotEqual(vehicle.anchor.calibration_bundle_sha256, self.calibration.sha256)
        self.assertEqual(vehicle.pi_interval, (1_109_800, 1_110_200))
        self.assertEqual(vehicle.j_interval, (1_009_790, 1_010_210))
        self.assertEqual(tuple(item.sensor_id for item in result.tof), (0, 1))
        self.assertEqual(result.tof[0].j_interval, (999_690, 1_000_310))
        self.assertEqual(result.tof[0].wire, self.tofs[0])
        expected = tuple(('VEHICLE', sha256(wire).hexdigest(), 1_015_000 + index)
            for index, wire in enumerate(self.vehicles)) + tuple(
            ('TOF', sha256(wire).hexdigest(), 1_025_000 + index)
            for index, wire in enumerate(self.tofs))
        self.assertEqual(result.delivery_metadata, expected)
        self.assertEqual(result.reasons, ())

    def test_duplicates_and_normal_lease_renewal_keep_buffer_and_original_evidence(self):
        first = self.read()
        buffer = self.state.poses
        original_samples = tuple(buffer._samples)
        duplicate_snapshot = self.snapshot()
        for row in duplicate_snapshot['vehicles'] + list(duplicate_snapshot['tof'].values()):
            row['received_j_us'] = str(self.now + 1)
        duplicate = self.read(duplicate_snapshot, now=self.now + 1)
        self.assertIs(self.state.poses, buffer)
        self.assertIs(duplicate.vehicle, first.vehicle)
        self.assertIs(duplicate.tof[0], first.tof[0])
        self.assertEqual(duplicate.delivery_metadata, ())
        new_probe = decode(self.probe_raw, schema.MapperClockProbe)
        new_probe.probe_seq += 1
        new_probe.pi_send_us = 1_170_000
        new_probe.mapper_trust.expires_pi_us = 2_050_000
        new_probe.mapper_trust.lower_offset_us = 99_995
        new_probe.mapper_trust.upper_offset_us = 100_005
        new_raw = encode(new_probe)
        new_key = sha256(new_raw).hexdigest()
        value = self.snapshot()
        value['probes'][new_key] = self.row(new_raw, key=new_key, received=1_075_000)
        value['active_probe_sha256'] = new_key
        renewed = self.read(value, now=1_080_000)
        self.assertEqual(renewed.clock_lease.raw_probe_bytes, new_raw)
        self.assertIs(self.state.poses, buffer)
        self.assertEqual(len(buffer._samples), 2)
        for old, current in zip(original_samples, buffer._samples):
            self.assertIs(current, old)
        self.assertIs(renewed.vehicle, first.vehicle)
        self.assertEqual(renewed.vehicle.j_interval, (1_009_790, 1_010_210))
        self.assertIs(renewed.vehicle.clock_lease, first.vehicle.clock_lease)
        self.assertIs(renewed.tof[0], first.tof[0])
        self.assertEqual(renewed.tof[0].j_interval, (999_690, 1_000_310))
        self.assertEqual(renewed.pose.captured_us, first.pose.captured_us)
        self.assertEqual(renewed.pose.bracket_us, first.pose.bracket_us)
        self.assertEqual(renewed.delivery_metadata, ())

    def test_pose_bracket_keeps_both_original_admissions_without_promoting_old_vehicle(self):
        result=self.read();bracket=self.state.original_pose_bracket(result.pose)
        self.assertIs(bracket.pose,result.pose)
        self.assertEqual(tuple(v.evidence.wire for v in bracket.originals),self.vehicles)
        self.assertEqual(tuple(v.evidence.pose_sample.at_us for v in bracket.originals),result.pose.bracket_us)
        self.assertEqual(tuple(v.admitted_j_us for v in bracket.originals),(self.now,self.now))
        self.assertEqual(tuple(v.received_j_us for v in bracket.originals),(1_015_000,1_015_001))
        self.assertIs(bracket.originals[1].evidence,result.vehicle)
        self.assertIsNot(bracket.originals[0].evidence,self.state.sensors.latest_vehicle)
        old=bracket.originals[0]
        duplicate=self.read(now=self.now+1)
        current=self.state.original_pose_bracket(duplicate.pose)
        self.assertIs(current.originals[0],old)
        self.assertEqual(old.evidence.j_interval,(989_790,990_210))
        self.assertEqual(old.evidence.clock_lease.raw_probe_bytes,self.probe_raw)
        self.assertEqual(len(self.state._vehicles),len(self.state.poses._samples))
        with self.assertRaises(ValueError):self.state.original_pose_bracket(result.pose)
        with self.assertRaises(ValueError):self.state.original_pose_bracket(replace(duplicate.pose))
        expired=self.read(now=1_900_000)
        self.assertIsNone(expired.pose)
        with self.assertRaises(ValueError):self.state.original_pose_bracket(duplicate.pose)

    def test_missing_returned_trust_and_expired_active_lease_never_make_pose(self):
        no_trust = decode(self.probe_raw, schema.MapperClockProbe)
        no_trust.ClearField('mapper_trust')
        raw = encode(no_trust)
        key = sha256(raw).hexdigest()
        value = self.snapshot()
        value['probes'] = {key: self.row(raw, key=key)}
        value['active_probe_sha256'] = key
        for row in value['vehicles'] + list(value['tof'].values()):
            row['clock_probe_sha256'] = key
        for label, snapshot, now in (('no-trust', value, self.now),
                ('expired', self.snapshot(), 1_900_000)):
            with self.subTest(boundary=label):
                owner = self.owner()
                original = self.read(owner=owner)
                buffer = owner.poses
                result = self.read(snapshot, owner=owner, now=now)
                self.assertIsNone(result.clock_lease)
                self.assertIsNone(result.pose)
                self.assertIsNone(result.vehicle)
                self.assertEqual(result.tof, ())
                self.assertIn('PI_J_CLOCK_UNAVAILABLE', result.reasons)
                self.assertIs(owner.poses, buffer)
                self.assertIs(owner.sensors.latest_vehicle, original.vehicle)
                self.assertEqual(original.vehicle.j_interval, (1_009_790, 1_010_210))

    def test_raw_origin_and_full_context_mismatches_reject_before_sensor_creation(self):
        cases = []
        value = self.snapshot()
        value['publication']['raw_binding_sha256'] = 'e' * 64
        self.update_context(value, source_binding=value['publication'])
        cases.append(('raw', value))
        value = self.snapshot()
        value['origin']['gateway_instance'] = sensor_fixtures.identity()
        cases.append(('origin', value))
        value = self.snapshot()
        value['publication']['service_instance'] = sensor_fixtures.identity()
        self.update_context(value, source_binding=value['publication'])
        cases.append(('service', value))
        value = self.snapshot()
        self.update_context(value, frame_epoch=sensor_fixtures.identity())
        cases.append(('context-flat', value))
        for key, other in (('configuration', 'other-config'), ('calibration', 'e' * 64),
                ('primitive', 'e' * 64)):
            value = self.snapshot()
            value['publication'][key] = other
            changes = dict(source_binding=value['publication'])
            changes['primitive_sha256' if key == 'primitive' else key] = other
            if key == 'calibration':
                geometry = deepcopy(value['storage_context']['geometry_origin'])
                geometry['calibration'] = other
                changes['geometry_origin'] = geometry
            self.update_context(value, **changes)
            cases.append(('consistent-foreign-' + key, value))
        for label, value in cases:
            with self.subTest(boundary=label):
                owner = self.owner()
                with self.assertRaises(ValueError):
                    self.read(value, owner=owner)
                self.assertIsNone(owner.sensors)
                self.assertIsNone(owner.poses)
        raw = self.raw.fields
        raw['calibration_bundle'] = 'e' * 64
        foreign = RawBinding(raw)
        value = self.snapshot()
        value['publication']['raw_binding_sha256'] = foreign.sha256
        self.update_context(value, source_binding=value['publication'])
        with self.assertRaises(ValueError):
            self.read(value, owner=self.owner(raw=foreign))

    def test_hash_consistent_foreign_mapper_cannot_replace_registered_compute_owner(self):
        for key in ('mapper_worker', 'mapper_instance'):
            with self.subTest(identity=key):
                value = self.snapshot()
                other = sensor_fixtures.identity()
                value['publication'][key] = other
                if key == 'mapper_instance':
                    value['source'][key] = other
                self.update_context(value, source_binding=value['publication'],
                    **{('origin_mapper_instance' if key == 'mapper_instance' else key): other})
                owner = self.owner()
                with self.assertRaises(ValueError):
                    self.read(value, owner=owner)
                self.assertIsNone(owner.sensors)

    def test_clockless_reset_revokes_only_original_tof_and_preserves_odom_buffer(self):
        original = self.read()
        buffer = self.state.poses
        original_samples = tuple(buffer._samples)
        reset = self.fixture.tof(source_sample=1, sensor_generation=2,
            sensor_instance=UUID(sensor_fixtures.identity()).bytes)
        raw = encode(reset)
        value = self.snapshot()
        value['active_probe_sha256'] = None
        value['tof'] = {'0': self.row(raw)}
        value['tof']['0']['clock_probe_sha256'] = None
        result = self.read(value, now=self.now + 1)
        self.assertIsNone(result.clock_lease)
        self.assertIsNone(result.pose)
        self.assertIsNone(self.state.sensors.latest_tof(0))
        self.assertFalse(self.state.sensors.current_tof(original.tof[0], self.now + 1))
        self.assertIs(self.state.sensors.latest_tof(1), original.tof[1])
        self.assertIs(self.state.sensors.latest_vehicle, original.vehicle)
        self.assertTrue(self.state.sensors.current_vehicle(original.vehicle, self.now + 1))
        self.assertIs(self.state.poses, buffer)
        self.assertEqual(tuple(buffer._samples), original_samples)
        event, = result.sensor_resets
        self.assertEqual(event.sensor_id, 0)
        self.assertEqual(event.previous_instance, original.tof[0].sensor_instance)
        self.assertEqual(event.sensor_instance, str(UUID(bytes=reset.sensor_instance)))
        self.assertEqual(event.sensor_generation, 2)
        self.assertEqual(event.wire_sha256, sha256(raw).hexdigest())
        self.assertEqual(self.state.sensors.context.source['odom_epoch'], self.fixture.source['odom_epoch'])
        again = self.read(value, now=self.now + 2)
        self.assertEqual(again.sensor_resets, ())

    def test_misindexed_clockless_reset_cannot_mutate_another_sensor(self):
        original = self.read()
        reset = self.fixture.tof(sensor_id=1, calibration=self.second_leaf.sha256,
            source_sample=1, sensor_generation=2,
            sensor_instance=UUID(sensor_fixtures.identity()).bytes)
        value = self.snapshot()
        value['active_probe_sha256'] = None
        value['tof'] = {'0': self.row(encode(reset))}
        value['tof']['0']['clock_probe_sha256'] = None
        with self.assertRaises(ValueError):
            self.read(value, now=self.now + 1)
        self.assertIs(self.state.sensors.latest_tof(0), original.tof[0])
        self.assertIs(self.state.sensors.latest_tof(1), original.tof[1])
        self.assertEqual(self.state.sensors.take_resets(), ())

    def test_contradictory_odom_quarantines_metric_pose_without_resetting_control_source(self):
        first = self.read()
        buffer = self.state.poses
        original_samples = tuple(buffer._samples)
        value = self.snapshot()
        bad = encode(self.fixture.vehicle(3, 1_120_000, 1.))
        value['vehicles'].append(self.row(bad, received=1_045_000))
        result = self.read(value)
        self.assertIsNotNone(result.clock_lease)
        self.assertEqual(result.storage_context, first.storage_context)
        self.assertEqual(result.source, first.source)
        self.assertIsNone(result.pose)
        self.assertIsNone(result.vehicle)
        self.assertIn('ODOMETRY_MOTION_CONTRADICTION', result.reasons)
        self.assertIs(self.state.poses, buffer)
        self.assertEqual(tuple(buffer._samples), original_samples)
        self.assertEqual(self.state.sensors.context.source['odom_epoch'], self.fixture.source['odom_epoch'])
        self.assertEqual(result.sensor_resets, ())
        again = self.read(value, now=self.now + 1)
        self.assertIsNone(again.pose)
        self.assertIsNone(again.vehicle)
        self.assertIsNotNone(again.clock_lease)

    def test_capture_midpoint_alone_cannot_authorize_unbracketed_or_unknown_hull(self):
        self.read()
        buffer = self.state.poses
        for hull in ((980_000, 1_020_000), None):
            with self.subTest(capture=hull):
                stamp = replace(self.stamp, captured_us=hull)
                result = self.read(stamp=stamp)
                self.assertIsNone(result.pose)
                self.assertIsNotNone(result.vehicle)
                self.assertIsNotNone(result.clock_lease)
                self.assertIn('FULL_CAPTURE_POSE_UNAVAILABLE', result.reasons)
                self.assertIs(self.state.poses, buffer)
                self.assertEqual(result.delivery_metadata, ())


if __name__ == '__main__':
    unittest.main()
