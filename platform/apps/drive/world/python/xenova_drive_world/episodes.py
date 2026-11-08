"""선택한 Episode profile의 구조·원 facts 검사. 등록/운전 권위는 발급하지 않는다."""
import math

NIL=bytes(16)
ZERO=bytes(32)


def initial_none_core():
    """실제 새 mission issuer가 사용할 불변 초기값. 호출 자체는 NONE 승인이 아니다."""
    from .codec import schema
    return schema.EpisodeCore(state=1,episode_id=NIL,revision=0,phase=0,phase_revision=0,
        home_lane_id=0,target_lane_id=0,policy=0,blocker_position_bound_m=0,
        blocker_world_sequence=0,blocker_observation_sequence=0,birth_snapshot_id=NIL,
        basis_revision=0,basis_prefix_count=0,basis_prefix_sha256=ZERO,source_geometry_sha256=ZERO,
        origin_vehicle_sequence=0,started_pi_us=0,deadline_pi_us=0,phase_started_pi_us=0,
        phase_deadline_pi_us=0,left_baseline_ticks=0,right_baseline_ticks=0,meter_epoch=NIL,
        meter_baseline_total_ticks=0,max_travel_m=0,progress_mode=0,
        phase_goal=schema.Pose(x=0,y=0,yaw=0),stop_wait=False,stop_wait_started_pi_us=0)


def initial_none_snapshot(status):
    """명시 Pi status의 초기 NONE stamp만 구성한다. legacy/분실은 NONE이 아니다."""
    from .codec import schema,validate
    from .fingerprints import episode_core_sha256
    validate(status)
    core=initial_none_core();digest=episode_core_sha256(core)
    if status.state!=1 or status.episode_id!=NIL or status.episode_revision or status.accepted_core_sha256!=digest:
        raise ValueError('authoritative initial NONE status required')
    snapshot=schema.EpisodeSnapshot(core=core,
        progress=schema.EpisodeProgress(used_travel_ticks=0,last_progress_pi_us=0,vehicle_sequence=0,
            station_lower_m=0,remaining_upper_m=0,revision=0),
        acceptance=schema.EpisodeAcceptanceRef(registry_epoch=status.registry_epoch,
            registry_revision=status.registry_revision,request_nonce=NIL,request_sha256=ZERO,
            accepted_core_sha256=digest,request_sequence=0,origin_vm=NIL,origin_execution=NIL,
            origin_app_instance=NIL,origin_generation=0,origin_grant_revision=0))
    return validate(snapshot)


def _positive(value,*fields):
    if any(getattr(value,key)<=0 for key in fields):raise ValueError('episode positive key')


def _inactive(core):
    return core.state in (0,1) and core.phase==0


def validate_one(value):
    name=value.DESCRIPTOR.name
    if name=='EpisodeCore':
        if value.state not in (0,1,2) or value.phase>5 or value.policy>2 or value.progress_mode>2:
            raise ValueError('episode enum')
        if _inactive(value):
            for field,part in value.ListFields():
                if field.name=='state':continue
                if field.type==field.TYPE_MESSAGE:
                    if field.label==field.LABEL_REPEATED:
                        if part:raise ValueError('inactive episode hull')
                    elif any(getattr(part,p.name) for p in part.DESCRIPTOR.fields):raise ValueError('inactive episode pose')
                elif field.type==field.TYPE_BYTES:
                    if any(part):raise ValueError('inactive episode identity')
                elif part:raise ValueError('inactive episode scalar')
            return
        if value.state==0 or (value.state==1)!=(value.phase==5) or not value.phase:
            raise ValueError('episode state/phase')
        _positive(value,'revision','phase_revision','home_lane_id','target_lane_id','basis_revision',
                  'origin_vehicle_sequence','blocker_world_sequence','blocker_observation_sequence')
        if (value.home_lane_id==value.target_lane_id or value.policy not in (1,2) or
                value.progress_mode not in (1,2) or not 2<=value.basis_prefix_count<=64 or
                value.episode_id==NIL or value.birth_snapshot_id==NIL or value.meter_epoch==NIL or
                value.basis_prefix_sha256==ZERO or value.source_geometry_sha256==ZERO or
                not 0<value.max_travel_m<=6 or value.blocker_position_bound_m<0 or not 3<=len(value.blocker)<=8):
            raise ValueError('episode original geometry/budget')
        from .codec import _simple,_cross
        points=[(p.x,p.y) for p in value.blocker]
        if any(abs(x)>=1e6 or abs(y)>=1e6 for x,y in points):raise ValueError('episode hull coordinate bound')
        _simple(points,True)
        turns=[_cross(points[i],points[(i+1)%len(points)],points[(i+2)%len(points)]) for i in range(len(points))]
        if not (all(t>1e-6 for t in turns) or all(t< -1e-6 for t in turns)):raise ValueError('episode convex hull')
        # An unregistered BEGIN candidate has zero Pi times. A registered snapshot does not.
        times=(value.started_pi_us,value.deadline_pi_us,value.phase_started_pi_us,value.phase_deadline_pi_us)
        if any(times) and (not all(times) or not value.started_pi_us<=value.phase_started_pi_us<value.phase_deadline_pi_us<=value.deadline_pi_us
                or value.deadline_pi_us-value.started_pi_us>40000000
                or value.phase_deadline_pi_us-value.phase_started_pi_us>10000000):
            raise ValueError('episode original time nesting')
        if value.stop_wait!=(value.stop_wait_started_pi_us>0):raise ValueError('episode stop wait time')
    elif name=='EpisodeProgress':
        if value.remaining_upper_m<0:raise ValueError('episode remaining bound')
    elif name=='EpisodeAcceptanceRef':
        _positive(value,'registry_revision')
        if value.registry_epoch==NIL or value.accepted_core_sha256==ZERO:raise ValueError('episode registry identity')
        if value.request_nonce==NIL:
            if (value.request_sha256!=ZERO or value.request_sequence or value.origin_vm!=NIL or
                    value.origin_execution!=NIL or value.origin_app_instance!=NIL or
                    value.origin_generation or value.origin_grant_revision):raise ValueError('initial NONE reference')
        elif (value.request_sha256==ZERO or not value.request_sequence or not value.origin_generation or
                not value.origin_grant_revision or any(x==NIL for x in
                    (value.origin_vm,value.origin_execution,value.origin_app_instance))):
            raise ValueError('registered episode origin')
    elif name=='EpisodeSnapshot':
        from .fingerprints import episode_core_sha256
        core=value.core;ref=value.acceptance;progress=value.progress
        if core.state==0 or ref.accepted_core_sha256!=episode_core_sha256(core):raise ValueError('episode core SHA')
        if _inactive(core):
            if ref.request_nonce!=NIL or any(getattr(progress,f.name) for f in progress.DESCRIPTOR.fields):
                raise ValueError('initial NONE snapshot')
        else:
            _positive(core,'started_pi_us','deadline_pi_us','phase_started_pi_us','phase_deadline_pi_us')
            _positive(progress,'vehicle_sequence','revision')
            if (ref.request_nonce==NIL or progress.vehicle_sequence<core.origin_vehicle_sequence or
                    progress.last_progress_pi_us and not core.started_pi_us<=progress.last_progress_pi_us<=core.deadline_pi_us):
                raise ValueError('episode progress original window')
    elif name=='EpisodeSourceContext':
        from .codec import _source_uuid
        for field in ('mapper_boot','mapper_instance','map_epoch','frame_epoch','odom_epoch','clock_epoch'):_source_uuid(getattr(value,field))
        _positive(value,'reference_revision','world_sequence','observation_sequence')
    elif name=='EpisodeRequest':
        from .codec import _source_uuid
        for field in ('vm','execution','app_instance'):_source_uuid(getattr(value,field))
        if value.action not in (1,2,3) or value.candidate.state==0:raise ValueError('episode request action')
        _positive(value,'request_sequence','control_generation','mission_revision','expected_grant_revision',
                  'completed_guest_us','phase_started_guest_us','valid_until_pi_us',
                  'clock_probe_sequence','vehicle_sequence','plan_revision','phase_revision','calculation_sequence')
        if (value.request_nonce==NIL or value.mission_id==NIL or value.expected_registry_epoch==NIL or
                not value.expected_registry_revision or value.clock_reply_sha256==ZERO or value.plan_sha256==ZERO or
                value.phase_state_message_id==NIL or value.candidate.phase_revision!=value.phase_revision or
                value.phase_started_guest_us>value.completed_guest_us or
                (value.action==1 and not 0<value.began_guest_us<=value.phase_started_guest_us) or
                (value.action!=1 and value.began_guest_us!=0) or
                (value.scene_admission_id==NIL)!=(value.scene_revision==0) or
                value.candidate.origin_vehicle_sequence>value.vehicle_sequence or
                (value.action==3)!=(value.candidate.state==1 and value.candidate.phase==5)):
            raise ValueError('episode request context')
    elif name=='EpisodeReceipt':
        _positive(value,'request_sequence','registry_revision')
        if (value.request_nonce==NIL or value.request_sha256==ZERO or value.registry_epoch==NIL or
                value.result not in (1,2,3,4,5) or value.reason>31 or value.HasField('snapshot')!=(value.result==1)):
            raise ValueError('episode exact receipt')
        if value.HasField('snapshot'):
            ref=value.snapshot.acceptance
            if (ref.request_nonce!=value.request_nonce or ref.request_sha256!=value.request_sha256 or
                    ref.request_sequence!=value.request_sequence or ref.registry_epoch!=value.registry_epoch or
                    ref.registry_revision!=value.registry_revision):raise ValueError('episode receipt/ref mismatch')
    elif name=='EpisodeStatus':
        if value.state>2 or value.commit_state>2:raise ValueError('episode status enum')
        if value.state==0:
            if value.commit_state:raise ValueError('UNKNOWN episode commit')
        else:
            _positive(value,'registry_revision','valid_until_pi_us')
            if value.registry_epoch==NIL or value.accepted_core_sha256==ZERO:raise ValueError('episode status identity')
            if value.state==2 and (value.episode_id==NIL or not value.episode_revision):raise ValueError('ACTIVE episode key')
        if value.commit_state==2 and (value.state_message_id==NIL or value.state_sha256==ZERO or
                not value.state_revision or not value.plan_revision or not value.phase_revision):
            raise ValueError('episode admitted copy')
    elif name=='PiAbsoluteMeter':
        if value.valid and (value.meter_epoch==NIL or not value.vehicle_sequence or value.metres_per_tick<=0):
            raise ValueError('absolute meter origin/upper scale')
        if value.metres_per_tick<0:raise ValueError('absolute meter scale')
    elif name=='StopFacts':
        if value.state>3 or value.producer_role>2:raise ValueError('ordinary STOP state/role')
        if any(getattr(value,k)<0 for k in ('left_speed_bound_mps','right_speed_bound_mps','yaw_rate_bound_rps')):
            raise ValueError('ordinary STOP physical bounds')
        if not value.state:return
        from .codec import _source_uuid
        _source_uuid(value.pi_session);_source_uuid(value.device_session)
        _positive(value,'command_sequence','valid_until_pi_us')
        pi_source=all(x==NIL for x in (value.source_vm,value.source_execution,value.source_app_instance)) and not value.calculation_sequence
        if value.producer_role==0 and not pi_source:raise ValueError('Pi STOP source sentinel')
        if not pi_source and (not value.control_generation or not value.calculation_sequence or
                any(x==NIL for x in (value.source_vm,value.source_execution,value.source_app_instance))):
            raise ValueError('VM STOP source')
        if value.state>=2:
            _positive(value,'applied_report_sequence','applied_source_sample','applied_pi_lower_us','applied_pi_upper_us')
            if value.applied_pi_lower_us>value.applied_pi_upper_us:raise ValueError('STOP applied interval')
        if value.state==3:
            _positive(value,'stationary_first_sequence','stationary_last_sequence','stationary_started_pi_us','stationary_observed_pi_us')
            if (value.stationary_first_sequence>value.stationary_last_sequence or
                    not value.applied_pi_upper_us<=value.stationary_started_pi_us<value.stationary_observed_pi_us<value.valid_until_pi_us):
                raise ValueError('STOP stationary original window')
    elif name=='DriveControlFacts':
        if (value.meter.valid or value.HasField('stop') and value.stop.state) and not value.valid_until_pi_us:
            raise ValueError('control facts original expiry')


def validate_clock(reply):
    if (reply.HasField('episode_status') or reply.HasField('control_facts')) and (
            not reply.probe.HasField('supported_state_features') or not reply.probe.supported_state_features&16):
        raise ValueError('episode/control facts require declared support')
    if not reply.HasField('control_facts'):return
    facts=reply.control_facts;vehicle=reply.vehicle
    if facts.meter.vehicle_sequence!=vehicle.sequence:raise ValueError('meter/parent Vehicle exact sequence')
    if facts.meter.valid and (vehicle.quality!=3 or not vehicle.HasField('pi_session') or
            not vehicle.HasField('gateway_instance') or vehicle.gateway_instance!=reply.gateway_instance or vehicle.host_boot!=reply.host_boot):
        raise ValueError('meter/parent source')
    if facts.HasField('stop') and facts.stop.state:
        stop=facts.stop
        if (not vehicle.HasField('pi_session') or stop.pi_session!=vehicle.pi_session or
                stop.device_session!=vehicle.device_session or stop.stationary_last_sequence>vehicle.sequence or
                stop.stationary_observed_pi_us>vehicle.pi_sample_us):raise ValueError('STOP/parent current source')


def validate_state(state):
    if not state.HasField('episode'):return
    from .fingerprints import basis_prefix_sha256
    core=state.episode.core
    if core.state==2 and (not state.HasField('task_reference') or core.home_lane_id!=state.task_reference.home_lane_id or
            core.phase_revision!=state.maneuver_revision or core.basis_revision>state.task_reference.basis_revision or
            core.basis_prefix_count>len(state.task_reference.basis_prefix) or
            core.basis_prefix_sha256!=basis_prefix_sha256(state.task_reference.basis_prefix[:core.basis_prefix_count])):
        raise ValueError('episode original task/basis/phase')
