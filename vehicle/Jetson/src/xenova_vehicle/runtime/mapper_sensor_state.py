"""M-compute에서 원 source snapshot을 clock·센서·pose 근거로 소비한다.

원 Pi wire와 J callback 시각을 따로 보존한다. 정상 clock 갱신에 원 pose/sample을
다시 찍지 않으며, 현재 lease·전체 capture bracket이 없으면 metric 입력을 보류한다.
actor·SDK·shared arena·SQLite·주행 권한은 소유하지 않는다.
"""

from base64 import b64decode
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import threading

from .storage_messages import publication_binding
from ..storage.contracts import StorageContext,canonical,json_bytes
from ..mapping.odometry import PoseBuffer
from ..perception.inputs import FrameStamp
from ..transport.sensor_samples import SensorContext,SensorSamples,SampleRejected


FIELDS=frozenset(('version','publication','source','origin','context_sha256','active_probe_sha256',
    'storage_context','vehicles','probes','tof','scene'))


@dataclass(frozen=True)
class MetricInputs:
    """같은 compute 회차의 후보 입력. pose 없음과 원 callback 정보는 그대로 노출한다."""
    storage_context: object
    source: object
    clock_lease: object
    pose: object
    vehicle: object
    tof: tuple
    sensor_resets: tuple
    delivery_metadata: tuple
    reasons: tuple


@dataclass(frozen=True)
class VehicleAdmission:
    """원 수락 사건과 불변 wire다. 과거 bracket을 현재 latest Vehicle로 바꾸지 않는다."""
    evidence: object
    admitted_j_us: int
    received_j_us: int


@dataclass(frozen=True)
class VehicleBracket:
    """PoseBuffer가 실제 사용한 양 끝 원본. endpoint가 같으면 한 원본만 가진다."""
    pose: object
    originals: tuple


def _row(value):
    if type(value) is not dict or set(value)!= {'raw','received_j_us','clock_probe_sha256'}:
        raise ValueError('exact bounded original source row required')
    received=value['received_j_us']
    if type(received) is not str or not received.isascii() or not received.isdecimal() or str(int(received))!=received:
        raise ValueError('original J callback time must be a decimal string')
    raw=b64decode(value['raw'],validate=True)
    if not 0<len(raw)<=1024 or not 0<=int(received)<2**63:raise ValueError('bounded original source bytes/time required')
    key=value['clock_probe_sha256']
    if key is not None and (type(key) is not str or len(key)!=64 or any(c not in '0123456789abcdef' for c in key)):
        raise ValueError('original probe SHA required')
    return raw,int(received),key


class MapperSensorState:
    """compute owner 하나의 sensor HWM/PoseBuffer. scene/lease 변화는 geometry reset이 아니다."""

    def __init__(self,calibration,raw_binding,clock_policy,*,tof_leaves,vehicle_max_age_us,
                 tof_max_age_us,max_speed_mps,max_yaw_rate_rps,max_gap_us,capacity,mapper_worker,mapper_instance):
        self.calibration,self.raw,self.policy=calibration,raw_binding,clock_policy
        from ..storage.contracts import uuid
        self.mapper_worker,self.mapper_instance=uuid(mapper_worker),uuid(mapper_instance)
        self.sensor_args=dict(tof_leaves=dict(tof_leaves),vehicle_max_age_us=vehicle_max_age_us,
            tof_max_age_us=tof_max_age_us,max_speed_mps=max_speed_mps,max_yaw_rate_rps=max_yaw_rate_rps)
        self.pose_args=dict(max_gap_us=max_gap_us,max_speed_mps=max_speed_mps,max_yaw_rate_rps=max_yaw_rate_rps,capacity=capacity)
        self._thread=threading.get_ident();self._identity=None
        self._development_context=None
        self.sensors=self.poses=None;self._seen_vehicle=OrderedDict();self._seen_tof=OrderedDict()
        self._pose_quarantined=False;self._vehicles={};self._vehicle_admissions=OrderedDict();self._last_bracket=None

    @classmethod
    def for_development(cls,profile,raw_binding,clock_policy,*,sensor_context,mapper_worker,mapper_instance):
        """원 개발 binding만 선택한다. 아래 wire/clock/admission/read 본문은 공유한다."""
        from ..simulation.motion_profile import DevelopmentMotionProfile
        from .raw_binding import RawBinding
        if (type(profile) is not DevelopmentMotionProfile or type(sensor_context) is not SensorContext
                or sensor_context.domain!='DEVELOPMENT_SYNTHETIC'
                or sensor_context.calibration is not profile.bound_calibration
                or type(raw_binding) is not RawBinding
                or raw_binding.fields['calibration_bundle']!=profile.bound_calibration.sha256):
            raise ValueError('exact development profile/source/raw binding required')
        source=sensor_context.source
        if clock_policy!=profile.clock_policy(device=source['device'],primitive=source['primitive']):
            raise ValueError('clock policy differs from the explicit development manifest')
        arguments=profile.sensor_arguments;pose=profile.pose_arguments
        result=cls(profile.bound_calibration,raw_binding,clock_policy,**arguments,
            max_gap_us=pose['max_gap_us'],capacity=pose['capacity'],
            mapper_worker=mapper_worker,mapper_instance=mapper_instance)
        result._development_context=sensor_context
        return result

    def original_pose_bracket(self,pose):
        """이번 read가 반환한 실제 pose의 원 admission을 읽는다. age/lease를 갱신하지 않는다."""
        if threading.get_ident()!=self._thread:raise RuntimeError('sensor state belongs to the original compute owner')
        bracket=self._last_bracket
        if bracket is None or bracket.pose is not pose:raise ValueError('exact original PoseBuffer result required')
        return bracket

    def original_vehicle_admission(self,evidence):
        """등록된 원 객체만 반환한다. 과거 admission과 current_vehicle의 판단을 분리한다."""
        from ..transport.sensor_samples import VehicleEvidence
        if threading.get_ident()!=self._thread:raise RuntimeError('sensor state belongs to the original compute owner')
        if type(evidence) is not VehicleEvidence:raise ValueError('actual original VehicleEvidence required')
        record=self._vehicle_admissions.get(evidence.wire_sha256)
        if record is None or record.evidence is not evidence:raise ValueError('original Vehicle admission was not retained')
        return record

    def validate_vehicle_admission(self,record):
        if type(record) is not VehicleAdmission or self.original_vehicle_admission(record.evidence) is not record:
            raise ValueError('exact original Vehicle admission required')
        return record.evidence

    def _bracket(self,pose):
        if pose is None:return None
        originals=[]
        for endpoint in dict.fromkeys(pose.bracket_us):
            record=self._vehicles.get(endpoint)
            if record is None or record.evidence.pose_sample.context!=pose.sample.context:
                raise ValueError('original Vehicle bracket admission was not retained')
            originals.append(record)
        return VehicleBracket(pose,tuple(originals))

    @staticmethod
    def _remember(registry,key):
        registry[key]=True
        while len(registry)>64:registry.popitem(last=False)

    def read(self,context_bytes,capture_stamp,*,now_us):
        """실제 job의 고정 bytes와 RGB/depth 전체 capture hull을 받는다. 외삽하지 않는다."""
        from xenova_drive_world.mapper_clock import restore_returned_probe,MapperClockError
        if threading.get_ident()!=self._thread:raise RuntimeError('sensor state belongs to the original compute owner')
        if type(capture_stamp) is not FrameStamp or type(now_us) is not int or now_us<capture_stamp.available_us:
            raise ValueError('original full-capture stamp and actual compute time required')
        self._last_bracket=None
        if not context_bytes:
            return MetricInputs(None,None,None,None,None,(),(),(),('SOURCE_NOT_BOUND',))
        value=json_bytes(context_bytes,65536)
        if type(value) is not dict or set(value)!=FIELDS or type(value['version']) is not int or value['version']!=1:
            raise ValueError('unsupported immutable mapper source snapshot')
        if value['publication'] is None:
            return MetricInputs(None,None,None,None,None,(),(),(),('SOURCE_NOT_BOUND',))
        binding=publication_binding(value['publication']);context=StorageContext.from_fields(value['storage_context'])
        if (context.sha256!=value['context_sha256'] or context.source_binding!=canonical(binding)
                or binding['raw_binding_sha256']!=self.raw.sha256
                or binding['service_instance']!=self.raw.fields['service_instance']
                or self.raw.fields['calibration_bundle']!=self.calibration.sha256
                or (context.mapper_worker,binding['mapper_worker'])!=(self.mapper_worker,self.mapper_worker)
                or (context.origin_mapper_instance,binding['mapper_instance'])!=(self.mapper_instance,self.mapper_instance)
                or any(getattr(context,key)!=binding[key] for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration'))
                or context.primitive_sha256!=binding['primitive']):
            raise ValueError('source snapshot changed original binding/context/raw proof')
        source=value['source'];origin=value['origin'];geometry=context.geometry_origin_fields
        if geometry is None:raise ValueError('metric source requires explicit original Pi geometry origin')
        expected=dict(vehicle=self.policy.vehicle,device=self.policy.device,configuration=self.policy.configuration,
            calibration=self.policy.calibration,primitive=self.policy.primitive)
        if (type(source) is not dict or set(source)!=set(expected)|{'mapper_boot','mapper_instance','clock_epoch','map_epoch','frame_epoch','odom_epoch'}
                or any(source[key]!=item for key,item in expected.items())
                or source['clock_epoch']!=self.raw.fields['clock_epoch']
                or any(source[key]!=binding[key] for key in ('mapper_instance','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))
                or origin!={key:geometry[key] for key in ('pi_host_boot','gateway_instance','device_session','pi_session')}
                or geometry['vehicle']!=source['vehicle'] or self.calibration.vehicle_bundle_sha256!=source['calibration']):
            raise ValueError('source snapshot differs from actual approved geometry/clock/sensor origin')
        identity=canonical(dict(source=source,origin=origin))
        if self._identity!=identity:
            if self._identity is not None:raise ValueError('metric geometry source changed without a new compute owner')
            selected=self._development_context
            if selected is not None:
                if selected.source!=source or any(getattr(selected,key)!=item for key,item in origin.items()):
                    raise ValueError('development snapshot changed its original explicit source binding')
            else:selected=SensorContext(self.calibration,source,**origin)
            self.sensors=SensorSamples(selected,**self.sensor_args)
            self.poses=PoseBuffer(**self.pose_args);self._identity=identity
        if (type(value['vehicles']) is not list or len(value['vehicles'])>24 or type(value['probes']) is not dict
                or len(value['probes'])>8 or type(value['tof']) is not dict or set(value['tof'])-{'0','1'}):
            raise ValueError('source history exceeds original bounded heads')
        leases={};reasons=[];metadata=[]
        for key,row in value['probes'].items():
            wire,received,_=_row(row)
            if sha256(wire).hexdigest()!=key or received>now_us:raise ValueError('original Probe bytes/callback hash changed')
            try:lease=restore_returned_probe(wire,self.policy,source,origin['pi_host_boot'],now_us)
            except MapperClockError:lease=None
            if lease is not None:leases[key]=lease
        active=leases.get(value['active_probe_sha256'])
        for row in value['vehicles']:
            wire,received,key=_row(row);digest=sha256(wire).hexdigest()
            if received>now_us:raise ValueError('future original Vehicle callback')
            if digest in self._seen_vehicle:continue
            if active is None:reasons.append('VEHICLE_CLOCK_UNAVAILABLE');continue
            lease=leases.get(key)
            if lease is None:reasons.append('VEHICLE_CLOCK_UNAVAILABLE');continue
            try:
                evidence=self.sensors.accept_vehicle(wire,lease,now_us)
                if evidence is not None:
                    try:
                        record=VehicleAdmission(evidence,now_us,received)
                        self._vehicle_admissions[evidence.wire_sha256]=record
                        if self.poses.add(evidence.pose_sample):
                            self._vehicles[evidence.pose_sample.at_us]=record
                        while len(self._vehicle_admissions)>self.pose_args['capacity']:
                            _,old=self._vehicle_admissions.popitem(last=False)
                            if self._vehicles.get(old.evidence.pose_sample.at_us) is old:self._vehicles.pop(old.evidence.pose_sample.at_us)
                    except ValueError:self._pose_quarantined=True;reasons.append('ODOMETRY_MOTION_CONTRADICTION')
            except (SampleRejected,MapperClockError):reasons.append('VEHICLE_UNAVAILABLE');continue
            self._remember(self._seen_vehicle,digest);metadata.append(('VEHICLE',digest,received))
        for sensor,row in value['tof'].items():
            wire,received,key=_row(row);digest=sha256(wire).hexdigest()
            if received>now_us:raise ValueError('future original ToF callback')
            from xenova_drive_world import decode,schema
            if decode(wire,schema.TofObservation).sensor_id!=int(sensor):
                raise ValueError('ToF row changed original sensor index')
            if digest in self._seen_tof:continue
            lease=leases.get(key)
            # SensorSamples는 CRC/origin/generation을 먼저 검사해 시각 미수락 reset도
            # 이전 sensor instance를 철회한다. clock이 없다고 원 reset을 건너뛰지 않는다.
            if lease is None:reasons.append('TOF_CLOCK_UNAVAILABLE')
            try:
                evidence=self.sensors.accept_tof(wire,lease,now_us)
            except (SampleRejected,MapperClockError):reasons.append('TOF_UNAVAILABLE');continue
            self._remember(self._seen_tof,digest);metadata.append(('TOF',digest,received))
        if active is None:
            return MetricInputs(context,source,None,None,None,(),self.sensors.take_resets(),tuple(metadata),('PI_J_CLOCK_UNAVAILABLE',))
        vehicle=self.sensors.latest_vehicle
        if not self.sensors.current_vehicle(vehicle,now_us):vehicle=None;reasons.append('VEHICLE_UNAVAILABLE')
        pose=None if self._pose_quarantined else self.poses.at(capture_stamp,self.calibration.sha256)
        try:self._last_bracket=self._bracket(pose)
        except ValueError:pose=None;reasons.append('ORIGINAL_VEHICLE_BRACKET_UNAVAILABLE')
        if self._pose_quarantined:vehicle=None;reasons.append('ODOMETRY_MOTION_CONTRADICTION')
        if pose is None:reasons.append('FULL_CAPTURE_POSE_UNAVAILABLE')
        tofs=tuple(e for sensor in (0,1) for e in (self.sensors.latest_tof(sensor),)
            if e is not None and self.sensors.current_tof(e,now_us))
        return MetricInputs(context,source,active,pose,vehicle,tofs,self.sensors.take_resets(),tuple(metadata),tuple(dict.fromkeys(reasons)))
