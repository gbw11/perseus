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
        self._last_received: float | None = None  # 어느 VM 이든 이 채널에서 마지막으로 받은 시각. 관측 경로가 실제로 전달한다는 증거

    def put(self, beat: T) -> T:
        with self._lock:
            self._last[beat.vm_id] = beat
            if self._last_received is None or beat.received_monotonic > self._last_received:
                self._last_received = beat.received_monotonic
        return beat

    def last_received_monotonic(self) -> float | None:
        """이 채널(토픽 필터)에서 마지막으로 유효한 메시지를 받은 단조 시각. VM 과 무관하다."""
        with self._lock:
            return self._last_received

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

    def record(self, vm_id: UUID, app_id: str, state: str, boot_id: str, seq: int, received_monotonic: float,instance_id: str | None=None) -> AppHeartbeat:
        return self.put(AppHeartbeat(vm_id=vm_id, app_id=app_id, state=state, boot_id=boot_id, seq=seq, received_monotonic=received_monotonic,instance_id=instance_id))

    def record_lifecycle(self,vm_id,app_id,boot,instance,value,received):
        """Keep per-app supervision separate from actual computation heartbeat."""
        from types import SimpleNamespace
        with self._lock:
            if not hasattr(self,'_supervision'):self._supervision={}
            key=vm_id,app_id;old=self._supervision.get(key)
            same=bool(old and old.boot_id==boot and old.instance_id==instance)
            if same and (value.check<=old.check or value.progress<old.progress):return False
            work_at=received if value.progress and (not same or value.progress>old.progress) else old.work_at if same else None
            self._supervision[key]=SimpleNamespace(vm_id=vm_id,app_id=app_id,boot_id=boot,instance_id=instance,
                check=value.check,progress=value.progress,phase=value.phase,readiness=value.readiness,
                reason=value.reason,received_monotonic=received,work_at=work_at)
            self._last_received=max(received,self._last_received or received)
        return True

    def supervision_pair(self,vm_id,boot,instances):
        """Require both current app identities. One app never covers another."""
        with self._lock:
            values=[getattr(self,'_supervision',{}).get((vm_id,app)) for app in ('autonomous','manual')]
            if any(value is None or value.boot_id!=boot or instances.get(value.app_id)!=value.instance_id for value in values):return None
            return tuple(values)

    def forget(self,vm_id):
        super().forget(vm_id)
        with self._lock:
            for key in list(getattr(self,'_supervision',{})):
                if key[0]==vm_id:del self._supervision[key]
