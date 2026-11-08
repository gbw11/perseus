"""실제 SQLite CP/LOAD와 원 META로 hazard 기억의 복원 경계를 검사한다.

센서·시계·depth 분류는 기존 소프트웨어 fixture다. 새 관측은 실제 typed source
gate를 통과하지만 실제 OAK/주행/Scene 사용권을 승인하는 시험은 아니다.
"""

from base64 import b64encode
from copy import deepcopy
from dataclasses import replace
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from xenova_vehicle.mapping.local_map import OCCUPIED
from xenova_vehicle.mapping.map_lattice import GridWindow, RollingPolicy, atlas_state_fields
from xenova_vehicle.mapping.rolling_grid import configure_grid, pack_live_tiles, reframe_grid
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.runtime.map_checkpoint import CheckpointPages, restore_pipeline
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_hazard import MapperHazard
from xenova_vehicle.runtime.mapper_mapping import MappingFrameDraft
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.scene import SceneEvidenceScope
from xenova_vehicle.storage.contracts import LoadRequest, PersistenceError, StorageJob, canonical, json_bytes
from xenova_vehicle.storage.mapper_state import (
    decode_mapper_state, encode_mapper_state, mapper_state_roots, validate_hazard_state)
from xenova_vehicle.storage.records import CELL, CheckpointPayload, EvidenceTable, TileVersion


def uid(): return str(uuid4())


def state(*, rolling=False):
    result=dict(profile='mapper-pipeline-state-1',spec=dict(width=2,height=2,resolution_m=.01,origin_xy=[0.,0.]),
        reference_provenance={},alignment_provenance=None)
    if rolling:
        from xenova_vehicle.mapping.local_map import GridSpec
        result['spec'].update(width=4,height=4)
        window=GridWindow.from_spec(GridSpec(4,4,.01,(0.,0.)))
        result['atlas']=json_bytes(canonical(atlas_state_fields(window,RollingPolicy(1,1))),4096)
    return result


def hazard(*, revision=7, key=(0,0), body=None):
    body=bytes([1])+bytes(511) if body is None else body
    return dict(profile='mapper-hazard-state-1',revision=revision,method_manifest_sha256='e'*64,
        tiles=[[key[0],key[1],b64encode(body).decode('ascii')]])


class HazardMetadataTests(unittest.TestCase):
    def test_fixed_and_signed_rolling_bytes_roundtrip_add_no_proof_roots(self):
        for rolling,key in ((False,(0,0)),(True,(-2,1))):
            original=state(rolling=rolling);original['hazard']=hazard(key=key)
            ref,pages=encode_mapper_state(original)
            restored,_=decode_mapper_state(dict(mapper_state=ref),pages)
            revision,method,tiles=validate_hazard_state(restored['hazard'],state=restored)
            self.assertEqual((revision,method),(7,'e'*64))
            self.assertEqual(tiles,((key,bytes([1])+bytes(511)),))
            self.assertEqual(mapper_state_roots(restored),())
            self.assertNotIn('observed_us',restored['hazard'])
            self.assertEqual(mapper_state_roots(state()),())

    def test_malformed_version_native_values_fixed_padding_and_noncanonical_body_are_rejected(self):
        good=hazard();bad=[]
        for name,value in (('profile','mapper-hazard-state-2'),('revision',True),('revision',0),
                ('revision',1<<63),('method_manifest_sha256','E'*64),('tiles',tuple(good['tiles']))):
            item=deepcopy(good);item[name]=value;bad.append(item)
        item=deepcopy(good);item['source_clock']='invented';bad.append(item)
        item=deepcopy(good);item['tiles'][0][0]=True;bad.append(item)
        item=deepcopy(good);item['tiles'][0][0]=1;bad.append(item)
        item=deepcopy(good);item['tiles'][0][2]+='=';bad.append(item)
        item=hazard(body=bytes([4])+bytes(511));bad.append(item)
        item=hazard(body=bytes(512));bad.append(item)
        for item in bad:
            with self.subTest(value=item),self.assertRaises(PersistenceError):
                validate_hazard_state(item,state=state())
            with self.assertRaises(PersistenceError):encode_mapper_state(dict(state(),hazard=item))

    def test_global_capacity_sorted_unique_and_full_signed_address_edges(self):
        original=state(rolling=True);item=hazard()
        item['tiles']=[[x,0,b64encode(bytes([1])+bytes(511)).decode('ascii')] for x in range(-64,64)]
        self.assertEqual(len(validate_hazard_state(item,state=original)[2]),128)
        for rows in (item['tiles']+[[64,0,item['tiles'][0][2]]],list(reversed(item['tiles'])),
                item['tiles'][:1]*2,[[1<<31,0,item['tiles'][0][2]]]):
            with self.assertRaises(PersistenceError):validate_hazard_state(dict(item,tiles=rows),state=original)
        for x in (-(1<<31),(1<<31)-1):
            self.assertEqual(validate_hazard_state(hazard(key=(x,0)),state=original)[2][0][0],(x,0))


@unittest.skipUnless(os.name=='posix','실제 D SQLite/lock 회귀는 대상 POSIX에서 검사한다')
class HazardCompleteRestoreTests(unittest.TestCase):
    def setUp(self):
        import test_map_checkpoint as fixtures
        from xenova_vehicle.mapping.local_map import GridSpec
        f=fixtures.MapCheckpointTests(methodName='runTest');self.addCleanup(f.doCleanups);f.setUp();self.f=f
        f.profile=replace(f.profile,grid=GridSpec(2,2,.01,(-.005,-.005)))
        f.pipeline=PerceptionPipeline(f.calibration,f.profile,targets=f.pipeline.targets)
        f.depth=replace(f.depth,hit_boxes=(PointBounds((-.001,-.001,.1),(.001,.001,.2),True),),
            patches=(),floor_count=0,valid_count=1,mode='HIT_ONLY')
        graph=SimpleNamespace(status='UNKNOWN',strips=(),reasons=(),work={})
        road=SimpleNamespace(status='UNKNOWN',hypotheses=(),reasons=(),semantic_scored=0)
        import numpy as np
        with patch('xenova_vehicle.perception.depth.analyze_depth',return_value=f.depth), \
                patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph), \
                patch('xenova_vehicle.perception.pipeline.infer_road',return_value=road):
            self.result=f.pipeline.process(f.observation,pose=f.pose,now_us=f.sensor.now,
                completed_clock=lambda:f.sensor.now+1000,motion_qualified=True,needed_mask=np.zeros((2,2),dtype=bool),
                cycle_bound_us=10_000,completion_bound_us=20_000,evidence_observer=f.collector.frame_observer(f.frame))
        f.sensor.now+=1000
        f.collector.observe_result(f.frame,self.result,f.pipeline,raw_pose=f.pose)
        self.owner=MapperHazard()
        draft=self.draft(f.pipeline,f.frame,self.result,f.sensor.now)
        candidate=self.owner.prepare(draft,now_us=f.sensor.now)
        self.assertTrue(candidate.ready,candidate.reason);self.assertTrue(candidate.changed)
        self.assertTrue(self.owner.accept(candidate));self.assertEqual(candidate.revision,2)

    def draft(self,pipeline,frame,result,at):
        f=self.f;rgb,depth=frame.observation.rgb,frame.observation.depth
        raw_rgb=RawFrame('rgb_video',rgb.stamp.sequence,400000+(rgb.stamp.sequence-1)*1000,500000+(rgb.stamp.sequence-1)*1000,
            at,6,6,'NV12',rgb.payload,0)
        raw_depth=RawFrame('depth_right',depth.stamp.sequence,400001+(depth.stamp.sequence-1)*1000,500001+(depth.stamp.sequence-1)*1000,
            at,6,6,'RAW16_LE',depth.payload,0)
        clocks={name:dict(bridge_sample=dict(j_before_us=str(at),sdk_us=str(raw.sdk_us),j_after_us=str(at)),
            sdk_host_timestamp_j_bracket_us=list(map(str,stamp.captured_us)))
            for name,raw,stamp in (('rgb_video',raw_rgb,rgb.stamp),('depth_right',raw_depth,depth.stamp))}
        item=MapperInput(uid(),frame.scope.raw_binding.sha256,CaptureSample(raw_rgb,raw_depth,None,None,clocks),at+150_000)
        return MappingFrameDraft(item,frame.scope.context,pipeline,f.collector,None,frame,None,0,True,'READY',result=result)

    def checkpoint(self,*,value=None,scene=None):
        f=self.f
        if value is None:value=self.owner.state_fields(f.pipeline,f.context,f.scope.method_manifest_sha256)
        request=f.collector.capture(f.pipeline,f.job(),now_us=f.sensor.now,subsystem_state=value,scene=scene)
        committed=f.store.commit(request.job,request.payload,current_context_sha256=f.context.sha256)
        self.assertEqual(committed.status,'COMMITTED')
        self.assertTrue(f.collector.accept_commit(request,committed,now_us=f.sensor.now))
        reference=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        loader=CheckpointPages(reference,f.context);complete=None
        for cursor in range(0,max(1,len(request.payload.tile_references)),32):
            load=LoadRequest(f.job(),reference,canonical(dict(kind='CHECKPOINT_PAGE',cursor=cursor)))
            page=f.store.load(load,current_context_sha256=f.context.sha256)
            complete=loader.accept(page,now_us=f.sensor.now)
        self.assertIsNotNone(complete)
        restored=PerceptionPipeline(f.calibration,f.profile,targets=f.pipeline.targets)
        restore_pipeline(restored,complete,f.scope)
        return complete,restored

    def fresh(self,pipeline,number=1,*,both=True,scope=None):
        f=self.f;at=f.sensor.now+number*1000
        scope=f.scope if scope is None else scope;raw=scope.raw_binding.fields
        def image(value,name):
            return replace(value,stamp=replace(value.stamp,sequence=value.stamp.sequence+number,
                source_id=raw['acquisition_worker'],stream_id=raw['stream_instances'][name],clock_id=raw['clock_epoch'],
                captured_us=tuple(v+number*1000 for v in value.stamp.captured_us),available_us=at))
        observation=replace(f.observation,rgb=image(f.observation.rgb,'rgb_video'),depth=image(f.observation.depth,'depth_right'))
        rgb=pipeline.rgb_gate.accept(observation.rgb.stamp,observation.rgb.sha256)
        depth=pipeline.depth_gate.accept(observation.depth.stamp,observation.depth.sha256)
        frame=replace(f.frame,scope=scope,observation=observation,observation_sequence=1+number,accepted_us=at,
            pi_interval=f.sensor.lease.j_interval_to_pi(observation.capture_interval,at),
            raw_keys=tuple((raw['stream_instances'][name],key[1]+number,key[2]+number*1000)
                for name,key in zip(('rgb_video','depth_right'),f.frame.raw_keys)))
        result=replace(self.result,summary=dict(self.result.summary,rgb_updated=rgb,depth_updated=depth and both,
            rgb_source_key=observation.rgb.stamp.key,now_us=at),map_snapshot=pipeline.grid.snapshot(at,clock_id=pipeline.grid.clock_id))
        return self.draft(pipeline,frame,result,at),at

    def test_sql_complete_preserves_original_occupied_even_when_cell_is_unknown_and_current_stamps_wait(self):
        complete,pipeline=self.checkpoint();f=self.f
        self.assertFalse(bool(pipeline.grid.state.any()))
        owner=MapperHazard(complete=complete)
        self.assertTrue(owner.restore_complete(complete,pipeline=pipeline,scope=f.scope,now_us=f.sensor.now))
        self.assertIsNone(owner.snapshot);self.assertIsNone(owner._staged)
        self.assertTrue(owner.restore_complete(complete,pipeline=pipeline,scope=f.scope,now_us=f.sensor.now))
        draft,at=self.fresh(pipeline)
        candidate=owner.prepare(draft,now_us=at)
        self.assertTrue(candidate.ready,candidate.reason);self.assertFalse(candidate.changed)
        self.assertEqual(candidate.revision,2);self.assertTrue(any(candidate.snapshot.occupied))
        self.assertFalse(bool(pipeline.grid.state.any()))
        self.assertEqual(candidate.snapshot.sources,(draft.frame.observation.rgb.stamp,draft.frame.observation.depth.stamp))
        owner.accept(candidate)
        exported=owner.state_fields(pipeline,f.context,f.scope.method_manifest_sha256)
        self.assertEqual(exported['hazard'],json_bytes(complete.state,512*1024)['hazard'])

    def test_missing_one_actual_new_source_does_not_bind_or_refresh_retained_baseline(self):
        complete,pipeline=self.checkpoint();owner=MapperHazard(complete=complete);f=self.f
        owner.restore_complete(complete,pipeline=pipeline,scope=f.scope,now_us=f.sensor.now)
        draft,at=self.fresh(pipeline,both=False)
        rejected=owner.prepare(draft,now_us=at)
        self.assertFalse(rejected.ready);self.assertIsNone(owner.snapshot)
        owner.cancel(rejected)
        next_draft,next_at=self.fresh(pipeline,2)
        accepted=owner.prepare(next_draft,now_us=next_at)
        self.assertTrue(accepted.ready,accepted.reason);self.assertEqual(accepted.revision,2)

    def test_cancelled_new_hit_floor_is_persisted_without_cancelled_bit(self):
        f=self.f;original=self.owner.snapshot;at=f.sensor.now+1000
        # 원 map 후보와 별개인 후보 grid다. 채택하지 않은 HIT는 내구 occupied에 넣지 않는다.
        from copy import deepcopy as clone
        candidate_pipeline=clone(f.pipeline)
        draft,at=self.fresh(candidate_pipeline)
        candidate_pipeline.grid.state[1,1]=OCCUPIED
        result=replace(draft.result,map_snapshot=candidate_pipeline.grid.snapshot(at,clock_id=candidate_pipeline.grid.clock_id))
        changed=self.owner.prepare(replace(draft,result=result),now_us=at)
        self.assertTrue(changed.changed);self.assertEqual(changed.revision,3)
        self.owner.cancel(changed)
        self.assertIs(self.owner.snapshot.occupied,original.occupied)
        value=self.owner.state_fields(f.pipeline,f.context,f.scope.method_manifest_sha256)
        self.assertEqual(value['hazard']['revision'],3)
        complete,pipeline=self.checkpoint(value=value)
        owner=MapperHazard(complete=complete);owner.restore_complete(complete,pipeline=pipeline,scope=f.scope,now_us=f.sensor.now)
        fresh,at=self.fresh(pipeline,2);restored=owner.prepare(fresh,now_us=at)
        self.assertTrue(restored.ready);self.assertEqual(restored.revision,3)
        self.assertEqual(restored.snapshot.occupied,original.occupied)

    def test_legacy_complete_and_foreign_method_never_create_empty_one_or_current_snapshot(self):
        for value in ({},dict(hazard=hazard(revision=2))):
            if value:value['hazard']['method_manifest_sha256']='f'*64
            with self.subTest(value=value):
                # 독립 원 job으로 재시도하며 이전 실제 CP는 그대로 보존한다.
                complete,pipeline=self.checkpoint(value=value);owner=MapperHazard(complete=complete)
                if value:
                    with self.assertRaises(ValueError):owner.restore_complete(complete,pipeline=pipeline,scope=self.f.scope,now_us=self.f.sensor.now)
                else:self.assertFalse(owner.restore_complete(complete,pipeline=pipeline,scope=self.f.scope,now_us=self.f.sensor.now))
                draft,at=self.fresh(pipeline);rejected=owner.prepare(draft,now_us=at)
                self.assertEqual(rejected.reason,'RESTORE_NOT_READY');self.assertIsNone(owner.snapshot)

    def test_actual_original_head_expiry_and_frozen_counter_mismatch_are_rejected(self):
        complete,pipeline=self.checkpoint(scene=dict(hazard_revision=9))
        owner=MapperHazard(complete=complete)
        with self.assertRaisesRegex(ValueError,'revision'):
            owner.restore_complete(complete,pipeline=pipeline,scope=self.f.scope,now_us=self.f.sensor.now)
        late=complete.page_receipts[-1][0].job.expires_us
        with self.assertRaises(ValueError):
            owner.restore_complete(complete,pipeline=pipeline,scope=self.f.scope,now_us=late)
        self.assertIsNone(owner.snapshot)

    def test_rolling_sql_complete_keeps_offscreen_occupied_when_all_selected_cells_are_unknown(self):
        from xenova_vehicle.runtime.map_checkpoint import _pipeline_state
        from xenova_vehicle.mapping.local_map import EvidenceGrid,GridSpec
        f=self.f;policy=RollingPolicy(1,1);f.pipeline._rolling_policy=policy
        # margin1이 유효한4x4 창으로 원2x2 관측을 그대로 옮긴다. 새 셀은 UNKNOWN이다.
        old=f.pipeline.grid;spec=GridSpec(4,4,old.spec.resolution_m,old.spec.origin_xy)
        grid=EvidenceGrid(spec,frame_id=old.frame_id,calibration_id=old.calibration_id,clock_id=old.clock_id,kind=old.kind,
            free_ttl_us=old.free_ttl_us,occupied_ttl_us=old.occupied_ttl_us,independent_view_m=old.independent_view_m)
        for name in ('state','observed_us','time_known','support','permission','conflict','unordered_hit','hit_available_us','hit_origin'):
            getattr(grid,name)[:2,:2]=getattr(old,name)
        grid.hit_view={row*4+col:value for index,value in old.hit_view.items() for row,col in (divmod(index,2),)}
        grid.revision,grid.applied_hits,grid.applied_free=old.revision,old.applied_hits,old.applied_free
        f.profile=replace(f.profile,grid=spec);f.pipeline.profile=f.profile;f.pipeline.grid=grid
        self.result=replace(self.result,map_snapshot=grid.snapshot(f.sensor.now,clock_id=grid.clock_id))
        initial=GridWindow.from_spec(f.pipeline.grid.spec);configure_grid(f.pipeline.grid,initial)
        owner=MapperHazard()
        first=self.draft(f.pipeline,f.frame,self.result,f.sensor.now)
        owner.accept(owner.prepare(first,now_us=f.sensor.now))
        original=owner.snapshot.global_occupied
        pool=dict(pack_live_tiles(f.pipeline.grid,{}))
        reframe_grid(f.pipeline.grid,initial.shifted((-65,-1)),pool)
        moved,at=self.fresh(f.pipeline)
        shifted=owner.prepare(moved,now_us=at)
        self.assertTrue(shifted.ready,shifted.reason);self.assertFalse(shifted.changed)
        owner.accept(shifted)
        saved=_pipeline_state(f.pipeline,f.context)
        saved.update(tile_revision_highwater=20,reference_provenance={},alignment_provenance=None)
        saved.update(owner.state_fields(f.pipeline,f.context,f.scope.method_manifest_sha256))
        ref,pages=encode_mapper_state(saved)
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)*4096
        keys=sorted({(0,0)}|{(p.tile_x,p.tile_y) for p in f.pipeline.grid.rolling_window.pieces()})
        tiles=tuple(TileVersion(f.context.map_epoch,*key,10+i,unknown) for i,key in enumerate(keys))
        job=f.job();payload=CheckpointPayload.build(job,tiles,EvidenceTable(contexts=pages),
            scene=dict(mapper_state=ref),durable_cutoff_pi_us=moved.frame.pi_interval[1])
        receipt=f.store.commit(job,payload,current_context_sha256=f.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        selected=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        load=LoadRequest(f.job(),selected,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        complete=CheckpointPages(selected,f.context).accept(f.store.load(load,current_context_sha256=f.context.sha256),now_us=f.sensor.now)
        restored=PerceptionPipeline(f.calibration,f.profile,targets=f.pipeline.targets,rolling_policy=policy)
        restore_pipeline(restored,complete,f.scope)
        new_owner=MapperHazard(complete=complete)
        self.assertTrue(new_owner.restore_complete(complete,pipeline=restored,scope=f.scope,now_us=f.sensor.now))
        self.assertIsNone(new_owner.snapshot)
        fresh,at=self.fresh(restored,3);candidate=new_owner.prepare(fresh,now_us=at)
        self.assertTrue(candidate.ready,candidate.reason);self.assertFalse(candidate.changed)
        self.assertEqual(candidate.revision,owner.snapshot.revision)
        self.assertEqual(candidate.snapshot.global_occupied,original)
        self.assertFalse(any(candidate.snapshot.occupied))
        self.assertTrue(all(CELL.unpack_from(tile.payload)[0]==255 for tile in complete.tiles))

    def test_new_registered_mapper_and_raw_clock_bind_only_after_original_bootstrap_pages_and_fresh_gates(self):
        original,_=self.checkpoint();f=self.f;fields=f.raw.fields
        for name in ('acquisition_worker','pipeline_instance','capture_epoch','sdk_bridge_epoch','clock_epoch'):fields[name]=uid()
        fields['stream_instances']={name:uid() for name in fields['stream_instances']}
        raw=RawBinding(fields);publication=json_bytes(f.context.source_binding,2048)
        publication.update(mapper_worker=uid(),mapper_instance=uid(),raw_binding_sha256=raw.sha256)
        current=replace(f.context,mapper_worker=publication['mapper_worker'],origin_mapper_instance=publication['mapper_instance'],
            source_binding=canonical(publication))
        scope=SceneEvidenceScope(current,raw,f.calibration,f.scope.method_manifest_sha256)
        selection=dict(kind='CHECKPOINT_PAGE',cursor=0)
        source=dict(storage_binding=dict(service_instance=fields['service_instance'],storage_worker=uid(),
            storage_profile_sha256='1'*64,database_uuid=current.database_uuid,db_schema_version=1),
            mapper_worker=current.mapper_worker,origin_mapper_instance=current.origin_mapper_instance,
            load_purpose='BOOTSTRAP_RESTORE',checkpoint_ref=json_bytes(original.checkpoint_ref,2048),load_selection=selection)
        provisional=replace(current,source_binding=canonical(source),geometry_origin=None)
        job=StorageJob(provisional,uid(),f.sensor.now,f.sensor.now+5_000_000)
        load=LoadRequest(job,original.checkpoint_ref,canonical(selection),'BOOTSTRAP_RESTORE')
        page=f.store.load(load,current_context_sha256=provisional.sha256)
        complete=CheckpointPages(original.checkpoint_ref,provisional).accept(page,now_us=f.sensor.now)
        scope=scope.with_complete_history(complete,now_us=f.sensor.now)
        pipeline=PerceptionPipeline(f.calibration,f.profile,targets=f.pipeline.targets)
        restore_pipeline(pipeline,complete,scope)
        owner=MapperHazard(complete=complete)
        self.assertTrue(owner.restore_complete(complete,pipeline=pipeline,scope=scope,now_us=f.sensor.now))
        self.assertIsNone(owner.snapshot)
        draft,at=self.fresh(pipeline,scope=scope)
        result=owner.prepare(draft,now_us=at)
        self.assertTrue(result.ready,result.reason);self.assertEqual(result.revision,2)
        self.assertTrue(any(result.snapshot.occupied))
        self.assertEqual({stamp.clock_id for stamp in result.snapshot.sources},{fields['clock_epoch']})


if __name__=='__main__':unittest.main()
