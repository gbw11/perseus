"""원 stereo 보정에서 소유한 nominal K/R/ROI와 DepthAI2.29 mesh를 만든다.

factory K를 active K로 추정하지 않는다. 선택한 OpenCV projection과 mesh sample의
수학 artifact이며 Device 적용/readback·실제 warp 보간·matcher support·물리 오차·
설치/G3/metric 수락은 별도다. 기존 CameraModel/CalibrationProfile을 변경하지 않는다.

고정 근거: depthai-core/v2.29.0 StereoDepth.hpp의 loadMeshFiles/setBaseline/
setFocalLength, depthai-python/v2.29.0.0 stereo_depth_video.py의 mesh 배치.
https://raw.githubusercontent.com/luxonis/depthai-core/v2.29.0/include/depthai/pipeline/node/StereoDepth.hpp
https://raw.githubusercontent.com/luxonis/depthai-python/v2.29.0.0/examples/StereoDepth/stereo_depth_video.py
고정640/16에서 row padding은0이다. SDK asset base의64-byte alignment는 mesh 내용이 아니다.
공식 예제의 마지막 pixel clamp는 쓰지 않고 같은 K/P의640,480 연속 좌표까지 평가한다.
"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import re
import struct


INPUT_SIZE = OUTPUT_SIZE = (640, 480)
MESH_STEP = (16, 16)
MESH_SHAPE = (31, 41, 2)
MESH_BYTES = 31 * 41 * 2 * 4
DESCRIPTOR_CAP = 32768
OPENCV_VERSION = '4.10.0'
DEPTHAI_VERSION = '2.29.0.0'


class RectificationError(ValueError):
    """원 입력·지원 profile·producer 수치 또는 독립 적용 pin이 맞지 않는다."""


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise RectificationError('original lowercase SHA256 pin required')
    return value


def _number(value):
    if type(value) not in (int, float):
        raise RectificationError('exact finite numeric calibration required')
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise RectificationError('calibration number exceeds finite float64') from error
    if not math.isfinite(result) or type(value) is int and int(result) != value:
        raise RectificationError('calibration number loses its original value')
    return result


def _vector(value, lengths):
    if type(value) not in (tuple, list) or len(value) not in lengths:
        raise RectificationError('unsupported calibration vector shape')
    return tuple(_number(item) for item in value)


def _matrix(value):
    if type(value) not in (tuple, list) or len(value) != 3:
        raise RectificationError('original3x3 calibration matrix required')
    return tuple(_vector(row, (3,)) for row in value)


def _intrinsics(value):
    result = _matrix(value)
    if (result[0][0] <= 0 or result[1][1] <= 0 or result[2] != (0., 0., 1.)
            or result[0][1] != 0. or result[1][0] != 0.):
        raise RectificationError('supported pinhole K requires positive focal/zero skew/normalized last row')
    return result


def _rotation(value):
    result = _matrix(value)
    # EEPROM float 수치의 rotation 형식 검산이다. 이 여유를 물리 extrinsic 오차로 쓰지 않는다.
    for i in range(3):
        for j in range(3):
            if not math.isclose(sum(result[k][i]*result[k][j] for k in range(3)),
                                float(i == j), rel_tol=1e-6, abs_tol=1e-6):
                raise RectificationError('calibrated extrinsic rotation is not orthonormal')
    determinant = (result[0][0]*(result[1][1]*result[2][2]-result[1][2]*result[2][1])
        - result[0][1]*(result[1][0]*result[2][2]-result[1][2]*result[2][0])
        + result[0][2]*(result[1][0]*result[2][1]-result[1][1]*result[2][0]))
    if not math.isclose(determinant, 1., rel_tol=1e-6, abs_tol=1e-6):
        raise RectificationError('calibrated extrinsic rotation must be proper')
    return result


def _distortion(value):
    result = _vector(value, (4, 5, 8, 14))
    if len(result) == 14 and any(result[8:]):
        raise RectificationError('nonzero thin-prism/tilt distortion is unsupported')
    return result


def _size(value, expected):
    if (type(value) not in (tuple, list) or len(value) != 2
            or any(type(item) is not int for item in value) or tuple(value) != expected):
        raise RectificationError('only explicit640x480 and meshstep16 are supported')
    return tuple(value)


def _canonical(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                     allow_nan=False).encode('ascii')
    if len(raw) > DESCRIPTOR_CAP:
        raise RectificationError('rectification descriptor exceeds32KiB')
    return raw


def _f32(value):
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except (OverflowError, struct.error) as error:
        raise RectificationError('SDK float32 scale is not finite') from error
    if not math.isfinite(result) or result <= 0:
        raise RectificationError('SDK float32 scale must remain positive')
    return result


@dataclass(frozen=True, slots=True, init=False)
class RectificationRequest:
    """양쪽 optical RDF에서 P_right=R*P_left+T_cm이다. source를 읽거나 승인하지 않는다."""

    factory_raw_sha256: str
    left_k: tuple
    left_d: tuple
    right_k: tuple
    right_d: tuple
    right_from_left_r: tuple
    right_from_left_t_cm: tuple
    input_size: tuple
    output_size: tuple
    mesh_step: tuple
    alpha: float
    distortion_model: str
    _canonical_bytes: bytes = field(repr=False)
    _request_subset_bytes: bytes = field(repr=False)

    def __init__(self, factory_raw_sha256, left_k, left_d, right_k, right_d,
                 right_from_left_r, right_from_left_t_cm, *, input_size, output_size,
                 mesh_step, alpha, distortion_model='BROWN_CONRADY'):
        source = _digest(factory_raw_sha256)
        if type(distortion_model) is not str or distortion_model != 'BROWN_CONRADY':
            raise RectificationError('only explicitly identified pinhole BrownConrady is supported')
        values = dict(factory_raw_sha256=source, left_k=_intrinsics(left_k), left_d=_distortion(left_d),
            right_k=_intrinsics(right_k), right_d=_distortion(right_d),
            right_from_left_r=_rotation(right_from_left_r), right_from_left_t_cm=_vector(right_from_left_t_cm, (3,)),
            input_size=_size(input_size, INPUT_SIZE), output_size=_size(output_size, OUTPUT_SIZE),
            mesh_step=_size(mesh_step, MESH_STEP), alpha=_number(alpha), distortion_model=distortion_model)
        if not 0 <= values['alpha'] <= 1 or math.hypot(*values['right_from_left_t_cm']) <= 0:
            raise RectificationError('explicit alpha0..1 and nonzero calibrated baseline required')
        subset = dict(input_size=values['input_size'], output_size=values['output_size'],
            mesh_step=values['mesh_step'], alpha=values['alpha'], zero_disparity=True,
            depth_align='RECTIFIED_RIGHT')
        original = dict(profile='owned-stereo-request-1', **values, translation_unit='cm',
            extrinsic_convention='P_RIGHT_EQUALS_R_P_LEFT_PLUS_T', graph_request_subset=subset)
        for key, item in values.items():
            object.__setattr__(self, key, item)
        object.__setattr__(self, '_canonical_bytes', _canonical(original))
        object.__setattr__(self, '_request_subset_bytes', _canonical(subset))

    @property
    def fields(self):return json.loads(self._canonical_bytes)
    @property
    def canonical_bytes(self):return self._canonical_bytes
    @property
    def sha256(self):return sha256(self._canonical_bytes).hexdigest()
    @property
    def request_subset(self):return json.loads(self._request_subset_bytes)
    @property
    def request_subset_sha256(self):return sha256(self._request_subset_bytes).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class OwnedRectificationPlan:
    """NumPy/view 없이 원 request·descriptor·양쪽 immutable mesh bytes만 소유한다."""

    request: RectificationRequest
    left_mesh_bytes: bytes = field(repr=False)
    right_mesh_bytes: bytes = field(repr=False)
    _canonical_bytes: bytes = field(repr=False)

    @property
    def fields(self):return json.loads(self._canonical_bytes)
    @property
    def canonical_bytes(self):return self._canonical_bytes
    @property
    def sha256(self):return sha256(self._canonical_bytes).hexdigest()
    @property
    def request_subset_sha256(self):return self.request.request_subset_sha256
    @property
    def left_k_rect(self):return tuple(tuple(row) for row in self.fields['left']['k_rect'])
    @property
    def right_k_rect(self):return tuple(tuple(row) for row in self.fields['right']['k_rect'])
    @property
    def left_r_rect(self):return tuple(tuple(row) for row in self.fields['left']['r_rect'])
    @property
    def right_r_rect(self):return tuple(tuple(row) for row in self.fields['right']['r_rect'])
    @property
    def left_projection(self):return tuple(tuple(row) for row in self.fields['left']['projection'])
    @property
    def right_projection(self):return tuple(tuple(row) for row in self.fields['right']['projection'])
    @property
    def left_roi(self):return tuple(self.fields['left']['roi'])
    @property
    def right_roi(self):return tuple(self.fields['right']['roi'])
    @property
    def baseline_cm(self):return self.fields['baseline_cm']
    @property
    def focal_px(self):return self.fields['focal_px']
    @property
    def sdk_baseline_cm(self):return self.fields['sdk_baseline_cm']
    @property
    def sdk_focal_px(self):return self.fields['sdk_focal_px']


def build_rectification_plan(request):
    """고정 OpenCV로 nominal projection과 mesh를 만든다. cv2/NumPy import는 이 계산에만 있다.

    mesh는 inverse map의(y,x), little-endian float32, row-major31×41이다. 동일 K_rect로
    평가 영역만641×481로 넓혀 u=640/v=480도 계산하며 dense 마지막 pixel로 clamp하지 않는다.
    OpenCV nominal ROI는 실제 sparse warp/matcher support의 전역 validity 증명이 아니다.
    """
    if type(request) is not RectificationRequest:
        raise RectificationError('original exact RectificationRequest required')
    import cv2
    import numpy as np

    if cv2.__version__ != OPENCV_VERSION:
        raise RectificationError('owned producer requires pinned OpenCV4.10.0')
    kl, kr = (np.asarray(value, dtype=np.float64) for value in (request.left_k, request.right_k))
    dl, dr = (np.asarray(value, dtype=np.float64) for value in (request.left_d, request.right_d))
    rotation = np.asarray(request.right_from_left_r, dtype=np.float64)
    translation = np.asarray(request.right_from_left_t_cm, dtype=np.float64)
    try:
        rl, rr, pl, pr, q, roi_l, roi_r = cv2.stereoRectify(kl, dl, kr, dr,
            request.input_size, rotation, translation, flags=cv2.CALIB_ZERO_DISPARITY,
            alpha=request.alpha, newImageSize=request.output_size)
    except cv2.error as error:
        raise RectificationError('stereoRectify rejected original calibration') from error
    for value, shape in ((rl,(3,3)),(rr,(3,3)),(pl,(3,4)),(pr,(3,4)),(q,(4,4))):
        if (type(value) is not np.ndarray or value.shape != shape or value.dtype != np.float64
                or not np.isfinite(value).all()):
            raise RectificationError('rectification producer returned malformed projection')
    _rotation(rl.tolist()); _rotation(rr.tolist())
    left_k, right_k = pl[:,:3], pr[:,:3]
    _intrinsics(left_k.tolist()); _intrinsics(right_k.tolist())
    baseline = math.hypot(*request.right_from_left_t_cm)
    focal = float(pl[0,0])
    # 소수 오차 허용은 같은 수학 projection의 수치 일관성 검사다. 물리 오차 상한이 아니다.
    close = lambda a,b: np.allclose(a,b,rtol=1e-10,atol=1e-8)
    if (not close(left_k,right_k) or not close(pl[:,3],np.zeros(3))
            or pr[1,3] != 0. or pr[2,3] != 0. or pr[0,3] >= 0.
            or not close(left_k[0,0],left_k[1,1])
            or not close(rr @ rotation @ rl.T,np.eye(3))
            or not close(rr @ translation,(-baseline,0.,0.))
            or not close(pr[0,3],-focal*baseline)):
        raise RectificationError('only consistent horizontal positive-disparity projection is supported')
    rois = []
    for value in (roi_l,roi_r):
        if (type(value) not in (tuple,list) or len(value) != 4
                or any(type(item) is not int for item in value)):
            raise RectificationError('original OpenCV ROI tuple required')
        x,y,width,height = value
        if min(x,y) < 0 or min(width,height) <= 0 or x+width > 640 or y+height > 480:
            raise RectificationError('nominal rectification ROI is empty or outside output')
        rois.append(tuple(value))

    def mesh(k, d, r, rect_k):
        try:
            map_x,map_y = cv2.initUndistortRectifyMap(k,d,r,rect_k,(641,481),cv2.CV_32FC1)
        except cv2.error as error:
            raise RectificationError('owned continuous inverse mesh projection failed') from error
        if (map_x.shape != (481,641) or map_y.shape != (481,641)
                or map_x.dtype != np.float32 or map_y.dtype != np.float32):
            raise RectificationError('owned inverse map format differs')
        samples = np.stack((map_y[::16,::16],map_x[::16,::16]),axis=-1)
        if samples.shape != MESH_SHAPE or not np.isfinite(samples).all():
            raise RectificationError('owned mesh samples are not finite31x41x2')
        raw = samples.astype('<f4',copy=False).tobytes(order='C')
        if len(raw) != MESH_BYTES:
            raise RectificationError('owned mesh byte length differs')
        return raw

    left_mesh = mesh(kl,dl,rl,left_k)
    right_mesh = mesh(kr,dr,rr,right_k)
    sdk_baseline,sdk_focal = _f32(baseline),_f32(focal)
    descriptor = dict(profile='owned-stereo-rectification-1', factory_raw_sha256=request.factory_raw_sha256,
        request_sha256=request.sha256, request_subset_sha256=request.request_subset_sha256,
        request=request.fields, producer=dict(opencv=cv2.__version__,numpy=np.__version__),
        mesh=dict(dtype='<f4',order='ROW_MAJOR_Y_X',shape=MESH_SHAPE,step=request.mesh_step,
            evaluation_size=(641,481),row_padding_bytes=0,boundary='CONTINUOUS_PROJECTION_AT_640_480'),
        left=dict(k_rect=left_k.tolist(),r_rect=rl.tolist(),projection=pl.tolist(),roi=rois[0],
            mesh_sha256=sha256(left_mesh).hexdigest(),mesh_bytes=len(left_mesh)),
        right=dict(k_rect=right_k.tolist(),r_rect=rr.tolist(),projection=pr.tolist(),roi=rois[1],
            mesh_sha256=sha256(right_mesh).hexdigest(),mesh_bytes=len(right_mesh)),
        disparity_to_depth_q=q.tolist(),disparity_q_depth_unit='cm',baseline_cm=baseline,focal_px=focal,
        sdk_baseline_cm=sdk_baseline,sdk_focal_px=sdk_focal,
        sdk_scale_relative_rounding=abs((sdk_baseline*sdk_focal)/(baseline*focal)-1.),
        roi_semantics='OPENCV_NOMINAL_NOT_DEVICE_INTERPOLATION_OR_MATCHER_SUPPORT',
        metric_qualified=False,g3_qualified=False,
        qualification='NOMINAL_PRODUCER_NOT_INSTALLATION_OR_PHYSICAL_ERROR_ADMISSION')
    result = object.__new__(OwnedRectificationPlan)
    for name,value in (('request',request),('left_mesh_bytes',left_mesh),('right_mesh_bytes',right_mesh),
                       ('_canonical_bytes',_canonical(descriptor))):
        object.__setattr__(result,name,value)
    return result


def apply_rectification_plan(stereo, plan, *, factory_raw_sha256, request_subset_sha256, depthai_version):
    """독립 pin 뒤 고정2.29의 네 graph setter만 호출한다. 적용 성공은 readback/수락이 아니다."""
    if type(plan) is not OwnedRectificationPlan or type(plan.request) is not RectificationRequest:
        raise RectificationError('original owned rectification plan required')
    if (type(depthai_version) is not str or depthai_version != DEPTHAI_VERSION
            or _digest(factory_raw_sha256) != plan.request.factory_raw_sha256
            or _digest(request_subset_sha256) != plan.request_subset_sha256):
        raise RectificationError('actual factory/independent graph request/fixed SDK differs')
    names = ('setMeshStep','loadMeshData','setFocalLength','setBaseline')
    methods = tuple(getattr(stereo,name,None) for name in names)
    if any(not callable(method) for method in methods):
        raise RectificationError('fixed StereoDepth mesh/scale API is unavailable')
    fields = plan.fields
    methods[0](*plan.request.mesh_step)
    methods[1](list(plan.left_mesh_bytes),list(plan.right_mesh_bytes))
    methods[2](fields['sdk_focal_px'])
    methods[3](fields['sdk_baseline_cm'])
