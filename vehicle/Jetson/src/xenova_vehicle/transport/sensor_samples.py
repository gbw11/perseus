"""원 Vehicle/ToF protobuf를 현재 source와 admitted Pi↔J lease에 결합한다.

원 wire/CRC/sequence/적분 오차를 보존한다. arrival은 available 시각일 뿐 측정시각이
아니다. 전체 차량 bundle과 sensor leaf를 구분하며 odom/센서 epoch를 발급하지 않는다.
"""

from dataclasses import dataclass,field
from hashlib import sha256
import json
import math
import struct
from uuid import UUID
import zlib

from ..mapping.odometry import PoseSample
from ..perception.calibration import CalibrationProfile,canonical
from ..perception.inputs import FrameStamp


SOURCE_FIELDS=frozenset(('vehicle','device','mapper_boot','mapper_instance','clock_epoch','map_epoch','frame_epoch',
    'odom_epoch','configuration','calibration','primitive'))
CRC_HEADER=struct.Struct('<16s16sQQI16sQB16sII32sBBH')
CRC_ZONE=struct.Struct('<HBB')


class SampleRejected(ValueError):
    """원 source/시간/보정/CRC가 현재 수락 조건을 만족하지 않는다."""


def _uint(value,maximum=(1<<63)-1,*,positive=False):
    if type(value) is not int or not int(positive)<=value<=maximum:raise SampleRejected('bounded unsigned sample scalar required')
    return value


def _uuid(value):
    if type(value) is not str or str(UUID(value))!=value or not UUID(value).int:raise SampleRejected('canonical nonzero source UUID required')
    return value


def _sha(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise SampleRejected('lowercase source SHA256 required')
    return value


def _label(value):
    if type(value) is not str or not 1<=len(value)<=64 or any(not 33<=ord(c)<=126 for c in value):raise SampleRejected('bounded source label required')
    return value


def _decode(raw,kind):
    from xenova_drive_world import schema,decode
    if type(raw) is not bytes or not 0<len(raw)<=1024:raise SampleRejected('bounded original immutable sample bytes required')
    return decode(raw,getattr(schema,kind))


@dataclass(frozen=True,slots=True,init=False)
class SensorContext:
    calibration: CalibrationProfile
    _source: bytes
    pi_host_boot: str
    gateway_instance: str
    pi_session: str
    device_session: str

    def __init__(self,calibration,current_source,*,pi_host_boot,gateway_instance,pi_session,device_session):
        if type(calibration) is not CalibrationProfile or calibration.kind=='SYNTHETIC' or calibration.vehicle_bundle_sha256 is None:
            raise SampleRejected('physical profile must declare the approved whole vehicle bundle')
        if type(current_source) is not dict or set(current_source)!=SOURCE_FIELDS:raise SampleRejected('exact current S source required')
        for key in ('mapper_boot','mapper_instance','clock_epoch','map_epoch','frame_epoch','odom_epoch'):_uuid(current_source[key])
        for key in ('vehicle','device','configuration'):_label(current_source[key])
        for key in ('calibration','primitive'):_sha(current_source[key])
        if current_source['calibration']!=calibration.vehicle_bundle_sha256:raise SampleRejected('Vehicle bundle is not the local camera profile hash')
        for name,value in (('pi_host_boot',pi_host_boot),('gateway_instance',gateway_instance),('pi_session',pi_session),('device_session',device_session)):
            _uuid(value);object.__setattr__(self,name,value)
        object.__setattr__(self,'calibration',calibration);object.__setattr__(self,'_source',canonical(current_source))

    @property
    def source(self):return json.loads(self._source)


@dataclass(frozen=True,slots=True)
class VehicleAnchor:
    """원 k의 odom pose. World.reference_vehicle_pose는 caller가 k의 map 변환 뒤 별도로 만든다."""
    sequence: int
    source_sample: int
    source_us: int
    pi_sample_us: int
    odom_epoch: str
    odom_pose: tuple
    calibration_bundle_sha256: str


@dataclass(frozen=True,slots=True)
class VehicleEvidence:
    wire: bytes
    wire_sha256: str
    anchor: VehicleAnchor
    pi_interval: tuple
    j_interval: tuple
    pose_sample: PoseSample
    clock_lease: object
    source_key: tuple
    valid_until_j_us: int

    def message(self):return _decode(self.wire,'VehicleSample')


@dataclass(frozen=True,slots=True)
class TofEvidence:
    wire: bytes
    wire_sha256: str
    sensor_id: int
    sensor_instance: str
    sensor_generation: int
    sensor_config_revision: int
    leaf_sha256: str
    calibration_profile_sha256: str
    odom_epoch: str
    source_sample: int
    source_us: int
    source_crc32: int
    source_clock_epoch: str
    source_clock_sequence: int
    clock_error_us: int
    sensor_time_error_us: int
    frame_status: int
    distances_mm: tuple
    status: bytes
    quality: bytes
    pi_interval: tuple
    j_interval: tuple
    stamp: FrameStamp
    clock_lease: object
    valid_until_j_us: int

    def message(self):return _decode(self.wire,'TofObservation')

    @property
    def raw_key(self):return self.sensor_instance,self.source_sample,self.source_us


@dataclass(frozen=True,slots=True)
class SensorReset:
    sensor_id: int
    previous_instance: str
    previous_generation: int
    sensor_instance: str
    sensor_generation: int
    wire_sha256: str


@dataclass(slots=True)
class _Sensor:
    instance: str
    generation: int
    retired: set=field(default_factory=set)
    wire: bytes | None=None
    source_sample: int=0
    source_us: int=0
    clock_epoch: str | None=None
    clock_sequence: int=0
    evidence: TofEvidence | None=None


def tof_crc32(sample):
    """부록B의 원 serial common metadata+64 interleaved zones=393B. Pi receive 시각은 제외한다."""
    if type(sample.sensor_instance) is not bytes or len(sample.sensor_instance)!=16:raise SampleRejected('original sensor instance16 required')
    distances=struct.unpack('<64H',sample.distance_mm_le)
    header=CRC_HEADER.pack(UUID(sample.pi_session).bytes,UUID(sample.device_session).bytes,sample.source_sample,sample.source_us,
        sample.sensor_time_error_us,UUID(sample.clock_epoch).bytes,sample.clock_seq,sample.sensor_id,sample.sensor_instance,sample.sensor_generation,
        sample.sensor_config_revision,bytes.fromhex(sample.calibration),sample.rows,sample.cols,sample.frame_status)
    zones=b''.join(CRC_ZONE.pack(distance,status,quality) for distance,status,quality in zip(distances,sample.status,sample.quality))
    if len(header)+len(zones)!=393:raise SampleRejected('original CRC profile layout differs')
    return zlib.crc32(header+zones)


class SensorSamples:
    """현재 source당 Vehicle1/ToF2/latest reset2. 센서당 retired instance 최대64, 무효과 eviction은 없다.

    context 교체는 caller의 실제 source 철회/재수락 뒤 새 객체로 수행한다. ToF reset은
    해당 source만 철회하며 PoseBuffer를 소유하거나 Vehicle odom_epoch를 바꾸지 않는다.
    max_speed/yaw_rate와 age는 설치자가 수락한 bound이며 실측 기본값을 만들지 않는다.
    """

    def __init__(self,context,*,tof_leaves=None,vehicle_max_age_us,tof_max_age_us,max_speed_mps,max_yaw_rate_rps):
        from ..perception.tof import TofLeaf
        if type(context) is not SensorContext:raise SampleRejected('exact current SensorContext required')
        for value in (vehicle_max_age_us,tof_max_age_us):_uint(value,1_000_000,positive=True)
        for value in (max_speed_mps,max_yaw_rate_rps):
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=100:raise SampleRejected('explicit finite motion bound required')
        leaves={} if tof_leaves is None else tof_leaves
        if type(leaves) is not dict or len(leaves)>2:raise SampleRejected('at most2 explicitly installed ToF leaves')
        external=context.calibration.leaf_hashes['external'];inventory={}
        for sensor_id,item in leaves.items():
            _uint(sensor_id,1)
            if type(item) is not tuple or len(item)!=2 or type(item[0]) is not str or type(item[1]) is not TofLeaf:raise SampleRejected('explicit sensor_id -> (external leaf name,TofLeaf) required')
            name,leaf=item
            if leaf.sensor_id!=sensor_id or external.get(name)!=leaf.sha256:raise SampleRejected('ToF leaf differs from approved bundle inventory')
            inventory[sensor_id]=leaf
        self.context=context;self.leaves=inventory;self.vehicle_age=vehicle_max_age_us;self.tof_age=tof_max_age_us
        self.speed=float(max_speed_mps);self.yaw_rate=float(max_yaw_rate_rps)
        self.latest_vehicle=None;self._sensors={};self._resets={}

    def _origin(self,sample,*,vehicle=False):
        c=self.context;source=c.source
        if vehicle and (not sample.HasField('gateway_instance') or not sample.HasField('pi_session')):raise SampleRejected('Vehicle source lacks original Gateway/Pi session')
        if (sample.vehicle,sample.host_boot,sample.gateway_instance,sample.pi_session,sample.device_session)!=(
                source['vehicle'],c.pi_host_boot,c.gateway_instance,c.pi_session,c.device_session):raise SampleRejected('sample is not from the current original device/Pi source')
        if vehicle and (sample.odom_epoch,sample.calibration)!=(source['odom_epoch'],source['calibration']):raise SampleRejected('Vehicle changed original odom epoch or whole bundle')

    def _clock(self,lease,pi_interval,now_us,age):
        from xenova_drive_world.mapper_clock import ClockLease
        _uint(now_us)
        if type(lease) is not ClockLease or lease.pi_host_boot!=self.context.pi_host_boot or lease.source!=self.context.source:
            raise SampleRejected('clock lease differs from current original Pi/J source')
        lo,hi=pi_interval
        if not 0<=lo<=hi<(1<<63):raise SampleRejected('source integration interval underflow/overflow')
        result=lease.pi_interval_to_j(pi_interval,now_us)
        if (type(result) is not tuple or len(result)!=2 or not 0<=result[0]<=result[1]<=now_us
                or now_us-result[0]>=age):raise SampleRejected('original sample interval is future, expired or unbounded')
        return result

    def accept_vehicle(self,raw,clock_lease,now_us):
        sample=_decode(raw,'VehicleSample');self._origin(sample,vehicle=True)
        old=self.latest_vehicle
        if old is not None:
            previous=old.anchor
            if sample.sequence==previous.sequence:
                if raw==old.wire:return None
                raise SampleRejected('Vehicle sequence conflict')
            if (sample.sequence<=previous.sequence or sample.source_sample<=previous.source_sample
                    or sample.source_us<=previous.source_us or sample.pi_sample_us<=previous.pi_sample_us):raise SampleRejected('original Vehicle sequence/time regressed')
        pi=(sample.pi_sample_us-sample.clock_error_us,sample.pi_sample_us+sample.clock_error_us)
        interval=self._clock(clock_lease,pi,now_us,self.vehicle_age)
        middle=sum(interval)//2;half=math.nextafter(max(middle-interval[0],interval[1]-middle)/1e6,math.inf)
        position=math.nextafter(sample.position_error_m+math.nextafter(self.speed*half,math.inf),math.inf)
        yaw=math.nextafter(sample.heading_error_rad+math.nextafter(self.yaw_rate*half,math.inf),math.inf)
        if yaw>math.pi:raise SampleRejected('Vehicle time/heading uncertainty exceeds supported pose tube')
        source=self.context.source
        pose=PoseSample(sample.odom_epoch,source['clock_epoch'],self.context.calibration.sha256,middle,
            sample.odom_pose.x,sample.odom_pose.y,sample.odom_pose.yaw,position,yaw,'ODOMETRY')
        anchor=VehicleAnchor(sample.sequence,sample.source_sample,sample.source_us,sample.pi_sample_us,sample.odom_epoch,
            (sample.odom_pose.x,sample.odom_pose.y,sample.odom_pose.yaw),sample.calibration)
        value=VehicleEvidence(raw,sha256(raw).hexdigest(),anchor,pi,interval,pose,clock_lease,(sample.device_session,sample.source_sample,sample.source_us),interval[0]+self.vehicle_age)
        self.latest_vehicle=value
        return value

    def accept_tof(self,raw,clock_lease,now_us):
        sample=_decode(raw,'TofObservation');self._origin(sample)
        leaf=self.leaves.get(sample.sensor_id)
        if leaf is None or (sample.calibration,sample.sensor_config_revision)!=(leaf.sha256,leaf.sensor_config_revision):raise SampleRejected('unsupported ToF sensor or unapproved leaf/configuration')
        _uuid(sample.clock_epoch)
        instance=str(UUID(bytes=sample.sensor_instance));_uuid(instance)
        if tof_crc32(sample)!=sample.source_crc32:raise SampleRejected('original ToF source CRC32 mismatch')
        old=self._sensors.get(sample.sensor_id)
        if old is not None:
            if sample.sensor_generation<old.generation:raise SampleRejected('ToF generation regressed')
            if sample.sensor_generation==old.generation and instance!=old.instance:raise SampleRejected('ToF generation/instance conflict')
            if sample.sensor_generation>old.generation:
                if instance==old.instance or instance in old.retired:raise SampleRejected('ToF reset reused a sensor instance')
                if len(old.retired)>=64:raise SampleRejected('ToF source inventory exhausted; no silent eviction')
                retired=old.retired|{old.instance}
                self._resets[sample.sensor_id]=SensorReset(sample.sensor_id,old.instance,old.generation,instance,sample.sensor_generation,sha256(raw).hexdigest())
                old=_Sensor(instance,sample.sensor_generation,retired);self._sensors[sample.sensor_id]=old
            elif sample.source_sample==old.source_sample:
                if raw!=old.wire:raise SampleRejected('ToF source sequence conflict')
                if old.evidence is not None:return None
            elif (sample.source_sample<old.source_sample or sample.source_us<=old.source_us
                    or sample.clock_epoch!=old.clock_epoch or sample.clock_seq<old.clock_sequence):raise SampleRejected('ToF original source/time/clock regressed')
        else:
            old=_Sensor(instance,sample.sensor_generation);self._sensors[sample.sensor_id]=old
        # 실제 새 generation의 원 complete/CRC는 위에서 수락했다. 시간 실패도 old source를 되살리지 않는다.
        old.wire=raw;old.source_sample=sample.source_sample;old.source_us=sample.source_us
        old.clock_epoch=sample.clock_epoch;old.clock_sequence=sample.clock_seq
        old.evidence=None
        if sample.frame_status&3!=3:raise SampleRejected('ToF sensor/integration timing is not valid')
        error=sample.clock_error_us+sample.sensor_time_error_us
        pi=(sample.pi_sample_us-error,sample.pi_sample_us+error)
        interval=self._clock(clock_lease,pi,now_us,self.tof_age)
        stamp=FrameStamp(self.context.device_session,instance,sample.source_sample,self.context.source['clock_epoch'],interval,now_us)
        value=TofEvidence(raw,sha256(raw).hexdigest(),sample.sensor_id,instance,sample.sensor_generation,sample.sensor_config_revision,
            sample.calibration,self.context.calibration.sha256,self.context.source['odom_epoch'],sample.source_sample,sample.source_us,sample.source_crc32,
            sample.clock_epoch,sample.clock_seq,sample.clock_error_us,sample.sensor_time_error_us,sample.frame_status,
            struct.unpack('<64H',sample.distance_mm_le),sample.status,sample.quality,pi,interval,stamp,clock_lease,interval[0]+self.tof_age)
        old.evidence=value
        return value

    def latest_tof(self,sensor_id):
        _uint(sensor_id,1);state=self._sensors.get(sensor_id)
        return None if state is None else state.evidence

    def take_resets(self):
        result=tuple(self._resets[key] for key in sorted(self._resets));self._resets.clear()
        return result

    def current_tof(self,evidence,now_us):
        """원 latest identity/고정 age/원 clock lease를 재검사한다. renewal로 원 J interval을 다시 만들지 않는다."""
        if type(evidence) is not TofEvidence or self.latest_tof(evidence.sensor_id) is not evidence:return False
        if not evidence.stamp.available_us<=now_us<evidence.valid_until_j_us:return False
        try:return evidence.clock_lease.pi_interval_to_j(evidence.pi_interval,now_us)==evidence.j_interval
        except ValueError:return False

    def current_vehicle(self,evidence,now_us):
        if type(evidence) is not VehicleEvidence or self.latest_vehicle is not evidence:return False
        if not evidence.j_interval[1]<=now_us<evidence.valid_until_j_us:return False
        try:return evidence.clock_lease.pi_interval_to_j(evidence.pi_interval,now_us)==evidence.j_interval
        except ValueError:return False
