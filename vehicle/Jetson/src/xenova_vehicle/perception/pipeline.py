"""실물·재생·합성 관측을 tape/depth 근거와 국소 지도에 연결한다.

RGB/depth/NN은 별도 HWM으로 처리한다. odom이 없으면 매 capture의 frame-local
진단만 만든다. 지도 정합은 이전 frozen reference만 사용하며 현재 결과를 먼저
등록하지 않는다. 이 코어는 scene 승인·D checkpoint·wire world·제어 허가가 아니다.
"""

from dataclasses import asdict,dataclass,replace
import hashlib
import json
import math
from time import perf_counter_ns

from .inputs import PerceptionInput, FrameStamp
from .tape import TapeProfile, extract_tape
from .road import RoadProfile, infer_road
from .depth import DepthConfig
from ..mapping.local_map import EvidenceGrid, GridSpec, SourceGate
from ..mapping.registration import (PointFeature,SegmentFeature,FrozenReference,
    RegistrationProfile,register,transform_point,segment_distance)
from ..mapping.odometry import PoseEstimate
from ..mapping.alignment import MapOdomAlignment
from ..mapping.free_schedule import FreeScheduler,StoppingRegion


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _inside(point, polygon):
    if not polygon:
        return False
    x,y = point
    signs=[]
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        signs.append((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]))
    return all(s>=-1e-10 for s in signs) or all(s<=1e-10 for s in signs)


@dataclass(frozen=True)
class PipelineProfile:
    """미실측 초기 계산 profile. 통행 영역은 support와 독립된 명시 설치 정책이다."""

    grid: GridSpec = GridSpec()
    tape: TapeProfile = TapeProfile()
    road: RoadProfile = RoadProfile()
    depth: DepthConfig = DepthConfig()
    registration: RegistrationProfile = RegistrationProfile()
    permission_polygon: tuple = ()
    permission_source: str = 'UNCONFIGURED'
    max_free_cells: int = 8
    max_free_tiles: int = 4096
    max_free_ray_tests: int = 200_000
    free_focus_xy: tuple[float,float] = (1.,0.)
    free_ttl_us: int = 500_000
    max_observation_age_us: int = 500_000

    def __post_init__(self):
        if (type(self.grid) is not GridSpec or type(self.tape) is not TapeProfile
                or type(self.road) is not RoadProfile or type(self.depth) is not DepthConfig
                or type(self.registration) is not RegistrationProfile):
            raise ValueError('pipeline requires explicit typed profiles')
        if type(self.permission_polygon) is not tuple:
            raise ValueError('permission must be an immutable convex polygon')
        if (type(self.free_focus_xy) is not tuple or len(self.free_focus_xy)!=2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in self.free_focus_xy)):
            raise ValueError('free-work focus must be an explicit finite base XY point')
        if self.permission_polygon:
            if not 3 <= len(self.permission_polygon) <= 32:
                raise ValueError('permission polygon exceeds its bound')
            turns=[]
            for a,b,c in zip(self.permission_polygon,self.permission_polygon[1:]+self.permission_polygon[:1],
                             self.permission_polygon[2:]+self.permission_polygon[:2]):
                if any(len(p)!=2 or any(not math.isfinite(v) for v in p) for p in (a,b,c)):
                    raise ValueError('invalid permission coordinates')
                turns.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
            if not (all(v>0 for v in turns) or all(v<0 for v in turns)):
                raise ValueError('permission polygon must be strictly convex')
            if self.permission_source=='UNCONFIGURED':
                raise ValueError('permission policy provenance is required')
        for name,cap in (('max_free_cells',256),('max_free_tiles',4096),('max_free_ray_tests',2**20),
                         ('free_ttl_us',10_000_000),('max_observation_age_us',1_000_000)):
            value=getattr(self,name)
            if type(value) is not int or not 1<=value<=cap:
                raise ValueError('pipeline work/age cap exceeded: '+name)


@dataclass(frozen=True)
class PerceptionResult:
    summary: dict
    tape: object
    road: object
    depth: object
    registration: object
    map_snapshot: dict
    rgb: object
    neural: object = None


@dataclass(frozen=True)
class MapEvidenceUpdate:
    """같은 compute 호출의 실제 적용 사실. observer는 원 source proof와 즉시 결합한다."""
    grid: EvidenceGrid
    mutation: object
    observation: PerceptionInput
    depth: object
    raw_pose: object
    pose: object
    rays: object
    certificate: object = None
    cell_bounds: tuple | None = None
    targets: tuple = ()


class PerceptionPipeline:
    """단일 M 또는 offline runner 소유. 원본 1개와 유한 grid/reference만 유지한다."""

    def __init__(self, calibration, profile=None, *, targets=(),rolling_policy=None):
        from .calibration import CalibrationProfile
        if type(calibration) is not CalibrationProfile:
            raise ValueError('pipeline requires CalibrationProfile')
        self.calibration = calibration
        self.profile = PipelineProfile() if profile is None else profile
        if type(self.profile) is not PipelineProfile or type(targets) is not tuple or len(targets)>4:
            raise ValueError('invalid pipeline or target profile')
        self.targets=targets
        self._rolling_policy=rolling_policy
        if rolling_policy is not None:
            from ..mapping.map_lattice import GridWindow,RollingPolicy
            if type(rolling_policy) is not RollingPolicy:raise ValueError('explicit frozen rolling policy required')
            rolling_policy.check_window(GridWindow.from_spec(self.profile.grid))
        self.rgb_gate,self.depth_gate,self.nn_gate = SourceGate(),SourceGate(),SourceGate()
        self.grid = self.reference = self._context = None
        self._alignment = None
        self._restore_pending = False
        self._domain_cache = None
        self._last_tape = self._last_road = self._last_rgb = None
        self._last_neural = None
        self._last_rgb_key = self._last_capture = self._last_now = self._last_clock = None
        self._free_cursor = 0
        self._free_scheduler = None
        self.counts = dict(inputs=0,rgb=0,depth=0,nn=0,duplicate_rgb=0,duplicate_depth=0)

    def _base(self, point, pose):
        if pose is None:
            return point
        p=pose.sample
        return transform_point((point[0]-p.x,point[1]-p.y),(0.,0.,-p.yaw))

    def _xy_bounds(self, box, pose):
        if pose is None:
            return box.lower[0],box.lower[1],box.upper[0],box.upper[1]
        points=[(x,y) for x in (box.lower[0],box.upper[0]) for y in (box.lower[1],box.upper[1])]
        error=0.
        if pose is not None:
            p=pose.sample
            error=p.position_error_m+2*max(math.hypot(*v) for v in points)*math.sin(min(math.pi,p.yaw_error_rad)/2)
            points=[transform_point(v,(p.x,p.y,p.yaw)) for v in points]
        return min(v[0] for v in points)-error,min(v[1] for v in points)-error,max(v[0] for v in points)+error,max(v[1] for v in points)+error

    def _domain(self, pose, grid):
        import numpy as np
        if pose is None and self._domain_cache is not None:
            grid.set_domain(*self._domain_cache)
            return
        rows,cols=np.indices(grid.state.shape,dtype=np.float64)
        r=grid.spec.resolution_m
        x0=grid.spec.origin_xy[0]+cols*r
        y0=grid.spec.origin_xy[1]+rows*r
        x=np.stack((x0,x0,x0+r,x0+r),axis=-1)
        y=np.stack((y0,y0+r,y0,y0+r),axis=-1)
        error=0.
        if pose is not None:
            p=pose.sample
            dx,dy=x-p.x,y-p.y
            c,s=math.cos(p.yaw),math.sin(p.yaw)
            x,y=c*dx+s*dy,-s*dx+c*dy
            error=p.position_error_m+2*np.max(np.hypot(x,y),axis=-1)*math.sin(min(math.pi,p.yaw_error_rad)/2)
        def whole_cell(polygon):
            result=np.ones(grid.state.shape,dtype=np.bool_)
            if not polygon:
                return ~result
            area=sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(polygon,polygon[1:]+polygon[:1]))
            sign=1 if area>0 else -1
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                ex,ey=b[0]-a[0],b[1]-a[1]
                cross=sign*(ex*(y-a[1])-ey*(x-a[0]))
                # affine half-plane의 최소는 꼭짓점에 있다. pose box pad는
                # edge normal의 L1 norm으로 전체 XY rectangle을 포함한다.
                lower=np.min(cross,axis=-1)-error*(abs(ex)+abs(ey))
                result &= lower>=1e-10
            return result
        support=whole_cell(self.calibration.support_polygon)
        permission=whole_cell(self.profile.permission_polygon)
        if pose is None:
            self._domain_cache=(support,permission)
        grid.set_domain(support,permission)

    def _occlusion(self, evidence):
        from .occlusion import box_occlusion
        return box_occlusion(() if evidence is None else evidence.hit_boxes,self.calibration.rgb)

    def _features(self, graph, capture):
        points=[]
        if graph is not None:
            for strip in graph.strips:
                if not strip.observed:
                    continue
                # 각 component에 같은 수의 sample을 둔다. 긴 선을 독립 관측으로 늘리지 않는다.
                for index in sorted({len(strip.points_xy)//3,2*len(strip.points_xy)//3}):
                    points.append(PointFeature(strip.points_xy[index],strip.error_m,'strip:'+str(strip.local_id),capture))
        return tuple(points)

    def _reference_from(self, graph, capture, pose, revision):
        p=pose.sample
        segments=[]
        for strip in graph.strips:
            if not strip.observed:
                continue
            a,b=strip.points_xy[0],strip.points_xy[-1]
            curve_error=max(segment_distance(v,a,b) for v in strip.points_xy)
            radius=max(math.hypot(*v) for v in strip.points_xy)
            error=strip.error_m+curve_error+p.position_error_m+2*radius*math.sin(min(math.pi,p.yaw_error_rad)/2)
            segments.append(SegmentFeature(transform_point(a,(p.x,p.y,p.yaw)),transform_point(b,(p.x,p.y,p.yaw)),
                error,'strip:'+str(strip.local_id),capture))
        return FrozenReference(revision,self.calibration.sha256,tuple(segments))

    def process(self, observation, *, now_us=None, pose=None, checkpoint=None, completed_clock=None,
                motion_qualified=False,needed_mask=None,cycle_bound_us=None,completion_bound_us=None,diagnostics=None,
                needed_region_factory=None,evidence_observer=None,localization_valid_for_us=None,
                localization_position_drift_mps=None,localization_yaw_drift_rps=None,roi_reframe=None):
        """원 관측을 처리한다. 선택 clock은 입력과 같은 domain의 실제 완료시각이다.

        checkpoint는 계산 owner의 취소/진전 callback이다. 부분 상태를 변경하므로
        취소 가능한 운영 호출은 독립 후보 core에서 실행한 뒤 현재성을 확인해 채택한다.
        callback/clock을 생략한 offline 입력은 원 명시 시각을 그대로 사용한다.
        physical/noninstant FREE는 원 full-capture pose tube·명시 needed/budget을 요구한다.
        needed_mask는 원 grid bool mask 또는 수락한 Vehicle에서 만든 StoppingRegion이다.
        factory는 domain/정합 뒤 정확히 한 번 호출한다. observer는 실제 cell mutation만 받으며
        두 callback 모두 동일 compute owner의 순수 계산용이다. I/O/scene 승인 역할은 없다.
        roi_reframe는 수락한 MAP pose에서 mutation 전에 후보 창과 proof만 함께 옮기는
        내부 계산 port다. 격리 입력/중복 관측에서는 호출하지 않으며 atlas를 소유하지 않는다.
        """
        from .depth import analyze_depth
        from .free_space import certify_cell
        from .geometry import TimingDiagnostics
        if diagnostics is not None and type(diagnostics) is not TimingDiagnostics:
            raise ValueError('expected optional elapsed-only TimingDiagnostics')
        import numpy as np
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError('checkpoint must be a callable or None')
        if completed_clock is not None and not callable(completed_clock):
            raise ValueError('completion clock must be a callable or None')
        if needed_region_factory is not None and (not callable(needed_region_factory) or needed_mask is not None):
            raise ValueError('choose explicit needed mask or one bounded region factory')
        if evidence_observer is not None and not callable(evidence_observer):raise ValueError('evidence observer must be callable')
        if roi_reframe is not None and not callable(roi_reframe):raise ValueError('optional compute-local ROI port required')
        limits=(localization_valid_for_us,localization_position_drift_mps,localization_yaw_drift_rps)
        localization_configured=all(value is not None for value in limits)
        if any(value is not None for value in limits) and not localization_configured:
            raise ValueError('localization validity and both drift bounds must be supplied together')
        if localization_configured:
            if (type(localization_valid_for_us) is not int or not 1<=localization_valid_for_us<=1_000_000
                    or any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in limits[1:])):
                raise ValueError('invalid explicit localization lifetime/drift')
        operational_localization=bool(localization_configured and observation.kind=='LIVE') if type(observation) is PerceptionInput else False
        def alignment_current(at):
            alignment=self._alignment
            return bool(localization_configured and alignment is not None and alignment.has_metadata and alignment.valid_at(at)
                and (alignment.valid_for_us,alignment.position_drift_mps,alignment.yaw_drift_rps)==limits)
        if type(motion_qualified) is not bool:raise ValueError('motion qualification must be explicit bool')
        for value,positive in ((cycle_bound_us,True),(completion_bound_us,False)):
            if value is not None and (type(value) is not int or not int(positive)<=value<2**63):
                raise ValueError('explicit finite cycle/completion microsecond bounds required')
        def step(phase):
            if checkpoint is not None:
                checkpoint(phase)
        step(None)
        started=perf_counter_ns()
        if type(observation) is not PerceptionInput or observation.calibration_id!=self.calibration.sha256:
            raise ValueError('observation calibration mismatch')
        if observation.kind!='SYNTHETIC' and self.calibration.kind=='SYNTHETIC':
            raise ValueError('synthetic calibration cannot label a physical observation')
        if (observation.rgb.width,observation.rgb.height)!=(self.calibration.rgb.width,self.calibration.rgb.height):
            raise ValueError('RGB dimensions differ from the active calibration plane')
        if observation.depth is not None and (observation.depth.width,observation.depth.height)!=(self.calibration.right.width,self.calibration.right.height):
            raise ValueError('native depth dimensions differ from the RIGHT calibration plane')
        if pose is not None and (type(pose) is not PoseEstimate or pose.sample.clock_id!=observation.rgb.stamp.clock_id
                or pose.sample.calibration_id!=self.calibration.sha256 or pose.captured_us!=observation.capture_interval):
            raise ValueError('pose is not bound to the full original sensor capture interval')
        if pose is not None and pose.sample.origin=='ORACLE_SIMULATION' and observation.kind!='SYNTHETIC':
            raise ValueError('oracle pose cannot be used for a physical camera')
        available_us=max(observation.rgb.stamp.available_us,
                0 if observation.depth is None else observation.depth.stamp.available_us,
                0 if observation.nn is None else observation.nn.packet.received_j_us)
        if now_us is None:
            now_us=available_us
            if self._last_clock==observation.rgb.stamp.clock_id and self._last_now is not None:
                now_us=max(now_us,self._last_now)
        if (type(now_us) is not int or not available_us<=now_us<2**64
                or self._last_clock==observation.rgb.stamp.clock_id and self._last_now is not None and now_us<self._last_now):
            raise ValueError('processing time must advance in its original clock domain')
        self._last_now,self._last_clock=now_us,observation.rgb.stamp.clock_id
        processing_began_us=now_us
        self.counts['inputs']+=1
        rgb_new=self.rgb_gate.accept(observation.rgb.stamp,observation.rgb.sha256)
        depth_new=observation.depth is not None and self.depth_gate.accept(observation.depth.stamp,observation.depth.sha256)
        depth_skipped_unpaired=False
        if not rgb_new and observation.rgb.stamp.key!=self._last_rgb_key and pose is None:
            depth_skipped_unpaired=bool(depth_new)
            depth_new=False
        nn_new=False
        if observation.nn is not None:
            packet=observation.nn.packet
            nn_stamp=FrameStamp(observation.rgb.stamp.source_id,'NN:'+str(observation.nn_key[1]),packet.sequence,
                observation.rgb.stamp.clock_id,None,packet.received_j_us)
            nn_new=self.nn_gate.accept(nn_stamp,hashlib.sha256(packet.payload).hexdigest())
            if nn_new:
                from .neural import build_neural_layer
                self._last_neural=build_neural_layer(observation,self.calibration)
        self.counts['rgb']+=int(rgb_new); self.counts['depth']+=int(depth_new); self.counts['nn']+=int(nn_new)
        self.counts['duplicate_rgb']+=int(not rgb_new)
        self.counts['duplicate_depth']+=int(observation.depth is not None and not depth_new)
        capture=_digest(observation.rgb.stamp.key)
        context=(observation.kind,self.calibration.sha256,observation.rgb.stamp.clock_id,
                 'frame:'+capture if pose is None else 'odom:'+pose.sample.epoch)
        if self._restore_pending:
            if (context[:3]!=self._context[:3] or pose is not None and context[-1]!=self._context[-1]):
                raise ValueError('restored map cannot cross its admitted current source/clock/odom context')
            # pose 미수락은 옛 reference를 지우는 새 map 생성 사유가 아니다.
            context=self._context
        if self._context is not None and self._context[0]!=observation.kind:
            raise ValueError('one processor cannot mix live, replay and synthetic authority domains')
        if pose is not None and not rgb_new and self._context is not None and context!=self._context:
            raise ValueError('a duplicate capture cannot replace its odom context')
        if self.grid is None or rgb_new and context!=self._context:
            self.grid=EvidenceGrid(self.profile.grid,frame_id=context[-1],calibration_id=self.calibration.sha256,
                clock_id=observation.rgb.stamp.clock_id,kind=observation.kind,free_ttl_us=self.profile.free_ttl_us)
            self.reference=None
            self._alignment=None
            self._free_scheduler=None
            self._context=context
        raw_pose=pose
        alignment_invalid=False
        if pose is not None and self._alignment is not None:
            try:
                pose=self._alignment.apply(pose)
            except ValueError:
                alignment_invalid=True
                pose=None
        input_done=perf_counter_ns()
        step('INPUT_COMPLETED')
        evidence=None
        if depth_new:
            evidence=analyze_depth(observation.depth.array(),self.calibration.right,self.calibration.plane,
                stamp=observation.depth.stamp,calibration_id=self.calibration.sha256,
                input_kind=observation.kind,profile=self.calibration,config=self.profile.depth,
                **({} if diagnostics is None else dict(diagnostics=diagnostics)))
        depth_done=perf_counter_ns()
        step('DEPTH_COMPLETED')
        if rgb_new:
            self._last_rgb_key,self._last_capture=observation.rgb.stamp.key,capture
            self._last_rgb=observation.rgb.array()
            self._last_tape=extract_tape(self._last_rgb,self.calibration.rgb,self.calibration.plane,self.profile.tape,
                stamp=observation.rgb.stamp,occlusion_mask=self._occlusion(evidence),nn_candidates=observation.nn,
                **({} if diagnostics is None else dict(diagnostics=diagnostics)))
            self._last_road=infer_road(self._last_tape,self.profile.road)
        if self._last_neural is not None and self._last_road is not None and (rgb_new or nn_new):
            from .neural import assist_road
            # late NN은 같은 원 RGB의 후보층만 갱신한다. source가 다르면 이 함수는
            # 현재 기하를 보존하며 지도/정합의 새 관측이나 시각을 발급하지 않는다.
            self._last_road=assist_road(self._last_road,self._last_neural,self.calibration)
        tape_done=perf_counter_ns()
        step('TAPE_COMPLETED')
        if completed_clock is not None:
            stage_us=completed_clock()
            if type(stage_us) is not int or not now_us<=stage_us<2**64:raise ValueError('registration clock must follow original processing time')
            now_us=stage_us
        registration=None
        geometry_qualified=(self.calibration.metric_qualified and self.calibration.time_qualified
                            and observation.capture_interval is not None)
        quarantined=alignment_invalid or pose is not None and not geometry_qualified
        if operational_localization and self._alignment is not None and not alignment_current(now_us):quarantined=True
        if pose is not None and rgb_new and geometry_qualified and self._last_tape is not None and self._last_tape.status=='VALID':
            points=self._features(self._last_tape,capture)
            if points and self.reference is not None and self.reference.segments:
                registration=register(points,self.reference,pose.prior,self.profile.registration,calibration_id=self.calibration.sha256)
                if registration.status=='BOUNDED':
                    x,y,yaw=registration.representative
                    fitted=replace(pose.sample,x=x,y=y,yaw=yaw,
                        position_error_m=registration.position_error_m,yaw_error_rad=registration.yaw_error_rad)
                    candidate_pose=PoseEstimate(fitted,pose.captured_us,pose.bracket_us)
                    metadata={} if not localization_configured else dict(observed_j_interval=raw_pose.captured_us,
                        registration_revision=self.reference.revision+1,valid_for_us=localization_valid_for_us,
                        position_drift_mps=localization_position_drift_mps,yaw_drift_rps=localization_yaw_drift_rps)
                    candidate_alignment=MapOdomAlignment.from_match(raw_pose,candidate_pose,**metadata)
                    if completed_clock is not None:
                        stage_us=completed_clock()
                        if type(stage_us) is not int or not now_us<=stage_us<2**64:raise ValueError('accepted registration clock moved backwards')
                        now_us=stage_us
                    if operational_localization and not candidate_alignment.valid_at(now_us):
                        quarantined=True
                    else:
                        pose=candidate_pose;self._alignment=candidate_alignment;self._restore_pending=False
                        quarantined=False
                else:
                    quarantined=True
            if (points and not quarantined
                    and pose.sample.position_error_m<=self.profile.registration.max_position_error_m
                    and pose.sample.yaw_error_rad<=self.profile.registration.max_yaw_error_rad
                    and (self.reference is None or registration is not None and registration.status=='BOUNDED')):
                # 이전 reference로 판정한 뒤에만 다음 revision을 만든다. 최초 seed는 명시 odom anchor다.
                self.reference=self._reference_from(self._last_tape,capture,pose,0 if self.reference is None else self.reference.revision+1)
        if pose is not None and (pose.sample.position_error_m>self.profile.registration.max_position_error_m
                or pose.sample.yaw_error_rad>self.profile.registration.max_yaw_error_rad):
            quarantined=True
        if self._restore_pending:quarantined=True
        if operational_localization and self._alignment is not None and not alignment_current(now_us):quarantined=True
        grid=self.grid
        if quarantined:
            # 기존 atlas/reference는 그대로 두고 새 관측은 별도 body-frame 진단으로만 처리한다.
            grid=EvidenceGrid(self.profile.grid,frame_id='quarantine:'+capture,calibration_id=self.calibration.sha256,
                clock_id=observation.rgb.stamp.clock_id,kind=observation.kind,free_ttl_us=self.profile.free_ttl_us)
            pose=None
        elif roi_reframe is not None and pose is not None and (rgb_new or depth_new):
            moved=roi_reframe(self,pose)
            if type(moved) is not bool:raise ValueError('ROI port must report its actual window change')
            if moved:
                self._domain_cache=None
                self._free_scheduler=None
                self._free_cursor=0
        if rgb_new or depth_new:
            self._domain(pose,grid)
        registration_done=perf_counter_ns()
        step('REGISTRATION_COMPLETED')
        if evidence is not None:
            from .point_batch import PointBoundsBatch
            if type(evidence.hit_boxes) is PointBoundsBatch:
                boxes=evidence.hit_boxes
                if pose is not None:
                    p=pose.sample
                    boxes=boxes.transformed_xy((p.x,p.y,p.yaw),position_error_m=p.position_error_m,
                                               yaw_error_rad=p.yaw_error_rad)
            else:
                boxes=tuple(self._xy_bounds(b,pose) for b in evidence.hit_boxes)
            origin=self.calibration.right.translation[:2]
            if pose is not None:
                p=pose.sample
                origin=transform_point(origin,(p.x,p.y,p.yaw))
            hit_observer=None if evidence_observer is None else lambda delta:evidence_observer(
                MapEvidenceUpdate(grid,delta,observation,evidence,raw_pose,pose,evidence.patches))
            grid.add_hits(boxes,observation.depth.stamp,view_id=observation.depth.stamp.stream_id,origin_xy=origin,observer=hit_observer)
        hits_done=perf_counter_ns()
        step('HITS_COMPLETED')
        certificates=[]
        certified_cells=[]
        certified_details={}
        attempted_cells=[]
        if needed_region_factory is not None:
            if completed_clock is not None:
                stage_us=completed_clock()
                if type(stage_us) is not int or not now_us<=stage_us<2**64:raise ValueError('needed-region clock must follow original processing time')
                now_us=stage_us
            needed_mask=needed_region_factory(grid,pose,now_us)
            if needed_mask is not None and type(needed_mask) is not StoppingRegion:
                raise ValueError('needed-region factory must return an original StoppingRegion or None')
        remaining=self.profile.max_free_ray_tests
        depth_age=None if observation.depth is None else observation.depth.stamp.age_us(now_us,observation.rgb.stamp.clock_id)
        capture_interval=observation.capture_interval
        instantaneous_synthetic=(observation.kind=='SYNTHETIC' and capture_interval is not None
            and capture_interval[0]==capture_interval[1])
        free_patches=() if evidence is None else evidence.patches
        temporal_free_status=('INSTANTANEOUS_SYNTHETIC' if instantaneous_synthetic else
            'CAPTURE_TIME_UNKNOWN' if capture_interval is None else 'MOTION_UNQUALIFIED' if not motion_qualified
            else 'FULL_CAPTURE_POSE_UNAVAILABLE' if raw_pose is None else 'MOTION_RAY_TUBE_UNAVAILABLE')
        if (not instantaneous_synthetic and evidence is not None and capture_interval is not None and motion_qualified):
            from .motion import widen_motion_rays
            # registration이 좁힌 error가 원 exposure의 motion hull을 줄이지 않게 한다.
            free_patches=widen_motion_rays(evidence.patches,observation.depth.stamp,self.calibration,raw_pose,
                input_kind=observation.kind,motion_qualified=True,capture_interval=capture_interval)
            if bool(np.any(free_patches.qualified)):temporal_free_status='FULL_CAPTURE_MOTION_RAY_TUBE'
        temporal_bound=instantaneous_synthetic or temporal_free_status=='FULL_CAPTURE_MOTION_RAY_TUBE'
        if temporal_bound and not instantaneous_synthetic and completed_clock is None:
            temporal_bound=False;temporal_free_status='ACTUAL_COMPLETION_CLOCK_REQUIRED'
        can_free=bool(evidence is not None and self.targets and self.calibration.free_qualified and temporal_bound
            and depth_age is not None and depth_age<=self.profile.max_observation_age_us
            and observation.rgb.stamp.captured_us is not None and self.profile.permission_polygon
            and (instantaneous_synthetic or not quarantined))
        localization_blocks_free=bool(not instantaneous_synthetic and observation.kind=='LIVE'
            and (operational_localization or self._alignment is not None) and not alignment_current(now_us))
        if localization_blocks_free:can_free=False
        region=needed_mask if type(needed_mask) is StoppingRegion else None
        needed=None
        scheduling_status='NEEDED_REGION_UNAVAILABLE' if needed_region_factory is not None and needed_mask is None else 'NEEDED_REGION_UNKNOWN'
        plan=None
        if region is not None:
            if (region.spec!=self.grid.spec or region.frame_id!=self.grid.frame_id or region.calibration_id!=self.calibration.sha256
                    or region.clock_id!=observation.rgb.stamp.clock_id or raw_pose is None or region.odom_epoch!=raw_pose.sample.epoch):
                raise ValueError('stopping region differs from current map/clock/odom/calibration')
            if cycle_bound_us is not None and cycle_bound_us!=region.cycle_bound_us or completion_bound_us is not None and completion_bound_us!=region.completion_bound_us:
                raise ValueError('stopping region scheduling bounds changed')
            cycle_bound_us,completion_bound_us=region.cycle_bound_us,region.completion_bound_us
            needed=region.mask.copy()
            if not region.complete:scheduling_status='NEEDED_REGION_OUTSIDE_GRID';can_free=False
            elif not region.valid_at(now_us):scheduling_status='NEEDED_REGION_EXPIRED';can_free=False
            elif not region.matches_pose(pose):scheduling_status='NEEDED_POSE_CHANGED';can_free=False
        elif needed_mask is not None:
            if type(needed_mask) is not np.ndarray or needed_mask.dtype!=np.bool_ or needed_mask.shape!=grid.state.shape:
                raise ValueError('needed region requires exact same-shape bool grid')
            needed=needed_mask.copy()
        selected=()
        legacy_positions=None
        if needed is not None and cycle_bound_us is not None and completion_bound_us is not None:
            if grid.spec.width*grid.spec.height>4096:
                scheduling_status='SCHEDULER_GRID_LIMIT';can_free=False
            else:
                schedule_context=(observation.kind,self.calibration.sha256,grid.clock_id,grid.frame_id)
                if quarantined or not can_free or not np.any(needed):
                    scheduler=FreeScheduler(grid.spec,context_key=schedule_context)
                elif self._free_scheduler is None:
                    scheduler=FreeScheduler(grid.spec,context_key=schedule_context)
                    self._free_scheduler=scheduler
                else:scheduler=self._free_scheduler
                plan=scheduler.plan(context_key=schedule_context,needed_mask=needed,support=grid.support,permission=grid.permission,
                    state=grid.state,observed_us=grid.observed_us,time_known=grid.time_known,now_us=now_us,
                    capture_lower_us=None if capture_interval is None else capture_interval[0],max_cells=self.profile.max_free_cells,
                    max_ray_tests=remaining,cycle_bound_us=cycle_bound_us,completion_bound_us=completion_bound_us,free_ttl_us=self.profile.free_ttl_us)
                selected=plan.cells
                if not plan.needed_count:can_free=False
                if scheduling_status not in ('NEEDED_REGION_OUTSIDE_GRID','NEEDED_REGION_EXPIRED','NEEDED_POSE_CHANGED'):scheduling_status=plan.status
        elif needed is not None:
            scheduling_status='SCHEDULING_BOUNDS_UNKNOWN';can_free=False
        elif not instantaneous_synthetic:can_free=False
        if can_free and needed is None:
            # 원 instantaneous synthetic 진단의 focus/cursor 호환만 보존한다.
            count=grid.spec.width*grid.spec.height
            focus=self.profile.free_focus_xy
            if pose is not None:
                p=pose.sample
                focus=transform_point(focus,(p.x,p.y,p.yaw))
            def priority(index):
                row,col=divmod(index,grid.spec.width)
                x0,y0,x1,y1=grid.spec.bounds(col,row)
                return ((x0+x1)/2-focus[0])**2+((y0+y1)/2-focus[1])**2,index
            order=sorted(range(count),key=priority)
            legacy_cursor_before=self._free_cursor
            legacy_positions=[]
            attempted=[];scanned=0
            while len(attempted)<self.profile.max_free_cells and scanned<count:
                index=order[self._free_cursor%count]
                self._free_cursor=(self._free_cursor+1)%count
                scanned+=1
                row,col=divmod(index,grid.spec.width)
                if not (grid.support[row,col] and grid.permission[row,col]):
                    continue
                attempted.append((col,row))
                legacy_positions.append(self._free_cursor)
            selected=tuple(attempted)
        if can_free:
            for col,row in selected:
                if remaining<=0:break
                step(None)
                attempted_cells.append((col,row))
                x0,y0,x1,y1=grid.spec.bounds(col,row)
                corners=[self._base(v,pose) for v in ((x0,y0),(x0,y1),(x1,y0),(x1,y1))]
                error=0. if pose is None else pose.sample.position_error_m+2*max(math.hypot(*v) for v in corners)*math.sin(min(math.pi,pose.sample.yaw_error_rad)/2)
                bounds=(min(v[0] for v in corners)-error,min(v[1] for v in corners)-error,
                        max(v[0] for v in corners)+error,max(v[1] for v in corners)+error)
                certificate=certify_cell(bounds,free_patches,self.targets,self.calibration,
                    permission_polygon=self.profile.permission_polygon,max_tiles=self.profile.max_free_tiles,
                    max_ray_tests=remaining,hazards=evidence.hit_boxes)
                work=certificate.work
                consumed=work.get('ray_tests',work.get('tests',0)) if type(work) is dict else int(work)
                if not 0<=consumed<=remaining:
                    raise ValueError('certificate work exceeds its original shared budget')
                remaining-=consumed
                certificates.append(dict(cell=[col,row],status=certificate.status,complete=certificate.complete,reason=certificate.reason,work=work))
                if certificate.free:
                    certified_cells.append((col,row));certified_details[(col,row)]=(certificate,bounds)
        if plan is not None:scheduler.note_attempts(plan,tuple(attempted_cells))
        elif legacy_positions is not None:
            self._free_cursor=legacy_positions[len(attempted_cells)-1] if attempted_cells else legacy_cursor_before
        free_done=perf_counter_ns()
        step('FREE_COMPLETED')
        if completed_clock is not None:
            completed_us=completed_clock()
            if type(completed_us) is not int or not now_us<=completed_us<2**64:
                raise ValueError('completion must follow processing in the original clock domain')
            now_us=completed_us
            self._last_now=completed_us
        actual_depth_age=None if observation.depth is None else observation.depth.stamp.age_us(now_us,observation.rgb.stamp.clock_id)
        completion_exceeded=completion_bound_us is not None and now_us-processing_began_us>completion_bound_us
        final_free=bool(can_free and not completion_exceeded and actual_depth_age is not None
            and actual_depth_age<=min(self.profile.max_observation_age_us,self.profile.free_ttl_us)
            and (region is None or region.valid_at(now_us))
            and (observation.kind!='LIVE' or not (operational_localization or self._alignment is not None) or alignment_current(now_us)))
        if completion_exceeded:scheduling_status='ACTUAL_COMPLETION_BOUND_EXCEEDED'
        elif region is not None and not region.valid_at(now_us):scheduling_status='NEEDED_REGION_EXPIRED'
        elif certified_cells and not final_free:temporal_free_status='OBSERVATION_EXPIRED_DURING_COMPUTE'
        applied_free=0
        if final_free and certified_cells:
            # 기존 cell별 revision 전이를 유지하되 완료 검산 전에는 새 FREE를 적용하지 않는다.
            for cell in certified_cells:
                certificate,bounds=certified_details[cell]
                free_observer=None if evidence_observer is None else lambda delta:evidence_observer(
                    MapEvidenceUpdate(grid,delta,observation,evidence,raw_pose,pose,free_patches,certificate,bounds,self.targets))
                applied_free+=grid.add_free((cell,),observation.depth.stamp,view_id=observation.depth.stamp.stream_id,
                    origin_xy=origin,certificate_complete=True,observer=free_observer)
        snapshot=grid.snapshot(now_us,clock_id=observation.rgb.stamp.clock_id)
        schedule_summary=dict(status=scheduling_status,renewal_proven=False,necessary_capacity_ok=False,after_all_needed_live_fresh=False)
        if plan is not None:
            schedule_summary.update(asdict(plan),selection_status=plan.status,status=scheduling_status,renewal_proven=False,
                necessary_capacity_ok=bool(plan.necessary_capacity_ok and can_free and not completion_exceeded
                    and (region is None or region.complete and region.valid_at(now_us))))
            schedule_summary['after_all_needed_live_fresh']=bool(np.any(needed) and np.all((snapshot['arrays']['state']==1)[needed])
                and (region is None or region.complete and region.valid_at(now_us)) and not completion_exceeded)
        schedule_summary['attempted_cells']=tuple(attempted_cells)
        alignment=self._alignment
        localization_status=('INITIAL_ANCHOR_UNQUALIFIED' if alignment is None else
            'ALIGNMENT_LEGACY_DIAGNOSTIC' if not alignment.has_metadata else
            'LOCALIZATION_PROFILE_UNCONFIGURED' if not localization_configured else
            'LOCALIZATION_PROFILE_CHANGED' if (alignment.valid_for_us,alignment.position_drift_mps,alignment.yaw_drift_rps)!=limits else
            'ALIGNMENT_EXPIRED' if not alignment.valid_at(now_us) else 'QUALIFIED')
        free_status=('NO_NEW_DEPTH_EVIDENCE' if evidence is None else
            'TARGET_PROFILE_UNQUALIFIED' if not self.targets or any(not target.qualified for target in self.targets) else
            'CALIBRATION_UNQUALIFIED' if not self.calibration.free_qualified else
            temporal_free_status if not temporal_bound else
            'REGISTRATION_QUARANTINED' if quarantined and not instantaneous_synthetic else
            localization_status if localization_blocks_free or operational_localization and not alignment_current(now_us) else
            'PERMISSION_POLICY_UNCONFIGURED' if not self.profile.permission_polygon else
            'OBSERVATION_EXPIRED' if actual_depth_age is None or actual_depth_age>min(self.profile.max_observation_age_us,self.profile.free_ttl_us) else
            scheduling_status if not final_free else 'QUALIFIED_CELL_ATTEMPTS')
        step('SNAPSHOT_COMPLETED')
        ended=perf_counter_ns()
        summary=dict(schema_version=1,kind=observation.kind,calibration_id=self.calibration.sha256,
            capture_id=capture,tape_capture_id=self._last_capture,rgb_source_key=observation.rgb.stamp.key,
            depth_source_key=None if observation.depth is None else observation.depth.stamp.key,
            captured_us=observation.capture_interval,rgb_captured_us=observation.rgb.stamp.captured_us,now_us=now_us,
            rgb_updated=rgb_new,depth_updated=bool(depth_new),depth_skipped_unpaired=depth_skipped_unpaired,
            nn_updated=nn_new,counts=dict(self.counts),
            tape_status=None if self._last_tape is None else self._last_tape.status,
            strips=0 if self._last_tape is None else len(self._last_tape.strips),
            road_status=None if self._last_road is None else self._last_road.status,
            road_hypotheses=0 if self._last_road is None else len(self._last_road.hypotheses),
            tape_reasons=[] if self._last_tape is None else list(self._last_tape.reasons),
            tape_work={} if self._last_tape is None else dict(self._last_tape.work),
            road_reasons=[] if self._last_road is None else list(self._last_road.reasons),
            road_semantic_scored=0 if self._last_road is None else self._last_road.semantic_scored,
            neural_layer=None if self._last_neural is None else self._last_neural.descriptor(),
            neural_matches_current_rgb=self._last_neural is not None and self._last_neural.rgb_source_key==self._last_rgb_key,
            depth_mode=None if evidence is None else evidence.mode,hit_boxes=0 if evidence is None else len(evidence.hit_boxes),
            depth_valid=0 if evidence is None else evidence.valid_count,
            depth_unknown=0 if evidence is None else evidence.unknown_count,
            omitted_hits=0 if evidence is None else evidence.omitted_hit_count,
            omitted_ray_patches=0 if evidence is None else evidence.omitted_patch_count,
            free_certificates=certificates,free_ray_tests=self.profile.max_free_ray_tests-remaining,
            free_temporal_status=temporal_free_status,
            free_status=free_status,
            free_schedule=schedule_summary,free_cells_applied=applied_free,
            map_frame=snapshot['frame_id'],map_counts=snapshot['counts'],map_revision=snapshot['revision'],
            odom_origin=None if pose is None else pose.sample.origin,
            input_odom_origin=None if raw_pose is None else raw_pose.sample.origin,
            mapped_pose=None if pose is None else [pose.sample.x,pose.sample.y,pose.sample.yaw],
            map_from_odom=None if self._alignment is None else self._alignment.pose,
            alignment_metadata=None if alignment is None else alignment.fields(),
            localization_status=localization_status,
            localization_qualified=bool(observation.kind=='LIVE' and raw_pose is not None and pose is not None
                and geometry_qualified and alignment_current(now_us) and not quarantined),
            quarantined=quarantined,
            registration_status=None if registration is None else registration.status,
            reference_revision=None if self.reference is None else self.reference.revision,
            geometric_model_qualified=bool(geometry_qualified),metric_ready=False,world_valid=False,live_authority=False,
            costs_us=dict(input=(input_done-started)//1000,depth=(depth_done-input_done)//1000,
                tape=(tape_done-depth_done)//1000,registration=(registration_done-tape_done)//1000,
                hits=(hits_done-registration_done)//1000,free=(free_done-hits_done)//1000,
                snapshot=(ended-free_done)//1000,total=(ended-started)//1000))
        if diagnostics is not None: summary['diagnostic_timings']=diagnostics.snapshot()
        return PerceptionResult(summary,self._last_tape,self._last_road,evidence,registration,snapshot,self._last_rgb,self._last_neural)


def render_diagnostics(result, calibration):
    """현재 한 장의 overlay와 bounded map 이미지를 반환한다. 파일/HTTP는 호출자 소유다."""
    import cv2
    import numpy as np
    from .geometry import ground_to_pixel
    image=result.rgb.copy()
    if result.neural is not None and result.summary.get('neural_matches_current_rgb'):
        selected=result.neural.road_mask!=0
        image[selected]=(image[selected].astype(np.uint16)*3+np.array((140,40,140),dtype=np.uint16))//4
        for detection in result.neural.detections_source:
            x0,y0,x1,y1=map(round,detection.xyxy)
            cv2.rectangle(image,(x0,y0),(x1,y1),(220,80,220),1)
    if result.tape is not None:
        for strip in result.tape.strips:
            pixels=[ground_to_pixel(x,y,calibration.rgb,calibration.plane) for x,y in strip.points_xy]
            for a,b in zip(pixels,pixels[1:]):
                if a is not None and b is not None:
                    cv2.line(image,tuple(map(round,a)),tuple(map(round,b)),(40,230,230),1)
    if result.road is not None:
        for hypothesis in result.road.hypotheses[:8]:
            for points,color in ((hypothesis.left_center,(90,220,70)),(hypothesis.right_center,(220,180,40))):
                pixels=[ground_to_pixel(x,y,calibration.rgb,calibration.plane) for x,y in points]
                for a,b in zip(pixels,pixels[1:]):
                    if a is not None and b is not None:
                        cv2.line(image,tuple(map(round,a)),tuple(map(round,b)),color,1)
    cv2.putText(image,str(result.summary['kind'])+' / '+str(result.summary['road_status']),
        (8,18),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,255),1)
    state=result.map_snapshot['arrays']['state']
    map_image=np.full((*state.shape,3),90,dtype=np.uint8)
    map_image[state==1]=(80,180,60)
    map_image[state==2]=(40,40,230)
    map_image[result.map_snapshot['arrays']['unordered_hit']]=(0,150,230)
    return image,np.flipud(map_image)
