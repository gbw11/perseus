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
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from ..errors import (
    Dom0ManagerError,
    DriverError,
    DriverTimeout,
    InsufficientResources,
    InvalidArgument,
    InvalidState,
    NotSupportedByPlatform,
    PlatformUnavailable,
    VMExists,
    VMNotFound,
)
from ..models import VmState
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
    ) -> dict[str, Any]: ...


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
    """

    def __init__(self, inner: ManagerLike, *, busy_timeout_s: float = 5.0) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.busy_timeout_s = busy_timeout_s

    @property
    def data(self) -> Path:
        return self._inner.data

    def call(self, action: str, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if not self._lock.acquire(timeout=self.busy_timeout_s):
            return _busy_envelope(action, self.busy_timeout_s, kwargs.get("request_id"))
        try:
            return self._inner.call(action, spec, **kwargs)
        finally:
            self._lock.release()


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


def raise_for_envelope(envelope: dict[str, Any], *, context: str) -> dict[str, Any]:
    """ok 면 data 를 돌려주고, 아니면 계약 예외로 바꾼다."""
    if envelope.get("ok"):
        return envelope.get("data") or {}
    error = envelope.get("error") or {}
    code = str(error.get("code", "UNKNOWN"))
    message = str(error.get("message", "platform error"))
    detail = f"{code}: {message}"  # 사람용. 분기는 platform_code 로 한다
    if code == "INSUFFICIENT_STORAGE":
        raise _insufficient_storage(message, detail, platform_code=code)
    if code == "IP_POOL_EXHAUSTED":  # 플랫폼이 전역 잠금 안에서 고른 Linux 내부 IP 가 바닥남
        raise InsufficientResources("ipv4", 1, 0, platform_code=code)
    if code in _TIMEOUT_MESSAGE_CODES and any(marker in message for marker in _TIMEOUT_MESSAGE_MARKERS):
        raise DriverTimeout(f"{context}: platform operation deadline exceeded", detail=detail, platform_code=code)
    cls = _CODE_MAP.get(code, DriverError)  # 모르는 코드(ARTIFACT_INVALID 등)는 500 이지만 원본 코드는 보존한다
    raise cls(f"{context}: {message}", detail=detail, platform_code=code)


def _insufficient_storage(message: str, detail: str, *, platform_code: str) -> Dom0ManagerError:
    # 계약 ResourceError.name 은 vcpus|memory_mib 뿐이라 resource 없이 507 만 낸다 (v2 에서 storage 추가 제안)
    err = Dom0ManagerError(f"insufficient storage on board: {message}", detail=detail, platform_code=platform_code)
    err.status_code = 507
    err.code = "InsufficientResources"
    return err


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
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise DriverError("platform view is missing vm_id/vcpus/memory_mib", detail=str(view)[:200]) from exc


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class PlatformDriver(Driver):
    name = "platform"

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
        data = raise_for_envelope(self.manager.call("list", {"lifecycle": lifecycle}), context="list")
        return list(data.get("vms") or [])

    def _view(self, vm_id: UUID) -> dict[str, Any]:
        data = raise_for_envelope(self.manager.call("get", {"vm_id": str(vm_id)}), context=f"get {vm_id}")
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
        data = raise_for_envelope(self.manager.call("start", {"vm_id": str(spec.vm_id)}), context=f"start {spec.vm_id}")
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
        envelope = self.manager.call("stop", {"vm_id": str(vm_id), "force": force})
        if not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
            return  # 멱등: 등록 자체가 없어도 목표 상태(도메인 부재)는 달성
        raise_for_envelope(envelope, context=f"stop {vm_id}")

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
        # 조회 한도는 요청 전체다. registry 조회(SD 카드)가 쓴 시간도 뺀다
        budget = self._xentop.timeout_s
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
        envelope = self.manager.call("get", {"vm_id": str(vm_id)})
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
        envelope = self.manager.call("create", spec, execute=True, idempotency_key=f"create-{vm_id}")
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
        envelope = self.manager.call("retire", {"vm_id": str(vm_id)}, execute=True, idempotency_key=f"retire-{vm_id}")
        if not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
            raise VMNotFound(f"instance {vm_id} not registered")
        raise_for_envelope(envelope, context=f"retire {vm_id}")
