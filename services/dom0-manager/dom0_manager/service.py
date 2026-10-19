"""Application 계층. 생성·종료·삭제의 순서와 락을 관리한다(계약 11장).

- vm_id 단위 락: 같은 VM 의 create/stop/delete 를 직렬화한다.
- 전역 자원 락: 자원 확인과 생성 사이를 묶어, 두 VM 이 같은 가용 메모리를 보고 둘 다 만들어지는 것을 막는다.
- 검사 순서는 vcpus → memory_mib 고정, 먼저 걸린 쪽 하나만 보고한다(S15P31E110-115).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from contextlib import contextmanager
from uuid import UUID

from .config import Settings
from .drivers.base import CreateSpec, Domain, Driver, Provisioner
from .errors import InsufficientResources, InvalidState, VMExists, VMNotFound
from .models import CreateVmRequest


class VmService:
    def __init__(self, driver: Driver, provisioner: Provisioner, settings: Settings) -> None:
        self._driver = driver
        self._provisioner = provisioner
        self._settings = settings
        self._vm_locks: dict[UUID, threading.Lock] = defaultdict(threading.Lock)
        self._vm_locks_guard = threading.Lock()
        self._resource_lock = threading.Lock()

    @property
    def driver(self) -> Driver:
        return self._driver

    @contextmanager
    def _vm_lock(self, vm_id: UUID):
        with self._vm_locks_guard:
            lock = self._vm_locks[vm_id]
        with lock:
            yield

    # ---- 조회 ----

    def list(self) -> list[Domain]:
        return self._driver.list_domains()

    def get(self, vm_id: UUID) -> Domain:
        return self._driver.get_domain(vm_id)

    def health(self) -> bool:
        return self._driver.health()

    # ---- 명령 ----

    def create(self, request: CreateVmRequest) -> Domain:
        with self._vm_lock(request.vm_id):
            if self._driver.has_domain(request.vm_id):
                raise VMExists(f"domain {request.vm_id} already exists")
            with self._resource_lock:
                host = self._driver.host_info()
                if request.vcpus > host.nr_cpus:
                    raise InsufficientResources("vcpus", request.vcpus, host.nr_cpus)
                if request.memory_mib > host.free_memory_mib:
                    raise InsufficientResources("memory_mib", request.memory_mib, host.free_memory_mib)
                instance = self._provisioner.ensure_instance(
                    request.vm_id,
                    request.template_id,
                    request.template_version,
                    vcpus=request.vcpus,
                    memory_mib=request.memory_mib,
                )
                spec = CreateSpec(
                    vm_id=request.vm_id,
                    vcpus=request.vcpus,
                    memory_mib=request.memory_mib,
                    instance=instance,
                )
                return self._driver.create(spec)

    def stop(self, vm_id: UUID) -> None:
        with self._vm_lock(vm_id):
            self._driver.shutdown(vm_id, grace_s=self._settings.shutdown_grace_s)

    def delete(self, vm_id: UUID) -> None:
        """계약 v1.1: 실행 중이면 409, 등록이 없으면 404, 정지된 VM 은 숨김 보관(retire). 강제 종료는 하지 않는다."""
        with self._vm_lock(vm_id):
            if self._driver.has_domain(vm_id):
                raise InvalidState(f"domain {vm_id} is running; stop it before delete")
            if not self._provisioner.instance_exists(vm_id):
                raise VMNotFound(f"instance {vm_id} not registered")
            self._provisioner.delete_instance(vm_id)
