"""K의 실제 parser/session/event loop helper. 장치·RF·실제 STOP 수락 시험은 아니다."""
import os
from uuid import uuid4

import pytest

from xenova_gateway import keyboard_contract as wire
from xenova_gateway.authority_journal import canonical
from xenova_gateway.keyboard_source import (
    A, D, ENTER, ESC, GLOBAL_STOP, MODIFIER, REARM, RELEASE, DETACH, SPACE, W,
    EVENT, EV_KEY, EV_SYN, SYN_DROPPED, SYN_REPORT, Frame, InputEvent, KeyboardError,
    KeyboardSession, KeyState, decode_events, drain_device, load_device_profile, profile_from_fields)

HOST = '11111111-1111-4111-8111-111111111111'
GATEWAY = '22222222-2222-4222-8222-222222222222'
OWNER = '33333333-3333-4333-8333-333333333333'
SID = '44444444-4444-4444-8444-444444444444'
EPOCH = '55555555-5555-4555-8555-555555555555'
NEXT_EPOCH = '66666666-6666-4666-8666-666666666666'


def fields():
    return dict(version=1, alias='control-keyboard', device_path='/dev/input/by-id/approved-event-kbd',
                device_identity_sha256='a'*64, kernel_abi_sha256='b'*64, transport='WIRED',
                radio_policy='NOT_APPLICABLE', acceptance_sha256='c'*64, max_link_loss_us=None)


def test_installed_radio_claim_is_not_inferred_from_evdev_or_hash_alone(tmp_path):
    value = fields()
    assert profile_from_fields(value).continuous_allowed
    value.update(transport='WIRELESS', radio_policy='UNVERIFIED', acceptance_sha256=None)
    profile = profile_from_fields(value)
    assert not profile.continuous_allowed and profile.reason == 'UNVERIFIED_RADIO'
    value.update(radio_policy='VISIBLE_LINK_EPOCH', acceptance_sha256='c'*64, max_link_loss_us=500000)
    assert not profile_from_fields(value).continuous_allowed
    value['radio_policy'] = 'RECEIVER_RELEASE_INHIBIT'
    assert profile_from_fields(value).continuous_allowed
    path = tmp_path/'control-keyboard.json'
    path.write_bytes(canonical(value));path.chmod(0o600)
    assert load_device_profile(path, 'control-keyboard', uid=os.geteuid()) == profile_from_fields(value)
    target = tmp_path/'alias.json';target.symlink_to(path)
    with pytest.raises(OSError):load_device_profile(target, uid=os.geteuid())


@pytest.mark.parametrize('mutation', [
    lambda p:p.update(version=True), lambda p:p.update(max_link_loss_us=True),
    lambda p:p.update(max_link_loss_us=500001), lambda p:p.update(alias='../event0'),
    lambda p:p.update(device_path='/dev/input/../event0'), lambda p:p.update(extra=1),
    lambda p:p.update(kernel_abi_sha256='F'*64), lambda p:p.update(acceptance_sha256='not-proof')])
def test_profile_exact_values(mutation):
    value = fields();mutation(value)
    with pytest.raises(ValueError):profile_from_fields(value)


def event(code, value, timestamp=1000000):
    return InputEvent(timestamp, EV_KEY, code, value)


def packet(state, *values, observed=1010000):
    for value in values:state.process(value, observed)
    state.process(InputEvent(observed, EV_SYN, SYN_REPORT, 0), observed)


def test_evdev_parser_partial_noncanonical_time_and_key_value():
    raw = EVENT.pack(1, 4, EV_KEY, 17, 1)+EVENT.pack(1, 5, EV_SYN, SYN_REPORT, 0)
    assert decode_events(raw) == (event(17, 1, 1000004), InputEvent(1000005, EV_SYN, SYN_REPORT, 0))
    for invalid in (raw[:-1], b'', EVENT.pack(1, 1000000, EV_KEY, 17, 1), EVENT.pack(1, 0, EV_KEY, 17, 3)):
        with pytest.raises(KeyboardError):decode_events(invalid)
    with pytest.raises(KeyboardError, match='INPUT_BACKLOG'):decode_events(raw*129)


def test_short_release_is_sticky_and_early_repress_cannot_resume_after_ready():
    state = KeyState();state.armed=True;state.blocked=state.require_rearm=False
    packet(state, event(17, 1))
    assert state.snapshot(1010000, 1010010).mask == W
    packet(state, event(17, 0, 1020000), event(17, 1, 1020001), observed=1020010)
    assert state.mask == W and state.fence_mask & RELEASE and 17 in state.early_keys
    state.fence_mask=0;state.stop_ready()
    assert 17 in state.early_keys
    packet(state, event(17, 0, 1030000), observed=1030010)
    assert not state.early_keys and not state.fence_mask
    packet(state, event(17, 1, 1040000), observed=1040010)
    assert state.mask == W and not state.early_keys


def test_quiet_held_and_repeat_do_not_make_new_human_edge_or_radio_evidence():
    state = KeyState();state.armed=True;state.blocked=state.require_rearm=False
    packet(state, event(17, 1))
    first=state.snapshot(1010000, 1010010)
    packet(state, event(17, 2, 1050000), observed=1050010)
    second=state.snapshot(1050020, 1050030)
    quiet=state.snapshot(2000000, 2000010)
    assert first.key_event == second.key_event == quiet.key_event == 1
    assert first.last_event == second.last_event == quiet.last_event == 1000000
    assert second.mask == quiet.mask == W and quiet.sample > first.sample


def test_modifiers_and_conflicts_rearm_and_a_d_release_keeps_forward_raw_state():
    state=KeyState();state.armed=True;state.blocked=state.require_rearm=False
    packet(state,event(17,1),event(30,1))
    assert state.mask == W | A
    packet(state,event(30,0,1020000),observed=1020010)
    assert state.mask == W and not state.fence_mask
    packet(state,event(42,1,1030000),event(54,1,1030001),event(42,0,1030002),observed=1030010)
    assert state.mask & MODIFIER and state.fence_mask & REARM and not state.armed
    other=KeyState();packet(other,event(30,1),event(32,1))
    assert other.mask == A | D and other.fence_reason == 'KEY_CONFLICT'


def test_syn_dropped_resync_never_synthesizes_press_or_arm():
    state=KeyState();state.armed=True;state.blocked=state.require_rearm=False
    packet(state,event(17,1))
    state.process(InputEvent(1020000,EV_SYN,SYN_DROPPED,0),1020010)
    state.process(event(17,0,1020001),1020010)
    state.process(InputEvent(1020002,EV_SYN,SYN_REPORT,0),1020010)
    assert state.need_resync and state.fence_reason == 'SYN_DROPPED'
    before=state.key_event;state.resync({17})
    assert state.mask == W and state.key_event == before and state.require_rearm and not state.armed
    assert 17 in state.early_keys and state.arm_event is None


def test_only_two_accepted_neutral_samples_then_fresh_enter_can_propose_arm():
    state=KeyState()
    neutral=state.snapshot(1000000,1000010)
    state.frame_accepted(neutral);state.frame_accepted(state.snapshot(1050000,1050010))
    packet(state,event(28,1,1060000),observed=1060010)
    assert state.arm_event == 1
    packet(state,event(28,2,1070000),observed=1070010)
    assert state.arm_event == 1 and state.key_event == 1 and state.last_event == 1060000
    state.fence(REARM,'MODIFIER');state.fence_mask=0
    state.neutral_samples=2
    packet(state,event(28,0,1080000),event(28,1,1080001),observed=1080010)
    assert state.arm_event is None  # Enter before STOP_READY is not saved.


class Clock:
    value=1000000
    def __call__(self):return self.value


def session():
    clock=Clock()
    return KeyboardSession(profile_from_fields(fields()),dict(host_boot_id=HOST,gateway_instance_id=GATEWAY,
        generation=1,owner=OWNER),clock),clock


def response(client, clock, kind, payload, original=None):
    original=client.ordinary if original is None else original
    return canonical(dict(version=1,kind=kind,host_boot_id=HOST,gateway_instance_id=GATEWAY,
        source_instance=client.source,channel_epoch=client.channel,sequence=str(client.received_sequence+1),
        request_id=str(uuid4()) if original is None else original.request,
        issued_pi_us=str(clock.value),expires_pi_us=str(clock.value+5000000),payload=payload))


def state_reply(client, clock, *, sid=SID, epoch=EPOCH, grant=1, generation=1, state='REARM_REQUIRED',
                reason='OK', accepted=True, original=None):
    original=client.ordinary if original is None else original
    payload=dict(driver_session=sid,input_epoch=epoch,grant_revision=str(grant),generation=generation,state=state,
                 reason=reason,reply_to_kind=None if original is None else original.kind,
                 reply_to_sequence=None if original is None else str(original.sequence),accepted=accepted,
                 accepted_state_seq='1' if accepted else None)
    return response(client,clock,'STATE',payload,original)


def attached():
    client,clock=session()
    client.receive(state_reply(client,clock,sid=None,epoch=None,grant=0,state='DETACHED',reason='HELLO_ACCEPTED'),clock())
    assert client.ordinary.kind == 'ATTACH'
    payload=dict(driver_session=SID,input_epoch=EPOCH,grant_revision='1',generation=1,
                 profile_sha256=client.profile.profile_sha256,state='REARM_REQUIRED',reason='ATTACHED')
    client.receive(response(client,clock,'GRANT',payload),clock())
    assert client.binding == (SID,EPOCH,1,1)
    return client,clock


def test_transport_retry_keeps_exact_original_bytes_and_expiry():
    client,clock=session();first=client.outgoing(clock())
    client.sent(first,clock());clock.value+=50000
    assert client.outgoing(clock()) is first and client.ordinary.raw == first.raw
    assert wire.decode(first.raw)['expires_pi_us'] == str(first.expires)


def test_actual_frame_ack_precedes_arm_and_arm_keeps_original_frame_deadline():
    client,clock=attached()
    for _ in range(2):
        frame=client.state.snapshot(clock(),clock()+1);client.offer(frame)
        clock.value+=2
        assert client.outgoing(clock()).kind == 'FRAME'
        client.receive(state_reply(client,clock),clock());clock.value+=50000
    packet(client.state,event(28,1,clock()),observed=clock()+1)
    frame=client.state.snapshot(clock(),clock()+2);client.offer(frame)
    clock.value+=3
    pending=client.outgoing(clock());assert pending.kind == 'FRAME'
    client.receive(state_reply(client,clock),clock())
    arm=client.outgoing(clock());assert arm.kind == 'ARM'
    assert arm.issued == frame.t1 and arm.expires == frame.t1+150000
    assert wire.decode(arm.raw)['payload']['arm_event_seq'] == str(frame.key_event)
    client.receive(state_reply(client,clock,epoch=NEXT_EPOCH,grant=2,state='ARMED_NEUTRAL',reason='ARMED'),clock())
    assert client.state.armed and client.binding[1:3] == (NEXT_EPOCH,2)
    original=arm.raw
    asynchronous=dict(driver_session=SID,input_epoch=NEXT_EPOCH,grant_revision='2',generation=2,state='ARMED_NEUTRAL',
        reason='OK',reply_to_kind=None,reply_to_sequence=None,accepted=None,accepted_state_seq=None)
    client.receive(response(client,clock,'STATE',asynchronous),clock())
    assert client.state.armed and client.binding[3] == 2 and arm.raw == original


def test_fence_ack_is_not_ordinary_credit_and_upgrade_precedes_old_stop_ready():
    client,clock=attached();client.state.armed=True;client.state.blocked=client.state.require_rearm=False
    packet(client.state,event(17,1,clock()),observed=clock()+1)
    client.offer(client.state.snapshot(clock(),clock()+2));clock.value+=2;ordinary=client.outgoing(clock())
    packet(client.state,event(17,0,clock()+3),event(17,1,clock()+4),observed=clock()+5)
    clock.value+=6;fence=client.outgoing(clock());assert fence.kind == 'FENCE'
    frozen=fence.raw
    packet(client.state,event(57,1,clock()+7),event(1,1,clock()+8),observed=clock()+9)
    clock.value+=10;client.outgoing(clock())
    assert fence.raw == frozen and client.ordinary is ordinary
    key=wire.decode(fence.raw)['payload']
    ack=dict(driver_session=key['driver_session'],input_epoch=key['input_epoch'],
             fence_revision=key['fence_revision'],accepted_pi_us=str(clock()))
    client.receive(response(client,clock,'FENCE_ACK',ack,fence),clock())
    upgrade=client.outgoing(clock());assert upgrade.kind == 'FENCE' and upgrade is not fence
    assert int(wire.decode(upgrade.raw)['payload']['fence_revision']) == 2
    assert wire.decode(upgrade.raw)['payload']['reason_mask'] == RELEASE | REARM | DETACH | GLOBAL_STOP
    assert client.ordinary is ordinary and client.wait_stop
    ready=dict(driver_session=SID,input_epoch=EPOCH,fence_revision=key['fence_revision'],
               stop_command_sequence='8',stationary_sample_sequence='9')
    client.receive(response(client,clock,'STOP_READY',ready,fence),clock())
    assert client.wait_stop and client.fence is upgrade


def test_priority_fence_rejection_retires_old_frame_without_turning_release_into_rearm():
    client,clock=attached();client.state.armed=True;client.state.blocked=client.state.require_rearm=False
    packet(client.state,event(17,1,clock()),observed=clock()+1)
    client.offer(client.state.snapshot(clock(),clock()+2));clock.value+=2
    original=client.outgoing(clock())
    packet(client.state,event(17,0,clock()+1),observed=clock()+2);clock.value+=3
    fence=client.outgoing(clock());client.sent(fence,clock())
    client.receive(state_reply(client,clock,reason='STALE_SAMPLE',accepted=False,original=original),clock())
    assert client.ordinary is None and client.wait_stop and not client.state.require_rearm
    assert client.state.armed and not client.state.fence_mask


def test_drain_requires_eagain_complete_packet_and_keeps_t1(monkeypatch):
    import xenova_gateway.keyboard_source as product
    clock=Clock();state=KeyState();state.armed=True;state.blocked=state.require_rearm=False
    class Device:
        fd=123
        def keys(self):return set()
    chunks=iter((EVENT.pack(1,0,EV_KEY,17,1)+EVENT.pack(1,1,EV_SYN,SYN_REPORT,0),None))
    def read(fd,length):
        assert fd == 123
        value=next(chunks)
        if value is None:raise BlockingIOError()
        return value
    clock.value=1000010;monkeypatch.setattr(product.os,'read',read)
    frame,changed=drain_device(Device(),state,clock)
    assert changed and frame.t1 == frame.t2 == 1000010 and frame.mask == W
    monkeypatch.setattr(product.os,'read',lambda *_:b'partial')
    with pytest.raises(KeyboardError,match='PROTOCOL_ERROR'):drain_device(Device(),state,clock)
