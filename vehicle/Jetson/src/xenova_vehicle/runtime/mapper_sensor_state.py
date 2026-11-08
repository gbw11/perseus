"""M-main의 fresh admission과 원 capture별 불변 query 결과를 분리한다.

원 Pi wire와 J callback 시각을 따로 보존한다. 정상 clock 갱신에 원 pose/sample을
다시 찍지 않으며, 현재 lease·전체 capture bracket이 없으면 metric 입력을 보류한다.
actor·SDK·shared arena·SQLite·주행 권한은 소유하지 않는다.
"""

from base64 import b64decode
from collections import OrderedDict
from dataclasses import dataclass,asdict
from hashlib import sha256
import threading

from .storage_messages import publication_binding
from ..storage.contracts import StorageContext,canonical,json_bytes
from ..mapping.odometry import PoseBuffer
from ..perception.inputs import FrameStamp
from ..transport.sensor_samples import SensorContext,SensorSamples,SampleRejected,VehicleEvidence


FIELDS=frozenset(('version','publication','source','origin','context_sha256','active_probe_sha256',
    'storage_context','vehicles','probes','tof','scene'))
MAX_SENSOR_SNAPSHOT_BYTES=512*1024


class SensorSnapshotUnavailable(RuntimeError):
    """원 proof 전체를 유한 snapshot에 담을 수 없다. 원본을 생략해 성공시키지 않는다."""


def _context_semantics(raw):
    if not raw:return b''
    value=json_bytes(raw,65536)
    if type(value) is not dict or set(value)!=FIELDS or type(value['version']) is not int or value['version']!=1:
        raise ValueError('exact original source context required')
    return canonical({key:value[key] for key in ('version','publication','source','origin','context_sha256','storage_context')})


def _frame_key(value):
    if type(value) is not tuple or not 1<=len(value)<=8:
        raise ValueError('bounded original frame source key required')
    for item in value:
        if type(item) is int:
            if not 0<=item<2**64:raise ValueError('original source key integer outside u64')
        elif type(item) is not str or not 1<=len(item)<=256 or any(not 33<=ord(c)<=126 for c in item):
            raise ValueError('original source key must contain bounded exact scalars')
    return value


@dataclass(frozen=True)
class MetricInputs:
    """원 frame의 후보 입력. delivery_metadata는 마지막 ingest의 신규 수락 delta다.

    delta가 비어도 원 wire·received/admitted J는 snapshot의 admission 원본에 남는다.
    """
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


@dataclass(frozen=True,slots=True,init=False)
class SensorFrameSnapshot:
    """한 원 frame의 admitted proof다. mutable owner·PoseBuffer·SensorSamples는 보유하지 않는다.

    CalibrationProfile의 provenance는 bytes, SensorContext source도 bytes이며 property는
    독립 사본이다. returned Probe/trust만 보존하며 Jetson에 없는 Pi P4를 생성하지 않는다.
    byte_length는 중복 bytes를 한 번 센 유한 proof closure 크기이며 RSS 측정값이 아니다.
    """
    calibration: object
    sensors: object
    vehicle_admissions: tuple
    capture_stamp: FrameStamp
    job_id: str
    source_generation: str
    source_key: tuple
    expires_us: int
    snapshot_j_us: int
    fence_revision: int
    byte_length: int
    source_context_bytes: bytes
    clock_proofs: tuple
    _inputs: tuple
    _bracket: object
    _pose_args: tuple
    _context_semantics: bytes

    def __init__(self,*args,**kwargs):
        raise TypeError('SensorFrameSnapshot is produced only by MapperSensorState.frame_snapshot')

    @property
    def metric_inputs(self):
        values=self._inputs
        return MetricInputs(values[0],None if values[1] is None else json_bytes(values[1],65536),*values[2:])

    @property
    def pose_args(self):return dict(self._pose_args)

    @property
    def p4_evidence(self):return None

    def original_pose_bracket(self,pose):
        if self._bracket is None or self._bracket.pose is not pose:
            raise ValueError('exact original snapshot PoseBuffer result required')
        return self._bracket

    def original_vehicle_admission(self,evidence):
        if type(evidence) is not VehicleEvidence:raise ValueError('actual original VehicleEvidence required')
        record=next((row for row in self.vehicle_admissions if row.evidence is evidence),None)
        if record is None:raise ValueError('original Vehicle admission is not in this snapshot')
        return record

    def validate_vehicle_admission(self,record):
        if type(record) is not VehicleAdmission or self.original_vehicle_admission(record.evidence) is not record:
            raise ValueError('exact original snapshot Vehicle admission required')
        return record.evidence

    def validate_frame(self,*,job_id,source_generation,source_key,expires_us,capture_stamp=None,
                       now_us=None,fence_revision=None,context_bytes=None):
        if (job_id,source_generation,source_key,expires_us)!=(
                self.job_id,self.source_generation,self.source_key,self.expires_us):
            raise ValueError('snapshot differs from original job/source/key/expiry')
        _frame_key(source_key)
        if type(expires_us) is not int or type(job_id) is not str or type(source_generation) is not str:
            raise ValueError('snapshot frame metadata has incorrect exact types')
        if capture_stamp is not None and (type(capture_stamp) is not FrameStamp or capture_stamp!=self.capture_stamp):
            raise ValueError('snapshot changed the original whole capture stamp')
        if now_us is not None and (type(now_us) is not int or not self.snapshot_j_us<=now_us<self.expires_us):
            raise ValueError('original snapshot/job expired or current time regressed')
        if fence_revision is not None and (type(fence_revision) is not int or fence_revision!=self.fence_revision):
            raise ValueError('sensor validity was fenced after the snapshot')
        if context_bytes is not None and _context_semantics(context_bytes)!=self._context_semantics:
            raise ValueError('snapshot changed source/origin/publication/storage context')
        return self

    def __copy__(self):return self

    def __deepcopy__(self,memo):return self


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
    """main owner 하나의 sensor HWM/PoseBuffer. scene/lease 변화는 geometry reset이 아니다."""

    def __init__(self,calibration,raw_binding,clock_policy,*,tof_leaves,vehicle_max_age_us,
                 tof_max_age_us,max_speed_mps,max_yaw_rate_rps,max_gap_us,capacity,mapper_worker,mapper_instance):
        self.calibration,self.raw,self.policy=calibration,raw_binding,clock_policy
        from ..storage.contracts import uuid
        self.mapper_worker,self.mapper_instance=uuid(mapper_worker),uuid(mapper_instance)
        self.sensor_args=dict(tof_leaves=dict(tof_leaves),vehicle_max_age_us=vehicle_max_age_us,
            tof_max_age_us=tof_max_age_us,max_speed_mps=max_speed_mps,max_yaw_rate_rps=max_yaw_rate_rps)
        self.pose_args=dict(max_gap_us=max_gap_us,max_speed_mps=max_speed_mps,max_yaw_rate_rps=max_yaw_rate_rps,capacity=capacity)
        PoseBuffer(**self.pose_args)
        self._thread=threading.get_ident();self._identity=None
        self._development_context=None
        self.sensors=self.poses=None;self._seen_vehicle=OrderedDict();self._seen_tof=OrderedDict()
        self._pose_quarantined=False;self._vehicles={};self._vehicle_admissions=OrderedDict();self._last_bracket=None
        self._context=None;self._source=None;self._active=None;self._source_context_bytes=b''
        self._metadata=();self._resets=();self._reasons=();self._last_ingest_us=None
        self.fence_revision=0;self._sensor_fence_revision=0
        self._validity=(False,False)

    @property
    def vehicle_admissions(self):
        self._owned()
        return tuple(self._vehicle_admissions.values())

    def _owned(self):
        if threading.get_ident()!=self._thread:raise RuntimeError('sensor state belongs to the original main owner')

    def _advance_fence(self,count):
        if type(count) is not int or not 0<=count or self.fence_revision+count>=2**63:
            raise SensorSnapshotUnavailable('sensor fence revision exhausted')
        self.fence_revision+=count

    def _observe_validity(self,clock,vehicle):
        current=(clock,vehicle)
        if any(old and not new for old,new in zip(self._validity,current)):
            self._advance_fence(1)
        self._validity=current

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
        if threading.get_ident()!=self._thread:raise RuntimeError('sensor state belongs to the original main owner')
        bracket=self._last_bracket
        if bracket is None or bracket.pose is not pose:raise ValueError('exact original PoseBuffer result required')
        return bracket

    def original_vehicle_admission(self,evidence):
        """등록된 원 객체만 반환한다. 과거 admission과 current_vehicle의 판단을 분리한다."""
        from ..transport.sensor_samples import VehicleEvidence
        if threading.get_ident()!=self._thread:raise RuntimeError('sensor state belongs to the original main owner')
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

    def ingest(self,context_bytes,*,now_us):
        """capture 없이 원 row를 실제 현재 시각에 수락한다. job 취소와 HWM은 독립이다."""
        from xenova_drive_world.mapper_clock import restore_returned_probe,MapperClockError
        self._owned()
        if (type(now_us) is not int or not 0<=now_us<2**63 or type(context_bytes) is not bytes
                or len(context_bytes)>65536 or self._last_ingest_us is not None and now_us<self._last_ingest_us):
            raise ValueError('bounded original source bytes and nonregressing admission J required')
        if not context_bytes:
            self._context=self._source=self._active=None;self._source_context_bytes=context_bytes
            self._metadata=self._resets=();self._reasons=('SOURCE_NOT_BOUND',);self._last_ingest_us=now_us
            return self._query(None,now_us)
        value=json_bytes(context_bytes,65536)
        if type(value) is not dict or set(value)!=FIELDS or type(value['version']) is not int or value['version']!=1:
            raise ValueError('unsupported immutable mapper source snapshot')
        if value['publication'] is None:
            self._context=self._source=self._active=None;self._source_context_bytes=context_bytes
            self._metadata=self._resets=();self._reasons=('SOURCE_NOT_BOUND',);self._last_ingest_us=now_us
            return self._query(None,now_us)
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
            if self._identity is not None:raise ValueError('metric geometry source changed without a new main owner')
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
                    except ValueError:
                        if not self._pose_quarantined:self._advance_fence(1)
                        self._validity=(self._validity[0],False)
                        self._pose_quarantined=True;reasons.append('ODOMETRY_MOTION_CONTRADICTION')
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
        self._advance_fence(self.sensors.fence_revision-self._sensor_fence_revision)
        self._sensor_fence_revision=self.sensors.fence_revision
        self._context=context;self._source=canonical(source);self._active=active
        self._source_context_bytes=context_bytes;self._metadata=tuple(metadata)
        self._resets=self.sensors.take_resets();self._reasons=tuple(reasons);self._last_ingest_us=now_us
        return self._query(None,now_us)

    def _query(self,capture_stamp,now_us):
        """과거 endpoint를 재-admit하지 않는다. 현재 lease/anchor와 원 bracket은 따로 검사한다."""
        self._last_bracket=None
        if self._context is None:
            self._observe_validity(False,False)
            return MetricInputs(None,None,None,None,None,(),(),(),('SOURCE_NOT_BOUND',))
        context=self._context;source=json_bytes(self._source,65536);active=self._active
        if active is not None:
            try:active.pi_interval_to_j((active.probe_pi_send_us,active.probe_pi_send_us),now_us)
            except ValueError:active=None
        if active is None:
            self._observe_validity(False,False)
            return MetricInputs(context,source,None,None,None,(),self._resets,self._metadata,('PI_J_CLOCK_UNAVAILABLE',))
        reasons=list(self._reasons)
        vehicle=self.sensors.latest_vehicle
        if not self.sensors.current_vehicle(vehicle,now_us):vehicle=None;reasons.append('VEHICLE_UNAVAILABLE')
        pose=None if self._pose_quarantined or capture_stamp is None else self.poses.at(capture_stamp,self.calibration.sha256)
        try:self._last_bracket=self._bracket(pose)
        except ValueError:pose=None;reasons.append('ORIGINAL_VEHICLE_BRACKET_UNAVAILABLE')
        if self._pose_quarantined:vehicle=None;reasons.append('ODOMETRY_MOTION_CONTRADICTION')
        self._observe_validity(True,vehicle is not None)
        if pose is None and capture_stamp is not None:reasons.append('FULL_CAPTURE_POSE_UNAVAILABLE')
        tofs=tuple(e for sensor in (0,1) for e in (self.sensors.latest_tof(sensor),)
            if e is not None and self.sensors.current_tof(e,now_us))
        return MetricInputs(context,source,active,pose,vehicle,tofs,self._resets,self._metadata,tuple(dict.fromkeys(reasons)))

    def read(self,context_bytes,capture_stamp,*,now_us):
        """legacy 편의 입구다. 생산 main은 ingest, compute는 frame snapshot만 사용한다."""
        self._owned()
        if type(capture_stamp) is not FrameStamp or type(now_us) is not int or now_us<capture_stamp.available_us:
            raise ValueError('original full-capture stamp and actual compute time required')
        self.ingest(context_bytes,now_us=now_us)
        return self._query(capture_stamp,now_us)

    def frame_snapshot(self,capture_stamp,*,now_us,expires_us,job_id,source_generation,source_key):
        """원 frame에 대한 유한 query 결과만 고정한다. ingest나 새 clock/admission은 수행하지 않는다."""
        self._owned()
        from ..storage.contracts import uuid
        uuid(job_id);_frame_key(source_key)
        if (type(source_generation) is not str or not 1<=len(source_generation)<=256
                or any(not 33<=ord(c)<=126 for c in source_generation)):
            raise ValueError('bounded original source generation required')
        if (type(capture_stamp) is not FrameStamp or type(now_us) is not int or type(expires_us) is not int
                or not capture_stamp.available_us<=now_us<expires_us<2**63
                or self._last_ingest_us is not None and now_us<self._last_ingest_us):
            raise SensorSnapshotUnavailable('original frame unavailable, expired, or query time regressed')
        inputs=self._query(capture_stamp,now_us)
        records=self.vehicle_admissions
        frozen=None if self.sensors is None else self.sensors.freeze()
        leases=tuple(row.evidence.clock_lease for row in records)
        if frozen is not None:
            leases+=tuple(e.clock_lease for _,e in frozen._tofs if e is not None)
        if inputs.clock_lease is not None:leases+=(inputs.clock_lease,)
        proofs={}
        for lease in leases:
            raw,trust=lease.raw_probe_bytes,lease.trust_bytes
            proofs[(sha256(raw).hexdigest(),sha256(trust).hexdigest())]=(sha256(raw).hexdigest(),raw,sha256(trust).hexdigest(),trust)
        clock_proofs=tuple(proofs.values())
        source=None if inputs.source is None else canonical(inputs.source)
        semantics=_context_semantics(self._source_context_bytes)
        values=(inputs.storage_context,source,inputs.clock_lease,inputs.pose,inputs.vehicle,inputs.tof,
            inputs.sensor_resets,inputs.delivery_metadata,inputs.reasons)
        raw_bytes={self._source_context_bytes,semantics,canonical(self.calibration.to_dict())}
        if source is not None:raw_bytes.add(source)
        raw_bytes.update(part for proof in clock_proofs for part in (proof[1],proof[3]))
        raw_bytes.update(row.evidence.wire for row in records)
        if frozen is not None:
            raw_bytes.add(frozen.context._source)
            raw_bytes.update(canonical(leaf.to_dict()) for _,leaf in frozen._leaves)
            raw_bytes.update(e.wire for _,e in frozen._tofs if e is not None)
        meta=canonical(dict(job_id=job_id,source_generation=source_generation,source_key=source_key,
            expires_us=expires_us,snapshot_j_us=now_us,fence_revision=self.fence_revision,
            capture_stamp=asdict(capture_stamp),pose_args=self.pose_args,
            clock_proofs=[(proof[0],proof[2]) for proof in clock_proofs],
            pose=None if inputs.pose is None else asdict(inputs.pose),
            metadata=inputs.delivery_metadata,reasons=inputs.reasons,resets=[asdict(value) for value in inputs.sensor_resets],
            admissions=[dict(wire_sha256=row.evidence.wire_sha256,admitted_j_us=row.admitted_j_us,received_j_us=row.received_j_us,
                pi_interval=row.evidence.pi_interval,j_interval=row.evidence.j_interval,
                valid_until_j_us=row.evidence.valid_until_j_us,pose=asdict(row.evidence.pose_sample)) for row in records]))
        byte_length=sum(map(len,raw_bytes))+len(meta)
        if byte_length>MAX_SENSOR_SNAPSHOT_BYTES:
            raise SensorSnapshotUnavailable('whole original sensor proof exceeds512KiB; no proof omitted')
        result=object.__new__(SensorFrameSnapshot)
        fields=dict(calibration=self.calibration,sensors=frozen,vehicle_admissions=records,capture_stamp=capture_stamp,
            job_id=job_id,source_generation=source_generation,source_key=source_key,expires_us=expires_us,
            snapshot_j_us=now_us,fence_revision=self.fence_revision,byte_length=byte_length,
            source_context_bytes=self._source_context_bytes,clock_proofs=clock_proofs,_inputs=values,
            _bracket=self._last_bracket,_pose_args=tuple(self.pose_args.items()),
            _context_semantics=semantics)
        for name,value in fields.items():object.__setattr__(result,name,value)
        return result
