"""tape graph의 유한 도로 역할 가설과 공통 station midpoint를 만든다.

동일 방향/폭은 설치 profile의 가정이다. 남은 가설·추정 gap·오차를 유지하며,
best score·apron 전체 폭·stable ID로 모호함을 숨기지 않는다.
"""

from dataclasses import asdict, dataclass, field
from itertools import combinations
import math

from .tape import TapeGraph


@dataclass(frozen=True)
class RoadProfile:
    """폭·오차·gap은 실측 조정 전의 명시 실험 값이며 길이는 m/각도는 rad다."""

    lane_width_min_m: float = .20
    lane_width_max_m: float = .50
    min_usable_length_m: float = .15
    max_heading_rad: float = 1.1
    max_curvature_1pm: float = 6.0
    station_count: int = 24
    hypothesis_cap: int = 4096
    intersection_work_cap: int = 1_048_576
    max_geometry_error_m: float = .12
    station_error_m: float = .005
    allow_dashed: bool = True
    max_gap_m: float = .35
    merge_lateral_m: float = .015
    merge_heading_rad: float = .15
    allow_missing_outer: bool = True
    missing_outer_side: str | None = None

    def __post_init__(self):
        names = ('lane_width_min_m', 'lane_width_max_m', 'min_usable_length_m', 'max_heading_rad',
                 'max_curvature_1pm', 'max_geometry_error_m', 'station_error_m', 'max_gap_m',
                 'merge_lateral_m', 'merge_heading_rad')
        if any(type(getattr(self, name)) not in (int, float) or not math.isfinite(getattr(self, name))
               or getattr(self, name) < 0 for name in names):
            raise ValueError('road profile requires finite nonnegative metric values')
        if (not 0 < self.lane_width_min_m <= self.lane_width_max_m <= 2
                or self.min_usable_length_m <= 0 or not 0 < self.max_heading_rad < math.pi / 2
                or self.max_curvature_1pm <= 0 or self.max_geometry_error_m <= 0
                or type(self.station_count) is not int or not 2 <= self.station_count <= 32
                or type(self.hypothesis_cap) is not int or not 1 <= self.hypothesis_cap <= 4096
                or type(self.intersection_work_cap) is not int or not 1 <= self.intersection_work_cap <= 4_194_304
                or type(self.allow_dashed) is not bool or type(self.allow_missing_outer) is not bool
                or self.missing_outer_side not in (None, 'LEFT', 'RIGHT')):
            raise ValueError('invalid bounded road profile')


@dataclass(frozen=True)
class LaneHypothesis:
    local_id: int
    strip_ids: tuple
    station_s: tuple
    divider: tuple
    left_outer: tuple
    right_outer: tuple
    left_center: tuple
    right_center: tuple
    inferred: tuple
    width_intervals_m: tuple
    error_m: float
    yaw_error_rad: float
    boundary_reference: str = 'tape_center'
    heading_reference: str = 'bounded_observed_polyline'
    semantic_support: object = None
    semantic_source: tuple | None = None

    def descriptor(self):
        return asdict(self)


@dataclass(frozen=True)
class RoadResult:
    status: str
    hypotheses: tuple[LaneHypothesis, ...]
    complete: bool
    reasons: tuple
    source_key: tuple | None
    calibration_qualified: bool
    work: dict = field(default_factory=dict)
    semantic_scored: int = 0
    semantic_total: int = 0
    semantic_complete: bool | None = None
    semantic_source: tuple | None = None

    def descriptor(self):
        return dict(status=self.status, complete=self.complete, reasons=list(self.reasons),
                    source_key=self.source_key, calibration_qualified=self.calibration_qualified,
                    hypotheses=[h.descriptor() for h in self.hypotheses], work=dict(self.work),
                    semantic_scored=self.semantic_scored, semantic_total=self.semantic_total,
                    semantic_complete=self.semantic_complete, semantic_source=self.semantic_source)


@dataclass(frozen=True)
class _Chain:
    ids: tuple
    points: tuple
    error: float
    gaps: tuple = ()
    point_error_xy: tuple = ()
    segment_error_xy: tuple = ()


def _arc(points):
    result = [0.]
    for a, b in zip(points, points[1:]):
        result.append(result[-1] + math.dist(a, b))
    return result


def _at(points, s):
    lengths = _arc(points)
    for i in range(len(points) - 1):
        if s <= lengths[i + 1] or i == len(points) - 2:
            t = max(0., min(1., (s - lengths[i]) / max(1e-12, lengths[i + 1] - lengths[i])))
            return tuple(a + t * (b - a) for a, b in zip(points[i], points[i + 1])), i
    return points[-1], len(points) - 2


def _angle(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _distance(point, a, b):
    delta = b[0] - a[0], b[1] - a[1]
    t = max(0., min(1., sum((point[i] - a[i]) * delta[i] for i in (0, 1))
                         / max(1e-20, sum(v * v for v in delta))))
    projection = tuple(a[i] + t * delta[i] for i in (0, 1))
    return math.dist(point, projection), t


def _simplify(chain, profile):
    """raster 잔꺾임을 관측 오차 규모에서 줄인다. 제거된 편차는 tube 오차에 더한다."""
    if len(chain.points) <= 2:
        return chain
    tolerance = max(profile.station_error_m, min(profile.max_geometry_error_m / 4,
        max(math.hypot(*e) for e in chain.point_error_xy) / 4))
    selected = {0, len(chain.points) - 1}
    pending = [(0, len(chain.points) - 1)]
    while pending:
        first, last = pending.pop()
        if last <= first + 1:
            continue
        maximum, index = max((_distance(chain.points[i], chain.points[first], chain.points[last])[0], i)
                             for i in range(first + 1, last))
        if maximum > tolerance:
            selected.add(index)
            pending.extend(((first, index), (index, last)))
    selected = sorted(selected)
    points = tuple(chain.points[i] for i in selected)
    segments = []
    maximum_deviation = 0.
    for first, last in zip(selected, selected[1:]):
        deviation = max(_distance(chain.points[i], chain.points[first], chain.points[last])[0]
                        for i in range(first, last + 1))
        maximum_deviation = max(maximum_deviation, deviation)
        segments.append(tuple(max(chain.segment_error_xy[i][axis] for i in range(first, last)) + deviation
                              for axis in (0, 1)))
    gaps = []
    lengths = _arc(points)
    for lo, hi in chain.gaps:
        mapped = []
        for boundary in (lo, hi):
            point, _ = _at(chain.points, boundary)
            _, index, t = min((_distance(point, a, b)[0], i, _distance(point, a, b)[1])
                             for i, (a, b) in enumerate(zip(points, points[1:])))
            mapped.append(lengths[index] + t * (lengths[index + 1] - lengths[index]))
        gaps.append((max(0., min(mapped) - maximum_deviation), max(mapped) + maximum_deviation))
    return _Chain(chain.ids, points, chain.error + maximum_deviation, tuple(gaps),
                  tuple(chain.point_error_xy[i] for i in selected), tuple(segments))


def _heading_error(chain, index):
    """두 관측 꼭짓점의 축별 box에서 polyline 방향 구간을 구한다. 종오차를 횡오차로 쓰지 않는다."""
    a, b = chain.points[index:index + 2]
    ea, eb = chain.point_error_xy[index:index + 2]
    dx, dy = b[0] - a[0], b[1] - a[1]
    ex, ey = ea[0] + eb[0], ea[1] + eb[1]
    if dx - ex <= 0:
        return math.pi / 2
    nominal = math.atan2(dy, dx)
    return min(math.pi / 2, max(abs(math.atan2(y, x) - nominal)
        for x in (dx - ex, dx + ex) for y in (dy - ey, dy + ey)))


def _chains(graph, profile):
    usable = []
    kinds = {}
    work = dict(short_or_closed_strips=0, nonmonotonic_strips=0, heading_rejected_strips=0,
                curvature_rejected_strips=0, dashed_merges=0)
    for strip in graph.strips:
        points = strip.points_xy if strip.points_xy[-1][0] >= strip.points_xy[0][0] else tuple(reversed(strip.points_xy))
        start, end = ((strip.start_kind, strip.end_kind) if points is strip.points_xy
                      else (strip.end_kind, strip.start_kind))
        errors = strip.point_error_xy or tuple((strip.error_m, strip.error_m) for _ in points)
        segment_errors = strip.segment_error_xy or tuple((strip.error_m, strip.error_m) for _ in points[1:])
        if points is not strip.points_xy:
            errors, segment_errors = tuple(reversed(errors)), tuple(reversed(segment_errors))
        chain = _simplify(_Chain((strip.local_id,), points, strip.error_m, (), errors, segment_errors), profile)
        points = chain.points
        if strip.closed or strip.length_m < profile.min_usable_length_m:
            work['short_or_closed_strips'] += 1
            continue
        if any(b[0] - a[0] <= 1e-8 for a, b in zip(points, points[1:])):
            work['nonmonotonic_strips'] += 1
            continue
        if any(abs(_angle(a, b)) > profile.max_heading_rad for a, b in zip(points, points[1:])):
            work['heading_rejected_strips'] += 1
            continue
        if any(abs(_angle(b, c) - _angle(a, b)) / max(1e-9, (math.dist(a, b) + math.dist(b, c)) / 2)
               > profile.max_curvature_1pm for a, b, c in zip(points, points[1:], points[2:])):
            work['curvature_rejected_strips'] += 1
            continue
        usable.append(chain)
        kinds[strip.local_id] = start, end
    if not profile.allow_dashed:
        return usable, False, work
    nexts = {i: [] for i in range(len(usable))}
    previous = {i: [] for i in range(len(usable))}
    for i, first in enumerate(usable):
        if kinds[first.ids[0]][1] != 'observed_paint_end':
            continue
        heading = _angle(first.points[-2], first.points[-1])
        c, s = math.cos(heading), math.sin(heading)
        for j, second in enumerate(usable):
            if i == j or kinds[second.ids[0]][0] != 'observed_paint_end':
                continue
            dx = second.points[0][0] - first.points[-1][0]
            dy = second.points[0][1] - first.points[-1][1]
            along, lateral = dx * c + dy * s, -dx * s + dy * c
            if (0 < along <= profile.max_gap_m and abs(lateral) <= profile.merge_lateral_m
                    and abs(_angle(second.points[0], second.points[1]) - heading) <= profile.merge_heading_rad):
                nexts[i].append(j)
                previous[j].append(i)
    ambiguous = any(len(v) > 1 for v in nexts.values()) or any(len(v) > 1 for v in previous.values())
    if ambiguous:
        return usable, True, work
    output = []
    visited = set()
    for index in range(len(usable)):
        if previous[index]:
            continue
        chain = usable[index]
        points, ids, gaps = list(chain.points), list(chain.ids), list(chain.gaps)
        point_errors, segment_errors = list(chain.point_error_xy), list(chain.segment_error_xy)
        error = chain.error
        current = index
        while current not in visited:
            visited.add(current)
            if not nexts[current]:
                break
            other = nexts[current][0]
            work['dashed_merges'] += 1
            added = usable[other]
            beginning = _arc(points)[-1]
            gap = math.dist(points[-1], added.points[0])
            heading = _angle(points[-2], points[-1])
            dx, dy = added.points[0][0] - points[-1][0], added.points[0][1] - points[-1][1]
            lateral = abs(-dx * math.sin(heading) + dy * math.cos(heading))
            gaps.append((beginning, beginning + gap))
            points.extend(added.points)
            ids.extend(added.ids)
            point_errors.extend(added.point_error_xy)
            gap_error = tuple(max(error, added.error) + lateral
                              + profile.max_curvature_1pm * gap * gap / 8 for _ in (0, 1))
            segment_errors.extend((gap_error,) + added.segment_error_xy)
            error = max(error, added.error + lateral + profile.max_curvature_1pm * gap * gap / 8)
            current = other
        output.append(_simplify(_Chain(tuple(ids), tuple(points), error, tuple(gaps),
                                     tuple(point_errors), tuple(segment_errors)), profile))
    if len(visited) != len(usable):
        return usable, True, work
    return output, False, work


def _y_at_x(chain, x):
    for a, b in zip(chain.points, chain.points[1:]):
        if a[0] - 1e-9 <= x <= b[0] + 1e-9:
            t = (x - a[0]) / (b[0] - a[0])
            return a[1] + t * (b[1] - a[1])
    return None


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


class _WorkCap(Exception):
    pass


def _in_gap(chain, point):
    if chain is None:
        return True
    lengths = _arc(chain.points)
    for index, (a, b) in enumerate(zip(chain.points, chain.points[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / max(1e-12, dx * dx + dy * dy)
        if -1e-7 <= t <= 1 + 1e-7 and abs(_cross((point[0] - a[0], point[1] - a[1]), (dx, dy))) < 1e-6:
            s = lengths[index] + t * math.hypot(dx, dy)
            return any(lo < s < hi for lo, hi in chain.gaps)
    return True


def _normal_hits(chain, point, normal, side, profile, error, work):
    hits = []
    for a, b in zip(chain.points, chain.points[1:]):
        if work['intersection_tests'] >= profile.intersection_work_cap:
            raise _WorkCap()
        work['intersection_tests'] += 1
        delta = b[0] - a[0], b[1] - a[1]
        denominator = _cross(normal, delta)
        if abs(denominator) <= 1e-10:
            continue
        offset = a[0] - point[0], a[1] - point[1]
        distance = _cross(offset, delta) / denominator
        t = _cross(offset, normal) / denominator
        width = side * distance
        if (-1e-8 <= t <= 1 + 1e-8 and width > 0
                and width + error >= profile.lane_width_min_m
                and width - error <= profile.lane_width_max_m
                and not any(abs(distance - old) < 1e-6 for old in hits)):
            hits.append(distance)
    return hits


def _hypothesis(left, divider, right, profile, local_id, work):
    length = _arc(divider.points)[-1]
    stations = [length * i / (profile.station_count - 1) for i in range(profile.station_count)]
    values = []
    ambiguous = False
    prior_pad = (profile.lane_width_max_m - profile.lane_width_min_m) / 2
    for station in stations:
        point, index = _at(divider.points, station)
        a, b = divider.points[index:index + 2]
        heading = _angle(a, b)
        normal = -math.sin(heading), math.cos(heading)
        error = divider.error + max(c.error if c is not None else prior_pad for c in (left, right))
        distances = []
        for chain, side in ((left, 1), (right, -1)):
            hits = ([side * (profile.lane_width_min_m + profile.lane_width_max_m) / 2] if chain is None
                    else _normal_hits(chain, point, normal, side, profile, error, work))
            if len(hits) > 1:
                work['ambiguous_normal_stations'] += 1
                ambiguous = True
            if len(hits) != 1:
                if not hits:
                    work['unmatched_normal_stations'] += 1
                distances = []
                break
            distances.append(hits[0])
        if not distances:
            values.append(None)
            continue
        outers = [tuple(point[k] + normal[k] * distance for k in (0, 1)) for distance in distances]
        inferred = (left is None or right is None or any(lo < station < hi for lo, hi in divider.gaps)
                    or _in_gap(left, outers[0]) or _in_gap(right, outers[1]))
        values.append((station, point, outers[0], outers[1], inferred, distances))
    # 관측된 공통 범위만 쓴다. 분리된 여러 범위를 하나의 연속 도로로 만들지 않는다.
    runs, current = [], []
    for value in values + [None]:
        if value is None:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(value)
    eligible = [run for run in runs if len(run) >= 2 and run[-1][0] - run[0][0] >= profile.min_usable_length_m]
    if len(eligible) != 1:
        work['no_usable_common_run' if not eligible else 'multiple_common_runs'] += 1
        return None, ambiguous or len(eligible) > 1
    run = eligible[0]
    used = [chain for chain in (left, divider, right) if chain is not None]
    error = max(c.error for c in used) + profile.station_error_m
    if left is None or right is None:
        error += prior_pad
    yaw_error = max(_heading_error(divider, _at(divider.points, row[0])[1]) for row in run)
    max_width = max(abs(distance) for row in run for distance in row[5])
    step = max(b[0] - a[0] for a, b in zip(run, run[1:]))
    error += max_width * math.sin(yaw_error) + profile.max_curvature_1pm * step * step / 8
    divider_points = tuple(row[1] for row in run)
    left_outer, right_outer = tuple(row[2] for row in run), tuple(row[3] for row in run)
    centers = [tuple(tuple((p[k] + q[k]) / 2 for k in (0, 1)) for p, q in zip(divider_points, outer))
               for outer in (left_outer, right_outer)]
    widths = tuple((max(0., min(side * row[5][i] for row in run) - 2 * error),
                    max(side * row[5][i] for row in run) + 2 * error) for i, side in ((0, 1), (1, -1)))
    result = LaneHypothesis(local_id, tuple(() if c is None else c.ids for c in (left, divider, right)),
        tuple(row[0] for row in run), divider_points, left_outer, right_outer, centers[0], centers[1],
        tuple(row[4] for row in run), widths, error, yaw_error)
    return result, ambiguous


def infer_road(graph, profile=RoadProfile()):
    """모든 잔존 가설을 유지한다. cap 미검사는 UNKNOWN, 복수 가설은 AMBIGUOUS다."""
    if type(graph) is not TapeGraph or type(profile) is not RoadProfile:
        raise ValueError('road inference requires a tape graph/profile')
    chains, merge_ambiguous, chain_work = _chains(graph, profile)
    complete = graph.complete
    reasons = list(graph.reasons)
    if merge_ambiguous:
        complete = False
        reasons.append('DASH_CHAIN_AMBIGUOUS')
    hypotheses = []
    work = dict(intersection_tests=0, no_x_overlap=0, unmatched_normal_stations=0,
                ambiguous_normal_stations=0, no_usable_common_run=0, multiple_common_runs=0)
    checked = 0
    missing_pairs_checked = missing_roles_checked = 0
    work_exhausted = False
    possible = math.comb(len(chains), 3) if len(chains) >= 3 else 0
    for three in combinations(chains, 3):
        if checked == profile.hypothesis_cap:
            complete = False
            reasons.append('ROAD_HYPOTHESIS_CAP')
            break
        checked += 1
        low, high = max(c.points[0][0] for c in three), min(c.points[-1][0] for c in three)
        if high <= low:
            work['no_x_overlap'] += 1
            continue
        ordered = sorted(three, key=lambda c: _y_at_x(c, (low + high) / 2), reverse=True)
        try:
            result, ambiguous = _hypothesis(*ordered, profile, len(hypotheses), work)
        except _WorkCap:
            complete = False
            reasons.append('ROAD_WORK_CAP')
            work_exhausted = True
            break
        if ambiguous:
            complete = False
            reasons.append('NORMAL_INTERSECTION_AMBIGUOUS')
        if result is not None:
            hypotheses.append(result)
    # 관측3선으로 지지되는 가설이 없을 때만 기존 missing-outer prior를 사용한다.
    # 다른 비도로선/가림 조각의 존재만으로 관측2선의 부분 가설까지 없애지는 않는다.
    if not hypotheses and not work_exhausted and len(chains) >= 2 and profile.allow_missing_outer:
        capped = False
        for pair in combinations(chains, 2):
            missing_pairs_checked += 1
            low, high = max(c.points[0][0] for c in pair), min(c.points[-1][0] for c in pair)
            if high <= low:
                continue
            first, second = sorted(pair, key=lambda c: _y_at_x(c, (low + high) / 2), reverse=True)
            options = [(first, second, None), (None, first, second)]
            if profile.missing_outer_side is not None:
                options = [options[0 if profile.missing_outer_side == 'RIGHT' else 1]]
            for roles in options:
                if checked + missing_roles_checked >= profile.hypothesis_cap:
                    complete = False
                    reasons.append('ROAD_HYPOTHESIS_CAP')
                    capped = True
                    break
                missing_roles_checked += 1
                try:
                    result, ambiguous = _hypothesis(*roles, profile, len(hypotheses), work)
                except _WorkCap:
                    complete = False
                    reasons.append('ROAD_WORK_CAP')
                    capped = True
                    break
                if result is not None:
                    hypotheses.append(result)
                complete = complete and not ambiguous
            if capped:
                break
    if not complete:
        status = 'UNKNOWN'
    elif len(hypotheses) > 1:
        status = 'AMBIGUOUS'
    elif not hypotheses:
        status = 'UNKNOWN'
        reasons.append('NO_SUPPORTED_ROAD')
    elif hypotheses[0].error_m > profile.max_geometry_error_m:
        status = 'UNKNOWN'
        reasons.append('GEOMETRY_ERROR_BUDGET')
    elif any(hypotheses[0].inferred):
        status = 'PARTIAL'
    else:
        status = 'VALID'
    return RoadResult(status, tuple(hypotheses), complete, tuple(dict.fromkeys(reasons)), graph.source_key,
                      graph.calibration_qualified, dict(chains=len(chains), triples_checked=checked,
                                                       triples_total=possible, hypotheses=len(hypotheses),
                                                       missing_pairs_checked=missing_pairs_checked,
                                                       missing_roles_checked=missing_roles_checked,
                                                       **chain_work, **work))
