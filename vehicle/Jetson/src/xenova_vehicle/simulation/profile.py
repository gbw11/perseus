"""가상차 공통 개발 profile의 불변 값·source pin·SYNTHETIC 보정 생성.

원 simulator의 layout/후륜축/dynamics와 새 미실측 실험값을 구분한다. GT 차선·칸·
장애물·목표를 M/C에 전달하지 않으며 Scene 검사는 renderer source 일치 확인이다.
물리 bundle·원점·시계·관측·출력 권한은 발급하지 않는다. calibration의 이상 수학
flags는 SYNTHETIC 안에서만 사용하며 measured/physical_admission은 항상 False다.
"""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
import re


PROFILE = 'course-development-profile-1'
MAX_PROFILE_BYTES = 16 * 1024
SOURCE_COMMIT = '0b56c764eab16a62870eb1ec3c4be97bbc39d6ad'
_PREFIX = 'platform/apps/simulator/xenova_simulator/'
SOURCE_FILES = (
    (_PREFIX+'config.py',7593,'3b97f162f4e09a611cf22338d4badb3affcef29b8abe49605b0289d836b5e5dd'),
    (_PREFIX+'model.py',6158,'5d967907d4a1d6cde080eb3acc156638a10df0198e5f1a40f15e795ebd1d429b'),
    (_PREFIX+'placement.py',16570,'d090704577ace3620a60b7360616b5eea8fa855073a04a45a9c834cd8c9525e9'),
)
SCENE_SHA256 = '06076e845f281b298ea4697f7e35f4fd947e9a4fe4daddb82fc2a53de68003e3'
SCENE_SOURCE_PATH = 'frontend/packages/ui/src/sim/jetson-tracks.json'
SCENE_SOURCE_SHA256 = '8439f216058a4f749d02a75290641efd0ab673ad98dca81ad1ac3b792fefbe8c'
_SUPPORT = ((-2.,-3.),(6.,-3.),(6.,3.),(-2.,3.))
_DRIVE = dict(wheel_radius_m=.033,encoder_cpr=1320,motor_tau_s=.12,max_wheel_rpm=450.,
    max_accel_mps2=2.,max_decel_mps2=4.,max_steer_rate_rps=5.)


class DevelopmentProfileError(ValueError):
    """개발 source/profile의 exact 형식·수치·원 asset이 맞지 않는다."""


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        raise DevelopmentProfileError('development profile has missing or unknown fields')


def _number(value):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise DevelopmentProfileError('finite exact numeric experiment value required')
    return value


def _positive(value):
    if not 0 < _number(value) <= 10000:
        raise DevelopmentProfileError('positive bounded experiment value required')
    return value


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise DevelopmentProfileError('original lowercase SHA256 required')
    return value


def _same_number(value, expected):
    if _number(value) != expected:
        raise DevelopmentProfileError('profile changed original source configuration')


def _derived(value, expected):
    # decimal JSON과 원 공식의 float 반올림만 검사한다. 물리 오차 허용값이 아니다.
    if not math.isclose(_number(value),expected,rel_tol=0.,abs_tol=1e-12):
        raise DevelopmentProfileError('rear-axle or steering derivation differs')


def _pairs(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise DevelopmentProfileError('duplicate development profile key')
        result[key] = value
    return result


def _nonfinite(value):
    raise DevelopmentProfileError('nonfinite development profile constant')


def _validate(value):
    _keys(value,'profile identity source vehicle camera rates surface qualification')
    if value['profile'] != PROFILE:
        raise DevelopmentProfileError('unsupported development profile version')
    identity = value['identity']
    _keys(identity,'vehicle configuration calibration')
    if identity != dict(vehicle='rc-sim-01',configuration='course-sim-1',calibration='course-sim-encoder-1'):
        raise DevelopmentProfileError('explicit common experiment identities required')
    source = value['source']
    _keys(source,'commit files scene')
    if source['commit'] != SOURCE_COMMIT or type(source['files']) is not list or len(source['files']) != 3:
        raise DevelopmentProfileError('pinned simulator source required')
    for row,(path,size,digest) in zip(source['files'],SOURCE_FILES):
        _keys(row,'path size_bytes sha256')
        if (row['path'] != path or type(row['size_bytes']) is not int or row['size_bytes'] != size
                or row['sha256'] != digest):
            raise DevelopmentProfileError('simulator original file pin differs')
    scene = source['scene']
    _keys(scene,'file size_bytes sha256 base_sha256 derived_from_profile_sha256 source_path source_sha256 track_id')
    if (scene['file'] != 'jetson-demo.json' or type(scene['size_bytes']) is not int or not 0<scene['size_bytes']<=65536
            or scene['base_sha256'] != SCENE_SHA256 or scene['source_path'] != SCENE_SOURCE_PATH
            or scene['source_sha256'] != SCENE_SOURCE_SHA256 or scene['track_id'] != 'jetson-demo'):
        raise DevelopmentProfileError('selected original scene pin differs')
    _digest(scene['sha256'])
    if scene['derived_from_profile_sha256'] is None:
        if scene['sha256'] != SCENE_SHA256 or scene['size_bytes'] != 5137:
            raise DevelopmentProfileError('base profile must pin the original scene bytes')
    else:_digest(scene['derived_from_profile_sha256'])
    vehicle = value['vehicle']
    _keys(vehicle,'pose_origin length_m width_m wheelbase_m min_turn_radius_m front_m rear_m track_width_m '
        'track_width_source wheel_radius_m encoder_cpr motor_tau_s max_wheel_rpm max_accel_mps2 '
        'max_decel_mps2 max_steer_rad max_steer_rate_rps estimated_steering_error_rad steering_error_source')
    if vehicle['pose_origin'] != 'REAR_AXLE_FLU':
        raise DevelopmentProfileError('rear-axle origin must be explicit')
    for key in ('length_m','width_m','wheelbase_m','min_turn_radius_m','front_m','rear_m','track_width_m'):
        _positive(vehicle[key])
    if not vehicle['wheelbase_m'] < vehicle['length_m'] or not vehicle['track_width_m'] <= vehicle['width_m']:
        raise DevelopmentProfileError('body/wheel track does not fit the stated vehicle')
    _derived(vehicle['front_m'],(vehicle['length_m']+vehicle['wheelbase_m'])/2)
    _derived(vehicle['rear_m'],(vehicle['length_m']-vehicle['wheelbase_m'])/2)
    _derived(vehicle['max_steer_rad'],math.atan(vehicle['wheelbase_m']/vehicle['min_turn_radius_m']))
    for key,expected in _DRIVE.items():
        if key == 'encoder_cpr' and type(vehicle[key]) is not int:
            raise DevelopmentProfileError('encoder CPR must be an exact source integer')
        _same_number(vehicle[key],expected)
    if (vehicle['track_width_source'] != 'new_unmeasured_experiment_value'
            or vehicle['steering_error_source'] != 'new_unmeasured_quantization_assumption'
            or not 0 < _number(vehicle['estimated_steering_error_rad']) <= math.pi):
        raise DevelopmentProfileError('explicit positive unmeasured track/steering assumptions required')
    camera = value['camera']
    _keys(camera,'rgb_size right_size ahead_m height_m pitch_rad hfov_rad baseline_m baseline_source fps')
    for key,expected in (('rgb_size',[640,360]),('right_size',[640,480])):
        if (type(camera[key]) is not list or len(camera[key]) != 2
                or any(type(n) is not int for n in camera[key]) or camera[key] != expected):
            raise DevelopmentProfileError('explicit RGB/native RIGHT shapes required')
    for key in ('ahead_m','height_m','baseline_m'):
        _positive(camera[key])
    if (not 0 < _number(camera['pitch_rad']) < math.pi/2
            or not 0 < _number(camera['hfov_rad']) < math.pi
            or camera['baseline_m'] >= 1
            or camera['baseline_source'] != 'synthetic_calibration_default_not_measured'):
        raise DevelopmentProfileError('explicit supported synthetic camera assumptions required')
    _same_number(camera['fps'],15.)
    _keys(value['rates'],'physics_hz'); _same_number(value['rates']['physics_hz'],100.)
    surface = value['surface']
    _keys(surface,'assumption support_polygon')
    points = surface['support_polygon']
    if (surface['assumption'] != 'ASSUMED_FLAT_SUPPORTED_APRON' or type(points) is not list or len(points) != 4
            or any(type(p) is not list or len(p) != 2 for p in points)):
        raise DevelopmentProfileError('explicit existing flat support assumption required')
    for point,expected in zip(points,_SUPPORT):
        for coordinate,original in zip(point,expected):_same_number(coordinate,original)
    qualification = value['qualification']
    _keys(qualification,'measured physical_admission')
    if qualification['measured'] is not False or qualification['physical_admission'] is not False:
        raise DevelopmentProfileError('development profile cannot claim physical qualification')


def _model_fields(value):
    """scene/pose/GT/파생 profile과 독립인 원 camera·body·flat-model subset이다."""
    vehicle,camera = value['vehicle'],value['camera']
    return dict(profile='course-simulation-calibration-model-1',
        body={key:vehicle[key] for key in ('pose_origin','length_m','width_m','wheelbase_m','front_m','rear_m')},
        camera={key:camera[key] for key in ('rgb_size','right_size','ahead_m','height_m','pitch_rad',
            'hfov_rad','baseline_m','baseline_source')},surface=value['surface'],
        source=dict(commit=value['source']['commit'],files=value['source']['files']),
        qualification=value['qualification'])


def _canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',', ':'),allow_nan=False).encode('ascii')


@dataclass(frozen=True, slots=True, init=False)
class DevelopmentProfile:
    """최대16KiB JSON의 canonical 사본이다. identity.calibration은 encoder label이며 bundle SHA가 아니다."""

    _canonical_bytes: bytes = field(repr=False)
    _sha256: str
    _calibration_model_sha256: str

    @classmethod
    def from_bytes(cls, raw):
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PROFILE_BYTES:
            raise DevelopmentProfileError('development profile requires bytes1..16384')
        try:
            value = json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=_nonfinite)
            _validate(value)
            owned = _canonical(value)
        except (UnicodeError,json.JSONDecodeError,RecursionError,OverflowError) as error:
            raise DevelopmentProfileError('invalid bounded development profile JSON') from error
        if len(owned) > MAX_PROFILE_BYTES:
            raise DevelopmentProfileError('canonical development profile exceeds16384 bytes')
        result = object.__new__(cls)
        object.__setattr__(result,'_canonical_bytes',owned)
        object.__setattr__(result,'_sha256',sha256(owned).hexdigest())
        object.__setattr__(result,'_calibration_model_sha256',sha256(_canonical(_model_fields(value))).hexdigest())
        return result

    @property
    def canonical_bytes(self):return self._canonical_bytes
    @property
    def sha256(self):return self._sha256
    @property
    def fields(self):return json.loads(self._canonical_bytes)
    @property
    def calibration_model_sha256(self):return self._calibration_model_sha256

    def _check_layout(self, scene):
        from .scene import Scene
        if type(scene) is not Scene:
            raise DevelopmentProfileError('original exact Scene required')
        value = self.fields;source = value['source']['scene'];fields = scene.fields
        if (fields.get('source_commit') != value['source']['commit']
                or fields.get('source_path') != source['source_path']
                or fields.get('source_sha256') != source['source_sha256']):
            raise DevelopmentProfileError('actual original scene/source SHA differs')
        try:
            track = fields['track'];layout = track['layout']
            body = layout['vehicle'];camera = layout['camera']
            _keys(body,'length_m width_m wheelbase_m min_turn_radius_m')
            _keys(camera,'height_m pitch_rad hfov_rad mount_x_m')
            if track['id'] != source['track_id']:
                raise DevelopmentProfileError('selected track differs')
            for key in body:_same_number(body[key],value['vehicle'][key])
            for actual,requested in (('height_m','height_m'),('pitch_rad','pitch_rad'),
                    ('hfov_rad','hfov_rad'),('mount_x_m','ahead_m')):
                _same_number(camera[actual],value['camera'][requested])
        except (KeyError,TypeError) as error:
            raise DevelopmentProfileError('selected layout body/camera metadata missing') from error

    def check_scene(self, scene):
        """자기 원 scene SHA와 선택 layout body/camera를 대조한다. GT는 내보내지 않는다."""
        self._check_layout(scene)
        source = self.fields['source']['scene']
        if len(scene.canonical_bytes) != source['size_bytes'] or scene.sha256 != source['sha256']:
            raise DevelopmentProfileError('actual original scene/source SHA differs')

    def for_scene(self, scene):
        """명시 renderer 변형을 원 기하·source pin에 결박한다. clock/관측/새 보정은 발급하지 않는다."""
        self._check_layout(scene)
        value = self.fields;source = value['source']['scene']
        if source['sha256'] == scene.sha256 and source['size_bytes'] == len(scene.canonical_bytes):
            return self
        source.update(sha256=scene.sha256,size_bytes=len(scene.canonical_bytes),
            derived_from_profile_sha256=self.sha256)
        return type(self).from_bytes(_canonical(value))

    def calibration(self):
        """후륜축 FLU의 명시 SYNTHETIC 모델이다. 운영 bundle/물리 FREE 자격으로 승격하지 않는다."""
        from ..perception.calibration import BodyModel,CalibrationProfile,synthetic_calibration
        value = self.fields;vehicle = value['vehicle'];camera = value['camera']
        model = synthetic_calibration(tuple(camera['rgb_size']),tuple(camera['right_size']),
            height_m=camera['height_m'],forward_m=camera['ahead_m'],pitch_rad=camera['pitch_rad'],
            hfov_deg=math.degrees(camera['hfov_rad']),baseline_m=camera['baseline_m'],
            support_polygon=tuple(tuple(p) for p in value['surface']['support_polygon']))
        body = BodyModel(length=vehicle['length_m'],width=vehicle['width_m'],wheelbase=vehicle['wheelbase_m'],
            front=vehicle['front_m'],rear=vehicle['rear_m'])
        fields = model.to_dict();fields['body'] = asdict(body)
        fields['provenance'] = dict(model.provenance,development_calibration_model_sha256=self.calibration_model_sha256,
            identity=value['identity'],measured=False,physical_admission=False,
            body_origin='REAR_AXLE_FLU',body_derivation='FRONT=(L+WB)/2;REAR=(L-WB)/2',
            camera_ahead_origin='REAR_AXLE',support=value['surface']['assumption'])
        return CalibrationProfile.from_dict(fields)
