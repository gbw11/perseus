"""실제 Linux D entry/기존 arena의 작은 왕복. source·scene 사용권을 승인하는 시험은 아니다."""

from dataclasses import replace
from hashlib import sha256
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from xenova_vehicle.runtime.channel import control_pair
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.native_resources import ArenaSpec,DB_COPY_BYTES
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.resident_profile import release_sha256
from xenova_vehicle.runtime.resident_storage import (StorageBoot,StorageProfile,_Database,binding_payload,
    discovery_payload,parse_bootstrap_result,storage_binding,storage_main)
from xenova_vehicle.runtime.resident_transport import ResidentLink,j_us
from xenova_vehicle.runtime.storage_copy import CopyArena,CopyLease,decode_copy,encode_checkpoint
from xenova_vehicle.runtime.storage_messages import db_payload,make_load_request,prepare_payload
from xenova_vehicle.storage import CheckpointPayload,CheckpointRequest,StorageContext,StorageJob
from xenova_vehicle.storage.contracts import PersistenceError,canonical


def identity():return str(uuid4())


@unittest.skipUnless(sys.platform=='linux','actual Linux seqpacket/SQLite/shared memory fixture')
class StorageIpcTests(unittest.TestCase):
    def test_d_entry_copy_commit_query_load_and_complete_reopen(self):
        from multiprocessing import shared_memory
        with tempfile.TemporaryDirectory(prefix='xnv-storage-ipc-') as temporary:
            directory=Path(temporary);directory.chmod(0o700)
            peer=LifecyclePeer(identity(),identity(),identity(),'D',release_sha256())
            mapper=Endpoint(identity(),identity());storage=Endpoint(peer.channel_epoch,peer.worker_instance)
            arena_id=identity();spec=ArenaSpec(arena_id,'/xnv_db_'+arena_id,DB_COPY_BYTES,'DB_COPY')
            # 테스트 fixture만 IPC를 생성한다. 제품 helper는 아래 원 handles를 attach할 뿐이다.
            memory=shared_memory.SharedMemory(name=spec.name[1:],create=True,size=spec.size_bytes)
            tokens=(multiprocessing.get_context('spawn').Semaphore(1),multiprocessing.get_context('spawn').Semaphore(0))
            parent,child=control_pair();link=ResidentLink(parent,peer,'S')
            profile=StorageProfile(str(directory/'map.sqlite3'),'CREATE_IF_ABSENT')
            boot=StorageBoot(peer,j_us()+15_000_000,profile,copy_handles=(spec,tokens))
            outcome=[];inbox=[]

            def run():
                try:storage_main(child,boot);outcome.append('RETURNED')
                except BaseException as error:outcome.append(error)

            def receive(kind,op=None):
                end=j_us()+5_000_000
                while j_us()<end:
                    for index,packet in enumerate(inbox):
                        if packet.kind==kind and (op is None or packet.payload.get('op')==op):return inbox.pop(index)
                    inbox.extend(p for p in link.poll() if p.kind!='HEARTBEAT')
                    time.sleep(.002)
                self.fail('missing actual D packet '+kind+'/'+str(op)+'; '+str(outcome))

            worker=threading.Thread(target=run,daemon=True);worker.start();camera=None
            try:
                receive('HELLO')
                issued=j_us();discover=link.send('DB_JOB',discovery_payload(profile,identity()),domain='STORAGE',revision=0,issued_us=issued,expires_us=issued+5_000_000)
                bootstrap=parse_bootstrap_result(receive('DB_JOB','BOOTSTRAP_RESULT'),peer,profile,discover,now_us=j_us())
                self.assertEqual(bootstrap['status'],'EMPTY')
                binding=storage_binding(peer,profile,bootstrap)
                link.send('BIND',binding_payload(binding),domain='STORAGE',revision=1);receive('BIND_ACK')
                publication=dict(service_instance=peer.service_instance,mapper_worker=mapper.worker_instance,mapper_instance=identity(),
                    raw_binding_sha256='a'*64,map_epoch=identity(),frame_epoch=identity(),odom_epoch=identity(),configuration='test-config',
                    calibration='test-calibration',primitive='b'*64,scene_admission_ref=None,source_binding_revision='1')
                pubsha=sha256(canonical(publication)).hexdigest()
                link.send('BIND',dict(binding_revision='1',binding_sha256=pubsha,binding=publication),domain='PUBLICATION',revision=1)
                self.assertEqual(receive('BIND_ACK').fields['binding_domain'],'PUBLICATION')
                reserve=dict(database_uuid=binding['database_uuid'],reservation_nonce=identity(),mapper_worker=mapper.worker_instance,block_count=8)
                link.send('DB_JOB',db_payload('RESERVE_PROVENANCE',reserve),domain='STORAGE',revision=1)
                allocation=receive('DB_JOB','RANGE_RESULT').payload['body'];self.assertEqual(allocation['start_inclusive'],'1')
                self.assertEqual(allocation['highwater_after'],'9')
                context=StorageContext(binding['database_uuid'],mapper.worker_instance,publication['mapper_instance'],publication['map_epoch'],
                    publication['frame_epoch'],publication['odom_epoch'],identity(),publication['configuration'],publication['calibration'],publication['primitive'],canonical(publication))
                issued=j_us();job=StorageJob(context,identity(),issued,issued+5_000_000)
                payload=CheckpointPayload.build(job,durable_cutoff_pi_us=100)
                request=CheckpointRequest(job,payload)
                link.send('DB_JOB',prepare_payload(request),domain='STORAGE',revision=1,request_id=job.job_id,issued_us=job.issued_us,expires_us=job.expires_us)
                lease=CopyLease(peer.service_instance,arena_id,1,mapper,storage,identity(),raw_binding_sha256='a'*64,publication_binding_sha256=pubsha)
                camera=CopyArena(spec,tokens,mapper,peer.service_instance)
                ready=camera.publish(lease,encode_checkpoint(request,lease),expires_us=job.expires_us)
                reader=replace(lease,read_token=identity());ready['copy_lease']=reader.fields
                link.send('DB_JOB',db_payload('READY',ready),domain='STORAGE',revision=1,request_id=job.job_id,expires_us=job.expires_us)
                self.assertEqual(receive('SLOT','RELEASE').payload,reader.slot_payload('RELEASE'))
                self.assertEqual(receive('DB_JOB','READY').payload['body'],ready)
                link.send('DB_JOB',db_payload('COMMIT',dict(database_uuid=context.database_uuid,job_id=job.job_id,job_digest=request.digest,copy_lease=reader.fields)),
                    domain='STORAGE',revision=1,request_id=job.job_id,expires_us=job.expires_us)
                committed=receive('DB_JOB','RESULT').payload['body'];self.assertEqual(committed['status'],'COMMITTED')
                link.send('DB_JOB',db_payload('QUERY',dict(database_uuid=context.database_uuid,job_id=job.job_id,job_digest=request.digest)),domain='STORAGE',revision=1)
                self.assertEqual(receive('DB_JOB','RESULT').payload['body'],committed)
                # 원 complete ref는 실제 재개방으로 읽고, 뒤 LOAD도 같은 D 실제 connection에서 실행한다.
                # writer lock이 살아 있으므로 새 writer를 열지 않고 원 payload/receipt로 exact ref를 구성한다.
                reference=dict(checkpoint_job_id=job.job_id,durable_revision=committed['durable_revision'],manifest_sha256=sha256(payload.manifest).hexdigest(),
                    manifest_length=len(payload.manifest),tile_count=0,map_epoch=context.map_epoch,frame_epoch=context.frame_epoch,odom_epoch=context.odom_epoch,
                    pi_host_boot=context.pi_host_boot,configuration=context.configuration,calibration=context.calibration,primitive_sha256=context.primitive_sha256,
                    origin_mapper_instance=context.origin_mapper_instance)
                issued=j_us();load=make_load_request(binding,mapper_worker=mapper.worker_instance,origin_mapper_instance=context.origin_mapper_instance,
                    job_id=identity(),issued_us=issued,expires_us=issued+5_000_000,checkpoint_ref=reference,selection=dict(kind='CHECKPOINT_PAGE',cursor=0),
                    purpose='CURRENT_MAP',publication=publication)
                self.assertEqual(load.job.context,context)
                link.send('DB_JOB',prepare_payload(load),domain='STORAGE',revision=1,request_id=load.job.job_id,issued_us=issued,expires_us=load.job.expires_us)
                writer=CopyLease(peer.service_instance,arena_id,2,storage,mapper,identity(),raw_binding_sha256='a'*64,publication_binding_sha256=pubsha)
                link.send('SLOT',writer.slot_payload('GRANT'),domain='STORAGE',revision=1,request_id=load.job.job_id,expires_us=load.job.expires_us)
                loadready=receive('DB_JOB','READY').payload['body'];loadlease=replace(writer,read_token=identity());loadready['copy_lease']=loadlease.fields
                with patch('xenova_vehicle.runtime.storage_copy.decode_copy',side_effect=AssertionError('semantic decode on copy owner')):
                    owned,returned=camera.copy_raw_owned(loadlease,loadready,expires_us=load.job.expires_us)
                self.assertTrue(tokens[0].acquire(False));tokens[0].release()
                self.assertFalse(tokens[1].acquire(False))
                loaded=owned.decode()
                self.assertTrue(loaded.current(load,context.sha256,j_us()))
                self.assertEqual(loaded.heads.heads,(('LANE',1,0),('SLOT',1,0)))
                self.assertEqual(loaded.original_job,job);self.assertLessEqual(returned.views_ended_us,returned.posted_us)
                link.send('SLOT',loadlease.slot_payload('RELEASE'),domain='STORAGE',revision=1,request_id=load.job.job_id,expires_us=load.job.expires_us)
                issued=j_us();link.send('STOP',dict(stop_revision='1',reason='TEST',drain_deadline_j_us=str(issued+4_000_000)),issued_us=issued,expires_us=issued+5_000_000)
                self.assertEqual(receive('STOPPED').payload['status'],'STOPPED')
                worker.join(2);self.assertFalse(worker.is_alive());self.assertEqual(outcome,['RETURNED'])
                reopened=_Database(StorageProfile(profile.database_path),j_us()+5_000_000)
                try:
                    self.assertEqual(reopened.metadata['database_uuid'],context.database_uuid)
                    self.assertEqual(reopened.metadata['allocator_highwater'],'9')
                    self.assertEqual(reopened.selected_checkpoint,reference)
                finally:reopened.close()
            finally:
                link.close();worker.join(2);child.close()
                if camera is not None:camera.close()
                memory.close();memory.unlink()

    def test_owned_copy_hash_tail_and_original_job_are_not_recreated(self):
        service=identity();producer=Endpoint(identity(),identity());consumer=Endpoint(identity(),identity())
        context=StorageContext(identity(),producer.worker_instance,identity(),identity(),identity(),identity(),identity(),'config','calib','a'*64,canonical(dict(test=True)))
        job=StorageJob(context,identity(),100,1000);request=CheckpointRequest(job,CheckpointPayload.build(job,durable_cutoff_pi_us=10))
        lease=CopyLease(service,identity(),1,producer,consumer,identity());encoded=encode_checkpoint(request,lease)
        cached=encoded.ready
        with patch('xenova_vehicle.runtime.storage_copy.sha256',side_effect=AssertionError('large hash on publication owner')):
            self.assertEqual(encoded.ready,cached)
            changed=encoded.ready;changed['job_id']=identity()
            self.assertEqual(encoded.ready,cached)
        reader=replace(lease,read_token=identity());ready=dict(encoded.ready,copy_lease=reader.fields)
        decoded=decode_copy(reader,ready,encoded.evidence,encoded.descriptors,encoded.manifest,encoded.tiles)
        self.assertEqual(decoded,request)
        for evidence,descriptors in ((encoded.evidence[:-1]+b'X',encoded.descriptors),(encoded.evidence,encoded.descriptors[:-1]+b'X')):
            with self.assertRaises(PersistenceError):decode_copy(reader,ready,evidence,descriptors,encoded.manifest,encoded.tiles)
        with self.assertRaises(PersistenceError):decode_copy(replace(reader,generation=2),ready,encoded.evidence,encoded.descriptors,encoded.manifest,encoded.tiles)
