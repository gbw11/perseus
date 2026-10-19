"""Xenova 플랫폼 관리 라이브러리(xenova_management.Manager) 기반 드라이버·프로비저너 (S15P31E110-115).

플랫폼 계약(docs/build-guides/xenova-vm-management.md):
- Manager.call(action, spec, execute=, idempotency_key=) 는 HTTP 를 모르고 항상 같은 JSON 봉투를 돌려준다.
  {schema_version, ok, request_id, operation_id, replayed, data, error{code, message, retryable, details}}
- registry(/srv/xenova/instances) 가 등록의 원본, Xen 이 실행 상태의 원본이다. dom0-manager 는 둘을 읽기만 한다.
- list/get 의 각 항목: vm_id, name, lifecycle, configuration(vcpus, memory_mib, guest_kind, ipv4 ...), xen_state, domid.
  domid 가 None 이면 Xen 에 도메인이 없다. 계약 v1.1 에서 그 VM 은 dom0-manager 목록에 나오지 않고 단건은 404 다.
- 생성은 registry 등록(create) + 시작(start) 두 단계다. 여기서는 Provisioner 가 create, Driver 가 start 를 맡는다.
- stop 은 플랫폼이 최대 90초 동기 대기하고 자동 강제 종료하지 않는다. force 는 명시적일 때만.

호출자 지정 vm_id: 팀 결정은 "Spring 이 vm_id 를 발급"이다. 플랫폼 create 가 spec['vm_id'] 를 받도록 바뀌기 전까지는
새 VM 생성이 NotSupportedByPlatform(501) 로 끝난다. 조회·시작·정지·보관은 그 변경과 무관하게 동작한다.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sys
import threading
from collections.abc import Iterable
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
from .base import CreateSpec, Domain, Driver, HostInfo, InstancePaths, Provisioner
from .xl import Xl

log = logging.getLogger(__name__)

IP_PREFIX = "192.168.77."
_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")


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
    """Manager.call 을 프로세스 안에서 직렬화한다.

    현재 Manager 는 요청별 상태(current_operation, replayed)를 인스턴스 변수에 두므로 FastAPI 스레드 여러 개가
    같은 객체를 동시에 부르면 operation_id·복구 기록이 섞일 수 있다. 플랫폼의 파일 잠금(/run/xenova)은 임계 구간만
    보호하고 그 앞뒤의 인스턴스 변수는 보호하지 않는다. 플랫폼이 요청별 context 로 바뀌어도 이 직렬화는 해가 없다.
    """

    def __init__(self, inner: ManagerLike) -> None:
        self._inner = inner
        self._lock = threading.Lock()

    @property
    def data(self) -> Path:
        return self._inner.data

    def call(self, action: str, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            return self._inner.call(action, spec, **kwargs)


def load_manager(platform_lib: str) -> ManagerLike:
    """PERSEUS_PLATFORM_LIB 의 디렉터리들을 sys.path 에 넣고 xenova_management.Manager() 를 만든다."""
    for entry in [p for p in platform_lib.split(os.pathsep) if p]:
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        module = importlib.import_module("xenova_management")
    except ImportError as exc:
        raise PlatformUnavailable(
            "xenova_management not importable; set PERSEUS_PLATFORM_LIB (board: /usr/lib/xenova)", detail=str(exc)
        ) from exc
    return SerializedManager(module.Manager())


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
    "OPERATION_IN_PROGRESS": InvalidState,
    "VM_NOT_STOPPED": InvalidState,
    "TARGET_CHANGED": InvalidState,
    "ASSET_IN_USE": InvalidState,
    "RESTORE_CONFLICT": InvalidState,
    "VM_PURGED": InvalidState,
    "STORAGE_UNAVAILABLE": PlatformUnavailable,
    "RECOVERY_REQUIRED": PlatformUnavailable,
    "CLEANUP_INCOMPLETE": PlatformUnavailable,
}


def raise_for_envelope(envelope: dict[str, Any], *, context: str) -> dict[str, Any]:
    """ok 면 data 를 돌려주고, 아니면 계약 예외로 바꾼다."""
    if envelope.get("ok"):
        return envelope.get("data") or {}
    error = envelope.get("error") or {}
    code = str(error.get("code", "UNKNOWN"))
    message = str(error.get("message", "platform error"))
    detail = f"{code}: {message}"
    if "deadline" in message:
        raise DriverTimeout(f"{context}: platform operation deadline exceeded", detail=detail)
    if code == "INSUFFICIENT_STORAGE":
        raise _insufficient_storage(message, detail)
    cls = _CODE_MAP.get(code)
    if cls is None:  # ARTIFACT_INVALID 등 실행 오류
        raise DriverError(f"{context}: {message}", detail=detail)
    raise cls(f"{context}: {message}", detail=detail)


def _insufficient_storage(message: str, detail: str) -> Dom0ManagerError:
    # 계약 ResourceError.name 은 vcpus|memory_mib 뿐이라 resource 없이 507 만 낸다 (v2 에서 storage 추가 제안)
    err = Dom0ManagerError(f"insufficient storage on board: {message}", detail=detail)
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


def instance_name(template_id: str, vm_id: UUID) -> str:
    """플랫폼 registry 이름 규칙 [a-z][a-z0-9-]{0,39}: <template_id 정규화>-<vm_id 앞 12자리>."""
    suffix = vm_id.hex[:12]
    base = re.sub(r"[^a-z0-9-]+", "-", template_id.lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)
    if not base or not base[0].isalpha():
        base = "vm-" + base
    base = base[: 40 - len(suffix) - 1].rstrip("-")
    name = f"{base}-{suffix}"
    if not _ID_RE.fullmatch(name):
        raise InvalidArgument(f"cannot derive a valid instance name from template_id {template_id!r}")
    return name


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class PlatformDriver(Driver):
    name = "platform"

    def __init__(self, manager: ManagerLike, xl: Xl | None = None) -> None:
        self.manager = manager
        self._xl = xl

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

    def health(self) -> bool:
        if self._xl is not None:
            try:
                self._xl.info()
            except DriverError:
                return False
        return bool(self.manager.call("list", {"lifecycle": "REGISTERED"}).get("ok"))


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------


class PlatformProvisioner(Provisioner):
    """플랫폼 create/retire 로 registry 를 만들고 보관한다. 디스크·설정 파일은 전부 플랫폼이 만든다."""

    def __init__(self, manager: ManagerLike, *, ip_pool: Iterable[int] = range(2, 100), max_ip_retries: int = 5) -> None:
        self.manager = manager
        self._ip_pool = list(ip_pool)
        self._max_ip_retries = max_ip_retries

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
        root = Path(self.manager.data) / "instances" / str(view["name"])
        return InstancePaths(
            vm_id=UUID(str(view["vm_id"])), root=root, kernel=root / "Image", ramdisk=root / "initrd", disk=root / "root.raw"
        )

    def instance_exists(self, vm_id: UUID) -> bool:
        return self._registration(vm_id) is not None

    def _guest_kind(self, template_id: str, template_version: str) -> str:
        data = raise_for_envelope(self.manager.call("templates"), context="templates")
        for entry in data.get("templates") or []:
            if entry.get("template_id") == template_id and str(entry.get("template_version")) == str(template_version):
                return str(entry.get("guest_kind", "linux"))
        raise InvalidArgument(f"template {template_id} {template_version} is not in the board catalog")

    def _used_ips(self) -> set[str]:
        data = raise_for_envelope(self.manager.call("list", {"lifecycle": "all"}), context="list all")
        used = set()
        for view in data.get("vms") or []:
            ip = (view.get("configuration") or {}).get("ipv4")
            if ip:
                used.add(str(ip))
        return used

    def _free_ip(self, exclude: set[str]) -> str:
        used = self._used_ips() | exclude
        for host in self._ip_pool:
            candidate = f"{IP_PREFIX}{host}"
            if candidate not in used:
                return candidate
        raise InsufficientResources("ipv4", 1, 0)

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
        self, vm_id: UUID, template_id: str, template_version: str, *, vcpus: int, memory_mib: int
    ) -> InstancePaths:
        existing = self._registration(vm_id)
        if existing is not None:
            mismatch = self._spec_mismatch(existing, template_id, template_version, vcpus, memory_mib)
            if mismatch:
                raise VMExists(
                    f"vm {vm_id} is registered with a different spec ({', '.join(mismatch)}); same vm_id must keep the same spec"
                )
            return self._paths(existing)

        name = instance_name(template_id, vm_id)
        spec: dict[str, Any] = {
            "vm_id": str(vm_id),  # 팀 결정: Spring 발급 UUID 를 registry·Xen UUID 로 그대로 쓴다
            "name": name,
            "template_id": template_id,
            "template_version": template_version,
            "vcpus": vcpus,
            "memory_mib": memory_mib,
        }
        needs_ip = self._guest_kind(template_id, template_version) == "linux"
        tried: set[str] = set()
        for _ in range(self._max_ip_retries if needs_ip else 1):
            if needs_ip:
                spec["ipv4"] = self._free_ip(tried)
            envelope = self.manager.call("create", spec, execute=True, idempotency_key=f"create-{vm_id}")
            error = envelope.get("error") or {}
            if not envelope.get("ok") and error.get("code") == "INVALID_SPEC":
                # 이름·템플릿·정수 범위는 위에서 먼저 검증했으므로, 남는 INVALID_SPEC 원인은 플랫폼이 아직 모르는 vm_id 키다.
                # (현재 플랫폼 메시지: "name, template_id, template_version, vcpus and memory_mib required")
                raise NotSupportedByPlatform(
                    "platform create does not accept a caller-supplied vm_id yet (xenova_management spec['vm_id'])",
                    detail=str(error.get("message")),
                )
            if not envelope.get("ok") and error.get("code") == "IP_CONFLICT" and needs_ip:
                tried.add(spec["ipv4"])
                continue
            data = raise_for_envelope(envelope, context=f"create {name}")
            view = (data.get("vm") or data.get("current") or data.get("original_result") or {}) if isinstance(data, dict) else {}
            if not view:
                view = self._registration(vm_id) or {"vm_id": str(vm_id), "name": name}
            if str(view.get("vm_id")) != str(vm_id):
                raise DriverError(
                    "platform registered a different vm_id than requested; Spring-issued vm_id must be honoured",
                    detail=f"requested={vm_id} registered={view.get('vm_id')}",
                )
            return self._paths(view)
        raise InvalidState(f"could not allocate a free IP for {name} after {self._max_ip_retries} attempts")

    def delete_instance(self, vm_id: UUID) -> None:
        envelope = self.manager.call("retire", {"vm_id": str(vm_id)}, execute=True, idempotency_key=f"retire-{vm_id}")
        if not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "VM_NOT_FOUND":
            raise VMNotFound(f"instance {vm_id} not registered")
        raise_for_envelope(envelope, context=f"retire {vm_id}")
