"""원 survey bytes와 scene-only publication의 실제 local socket 연결 회귀다.

SourceRuntime의 S/M seqpacket과 공용 protobuf는 실제 사용한다. ClockLease/승인 grant는
명시 software fixture이며 실차 시계·실제 operator/STOP·자료 내구 수락 증거가 아니다.
JIO callback/reply는 네트워크 thread/broker를 열지 않고 실제 typed consumer를 호출한다.
"""

from dataclasses import replace
from hashlib import sha256
import socket
import unittest

import test_jetson_jio as jio_fixture
import test_source_runtime as source_fixture
from test_world_adapter import template
from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import ClockLease
from xenova_vehicle.runtime.source_identity import scene_ref_fields
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.transport.jetson_jio import JioInput, JetsonJio, PREFIX


def grant(source, origin, lease, now):
    value = schema.SceneGrant(major=1, vehicle=source['vehicle'], pi_host_boot=origin['pi_host_boot'],
        gateway_instance=origin['gateway_instance'], source_admission_revision=lease.source_revision,
        mapper_device=source['device'], issued_pi_us=max(1, now - 100), valid_until_pi_us=lease.expires_pi_us,
        lifecycle_state=2, request_nonce=bytes([3]*16), survey_deadline_pi_us=lease.expires_pi_us,
        max_observation_stations=24, purpose=0, frozen_evidence_sha256=bytes([4]*32),
        frozen_survey_revision=1, frozen_hazard_revision=1, lifecycle_revision=1,
        **{key:source[key] for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')})
    value.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=bytes([5]*16), admission_revision=1,
        survey_id=bytes([6]*16), survey_revision=1, policy=1, coverage_profile_sha256=bytes([7]*32),
        allow_restore_after_mapper_restart=True))
    return value


def summary(grant_value, now):
    return schema.SurveySummary(major=1, request_nonce=grant_value.request_nonce,
        survey_id=grant_value.scene.survey_id, survey_revision=1, status=2, world_sequence=1,
        evidence_sequence=1, started_peer_us=now-100, last_observed_peer_us=now-50,
        completed_peer_us=now, positive_cell_count=1, observed_slot_count=0,
        required_sensor_mask=1, valid_sensor_mask=1, evidence_sha256=grant_value.frozen_evidence_sha256,
        coverage_profile_sha256=grant_value.scene.coverage_profile_sha256,
        observation_station_count=1, hazard_revision=1, alignment_revision=1,
        anchor_position_bound_m=.01, anchor_heading_bound_rad=.01, evidence_flags=31,
        failure_flags=0, timestamp_error_us=0)


@unittest.skipUnless(hasattr(socket, 'SOCK_SEQPACKET'), '실제 Linux local binary socket을 사용함')
class SceneSourcePortTests(unittest.TestCase):
    def runtime(self):
        value=source_fixture.SourceRuntimeTests(methodName='runTest')
        value.setUp();self.addCleanup(value.tearDown)
        value.vehicle();value.cycle()
        return value

    def original_scene(self, fixture):
        source=fixture.supervisor.source;origin=fixture.supervisor.origin;now=fixture.now
        trust=schema.WorldTrust(**{key:value for key,value in source.items() if key!='device'},
            lower_offset_us=0,upper_offset_us=0,expires_pi_us=now+1_000_000,max_error_us=100)
        probe=schema.MapperClockProbe(major=1,vehicle=source['vehicle'],device=source['device'],
            pi_host_boot=origin['pi_host_boot'],pi_client_instance=source_fixture.uid(),probe_seq=1,
            nonce=bytes([8]*16),pi_send_us=now,admission_revision=1,mapper_trust=trust)
        raw_probe=encode(probe)
        lease=ClockLease(encode(trust),1,None,None,now,now,0,now+1_000_000,100,raw_probe,now,1_000_000)
        value=grant(source,origin,lease,now);raw=encode(value)
        item=JioInput('SCENE',raw,now,lease=lease)
        fixture.jio.current_scene=item;fixture.jio.current_lease=lease
        fixture.jio.inputs.extend((JioInput('PROBE',raw_probe,now,lease=lease),item))
        fixture.cycle()
        return value,lease

    def offer(self,fixture,value,*,expires=None):
        raw=encode(value)
        accepted=fixture.mapper.offer_survey_summary(raw,
            publication_sha256=sha256(canonical(fixture.mapper.publication)).hexdigest(),
            grant_sha256=sha256(fixture.jio.current_scene.raw).hexdigest(),
            issued_us=value.completed_peer_us,expires_us=fixture.now+100 if expires is None else expires)
        fixture.cycle()
        return raw,accepted

    def test_actual_summary_publication_roundtrip_duplicate_and_revoke_preserve_geometry_and_original_bytes(self):
        fixture=self.runtime();value,lease=self.original_scene(fixture)
        original=dict(fixture.mapper.publication);context=fixture.mapper.storage_context
        world=template(0)
        for key,data in fixture.mapper.source.items():
            if key!='device':setattr(world,key,data)
        world.width=world.height=2;world.resolution_m=.1;world.occupancy=b'\xff'*4
        world.cell_flags=b'\0'*4;world.age_le32=b'\xff'*16
        world.observed_us=fixture.now-50;world.completed_us=world.published_us=fixture.now
        world_raw=encode(world);pub_sha=sha256(canonical(original)).hexdigest()
        self.assertTrue(fixture.mapper.offer_world(world_raw,publication_sha256=pub_sha,
            issued_us=fixture.now,expires_us=fixture.now+100))
        with self.assertRaises(ValueError):fixture.mapper.offer_world(world_raw,publication_sha256=pub_sha,
            issued_us=fixture.now,expires_us=fixture.now+101)
        raw,accepted=self.offer(fixture,summary(value,fixture.now))
        self.assertTrue(accepted)
        current=fixture.mapper.publication
        self.assertEqual(current['scene_admission_ref'],scene_ref_fields(value.scene))
        self.assertEqual(int(current['source_binding_revision']),int(original['source_binding_revision'])+1)
        self.assertEqual({k:v for k,v in current.items() if k not in ('scene_admission_ref','source_binding_revision')},
            {k:v for k,v in original.items() if k not in ('scene_admission_ref','source_binding_revision')})
        self.assertEqual(fixture.mapper.storage_context.geometry_origin_fields,context.geometry_origin_fields)
        self.assertNotEqual(fixture.mapper.context_sha256,context.sha256)
        self.assertIs(fixture.jio.current_lease,lease)
        self.assertEqual(fixture.jio.summaries[-1],(raw,sha256(encode(value)).hexdigest(),fixture.now+100))
        self.assertEqual(len(fixture.publications),2)
        self.assertEqual(fixture.jio.worlds,[(world_raw,fixture.now+100)])
        self.offer(fixture,decode(raw,schema.SurveySummary))
        self.assertEqual(fixture.mapper.publication,current)
        self.assertEqual(len(fixture.publications),2)
        revoked=schema.SceneGrant();revoked.CopyFrom(value);revoked.lifecycle_state=3;revoked.lifecycle_revision+=1
        item=JioInput('SCENE',encode(revoked),fixture.now,lease=lease)
        fixture.jio.current_scene=item;fixture.jio.inputs.append(item);fixture.cycle()
        self.assertIsNone(fixture.mapper.publication['scene_admission_ref'])
        self.assertEqual(int(fixture.mapper.publication['source_binding_revision']),int(current['source_binding_revision'])+1)
        self.assertEqual(len(fixture.publications),3)
        fixture.cycle();self.assertEqual(len(fixture.publications),3)

    def test_foreign_or_expired_report_is_bounded_rejection_not_source_fault_or_positive_authority(self):
        fixture=self.runtime();value,_=self.original_scene(fixture)
        original=fixture.mapper.publication
        report=summary(value,fixture.now);report.request_nonce=bytes([9]*16)
        self.offer(fixture,report)
        self.assertEqual(fixture.mapper.publication,original)
        self.assertEqual(fixture.supervisor.counts['survey_rejected'],1)
        raw=encode(summary(value,fixture.now))
        fixture.mapper.link.send('SURVEY_SUMMARY',dict(publication_sha256='f'*64,
            grant_sha256=sha256(encode(value)).hexdigest()),raw,issued_us=fixture.now,expires_us=fixture.now+10)
        fixture.mapper.link.flush();fixture.cycle()
        self.assertEqual(fixture.supervisor.counts['survey_rejected'],2)
        fixture.mapper.link.send('SURVEY_SUMMARY',dict(publication_sha256=sha256(canonical(original)).hexdigest(),
            grant_sha256=sha256(encode(value)).hexdigest(),extra=True),raw,
            issued_us=fixture.now,expires_us=fixture.now+10)
        fixture.mapper.link.flush();fixture.cycle()
        self.assertEqual(fixture.supervisor.counts['survey_rejected'],3)
        # 공용 decoder가 버리는 unknown field를 원 summary와 같은 것으로 relay하지 않는다.
        fixture.mapper.link.send('SURVEY_SUMMARY',dict(publication_sha256=sha256(canonical(original)).hexdigest(),
            grant_sha256=sha256(encode(value)).hexdigest()),raw+b'\xf8\x07\x01',
            issued_us=fixture.now,expires_us=fixture.now+10)
        fixture.mapper.link.flush();fixture.cycle()
        self.assertEqual(fixture.supervisor.counts['survey_rejected'],4)
        fixture.mapper.link.send('SURVEY_SUMMARY',dict(publication_sha256=sha256(canonical(original)).hexdigest(),
            grant_sha256=sha256(encode(value)).hexdigest()),raw,issued_us=fixture.now,expires_us=fixture.now+1)
        fixture.mapper.link.flush();fixture.now+=1;fixture.cycle()
        self.assertIsNone(fixture.mapper.publication['scene_admission_ref'])
        self.assertEqual(len(fixture.publications),1)
        self.assertFalse(fixture.jio.fenced)

    def test_positive_publication_requires_actual_grant_and_exact_next_revision_and_null_keeps_source(self):
        fixture=self.runtime()
        # 실제 S peer라도 M이 받은 grant 없이 positive ref를 채우지 못한다.
        source=fixture.supervisor.source;origin=fixture.supervisor.origin
        trust=schema.WorldTrust(**{key:v for key,v in source.items() if key!='device'},lower_offset_us=0,
            upper_offset_us=0,expires_pi_us=1_001_000,max_error_us=100)
        probe=schema.MapperClockProbe(major=1,vehicle=source['vehicle'],device=source['device'],
            pi_host_boot=origin['pi_host_boot'],pi_client_instance=source_fixture.uid(),probe_seq=1,
            nonce=bytes([8]*16),pi_send_us=1000,admission_revision=1,mapper_trust=trust)
        lease=ClockLease(encode(trust),1,None,None,1000,1000,0,1_001_000,100,encode(probe),1000,1_000_000)
        value=grant(source,origin,lease,1000)
        original=fixture.mapper.publication
        binding=dict(original,scene_admission_ref=scene_ref_fields(value.scene),
            source_binding_revision=str(int(original['source_binding_revision'])+1))
        def send(binding_value):
            context=replace(fixture.mapper.storage_context,source_binding=canonical(binding_value))
            fixture.supervisor.link.send('SOURCE_PUBLICATION',dict(source=source,origin=origin,
                geometry_origin=context.geometry_origin_fields,context_sha256=context.sha256),canonical(binding_value),
                issued_us=fixture.now,expires_us=fixture.now+100)
            fixture.supervisor.link.flush();fixture.mapper.pump()
        send(binding)
        self.assertEqual(fixture.mapper.publication,original)
        self.assertEqual(fixture.mapper.counts['publication_rejected'],1)
        null=dict(original,source_binding_revision=str(int(original['source_binding_revision'])+2))
        send(null)
        self.assertEqual(fixture.mapper.publication,original)
        self.assertEqual(fixture.mapper.counts['publication_rejected'],2)
        null['source_binding_revision']=str(int(original['source_binding_revision'])+1);send(null)
        self.assertEqual(fixture.mapper.publication,null)
        self.assertEqual(fixture.mapper.source,source)

    def test_new_local_caps_are_structural_only_and_keep_original_header_and_direction_limits(self):
        fixture=self.runtime()
        metadata=dict(publication_sha256='a'*64,grant_sha256='b'*64)
        packet=fixture.mapper.link.send('SURVEY_SUMMARY',metadata,b'x'*512,
            issued_us=fixture.now,expires_us=fixture.now+100)
        fixture.mapper.link.flush()
        self.assertEqual(fixture.supervisor.link.poll()[0].raw,packet.raw)
        with self.assertRaises(ValueError):fixture.mapper.link.send('SURVEY_SUMMARY',metadata,b'x'*513,
            issued_us=fixture.now,expires_us=fixture.now+100)
        with self.assertRaises(ValueError):fixture.supervisor.link.send('SURVEY_SUMMARY',metadata,b'x')
        publication=dict(source={},origin={},geometry_origin={},context_sha256='a'*64)
        with self.assertRaises(ValueError):fixture.supervisor.link.send('SOURCE_PUBLICATION',publication,b'x'*2049)


class JioSurveyPortTests(unittest.TestCase):
    def owner(self):
        clock=jio_fixture.Clock(1300200);owner=JetsonJio(jio_fixture.profile(),clock=clock)
        jio_fixture.bind(owner)
        jio_fixture.deliver(owner,f'{PREFIX}/mapper/oak-1/clock/request.pb',jio_fixture.returned_probe())
        owner.take_inputs()
        source=jio_fixture.source();origin=dict(pi_host_boot=jio_fixture.uid(1),gateway_instance=jio_fixture.uid(4))
        value=grant(source,origin,owner.current_lease,clock.now)
        trust=decode(owner.current_lease.trust_bytes,schema.WorldTrust)
        value.issued_pi_us=clock.now+trust.lower_offset_us-100
        jio_fixture.deliver(owner,f'{PREFIX}/mapper/oak-1/scene.pb',value,qos=1)
        item=owner.take_inputs()[0]
        self.assertIs(owner.current_scene,item)
        return clock,owner,value

    def test_current_reply_copies_original_summary_across_probe_renewal_and_expiry_leaves_plain_clock(self):
        clock,owner,value=self.owner();raw=encode(summary(value,clock.now));token=owner._token
        self.assertTrue(owner.offer_survey_summary(raw,grant_sha256=sha256(encode(value)).hexdigest(),expires_j_us=clock.now+1000))
        self.assertEqual(encode(decode(owner._reply(owner._probe),schema.MapperClockReply).survey_summary),raw)
        probe=decode(jio_fixture.returned_probe(),schema.MapperClockProbe);probe.probe_seq+=1;probe.pi_send_us+=100
        clock.now+=100
        jio_fixture.deliver(owner,f'{PREFIX}/mapper/oak-1/clock/request.pb',probe)
        owner.take_inputs();self.assertIs(owner._token,token)
        reply=decode(owner._reply(owner._probe),schema.MapperClockReply)
        self.assertEqual(encode(reply.survey_summary),raw)
        self.assertEqual(reply.survey_summary.completed_peer_us,clock.now-100)
        clock.now+=900
        self.assertFalse(decode(owner._reply(owner._probe),schema.MapperClockReply).HasField('survey_summary'))

    def test_bad_profile_or_source_fence_does_not_retime_report_or_break_independent_clock(self):
        clock,owner,value=self.owner();token=owner._token
        wrong=summary(value,clock.now);wrong.coverage_profile_sha256=bytes([2]*32)
        self.assertFalse(owner.offer_survey_summary(encode(wrong),grant_sha256=sha256(encode(value)).hexdigest(),expires_j_us=clock.now+1000))
        self.assertFalse(decode(owner._reply(owner._probe),schema.MapperClockReply).HasField('survey_summary'))
        raw=encode(summary(value,clock.now))
        self.assertTrue(owner.offer_survey_summary(raw,grant_sha256=sha256(encode(value)).hexdigest(),expires_j_us=clock.now+1000))
        self.assertFalse(jio_fixture.bind(owner));self.assertIs(owner._token,token)
        with_changed=dict(jio_fixture.source(),map_epoch=jio_fixture.uid(99));jio_fixture.bind(owner,with_changed)
        self.assertIsNone(owner.current_scene);self.assertIsNone(owner.current_lease);self.assertIsNone(owner._summary)
        self.assertIsNot(owner._token,token)

    def test_same_admission_revoke_cannot_roll_back_after_take_inputs_and_equal_revision_conflicts(self):
        _,owner,value=self.owner();token=owner._token
        revoked=schema.SceneGrant();revoked.CopyFrom(value)
        revoked.lifecycle_state=3;revoked.lifecycle_revision+=1
        topic=f'{PREFIX}/mapper/oak-1/scene.pb'
        jio_fixture.deliver(owner,topic,revoked,qos=1)
        current=owner.take_inputs()[0]
        self.assertEqual(current.raw,encode(revoked))
        jio_fixture.deliver(owner,topic,value,qos=1)
        conflict=schema.SceneGrant();conflict.CopyFrom(revoked);conflict.request_nonce=bytes([9]*16)
        jio_fixture.deliver(owner,topic,conflict,qos=1)
        self.assertEqual(owner.take_inputs(),())
        self.assertIs(owner.current_scene,current)
        self.assertIs(owner._token,token)
        # 다른 ID의 새 begin은 revision1이다. 전역 max revision이나 retired registry를 만들지 않는다.
        fresh=schema.SceneGrant();fresh.CopyFrom(value);fresh.scene.admission_id=bytes([10]*16)
        fresh.lifecycle_state=0;fresh.ClearField('frozen_evidence_sha256')
        fresh.frozen_survey_revision=fresh.frozen_hazard_revision=0
        jio_fixture.deliver(owner,topic,fresh,qos=1)
        self.assertEqual(owner.take_inputs()[0].raw,encode(fresh))
