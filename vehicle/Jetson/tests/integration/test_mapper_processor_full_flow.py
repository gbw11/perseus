"""실제 Processor normal path와 원 D/Scene/World 채택을 잇는 software 통합 시험.

카메라·차량·시계의 물리 수락 대신 명시 software bench 입력을 사용한다. 원 pixels만
core에 넣으며 builder/등록/최종 채택/Scene eligible를 patch하거나 GT로 채우지 않는다.
SQL과 S/M seqpacket, compute/JIO thread와 최종 outbox는 실제이며 DB copy는 실제 codec의 소유 bytes다.
원 World 기록은 실제 JIO final claim 뒤 FakeClient.publish를 호출할 때만 생성한다.
native SHM/token·실물 OAK·MQTT broker·주행 허가/성능 검증은 별도 범위다.
"""

import os
import socket
import threading
import unittest

from mapper_processor_flow_rig import ProcessorFlowRig, uid
from xenova_drive_world import decode, schema
from xenova_vehicle.mapping.local_map import FREE, UNKNOWN
from xenova_vehicle.mapping.free_schedule import stopping_region
from xenova_vehicle.runtime.mapper_catalogs import MapperCatalogs
from xenova_vehicle.runtime.mapper_mapping import MapperMapping
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.storage.contracts import StorageJob, canonical, json_bytes
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload


@unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'SOCK_SEQPACKET'),
    'actual SQLite writer and Linux S/M seqpacket required')
class MapperProcessorFullFlowTests(unittest.TestCase):
    def rig(self, **kwargs):
        value = ProcessorFlowRig.__new__(ProcessorFlowRig)
        self.addCleanup(value.close)
        value.__init__(**kwargs)
        return value

    def assert_world(self, rig, world):
        self.assertEqual(world.major, 1)
        self.assertEqual(world.required_features, 11)
        self.assertEqual(world.mapper_instance, rig.mapper_instance)
        self.assertEqual(world.clock_epoch, rig.raw.fields['clock_epoch'])
        self.assertEqual(world.reference_odom_seq,
            rig.last_item.sensor_snapshot.metric_inputs.vehicle.anchor.sequence)
        self.assertEqual(len(world.lane_model.lanes), 2)
        ids = tuple(row.lane_id for row in world.lane_model.lanes)
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(value > 0 for value in ids))
        self.assertIsNotNone(rig.processor._accepted.pipeline._alignment)
        self.assertTrue(rig.processor._accepted.latest_summary['localization_qualified'])
        self.assertEqual(rig.processor._mapping.catalogs.view.road_basis.status, 'READY')
        self.assertIsNotNone(rig.processor._mapping._atlas)
        self.assertTrue(rig.commits)
        self.assertTrue(rig.loads)
        durable_ids = tuple(row[0] for row in rig.db.connection.execute(
            "SELECT DISTINCT stable_id FROM catalog_versions WHERE map_epoch=? AND kind='LANE' ORDER BY stable_id",
            (world.map_epoch,)))
        self.assertEqual(tuple(sorted(ids)), durable_ids)
        raw, expires, publication_sha = rig.jio.worlds[-1]
        self.assertEqual(decode(raw, schema.WorldSnapshot), world)
        self.assertEqual(publication_sha, rig.supervisor.owner.binding_sha256)
        self.assertGreater(expires, rig.now)
        self.assertIsNone(rig.publications.peek('WORLD_CANDIDATE', now_us=rig.now))
        self.assertTrue(rig.jio.snapshot()['alive'])
        self.assertFalse(rig.jio.transport._thread.daemon)
        self.assertEqual(len(rig.jio.io_thread_ids), 1)
        self.assertGreaterEqual(rig.jio.snapshot()['counts']['world_dispatch_started'], len(rig.jio.worlds))
        self.assertIsNone(rig.jio.snapshot()['failure'])
        self.assertFalse(rig.last_summary['live_authority'])
        self.assertTrue(rig.last_result.accepted)
        self.assertEqual(rig.sensor_owner._thread,threading.get_ident())
        self.assertNotEqual(rig.sensor_owner._thread,rig.compute._thread.ident)
        self.assertFalse(hasattr(rig.processor,'_accepted_sensors'))
        # 실제 원 입력+현재 alignment의 보수 pose로도 ROI가 정지영역 전체를 담아야 한다.
        inputs = rig.processor._latest_source_inputs
        core = rig.processor._accepted.pipeline
        limits = rig.config.stopping
        region = stopping_region(core.grid.spec, inputs.vehicle, core._alignment.apply(inputs.pose),
            rig.calibration, admission=rig.last_item.sensor_snapshot.sensors,
            frame_id=core.grid.frame_id, now_us=rig.now, max_speed_mps=limits.max_speed_mps,
            min_braking_mps2=limits.min_braking_mps2, reaction_latency_us=limits.reaction_latency_us,
            cycle_bound_us=limits.cycle_bound_us, completion_bound_us=limits.completion_bound_us,
            body_error_m=limits.body_error_m)
        self.assertTrue(region.complete, region.bounds_xy)

    def test_empty_atlas_actual_pixels_registration_sql_ids_adoption_and_world_outbox(self):
        rig = self.rig()
        world = rig.world()
        self.assert_world(rig, world)
        self.assertEqual(rig.config.sensors.tof_sources, ())
        self.assertTrue(any(request.payload.evidence.reservations for request, _ in rig.commits))
        self.assertIsNone(rig.mapper.publication['scene_admission_ref'])
        self.assertGreater(rig.processor._mapping.collector._next,
            rig.processor._mapping.collector.grant.start_inclusive)
        # FREE 유무는 실제 연속 certificate에 맡긴다. 그리드 전체를 빈 공간으로 채우지 않는다.
        self.assertIn(255, world.occupancy)

    def test_actual_freeze_sql_complete_restart_fresh_validation_and_retained_world(self):
        old = self.rig()
        collecting = old.grant()
        self.assert_world(old, old.world())
        admitted, frozen = old.freeze_and_admit(collecting)
        self.assertGreater(decode(frozen.summary, schema.SurveySummary).positive_cell_count, 0)
        old_world = old.world()
        self.assertTrue(old.processor._scene.scene.eligible)
        old_context = old.mapper.storage_context
        old_ids = tuple(row.lane_id for row in old_world.lane_model.lanes)
        old_highwater = old.db.connection.execute(
            'SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone()[0]
        old.stop()
        fresh = self.rig(database=old.db, predecessor=old)
        self.assertNotEqual(fresh.mapper_worker, old.mapper_worker)
        self.assertEqual(fresh.binding, old.binding)
        self.assertEqual(fresh.bootstrap_fields['allocator_highwater'], old_highwater)
        self.assertEqual(fresh.mapper.storage_context.map_epoch, old_context.map_epoch)
        complete = fresh.bootstrap._complete
        self.assertIsNotNone(complete)
        self.assertIsNotNone(complete.original_commit)
        self.assertEqual(complete.original_job.context.mapper_worker, old.mapper_worker)
        self.assertIsNone(fresh.processor)
        restored_admitted, complete = fresh.restore_and_admit(admitted)
        world = fresh.world()
        self.assert_world(fresh, world)
        self.assertEqual(tuple(row.lane_id for row in world.lane_model.lanes), old_ids)
        self.assertEqual(world.map_epoch, old_world.map_epoch)
        self.assertEqual(restored_admitted.valid_until_pi_us, admitted.valid_until_pi_us)
        self.assertEqual(world.scene.admission_id, admitted.scene.admission_id)
        self.assertEqual(fresh.processor._durable_source, complete)
        self.assertTrue(fresh.processor._scene.scene.eligible)
        self.assertGreaterEqual(fresh.processor._mapping.collector.grant.start_inclusive, int(old_highwater))

    def test_blank_rgb_never_substitutes_builder_or_issues_lane_ids_world(self):
        rig = self.rig()
        for _ in range(3):
            result = rig.frame(blank_rgb=True)
            self.assertTrue(result.accepted)
        self.assertFalse(rig.jio.worlds)
        self.assertIsNone(rig.processor._accepted.pipeline._alignment)
        self.assertIsNone(rig.processor._mapping.catalogs.view.lane)
        self.assertEqual(rig.last_summary['road_hypotheses'], 0)

    def test_missing_depth_is_unknown_and_cannot_create_free_or_frozen_scene(self):
        rig = self.rig()
        grant = rig.grant()
        rig.world(missing_depth=True)
        self.assertFalse(bool((rig.processor._accepted.pipeline.grid.state == FREE).any()))
        self.assertTrue(bool((rig.processor._accepted.pipeline.grid.state == UNKNOWN).all()))
        for _ in range(2):
            rig.frame(missing_depth=True)
        pending = decode(encode_grant(grant), schema.SceneGrant)
        pending.lifecycle_state = 1
        pending.lifecycle_revision += 1
        pending.request_nonce = bytes.fromhex(uid().replace('-', ''))
        rig.deliver_grant(pending)
        for _ in range(8):
            rig.tick()
        self.assertFalse(rig.processor._scene.scene.eligible)
        self.assertIsNone(rig.processor._scene.scene._frozen)

    def test_duplicate_missing_clock_foreign_source_and_expired_frame_publish_no_new_world(self):
        rig = self.rig()
        self.assert_world(rig, rig.world())
        count = len(rig.jio.worlds)
        last = rig.last_item
        duplicate = replace_input(last, context=rig.mapper.context_bytes(), job_id=uid(),
            sensor_owner=rig.sensor_owner,timing=rig.config.capture_timing,now_us=rig.now)
        rig.submit(duplicate)
        for _ in range(4):
            rig.tick()
        self.assertEqual(len(rig.jio.worlds), count)
        snapshot = json_bytes(rig.mapper.context_bytes(), 65536)
        snapshot['active_probe_sha256'] = None
        snapshot['probes'] = {}
        rig.submit(rig.input(context=canonical(snapshot)))
        self.assertEqual(len(rig.jio.worlds), count)
        expired = replace_input(last, job_id=uid())
        rig.now = expired.expires_us
        self.assertFalse(rig.compute.submit(expired))
        self.assertEqual(len(rig.jio.worlds), count)
        foreign = json_bytes(rig.mapper.context_bytes(), 65536)
        foreign['publication']['raw_binding_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'source snapshot changed original binding/context/raw proof'):
            rig.submit(rig.input(context=canonical(foreign)))
        self.assertEqual(len(rig.jio.worlds), count)

    def test_original_completion_clock_exact_expiry_rejects_both_owner_encoders(self):
        rig = self.rig()
        context = rig.mapper.storage_context
        for kind in ('MAPPING', 'CATALOG'):
            with self.subTest(owner=kind):
                mailbox = MapperStorageMailbox(context.sha256)
                expires = rig.now + 100_000
                job = StorageJob(context, uid(), rig.now, expires)
                request = CheckpointRequest(job, CheckpointPayload.build(job, durable_cutoff_pi_us=rig.now + 100_000))
                self.assertTrue(mailbox.compute_offer(request, now_us=rig.now))
                dispatch = mailbox.main_take(current_context_sha256=context.sha256, now_us=rig.now)
                writer = rig._lease()
                mailbox.main_request_encode(dispatch, writer, issued_us=rig.now, expires_us=expires)
                if kind == 'MAPPING':
                    owner = MapperMapping(rig.port, mailbox, rig.raw, rig.calibration, rig.config,
                        initial_storage_material=rig.material, clock_us=lambda: expires)
                else:
                    owner = MapperCatalogs(context, mailbox=mailbox, clock_id=rig.raw.fields['clock_epoch'],
                        map_frame_id='odom:' + context.odom_epoch, body=rig.calibration.body,
                        method_manifest_sha256=rig.config.sha256, input_kind='LIVE',
                        calibration_profile=rig.calibration, clock_us=lambda: expires)
                owner.maintenance(now_us=rig.now)
                result = mailbox.main_take_encoding(dispatch)
                self.assertIsNotNone(result)
                self.assertEqual(result.completed_us, expires)
                self.assertEqual(result.error, 'STALE_OR_EXPIRED')
                self.assertIsNone(result.encoding)
                self.assertEqual(job.expires_us, expires)


def encode_grant(value):
    from xenova_drive_world import encode
    return encode(value)


def replace_input(original, *, context=None, job_id=None,sensor_owner=None,timing=None,now_us=None):
    from xenova_vehicle.runtime.mapper_compute import MapperInput
    item=MapperInput(original.job_id if job_id is None else job_id, original.source_generation,
        original.sample, original.expires_us, parser_cap_us=original.parser_cap_us,
        context_bytes=original.context_bytes if context is None else context)
    if sensor_owner is None:return item
    from xenova_vehicle.runtime.resident_workers import attach_sensor_snapshot
    sensor_owner.ingest(item.context_bytes,now_us=now_us)
    return attach_sensor_snapshot(item,sensor_owner,timing,now_us=now_us)
