"""ESP32 출력. 실제는 USB 시리얼(pyserial), 보드가 없을 때는 목 싱크가 프레임을 기록한다.

시리얼 포트 자리에는 pyserial URL 도 줄 수 있다. ``socket://<host>:<port>`` 로 주면 같은 바이트를
TCP 로 보낸다 (PC 시뮬레이터의 프레임 수신 포트, 예: ``socket://192.168.0.10:7600``).
"""

from __future__ import annotations

import logging
import os
import sys
from collections import deque
from typing import IO, Protocol

from .frame import DriveFrame, Frame, encode

log = logging.getLogger("xenova_gateway")


class SinkError(RuntimeError):
    pass


class Sink(Protocol):
    def write(self, frame: Frame, t: float) -> int: ...

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

    def write(self, frame: Frame, t: float) -> int:
        data = encode(frame)
        self.frames.append((t, frame, data))
        if self._stream is not None:
            self._stream.write(f"{t:.6f} {describe(frame)} hex={data.hex()}\n")
            self._stream.flush()
        return len(data)

    def close(self) -> None:
        if self._stream is not None and self._stream not in (sys.stdout, sys.stderr):
            self._stream.close()


class SerialSink:
    """USB 시리얼로 프레임을 보낸다. 쓰기가 막히면 기다리지 않고 실패로 센다 (최종 정지는 ESP32 와치독).

    port 는 장치 경로(/dev/ttyUSB0) 또는 pyserial URL(socket://host:port) 이다. 둘 다
    serial_for_url 로 열어 같은 non-blocking 쓰기·SinkError 흐름을 탄다 (socket:// 은 baudrate 무시).
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SinkError("시리얼 출력에는 pyserial 이 필요하다 (apt install python3-serial 또는 pip install pyserial)") from exc
        try:
            # One nonblocking kernel submission. A partial/EAGAIN result is
            # uncertain and fails below; no wait/retry can extend its command.
            # pyserial's timed path may recheck the deadline after a full write
            # if this Python thread was descheduled before its select call.
            self._serial = serial.serial_for_url(port, baudrate=baudrate, timeout=0, write_timeout=0.005)
            self._write_fd=None
            if os.name=='posix':
                try:fd=self._serial.fileno()
                except (AttributeError,NotImplementedError):pass
                else:
                    os.set_blocking(fd,False);self._write_fd=fd
        except Exception as exc:  # serial.SerialException
            opened=getattr(self,'_serial',None)
            if opened is not None:
                try:opened.close()
                except Exception as cleanup:exc.add_note('serial initialization cleanup: '+repr(cleanup))
            raise SinkError(f"시리얼 포트 {port} 열기 실패: {exc}") from exc
        self.failures = 0
        self.first_failure = None

    def write(self, frame: Frame, t: float) -> int:
        try:
            data=encode(frame);accepted=self._write_bytes(data)
            if accepted!=len(data):raise SinkError('partial serial write')
            return accepted
        except Exception as exc:  # serial.SerialTimeoutException 등
            self.failures += 1
            if self.first_failure is None:
                self.first_failure={'cause_type':type(exc).__name__,'errno':getattr(exc,'errno',None)}
            # caller가 즉시 출력 철회/STOP을 수행해야 한다. handler I/O를 먼저 기다리지 않는다.
            raise SinkError('serial submission failed; device application unknown') from exc

    def _write_bytes(self,data):
        # A single POSIX write, including EAGAIN/partial outcomes. pyserial's
        # write_timeout=0 implementation retries EAGAIN, so it is not a bounded
        # one-shot submission. URL backends without an fd retain 5 ms timeout.
        return os.write(self._write_fd,data) if self._write_fd is not None else self._serial.write(data)

    def close(self) -> None:
        self._serial.close()
