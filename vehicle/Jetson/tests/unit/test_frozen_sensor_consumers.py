"""원 M-main admission의 frozen view를 계산 소비 경계에 연결하는 회귀다.

시계·센서·정합은 기존 명시 software fixture다. 원 codec, admission, Scene/World
consumer와 마지막 한 사례의 실제 SQLite를 사용하지만 실물 source 승인은 뜻하지 않는다.
owner thread를 바꾸거나 원 record/시각을 새 admission으로 재발급하지 않는다.
"""

from copy import copy
from dataclasses import replace
from hashlib import sha256
import os
from types import SimpleNamespace
import unittest
from uuid import uuid4

import test_mapper_mapping as mapping_fixture
import test_mapper_scene as scene_fixture
import test_mapper_sensor_state as sensor_fixture
import test_mapper_world as world_fixture
from xenova_drive_world import decode, encode, schema
from xenova_vehicle.mapping.free_schedule import stopping_region
from xenova_vehicle.mapping.local_map import GridSpec
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.tof import tof_evidence
from xenova_vehicle.runtime.map_checkpoint import (
    MapCheckpointCollector, MapCheckpointNotReady, _reference_vehicle,
)
from xenova_vehicle.runtime.mapper_scene import SceneStationWitness
from xenova_vehicle.runtime.mapper_sensor_state import SensorFrameSnapshot
from xenova_vehicle.runtime.scene import SceneError, SceneEvidenceScope
from xenova_vehicle.storage.contracts import StorageJob, ProvenanceRange, canonical
from xenova_vehicle.storage.records import PROVENANCE
from xenova_vehicle.transport.sensor_samples import FrozenSensorSamples
from xenova_vehicle.transport.world_adapter import WorldBuildError


def uid():
    return str(uuid4())


class FrozenSensorConsumerTests(unittest.TestCase):
    def fixture(self, kind=sensor_fixture.MapperSensorStateTests):
        result = kind(methodName='runTest')
        self.addCleanup(result.doCleanups)
        result.setUp()
        return result

    def snapshot(self, f, *, now=None, source_bytes=None, item=None):
        now = f.now if now is None else now
        raw = canonical(f.snapshot()) if source_bytes is None else source_bytes
        f.state.ingest(raw, now_us=now)
        return f.state.frame_snapshot(f.stamp, now_us=now,
            expires_us=now+100_000 if item is None else item.expires_us,
            job_id=uid() if item is None else item.job_id,
            source_generation=f.raw.sha256 if item is None else item.source_generation,
            source_key=('rgb_video', 12, 400_000, 500_000) if item is None else item.source_key)

    @staticmethod
    def collector(f, now):
        # 외부 D의 원 COMMITTED range는 명시 typed fixture다. 이 helper는 SQL을 주장하지 않는다.
        scope = SceneEvidenceScope(f.context, f.raw, f.calibration, 'e'*64)
        job = StorageJob(f.context, uid(), f.now-100, f.now+500_000)
        grant = ProvenanceRange(job, uid(), 1, 257, 257, f.now-50)
        return MapCheckpointCollector(scope, grant, now_us=now)

    def test_current_reference_and_stopping_region_require_exact_frozen_membership(self):
        f = self.fixture()
        view = self.snapshot(f)
        self.assertIs(type(view), SensorFrameSnapshot)
        self.assertIs(type(view.sensors), FrozenSensorSamples)
        inputs = view.metric_inputs
        scope = SceneEvidenceScope(f.context, f.raw, f.calibration, 'e'*64)
        _reference_vehicle(scope, inputs.vehicle, view.sensors, 100_000, f.now)
        region = stopping_region(GridSpec(32, 32, .1, (-1.6, -1.6)), inputs.vehicle,
            inputs.pose, f.calibration, admission=view.sensors, frame_id='odom:'+f.context.odom_epoch,
            now_us=f.now, max_speed_mps=.5, min_braking_mps2=1., reaction_latency_us=0,
            cycle_bound_us=10_000, completion_bound_us=20_000, body_error_m=.01)
        self.assertTrue(region.valid_at(f.now))
        self.assertFalse(region.valid_at(region.valid_until_us))
        foreign = replace(inputs.vehicle)
        self.assertEqual(foreign, inputs.vehicle)
        self.assertFalse(view.sensors.current_vehicle(foreign, f.now))
        with self.assertRaises(MapCheckpointNotReady):
            _reference_vehicle(scope, foreign, view.sensors, 100_000, f.now)
        fake = SimpleNamespace(context=view.sensors.context, current_vehicle=lambda *args: True)
        with self.assertRaises(MapCheckpointNotReady):
            _reference_vehicle(scope, inputs.vehicle, fake, 100_000, f.now)
        with self.assertRaises(ValueError):
            stopping_region(region.spec, inputs.vehicle, inputs.pose, f.calibration,
                admission=fake, frame_id=region.frame_id, now_us=f.now, max_speed_mps=.5,
                min_braking_mps2=1., reaction_latency_us=0, cycle_bound_us=10_000,
                completion_bound_us=20_000, body_error_m=.01)

    def test_historical_bracket_parent_is_not_current_vehicle_and_foreign_record_is_rejected(self):
        f = self.fixture()
        view = self.snapshot(f)
        inputs = view.metric_inputs
        bracket = view.original_pose_bracket(inputs.pose)
        original = bracket.originals[0]
        later = inputs.vehicle.valid_until_j_us
        self.assertFalse(view.sensors.current_vehicle(inputs.vehicle, later))
        collector = self.collector(f, later)
        parent, context = collector.vehicle_admission_parent(original, sensor_state=view)
        self.assertEqual(PROVENANCE.unpack(parent.payload)[6:8], original.evidence.pi_interval)
        self.assertEqual(parent.context_sha256, context.sha256)
        self.assertIs(view.validate_vehicle_admission(original), original.evidence)
        with self.assertRaises(MapCheckpointNotReady):
            collector.vehicle_parent(inputs.vehicle, admission=view.sensors, now_us=later)
        with self.assertRaises(ValueError):
            collector.vehicle_admission_parent(replace(original), sensor_state=view)
        fake = SimpleNamespace(validate_vehicle_admission=lambda row: row.evidence)
        with self.assertRaises(MapCheckpointNotReady):
            collector.vehicle_admission_parent(original, sensor_state=fake)

    def test_scene_station_window_keeps_original_snapshot_records_after_owner_progress(self):
        f = self.fixture(scene_fixture.MapperSceneInputsTests)
        view = self.snapshot(f.f, item=f.mapping.original_job)
        mapping = replace(f.mapping, inputs=view.metric_inputs)
        draft = f.coordinator.prepare_frame(mapping, f.result, view,
            world_sequence=1, hazard_revision=1, now_j_us=f.now)
        self.assertTrue(draft.observed, draft.reason)
        self.assertIs(type(draft.station), SceneStationWitness)
        witness = draft.station
        self.assertIs(witness.sensor_state, view)
        self.assertTrue(all(any(row is known for known in view.vehicle_admissions)
            for row in witness.originals))
        # 새 원 Vehicle의 main admission이 기존 snapshot의 latest/원 창을 고치지 않는다.
        updated = f.f.snapshot()
        updated['vehicles'].append(f.f.row(encode(f.f.fixture.vehicle(3, 1_120_000, .004)), received=f.now+1))
        f.f.state.ingest(canonical(updated), now_us=f.now+2)
        self.assertIsNot(f.f.state.sensors.latest_vehicle, view.sensors.latest_vehicle)
        witness.validate(witness.observation, f.f.context, f.profile, f.now)
        foreign = copy(witness)
        object.__setattr__(foreign, 'originals', (replace(witness.originals[0]), *witness.originals[1:]))
        with self.assertRaises(ValueError):
            foreign.validate(foreign.observation, f.f.context, f.profile, f.now)
        f.coordinator.cancel(draft)
        with self.assertRaises(SceneError):
            SceneStationWitness.from_frame(SimpleNamespace(**vars(f.f.state)),
                witness.observation, f.f.context, f.profile, now_j_us=f.now)

    def test_returned_clock_renewal_does_not_remap_old_station_admissions(self):
        f = self.fixture(scene_fixture.MapperSceneInputsTests)
        first = self.snapshot(f.f, item=f.mapping.original_job)
        originals = first.vehicle_admissions
        intervals = tuple(row.evidence.j_interval for row in originals)
        original_leases = tuple(row.evidence.clock_lease for row in originals)
        probe = decode(f.f.probe_raw, schema.MapperClockProbe)
        probe.mapper_trust.lower_offset_us += 1000
        probe.mapper_trust.upper_offset_us += 1000
        wire = encode(probe)
        key = sha256(wire).hexdigest()
        source = f.f.snapshot()
        source['probes'][key] = f.f.row(wire, key=key, received=f.now+1)
        source['active_probe_sha256'] = key
        view = self.snapshot(f.f, now=f.now+2, source_bytes=canonical(source), item=f.mapping.original_job)
        self.assertEqual(tuple(row.evidence.j_interval for row in view.vehicle_admissions), intervals)
        self.assertTrue(all(row is original for row, original in zip(view.vehicle_admissions, originals, strict=True)))
        self.assertTrue(all(row.evidence.clock_lease is original
            for row, original in zip(view.vehicle_admissions, original_leases, strict=True)))
        mapping = replace(f.mapping, inputs=view.metric_inputs,
            frame=replace(f.mapping.frame, clock_lease=view.metric_inputs.clock_lease))
        draft = f.coordinator.prepare_frame(mapping, f.result, view,
            world_sequence=1, hazard_revision=1, now_j_us=f.now+2)
        self.assertIsNotNone(draft.station, draft.reason)
        self.assertEqual(tuple(row.evidence.j_interval for row in draft.station.originals), intervals)
        f.coordinator.cancel(draft)

    def test_world_and_tof_consume_frozen_view_without_accepting_equal_foreign_evidence(self):
        f = self.fixture(world_fixture.MapperWorldTests)
        view = self.snapshot(f.fixture, item=f.item)
        inputs = view.metric_inputs
        draft = replace(f.draft, inputs=inputs,
            frame=replace(f.draft.frame, clock_lease=inputs.clock_lease, reference_vehicle=inputs.vehicle))
        built = f.build(draft=draft, sensors=view.sensors, current_vehicle=inputs.vehicle,
            clock_lease=inputs.clock_lease)
        self.assertIsNotNone(built)
        self.assertEqual(built.message().reference_odom_seq, inputs.vehicle.anchor.sequence)
        self.assertIsNone(f.build(draft=draft, sensors=view.sensors,
            current_vehicle=replace(inputs.vehicle), clock_lease=inputs.clock_lease))
        with self.assertRaises(WorldBuildError):
            f.build(draft=draft, sensors=SimpleNamespace(**vars(f.sensor_owner)))
        evidence = inputs.tof[0]
        leaf = f.fixture.leaves[evidence.sensor_id][1]
        result = tof_evidence(evidence, leaf, f.calibration, admission=view.sensors, now_us=f.now)
        self.assertIs(result.stamp, evidence.stamp)
        with self.assertRaises(ValueError):
            tof_evidence(replace(evidence), leaf, f.calibration, admission=view.sensors, now_us=f.now)

    @unittest.skipUnless(os.name == 'posix', '실제 SQLite/lock fixture는 대상 POSIX에서 검사한다')
    def test_mapping_checks_original_snapshot_job_before_issuing_frame_provenance(self):
        f = self.fixture(mapping_fixture.MapperMappingTests)
        f.grant()
        item, observation = f.item()
        view = self.snapshot(f.fixture, source_bytes=f.source_bytes, item=item)
        pipeline = PerceptionPipeline(f.fixture.calibration, f.profile.pipeline, targets=f.profile.targets)
        draft = f.mapping.prepare_frame(item, pipeline, observation, view.metric_inputs, view, f.now)
        self.assertTrue(draft.ready, draft.reason)
        f.mapping.cancel(draft)
        other, other_observation = f.item()
        cursor = f.mapping.collector._next
        with self.assertRaises(ValueError):
            f.mapping.prepare_frame(other, pipeline, other_observation, view.metric_inputs, view, f.now)
        self.assertEqual(f.mapping.collector._next, cursor)


if __name__ == '__main__':
    unittest.main()
