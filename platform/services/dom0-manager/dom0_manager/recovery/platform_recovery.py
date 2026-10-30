"""플랫폼 복구 기반(xenova-recovery-foundation) 호출. recover_once · recovery-operation · get.

recover_once(execute=True) 는 플랫폼 잠금 안에서 정지 대기·시작·관측까지 deadline 까지 막힐 수 있다. 감시 루프에서 직접 부르지
않고 엔진의 실행 스레드에서 부른다. 오류는 drivers.platform.raise_for_envelope 가 계약 예외(platform_code 보존)로 바꾼다.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from ..drivers.platform import ManagerLike, raise_for_envelope


class RecoveryPlatform(Protocol):
    def view(self, vm_id: UUID) -> dict[str, Any]: ...

    def recover_once(self, spec: dict[str, Any], *, execute: bool) -> dict[str, Any]: ...

    def attempt(self, recovery_attempt_id: UUID) -> dict[str, Any]: ...


class PlatformRecovery:
    def __init__(self, manager: ManagerLike) -> None:
        self._manager = manager

    def view(self, vm_id: UUID) -> dict[str, Any]:
        """플랫폼 get: 의도·revision·host_boot_id·execution_id·observation_revision 이 든 view."""
        data = raise_for_envelope(self._manager.call("get", {"vm_id": str(vm_id)}), context=f"recovery get {vm_id}")
        return data["vm"]

    def recover_once(self, spec: dict[str, Any], *, execute: bool) -> dict[str, Any]:
        data = raise_for_envelope(
            self._manager.call("recover_once", spec, execute=execute), context=f"recover_once {spec.get('vm_id')} {spec.get('mode')}",
        )
        return data

    def attempt(self, recovery_attempt_id: UUID) -> dict[str, Any]:
        """recovery-operation: 플랫폼 journal 의 시도 기록(phase accepted/stopping/removed/launching/observed/uncertain/completed/failed)."""
        data = raise_for_envelope(
            self._manager.call("recovery-operation", {"recovery_attempt_id": str(recovery_attempt_id)}),
            context=f"recovery-operation {recovery_attempt_id}",
        )
        return data["attempt"]
