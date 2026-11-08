"""분류기의 typed ingress 경계. 실제 serial/장치 APPLIED 생산자는 별도 통합 시험이다."""
from types import SimpleNamespace

import pytest

from xenova_gateway.handover_evidence import HandoverEvidence


def submission(vm,generation,sequence,*,kind='DRIVE',end=2000000,device='device'):
    return dict(source=dict(vm_id=vm,app_id='autonomous'),generation=generation,command_sequence=sequence,
        frame_counter=sequence,pi_session='pi',device_session=device,output_kind=kind,
        v_mps=.2 if kind=='DRIVE' else 0.,original_expires_pi_us=end)


def applied(row,at):
    return dict(status='APPLIED',command_sequence=row['command_sequence'],command_generation=row['generation'],
        command_counter=row['frame_counter'],pi_session=row['pi_session'],device_session=row['device_session'],
        applied_pi_lower_us=None if at is None else at-10,applied_pi_upper_us=at)


def prepared():
    machine=SimpleNamespace(generation=1,active_vm='A',active_app='autonomous',_handoff_grace_until=0.)
    tracker=HandoverEvidence();tracker.transition(machine,1000000)
    original=submission('A',1,1);tracker.submission(original);tracker.feedback(applied(original,1010000),machine)
    machine.generation=2;machine.active_vm='B';machine._handoff_grace_until=2.
    tracker.transition(machine,1020000)
    return tracker,machine


def test_b_first_applied_must_be_inside_actual_original_output_budget():
    tracker,machine=prepared();fresh=submission('B',2,2,end=3000000)
    tracker.submission(fresh);tracker.feedback(applied(fresh,1030000),machine)
    assert tracker.terminal['classification']=='continuous'
    assert tracker.terminal['original']['original_expires_pi_us']==2000000
    assert tracker.terminal['first_b']['source']['vm_id']=='B'


@pytest.mark.parametrize('failure',['stop','expired','session','unmapped','no-carry'])
def test_missing_continuity_evidence_is_stop_then_resume(failure):
    tracker,machine=prepared()
    if failure=='stop':
        stop=submission('B',2,2,kind='STOP');tracker.submission(stop);tracker.feedback(applied(stop,1021000),machine)
    if failure=='no-carry':tracker.current['carry_authorized']=False
    fresh=submission('B',2,3,end=4000000,device='replacement' if failure=='session' else 'device')
    tracker.submission(fresh)
    tracker.feedback(applied(fresh,None if failure=='unmapped' else 2000000 if failure=='expired' else 1030000),machine)
    assert tracker.terminal['classification']=='stop_then_resume'
    if failure=='stop':assert tracker.terminal['reason']=='intermediate_stop_applied'


def test_late_b_is_recorded_without_relabelling_expired_handover_as_continuous():
    tracker,machine=prepared();tracker.tick(2000000)
    assert tracker.terminal['classification']=='stop_then_resume' and tracker.terminal['first_b'] is None
    fresh=submission('B',2,2,end=4000000);tracker.submission(fresh);tracker.feedback(applied(fresh,2100000),machine)
    assert tracker.terminal['first_b']['applied_pi_upper_us']==2100000
    assert tracker.terminal['classification']=='stop_then_resume'
