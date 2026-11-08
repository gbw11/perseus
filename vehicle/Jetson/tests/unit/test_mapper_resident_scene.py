"""실제 ResidentMapperProcessor의 Scene 부정 연결을 검사한다.

원 wire grant·SensorState·zero NV12/native RIGHT depth를 재사용한다. 보정/시계는
기존 명시 software fixture이며 GT fit, 정지 창, FREE, FROZEN/eligible를 주입하지 않는다.
마지막 시험은 실제 public revoke 연결을 관찰하며 이미 eligible인 실차 양성 수락이 아니다.
"""

from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch
from uuid import UUID

import test_mapper_processor_mapping as mapping_fixture
from test_mapper_runtime_profile import uid
from xenova_drive_world import encode, schema
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_profile import MapperProfile
from xenova_vehicle.runtime.mapper_publication import MapperPublicationPort
from xenova_vehicle.storage.contracts import canonical, json_bytes


def checkpoint(*_): return None


class MapperResidentSceneTests(unittest.TestCase):
    def setUp(self):
        self.base = mapping_fixture.MapperProcessorMappingTests(methodName='runTest')
        self.addCleanup(self.base.doCleanups)
        self.base.setUp()
        self.runtime = self.base.fixture
        fields = json_bytes(self.base.config.canonical_bytes, 65536)
        # 12개 실제 SurveyProfile 필드를 모두 명시한다. 실물 수락 기본값이 아니다.
        fields['survey'] = dict(coverage_profile_sha256='e'*64, required_sensor_mask=3,
            atlas_cell_capacity=fields['pipeline']['grid']['width']*fields['pipeline']['grid']['height'],
            station_window_us=100_000, station_distance_m=.1, station_motion_m=.001,
            station_yaw_rad=.001, max_pose_gap_us=self.base.config.sensors.max_gap_us,
            max_anchor_position_m=.01, max_anchor_heading_rad=.01,
            observation_max_age_us=min(500_000, self.base.config.pipeline.max_observation_age_us),
            input_kind='LIVE')
        self.config = MapperProfile(fields)
        self.publications = MapperPublicationPort(self.base.port)
        fixture = self.runtime.fixture
        self.processor = ResidentMapperProcessor(self.runtime.calibration_bytes, self.runtime.raw,
            clock=lambda:self.runtime.now, mapper_profile_bytes=self.config.canonical_bytes,
            source_policy=self.runtime.source_policy, clock_policy=fixture.policy,
            mapper_worker=fixture.mapper_worker, mapper_instance=fixture.mapper_instance,
            mapping_context_port=self.base.port, mapping_mailbox=self.base.mailbox,
            publication_port=self.publications)
        self.base.processor = self.processor
        self.base.config = self.config
        self.base.grant()
        self.scene_row = None

    def adopt(self, number, *, snapshot=None):
        item = self.runtime.item(number, snapshot=snapshot)
        result = json.loads(self.processor.geometry(item, None, checkpoint))['latest']
        self.processor.accepted_at(item, self.runtime.now)
        return item, result

    def establish_original_grant(self):
        self.adopt(1)
        inputs = self.processor._latest_source_inputs
        self.assertIsNotNone(inputs.clock_lease)
        self.assertIsNotNone(inputs.pose)
        source, lease = inputs.source, inputs.clock_lease
        now = self.runtime.now
        issued = max(1, lease.j_interval_to_pi((now, now), now)[0]-1000)
        origin = self.base.context.geometry_origin_fields
        value = schema.SceneGrant(major=1, vehicle=source['vehicle'], pi_host_boot=origin['pi_host_boot'],
            gateway_instance=origin['gateway_instance'], source_admission_revision=lease.source_revision,
            mapper_device=source['device'], issued_pi_us=issued, valid_until_pi_us=issued+14_400_000_000,
            lifecycle_state=0, request_nonce=UUID(uid(810)).bytes,
            survey_deadline_pi_us=issued+180_000_000, max_observation_stations=24, purpose=0,
            frozen_survey_revision=0, frozen_hazard_revision=0, lifecycle_revision=1,
            **{key:source[key] for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')})
        value.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(uid(811)).bytes, admission_revision=1,
            survey_id=UUID(uid(812)).bytes, survey_revision=1, policy=1,
            coverage_profile_sha256=bytes.fromhex(self.config.survey.coverage_profile_sha256),
            allow_restore_after_mapper_restart=True))
        self.publish_grant(value)
        self.processor.maintenance()
        self.assertIsNotNone(self.processor._scene)
        self.assertEqual(self.processor._scene.scene.grant_bytes, encode(value))
        self.assertFalse(self.processor._scene.scene.eligible)
        return value

    def publish_grant(self, value):
        inputs = self.processor._latest_source_inputs
        self.scene_row = dict(raw=b64encode(encode(value)).decode('ascii'),
            received_j_us=str(self.runtime.now),
            clock_probe_sha256=sha256(inputs.clock_lease.raw_probe_bytes).hexdigest())
        self.base.port.publish_scene(canonical(self.scene_row))

    def map_snapshot(self):
        core = self.processor._accepted.pipeline
        return core, core.grid.snapshot(self.runtime.now, clock_id=core.grid.clock_id)

    def assert_preserved_map(self, core, before):
        import numpy as np
        self.assertIs(self.processor._accepted.pipeline, core)
        after = core.grid.snapshot(self.runtime.now, clock_id=core.grid.clock_id)
        for key in ('frame_id','revision','calibration_id','clock_id'):
            self.assertEqual(after[key], before[key])
        for key, value in before['arrays'].items():
            np.testing.assert_array_equal(after['arrays'][key], value)

    def assert_no_positive_report(self):
        self.assertIsNone(self.publications.peek('WORLD_CANDIDATE', self.runtime.now))
        report = self.publications.peek('SURVEY_SUMMARY', self.runtime.now)
        if report is not None:
            from xenova_drive_world import decode
            self.assertNotIn(decode(report.raw, schema.SurveySummary).status, (1,2,4))
        scene = self.processor._scene
        self.assertFalse(scene.scene.eligible)
        self.assertIsNone(scene.scene._frozen)
        self.assertIsNone(scene._freeze_request)
        self.assertIsNone(self.processor._scene_freeze_request)

    def test_actual_grant_and_native_empty_images_do_not_invent_station_free_frozen_or_world(self):
        import numpy as np
        value = self.establish_original_grant()
        freeze = schema.SceneGrant(); freeze.CopyFrom(value)
        freeze.lifecycle_state=1; freeze.lifecycle_revision+=1; freeze.request_nonce=UUID(uid(813)).bytes
        inputs=self.processor._latest_source_inputs
        freeze.survey_deadline_pi_us=inputs.clock_lease.j_interval_to_pi(
            (self.runtime.now,self.runtime.now),self.runtime.now)[0]+10_000_000
        self.publish_grant(freeze)
        self.processor.maintenance()
        snapshot = self.runtime.fixture.snapshot(); snapshot['scene'] = deepcopy(self.scene_row)
        item, summary = self.adopt(2, snapshot=snapshot)
        self.processor.maintenance()
        self.assertEqual(self.processor._scene.scene.grant_bytes, encode(freeze))
        self.assertEqual(item.sample.depth.payload, bytes(640*480*2))
        self.assertTrue(summary['mapping_ready'])
        self.assertFalse(summary['world_candidate_ready'])
        self.assertEqual(self.processor._scene.scene._stations, ())
        self.assertIsNone(self.processor._scene.scene._observation)
        state = self.processor._accepted.pipeline.grid.state
        self.assertEqual(int(np.count_nonzero(state == 1)), 0)
        self.assertIsNone(self.base.mailbox.main_peek())
        self.assertIsNone(self.processor._mapping.pending)
        self.assert_no_positive_report()

    def test_clockless_original_snapshot_uses_diagnostic_core_preserves_map_and_revokes_scene_clock(self):
        self.establish_original_grant()
        core, before = self.map_snapshot()
        collector = self.processor._mapping.collector
        cursor = collector._next
        coordinator = self.processor._scene
        snapshot = self.runtime.fixture.snapshot()
        snapshot.update(probes={}, active_probe_sha256=None, scene=deepcopy(self.scene_row))
        _, summary = self.adopt(2, snapshot=snapshot)
        with patch.object(coordinator, 'revoke', wraps=coordinator.revoke) as revoke:
            self.processor.maintenance()
        revoke.assert_called_with('CLOCK', now_j_us=self.runtime.now)
        self.assertTrue(summary['preserved_metric_map'])
        self.assertIn('PI_J_CLOCK_UNAVAILABLE', summary['metric_input_reasons'])
        self.assertIsNone(self.processor._latest_source_inputs.clock_lease)
        self.assertIsNot(self.processor._diagnostic.pipeline, core)
        self.assertIs(self.processor._mapping.collector, collector)
        self.assertEqual(collector._next, cursor)
        self.assertEqual(self.processor._scene_status, 'WAIT_SCENE_CLOCK')
        self.assertEqual(coordinator.scene.blocked_reason, 'CLOCK')
        self.assert_preserved_map(core, before)
        self.assert_no_positive_report()

    def test_explicit_absent_scene_port_calls_actual_revoke_without_resetting_raw_map_or_allocator(self):
        self.establish_original_grant()
        core, before = self.map_snapshot()
        owner = self.processor._mapping; collector=owner.collector; coordinator=self.processor._scene
        original=(owner._revision,collector._next,collector.grant,owner.catalogs._head,self.processor._binding)
        context = self.base.port.read()
        self.base.port.publish_scene(None)
        with patch.object(coordinator, 'revoke', wraps=coordinator.revoke) as revoke:
            self.processor.maintenance()
        revoke.assert_called_with('SOURCE', now_j_us=self.runtime.now)
        self.assertIs(self.processor._scene, coordinator)
        self.assertEqual(self.processor._scene_status, 'WAIT_SCENE_GRANT')
        self.assertEqual(coordinator.scene.blocked_reason, 'SOURCE')
        self.assertEqual((owner._revision,collector._next,collector.grant,owner.catalogs._head,self.processor._binding),original)
        self.assertIs(self.base.port.read(), context)
        self.assertIs(self.processor._binding, self.runtime.raw)
        self.assertIs(owner.collector, collector)
        self.assert_preserved_map(core, before)
        self.assert_no_positive_report()
