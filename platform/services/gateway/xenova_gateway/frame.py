"""게이트웨이 → ESP32 시리얼 프레임 코덱 (초안, S15P31E110-241 에서 계약 v1.1 로 옮긴다).

옛 차량 제어 명세(docs/design/vehicle-control-spec.md 5.2)의 USB 시리얼 형식을 따른다.

    본문   = [id:u16 LE][len:u8][payload][crc:u16 LE]
    프레임 = COBS(본문) + 0x00

- CRC 는 CRC-16/CCITT-FALSE (다항식 0x1021, 초기값 0xFFFF, 반전 없음). 범위는 id·len·payload.
- 세션 핸드셰이크(hello/ack)·세션 태그는 아직 넣지 않는다. 세대(generation)가 그 역할의 일부를 맡는다.
- counter 는 모든 프레임(drive·stop)이 함께 쓰는 8비트 순환 값이다.
"""

from __future__ import annotations

from binascii import crc_hqx
import math
import struct
from dataclasses import dataclass

DRIVE_ID = 0x0100
STOP_ID = 0x0010

# drive: generation u32, counter u8, 속도 i16 mm/s, 조향 i16 mrad
_DRIVE = struct.Struct("<IBhh")
# stop: generation u32, counter u8, reason u8
_STOP = struct.Struct("<IBB")
_HEADER = struct.Struct("<HB")
_CRC = struct.Struct("<H")

# stop 프레임 reason
STOP_SAFE_STOP = 1   # 활성 앱 없음 (SAFE_STOP)
STOP_SHUTDOWN = 2    # 게이트웨이 종료

MAX_FRAME_BYTES = 16
_I16_MIN, _I16_MAX = -32768, 32767


class FrameError(ValueError):
    """프레임이 깨졌거나 형식이 다르다. 받는 쪽은 버리고 다음 0x00 까지 다시 맞춘다."""


@dataclass(frozen=True)
class DriveFrame:
    generation: int
    counter: int
    v_mps: float
    steer_rad: float


@dataclass(frozen=True)
class StopFrame:
    generation: int
    counter: int
    reason: int


Frame = DriveFrame | StopFrame


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE. 검증 벡터 b"123456789" → 0x29B1."""
    return crc_hqx(data, 0xFFFF)


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    block = bytearray()
    for byte in data:
        if byte == 0:
            out.append(len(block) + 1)
            out += block
            block.clear()
        else:
            block.append(byte)
            if len(block) == 254:
                out.append(255)
                out += block
                block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        end = index + code
        if code == 0 or end > len(data):
            raise FrameError("COBS 길이 오류")
        block = data[index + 1:end]
        if 0 in block:
            raise FrameError("COBS 블록 안에 0x00")
        out += block
        index = end
        if code < 255 and index < len(data):
            out.append(0)
    return bytes(out)


def _scaled(value: float, scale: float) -> int:
    if not math.isfinite(value):
        raise ValueError(f"유한한 값이 아니다: {value!r}")
    # Saturate before scaling: a finite extreme value can overflow when
    # multiplied, before the old integer clamp ever runs.
    return round(max(_I16_MIN / scale, min(_I16_MAX / scale, value)) * scale)


def encode(frame: Frame) -> bytes:
    """프레임을 COBS 인코딩하고 끝에 0x00 을 붙인다. 속도·조향은 i16 범위로 자른다."""
    if not 0 <= frame.generation <= 0xFFFFFFFF:
        raise ValueError(f"generation 범위 밖: {frame.generation}")
    counter = frame.counter & 0xFF
    if isinstance(frame, DriveFrame):
        frame_id = DRIVE_ID
        payload = _DRIVE.pack(frame.generation, counter,
                              _scaled(frame.v_mps, 1000.0), _scaled(frame.steer_rad, 1000.0))
    else:
        frame_id = STOP_ID
        payload = _STOP.pack(frame.generation, counter, frame.reason & 0xFF)
    body = _HEADER.pack(frame_id, len(payload)) + payload
    body += _CRC.pack(crc16(body))
    return cobs_encode(body) + b"\x00"


def decode(data: bytes) -> Frame:
    """encode 의 역. 끝의 0x00 은 있어도 없어도 된다. ESP32 펌웨어 구현의 기준값으로 쓴다."""
    if data.endswith(b"\x00"):
        data = data[:-1]
    if not data or 0 in data:
        raise FrameError("빈 프레임이거나 중간에 0x00")
    body = cobs_decode(data)
    if len(body) < _HEADER.size + _CRC.size:
        raise FrameError(f"너무 짧다: {len(body)} bytes")
    frame_id, length = _HEADER.unpack_from(body)
    if len(body) != _HEADER.size + length + _CRC.size:
        raise FrameError(f"len {length} 과 본문 길이 {len(body)} 불일치")
    (crc,) = _CRC.unpack_from(body, len(body) - _CRC.size)
    if crc != crc16(body[:-_CRC.size]):
        raise FrameError("CRC 불일치")
    payload = body[_HEADER.size:-_CRC.size]
    if frame_id == DRIVE_ID and length == _DRIVE.size:
        generation, counter, v_mm, steer_mrad = _DRIVE.unpack(payload)
        return DriveFrame(generation, counter, v_mm / 1000.0, steer_mrad / 1000.0)
    if frame_id == STOP_ID and length == _STOP.size:
        return StopFrame(*_STOP.unpack(payload))
    raise FrameError(f"모르는 id 0x{frame_id:04X} 또는 길이 {length}")


class FrameEncoder:
    """counter 를 붙여 프레임을 만든다. drive·stop 이 같은 counter 를 쓴다."""

    def __init__(self) -> None:
        self._counter = 0

    def _next(self) -> int:
        self._counter = (self._counter + 1) & 0xFF
        return self._counter

    def drive(self, generation: int, v_mps: float, steer_rad: float) -> DriveFrame:
        return DriveFrame(generation, self._next(), v_mps, steer_rad)

    def stop(self, generation: int, reason: int) -> StopFrame:
        return StopFrame(generation, self._next(), reason)
