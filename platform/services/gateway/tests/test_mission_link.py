"""실제 공용 protobuf와 전송 slot 검증. 내구 예약·권위·장치 수락 시험이 아니다."""

from dataclasses import FrozenInstanceError,replace
from hashlib import sha256

import pytest

from xenova_drive_world import decode,encode,schema
from xenova_gateway.mission_link import BudgetEnvelope,MissionLinkPort


A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
EXECUTION='33333333-3333-4333-8333-333333333333'
INSTANCE='44444444-4444-4444-8444-444444444444'


def request(nonce=1):
    return schema.MissionBudgetRequest(major=1,request_nonce=bytes([nonce])*16,
        mission_id=bytes([5])*16,mission_revision=1,control_generation=1,expected_grant_revision=1,
        action=1,slot_id=1,slot_revision=1,goal_revision=1,plan_revision=1,
        plan_sha256=bytes([6])*32,selected_goal_sha256=bytes([7])*32,
        scene_admission_id=bytes(16),scene_revision=0,completed_guest_us=1000000,valid_until_pi_us=2000000)


def grant():
    return schema.MissionGrant(major=1,vehicle='rc-test-1',pi_host_boot=bytes([1])*16,
        gateway_instance=bytes([2])*16,owner_vm=bytes([3])*16,owner_execution=bytes([4])*16,
        app_instance=bytes([5])*16,control_generation=1,mission_id=bytes([5])*16,mission_revision=1,
        grant_revision=1,kind=2,strategy=1,operator_request_nonce=bytes([8])*16,issued_pi_us=1000000,
        mission_deadline_pi_us=2000000,cancel_fence_revision=0,scene_admission_id=bytes(16),scene_revision=0,
        configuration='configuration-1',calibration='calibration-1',primitive=bytes([9])*32,
        execution_deadline_pi_us=0,correction_attempts_used=0,execution_travel_m=0.,
        max_execution_travel_m=5.,admitted=False,search_deadline_pi_us=1500000,
        search_travel_m=0.,max_search_travel_m=10.)


def offer(port,vm,body,received=1000000,generation=1):
    return port.offer_budget(vm,EXECUTION,INSTANCE,generation,body,received)


def reply(envelope):
    return schema.MissionBudgetReply(major=1,request_nonce=envelope.request_nonce,
        request_sha256=envelope.request_sha256,result=5,reason=1)


def test_budget_is_disabled_by_default_and_never_invents_a_grant():
    port=MissionLinkPort((A,B))
    body=request()
    result=offer(port,A,body)
    assert type(result) is schema.MissionBudgetReply
    assert (result.result,result.reason)==(5,1)
    assert result.request_nonce==body.request_nonce
    assert result.request_sha256==sha256(encode(body)).digest()
    assert not result.HasField('grant') and port.take_budget() is None
    assert port.current_grant(A) is None and port.current_grant(B) is None


def test_two_vm_slots_remain_reserved_after_take_and_conflicts_have_no_effect():
    port=MissionLinkPort((A,B));port.enable_budget_input()
    first,second=offer(port,A,request(1)),offer(port,B,request(2))
    assert port.take_budget() is first and port.take_budget() is second and port.take_budget() is None
    busy=offer(port,A,request(3))
    assert (busy.result,busy.reason)==(4,1) and not busy.HasField('grant')
    conflict=request(1);conflict.plan_revision=2
    denied=offer(port,A,conflict)
    assert (denied.result,denied.reason)==(5,8)
    assert offer(port,A,request(1),received=1999999,generation=9) is first
    assert first.received_pi_us==1000000 and first.valid_until_pi_us==2000000
    assert port.take_budget() is None
    assert decode(port.complete_budget(first,reply(first)),schema.MissionBudgetReply)==reply(first)
    third=offer(port,A,request(3),received=1100000)
    assert port.take_budget() is third
    with pytest.raises(ValueError):port.complete_budget(first,reply(first))
    assert port.complete_budget(third,reply(third))==encode(reply(third))
    assert port.complete_budget(second,reply(second))==encode(reply(second))


def test_canonical_unknown_fields_and_changed_owner_do_not_renew_pending_request():
    port=MissionLinkPort((A,B));port.enable_budget_input()
    body=request();known=encode(body)
    extended=schema.MissionBudgetRequest();extended.ParseFromString(known+b'\x98\x06\x01')
    original=offer(port,A,extended)
    assert original.request_wire==known and original.request_sha256==sha256(known).digest()
    assert extended.ByteSize()>len(known)
    # 새 인증 generation과 옛 body의 불일치를 먼저 거절하면 S18 원 nonce 조회를 막는다.
    duplicate=port.offer_budget(B,INSTANCE,EXECUTION,99,body,3000000)
    assert duplicate is original and duplicate.vm==A
    assert duplicate.authenticated_generation==1 and duplicate.received_pi_us==1000000
    assert duplicate.valid_until_pi_us==2000000
    assert port.take_budget() is original and port.take_budget() is None
    body.plan_revision=99
    assert original.request_message().plan_revision==1
    copy=original.request_message();copy.plan_revision=98
    assert original.request_message().plan_revision==1
    with pytest.raises(FrozenInstanceError):original.received_pi_us=3000000


def test_completion_requires_original_envelope_and_exact_nonce_fingerprint():
    port=MissionLinkPort((A,B));port.enable_budget_input()
    original=offer(port,A,request());assert port.take_budget() is original
    assert type(original) is BudgetEnvelope
    with pytest.raises(ValueError):port.complete_budget(replace(original),reply(original))
    for field,value in (('request_nonce',bytes([9])*16),('request_sha256',bytes([9])*32)):
        wrong=reply(original);setattr(wrong,field,value)
        with pytest.raises(ValueError):port.complete_budget(original,wrong)
    final=reply(original);final.result=1;final.reason=0;final.grant.CopyFrom(grant())
    known=encode(final)
    extended=schema.MissionBudgetReply();extended.ParseFromString(known+b'\x98\x06\x01')
    assert port.complete_budget(original,extended)==known
    assert extended.ByteSize()>len(known)
    assert port.current_grant(A) is None
    assert port.take_budget() is None
    # 완료 사실을 내구 원장처럼 replay하지 않는다. 후속 query는 S18 소유자에게 전달한다.
    subsequent=offer(port,B,request(),received=3000000,generation=99)
    assert type(subsequent) is BudgetEnvelope and port.take_budget() is subsequent
    assert subsequent.valid_until_pi_us==original.valid_until_pi_us


def test_grant_cache_keeps_original_bytes_without_implicit_authority_or_clock_changes():
    port=MissionLinkPort((A,B));body=grant();expected=encode(body)
    assert port.publish_grant(A,body)==expected
    body.grant_revision=99;body.admitted=True;body.mission_deadline_pi_us=9000000
    assert port.current_grant(A)==expected and port.current_grant(B) is None
    original=decode(port.current_grant(A),schema.MissionGrant)
    assert (original.grant_revision,original.mission_deadline_pi_us,original.admitted)==(1,2000000,False)
    invalid=grant();invalid.primitive=bytes(31)
    with pytest.raises(ValueError):port.publish_grant(A,invalid)
    assert port.current_grant(A)==expected
    with pytest.raises(ValueError):port.publish_grant('unknown',grant())


def test_installation_and_malformed_input_leave_no_pending_record():
    for vm_ids in ((A,),(A,A),(A,B,EXECUTION),([A],B)):
        with pytest.raises(ValueError):MissionLinkPort(vm_ids)
    port=MissionLinkPort((A,B));port.enable_budget_input()
    malformed=request();malformed.request_nonce=bytes(15)
    with pytest.raises(ValueError):offer(port,A,malformed)
    with pytest.raises(ValueError):offer(port,A,request(),received=True)
    denied=offer(port,'uninstalled-vm',request())
    assert (denied.result,denied.reason)==(5,1)
    assert port.take_budget() is None
