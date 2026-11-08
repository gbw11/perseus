"""현재 stopping/body/error 필요 셀을 먼저 갱신하는 유한 FREE 작업 선택기.

needed_mask는 호출자가 원 VehicleSample/수락한 motion bounds에서 만든 whole-cell 영역이다.
Pi path/임무 wire를 만들거나 retained-free를 live 근거로 합치지 않는다. 선택은 시도일 뿐
certificate 성공이 아니다. 필요한 영역을 원 TTL/처리 예산 안에 갱신할 수 없음을 숨기지 않는다.
"""

from dataclasses import dataclass
import math

from .local_map import FREE, OCCUPIED, GridSpec


@dataclass(frozen=True)
class FreePlan:
    cells: tuple
    priorities: tuple
    ray_budget: int
    needed_count: int
    due_needed_count: int
    selected_needed_count: int
    unavailable_needed_count: int
    occupied_needed_count: int
    due_not_scheduled_count: int
    all_needed_live_fresh: bool
    necessary_capacity_ok: bool
    minimum_refresh_cycles: int | None
    status: str

    @property
    def renewal_proven(self):
        """계산량 산술과 작업 예약만으로 실제 부재 witness/미래 갱신 성공을 발급하지 않는다."""
        return False


def _uint(value, *, minimum=0, maximum=2**64 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('invalid original scheduler time/budget')
    return value


@dataclass(frozen=True)
class StoppingRegion:
    """원 source/pose와 명시 운동 가정의 보수 whole-cell 요청. mask가 존재해도 관측 가능/갱신 성공은 아니다."""
    spec: GridSpec
    frame_id: str
    calibration_id: str
    clock_id: str
    odom_epoch: str
    source_epochs: tuple
    vehicle_key: tuple
    vehicle_sha256: str
    built_us: int
    valid_until_us: int
    cycle_bound_us: int
    completion_bound_us: int
    bounds_xy: tuple
    radius_m: float
    complete: bool
    _mask: bytes
    _vehicle: object
    _admission: object
    _pose: object

    @property
    def mask(self):
        import numpy as np
        return np.frombuffer(self._mask,dtype=np.bool_).reshape(self.spec.height,self.spec.width)

    def valid_at(self,now_us):
        if type(now_us) is not int or not self.built_us<=now_us<self.valid_until_us:return False
        source=self._admission.context.source
        return ((source['map_epoch'],source['frame_epoch'],source['odom_epoch'])==self.source_epochs
            and self._admission.current_vehicle(self._vehicle,now_us))

    def matches_pose(self,pose):
        """동일한 grid 이름만으로 새 정합의 다른 nominal pose에 필요 영역을 붙이지 않는다."""
        return pose==self._pose


def stopping_region(spec,vehicle,pose,calibration,*,admission,frame_id,now_us,max_speed_mps,
                    min_braking_mps2,reaction_latency_us,cycle_bound_us,completion_bound_us,body_error_m):
    """어떤 조향/진행 방향에도 포함되는 후륜축 중심 swept disk의 바깥 사각형을 whole-cell로 덮는다.

    원 Vehicle·full PoseEstimate와 현재 J lease를 사용한다. 전체 speed 상한 v와 보장된
    최소 감속 a에서 이동거리 v*(원 age+reaction+cycle+completion)+v²/(2a)를 쓴다.
    차체 corner 반경이 모든 yaw를 덮으므로 별도 작은 yaw 가정이나 Pi 경로를 만들지 않는다.
    max speed/braking/latency/body error는 호출자가 실제 수락한 가정이며 실측 기본값은 없다.
    """
    import numpy as np
    from .odometry import PoseEstimate
    from ..perception.calibration import CalibrationProfile
    from ..transport.sensor_samples import SensorSamples,VehicleEvidence
    if type(spec) is not GridSpec or spec.width*spec.height>4096:raise ValueError('stopping region grid exceeds4096 cells')
    if type(vehicle) is not VehicleEvidence or type(admission) is not SensorSamples or not admission.current_vehicle(vehicle,now_us):
        raise ValueError('actual admitted current Vehicle evidence required')
    if type(calibration) is not CalibrationProfile or calibration.sha256!=admission.context.calibration.sha256:
        raise ValueError('stopping body/calibration differs from current source')
    if type(pose) is not PoseEstimate or type(frame_id) is not str or not 1<=len(frame_id)<=256:raise ValueError('explicit full pose/map frame required')
    p=pose.sample;source=admission.context.source
    if (p.origin!='ODOMETRY' or p.clock_id!=source['clock_epoch'] or p.calibration_id!=calibration.sha256 or p.epoch!=source['odom_epoch']
            or type(pose.captured_us) is not tuple or len(pose.captured_us)!=2 or type(pose.bracket_us) is not tuple or len(pose.bracket_us)!=2
            or any(type(v) is not int or not 0<=v<2**64 for v in (*pose.captured_us,*pose.bracket_us))
            or not pose.bracket_us[0]<=pose.captured_us[0]<=p.at_us<=pose.captured_us[1]<=min(now_us,pose.bracket_us[1])):
        raise ValueError('stopping pose lacks original full interval/context bracket')
    for value in (max_speed_mps,min_braking_mps2):
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=100:raise ValueError('explicit finite positive speed/braking bound required')
    if max_speed_mps<admission.speed:raise ValueError('stopping speed cannot be smaller than admitted sensor motion bound')
    if type(body_error_m) not in (int,float) or not math.isfinite(body_error_m) or not 0<=body_error_m<=10:raise ValueError('explicit accepted body error bound required')
    reaction=_uint(reaction_latency_us,maximum=10_000_000)
    cycle=_uint(cycle_bound_us,minimum=1,maximum=10_000_000)
    completion=_uint(completion_bound_us,maximum=10_000_000)
    if abs(vehicle.message().velocity_mps)>max_speed_mps:raise ValueError('actual Vehicle exceeds admitted stopping speed')
    age=now_us-min(vehicle.j_interval[0],pose.captured_us[0])
    seconds=math.nextafter((age+reaction+cycle+completion)/1e6,math.inf)
    body=calibration.body
    body_radius=math.nextafter(math.hypot(max(body.front,body.rear),body.width/2),math.inf)
    travel=math.nextafter(max_speed_mps*seconds,math.inf)
    speed_squared=math.nextafter(max_speed_mps*max_speed_mps,math.inf)
    denominator=math.nextafter(2*min_braking_mps2,-math.inf)
    braking=math.nextafter(speed_squared/denominator,math.inf)
    radius=body_radius
    for pad in (body_error_m,p.position_error_m,travel,braking):radius=math.nextafter(radius+pad,math.inf)
    if not math.isfinite(radius):raise ValueError('stopping envelope is not finitely bounded')
    bounds=(math.nextafter(p.x-radius,-math.inf),math.nextafter(p.y-radius,-math.inf),
        math.nextafter(p.x+radius,math.inf),math.nextafter(p.y+radius,math.inf))
    ox,oy=spec.origin_xy;r=spec.resolution_m
    rows,cols=np.indices((spec.height,spec.width),dtype=np.float64)
    x0=ox+cols*r;y0=oy+rows*r
    # 닫힌 sweep와 접촉하는 cell 전체가 필요하다. center sampling으로 가장자리를 누락하지 않는다.
    needed=(np.nextafter(x0,-np.inf)<=bounds[2])&(np.nextafter(x0+r,np.inf)>=bounds[0])&(
        np.nextafter(y0,-np.inf)<=bounds[3])&(np.nextafter(y0+r,np.inf)>=bounds[1])
    complete=bool(ox<=bounds[0] and oy<=bounds[1] and bounds[2]<=ox+spec.width*r and bounds[3]<=oy+spec.height*r)
    return StoppingRegion(spec,frame_id,calibration.sha256,p.clock_id,p.epoch,
        (source['map_epoch'],source['frame_epoch'],source['odom_epoch']),vehicle.source_key,vehicle.wire_sha256,now_us,
        min(vehicle.valid_until_j_us,now_us+cycle+completion),cycle,completion,bounds,radius,complete,
        needed.tobytes(),vehicle,admission,pose)


class FreeScheduler:
    """최대4096셀. 원 context·needed bytes·cursor와 최신 유한 선택만 보존한다."""

    def __init__(self, spec, *, context_key):
        if type(spec) is not GridSpec or spec.width * spec.height > 4096:
            raise ValueError('FREE scheduler grid exceeds4096 cells')
        if (type(context_key) is not tuple or not 1 <= len(context_key) <= 8
                or any(type(value) not in (int, str) or type(value) is str and len(value) > 256 for value in context_key)):
            raise ValueError('explicit bounded original map/calibration/clock context required')
        self.spec, self.context_key = spec, context_key
        self._previous_needed = bytes(spec.width * spec.height)
        self._required_cursor = self._surround_cursor = 0
        self._last_plan = self._cursor_before = None

    def note_attempts(self,plan,attempted_cells):
        """최신 선택의 실제 시도 prefix만 반영한다. ray budget으로 미시도한 셀을 건너뛰지 않는다.

        plan 단독 호출은 기존처럼 모든 선택을 시도로 간주한다. 공유 ray budget을 쓰는
        caller는 다음 plan 전에 한 번 보고한다. 이것은 certificate 성공/갱신 증명이 아니다.
        """
        if (type(plan) is not FreePlan or plan is not self._last_plan or type(attempted_cells) is not tuple
                or attempted_cells!=plan.cells[:len(attempted_cells)]
                or any(type(cell) is not tuple or len(cell)!=2 or any(type(v) is not int for v in cell) for cell in attempted_cells)):
            raise ValueError('actual attempts must be the original latest selected prefix')
        needed=self._previous_needed
        self._required_cursor,self._surround_cursor,previous=self._cursor_before
        if not attempted_cells:self._previous_needed=previous
        for (col,row),priority in zip(attempted_cells,plan.priorities):
            index=row*self.spec.width+col
            if needed[index]:self._required_cursor=(index+1)%(self.spec.width*self.spec.height)
            if priority=='SURROUNDING':self._surround_cursor=(index+1)%(self.spec.width*self.spec.height)
        self._last_plan=self._cursor_before=None

    def plan(self, *, context_key, needed_mask, support, permission, state, observed_us, time_known,
             now_us, capture_lower_us, max_cells, max_ray_tests, cycle_bound_us, completion_bound_us,
             free_ttl_us=500_000):
        """원 live grid만 읽는다. retained mask는 받지 않으며 UNKNOWN/실패도 원 시도 예산을 쓴다.

        cycle/completion은 caller의 명시 상한이다. capacity_ok도 그 상한 아래의 필요조건일
        뿐이다. actual certificate/완료 시 freshness와 전체 필요 corridor 채택은 별도다.
        """
        import numpy as np
        if context_key != self.context_key:
            raise ValueError('FREE schedule cannot cross original map/calibration/clock context')
        now = _uint(now_us)
        ttl = _uint(free_ttl_us, minimum=1, maximum=10_000_000)
        cells = _uint(max_cells, minimum=1, maximum=256)
        rays = _uint(max_ray_tests, maximum=2**20)
        cycle = _uint(cycle_bound_us, minimum=1)
        completion = _uint(completion_bound_us)
        if capture_lower_us is not None:
            _uint(capture_lower_us, maximum=now)
        shape, count = (self.spec.height, self.spec.width), self.spec.width * self.spec.height
        for array in (support, permission, time_known):
            if type(array) is not np.ndarray or array.dtype != np.bool_ or array.shape != shape:
                raise ValueError('scheduler domains/time mask require exact same-shape bool arrays')
        if type(state) is not np.ndarray or state.dtype != np.uint8 or state.shape != shape or np.any(state > 2):
            raise ValueError('scheduler requires internal live UNKNOWN/FREE/OCCUPIED codes')
        if type(observed_us) is not np.ndarray or observed_us.dtype != np.uint64 or observed_us.shape != shape:
            raise ValueError('scheduler requires original uint64 observation times')
        defined = needed_mask is not None
        if defined and (type(needed_mask) is not np.ndarray or needed_mask.dtype != np.bool_ or needed_mask.shape != shape):
            raise ValueError('required corridor must be an explicit whole-cell bool mask')
        needed = np.zeros(count, dtype=bool) if not defined else needed_mask.reshape(-1)
        previous = np.frombuffer(self._previous_needed, dtype=np.bool_)
        eligible = (support & permission).reshape(-1)
        states, observed = state.reshape(-1), observed_us.reshape(-1)
        known = time_known.reshape(-1) & (observed <= np.uint64(now))
        age = np.full(count, np.iinfo(np.uint64).max, dtype=np.uint64)
        age[known] = np.uint64(now) - observed[known]
        fresh = (states == FREE) & known & (age <= np.uint64(ttl))
        # Python integer 산술로 큰 u64에서도 horizon 합산/expiry 정렬을 wrap하지 않는다.
        horizon = completion + cycle
        urgent = needed & eligible & (states == FREE) & known & (age >= np.uint64(max(0, ttl - horizon)))
        new_or_missing = needed & eligible & ~urgent & ((~previous) | ~fresh)
        first = sorted(np.flatnonzero(urgent).tolist(), key=lambda i: (int(observed[i]) + ttl,
            (i - self._required_cursor) % count))
        second = sorted(np.flatnonzero(new_or_missing).tolist(), key=lambda i: (i - self._required_cursor) % count)
        excluded = urgent | new_or_missing
        third = sorted(np.flatnonzero(eligible & ~excluded).tolist(), key=lambda i: (i - self._surround_cursor) % count)
        ordered = tuple((index, priority) for priority, group in
            (('REQUIRED_EXPIRING', first), ('REQUIRED_NEW_OR_UNPROVEN', second), ('SURROUNDING', third)) for index in group)
        selected = ordered[:cells] if rays else ()
        self._cursor_before=(self._required_cursor,self._surround_cursor,self._previous_needed)
        required_selected = [index for index, _ in selected if needed[index]]
        surrounding_selected = [index for index, priority in selected if priority == 'SURROUNDING']
        if required_selected:
            self._required_cursor = (required_selected[-1] + 1) % count
        if surrounding_selected:
            self._surround_cursor = (surrounding_selected[-1] + 1) % count
        self._previous_needed = needed.tobytes()
        total_needed = int(np.count_nonzero(needed))
        unavailable = int(np.count_nonzero(needed & ~eligible))
        occupied = int(np.count_nonzero(needed & (states == OCCUPIED)))
        due = urgent | new_or_missing
        due_count = int(np.count_nonzero(due))
        due_selected = sum(bool(due[index]) for index, _ in selected)
        cycles = None if not total_needed else (total_needed + cells - 1) // cells
        all_fresh = bool(total_needed and np.all((eligible & fresh)[needed]))
        capacity = bool(defined and total_needed and not unavailable and not occupied and rays
            and capture_lower_us is not None
            and now - capture_lower_us + completion + cycles * cycle <= ttl)
        status = ('NEEDED_REGION_UNKNOWN' if not defined else 'NO_REQUIRED_CELLS' if not total_needed
            else 'REQUIRED_REGION_NOT_ELIGIBLE' if unavailable else 'REQUIRED_REGION_OCCUPIED' if occupied
            else 'CAPTURE_TIME_UNKNOWN' if capture_lower_us is None
            else 'BUDGET_INSUFFICIENT' if not capacity else 'NECESSARY_CAPACITY_ONLY')
        result=FreePlan(tuple((index % self.spec.width, index // self.spec.width) for index, _ in selected),
            tuple(priority for _, priority in selected), rays, total_needed, due_count, len(required_selected),
            unavailable, occupied, due_count - due_selected, all_fresh, capacity, cycles, status)
        self._last_plan=result
        return result
