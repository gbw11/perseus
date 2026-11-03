"""Application 계층. 생성·종료·삭제의 순서와 락을 관리한다(계약 11장).

- vm_id 단위 락: 같은 VM 의 create/stop/delete 를 직렬화한다.
- 전역 자원 락: 자원 확인과 생성 사이를 묶어, 두 VM 이 같은 가용 메모리를 보고 둘 다 만들어지는 것을 막는다.
- 검사 순서는 vcpus → memory_mib 고정, 먼저 걸린 쪽 하나만 보고한다.
- Domain-0 보호: Xen 이 Domain-0 에 주는 nil UUID 를 대상으로 한 호출은 드라이버에 닿기 전에 400 으로 거부한다.
  목록에서 Domain-0 을 빼는 것은 드라이버 몫이고, 이 가드는 백엔드가 막는 것과 별개로 모든 경로를 막는다.
- 요청 예산(X-Request-Budget-Ms)이 있으면 vm_id 락·자원 락 대기도 그 안에서 끝난다. 다 쓰면 504 로 돌려주고 뒤늦게 제출하지 않는다.
  등록·시작·정지·보관 직전에는 드라이버 종류와 무관하게 여기서 한 번 더 확인한다(mock 도 실보드와 같은 의미가 되게).
- 키 있는 정지(request_stop)는 작업을 추적하는 드라이버(platform)에서 vm_id 락·선행 조회 없이 바로 하위 수락으로 간다.
  긴 start 가 같은 VM 의 락을 쥐어도 정지 의도는 먼저 받아들여진다(실제 실행 순서는 플랫폼 실행 잠금이 정한다).
- 재부팅(S15P31E110-208): 키 유무와 관계없이 신규 재부팅은 실행 중인 도메인만 받는다(없으면 404, 실행 중이 아니면 409). 키 있으면
  같은 키의 원 작업을 먼저 조회해, 있으면 재전달로 보고 상태 검사 없이 하위로 보낸다. 재부팅 중에는 도메인이 잠깐 없어 상태 검사가
  재전달을 409 로 막기 때문이다. 키 없는 재부팅만 vm_id 락 안에서 동기로 기다린다
"""

from __future__ import annotations

import threading
from collections import defaultdict
from contextlib import contextmanager
from uuid import UUID

from . import budget as request_budget
from .config import Settings
from .drivers.base import CreateSpec, Domain, Driver, Provisioner, StatsSample
from .errors import InsufficientResources, InvalidArgument, InvalidState, OperationNotFound, VMExists, VMNotFound
from .models import ControlOperation, ControlStatus, CpuLayout, CreateVmRequest, VmState


DOM0_UUID = UUID(int=0)  # xl list -l 에서 Domain-0 의 uuid 는 00000000-0000-0000-0000-000000000000 이다


def reject_dom0(vm_id: UUID) -> None:
    """Domain-0 은 관리 대상이 아니다. dom0 가 멈추면 제어 계층과 모든 게스트가 함께 멈추므로 어떤 경로로도 통과시키지 않는다."""
    if vm_id == DOM0_UUID:
        raise InvalidArgument("Domain-0 (nil UUID) is not a managed VM")


class VmService:
    def __init__(self, driver: Driver, provisioner: Provisioner, settings: Settings) -> None:
        self._driver = driver
        self._provisioner = provisioner
        self._settings = settings
        self._vm_locks: dict[UUID, threading.Lock] = defaultdict(threading.Lock)
        self._vm_locks_guard = threading.Lock()
        self._resource_lock = threading.Lock()
        self.observations = None

    @property
    def driver(self) -> Driver:
        return self._driver

    @contextmanager
    def _vm_lock(self, vm_id: UUID):
        with self._vm_locks_guard:
            lock = self._vm_locks[vm_id]
        request_budget.acquire(lock, f"vm {vm_id}")
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def _resource_guard(self):
        request_budget.acquire(self._resource_lock, "host resource check")
        try:
            yield
        finally:
            self._resource_lock.release()

    # ---- 조회 ----

    def list(self) -> list[Domain]:
        if self.observations is not None:
            return self.observations.list_domains()
        return self._driver.list_domains()

    def get(self, vm_id: UUID) -> Domain:
        reject_dom0(vm_id)
        return self._driver.get_domain(vm_id)

    def health(self) -> bool:
        return self._driver.health()

    def stats(self) -> StatsSample:
        """xentop 원시값(계약 2.4). 생명주기 락과 무관한 조회다."""
        return self._driver.collect_stats()

    # ---- 명령 ----

    def create(self, request: CreateVmRequest) -> Domain:
        reject_dom0(request.vm_id)
        with self._vm_lock(request.vm_id):
            if self._driver.has_domain(request.vm_id):
                raise VMExists(f"domain {request.vm_id} already exists")
            with self._resource_guard():
                host = self._driver.host_info()
                if request.vcpus > host.nr_cpus:
                    raise InsufficientResources("vcpus", request.vcpus, host.nr_cpus)
                if request.memory_mib > host.free_memory_mib:
                    raise InsufficientResources("memory_mib", request.memory_mib, host.free_memory_mib)
                request_budget.ensure(f"registering vm {request.vm_id}")
                instance = self._provisioner.ensure_instance(
                    request.vm_id,
                    request.template_id,
                    request.template_version,
                    name=request.name,
                    vcpus=request.vcpus,
                    memory_mib=request.memory_mib,
                )
                spec = CreateSpec(
                    vm_id=request.vm_id,
                    vcpus=request.vcpus,
                    memory_mib=request.memory_mib,
                    instance=instance,
                )
                request_budget.ensure(f"starting vm {request.vm_id}")
                return self._driver.create(spec)

    def stop(self, vm_id: UUID) -> None:
        reject_dom0(vm_id)
        with self._vm_lock(vm_id):
            request_budget.ensure(f"stopping vm {vm_id}")
            self._driver.shutdown(vm_id, grace_s=self._settings.shutdown_grace_s)

    def request_stop(self, vm_id: UUID, key: str) -> ControlOperation | None:
        """키 있는 정지. 완료 또는 접수(pending) 상태를 돌려주고, 등록이 없으면 None.

        작업 기록이 없는 드라이버(mock·xen)는 기존 동기 정지를 하고 완료로 답한다(operation_id 없음, 조회 불가).
        """
        reject_dom0(vm_id)
        if self._driver.tracks_control_operations:
            request_budget.ensure(f"stopping vm {vm_id}")
            return self._driver.request_stop(vm_id, key=key)
        self.stop(vm_id)
        return ControlOperation(vm_id=vm_id, action="stop", request_key=key, status=ControlStatus.completed, accepted=True)

    def reboot(self, vm_id: UUID) -> None:
        reject_dom0(vm_id)
        with self._vm_lock(vm_id):
            self._require_running(vm_id)
            request_budget.ensure(f"rebooting vm {vm_id}")
            self._driver.reboot(vm_id)

    def request_reboot(self, vm_id: UUID, key: str) -> ControlOperation:
        """키 있는 재부팅. 신규 요청은 키 없는 재부팅과 같은 전제(실행 중)를 본다. 같은 키의 원 작업이 있으면 재전달로 보고 상태를 보지 않는다.
        작업 기록이 없는 드라이버(mock·xen)는 동기 재부팅 뒤 완료로 답한다(operation_id 없음, 조회 불가)."""
        reject_dom0(vm_id)
        if self._driver.tracks_control_operations:
            if not self._has_original(vm_id, key):
                self._require_running(vm_id)
            request_budget.ensure(f"rebooting vm {vm_id}")
            return self._driver.request_reboot(vm_id, key=key)
        self.reboot(vm_id)
        return ControlOperation(vm_id=vm_id, action="reboot", request_key=key, status=ControlStatus.completed, accepted=True)

    def control_operation(self, vm_id: UUID, key: str) -> ControlOperation:
        reject_dom0(vm_id)
        return self._driver.control_operation(vm_id, key=key)

    def cpu_layout(self, vm_id: UUID | None = None) -> CpuLayout:
        """CPU 배치 조회(S15P31E110-353). 조회만 하므로 vm_id 락을 쓰지 않는다."""
        if vm_id is not None:
            reject_dom0(vm_id)
        return self._driver.cpu_layout(vm_id)

    def _require_running(self, vm_id: UUID) -> None:
        """재부팅 전제: 등록이 있고(없으면 404) 도메인이 running 이다(꺼짐·paused·crashed·stopping 은 409). 하위 reboot 는 도메인
        존재만 확인하므로 이 검사가 키 유무와 관계없이 같은 의미를 만든다."""
        if not self._provisioner.instance_exists(vm_id):
            raise VMNotFound(f"instance {vm_id} not registered")
        try:
            domain = self._driver.get_domain(vm_id)
        except VMNotFound:
            raise InvalidState(f"vm {vm_id} is not running; start it instead of rebooting") from None
        if domain.state != VmState.running:
            raise InvalidState(f"vm {vm_id} is {domain.state.value}; reboot needs a running domain (stop it, then start)")

    def _has_original(self, vm_id: UUID, key: str) -> bool:
        """같은 키의 원 제어 작업이 있는가(읽기만). 없다는 확인(OperationNotFound)만 신규 요청이다. 조회를 모르는 옛 Manager(501)나
        다른 조회 실패는 그대로 올린다: 신규·재전달을 가르지 못한 채 보내면 진행 중인 재부팅의 재전달을 409 로 거절할 수 있다."""
        try:
            self._driver.control_operation(vm_id, key=key)
        except OperationNotFound:
            return False
        return True

    def delete(self, vm_id: UUID) -> None:
        """계약 v1.1: 실행 중이면 409, 등록이 없으면 404, 정지된 VM 은 숨김 보관(retire). 강제 종료는 하지 않는다."""
        reject_dom0(vm_id)
        with self._vm_lock(vm_id):
            if self._driver.has_domain(vm_id):
                raise InvalidState(f"domain {vm_id} is running; stop it before delete")
            if not self._provisioner.instance_exists(vm_id):
                raise VMNotFound(f"instance {vm_id} not registered")
            request_budget.ensure(f"retiring vm {vm_id}")
            self._provisioner.delete_instance(vm_id)
