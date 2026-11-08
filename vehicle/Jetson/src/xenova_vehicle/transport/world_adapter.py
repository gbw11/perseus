"""내부 grid를 공용 world codec으로 옮기는 순수 진단 builder.

원 문맥·시각·stable ID·권한은 caller가 제공한다. broker/시계/장치 접근이나 ID 발급,
운영 승인, UNKNOWN의 FREE 승격을 하지 않는다. 공용 protobuf는 호출 때만 import한다.
"""

from dataclasses import dataclass, field
import math
import struct
import uuid


MAX_U63 = (1 << 63) - 1


class WorldBuildError(ValueError):
    """원 source/template/geometry가 불완전하거나 공용 계약과 호환되지 않는다."""


def _uint(value, maximum=MAX_U63):
    if type(value) is not int or not 0 <= value <= maximum:
        raise WorldBuildError('expected bounded builtin unsigned integer')
    return value


def _text(value, maximum):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(not 33 <= ord(c) <= 126 for c in value)):
        raise WorldBuildError('expected bounded printable ASCII identity')
    return value


def _shared():
    from xenova_drive_world import schema, encode, decode, validate
    return schema, encode, decode, validate


def _f32(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise WorldBuildError('expected finite native geometry')
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except (OverflowError, struct.error) as error:
        raise WorldBuildError('geometry exceeds float32') from error
    if not math.isfinite(result):
        raise WorldBuildError('geometry exceeds float32')
    return result


def _f32_up(value):
    """음이 아닌 오차 상한을 float32로 내릴 때 축소하지 않는다."""
    if value < 0:
        raise WorldBuildError('negative geometry bound')
    result = _f32(value)
    if result < value:
        bits = struct.unpack('<I', struct.pack('<f', result))[0]
        result = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
    if not math.isfinite(result):
        raise WorldBuildError('geometry bound exceeds float32')
    return result


@dataclass(frozen=True)
class WorldSource:
    """caller의 원 snapshot 문맥. frame_epoch는 외부에서 결박한 wire frame ID다.

    clock_id는 FrameStamp의 원 label(최대128자) 그대로다. wire_clock_epoch는 caller가
    명시적으로 결박한 같은 원 시계의 wire UUID이며 J 변환/시각 변환을 수행하지 않는다.
    생략하면 원 clock_id 자체가 canonical wire UUID여야 한다. captured_us의 전체 구간과
    snapshot_us는 원 기록 값이어야 한다. 이 DTO 자체는 승인 증거가 아니다.
    """

    kind: str
    map_frame_id: str
    frame_epoch: str
    clock_id: str
    calibration_id: str
    captured_us: tuple[int, int]
    snapshot_us: int
    wire_clock_epoch: str | None = None
    vehicle_bundle_sha256: str | None = None

    def __post_init__(self):
        if self.kind not in ('LIVE', 'REPLAY', 'SYNTHETIC'):
            raise WorldBuildError('explicit original input kind required')
        _text(self.map_frame_id, 128)
        _text(self.frame_epoch, 36)
        _text(self.clock_id, 128)
        wire_epoch = self.wire_epoch
        _text(wire_epoch, 36)
        try:
            parsed = uuid.UUID(wire_epoch)
        except ValueError as error:
            raise WorldBuildError('caller-declared canonical wire clock UUID required') from error
        if str(parsed) != wire_epoch or not parsed.int:
            raise WorldBuildError('caller-declared canonical nonzero wire clock UUID required')
        if (type(self.calibration_id) is not str or len(self.calibration_id) != 64
                or any(c not in '0123456789abcdef' for c in self.calibration_id)):
            raise WorldBuildError('original calibration SHA256 required')
        if self.vehicle_bundle_sha256 is not None and (type(self.vehicle_bundle_sha256) is not str
                or len(self.vehicle_bundle_sha256)!=64
                or any(c not in '0123456789abcdef' for c in self.vehicle_bundle_sha256)):
            raise WorldBuildError('declared vehicle calibration bundle SHA256 required')
        if type(self.captured_us) is not tuple or len(self.captured_us) != 2:
            raise WorldBuildError('original capture interval required; receive time is not a substitute')
        lo, hi = (_uint(value) for value in self.captured_us)
        _uint(self.snapshot_us)
        if not lo <= hi <= self.snapshot_us:
            raise WorldBuildError('capture must precede original snapshot')

    @property
    def wire_epoch(self):
        return self.clock_id if self.wire_clock_epoch is None else self.wire_clock_epoch

    @property
    def wire_calibration(self):
        """내부 profile ID와 승인한 전체 차량 bundle ID를 별도로 보존한다."""
        return self.calibration_id if self.vehicle_bundle_sha256 is None else self.vehicle_bundle_sha256

    @classmethod
    def from_calibration(cls,kind,map_frame_id,frame_epoch,clock_id,calibration,captured_us,snapshot_us,
                         *,wire_clock_epoch=None):
        from ..perception.calibration import CalibrationProfile
        if type(calibration) is not CalibrationProfile or calibration.vehicle_bundle_sha256 is None:
            raise WorldBuildError('pinned profile must explicitly declare its approved vehicle bundle relation')
        return cls(kind,map_frame_id,frame_epoch,clock_id,calibration.sha256,captured_us,snapshot_us,
                   wire_clock_epoch=wire_clock_epoch,vehicle_bundle_sha256=calibration.vehicle_bundle_sha256)


@dataclass(frozen=True)
class BuiltWorld:
    """검증된 후보 bytes. kind와 원 문맥은 wire 밖에도 보존하며 운영 권한은 없다."""

    payload: bytes
    source: WorldSource
    world_valid: bool = field(default=False, init=False)
    metric_ready: bool = field(default=False, init=False)
    live_authority: bool = field(default=False, init=False)

    @property
    def input_kind(self):
        return self.source.kind

    def message(self):
        """원 bytes의 새 protobuf 사본. 반환값 변이는 원 후보를 바꾸지 않는다."""
        return _shared()[2](self.payload)


def _layer(value, shape, dtype, name):
    import numpy as np
    if type(value) is not np.ndarray or value.shape != shape or value.dtype != np.dtype(dtype):
        raise WorldBuildError(name + ' must be a same-shape exact dtype array')
    return value


def build_world(snapshot, template, *, source, preferred_road=None, static_retained=None, expired=None):
    """완전한 원 template과 grid로 candidate를 만든다. 누락 lane/slot/localization은 채우지 않는다.

    template의 origin은 원 axis-aligned GridSpec과 같아야 한다. float32 grid 양자화는
    기존 localization geometry bound 안에 있어야 하며 bound 없는 legacy는 exact grid만
    지원한다. permission은 원 whole-cell support/permission의 교집합이며 road bit와 별개다.
    """
    import numpy as np
    from ..mapping.local_map import GridSpec
    schema, encode, _, _ = _shared()
    if type(source) is not WorldSource or type(template) is not schema.WorldSnapshot or type(snapshot) is not dict:
        raise WorldBuildError('exact source/snapshot/shared WorldSnapshot template required')
    if any(snapshot.get(name) != expected for name, expected in (
            ('kind', source.kind), ('frame_id', source.map_frame_id), ('clock_id', source.clock_id),
            ('calibration_id', source.calibration_id))):
        raise WorldBuildError('snapshot differs from original source context')
    if snapshot.get('world_valid') is not False or snapshot.get('live_authority') is not False:
        raise WorldBuildError('this adapter only accepts non-authoritative core snapshots')
    spec, arrays = snapshot.get('spec'), snapshot.get('arrays')
    if type(spec) is not GridSpec or type(arrays) is not dict or not 2 <= spec.width <= 64 or not 2 <= spec.height <= 64:
        raise WorldBuildError('wire requires a bounded 2..64 grid')
    shape = spec.height, spec.width
    state = _layer(arrays.get('state'), shape, 'u1', 'state')
    age = _layer(arrays.get('age_ms'), shape, 'u4', 'age_ms')
    support = _layer(arrays.get('support'), shape, '?', 'support')
    permission = _layer(arrays.get('permission'), shape, '?', 'permission')
    if np.any(state > 2):
        raise WorldBuildError('unsupported internal occupancy state')
    masks = []
    for value, name in ((preferred_road, 'preferred_road'), (static_retained, 'static_retained'), (expired, 'expired')):
        masks.append(np.zeros(shape, dtype=bool) if value is None else _layer(value, shape, '?', name))
    road, retained, old = masks
    result = schema.WorldSnapshot()
    result.CopyFrom(template)
    # 공용 decoder와 같이 미검사 unknown bytes를 재발행/fingerprint에 싣지 않는다.
    result.DiscardUnknownFields()
    for name, expected in (('frame_epoch', source.frame_epoch), ('clock_epoch', source.wire_epoch),
                           ('calibration', source.wire_calibration)):
        if not result.HasField(name) or getattr(result, name) != expected:
            raise WorldBuildError('template source mismatch: ' + name)
    lo, hi = source.captured_us
    midpoint = (lo + hi) // 2
    times = result.observed_us, result.completed_us, result.published_us
    for value in times:
        _uint(value)
    if (not all(result.HasField(name) for name in ('observed_us', 'completed_us', 'published_us'))
            or result.observed_us != midpoint or not hi <= source.snapshot_us <= result.completed_us <= result.published_us):
        raise WorldBuildError('template changed original observation/completion/publication ordering')
    half_width = (hi - lo + 1) // 2
    if result.required_features in (0, 1) and half_width:
        raise WorldBuildError('legacy world has no field for nonzero capture timestamp uncertainty')
    if result.required_features in (11, 15) and (not result.HasField('localization')
            or result.localization.timestamp_error_us < half_width):
        raise WorldBuildError('localization omits original capture timestamp uncertainty')
    resolution = _f32(spec.resolution_m)
    origin_x, origin_y = (_f32(value) for value in spec.origin_xy)
    if not result.HasField('origin') or (result.origin.x, result.origin.y, result.origin.yaw) != (origin_x, origin_y, 0.):
        raise WorldBuildError('template origin differs from axis-aligned source grid')
    corner_error = max(math.hypot(origin_x + x * resolution - (spec.origin_xy[0] + x * spec.resolution_m),
                                  origin_y + y * resolution - (spec.origin_xy[1] + y * spec.resolution_m))
                       for x in (0, spec.width) for y in (0, spec.height))
    if corner_error and (not result.HasField('localization') or result.localization.geometry_position_bound_m < corner_error):
        raise WorldBuildError('float32 grid conversion exceeds declared geometry uncertainty')
    known = state != 0
    # age는 기간이다. 재기동 전 보존 나이를 현재 boot의 uptime으로 제한하거나 갱신하지 않는다.
    occupancy = np.take(np.asarray((255, 0, 100), dtype=np.uint8), state)
    flags = known.astype(np.uint8) | (retained.astype(np.uint8) << 1) | (old.astype(np.uint8) << 2) | (road.astype(np.uint8) << 3)
    if np.any(road) and not result.required_features & 1:
        raise WorldBuildError('preferred-road mask has no declared feature')
    fields = dict(width=spec.width, height=spec.height, resolution_m=resolution,
                  occupancy=occupancy.tobytes(order='C'), cell_flags=flags.tobytes(order='C'),
                  age_le32=age.astype('<u4', copy=False).tobytes(order='C'))
    if result.required_features in (11, 15):
        fields['drivable_permission'] = np.packbits((support & permission).reshape(-1), bitorder='little').tobytes()
    for name, value in fields.items():
        if result.HasField(name) and getattr(result, name) != value:
            raise WorldBuildError('template carries different managed grid field: ' + name)
        setattr(result, name, value)
    try:
        payload = encode(result)
    except ValueError as error:
        raise WorldBuildError('incomplete or invalid shared world: ' + str(error)) from error
    return BuiltWorld(payload, source)


def _lane_source_topology(point_sets):
    """원 dense 기하를 먼저 검산한다. 점 생략으로 loop/순서/side를 수리하지 않는다."""
    # 같은 저장소의 현재 codec과 동일한 명목 기하 계약이며 새 공개 API가 아니다.
    from xenova_drive_world.codec import _simple,_cross
    divider=point_sets[0]
    for points in point_sets:
        if any(len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in points):
            raise WorldBuildError('finite original common station points required')
        _simple(list(points))
    signs=[]
    for center,outer in zip(point_sets[1:3],point_sets[3:]):
        _simple(list(divider)+list(reversed(outer)),True)
        sign=0
        for i,p in enumerate(center):
            k=min(i,len(divider)-2);side=_cross(divider[k],divider[k+1],p)
            current=1 if side>1e-6 else -1 if side< -1e-6 else 0
            if not current or sign and current!=sign:raise WorldBuildError('original lane divider side')
            sign=current
            if i and sum((p[j]-center[i-1][j])*(divider[i][j]-divider[i-1][j]) for j in (0,1))<=0:
                raise WorldBuildError('original lane station order')
        signs.append(sign)
    if signs[0]==signs[1]:raise WorldBuildError('original lanes must straddle divider')


def _wire_outer(center,divider):
    # world.c의 float 연산 순서대로 재구성한다. Python double 식으로 통과시키지 않는다.
    return tuple(_f32(_f32(2*v)-d) for v,d in zip(center,divider))


def _lane_c_heading(model):
    """C의 float32 입력/연산 순서를 대조한다. 대상 hypotf 일치는 네이티브 검산이 별도다."""
    if any(abs(v)>=1e6 for p in model.divider for v in (p.x,p.y)):
        raise ValueError('converted lane coordinate outside C domain')
    for lane in model.lanes:
        if any(abs(v)>=1e6 for p in lane.centerline for v in (p.x,p.y)):
            raise ValueError('converted lane coordinate outside C domain')
        error=_f32(_f32(2*lane.position_bound_m)+model.divider_position_bound_m)
        limit=_f32(2*error)
        points=[_wire_outer((p.x,p.y),(d.x,d.y)) for p,d in zip(lane.centerline,model.divider)]
        if any(abs(v)>=1e6 for p in points for v in p):
            raise ValueError('converted lane coordinate outside C domain')
        for a,b in zip(points,points[1:]):
            length=_f32(math.hypot(_f32(b[0]-a[0]),_f32(b[1]-a[1])))
            if length<=limit:raise ValueError('unbounded reconstructed lane heading')


def _positive_up(value):
    return math.nextafter(value,math.inf) if value else 0.


def _lane_subset_bounds(h,point_sets,converted,indices,crossing,original_bound):
    """원 common-s 보간 전체를 포함하는 위치/방향 bound와 보수 metadata를 만든다."""
    wires=[tuple(points[k] for k in indices) for points in converted]
    wires.extend(tuple(_wire_outer(c,d) for c,d in zip(points,wires[0])) for points in wires[1:3])
    residual=[0.]*5;quantization=[0.]*5;turn=[0.]*5
    states=[False]*len(indices);crossings=[]
    for q,(a,b) in enumerate(zip(indices,indices[1:])):
        crossings.append(int(all(crossing[a:b])))
        inferred=any(h.inferred[a:b+1]);states[q]|=inferred;states[q+1]|=inferred
        for side,(original,wire) in enumerate(zip(point_sets,wires)):
            start,end=original[a],original[b]
            dx,dy=end[0]-start[0],end[1]-start[1]
            wx,wy=wire[q+1][0]-wire[q][0],wire[q+1][1]-wire[q][1]
            if dx==dy==0 or wx==wy==0:return None
            quantization[side]=max(quantization[side],math.dist(start,wire[q]),math.dist(end,wire[q+1]))
            quant_angle=abs(math.atan2(dx*wy-dy*wx,dx*wx+dy*wy))
            for k in range(a,b+1):
                t=(h.station_s[k]-h.station_s[a])/(h.station_s[b]-h.station_s[a])
                chord=tuple((1-t)*start[j]+t*end[j] for j in (0,1))
                residual[side]=max(residual[side],math.dist(original[k],chord))
                if k==b:continue
                sx,sy=original[k+1][0]-original[k][0],original[k+1][1]-original[k][1]
                # 역방향 subsegment를 큰 오차 한 개로 숨기는 간략화는 하지 않는다.
                if sx*dx+sy*dy<=0:return None
                angle=abs(math.atan2(sx*dy-sy*dx,sx*dx+sy*dy))
                turn[side]=max(turn[side],_positive_up(angle+quant_angle))
    full=len(indices)==len(h.station_s)
    # full-index에는 생략 잔차가 없다. 원 float32 bound가 이미 덮으면 bytes를 보존한다.
    bounds=[max(original_bound,_f32_up(h.error_m+q if full else
                _positive_up(h.error_m+_positive_up(r)+_positive_up(q))))
            for r,q in zip(residual,quantization)]
    # 실제 원 outer도 포함한다. center/divider 반올림으로 생긴 복원 잔차를 버리지 않는다.
    for side in (1,2):
        required=bounds[side+2]
        if _f32(_f32(2*bounds[side])+bounds[0])<required:
            bounds[side]=max(bounds[side],_f32_up(_positive_up((required-bounds[0])/2)))
            if _f32(_f32(2*bounds[side])+bounds[0])<required:return None
    def heading(value):return _f32_up(h.yaw_error_rad if full and value==0 else _positive_up(h.yaw_error_rad+value))
    headings=[heading(turn[0])]
    headings.extend(heading(max(turn[side],turn[side+2])) for side in (1,2))
    return wires[:3],bounds[:3],headings,bytes(map(int,states)),bytes(crossings)


def lane_model_from_road(road, *, lane_ids, model_revision, lane_revisions, crossing):
    """원 source/ID/revision을 보존해 유한 wire common station 표현만 선택한다.

    원 모든 station과 C outer의 원 tube가 이미 포함되면 bytes를 그대로 유지한다. 짧은 outer heading 때문에만
    거절된 경우 최대30개의 balanced 부분열을 시도하며 원 양끝/전체 구간을 보존한다.
    원 dense 위치·방향·inferred·교차 정책의 불확실성은 줄이지 않는다.
    """
    from ..perception.road import RoadResult
    schema, _, _, validate = _shared()
    if type(road) is not RoadResult or not road.complete or road.status not in ('VALID', 'PARTIAL') or len(road.hypotheses) != 1:
        raise WorldBuildError('one complete unambiguous road hypothesis required')
    if type(lane_ids) is not tuple or len(lane_ids) != 2 or type(lane_revisions) is not tuple or len(lane_revisions) != 2:
        raise WorldBuildError('two external lane IDs and revisions required')
    for value in lane_ids:
        if not _uint(value, (1 << 32) - 1):
            raise WorldBuildError('stable lane IDs must be nonzero')
    if lane_ids[0] == lane_ids[1] or not _uint(model_revision) or any(not _uint(value) for value in lane_revisions):
        raise WorldBuildError('distinct stable IDs and positive external revisions required')
    h = road.hypotheses[0]
    n = len(h.station_s)
    point_sets = h.divider, h.left_center, h.right_center, h.left_outer, h.right_outer
    if (not 2 <= n <= 32 or any(len(points) != n for points in point_sets) or len(h.inferred) != n
            or any(type(value) is not bool for value in h.inferred)
            or any(not math.isfinite(value) for value in h.station_s)
            or any(a >= b for a, b in zip(h.station_s, h.station_s[1:]))):
        raise WorldBuildError('common station geometry is incomplete')
    if type(crossing) is not bytes or len(crossing) != n - 1 or any(value > 1 for value in crossing):
        raise WorldBuildError('external crossing policy must match common station intervals')
    for divider, left, right, left_outer, right_outer in zip(*point_sets):
        for center, outer in ((left, left_outer), (right, right_outer)):
            if any(not math.isclose(center[i], (divider[i] + outer[i]) / 2, abs_tol=1e-12, rel_tol=1e-12) for i in (0, 1)):
                raise WorldBuildError('lane centers are not actual outer/divider midpoints')
    try:_lane_source_topology(point_sets)
    except (ValueError,TypeError,OverflowError) as error:
        raise WorldBuildError('invalid original lane topology: '+str(error)) from error
    converted = [tuple((_f32(point[0]), _f32(point[1])) for point in points) for points in point_sets[:3]]
    quantization = max(math.dist(point, wire) for points, wires in zip(point_sets[:3], converted)
                       for point, wire in zip(points, wires))
    bound, heading = _f32_up(h.error_m + quantization), _f32_up(h.yaw_error_rad)
    states = bytes(int(value) for value in h.inferred)
    def build(points,bounds,headings,states,allowed):
        model=schema.LaneModel(revision=model_revision,crossing=allowed,divider_state=states,
            divider_position_bound_m=bounds[0],divider_heading_bound_rad=headings[0])
        for point in points[0]:model.divider.add(x=point[0],y=point[1])
        for index in range(2):
            lane=model.lanes.add(lane_id=lane_ids[index],adjacent_lane_id=lane_ids[1-index],
                revision=lane_revisions[index],travel_direction=1,position_bound_m=bounds[index+1],
                heading_bound_rad=headings[index+1],point_state=states)
            for point in points[index+1]:lane.centerline.add(x=point[0],y=point[1])
        return model
    audit=_lane_subset_bounds(h,point_sets,converted,tuple(range(n)),crossing,bound)
    if audit is None:raise WorldBuildError('original station coverage cannot be represented')
    # full audit의 inferred OR은 직접 경로에 적용하지 않는다. 원 state/crossing 그대로다.
    model=build(converted,audit[1],tuple(max(heading,v) for v in audit[2]),states,crossing)
    try:
        validate(model)
        _lane_c_heading(model)
    except ValueError as error:
        if str(error)!='unbounded reconstructed lane heading':
            raise WorldBuildError('invalid shared lane model: '+str(error)) from error
        original_error=error
    else:return model
    for count in range(n-1,1,-1):
        indices=tuple((2*i*(n-1)+count-1)//(2*(count-1)) for i in range(count))
        candidate=_lane_subset_bounds(h,point_sets,converted,indices,crossing,bound)
        if candidate is None:continue
        model=build(*candidate)
        try:
            validate(model);_lane_c_heading(model)
        except ValueError:continue
        return model
    raise WorldBuildError('invalid shared lane model: '+str(original_error)) from original_error
