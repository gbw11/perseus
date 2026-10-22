"""VM 별 마지막 생존 신호 표. paho 네트워크 스레드가 쓰고 감시 루프가 읽는다."""

from __future__ import annotations

import threading
from typing import Generic, TypeVar
from uuid import UUID

from .model import AppHeartbeat, Heartbeat

T = TypeVar("T", Heartbeat, AppHeartbeat)


class LastSeenTable(Generic[T]):
    """seq 역전·중복은 공용 클라이언트(SeqTracker)가 이미 걸렀다. 여기서는 VM 마다 마지막 것만 둔다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[UUID, T] = {}

    def put(self, beat: T) -> T:
        with self._lock:
            self._last[beat.vm_id] = beat
        return beat

    def last(self, vm_id: UUID) -> T | None:
        with self._lock:
            return self._last.get(vm_id)

    def age_s(self, vm_id: UUID, now: float) -> float | None:
        beat = self.last(vm_id)
        return None if beat is None else max(0.0, now - beat.received_monotonic)

    def forget(self, vm_id: UUID) -> None:
        with self._lock:
            self._last.pop(vm_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._last)


class HeartbeatTable(LastSeenTable[Heartbeat]):
    """agent.heartbeat (Linux VM)."""

    def record(self, vm_id: UUID, boot_id: str, seq: int, received_monotonic: float) -> Heartbeat:
        return self.put(Heartbeat(vm_id=vm_id, boot_id=boot_id, seq=seq, received_monotonic=received_monotonic))


class AppHeartbeatTable(LastSeenTable[AppHeartbeat]):
    """app.heartbeat (Zephyr 주행 VM). 봉투의 vm_id 로 묶으므로 app_id↔vm_id 대응표가 따로 필요 없다."""

    def record(self, vm_id: UUID, app_id: str, state: str, boot_id: str, seq: int, received_monotonic: float) -> AppHeartbeat:
        return self.put(AppHeartbeat(vm_id=vm_id, app_id=app_id, state=state, boot_id=boot_id, seq=seq, received_monotonic=received_monotonic))
