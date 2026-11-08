"""Scene 값 흐름과 같은 Authority의 실제 임시 journal. 실물 STOP/접근 통제 시험은 아니다."""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from xenova_drive_world import schema, encode, decode
from xenova_drive_world.mapper_clock import ClockLease
from xenova_gateway.authority import AuthorityStore
from xenova_gateway.scene_authority import (SceneAuthority, ScenePolicy, StationaryPolicy,
    SceneGate, SceneRequest, MatchedStop, SceneAuthorityError, COMMAND3, FEEDBACK3, execute_scene_journal)


def uid(number): return '00000000-0000-4000-8000-'+f'{number:012x}'
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode('ascii')


def source():
    return dict(vehicle='rc-test-1',device='oak-1',mapper_boot=uid(10),mapper_instance=uid(11),clock_epoch=uid(12),
        map_epoch=uid(5),frame_epoch=uid(6),odom_epoch=uid(3),configuration='scene-lab',calibration='a'*64,primitive='b'*64)


def lease(src=None, *, p4=1010000):
    src=source() if src is None else src
    p1=p4-1000
    probe=schema.MapperClockProbe(major=1,vehicle=src['vehicle'],device=src['device'],pi_host_boot=uid(1),
        pi_client_instance=uid(13),probe_seq=p4,nonce=UUID(uid(p4)).bytes,pi_send_us=p1,admission_revision=1)
    trust=schema.WorldTrust(**{key:value for key,value in src.items() if key!='device'},
        lower_offset_us=0,upper_offset_us=0,expires_pi_us=p1+1_000_000,max_error_us=100)
    return ClockLease(encode(trust),1,p4,p4,p1,p1,max(0,p1-1_000_000),p1+1_000_000,100,encode(probe),1_000_000,1_000_000)


def gate():
    def vehicle(at,seq):
        return encode(schema.VehicleSample(major=1,vehicle='rc-test-1',host_boot=uid(1),device_session=uid(2),
            odom_epoch=uid(3),sequence=seq,source_sample=seq,source_us=at-100,pi_sample_us=at,clock_error_us=0,
            left_ticks=0,right_ticks=0,odom_pose=schema.Pose(x=0,y=0,yaw=0),velocity_mps=0,
            position_error_m=0,heading_error_rad=0,quality=3,calibration='a'*64,gateway_instance=uid(4),pi_session=uid(7)))
    command=COMMAND3.pack(UUID(uid(7)).bytes,UUID(uid(2)).bytes,77,bytes(16),0,0,0,0,1,4,2,0,0,0,1,0)
    report=FEEDBACK3.pack(UUID(uid(7)).bytes,UUID(uid(2)).bytes,77,1,4,2,2,88,2,999900,4,0,0,1,bytes(16),0,0,0,0)
    return SceneGate('MANUAL',1,uid(7),uid(2),0,MatchedStop(command,report,(vehicle(980000,1),vehicle(1000000,2))))


def request(action,number,at,args):
    return SceneRequest(uid(number),uid(number+100),at,at+200000,1,action,canonical(args))


def reply(summary, clock):
    probe=decode(clock.raw_probe_bytes,schema.MapperClockProbe); src=clock.source
    value=schema.MapperClockReply(major=1,pi_host_boot=uid(1),pi_client_instance=probe.pi_client_instance,
        probe_seq=probe.probe_seq,nonce=probe.nonce,pi_send_us=probe.pi_send_us,j2=probe.pi_send_us+10,j3=clock.completed_pi_us-1,
        **{key:item for key,item in src.items() if key!='vehicle'})
    value.survey_summary.CopyFrom(summary); return encode(value)


def summary(grant, *, status=0):
    return schema.SurveySummary(major=1,request_nonce=grant.request_nonce,survey_id=grant.scene.survey_id,
        survey_revision=grant.scene.survey_revision,status=status,world_sequence=5,evidence_sequence=5,
        started_peer_us=1005000,last_observed_peer_us=1015000,completed_peer_us=1019000,
        positive_cell_count=0 if status==0 else 1,observed_slot_count=0,required_sensor_mask=1,
        valid_sensor_mask=0 if status==0 else 1,evidence_sha256=bytes(32) if status==0 else bytes.fromhex('c'*64),
        coverage_profile_sha256=grant.scene.coverage_profile_sha256,observation_station_count=0 if status==0 else 1,
        hazard_revision=1,alignment_revision=1,anchor_position_bound_m=.001,anchor_heading_bound_rad=.001,
        evidence_flags=4 if status==0 else 31,failure_flags=0,timestamp_error_us=0)


@unittest.skipUnless(hasattr(os,'O_DIRECTORY'),'실제 Authority journal은 POSIX에서 검사한다')
class SceneAuthorityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store=AuthorityStore(Path(tmp.name),uid(1),uid=os.geteuid(),mountpoint=None)
        self.store.initialize(uid(90)); self.events=[]; self.clock=lease(); self.gate=gate()
        policy=ScenePolicy('d'*64,1,4096,.05,.05,500000,
            StationaryPolicy(10000,30000,1_000_000,.000001,.01,.01,0.,0.))
        self.owner=SceneAuthority(source(),policy,pi_host_boot=uid(1),gateway_instance=uid(4),fence_stop=self.events.append)

    def durable(self, at):
        original=self.owner.take_journal()
        ack=execute_scene_journal(self.store,original,clock_us=lambda:at)
        return self.owner.complete_journal(ack,self.gate,now_us=at)

    def accept(self, value, at):
        self.clock=lease(p4=at)
        return self.owner.accept_reply(reply(value,self.clock),self.clock,self.gate,now_us=at)

    def begin(self):
        req=request('scene.survey.begin',20,1001000,dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=True))
        self.owner.begin(req,self.gate,admission_id=uid(30),survey_id=uid(31),source_admission_revision=1,now_us=1001000)
        raw=self.durable(1001100); grant=decode(raw,schema.SceneGrant)
        self.assertEqual(self.accept(summary(grant),1020000),'APPLIED')
        return grant

    def freeze(self):
        self.begin(); req=request('scene.survey.commit',21,1025000,dict(survey_id=uid(31)))
        self.owner.commit(req,self.gate,now_us=1025000); raw=self.durable(1025100)
        grant=decode(raw,schema.SceneGrant); frozen=summary(grant,status=1)
        frozen.last_observed_peer_us=1030000; frozen.completed_peer_us=1031000
        proposal=self.accept(frozen,1035000)
        self.assertIs(proposal,self.owner.take_journal()); self.assertTrue(self.owner.scene_commit_hold)
        return frozen

    def test_actual_journal_then_exact_mapper_applied_without_motion_permission(self):
        frozen=self.freeze(); self.assertFalse(self.owner.eligible)
        admitted=decode(self.durable(1035100),schema.SceneGrant)
        self.assertEqual(admitted.lifecycle_state,2); self.assertFalse(self.owner.eligible)
        applied=schema.SurveySummary(); applied.CopyFrom(frozen); applied.status=2
        self.assertEqual(self.accept(applied,1040000),'APPLIED')
        self.assertTrue(self.owner.eligible); self.assertFalse(self.owner.scene_commit_hold)
        self.assertEqual(self.store.value['generation'],1); self.assertEqual(self.store.value['mode'],'SAFE_STOP')

    def test_missing_or_feedback2_style_stop_is_not_a_scene_gate(self):
        req=request('scene.survey.begin',20,1001000,dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=False))
        with self.assertRaises(SceneAuthorityError):
            self.owner.begin(req,replace(self.gate,stop=None),admission_id=uid(30),survey_id=uid(31),source_admission_revision=1,now_us=1001000)
        self.assertEqual(self.events,[]); self.assertIsNone(self.owner.take_journal())
        with self.assertRaises(SceneAuthorityError): MatchedStop(b'feedback-2',b'APPLIED',self.gate.stop.vehicle_window)
        with self.assertRaises(SceneAuthorityError):
            replace(req,expires_us=req.issued_us+10_000_001)

    def test_each_wheel_and_original_report_sample_are_required(self):
        stop=self.gate.stop; values=list(stop.vehicle_window)
        moving=decode(values[-1],schema.VehicleSample); moving.left_ticks=1000; moving.right_ticks=-1000
        values[-1]=encode(moving)
        bad=replace(stop,vehicle_window=tuple(values))
        with self.assertRaises(SceneAuthorityError):
            bad.check(source(),uid(1),uid(4),1,uid(7),uid(2),self.owner.policy.stationary,1001000)
        with self.assertRaises(SceneAuthorityError):
            stop.check(source(),uid(1),uid(4),1,uid(7),uid(2),self.owner.policy.stationary,2000001)

    def test_revoke_fences_before_pending_commit_fsync_and_late_ack_cannot_readmit(self):
        self.freeze(); original=self.owner.take_journal(); before=self.store.value['current_scene']
        req=request('scene.revoke',22,1036000,dict(scene_id=uid(30)))
        self.owner.revoke(req,source_admission_revision=2,now_us=1036000,cancel_fence=1)
        self.assertEqual(self.events[-1],'REVOKED'); self.assertFalse(self.owner.eligible)
        self.assertEqual(self.store.value['current_scene'],before)
        ack=execute_scene_journal(self.store,original,clock_us=lambda:1037000)
        self.assertIsNone(self.owner.complete_journal(ack,self.gate,now_us=1037000)); self.assertFalse(self.owner.eligible)
        # owner/generation 이동도 원 대상 revoke를 무효화하지 않는다.
        self.store.update(generation=2)
        revoked=self.durable(1038000)
        self.assertEqual(decode(revoked,schema.SceneGrant).lifecycle_state,3); self.assertFalse(self.owner.eligible)

    def test_unknown_commit_keeps_original_hold_and_request(self):
        self.freeze(); proposal=self.owner.take_journal()
        self.owner.journal_failed(proposal,unknown=True)
        self.assertIs(self.owner.take_journal(),proposal); self.assertTrue(self.owner.scene_commit_hold)
        self.assertFalse(self.owner.eligible)

    def test_old_nonce_changed_summary_or_parent_never_admits(self):
        grant=self.begin(); req=request('scene.survey.commit',21,1025000,dict(survey_id=uid(31)))
        self.owner.commit(req,self.gate,now_us=1025000); grant=decode(self.durable(1025100),schema.SceneGrant)
        stale=summary(grant,status=1); stale.request_nonce=UUID(uid(999)).bytes
        self.clock=lease(p4=1035000)
        with self.assertRaises(SceneAuthorityError): self.owner.accept_reply(reply(stale,self.clock),self.clock,self.gate,now_us=1035000)
        wrong=decode(reply(summary(grant,status=1),self.clock),schema.MapperClockReply); wrong.mapper_instance=uid(999)
        with self.assertRaises(SceneAuthorityError): self.owner.accept_reply(encode(wrong),self.clock,self.gate,now_us=1035000)

    def test_gateway_restart_and_original_deadline_never_auto_restore(self):
        self.freeze(); self.durable(1035100)
        raw=canonical(self.store.value['current_scene'])
        restored=SceneAuthority(source(),self.owner.policy,pi_host_boot=uid(1),gateway_instance=uid(40),
            fence_stop=self.events.append,journal_record=raw)
        self.assertFalse(restored.eligible); self.assertEqual(restored.blocked_reason,'GATEWAY_RESTART')
        req=request('scene.restore',23,1040000,dict(checkpoint_sha256='c'*64))
        with self.assertRaises(SceneAuthorityError): restored.restore(req,self.gate,source_admission_revision=2,now_us=1040000)
        self.owner.tick(now_us=1225000)
        self.assertFalse(self.owner.eligible); self.assertTrue(self.owner.scene_commit_hold)

    def test_no_scene_is_live_only_and_retry_does_not_extend_grant(self):
        self.assertIsNone(self.owner.grant_bytes); self.assertFalse(self.owner.eligible)
        grant=self.begin(); before=self.owner.grant_bytes
        original=request('scene.survey.begin',20,1001000,dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=True))
        self.assertIsNone(self.owner.begin(original,self.gate,admission_id=uid(30),survey_id=uid(31),source_admission_revision=1,now_us=1999999))
        self.assertEqual(self.owner.grant_bytes,before)
        self.assertEqual(grant.valid_until_pi_us,1001000+14_400_000_000)
        self.assertTrue(self.owner.tick(now_us=1001000+180_000_000))
        self.assertEqual(self.owner.state,'FAILED'); self.assertFalse(self.owner.eligible)
