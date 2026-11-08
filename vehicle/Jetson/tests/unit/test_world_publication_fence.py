"""원 World outbox의 publication/clock/Scene 철회와 실제 dispatch 경계를 검사한다.

여기서 World/외부 Pi grant는 명시 software transport fixture다. 실제 Processor의
생산 양성, Pi Authority/주행 수락 또는 MQTT broker 완료를 주장하지 않는다. clock은
공용 clock owner의 원 returned Probe13을 실제 callback에 넣고, local 통로는 seqpacket이다.
"""

from hashlib import sha256
import socket
import threading
import time
import unittest

import test_jetson_jio as fixtures
import test_source_runtime as source_fixtures
import test_scene_source_ports as scene_fixtures
from test_world_adapter import template
from xenova_drive_world import decode,encode,schema
from xenova_drive_world.codec import CELL_ROAD
from xenova_vehicle.runtime.data_channel import DataPacket,DataPeer,DataLink,data_pair
from xenova_vehicle.runtime.mapper_mapping import MappingContextPort
from xenova_vehicle.runtime.mapper_publication import MapperPublicationPort,PublicationReport
from xenova_vehicle.runtime.resident_workers import relay_publications
from xenova_vehicle.runtime.source_identity import scene_ref_fields
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.transport.jetson_jio import JetsonJio,JioInput,PREFIX,WORLD_TOPIC


def publication_sha(binding):return sha256(canonical(binding)).hexdigest()


def scene_world(grant, sequence=1):
    """기하를 수락한 producer 양성이 아닌, 공용 codec를 통과하는 작은 transport 입력이다."""
    value=decode(fixtures.world(sequence),schema.WorldSnapshot)
    leaf=template(11)
    value.minor=2;value.required_features=11
    value.lane_model.CopyFrom(leaf.lane_model)
    value.localization.CopyFrom(leaf.localization)
    value.localization.observed_mapper_us=value.observed_us
    value.drivable_permission=b'\x0f'
    value.cell_flags=bytes((CELL_ROAD,0,0,0))
    value.scene.CopyFrom(grant.scene)
    return encode(value)


class EndpointProxy:
    """실제 seqpacket 위의 제어된 send 실패다. 커널 역압/부분 송신 실측을 주장하지 않는다."""
    def __init__(self, endpoint, failure):self.endpoint,self.failure=endpoint,failure
    def __getattr__(self,name):return getattr(self.endpoint,name)
    def send(self,raw):
        if self.failure=='BLOCK':raise BlockingIOError()
        if self.failure=='PARTIAL':return len(raw)-1
        return self.endpoint.send(raw)


class WorldPublicationFenceTests(unittest.TestCase):
    def owner(self,*,client=None,owner_type=JetsonJio):
        clock=fixtures.Clock(1300200)
        owner=owner_type(fixtures.profile(),clock=clock,
            client_factory=None if client is None else lambda *args:client)
        fixtures.bind(owner)
        return clock,owner

    def offer(self,owner,raw=None,*,binding=None,expires=1400000):
        return owner.offer_world(fixtures.world() if raw is None else raw,expires_j_us=expires,
            publication_sha256=publication_sha(fixtures.publication() if binding is None else binding))

    def wait(self,predicate):
        end=time.monotonic()+1
        while not predicate() and time.monotonic()<end:time.sleep(.002)
        self.assertTrue(predicate(),'bounded software dispatch event did not occur')

    def test_all_worlds_need_actual_clock_and_same_source_numeric_renewal_keeps_original_expiry(self):
        clock,owner=self.owner()
        self.assertFalse(self.offer(owner))
        fixtures.qualify_clock(owner)
        self.assertTrue(self.offer(owner))
        original=owner._world;token=owner._token;lease=owner.current_lease
        renewed=decode(fixtures.returned_probe(),schema.MapperClockProbe)
        renewed.probe_seq+=1;renewed.pi_send_us+=10
        fixtures.deliver(owner,f'{PREFIX}/mapper/oak-1/clock/request.pb',renewed)
        self.assertIsNot(owner.current_lease,lease)
        self.assertIs(owner._world,original)
        self.assertIs(owner._token,token)
        self.assertTrue(owner._claim_world(original))
        self.assertEqual(original.expires_us,1400000)
        # 실제 새 Probe가 prior trust를 돌려주지 않으면 이미 queued인 live-only도 새 dispatch 불가다.
        missing=schema.MapperClockProbe();missing.CopyFrom(renewed)
        missing.probe_seq+=1;missing.ClearField('mapper_trust')
        fixtures.deliver(owner,f'{PREFIX}/mapper/oak-1/clock/request.pb',missing)
        self.assertIsNone(owner.current_lease)
        self.assertFalse(owner._claim_world(original))
        self.assertEqual(original.expires_us,1400000)

    def test_publication_only_fence_preserves_source_clock_and_sequence_hwm(self):
        clock,owner=self.owner();fixtures.qualify_clock(owner)
        self.assertTrue(self.offer(owner));original=owner._world
        token,lease,head=owner._token,owner.current_lease,owner._world_head
        changed=fixtures.publication(revision=2)
        self.assertTrue(owner.replace_publication(changed))
        self.assertIs(owner._token,token);self.assertIs(owner.current_lease,lease)
        self.assertIs(owner._world_head,head);self.assertIsNone(owner._world)
        self.assertFalse(owner._claim_world(original))
        self.assertFalse(self.offer(owner,binding=fixtures.publication()))
        self.assertFalse(self.offer(owner,binding=changed))  # 원 sequence1의 replay는 새 context에서도 새 결과가 아니다.
        self.assertTrue(self.offer(owner,fixtures.world(2),binding=changed))
        latest=owner._world
        self.assertFalse(owner.replace_publication(dict(changed)))
        self.assertIs(owner._world,latest)
        self.assertEqual(latest.expires_us,1400000)

    def test_known_negative_blocks_scene_world_before_supervisor_binding_update(self):
        clock,owner,grant=scene_fixtures.JioSurveyPortTests(methodName='runTest').owner()
        binding=fixtures.publication(scene=scene_ref_fields(grant.scene),revision=2)
        owner.replace_publication(binding)
        self.assertTrue(self.offer(owner,scene_world(grant),binding=binding))
        original=owner._world;publication=owner._publication;source_token=owner._token
        negative=schema.SceneGrant();negative.CopyFrom(grant)
        negative.lifecycle_state=3;negative.lifecycle_revision+=1
        fixtures.deliver(owner,f'{PREFIX}/mapper/oak-1/scene.pb',negative,qos=1)
        self.assertIs(owner._publication,publication)  # S의 scene-only clear가 아직 오지 않았다.
        self.assertIs(owner._token,source_token)
        self.assertFalse(owner._claim_world(original))
        self.assertFalse(self.offer(owner,scene_world(grant,2),binding=binding))
        self.assertTrue(self.offer(owner,fixtures.world(2),binding=binding))

    def test_fence_before_final_claim_prevents_actual_network_dispatch(self):
        entered=threading.Event();release=threading.Event()
        class Paused(JetsonJio):
            def _claim_world(self,item):
                entered.set();release.wait(1)
                return super()._claim_world(item)
        client=fixtures.FakeClient();clock,owner=self.owner(client=client,owner_type=Paused)
        fixtures.qualify_clock(owner);self.assertTrue(self.offer(owner));owner.start()
        self.addCleanup(lambda:(release.set(),owner.stop(),owner.join(1)))
        self.assertTrue(entered.wait(1))
        owner.replace_publication(fixtures.publication(revision=2))
        release.set()
        self.wait(lambda:owner.snapshot()['counts']['world_fenced']>=1)
        self.assertEqual([row for row in client.published if row[0]==WORLD_TOPIC],[])
        self.assertEqual(owner.snapshot()['counts']['world_dispatch_started'],0)

    def test_final_claim_reads_actual_time_after_waiting_for_the_lock(self):
        clock,owner=self.owner();fixtures.qualify_clock(owner)
        expires=clock.now+10;self.assertTrue(self.offer(owner,expires=expires))
        original=owner._world;waiting=threading.Event();result=[]
        class ObservedLock:
            def __init__(self):self.actual=threading.Lock()
            def __enter__(self):
                if threading.current_thread() is thread:waiting.set()
                self.actual.acquire();return self
            def __exit__(self,*_):self.actual.release()
        owner._lock=ObservedLock()
        thread=threading.Thread(target=lambda:result.append(owner._claim_world(original)))
        with owner._lock:
            thread.start()
            # 옛 구현은 여기서 lock 이전 시각을 읽었다. 새 구현은 lock 뒤 원 now를 읽는다.
            self.assertTrue(waiting.wait(1))
            clock.now=expires
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result,[False])
        self.assertEqual(owner.snapshot()['counts']['world_dispatch_started'],0)
        self.assertEqual(owner.snapshot()['counts']['expired'],1)

    def test_fence_after_dispatch_claim_does_not_claim_recall_or_hold_lock_across_io(self):
        entered=threading.Event();release=threading.Event()
        class PausedClient(fixtures.FakeClient):
            def publish(self,topic,raw,**kwargs):
                if topic==WORLD_TOPIC:entered.set();release.wait(1)
                return super().publish(topic,raw,**kwargs)
        client=PausedClient();clock,owner=self.owner(client=client)
        fixtures.qualify_clock(owner);raw=fixtures.world();self.assertTrue(self.offer(owner,raw));owner.start()
        self.addCleanup(lambda:(release.set(),owner.stop(),owner.join(1)))
        self.assertTrue(entered.wait(1))
        self.assertEqual(owner.snapshot()['counts']['world_dispatch_started'],1)
        # publish가 막혀 있어도 S-main의 짧은 publication 교환은 끝난다.
        self.assertTrue(owner.replace_publication(fixtures.publication(revision=2)))
        release.set()
        self.wait(lambda:any(row[0]==WORLD_TOPIC for row in client.published))
        self.assertEqual([row[1] for row in client.published if row[0]==WORLD_TOPIC],[raw])
        self.assertEqual(owner.snapshot()['counts']['world_dispatch_started'],1)


@unittest.skipUnless(hasattr(socket,'SOCK_SEQPACKET'),'actual Linux seqpacket required')
class MapperPendingOutputTests(unittest.TestCase):
    def source(self):
        fixture=source_fixtures.SourceRuntimeTests(methodName='runTest');fixture.setUp()
        self.addCleanup(fixture.tearDown);fixture.vehicle();fixture.cycle()
        return fixture

    def world(self,fixture,sequence=1):
        value=decode(fixtures.world(sequence),schema.WorldSnapshot)
        for key,data in fixture.mapper.source.items():
            if key!='device':setattr(value,key,data)
        value.observed_us=fixture.now-50;value.completed_us=value.published_us=fixture.now
        return encode(value)

    def test_actual_pending_world_expiry_discards_only_original_output_and_never_refunds_sequence(self):
        fixture=self.source();mapper=fixture.mapper;proxy=EndpointProxy(mapper.link.endpoint,'BLOCK')
        mapper.link.endpoint=proxy;raw=self.world(fixture)
        self.assertTrue(mapper.offer_world(raw,publication_sha256=publication_sha(mapper.publication),
            issued_us=fixture.now,expires_us=fixture.now+10))
        mapper.pump();original=mapper.link.pending
        self.assertEqual(original.kind,'WORLD_CANDIDATE')
        fixture.now=original.expires_us;mapper.pump()
        self.assertIsNone(mapper.link.pending)
        self.assertEqual(mapper.counts['output_expired'],1)
        self.assertEqual(fixture.supervisor.link.poll(),())
        proxy.failure=None
        self.assertTrue(mapper.offer_world(self.world(fixture,2),publication_sha256=publication_sha(mapper.publication),
            issued_us=fixture.now,expires_us=fixture.now+10))
        mapper.pump();actual,=fixture.supervisor.link.poll()
        self.assertEqual(int(actual.header['message_sequence']),int(original.header['message_sequence'])+1)
        self.assertNotEqual(actual.header['request_id'],original.header['request_id'])

    def test_expiry_between_actual_port_peek_and_source_offer_is_stale_discard(self):
        fixture=self.source();context=fixture.mapper.storage_context
        context_port=MappingContextPort();context_port.publish(context)
        port=MapperPublicationPort(context_port)
        report=PublicationReport(context,'WORLD_CANDIDATE',self.world(fixture),fixture.now,fixture.now+10,
            publication_sha(fixture.mapper.publication))
        self.assertTrue(port.offer(report));calls=[0]
        def peek_clock():
            calls[0]+=1;original=fixture.now
            if calls[0]==2:fixture.now=report.expires_us
            return original
        self.assertEqual(relay_publications(port,fixture.mapper,clock=peek_clock),0)
        self.assertEqual(fixture.mapper.counts['output_expired'],1)
        self.assertIsNone(fixture.mapper.link.pending)
        self.assertIsNone(port.peek('WORLD_CANDIDATE',fixture.now))
        self.assertEqual(report.expires_us,1010)

    def test_actual_old_publication_packet_is_counted_stale_without_service_fault(self):
        fixture=self.source();raw=self.world(fixture)
        packet=fixture.mapper.link.send('WORLD_CANDIDATE',{'publication_sha256':'f'*64},raw,
            issued_us=fixture.now,expires_us=fixture.now+10)
        self.assertIs(fixture.mapper.link.flush().packet,packet)
        fixture.supervisor.pump(fixture.material,bind_ready=True,on_publication=lambda *_:None)
        self.assertEqual(fixture.supervisor.counts['world_fenced'],1)
        self.assertEqual(fixture.jio.worlds,[])

    def test_actual_scene_publication_change_discards_selected_unsent_old_output(self):
        scene_fixture=scene_fixtures.SceneSourcePortTests(methodName='runTest')
        self.addCleanup(scene_fixture.doCleanups)
        fixture=scene_fixture.runtime();grant,lease=scene_fixture.original_scene(fixture)
        scene_fixture.offer(fixture,scene_fixtures.summary(grant,fixture.now))
        mapper=fixture.mapper;old_publication=mapper.publication
        proxy=EndpointProxy(mapper.link.endpoint,'BLOCK');mapper.link.endpoint=proxy
        self.assertTrue(mapper.offer_world(self.world(fixture),publication_sha256=publication_sha(old_publication),
            issued_us=fixture.now,expires_us=fixture.now+100))
        mapper.pump();original=mapper.link.pending
        self.assertIsNotNone(original)
        negative=schema.SceneGrant();negative.CopyFrom(grant)
        negative.lifecycle_state=3;negative.lifecycle_revision+=1
        actual=JioInput('SCENE',encode(negative),fixture.now,lease=lease)
        fixture.jio.current_scene=actual;fixture.jio.inputs.append(actual)
        fixture.cycle()
        self.assertIsNone(mapper.link.pending)
        self.assertEqual(mapper.counts['output_fenced'],1)
        self.assertIsNone(mapper.publication['scene_admission_ref'])
        self.assertIs(fixture.jio.current_lease,lease)
        self.assertEqual(original.header['metadata']['publication_sha256'],publication_sha(old_publication))
        self.assertEqual(original.expires_us,fixture.now+100)

    def test_discard_requires_original_unsent_output_and_refuses_partial_unknown_send(self):
        left,right=data_pair();peer=DataPeer(source_fixtures.uid(),source_fixtures.uid(),source_fixtures.uid())
        sender=DataLink(left,peer,'M',clock=lambda:1000)
        receiver=DataLink(right,peer,'S',clock=lambda:1000)
        self.addCleanup(sender.close);self.addCleanup(receiver.close)
        original=sender.send('WORLD_CANDIDATE',{},b'opaque-output',issued_us=1000,expires_us=2000)
        self.assertFalse(sender.discard_pending(DataPacket(original.raw)))
        self.assertIs(sender.pending,original)
        self.assertTrue(sender.discard_pending(original))
        identity=sender.send('SOURCE_IDENTITY',{},issued_us=1000,expires_us=2000)
        with self.assertRaises(ValueError):sender.discard_pending(identity)
        self.assertIs(sender.flush().packet,identity);receiver.poll()
        original=sender.send('WORLD_CANDIDATE',{},b'opaque-output',issued_us=1000,expires_us=2000)
        sender.endpoint=EndpointProxy(sender.endpoint,'PARTIAL')
        with self.assertRaises(OSError):sender.flush()
        with self.assertRaisesRegex(RuntimeError,'unknown'):sender.discard_pending(original)
        self.assertIs(sender.pending,original)


if __name__=='__main__':unittest.main()
