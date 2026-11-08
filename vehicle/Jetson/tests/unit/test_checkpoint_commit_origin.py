"""원 SQLite CP와 LOAD 페이지의 서로 다른 SHA 및 copy-local descriptor를 검산한다.

실제 저장/재개방 fixture와 명시 software clock을 쓴다. 과거 COMMITTED를 새 source
성공으로 바꾸지 않으며 최대 metadata 시험도 Scene/물리 성능 수락을 뜻하지 않는다.
"""

from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
from uuid import UUID,uuid4

from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.resident_storage import _Database,StorageProfile
from xenova_vehicle.runtime.storage_copy import (CopyLease,EVIDENCE_REGION,PREFIX,_META_CAP,_encode,
    decode_copy,encode_checkpoint,encode_load)
from xenova_vehicle.storage.contracts import (CatalogHeadSnapshot,CommitReceipt,LoadRequest,MAX_U63,
    PersistenceError,StorageContext,StorageJob,canonical,json_bytes)
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import (CELL,PROVENANCE,CheckpointCommitOrigin,CheckpointPayload,
    ContextRecord,EvidenceTable,LoadedCheckpoint,ProvenanceRecord,TileVersion)
from xenova_vehicle.storage.repository import PersistenceStore


def uid():return str(uuid4())


class Clock:
    now=1_000_000
    def __call__(self):return self.now


def reference(job,payload,revision):
    context=job.context
    return canonical(dict(checkpoint_job_id=job.job_id,durable_revision=str(revision),
        manifest_sha256=sha256(payload.manifest).hexdigest(),manifest_length=len(payload.manifest),
        tile_count=len(payload.tile_references),**{name:getattr(context,name) for name in
            ('map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration',
             'primitive_sha256','origin_mapper_instance')}))


def copy_roundtrip(loaded,*,encoding=None):
    writer=CopyLease(uid(),uid(),1,Endpoint(uid(),uid()),Endpoint(uid(),uid()),uid())
    encoded=encode_load(loaded,writer) if encoding is None else encoding(loaded,writer)
    reader=replace(writer,read_token=uid());ready=dict(encoded.ready,copy_lease=reader.fields)
    result=decode_copy(reader,ready,encoded.evidence,encoded.descriptors,encoded.manifest,encoded.tiles)
    return result,encoded


def metadata(encoded):
    prefix=PREFIX.unpack_from(encoded.evidence)
    return json_bytes(encoded.evidence[256:256+prefix[3]],_META_CAP)


def mutate_metadata(encoded,change):
    prefix=list(PREFIX.unpack_from(encoded.evidence));value=metadata(encoded);change(value)
    raw=canonical(value);table=encoded.evidence[256+prefix[3]:]
    prefix[3]=len(raw);prefix[9]=sha256(raw).digest()
    return replace(encoded,evidence=PREFIX.pack(*prefix)+raw+table)


@unittest.skipUnless(os.name=='posix','실제 D SQLite/lock fixture는 대상 POSIX에서 검사한다')
class CheckpointCommitOriginTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'original.sqlite3'
        self.db=_Database(StorageProfile(str(self.path),'CREATE_IF_ABSENT'),time.monotonic_ns()//1000+5_000_000)
        self.addCleanup(self.db.close);self.db.connection.execute("UPDATE storage_metadata SET allocator_highwater='37' WHERE singleton=1")
        self.clock=Clock();self.context=StorageContext(self.db.metadata['database_uuid'],uid(),uid(),uid(),uid(),uid(),uid(),
            'explicit-software-config','explicit-software-calibration','a'*64,canonical(dict(profile='commit-origin-fixture')))
        self.store=PersistenceStore(self.db.connection,self.context.database_uuid,expires_us=self.clock()+5_000_000,clock_us=self.clock)
        self.store.reserve_provenance(self.job(),uid(),block_count=8,current_context_sha256=self.context.sha256)
        context=ContextRecord(canonical(dict(kind='RAW',body=dict(method='software-first-surface-fixture'))))
        records=[];tiles=[];unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        for index in range(3):
            identity=37+index;raw=struct.pack('<16sQQ',UUID(self.context.mapper_worker).bytes,index+1,100+index)
            record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),2,0,1,0,
                UUID(self.context.pi_host_boot).bytes,100,200,index+1,0,0,0,0,raw.ljust(128,b'\0'),0,bytes(16)))
            cell=CELL.pack(100,1,2,.05,.1,.01,identity,100,200,index+1,1,0)
            records.append(record);tiles.append(TileVersion(self.context.map_epoch,index,0,1,cell+unknown*4095))
        first=self.job();payload=CheckpointPayload.build(first,(tiles[0],),EvidenceTable((records[0],),(context,)),durable_cutoff_pi_us=200)
        self.assertEqual(self.store.commit(first,payload,current_context_sha256=self.context.sha256).status,'COMMITTED')
        self.original_job=self.job()
        # 원래 copy 순서는 manifest 정렬과 다르며 evidence는 새 두 tile의 delta다.
        self.original_payload=CheckpointPayload.build(self.original_job,(tiles[2],tiles[1]),EvidenceTable(tuple(records[1:]),(context,)),
            tile_references=tuple(tile.reference for tile in tiles),durable_cutoff_pi_us=200)
        self.receipt=self.store.commit(self.original_job,self.original_payload,current_context_sha256=self.context.sha256)
        self.assertEqual(self.receipt.status,'COMMITTED')
        self.selected=canonical(self.store.latest_complete(expires_us=self.clock()+5_000_000))

    def job(self):return StorageJob(self.context,uid(),self.clock(),self.clock()+5_000_000)

    def loaded(self):
        request=LoadRequest(self.job(),self.selected,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        return self.store.load(request,current_context_sha256=self.context.sha256)

    def test_actual_reopened_load_preserves_original_commit_while_page_and_closure_sha_differ(self):
        self.db.close();connection=sqlite3.connect(str(self.path),isolation_level=None,timeout=0);self.addCleanup(connection.close)
        self.store=PersistenceStore(connection,self.context.database_uuid,expires_us=self.clock()+5_000_000,clock_us=self.clock)
        loaded=self.loaded();origin=loaded.original_commit
        self.assertNotEqual(loaded.payload.sha256,self.original_payload.sha256)
        self.assertNotEqual(loaded.payload.evidence.sha256,self.original_payload.evidence.sha256)
        self.assertEqual(len(loaded.payload.tiles),3);self.assertEqual(len(origin.copy_tiles),2)
        self.assertEqual(origin.copy_tiles,tuple(tile.reference for tile in self.original_payload.tiles))
        self.assertEqual(origin.validate(self.original_job,loaded.payload.manifest,self.selected),self.original_payload.sha256)
        self.assertEqual(origin.receipt,self.store.query(self.original_job.job_id,self.original_job.digest(self.original_payload),
            expires_us=self.clock()+5_000_000))
        copied,encoded=copy_roundtrip(loaded)
        self.assertEqual(copied,loaded);self.assertEqual(copied.original_commit.bytes,origin.bytes)
        self.assertIn('original_commit',metadata(encoded))
        self.assertEqual(connection.execute('SELECT database_uuid,allocator_highwater FROM storage_metadata').fetchone(),
            (self.context.database_uuid,'45'))

    def test_order_hash_source_revision_and_original_lifetime_tampering_are_rejected(self):
        loaded=self.loaded();origin=loaded.original_commit
        variants=(replace(origin,copy_tiles=tuple(reversed(origin.copy_tiles))),
            replace(origin,evidence_sha256='f'*64),
            replace(origin,receipt=replace(origin.receipt,job_id=uid())),
            replace(origin,receipt=replace(origin.receipt,database_uuid=uid())),
            replace(origin,receipt=replace(origin.receipt,job_digest='c'*64)),
            replace(origin,receipt=replace(origin.receipt,origin_context_sha256='e'*64)),
            replace(origin,receipt=replace(origin.receipt,committed_j_us=self.original_job.expires_us)),
            replace(origin,receipt=replace(origin.receipt,committed_j_us=self.original_job.issued_us-1)))
        for changed in variants:
            with self.subTest(changed=changed):
                with self.assertRaises(PersistenceError):replace(loaded,original_commit=changed)
        for field,value in (('durable_revision',str(origin.receipt.durable_revision+1)),('manifest_sha256','d'*64),
                            ('map_epoch',uid()),('checkpoint_job_id',uid())):
            selected=dict(json_bytes(self.selected,2048));selected[field]=value
            with self.subTest(field=field):
                with self.assertRaises(PersistenceError):origin.validate(self.original_job,loaded.payload.manifest,canonical(selected))
        with self.assertRaises(PersistenceError):CheckpointCommitOrigin(replace(origin.receipt,status='DUPLICATE'),origin.evidence_sha256,origin.copy_tiles)
        with self.assertRaises(PersistenceError):replace(loaded,original_commit=origin.fields)
        with self.assertRaises(PersistenceError):origin.validate(replace(self.original_job,context=replace(self.context,mapper_worker=uid())),
            loaded.payload.manifest,self.selected)

    def test_original_expired_commit_is_history_while_new_load_keeps_its_own_current_deadline(self):
        stamp=self.receipt.committed_j_us;self.clock.now=self.original_job.expires_us+1
        loaded=self.loaded();copied,_=copy_roundtrip(loaded)
        self.assertTrue(copied.current(copied.request,self.context.sha256,self.clock()))
        self.assertEqual(copied.original_commit.receipt,self.receipt)
        self.assertEqual(copied.original_commit.receipt.committed_j_us,stamp)
        self.assertEqual(copied.original_commit.validate(self.original_job,copied.payload.manifest,self.selected),self.original_payload.sha256)
        self.assertFalse(self.receipt.current(self.original_job,self.original_payload,self.context.sha256,self.clock()))
        self.clock.now=copied.request.job.expires_us
        self.assertFalse(copied.current(copied.request,self.context.sha256,self.clock()))
        self.assertEqual(copied.original_commit.receipt.committed_j_us,stamp)

    def test_legacy_eight_fields_stay_legacy_and_malformed_ninth_field_is_not_downgraded(self):
        loaded=self.loaded();legacy=replace(loaded,original_commit=None)
        copied,encoded=copy_roundtrip(legacy)
        self.assertIsNone(copied.original_commit);self.assertNotIn('original_commit',metadata(encoded))
        for change in (lambda value:value.update(original_commit=None),
                       lambda value:value.update(original_commit=dict(loaded.original_commit.fields,profile='unknown')),
                       lambda value:value.update(original_commit=dict(loaded.original_commit.fields,extra=1))):
            with self.assertRaises(PersistenceError):
                copy_roundtrip(legacy,encoding=lambda value,lease:mutate_metadata(encode_load(value,lease),change))
        checkpoint=CheckpointRequest(self.original_job,self.original_payload)
        writer=CopyLease(uid(),uid(),1,Endpoint(uid(),uid()),Endpoint(uid(),uid()),uid())
        original=encode_checkpoint(checkpoint,writer)
        self.assertEqual(set(metadata(original)),{'direction','job','original_job','checkpoint_ref','selection','purpose','heads','completed_us'})
        poisoned=mutate_metadata(original,lambda value:value.update(original_commit=loaded.original_commit.fields))
        reader=replace(writer,read_token=uid());ready=dict(poisoned.ready,copy_lease=reader.fields)
        with self.assertRaises(PersistenceError):decode_copy(reader,ready,poisoned.evidence,poisoned.descriptors,poisoned.manifest,poisoned.tiles)

    def test_maximal_context_and_ordered_copy_descriptor_fit_existing_metadata_cap(self):
        # 이 case는 직렬화 최대형 software DTO이며 SQL/센서 사용권을 발급하지 않는다.
        padding=2048-len(canonical(dict(padding='')));source=canonical(dict(padding='x'*padding))
        origin=PiGeometryOrigin('v'*64,self.context.pi_host_boot,uid(),uid(),uid(),self.context.odom_epoch,'b'*64)
        context=replace(self.context,source_binding=source,configuration='c'*64,calibration='b'*64,geometry_origin=origin.fields)
        old=StorageJob(context,uid(),MAX_U63-5_000_000,MAX_U63,uid())
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)*4096
        tiles=tuple(TileVersion(context.map_epoch,-(1<<31)+index,(1<<31)-1,MAX_U63,unknown) for index in range(32))
        refs=tuple(tile.reference for tile in tiles)+tuple((-(1<<31)+index,(1<<31)-1,MAX_U63,'f'*64) for index in range(32,128))
        payload=CheckpointPayload.build(old,tuple(reversed(tiles)),tile_references=refs,
            scene=dict(scene_commit_nonce=old.scene_commit_nonce),durable_cutoff_pi_us=MAX_U63)
        receipt=CommitReceipt(context.database_uuid,old.job_id,old.digest(payload),'COMMITTED',MAX_U63,old.issued_us,context.sha256)
        descriptor=CheckpointCommitOrigin(receipt,payload.evidence.sha256,tuple(tile.reference for tile in payload.tiles))
        selected=reference(old,payload,MAX_U63)
        job=StorageJob(context,uid(),MAX_U63-5_000_000,MAX_U63)
        request=LoadRequest(job,selected,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        heads=CatalogHeadSnapshot(job.job_id,context.sha256,context.database_uuid,context.map_epoch,
            (('LANE',1<<32,MAX_U63),('SLOT',1<<32,MAX_U63)),job.issued_us,job.expires_us)
        page=CheckpointPayload(payload.manifest,tiles,payload.evidence)
        loaded=LoadedCheckpoint(request,old,selected,page,heads,job.issued_us,descriptor)
        copied,encoded=copy_roundtrip(loaded)
        self.assertEqual(len(descriptor.copy_tiles),32);self.assertEqual(len(source),2048)
        self.assertLessEqual(PREFIX.unpack_from(encoded.evidence)[3],16384)
        self.assertLessEqual(len(encoded.evidence),5*1024*1024)
        self.assertEqual(copied.original_commit.bytes,descriptor.bytes)
        self.assertEqual(copied.original_commit.payload_sha256(page.manifest),payload.sha256)
        self.assertEqual((_META_CAP,EVIDENCE_REGION),(16384,5*1024*1024))
        too_large=dict(metadata(encoded),heads=dict(padding='x'*16384))
        with self.assertRaisesRegex(PersistenceError,'metadata cap'):
            _encode(encoded.lease,job,page,request.digest,too_large)


if __name__=='__main__':unittest.main()
