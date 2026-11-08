"""실제 SQLite/LOAD codec/S-M seqpacket의 COMPLETE 연결을 검사한다.

native arena와 S의 control full-send는 기존 명시 fixture를 쓴다. 이 fact 주입은 실제
SHM/token/child 종료 증명이 아니며 semantic decode는 실제 OwnedCopy에서 수행한다.
"""

from collections import deque
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
import zlib

from test_source_identity import uid
import test_source_runtime as source_fixture
from test_mapper_storage_client import Actor,Arena
from xenova_vehicle.runtime.data_channel import DataPacket
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.mapper_bootstrap import MapperBootstrap,BootstrapReport,BootstrapIdentityEvidence
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox,StorageAbort
from xenova_vehicle.runtime.mapper_storage_client import MapperStorageClient
from xenova_vehicle.runtime.native_resources import ArenaSpec,DB_COPY_BYTES
from xenova_vehicle.runtime.resident_db import ResidentDbBroker
from xenova_vehicle.runtime.resident_runtime import _Runtime
from xenova_vehicle.runtime.resident_storage import _Database,StorageProfile
from xenova_vehicle.runtime.source_identity import proposal_metadata
from xenova_vehicle.runtime.storage_copy import CopyLease,OwnedCopy,encode_load
from xenova_vehicle.runtime.storage_messages import db_payload
from xenova_vehicle.storage.contracts import StorageContext,StorageJob,canonical,json_bytes
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin
from xenova_vehicle.storage.records import CELL,TileVersion,CheckpointPayload
from xenova_vehicle.storage.repository import PersistenceStore


@unittest.skipUnless(os.name=='posix' and hasattr(socket,'SOCK_SEQPACKET'),'actual SQLite writer and S/M seqpacket required')
class MapperBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.f=source_fixture.SourceRuntimeTests(methodName='runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        f=self.f;self.clock=lambda:f.now
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=_Database(StorageProfile(str(Path(tmp.name)/'restore.sqlite3'),'CREATE_IF_ABSENT'),
            time.monotonic_ns()//1000+5_000_000)
        self.addCleanup(self.db.close)
        self.store=PersistenceStore(self.db.connection,self.db.metadata['database_uuid'],
            expires_us=f.now+5_000_000,clock_us=self.clock)
        self.m=LifecyclePeer(f.peer.service_instance,f.peer.channel_epoch,f.peer.mapper_worker,'M','a'*64)
        self.d=LifecyclePeer(f.peer.service_instance,uid(),uid(),'D','a'*64)
        arena=uid();self.spec=ArenaSpec(arena,'/xnv_db_'+arena,DB_COPY_BYTES,'DB_COPY')
        self.mailbox=MapperStorageMailbox(f.raw.sha256)
        self.arena=Arena();self.arena.clock=self.clock
        self.actor=Actor(self.m,self.clock)
        self.client=MapperStorageClient(self.actor,self.mailbox,(self.spec,('fake-writable','fake-ready')),
            clock_us=self.clock,arena_factory=lambda *a,**kw:self.arena)
        self.actor.client=self.client;self.addCleanup(self.client.close)
        self.bootstrap=MapperBootstrap(self.mailbox,mapper_worker=f.peer.mapper_worker,mapper_instance=f.incarnation,
            cap_us=f.now+15_000_000,clock_us=self.clock)
        self.runtime=_Runtime.__new__(_Runtime);self.runtime._ensure_db_broker=lambda:None
        self.runtime.storage_material=lambda:f.material;self.runtime.db_outbox=deque()
        self.runtime.binding=f.raw;self.runtime.workers={'M':SimpleNamespace(peer=self.m)}
        self.runtime.publication_context=None
        self.original=None

    def complete_database(self,count=0):
        f=self.f;sample=f.sample;old_worker,old_instance=uid(),uid()
        publication=dict(service_instance=f.peer.service_instance,mapper_worker=old_worker,mapper_instance=old_instance,
            raw_binding_sha256=f.raw.sha256,map_epoch=uid(),frame_epoch=uid(),odom_epoch=sample.odom_epoch,
            configuration=f.policy.configuration,calibration=f.policy.calibration,primitive=f.policy.primitive,
            scene_admission_ref=None,source_binding_revision='1')
        origin=PiGeometryOrigin(sample.vehicle,sample.host_boot,sample.gateway_instance,sample.device_session,
            sample.pi_session,sample.odom_epoch,sample.calibration)
        context=StorageContext(self.db.metadata['database_uuid'],old_worker,old_instance,publication['map_epoch'],
            publication['frame_epoch'],sample.odom_epoch,sample.host_boot,f.policy.configuration,f.policy.calibration,
            f.policy.primitive,canonical(publication),geometry_origin=origin.fields)
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)*4096
        tiles=tuple(TileVersion(context.map_epoch,index,0,1,unknown) for index in range(count))
        for end in range(32,count+32,32) if count else (0,):
            end=min(count,end);job=StorageJob(context,uid(),f.now,f.now+5_000_000)
            payload=CheckpointPayload.build(job,tiles[max(0,end-32):end],
                tile_references=tuple(tile.reference for tile in tiles[:end]),scene=dict(profile='legacy-data-only'),durable_cutoff_pi_us=1)
            self.assertEqual(self.store.commit(job,payload,current_context_sha256=context.sha256).status,'COMMITTED')
        selected=self.store.latest_complete(expires_us=f.now+5_000_000)
        f.bootstrap=dict(f.bootstrap,status='COMPLETE',database_uuid=context.database_uuid,selected_checkpoint=selected)
        binding=dict(service_instance=f.peer.service_instance,storage_worker=self.d.worker_instance,
            storage_profile_sha256='c'*64,database_uuid=context.database_uuid,db_schema_version=1)
        f.material=(binding,f.bootstrap)
        self.runtime.db_broker=ResidentDbBroker(self.d,self.m,binding,self.spec,clock_us=self.clock)
        self.original=context

    def source_cycle(self,count=8):
        f=self.f
        for _ in range(count):
            f.supervisor.pump(f.material,bind_ready=True,on_publication=self.published,
                on_bootstrap_load=self.runtime.request_storage_bootstrap_load,
                bootstrap_completion=lambda:self.runtime.db_broker.last_completion)
            f.mapper.pump()

    def published(self,binding,context):
        self.f.publications.append((binding,context));self.runtime.set_publication(binding,context)

    def packet(self,peer,kind,payload,job,*,child):
        return encode_structure(dict(version=1,service_instance=peer.service_instance,channel_epoch=peer.channel_epoch,
            sender_instance=peer.worker_instance if child else peer.service_instance,message_sequence='1',kind=kind,
            binding_domain='STORAGE',binding_revision='1',request_id=job.job_id,
            issued_j_us=str(self.f.now),expires_j_us=str(job.expires_us),payload=payload))

    def sent(self,drafts,job):
        for draft in drafts:
            peer=self.d if draft.role=='D' else self.m
            packet=self.packet(peer,draft.kind,draft.payload,job,child=False)
            self.runtime.db_broker.note_sent(draft,packet,self.f.now)

    def copy_page(self,request):
        # explicit adopt 전에는 기존 client가 M PREPARE를 재발급하지 않는다.
        before=len(self.actor.link.packets)
        self.client.pump(current_context_sha256=request.job.context.sha256)
        self.assertEqual(len(self.actor.link.packets),before)
        self.assertTrue(self.client.adopt_bootstrap_load(current_context_sha256=request.job.context.sha256))
        self.f.mapper.offer_bootstrap_load(request);self.source_cycle()
        drafts=tuple(self.runtime.db_outbox);self.runtime.db_outbox.clear();self.sent(drafts,request.job)
        broker=self.runtime.db_broker
        loaded=self.store.load(request,current_context_sha256=request.job.context.sha256)
        encoding=encode_load(loaded,broker.pending.lease)
        outgoing=broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('READY',encoding.ready),request.job,child=True))
        ready_draft,=outgoing;self.sent(outgoing,request.job)
        ready=ready_draft.payload['body'];lease=CopyLease.from_fields(ready['copy_lease'])
        self.arena.owned=OwnedCopy(lease,canonical(ready),encoding.evidence,encoding.descriptors,encoding.manifest,encoding.tiles)
        self.assertTrue(self.client.receive(self.packet(self.m,'DB_JOB',ready_draft.payload,request.job,child=False)))
        self.client.pump(current_context_sha256=request.job.context.sha256)
        release=self.actor.link.packets[-1]
        self.assertEqual(release.payload['op'],'RELEASE')
        broker.receive_mapper(release)
        self.source_cycle()
        # 물리 RELEASE만으로는 M/S 어느 쪽도 identity를 열지 않는다.
        self.assertIsNone(self.f.mapper.publication);self.assertIsNone(self.f.supervisor.owner.binding)
        self.f.now+=10
        self.bootstrap.compute_step()

    def start(self,count=0):
        self.complete_database(count);self.f.vehicle();self.source_cycle()
        self.bootstrap.offer_material(self.f.mapper.material);self.bootstrap.compute_step()
        request=self.bootstrap.take_request();self.assertIsNotNone(request);return request

    def test_actual_four_pages_decode_then_original_identity_and_late_processor_port(self):
        request=self.start(128);jobs=[]
        for page in range(4):
            jobs.append(request);self.copy_page(request)
            self.assertIsNone(self.bootstrap.failure)
            if page<3:
                self.assertIsNone(self.bootstrap.take_complete())
                self.client.pump(current_context_sha256=self.mailbox.context_sha256)
                request=self.bootstrap.take_request();self.assertIsNotNone(request)
        report=self.bootstrap.take_complete();self.assertIs(type(report),BootstrapReport)
        self.assertEqual(len({r.job.context.sha256 for r in jobs}),4)
        self.assertEqual([r.job.issued_us for r in jobs],[1000,1010,1020,1030])
        self.assertTrue(all(r.job.expires_us-r.job.issued_us==5_000_000 for r in jobs))
        self.assertEqual((report.issued_us,report.expires_us),(jobs[-1].job.issued_us,jobs[-1].job.expires_us))
        received=[];processor=SimpleNamespace(accept_bootstrap_source=received.append)
        self.bootstrap.attach_processor(processor);self.bootstrap.compute_step()
        self.assertEqual(len(received),1);complete=received[0]
        self.assertEqual(len(complete.tiles),128);self.assertEqual(len(complete.page_receipts),4)
        self.assertIsNone(complete.state)
        from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
        actual_processor=ResidentMapperProcessor(None,self.f.raw)
        actual_processor.accept_bootstrap_source(complete)
        self.assertIs(actual_processor._bootstrap_source,complete)
        self.assertIsNone(actual_processor._accepted)
        with self.assertRaises(ValueError):actual_processor.accept_bootstrap_source(True)
        self.f.mapper.accept_bootstrap(report);self.source_cycle()
        self.assertEqual(len(self.f.publications),1)
        binding=self.f.mapper.publication
        for name in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration'):
            self.assertEqual(binding[name],getattr(self.original,name))
        self.assertEqual(self.f.mapper.storage_context.database_uuid,self.original.database_uuid)
        self.assertEqual(self.f.supervisor.bootstrap.evidence.checkpoint_ref,complete.checkpoint_ref)
        evidence=self.f.supervisor.bootstrap.evidence
        self.assertTrue(evidence.current(self.f.bootstrap,self.f.peer,self.f.incarnation,self.f.now))
        self.assertFalse(evidence.current(dict(self.f.bootstrap,database_uuid=uid()),self.f.peer,self.f.incarnation,self.f.now))
        self.assertFalse(evidence.current(dict(self.f.bootstrap,allocator_highwater='2'),self.f.peer,self.f.incarnation,self.f.now))
        self.assertFalse(evidence.current(self.f.bootstrap,self.f.peer,self.f.incarnation,report.expires_us))
        self.assertEqual(self.store.latest_complete(expires_us=self.f.now+5_000_000),self.f.bootstrap['selected_checkpoint'])
        self.assertIsNone(binding['scene_admission_ref'])
        self.assertFalse(self.f.jio.worlds)

    def test_missing_corrupt_or_historical_material_never_becomes_empty_or_reissued(self):
        self.complete_database()
        for reason in ('EXPIRED','CANCELLED','SOURCE_REPLACED','DB_ABORT'):
            mailbox=MapperStorageMailbox(self.f.raw.sha256)
            bootstrap=MapperBootstrap(mailbox,mapper_worker=self.f.peer.mapper_worker,mapper_instance=self.f.incarnation,
                cap_us=self.f.now+15_000_000,clock_us=self.clock)
            bootstrap.offer_material(self.f.material);bootstrap.compute_step();original=bootstrap.take_request()
            self.assertIsNotNone(original)
            if reason=='EXPIRED':
                old=self.f.now;self.f.now=original.job.expires_us;bootstrap.compute_step();self.f.now=old
            elif reason=='SOURCE_REPLACED':
                bootstrap.offer_material((dict(self.f.material[0],storage_worker=uid()),self.f.material[1]));bootstrap.compute_step()
            elif reason=='DB_ABORT':
                dispatch=mailbox.main_take(current_context_sha256=original.job.context.sha256,now_us=self.f.now)
                mailbox.main_complete(dispatch,StorageAbort('CANCELLED'),completed_us=self.f.now);mailbox.main_release(dispatch)
                bootstrap.compute_step()
            else:bootstrap.cancel('CANCELLED');bootstrap.compute_step()
            with self.subTest(reason=reason):
                self.assertIsNotNone(bootstrap.failure);self.assertIsNone(bootstrap.take_request());self.assertIsNone(bootstrap.take_complete())
                bootstrap.compute_step();self.assertIsNone(bootstrap.take_request())
        bootstrap=MapperBootstrap(MapperStorageMailbox(self.f.raw.sha256),mapper_worker=self.f.peer.mapper_worker,
            mapper_instance=self.f.incarnation,cap_us=self.f.now+15_000_000,clock_us=self.clock)
        value=dict(self.f.bootstrap,status='CORRUPT');bootstrap.offer_material((self.f.material[0],value));bootstrap.compute_step()
        self.assertIsNotNone(bootstrap.failure);self.assertIsNone(bootstrap.take_request())

    def test_empty_bootstrap_does_not_load_or_change_original_source_flow(self):
        self.bootstrap.offer_material(self.f.material);self.bootstrap.compute_step()
        self.assertIsNone(self.bootstrap.take_request());self.assertIsNone(self.bootstrap.take_complete())
        self.f.vehicle();self.f.cycle()
        self.assertEqual(len(self.f.publications),1);self.assertIsNone(self.bootstrap.failure)

    def test_false_foreign_and_late_complete_reports_do_not_open_identity(self):
        request=self.start();self.copy_page(request);report=self.bootstrap.take_complete()
        self.assertIsNotNone(report)
        wire=self.f.mapper._vehicle_head
        metadata=proposal_metadata(raw_binding=self.f.raw,mapper_instance=self.f.incarnation,
            bootstrap=self.f.bootstrap,vehicle_wire=wire,policy=self.f.policy,nonce=uid())
        self.f.mapper.link.send('SOURCE_IDENTITY',metadata,issued_us=self.f.now,expires_us=request.job.expires_us)
        self.f.mapper.link.flush();proposal,=self.f.supervisor.link.poll()
        with self.assertRaises(ValueError):
            self.f.supervisor.owner.accept(proposal,bootstrap=self.f.bootstrap,vehicle_wire=wire,
                vehicle_received_us=1000,now_us=self.f.now,restore_material=True)
        self.assertIsNone(self.f.supervisor.owner.binding)
        self.f.mapper.link.send('SOURCE_BOOTSTRAP_COMPLETE',{},report.body,request_id=report.request_id,
            issued_us=report.issued_us,expires_us=report.expires_us);self.f.mapper.link.flush()
        original,=self.f.supervisor.link.poll()
        value=json.loads(report.body)
        for change in ('page','digest','future','foreign','closure_type'):
            body=json.loads(report.body);header=original.header
            if change=='page':body['pages']=[]
            elif change=='digest':body['pages'][0]['job_digest']='f'*64
            elif change=='future':body['completed_us']=str(request.job.expires_us)
            elif change=='foreign':header['mapper_worker']=uid()
            else:body['tile_count']=True
            raw=canonical(body);header['payload_length']=len(raw);header['payload_crc32']=zlib.crc32(raw)
            hb=canonical(header);packet=DataPacket(struct.pack('<II',len(hb),len(raw))+hb+raw)
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.f.supervisor.bootstrap.accept_complete(packet,self.f.material,self.f.now)
            self.assertIsNone(self.f.supervisor.owner.binding)
        with self.assertRaises(ValueError):
            self.f.supervisor.bootstrap.accept_complete(original,self.f.material,request.job.expires_us)
        self.assertIsNone(self.f.supervisor.bootstrap.evidence)
        self.assertEqual(value['checkpoint_ref'],self.f.bootstrap['selected_checkpoint'])
        with self.assertRaises(ValueError):BootstrapReport()
        with self.assertRaises(ValueError):BootstrapIdentityEvidence()
