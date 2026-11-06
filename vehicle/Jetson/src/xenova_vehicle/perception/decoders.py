"""소유 RGB/depth 원본의 배열 표현을 만든다. 기하·빈공간·주행 권한은 판단하지 않는다.

NumPy/OpenCV는 decode 호출에서만 불러온다. readonly는 호출자 간 공유를 위한 API
계약이며 임의 호출자의 메모리 변경을 막는 보안 경계가 아니다.
"""

from dataclasses import dataclass, field
from typing import Any

from ..oak.frames import RawFrame


@dataclass(frozen=True)
class DecodedRgb:
    """원 source/time을 가진 frame과 새 소유 BGR uint8 HWC 배열. 배열은 C-packed/readonly다."""

    frame: RawFrame
    bgr: Any


@dataclass(frozen=True)
class DecodedDepth:
    """원 bytes를 유지하는 native axial depth view와 단순 D!=0 표식.

    has_measurement는 물리적 유효거리나 free 판정이 아니다. 0은 invalid/unknown이며
    RGB와의 정렬·metric 기하·depth 시간 보정은 이 decoder의 책임이 아니다.
    """

    frame: RawFrame
    depth_mm: Any
    has_measurement: Any
    depth_unit: str = field(default="millimeter", init=False)
    alignment: str = field(default="RECTIFIED_RIGHT", init=False)
    rgb_aligned: bool = field(default=False, init=False)
    metric_ready: bool = field(default=False, init=False)


def _validate(frame, stream, encoding):
    if not isinstance(frame, RawFrame):
        raise ValueError("decoder requires a RawFrame")
    RawFrame.validate(frame)
    if frame.stream != stream or frame.encoding != encoding:
        raise ValueError("decoder stream/encoding mismatch")


def decode_rgb(frame: RawFrame) -> DecodedRgb:
    """packed NV12를 BGR로 한 번 변환한다. even 크기와 payload는 RawFrame 계약을 따른다.

    원 NV12 view는 bytes를 빌리고 결과 BGR만 새로 소유한다. 여러 후속 소비자는 반환
    객체를 공유해야 하며 이 함수의 반복 호출을 자동으로 합치거나 캐시하지 않는다.
    """
    _validate(frame, "rgb_video", "NV12")
    import numpy as np
    import cv2

    packed = np.frombuffer(frame.payload, dtype=np.uint8).reshape(frame.height * 3 // 2, frame.width)
    if packed.strides != (frame.width, 1):
        raise ValueError("NV12 input must have packed rows")
    bgr = np.empty((frame.height, frame.width, 3), dtype=np.uint8, order="C")
    converted = cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_NV12, dst=bgr)
    if (not isinstance(converted, np.ndarray) or converted.shape != bgr.shape
            or converted.dtype != np.uint8 or not np.shares_memory(converted, bgr)):
        raise ValueError("OpenCV did not populate the owned BGR destination")
    bgr.setflags(write=False)
    return DecodedRgb(frame, bgr)


def decode_depth(frame: RawFrame) -> DecodedDepth:
    """RAW16_LE를 복사 없이 HxW '<u2' view로 해석한다. 단위 mm, native RIGHT 좌표다.

    frame과 ndarray의 bytes 기반 참조가 원 payload 수명을 유지한다. 별도 생성하는
    has_measurement 배열은 D!=0만 나타내며 관측 신뢰도·점유·빈공간을 승인하지 않는다.
    """
    _validate(frame, "depth_right", "RAW16_LE")
    import numpy as np

    depth = np.frombuffer(frame.payload, dtype="<u2").reshape(frame.height, frame.width)
    if depth.strides != (frame.width * 2, 2):
        raise ValueError("native depth input must have packed rows")
    has_measurement = depth != 0
    depth.setflags(write=False)
    has_measurement.setflags(write=False)
    return DecodedDepth(frame, depth, has_measurement)
