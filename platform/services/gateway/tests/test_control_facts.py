"""실제 PTY encoder/STOP/APPLIED ingress에서 ordinary facts를 만든다. 실물 ESP32 시험은 아니다."""
import time
from types import SimpleNamespace

from xenova_drive_world import schema,validate
from xenova_gateway.control_facts import build_control_facts
from xenova_gateway.frame import StopFrame
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from test_feedback import wait
from test_feedback3_adapter import link,source,GATEWAY
from test_verified import HOST


def runtime(owner):
    runner=SimpleNamespace(_sink=owner,_clock=time.monotonic,authority=SimpleNamespace(host=HOST),
        machine=SimpleNamespace(generation=7),drive_link=SimpleNamespace(instance=GATEWAY),
        device_view=owner.device_view)
    runner.matched_stop=lambda:VerifiedGatewayRunner.matched_stop(runner)
    return runner


def stationary(runner):
    value=build_control_facts(runner)
    return value if value is not None and value.HasField('stop') and value.stop.state==3 else None


def test_actual_stop_facts_preserve_first_apply_and_refresh_only_measured_window(link):
    owner,sink,_=link;runner=runtime(owner)
    receipt=owner.write(StopFrame(7,0,1,witness=source()),time.monotonic())
    wait(lambda:stationary(runner) is not None)
    first=stationary(runner);validate(first)
    assert first.meter.valid and first.meter.metres_per_tick==.000001
    stop=first.stop;original=(stop.applied_report_sequence,stop.applied_source_sample,
        stop.applied_pi_lower_us,stop.applied_pi_upper_us)
    assert stop.command_sequence==receipt.metadata['command_sequence'] and stop.producer_role==1
    assert stop.calculation_sequence==1 and stop.plan_revision==stop.phase_revision==1
    assert stop.applied_pi_upper_us<=stop.stationary_started_pi_us<stop.stationary_observed_pi_us
    repeated=owner.write(StopFrame(7,1,1,witness=source(calc=2)),time.monotonic())
    assert repeated.metadata['command_sequence']==stop.command_sequence
    wait(lambda:stationary(runner) is not None and stationary(runner).stop.stationary_observed_pi_us>stop.stationary_observed_pi_us)
    later=stationary(runner);validate(later)
    assert (later.stop.applied_report_sequence,later.stop.applied_source_sample,
        later.stop.applied_pi_lower_us,later.stop.applied_pi_upper_us)==original
    assert later.stop.calculation_sequence==1  # repeated STOP did not re-label original app source
    assert later.stop.stationary_last_sequence>stop.stationary_last_sequence
    assert later.stop.left_speed_bound_mps>0 and later.stop.right_speed_bound_mps>0
    assert not hasattr(later.stop,'ready')


def test_speed_zero_without_current_matched_stop_is_not_stationary(link):
    owner,_,_=link;runner=runtime(owner)
    owner.write(StopFrame(7,0,1,witness=source()),time.monotonic())
    wait(lambda:stationary(runner) is not None)
    runner.matched_stop=lambda:None  # Explicit missing input, not a replacement proof.
    value=build_control_facts(runner);validate(value)
    assert value.stop.state==2 and not value.stop.stationary_first_sequence
    assert value.stop.applied_report_sequence>0


def test_meter_is_bound_to_exact_parent_vehicle_sequence(link):
    owner,_,_=link;runner=runtime(owner)
    view=owner.device_view();view={**view,'vehicle_sample':{**view['vehicle_sample']}}
    view['vehicle_sample']['sequence']+=1
    runner.device_view=lambda:view
    assert build_control_facts(runner) is None
