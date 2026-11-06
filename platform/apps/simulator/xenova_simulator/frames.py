"""가상 ESP32 의 시리얼 입력: 게이트웨이가 ESP32 로 보내는 프레임을 TCP 로 받는다 (실제 ESP32 자리).

프레임 형식·코덱은 게이트웨이의 `xenova_gateway.frame` 을 그대로 쓴다 (COBS + CRC-16, 0x00 구분).
ESP32 쪽 규칙 (게이트웨이 README "ESP32 시리얼 프레임"):
- CRC·길이가 틀린 프레임은 버리고 다음 0x00 부터 다시 맞춘다.
- 프레임의 generation 이 바뀌면 counter 기준을 새로 잡는다. 더 낮은 generation 은 지난 세대라 버린다.
- 같은 generation 에서 counter 가 같거나 뒤로 간(8비트 순환으로 128 이상 차이) 프레임은 중복·역전이라 버린다.
- 유효 프레임이 300 ms 없으면 정지한다 (와치독은 Simulator 가 시각으로 판정).

게이트웨이는 `serial.serial_for_url` 로 열므로 `--serial-port socket://<PC>:7600` 으로 바로 붙는다
(S15P31E110-269, 게이트웨이 README). socat 가상 시리얼(`socat pty,link=/tmp/ttyXENOVA,raw,echo=0 tcp:<PC>:7600`)은 보조 방법.
게이트웨이의 write-only(legacy DRIVE·STOP) 프레임만 흉내 낸다. feedback HELLO·COMMAND 프레임은 버린다.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from xenova_gateway.frame import MAX_FRAME_BYTES, DriveFrame, FrameError, StopFrame, decode

log = logging.getLogger("xenova_simulator")

# 0x00 이 한참 안 오면 잡음이라 버린다 (정상 프레임은 16 바이트 이하)
_MAX_PENDING = MAX_FRAME_BYTES * 4
# 게이트웨이는 명령·정지 프레임을 20 ms 마다 보낸다. 이만큼 아무것도 안 오면 반쯤 열린 연결로 보고 닫는다
IDLE_DISCONNECT_S = 2.0


@dataclass(frozen=True)
class FrameCommand:
    """마지막으로 받아들인 프레임. stop 이면 v·steer 는 쓰지 않는다."""

    generation: int
    counter: int
    stop: bool
    v_mps: float
    steer_rad: float
    received: float


class FrameReceiver:
    """바이트 → 프레임 → ESP32 수용 규칙. 스레드 안전 (수신 스레드가 feed, 물리 스레드가 latest)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._generation: int | None = None
        self._counter = 0
        self._latest: FrameCommand | None = None
        self.accepted = 0
        self.bad_frames = 0
        self.duplicates = 0
        self.stale_generation = 0

    def latest(self) -> FrameCommand | None:
        with self._lock:
            return self._latest

    def reset_session(self) -> None:
        """연결이 새로 맺어지면 바이트 버퍼만 비운다 (세대·counter 기준은 ESP32 처럼 유지)."""
        with self._lock:
            self._pending.clear()

    def feed(self, data: bytes, now: float) -> int:
        """받은 바이트를 넣는다. 받아들인 프레임 수를 돌려준다."""
        accepted = 0
        with self._lock:
            self._pending += data
            while True:
                end = self._pending.find(0)
                if end < 0:
                    if len(self._pending) > _MAX_PENDING:
                        self.bad_frames += 1
                        self._pending.clear()
                    break
                chunk = bytes(self._pending[:end])
                del self._pending[:end + 1]
                if not chunk:
                    continue
                try:
                    frame = decode(chunk)
                except FrameError as exc:
                    self.bad_frames += 1
                    log.debug("프레임 버림: %s", exc)
                    continue
                if self._accept(frame, now):
                    accepted += 1
        return accepted

    def _accept(self, frame: DriveFrame | StopFrame, now: float) -> bool:
        if self._generation is not None and frame.generation < self._generation:
            self.stale_generation += 1
            return False
        if self._generation is not None and frame.generation == self._generation:
            step = (frame.counter - self._counter) & 0xFF
            if step == 0 or step >= 128:
                self.duplicates += 1
                return False
        elif self._generation != frame.generation:
            log.info("시리얼 프레임 세대 %s → %d (counter 기준 %d)", self._generation, frame.generation, frame.counter)
        self._generation, self._counter = frame.generation, frame.counter
        if isinstance(frame, DriveFrame):
            self._latest = FrameCommand(frame.generation, frame.counter, False, frame.v_mps, frame.steer_rad, now)
        else:
            self._latest = FrameCommand(frame.generation, frame.counter, True, 0.0, 0.0, now)
        self.accepted += 1
        return True


class FrameServer:
    """TCP 로 프레임 바이트를 받는다. 게이트웨이 연결은 한 번에 하나만 읽고, 끊기면 다음 연결을 받는다."""

    def __init__(
        self,
        receiver: FrameReceiver,
        host: str,
        port: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_s: float = IDLE_DISCONNECT_S,
    ) -> None:
        self._receiver = receiver
        self._clock = clock
        self._idle_s = idle_s
        self._sock = socket.create_server((host, port))
        self._sock.settimeout(0.2)
        self.address = self._sock.getsockname()[:2]
        self._conn: socket.socket | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="frame-server", daemon=True)
        self.connections = 0

    def start(self) -> None:
        self._thread.start()
        log.info("시리얼 프레임 TCP 대기 %s:%d", *self.address)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, peer = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            log.info("게이트웨이 시리얼 연결 %s:%d", *peer[:2])
            self._receiver.reset_session()
            conn.settimeout(0.2)
            with conn:
                self._read(conn)
            log.warning("게이트웨이 시리얼 연결 끊김 (300 ms 뒤 와치독 정지)")

    def _read(self, conn: socket.socket) -> None:
        last = time.monotonic()
        while not self._stop.is_set():
            try:
                data = conn.recv(4096)
            except TimeoutError:
                # FIN 없이 사라진 연결(dom0 재부팅·네트워크 단절)에 묶이면 게이트웨이가 다시 붙어도
                # 읽지 못한다. 게이트웨이는 늘 20 ms 마다 프레임을 내므로 오래 조용하면 끊고 accept 로 돌아간다
                if time.monotonic() - last > self._idle_s:
                    log.warning("게이트웨이 시리얼 연결이 %.1f s 동안 조용해 닫음", self._idle_s)
                    return
                continue
            except OSError:
                return
            if not data:
                return
            last = time.monotonic()
            self._receiver.feed(data, self._clock())
