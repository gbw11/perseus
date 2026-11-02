"""주행 VM 복구 전 Gateway 보호 수명주기(S15P31E110-346, 계약 docs/contracts/gateway-provider-v1.md G1).

주행 VM 을 force-restart 하기 전에 Gateway `purpose=protect` 로 그 실행의 출력을 철회하고 재선택을 막는다. 새 실행이 binding 으로
합류하면 finish, 복구 전에 정당하게 끝났으면 cancel 로 종결한다. 보장 범위는 출력 철회와 재선택 차단이다. 차량 물리 정지를 주장하지 않는다.

- 키와 원 spec 은 첫 송신 전에 복구 기록에 저장한다(엔진). 응답을 잃으면 같은 키·같은 spec 으로 다시 묻는다. 새 키를 만들지 않는다
- 이 모듈은 Gateway I/O 와 응답 판정만 한다. 기록 전이·저장은 엔진이 잠금 안에서 한다
- host 가 바뀐 보호는 정산하지 않고 이력(HISTORICAL)으로 둔다. Gateway 는 현재 host 의 보호만 활성으로 본다(플랫폼 결정)
- Gateway 응답 하나를 성공으로 믿지 않는다: 원 spec 과 같은 대상·실행·의도·기한, durable, 현재 host·대상 일치, held·owner_excluded 를 본다
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

# 보호 기록의 단계. PLANNED·UNKNOWN·PROTECTED 는 Gateway 에 보호가 있을 수 있어 정산이 끝날 때까지 기록을 지우지 않는다
PLANNED = "PLANNED"  # 키·원 spec 저장 완료. 아직 보내지 않았거나 보낸 결과를 모른다
UNKNOWN = "UNKNOWN"  # 보냈지만 결과를 모른다(응답 유실·일시 거절). 같은 키로 다시 묻는다
PROTECTED = "PROTECTED"  # Gateway 가 원 spec 그대로 내구 수락했다
RELEASED = "RELEASED"  # 새 실행 binding 확인 뒤 finish
CANCELLED = "CANCELLED"  # 복구 전 정당 종료로 원 실행에 cancel
ABANDONED = "ABANDONED"  # Gateway 가 만들지 않았음이 확정됐다(거절·기한 경과 뒤 부재)
HISTORICAL = "HISTORICAL"  # 이전 host 부팅의 보호. 정산하지 않는다
LOST = "LOST"  # 수락됐던 보호를 Gateway 가 더는 모른다. 운영자 확인 대상
PHASES = frozenset({PLANNED, UNKNOWN, PROTECTED, RELEASED, CANCELLED, ABANDONED, HISTORICAL, LOST})
LIVE = frozenset({PLANNED, UNKNOWN, PROTECTED})

# submit 거절 가운데 이 키의 보호를 만들지 않았음이 확정인 것. submit 은 같은 키가 있으면 원 view 를 돌려주므로 이 거절은 키가 없다는 뜻이고,
# 같은 키의 앞선 송신은 같은 mailbox 에서 먼저 처리됐거나 버려졌다
NOT_CREATED = frozenset({
    "TARGET_CHANGED", "HOST_CHANGED", "AUTHORITY_CHANGED", "DEADLINE_EXCEEDED", "TARGET_NOT_MANAGED", "TARGET_ALREADY_PROTECTED",
    "HISTORY_FULL", "HISTORY_BYTES_RESERVED", "REQUEST_WINDOW_EXPIRED", "LEGACY_KEY_NOT_ADMISSIBLE", "HISTORY_POLICY_REQUIRED",
    "REQUEST_DEADLINE_OUTSIDE_WINDOW", "REQUEST_TIME_INVALID", "GENERATION_EXHAUSTED", "INVALID_INPUT",
})
ABSENT = frozenset({"NOT_FOUND", "RESULT_NOT_RETAINED_OR_UNKNOWN"})
SPEC_FIELDS = ("target_vm", "target_execution_id", "intent_revision", "lifecycle_generation", "deadline_ms")


class GatewayUncertain(Exception):
    """통신 실패. 송신 전(UNAVAILABLE)이든 뒤(OUTCOME_UNKNOWN)든 수락·취소의 증거가 아니다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GatewayRefused(Exception):
    """Gateway 가 거절 코드를 돌려줬다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProtectionHold(Exception):
    """지금은 보호를 요청할 수 없다(Gateway 문맥이 이 실행과 맞지 않음 등). 복구를 보류한다."""


class GatewayPort(Protocol):
    def context(self) -> dict[str, Any]: ...
    def submit(self, request_id: str, spec: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, request_id: str) -> dict[str, Any]: ...
    def finish(self, request_id: str, context: dict[str, Any]) -> dict[str, Any]: ...
    def cancel(self, request_id: str, context: dict[str, Any]) -> dict[str, Any]: ...
    def new_request_id(self, host_boot_id: str) -> str: ...


class ClientPort:
    """xenova_gateway.client.GatewayClient 를 GatewayPort 로. 클라이언트 예외를 이 모듈의 두 예외로 바꾼다."""

    def __init__(self, client: Any, unavailable: type[Exception], rejected: type[Exception], new_request_id: Callable[[str], str]) -> None:
        self._client, self._unavailable, self._rejected, self._new = client, unavailable, rejected, new_request_id

    @classmethod
    def production(cls, endpoint: str, budget_s: float) -> "ClientPort":
        from xenova_gateway.client import GatewayClient, GatewayRejected, GatewayUnavailable, new_request_id  # xenova-gateway-client RPM

        return cls(GatewayClient(endpoint, budget_s=budget_s), GatewayUnavailable, GatewayRejected, new_request_id)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return getattr(self._client, method)(*args, **kwargs)
        except self._rejected as exc:
            raise GatewayRefused(exc.code) from exc
        except self._unavailable as exc:
            raise GatewayUncertain(exc.code) from exc

    def context(self) -> dict[str, Any]:
        return self._call("context")

    def submit(self, request_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        return self._call("submit", request_id, spec)

    def get(self, request_id: str) -> dict[str, Any]:
        return self._call("get", request_id)

    def finish(self, request_id: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._call("finish", request_id, context)

    def cancel(self, request_id: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._call("cancel", request_id, context=context)

    def new_request_id(self, host_boot_id: str) -> str:
        return self._new(host_boot_id)


@dataclass(frozen=True)
class Outcome:
    """Gateway 를 한 번 다녀온 결과. phase 는 기록에 적을 단계, ready 는 이 실행을 지금 재시작해도 되는 보호인가."""

    phase: str
    note: str
    ready: bool = False


def _canonical(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _revision(value: Any) -> bool:
    return type(value) is int and value >= 0


def _generation(value: Any) -> bool:
    return type(value) is int and 1 <= value <= 0xFFFFFFFF


class GatewayProtection:
    def __init__(self, gateway: GatewayPort, *, deadline_s: float, call_budget_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._gateway = gateway
        self._deadline_ms = int(deadline_s * 1000)
        self._budget_ms = int(call_budget_s * 1000)
        self._clock = clock  # Gateway 와 같은 host 의 단조시계(CLOCK_MONOTONIC). 기한은 이 시계의 ms 다

    # ---- 요청 만들기 ----

    def plan(self, vm_id: UUID, view: dict[str, Any], parent_deadline_ms: int) -> dict[str, Any]:
        """지금 Gateway 문맥과 플랫폼 view 로 원 spec 을 만든다. 키는 여기서 한 번 만든다. 맞지 않으면 ProtectionHold."""
        try:
            context = self._gateway.context()
        except (GatewayUncertain, GatewayRefused) as exc:
            raise ProtectionHold(f"Gateway context unavailable ({exc.code})") from None
        host, owner, generation, targets = (context.get(k) if isinstance(context, dict) else None
                                            for k in ("host_boot_id", "owner", "generation", "targets"))
        if not _canonical(host) or not _canonical(owner) or not _generation(generation) or not isinstance(targets, dict):
            raise ProtectionHold("Gateway context has no current host/owner/generation")
        if context.get("storage_uncertain") is not False:
            raise ProtectionHold("Gateway storage is uncertain")
        if host != view.get("host_boot_id"):
            raise ProtectionHold(f"Gateway host {host} differs from the platform host {view.get('host_boot_id')}")
        target = targets.get(str(vm_id))
        if not isinstance(target, dict):
            # crashed·미합류 실행은 binding 이 없어 Gateway 가 보호를 받지 않는다(TARGET_CHANGED). 보내지 않고 보류한다
            raise ProtectionHold("Gateway has no current binding for this VM; protection cannot be issued")
        bound = (target.get("execution_id"), target.get("intent_revision"), target.get("lifecycle_generation"))
        current = (view.get("execution_id"), view.get("intent_revision"), view.get("generation"))
        if bound != current or not _canonical(bound[0]) or not _revision(bound[1]) or not _revision(bound[2]):
            raise ProtectionHold(f"Gateway binding {bound} is not the platform execution {current}")
        now_ms = int(self._clock() * 1000)
        if parent_deadline_ms <= now_ms:
            raise ProtectionHold("recovery deadline already elapsed")
        spec = {
            "host_boot_id": host, "target_vm": str(vm_id), "target_execution_id": bound[0], "intent_revision": bound[1],
            "lifecycle_generation": bound[2], "expected_owner": owner, "expected_generation": generation,
            "deadline_ms": min(now_ms + self._deadline_ms, parent_deadline_ms), "parent_deadline_ms": parent_deadline_ms, "purpose": "protect",
        }
        return {"request_id": self._gateway.new_request_id(host), "spec": spec, "phase": PLANNED, "note": None}

    # ---- 보호 확보 ----

    def secure(self, entry: dict[str, Any]) -> Outcome:
        """복구 직전: 아직 모르면 같은 키·spec 으로 다시 보내고(멱등), 수락됐으면 같은 키로 현재 상태를 다시 본다."""
        phase = entry["phase"]
        try:
            data = self._gateway.get(entry["request_id"]) if phase == PROTECTED else self._gateway.submit(entry["request_id"], entry["spec"])
        except GatewayUncertain as exc:
            return Outcome(phase if phase == PROTECTED else UNKNOWN, f"Gateway outcome unknown ({exc.code}); asking again with the same key")
        except GatewayRefused as exc:
            if exc.code == "HOST_CHANGED" and phase == PROTECTED:
                return Outcome(HISTORICAL, "protection belongs to an earlier host boot")
            if phase == PROTECTED and exc.code in ABSENT:
                return Outcome(LOST, f"accepted protection no longer known to the Gateway ({exc.code})")
            if phase != PROTECTED and exc.code in NOT_CREATED:
                return Outcome(ABANDONED, f"Gateway refused the protection ({exc.code}); not created")
            return Outcome(phase if phase == PROTECTED else UNKNOWN, f"Gateway refused ({exc.code}); same key kept")
        return self._judge(data, entry)

    # ---- 정산 ----

    def settle(self, entry: dict[str, Any], *, allow_cancel: bool) -> Outcome:
        """이 보호를 더 쓰지 않는다. 새 실행 binding 이 있으면 finish, 원 실행 그대로이고 정당 종료면 cancel. 아니면 기다린다."""
        phase, spec = entry["phase"], entry["spec"]
        try:
            data = self._gateway.get(entry["request_id"])
        except GatewayUncertain as exc:
            return Outcome(phase, f"Gateway unreachable ({exc.code})")
        except GatewayRefused as exc:
            if exc.code == "HOST_CHANGED":
                return Outcome(HISTORICAL, "protection belongs to an earlier host boot")
            if exc.code in ABSENT and phase != PROTECTED:
                # 보낸 결과를 모르던 키: 원 기한(과 호출 한 번)이 지나면 mailbox 에 남은 송신도 DEADLINE_EXCEEDED 로 거절된다
                if self._clock() * 1000 >= spec["deadline_ms"] + self._budget_ms:
                    return Outcome(ABANDONED, f"never accepted ({exc.code} after the original deadline)")
                return Outcome(phase, f"not accepted yet ({exc.code}); original deadline still open")
            if exc.code in ABSENT:
                return Outcome(LOST, f"accepted protection no longer known to the Gateway ({exc.code})")
            return Outcome(phase, f"Gateway refused get ({exc.code})")
        judged = self._judge(data, entry)
        if judged.phase != PROTECTED:
            return judged
        try:
            context = self._gateway.context()
        except (GatewayUncertain, GatewayRefused) as exc:
            return Outcome(PROTECTED, f"Gateway context unavailable ({exc.code})")
        target = (context.get("targets") or {}).get(spec["target_vm"]) if isinstance(context, dict) else None
        if not isinstance(target, dict) or not _canonical(target.get("execution_id")):
            return Outcome(PROTECTED, "waiting for a current binding of the VM")
        if not _generation(context.get("generation")) or context.get("host_boot_id") != spec["host_boot_id"]:
            return Outcome(PROTECTED, "Gateway context has no current generation for this host")
        original = spec["target_execution_id"]
        if target["execution_id"] != original:
            call, execution = self._gateway.finish, target["execution_id"]
        elif allow_cancel:
            call, execution = self._gateway.cancel, original
        else:
            return Outcome(PROTECTED, "waiting for a new execution binding")
        end = {"host_boot_id": spec["host_boot_id"], "expected_generation": context["generation"], "target_execution_id": execution}
        try:
            data = call(entry["request_id"], end)
        except GatewayUncertain as exc:
            return Outcome(PROTECTED, f"{call.__name__} outcome unknown ({exc.code}); rechecked with get")
        except GatewayRefused as exc:
            if exc.code == "HOST_CHANGED":
                return Outcome(HISTORICAL, "protection belongs to an earlier host boot")
            return Outcome(PROTECTED, f"{call.__name__} refused ({exc.code}); rechecked later")  # AUTHORITY_CHANGED 등: 다시 관측한다
        return self._judge(data, entry)

    # ---- 응답 판정 ----

    def _judge(self, data: Any, entry: dict[str, Any]) -> Outcome:
        keep = entry["phase"] if entry["phase"] == PROTECTED else UNKNOWN
        spec = entry["spec"]
        try:
            same = (data["request_id"] == entry["request_id"] and data["purpose"] == "protect" and data["acceptance"] == "durable"
                    and data["host_boot_id"] == spec["host_boot_id"] and all(data[k] == spec[k] for k in SPEC_FIELDS))
            phase, guard, uncertain = data["phase"], data["protection"], data["storage_uncertain"]
            current_host, current_target = guard["current_host_matches"], guard["current_target_matches"]
            held, excluded = guard["held"], guard["owner_excluded"]
        except (KeyError, TypeError):
            return Outcome(keep, "Gateway view malformed")
        if not same:
            return Outcome(keep, "Gateway view does not match the original protection request")
        if uncertain is not False:
            return Outcome(keep, "Gateway storage uncertain")
        if phase in (RELEASED, CANCELLED):
            return Outcome(phase, f"protection {phase.lower()}")
        if phase != PROTECTED:
            return Outcome(keep, f"unexpected protection phase {phase!r}")
        if current_host is not True:
            return Outcome(HISTORICAL, "protection belongs to an earlier host boot")
        if held is not True or excluded is not True:
            return Outcome(PROTECTED, "protection accepted but the VM is not held/excluded")
        if current_target is not True:
            return Outcome(PROTECTED, "protection accepted but the protected execution is no longer the current binding")
        return Outcome(PROTECTED, "protected", ready=True)
