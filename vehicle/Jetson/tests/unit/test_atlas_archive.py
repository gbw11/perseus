"""원 full Archive pin과 stage 이후 scoped read의 수명/메모리 경계를 검산한다.

pool/작은 frame 시험은 명시 software 관측과 terminal 사건만 쓰며 Scene/SQL 성공을
꾸미지 않는다. full128 case는 실제 SQLite CP/네 LOAD와 원 COMMITTED를 사용한다.
"""

from dataclasses import replace
import gc
import os
import struct
import unittest
from uuid import UUID,uuid4
import weakref

from xenova_vehicle.mapping.atlas import AtlasPool,AtlasUpdate,AtlasError,AtlasResource,TILE_BYTES
from xenova_vehicle.mapping.local_map import EvidenceGrid,GridSpec
from xenova_vehicle.mapping.map_lattice import GridWindow,RollingPolicy
from xenova_vehicle.mapping.rolling_grid import configure_grid,reframe_grid
from xenova_vehicle.perception.pipeline import PerceptionPipeline,PipelineProfile
from xenova_vehicle.runtime.atlas_archive import SceneAtlasArchive
from xenova_vehicle.runtime.atlas_proof import AtlasProofWindow
from xenova_vehicle.runtime.mapper_atlas import MapperAtlas,MapperAtlasNotReady
from xenova_vehicle.storage.contracts import LoadRequest,StorageContext,StorageJob,canonical,json_bytes
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CELL,PROVENANCE,CheckpointPayload,EvidenceTable,ProvenanceRecord,TileVersion


def uid():return str(uuid4())


def install(pool,kind,key,value):
    delta=pool.prepare((AtlasUpdate(kind,key,bytes((value,))*TILE_BYTES,pool.current(kind,key)),))
    pool.accept(delta);pool.drain_retired();return delta.refs[0]


class AtlasArchiveBorrowTests(unittest.TestCase):
    def test_registered_borrow_keeps_owner_alive_and_staged_adoption_generation_unchanged(self):
        class Owner:pass
        pool=AtlasPool(reserved_workspace_bytes=0,max_versions=2,max_data_bytes=2*TILE_BYTES)
        original=install(pool,'EVIDENCE64',(0,0),1);holder=Owner();pin=pool.retain_owner(holder,(original,))
        owner_ref=weakref.ref(holder)
        delta=pool.prepare((AtlasUpdate('EVIDENCE64',(0,0),bytes((2,))*TILE_BYTES,original),))
        before=pool.stats();borrow=pool.borrow_owner(holder,pin,(original,))
        with borrow as reader:
            del holder;gc.collect()
            self.assertIsNotNone(owner_ref());self.assertEqual(reader(original),bytes((1,))*TILE_BYTES)
            self.assertEqual(pool.stats(),before)
            pool.validate(delta)
        self.assertIsNone(owner_ref());self.assertEqual(pool.stats(),before)
        with self.assertRaisesRegex(AtlasError,'ended'):reader(original)
        pool.accept(delta);pool.drain_retired()
        self.assertEqual((pool.stats()['versions'],pool.stats()['owner_pins']),(2,1))
        with self.assertRaisesRegex(AtlasResource,'VERSIONS'):pool.ensure_capacity(1)
        self.assertEqual(pool.drain_owner_releases(),(pin,));pool.coalesce_unleased()
        self.assertEqual((pool.stats()['versions'],pool.stats()['dirty_versions']),(1,1))
        self.assertIs(pool.current('EVIDENCE64',(0,0)),delta.refs[0]);pool.ensure_capacity(1)

    def test_foreign_or_copied_pin_live_and_nonmember_refs_do_not_create_a_borrow(self):
        class Owner:pass
        pool=AtlasPool(reserved_workspace_bytes=0)
        evidence=install(pool,'EVIDENCE64',(0,0),1);other=install(pool,'EVIDENCE64',(1,0),2)
        live=install(pool,'LIVE64',(0,0),3)
        holders=[Owner() for _ in range(16)];pins=[pool.retain_owner(owner,(evidence,)) for owner in holders]
        before=pool.stats()
        for owner,pin,refs in ((Owner(),pins[0],(evidence,)),(holders[0],replace(pins[0]),(evidence,)),
                               (holders[0],pins[0],(other,)),(holders[0],pins[0],(live,)),
                               (holders[0],pins[0],(evidence,evidence))):
            with self.assertRaises(AtlasError):
                with pool.borrow_owner(owner,pin,refs):pass
        del owner
        self.assertEqual(pool.stats(),before)
        with self.assertRaisesRegex(AtlasResource,'OWNER_PINS'):pool.retain_owner(Owner(),(evidence,))
        with pool.borrow_owner(holders[0],pins[0],(evidence,)) as reader:
            with self.assertRaises(AtlasError):reader(other)
        del holders;gc.collect()
        self.assertEqual(pool.stats()['owner_pins'],16)
        self.assertEqual(len(pool.drain_owner_releases()),16);self.assertEqual(pool.stats()['owner_pins'],0)


class MapperSceneArchiveTests(unittest.TestCase):
    def test_independent_scene_pin_reads_old_body_after_stage_and_releases_only_on_gc_drain(self):
        import test_mapper_atlas as fixtures
        f=fixtures.MapperAtlasTests(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups)
        f.hit();f.accept();owner=f.owner;snapshot=owner.checkpoint_snapshot()
        context=StorageContext(uid(),uid(),uid(),owner.map_epoch,uid(),uid(),uid(),'software-config','software-calibration',
            'a'*64,canonical(dict(profile='archive-lifetime-only')))
        job=StorageJob(context,uid(),1000,2000)
        request=CheckpointRequest(job,CheckpointPayload.build(job,snapshot.tiles,
            tile_references=snapshot.tile_references,durable_cutoff_pi_us=2002))
        copy_pin=owner.retain_checkpoint_owner(snapshot,request)
        archive=owner.retain_scene(snapshot,request)
        self.assertIs(type(archive),SceneAtlasArchive);self.assertIs(owner.retain_scene(snapshot,request),archive)
        self.assertIsNot(archive.pin,copy_pin);self.assertFalse(hasattr(archive,'tiles'))
        self.assertEqual(archive.checkpoint_sha256,request.payload.sha256)
        self.assertEqual(archive.checkpoint_digest,request.digest);self.assertIsNone(archive.checkpoint_ref)
        with self.assertRaises(TypeError):SceneAtlasArchive()
        changed_tile=replace(request.payload.tiles[0],payload=bytes(bytearray(request.payload.tiles[0].payload)))
        changed_request=CheckpointRequest(job,replace(request.payload,tiles=(changed_tile,)))
        before=owner.pool.stats()
        with self.assertRaises(MapperAtlasNotReady):owner.retain_scene(snapshot,changed_request)
        self.assertEqual(owner.pool.stats(),before)
        del changed_request,changed_tile
        owner.retire_checkpoint(snapshot)
        f.hit(12,8,2000);delta=owner.prepare_frame(f.grid,f.proof);before=owner.pool.stats()
        with owner.read_archive(archive,f.grid.rolling_window) as reader:
            original=reader(0,0)
            self.assertIs(original,request.payload.tiles[0].payload)
            self.assertEqual(CELL.unpack_from(original,(1*64+1)*64)[6],11)
            with self.assertRaises(MapperAtlasNotReady):reader(1,0)
            self.assertEqual(owner.pool.stats(),before)
        del original
        with self.assertRaisesRegex(MapperAtlasNotReady,'ended'):reader(0,0)
        with self.assertRaises(MapperAtlasNotReady):owner.retain_scene(snapshot,request)
        owner.accept_frame(delta);owner.drain_retired()
        self.assertEqual(owner.pool.stats()['versions'],3)
        archive_ref=weakref.ref(archive);del archive,request;gc.collect()
        self.assertIsNone(archive_ref());self.assertEqual(owner.pool.stats()['owner_pins'],2)
        self.assertEqual(len(owner.drain_owner_releases()),2)
        self.assertEqual((owner.pool.stats()['versions'],owner.pool.stats()['dirty_versions']),(2,1))


@unittest.skipUnless(os.name=='posix','실제 SQLite/원 전체 LOAD fixture는 대상 POSIX에서 검사한다')
class CompleteAtlasArchiveTests(unittest.TestCase):
    def test_full128_scene_pin_copy32_and_old_bootstrap_refs_survive_current_replacements(self):
        import test_map_checkpoint as fixtures
        from xenova_vehicle.runtime.map_checkpoint import _pipeline_state,CheckpointPages
        f=fixtures.MapCheckpointTests(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups)
        profile=PipelineProfile(grid=GridSpec(8,8,.01,(-.005,-.005)));policy=RollingPolicy(1,1)
        pipeline=PerceptionPipeline(f.calibration,profile,rolling_policy=policy)
        pipeline.grid=EvidenceGrid(profile.grid,frame_id='odom:'+f.context.odom_epoch,
            calibration_id=f.calibration.sha256,clock_id=f.raw.fields['clock_epoch'],kind='LIVE')
        configure_grid(pipeline.grid,GridWindow.from_spec(profile.grid))
        state=_pipeline_state(pipeline,f.context)
        state.update(tile_revision_highwater=999,reference_provenance={},alignment_provenance=None)
        state_ref,pages=encode_mapper_state(state);unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)*4096
        tiles=tuple(TileVersion(f.context.map_epoch,index,0,index+1,unknown) for index in range(128))
        for end in (32,64,96,128):
            job=f.job();payload=CheckpointPayload.build(job,tiles[end-32:end],EvidenceTable(contexts=pages),
                tile_references=tuple(tile.reference for tile in tiles[:end]),scene=dict(mapper_state=state_ref),
                durable_cutoff_pi_us=f.frame.pi_interval[1])
            self.assertEqual(f.store.commit(job,payload,current_context_sha256=f.context.sha256).status,'COMMITTED')
        selected=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        loader=CheckpointPages(selected,f.context);complete=None
        for cursor in (0,32,64,96):
            request=LoadRequest(f.job(),selected,canonical(dict(kind='CHECKPOINT_PAGE',cursor=cursor)))
            complete=loader.accept(f.store.load(request,current_context_sha256=f.context.sha256),now_us=f.sensor.now)
        pipeline=PerceptionPipeline(f.calibration,profile,rolling_policy=policy)
        owner=MapperAtlas.from_complete(complete,pipeline=pipeline,current_scope=f.scope,
            selected_bootstrap=dict(status='COMPLETE',database_uuid=f.context.database_uuid,selected_checkpoint=json_bytes(selected,2048)),
            now_us=f.sensor.now,reserved_workspace_bytes=8*1024*1024)
        window=owner.window;grid=pipeline.grid
        with owner.read_tiles(window) as (_,reader):proof=AtlasProofWindow(window,reader)
        records=[];context=f.scope.context_record('RAW')
        for index in range(32):
            next_window=window.shifted((index*64,0))
            with owner.read_tiles(next_window) as (live,reader):
                reframe_grid(grid,next_window,live);proof=proof.reframe(next_window,immutable_tile_reader=reader)
            identity=f.collector._allocate();sequence=index+2
            raw=struct.pack('<16sQQ',UUID(f.raw.fields['stream_instances']['depth_right']).bytes,sequence,400001+index)
            records.append(ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),2,0,1,0,
                UUID(f.context.pi_host_boot).bytes,*f.frame.pi_interval,sequence,0,0,0,0,raw.ljust(128,b'\0'),0,bytes(16))))
            proof.write(*next_window.local_to_global(0),CELL.pack(100,1,2,.05,.1,.01,identity,*f.frame.pi_interval,sequence,1,0))
            delta=owner.prepare_frame(grid,proof);owner.accept_frame(delta);owner.drain_retired()
        # Archive를 새 frame 뒤에 만들어도 bootstrap의 옛 pool refs/body만 선택해야 한다.
        bootstrap=owner.archive_from_complete(complete,current_scope=f.scope,now_us=f.sensor.now)
        self.assertEqual(len(bootstrap.pool_refs),128)
        self.assertIsNot(bootstrap.pool_refs[0],owner.pool.current('EVIDENCE64',(0,0)))
        self.assertEqual(bootstrap.checkpoint_sha256,complete.original_commit.payload_sha256(complete.manifest))
        snapshot=owner.checkpoint_snapshot();self.assertEqual((len(snapshot.tiles),len(snapshot.tile_references)),(32,128))
        job=f.job();payload=CheckpointPayload.build(job,snapshot.tiles,EvidenceTable(tuple(records),(context,)),
            tile_references=snapshot.tile_references,durable_cutoff_pi_us=f.frame.pi_interval[1])
        request=CheckpointRequest(job,payload);copy_pin=owner.retain_checkpoint_owner(snapshot,request)
        scene=owner.retain_scene(snapshot,request)
        self.assertEqual((len(copy_pin.refs),len(scene.pin.refs)),(32,128));self.assertIsNot(copy_pin,scene.pin)
        receipt=f.store.commit(job,payload,current_context_sha256=f.context.sha256)
        self.assertTrue(receipt.current(job,payload,f.context.sha256,f.sensor.now))
        owner.complete_checkpoint(snapshot)
        with owner.read_tiles(window) as (live,reader):
            reframe_grid(grid,window,live);proof=proof.reframe(window,immutable_tile_reader=reader)
        proof.write(*window.local_to_global(1),CELL.pack(100,1,2,.05,.1,.01,records[0].provenance_id,*f.frame.pi_interval,2,1,0))
        delta=owner.prepare_frame(grid,proof);before=owner.pool.stats()
        with owner.read_archive(bootstrap,window) as old_reader,owner.read_archive(scene,window) as frozen_reader:
            self.assertIs(old_reader(0,0),complete.tiles[0].payload)
            self.assertEqual(CELL.unpack_from(old_reader(0,0))[6],0)
            self.assertEqual(CELL.unpack_from(frozen_reader(0,0))[6],records[0].provenance_id)
            self.assertEqual(owner.pool.stats(),before)
        owner.accept_frame(delta);owner.drain_retired()
        self.assertEqual(owner.pool.stats()['dirty_versions'],1)
        with owner.read_archive(scene,window) as reader:
            self.assertEqual(CELL.unpack_from(reader(0,0),64)[6],0)
        self.assertEqual(owner.tile_revision_highwater,1032)
        self.assertEqual(f.db.connection.execute('SELECT allocator_highwater FROM storage_metadata').fetchone()[0],str(f.grant.highwater_after))


if __name__=='__main__':unittest.main()
