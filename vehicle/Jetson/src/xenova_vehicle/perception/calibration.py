"""불변 인지 보정 bundle. 해시·nominal 기하와 실물 수락 자격을 구분한다.

optical은 RDF, base는 후륜축 FLU이며 위치는 m/각도는 rad다. factory EEPROM,
장착·plane·시간·first-surface 수락은 별개다. SYNTHETIC은 명시한 이상 모델이며
이 bundle SHA를 실차 VehicleSample의 승인된 차량 보정 ID로 자동 승격하지 않는다.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import math


IDENTITY = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('ascii')


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('expected finite builtin number')
    return float(value)


def vector(value, size):
    if type(value) not in (tuple, list) or len(value) != size:
        raise ValueError('vector shape mismatch')
    return tuple(number(x) for x in value)


def matrix(value, *, rotation=False):
    if type(value) not in (tuple, list):
        raise ValueError('matrix requires a native sequence')
    if len(value) == 9:
        value = tuple(value[i:i + 3] for i in (0, 3, 6))
    result = tuple(vector(row, 3) for row in value)
    if len(result) != 3:
        raise ValueError('matrix must be 3x3')
    if rotation:
        error = max(abs(sum(result[k][i] * result[k][j] for k in range(3)) - (i == j))
                    for i in range(3) for j in range(3))
        det = sum(result[0][i] * (result[1][(i + 1) % 3] * result[2][(i + 2) % 3]
                  - result[1][(i + 2) % 3] * result[2][(i + 1) % 3]) for i in range(3))
        if error > 1e-6 or abs(det - 1.) > 1e-6:
            raise ValueError('rotation must be proper and orthonormal')
    return result


def _boolean(value):
    if type(value) is not bool:
        raise ValueError('qualification must be an exact bool')
    return value


def _nonnegative(value, maximum=100.):
    value = number(value)
    if not 0 <= value <= maximum:
        raise ValueError('error/profile bound is outside supported range')
    return value


def polygon(value):
    if type(value) not in (tuple, list) or not 3 <= len(value) <= 32:
        raise ValueError('support polygon requires 3..32 vertices')
    points = tuple(vector(p, 2) for p in value)
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(points, points[1:] + points[:1]))
    if abs(area) < 1e-12:
        raise ValueError('support polygon has no area')
    if area < 0:
        points = tuple(reversed(points))
    for a, b in zip(points, points[1:] + points[:1]):
        if any((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < -1e-12 for c in points):
            raise ValueError('support polygon must be simple and convex')
    return points


@dataclass(frozen=True)
class CameraModel:
    """active grid 모델. near_m/far_m는 radial range가 아닌 active optical axial-Z다."""
    width: int
    height: int
    k: tuple
    rotation: tuple
    translation: tuple
    roi: tuple | None = None
    rectification: tuple = IDENTITY
    distortion: tuple = ()
    distortion_model: str = 'PERSPECTIVE'
    pixel_error: float = .5
    ray_error_rad: float = 0.
    translation_error_m: float = 0.
    rotation_error_rad: float = 0.
    qualified: bool = False
    remap_qualified: bool = False
    near_m: float = .15
    far_m: float = 3.

    def __post_init__(self):
        if (type(self.width) is not int or type(self.height) is not int
                or not 1 <= self.width <= 1280 or not 1 <= self.height <= 720):
            raise ValueError('camera shape exceeds the input profile')
        k = matrix(self.k)
        if k[0][0] <= 0 or k[1][1] <= 0 or k[1][0] != 0 or k[2] != (0., 0., 1.):
            raise ValueError('K must be a conventional invertible pinhole matrix')
        object.__setattr__(self, 'k', k)
        object.__setattr__(self, 'rotation', matrix(self.rotation, rotation=True))
        object.__setattr__(self, 'rectification', matrix(self.rectification, rotation=True))
        object.__setattr__(self, 'translation', vector(self.translation, 3))
        roi = (0., 0., float(self.width), float(self.height)) if self.roi is None else vector(self.roi, 4)
        if not 0 <= roi[0] < roi[2] <= self.width or not 0 <= roi[1] < roi[3] <= self.height:
            raise ValueError('ROI must be a half-open subset of the active grid')
        object.__setattr__(self, 'roi', roi)
        if type(self.distortion) not in (tuple, list) or len(self.distortion) not in (0, 4, 5, 8, 12, 14):
            raise ValueError('unsupported perspective distortion coefficient count')
        object.__setattr__(self, 'distortion', tuple(number(v) for v in self.distortion))
        if type(self.distortion_model) is not str or self.distortion_model != 'PERSPECTIVE':
            raise ValueError('this active camera profile supports perspective distortion only')
        if any(self.distortion) and k[0][1] != 0:
            raise ValueError('OpenCV distorted projection with skew is unsupported')
        for name, maximum in (('pixel_error', 16.), ('ray_error_rad', math.pi),
                              ('rotation_error_rad', math.pi), ('translation_error_m', 10.)):
            object.__setattr__(self, name, _nonnegative(getattr(self, name), maximum))
        _boolean(self.qualified)
        _boolean(self.remap_qualified)
        near, far = number(self.near_m), number(self.far_m)
        if not 0 <= near < far <= 100:
            raise ValueError('invalid qualified near/far profile')
        object.__setattr__(self, 'near_m', near)
        object.__setattr__(self, 'far_m', far)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if (type(value) is not dict or set(value) - set(cls.__dataclass_fields__)
                or not {'width', 'height', 'k', 'rotation', 'translation'} <= set(value)):
            raise ValueError('camera requires a native object')
        return cls(**value)

    @property
    def sha256(self):
        return hashlib.sha256(canonical(self.to_dict())).hexdigest()


@dataclass(frozen=True)
class Plane:
    normal: tuple = (0., 0., 1.)
    offset: float = 0.
    normal_error_rad: float = 0.
    offset_error_m: float = 0.
    qualified: bool = False

    def __post_init__(self):
        n = vector(self.normal, 3)
        if abs(math.sqrt(sum(x * x for x in n)) - 1.) > 1e-6:
            raise ValueError('plane normal must have unit length')
        object.__setattr__(self, 'normal', n)
        object.__setattr__(self, 'offset', number(self.offset))
        object.__setattr__(self, 'normal_error_rad', _nonnegative(self.normal_error_rad, math.pi))
        object.__setattr__(self, 'offset_error_m', _nonnegative(self.offset_error_m, 10.))
        _boolean(self.qualified)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) - set(cls.__dataclass_fields__):
            raise ValueError('invalid plane object')
        return cls(**value)


@dataclass(frozen=True)
class BodyModel:
    length: float = .30
    width: float = .22
    wheelbase: float = .20
    front: float = .25
    rear: float = .05

    def __post_init__(self):
        for name in ('length', 'width', 'wheelbase', 'front', 'rear'):
            v = number(getattr(self, name))
            if not 0 < v <= 10:
                raise ValueError('invalid body dimension')
            object.__setattr__(self, name, v)
        if abs(self.front + self.rear - self.length) > 1e-9 or self.wheelbase > self.length:
            raise ValueError('body dimensions do not share the rear-axle origin')


@dataclass(frozen=True, init=False)
class CalibrationProfile:
    rgb: CameraModel
    right: CameraModel
    plane: Plane
    support_polygon: tuple
    body: BodyModel
    kind: str
    first_surface_qualified: bool
    time_qualified: bool
    minimum_target_qualified: bool
    depth_error_m: float
    depth_relative_error: float
    vehicle_bundle_sha256: str | None
    _provenance: bytes
    _external_leaves: tuple

    def __init__(self, rgb, right, plane, support_polygon, body=BodyModel(), kind='PROVISIONAL',
                 provenance=(), first_surface_qualified=False, time_qualified=False,
                 minimum_target_qualified=False, depth_error_m=0., depth_relative_error=0.,
                 vehicle_bundle_sha256=None, external_leaves=None):
        if type(rgb) is not CameraModel or type(right) is not CameraModel or type(plane) is not Plane or type(body) is not BodyModel:
            raise ValueError('calibration requires exact immutable component models')
        if kind not in ('LIVE', 'SYNTHETIC', 'PROVISIONAL'):
            raise ValueError('calibration kind must be explicit')
        leaves = {} if external_leaves is None else external_leaves
        if type(leaves) is not dict or len(leaves) > 16:
            raise ValueError('external leaf inventory exceeds bound')
        for key, value in leaves.items():
            if type(key) is not str or not 1 <= len(key) <= 64 or not key.isascii():
                raise ValueError('invalid external leaf ID')
            self._check_sha(value)
        if vehicle_bundle_sha256 is not None:
            self._check_sha(vehicle_bundle_sha256)
        provenance_raw = canonical(provenance)
        if len(provenance_raw) > 65536:
            raise ValueError('calibration provenance exceeds bound')
        values = dict(rgb=rgb, right=right, plane=plane, support_polygon=polygon(support_polygon),
            body=body, kind=kind, first_surface_qualified=_boolean(first_surface_qualified),
            time_qualified=_boolean(time_qualified), minimum_target_qualified=_boolean(minimum_target_qualified),
            depth_error_m=_nonnegative(depth_error_m, 10.),
            depth_relative_error=_nonnegative(depth_relative_error, 1.),
            vehicle_bundle_sha256=vehicle_bundle_sha256, _provenance=provenance_raw,
            _external_leaves=tuple(sorted(leaves.items())))
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @staticmethod
    def _check_sha(value):
        if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('expected lowercase SHA256')

    @property
    def provenance(self):
        return json.loads(self._provenance)

    @property
    def metric_qualified(self):
        return self.kind != 'PROVISIONAL' and self.rgb.qualified and self.right.qualified and self.plane.qualified

    @property
    def free_qualified(self):
        return (self.metric_qualified and self.right.remap_qualified and self.first_surface_qualified
                and self.time_qualified and self.minimum_target_qualified)

    @property
    def leaf_hashes(self):
        return dict(rgb=self.rgb.sha256, right=self.right.sha256,
                    plane=hashlib.sha256(canonical(self.plane.to_dict())).hexdigest(),
                    body=hashlib.sha256(canonical(asdict(self.body))).hexdigest(),
                    external=dict(self._external_leaves))

    def to_dict(self):
        return dict(schema_version=1, rgb=self.rgb.to_dict(), right=self.right.to_dict(),
            plane=self.plane.to_dict(), support_polygon=self.support_polygon, body=asdict(self.body), kind=self.kind,
            provenance=self.provenance, first_surface_qualified=self.first_surface_qualified,
            time_qualified=self.time_qualified, minimum_target_qualified=self.minimum_target_qualified,
            depth_error_m=self.depth_error_m, depth_relative_error=self.depth_relative_error,
            vehicle_bundle_sha256=self.vehicle_bundle_sha256, external_leaves=dict(self._external_leaves))

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
            raise ValueError('unsupported calibration bundle')
        expected = {'schema_version', 'rgb', 'right', 'plane', 'support_polygon', 'body', 'kind',
            'provenance', 'first_surface_qualified', 'time_qualified', 'minimum_target_qualified',
            'depth_error_m', 'depth_relative_error', 'vehicle_bundle_sha256', 'external_leaves'}
        if set(value) != expected or type(value['body']) is not dict or set(value['body']) != set(BodyModel.__dataclass_fields__):
            raise ValueError('calibration bundle has missing or unknown component fields')
        value = dict(value)
        value.pop('schema_version')
        value.update(rgb=CameraModel.from_dict(value['rgb']), right=CameraModel.from_dict(value['right']),
                     plane=Plane.from_dict(value['plane']), body=BodyModel(**value['body']))
        return cls(**value)

    @property
    def sha256(self):
        return hashlib.sha256(canonical(self.to_dict())).hexdigest()


MetricCalibration = CalibrationProfile


def extrinsic_cm_to_m(value):
    """SDK의 source→destination 4x4 extrinsic을 (R,t_m)로 한 번 변환한다."""
    if type(value) not in (list, tuple) or len(value) != 4:
        raise ValueError('expected 4x4 centimeter extrinsic')
    rows = tuple(vector(row, 4) for row in value)
    if rows[3] != (0., 0., 0., 1.):
        raise ValueError('invalid homogeneous extrinsic')
    return matrix(tuple(row[:3] for row in rows[:3]), rotation=True), tuple(row[3] * .01 for row in rows[:3])


def synthetic_calibration(rgb_size=(640, 360), right_size=(640, 480), *, height_m=.12,
                          forward_m=.20, pitch_rad=math.radians(15.), hfov_deg=69., baseline_m=.075,
                          support_polygon=((-2., -3.), (6., -3.), (6., 3.), (-2., 3.))):
    """실측이 아닌 ideal snapshot 모델. RIGHT는 RGB보다 base 오른쪽 baseline/2에 둔다."""
    height_m, forward_m, pitch_rad, hfov_deg, baseline_m = map(number,
        (height_m, forward_m, pitch_rad, hfov_deg, baseline_m))
    if not height_m > 0 or not 1 < hfov_deg < 170 or not 0 <= baseline_m < 1:
        raise ValueError('invalid synthetic optical profile')
    s, c = math.sin(pitch_rad), math.cos(pitch_rad)
    rotation = ((0., -s, c), (-1., 0., 0.), (0., -c, -s))
    def camera(size, lateral):
        width, height = size
        f = width / (2 * math.tan(math.radians(hfov_deg) / 2))
        return CameraModel(width, height, ((f, 0., (width - 1) / 2), (0., f, (height - 1) / 2), (0., 0., 1.)),
            rotation, (forward_m, lateral, height_m), qualified=True, remap_qualified=True)
    return CalibrationProfile(camera(rgb_size, 0.), camera(right_size, -baseline_m / 2),
        Plane(qualified=True), support_polygon, kind='SYNTHETIC',
        provenance=dict(model='IDEAL_FIRST_SURFACE_SNAPSHOT', measured=False,
            height_m=height_m, forward_m=forward_m, pitch_rad=pitch_rad, hfov_deg=hfov_deg,
            synthetic_baseline_m=baseline_m, support='ASSUMED_FLAT_SUPPORTED_APRON',
            depth_storage='ROUND_NEAREST_RAW16_AXIAL_MM', depth_quantization_bound_m=.0005),
        depth_error_m=.0005 + 1e-9, first_surface_qualified=True, time_qualified=True,
        minimum_target_qualified=True)
