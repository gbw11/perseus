"""원 encoder별 절대 이동과 불변 anchor. 장치/clock 실측 수락은 별도다."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from xenova_gateway.absolute_meter import AbsoluteWheelMeter
from xenova_gateway.odometry import OdometryConfig,VehicleOdometry


HOST='11111111-1111-4111-8111-111111111111'
PI='22222222-2222-4222-8222-222222222222'
DEVICE='33333333-3333-4333-8333-333333333333'
ODOM='44444444-4444-4444-8444-444444444444'


def meter():
    return AbsoluteWheelMeter(SimpleNamespace(encoder_bits=32,metres_per_tick=.001,
        metres_per_tick_upper=.001,calibration='encoder-lab-1',maximum_sample_age_us=100000),HOST)


def record(owner,sequence,left,right,*,odom=ODOM):
    return owner.record(pi_session=PI,device_session=DEVICE,odom_epoch=odom,sequence=sequence,
        source_sample=sequence,source_us=sequence*20000,left=left,right=right,
        pi_sample_us=sequence*20000+1000000,clock_error_us=10)


def test_opposite_wheels_and_forward_reverse_cannot_cancel_absolute_distance():
    owner=meter();record(owner,1,0,0)
    first=record(owner,2,10,-10);second=record(owner,3,0,0)
    assert first.valid and first.total_ticks==10 and second.total_ticks==20
    assert second.total_m==pytest.approx(.02)


def test_counter_wrap_preserves_original_integer_ticks_and_immutable_anchor():
    owner=meter();record(owner,1,2**31-2,100)
    first=record(owner,2,-2**31+2,100);snapshot=owner.snapshot
    assert first.valid and first.total_ticks==4
    record(owner,3,-2**31+5,101)
    assert snapshot[0] is first and first.total_ticks==4 and owner.snapshot[0].total_ticks==7
    with pytest.raises(FrozenInstanceError):first.total_ticks=0


def test_missing_encoder_or_epoch_replacement_cannot_revalidate_old_birth_anchor():
    owner=meter();record(owner,1,0,0);birth=record(owner,2,10,10)
    missing=record(owner,4,30,30)
    assert not missing.valid and missing.meter_epoch!=birth.meter_epoch
    assert missing.gap_revision>birth.gap_revision
    current=record(owner,5,31,31)
    assert current.valid and current.total_ticks==1 and current.meter_epoch!=birth.meter_epoch
    replaced=record(owner,6,32,32,odom=PI)
    assert not replaced.valid and replaced.meter_epoch!=current.meter_epoch


def test_actual_odometry_ingress_accounts_for_samples_between_actor_observations():
    config=OdometryConfig('rc-test-1','encoder-lab-1',.001,.2,.25,10.,1000,100000,20,32,
        .001,.001,.001,.001,metres_per_tick_upper=.001)
    odometry=VehicleOdometry(config,HOST);odometry.device_session(DEVICE)
    for sequence,ticks in ((1,0),(2,100),(3,0)):
        stamp=1000000+sequence*20000
        odometry.observe_mapped(PI,DEVICE,stamp,sequence,ticks,ticks,1,0,0,stamp,stamp+10)
    # actor가 마지막 표본 하나만 읽어도 ingress는 중간 전진/후진을 모두 소유한다.
    current,history=odometry.absolute.snapshot
    assert current.valid and current.total_ticks==200 and current.total_m==pytest.approx(.2)
    assert current.vehicle_sequence==odometry.latest['sequence']
    assert len(history)==3 and odometry.latest['left_ticks']==odometry.latest['right_ticks']==0


def test_scale_upper_change_and_missing_approval_never_share_a_valid_meter_epoch():
    owner=meter();record(owner,1,0,0);old=record(owner,2,1,1)
    owner.config=SimpleNamespace(**{**vars(owner.config),'metres_per_tick_upper':.0011})
    changed=record(owner,3,2,2)
    assert not changed.valid and changed.meter_epoch!=old.meter_epoch
    owner.config=SimpleNamespace(**{**vars(owner.config),'metres_per_tick_upper':None})
    missing=record(owner,4,3,3);later=record(owner,5,4,4)
    assert not missing.valid and not later.valid
