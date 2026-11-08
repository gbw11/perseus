"""유한 world/state 계약의 형식·기하 검증. 권위·시계·센서 수락을 발급하지 않는다."""
import math
from google.protobuf.message import DecodeError
from . import world_pb2 as schema

MAX_BYTES=32768
MAX_CELLS=4096
ROAD_PRIORITY=1
LANE_MODEL=2
PARKING_MODEL=4
LOCALIZATION_BOUNDS=8
WORLD_FEATURES=15
STATE_FEATURES=7
CELL_ROAD=8
MAX_U63=(1<<63)-1
NIL_UUID=bytes(16)
_STR64={'vehicle','configuration','calibration','primitive','reference_id','reason','device','mapper_device'}
_UUID_BYTES={'admission_id','survey_id','mission_id','scene_admission_id','operator_request_nonce','sensor_instance',
             'request_nonce','nonce','pi_host_boot','gateway_instance','owner_vm','owner_execution','app_instance'}
_REPEATED_CAPS={'DriveState.path':512,'DriveState.segments':3,'LaneModel.lanes':2,
    'LaneGeometry.centerline':32,'LaneModel.divider':32,'WorldSnapshot.parking_slots':8,
    'ParkingSlot.corners':4,'MissionExecution.selected_slot_polygon':4}
_MESSAGE_CAPS={'WorldSnapshot':32768,'DriveState':32768,'DriveLink':32768,'LaneModel':1408,
    'ParkingSlot':256,'MapLocalization':96,'SceneAdmissionRef':96,'ParkingSetMeta':64,
    'MissionGrant':1024,'MissionBudgetRequest':512,'MissionBudgetReply':1536,
    'MapperClockProbe':1024,'MapperClockReply':1024,'SurveySummary':288,'SceneGrant':1024,
    'TofObservation':1024}


def _bytes_bound(field):
    name=field.name
    if name in _UUID_BYTES:return 16,True
    if name.endswith('_sha256') or name=='primitive':return 32,True
    return {'occupancy':(4096,False),'cell_flags':(4096,False),'age_le32':(16384,False),
        'point_state':(32,False),'crossing':(31,False),'divider_state':(32,False),
        'distance_mm_le':(128,True),'status':(64,True),'quality':(64,True),
        'drivable_permission':(512,False)}.get(name,(MAX_BYTES,False))

def _repeated(field):
    # 6.x deprecates label access; 3.20 remains the fixed generator/test reader.
    value=getattr(field,'is_repeated',None)
    return field.label==field.LABEL_REPEATED if value is None else value

def _finite(message):
    cap=_MESSAGE_CAPS.get(message.DESCRIPTOR.name,MAX_BYTES)
    if message.ByteSize()>cap:raise ValueError('message size cap '+message.DESCRIPTOR.name)
    for field,value in message.ListFields():
        values=value if _repeated(field) else (value,)
        if _repeated(field) and len(value)>_REPEATED_CAPS.get(field.containing_type.name+'.'+field.name,64):
            raise ValueError('repeated field cap '+field.name)
        if field.type in (field.TYPE_FLOAT,field.TYPE_DOUBLE):
            if not all(math.isfinite(item) for item in values):raise ValueError('nonfinite field '+field.name)
        elif field.type==field.TYPE_UINT64:
            if any(item>MAX_U63 for item in values):raise ValueError('semantic uint64 range '+field.name)
        elif field.type==field.TYPE_STRING:
            limit=64 if field.name in _STR64 else 36
            for item in values:
                if not item or len(item.encode('utf-8'))>limit or any(ord(c)<33 or ord(c)>126 for c in item):raise ValueError('bounded identifier '+field.name)
        elif field.type==field.TYPE_BYTES:
            limit,exact=_bytes_bound(field)
            if any(len(item)>limit or exact and len(item)!=limit for item in values):raise ValueError('bounded bytes '+field.name)
        elif field.type==field.TYPE_MESSAGE:
            for item in values:_finite(item)


def _positive(message,*names):
    if any(getattr(message,name)<=0 for name in names):raise ValueError('positive identity/revision required')


def _nonnegative(message,*names):
    if any(getattr(message,name)<0 for name in names):raise ValueError('negative bound or distance')


def _scene_pair(message,revision='scene_admission_revision'):
    if (message.scene_admission_id==NIL_UUID)!=(getattr(message,revision)==0):raise ValueError('scene sentinel pair')


def _xy(point):return point.x,point.y
def _cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def _intersects(a,b,c,d):
    values=(_cross(a,b,c),_cross(a,b,d),_cross(c,d,a),_cross(c,d,b))
    if values[0]*values[1]<0 and values[2]*values[3]<0:return True
    return any(abs(value)<1e-6 and min(p[0],q[0])-1e-6<=r[0]<=max(p[0],q[0])+1e-6
        and min(p[1],q[1])-1e-6<=r[1]<=max(p[1],q[1])+1e-6
        for value,p,q,r in ((values[0],a,b,c),(values[1],a,b,d),(values[2],c,d,a),(values[3],c,d,b)))


def _simple(points,closed=False):
    edges=list(zip(points,points[1:]+points[:1] if closed else points[1:]))
    if any(math.hypot(a[0]-b[0],a[1]-b[1])<1e-6 for a,b in edges):raise ValueError('degenerate geometry edge')
    for i,(a,b) in enumerate(edges):
        for j in range(i+2,len(edges)):
            if closed and i==0 and j==len(edges)-1:continue
            if _intersects(a,b,*edges[j]):raise ValueError('self-intersecting geometry')


def _open_basis(points):
    _simple(points)
    if math.dist(points[0],points[-1])<1e-6:raise ValueError('closed road basis')
    for a,b,c in zip(points,points[1:],points[2:]):
        if abs(_cross(a,b,c))<1e-6 and (b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1])<0:
            raise ValueError('adjacent road basis retraces a station')


def _convex(points):
    if len(points)!=4:raise ValueError('four corners required')
    _simple(points,True)
    turns=[_cross(points[i],points[(i+1)%4],points[(i+2)%4]) for i in range(4)]
    if not (all(value>1e-6 for value in turns) or all(value< -1e-6 for value in turns)):
        raise ValueError('slot polygon must be strictly convex')


def _lane_model(model):
    if len(model.lanes)!=2 or not 2<=len(model.divider)<=32:raise ValueError('two lanes with common stations required')
    _positive(model,'revision')
    _nonnegative(model,'divider_position_bound_m','divider_heading_bound_rad')
    n=len(model.divider)
    if len(model.crossing)!=n-1 or len(model.divider_state)!=n or any(v>1 for v in model.crossing+model.divider_state):
        raise ValueError('divider state/crossing')
    divider=[_xy(p) for p in model.divider]
    _simple(divider)
    a,b=model.lanes
    if not a.lane_id or not b.lane_id or a.lane_id==b.lane_id or a.adjacent_lane_id!=b.lane_id or b.adjacent_lane_id!=a.lane_id:
        raise ValueError('lane adjacency identity')
    sides=[]
    for lane in model.lanes:
        _positive(lane,'revision')
        _nonnegative(lane,'position_bound_m','heading_bound_rad')
        if lane.travel_direction!=1 or len(lane.centerline)!=n or len(lane.point_state)!=n or any(v>1 for v in lane.point_state):
            raise ValueError('lane stations/state/direction')
        center=[_xy(p) for p in lane.centerline]
        outer=[(2*p[0]-d[0],2*p[1]-d[1]) for p,d in zip(center,divider)]
        _simple(center)
        _simple(divider+outer[::-1],True)
        sign=0
        for i,p in enumerate(center):
            index=min(i,n-2)
            side=_cross(divider[index],divider[index+1],p)
            current=1 if side>1e-6 else -1 if side< -1e-6 else 0
            if not current or sign and current!=sign:raise ValueError('lane divider side')
            sign=current
            if i:
                delta=(divider[i][0]-divider[i-1][0],divider[i][1]-divider[i-1][1])
                if (p[0]-center[i-1][0])*delta[0]+(p[1]-center[i-1][1])*delta[1]<=0:
                    raise ValueError('lane station order')
                error=2*lane.position_bound_m+model.divider_position_bound_m
                if math.hypot(outer[i][0]-outer[i-1][0],outer[i][1]-outer[i-1][1])<=2*error:
                    raise ValueError('unbounded reconstructed lane heading')
        sides.append(sign)
    if sides[0]==sides[1]:raise ValueError('lanes must straddle divider')


def _slot(slot):
    _positive(slot,'slot_id','geometry_revision','occupancy_revision')
    _nonnegative(slot,'position_bound_m','heading_bound_rad','usable_width_m','usable_depth_m')
    if (slot.geometry_state>2 or slot.occupancy_state>2 or slot.occupancy_reason>7
            or slot.source_kinds&~7 or slot.entry_edge>3 or slot.goal_mask&~3):raise ValueError('parking enum/mask')
    if (slot.HasField('reverse_goal')!=bool(slot.goal_mask&1)
            or slot.HasField('forward_goal')!=bool(slot.goal_mask&2)):raise ValueError('goal presence/mask')
    if slot.geometry_state==1:
        _convex([_xy(p) for p in slot.corners])
        if slot.usable_width_m<=0 or slot.usable_depth_m<=0:raise ValueError('positive usable slot dimensions')
    elif slot.goal_mask or slot.occupancy_state!=0:raise ValueError('partial/ambiguous slot cannot have goal or clearance')


def _parking_set(value):
    _positive(value,'revision')
    if value.state>2 or value.track_count_in_roi>64 or value.capacity_limited and value.complete:
        raise ValueError('parking set completeness')


def _mission(value,grant=False):
    if value.kind not in (0,1,2) or value.strategy not in (0,1,2) or value.correction_attempts_used>2:
        raise ValueError('mission kind/strategy/correction')
    _scene_pair(value,'scene_revision' if grant else 'scene_admission_revision')
    _nonnegative(value,'execution_travel_m','search_travel_m')
    if grant:
        _nonnegative(value,'max_execution_travel_m','max_search_travel_m')
        if value.max_execution_travel_m>5 or value.max_search_travel_m>10:raise ValueError('mission travel budget cap')
    if value.kind==0:
        if value.mission_id!=NIL_UUID or value.mission_revision or grant and value.admitted:raise ValueError('NONE mission sentinel')
    else:
        if value.mission_id==NIL_UUID:raise ValueError('active mission UUID')
        _positive(value,'mission_revision','grant_revision')
    zeros=('mission_deadline_pi_us','execution_deadline_pi_us','correction_attempts_used',
           'execution_travel_m','search_travel_m','search_deadline_pi_us')
    if value.kind!=2:
        if value.strategy or any(getattr(value,name) for name in zeros):raise ValueError('nonparking budget sentinel')
        if grant and (value.max_execution_travel_m or value.max_search_travel_m or value.HasField('locked_goal_sha256')):
            raise ValueError('nonparking grant budget')
    else:
        _positive(value,'mission_deadline_pi_us','search_deadline_pi_us')
        if value.search_deadline_pi_us>value.mission_deadline_pi_us or value.execution_deadline_pi_us>value.mission_deadline_pi_us:
            raise ValueError('mission deadline nesting')
        if grant and (value.HasField('locked_goal_sha256')!=bool(value.execution_deadline_pi_us)):
            raise ValueError('locked goal/execution deadline')
    if grant:return
    if value.result>3 or value.phase not in ((0,) if value.kind==0 else (0,10) if value.kind==1 else range(10)):
        raise ValueError('mission phase/result')
    if value.result==1 and (value.kind!=2 or value.phase!=0):raise ValueError('PARKED must be parking HOLD')
    names=('slot_id','slot_revision','goal','goal_revision','goal_position_bound_m','goal_heading_bound_rad')
    present=[value.HasField(name) for name in names]
    locked=value.kind==2 and value.phase in (3,4,5,6,7)
    if value.kind!=2 and (any(present) or value.selected_slot_polygon):raise ValueError('nonparking goal sentinel')
    if locked or any(present) or value.selected_slot_polygon:
        if not all(present) or len(value.selected_slot_polygon)!=4:raise ValueError('complete locked goal required')
        _positive(value,'slot_id','slot_revision','goal_revision')
        _nonnegative(value,'goal_position_bound_m','goal_heading_bound_rad')
        _convex([_xy(p) for p in value.selected_slot_polygon])


def _segments(state):
    segments=state.segments
    if not segments:
        if state.mission.active_segment or any(p.velocity_mps for p in state.path):raise ValueError('moving path requires gear segments')
        return
    if state.mission.active_segment>=len(segments):raise ValueError('active segment index')
    boundaries=set()
    for i,segment in enumerate(segments):
        if (segment.signed_direction not in (-1,1) or segment.last_index>=len(state.path)
                or segment.last_index<=segment.first_index):raise ValueError('gear segment range')
        if i==0 and segment.first_index!=0 or i and segment.first_index!=segments[i-1].last_index+1:
            raise ValueError('gear segment partition')
        if state.mission.kind==1 and segment.signed_direction!=1:raise ValueError('LANE requires forward gear')
        travel=0.
        for j in range(segment.first_index,segment.last_index+1):
            point=state.path[j]
            if point.velocity_mps*segment.signed_direction<0:raise ValueError('path velocity/gear sign')
            if j>segment.first_index:
                previous=state.path[j-1].pose
                travel+=math.hypot(point.pose.x-previous.x,point.pose.y-previous.y)
        if travel<=0:raise ValueError('positive segment travel required')
        if i:
            previous=segments[i-1]
            before,after=state.path[previous.last_index],state.path[segment.first_index]
            if (segment.signed_direction==previous.signed_direction or before.pose!=after.pose
                    or before.velocity_mps or after.velocity_mps):raise ValueError('stationary cusp pair')
            boundaries.add(segment.first_index)
    if segments[-1].last_index!=len(state.path)-1:raise ValueError('gear segments must cover path')
    seen={}
    for i,point in enumerate(state.path):
        key=(point.pose.x,point.pose.y,point.pose.yaw)
        if key in seen and not (i in boundaries and seen[key]==i-1):raise ValueError('duplicate pose outside cusp')
        seen[key]=i

def validate(message):
    if not message.IsInitialized():raise ValueError('required fields missing: '+','.join(message.FindInitializationErrors()))
    _finite(message)
    _validate_tree(message)
    return message


def _validate_tree(message):
    for field,value in message.ListFields():
        if field.type==field.TYPE_MESSAGE:
            for child in value if _repeated(field) else (value,):_validate_tree(child)
    _validate_one(message)


def _validate_one(message):
    name=message.DESCRIPTOR.name
    major=2 if name in ('DriveState','DriveLink') else 1
    if getattr(message,'major',major)!=major:raise ValueError('unsupported schema major')
    if isinstance(message,schema.WorldSnapshot):
        n=message.width*message.height
        if not 2<=message.width<=64 or not 2<=message.height<=64 or n>MAX_CELLS:raise ValueError('grid dimensions')
        if (len(message.occupancy),len(message.cell_flags),len(message.age_le32))!=(n,n,n*4):raise ValueError('grid arrays incomplete')
        if not message.complete or message.truncated or message.required_features not in (0,1,11,15) or message.inflation_policy:raise ValueError('unsupported map features')
        if not 2<=len(message.reference)<=64 or len(message.left_boundary)!=len(message.reference) or len(message.right_boundary)!=len(message.reference):raise ValueError('road arrays')
        if message.resolution_m<.001 or message.resolution_m>10:raise ValueError('resolution')
        if message.observed_us>message.completed_us or message.completed_us>message.published_us:raise ValueError('source time ordering')
        allowed=15 if message.required_features&ROAD_PRIORITY else 7
        if any(v>100 and v!=255 for v in message.occupancy) or any(v&~allowed for v in message.cell_flags):raise ValueError('cell encoding')
        if message.required_features&ROAD_PRIORITY and not any(v&CELL_ROAD for v in message.cell_flags):raise ValueError('missing preferred road')
        new=message.required_features in (11,15)
        if new and message.minor!=2:raise ValueError('world minor2 required')
        if new:
            _open_basis([_xy(p) for p in message.reference])
            _simple([_xy(p) for p in message.left_boundary]+[_xy(p) for p in reversed(message.right_boundary)],True)
        for field,required in (('lane_model',bool(message.required_features&LANE_MODEL)),
                ('localization',bool(message.required_features&LOCALIZATION_BOUNDS)),
                ('parking_set',bool(message.required_features&PARKING_MODEL)),('drivable_permission',new)):
            if message.HasField(field)!=required:raise ValueError('feature presence '+field)
        if message.parking_slots and not message.required_features&PARKING_MODEL:raise ValueError('parking feature missing')
        if new:
            if len(message.drivable_permission)!=(n+7)//8 or n%8 and message.drivable_permission[-1]>>(n%8):
                raise ValueError('whole-cell permission mask')
            if message.localization.observed_mapper_us>message.completed_us:raise ValueError('localization time ordering')
            if message.HasField('parking_set'):
                count=message.parking_set.track_count_in_roi
                if count<len(message.parking_slots) or message.parking_set.complete and count!=len(message.parking_slots):
                    raise ValueError('parking set inventory')
            if len({slot.slot_id for slot in message.parking_slots})!=len(message.parking_slots):raise ValueError('duplicate slot ID')
            if message.HasField('scene') and message.scene.policy!=1:raise ValueError('live-only scene must be absent')
            retained=any(occupancy<70 and flags&3==3 and not flags&4
                         for occupancy,flags in zip(message.occupancy,message.cell_flags))
            retained|=any(slot.occupancy_state==2 and slot.source_kinds&4 for slot in message.parking_slots)
            if retained and not message.HasField('scene'):raise ValueError('retained clearance requires scene reference')
        elif message.HasField('scene'):raise ValueError('scene requires new world profile')
    elif isinstance(message,schema.DriveState):
        if message.world_features not in (0,1,11,15):raise ValueError('unsupported state world features')
        if not message.HasField('state_features') or message.state_features!=STATE_FEATURES or not message.HasField('mission'):
            raise ValueError('state features/mission required')
        if not message.complete or not 2<=len(message.path)<=512 or message.tracker_index>=len(message.path):raise ValueError('incomplete path state')
        if message.direction not in (-1,0,1) or message.maneuver_phase>2:raise ValueError('maneuver')
        if message.HasField('merge_passed_reference_revision') and (not message.direction or
                not message.merge_passed_reference_revision or message.merge_passed_reference_revision!=message.reference_revision):
            raise ValueError('merge passage reference')
        _segments(message)
        metadata=schema.DriveState();metadata.CopyFrom(message)
        for field in message.DESCRIPTOR.fields:
            if field.number<29:metadata.ClearField(field.name)
        if len(metadata.SerializePartialToString(deterministic=True))>4096:raise ValueError('state metadata cap')
    elif isinstance(message,schema.DriveLink):
        kind=message.WhichOneof('payload')
        if kind is None:raise ValueError('missing link payload')
        inner=getattr(message,kind)
        if kind=='applied' and (inner.rebuilt==inner.HasField('copy_message_id') or
             (inner.applied and (not inner.calculation_sequence or not inner.vehicle_sequence or not inner.maneuver_revision))):raise ValueError('application provenance')
        if kind=='applied':
            import re
            if not inner.application_sequence:raise ValueError('application sequence')
            if inner.HasField('reason') and not re.fullmatch('[A-Z][A-Z0-9_]{1,63}',inner.reason):raise ValueError('application reason code')
    elif isinstance(message,schema.VehicleSample):
        if message.quality!=3 or not message.sequence or not message.source_sample:raise ValueError('vehicle sample quality')
        if message.position_error_m<0 or message.heading_error_rad<0 or (message.HasField('steering_error_rad') and message.steering_error_rad<0):raise ValueError('vehicle uncertainty')
    elif name=='LaneGeometry':
        _positive(message,'lane_id','adjacent_lane_id','revision')
        _nonnegative(message,'position_bound_m','heading_bound_rad')
        if (message.travel_direction!=1 or not 2<=len(message.centerline)<=32
                or len(message.point_state)!=len(message.centerline) or any(value>1 for value in message.point_state)):
            raise ValueError('lane station/state/direction')
        _simple([_xy(p) for p in message.centerline])
    elif name=='LaneModel':_lane_model(message)
    elif name=='ParkingSlot':_slot(message)
    elif name=='ParkingSetMeta':_parking_set(message)
    elif name=='MapLocalization':
        _positive(message,'registration_revision','observed_mapper_us')
        _nonnegative(message,'anchor_position_bound_m','anchor_heading_bound_rad','geometry_position_bound_m',
            'geometry_heading_bound_rad','position_drift_bound_mps','heading_drift_bound_rps')
        if message.quality not in (0,1) or message.quality==1 and not message.valid_for_us:raise ValueError('localization quality/lease')
    elif name=='SceneAdmissionRef':
        _positive(message,'admission_revision','survey_revision')
        if message.policy not in (0,1) or not message.policy and message.allow_restore_after_mapper_restart:raise ValueError('scene policy')
    elif name=='MissionExecution':_mission(message)
    elif name=='MissionGrant':
        _positive(message,'control_generation','issued_pi_us')
        _mission(message,True)
    elif name=='DirectionGateRef':
        if message.state>4 or message.requested_direction not in (-1,0,1):raise ValueError('direction gate enum')
        if message.state and (not message.gate_revision or not message.stop_command_sequence):raise ValueError('direction gate provenance')
    elif name=='GearSegment':
        if message.signed_direction not in (-1,1) or not message.first_index<message.last_index<512:
            raise ValueError('gear segment range/direction')
    elif name=='MissionBudgetRequest':
        _positive(message,'mission_revision','control_generation','expected_grant_revision','slot_id',
            'slot_revision','goal_revision','plan_revision','completed_guest_us','valid_until_pi_us')
        _scene_pair(message,'scene_revision')
        if message.action not in (1,2) or message.mission_id==NIL_UUID:raise ValueError('budget request action/mission')
    elif name=='MissionBudgetReply':
        if (message.result not in (1,2,3,4,5) or message.reason>10 or message.HasField('grant')!=(message.result==1)
                or message.HasField('grant') and message.grant.kind!=2):
            raise ValueError('budget reply result/grant')
    elif name=='MapperClockProbe':
        _positive(message,'probe_seq','pi_send_us')
        if message.HasField('mapper_trust'):
            trust=message.mapper_trust
            if not message.admission_revision or trust.vehicle!=message.vehicle:
                raise ValueError('returned mapper trust lacks original admitted vehicle/source')
            for expected,actual in (('expected_mapper_boot','mapper_boot'),
                    ('expected_mapper_instance','mapper_instance'),('expected_clock_epoch','clock_epoch')):
                if message.HasField(expected) and getattr(message,expected)!=getattr(trust,actual):
                    raise ValueError('returned mapper trust differs from expected peer')
    elif name=='WorldTrust':
        if (message.lower_offset_us>message.upper_offset_us or not message.max_error_us
                or message.upper_offset_us-message.lower_offset_us>message.max_error_us):
            raise ValueError('mapper trust offset interval exceeds original error budget')
        _positive(message,'expires_pi_us')
    elif name=='MapperClockReply':
        _positive(message,'probe_seq','pi_send_us','j2','j3')
        if message.j2>message.j3:raise ValueError('mapper clock order')
    elif name=='ClockReply':
        if message.pi_receive_us>message.pi_send_us:raise ValueError('Pi clock order')
    elif name=='SurveySummary':
        _nonnegative(message,'anchor_position_bound_m','anchor_heading_bound_rad')
        if (message.status>4 or message.observed_slot_count>64 or message.observation_station_count>24
                or message.required_sensor_mask&~31 or message.valid_sensor_mask&~31
                or message.evidence_flags&~63 or message.failure_flags&~255):raise ValueError('survey enum/mask/count')
        if message.status in (1,2,4):
            flags=63 if message.status==4 else 31
            _positive(message,'survey_revision','world_sequence','evidence_sequence','started_peer_us',
                'last_observed_peer_us','completed_peer_us','hazard_revision','alignment_revision','observation_station_count')
            if (message.evidence_flags&flags!=flags or message.failure_flags or not message.required_sensor_mask
                    or message.required_sensor_mask&message.valid_sensor_mask!=message.required_sensor_mask
                    or not message.started_peer_us<=message.last_observed_peer_us<=message.completed_peer_us):
                raise ValueError('incomplete frozen survey evidence')
    elif name=='SceneGrant':
        _positive(message,'source_admission_revision','issued_pi_us','valid_until_pi_us','survey_deadline_pi_us','lifecycle_revision')
        if (message.scene.policy!=1 or message.lifecycle_state>4 or message.purpose>1 or message.max_observation_stations!=24
                or not message.issued_pi_us<message.valid_until_pi_us<=message.issued_pi_us+14_400_000_000
                or message.survey_deadline_pi_us>message.valid_until_pi_us):raise ValueError('scene lifecycle/time')
        if (message.HasField('frozen_evidence_sha256')!=bool(message.frozen_survey_revision)
                or not message.HasField('frozen_evidence_sha256') and message.frozen_hazard_revision
                or message.HasField('restore_checkpoint_sha256')!=(message.purpose==1)
                or message.lifecycle_state==2 and not message.HasField('frozen_evidence_sha256')):
            raise ValueError('scene frozen/restore evidence presence')
    elif name=='TofObservation':
        _positive(message,'source_sample','source_us','pi_sample_us','sensor_config_revision','clock_seq','sensor_generation')
        if (not message.complete or message.sensor_id not in (0,1) or message.rows!=8 or message.cols!=8
                or message.frame_status&~7 or any(value>7 for value in message.status)
                or any(value&~31 for value in message.quality)
                or len(message.calibration)!=64 or any(c not in '0123456789abcdefABCDEF' for c in message.calibration)):
            raise ValueError('ToF bounded frame semantics')

def encode(message):
    validate(message)
    data=message.SerializeToString(deterministic=True)
    if len(data)>MAX_BYTES:raise ValueError('wire size cap')
    return data

def decode(data,message_type=schema.WorldSnapshot):
    if not isinstance(data,bytes) or len(data)>MAX_BYTES:raise ValueError('bounded bytes required')
    _audit(data,message_type.DESCRIPTOR)
    value=message_type()
    try:value.ParseFromString(data)
    except DecodeError as error:raise ValueError('invalid protobuf') from error
    # Unknown optional fields are not part of this reader's meaning. Do not
    # retain them in a duplicate-copy fingerprint or relay unexamined bytes.
    value.DiscardUnknownFields()
    return validate(value)


def _audit(data,descriptor,depth=0):
    """Reject duplicate singular/oneof and nested ambiguity before protobuf's
    last-one-wins merge. One bounded lexical pass, no additional source read."""
    if depth>8 or len(data)>_MESSAGE_CAPS.get(descriptor.name,MAX_BYTES):raise ValueError('nested message cap')
    at=0;seen=set();unions=set();counts={};metadata=0
    def varint():
        nonlocal at
        number=0
        for i in range(10):
            if at>=len(data):raise ValueError('partial varint')
            byte=data[at];at+=1
            if i==9 and byte&254:raise ValueError('overflow varint')
            number|=(byte&127)<<(7*i)
            if not byte&128:return number
        raise ValueError('overflow varint')
    while at<len(data):
        start=at
        tag=varint();fieldno=tag>>3;kind=tag&7
        if not 0<fieldno<=0x1fffffff:raise ValueError('field number')
        field=descriptor.fields_by_number.get(fieldno)
        if field is not None and _repeated(field):
            limit=_REPEATED_CAPS.get(descriptor.name+'.'+field.name,64)
            counts[fieldno]=counts.get(fieldno,0)+1
            if counts[fieldno]>limit:raise ValueError('repeated field cap '+field.name)
        if field is not None and not _repeated(field):
            if fieldno in seen:raise ValueError('duplicate singular field')
            seen.add(fieldno)
            if field.containing_oneof:
                name=field.containing_oneof.name
                if name in unions:raise ValueError('multiple oneof fields')
                unions.add(name)
        if field is not None:
            expected=2 if field.type in (field.TYPE_STRING,field.TYPE_BYTES,field.TYPE_MESSAGE) else 1 if field.type in (field.TYPE_DOUBLE,field.TYPE_FIXED64,field.TYPE_SFIXED64) else 5 if field.type in (field.TYPE_FLOAT,field.TYPE_FIXED32,field.TYPE_SFIXED32) else 0
            if kind!=expected:raise ValueError('field wire type '+field.name)
        if kind==0:
            value=varint()
            if field is not None and (field.type==field.TYPE_UINT64 and value>MAX_U63
                    or field.type in (field.TYPE_UINT32,field.TYPE_SINT32) and value>0xffffffff
                    or field.type==field.TYPE_BOOL and value>1):raise ValueError('scalar wire range')
            if descriptor.name=='DriveState' and fieldno>=29:
                metadata+=at-start
                if metadata>4096:raise ValueError('state metadata cap')
            continue
        if kind==2:size=varint()
        elif kind==1:size=8
        elif kind==5:size=4
        else:raise ValueError('wire type')
        if size>len(data)-at:raise ValueError('partial field')
        if field is not None and field.type==field.TYPE_STRING:
            limit=64 if field.name in _STR64 else 36
            if kind!=2 or size>limit:raise ValueError('string cap '+field.name)
        if field is not None and field.type==field.TYPE_BYTES:
            limit,exact=_bytes_bound(field)
            if size>limit or exact and size!=limit:raise ValueError('bytes cap '+field.name)
        if field is not None and field.type==field.TYPE_MESSAGE:
            if kind!=2:raise ValueError('message wire type')
            _audit(data[at:at+size],field.message_type,depth+1)
        at+=size
        if descriptor.name=='DriveState' and fieldno>=29:
            metadata+=at-start
            if metadata>4096:raise ValueError('state metadata cap')
