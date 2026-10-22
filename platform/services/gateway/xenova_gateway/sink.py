"""ESP32 출력. 실제는 USB 시리얼(pyserial), 보드가 없을 때는 목 싱크가 프레임을 기록한다."""

from __future__ import annotations

import logging
import sys
from collections import deque
from typing import IO, Protocol

from .frame import DriveFrame, Frame, encode

log = logging.getLogger("xenova_gateway")


class SinkError(RuntimeError):
    pass


class Sink(Protocol):
    def write(self, frame: Frame, t: float) -> None: ...

    def close(self) -> None: ...


def describe(frame: Frame) -> str:
    if isinstance(frame, DriveFrame):
        return f"DRIVE gen={frame.generation} counter={frame.counter} v_mps={frame.v_mps:.3f} steer_rad={frame.steer_rad:.3f}"
    return f"STOP gen={frame.generation} counter={frame.counter} reason={frame.reason}"


class MockSink:
    """프레임을 메모리에 남기고, stream 을 주면 한 줄씩 쓴다 (단조 시각, 내용, 인코딩 hex)."""

    def __init__(self, stream: IO[str] | None = None, *, keep: int = 10000) -> None:
        self.frames: deque[tuple[float, Frame, bytes]] = deque(maxlen=keep)
        self._stream = stream

    def write(self, frame: Frame, t: float) -> None:
        data = encode(frame)
        self.frames.append((t, frame, data))
        if self._stream is not None:
            self._stream.write(f"{t:.6f} {describe(frame)} hex={data.hex()}\n")
            self._stream.flush()

    def close(self) -> None:
        if self._stream is not None and self._stream not in (sys.stdout, sys.stderr):
            self._stream.close()


class SerialSink:
    """USB 시리얼로 프레임을 보낸다. 쓰기가 막히면 기다리지 않고 실패로 센다 (최종 정지는 ESP32 와치독)."""

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SinkError("시리얼 출력에는 pyserial 이 필요하다 (apt install python3-serial 또는 pip install pyserial)") from exc
        try:
            self._serial = serial.Serial(port, baudrate=baudrate, timeout=0, write_timeout=0.005)
        except Exception as exc:  # serial.SerialException
            raise SinkError(f"시리얼 포트 {port} 열기 실패: {exc}") from exc
        self.failures = 0

    def write(self, frame: Frame, t: float) -> None:
        try:
            self._serial.write(encode(frame))
        except Exception as exc:  # serial.SerialTimeoutException 등
            self.failures += 1
            log.error("시리얼 쓰기 실패 (%d회): %s", self.failures, exc)

    def close(self) -> None:
        self._serial.close()
