"""유한 SE(2) 탐색의 가능한 전체 집합을 보존한다. 최저 loss는 위치 증명이 아니다.

result17의 중심거리 하한에서 translation/yaw 이동·관측/지도 오차를 빼서만 cell을
배제한다. 작업이 끝나지 않은 cell은 남긴다. frozen reference와 현재 capture는
분리하며, 물리 prior/오차의 타당성은 생산자가 검증해야 한다.
"""

from collections import deque
from dataclasses import dataclass
import math


def _number(value, name, limit=10000):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > limit:
        raise ValueError(name + ' must be a bounded finite number')
    return float(value)


def _point(value):
    if type(value) is not tuple or len(value) != 2:
        raise ValueError('point must be an exact XY pair')
    return tuple(_number(v, 'coordinate') for v in value)


def wrap_yaw(value):
    return (value + math.pi) % (2 * math.pi) - math.pi


def transform_point(point, pose):
    x, y = point
    px, py, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    return px + c * x - s * y, py + s * x + c * y


def segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    if den == 0:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = max(0., min(1., ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / den))
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


@dataclass(frozen=True)
class PointFeature:
    point: tuple[float, float]
    error_m: float
    component: str
    capture_id: str
    compatible: tuple[str, ...] = ()

    def __post_init__(self):
        _point(self.point)
        if _number(self.error_m, 'point error') < 0:
            raise ValueError('negative point error')
        if not self.component or not self.capture_id or type(self.compatible) is not tuple:
            raise ValueError('feature requires component/source identity')


@dataclass(frozen=True)
class SegmentFeature:
    a: tuple[float, float]
    b: tuple[float, float]
    error_m: float
    component: str
    capture_id: str

    def __post_init__(self):
        _point(self.a)
        _point(self.b)
        if _number(self.error_m, 'segment error') < 0 or not self.component or not self.capture_id:
            raise ValueError('invalid segment error/source identity')


@dataclass(frozen=True)
class FrozenReference:
    revision: int
    calibration_id: str
    segments: tuple[SegmentFeature, ...]
    shared_error_m: float = 0.

    def __post_init__(self):
        if type(self.revision) is not int or self.revision < 0 or not self.calibration_id:
            raise ValueError('reference requires revision and calibration identity')
        if (type(self.segments) is not tuple or len(self.segments) > 64
                or any(type(s) is not SegmentFeature for s in self.segments)):
            raise ValueError('reference segment cap exceeded')
        if _number(self.shared_error_m, 'shared error') < 0:
            raise ValueError('negative inherited error')


@dataclass(frozen=True)
class PoseBox:
    x: tuple[float, float]
    y: tuple[float, float]
    yaw: tuple[float, float]

    def __post_init__(self):
        for name in ('x', 'y', 'yaw'):
            bounds = _point(getattr(self, name))
            if bounds[0] > bounds[1]:
                raise ValueError('reversed prior interval')
        # yaw를 연속된 unwrapped 구간으로 보관한다. ±pi 경계를 버리지 않는다.
        if self.yaw[1] - self.yaw[0] > 2 * math.pi:
            raise ValueError('yaw interval exceeds one full rotation')

    @property
    def center(self):
        return tuple((v[0] + v[1]) / 2 for v in (self.x, self.y, self.yaw))

    @property
    def widths(self):
        return tuple(v[1] - v[0] for v in (self.x, self.y, self.yaw))

    def split(self, steps):
        widths = self.widths
        axis = max(range(3), key=lambda i: (widths[i] / steps[i], -i))
        bounds = [self.x, self.y, self.yaw]
        lo, hi = bounds[axis]
        middle = (lo + hi) / 2
        if middle in (lo, hi):
            return None
        first, second = bounds.copy(), bounds.copy()
        first[axis], second[axis] = (lo, middle), (middle, hi)
        return PoseBox(*first), PoseBox(*second)


@dataclass(frozen=True)
class RegistrationProfile:
    cell_steps: tuple[float, float, float] = (.02, .02, .01)
    residual_m: float = .01
    max_position_error_m: float = .05
    max_yaw_error_rad: float = .05
    shared_bias_m: float = 0.
    shared_bias_rad: float = 0.
    max_cells: int = 4096
    max_distance_tests: int = 2**22
    outlier_components: int = 0
    numeric_guard_m: float = 1e-9

    def __post_init__(self):
        if type(self.cell_steps) is not tuple or len(self.cell_steps) != 3:
            raise ValueError('registration needs three split resolutions')
        for value in self.cell_steps:
            if _number(value, 'cell step') <= 0:
                raise ValueError('nonpositive cell step')
        for name in ('residual_m', 'max_position_error_m', 'max_yaw_error_rad',
                     'shared_bias_m', 'shared_bias_rad', 'numeric_guard_m'):
            if _number(getattr(self, name), name) < 0:
                raise ValueError('negative registration bound')
        for name, maximum in (('max_cells', 4096), ('max_distance_tests', 2**22)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError('registration work cap exceeded')
        if type(self.outlier_components) is not int or not 0 <= self.outlier_components <= 4:
            raise ValueError('outlier budget must count components')


@dataclass(frozen=True)
class RegistrationResult:
    status: str
    representative: tuple[float, float, float] | None
    position_error_m: float | None
    yaw_error_rad: float | None
    feasible: tuple[PoseBox, ...]
    reference_revision: int
    tested_cells: int
    distance_tests: int
    complete: bool


def register(points, reference, prior, profile=RegistrationProfile(), *, calibration_id):
    """연속 prior의 conservative outer set과 유한 representative를 반환한다.

    max64 sample/segment, cap 도달 시 UNKNOWN_BUDGET다. BOUNDED는 입력 prior와
    feature-error 가정 아래의 수학적 결과이며 실제 odom/scene·이동 권한은 아니다.
    """
    if (type(reference) is not FrozenReference or type(prior) is not PoseBox
            or type(profile) is not RegistrationProfile or calibration_id != reference.calibration_id):
        raise ValueError('registration context mismatch')
    if (type(points) is not tuple or not 1 <= len(points) <= 64
            or any(type(p) is not PointFeature for p in points)):
        raise ValueError('registration requires 1..64 point features')
    captures = {p.capture_id for p in points}
    if any(s.capture_id in captures for s in reference.segments):
        raise ValueError('current capture cannot validate itself against the reference')
    if not reference.segments:
        return RegistrationResult('NO_REFERENCE', None, None, None, (prior,), reference.revision, 0, 0, False)
    compatible = [tuple(s for s in reference.segments if not p.compatible or s.component in p.compatible)
                  for p in points]
    missing_components = {p.component for p, value in zip(points, compatible) if not value}
    if len(missing_components) > profile.outlier_components:
        return RegistrationResult('INCONSISTENT', None, None, None, (), reference.revision, 0, 0, True)
    if len({p.component for p in points}) <= profile.outlier_components:
        return RegistrationResult('UNOBSERVABLE', None, None, None, (prior,), reference.revision, 0, 0, True)
    pending, leaves = deque((prior,)), []
    tested = distance_tests = 0
    candidates = []
    exhausted = False
    while pending:
        cell = pending.popleft()
        needed = sum(len(s) for s in compatible)
        if tested >= profile.max_cells or distance_tests + needed > profile.max_distance_tests:
            pending.appendleft(cell)
            exhausted = True
            break
        tested += 1
        center = cell.center
        ex, ey, etheta = (w / 2 for w in cell.widths)
        translation = math.hypot(ex, ey)
        violations, nominal_violations, costs = set(), set(), {}
        for point, segments in zip(points, compatible):
            if not segments:
                violations.add(point.component)
                nominal_violations.add(point.component)
                costs.setdefault(point.component, []).append(.25)
                continue
            location = transform_point(point.point, center)
            distances = [segment_distance(location, s.a, s.b) for s in segments]
            distance_tests += len(segments)
            # 연속 yaw 구간의 이동 상한을 사용한다.
            motion = translation + 2 * math.hypot(*point.point) * math.sin(etheta / 2)
            lower = min(max(0., d - motion - point.error_m - s.error_m
                            - reference.shared_error_m - profile.numeric_guard_m)
                        for d, s in zip(distances, segments))
            nominal = min(max(0., d - point.error_m - s.error_m - reference.shared_error_m)
                          for d, s in zip(distances, segments))
            if lower > profile.residual_m:
                violations.add(point.component)
            if nominal > profile.residual_m + profile.numeric_guard_m:
                nominal_violations.add(point.component)
            costs.setdefault(point.component, []).append(min(min(distances)**2, .25))
        if len(violations) > profile.outlier_components:
            continue
        if len(nominal_violations) <= profile.outlier_components:
            # component별 가중치를 제한해 긴 선이 loss를 독점하지 않게 한다.
            rank = sum(sum(v) / len(v) for v in costs.values())
            candidates.append((rank, center))
            if len(candidates) > profile.max_cells:
                raise AssertionError('representative pool exceeded the tested-cell cap')
        if all(w <= step for w, step in zip(cell.widths, profile.cell_steps)):
            leaves.append(cell)
        else:
            split = cell.split(profile.cell_steps)
            if split is None:
                leaves.append(cell)
            else:
                pending.extend(split)
    feasible = tuple(leaves) + tuple(pending)
    if not feasible:
        return RegistrationResult('INCONSISTENT', None, None, None, (), reference.revision,
                                  tested, distance_tests, True)
    # 최종 F에 포함된 후보만 쓴다. 배제된 조상 cell의 후보를 자동 채택하지 않는다.
    def inside(pose):
        return any(all(lo <= v <= hi for v, (lo, hi) in zip(pose, (b.x, b.y, b.yaw))) for b in feasible)
    representative = next((c for c in sorted(candidates) if inside(c[1])), None)
    if representative is None:
        return RegistrationResult('UNKNOWN_BUDGET' if exhausted else 'UNRESOLVED', None, None, None,
                                  feasible, reference.revision, tested, distance_tests, not exhausted)
    pose = representative[1]
    position_error = max(math.hypot(max(abs(b.x[0]-pose[0]), abs(b.x[1]-pose[0])),
                                   max(abs(b.y[0]-pose[1]), abs(b.y[1]-pose[1]))) for b in feasible)
    yaw_error = max(max(abs(b.yaw[0]-pose[2]), abs(b.yaw[1]-pose[2])) for b in feasible)
    position_error += reference.shared_error_m + profile.shared_bias_m + profile.numeric_guard_m
    yaw_error += profile.shared_bias_rad
    status = ('UNKNOWN_BUDGET' if exhausted else
              'BOUNDED' if position_error <= profile.max_position_error_m and yaw_error <= profile.max_yaw_error_rad
              else 'AMBIGUOUS_OR_UNOBSERVABLE')
    return RegistrationResult(status, pose, position_error, yaw_error, feasible,
                              reference.revision, tested, distance_tests, not exhausted)
