"""Receipt/명시 ACK/원 Gate job의 형식 검사. 실제 actor·P4·STOP 증명은 통합 시험에서 한다."""
import pytest

from test_input_clock import reply
from xenova_drive_world import decode, encode, schema, PreparedClock


SESSION='88888888-8888-4888-8888-888888888888'


def acknowledged_reply():
    link=reply()
    link.clock.probe.sequence=2
    link.clock.probe.guest_send_us=1200000
    link.clock.probe.receipt.CopyFrom(schema.GuestClockReceipt(probe_sequence=1,
        reply_sha256=bytes([3])*32,guest_receive_us=1005000))
    link.clock.accepted_receipt.CopyFrom(link.clock.probe.receipt)
    link.clock.guest_clock_valid_until_pi_us=6400000
    return link


def manual_gate_reply():
    link=acknowledged_reply();body=link.clock
    body.vehicle.pi_session=SESSION;body.vehicle.gateway_instance=body.gateway_instance
    body.direction_gate.CopyFrom(schema.DirectionGateWitness(
        gate=schema.DirectionGateRef(gate_revision=1,stop_command_sequence=42,state=4,requested_direction=-1),
        control_generation=1,producer_role=2,calculation_sequence=1,
        plan_revision=0,phase_revision=0,segment_index=0,cusp_index=0,
        mission_id=bytes(16),mission_revision=0,grant_revision=0,
        driver_session=bytes([1])*16,driver_action_seq=3,driver_state_seq=4,
        vehicle_sequence=body.vehicle.sequence,valid_until_pi_us=6300000,
        pi_session=SESSION,device_session=body.vehicle.device_session))
    return link


def test_ack_and_original_expiry_survive_only_the_existing_send_stamp():
    value=acknowledged_reply();raw=encode(value)
    assert decode(raw,schema.DriveLink)==value
    stamped=decode(PreparedClock(value).stamp(6004000),schema.DriveLink)
    assert stamped.clock.pi_send_us==6004000
    assert stamped.clock.guest_clock_valid_until_pi_us==6400000
    assert stamped.clock.accepted_receipt==value.clock.accepted_receipt
    assert len(encode(stamped.clock.accepted_receipt))<=64


@pytest.mark.parametrize('change',[
    lambda v:setattr(v.clock.probe.receipt,'probe_sequence',2),
    lambda v:setattr(v.clock.probe.receipt,'guest_receive_us',1200001),
    lambda v:setattr(v.clock.accepted_receipt,'reply_sha256',bytes([4])*32),
    lambda v:v.clock.probe.ClearField('receipt'),
    lambda v:v.clock.ClearField('guest_clock_valid_until_pi_us'),
    lambda v:setattr(v.clock,'guest_clock_valid_until_pi_us',6002000),
])
def test_receipt_order_qualified_ack_and_original_expiry_are_explicit(change):
    value=acknowledged_reply();change(value)
    with pytest.raises(ValueError):encode(value)


def test_manual_witness_keeps_serial_stop_and_vm_calculation_sequences_distinct():
    value=manual_gate_reply();result=decode(encode(value),schema.DriveLink)
    assert result.clock.direction_gate.gate.stop_command_sequence==42
    assert result.clock.direction_gate.calculation_sequence==1
    assert len(encode(result.clock.direction_gate))<=512


def test_initial_neutral_stop_keeps_real_action_zero_without_minting_user_input():
    value=manual_gate_reply();proof=value.clock.direction_gate
    proof.calculation_sequence=0;proof.driver_action_seq=0;proof.gate.requested_direction=0
    result=decode(encode(value),schema.DriveLink).clock.direction_gate
    assert result.driver_action_seq==0 and result.driver_state_seq==4
    assert result.gate.requested_direction==0


@pytest.mark.parametrize('change',[
    lambda v:setattr(v.clock.direction_gate,'plan_revision',1),
    lambda v:setattr(v.clock.direction_gate,'driver_session',bytes(16)),
    lambda v:setattr(v.clock.direction_gate,'driver_state_seq',0),
    lambda v:setattr(v.clock.direction_gate,'producer_role',0),
    lambda v:setattr(v.clock.direction_gate,'device_session',SESSION),
    lambda v:setattr(v.clock.direction_gate,'pi_session',v.clock.vehicle.device_session),
    lambda v:setattr(v.clock.direction_gate,'vehicle_sequence',2),
    lambda v:setattr(v.clock.direction_gate.gate,'stop_command_sequence',0),
    lambda v:setattr(v.clock.direction_gate,'valid_until_pi_us',1<<63),
])
def test_ready_shape_cannot_silently_change_role_or_current_device_source(change):
    value=manual_gate_reply();change(value)
    with pytest.raises(ValueError):encode(value)


def test_receipt_duplicate_wire_fields_are_rejected_before_merging():
    value=acknowledged_reply().clock.accepted_receipt
    raw=encode(value)
    with pytest.raises(ValueError,match='duplicate'):
        decode(raw+b'\x18\x01',schema.GuestClockReceipt)
