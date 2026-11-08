"""불변 device snapshot의 원 meter/STOP 사실을 ClockReply에 연결한다. 권위는 발급하지 않는다."""
import math
import struct
from uuid import UUID

from xenova_drive_world import schema,decode
from .scene_authority import FEEDBACK3,check_stationary_window


NIL=bytes(16)


def uuid_bytes(value):return NIL if value is None else UUID(value).bytes


def float_upper(value):
    """양수 상한을 float32 wire로 줄일 때 rounddown하지 않는다."""
    packed=struct.pack('<f',value);rounded=struct.unpack('<f',packed)[0]
    if rounded<value:
        bits=struct.unpack('<I',packed)[0];rounded=struct.unpack('<f',struct.pack('<I',bits+1))[0]
    return rounded


def build_control_facts(runner):
    view=runner.device_view();sample=view.get('vehicle_sample');original=view.get('absolute_meter')
    if sample is None or original is None:return None
    anchor,_=original;now=int(runner._clock()*1000000)
    if (anchor.vehicle_sequence!=sample['sequence'] or anchor.host_boot!=runner.authority.host
            or any(getattr(anchor,key)!=sample.get(key) for key in ('pi_session','device_session','odom_epoch','calibration'))):return None
    maximum=getattr(runner._sink,'_vehicle_maximum_age_us',None)
    if maximum is None:
        device=getattr(runner._sink,'device',None)
        maximum=getattr(getattr(getattr(device,'odometry',None),'config',None),'maximum_sample_age_us',None)
    if maximum is None:return None
    end=min(view.get('clock_valid_until_pi_us',0),sample['pi_sample_us']-sample['clock_error_us']+maximum)
    if not sample['pi_sample_us']+sample['clock_error_us']<=now<end:return None
    value=schema.DriveControlFacts(major=1,valid_until_pi_us=end,meter=schema.PiAbsoluteMeter(
        meter_epoch=UUID(anchor.meter_epoch).bytes,total_ticks=anchor.total_ticks,metres_per_tick=anchor.metres_per_tick,
        gap_revision=anchor.gap_revision,valid=anchor.valid,vehicle_sequence=anchor.vehicle_sequence))
    source=view.get('stop_facts_source')
    if source is None:return value
    pin=source['pin'];frame=pin['frame'];witness=pin['witness'];command=witness.get('command',{})
    role={'AUTO':1,'MANUAL':2}.get(command.get('producer_role'),0)
    facts=schema.StopFacts(state=2 if pin.get('applied') else 1,pi_session=sample['pi_session'],
        device_session=sample['device_session'],command_sequence=pin['sequence'],control_generation=frame.generation,
        producer_role=role,source_vm=uuid_bytes(witness.get('vm_id')),source_execution=uuid_bytes(witness.get('execution_id')),
        source_app_instance=uuid_bytes(witness.get('agent_instance_id')),calculation_sequence=int(command.get('calculation_sequence',0)),
        counter=frame.counter,applied_report_sequence=0,applied_source_sample=0,applied_pi_lower_us=0,applied_pi_upper_us=0,
        stationary_first_sequence=0,stationary_last_sequence=0,stationary_started_pi_us=0,stationary_observed_pi_us=0,
        valid_until_pi_us=end,left_speed_bound_mps=0,right_speed_bound_mps=0,yaw_rate_bound_rps=0,
        plan_revision=int(command.get('plan_revision',0)),phase_revision=int(command.get('phase_revision',0)),
        mission_id=uuid_bytes(command.get('mission_id')),mission_revision=int(command.get('mission_revision',0)),
        grant_revision=int(command.get('grant_revision',0)),driver_session=uuid_bytes(command.get('driver_session')),
        driver_action_seq=int(command.get('driver_action_seq',0)),driver_state_seq=int(command.get('driver_state_seq',0)))
    feedback=pin.get('feedback')
    if pin.get('applied') and feedback is not None:
        report=FEEDBACK3.unpack(feedback);reported=next((row for row in source['rows'] if row['source_sample']==report[8]),None)
        if reported is not None:
            first=pin.get('first_applied_feedback')
            if first is None or pin.get('first_applied_pi_lower_us') is None:return value
            original_report=FEEDBACK3.unpack(first)
            facts.applied_report_sequence=original_report[7];facts.applied_source_sample=original_report[8]
            facts.applied_pi_lower_us=pin['first_applied_pi_lower_us'];facts.applied_pi_upper_us=pin['first_applied_pi_upper_us']
            facts.valid_until_pi_us=min(end,reported['pi_sample_us']-reported['clock_error_us']+maximum)
        else:return value
    if now<facts.valid_until_pi_us:value.stop.CopyFrom(facts)
    # 원 pin의 APPLIED와 현재 실측 window. 속도0/arrival/cusp READY를 사용하지 않는다.
    matched=runner.matched_stop()
    if matched is not None and matched.command_payload==pin['command']:
        device=getattr(runner._sink,'device',None);policy=getattr(device,'stationary_policy',None)
        if policy is not None:
            rows=[decode(raw,schema.VehicleSample) for raw in matched.vehicle_window]
            rows=[row for row in rows if row.pi_sample_us-row.clock_error_us>=facts.applied_pi_upper_us]
            if len(rows)<2:return value
            try:
                check_stationary_window(tuple(row.SerializeToString(deterministic=True) for row in rows),
                    dict(vehicle=sample['vehicle'],calibration=sample['calibration'],odom_epoch=sample['odom_epoch']),
                    runner.authority.host,runner.drive_link.instance,sample['pi_session'],sample['device_session'],
                    policy,now,rows[-1].source_sample,rows[-1].source_us)
            except ValueError:return value
            left=right=yaw=0.
            for before,after in zip(rows,rows[1:]):
                dt=(after.pi_sample_us-before.pi_sample_us-after.clock_error_us-before.clock_error_us)/1000000
                left=max(left,(abs(after.left_ticks-before.left_ticks)+1)*policy.metres_per_tick/dt+policy.unobserved_wheel_mps)
                right=max(right,(abs(after.right_ticks-before.right_ticks)+1)*policy.metres_per_tick/dt+policy.unobserved_wheel_mps)
                yaw=max(yaw,(abs(math.remainder(after.odom_pose.yaw-before.odom_pose.yaw,2*math.pi))+
                    after.heading_error_rad+before.heading_error_rad)/dt+policy.unobserved_yaw_rps)
            first,last=rows[0],rows[-1];facts.state=3
            facts.stationary_first_sequence=first.sequence;facts.stationary_last_sequence=last.sequence
            facts.stationary_started_pi_us=first.pi_sample_us+first.clock_error_us
            facts.stationary_observed_pi_us=last.pi_sample_us-last.clock_error_us
            facts.valid_until_pi_us=min(end,last.pi_sample_us-last.clock_error_us+policy.max_age_us)
            facts.left_speed_bound_mps=float_upper(left);facts.right_speed_bound_mps=float_upper(right)
            facts.yaw_rate_bound_rps=float_upper(yaw)
    if now<facts.valid_until_pi_us:value.stop.CopyFrom(facts)
    return value
