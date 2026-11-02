"""Xenova 플랫폼 관리 라이브러리(xenova_management.Manager) 기반 드라이버·프로비저너 (S15P31E110-115).

플랫폼 계약(docs/build-guides/xenova-vm-management.md):
- Manager.call(action, spec, execute=, idempotency_key=) 는 HTTP 를 모르고 항상 같은 JSON 봉투를 돌려준다.
  {schema_version, ok, request_id, operation_id, replayed, data, error{code, message, retryable, details}}
- registry(/srv/xenova/instances) 가 등록의 원본, Xen 이 실행 상태의 원본이다. dom0-manager 는 둘을 읽기만 한다.
- list/get 의 각 항목: vm_id, name, lifecycle, configuration(vcpus, memory_mib, guest_kind, ipv4 ...), xen_state, domid.
  domid 가 None 이면 Xen 에 도메인이 없다. 계약 v1.1 에서 그 VM 은 dom0-manager 목록에 나오지 않고 단건은 404 다.
- 생성은 registry 등록(create) + 시작(start) 두 단계다. 여기서는 Provisioner 가 create, Driver 가 start 를 맡는다.
- stop 은 플랫폼이 최대 90초 동기 대기하고 자동 강제 종료하지 않는다. force 는 명시적일 때만.

- create 는 Spring 이 발급한 vm_id 를 필수로 받아 registry·Xen UUID 로 쓰고, 내부 실행 이름(vm-<hex>)·MAC·Linux IP 는
  플랫폼이 전역 잠금 안에서 발급한다. dom0-manager 는 ipv4 를 보내지 않고 IP_POOL_EXHAUSTED 를 507 로 옮긴다.

정지 요청 추적(S15P31E110-305, docs/platform/cli-manager-spec.md 공통 계약 A·Python Manager 호출):
- 키 있는 stop 과 원 작업 조회(control-operation)는 Manager.fork() 의 요청별 객체로 부른다. 긴 start 가 공유 Manager 를 쥐어도
  stop 은 짧은 admission 잠금으로 정지 의도를 내구 수락하고, 실행 잠금이 잡혀 있으면 기다리지 않고 pending 으로 돌아온다.
  나머지 action 은 지금처럼 SerializedManager 로 직렬화한다. fork 가 없는 옛 Manager 는 직렬화된 경로를 그대로 쓴다
- 요청 예산(budget.py)이 있으면 직렬화 대기를 남은 예산으로 줄이고, 하위에는 budget_ms(get/list/control-operation 은 timeout_ms 도)로
  남은 값만 준다. 남은 값이 없으면 제출하지 않는다
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from .. import budget as request_budget
from ..errors import (
    Dom0ManagerError,
    DriverError,
    DriverTimeout,
    InsufficientResources,
    InvalidArgument,
    InvalidState,
    NotSupportedByPlatform,
    OperationNotFound,
    PlatformUnavailable,
    VMExists,
    VMNotFound,
)
from ..models import ControlOperation, ControlStatus, VmState
from .base import CreateSpec, Domain, Driver, HostInfo, InstancePaths, Provisioner, StatsSample
from .xentop import Xentop, collect_stats
from .xl import Xl

log = logging.getLogger(__name__)


class ManagerLike(Protocol):
    data: Path

    def call(
        self,
        action: str,
        spec: dict[str, Any] | None = None,
        *,
        execute: bool = False,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]: ...  # 새 Manager 는 budget_ms 와 fork() 도 받는다. 있는지는 호출 때 확인한다


# ---------------------------------------------------------------------------
# 요청 예산 전달
# ---------------------------------------------------------------------------

_BUDGET_ACTIONS = frozenset({"start", "stop", "reboot", "destroy", "get", "list", "control-operation"})  # 하위 budget_ms 허용 action
_TIMEOUT_ACTIONS = frozenset({"get", "list", "control-operation"})  # spec timeout_ms(1..15000) 를 받는 조회
_READ_TIMEOUT_MS = 15_000  # 하위 조회의 기본·상한


def _accepts_budget(manager: Any) -> bool:
    try:
        return "budget_ms" in inspect.signature(manager.call).parameters
    except (TypeError, ValueError):
        return False


def call_with_budget(manager: Any, action: str, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """요청 예산이 있으면 남은 값만 하위에 준다. 남은 값이 없으면 제출하지 않고 DEADLINE_EXCEEDED 봉투를 돌려준다.

    budget_ms 를 모르는 옛 Manager 에는 넘기지 않는다(넘기면 호출 자체가 실패한다). 그때도 만료 뒤 제출 금지는 지킨다.
    """
    budget = request_budget.current()
    if budget is None:
        return manager.call(action, spec, **kwargs)
    remaining = budget.downstream_ms()
    if remaining is None:
        return _deadline_envelope(action, kwargs.get("request_id"))
    if action in _BUDGET_ACTIONS and _accepts_budget(manager):
        kwargs["budget_ms"] = min(remaining, request_budget.MAX_MS)
        if action in _TIMEOUT_ACTIONS:
            spec = dict(spec or {})
            spec["timeout_ms"] = min(int(spec.get("timeout_ms", _READ_TIMEOUT_MS)), remaining, _READ_TIMEOUT_MS)
    return manager.call(action, spec, **kwargs)


def _deadline_envelope(action: str, request_id: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "ok": False,
        "request_id": request_id,
        "operation_id": None,
        "replayed": False,
        "data": None,
        "error": {
            "code": "DEADLINE_EXCEEDED",
            "message": f"request budget exhausted before {action} was submitted; nothing was sent to the platform",
            "retryable": False,
            "details": {"submitted": False},
        },
    }


# ---------------------------------------------------------------------------
# 모듈 로딩
# ---------------------------------------------------------------------------


class SerializedManager:
    """Manager.call 을 프로세스 안에서 직렬화하되, 차례를 기다리는 시간은 busy_timeout_s 로 제한한다.

    현재 Manager 는 요청별 상태(current_operation, replayed)를 인스턴스 변수에 두므로 FastAPI 스레드 여러 개가
    같은 객체를 동시에 부르면 operation_id·복구 기록이 섞일 수 있다. 플랫폼의 파일 잠금(/run/xenova)은 임계 구간만
    보호하고 그 앞뒤의 인스턴스 변수는 보호하지 않는다. 플랫폼이 요청별 context 로 바뀌어도 이 직렬화는 해가 없다.

    정지는 최대 90초 동안 잠금을 쥔다. 그동안 조회·생성·health 가 무한정 줄을 서면 FastAPI 스레드풀이 잠금 대기로
    가득 차 서비스 전체가 멈춘 것처럼 보인다. 그래서 대기 시간을 넘기면 플랫폼의 파일 잠금 충돌과 같은 모양의
    OPERATION_IN_PROGRESS(retryable) 봉투를 돌려주고, 호출자는 그것을 503 PlatformUnavailable 로 낸다.
    요청 예산이 있으면 대기 한도는 남은 예산과 busy_timeout_s 중 짧은 쪽이고, 차례가 왔을 때 예산이 다 썼으면 제출하지 않는다.
    """

    def __init__(self, inner: ManagerLike, *, busy_timeout_s: float = 5.0) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.busy_timeout_s = busy_timeout_s

    @property
    def data(self) -> Path:
        return self._inner.data

    def call(self, action: str, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        budget = request_budget.current()
        wait = self.busy_timeout_s if budget is None else budget.wait_s(self.busy_timeout_s)
        if not self._lock.acquire(timeout=wait):
            if budget is not None and budget.downstream_ms() is None:
                return _deadline_envelope(action, kwargs.get("request_id"))
            return _busy_envelope(action, wait, kwargs.get("request_id"))
        try:
            return call_with_budget(self._inner, action, spec, **kwargs)
        finally:
            self._lock.release()

    def fork(self) -> ManagerLike | None:
        """정지 수락·원 작업 조회용 요청별 객체(플랫폼 Manager.fork). 직렬화하지 않는다.

        같은 저장소·실행 잠금·guard 를 쓰는 별도 요청 객체일 뿐 새 프로세스가 아니다. 플랫폼이 fork 를 모르면 None 이고,
        호출자는 직렬화된 이 객체를 그대로 쓴다(공유 inner 를 잠금 없이 부르지 않는다).
        """
        fork = getattr(self._inner, "fork", None)
        return fork() if callable(fork) else None


def _busy_envelope(action: str, waited_s: float, request_id: Any) -> dict[str, Any]:
    """플랫폼이 파일 잠금 충돌 때 내는 봉투와 같은 모양 (xenova_management.Manager.call 의 PlatformError 분기)."""
    return {
        "schema_version": "1.0",
        "ok": False,
        "request_id": request_id,
        "operation_id": None,
        "replayed": False,
        "data": None,
        "error": {
            "code": "OPERATION_IN_PROGRESS",
            "message": f"another platform operation is in progress; {action} waited {waited_s:g}s for the manager",
            "retryable": True,
            "details": {"busy_timeout_s": waited_s},
        },
    }


def load_manager(platform_lib: str, *, busy_timeout_s: float = 5.0) -> ManagerLike:
    """XENOVA_PLATFORM_LIB 의 디렉터리들을 sys.path 에 넣고 xenova_management.Manager() 를 만든다."""
    for entry in [p for p in platform_lib.split(os.pathsep) if p]:
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        module = importlib.import_module("xenova_management")
    except ImportError as exc:
        raise PlatformUnavailable(
            "xenova_management not importable; set XENOVA_PLATFORM_LIB (board: /usr/lib/xenova)", detail=str(exc)
        ) from exc
    return SerializedManager(module.Manager(), busy_timeout_s=busy_timeout_s)


RecordsLoader = Callable[[], Mapping[str, Mapping[str, Any]]]


def load_records_loader(platform_lib: str, manager: ManagerLike) -> RecordsLoader:
    """플랫폼 라이브러리의 읽기 전용 registry 검증 `xenova_vm.load_records(data, validate_assets=False)` 를 부르는 함수를 만든다.

    잠금·Xen 호출 없이 활성 레코드(instances/<id>/instance.json)만 읽으며, 플랫폼과 같은 규칙으로 심볼릭 링크·비정규 경로·
    id 와 디렉터리 이름 불일치·중복 신원·누락 레코드를 거부한다(PlatformError). 부팅 자산 검사는 하지 않는다.
    xenova_management 가 모듈 로딩 때 xenova_vm 을 import 하므로 실제 조립(build_driver)에서는 두 모듈이 함께 있거나 함께 없고,
    없으면 load_manager 에서 서비스 시작이 실패한다. 여기서 import 를 호출 시점으로 미루는 것은 드라이버를 직접 조립하는 테스트 때문이다.
    """
    for entry in [p for p in platform_lib.split(os.pathsep) if p]:
        if entry not in sys.path:
            sys.path.insert(0, entry)

    def load() -> Mapping[str, Mapping[str, Any]]:
        try:
            module = importlib.import_module("xenova_vm")
        except ImportError as exc:
            raise PlatformUnavailable("xenova_vm not importable; set XENOVA_PLATFORM_LIB (board: /usr/lib/xenova)", detail=str(exc)) from exc
        return module.load_records(Path(manager.data), validate_assets=False)

    return load


# ---------------------------------------------------------------------------
# 플랫폼 봉투 → 계약 오류
# ---------------------------------------------------------------------------

# 플랫폼 오류 코드 → (예외 클래스). 백엔드 파트와 합의한 HTTP 매핑: 400 / 404 / 409 / 507 / 503 / 504
_CODE_MAP: dict[str, type[Dom0ManagerError]] = {
    "INVALID_SPEC": InvalidArgument,
    "TEMPLATE_NOT_FOUND": InvalidArgument,
    "TEMPLATE_VERSION_NOT_FOUND": InvalidArgument,
    "RESOURCE_UNSUPPORTED": InvalidArgument,
    "VM_NOT_FOUND": VMNotFound,
    "VM_NAME_CONFLICT": VMExists,
    "IDEMPOTENCY_CONFLICT": VMExists,
    "IP_CONFLICT": InvalidState,
    "OPERATION_IN_PROGRESS": PlatformUnavailable,  # 다른 플랫폼 작업이 잠금 보유(재시도 가능). 프로세스 안 직렬화 대기 초과도 같은 코드
    "VM_NOT_STOPPED": InvalidState,
    "TARGET_CHANGED": InvalidState,
    "ASSET_IN_USE": InvalidState,
    "RESTORE_CONFLICT": InvalidState,
    "VM_PURGED": InvalidState,
    "STORAGE_UNAVAILABLE": PlatformUnavailable,
    "RECOVERY_REQUIRED": PlatformUnavailable,
    "CLEANUP_INCOMPLETE": PlatformUnavailable,
    # 복구 기반(xenova-recovery-foundation.md "오류와 관측"). 충돌(CLI exit 3)은 409, 실행/불확실(exit 4)은 503·500
    "HOST_QUIESCING": InvalidState,  # host 가 새 시작·복구 수락을 닫음 (종료 barrier)
    "BOOT_ADMISSION_REQUIRED": InvalidState,  # 현재 host 의 실행 정책이 미평가·미확정·거절
    "INTENT_MISMATCH": InvalidState,  # 적용 가능한 running 의도 또는 revision 불일치
    "RECOVERY_MODE_UNSUPPORTED": NotSupportedByPlatform,  # graceful 미지원(Zephyr) 또는 per-VM force 승인 없음
    "DEADLINE_EXCEEDED": DriverTimeout,  # 같은 host 의 최초 deadline 만료·범위 초과
    "OBSERVATION_UNAVAILABLE": PlatformUnavailable,  # Xen 접근·출력·incarnation 을 확인하지 못함. 부재가 아님
    "RECONCILE_REQUIRED": PlatformUnavailable,  # 실행·영구 기록의 결과를 안전하게 확정하지 못함. 운영자 재판정 전까지 재시도
    "INTENT_RECONCILIATION_REQUIRED": PlatformUnavailable,  # 의도 원본과 journal 불일치로 start 보류
    "XEN_EXECUTION_FAILED": DriverError,  # 수락 후 물리 제어 실패. accepted 의도는 유지
}

# 정지 대기 만료는 아직 전용 코드 없이 XEN_EXECUTION_FAILED(복구 기반 control 경로)나 ARTIFACT_INVALID(일반 PlatformError 포장)에
# 메시지로만 실린다. 플랫폼이 DEADLINE_EXCEEDED 를 내기 전까지 이 두 코드에 한해 메시지로 시한 초과를 가려 504 로 낸다
_TIMEOUT_MESSAGE_CODES = frozenset({"XEN_EXECUTION_FAILED", "ARTIFACT_INVALID"})
_TIMEOUT_MESSAGE_MARKERS = ("deadline", "timed out")


def raise_for_envelope(envelope: dict[str, Any], *, context: str, operation: ControlOperation | None = None) -> dict[str, Any]:
    """ok 면 data 를 돌려주고, 아니면 계약 예외로 바꾼다. 원 retryable 을 보존하고, 제어 요청이면 원 작업 문맥(operation)을 붙인다.

    operation 이 superseded 면 409(이후 사용자 의도가 이겼다), uncertain 이면 최소 503(조사 전 재시도 금지)으로 낸다.
    """
    if envelope.get("ok"):
        return envelope.get("data") or {}
    error = envelope.get("error") or {}
    code = str(error.get("code", "UNKNOWN"))
    message = str(error.get("message", "platform error"))
    detail = f"{code}: {message}"  # 사람용. 분기는 platform_code 로 한다
    retryable = error.get("retryable") if isinstance(error.get("retryable"), bool) else None
    extra: dict[str, Any] = {"platform_code": code, "retryable": retryable}
    if operation is not None:
        extra["operation"] = operation.model_dump(mode="json")
    if code == "INSUFFICIENT_STORAGE":
        raise _insufficient_storage(message, detail, **extra)
    if code == "IP_POOL_EXHAUSTED":  # 플랫폼이 전역 잠금 안에서 고른 Linux 내부 IP 가 바닥남
        raise InsufficientResources("ipv4", 1, 0, platform_code=code, retryable=retryable)
    cls: type[Dom0ManagerError]
    if code in _TIMEOUT_MESSAGE_CODES and any(marker in message for marker in _TIMEOUT_MESSAGE_MARKERS):
        cls, message = DriverTimeout, "platform operation deadline exceeded"
    else:
        cls = _CODE_MAP.get(code, DriverError)  # 모르는 코드(ARTIFACT_INVALID 등)는 500 이지만 원본 코드는 보존한다
    if operation is not None and operation.status == ControlStatus.superseded:
        cls = InvalidState
    elif operation is not None and operation.status == ControlStatus.uncertain and cls.status_code < 503:
        cls = PlatformUnavailable
    raise cls(f"{context}: {message}", detail=detail, **extra)


def _insufficient_storage(message: str, detail: str, **extra: Any) -> Dom0ManagerError:
    # 계약 ResourceError.name 은 vcpus|memory_mib 뿐이라 resource 없이 507 만 낸다 (v2 에서 storage 추가 제안)
    err = Dom0ManagerError(f"insufficient storage on board: {message}", detail=detail, **extra)
    err.status_code = 507
    err.code = "InsufficientResources"
    return err


# ---------------------------------------------------------------------------
# 제어 요청 상태 (S15P31E110-305)
# ---------------------------------------------------------------------------


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _warning_codes(warnings: Any) -> list[str]:
    if not isinstance(warnings, list):
        return []
    return [w["code"] for w in warnings if isinstance(w, dict) and isinstance(w.get("code"), str)]


_PHASE_STATUS: dict[str, tuple[ControlStatus, bool | None]] = {
    "prepared": (ControlStatus.unknown, None),  # 준비 기록만 있고 의도 게시 전: 수락 성공이 아니다
    "accepted": (ControlStatus.pending, True),
    "executing": (ControlStatus.pending, True),
    "completed": (ControlStatus.completed, True),
    "failed": (ControlStatus.failed, True),
    "uncertain": (ControlStatus.uncertain, True),
}


def _accepted_without_operation(code: str | None) -> PlatformUnavailable:
    return PlatformUnavailable(
        "platform accepted the control request but returned no valid operation_id; the original operation cannot be tracked",
        platform_code=code,
    )


def control_operation_from_envelope(envelope: dict[str, Any], *, vm_id: UUID, action: str, key: str | None) -> ControlOperation:
    """제어 호출(stop) 봉투 → 상태. 수락된 요청의 오류는 details 의 허용 필드만 옮긴다.

    수락된 요청(완료 포함)은 원 작업 ID 가 있어야 추적할 수 있다. 없거나 UUID 가 아니면 하위 버전 불일치·기록 손상이므로
    수락·완료로 내지 않고 503 이다.

    봉투의 replayed 는 옮기지 않는다. 하위 stop 은 의도 수락과 실행이 두 단계라 첫 요청도 실행 단계가 방금 수락한 기록을 다시
    찾아 replayed=true 가 되므로, 첫 요청과 재전달을 가르는 근거가 아니다. 재전달 여부는 호출자가 자기 키로 안다.
    """
    base = {"vm_id": vm_id, "action": action, "request_key": key}
    if envelope.get("ok"):
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        original = data.get("original_result") if isinstance(data.get("original_result"), dict) else {}
        operation_id = _uuid(envelope.get("operation_id"))
        if operation_id is None:
            raise _accepted_without_operation(None)
        return ControlOperation(
            **base, operation_id=operation_id, status=ControlStatus.completed, accepted=True, phase="completed",
            intent_revision=_int(original.get("intent_revision")), warnings=_warning_codes(data.get("diagnostic_warnings")),
        )
    error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    code = _str(error.get("code"))
    if details.get("accepted") is not True:
        # 수락 전 거절. 수락 저장을 확인하지 못했을 때(unconfirmed)만 수락 여부가 불명이다
        unconfirmed = details.get("acceptance") == "unconfirmed"
        return ControlOperation(
            **base, operation_id=_uuid(envelope.get("operation_id")), status=ControlStatus.unknown if unconfirmed else ControlStatus.rejected,
            accepted=None if unconfirmed else False, error_code=code, warnings=_warning_codes(details.get("diagnostic_warnings")),
        )
    phase, completion = _str(details.get("phase")), details.get("completion")
    if phase == "uncertain" or code == "RECONCILE_REQUIRED":
        status = ControlStatus.uncertain  # completion=pending 이어도 자동 재시도 가능한 접수로 바꾸지 않는다
    elif completion == "superseded":
        status = ControlStatus.superseded
    elif completion == "pending" and phase in ("accepted", "executing"):
        status = ControlStatus.pending
    elif completion == "failed" or phase == "failed":
        status = ControlStatus.failed
    else:
        status = ControlStatus.uncertain
    operation_id = _uuid(details.get("operation_id") or envelope.get("operation_id"))
    if operation_id is None:
        raise _accepted_without_operation(code)
    return ControlOperation(
        **base, operation_id=operation_id, status=status, accepted=True,
        phase=phase, intent_revision=_int(details.get("intent_revision")), host_boot_id=_str(details.get("host_boot_id")),
        lifecycle_generation=_int(details.get("lifecycle_generation")), deadline_monotonic_ms=_int(details.get("deadline_monotonic_ms")),
        execution_deadline_expired=_bool(details.get("execution_deadline_expired")), next_action=_str(details.get("next_action")),
        error_code=code, warnings=_warning_codes(details.get("diagnostic_warnings")),
    )


_TRACKED_REQUEST = {"action": "stop", "force": False}  # HTTP 가 키로 추적하는 제어 요청(4.3.1). 다른 기록은 이 조회의 대상이 아니다


def control_operation_from_record(
    record: dict[str, Any], observation: dict[str, Any], *, vm_id: UUID, key: str, envelope_operation_id: Any,
) -> ControlOperation:
    """control-operation 조회의 원 기록 → 상태. superseded 는 현재 의도와 대조해야 알 수 있어 여기서는 내지 않는다.

    응답 유실 뒤 원 작업을 되찾는 경계라 기록이 정확히 '이 VM·이 키의 graceful stop' 이어야 한다: 유효한 operation_id 가 봉투의 값과
    같고, record.vm_id·request.vm_id 가 조회 VM 이고, request 가 stop(force 없음)이고, idempotency_key 가 조회 키이고, phase 가 알려진
    값. 하나라도 어긋나면 기록 손상이나 하위 버전 불일치이므로 200 으로 꾸미지 않고 503 이다.
    """
    phase = _str(record.get("phase"))
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    operation_id = _uuid(record.get("operation_id"))
    problems = [name for name, bad in (
        ("operation_id", operation_id is None or _uuid(envelope_operation_id) != operation_id),
        ("vm_id", _uuid(record.get("vm_id")) != vm_id),
        ("request.vm_id", _uuid(request.get("vm_id")) != vm_id),
        ("request.action", request.get("action") != _TRACKED_REQUEST["action"]),
        ("request.force", request.get("force") is not _TRACKED_REQUEST["force"]),  # 빠진 필드를 False 로 채우지 않는다
        ("idempotency_key", record.get("idempotency_key") != key),
        ("phase", phase not in _PHASE_STATUS),
    ) if bad]
    if problems:
        raise PlatformUnavailable(f"control-operation record for vm {vm_id} is malformed ({', '.join(problems)}); inspect the original journal")
    status, accepted = _PHASE_STATUS[phase]
    error = record.get("error") if isinstance(record.get("error"), dict) else {}
    return ControlOperation(
        vm_id=vm_id, action=request["action"], request_key=key,
        operation_id=operation_id, status=status, accepted=accepted, phase=phase,
        intent_revision=_int(record.get("intent_revision")), host_boot_id=_str(record.get("host_boot_id")),
        lifecycle_generation=_int(record.get("lifecycle_generation")), deadline_monotonic_ms=_int(record.get("deadline_monotonic_ms")),
        execution_deadline_expired=_bool(observation.get("execution_deadline_expired")), error_code=_str(error.get("code")),
    )


# ---------------------------------------------------------------------------
# 상태 변환
# ---------------------------------------------------------------------------

# 플랫폼 xen_state(xenova_vm.xen_state) → 계약 3.1 상태. 'stopped' 는 도메인 부재라 None.
_STATE_MAP: dict[str, VmState | None] = {
    "running": VmState.running,
    "blocked": VmState.running,  # I/O 대기 중인 정상 상태
    "unknown": VmState.running,  # '------': 실행 가능·미스케줄 (libxl 규칙)
    "paused": VmState.paused,
    "shutdown": VmState.stopping,
    "dying": VmState.stopping,
    "crashed": VmState.crashed,
    "stopped": None,
}


def map_platform_state(xen_state: str | None) -> VmState | None:
    if xen_state is None:
        return None
    try:
        return _STATE_MAP[xen_state]
    except KeyError as exc:
        raise DriverError(f"unknown platform xen_state {xen_state!r}") from exc


def view_to_domain(view: dict[str, Any]) -> Domain | None:
    """플랫폼 list/get 항목 → Domain. Xen 에 없으면 None."""
    if view.get("domid") is None:
        return None
    state = map_platform_state(view.get("xen_state"))
    if state is None:
        return None
    configuration = view.get("configuration") or {}
    try:
        return Domain(
            vm_id=UUID(str(view["vm_id"])),
            state=state,
            vcpus=int(configuration["vcpus"]),
            memory_mib=int(configuration["memory_mib"]),
            domid=int(view["domid"]),
            name=view.get("name"),
            observation_id=view.get("execution_id"),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise DriverError("platform view is missing vm_id/vcpus/memory_mib", detail=str(view)[:200]) from exc


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


_LOOKUP_TIMEOUT_MS = 5_000  # 원 작업 조회의 기본 한도. HTTP 조회 계약 5초와 같다


class PlatformDriver(Driver):
    name = "platform"
    tracks_control_operations = True

    def __init__(
        self,
        manager: ManagerLike,
        xl: Xl | None = None,
        xentop: Xentop | None = None,
        *,
        records_loader: RecordsLoader | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.manager = manager
        self._xl = xl
        self._xentop = xentop
        self._records_loader = records_loader
        self._monotonic = monotonic

    # ---- 조회 ----

    def _views(self, lifecycle: str = "REGISTERED") -> list[dict[str, Any]]:
        data = raise_for_envelope(call_with_budget(self.manager, "list", {"lifecycle": lifecycle}), context="list")
        return list(data.get("vms") or [])

    def _view(self, vm_id: UUID) -> dict[str, Any]:
        data = raise_for_envelope(call_with_budget(self.manager, "get", {"vm_id": str(vm_id)}), context=f"get {vm_id}")
        view = data.get("vm")
        if not view:
            raise VMNotFound(f"vm {vm_id} not registered")
        return view

    def list_domains(self) -> list[Domain]:
        domains = [view_to_domain(v) for v in self._views()]
        return [d for d in domains if d is not None]

    def get_domain(self, vm_id: UUID) -> Domain:
        domain = view_to_domain(self._view(vm_id))
        if domain is None:
            raise VMNotFound(f"domain {vm_id} is not running")
        return domain

    def has_domain(self, vm_id: UUID) -> bool:
        try:
            self.get_domain(vm_id)
        except VMNotFound:
            return False
        return True

    def registration(self, vm_id: UUID) -> dict[str, Any] | None:
        """등록 정보(view). 미등록이면 None. Provisioner 가 쓴다."""
        try:
            return self._view(vm_id)
        except VMNotFound:
            return None

    # ---- 명령 ----

    def create(self, spec: CreateSpec) -> Domain:
        """registry 에 등록된 VM 을 시작한다. 등록은 Provisioner.ensure_instance 가 먼저 한다."""
        data = raise_for_envelope(call_with_budget(self.manager, "start", {"vm_id": str(spec.vm_id)}), context=f"start {spec.vm_id}")
        domain = view_to_domain(data.get("vm") or {})
        if domain is None:
            # start 가 돌아왔는데 도메인이 안 보이면 플랫폼이 거부한 것(예: unknown 상태). 그대로 실패로 올린다
            raise DriverError(f"start {spec.vm_id}: platform returned without a live domain")
        return domain

    def shutdown(self, vm_id: UUID, *, grace_s: float) -> None:
        # Manager stop 에는 대기 시간 입력이 없다(vm_id·force 뿐). 실제 대기는 config.PLATFORM_STOP_WAIT_S 고정이고 Settings 가
        # platform 모드에서 shutdown_grace_s 를 그 값으로 강제하므로 grace_s 는 여기서 쓰지 않는다
        self._stop(vm_id, force=False)

    def destroy(self, vm_id: UUID) -> None:
        self._stop(vm_id, force=True)

    def _stop(self, vm_id: UUID, *, force: bool) -> None:
        envelope = call_with_budget(self.manager, "stop", {"vm_id": str(vm_id), "force": force})
        if not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
            return  # 멱등: 등록 자체가 없어도 목표 상태(도메인 부재)는 달성
        raise_for_envelope(envelope, context=f"stop {vm_id}")

    # ---- 정지 요청 추적 (S15P31E110-305) ----

    def _request_manager(self) -> ManagerLike:
        """stop 수락·원 작업 조회용 요청별 Manager. fork 가 없으면 공유(직렬화된) Manager 를 그대로 쓴다."""
        fork = getattr(self.manager, "fork", None)
        forked = fork() if callable(fork) else None
        return forked if forked is not None else self.manager

    def request_stop(self, vm_id: UUID, *, key: str) -> ControlOperation | None:
        """키 있는 graceful stop. 완료·접수(pending)는 상태로 돌려주고, 그 밖은 원 작업 문맥을 붙인 예외로 올린다.
        등록이 없으면 None(작업 기록 없이 목표 상태 달성)."""
        envelope = call_with_budget(self._request_manager(), "stop", {"vm_id": str(vm_id), "force": False}, idempotency_key=key)
        if not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
            return None
        operation = control_operation_from_envelope(envelope, vm_id=vm_id, action="stop", key=key)
        if envelope.get("ok") or operation.status == ControlStatus.pending:
            return operation
        raise_for_envelope(envelope, context=f"stop {vm_id}", operation=operation)
        return None  # raise_for_envelope 는 ok 가 아니면 반드시 예외를 낸다

    def control_operation(self, vm_id: UUID, *, key: str) -> ControlOperation:
        """원 제어 작업을 요청 키로 읽는다. 짧은 admission 잠금만 쓰고 시작·정지·이행이나 Xen 관측을 하지 않는다."""
        spec = {"vm_id": str(vm_id), "idempotency_key": key, "timeout_ms": _LOOKUP_TIMEOUT_MS}
        envelope = call_with_budget(self._request_manager(), "control-operation", spec)
        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            code = error.get("code")
            if code == "VM_NOT_FOUND":
                raise OperationNotFound(
                    f"no control operation for this key on vm {vm_id} at lookup time; an earlier submission may still be in flight",
                    platform_code=code,
                )
            if code == "INVALID_SPEC":  # 키·UUID 는 API 가 먼저 검사한다. 남는 경우는 조회를 모르는 옛 Manager 다
                raise NotSupportedByPlatform("platform manager does not support control-operation lookup",
                                             detail=f"{code}: {error.get('message')}", platform_code=code)
            raise_for_envelope(envelope, context=f"control-operation {vm_id}")
        data = envelope.get("data") or {}
        record = data.get("operation") if isinstance(data.get("operation"), dict) else None
        if record is None:
            raise DriverError("platform control-operation returned no operation record", platform_code=None)
        observation = data.get("deadline_observation") if isinstance(data.get("deadline_observation"), dict) else {}
        return control_operation_from_record(record, observation, vm_id=vm_id, key=key,
                                             envelope_operation_id=envelope.get("operation_id"))

    # ---- 호스트 ----

    def host_info(self) -> HostInfo:
        if self._xl is None:
            raise PlatformUnavailable("xl is not configured for host_info")
        return self._xl.info()

    def collect_stats(self) -> StatsSample:
        """xentop 은 Manager 를 거치지 않는다(잠금·xl list 실행 시간을 피해 조회 한도 안에 끝낸다).

        관리 대상 판별은 uuid 유무가 아니라 플랫폼 registry 등록 여부다. Xen 은 설정에 uuid 가 없어도 새로 만들어 주므로
        xl 로 직접 띄운 도메인도 uuid 를 가진다. registry 는 플랫폼 라이브러리의 읽기 전용 검증(load_records_loader)으로 읽는다.
        """
        if self._xl is None or self._xentop is None:
            raise PlatformUnavailable("xl/xentop are not configured for stats")
        # 조회 한도는 요청 전체다. registry 조회(SD 카드)가 쓴 시간도 뺀다. 요청 예산이 더 짧으면 그 값이다
        budget = request_budget.clamp(self._xentop.timeout_s, "stats")
        started = self._monotonic()
        registered = self.registered_vm_ids()
        remaining = budget - (self._monotonic() - started)
        if remaining <= 0:
            raise DriverTimeout(f"stats deadline of {budget:g}s exhausted by registry scan")
        return collect_stats(
            self._xentop, self._xl, allowed=registered.__contains__, budget_s=remaining, monotonic=self._monotonic
        )

    def registered_vm_ids(self) -> set[UUID]:
        """registry 에 등록된(REGISTERED) VM 의 uuid. 플랫폼 `xenova_vm.load_records(validate_assets=False)` 의 결과다.

        플랫폼이 거부하는 상태(심볼릭 링크, id/디렉터리 불일치, 중복 신원, 레코드 누락·손상)는 RECOVERY_REQUIRED 에 해당하므로
        그 VM 만 빼고 200 을 내지 않고 503 이다. 점으로 시작하는 보관본·작업 공간 디렉터리는 플랫폼이 건너뛴다.
        """
        if self._records_loader is None:
            raise PlatformUnavailable("platform record loader is not configured for stats")
        try:
            records = self._records_loader()
        except Dom0ManagerError:
            raise
        except Exception as exc:  # noqa: BLE001 - 플랫폼 PlatformError 는 정적으로 import 할 수 없다. 읽기 전용 검증 실패는 전부 복구 필요 상태다
            raise PlatformUnavailable(
                "registry is invalid; platform recovery required", detail=f"{type(exc).__name__}: {exc}"[:200]
            ) from exc
        found: set[UUID] = set()
        for name, record in records.items():
            try:
                found.add(UUID(str(record["uuid"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise PlatformUnavailable(f"registry record {name} has no valid uuid; platform recovery required") from exc
        return found

    def health(self) -> bool:
        """xl 응답과 registry 저장소 존재만 본다. Manager 를 거치지 않는다.

        Manager.call("list") 는 잠금 대기에 더해 잠금을 얻은 뒤 xl list 를 두 번(각각 최대 15초) 실행할 수 있어 조회 한도 5초
        (Spring 대기 6초) 안에 끝난다고 보장할 수 없다. 그래서 /healthz 는 xl info(xl_query_timeout_s 한도)와 registry 디렉터리
        존재만 확인한다. registry 내용 오류(STORAGE_UNAVAILABLE)는 목록·생성 호출이 503 으로 알린다.
        """
        if self._xl is not None:
            try:
                self._xl.info()
            except DriverError:
                return False
        return (Path(self.manager.data) / "instances").is_dir()


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------


class PlatformProvisioner(Provisioner):
    """플랫폼 create/retire 로 registry 를 만들고 보관한다. 디스크·설정 파일은 전부 플랫폼이 만든다."""

    def __init__(self, manager: ManagerLike) -> None:
        self.manager = manager

    # ---- 조회 ----

    def _registration(self, vm_id: UUID) -> dict[str, Any] | None:
        envelope = call_with_budget(self.manager, "get", {"vm_id": str(vm_id)})
        if not envelope.get("ok"):
            if (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
                return None
            raise_for_envelope(envelope, context=f"get {vm_id}")
        view = (envelope.get("data") or {}).get("vm")
        return view if view and view.get("lifecycle") == "REGISTERED" else None

    def _paths(self, view: dict[str, Any]) -> InstancePaths:
        # 디렉터리는 플랫폼 내부 실행 이름(configuration.id = vm-<hex>)이다. view["name"] 은 표시 이름이라 경로가 아니다
        internal = (view.get("configuration") or {}).get("id")
        if not internal:
            raise DriverError("platform view lacks configuration.id (internal instance name)", detail=f"vm_id={view.get('vm_id')}")
        root = Path(self.manager.data) / "instances" / str(internal)
        return InstancePaths(
            vm_id=UUID(str(view["vm_id"])), root=root, kernel=root / "Image", ramdisk=root / "initrd", disk=root / "root.raw"
        )

    def instance_exists(self, vm_id: UUID) -> bool:
        return self._registration(vm_id) is not None

    # ---- 생성·보관 ----

    @staticmethod
    def _spec_mismatch(existing: dict[str, Any], template_id: str, template_version: str, vcpus: int, memory_mib: int) -> list[str]:
        """계약 4.2 Spring 불변조건: 같은 vm_id 에는 같은 생성 명세. 기록된 값과 다른 항목을 돌려준다."""
        initial = existing.get("initial_spec") or {}
        configuration = existing.get("configuration") or {}
        recorded = {
            "template_id": initial.get("template_id"),
            "template_version": initial.get("template_version"),
            "vcpus": initial.get("vcpus", configuration.get("vcpus")),
            "memory_mib": initial.get("memory_mib", configuration.get("memory_mib")),
        }
        requested = {"template_id": template_id, "template_version": template_version, "vcpus": vcpus, "memory_mib": memory_mib}
        # 공장 기본 VM 처럼 initial_spec 이 없는 옛 등록은 템플릿을 비교할 수 없어 자원만 비교한다
        return [k for k, v in recorded.items() if v is not None and str(v) != str(requested[k])]

    def ensure_instance(
        self, vm_id: UUID, template_id: str, template_version: str, *, name: str, vcpus: int, memory_mib: int
    ) -> InstancePaths:
        existing = self._registration(vm_id)
        if existing is not None:
            mismatch = self._spec_mismatch(existing, template_id, template_version, vcpus, memory_mib)
            if mismatch:
                raise VMExists(
                    f"vm {vm_id} is registered with a different spec ({', '.join(mismatch)}); same vm_id must keep the same spec"
                )
            return self._paths(existing)

        spec: dict[str, Any] = {
            "vm_id": str(vm_id),  # 팀 결정: Spring 발급 UUID 를 registry·Xen UUID 로 그대로 쓴다
            "name": name,
            "template_id": template_id,
            "template_version": template_version,
            "vcpus": vcpus,
            "memory_mib": memory_mib,
        }
        # ipv4 는 보내지 않는다. 플랫폼이 registry·tombstone·진행 중 작업·공장 예약·DHCP lease 를 전역 잠금 안에서 보고 고르며,
        # 바닥나면 IP_POOL_EXHAUSTED → 507 InsufficientResources(ipv4). 명세 오류(INVALID_SPEC·템플릿 없음)는 400 이다.
        # create 는 하위 budget_ms 를 받지 않지만, 앞의 잠금·조회에서 요청 예산을 다 썼으면 등록을 제출하지 않는다
        envelope = call_with_budget(self.manager, "create", spec, execute=True, idempotency_key=f"create-{vm_id}")
        data = raise_for_envelope(envelope, context=f"create {name}")
        view = (data.get("vm") or data.get("current") or data.get("original_result") or {}) if isinstance(data, dict) else {}
        if not (view.get("configuration") or {}).get("id"):
            view = self._registration(vm_id) or {}
        if str(view.get("vm_id")) != str(vm_id):
            raise DriverError(
                "platform registered a different vm_id than requested; Spring-issued vm_id must be honoured",
                detail=f"requested={vm_id} registered={view.get('vm_id')}",
            )
        return self._paths(view)

    def delete_instance(self, vm_id: UUID) -> None:
        envelope = call_with_budget(self.manager, "retire", {"vm_id": str(vm_id)}, execute=True, idempotency_key=f"retire-{vm_id}")
        if not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
            raise VMNotFound(f"instance {vm_id} not registered")
        raise_for_envelope(envelope, context=f"retire {vm_id}")
