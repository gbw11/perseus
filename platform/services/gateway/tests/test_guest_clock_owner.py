"""실제 I/O stamp wrapper→SentClock→P4 receipt owner. 차량/출력 권위를 대체하지 않는다."""
from hashlib import sha256

import pytest

from xenova_drive_world import decode, schema
from xenova_gateway.guest_clock import (OWNER_PEERS, PER_PEER, GuestClockOwner,
                                        GuestClockPolicy, OwnedPreparedClock, SentClock)

HOST = '11111111-1111-4111-8111-111111111111'
GATEWAY = '22222222-2222-4222-8222-222222222222'
VM = '33333333-3333-4333-8333-333333333333'
EXECUTION = '44444444-4444-4444-8444-444444444444'
AUTO = '55555555-5555-4555-8555-555555555555'
MANUAL = '66666666-6666-4666-8666-666666666666'
DEVICE = '77777777-7777-4777-8777-777777777777'
ODOM = '88888888-8888-4888-8888-888888888888'
PEER = (VM, EXECUTION, AUTO)
PEERS = (PEER, (VM, EXECUTION, MANUAL),
         ('99999999-9999-4999-8999-999999999999', EXECUTION, AUTO),
         ('99999999-9999-4999-8999-999999999999', EXECUTION, MANUAL))


def policy(**changes):
    fields = dict(timestamp_error_us=5, drift_ppm=10, max_roundtrip_us=10000,
                  max_offset_width_us=50000, lease_us=500000)
    fields.update(changes)
    return GuestClockPolicy(**fields)


def probe(sequence=1, guest=1000000, peer=PEER):
    return schema.ClockProbe(vm=peer[0], execution=peer[1], app_instance=peer[2],
                             sequence=sequence, guest_send_us=guest)


def reply(original, *, receive=6000100, expiry=None, ack=None):
    link=schema.DriveLink(major=2);body=link.clock
    body.probe.CopyFrom(original);body.host_boot=HOST;body.gateway_instance=GATEWAY
    body.pi_receive_us=body.pi_send_us=receive
    body.guest_clock_valid_until_pi_us=receive+500000 if expiry is None else expiry
    if ack is not None:body.accepted_receipt.CopyFrom(ack)
    body.vehicle.CopyFrom(schema.VehicleSample(major=1,vehicle='rc-test-1',host_boot=HOST,
        device_session=DEVICE,odom_epoch=ODOM,sequence=1,source_sample=1,source_us=receive-100,
        pi_sample_us=receive-80,clock_error_us=20,left_ticks=0,right_ticks=0,
        odom_pose=schema.Pose(x=0,y=0,yaw=0),velocity_mps=0,position_error_m=.003,
        heading_error_rad=.003,quality=3,calibration='encoder-test-1'))
    return link


def sent(owner, *, original=None, peer=PEER, generation=1, receive=6000100, expiry=None, send=None):
    original=probe(peer=peer) if original is None else original
    assert owner.accept_probe(original,peer,generation,receive) == (None,None)
    value=reply(original,receive=receive,expiry=expiry)
    prepared=OwnedPreparedClock(value,peer,generation)
    raw=prepared.stamp(receive+20 if send is None else send)
    completed=SentClock(prepared.peer,prepared.generation,raw)
    owner.sent_reply(completed)
    return value,prepared,completed


def receipt(completed, p4=1000300):
    original=decode(completed.raw,schema.DriveLink).clock.probe
    return schema.GuestClockReceipt(probe_sequence=original.sequence,
        reply_sha256=sha256(completed.raw).digest(),guest_receive_us=p4)


def next_probe(completed, *, sequence=2, guest=1010000, p4=1000300):
    peer=completed.peer
    value=probe(sequence,guest,peer);value.receipt.CopyFrom(receipt(completed,p4))
    return value


def test_original_io_bytes_p4_and_explicit_ack_keep_the_original_interval_and_expiry():
    owner=GuestClockOwner(policy(),HOST,GATEWAY)
    before,prepared,completed=sent(owner)
    original=decode(completed.raw,schema.DriveLink).clock
    assert original.pi_send_us == 6000120
    assert original.guest_clock_valid_until_pi_us == before.clock.guest_clock_valid_until_pi_us == 6500100
    assert not original.HasField('accepted_receipt') and owner.lease(PEER,1,6010000,1) is None
    request=next_probe(completed)
    acknowledged,replay=owner.accept_probe(request,PEER,1,6010000)
    assert replay is None and acknowledged == request.receipt
    lease=owner.lease(PEER,1,6010000,1)
    # Independent integer rounding of the declared bound, not drift_pad().
    elapsed=300;span=500000
    denominator=1000000-10
    expansion=2*5+(10*(elapsed+2*span)+denominator-1)//denominator
    assert expansion == 21
    assert (lease.lower,lease.upper,lease.expires) == (4999799,5000121,6500100)
    assert lease.digest == sha256(completed.raw).digest() and lease.p4 == 1000300
    assert owner.interval(PEER,1,1005000,6010000,1) == (6004799,6005121,6500100)
    following=reply(request,receive=6010000,ack=acknowledged)
    acknowledged_raw=OwnedPreparedClock(following,PEER,1).stamp(6010020)
    actual=decode(acknowledged_raw,schema.DriveLink).clock
    assert actual.accepted_receipt == request.receipt
    assert actual.guest_clock_valid_until_pi_us == 6510000
    assert owner.lease(PEER,1,6010020,1).expires == 6500100
    assert prepared.peer == completed.peer and prepared.generation == completed.generation


def test_zero_guest_roundtrip_can_be_valid_after_the_original_error_expansion():
    owner=GuestClockOwner(policy(),HOST,GATEWAY);_,_,completed=sent(owner)
    request=next_probe(completed,p4=1000000)
    ack,_=owner.accept_probe(request,PEER,1,6010000)
    assert ack == request.receipt
    lease=owner.lease(PEER,1,6010000,1)
    # Pi 기간을 guest 기간으로 감쌀 때 분모는1e6-ppm이다. 원RTT가0이어도
    # 2*error10 + ceil(10*1000000/999990)11 =21us를 원 lease 끝까지 보존한다.
    assert (lease.lower,lease.upper,lease.p4) == (5000099,5000121,1000000)


def test_duplicate_probe_replays_original_raw_without_restamping_or_renewing():
    owner=GuestClockOwner(policy(),HOST,GATEWAY);before,_,completed=sent(owner)
    ack,replay=owner.accept_probe(before.clock.probe,PEER,2,6100000)
    assert ack is None and replay is completed.raw
    retried=OwnedPreparedClock(before,PEER,2,replay=replay).stamp(6200000)
    assert retried == completed.raw
    assert decode(retried,schema.DriveLink).clock.pi_send_us == 6000120
    assert decode(retried,schema.DriveLink).clock.guest_clock_valid_until_pi_us == 6500100
    owner.sent_reply(SentClock(PEER,1,retried))
    assert len(owner.sent) == 1
    changed=probe(1,1000001)
    with pytest.raises(ValueError,match='conflict'):owner.accept_probe(changed,PEER,2,6100000)


def test_lost_ack_can_be_confirmed_by_a_later_probe_with_the_same_original_receipt():
    owner=GuestClockOwner(policy(),HOST,GATEWAY);_,_,completed=sent(owner)
    original=next_probe(completed)
    first,_=owner.accept_probe(original,PEER,1,6010000)
    # No ACK was delivered to the VM. The next fresh Probe carries original P4.
    again=next_probe(completed,sequence=3,guest=1020000)
    second,_=owner.accept_probe(again,PEER,1,6020000)
    assert first == second == original.receipt
    assert len(owner.confirmed) == 1 and owner.lease(PEER,1,6020000,1).expires == 6500100


def test_expired_pending_receipt_can_bootstrap_a_new_original_without_extending_the_old_one():
    owner=GuestClockOwner(policy(),HOST,GATEWAY);_,_,old=sent(owner)
    with pytest.raises(ValueError):owner.accept_probe(next_probe(old),PEER,1,6500100)
    _,_,fresh=sent(owner,original=probe(3,1600000),receive=6600100)
    request=next_probe(fresh,sequence=4,guest=1610000,p4=1600300)
    acknowledged,_=owner.accept_probe(request,PEER,1,6610000)
    assert acknowledged == request.receipt
    assert owner.lease(PEER,1,6610000,1) is None
    assert owner.lease(PEER,3,6610000,1).expires == 7100100


def test_clock_is_full_app_source_scoped_and_generation_alone_does_not_mint_a_new_lease():
    owner=GuestClockOwner(policy(),HOST,GATEWAY);_,_,completed=sent(owner)
    owner.accept_probe(next_probe(completed),PEER,1,6010000)
    original=owner.lease(PEER,1,6010000,1)
    assert owner.lease(PEER,1,6010000,99) is original and original.generation == 1
    manual=(VM,EXECUTION,MANUAL)
    assert owner.lease(manual,1,6010000,1) is None
    _,_,own=sent(owner,peer=manual,generation=2)
    owner.accept_probe(next_probe(own),manual,2,6010000)
    assert owner.lease(manual,1,6010000,2).generation == 2
    standby=('99999999-9999-4999-8999-999999999999',EXECUTION,AUTO)
    assert owner.lease(standby,1,6010000,2) is None
    _,_,own_b=sent(owner,peer=standby,generation=2)
    owner.accept_probe(next_probe(own_b),standby,2,6010000)
    assert owner.lease(standby,1,6010000,2).generation == 2
    foreign=(VM,'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',AUTO)
    assert owner.lease(foreign,1,6010000,1) is None
    assert owner.lease(PEER,1,6500100,99) is None


def test_peer_capacity_preserves_unexpired_originals_and_prunes_only_original_expiry():
    owner=GuestClockOwner(policy(),HOST,GATEWAY);owner.register_peers(PEERS)
    assert OWNER_PEERS == 4 and PER_PEER == 32
    originals=[]
    for index in range(PER_PEER):
        guest=1000000+index*1000;receive=6000000+index*1000
        for peer in PEERS:
            _,_,completed=sent(owner,peer=peer,original=probe(2*index+1,guest,peer),receive=receive)
            originals.append(completed)
            acknowledgement=next_probe(completed,sequence=2*index+2,guest=guest+500,p4=guest+300)
            owner.accept_probe(acknowledgement,peer,1,receive+500)
    assert len(owner.sent) == len(owner.confirmed) == OWNER_PEERS*PER_PEER == 128
    assert owner.sent[(*PEER,1)].raw is originals[0].raw
    assert owner.lease(PEER,1,6032000,1).expires == 6500000
    with pytest.raises(ValueError,match='capacity'):
        sent(owner,original=probe(65,1032000),receive=6032000)
    assert len(owner.sent) == len(owner.confirmed) == 128
    # The next send retires only parents whose original E has actually passed.
    sent(owner,original=probe(66,1531000),receive=6531000)
    assert len(owner.sent) == 1 and not owner.confirmed
    assert (*PEER,1) not in owner.sent


def test_four_peer_fast_traffic_cannot_evict_a_150ms_original_receipt():
    owner=GuestClockOwner(policy(max_roundtrip_us=200000,max_offset_width_us=200000),HOST,GATEWAY)
    owner.register_peers(PEERS)
    original_bytes={};delayed=None
    for tick in range(4):
        for peer in PEERS:
            guest=1000000+tick*50000;receive=6000100+tick*50000
            _,_,completed=sent(owner,peer=peer,original=probe(tick+1,guest,peer),receive=receive)
            original_bytes[(*peer,tick+1)]=completed.raw
            if tick == 0 and peer == PEER:delayed=completed
    assert len(owner.sent) == 16
    assert all(owner.sent[key].raw is raw for key,raw in original_bytes.items())
    request=next_probe(delayed,sequence=5,guest=1160000,p4=1150000)
    acknowledged,replay=owner.accept_probe(request,PEER,1,6160100)
    assert acknowledged == request.receipt and replay is None
    original=owner.lease(PEER,1,6160100,1)
    assert original.digest == sha256(delayed.raw).digest() and original.p4 == 1150000
    assert original.expires == 6500100
    with pytest.raises(ValueError,match='unregistered'):
        sent(owner,peer=(VM,'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',AUTO))
    assert len(owner.sent) == 16 and len(owner.confirmed) == 1


@pytest.mark.parametrize('failure',['sha','p4-before','p4-after-probe','expired','oversized-original-E','send-after-E','roundtrip','width'])
def test_invalid_receipt_or_original_budget_never_becomes_confirmed(failure):
    owner=GuestClockOwner(policy(max_offset_width_us=10) if failure=='width' else policy(),HOST,GATEWAY)
    options={}
    if failure=='oversized-original-E':options['expiry']=6500101
    if failure=='send-after-E':options['send']=6500101
    now=6500100 if failure=='expired' else 6010000
    with pytest.raises(ValueError):
        _,_,completed=sent(owner,**options)
        request=next_probe(completed)
        if failure=='sha':request.receipt.reply_sha256=b'x'*32
        elif failure=='p4-before':request.receipt.guest_receive_us=999999
        elif failure=='p4-after-probe':request.receipt.guest_receive_us=request.guest_send_us+1
        elif failure=='roundtrip':
            request.guest_send_us=1020000;request.receipt.guest_receive_us=1010001
        owner.accept_probe(request,PEER,1,now)
    assert not owner.confirmed and owner.lease(PEER,1,now,1) is None
