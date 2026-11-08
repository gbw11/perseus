"""실제 Scene freeze/SQLite COMMITTED/ADMITTED 뒤의 좁은 retained 투영 회귀.

조사·보정·센서 값은 기존 SYNTHETIC fixture다. 현재 Mapping/Scene/Hazard DTO는
projection 경계의 software 입력이며 실제 알고리즘/정지/World 이동권위를 수락하지 않는다.
eligible는 직접 켜지 않고 실제 Scene lifecycle로만 얻는다.
"""

from copy import copy
from dataclasses import replace
from hashlib import sha256
import sys
from types import SimpleNamespace
import unittest

import test_scene as fixtures
from xenova_drive_world import decode, encode, schema
from xenova_vehicle.mapping.local_map import EvidenceGrid, FREE, OCCUPIED, GridSpec
from xenova_vehicle.mapping.map_lattice import RollingPolicy
from xenova_vehicle.mapping.rolling_grid import configure_grid
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.inputs import ImageFrame, PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline, PipelineProfile, PerceptionResult
from xenova_vehicle.runtime.atlas_proof import AtlasProofWindow
from xenova_vehicle.runtime.map_checkpoint import FrameEvidence, _pipeline_state
from xenova_vehicle.runtime.mapper_atlas import MapperAtlas
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_hazard import HazardDraft, HazardSnapshot
from xenova_vehicle.runtime.mapper_mapping import MappingFrameDraft
from xenova_vehicle.runtime.mapper_scene import SceneDraft
from xenova_vehicle.runtime.mapper_sensor_state import MetricInputs
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.retained_world import RetainedWorldError, project_retained_grid
from xenova_vehicle.runtime.scene import SceneEvidenceScope
from xenova_vehicle.runtime.source_identity import scene_ref_fields
from xenova_vehicle.storage.contracts import StorageJob, canonical, json_bytes
from xenova_vehicle.storage.geometry_scope import CatalogGeometryScope
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CELL, CheckpointPayload, TileVersion


@unittest.skipUnless(sys.platform == 'linux', '실제 기존 D SQLite owner는 대상 Linux에서 검증한다')
class RetainedWorldTests(unittest.TestCase):
    def setUp(self):
        self.case = self.admit()

    def admit(self, *, rolling=False, metadata=True):
        import numpy as np
        f = fixtures.MapperSceneTests(methodName='runTest'); self.addCleanup(f.doCleanups); f.setUp()
        spec = GridSpec(16 if rolling else 2, 16 if rolling else 2, .05, (0., 0.))
        profile = PipelineProfile(grid=spec)
        policy = RollingPolicy(4, 0) if rolling else None
        pipeline = PerceptionPipeline(f.calibration, profile, rolling_policy=policy)
        grid = EvidenceGrid(spec, frame_id='odom:' + f.context.odom_epoch, calibration_id=f.calibration.sha256,
            clock_id=f.raw.fields['clock_epoch'], kind='SYNTHETIC', occupied_ttl_us=10)
        pipeline.grid = grid
        domain = np.ones(grid.state.shape, dtype=bool); grid.set_domain(domain, domain)
        grid.add_free(((0, 0),), f.obs.stamp, view_id='explicit-original-synthetic-free',
                      origin_xy=(0., -.1), certificate_complete=True)
        tiles, evidence = f.copies(); owner = snapshot = archive = None
        if rolling:
            owner = MapperAtlas(f.context.map_epoch, spec, policy, reserved_workspace_bytes=4 * 1024 * 1024)
            configure_grid(grid, owner.window)
            with owner.read_tiles(owner.window) as (_, reader):
                proof = AtlasProofWindow(owner.window, reader)
            proof.write(0, 0, tiles[0].payload[:CELL.size])
            delta = owner.prepare_frame(grid, proof); owner.accept_frame(delta)
            snapshot = owner.checkpoint_snapshot(); tiles = snapshot.tiles
        references = tuple(sorted(tile.reference for tile in tiles))
        state = _pipeline_state(pipeline, f.context)
        # Collector.snapshot과 같이 원 proof metadata를 붙인다. 이 fixture에는 둘 다 없다.
        self.assertIsNone(pipeline.reference)
        self.assertIsNone(pipeline._alignment)
        state['reference_provenance'] = {}
        state['alignment_provenance'] = None
        state['tile_revision_highwater'] = max(tile.revision for tile in tiles)
        reference, pages = encode_mapper_state(state) if metadata else (None, ())
        evidence = replace(evidence, contexts=(*evidence.contexts, *pages))
        pending = decode(encode(f.grant), schema.SceneGrant)
        pending.lifecycle_state = 1; pending.lifecycle_revision = 2
        pending.request_nonce = fixtures.UUID(fixtures.uid(32)).bytes; pending.survey_deadline_pi_us = 1_500_000
        f.mapper.receive_grant(encode(pending), f.clock, now_j_us=f.now)
        job = StorageJob(f.context, fixtures.uid(23), f.now, 1_500_000, fixtures.uid(32))
        descriptor = f.mapper.freeze_scene(job, current_tile_references=references,
                                          current_hazard_revision=1, now_j_us=f.now)
        if reference is not None:
            descriptor['mapper_state'] = reference
        request = CheckpointRequest(job, CheckpointPayload.build(job, tiles, evidence, scene=descriptor,
            tile_references=references, durable_cutoff_pi_us=1_010_002))
        if rolling:
            owner.retain_checkpoint_owner(snapshot, request)
            archive = owner.retain_scene(snapshot, request)
        f.mapper.adopt_freeze(request, current_tile_references=references, current_hazard_revision=1,
            now_j_us=f.now, all_tiles=tuple(owner.iter_tiles(snapshot)) if rolling else tiles,
            mapper_state=reference, archive=archive)
        receipt = f.store.commit(job, request.payload, current_context_sha256=f.context.sha256)
        self.assertEqual(receipt.status, 'COMMITTED')
        self.assertTrue(f.mapper.complete_freeze(request, receipt, now_j_us=f.now + 20))
        if rolling:
            owner.complete_checkpoint(snapshot)
        frozen = decode(f.mapper._frozen.summary, schema.SurveySummary)
        admitted = decode(encode(pending), schema.SceneGrant)
        admitted.lifecycle_state = 2; admitted.lifecycle_revision = 3
        admitted.frozen_evidence_sha256 = frozen.evidence_sha256
        admitted.frozen_survey_revision = frozen.survey_revision
        admitted.frozen_hazard_revision = frozen.hazard_revision
        self.assertTrue(f.mapper.receive_grant(encode(admitted), f.clock, now_j_us=f.now + 30))
        self.assertTrue(f.mapper.eligible)
        binding = json_bytes(f.context.source_binding, 2048)
        binding.update(scene_admission_ref=scene_ref_fields(admitted.scene), source_binding_revision='2')
        context = replace(f.context, source_binding=canonical(binding))
        scope = SceneEvidenceScope(context, f.raw, f.calibration, f.scope.method_manifest_sha256,
                                   historical_scopes=(f.scope,))
        # 실제 ADMITTED 이후의 동일 geometry publication 경계다. eligibility/원 frozen은 그대로다.
        scene = copy(f.mapper); scene.context = context; scene.evidence_scope = scope
        return SimpleNamespace(f=f, spec=spec, profile=profile, policy=policy, owner=owner,
            archive=archive, request=request, context=context, scope=scope, scene=scene,
            admitted=admitted, published=f.now + 100)

    def bundle(self, case=None, *, modify=None, hazard_bits=None, global_bits=()):
        import numpy as np
        c = self.case if case is None else case; f = c.f; raw = f.raw.fields
        pipeline = PerceptionPipeline(f.calibration, c.profile, rolling_policy=c.policy)
        grid = EvidenceGrid(c.spec, frame_id='odom:' + c.context.odom_epoch, calibration_id=f.calibration.sha256,
            clock_id=raw['clock_epoch'], kind='SYNTHETIC', occupied_ttl_us=10)
        pipeline.grid = grid
        if c.owner is not None:
            configure_grid(grid, c.owner.window)
        if modify is not None:
            modify(grid)
        snapshot = grid.snapshot(c.published, clock_id=grid.clock_id)
        scene = copy(c.scene)
        current = fixtures.observation(sequence=2, at=1_010_010, calibration_id=f.calibration.sha256)
        observed = scene.observe(current, now_j_us=c.published - 1)
        self.assertTrue(observed)
        rgb = RawFrame('rgb_video', 2, 1_010_010, 1_010_010, c.published - 1,
                       640, 360, 'NV12', bytes(640 * 360 * 3 // 2), 0)
        depth = RawFrame('depth_right', 2, 1_010_011, 1_010_011, c.published - 1,
                         640, 480, 'RAW16_LE', bytes(640 * 480 * 2), 0)
        clocks = {name: dict(bridge_sample=dict(j_before_us=str(c.published - 1), sdk_us=str(c.published - 1),
            j_after_us=str(c.published - 1)), sdk_host_timestamp_j_bracket_us=[str(x) for x in current.raw_pose.captured_us])
            for name in ('rgb_video', 'depth_right')}
        sample = CaptureSample(rgb, depth, None, None, clocks)
        item = MapperInput(fixtures.uid(700), f.raw.sha256, sample, c.published + 100_000,
                           context_bytes=canonical(dict(context_sha256=c.context.sha256, storage_context=c.context.fields)))
        observation = PerceptionInput('SYNTHETIC', f.calibration.sha256,
            ImageFrame(current.stamp, 640, 360, 'NV12', rgb.payload),
            ImageFrame(replace(current.stamp, stream_id=raw['stream_instances']['depth_right']), 640, 480, 'RAW16_LE', depth.payload))
        frame = FrameEvidence(c.scope, observation, current.raw_pose.captured_us, (), 2, (), (), f.clock, f.now)
        inputs = MetricInputs(c.context, fixtures.source(), f.clock, current.raw_pose, None, (), (), (), ())
        result = PerceptionResult(dict(kind='SYNTHETIC', now_us=c.published, quarantined=False),
                                  None, None, None, None, snapshot, None)
        draft = MappingFrameDraft(item, c.context, pipeline, None, inputs, frame, None, 0, True, 'READY', result=result)
        scene_draft = SceneDraft(draft, result, scene, 0, observed, None, 'SOFTWARE_BOUNDARY',
                                 c.published + 90_000, c.published - 1)
        geometry = (CatalogGeometryScope.from_context(c.context, 'SLOT').bytes, c.scope.method_manifest_sha256,
            grid.frame_id, grid.calibration_id, grid.clock_id, grid.kind,
            grid.spec if grid.rolling_window is None else ('ROLLING', grid.rolling_window.lattice))
        size = grid.spec.width * grid.spec.height
        occupied = bytes((size + 7) // 8) if hazard_bits is None else hazard_bits
        hazard = HazardSnapshot(1, geometry, grid.spec, occupied, (), global_bits, grid.rolling_window)
        hazard_draft = HazardDraft(draft, True, 'SOFTWARE_BOUNDARY', hazard, False, False, False)
        return draft, scene_draft, hazard_draft

    def project(self, bundle, case=None, **changes):
        c = self.case if case is None else case; draft, scene, hazard = bundle
        args = dict(scene_draft=scene, hazard_draft=hazard, source=fixtures.source(),
                    clock_lease=c.f.clock, published_j_us=c.published, atlas_owner=c.owner)
        args.update(changes)
        return project_retained_grid(draft, **args)

    def test_actual_freeze_commit_grant_projects_original_free_without_live_mutation(self):
        import numpy as np
        bundle = self.bundle(); grid = bundle[0].pipeline.grid
        before = tuple(getattr(grid, name).tobytes() for name in ('state', 'observed_us', 'time_known', 'support', 'permission'))
        original = self.case.request.payload.tiles[0].payload
        result = self.project(bundle)
        self.assertIsNotNone(result)
        self.assertTrue(result.static_retained[0, 0])
        self.assertEqual(int(result.snapshot['arrays']['state'][0, 0]), FREE)
        self.assertTrue(result.snapshot['arrays']['support'][0, 0] and result.snapshot['arrays']['permission'][0, 0])
        pi_upper = self.case.f.clock.j_interval_to_pi((self.case.published, self.case.published), self.case.published)[1]
        expected = (pi_upper - CELL.unpack_from(original)[7] + 999) // 1000
        self.assertEqual(int(result.snapshot['arrays']['age_ms'][0, 0]), expected)
        self.assertEqual(result.scene_ref, encode(self.case.admitted.scene))
        self.assertEqual(result.grant_sha256, sha256(self.case.scene.grant_bytes).hexdigest())
        self.assertLessEqual(result.expires_j_us, bundle[1].expires_j_us)
        self.assertEqual(tuple(getattr(grid, name).tobytes() for name in ('state', 'observed_us', 'time_known', 'support', 'permission')), before)
        self.assertIs(self.case.request.payload.tiles[0].payload, original)
        self.assertFalse(result.snapshot['live_authority'] or result.snapshot['world_valid'])
        for array in (*result.snapshot['arrays'].values(), result.static_retained):
            self.assertFalse(array.flags.writeable)
            with self.assertRaises(ValueError): array.setflags(write=True)
        self.assertEqual(np.count_nonzero(result.static_retained), 1)

    def test_fresh_live_free_stays_live_and_real_age_is_never_reduced(self):
        import numpy as np
        def fresh(grid):
            domain = np.ones(grid.state.shape, dtype=bool); grid.set_domain(domain, domain)
            grid.add_free(((0, 0),), self.case.f.obs.stamp, view_id='current-synthetic-free',
                          origin_xy=(0., -.1), certificate_complete=True)
        bundle = self.bundle(modify=fresh)
        result = self.project(bundle)
        self.assertFalse(result.static_retained.any())
        self.assertEqual(result.snapshot['arrays']['age_ms'].tobytes(), bundle[0].result.map_snapshot['arrays']['age_ms'].tobytes())
        self.assertEqual(int(result.snapshot['arrays']['state'][0, 0]), FREE)
        def stale(grid):
            fresh(grid); grid.free_ttl_us = 1
        bundle = self.bundle(modify=stale)
        result = self.project(bundle)
        self.assertTrue(result.static_retained[0, 0])
        self.assertGreaterEqual(int(result.snapshot['arrays']['age_ms'][0, 0]),
                                int(bundle[0].result.map_snapshot['arrays']['age_ms'][0, 0]))

    def test_raw_expired_occupied_conflict_unordered_and_hazard_always_block_retained(self):
        def hit(grid):
            grid.add_hits(((.001, .001, .002, .002),), self.case.f.obs.stamp,
                          view_id='original-occupied', origin_xy=(0., -.1))
        for name, change in (('occupied', hit), ('conflict', lambda grid: grid.conflict.__setitem__((0, 0), True)),
                             ('unordered', lambda grid: grid.unordered_hit.__setitem__((0, 0), True))):
            with self.subTest(name=name):
                bundle = self.bundle(modify=change)
                if name == 'occupied':
                    self.assertEqual(int(bundle[0].pipeline.grid.state[0, 0]), OCCUPIED)
                    self.assertEqual(int(bundle[0].result.map_snapshot['arrays']['state'][0, 0]), 0)
                result = self.project(bundle)
                self.assertFalse(result.static_retained[0, 0])
                self.assertNotEqual(int(result.snapshot['arrays']['state'][0, 0]), FREE)
        result = self.project(self.bundle(hazard_bits=b'\x01'))
        self.assertFalse(result.static_retained[0, 0])

    def test_missing_archive_or_eligibility_is_not_ready_and_bad_binding_fails_closed(self):
        bundle = self.bundle(); scene = copy(bundle[1].scene); scene.revoke('SOURCE')
        self.assertIsNone(self.project((bundle[0], replace(bundle[1], scene=scene), bundle[2])))
        scene = copy(bundle[1].scene); scene._frozen = replace(scene._frozen, fixed_tiles=())
        self.assertIsNone(self.project((bundle[0], replace(bundle[1], scene=scene), bundle[2])))
        with self.assertRaises(RetainedWorldError):
            self.project(bundle, source=dict(fixtures.source(), vehicle='another-vehicle'))
        with self.assertRaises(RetainedWorldError):
            self.project((bundle[0], replace(bundle[1], original_mapping=replace(bundle[0])), bundle[2]))
        with self.assertRaises(RetainedWorldError):
            self.project((bundle[0], replace(bundle[1], observed=False), bundle[2]))
        with self.assertRaises(RetainedWorldError):
            self.project(bundle, published_j_us=bundle[1].expires_j_us)

    def test_legacy_mapper_state_and_foreign_fixed_tile_map_are_never_projected(self):
        legacy = self.admit(metadata=False)
        self.assertIsNone(self.project(self.bundle(legacy), legacy))
        bundle = self.bundle(); scene = copy(bundle[1].scene); frozen = scene._frozen
        tile = frozen.fixed_tiles[0]
        scene._frozen = replace(frozen, fixed_tiles=(TileVersion(fixtures.uid(999), tile.tile_x, tile.tile_y,
                                                               tile.revision, tile.payload),))
        with self.assertRaises(RetainedWorldError):
            self.project((bundle[0], replace(bundle[1], scene=scene), bundle[2]))

    def test_actual_rolling_archive_reads_only_current_window_without_staged_generation_change(self):
        case = self.admit(rolling=True); bundle = self.bundle(case)
        grid = bundle[0].pipeline.grid; owner = case.owner
        with owner.read_tiles(grid.rolling_window) as (_, reader):
            proof = AtlasProofWindow(grid.rolling_window, reader)
        delta = owner.prepare_frame(grid, proof)
        generation = owner.pool.generation
        result = self.project(bundle, case)
        self.assertTrue(result.static_retained[0, 0])
        self.assertEqual(owner.pool.generation, generation)
        owner.cancel_frame(delta)
        body = b'\x01' + bytes(511)
        result = self.project(self.bundle(case, global_bits=(((0, 0), body),)), case)
        self.assertFalse(result.static_retained[0, 0])
        self.assertNotEqual(int(result.snapshot['arrays']['state'][0, 0]), FREE)
