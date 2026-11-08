"""fresh admission과 지연된 원 capture query의 수명 경계를 검사한다.

MotionFixture의 명시 software Vehicle/returned-trust bytes를 쓴다. 여기의 literal J/Pi
시각은 단위 시험 가정이며 실제 FD 4시각·부하·HIL 수락 증거가 아니다. 원 RGB/RIGHT
capture hull/E는 바꾸지 않는다. snapshot 폐기는 Compute.cancel 실행을 대신하지 않는다.
"""

from base64 import b64encode
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import threading
import unittest

import test_simulation_motion as motion_fixture
import test_simulation_motion_profile as motion_profiles
from xenova_drive_world import decode, encode, schema
from xenova_vehicle.runtime.mapper_sensor_state import MapperSensorState
from xenova_vehicle.storage.contracts import StorageContext, canonical


class AdmissionFixture:
    """20ms/0.002m의 원 source와 SHA별 최초 callback을 한 번만 생산한다."""

    def __init__(self, *, capacity=32):
        self.original = motion_fixture.MotionFixture(profile=motion_profiles.motion_profile(pose_capacity=capacity))
        self.observation = self.original.observation()
        # DevelopmentMotion.read와 같은 실제 RGB/RIGHT 전체 hull이다. 새 측정시각은 없다.
        self.stamp = replace(self.observation.rgb.stamp,
            captured_us=self.observation.capture_interval,
            available_us=max(self.observation.rgb.stamp.available_us,
                             self.observation.depth.stamp.available_us))
        self.expires = 1_499_800
        self.job_id = motion_fixture.identifier(700)
        self.generation = self.original.raw.sha256
        self.source_key = self.observation.rgb.stamp.key
        base = self.original.snapshot()
        self.probes = OrderedDict(base['probes'])
        self.active_probe = base['active_probe_sha256']
        self.wires = list(self.original.vehicles)
        self.deliveries = [dict(row) for row in base['vehicles']]

    def owner(self):
        f = self.original
        return MapperSensorState.for_development(f.profile, f.raw, f.policy,
            sensor_context=f.sensor_context, mapper_worker=f.mapper_worker,
            mapper_instance=f.source['mapper_instance'])

    @staticmethod
    def sample_j(index): return 990_000 + index * 20_000

    @classmethod
    def admitted_j(cls, index):
        return 1_020_000 if index < 2 else cls.sample_j(index) + 5_000

    def _ensure(self, last):
        for index in range(len(self.wires), last + 1):
            at = self.sample_j(index)
            if index % 10 == 0:
                # 긴 capacity 시험의 명시 software 갱신이다. 과거 row의 key/시각은 유지한다.
                probe = decode(self.original.probe, schema.MapperClockProbe)
                probe.probe_seq += index // 10
                probe.nonce = (probe.probe_seq).to_bytes(16, 'big')
                probe.pi_send_us = at + 100_000 + 2_000
                probe.mapper_trust.expires_pi_us = probe.pi_send_us + 1_000_000
                raw = encode(probe)
                key = sha256(raw).hexdigest()
                self.probes[key] = dict(raw=b64encode(raw).decode('ascii'),
                    received_j_us=str(at + 3_000), clock_probe_sha256=key)
                self.active_probe = key
                while len(self.probes) > 8:
                    self.probes.popitem(last=False)
            raw = self.original.vehicle(index + 1, 1_090_000 + index * 20_000, index * .002)
            self.wires.append(raw)
            self.deliveries.append(dict(raw=b64encode(raw).decode('ascii'),
                received_j_us=str(at + 4_000), clock_probe_sha256=self.active_probe))

    def body(self, last):
        self._ensure(last)
        value = self.original.snapshot(vehicles=())
        value['vehicles'] = [dict(row) for row in self.deliveries[max(0, last - 23):last + 1]]
        value['probes'] = {key: dict(row) for key, row in self.probes.items()}
        value['active_probe_sha256'] = self.active_probe
        return value

    def ingest_through(self, owner, last):
        result = owner.ingest(canonical(self.body(1)), now_us=self.admitted_j(1))
        for index in range(2, last + 1):
            result = owner.ingest(canonical(self.body(index)), now_us=self.admitted_j(index))
        return result

    def query(self, owner, now):
        return owner.frame_snapshot(self.stamp, now_us=now, expires_us=self.expires,
            job_id=self.job_id, source_generation=self.generation, source_key=self.source_key)

    def validate(self, snapshot, now):
        return snapshot.validate_frame(job_id=self.job_id, source_generation=self.generation,
            source_key=self.source_key, expires_us=self.expires, capture_stamp=self.stamp, now_us=now)


class SensorAdmissionLifecycleTests(unittest.TestCase):
    def assert_source_order(self, fixture, admissions):
        for record in admissions:
            value = record.evidence.message()
            self.assertEqual(value.pi_sample_us - value.source_us, 700_000)
            self.assertEqual(record.evidence.pose_sample.at_us, value.pi_sample_us - 100_000)
            self.assertLessEqual(record.evidence.j_interval[1], record.received_j_us)
            self.assertLessEqual(record.received_j_us, record.admitted_j_us)
            self.assertEqual(record.received_j_us,
                int(fixture.deliveries[value.sequence - 1]['received_j_us']))
            self.assertAlmostEqual(value.odom_pose.x, (value.sequence - 1) * .002, places=6)
            self.assertAlmostEqual(value.velocity_mps, .1, places=7)

    def test_fresh_ingest_keeps_original_bracket_after_330ms_but_first_late_ingest_cannot(self):
        f = AdmissionFixture()
        owner = f.owner()
        f.ingest_through(owner, 16)
        now = 1_330_000
        snapshot = f.query(owner, now)
        inputs = snapshot.metric_inputs
        self.assertEqual(inputs.pose.captured_us, f.observation.capture_interval)
        self.assertEqual(inputs.pose.bracket_us, (990_000, 1_010_000))
        self.assertEqual(inputs.pose.sample.origin, 'SYNTHETIC_ODOMETRY')
        bracket = snapshot.original_pose_bracket(inputs.pose)
        self.assertEqual(tuple(row.evidence.wire for row in bracket.originals), tuple(f.wires[:2]))
        self.assertEqual(tuple(row.admitted_j_us for row in bracket.originals), (1_020_000, 1_020_000))
        self.assertIsNotNone(inputs.vehicle)
        self.assertEqual(inputs.vehicle.anchor.sequence, 17)
        self.assertTrue(snapshot.sensors.current_vehicle(inputs.vehicle, now))
        self.assertFalse(snapshot.sensors.current_vehicle(bracket.originals[0].evidence, now))
        self.assertIs(f.validate(snapshot, now), snapshot)
        self.assert_source_order(f, snapshot.vehicle_admissions)
        self.assertEqual(tuple(row.evidence.message().source_us for row in snapshot.vehicle_admissions),
                         tuple(390_000 + index * 20_000 for index in range(17)))

        late = f.owner()
        late.ingest(canonical(f.body(16)), now_us=now)
        late_snapshot = f.query(late, now)
        self.assertIsNone(late_snapshot.metric_inputs.pose)
        self.assertIn('FULL_CAPTURE_POSE_UNAVAILABLE', late_snapshot.metric_inputs.reasons)
        self.assertNotIn(f.wires[0], tuple(row.evidence.wire for row in late_snapshot.vehicle_admissions))
        self.assertEqual(late_snapshot.metric_inputs.vehicle.anchor.sequence, 17)
        self.assertEqual(f.stamp.captured_us, (999_800, 1_000_200))
        self.assertEqual(snapshot.expires_us, f.expires)

    def test_discarding_unadopted_snapshot_does_not_rollback_owner_or_reissue_old_rows(self):
        f = AdmissionFixture()
        owner = f.owner()
        f.ingest_through(owner, 1)
        candidate = f.query(owner, 1_050_000)
        originals = candidate.vehicle_admissions
        del candidate  # 소비 후보 폐기만 한다. 존재하지 않는 cancel API나 owner 상태 조작은 없다.
        newer = owner.ingest(canonical(f.body(3)), now_us=f.admitted_j(3))
        head = newer.vehicle
        retained = owner.vehicle_admissions
        self.assertEqual(head.anchor.sequence, 4)
        self.assertIs(retained[0], originals[0])
        self.assertIs(retained[1], originals[1])
        replay = f.body(3)
        replay['vehicles'] = [dict(row) for row in f.deliveries[:2]]
        result = owner.ingest(canonical(replay), now_us=1_060_000)
        self.assertIs(result.vehicle, head)
        self.assertEqual(owner.vehicle_admissions, retained)
        again = f.query(owner, 1_070_000)
        bracket = again.original_pose_bracket(again.metric_inputs.pose)
        self.assertIs(bracket.originals[0], originals[0])
        self.assertIs(bracket.originals[1], originals[1])
        self.assertEqual(tuple(row.received_j_us for row in originals), (1_015_000, 1_015_001))

    def test_capacity_eviction_preserves_issued_refs_and_rejects_replay_or_fresh_counter_regression(self):
        for capacity in (2, 3, 256):
            with self.subTest(capacity=capacity):
                f = AdmissionFixture(capacity=capacity)
                owner = f.owner()
                f.ingest_through(owner, 1)
                issued = f.query(owner, 1_050_000)
                pose = issued.metric_inputs.pose
                bracket = issued.original_pose_bracket(pose)
                originals = bracket.originals
                original_wires = tuple(row.evidence.wire for row in originals)
                # seen-cache도 지나는 수명이다. source callback과 원 lease key는 재발급하지 않는다.
                last = max(69, capacity + 1)
                for index in range(2, last + 1):
                    admitted = max(1_055_000, f.admitted_j(index))
                    result = owner.ingest(canonical(f.body(index)), now_us=admitted)
                self.assertEqual(len(owner.vehicle_admissions), capacity)
                self.assertEqual(result.vehicle.anchor.sequence, last + 1)
                self.assertNotIn(originals[0], owner.vehicle_admissions)
                with self.assertRaises(ValueError):
                    owner.original_vehicle_admission(originals[0].evidence)
                for record in originals:
                    self.assertIs(issued.validate_vehicle_admission(record), record.evidence)
                self.assertIs(issued.original_pose_bracket(pose), bracket)
                self.assertEqual(tuple(row.evidence.wire for row in bracket.originals), original_wires)

                held = owner.vehicle_admissions
                head = result.vehicle
                replay = f.body(last)
                replay['vehicles'] = [dict(f.deliveries[0])]
                now = max(1_055_000, f.admitted_j(last)) + 1_000
                self.assertIs(owner.ingest(canonical(replay), now_us=now).vehicle, head)
                self.assertEqual(owner.vehicle_admissions, held)

                # 원 old byte를 retime하지 않는다. 별도 불량 sender의 fresh seq 회귀도 검산한다.
                index = last + 1
                bad = decode(f.original.vehicle(index + 1, 1_090_000 + index * 20_000, index * .002),
                             schema.VehicleSample)
                bad.sequence = 1
                wire = encode(bad)
                conflict = f.body(last)
                conflict['vehicles'] = [dict(raw=b64encode(wire).decode('ascii'),
                    received_j_us=str(f.sample_j(index) + 4_000), clock_probe_sha256=f.active_probe)]
                rejected = owner.ingest(canonical(conflict), now_us=f.admitted_j(index))
                self.assertIs(rejected.vehicle, head)
                self.assertIn('VEHICLE_UNAVAILABLE', rejected.reasons)
                self.assertEqual(owner.vehicle_admissions, held)
                self.assertIs(issued.metric_inputs.pose, pose)
                self.assertFalse(issued.sensors.current_vehicle(issued.metric_inputs.vehicle,
                    f.admitted_j(index)))
                with self.assertRaises(ValueError):
                    f.validate(issued, f.admitted_j(index))

    def test_current_vehicle_expiry_does_not_erase_already_admitted_historical_pose(self):
        f = AdmissionFixture()
        owner = f.owner()
        f.ingest_through(owner, 1)
        first = f.query(owner, 1_050_000)
        old_vehicle = first.metric_inputs.vehicle
        old_records = first.vehicle_admissions
        stale_now = 1_330_000
        snapshot = f.query(owner, stale_now)
        self.assertIsNone(snapshot.metric_inputs.vehicle)
        self.assertIn('VEHICLE_UNAVAILABLE', snapshot.metric_inputs.reasons)
        self.assertIsNotNone(snapshot.metric_inputs.clock_lease)
        self.assertIsNotNone(snapshot.metric_inputs.pose)
        self.assertEqual(snapshot.metric_inputs.pose.bracket_us, (990_000, 1_010_000))
        self.assertEqual(snapshot.vehicle_admissions, old_records)
        self.assertFalse(snapshot.sensors.current_vehicle(old_vehicle, stale_now))
        self.assertEqual(tuple(row.admitted_j_us for row in owner.vehicle_admissions),
                         (1_020_000, 1_020_000))

    def test_renewed_lease_preserves_old_admission_and_changed_source_origin_or_clock_cannot_reset(self):
        f = AdmissionFixture()
        owner = f.owner()
        f.ingest_through(owner, 1)
        before = f.query(owner, 1_050_000)
        records = owner.vehicle_admissions
        evidence = records[-1].evidence
        probe = decode(f.original.probe, schema.MapperClockProbe)
        probe.probe_seq += 1
        probe.nonce = probe.probe_seq.to_bytes(16, 'big')
        probe.pi_send_us = 1_170_000
        probe.mapper_trust.expires_pi_us = 2_050_000
        raw = encode(probe)
        key = sha256(raw).hexdigest()
        renewal = f.body(1)
        renewal['probes'][key] = dict(raw=b64encode(raw).decode('ascii'), received_j_us='1075000',
                                     clock_probe_sha256=key)
        renewal['active_probe_sha256'] = key
        owner.ingest(canonical(renewal), now_us=1_080_000)
        after = f.query(owner, 1_080_000)
        self.assertIs(after.metric_inputs.vehicle, evidence)
        self.assertEqual(owner.vehicle_admissions, records)
        self.assertIs(after.vehicle_admissions[0], records[0])
        self.assertEqual(after.vehicle_admissions[0].evidence.j_interval,
                         before.vehicle_admissions[0].evidence.j_interval)
        self.assertIs(after.vehicle_admissions[0].evidence.clock_lease,
                      before.vehicle_admissions[0].evidence.clock_lease)
        self.assertEqual(tuple(row.admitted_j_us for row in records), (1_020_000, 1_020_000))
        self.assertEqual(tuple(row.received_j_us for row in records), (1_015_000, 1_015_001))
        self.assertEqual(after.expires_us, before.expires_us)
        self.assertIn(sha256(f.original.probe).hexdigest(), tuple(proof[0] for proof in after.clock_proofs))

        changed_geometry = motion_fixture.MotionFixture(profile=f.original.profile,
            odom_epoch=motion_fixture.identifier(999)).snapshot()
        changed_origin = deepcopy(renewal)
        changed_origin['origin']['gateway_instance'] = motion_fixture.identifier(998)
        changed_origin['storage_context']['geometry_origin']['gateway_instance'] = motion_fixture.identifier(998)
        changed_origin['context_sha256'] = StorageContext.from_fields(changed_origin['storage_context']).sha256
        changed_clock = deepcopy(renewal)
        changed_clock['source']['clock_epoch'] = motion_fixture.identifier(997)
        for changed in (changed_geometry, changed_origin, changed_clock):
            with self.assertRaises(ValueError):
                owner.ingest(canonical(changed), now_us=1_090_000)
            self.assertEqual(owner.vehicle_admissions, records)
            self.assertIs(owner.sensors.latest_vehicle, evidence)
        with self.assertRaises(ValueError):
            f.validate(after, f.expires)

    def test_owner_rejects_other_thread_but_published_snapshot_reads_keep_original_membership(self):
        f = AdmissionFixture()
        owner = f.owner()
        f.ingest_through(owner, 1)
        snapshot = f.query(owner, 1_050_000)
        pose = snapshot.metric_inputs.pose
        originals = snapshot.original_pose_bracket(pose).originals
        ready, release = threading.Event(), threading.Event()
        observed = []
        failures = []
        def reader():
            try:
                ready.set()
                if not release.wait(2):
                    raise AssertionError('test owner did not release immutable reader')
                for operation in (
                    lambda: owner.ingest(canonical(f.body(3)), now_us=1_060_000),
                    lambda: f.query(owner, 1_060_000),
                ):
                    with self.assertRaisesRegex(RuntimeError, 'original main owner'):
                        operation()
                values = snapshot.metric_inputs
                self.assertIs(values.pose, pose)
                self.assertIs(snapshot.original_pose_bracket(values.pose).originals[0], originals[0])
                for record in originals:
                    self.assertIs(snapshot.original_vehicle_admission(record.evidence), record)
                    self.assertIs(snapshot.validate_vehicle_admission(record), record.evidence)
                with self.assertRaises(ValueError):
                    snapshot.validate_vehicle_admission(replace(originals[0]))
                observed.append((values.pose, snapshot.vehicle_admissions,
                                 tuple(row.evidence.wire for row in originals)))
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=reader, name='immutable-sensor-snapshot-reader', daemon=False)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            advanced = owner.ingest(canonical(f.body(3)), now_us=f.admitted_j(3))
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        if failures:
            raise failures[0]
        self.assertEqual(len(observed), 1)
        self.assertIs(observed[0][0], pose)
        self.assertIs(observed[0][1][0], originals[0])
        self.assertIs(owner.sensors.latest_vehicle, advanced.vehicle)
        self.assertEqual(advanced.vehicle.anchor.sequence, 4)
        self.assertEqual(tuple(row.evidence.wire for row in originals), observed[0][2])


if __name__ == '__main__':
    unittest.main()
