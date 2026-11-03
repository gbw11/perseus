"""Gateway provider(platform/services/gateway/xenova_gateway/provider.py)의 보호 규칙만 흉내 내는 GatewayPort.

같은 키 멱등, 대상 binding·host·owner/generation·기한 확인, VM 당 활성 보호 하나, finish 는 새 실행·cancel 은 원 실행, 종결 뒤 같은 문맥 재호출은
원 결과다. faults 로 호출 전 실패(효과 없음)나 효과 뒤 응답 유실을 넣는다.
"""

from __future__ import annotations

from typing import Any

from dom0_manager.recovery.protection import GatewayRefused, GatewayUncertain

OWNER = "9b2a4c3e-1d5f-4e6a-8b7c-0d1e2f3a4b5c"


class FakeGateway:
    def __init__(self, host: str, clock, targets: dict[str, dict[str, Any]] | None = None) -> None:
        self.host = host
        self.clock = clock  # 엔진과 같은 단조시계(초)
        self.owner = OWNER
        self.generation = 7
        self.storage_uncertain = False
        self.targets = targets or {}  # vm → {execution_id, intent_revision, lifecycle_generation} (현재 binding)
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, Any]] = []
        self.faults: dict[str, list[tuple[str, Exception]]] = {}  # method → [("before"|"after", 예외)]
        self.issued = 0

    def bind(self, vm: str, execution_id: str | None, *, revision: int = 3, generation: int = 0) -> None:
        if execution_id is None:
            self.targets.pop(vm, None)
        else:
            self.targets[vm] = {"execution_id": execution_id, "boot_id": "6a3b1c2d-0000-4000-8000-000000000001",
                                "intent_revision": revision, "lifecycle_generation": generation}

    def fail(self, method: str, exc: Exception, when: str = "before") -> None:
        self.faults.setdefault(method, []).append((when, exc))

    def _fault(self, method: str, when: str) -> None:
        queue = self.faults.get(method) or []
        if queue and queue[0][0] == when:
            raise queue.pop(0)[1]

    def _call(self, method: str, work):
        self._fault(method, "before")
        result = work()
        self._fault(method, "after")  # 효과는 났지만 응답을 잃었다
        return result

    # ---- GatewayPort ----

    def new_request_id(self, host_boot_id: str) -> str:
        self.issued += 1
        return f"g1:{host_boot_id}:{int(self.clock() * 1000)}:00000000-0000-4000-8000-{self.issued:012d}"

    def context(self) -> dict[str, Any]:
        self.calls.append(("context", None))
        return self._call("context", lambda: {
            "host_boot_id": self.host, "owner": self.owner, "generation": self.generation, "mode": "AUTO", "phase": "APPLIED",
            "output_permitted": True, "storage_uncertain": self.storage_uncertain, "targets": {k: dict(v) for k, v in self.targets.items()}})

    def submit(self, request_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("submit", request_id))

        def work():
            row = self.rows.get(request_id)
            if row is not None:
                if row["spec"] != spec:
                    raise GatewayRefused("IDEMPOTENCY_CONFLICT")
                return self.view(request_id)
            if spec["host_boot_id"] != self.host:
                raise GatewayRefused("HOST_CHANGED")
            if spec["expected_generation"] != self.generation or spec["expected_owner"] != self.owner:
                raise GatewayRefused("AUTHORITY_CHANGED")
            if self.clock() * 1000 >= spec["deadline_ms"]:
                raise GatewayRefused("DEADLINE_EXCEEDED")
            if not self._same_target(spec):
                raise GatewayRefused("TARGET_CHANGED")
            if any(r["phase"] == "PROTECTED" and r["spec"]["target_vm"] == spec["target_vm"] and r["host"] == self.host for r in self.rows.values()):
                raise GatewayRefused("TARGET_ALREADY_PROTECTED")
            self.rows[request_id] = {"spec": dict(spec), "phase": "PROTECTED", "host": self.host, "completion": None}
            return self.view(request_id)

        return self._call("submit", work)

    def get(self, request_id: str) -> dict[str, Any]:
        self.calls.append(("get", request_id))
        return self._call("get", lambda: self.view(request_id))

    def finish(self, request_id: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("finish", (request_id, dict(context))))
        return self._call("finish", lambda: self._end(request_id, context, cancel=False))

    def cancel(self, request_id: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("cancel", (request_id, dict(context))))
        return self._call("cancel", lambda: self._end(request_id, context, cancel=True))

    # ---- 흉내 ----

    def _same_target(self, spec: dict[str, Any]) -> bool:
        row = self.targets.get(spec["target_vm"])
        return bool(row and row["execution_id"] == spec["target_execution_id"]
                    and row["intent_revision"] == spec["intent_revision"] and row["lifecycle_generation"] == spec["lifecycle_generation"])

    def view(self, request_id: str) -> dict[str, Any]:
        row = self.rows.get(request_id)
        if row is None:
            if request_id.split(":")[1] != self.host:
                raise GatewayRefused("HOST_CHANGED")
            raise GatewayRefused("NOT_FOUND")
        spec, held = row["spec"], row["phase"] == "PROTECTED"
        return {"request_id": request_id, "purpose": "protect", "phase": row["phase"], "acceptance": "durable", "host_boot_id": row["host"],
                **{k: spec[k] for k in ("target_vm", "target_execution_id", "intent_revision", "lifecycle_generation", "deadline_ms")},
                "protection": {"held": held, "owner_excluded": held, "current_target_matches": self._same_target(spec),
                               "current_host_matches": row["host"] == self.host, "deadline_elapsed": False},
                "current_generation": self.generation, "storage_uncertain": self.storage_uncertain}

    def _end(self, request_id: str, context: dict[str, Any], *, cancel: bool) -> dict[str, Any]:
        row = self.rows.get(request_id)
        if row is None:
            raise GatewayRefused("NOT_FOUND")
        if row["phase"] != "PROTECTED":
            if context != row["completion"] or cancel != (row["phase"] == "CANCELLED"):
                raise GatewayRefused("IDEMPOTENCY_CONFLICT")
            return self.view(request_id)
        if context["host_boot_id"] != self.host:
            raise GatewayRefused("HOST_CHANGED")
        if context["expected_generation"] != self.generation:
            raise GatewayRefused("AUTHORITY_CHANGED")
        current = self.targets.get(row["spec"]["target_vm"])
        if not current or current["execution_id"] != context["target_execution_id"]:
            raise GatewayRefused("TARGET_CHANGED")
        if (current["execution_id"] == row["spec"]["target_execution_id"]) != cancel:
            raise GatewayRefused("TARGET_CHANGED")
        row.update(phase="CANCELLED" if cancel else "RELEASED", completion=dict(context))
        return self.view(request_id)

    def phase(self, request_id: str) -> str:
        return self.rows[request_id]["phase"]

    def count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)


def lost(code: str = "OUTCOME_UNKNOWN") -> GatewayUncertain:
    return GatewayUncertain(code)
