"""Bounded protobuf provider codec. No sensor/mapper, broker or authority side effects."""
import math
from google.protobuf.message import DecodeError
from . import world_pb2 as schema

MAX_BYTES=32768
MAX_CELLS=4096
ROAD_PRIORITY=1
CELL_ROAD=8

def _repeated(field):
    # 6.x deprecates label access; 3.20 remains the fixed generator/test reader.
    value=getattr(field,'is_repeated',None)
    return field.label==field.LABEL_REPEATED if value is None else value

def _finite(message):
    for field,value in message.ListFields():
        values=value if _repeated(field) else (value,)
        if field.type in (field.TYPE_FLOAT,field.TYPE_DOUBLE):
            if not all(math.isfinite(item) for item in values):raise ValueError('nonfinite field '+field.name)
        elif field.type==field.TYPE_STRING:
            limit=64 if field.name in ('vehicle','configuration','calibration','primitive','reference_id','reason') else 36
            for item in values:
                if not item or len(item.encode('utf-8'))>limit or any(ord(c)<33 or ord(c)>126 for c in item):raise ValueError('bounded identifier '+field.name)
        elif field.type==field.TYPE_MESSAGE:
            for item in values:_finite(item)

def validate(message):
    if not message.IsInitialized():raise ValueError('required fields missing: '+','.join(message.FindInitializationErrors()))
    _finite(message)
    if getattr(message,'major',1)!=1:raise ValueError('unsupported schema major')
    if isinstance(message,schema.WorldSnapshot):
        n=message.width*message.height
        if not 2<=message.width<=64 or not 2<=message.height<=64 or n>MAX_CELLS:raise ValueError('grid dimensions')
        if (len(message.occupancy),len(message.cell_flags),len(message.age_le32))!=(n,n,n*4):raise ValueError('grid arrays incomplete')
        if not message.complete or message.truncated or message.required_features&~ROAD_PRIORITY or message.inflation_policy:raise ValueError('unsupported map features')
        if not 2<=len(message.reference)<=64 or len(message.left_boundary)!=len(message.reference) or len(message.right_boundary)!=len(message.reference):raise ValueError('road arrays')
        if message.resolution_m<.001 or message.resolution_m>10:raise ValueError('resolution')
        if message.observed_us>message.completed_us or message.completed_us>message.published_us:raise ValueError('source time ordering')
        allowed=15 if message.required_features&ROAD_PRIORITY else 7
        if any(v>100 and v!=255 for v in message.occupancy) or any(v&~allowed for v in message.cell_flags):raise ValueError('cell encoding')
        if message.required_features&ROAD_PRIORITY and not any(v&CELL_ROAD for v in message.cell_flags):raise ValueError('missing preferred road')
    elif isinstance(message,schema.DriveState):
        if message.world_features&~ROAD_PRIORITY:raise ValueError('unsupported state world features')
        if not message.complete or not 2<=len(message.path)<=512 or message.tracker_index>=len(message.path):raise ValueError('incomplete path state')
        if message.direction not in (-1,0,1) or message.maneuver_phase>2:raise ValueError('maneuver')
        if message.HasField('merge_passed_reference_revision') and (not message.direction or
                not message.merge_passed_reference_revision or message.merge_passed_reference_revision!=message.reference_revision):
            raise ValueError('merge passage reference')
    elif isinstance(message,schema.DriveLink):
        kind=message.WhichOneof('payload')
        if kind is None:raise ValueError('missing link payload')
        inner=getattr(message,kind)
        if kind=='snapshot':validate(inner)
        elif kind=='accepted':validate(inner.state)
        if kind=='applied' and (inner.rebuilt==inner.HasField('copy_message_id') or
             (inner.applied and (not inner.calculation_sequence or not inner.vehicle_sequence or not inner.maneuver_revision))):raise ValueError('application provenance')
        if kind=='applied':
            import re
            if not inner.application_sequence:raise ValueError('application sequence')
            if inner.HasField('reason') and not re.fullmatch('[A-Z][A-Z0-9_]{1,63}',inner.reason):raise ValueError('application reason code')
    elif isinstance(message,schema.VehicleSample):
        if message.quality!=3 or not message.sequence or not message.source_sample:raise ValueError('vehicle sample quality')
        if message.position_error_m<0 or message.heading_error_rad<0 or (message.HasField('steering_error_rad') and message.steering_error_rad<0):raise ValueError('vehicle uncertainty')
    return message

def encode(message):
    validate(message)
    data=message.SerializeToString(deterministic=True)
    if len(data)>MAX_BYTES:raise ValueError('wire size cap')
    return data


def _clock_uint(value):
    if type(value) is not int or not 0<=value<1<<64:raise ValueError('clock uint64 required')
    data=bytearray()
    while value>127:data.append((value&127)|128);value>>=7
    data.append(value)
    return bytes(data)


def _clock_layout(data):
    """Locate only the two variable-width boundaries in canonical Clock wire.

    encode() already validated the message. This bounded lexical walk avoids a
    second protobuf decode/encode when the I/O owner stamps its send boundary.
    """
    def uint(at,end):
        number=0
        for n in range(10):
            if at>=end:raise ValueError('partial clock varint')
            byte=data[at];at+=1
            if n==9 and byte&254:raise ValueError('overflow clock varint')
            number|=(byte&127)<<(7*n)
            if not byte&128:return number,at
        raise ValueError('overflow clock varint')
    def fields(at,end):
        while at<end:
            tag,at=uint(at,end);number,kind=tag>>3,tag&7
            if not 0<number<=0x1fffffff:raise ValueError('clock field number')
            size_at=at
            if kind==0:
                value,after=uint(at,end)
                yield number,kind,size_at,at,after,value
            else:
                if kind==2:size,at=uint(at,end)
                elif kind==1:size=8
                elif kind==5:size=4
                else:raise ValueError('clock wire type')
                after=at+size
                if after>end:raise ValueError('partial clock field')
                yield number,kind,size_at,at,after,None
            at=after
    clock=None
    for number,kind,size_at,start,end,_ in fields(0,len(data)):
        if number==3:
            if kind!=2 or clock is not None:raise ValueError('clock reply wire')
            clock=(size_at,start,end)
    if clock is None:raise ValueError('clock reply wire')
    size_at,start,end=clock;sent=None;received=None
    for number,kind,_,value_at,after,value in fields(start,end):
        if number in (4,5):
            if kind!=0:raise ValueError('clock timestamp wire')
            if number==4:
                if received is not None:raise ValueError('duplicate clock receive')
                received=value
            else:
                if sent is not None:raise ValueError('duplicate clock send')
                sent=(value_at,after,value)
    if received is None or sent is None:raise ValueError('clock timestamps missing')
    if sent[2]<received:raise ValueError('clock send before receive')
    return size_at,start,end,sent[0],sent[1],received


class PreparedClock(bytes):
    """Immutable, validated ClockReply snapshot awaiting its I/O send stamp.

    Only pi_send_us and protobuf length varints change at publish entry. Probe,
    identity, vehicle observation and trust expiry remain the original evidence.
    This is a submission timestamp, not TCP completion, PUBACK or guest receipt.
    """
    def __new__(cls,message):
        if not isinstance(message,schema.DriveLink) or message.WhichOneof('payload')!='clock':
            raise ValueError('clock reply required')
        data=encode(message)
        result=bytes.__new__(cls,data)
        object.__setattr__(result,'_layout',_clock_layout(data))
        object.__setattr__(result,'vm',message.clock.probe.vm)
        return result

    def __setattr__(self,name,value):raise AttributeError('prepared clock is immutable')
    def __delattr__(self,name):raise AttributeError('prepared clock is immutable')

    def stamp(self,send_us):
        """Bounded varint splice; no full message validation or codec at send."""
        value=_clock_uint(send_us)
        size_at,start,end,value_at,after,received=self._layout
        if send_us<received:raise ValueError('clock send before receive')
        size=end-start+len(value)-(after-value_at)
        length=_clock_uint(size)
        total=len(self)+len(length)-(start-size_at)+len(value)-(after-value_at)
        if total>MAX_BYTES:raise ValueError('wire size cap')
        return b''.join((self[:size_at],length,self[start:value_at],value,self[after:]))


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


def _audit(data,descriptor):
    """Reject duplicate singular/oneof and nested ambiguity before protobuf's
    last-one-wins merge. One bounded lexical pass, no additional source read."""
    at=0;seen=set();unions=set();counts={}
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
        tag=varint();fieldno=tag>>3;kind=tag&7
        if not 0<fieldno<=0x1fffffff:raise ValueError('field number')
        field=descriptor.fields_by_number.get(fieldno)
        if field is not None and _repeated(field):
            limit=512 if field.full_name=='xenova.drive.DriveState.path' else 64
            counts[fieldno]=counts.get(fieldno,0)+1
            if counts[fieldno]>limit:raise ValueError('repeated field cap '+field.name)
        if field is not None and not _repeated(field):
            if fieldno in seen:raise ValueError('duplicate singular field')
            seen.add(fieldno)
            if field.containing_oneof:
                name=field.containing_oneof.name
                if name in unions:raise ValueError('multiple oneof fields')
                unions.add(name)
        if kind==0:varint();continue
        if kind==2:size=varint()
        elif kind==1:size=8
        elif kind==5:size=4
        else:raise ValueError('wire type')
        if size>len(data)-at:raise ValueError('partial field')
        if field is not None and field.type==field.TYPE_STRING:
            limit=64 if field.name in ('vehicle','configuration','calibration','primitive','reference_id','reason') else 36
            if kind!=2 or size>limit:raise ValueError('string cap '+field.name)
        if field is not None and field.type==field.TYPE_MESSAGE:
            if kind!=2:raise ValueError('message wire type')
            _audit(data[at:at+size],field.message_type)
        at+=size
