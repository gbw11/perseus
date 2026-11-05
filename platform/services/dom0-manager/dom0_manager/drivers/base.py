"""하이퍼바이저 추상화 인터페이스 (S15P31E110-31).

Xen 드라이버와 Mock 드라이버가 같은 인터페이스를 구현하고, 상위 계층(VmService, API)은 드라이버 종류를 모른다.

설계 원칙
- 드라이버는 "지금 Xen 에 무엇이 떠 있나"만 다룬다. VM 명세는 Spring이 공급하고 Provisioner가 표시 이름·템플릿을 플랫폼 create에 전달한다.
- 디스크·부팅 파일 배치는 Provisioner 가 맡는다. 드라이버는 Provisioner 가 준 경로로 도메인을 만들 뿐이다.
  실제 디스크 생성은 P2(플랫폼) 쪽 인스턴스 생성 기능을 연결할 때 구현한다. 여기서는 인터페이스만 둔다.
- 모든 메서드는 동기(blocking)다. xl 은 subprocess 이므로 API 계층이 스레드풀에서 호출한다(계약 11장).
- shutdown/destroy 는 멱등이다. 없는 VM 에 대해 예외를 내지 않는다(계약 4.3, 4.4).

v1 계약 범위 밖 메서드(reboot, cpu_layout 등)는 기본 구현이 501 을 낸다. collect_stats 는 xentop 원시값(S15P31E110-112, 계약 2.4·4.6)이다.
정지한 VM 의 자원 변경은 실행 상태가 아니라 등록 설정을 바꾸므로 Provisioner.update_resources 다(S15P31E110-368). 실행 중 vCPU 핀·
스케줄러 가중치·메모리 변경은 플랫폼이 지원하지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from ..errors import NotSupportedByPlatform, VMNotFound
from ..models import ControlOperation, CpuLayout, ResourceUpdate, StatsSource, UnavailableReason, Vm, VmState, VmStats


@dataclass(frozen=True)
class Domain:
    """Xen 에 존재하는 도메인 하나. Domain-0 은 드라이버가 목록에서 제외한다."""

    vm_id: UUID
    state: VmState
    vcpus: int
    memory_mib: int
    domid: int | None = None
    name: str | None = None

    # Internal observation identity; deliberately absent from the public Vm schema.
    observation_id: str | None = None

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


@dataclass(frozen=True)
class DomainStats:
    """xentop 한 행을 계약 2.4 통계 객체로 옮긴 것. 측정하지 못한 필드는 None 이고 unavailable 에 사유(UnavailableReason 값)가 있다."""

    vm_id: UUID
    domid: int
    sampled_at: datetime
    state: VmState
    vcpus: int
    cpu_time_s: int
    memory_mib: int
    max_memory_mib: int | None
    net_rx_bytes: int | None
    net_tx_bytes: int | None
    disk_rd_bytes: int | None
    disk_wr_bytes: int | None
    unavailable: dict[str, str] = field(default_factory=dict)
    name: str | None = None

    def to_model(self) -> VmStats:
        return VmStats(
            vm_id=self.vm_id,
            domid=self.domid,
            source=StatsSource.xen,
            sampled_at=self.sampled_at,
            state=self.state,
            vcpus=self.vcpus,
            cpu_time_s=self.cpu_time_s,
            memory_mib=self.memory_mib,
            max_memory_mib=self.max_memory_mib,
            net_rx_bytes=self.net_rx_bytes,
            net_tx_bytes=self.net_tx_bytes,
            disk_rd_bytes=self.disk_rd_bytes,
            disk_wr_bytes=self.disk_wr_bytes,
            unavailable={k: UnavailableReason(v) for k, v in self.unavailable.items()},
        )


@dataclass(frozen=True)
class StatsSample:
    """한 번의 xentop 실행 결과. Domain-0 은 없다."""

    sampled_at: datetime
    domains: list[DomainStats]


class Driver(ABC):
    name: ClassVar[str] = "base"
    # 제어 요청을 하위 작업 기록(멱등 키·원 작업 조회)으로 추적하는가. platform 만 True. 아니면 VmService 가 동기 정지로 처리한다
    tracks_control_operations: ClassVar[bool] = False

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
        """정상 종료를 요청하고 grace_s 까지 소멸을 기다린다. 시간이 지나도 자동 강제 종료하지 않고 DriverTimeout 을 낸다(계약 v1.1).
        디스크 보존. 없는 VM 은 그냥 성공(멱등)."""

    @abstractmethod
    def destroy(self, vm_id: UUID) -> None:
        """강제 종료. 디스크는 건드리지 않는다(디스크 삭제는 Provisioner). 멱등."""

    def request_stop(self, vm_id: UUID, *, key: str) -> ControlOperation | None:
        """키 있는 정지(S15P31E110-305). tracks_control_operations 인 드라이버만 구현한다. 등록이 없으면 None."""
        raise NotSupportedByPlatform(f"{self.name} driver does not track control operations")

    def control_operation(self, vm_id: UUID, *, key: str) -> ControlOperation:
        """요청 키로 원 제어 작업 조회. 작업 기록이 없는 드라이버는 501."""
        raise NotSupportedByPlatform(f"{self.name} driver does not track control operations")

    def reboot(self, vm_id: UUID) -> None:  # S15P31E110-208
        """실행 중인 도메인을 같은 디스크로 다시 시작한다(게스트 정상 재부팅, 새 실행). 제공하지 않는 드라이버는 501.
        재부팅 명령을 제출한 뒤 새 실행을 확인하지 못하면 결과 미확정이다(기존·새 도메인의 유무를 단정하지 않는다). 자동 강제 재부팅은 없다."""
        raise NotSupportedByPlatform(f"{self.name} driver does not support reboot")

    def cpu_layout(self, vm_id: UUID | None = None) -> CpuLayout:  # S15P31E110-353
        """정책 CPU 배치와 실제 Xen hard affinity 조회. 플랫폼 CPU 정책이 없는 드라이버는 501."""
        raise NotSupportedByPlatform(f"{self.name} driver does not report CPU layout")

    def request_reboot(self, vm_id: UUID, *, key: str) -> ControlOperation:
        """키 있는 재부팅(S15P31E110-208). tracks_control_operations 인 드라이버만 구현한다."""
        raise NotSupportedByPlatform(f"{self.name} driver does not track control operations")

    @abstractmethod
    def host_info(self) -> HostInfo:
        """가용 메모리·코어 수."""

    @abstractmethod
    def health(self) -> bool:
        """하이퍼바이저가 응답하면 True. /healthz 가 503 을 구분하는 근거."""

    # ---- 통계 (S15P31E110-112) ----

    def collect_stats(self) -> StatsSample:
        """실행 중 도메인 전부의 xentop 원시값. Domain-0 제외. xentop 이 없는 드라이버는 501."""
        raise NotImplementedError("collect_stats: this driver has no xentop")


class Provisioner(ABC):
    """템플릿 → 인스턴스(디스크·커널·initrd) 준비. 실제 구현은 플랫폼 관리 계층(xenova_management)과 연결한다."""

    @abstractmethod
    def ensure_instance(
        self, vm_id: UUID, template_id: str, template_version: str, *, name: str, vcpus: int, memory_mib: int
    ) -> InstancePaths:
        """등록(디스크)이 있으면 재사용하고 없으면 템플릿에서 만든다(계약 4.2). 생성 명세는 registry 에 기록된다."""

    @abstractmethod
    def instance_exists(self, vm_id: UUID) -> bool: ...

    @abstractmethod
    def delete_instance(self, vm_id: UUID) -> None:
        """디스크를 포함한 인스턴스 삭제. 없으면 아무것도 하지 않는다(계약 4.4)."""

    def update_resources(self, vm_id: UUID, *, vcpus: int, memory_mib: int, key: str) -> ResourceUpdate:  # S15P31E110-368
        """정지한 Linux VM 의 등록 설정에서 vCPU·메모리를 바꾼다(계약 4.12). 다음 시작부터 적용되고 게스트를 시작하지 않는다.
        같은 키·같은 값은 원 결과, 같은 키·다른 요청은 409. 제공하지 않는 Provisioner 는 501."""
        raise NotSupportedByPlatform(f"{type(self).__name__} does not support resource updates")
