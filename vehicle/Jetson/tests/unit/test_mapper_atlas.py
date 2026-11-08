"""실제 rolling codec/proof/pool 경로의 주소·원 시각·CP 수명을 검사한다.

CELL의 sensor/Pi 수락과 실제 SQL receipt는 이 시험의 명시 software 사건이다.
pool/ref/codec는 제품 구현을 사용하며 map 사용권이나 실기기 메모리 수락은 주장하지 않는다.
"""

import unittest
import gc
import os
from dataclasses import replace
from threading import Lock
from unittest.mock import patch
from uuid import uuid4

from xenova_vehicle.mapping.local_map import EvidenceGrid,GridSpec,OCCUPIED,UNKNOWN
from xenova_vehicle.mapping.map_lattice import RollingPolicy,GridWindow
from xenova_vehicle.mapping.rolling_grid import configure_grid,reframe_grid
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.runtime.atlas_proof import AtlasProofWindow
from xenova_vehicle.runtime.mapper_atlas import MapperAtlas,MapperAtlasNotReady
from xenova_vehicle.storage.records import CELL,CheckpointPayload,EvidenceTable
from xenova_vehicle.runtime.map_checkpoint import MapSnapshot
from xenova_vehicle.storage.contracts import StorageContext,StorageJob,canonical
from xenova_vehicle.storage.port import CheckpointRequest


class MapperAtlasTests(unittest.TestCase):
    def setUp(self):
        self.spec=GridSpec(16,16,.05,(0.,-.4));self.policy=RollingPolicy(2,2)
        self.owner=MapperAtlas(str(uuid4()),self.spec,self.policy,reserved_workspace_bytes=8*1024*1024,
            tile_revision_highwater=40)
        self.grid=EvidenceGrid(self.spec,frame_id='odom-original',calibration_id='local-calibration',clock_id='J-original',kind='SYNTHETIC')
        configure_grid(self.grid,self.owner.window)
        with self.owner.read_tiles(self.owner.window) as (_,reader):self.proof=AtlasProofWindow(self.owner.window,reader)

    def hit(self,identity=11,sequence=7,at=1000):
        x,y,x1,y1=self.grid.spec.bounds(1,1)
        stamp=FrameStamp('original-camera','original-right',sequence,'J-original',(at,at+2),at+100)
        self.grid.add_hits(((x+.01,y+.01,x1-.01,y1-.01),),stamp,view_id='original-front-view',origin_xy=(.2,.1))
        global_cell=self.proof.window.local_to_global(17)
        raw=CELL.pack(100,1,2,.05,.1,.001,identity,at+1000,at+1002,sequence,1,0)
        self.proof.write(*global_cell,raw);return global_cell,raw

    def accept(self):
        delta=self.owner.prepare_frame(self.grid,self.proof);self.owner.accept_frame(delta)
        self.owner.drain_retired();return delta

    def test_final_metadata_swap_defers_destruction_and_cp_coalesce_keeps_retired_bytes_charged(self):
        self.hit();self.accept();snapshot=self.owner.checkpoint_snapshot()
        final_lock=Lock();finalized=[]
        class WatchedMetadata(dict):
            def __del__(self):finalized.append(('metadata',final_lock.locked()))
        class WatchedRoots(tuple):
            def __del__(self):finalized.append(('roots',final_lock.locked()))
        # 값은 원 제품 metadata/roots다. 컨테이너 해제 시점만 관측 가능하게 한다.
        self.owner._metadata=WatchedMetadata(self.owner._metadata)
        self.owner._roots=WatchedRoots(self.owner._roots)
        self.hit(12,8,2000)
        delta=self.owner.prepare_frame(self.grid,self.proof)
        with final_lock:
            self.owner.accept_frame(delta)
            self.assertEqual(finalized,[])
        self.assertTrue(self.owner.stats()['retiring_metadata'])
        self.assertEqual((self.owner.pool.stats()['versions'],self.owner.pool.stats()['retired_versions']),(4,1))
        self.owner.complete_checkpoint(snapshot)
        self.owner.pool.coalesce_unleased()
        self.assertEqual((self.owner.pool.stats()['versions'],self.owner.pool.stats()['retired_versions']),(4,2))
        self.assertEqual(self.owner.pool.stats()['charged_bytes'],4*262144+8*1024*1024)
        self.assertEqual(finalized,[])
        self.assertTrue(self.owner.drain_retired())
        self.assertCountEqual(finalized,[('metadata',False),('roots',False)])
        self.assertEqual((self.owner.pool.stats()['versions'],self.owner.pool.stats()['retired_versions']),(2,0))
        self.assertEqual(self.owner.provenance_roots(),(12,))
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],1)
        self.assertEqual(tuple(item.reference for item in self.owner.metadata()),delta.tile_references)

    def move(self,window):
        with self.owner.read_tiles(window) as (live,reader):
            reframe_grid(self.grid,window,live)
            self.proof=self.proof.reframe(window,immutable_tile_reader=reader)

    def test_two_tiles_and_return_preserve_original_live_age_occupied_proof_and_revision(self):
        cell,raw=self.hit();first=self.accept();original_ref=first.tile_references[0]
        self.assertEqual(self.owner.provenance_roots(),(11,))
        with self.owner.read_tiles(self.owner.window) as (live,reader):
            borrowed_reader=reader;self.assertEqual(len(live),1);self.assertIsNotNone(reader(0,0))
        self.assertFalse(live)
        with self.assertRaises(MapperAtlasNotReady):borrowed_reader(0,0)
        first_window=self.owner.window;next_window=first_window.shifted((64,0))
        self.move(next_window);self.assertEqual(int(self.grid.state[1,1]),UNKNOWN)
        self.hit(22,8,5000);self.accept()
        self.move(first_window)
        self.assertEqual(int(self.grid.state[1,1]),OCCUPIED)
        self.assertEqual(int(self.grid.observed_us[1,1]),1000)
        self.assertEqual(int(self.grid.hit_available_us[1,1]),1100)
        self.assertEqual(self.proof.read(*cell),raw)
        before=self.owner.tile_revision_highwater;returned=self.accept()
        self.assertEqual(self.owner.tile_revision_highwater,before)
        self.assertIn(original_ref,returned.tile_references)
        self.assertEqual(self.owner.provenance_roots(),(11,22))
        checkpoint=self.owner.checkpoint_snapshot()
        self.assertEqual(len(checkpoint.tiles),2);self.assertEqual(len(checkpoint.tile_references),2)
        self.assertEqual(tuple(tile.reference for tile in self.owner.iter_tiles(checkpoint)),checkpoint.tile_references)
        self.owner.retire_checkpoint(checkpoint)

    def test_checkpoint_a_original_lease_and_clean_never_apply_to_new_observation_b(self):
        self.hit();a=self.accept();snapshot=self.owner.checkpoint_snapshot()
        tile_a=snapshot.tiles[0];self.assertEqual(tile_a.revision,41)
        self.hit(12,8,2000);b=self.accept()
        self.assertNotEqual(a.tile_references,b.tile_references)
        self.assertEqual(snapshot.tiles[0].reference,tile_a.reference)
        self.assertEqual(CELL.unpack_from(snapshot.tiles[0].payload,(1*64+1)*64)[6],11)
        self.assertEqual(self.owner.provenance_roots(),(11,12))
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],2)
        # 원 SQL COMMITTED를 검산한 caller의 사건만 주입한다. 여기서 SQL은 실행하지 않는다.
        self.owner.complete_checkpoint(snapshot)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],1)
        with self.assertRaises(MapperAtlasNotReady):_ = snapshot.tiles
        next_snapshot=self.owner.checkpoint_snapshot();tile_b=next_snapshot.tiles[0]
        self.assertEqual(tile_b.revision,42);self.assertEqual(tile_b.reference,b.tile_references[0])
        self.assertEqual(self.owner.provenance_roots(),(12,))

        self.owner.retire_checkpoint(next_snapshot)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],1)
        retry=self.owner.checkpoint_snapshot();self.assertEqual(retry.tiles[0].reference,tile_b.reference)
        self.owner.retire_checkpoint(retry)

    def test_cancel_burns_only_tile_revision_and_unresolved_cannot_become_complete(self):
        cell,_=self.hit();candidate=self.owner.prepare_frame(self.grid,self.proof)
        self.assertEqual(candidate.tile_references[0][2],41)
        self.owner.cancel_frame(candidate)
        self.assertEqual(self.owner.tile_revision_highwater,41)
        self.assertEqual(self.owner.pool.stats()['versions'],0);self.assertEqual(self.owner.provenance_roots(),())
        self.proof.mark_unresolved(*cell);accepted=self.accept()
        self.assertEqual(accepted.tile_references[0][2],42)
        self.assertTrue(self.owner.unresolved)
        with self.assertRaisesRegex(MapperAtlasNotReady,'UNRESOLVED_PROOF'):self.owner.checkpoint_snapshot(allow_clean=True)
        self.proof.clear_unresolved(*cell);self.accept()
        self.assertEqual(self.owner.tile_revision_highwater,42)
        snapshot=self.owner.checkpoint_snapshot();self.assertEqual(snapshot.tiles[0].revision,42)
        self.owner.retire_checkpoint(snapshot)

    def test_actual_terminal_retire_coalesces_unleased_a_but_keeps_current_b_dirty(self):
        self.hit();self.accept();a=self.owner.checkpoint_snapshot()
        self.hit(12,8,2000);latest=self.accept()
        self.owner.retire_checkpoint(a)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],2)
        b=self.owner.checkpoint_snapshot()
        self.assertEqual(tuple(tile.reference for tile in b.tiles),latest.tile_references)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],1)
        self.assertEqual(self.owner.provenance_roots(),(12,))
        with self.assertRaises(MapperAtlasNotReady):self.owner.complete_checkpoint(a)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],1)
        self.owner.retire_checkpoint(b)

    def test_explicit_metadata_only_checkpoint_does_not_mint_tile_or_clearance(self):
        with self.assertRaisesRegex(MapperAtlasNotReady,'NO_DIRTY_EVIDENCE'):self.owner.checkpoint_snapshot()
        empty=self.owner.checkpoint_snapshot(allow_clean=True)
        self.assertEqual((empty.tiles,empty.tile_references),((),()))
        self.assertEqual(self.owner.pool.stats()['active_leases'],0)
        self.owner.complete_checkpoint(empty)
        self.assertEqual(self.owner.tile_revision_highwater,40);self.assertEqual(self.owner.provenance_roots(),())
        self.hit();self.accept();first=self.owner.checkpoint_snapshot();original=first.tile_references
        self.owner.complete_checkpoint(first)
        with self.assertRaisesRegex(MapperAtlasNotReady,'NO_DIRTY_EVIDENCE'):self.owner.checkpoint_snapshot()
        metadata=self.owner.checkpoint_snapshot(allow_clean=True)
        self.assertEqual(metadata.tiles,());self.assertEqual(metadata.tile_references,original)
        self.assertEqual(self.owner.pool.stats()['active_leases'],1)
        self.assertEqual(tuple(tile.reference for tile in self.owner.iter_tiles(metadata)),original)
        self.owner.retire_checkpoint(metadata)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],0)

    def test_clean_eviction_keeps_original_selected_ref_and_requires_load_before_return(self):
        self.hit();accepted=self.accept();original=accepted.tile_references[0]
        checkpoint=self.owner.checkpoint_snapshot();self.owner.complete_checkpoint(checkpoint)
        self.owner.pool.evict_clean(((0,0),))
        self.assertEqual(self.owner.needs_load,(((0,0),original),))
        self.assertEqual(self.owner.provenance_roots(),(11,))
        hwm=self.owner.tile_revision_highwater
        with self.assertRaises(MapperAtlasNotReady) as caught:
            with self.owner.read_tiles(self.owner.window):pass
        self.assertEqual(str(caught.exception),'EVIDENCE_LOAD_REQUIRED')
        self.assertEqual(caught.exception.needs_load,(((0,0),original),))
        with self.assertRaisesRegex(MapperAtlasNotReady,'EVIDENCE_LOAD_REQUIRED'):
            self.owner.prepare_frame(self.grid,self.proof)
        with self.assertRaisesRegex(MapperAtlasNotReady,'EVIDENCE_LOAD_REQUIRED'):
            self.owner.checkpoint_snapshot(allow_clean=True)
        self.assertEqual(self.owner.tile_revision_highwater,hwm)
        self.assertEqual(self.owner.metadata()[0].reference,original)
        self.assertEqual(self.owner.pool.stats()['versions'],0)

    def test_original_request_keeps_a_charged_after_cp_close_until_gc_and_compute_drain(self):
        self.hit();self.accept();snapshot=self.owner.checkpoint_snapshot()
        context=StorageContext(str(uuid4()),str(uuid4()),str(uuid4()),self.owner.map_epoch,
            str(uuid4()),str(uuid4()),str(uuid4()),'explicit-owner-test','local-fixture','b'*64,
            canonical(dict(profile='owner-lifetime-test-only')))
        job=StorageJob(context,str(uuid4()),1000,2000)
        map_snapshot=MapSnapshot(job,snapshot.tiles,(),EvidenceTable(),b'{}',snapshot.tile_references,2002,0,0)
        map_pin=self.owner.retain_checkpoint_owner(snapshot,map_snapshot)
        request=CheckpointRequest(job,CheckpointPayload.build(job,map_snapshot.tiles,
            tile_references=snapshot.tile_references,durable_cutoff_pi_us=2002))
        pin=self.owner.retain_checkpoint_owner(snapshot,request)
        self.assertIs(self.owner.retain_checkpoint_owner(snapshot,request),pin)
        self.owner.complete_checkpoint(snapshot)
        self.hit(12,8,2000);self.accept()
        self.assertEqual(self.owner.pool.stats()['versions'],3)
        self.assertEqual(self.owner.pool.stats()['owner_pins'],2)
        self.assertEqual(self.owner.provenance_roots(),(11,12))
        self.owner.drain_owner_releases()
        self.assertEqual(self.owner.pool.stats()['versions'],3)
        del request;gc.collect()
        self.assertEqual(self.owner.pool.stats()['owner_pins'],2)
        self.assertEqual(self.owner.pool.stats()['versions'],3)
        released=self.owner.drain_owner_releases()
        self.assertEqual(released,(pin,))
        self.assertEqual(self.owner.pool.stats()['versions'],3)
        self.assertEqual(self.owner.pool.stats()['owner_pins'],1)
        del map_snapshot;gc.collect()
        self.assertEqual(self.owner.drain_owner_releases(),(map_pin,))
        self.assertEqual(self.owner.pool.stats()['versions'],2)
        self.assertEqual(self.owner.pool.stats()['dirty_evidence_versions'],1)
        self.assertEqual(self.owner.provenance_roots(),(12,))

@unittest.skipUnless(os.name=='posix','실제 SQLite/전체 LOAD producer fixture는 대상 POSIX에서 검사한다')
class MapperAtlasCompleteInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_map_checkpoint as fixtures
        from xenova_vehicle.perception.pipeline import PerceptionPipeline,PipelineProfile
        from xenova_vehicle.runtime.map_checkpoint import _pipeline_state,CheckpointPages
        from xenova_vehicle.storage.mapper_state import encode_mapper_state
        from xenova_vehicle.storage.contracts import LoadRequest,json_bytes
        from xenova_vehicle.storage.records import TileVersion
        f=fixtures.MapCheckpointTests(methodName='runTest');cls.addClassCleanup(f.doCleanups);f.setUp();cls.fixture=f
        cls.profile=PipelineProfile(grid=GridSpec(8,8,.01,(-.005,-.005)));cls.policy=RollingPolicy(1,1)
        original=PerceptionPipeline(f.calibration,cls.profile,rolling_policy=cls.policy)
        cls.window=GridWindow.from_spec(cls.profile.grid).shifted((-65,64))
        original.grid=EvidenceGrid(cls.window.spec,frame_id='odom:'+f.context.odom_epoch,
            calibration_id=f.calibration.sha256,clock_id=f.raw.fields['clock_epoch'],kind='LIVE')
        configure_grid(original.grid,cls.window);original.grid.revision=7
        state=_pipeline_state(original,f.context)
        state.update(tile_revision_highwater=999,reference_provenance={},alignment_provenance=None)
        reference,pages=encode_mapper_state(state)
        unknown=CELL.pack(255,0,0,0.,0.,-0.,0,0,0,0,0,0)
        keys=((-2,1),)+tuple((index,1) for index in range(32))
        tiles=tuple(TileVersion(f.context.map_epoch,*key,index+11,unknown*4096) for index,key in enumerate(keys))
        for end in (32,33):
            start=0 if end==32 else 32;job=f.job()
            payload=CheckpointPayload.build(job,tiles[start:end],EvidenceTable(contexts=pages),
                tile_references=tuple(tile.reference for tile in tiles[:end]),scene=dict(mapper_state=reference),
                durable_cutoff_pi_us=f.frame.pi_interval[1])
            f.assertEqual(f.store.commit(job,payload,current_context_sha256=f.context.sha256).status,'COMMITTED')
        selected=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        loader=CheckpointPages(selected,f.context);complete=None
        for cursor in (0,32):
            load=LoadRequest(f.job(),selected,canonical(dict(kind='CHECKPOINT_PAGE',cursor=cursor)))
            decoded=f.store.load(load,current_context_sha256=f.context.sha256)
            complete=loader.accept(decoded,now_us=f.sensor.now)
        cls.complete=complete
        cls.bootstrap=dict(status='COMPLETE',database_uuid=f.context.database_uuid,selected_checkpoint=json_bytes(selected,2048))

    def pipeline(self):
        from xenova_vehicle.perception.pipeline import PerceptionPipeline
        return PerceptionPipeline(self.fixture.calibration,self.profile,rolling_policy=self.policy)

    def install(self,*,pipeline=None,bootstrap=None,workspace=8*1024*1024,now=None):
        return MapperAtlas.from_complete(self.complete,pipeline=self.pipeline() if pipeline is None else pipeline,
            current_scope=self.fixture.scope,selected_bootstrap=self.bootstrap if bootstrap is None else bootstrap,
            now_us=self.fixture.sensor.now if now is None else now,reserved_workspace_bytes=workspace)

    def test_actual_complete_installs_two_batches_with_original_bodies_refs_head_and_retained_owner(self):
        pipeline=self.pipeline();owner=self.install(pipeline=pipeline)
        self.assertEqual(owner.window,self.window);self.assertEqual(owner.tile_revision_highwater,999)
        self.assertEqual(tuple(item.reference for item in owner._selected.values()),tuple(tile.reference for tile in self.complete.tiles))
        self.assertIs(owner._bootstrap_owner,self.complete)
        receipt=owner.install_receipt
        self.assertEqual((receipt.input_tile_count,receipt.input_body_bytes,receipt.aliased_install_bytes),(33,33*262144,33*262144))
        self.assertEqual(receipt.peak_body_and_workspace_bytes,33*262144+8*1024*1024)
        stats=owner.stats()
        self.assertEqual((stats['live_versions'],stats['evidence_versions'],stats['dirty_evidence_versions']),(0,33,0))
        self.assertEqual((stats['owner_pins'],stats['owner_pinned_versions']),(1,33))
        self.assertEqual(stats['charged_bytes'],receipt.peak_body_and_workspace_bytes)
        self.assertFalse(pipeline.grid.state.any() or pipeline.grid.time_known.any() or pipeline.grid.observed_us.any())
        self.assertFalse(pipeline.grid.hit_available_us.any() or pipeline.grid.hit_view)
        with owner.read_tiles(owner.window) as (live,reader):
            self.assertEqual(live,{})
            self.assertIs(reader(-2,1),self.complete.tiles[0].payload)
        snapshot=owner.checkpoint_snapshot(allow_clean=True)
        self.assertEqual(snapshot.tiles,())
        self.assertEqual(snapshot.tile_references,tuple(tile.reference for tile in self.complete.tiles))
        for original,loaded in zip(self.complete.tiles,owner.iter_tiles(snapshot)):
            self.assertEqual(loaded.reference,original.reference);self.assertIs(loaded.payload,original.payload)
        owner.retire_checkpoint(snapshot);gc.collect();self.assertEqual(owner.drain_owner_releases(),())
        self.assertEqual(owner.pool.stats()['owner_pins'],1)

    def test_wrong_bootstrap_expired_original_head_and_input_peak_never_return_an_owner(self):
        pipeline=self.pipeline()
        with self.assertRaises(MapperAtlasNotReady):
            self.install(pipeline=pipeline,bootstrap=dict(self.bootstrap,database_uuid=str(uuid4())))
        self.assertIsNone(pipeline.grid)
        from xenova_vehicle.mapping.atlas import AtlasResource,MAX_DATA_BYTES
        with self.assertRaisesRegex(AtlasResource,'RESTORE_INPUT_PEAK'):
            self.install(workspace=MAX_DATA_BYTES)
        from xenova_vehicle.runtime.scene import SceneError
        expires=self.complete.page_receipts[-1][0].job.expires_us
        with self.assertRaises(SceneError):self.install(now=expires)
        hwm=self.fixture.db.connection.execute('SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone()[0]
        self.assertEqual(hwm,str(self.fixture.grant.highwater_after))

    def test_second_batch_failure_keeps_private_partial_owner_not_ready_and_never_returns_it(self):
        from xenova_vehicle.mapping.atlas import AtlasPool,AtlasResource
        initialize=MapperAtlas.__init__;prepare=AtlasPool.prepare;created=[];sizes=[]
        def observe(owner,*args,**kwargs):
            initialize(owner,*args,**kwargs);created.append(owner)
        def fail_second(pool,updates):
            sizes.append(len(updates))
            if len(sizes)==2:raise AtlasResource('INJECTED_SECOND_BATCH_FAILURE')
            return prepare(pool,updates)
        with patch.object(MapperAtlas,'__init__',observe),patch.object(AtlasPool,'prepare',fail_second):
            with self.assertRaisesRegex(AtlasResource,'INJECTED_SECOND_BATCH_FAILURE'):self.install()
        self.assertEqual(sizes,[32,1]);self.assertEqual(len(created),1)
        partial=created[0];self.assertTrue(partial._restore_failed)
        self.assertEqual(partial.pool.stats()['versions'],32)
        with self.assertRaisesRegex(MapperAtlasNotReady,'RESTORE_NOT_READY'):
            with partial.read_tiles(partial.window):pass
        with self.assertRaisesRegex(MapperAtlasNotReady,'RESTORE_NOT_READY'):partial.provenance_roots()

    def evicted_tile_load(self):
        """실제 CP commit→clean eviction→원 CURRENT_MAP TILE LOAD를 만든다."""
        import struct
        from uuid import UUID
        from xenova_vehicle.runtime.map_checkpoint import _pipeline_state
        from xenova_vehicle.storage.mapper_state import encode_mapper_state
        from xenova_vehicle.storage.records import PROVENANCE,ProvenanceRecord
        from xenova_vehicle.storage.contracts import LoadRequest
        f=self.fixture;pipeline=self.pipeline()
        owner=MapperAtlas(f.context.map_epoch,self.profile.grid,self.policy,reserved_workspace_bytes=8*1024*1024,
            tile_revision_highwater=getattr(type(self),'_load_tile_head',2000))
        grid=EvidenceGrid(self.window.spec,frame_id='odom:'+f.context.odom_epoch,
            calibration_id=f.calibration.sha256,clock_id=f.raw.fields['clock_epoch'],kind='LIVE')
        configure_grid(grid,self.window);pipeline.grid=grid
        with owner.read_tiles(self.window) as (_,reader):proof=AtlasProofWindow(self.window,reader)
        identity=f.collector._allocate();context=f.scope.context_record('RAW')
        raw=struct.pack('<16sQQ',UUID(f.raw.fields['stream_instances']['depth_right']).bytes,1,400001)
        parents=f.frame.parents;interval=f.frame.pi_interval
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),18,1,1,1,
            UUID(f.context.pi_host_boot).bytes,*interval,1,parents[0].provenance_id,0,0,0,raw.ljust(128,b'\0'),0,bytes(16)))
        x,y,x1,y1=grid.spec.bounds(0,0)
        grid.add_hits(((x+.002,y+.002,x1-.002,y1-.002),),f.observation.depth.stamp,
            view_id=f.raw.fields['stream_instances']['depth_right'],origin_xy=(0.,0.))
        proof.write(*self.window.local_to_global(0),CELL.pack(100,1,18,.1,.2,.01,identity,*interval,1,grid.revision,0))
        delta=owner.prepare_frame(grid,proof);owner.accept_frame(delta)
        type(self)._load_tile_head=owner.tile_revision_highwater
        snapshot=owner.checkpoint_snapshot();original_reference=snapshot.tile_references[0]
        state=_pipeline_state(pipeline,f.context)
        state.update(tile_revision_highwater=owner.tile_revision_highwater,reference_provenance={},alignment_provenance=None)
        metadata,pages=encode_mapper_state(state)
        contexts={value.sha256:value for value in (*f.frame.contexts,context,*pages)}
        evidence=EvidenceTable((*parents,record),tuple(contexts.values()))
        job=f.job();payload=CheckpointPayload.build(job,snapshot.tiles,evidence,
            tile_references=snapshot.tile_references,scene=dict(mapper_state=metadata),durable_cutoff_pi_us=interval[1])
        request=CheckpointRequest(job,payload);owner.retain_checkpoint_owner(snapshot,request)
        receipt=f.store.commit(job,payload,current_context_sha256=f.context.sha256)
        self.assertTrue(receipt.current(job,payload,f.context.sha256,f.sensor.now))
        owner.complete_checkpoint(snapshot)
        del request,payload;gc.collect();owner.drain_owner_releases()
        key=original_reference[:2];owner.pool.evict_clean((key,))
        selected=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        load=LoadRequest(f.job(),selected,canonical(dict(kind='TILE',map_epoch=f.context.map_epoch,
            tile_x=key[0],tile_y=key[1],tile_revision=str(original_reference[2]))),purpose='CURRENT_MAP')
        actual=f.store.load(load,current_context_sha256=f.context.sha256)
        return owner,actual,key,original_reference,identity

    def test_actual_tile_load_after_clean_eviction_preserves_body_reference_roots_and_weak_owner(self):
        from xenova_vehicle.mapping.atlas import AtlasResource
        owner,loaded,key,reference,identity=self.evicted_tile_load();hwm=owner.tile_revision_highwater
        self.assertIn((key,reference),owner.needs_load)
        installed=owner.install_loaded_tile(loaded,context=self.fixture.context,now_us=self.fixture.sensor.now)
        self.assertEqual(installed.reference,reference);self.assertEqual(tuple(installed.iter_roots()),(identity,))
        self.assertEqual(owner.tile_revision_highwater,hwm);self.assertEqual(owner.needs_load,())
        self.assertEqual(owner.pool.stats()['dirty_evidence_versions'],0)
        self.assertEqual(owner.pool.stats()['owner_pins'],1)
        with owner.read_tiles(self.window) as (live,reader):
            self.assertNotIn(key,live);self.assertIs(reader(*key),loaded.payload.tiles[0].payload)
        with self.assertRaises(AtlasResource):owner.pool.evict_clean((key,))
        del loaded;gc.collect();owner.drain_owner_releases()
        self.assertEqual(owner.pool.stats()['owner_pins'],0)
        owner.pool.evict_clean((key,));self.assertIn((key,reference),owner.needs_load)

    def test_expired_foreign_or_wrong_selection_tile_load_has_no_effect_and_cannot_overwrite_current(self):
        owner,loaded,key,reference,_=self.evicted_tile_load();f=self.fixture;before=owner.pool.stats();hwm=owner.tile_revision_highwater
        from xenova_vehicle.storage.contracts import json_bytes
        selection=json_bytes(loaded.request.selection,512);selection['tile_revision']=str(reference[2]+1)
        wrong=replace(loaded,request=replace(loaded.request,selection=canonical(selection)))
        for result,context,now in ((wrong,f.context,f.sensor.now),
                (loaded,replace(f.context,database_uuid=str(uuid4())),f.sensor.now),
                (loaded,f.context,loaded.request.job.expires_us)):
            with self.assertRaises(MapperAtlasNotReady):owner.install_loaded_tile(result,context=context,now_us=now)
            self.assertEqual(owner.pool.stats(),before);self.assertEqual(owner.tile_revision_highwater,hwm)
        other_window=owner.window.shifted((64,0))
        grid=EvidenceGrid(other_window.spec,frame_id='odom:'+f.context.odom_epoch,
            calibration_id=f.calibration.sha256,clock_id=f.raw.fields['clock_epoch'],kind='LIVE')
        configure_grid(grid,other_window)
        with owner.read_tiles(other_window) as (_,reader):proof=AtlasProofWindow(other_window,reader)
        frame=owner.prepare_frame(grid,proof);prepared_hwm=owner.tile_revision_highwater;prepared_stats=owner.pool.stats()
        with self.assertRaises(MapperAtlasNotReady):owner.install_loaded_tile(loaded,context=f.context,now_us=f.sensor.now)
        self.assertEqual(owner.tile_revision_highwater,prepared_hwm);self.assertEqual(owner.pool.stats(),prepared_stats)
        owner.cancel_frame(frame)
        type(self)._load_tile_head=max(type(self)._load_tile_head,owner.tile_revision_highwater)
        owner.install_loaded_tile(loaded,context=f.context,now_us=f.sensor.now)
        original=owner.pool.current('EVIDENCE64',key)
        with self.assertRaises(MapperAtlasNotReady):owner.install_loaded_tile(loaded,context=f.context,now_us=f.sensor.now)
        self.assertIs(owner.pool.current('EVIDENCE64',key),original)
        pending=owner.checkpoint_snapshot(allow_clean=True)
        with self.assertRaises(MapperAtlasNotReady):owner.install_loaded_tile(loaded,context=f.context,now_us=f.sensor.now)
        owner.retire_checkpoint(pending)
        allocator=f.db.connection.execute('SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone()[0]
        self.assertEqual(allocator,str(f.grant.highwater_after))

    def test_owner_pin_capacity_rejects_before_tile_accept_without_changing_missing_ref_or_hwm(self):
        from xenova_vehicle.mapping.atlas import AtlasResource
        class Holder:pass
        owner,loaded,key,reference,_=self.evicted_tile_load()
        available=owner.pool.current_refs('EVIDENCE64');self.assertEqual(len(available),1)
        holders=[Holder() for _ in range(16)]
        for holder in holders:owner.pool.retain_owner(holder,available)
        before=owner.pool.stats();hwm=owner.tile_revision_highwater
        with self.assertRaisesRegex(AtlasResource,'OWNER_PINS'):
            owner.install_loaded_tile(loaded,context=self.fixture.context,now_us=self.fixture.sensor.now)
        self.assertEqual(owner.pool.stats(),before);self.assertEqual(owner.tile_revision_highwater,hwm)
        self.assertIsNone(owner.pool.current('EVIDENCE64',key));self.assertIn((key,reference),owner.needs_load)
