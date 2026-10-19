"""하이퍼바이저 추상화 인터페이스 (S15P31E110-31).

Xen 드라이버와 Mock 드라이버가 같은 인터페이스를 구현하고, 상위 계층(VmService, API)은 드라이버 종류를 모른다.

설계 원칙
- 드라이버는 "지금 Xen 에 무엇이 떠 있나"만 다룬다. VM 명세(name·template)는 Spring 이 보유한다(계약 1.2).
- 디스크·부팅 파일 배치는 Provisioner 가 맡는다. 드라이버는 Provisioner 가 준 경로로 도메인을 만들 뿐이다.
  실제 디스크 생성은 P2(플랫폼) 쪽 인스턴스 생성 기능을 연결할 때 구현한다. 여기서는 인터페이스만 둔다.
- 모든 메서드는 동기(blocking)다. xl 은 subprocess 이므로 API 계층이 스레드풀에서 호출한다(계약 11장).
- shutdown/destroy 는 멱등이다. 없는 VM 에 대해 예외를 내지 않는다(계약 4.3, 4.4).

v1 계약 범위 밖 메서드(reboot, set_vcpus, set_memory, pin_vcpu, set_sched_weight, get_stats)는
기본 구현이 NotImplementedError 를 낸다. S15P31E110-208, -101, -112 에서 채운다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from ..errors import VMNotFound
from ..models import Vm, VmState


@dataclass(frozen=True)
class Domain:
    """Xen 에 존재하는 도메인 하나. Domain-0 은 드라이버가 목록에서 제외한다."""

    vm_id: UUID
    state: VmState
    vcpus: int
    memory_mib: int
    domid: int | None = None
    name: str | None = None

    def to_vm(self) -> Vm:
        return Vm(vm_id=self.vm_id, state=self.state, vcpus=self.vcpus, memory_mib=self.memory_mib)


@dataclass(frozen=True)
class InstancePaths:
    """Provisioner 가 준비한 인스턴스의 파일 위치. 경로는 전부 vm_id 에서 파생한다(계약 4.2)."""

    vm_id: UUID
    root: Path
    kernel: Path | None = None
    ramdisk: Path | None = None
    disk: Path | None = None


@dataclass(frozen=True)
class CreateSpec:
    vm_id: UUID
    vcpus: int
    memory_mib: int
    instance: InstancePaths


@dataclass(frozen=True)
class HostInfo:
    """xl info 요약. InsufficientResources 판정 근거(계약 12장: free_memory 기준)."""

    free_memory_mib: int
    total_memory_mib: int
    nr_cpus: int


class Driver(ABC):
    name: ClassVar[str] = "base"

    # ---- v1 계약 (생명주기·조회·상태) ----

    @abstractmethod
    def list_domains(self) -> list[Domain]:
        """Xen 에 존재하는 도메인 전부. Domain-0 은 제외한다."""

    def get_domain(self, vm_id: UUID) -> Domain:
        for domain in self.list_domains():
            if domain.vm_id == vm_id:
                return domain
        raise VMNotFound(f"domain {vm_id} not found")

    def has_domain(self, vm_id: UUID) -> bool:
        return any(d.vm_id == vm_id for d in self.list_domains())

    @abstractmethod
    def create(self, spec: CreateSpec) -> Domain:
        """도메인을 만들어 실행 상태로 둔다. 이미 있으면 VMExists."""

    @abstractmethod
    def shutdown(self, vm_id: UUID, *, grace_s: float) -> None:
        """정상 종료 요청 후 grace_s 안에 소멸하지 않으면 강제 종료로 전환한다. 디스크 보존. 멱등."""

    @abstractmethod
    def destroy(self, vm_id: UUID) -> None:
        """강제 종료. 디스크는 건드리지 않는다(디스크 삭제는 Provisioner). 멱등."""

    @abstractmethod
    def host_info(self) -> HostInfo:
        """가용 메모리·코어 수."""

    @abstractmethod
    def health(self) -> bool:
        """하이퍼바이저가 응답하면 True. /healthz 가 503 을 구분하는 근거."""

    # ---- v2 (별도 계약 작성 후) ----

    def reboot(self, vm_id: UUID) -> None:  # S15P31E110-208
        raise NotImplementedError("reboot: S15P31E110-208")

    def set_vcpus(self, vm_id: UUID, vcpus: int) -> None:  # S15P31E110-101
        raise NotImplementedError("set_vcpus: S15P31E110-101")

    def set_memory(self, vm_id: UUID, memory_mib: int) -> None:  # S15P31E110-101
        raise NotImplementedError("set_memory: S15P31E110-101")

    def pin_vcpu(self, vm_id: UUID, vcpu: int, pcpus: list[int]) -> None:  # S15P31E110-101
        raise NotImplementedError("pin_vcpu: S15P31E110-101")

    def set_sched_weight(self, vm_id: UUID, weight: int) -> None:  # S15P31E110-101
        raise NotImplementedError("set_sched_weight: S15P31E110-101")

    def get_stats(self, vm_id: UUID) -> dict[str, Any]:  # S15P31E110-112
        raise NotImplementedError("get_stats: S15P31E110-112")


class Provisioner(ABC):
    """템플릿 → 인스턴스(디스크·커널·initrd) 준비. 실제 구현은 플랫폼 관리 계층(xenova_management)과 연결한다."""

    @abstractmethod
    def ensure_instance(
        self, vm_id: UUID, template_id: str, template_version: str, *, vcpus: int, memory_mib: int
    ) -> InstancePaths:
        """등록(디스크)이 있으면 재사용하고 없으면 템플릿에서 만든다(계약 4.2). 생성 명세는 registry 에 기록된다."""

    @abstractmethod
    def instance_exists(self, vm_id: UUID) -> bool: ...

    @abstractmethod
    def delete_instance(self, vm_id: UUID) -> None:
        """디스크를 포함한 인스턴스 삭제. 없으면 아무것도 하지 않는다(계약 4.4)."""
