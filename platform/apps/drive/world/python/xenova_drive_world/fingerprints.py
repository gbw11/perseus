"""C와 같은 유한 hash 범위. fingerprint는 현재 권한·기하 수락을 대신하지 않는다."""
from hashlib import sha256
import math
import struct


def _canonical(message):
    from .codec import validate
    validate(message)
    clone=type(message)();clone.CopyFrom(message);clone.DiscardUnknownFields()
    return clone.SerializeToString(deterministic=True)


def episode_core_sha256(core):
    return sha256(b'XNVECORE1\0'+_canonical(core)).digest()


def episode_source_sha256(source):
    _canonical(source)
    clone=type(source)();clone.CopyFrom(source);clone.DiscardUnknownFields()
    clone.reference_revision=clone.world_sequence=clone.observation_sequence=0
    return sha256(b'XNVESRC1\0'+clone.SerializeToString(deterministic=True)).digest()


def episode_request_sha256(raw_body):
    from .codec import decode,schema
    if type(raw_body) is not bytes:raise ValueError('original request body bytes required')
    decode(raw_body,schema.EpisodeRequest)
    return sha256(raw_body).digest()


def _request_body(link_wire,name,number):
    from .codec import decode,schema
    if type(link_wire) is not bytes:raise ValueError('original DriveLink bytes required')
    link=decode(link_wire,schema.DriveLink)
    if link.WhichOneof('payload')!=name:raise ValueError('request link required')
    at=0
    def varint():
        nonlocal at
        value=0;shift=0
        while True:
            byte=link_wire[at];at+=1;value|=(byte&127)<<shift
            if not byte&128:return value
            shift+=7
    while at<len(link_wire):
        tag=varint();kind=tag&7
        if kind==0:varint();continue
        length=varint() if kind==2 else 8 if kind==1 else 4
        if tag>>3==number:return link_wire[at:at+length]
        at+=length
    raise ValueError('request body absent')


def episode_request_body(link_wire):return _request_body(link_wire,'episode_request',11)


def budget_request_body(link_wire):return _request_body(link_wire,'budget_request',8)


class _Writer:
    def __init__(self,domain):self.hash=sha256(domain)
    def number(self,fmt,value):self.hash.update(struct.pack('<'+fmt,value))
    def text(self,text):
        raw=text.encode('ascii')
        if not 0<len(raw)<=64 or any(b<33 or b>126 for b in raw):raise ValueError('fingerprint identifier')
        self.number('H',len(raw));self.hash.update(raw)
    def scalar(self,value):
        if not math.isfinite(value):raise ValueError('fingerprint nonfinite')
        self.number('f',value)
    def point(self,point):self.scalar(point.x);self.scalar(point.y)
    def pose(self,pose):self.point(pose);self.scalar(pose.yaw)


def basis_prefix_sha256(points):
    if not 2<=len(points)<=64:raise ValueError('basis prefix count')
    writer=_Writer(b'XNVBASIS1\0');writer.number('B',len(points))
    for point in points:writer.point(point)
    return writer.hash.digest()


def plan_sha256(state):
    if not 2<=len(state.path)<=512 or len(state.segments)>3:raise ValueError('plan fingerprint count')
    writer=_Writer(b'XNVPLAN1\0');writer.number('I',state.world_features)
    for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive','reference_id'):
        writer.text(getattr(state,key))
    writer.number('Q',state.task_reference.basis_revision if state.HasField('task_reference') else 0)
    for key in ('path_revision','maneuver_revision'):writer.number('Q',getattr(state,key))
    writer.number('H',len(state.path))
    for point in state.path:
        writer.pose(point.pose);writer.scalar(point.steering_rad);writer.scalar(point.velocity_mps)
    writer.number('B',len(state.segments))
    for segment in state.segments:
        if not segment.first_index<segment.last_index<len(state.path) or segment.signed_direction not in (-1,1):
            raise ValueError('plan fingerprint segment')
        writer.number('H',segment.first_index);writer.number('H',segment.last_index);writer.number('b',segment.signed_direction)
    present=state.HasField('task_reference');writer.number('B',present)
    if present:
        task=state.task_reference
        if not 2<=len(task.basis_prefix)<=64:raise ValueError('plan basis count')
        for key in ('home_lane_id','from_lane_id','selected_lane_id'):writer.number('I',getattr(task,key))
        writer.number('B',task.changing);writer.number('Q',task.basis_revision);writer.number('B',len(task.basis_prefix))
        for point in task.basis_prefix:writer.point(point)
        for key in ('vehicle','mapper_boot','mapper_instance','clock_epoch'):writer.text(getattr(task,key))
    return writer.hash.digest()


def selected_goal_sha256(mission):
    if (mission.kind!=2 or any(not mission.HasField(key) for key in ('slot_id','slot_revision','goal','goal_revision',
            'goal_position_bound_m','goal_heading_bound_rad')) or len(mission.selected_slot_polygon)!=4 or
            len(mission.mission_id)!=16 or len(mission.scene_admission_id)!=16):
        raise ValueError('selected goal fingerprint fields')
    writer=_Writer(b'XNVGOAL1\0');writer.hash.update(mission.mission_id);writer.number('Q',mission.mission_revision)
    writer.hash.update(mission.scene_admission_id);writer.number('Q',mission.scene_admission_revision)
    writer.number('I',mission.slot_id);writer.number('Q',mission.slot_revision);writer.number('Q',mission.goal_revision)
    writer.pose(mission.goal);writer.number('B',len(mission.selected_slot_polygon))
    for point in mission.selected_slot_polygon:writer.point(point)
    writer.scalar(mission.goal_position_bound_m);writer.scalar(mission.goal_heading_bound_rad)
    return writer.hash.digest()
