"""실제 SQLite 파일의 원자 commit/복원/CAS. 시각 주입은 software 반례이며 실측 지연이 아니다."""

from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import time
import unittest
from uuid import UUID,uuid4

from xenova_vehicle.runtime.resident_storage import _Database,StorageProfile
from xenova_vehicle.storage import (BoundedStoragePort,CatalogRecord,CatalogReservation,CheckpointPayload,
    CheckpointRequest,ContextRecord,EvidenceTable,LoadRequest,PersistenceConflict,PersistenceCorrupt,
    PersistenceError,ProvenanceRecord,StorageContext,StorageJob,TileVersion)
from xenova_vehicle.storage.contracts import canonical,json_bytes
from xenova_vehicle.storage.records import CELL,PROVENANCE
from xenova_vehicle.storage.repository import PersistenceStore
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin


class Clock:
    now=1_000_000
    def __call__(self):return self.now


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'existing.sqlite3'
        self.db=_Database(StorageProfile(str(self.path),'CREATE_IF_ABSENT'),time.monotonic_ns()//1000+5_000_000)
        self.addCleanup(self.db.close)
        # 기존 allocator의 건너뛴 번호를 migration이1로 초기화하면 안 된다.
        self.db.connection.execute("UPDATE storage_metadata SET allocator_highwater='37' WHERE singleton=1")
        self.clock=Clock();self.connection=self.db.connection
        self.context=StorageContext(self.db.metadata['database_uuid'],str(uuid4()),str(uuid4()),
            str(uuid4()),str(uuid4()),str(uuid4()),str(uuid4()),'configuration-lab','calibration-lab','a'*64,
            canonical(dict(profile='explicit-test-source',kind='SYNTHETIC')))
        self.store=PersistenceStore(self.connection,self.context.database_uuid,expires_us=self.clock()+5_000_000,clock_us=self.clock)

    def job(self,expires=None):
        return StorageJob(self.context,str(uuid4()),self.clock(),self.clock()+5_000_000 if expires is None else expires)

    def reserve_catalog(self,start=1,end=65):
        job=self.job()
        reservation=CatalogReservation(self.context.database_uuid,self.context.map_epoch,'SLOT',start,start,end,
            str(uuid4()),self.context.mapper_worker,self.context.sha256)
        payload=CheckpointPayload.build(job,evidence=EvidenceTable(reservations=(reservation,)),intent='CATALOG_RESERVATION')
        receipt=self.store.commit(job,payload,current_context_sha256=self.context.sha256)
        return job,payload,reservation,receipt

    def evidence(self,identity):
        context=ContextRecord(canonical(dict(kind='METRIC',body=dict(method='SYNTHETIC_TEST',calibration='calibration-lab'))))
        raw=struct.pack('<16sQQ',UUID(self.context.mapper_worker).bytes,0,123)+bytes(96)
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),2,0,1,0,
            UUID(self.context.pi_host_boot).bytes,100,200,1,0,0,0,0,raw,0,bytes(16)))
        return context,record

    def checkpoint(self):
        original_provenance=self.job()
        allocated=self.store.reserve_provenance(original_provenance,str(uuid4()),block_count=8,current_context_sha256=self.context.sha256)
        self.assertEqual((allocated.start_inclusive,allocated.end_exclusive),(37,45))
        _,_,_,range_receipt=self.reserve_catalog();self.assertEqual(range_receipt.status,'COMMITTED')
        context,record=self.evidence(37)
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        observed=CELL.pack(0,1,2,0.,0.,.001,37,100,200,1,1,0)
        tile=TileVersion(self.context.map_epoch,0,0,1,observed+unknown*4095)
        catalog=CatalogRecord('SLOT',1,1,1,37,b'opaque-slot-body')
        evidence=EvidenceTable((record,),(context,),(catalog,),catalog_highwaters=(1,65))
        job=self.job();payload=CheckpointPayload.build(job,(tile,),evidence,durable_cutoff_pi_us=200)
        receipt=self.store.commit(job,payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        return job,payload,receipt

    def geometry_context(self):
        c=self.context
        binding=dict(service_instance=str(uuid4()),mapper_worker=c.mapper_worker,mapper_instance=c.origin_mapper_instance,
            raw_binding_sha256='a'*64,map_epoch=c.map_epoch,frame_epoch=c.frame_epoch,odom_epoch=c.odom_epoch,
            configuration=c.configuration,calibration='b'*64,primitive=c.primitive_sha256,
            scene_admission_ref=None,source_binding_revision='1')
        origin=PiGeometryOrigin('software-test',c.pi_host_boot,str(uuid4()),str(uuid4()),str(uuid4()),c.odom_epoch,'b'*64)
        self.context=replace(c,calibration='b'*64,source_binding=canonical(binding),geometry_origin=origin.fields)
        return binding,origin

    def test_same_mapper_scene_only_first_mapping_uses_original_geometry_range_without_rewriting_reservation(self):
        binding,origin=self.geometry_context()
        original_context=self.context
        reserve_job,reserve_payload,reservation,reserve_receipt=self.reserve_catalog()
        old_job_bytes=self.connection.execute('SELECT job_json FROM persistence_jobs WHERE job_id=?',(reserve_job.job_id,)).fetchone()[0]
        allocated=self.store.reserve_provenance(self.job(),str(uuid4()),block_count=8,current_context_sha256=self.context.sha256)
        context,record=self.evidence(allocated.start_inclusive)
        changed=dict(binding,scene_admission_ref=dict(admission_id=str(uuid4()),revision='1'),source_binding_revision='2')
        self.context=replace(self.context,source_binding=canonical(changed))
        self.assertNotEqual(original_context.sha256,self.context.sha256)
        job=self.job();payload=CheckpointPayload.build(job,evidence=EvidenceTable((record,),(context,),
            (CatalogRecord('SLOT',1,1,1,record.provenance_id,b'new-scene-first-mapping'),),catalog_highwaters=(1,65)),durable_cutoff_pi_us=200)
        receipt=self.store.commit(job,payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        self.assertEqual(self.connection.execute('SELECT context_sha256,job_id FROM catalog_ranges').fetchone(),(original_context.sha256,reserve_job.job_id))
        self.assertEqual(self.connection.execute('SELECT job_json FROM persistence_jobs WHERE job_id=?',(reserve_job.job_id,)).fetchone()[0],old_job_bytes)
        self.assertEqual(self.store.query(reserve_job.job_id,reserve_job.digest(reserve_payload),expires_us=self.clock()+5_000_000),reserve_receipt)
        self.assertFalse(reserve_receipt.current(reserve_job,reserve_payload,self.context.sha256,self.clock()))
        self.assertEqual(self.connection.execute('SELECT highwater FROM catalog_heads').fetchone(),(65,))

    def test_new_origin_raw_mapper_and_legacy_ranges_cannot_borrow_unused_ids_across_full_contexts(self):
        binding,origin=self.geometry_context();old=self.context
        self.reserve_catalog()
        allocated=self.store.reserve_provenance(self.job(),str(uuid4()),block_count=8,current_context_sha256=self.context.sha256)
        context,record=self.evidence(allocated.start_inclusive)
        # 원 reservation의 첫 ID가 아직 없다. 각 거절은 새 원 FAILED receipt만 남기고 HWM/geometry를 바꾸지 않는다.
        other_mapper=str(uuid4())
        variants=(replace(old,source_binding=canonical(dict(binding,raw_binding_sha256='d'*64,source_binding_revision='2'))),
            replace(old,source_binding=canonical(dict(binding,mapper_worker=other_mapper,source_binding_revision='2')),mapper_worker=other_mapper),
            replace(old,geometry_origin=replace(origin,gateway_instance=str(uuid4())).fields,
                source_binding=canonical(dict(binding,source_binding_revision='2'))))
        for current in variants:
            self.context=current;provided_context,provided_record=context,record
            if current.mapper_worker!=old.mapper_worker:
                block=self.store.reserve_provenance(self.job(),str(uuid4()),block_count=8,current_context_sha256=current.sha256)
                provided_context,provided_record=self.evidence(block.start_inclusive)
            job=self.job();payload=CheckpointPayload.build(job,evidence=EvidenceTable((provided_record,),(provided_context,),
                (CatalogRecord('SLOT',1,1,1,provided_record.provenance_id,b'forbidden-unused-id'),),catalog_highwaters=(1,65)),durable_cutoff_pi_us=200)
            self.assertEqual(self.store.commit(job,payload,current_context_sha256=current.sha256).status,'FAILED')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM catalog_versions').fetchone(),(0,))
        self.assertEqual(self.connection.execute('SELECT highwater FROM catalog_heads').fetchone(),(65,))
        # legacy 예약은 scene-only처럼 보여도 origin proof를 사후 삽입해 재사용하지 않는다.
        legacy_map=str(uuid4());legacy_binding=dict(binding,map_epoch=legacy_map)
        self.context=replace(old,map_epoch=legacy_map,geometry_origin=None,source_binding=canonical(legacy_binding))
        self.reserve_catalog()
        self.context=replace(self.context,geometry_origin=origin.fields,source_binding=canonical(dict(legacy_binding,source_binding_revision='2')))
        job=self.job();payload=CheckpointPayload.build(job,evidence=EvidenceTable((record,),(context,),
            (CatalogRecord('SLOT',1,1,1,record.provenance_id,b'no-legacy-backfill'),),catalog_highwaters=(1,65)),durable_cutoff_pi_us=200)
        self.assertEqual(self.store.commit(job,payload,current_context_sha256=self.context.sha256).status,'FAILED')

    def test_actual_checkpoint_reopen_load_preserves_uuid_highwater_and_original_proof(self):
        job,payload,receipt=self.checkpoint()
        reference=self.store.latest_complete(expires_us=self.clock()+5_000_000)
        self.assertEqual(reference['checkpoint_job_id'],job.job_id)
        self.db.close()
        import sqlite3
        connection=sqlite3.connect(str(self.path),isolation_level=None,timeout=0);self.addCleanup(connection.close)
        store=PersistenceStore(connection,self.context.database_uuid,expires_us=self.clock()+5_000_000,clock_us=self.clock)
        self.assertEqual(connection.execute('SELECT database_uuid,allocator_highwater FROM storage_metadata').fetchone(),
            (self.context.database_uuid,'45'))
        self.assertEqual(store.query(job.job_id,job.digest(payload),expires_us=self.clock()+5_000_000),receipt)
        request=LoadRequest(self.job(),canonical(reference),canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=store.load(request,current_context_sha256=self.context.sha256)
        self.assertTrue(loaded.current(request,self.context.sha256,self.clock()))
        self.assertEqual(loaded.payload.tiles[0].payload,payload.tiles[0].payload)
        self.assertEqual(loaded.payload.evidence.provenance,payload.evidence.provenance)
        self.assertEqual(loaded.payload.evidence.catalog,payload.evidence.catalog)
        self.assertEqual(loaded.heads.heads,(('LANE',1,0),('SLOT',65,1)))
        self.assertFalse(loaded.current(request,'b'*64,self.clock()))

    def test_catalog_cas_failure_is_atomic_and_original_failure_is_not_reissued(self):
        first_job,first_payload,_,first=self.reserve_catalog()
        second_job,second_payload,_,second=self.reserve_catalog()
        self.assertEqual(second.status,'FAILED')
        self.assertEqual(self.connection.execute('SELECT highwater,reservation_revision FROM catalog_heads').fetchone(),(65,1))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM catalog_ranges').fetchone(),(1,))
        self.assertEqual(self.connection.execute('SELECT next_revision FROM persistence_profile').fetchone(),(2,))
        self.assertEqual(self.store.commit(second_job,second_payload,current_context_sha256=self.context.sha256),second)
        duplicate=self.store.commit(first_job,first_payload,current_context_sha256=self.context.sha256)
        self.assertEqual((duplicate.status,duplicate.committed_j_us,duplicate.durable_revision),('DUPLICATE',first.committed_j_us,first.durable_revision))
        self.assertEqual(self.connection.execute('SELECT allocator_highwater FROM storage_metadata').fetchone(),('37',))
        with self.assertRaises(PersistenceConflict):
            self.store.query(first_job.job_id,'b'*64,expires_us=self.clock()+5_000_000)

    def test_new_mapper_skips_unused_old_range_but_can_update_durable_stable_id(self):
        self.checkpoint()
        self.context=replace(self.context,mapper_worker=str(uuid4()),origin_mapper_instance=str(uuid4()))
        range_job=self.job()
        allocated=self.store.reserve_provenance(range_job,str(uuid4()),block_count=8,current_context_sha256=self.context.sha256)
        context,record=self.evidence(allocated.start_inclusive)
        for stable_id,status in ((2,'FAILED'),(1,'COMMITTED')):
            with self.subTest(stable_id=stable_id):
                catalog=CatalogRecord('SLOT',stable_id,2,2,record.provenance_id,b'new-source-geometry')
                evidence=EvidenceTable((record,),(context,),(catalog,),catalog_highwaters=(1,65))
                job=self.job();payload=CheckpointPayload.build(job,evidence=evidence,durable_cutoff_pi_us=200)
                result=self.store.commit(job,payload,current_context_sha256=self.context.sha256)
                self.assertEqual(result.status,status)
        self.assertEqual(self.connection.execute('SELECT DISTINCT stable_id FROM catalog_versions ORDER BY stable_id').fetchall(),[(1,)])
        self.assertEqual(self.connection.execute('SELECT highwater,reservation_revision FROM catalog_heads').fetchone(),(65,1))

    def test_missing_proof_rolls_back_tiles_and_does_not_claim_complete_checkpoint(self):
        self.reserve_catalog()
        _,record=self.evidence(37)
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        observed=CELL.pack(0,1,2,0.,0.,.001,37,100,200,1,1,0)
        tile=TileVersion(self.context.map_epoch,0,0,1,observed+unknown*4095)
        job=self.job();payload=CheckpointPayload.build(job,(tile,),EvidenceTable(),durable_cutoff_pi_us=200)
        result=self.store.commit(job,payload,current_context_sha256=self.context.sha256)
        self.assertEqual(result.status,'FAILED')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM tile_versions').fetchone(),(0,))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM source_contexts').fetchone(),(0,))
        self.assertIsNone(self.store.latest_complete(expires_us=self.clock()+5_000_000))

    def test_corrupt_tile_bytes_never_become_empty_or_complete_restore(self):
        job,payload,_=self.checkpoint()
        damaged=bytes([100])+payload.tiles[0].payload[1:]
        self.connection.execute('UPDATE tile_versions SET payload=?',(damaged,))
        with self.assertRaises(PersistenceCorrupt):self.store.latest_complete(expires_us=self.clock()+5_000_000)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM checkpoints WHERE complete=1').fetchone(),(1,))
        self.assertEqual(self.connection.execute('SELECT database_uuid FROM storage_metadata').fetchone(),(self.context.database_uuid,))

    def test_corrupt_provenance_bytes_are_checked_when_loading_original_tile(self):
        self.checkpoint()
        self.connection.execute('UPDATE provenance_records SET payload=?',(bytes(256),))
        with self.assertRaises(PersistenceCorrupt):self.store.latest_complete(expires_us=self.clock()+5_000_000)

    def test_delayed_actual_commit_keeps_history_but_current_admission_expires(self):
        import sqlite3
        self.db.close();clock=self.clock
        class DelayedCommit(sqlite3.Connection):
            delay=False
            def execute(self,sql,parameters=()):
                result=super().execute(sql,parameters)
                if sql=='COMMIT' and self.delay:
                    self.delay=False;clock.now=1_100_001
                return result
        connection=sqlite3.connect(str(self.path),isolation_level=None,timeout=0,factory=DelayedCommit)
        self.addCleanup(connection.close)
        store=PersistenceStore(connection,self.context.database_uuid,expires_us=6_000_000,clock_us=clock)
        job=self.job(expires=1_100_000)
        reservation=CatalogReservation(self.context.database_uuid,self.context.map_epoch,'SLOT',1,1,65,str(uuid4()),
            self.context.mapper_worker,self.context.sha256)
        payload=CheckpointPayload.build(job,evidence=EvidenceTable(reservations=(reservation,)),intent='CATALOG_RESERVATION')
        port=BoundedStoragePort();request=CheckpointRequest(job,payload)
        self.assertTrue(port.offer(request));self.assertIs(port.take(current_context_sha256=self.context.sha256,now_us=clock()),request)
        connection.delay=True
        committed=store.commit(job,payload,current_context_sha256=self.context.sha256)
        self.assertEqual((committed.status,committed.committed_j_us),('COMMITTED',1_000_000))
        port.complete(request,committed,completed_us=clock())
        completion=port.take_receipt()
        self.assertFalse(completion.current(self.context.sha256,clock()))
        self.assertFalse(committed.current(job,payload,self.context.sha256,clock()))
        queried=store.query(job.job_id,job.digest(payload),expires_us=clock()+5_000_000)
        self.assertEqual(queried,committed)
        raw=connection.execute('SELECT job_json FROM persistence_jobs WHERE job_id=?',(job.job_id,)).fetchone()[0]
        self.assertEqual(json_bytes(bytes(raw),8192)['expires_us'],'1100000')

    def test_async_port_preserves_one_original_and_fences_unstarted_source_without_io(self):
        job,payload,_,receipt=self.reserve_catalog()
        request=CheckpointRequest(job,payload);port=BoundedStoragePort()
        self.assertTrue(port.offer(request));self.assertTrue(port.offer(CheckpointRequest(job,payload)))
        other_job=self.job();other=CheckpointRequest(other_job,CheckpointPayload.build(other_job,evidence=payload.evidence,intent='CATALOG_RESERVATION'))
        self.assertFalse(port.offer(other))
        self.assertIsNone(port.take(current_context_sha256='b'*64,now_us=self.clock()))
        self.assertIs(port.discard_unstarted(current_context_sha256='b'*64,now_us=self.clock()),request)
        self.assertTrue(port.offer(other))
        self.assertIs(port.take(current_context_sha256=self.context.sha256,now_us=self.clock()),other)
        self.assertIsNone(port.take(current_context_sha256=self.context.sha256,now_us=self.clock()))
        with self.assertRaises(PersistenceConflict):port.complete(other,receipt,completed_us=self.clock())

    def test_exact_binary_roundtrip_padding_and_unknown_cell_zero_are_checked(self):
        job,payload,_=self.checkpoint()
        table=payload.evidence
        self.assertEqual(EvidenceTable.decode(table.bytes,job).bytes,table.bytes)
        damaged=table.bytes[:-1]+b'\x01'
        with self.assertRaises(PersistenceError):EvidenceTable.decode(damaged,job)
        with self.assertRaises(PersistenceError):
            CatalogRecord('SLOT',1,1,1,37,bytes(225))
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        invalid=CELL.pack(0,0,0,0.,0.,0.,0,0,0,0,0,0)
        with self.assertRaises(PersistenceError):TileVersion(self.context.map_epoch,0,0,1,invalid+unknown*4095)


if __name__=='__main__':unittest.main()
