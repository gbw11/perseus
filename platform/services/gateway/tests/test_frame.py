"""프레임 코덱 초안 시험 (codec)."""

from __future__ import annotations

import pytest

from xenova_gateway.frame import (
    MAX_FRAME_BYTES,
    DriveFrame,
    FrameEncoder,
    FrameError,
    StopFrame,
    cobs_decode,
    cobs_encode,
    crc16,
    decode,
    encode,
)


def test_codec_crc_vector():
    assert crc16(b"123456789") == 0x29B1


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x00", b"\x11\x22\x00\x33", bytes(range(1, 255)), bytes(300)])
def test_codec_cobs_round_trip(data):
    encoded = cobs_encode(data)
    assert 0 not in encoded
    assert cobs_decode(encoded) == data


@pytest.mark.parametrize("frame", [
    DriveFrame(1, 1, 0.5, -0.2),
    DriveFrame(0xFFFFFFFF, 255, -1.25, 0.4),
    DriveFrame(256, 0, 0.0, 0.0),      # 0x00 바이트가 많은 경우
    StopFrame(7, 3, 1),
    StopFrame(0, 0, 2),
])
def test_codec_round_trip(frame):
    data = encode(frame)
    assert data.endswith(b"\x00") and 0 not in data[:-1]
    assert len(data) <= MAX_FRAME_BYTES
    assert decode(data) == frame


def test_codec_scales_and_clamps():
    assert decode(encode(DriveFrame(1, 1, 0.12345, 0.0))).v_mps == pytest.approx(0.123)
    assert decode(encode(DriveFrame(1, 1, 100.0, -100.0))) == DriveFrame(1, 1, 32.767, -32.768)
    with pytest.raises(ValueError):
        encode(DriveFrame(1, 1, float("inf"), 0.0))


def test_codec_drive_layout():
    # 본문 = id 0x0100 LE, len 9, gen u32, counter, v mm/s, steer mrad, crc
    frame = DriveFrame(0x01020304, 5, 0.5, -0.25)
    body = cobs_decode(encode(frame)[:-1])
    assert body[:3] == b"\x00\x01\x09"
    assert body[3:12] == bytes.fromhex("04030201") + b"\x05" + (500).to_bytes(2, "little") + (-250).to_bytes(2, "little", signed=True)
    assert int.from_bytes(body[12:], "little") == crc16(body[:12])


def test_codec_rejects_corruption():
    data = bytearray(encode(DriveFrame(3, 9, 0.5, 0.1)))
    data[5] ^= 0x01
    with pytest.raises(FrameError):
        decode(bytes(data))
    with pytest.raises(FrameError):
        decode(b"\x00")
    with pytest.raises(FrameError):
        decode(b"\x05\x01\x02")


def test_codec_counter_shared_and_wraps():
    enc = FrameEncoder()
    counters = [enc.drive(1, 0, 0).counter if i % 2 else enc.stop(1, 1).counter for i in range(300)]
    assert counters[:3] == [1, 2, 3]
    assert counters[254] == 255 and counters[255] == 0
