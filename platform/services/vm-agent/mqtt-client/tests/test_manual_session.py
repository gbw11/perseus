"""Shared C/Python vectors and causal input ordering, no transport-count intent."""
import json
from pathlib import Path
import pytest
from xenova_mqtt.manual_session import parse,SessionEvidence,MovementGate

SESSION='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
def sample(seq,action,keys=(),*,generation=42,valid=True,age=0):
    return parse(dict(profile='manual-session-1',session_id=SESSION,sample_seq=str(seq),action_seq=str(action),
                      generation=generation,keys=list(keys),valid=valid,age_ms=age))

def test_shared_c_python_contract_vectors():
    path=Path(__file__).resolve().parents[5]/'docs/contracts/vectors/manual-session-1.jsonl'
    for line in path.read_text().splitlines():
        row=json.loads(line)
        if row['accepted']:parse(row['payload'])
        else:
            with pytest.raises(ValueError):parse(row['payload'])

def test_current_confirmation_is_not_a_new_action_and_duplicate_does_not_renew_age():
    evidence=SessionEvidence();held=sample(1,5,['up'])
    assert evidence.observe(held,1.) and evidence.current(1.199)==held
    assert not evidence.observe(held,1.19) and evidence.current(1.201) is None
    assert evidence.observe(sample(2,5,['up']),1.21)  # genuinely reconfirmed live session
    assert not evidence.observe(sample(3,5,[]),1.22)  # same original action cannot change keys
    assert evidence.observe(sample(3,5,[],valid=False),1.23) and evidence.current(1.23) is None

def test_neutral_latch_survives_latest_slot_replacement_but_transport_repeat_cannot_move():
    gate=MovementGate(42,SESSION,'NEUTRAL_THEN_ACTION')
    assert gate.observe(sample(1,2,['up'])) and not gate.allowed
    assert gate.observe(sample(2,3,[])) and not gate.allowed
    assert not gate.observe(sample(3,3,['up'])) and not gate.allowed
    assert gate.observe(sample(4,4,['up'])) and gate.allowed
    assert gate.observe(sample(5,4,['up'])) and gate.allowed
    assert not gate.observe(sample(6,5,['up'],generation=41))
    assert gate.observe(sample(7,5,[],valid=False)) is False and gate.blocked
    assert not gate.observe(sample(8,6,['up']))

def test_same_manual_handoff_does_not_require_release_and_press():
    gate=MovementGate(43,SESSION,'CONTINUE_SESSION')
    assert gate.observe(sample(100,5,['up'],generation=43)) and gate.allowed
    # A restarted transition cannot reuse the old transition's neutral latch.
    new=MovementGate(44,SESSION,'NEUTRAL_THEN_ACTION')
    assert new.observe(sample(101,5,['up'],generation=44)) and not new.allowed
