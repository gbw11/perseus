"""명시 ToF leaf의 zone 전체를 연속 ray/hit bound로 옮긴다.

sensor optical RDF→후륜축 base FLU, 거리 m/각 rad다. AXIAL_Z는 sensor z로만
radial 변환하며 base z로 나누지 않는다. 미수락 작은 표적/멀티 return/시간/pose는
FREE가 아니다. 설치·후방 시야·물리 first-surface 수락은 이 모듈이 만들지 않는다.
"""

from dataclasses import asdict,dataclass,field
from hashlib import sha256
import math

from .calibration import CalibrationProfile,canonical,matrix,number,vector
from .free_space import RayBundle
from .geometry import Interval,PointBounds
from .inputs import FrameStamp
from .motion import sin_cos_bounds
from .ray_batch import RayBundleBatch
from ..mapping.odometry import PoseBuffer,PoseEstimate


def _uint(value,maximum=(1<<63)-1,*,positive=False):
    if type(value) is not int or not int(positive)<=value<=maximum:raise ValueError('bounded native unsigned integer required')
    return value


def _flag(value):
    if type(value) is not bool:raise ValueError('qualification must be exact bool')
    return value


def _bound(value,maximum):
    value=number(value)
    if not 0<=value<=maximum:raise ValueError('ToF bound outside explicit profile')
    return value


@dataclass(frozen=True,slots=True)
class TofLeaf:
    """새 local asset schema. hash는 내용 식별이며 실제 센서 수락 증거를 대신하지 않는다.

    zone_bearings는 row-major64개의 (az_lo,az_hi,el_lo,el_hi)다. sensor RDF에서
    az는 오른쪽+,el은 위쪽+이며 h=(cos(el)sin(az),-sin(el),cos(el)cos(az))다.
    near/far/measurement_error는 distance_convention의 native m 단위다.
    driver_profile_sha256는 native→wire status/reducer·적분시각 profile을 pin한다.
    """
    sensor_id: int
    sensor_config_revision: int
    model: str
    driver_profile_sha256: str
    rotation: tuple
    translation: tuple
    zone_bearings: tuple
    distance_convention: str
    near_m: float
    far_m: float
    measurement_error_m: float
    relative_error: float=0.
    bearing_error_rad: float=0.
    rotation_error_rad: float=0.
    translation_error_m: float=0.
    free_required_quality: int=11
    extrinsics_qualified: bool=False
    first_surface_qualified: bool=False
    coverage_qualified: bool=False
    time_qualified: bool=False

    def __post_init__(self):
        _uint(self.sensor_id,1);_uint(self.sensor_config_revision,(1<<32)-1,positive=True)
        if type(self.model) is not str or not 1<=len(self.model)<=64 or any(not 33<=ord(c)<=126 for c in self.model):raise ValueError('explicit ToF model required')
        if type(self.driver_profile_sha256) is not str or len(self.driver_profile_sha256)!=64 or any(c not in '0123456789abcdef' for c in self.driver_profile_sha256):raise ValueError('ToF driver profile SHA256 required')
        object.__setattr__(self,'rotation',matrix(self.rotation,rotation=True));object.__setattr__(self,'translation',vector(self.translation,3))
        if type(self.zone_bearings) is not tuple or len(self.zone_bearings)!=64:raise ValueError('exact64 original zone angular intervals required')
        zones=tuple(vector(zone,4) for zone in self.zone_bearings)
        if any(not -math.pi<=a<=b<=math.pi or not -math.pi/2<=c<=d<=math.pi/2 for a,b,c,d in zones):raise ValueError('invalid continuous zone bearings')
        object.__setattr__(self,'zone_bearings',zones)
        if self.distance_convention not in ('AXIAL_Z','RADIAL'):raise ValueError('explicit ToF native range convention required')
        near,far=number(self.near_m),number(self.far_m)
        if not 0<=near<far<=100:raise ValueError('invalid native ToF detection range')
        object.__setattr__(self,'near_m',near);object.__setattr__(self,'far_m',far)
        for key,maximum in (('measurement_error_m',10.),('relative_error',1.),('bearing_error_rad',math.pi),
                ('rotation_error_rad',math.pi),('translation_error_m',10.)):
            object.__setattr__(self,key,_bound(getattr(self,key),maximum))
        _uint(self.free_required_quality,31)
        if self.free_required_quality&11!=11 or self.free_required_quality&16:raise ValueError('free requires valid/native-quality/closest and excludes FREE_BLOCKED')
        for key in ('extrinsics_qualified','first_surface_qualified','coverage_qualified','time_qualified'):_flag(getattr(self,key))

    def to_dict(self):return dict(schema_version=1,**asdict(self))

    @classmethod
    def from_dict(cls,value):
        if type(value) is not dict or set(value)!= {'schema_version'}|set(cls.__dataclass_fields__) or value['schema_version']!=1:raise ValueError('exact ToF leaf schema1 required')
        fields=dict(value);fields.pop('schema_version');fields['zone_bearings']=tuple(tuple(zone) for zone in fields['zone_bearings'])
        return cls(**fields)

    @property
    def sha256(self):return sha256(canonical(self.to_dict())).hexdigest()


@dataclass(frozen=True,slots=True)
class TofResult:
    stamp: FrameStamp
    leaf_sha256: str
    hit_boxes: tuple
    patches: RayBundleBatch
    accepted_zones: tuple
    rejected_zones: tuple
    mapping_ready: bool
    status: str
    world_valid: bool=field(default=False,init=False)


def _trig(lower,upper):
    s,c=sin_cos_bounds(lower,upper)
    return Interval(*s),Interval(*c)


def _direction(leaf,zone):
    az0,az1,el0,el1=zone;e=leaf.bearing_error_rad
    sa,ca=_trig(az0-e,az1+e);se,ce=_trig(el0-e,el1+e)
    sensor=(ce*sa,-se,ce*ca)
    sensor=tuple(Interval(max(-1.,v.lo),min(1.,v.hi)) for v in sensor)
    residual=max(abs(sum(leaf.rotation[k][i]*leaf.rotation[k][j] for k in range(3))-(i==j)) for i in range(3) for j in range(3))
    # 수치 proper-rotation 허용오차도 이상 SO(3)와의 component 차이 안에 둔다.
    rotation_error=math.nextafter(2*math.sin(leaf.rotation_error_rad/2)+3*residual+64*math.ulp(1.),math.inf)
    base=tuple(sum((v*coefficient for v,coefficient in zip(sensor,row)),Interval(0.,0.)) for row in leaf.rotation)
    base=tuple(Interval(max(-1.,math.nextafter(v.lo-rotation_error,-math.inf)),
                        min(1.,math.nextafter(v.hi+rotation_error,math.inf))) for v in base)
    return sensor,base


def _pose(evidence,calibration,pose,motion_qualified):
    if not motion_qualified:return None
    if type(pose) is PoseBuffer:pose=pose.at(evidence.stamp,calibration.sha256)
    if type(pose) is not PoseEstimate:return None
    sample=pose.sample;lo,hi=evidence.j_interval
    if (sample.origin!='ODOMETRY' or sample.epoch!=evidence.odom_epoch or sample.clock_id!=evidence.stamp.clock_id
            or sample.calibration_id!=calibration.sha256 or pose.captured_us!=(lo,hi)
            or not pose.bracket_us[0]<=lo<=sample.at_us<=hi<=pose.bracket_us[1] or sample.yaw_error_rad>math.pi):return None
    return pose


def _motion(origin,direction,pose):
    if pose is None:return origin,direction
    sample=pose.sample;angle=sample.yaw_error_rad;position=sample.position_error_m
    sine,cosine=_trig(-angle,angle);ns,nc=_trig(sample.yaw,sample.yaw)
    error=Interval(-position,position)
    tx,ty=error*nc+error*ns,-error*ns+error*nc
    x,y,z=origin
    moved=(cosine*x-sine*y+tx,sine*x+cosine*y+ty,z)
    x,y,z=direction
    turned=(cosine*x-sine*y,sine*x+cosine*y,z)
    turned=tuple(Interval(max(-1.,v.lo),min(1.,v.hi)) for v in turned)
    return moved,turned


def tof_evidence(evidence,leaf,calibration,*,admission,now_us,pose=None,motion_qualified=False):
    """검산된 원 complete frame을 처리한다. pose 부재는 frame-local 진단이며 지도/FREE 자격0이다."""
    from ..transport.sensor_samples import SensorSamples,FrozenSensorSamples,TofEvidence
    if type(evidence) is not TofEvidence or type(leaf) is not TofLeaf or type(calibration) is not CalibrationProfile:
        raise ValueError('exact admitted ToF evidence/leaf/local calibration required')
    _flag(motion_qualified)
    if type(admission) not in (SensorSamples,FrozenSensorSamples) or not admission.current_tof(evidence,now_us):raise ValueError('ToF original source/time qualification expired or was replaced')
    if (evidence.leaf_sha256,evidence.sensor_id,evidence.sensor_config_revision,evidence.calibration_profile_sha256)!=(
            leaf.sha256,leaf.sensor_id,leaf.sensor_config_revision,calibration.sha256):raise ValueError('ToF source/leaf differs from admitted original')
    bound=_pose(evidence,calibration,pose,motion_qualified)
    rays=[];hits=[];accepted=[];rejected=[]
    for index,(distance,status,quality,zone) in enumerate(zip(evidence.distances_mm,evidence.status,evidence.quality,leaf.zone_bearings)):
        if status!=1 or not quality&1 or distance in (0,65535):
            rejected.append((index,'INVALID_OR_MISSING_RETURN'));continue
        measured=distance*.001
        error=math.nextafter(leaf.measurement_error_m+math.nextafter(leaf.relative_error*measured,math.inf),math.inf)
        native=Interval.of(distance)*Interval.around(.001,math.ulp(.001))+Interval(-error,error)
        if native.lo<=0 or native.lo<leaf.near_m or native.hi>leaf.far_m:
            rejected.append((index,'OUTSIDE_NATIVE_RANGE'));continue
        sensor,direction=_direction(leaf,zone)
        if leaf.distance_convention=='AXIAL_Z':
            if sensor[2].lo<=0:
                rejected.append((index,'AXIAL_SENSOR_Z_NOT_POSITIVE'));continue
            radial=native/sensor[2]
            near=math.nextafter(leaf.near_m/sensor[2].lo,math.inf)
            far=math.nextafter(leaf.far_m/sensor[2].hi,-math.inf)
        else:radial=native;near=leaf.near_m;far=leaf.far_m
        origin=tuple(Interval.around(value,leaf.translation_error_m) for value in leaf.translation)
        origin,direction=_motion(origin,direction,bound)
        hit=tuple(a+b*radial for a,b in zip(origin,direction))
        hit_qualified=bool(bound is not None and leaf.extrinsics_qualified and leaf.time_qualified)
        hits.append(PointBounds(tuple(v.lo for v in hit),tuple(v.hi for v in hit),hit_qualified));accepted.append(index)
        if near>=far:
            rejected.append((index,'NO_COMMON_RADIAL_SUPPORT'));continue
        free=bool(hit_qualified and leaf.first_surface_qualified and leaf.coverage_qualified
            and evidence.frame_status&7==7 and quality&leaf.free_required_quality==leaf.free_required_quality and not quality&16)
        rays.append(RayBundle(tuple(v.lo for v in origin),tuple(v.hi for v in origin),tuple(v.lo for v in direction),tuple(v.hi for v in direction),
            radial.lo,radial.hi,near,far,free,evidence.stamp.key,None))
    patches=RayBundleBatch.from_rays(tuple(rays))
    status='QUALIFIED_RAYS' if any(ray.qualified for ray in rays) else 'HIT_ONLY' if any(hit.qualified for hit in hits) else 'UNKNOWN'
    return TofResult(evidence.stamp,leaf.sha256,tuple(hits),patches,tuple(accepted),tuple(rejected),
        bool(bound is not None and leaf.extrinsics_qualified and leaf.time_qualified),status)
