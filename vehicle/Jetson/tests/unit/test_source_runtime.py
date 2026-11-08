"""실제 Linux S/M binary 왕복으로 identity와 원 source 전달의 수명을 확인한다."""

from base64 import b64decode
from collections import deque
import json
import socket
from types import SimpleNamespace
import unittest

from test_source_identity import fixture,uid
from xenova_drive_world import encode
from xenova_vehicle.runtime.data_channel import DataLink,data_pair
from xenova_vehicle.runtime.source_runtime import SupervisorSource,MapperSource,SourceChanged
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.transport.jetson_jio import JioInput


class JioPort:
    def __init__(self,policy,device):
        self.profile=SimpleNamespace(source_policy=policy,device=device)
        self.inputs=deque();self.bound=None;self.fenced=False;self.worlds=[]
        self.current_scene=self.current_lease=None;self.summaries=[]

    def take_inputs(self):
        result=tuple(self.inputs);self.inputs.clear();return result

    def replace_source(self,source,**origin):self.bound=(source,origin)
    def fence_source(self):self.fenced=True
    def offer_world(self,raw,*,expires_j_us):self.worlds.append((raw,expires_j_us))
    def offer_survey_summary(self,raw,*,grant_sha256,expires_j_us):
        self.summaries.append((raw,grant_sha256,expires_j_us));return True


@unittest.skipUnless(hasattr(socket,'SOCK_SEQPACKET'),'actual Linux binary seqpacket required')
class SourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.profile,self.raw,self.policy,self.sample,self.bootstrap,self.peer,self.incarnation=fixture()
        self.now=1000;self.jio=JioPort(self.policy,'oak-front')
        left,right=data_pair()
        self.mapper=MapperSource(DataLink(left,self.peer,'M',clock=lambda:self.now),self.raw,self.policy,
            mapper_instance=self.incarnation,device=self.jio.profile.device,clock=lambda:self.now)
        self.supervisor=SupervisorSource(DataLink(right,self.peer,'S',clock=lambda:self.now),self.raw,self.jio,
            mapper_instance=self.incarnation,mapper_boot=uid(),clock_epoch=self.raw.fields['clock_epoch'],clock=lambda:self.now)
        self.material=({'service_instance':self.peer.service_instance,'storage_worker':uid(),
            'storage_profile_sha256':'c'*64,'database_uuid':self.bootstrap['database_uuid'],'db_schema_version':1},self.bootstrap)
        self.publications=[]

    def tearDown(self):self.mapper.close();self.supervisor.close()

    def cycle(self,*,ready=True,count=8):
        for _ in range(count):
            self.supervisor.pump(self.material,bind_ready=ready,on_publication=lambda binding,context:self.publications.append((binding,context)))
            self.mapper.pump()

    def vehicle(self):
        wire=encode(self.sample);self.jio.inputs.append(JioInput('VEHICLE',wire,self.now));return wire

    def test_empty_original_vehicle_proposal_ack_and_snapshot_keep_ids_bytes_and_callback_time(self):
        original=self.vehicle();self.cycle()
        self.assertEqual(len(self.publications),1)
        binding,context=self.publications[0]
        self.assertEqual(self.mapper.publication,binding)
        self.assertEqual(binding['mapper_instance'],self.incarnation)
        self.assertEqual((binding['map_epoch'],binding['frame_epoch']),(self.mapper.map_epoch,self.mapper.frame_epoch))
        self.assertEqual(context.source_binding,canonical(binding))
        self.assertEqual(context.calibration,self.policy.calibration)
        self.assertNotEqual(context.calibration,self.profile.sha256)
        snapshot=self.mapper.context_bytes();value=json.loads(snapshot)
        self.assertEqual(b64decode(value['vehicles'][0]['raw']),original)
        self.assertEqual(value['vehicles'][0]['received_j_us'],'1000')
        self.assertIsNone(value['active_probe_sha256'])
        self.assertEqual(self.jio.bound[0],self.mapper.source)
        self.assertLessEqual(len(snapshot),65536)
        self.now=1100;self.cycle()
        self.assertEqual(len(self.publications),1)
        self.assertEqual(self.mapper.context_bytes(),snapshot)

    def test_no_original_vehicle_does_not_generate_identity_or_fake_odom(self):
        self.cycle()
        self.assertIsNone(self.mapper.publication)
        self.assertIsNone(self.mapper._proposal_packet)
        self.assertEqual(self.publications,[])

    def test_actual_supervisor_publication_callback_passes_original_context_and_origin_to_d_broker(self):
        from xenova_vehicle.runtime.resident_runtime import _Runtime
        from xenova_vehicle.runtime.resident_db import ResidentDbBroker
        from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
        from xenova_vehicle.runtime.native_resources import ArenaSpec,DB_COPY_BYTES
        runtime=_Runtime.__new__(_Runtime)
        mapper=LifecyclePeer(self.peer.service_instance,uid(),self.peer.mapper_worker,'M','a'*64)
        storage=LifecyclePeer(self.peer.service_instance,uid(),self.material[0]['storage_worker'],'D','a'*64)
        arena=uid()
        runtime.db_broker=ResidentDbBroker(storage,mapper,self.material[0],
            ArenaSpec(arena,'/xnv_db_'+arena,DB_COPY_BYTES,'DB_COPY'),clock_us=lambda:self.now)
        runtime._ensure_db_broker=lambda:None
        runtime.binding=self.raw;runtime.workers={'M':SimpleNamespace(peer=mapper)}
        runtime.db_outbox=deque();runtime.publication_context=None
        self.vehicle()
        for _ in range(8):
            self.supervisor.pump(self.material,bind_ready=True,on_publication=runtime.set_publication)
            self.mapper.pump()
        self.assertEqual(runtime.publication_context,self.mapper.storage_context)
        draft,=runtime.db_outbox
        self.assertEqual(draft.kind,'BIND')
        self.assertEqual(draft.domain,'PUBLICATION')
        self.assertEqual(draft.payload['binding'],self.mapper.publication)
        self.assertEqual(draft.payload['geometry_origin'],self.mapper.storage_context.geometry_origin_fields)
        self.assertEqual(runtime.db_broker.context_sha256,self.mapper.context_sha256)

    def test_expired_storage_terminal_keeps_original_job_and_uses_only_local_delivery_lifetime(self):
        from xenova_vehicle.runtime.raw_leases import Endpoint
        from xenova_vehicle.runtime.resident_db import DbAbort
        from xenova_vehicle.runtime.storage_messages import prepare_payload
        from xenova_vehicle.storage.contracts import StorageJob
        from xenova_vehicle.storage.port import CheckpointRequest
        from xenova_vehicle.storage.records import CheckpointPayload
        self.vehicle();self.cycle()
        context=self.mapper.storage_context
        job=StorageJob(context,uid(),100,200)
        request=CheckpointRequest(job,CheckpointPayload.build(job,durable_cutoff_pi_us=1))
        notice=DbAbort('CHECKPOINT',canonical(prepare_payload(request)['body']),job.job_id,100,200,900,
            Endpoint(uid(),self.peer.mapper_worker),context.sha256,'D_OWNER_REBOUND_UNKNOWN')
        self.supervisor.offer_abort(notice);self.cycle()
        result,=self.mapper.take_aborts()
        self.assertEqual(result,notice)
        self.assertEqual(result.expires_us,200)
        self.assertLess(result.expires_us,self.now)
        self.assertEqual(self.mapper.take_aborts(),())

    def test_early_proposal_waits_actual_raw_ack_without_changing_original_job(self):
        self.vehicle();self.cycle(ready=False)
        original=self.mapper._proposal_packet
        self.assertIsNotNone(original);self.assertIsNone(self.mapper.publication)
        self.assertEqual(self.supervisor._early_identity.raw,original.raw)
        self.cycle(ready=True)
        self.assertEqual(self.mapper._proposal_packet.raw,original.raw)
        self.assertEqual(len(self.publications),1)

    def test_original_pending_identity_expiry_is_not_renewed(self):
        self.vehicle();self.cycle(ready=False)
        original=self.mapper._proposal_packet
        self.now=original.expires_us
        with self.assertRaises(TimeoutError):self.mapper.pump()
        self.assertEqual(self.mapper._proposal_packet.raw,original.raw)

    def test_new_gateway_or_odom_source_immediately_fences_jio_before_m_recovery(self):
        self.vehicle();self.cycle()
        self.sample.gateway_instance=uid();self.sample.sequence+=1;self.vehicle()
        with self.assertRaises(SourceChanged):self.cycle(count=1)
        self.assertTrue(self.jio.fenced)
        self.assertEqual(len(self.publications),1)

    def test_current_db_material_is_kept_separate_and_no_complete_restore_success_is_invented(self):
        self.vehicle();self.cycle()
        old=self.mapper.publication
        self.bootstrap=dict(self.bootstrap,status='COMPLETE',selected_checkpoint=dict(checkpoint_job_id=uid(),
            durable_revision='1',manifest_sha256='d'*64,manifest_length=10,tile_count=0,
            map_epoch=old['map_epoch'],frame_epoch=old['frame_epoch'],odom_epoch=old['odom_epoch'],
            pi_host_boot=self.sample.host_boot,configuration=self.policy.configuration,calibration=self.policy.calibration,
            primitive_sha256=self.policy.primitive,origin_mapper_instance=self.incarnation))
        self.material=(self.material[0],self.bootstrap)
        self.cycle()
        self.assertEqual(self.mapper.publication,old)
        self.assertEqual(self.mapper.material[1]['status'],'COMPLETE')
        self.assertEqual(len(self.publications),1)


if __name__=='__main__':unittest.main()
