"""RGB camera의 box 후보를 보수 영상 rectangle 합집합으로 투영한다.

lane 가림 후보 mask일 뿐 실제 장애물·보정·주행 권한이 아니다. 선언된 pinhole/
extrinsic 오차를 포함하며 near 교차와 distortion 불명은 유효 ROI 전체 가림이다.
RIGHT depth 원점은 바꾸지 않는다. native import는 호출 때만 수행한다.
"""

import math
from .calibration import CameraModel
from .geometry import PointBounds

MAX_BOXES = 8192
WORKSPACE_LIMIT_BYTES = 16 * 1024**2


def box_occlusion(boxes, camera):
    """최대8192 PointBounds→uint8 mask. 선언된 연속 오차 범위의 투영을 포함한다.

    좌표 interval의 pinhole rectangle을 채운다. translation은 base 축 각각의
    오차이며 angular error는 chord bound로 덮는다. 불명은 빈 mask가 아니다.
    명시 ndarray workspace는 box당1KiB+int32차분+출력/bool image로16MiB 안이다.
    외부 Python 객체와 NumPy/BLAS 내부 allocator 비용은 이 배열 상한과 별개다.
    """
    import numpy as np
    if type(camera) is not CameraModel or type(boxes) not in (tuple, list) or len(boxes) > MAX_BOXES:
        raise ValueError('occlusion requires a camera and at most8192 bounded boxes')
    if any(type(box) is not PointBounds for box in boxes):
        raise ValueError('occlusion requires exact PointBounds candidates')
    workspace = (len(boxes) * 1024 + (camera.height + 1) * (camera.width + 1) * 4
                 + camera.height * camera.width * 2)
    if workspace > WORKSPACE_LIMIT_BYTES:
        raise ValueError('occlusion explicit array workspace exceeds16MiB')
    mask = np.zeros((camera.height, camera.width), np.uint8)
    if not boxes:
        return mask
    x0, y0, x1, y1 = camera.roi
    rx0, ry0, rx1, ry1 = math.ceil(x0), math.ceil(y0), math.ceil(x1) - 1, math.ceil(y1) - 1
    if rx0 > rx1 or ry0 > ry1:
        return mask
    def full_roi():
        mask[ry0:ry1 + 1, rx0:rx1 + 1] = 1
        return mask
    # lens 곡선의 내부/경계 extrema는 꼭짓점만으로 보장할 수 없다.
    if any(camera.distortion):
        return full_roi()
    bounds = np.asarray([(box.lower, box.upper) for box in boxes], dtype=np.float64)
    bits = np.asarray(((0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
                       (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)), dtype=np.bool_)
    corners = np.where(bits[None, :, :], bounds[:, 1, None, :], bounds[:, 0, None, :])
    corners -= np.asarray(camera.translation)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        distance = np.sqrt(np.einsum('nki,nki->nk', corners, corners).max(axis=1))
        rotation = np.asarray(camera.rectification) @ np.asarray(camera.rotation).T
        active = corners @ rotation.T
        translation_radius = math.sqrt(3) * camera.translation_error_m
        angle = min(math.pi, camera.rotation_error_rad + camera.ray_error_rad)
        angular_pad = 2 * math.sin(angle / 2) * (distance + translation_radius)
        translation_pad = camera.translation_error_m * np.abs(rotation).sum(axis=1)
        numeric_pad = 128 * np.finfo(np.float64).eps * (1 + distance + sum(abs(v) for v in camera.translation))
        pad = angular_pad[:, None] + translation_pad + numeric_pad[:, None]
        lower = np.nextafter(active.min(axis=1) - pad, -np.inf)
        upper = np.nextafter(active.max(axis=1) + pad, np.inf)
    if not np.isfinite(lower).all() or not np.isfinite(upper).all():
        return full_roi()
    # 완전히 뒤쪽인 box는 제외한다. Z0/near 접촉은 일부 corner를 버리는 대신 전체 가림이다.
    front = upper[:, 2] >= 0
    if np.any(front & (lower[:, 2] <= max(camera.near_m, 1e-9))):
        return full_roi()
    if not np.any(front):
        return mask
    lower, upper = lower[front], upper[front]
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        ratios = np.stack((lower[:, :2] / lower[:, 2, None], lower[:, :2] / upper[:, 2, None],
                           upper[:, :2] / lower[:, 2, None], upper[:, :2] / upper[:, 2, None]))
        lo, hi = ratios.min(axis=0), ratios.max(axis=0)
        k = camera.k
        skew_lo = np.minimum(k[0][1] * lo[:, 1], k[0][1] * hi[:, 1])
        skew_hi = np.maximum(k[0][1] * lo[:, 1], k[0][1] * hi[:, 1])
        magnitude = 1 + np.abs(lo).max(axis=1) + np.abs(hi).max(axis=1)
        pixel_pad = camera.pixel_error + 128 * np.finfo(np.float64).eps * magnitude * (1 + sum(abs(v) for row in k for v in row))
        u0 = np.nextafter(k[0][0] * lo[:, 0] + skew_lo + k[0][2] - pixel_pad, -np.inf)
        u1 = np.nextafter(k[0][0] * hi[:, 0] + skew_hi + k[0][2] + pixel_pad, np.inf)
        v0 = np.nextafter(k[1][1] * lo[:, 1] + k[1][2] - pixel_pad, -np.inf)
        v1 = np.nextafter(k[1][1] * hi[:, 1] + k[1][2] + pixel_pad, np.inf)
    if not all(np.isfinite(value).all() for value in (u0, u1, v0, v1)):
        return full_roi()
    # 연속 ROI는 u<width/v<height다. 마지막 pixel의 소수 좌표도 정수 사각형에 포함한다.
    u0, u1, v0, v1 = np.floor(u0), np.ceil(u1), np.floor(v0), np.ceil(v1)
    intersects = (u1 >= rx0) & (u0 <= rx1) & (v1 >= ry0) & (v0 <= ry1)
    if not np.any(intersects):
        return mask
    left = np.clip(u0[intersects], rx0, rx1).astype(np.intp)
    right = np.clip(u1[intersects], rx0, rx1).astype(np.intp) + 1
    top = np.clip(v0[intersects], ry0, ry1).astype(np.intp)
    bottom = np.clip(v1[intersects], ry0, ry1).astype(np.intp) + 1
    # rectangle union을 scatter4회+in-place 누적으로 만든다. cap8192라 int32가 충분하다.
    difference = np.zeros((camera.height + 1, camera.width + 1), dtype=np.int32)
    np.add.at(difference, (top, left), 1)
    np.add.at(difference, (bottom, left), -1)
    np.add.at(difference, (top, right), -1)
    np.add.at(difference, (bottom, right), 1)
    np.cumsum(difference, axis=0, dtype=np.int32, out=difference)
    np.cumsum(difference, axis=1, dtype=np.int32, out=difference)
    mask[:] = difference[:-1, :-1] > 0
    return mask
