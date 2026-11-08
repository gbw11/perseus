"""원 차로 의도·basis의 State33 형식 검사. 현재 World/사용권 채택은 C 소비자의 책임이다."""
import pytest

from test_world import fixture, parking_state
from xenova_drive_world import decode, encode, schema


def task_state():
    value=parking_state()
    value.state_features=15
    value.reference_revision=2
    value.task_reference.CopyFrom(schema.TaskReferenceState(home_lane_id=2,from_lane_id=1,
        selected_lane_id=1,changing=False,basis_revision=1,vehicle='rc-test-1',
        mapper_boot=fixture.BOOT,mapper_instance=fixture.INSTANCE,clock_epoch=fixture.CLOCK))
    for i in range(64):value.task_reference.basis_prefix.add(x=i*.1,y=1.6)
    return value


def test_original_task_and_float32_prefix_roundtrip_without_making_new_clock_or_intent():
    value=task_state()
    raw=encode(value)
    result=decode(raw,schema.DriveState)
    assert result==value and result.task_reference.home_lane_id==2
    assert result.task_reference.basis_revision==1 and result.reference_revision==2
    assert len(encode(result.task_reference))<=1024 and len(raw)<=32768


@pytest.mark.parametrize('change',[
    lambda s:setattr(s,'state_features',7),
    lambda s:setattr(s,'state_features',8),
    lambda s:setattr(s,'state_features',31),
    lambda s:s.ClearField('task_reference'),
    lambda s:setattr(s,'world_features',0),
    lambda s:setattr(s.task_reference,'basis_revision',3),
    lambda s:setattr(s.task_reference,'basis_revision',0),
    lambda s:setattr(s.task_reference,'home_lane_id',0),
    lambda s:setattr(s.task_reference,'selected_lane_id',0),
    lambda s:setattr(s.task_reference,'changing',True),
    lambda s:setattr(s.task_reference,'from_lane_id',2),
    lambda s:setattr(s.task_reference,'mapper_boot','00000000-0000-0000-0000-000000000000'),
    lambda s:setattr(s.task_reference,'mapper_instance','not-a-source'),
    lambda s:s.task_reference.basis_prefix.add(x=7.,y=1.6),
    lambda s:setattr(s.task_reference.basis_prefix[3],'x',float('nan')),
])
def test_feature_intent_source_and_prefix_errors_do_not_decode_as_default_task(change):
    value=task_state();change(value)
    with pytest.raises(ValueError):encode(value)
    with pytest.raises(ValueError):decode(value.SerializeToString(),schema.DriveState)


@pytest.mark.parametrize('points',[
    [(0.,0.)],[(0.,0.),(2.,0.),(1.,0.)],
    [(0.,0.),(1.,1.),(0.,1.),(1.,0.)],[(0.,0.),(1.,0.),(0.,0.)],
])
def test_task_basis_is_an_open_finite_prefix(points):
    value=task_state();del value.task_reference.basis_prefix[:]
    for x,y in points:value.task_reference.basis_prefix.add(x=x,y=y)
    with pytest.raises(ValueError):encode(value)


def test_existing_state7_remains_a_separate_structural_profile():
    value=parking_state()
    assert not value.HasField('task_reference') and value.state_features==7
    assert decode(encode(value),schema.DriveState)==value
