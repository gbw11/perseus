"""Atlas optional META와 현재 ROI proof hook의 유한 연결 회귀다.

기존 SQLite/FrameEvidence fixture를 재사용한다. rolling full CP 발행은 아직 미연결이며,
복원 fixture는 공개 저장/LOAD 경로로 만든 UNKNOWN EVIDENCE와 진단 META다. 이 fixture의
LIVE 배열/주소 검산을 센서 관측·현재 FREE·Scene/이동 사용권으로 해석하지 않는다.
"""

from dataclasses import asdict, replace
from hashlib import sha256
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_map_checkpoint as fixture_module
from xenova_vehicle.mapping.local_map import EvidenceGrid, GridSpec
from xenova_vehicle.mapping.map_lattice import GridWindow, MapLattice, RollingPolicy, atlas_state_fields
from xenova_vehicle.mapping.rolling_grid import configure_grid, ViewKey
from xenova_vehicle.perception.pipeline import PerceptionPipeline, PipelineProfile
from xenova_vehicle.runtime.atlas_proof import AtlasProofWindow
from xenova_vehicle.runtime.map_checkpoint import (MapCheckpointNotReady, MapRestoreNotReady,
    CheckpointPages, restore_pipeline, _pipeline_state, _pack_array, _gate_fields)
from xenova_vehicle.storage.contracts import LoadRequest, canonical, json_bytes
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.records import CELL, EvidenceTable, TileVersion, CheckpointPayload


UNKNOWN = CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)


@unittest.skipUnless(os.name == 'posix', '기존 실제 SQLite fixture를 사용함')
class AtlasCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.MapCheckpointTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def rolling_pipeline(self, *, foreign_anchor=False):
        f=self.fixture;profile=PipelineProfile(grid=GridSpec(8,8,.01,(-.005,-.005)))
        policy=RollingPolicy(1,1)
        pipeline=PerceptionPipeline(f.calibration,profile,rolling_policy=policy)
        lattice=MapLattice.from_spec(profile.grid) if not foreign_anchor else MapLattice((.125,.25),.01)
        window=GridWindow(lattice,(-65,64),8,8)
        grid=EvidenceGrid(window.spec,frame_id='odom:'+f.context.odom_epoch,
            calibration_id=f.calibration.sha256,clock_id=f.raw.fields['clock_epoch'],kind='LIVE')
        grid.hit_view[0]='original-independent-view'
        grid.revision=7;configure_grid(grid,window);pipeline.grid=grid
        return pipeline,window,policy

    def complete(self, pipeline):
        """진단 META가 들어 있는 실제 CP/LOAD 자료다. Atlas pool 게시 성공을 위조하지 않는다."""
        f=self.fixture
        state=_pipeline_state(pipeline,f.context)
        state.update(tile_revision_highwater=1,reference_provenance={},alignment_provenance=None)
        reference,pages=encode_mapper_state(state)
        tile=TileVersion(f.context.map_epoch,-2,1,1,UNKNOWN*4096)
        job=f.job()
        payload=CheckpointPayload.build(job,(tile,),EvidenceTable(contexts=pages),
            scene=dict(mapper_state=reference),durable_cutoff_pi_us=f.frame.pi_interval[1])
        receipt=f.store.commit(job,payload,current_context_sha256=f.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        selected=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        request=LoadRequest(f.job(),selected,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=f.store.load(request,current_context_sha256=f.context.sha256)
        return CheckpointPages(selected,f.context).accept(loaded,now_us=f.sensor.now)

    def test_fixed_metadata_keeps_frozen_canonical_field_assembly_without_optional_atlas(self):
        import numpy as np
        f=self.fixture;f.process();pipeline=f.pipeline;grid=pipeline.grid
        arrays={name:_pack_array(getattr(grid,name)) for name in ('state','observed_us','time_known','support','permission',
            'conflict','hit_origin','unordered_hit','hit_available_us')}
        labels=sorted(set(grid.hit_view.values()));codes=np.zeros(grid.state.shape,dtype=np.uint8)
        lookup={value:index+1 for index,value in enumerate(labels)}
        for index,value in grid.hit_view.items():codes.reshape(-1)[index]=lookup[value]
        # 변경 전 fixed serializer의 필드 조립이다. encoding helper는 바꾸지 않는다.
        expected=dict(profile='mapper-pipeline-state-1',storage_context=f.context.fields,
            calibration_id=pipeline.calibration.sha256,pipeline_profile_sha256=sha256(canonical(asdict(pipeline.profile))).hexdigest(),
            clock_id=grid.clock_id,kind=grid.kind,frame_id=grid.frame_id,spec=asdict(grid.spec),
            free_ttl_us=grid.free_ttl_us,occupied_ttl_us=grid.occupied_ttl_us,independent_view_m=grid.independent_view_m,
            revision=grid.revision,applied_hits=grid.applied_hits,applied_free=grid.applied_free,arrays=arrays,
            hit_views=dict(labels=labels,codes=_pack_array(codes)),
            gates={name:_gate_fields(getattr(pipeline,name)) for name in ('rgb_gate','depth_gate','nn_gate')},
            map_from_odom_identity=bool(pipeline._alignment is None and not pipeline._restore_pending),
            alignment=None if pipeline._alignment is None else pipeline._alignment.fields(),
            reference=None if pipeline.reference is None else asdict(pipeline.reference))
        original=canonical(_pipeline_state(pipeline,f.context))
        self.assertEqual(original,canonical(expected))
        self.assertNotIn('atlas',json_bytes(original,512*1024))
        f.sensor.now+=10
        self.assertEqual(canonical(_pipeline_state(pipeline,f.context)),original)

    def test_rolling_metadata_is_exact_six_fields_and_native_view_labels_require_actual_policy(self):
        f=self.fixture;pipeline,window,policy=self.rolling_pipeline()
        self.assertIsInstance(pipeline.grid.hit_view[0],ViewKey)
        state=_pipeline_state(pipeline,f.context)
        self.assertEqual(state['atlas'],atlas_state_fields(window,policy))
        self.assertEqual(set(state['atlas']),{'profile','lattice_origin_xy_m','roi_origin_cell',
            'tile_cells','live_restore','policy_sha256'})
        self.assertEqual(state['atlas']['roi_origin_cell'],[-65,64])
        self.assertTrue(all(type(label) is str for label in state['hit_views']['labels']))
        self.assertLess(len(canonical(state['atlas'])),512)
        pipeline._rolling_policy=None
        with self.assertRaises(MapCheckpointNotReady):_pipeline_state(pipeline,f.context)

    def test_actual_complete_load_restores_original_window_as_unknown_and_rejects_missing_or_foreign_policy_anchor(self):
        f=self.fixture;pipeline,window,policy=self.rolling_pipeline()
        # META의 과거 진단 배열도 새 현재성을 만들 수 없다. 원 EVIDENCE는 UNKNOWN이다.
        pipeline.grid.state.fill(1);pipeline.grid.time_known.fill(True);pipeline.grid.observed_us.fill(1234)
        complete=self.complete(pipeline);original=complete.state
        restored=PerceptionPipeline(f.calibration,pipeline.profile,rolling_policy=policy)
        self.assertIs(restore_pipeline(restored,complete,f.scope),complete)
        self.assertEqual(restored.grid.rolling_window,window)
        self.assertEqual(restored.grid.spec,window.spec)
        self.assertFalse(restored.grid.state.any() or restored.grid.time_known.any() or restored.grid.observed_us.any())
        self.assertFalse(restored.grid.hit_view)
        self.assertEqual(restored.grid.revision,7)
        self.assertEqual(complete.state,original)
        self.assertEqual(complete.tiles[0].payload,UNKNOWN*4096)
        for configured in (None,RollingPolicy(2,1)):
            with self.subTest(policy=configured),self.assertRaises(MapRestoreNotReady):
                restore_pipeline(PerceptionPipeline(f.calibration,pipeline.profile,rolling_policy=configured),complete,f.scope)
        foreign,_,_=self.rolling_pipeline(foreign_anchor=True)
        wrong=self.complete(foreign)
        with self.assertRaises(MapRestoreNotReady):
            restore_pipeline(PerceptionPipeline(f.calibration,foreign.profile,rolling_policy=policy),wrong,f.scope)

    def test_original_mutation_offset_updates_exact_global_proof_and_off_roi_unresolved_blocks_snapshot(self):
        f=self.fixture
        with patch.object(f.collector,'_observe',wraps=f.collector._observe) as observe:
            f.process()
        update=observe.call_args.args[1];grid=f.pipeline.grid;original=f.collector._cells[0]
        # 같은 물리 cell에 명시 signed 주소를 부여한다. 위치/pose/측정시각은 움직이지 않는다.
        window=GridWindow(MapLattice((.005,.005),.01),(-1,-1),1,1)
        self.assertEqual(window.spec,grid.spec);configure_grid(grid,window)
        body=bytearray(UNKNOWN*4096);body[4095*64:]=original;body=bytes(body)
        proof=AtlasProofWindow(window,lambda x,y:body if (x,y)==(-1,-1) else None)
        f.collector.reframe_atlas_proof(proof)
        self.assertEqual(f.collector._cells,{0:original})
        deltas=[]
        grid.add_free(((0,0),),f.observation.depth.stamp,view_id='same-original-certificate',origin_xy=(0.,0.),
            certificate_complete=True,observer=deltas.append)
        self.assertEqual(deltas[0].window_offset_xy,(-1,-1))
        applied=replace(update,mutation=deltas[0])
        observer=f.collector.frame_observer(f.frame,pipeline=f.pipeline)
        observer(applied)
        cell=proof.read(-1,-1)
        self.assertEqual(cell,f.collector._cells[0])
        self.assertEqual(CELL.unpack(cell)[7:10],(*f.frame.pi_interval,f.frame.observation_sequence))
        self.assertEqual(CELL.unpack(cell)[10],grid.revision)
        cursor=f.collector._next;known=dict(f.collector._cells)
        with self.assertRaises(MapCheckpointNotReady):
            observer(replace(applied,mutation=replace(deltas[0],window_offset_xy=(0,0))))
        self.assertEqual(f.collector._next,cursor);self.assertEqual(f.collector._cells,known)
        proof.mark_unresolved(128,128)
        with self.assertRaisesRegex(MapCheckpointNotReady,'global atlas'):
            f.collector.snapshot(f.pipeline,f.job(),now_us=f.sensor.now)
        self.assertTrue(proof.is_unresolved(128,128))
        proof.clear_unresolved(128,128)
        head=f.collector._tile_revision_head
        with self.assertRaisesRegex(MapCheckpointNotReady,'snapshot port is not connected'):
            f.collector._copy_tiles(grid,f.sensor.now,False)
        self.assertEqual(f.collector._tile_revision_head,head)

    def test_trim_includes_original_off_roi_owner_roots_and_never_treats_unknown_zero_as_proof(self):
        f=self.fixture;f.process();owner=f.collector;records=dict(owner._records)
        root=CELL.unpack(owner._cells[0])[6]
        expected=set();pending=[root]
        while pending:
            key=pending.pop()
            if key in expected:continue
            expected.add(key);pending.extend(records[key].parents)
        owner._cells={0:UNKNOWN};owner._rgb_refs={};owner._vehicle_refs={};owner._reference_geometry={}
        owner._atlas_owner=SimpleNamespace(provenance_roots=lambda:(0,root))
        owner._trim()
        self.assertEqual(set(owner._records),expected)
        self.assertNotIn(0,owner._records)
        self.assertTrue(all(owner._records[key] is records[key] for key in expected))
