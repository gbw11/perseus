"""S 원장의 순수 protocol 반례. send/반환 fact는 명시 주입이며 실제 IPC는 test_storage_ipc가 맡는다."""

from dataclasses import replace
from hashlib import sha256
import unittest
from uuid import uuid4

from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.native_resources import ArenaSpec,DB_COPY_BYTES
from xenova_vehicle.runtime.resident_db import ResidentDbBroker
from xenova_vehicle.runtime.storage_copy import CopyReturned
from xenova_vehicle.runtime.storage_messages import db_payload,make_load_request,prepare_payload
from xenova_vehicle.storage.contracts import PersistenceConflict,canonical


def identity():return str(uuid4())


class Clock:
    now=1000
    def __call__(self):return self.now


class DbBrokerTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.service=identity()
        self.d=LifecyclePeer(self.service,identity(),identity(),'D','a'*64)
        self.m=LifecyclePeer(self.service,identity(),identity(),'M','a'*64)
        self.binding=dict(service_instance=self.service,storage_worker=self.d.worker_instance,storage_profile_sha256='b'*64,database_uuid=identity(),db_schema_version=1)
        arena=identity();self.broker=ResidentDbBroker(self.d,self.m,self.binding,ArenaSpec(arena,'/xnv_db_'+arena,DB_COPY_BYTES,'DB_COPY'),clock_us=self.clock)
        self.publication=dict(service_instance=self.service,mapper_worker=self.m.worker_instance,mapper_instance=identity(),raw_binding_sha256='c'*64,
            map_epoch=identity(),frame_epoch=identity(),odom_epoch=identity(),configuration='test-config',calibration='test-calib',primitive='d'*64,
            scene_admission_ref=None,source_binding_revision='1')
        self.context='e'*64

    def packet(self,peer,kind,payload,*,request_id,issued=1000,expires=4000,domain='STORAGE',revision=1,child=True):
        return encode_structure(dict(version=1,service_instance=self.service,channel_epoch=peer.channel_epoch,
            sender_instance=peer.worker_instance if child else self.service,message_sequence='1',kind=kind,binding_domain=domain,
            binding_revision=str(revision),request_id=request_id,issued_j_us=str(issued),expires_j_us=str(expires),payload=payload))

    def sent(self,drafts):
        for draft in drafts:
            self.assertTrue(self.broker.dispatchable(draft))
            peer=self.d if draft.role=='D' else self.m
            packet=self.packet(peer,draft.kind,draft.payload,request_id=draft.request_id,issued=draft.issued_us,expires=draft.expires_us,
                domain=draft.domain,revision=draft.revision,child=False)
            self.broker.note_sent(draft,packet,self.clock())

    def bind(self):
        draft,=self.broker.set_publication(self.publication,self.context);self.sent((draft,))
        ack=self.packet(self.d,'BIND_ACK',dict(binding_revision='1',binding_sha256=sha256(canonical(self.publication)).hexdigest(),accepted=True,state='IDLE',reason=None),
            request_id=draft.request_id,issued=draft.issued_us,expires=draft.expires_us,domain='PUBLICATION')
        self.broker.receive_storage(ack)

    def prepare(self):
        job=identity();body=dict(database_uuid=self.binding['database_uuid'],job_id=job,job_digest='f'*64,direction='CHECKPOINT',tile_count=0,
            context_sha256=self.context,origin_mapper_instance=self.publication['mapper_instance'],scene_commit_nonce=None,
            load_purpose=None,checkpoint_ref=None,load_selection=None)
        outgoing=self.broker.receive_mapper(self.packet(self.m,'DB_JOB',db_payload('PREPARE',body),request_id=job))
        self.sent(outgoing)
        return job,body

    def ready(self,body,lease):
        return dict(database_uuid=body['database_uuid'],job_id=body['job_id'],job_digest=body['job_digest'],copy_lease=lease.fields,
            manifest_length=1,manifest_sha256='a'*64,provenance_length=1,provenance_sha256='b'*64,provenance_count=0,context_count=0)

    def test_checkpoint_waits_for_exact_actual_release_and_commit_send(self):
        self.bind();job,body=self.prepare();writer=self.broker.pending.lease
        ready=self.ready(body,writer)
        read,=self.broker.receive_mapper(self.packet(self.m,'DB_JOB',db_payload('READY',ready),request_id=job))
        self.sent((read,));reader=self.broker.pending.lease
        dready=dict(ready,copy_lease=reader.fields)
        with self.assertRaises(PersistenceConflict):
            self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('READY',dready),request_id=job))
        self.broker.receive_storage(self.packet(self.d,'SLOT',reader.slot_payload('RELEASE'),request_id=job))
        commit,=self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('READY',dready),request_id=job))
        result=dict(database_uuid=body['database_uuid'],job_id=job,job_digest=body['job_digest'],status='COMMITTED',durable_revision='1',committed_j_us='1000',origin_context_sha256=self.context)
        with self.assertRaises(PersistenceConflict):
            self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('RESULT',result),request_id=job))
        self.sent((commit,));reply,=self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('RESULT',result),request_id=job))
        self.assertEqual(reply.expires_us,4000);self.assertEqual(reply.payload['body'],result)
        self.assertTrue(self.broker.last_completion.current);self.assertIsNone(self.broker.pending)
        self.assertEqual(self.broker.generation,1)
        self.broker.fence('SOURCE_DIED_BEFORE_REPLY_SELECTION')
        self.assertFalse(self.broker.dispatchable(reply));self.broker.abandon_unselected(reply)
        revoked=self.broker.take_aborted();self.assertEqual(revoked.historical_result,result);self.assertFalse(revoked.current)

    def test_source_fence_preserves_unclaimed_key_and_requires_actual_cleanup(self):
        self.bind();job,body=self.prepare();writer=self.broker.pending.lease
        self.broker.fence('SOURCE_DIED')
        result=self.broker.receive_mapper(self.packet(self.m,'DB_JOB',db_payload('READY',self.ready(body,writer)),request_id=job))
        self.assertEqual(result,());self.assertEqual(self.broker.cleanup_ready,writer)
        with self.assertRaises(PersistenceConflict):self.broker.note_discarded(CopyReturned(replace(writer,generation=2),1000,1000))
        self.broker.note_discarded(CopyReturned(writer,1000,1000));self.assertIsNone(self.broker.pending)
        self.assertEqual(self.broker.generation,1)
        # 다시 시작하려면 새 source binding이 필요하다. EMPTY에서 head/범위를 만드는 경로는 없다.
        with self.assertRaises(PersistenceConflict):self.prepare()

    def test_provisional_load_needs_no_publication_and_expired_release_is_cleanup_only(self):
        reference=dict(checkpoint_job_id=identity(),durable_revision='7',manifest_sha256='a'*64,manifest_length=200,tile_count=0,
            map_epoch=identity(),frame_epoch=identity(),odom_epoch=identity(),pi_host_boot=identity(),configuration='config',calibration='calib',
            primitive_sha256='b'*64,origin_mapper_instance=identity())
        request=make_load_request(self.binding,mapper_worker=self.m.worker_instance,origin_mapper_instance=identity(),job_id=identity(),issued_us=1000,expires_us=4000,
            checkpoint_ref=reference,selection=dict(kind='CHECKPOINT_PAGE',cursor=0))
        outgoing=self.broker.request_bootstrap_load(request);self.sent(outgoing)
        writer=self.broker.pending.lease;body=prepare_payload(request)['body']
        read,=self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('READY',self.ready(body,writer)),request_id=request.job.job_id))
        self.sent((read,));reader=self.broker.pending.lease
        self.assertIsNone(reader.publication_binding_sha256)
        self.clock.now=5000
        cleanup,=self.broker.receive_mapper(self.packet(self.m,'SLOT',reader.slot_payload('RELEASE'),request_id=request.job.job_id,issued=5000))
        self.assertTrue(self.broker.dispatchable(cleanup));self.assertEqual(cleanup.expires_us,4000)
        self.assertEqual(cleanup.payload['op'],'RELEASE');self.assertFalse(self.broker.last_completion.current)
        self.assertIsNone(self.broker.pending)

    def test_unknown_writer_is_not_normalized_by_d_rebind(self):
        self.bind();self.prepare();self.broker.fence('D_DIED')
        replacement=LifecyclePeer(self.service,identity(),identity(),'D','a'*64)
        with self.assertRaises(PersistenceConflict):self.broker.rebind_storage(replacement,dict(self.binding,storage_worker=replacement.worker_instance))
        self.assertTrue(self.broker.requires_quiescence);self.assertEqual(self.broker.generation,1)
        self.assertIsNone(self.broker.take_aborted())

    def test_safe_rebind_emits_original_unknown_notice_without_renewing_job_or_mapper_context(self):
        self.bind();job,body=self.prepare();writer=self.broker.pending.lease
        read,=self.broker.receive_mapper(self.packet(self.m,'DB_JOB',db_payload('READY',self.ready(body,writer)),request_id=job));self.sent((read,))
        reader=self.broker.pending.lease
        self.broker.receive_storage(self.packet(self.d,'SLOT',reader.slot_payload('RELEASE'),request_id=job))
        self.broker.fence('D_DIED');self.clock.now=5000
        replacement=LifecyclePeer(self.service,identity(),identity(),'D','a'*64)
        draft,=self.broker.rebind_storage(replacement,dict(self.binding,storage_worker=replacement.worker_instance))
        self.assertEqual(draft.purpose,'PUBLICATION_BIND');self.assertEqual(draft.payload['binding'],self.publication)
        self.assertEqual(self.broker.mapper.worker_instance,self.m.worker_instance)
        self.assertEqual(self.broker.context_sha256,self.context);self.assertEqual(self.broker.generation,1)
        notice=self.broker.take_aborted()
        self.assertEqual(notice.operation,'CHECKPOINT');self.assertEqual(notice.body,body);self.assertEqual(notice.request_id,job)
        self.assertEqual((notice.issued_us,notice.expires_us,notice.observed_us),(1000,4000,5000))
        self.assertFalse(notice.current);self.assertIsNone(notice.historical_result);self.assertIsNone(self.broker.take_aborted())
        self.assertEqual(type(notice).from_fields(notice.fields),notice)
        self.assertIsNone(self.broker.pending)

    def test_fenced_original_commit_receipt_remains_history_and_terminates_only_its_original_wait(self):
        self.bind();job,body=self.prepare();writer=self.broker.pending.lease
        ready=self.ready(body,writer)
        read,=self.broker.receive_mapper(self.packet(self.m,'DB_JOB',db_payload('READY',ready),request_id=job));self.sent((read,))
        reader=self.broker.pending.lease;ready['copy_lease']=reader.fields
        self.broker.receive_storage(self.packet(self.d,'SLOT',reader.slot_payload('RELEASE'),request_id=job))
        commit,=self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('READY',ready),request_id=job));self.sent((commit,))
        self.broker.fence('D_RECOVERY')
        receipt=dict(database_uuid=body['database_uuid'],job_id=job,job_digest=body['job_digest'],status='COMMITTED',durable_revision='9',committed_j_us='1000',origin_context_sha256=self.context)
        self.assertEqual(self.broker.receive_storage(self.packet(self.d,'DB_JOB',db_payload('RESULT',receipt),request_id=job)),())
        self.assertFalse(self.broker.last_completion.current)
        notice=self.broker.take_aborted();self.assertEqual(notice.historical_result,receipt)
        self.assertEqual(notice.expires_us,4000);self.assertEqual(notice.body['job_digest'],body['job_digest']);self.assertFalse(notice.current)
