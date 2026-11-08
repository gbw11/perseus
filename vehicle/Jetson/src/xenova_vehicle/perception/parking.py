"""테이프 구획의 모양·공간 근거·후륜축 목표 후보를 따로 계산한다.

흰색/열린 paint/누락 depth는 빈칸 증거가 아니다. 칸 ID와 최종 목표 선택,
Pi 경로·추진 허가는 만들지 않는다. 길이는 m, yaw는 rad, 시각은 원 clock의 us다.
"""

from dataclasses import dataclass, field, replace
import hashlib
import json
import math

from .calibration import BodyModel
from .inputs import FrameStamp
from .tape import TapeGraph


PARTIAL, VALID, AMBIGUOUS = 0, 1, 2
UNKNOWN, OCCUPIED, CLEAR_REGION = 0, 1, 2
NONE, HIT, OCCLUDED, INVALID_DEPTH, STALE, GEOMETRY, SOURCE_UNAVAILABLE, AMBIGUOUS_REASON = range(8)
_PHYSICAL = frozenset(('observed_paint_end', 'physical_junction'))


def _number(value, name, lo=0., hi=10000.):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(name + ' must be a bounded finite builtin number')
    return float(value)


def _uint(value, name, maximum=(1 << 63) - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(name + ' must be a bounded unsigned builtin integer')
    return value


def _text(value, name):
    if type(value) is not str or not 1 <= len(value) <= 128 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError(name + ' requires a bounded ASCII identity')


def _sha(value):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('original lowercase SHA256 required')


def _point(value):
    if type(value) is not tuple or len(value) != 2:
        raise ValueError('exact XY tuple required')
    return tuple(_number(v, 'coordinate', -10000., 10000.) for v in value)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _add(a, b, scale=1.):
    return a[0] + scale * b[0], a[1] + scale * b[1]


def _unit(a):
    length = math.hypot(*a)
    if length <= 1e-12:
        raise ValueError('zero direction')
    return a[0] / length, a[1] / length


def _area(points):
    return sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(points, points[1:] + points[:1])) / 2


def _polygon(points):
    if type(points) is not tuple or not 3 <= len(points) <= 4:
        raise ValueError('bounded convex polygon required')
    for p in points:
        _point(p)
    if abs(_area(points)) <= 1e-12:
        raise ValueError('degenerate polygon')
    ccw = points if _area(points) > 0 else tuple(reversed(points))
    for a, b in zip(ccw, ccw[1:] + ccw[:1]):
        if any((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) < -1e-10 for p in ccw):
            raise ValueError('polygon must be simple and convex')
    return ccw


def _inside(point, polygon, margin=0.):
    polygon = polygon if _area(polygon) > 0 else tuple(reversed(polygon))
    return all((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
               >= margin * math.dist(a, b) - 1e-12 for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def polygons_touch(a, b, *, interior=False):
    """convex SAT. cell 경계 접촉도 포함하며 겹친 칸 가설 검사만 interior=True다."""
    for polygon in (a, b):
        for p, q in zip(polygon, polygon[1:] + polygon[:1]):
            normal = (-(q[1] - p[1]), q[0] - p[0])
            first, second = [_dot(v, normal) for v in a], [_dot(v, normal) for v in b]
            overlap = min(max(first), max(second)) - max(min(first), min(second))
            if overlap < -1e-12 or interior and overlap <= 1e-10:
                return False
    return True


@dataclass(frozen=True)
class SlotSource:
    """원 capture와 map/odom/보정 문맥. available_us를 관측시각으로 대체하지 않는다."""

    stamp: FrameStamp
    calibration_id: str
    map_epoch: str
    frame_id: str
    odom_epoch: str
    map_revision: int
    registration_revision: int
    qualified: bool = False
    input_kind: str = 'LIVE'

    def __post_init__(self):
        if type(self.stamp) is not FrameStamp or type(self.qualified) is not bool:
            raise ValueError('original source stamp and explicit qualification required')
        if self.input_kind not in ('LIVE', 'REPLAY', 'SYNTHETIC'):
            raise ValueError('original physical/replay/synthetic input kind required')
        _sha(self.calibration_id)
        for name in ('map_epoch', 'frame_id', 'odom_epoch'):
            _text(getattr(self, name), name)
        for name in ('map_revision', 'registration_revision'):
            _uint(getattr(self, name), name)

    @property
    def key(self):
        return self.calibration_id, self.map_epoch, self.frame_id, self.odom_epoch, self.stamp.key

    @property
    def domain(self):
        return self.calibration_id, self.map_epoch, self.frame_id, self.odom_epoch, self.stamp.clock_id

    @property
    def sha256(self):
        from dataclasses import asdict
        raw = json.dumps(asdict(self), sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ParkingProfile:
    """미실측 초기 모형 값이다. 설치 치수/오차/수락 영상으로 다시 정해야 한다."""

    width_m: tuple = (.28, .70)
    depth_m: tuple = (.35, 1.20)
    max_parallel_rad: float = .10
    max_line_residual_m: float = .015
    entry_tolerance_m: float = .025
    endpoint_tolerance_m: float = .025
    max_position_error_m: float = .05
    max_heading_error_rad: float = .10
    side_margin_m: float = .02
    entry_margin_m: float = .02
    back_margin_m: float = .02
    body_position_error_m: float = .005
    body_yaw_error_rad: float = .02
    hypothesis_cap: int = 1024
    free_age_us: int = 500_000
    hit_age_us: int = 5_000_000

    def __post_init__(self):
        for name in ('width_m', 'depth_m'):
            v = getattr(self, name)
            if type(v) is not tuple or len(v) != 2 or not 0 < _number(v[0], name) <= _number(v[1], name, hi=10.):
                raise ValueError('invalid installation size interval')
        for name in ('max_parallel_rad', 'max_line_residual_m', 'entry_tolerance_m', 'endpoint_tolerance_m',
                     'max_position_error_m', 'max_heading_error_rad', 'side_margin_m', 'entry_margin_m',
                     'back_margin_m', 'body_position_error_m', 'body_yaw_error_rad'):
            _number(getattr(self, name), name, hi=1.)
        if not 0 < self.max_parallel_rad < math.pi / 2 or not 0 < self.max_position_error_m:
            raise ValueError('invalid parking geometry budget')
        if type(self.hypothesis_cap) is not int or not 1 <= self.hypothesis_cap <= 1024:
            raise ValueError('slot hypothesis cap must be in 1..1024')
        for name in ('free_age_us', 'hit_age_us'):
            if not 0 < _uint(getattr(self, name), name) <= 5_000_000:
                raise ValueError('invalid original evidence age limit')


@dataclass(frozen=True)
class ParkingAisle:
    """설치/도로 역할 가설의 실제 유한 입구 선. side=+1은 선의 왼쪽 안쪽이다.

    a→b는 설치한 aisle 진행방향이며 화면 좌우 정렬 방향이 아니다. 관측마다 이를
    뒤집으면 서로 다른 corner 역할을 비교하게 되므로 ID 대응에 사용할 수 없다.
    """

    a: tuple
    b: tuple
    side: int
    error_m: float
    approach_lane_id: int = 0

    def __post_init__(self):
        _point(self.a); _point(self.b)
        if math.dist(self.a, self.b) <= 1e-9 or type(self.side) is not int or self.side not in (-1, 1):
            raise ValueError('explicit finite aisle orientation required')
        _number(self.error_m, 'aisle error', hi=1.)
        _uint(self.approach_lane_id, 'D-allocated lane ID', (1 << 32) - 1)

    @property
    def tangent(self):
        return _unit(_sub(self.b, self.a))

    @property
    def inward(self):
        t = self.tangent
        return -self.side * t[1], self.side * t[0]


@dataclass(frozen=True)
class GoalSeed:
    direction: str
    pose: tuple
    d_interval_m: tuple
    position_bound_m: float
    heading_bound_rad: float
    metric_qualified: bool
    coordinates: str
    feasible: bool = field(default=False, init=False)


@dataclass(frozen=True)
class SlotCandidate:
    local_id: int
    strip_ids: tuple
    geometry_state: int
    corners: tuple
    partial_points: tuple
    entry: tuple
    inward: tuple
    width_interval_m: tuple
    depth_interval_m: tuple
    usable_polygon: tuple
    position_bound_m: float
    heading_bound_rad: float
    source: SlotSource
    endpoint_kinds: tuple
    back_evidence: str
    approach_lane_id: int
    reasons: tuple = ()
    calibration_qualified: bool = False
    coordinates: str = 'BASE'

    def __post_init__(self):
        _uint(self.local_id, 'frame-local ID', 1023)
        if type(self.geometry_state) is not int or self.geometry_state not in (PARTIAL, VALID, AMBIGUOUS) or type(self.source) is not SlotSource:
            raise ValueError('invalid slot geometry/source')
        for name in ('corners', 'partial_points', 'entry', 'usable_polygon'):
            points = getattr(self, name)
            if type(points) is not tuple or len(points) > 4:
                raise ValueError('bounded owned points required')
            for p in points:
                _point(p)
        if (len(self.entry) != 2 or type(self.strip_ids) is not tuple
                or not (2 <= len(self.strip_ids) <= 3 or not self.strip_ids and self.coordinates == 'MAP'
                        and self.back_evidence == 'restored_committed')):
            raise ValueError('slot requires original pair and entry')
        for value in self.strip_ids:
            _uint(value, 'frame-local strip ID', (1 << 32) - 1)
        _uint(self.approach_lane_id, 'D-allocated lane ID', (1 << 32) - 1)
        for name in ('width_interval_m', 'depth_interval_m'):
            pair = getattr(self, name)
            if type(pair) is not tuple or len(pair) != 2 or not 0 <= pair[0] <= pair[1] or not math.isfinite(pair[1]):
                raise ValueError('invalid measured/prior interval')
        _number(self.position_bound_m, 'geometry bound', hi=100.)
        _number(self.heading_bound_rad, 'heading bound', hi=math.pi)
        if type(self.calibration_qualified) is not bool or self.coordinates not in ('BASE', 'MAP'):
            raise ValueError('explicit coordinate/qualification domain required')
        _point(self.inward)
        if abs(math.hypot(*self.inward) - 1) > 1e-9:
            raise ValueError('unit inward vector required')
        if self.geometry_state == VALID:
            if len(self.corners) != 4 or len(self.usable_polygon) != 4:
                raise ValueError('VALID requires complete original and usable corners')
            _polygon(self.corners); _polygon(self.usable_polygon)

    @property
    def geometry_complete(self):
        return self.geometry_state == VALID

    @property
    def entry_midpoint(self):
        return tuple((a + b) / 2 for a, b in zip(*self.entry))

    @property
    def geometry_sha256(self):
        values = (self.geometry_state, self.corners, self.partial_points, self.entry, self.inward,
                  self.width_interval_m, self.depth_interval_m, self.usable_polygon, self.position_bound_m,
                  self.heading_bound_rad, self.endpoint_kinds, self.back_evidence, self.approach_lane_id, self.coordinates)
        return hashlib.sha256(json.dumps(values, separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()


@dataclass(frozen=True)
class SlotGeometryResult:
    """complete는 가설 열거의 완료다. wire ParkingSetMeta의 ROI inventory 완료와 다르다."""

    candidates: tuple
    source: SlotSource
    complete: bool
    status: str
    reasons: tuple
    hypotheses_checked: int
    pair_count: int
    metric_ready: bool = field(default=False, init=False)
    live_authority: bool = field(default=False, init=False)

    def __post_init__(self):
        if (type(self.candidates) is not tuple or len(self.candidates) > 1024
                or any(type(c) is not SlotCandidate for c in self.candidates)
                or len({c.local_id for c in self.candidates}) != len(self.candidates)
                or type(self.source) is not SlotSource or type(self.complete) is not bool
                or self.status not in ('VALID', 'PARTIAL', 'AMBIGUOUS', 'UNKNOWN', 'EMPTY')):
            raise ValueError('bounded owned geometry result required')
        _uint(self.hypotheses_checked, 'checked hypotheses', 1024)
        _uint(self.pair_count, 'parallel pairs across declared aisle roles', 4 * 496)


def _line(strip, aisle, profile):
    if strip.closed or not strip.observed:
        return None
    a, b = strip.points_xy[0], strip.points_xy[-1]
    d = _sub(b, a); length = math.hypot(*d)
    if length <= 2 * strip.error_m:
        return None
    unit = _unit(d)
    residual = max(abs((p[0] - a[0]) * unit[1] - (p[1] - a[1]) * unit[0]) for p in strip.points_xy)
    if residual > profile.max_line_residual_m:
        return None
    if _dot(d, aisle.inward) < 0:
        a, b = b, a
        kinds = strip.end_kind, strip.start_kind
    else:
        kinds = strip.start_kind, strip.end_kind
    return a, b, kinds, strip.error_m + residual


def _intersection(a, b, c, d):
    u, v = _sub(b, a), _sub(d, c)
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) <= 1e-12:
        return None
    delta = _sub(c, a)
    s = (delta[0] * v[1] - delta[1] * v[0]) / den
    t = (delta[0] * u[1] - delta[1] * u[0]) / den
    return _add(a, u, s), s, t


def _intersection_error(a, b, error_ab, c, d, error_cd, nominal):
    """endpoint box 전체의 교점 interval. denominator가 0을 포함하면 수락하지 않는다."""
    def point(p, e):
        return tuple((math.nextafter(v - e, -math.inf), math.nextafter(v + e, math.inf)) for v in p)
    def minus(x, y):
        return math.nextafter(x[0] - y[1], -math.inf), math.nextafter(x[1] - y[0], math.inf)
    def plus(x, y):
        return math.nextafter(x[0] + y[0], -math.inf), math.nextafter(x[1] + y[1], math.inf)
    def times(x, y):
        values = [p * q for p in x for q in y]
        return math.nextafter(min(values), -math.inf), math.nextafter(max(values), math.inf)
    def cross(x, y):
        return minus(times(x[0], y[1]), times(x[1], y[0]))
    aa, bb, cc, dd = point(a, error_ab), point(b, error_ab), point(c, error_cd), point(d, error_cd)
    u = tuple(minus(q, p) for p, q in zip(aa, bb)); v = tuple(minus(q, p) for p, q in zip(cc, dd))
    delta = tuple(minus(q, p) for p, q in zip(aa, cc)); den = cross(u, v)
    if den[0] <= 0 <= den[1]:
        return None
    reciprocal = (math.nextafter(1 / den[1], -math.inf), math.nextafter(1 / den[0], math.inf))
    parameter = times(cross(delta, v), reciprocal)
    result = tuple(plus(origin, times(parameter, direction)) for origin, direction in zip(aa, u))
    bound = math.hypot(*(max(abs(lo - p), abs(hi - p)) for (lo, hi), p in zip(result, nominal)))
    return math.nextafter(bound, math.inf) + 1e-12 if math.isfinite(bound) else None


def _physical_back(point, line, graph, tolerance):
    a, b, kinds, error = line
    if math.dist(point, b) <= tolerance + error and kinds[1] in _PHYSICAL:
        return True
    return any(j.kind == 'physical_junction' and math.dist(point, j.xy) <= tolerance + error for j in graph.junctions)


def extract_slots(graph, aisle, *, source, body, profile=ParkingProfile()):
    """평행 side 쌍과 실제 back/end 근거를 열거한다. missing back은 채우지 않는다.

    graph가 불완전하거나 hypothesis cap이 남으면 결과 전체의 complete=False다.
    각 후보의 local_id는 이번 frame에만 유효하며 D의 stable ID가 아니다.
    """
    if (type(graph) is not TapeGraph or type(aisle) is not ParkingAisle or type(source) is not SlotSource
            or type(body) is not BodyModel or type(profile) is not ParkingProfile):
        raise ValueError('exact graph/aisle/source/body/profile required')
    if graph.source_key != source.stamp.key:
        raise ValueError('slot source differs from original tape capture')
    t, n = aisle.tangent, aisle.inward
    lines = [(s, _line(s, aisle, profile)) for s in graph.strips]
    sides = [(s, line) for s, line in lines if line is not None
             and abs(_dot(_unit(_sub(line[1], line[0])), n)) >= math.cos(profile.max_parallel_rad)]
    output = []; checked = 0; limited = False
    pair_count = len(sides) * (len(sides) - 1) // 2
    for i, (first0, left0) in enumerate(sides):
        for second0, right0 in sides[i + 1:]:
            first, second, left, right = first0, second0, left0, right0
            if checked >= profile.hypothesis_cap:
                limited = True; break
            checked += 1
            crossing = [_intersection(line[0], line[1], aisle.a, aisle.b) for line in (left, right)]
            if any(v is None for v in crossing):
                continue
            entries = [v[0] for v in crossing]
            if _dot(_sub(entries[1], entries[0]), t) < 0:
                first, second, left, right = second, first, right, left
                entries.reverse(); crossing.reverse()
            entry_errors = [_intersection_error(line[0], line[1], line[3], aisle.a, aisle.b, aisle.error_m, value[0])
                            for line, value in zip((left, right), crossing)]
            if any(v is None for v in entry_errors):
                continue
            error = max(entry_errors)
            width = math.dist(*entries)
            width_interval = max(0., width - 2 * error), width + 2 * error
            if width_interval[1] < profile.width_m[0] or width_interval[0] > profile.width_m[1]:
                continue
            if any(v[2] < -error / math.dist(aisle.a, aisle.b) or v[2] > 1 + error / math.dist(aisle.a, aisle.b) for v in crossing):
                continue
            # 중간 선이 실제로 구획을 나누면 두 칸을 합친 바깥 쌍을 배제한다.
            merged = False
            for other, line in sides:
                if other.local_id in (first.local_id, second.local_id):
                    continue
                middle = _intersection(line[0], line[1], aisle.a, aisle.b)
                if middle is None:
                    continue
                s = _dot(_sub(middle[0], entries[0]), t)
                if 2 * error < s < width - 2 * error and _dot(_sub(line[1], aisle.a), n) >= profile.depth_m[0]:
                    merged = True; break
            if merged:
                continue
            entry_complete = all(-profile.entry_tolerance_m / math.dist(line[0], line[1]) <= v[1] <= 1
                                 for line, v in zip((left, right), crossing))
            entry_complete = entry_complete and all(v[1] * math.dist(line[0], line[1]) > profile.entry_tolerance_m
                or line[2][0] in _PHYSICAL for line, v in zip((left, right), crossing))
            backs = []
            for back, line in lines:
                if back.local_id in (first.local_id, second.local_id) or line is None:
                    continue
                if checked >= profile.hypothesis_cap:
                    limited = True; break
                checked += 1
                if abs(_dot(_unit(_sub(line[1], line[0])), t)) < math.cos(profile.max_parallel_rad):
                    continue
                ends = [_intersection(side[0], side[1], line[0], line[1]) for side in (left, right)]
                if any(v is None or not -1e-9 <= v[2] <= 1 + 1e-9 or not -1e-9 <= v[1] <= 1 + 1e-9 for v in ends):
                    continue
                if not all(_physical_back(v[0], side, graph, profile.endpoint_tolerance_m) for v, side in zip(ends, (left, right))):
                    continue
                depth = [_dot(_sub(v[0], entry), n) for v, entry in zip(ends, entries)]
                if min(depth) > 0:
                    errors = [_intersection_error(side[0], side[1], side[3], line[0], line[1], line[3], value[0])
                              for side, value in zip((left, right), ends)]
                    if any(v is None for v in errors):
                        continue
                    backs.append((tuple(v[0] for v in ends), max(error, *errors),
                                  (first.local_id, second.local_id, back.local_id), 'observed_back'))
            # 선이 없어도 두 physical paint-end가 실제 관측된 경우만 back extent를 후보화한다.
            if not backs and all(line[2][1] in _PHYSICAL for line in (left, right)):
                depth = [_dot(_sub(line[1], entry), n) for line, entry in zip((left, right), entries)]
                if abs(depth[0] - depth[1]) <= profile.endpoint_tolerance_m + 2 * error:
                    backs.append(((left[1], right[1]), max(error, left[3], right[3]),
                                  (first.local_id, second.local_id), 'observed_endpoints'))
            if not backs:
                visible = max(0., min(_dot(_sub(line[1], entry), n) for line, entry in zip((left, right), entries)))
                output.append(SlotCandidate(len(output), (first.local_id, second.local_id), PARTIAL, (),
                    (entries[0], entries[1], right[1], left[1]), tuple(entries), n, width_interval,
                    (min(visible, profile.depth_m[1]), profile.depth_m[1]), (), error, profile.max_parallel_rad,
                    source, (left[2], right[2]), 'missing_back', aisle.approach_lane_id, ('CENSORED_BACK',),
                    calibration_qualified=source.qualified and graph.calibration_qualified))
                continue
            for ends, bound, ids, evidence in backs:
                depth = min(_dot(_sub(p, e), n) for p, e in zip(ends, entries))
                interval = max(0., depth - 2 * bound), depth + 2 * bound
                if interval[1] < profile.depth_m[0] or interval[0] > profile.depth_m[1]:
                    continue
                corners = (entries[0], entries[1], ends[1], ends[0])
                try:
                    _polygon(corners)
                except ValueError:
                    continue
                aisle_yaw = math.asin(min(1., 2 * aisle.error_m / max(1e-12, math.dist(aisle.a, aisle.b) - 2 * aisle.error_m)))
                side_yaw = max(abs(math.atan2(_unit(_sub(line[1], line[0]))[0] * n[1]
                                             - _unit(_sub(line[1], line[0]))[1] * n[0],
                                             _dot(_unit(_sub(line[1], line[0])), n)))
                    + math.asin(min(1., 2 * line[3] / max(1e-12, math.dist(line[0], line[1]) - 2 * line[3])))
                    for line in (left, right))
                yaw_error = min(math.pi, max(side_yaw,
                    math.asin(min(1., 2 * bound / max(1e-12, depth - 2 * bound)))) + aisle_yaw)
                paint = max(first.width_interval_m[1], second.width_interval_m[1]) / 2
                padding = paint + bound
                usable_width, usable_depth = width - 2 * padding, depth - 2 * padding
                usable = ()
                if min(usable_width, usable_depth) > 0:
                    origin = _add(_add(entries[0], t, padding), n, padding)
                    usable = (origin, _add(origin, t, usable_width),
                              _add(_add(origin, t, usable_width), n, usable_depth), _add(origin, n, usable_depth))
                    if not all(_inside(p, corners, bound) for p in usable):
                        usable = ()
                valid = entry_complete and usable and bound <= profile.max_position_error_m and yaw_error <= profile.max_heading_error_rad
                output.append(SlotCandidate(len(output), ids, VALID if valid else PARTIAL, corners, (), tuple(entries), n,
                    (max(0., min(width, math.dist(*ends)) - 2 * bound), max(width, math.dist(*ends)) + 2 * bound),
                    interval, usable, bound, yaw_error, source, (left[2], right[2]), evidence,
                    aisle.approach_lane_id, () if valid else ('ENTRY_OR_GEOMETRY_UNRESOLVED',),
                    calibration_qualified=source.qualified and graph.calibration_qualified))
            if len(output) >= 1024:
                limited = True; break
        if limited:
            break
    # 공유 paint 접촉은 별개 칸이다. 면적이 겹친 다른 pairing/back 가설은 함께 보존한다.
    conflicting = set()
    for i, a in enumerate(output):
        for j in range(i):
            b = output[j]
            if a.corners and b.corners and polygons_touch(a.corners, b.corners, interior=True):
                conflicting.update((i, j))
    for i in conflicting:
        output[i] = replace(output[i], geometry_state=AMBIGUOUS, reasons=output[i].reasons + ('COMPETING_SLOT_TOPOLOGY',))
    complete = graph.complete and not limited
    reasons = tuple(graph.reasons) + (('SLOT_HYPOTHESIS_CAP',) if limited else ())
    status = 'UNKNOWN' if not complete else 'AMBIGUOUS' if conflicting else 'PARTIAL' if any(c.geometry_state != VALID for c in output) else 'VALID' if output else 'EMPTY'
    return SlotGeometryResult(tuple(output), source, complete, status, reasons, checked, pair_count)


def to_map(candidate, pose, *, position_error_m, yaw_error_rad):
    """caller가 원 capture odom/정합으로 얻은 pose를 적용한다. latest/가짜 0 pose를 만들지 않는다."""
    if type(candidate) is not SlotCandidate or candidate.coordinates != 'BASE':
        raise ValueError('original BASE candidate required; transform must be applied once')
    if type(pose) is not tuple or len(pose) != 3:
        raise ValueError('explicit capture map pose required')
    for value in pose:
        _number(value, 'capture map pose', -10000., 10000.)
    _number(position_error_m, 'capture position error', hi=100.)
    _number(yaw_error_rad, 'capture yaw error', hi=math.pi)
    c, s = math.cos(pose[2]), math.sin(pose[2])
    def transform(p):
        return pose[0] + c * p[0] - s * p[1], pose[1] + s * p[0] + c * p[1]
    points = candidate.corners or candidate.partial_points
    radius = max((math.hypot(*p) for p in points), default=0.)
    pad = position_error_m + 2 * radius * math.sin(yaw_error_rad / 2)
    n = candidate.inward
    return replace(candidate, coordinates='MAP', corners=tuple(transform(p) for p in candidate.corners),
        partial_points=tuple(transform(p) for p in candidate.partial_points),
        entry=tuple(transform(p) for p in candidate.entry), usable_polygon=tuple(transform(p) for p in candidate.usable_polygon),
        inward=(c * n[0] - s * n[1], s * n[0] + c * n[1]),
        position_bound_m=candidate.position_bound_m + pad,
        heading_bound_rad=min(math.pi, candidate.heading_bound_rad + yaw_error_rad))


def goal_candidates(candidate, *, body, profile=ParkingProfile()):
    """직각 칸의 reverse/forward midpoint seed만 만든다. Pi 접근/sweep 가능성은 미판정이다."""
    if type(candidate) is not SlotCandidate or type(body) is not BodyModel or type(profile) is not ParkingProfile:
        raise ValueError('explicit slot/body/profile required')
    if candidate.geometry_state != VALID or len(candidate.usable_polygon) != 4:
        return ()
    poly = candidate.usable_polygon
    width, depth = math.dist(poly[0], poly[1]), math.dist(poly[0], poly[3])
    radius = math.hypot(max(body.front, body.rear), body.width / 2)
    tube = profile.body_position_error_m + 2 * radius * math.sin(min(math.pi, profile.body_yaw_error_rad) / 2)
    if width < body.width + 2 * (profile.side_margin_m + tube):
        return ()
    e = tuple((a + b) / 2 for a, b in zip(poly[0], poly[1])); n = candidate.inward
    output = []
    for direction, front, rear, sign in (('REVERSE', body.front, body.rear, -1), ('FORWARD', body.rear, body.front, 1)):
        low = front + profile.entry_margin_m + tube
        high = depth - rear - profile.back_margin_m - tube
        if low > high:
            continue
        d = (low + high) / 2; p = _add(e, n, d)
        yaw = math.atan2(sign * n[1], sign * n[0]); c, s = math.cos(yaw), math.sin(yaw)
        corners = tuple((p[0] + c * x - s * y, p[1] + s * x + c * y)
                        for x in (-body.rear, body.front) for y in (-body.width / 2, body.width / 2))
        if not all(_inside(v, poly, max(profile.side_margin_m, profile.entry_margin_m, profile.back_margin_m) + tube) for v in corners):
            continue
        output.append(GoalSeed(direction, (p[0], p[1], yaw), (low, high),
            candidate.position_bound_m + tube, candidate.heading_bound_rad + profile.body_yaw_error_rad,
            candidate.calibration_qualified and candidate.source.stamp.captured_us is not None, candidate.coordinates))
    return tuple(output)


@dataclass(frozen=True)
class ClearanceMap:
    """원 snapshot의 소유 bytes. FREE의 ray 검산은 기존 map/certificate 생산자의 책임이다."""

    source_domain: tuple
    spec: object
    snapshot_us: int
    revision: int
    state: bytes
    support: bytes
    permission: bytes
    conflict: bytes
    age_le32: bytes
    source_kinds: int

    def __post_init__(self):
        from ..mapping.local_map import GridSpec
        if type(self.spec) is not GridSpec or type(self.source_domain) is not tuple or len(self.source_domain) != 5:
            raise ValueError('exact grid/source domain required')
        _sha(self.source_domain[0])
        for v in self.source_domain[1:]:
            _text(v, 'source domain')
        _uint(self.snapshot_us, 'original snapshot time'); _uint(self.revision, 'map revision')
        size = self.spec.width * self.spec.height
        for name in ('state', 'support', 'permission', 'conflict'):
            value = getattr(self, name)
            if type(value) is not bytes or len(value) != size or any(v > (2 if name == 'state' else 1) for v in value):
                raise ValueError('bounded whole-cell owned bytes required')
        if type(self.age_le32) is not bytes or len(self.age_le32) != size * 4:
            raise ValueError('original little endian ages required')
        if type(self.source_kinds) is not int or not 1 <= self.source_kinds <= 3:
            raise ValueError('live OAK/ToF source bits required; retained-static admission is separate')

    @classmethod
    def from_snapshot(cls, snapshot, *, snapshot_us, map_epoch, odom_epoch, source_kinds):
        import numpy as np
        from ..mapping.local_map import GridSpec
        if type(snapshot) is not dict or type(snapshot.get('spec')) is not GridSpec:
            raise ValueError('original bounded map snapshot required')
        _uint(snapshot_us, 'original snapshot time')
        _uint(snapshot['revision'], 'map revision')
        if snapshot.get('world_valid') is not False or snapshot.get('live_authority') is not False:
            raise ValueError('candidate layer requires a non-authoritative map snapshot')
        for v in (map_epoch, odom_epoch):
            _text(v, 'map/odom domain')
        if type(source_kinds) is not int or not 1 <= source_kinds <= 7 or source_kinds & 4:
            raise ValueError('this live-only profile cannot authorize retained-static FREE')
        spec = snapshot['spec']; shape = spec.height, spec.width; arrays = snapshot.get('arrays')
        if type(arrays) is not dict:
            raise ValueError('original map arrays required')
        copied = {}
        for name, dtype in (('state', 'u1'), ('support', '?'), ('permission', '?'), ('conflict', '?'), ('age_ms', 'u4')):
            a = arrays.get(name)
            if type(a) is not np.ndarray or a.shape != shape or a.dtype != np.dtype(dtype):
                raise ValueError('map array shape/type mismatch')
            copied[name] = a.astype('<u4', copy=False).tobytes() if name == 'age_ms' else a.tobytes()
        if any(v > 2 for v in copied['state']):
            raise ValueError('invalid internal map occupancy value')
        return cls((snapshot['calibration_id'], map_epoch, snapshot['frame_id'], odom_epoch, snapshot['clock_id']),
            spec, snapshot_us, snapshot['revision'], copied['state'], copied['support'], copied['permission'],
            copied['conflict'], copied['age_ms'], source_kinds)


@dataclass(frozen=True)
class OccupancyEvidence:
    """source는 구획의 원 문맥이고 map_revision은 독립 공간 근거를 참조한다.

    새 RGB key가 새 depth 증거라는 뜻이 아니다. 원 cell 시각의 보수 하한과 region을
    보존하며 실제 cell/provenance closure는 원 지도 checkpoint가 제공해야 한다.
    """

    state: int
    reason: int
    source: SlotSource
    map_revision: int
    observed_lower_us: int | None
    clearance_age_ms: int
    source_kinds: int
    checked_cells: int
    hit_cells: int
    unknown_cells: int
    required_region: tuple
    evaluated_us: int
    qualified: bool = False
    clock_id: str | None = None
    feasible: bool = field(default=False, init=False)

    def __post_init__(self):
        if (type(self.state) is not int or self.state not in (UNKNOWN, OCCUPIED, CLEAR_REGION)
                or type(self.reason) is not int or self.reason not in range(8)
                or type(self.source) is not SlotSource or type(self.qualified) is not bool):
            raise ValueError('exact independent occupancy statement required')
        if self.clock_id is None:
            object.__setattr__(self, 'clock_id', self.source.stamp.clock_id)
        _text(self.clock_id, 'original map evidence clock')
        _uint(self.map_revision, 'map revision'); _uint(self.evaluated_us, 'evaluation time')
        _uint(self.clearance_age_ms, 'clearance age', (1 << 32) - 1)
        for name in ('checked_cells', 'hit_cells', 'unknown_cells'):
            _uint(getattr(self, name), name, 128 * 128)
        if self.unknown_cells > self.checked_cells or type(self.source_kinds) is not int or not 1 <= self.source_kinds <= 7:
            raise ValueError('invalid occupancy counters/source bits')
        if self.observed_lower_us is not None:
            _uint(self.observed_lower_us, 'original observation lower time')
            if self.observed_lower_us > self.evaluated_us:
                raise ValueError('original observation must precede evaluation')
        if type(self.required_region) is not tuple or len(self.required_region) not in (0, 3, 4):
            raise ValueError('bounded original required region required')
        if self.required_region:
            _polygon(self.required_region)
        if self.state == CLEAR_REGION and (self.observed_lower_us is None or not self.checked_cells or self.hit_cells or self.unknown_cells):
            raise ValueError('CLEAR_REGION requires actual complete positive-free coverage')
        if self.state == OCCUPIED and not self.hit_cells:
            raise ValueError('OCCUPIED requires a positive hit')


def evaluate_occupancy(candidate, evidence, *, now_us, required_region=None, profile=ParkingProfile(), clock_id=None):
    """칸 전체 hit 우선, 필요한 영역의 모든 whole-cell FREE를 확인한다.

    부분 칸/새로운 clock/미관측/기한 만료는 UNKNOWN이다. 원 ms age의 양자화 폭을
    보수적으로 더하며 재발행 now로 cell 관측시각을 갱신하지 않는다.
    """
    if type(candidate) is not SlotCandidate or type(evidence) is not ClearanceMap or type(profile) is not ParkingProfile:
        raise ValueError('exact candidate/map/profile required')
    _uint(now_us, 'evaluation time')
    original_clock = candidate.source.stamp.clock_id if clock_id is None else clock_id
    if (candidate.coordinates != 'MAP' or evidence.source_domain[:4] != candidate.source.domain[:4]
            or evidence.source_domain[4] != original_clock or now_us < evidence.snapshot_us):
        raise ValueError('map/candidate frame, clock, calibration or time mismatch')
    region = candidate.usable_polygon if required_region is None else _polygon(required_region)
    if required_region is not None and (not candidate.usable_polygon or not all(_inside(p, candidate.usable_polygon) for p in region)):
        raise ValueError('required region must be wholly inside the usable candidate')
    if candidate.geometry_state != VALID:
        return OccupancyEvidence(UNKNOWN, AMBIGUOUS_REASON if candidate.geometry_state == AMBIGUOUS else GEOMETRY,
            candidate.source, evidence.revision, None, (1 << 32) - 1, evidence.source_kinds, 0, 0, 0, tuple(region), now_us,
            clock_id=original_clock)
    import struct
    spec = evidence.spec; count = spec.width * spec.height
    if any(type(v) is not bytes or len(v) != count for v in (evidence.state, evidence.support, evidence.permission, evidence.conflict)) or len(evidence.age_le32) != count * 4:
        raise ValueError('owned whole-cell map lengths changed')
    corners = candidate.corners; checked = hits = unknown = 0; oldest = oldest_hit = 0; stale = False
    bounds = spec.bounds(0, 0)[:2] + spec.bounds(spec.width - 1, spec.height - 1)[2:]
    pad = candidate.position_bound_m
    outside = any(not bounds[0] + pad <= p[0] <= bounds[2] - pad or not bounds[1] + pad <= p[1] <= bounds[3] - pad for p in region)
    for row in range(spec.height):
        for col in range(spec.width):
            x0, y0, x1, y1 = spec.bounds(col, row)
            cell = ((x0 - pad, y0 - pad), (x1 + pad, y0 - pad), (x1 + pad, y1 + pad), (x0 - pad, y1 + pad))
            in_bay, required = polygons_touch(cell, corners), polygons_touch(cell, region)
            if not in_bay and not required:
                continue
            index = row * spec.width + col
            age = struct.unpack_from('<I', evidence.age_le32, index * 4)[0]
            age_us = age * 1000 + 999 + now_us - evidence.snapshot_us
            if in_bay and evidence.state[index] == 2 and age_us < profile.hit_age_us:
                hits += 1; oldest_hit = max(oldest_hit, age_us)
            if required:
                checked += 1; oldest = max(oldest, age_us)
                fresh = age_us < profile.free_age_us
                if not fresh:
                    stale = True
                if not (evidence.state[index] == 1 and evidence.support[index] and evidence.permission[index]
                        and not evidence.conflict[index] and fresh):
                    unknown += 1
    known_capture = candidate.source.stamp.captured_us is not None
    state = OCCUPIED if hits else CLEAR_REGION if checked and not unknown and not outside and known_capture else UNKNOWN
    reason = HIT if hits else NONE if state == CLEAR_REGION else STALE if stale else GEOMETRY if outside else INVALID_DEPTH if known_capture else SOURCE_UNAVAILABLE
    selected_age = oldest if state == CLEAR_REGION else oldest_hit
    lower = max(0, now_us - selected_age) if state in (CLEAR_REGION, OCCUPIED) else None
    return OccupancyEvidence(state, reason, candidate.source, evidence.revision, lower,
        min((1 << 32) - 1, (oldest + 999) // 1000) if state == CLEAR_REGION else (1 << 32) - 1,
        evidence.source_kinds, checked, hits, unknown, tuple(region), now_us,
        state != UNKNOWN and candidate.calibration_qualified and known_capture, clock_id=original_clock)
