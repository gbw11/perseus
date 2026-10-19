"""Mock 드라이버 (S15P31E110-35). Xen 없이 API 흐름·복구 엔진 개발에 쓴다.

- 인메모리 도메인 저장소. 생성하면 running, shutdown/destroy 하면 목록에서 사라진다(도메인 부재 = 꺼짐).
- 장애 주입: inject_crash(상태 crashed), inject_hang(정상 종료 무시 → 강제 전환 경로),
  inject_next_create("timeout"|"insufficient"), set_healthy(False) → /healthz 503.
- 가짜 메트릭: get_stats 가 누적 CPU 시간을 돌려주고 inject_load 로 부하를 올린다.
- seed_samples(): 배포판 3종 샘플 VM. vm_id 는 고정(uuid5)이라 수환·송하가 그대로 쓸 수 있다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ..errors import DriverTimeout, InsufficientResources, VMExists, VMNotFound
from ..models import VmState
from .base import CreateSpec, Domain, Driver, HostInfo, InstancePaths, Provisioner

SAMPLE_VMS: dict[str, tuple[UUID, int, int]] = {
    # name: (vm_id, vcpus, memory_mib)
    "alpine-drive": (uuid5(NAMESPACE_URL, "perseus/mock/alpine-drive"), 1, 256),
    "debian-safety": (uuid5(NAMESPACE_URL, "perseus/mock/debian-safety"), 1, 512),
    "ubuntu-infotainment": (uuid5(NAMESPACE_URL, "perseus/mock/ubuntu-infotainment"), 2, 512),
}


@dataclass
class _MockDomain:
    vm_id: UUID
    name: str
    domid: int
    vcpus: int
    memory_mib: int
    state: VmState = VmState.running
    hang: bool = False
    load: float = 0.1  # 0.0~1.0, CPU 사용률 흉내
    started_at: float = 0.0
    pins: dict[int, list[int]] = field(default_factory=dict)
    weight: int = 256
    net_rx_bytes: int = 0
    net_tx_bytes: int = 0


class MockProvisioner(Provisioner):
    def __init__(self) -> None:
        self._instances: dict[UUID, tuple[str, str]] = {}

    def ensure_instance(self, vm_id: UUID, template_id: str, template_version: str) -> InstancePaths:
        self._instances.setdefault(vm_id, (template_id, template_version))
        root = Path("/mock/instances") / str(vm_id)
        return InstancePaths(vm_id=vm_id, root=root, kernel=root / "Image", ramdisk=root / "initrd", disk=root / "root.raw")

    def instance_exists(self, vm_id: UUID) -> bool:
        return vm_id in self._instances

    def delete_instance(self, vm_id: UUID) -> None:
        self._instances.pop(vm_id, None)

    def template_of(self, vm_id: UUID) -> tuple[str, str] | None:
        return self._instances.get(vm_id)


class MockDriver(Driver):
    name = "mock"

    def __init__(
        self,
        *,
        host_cpus: int = 4,
        total_memory_mib: int = 6500,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host_cpus = host_cpus
        self._total_memory_mib = total_memory_mib
        self._clock = clock
        self._domains: dict[UUID, _MockDomain] = {}
        self._next_domid = 1
        self._next_create_fault: str | None = None
        self._healthy = True
        self._lock = threading.RLock()
        self.events: list[tuple[str, UUID, dict[str, Any]]] = []

    # ---- v1 ----

    def list_domains(self) -> list[Domain]:
        with self._lock:
            return [self._to_domain(d) for d in sorted(self._domains.values(), key=lambda d: d.domid)]

    def create(self, spec: CreateSpec) -> Domain:
        with self._lock:
            if spec.vm_id in self._domains:
                raise VMExists(f"domain {spec.vm_id} already exists")
            fault, self._next_create_fault = self._next_create_fault, None
            if fault == "timeout":
                raise DriverTimeout("xl create timed out (injected)")
            if fault == "insufficient":
                raise InsufficientResources("memory_mib", spec.memory_mib, 0)
            free = self._free_memory_mib()
            if spec.memory_mib > free:
                raise InsufficientResources("memory_mib", spec.memory_mib, free)
            domain = _MockDomain(
                vm_id=spec.vm_id,
                name=f"vm-{spec.vm_id.hex[:8]}",
                domid=self._next_domid,
                vcpus=spec.vcpus,
                memory_mib=spec.memory_mib,
                started_at=self._clock(),
            )
            self._next_domid += 1
            self._domains[spec.vm_id] = domain
            self.events.append(("create", spec.vm_id, {"domid": domain.domid}))
            return self._to_domain(domain)

    def shutdown(self, vm_id: UUID, *, grace_s: float) -> None:
        with self._lock:
            domain = self._domains.get(vm_id)
            if domain is None:
                return
            if domain.hang:
                # 계약 v1.1: 정상 종료가 grace 안에 끝나지 않아도 자동 강제 종료하지 않는다. 도메인은 그대로 남는다.
                self.events.append(("shutdown", vm_id, {"forced": False, "grace_s": grace_s, "timed_out": True}))
                raise DriverTimeout(f"domain {vm_id} did not shut down within {grace_s:g}s; no automatic force")
            forced = domain.state == VmState.crashed  # crashed 는 종료 요청에 응답할 수 없어 destroy 로 처리
            del self._domains[vm_id]
            self.events.append(("shutdown", vm_id, {"forced": forced, "grace_s": grace_s}))

    def destroy(self, vm_id: UUID) -> None:
        with self._lock:
            if self._domains.pop(vm_id, None) is not None:
                self.events.append(("destroy", vm_id, {}))

    def host_info(self) -> HostInfo:
        with self._lock:
            return HostInfo(
                free_memory_mib=self._free_memory_mib(),
                total_memory_mib=self._total_memory_mib,
                nr_cpus=self._host_cpus,
            )

    def health(self) -> bool:
        return self._healthy

    # ---- v2 (Mock 은 미리 구현해 복구 엔진·리소스 제어 개발에 쓴다) ----

    def reboot(self, vm_id: UUID) -> None:
        with self._lock:
            domain = self._get(vm_id)
            domain.state = VmState.running
            domain.domid = self._next_domid
            self._next_domid += 1
            domain.started_at = self._clock()
            self.events.append(("reboot", vm_id, {"domid": domain.domid}))

    def set_vcpus(self, vm_id: UUID, vcpus: int) -> None:
        with self._lock:
            if vcpus > self._host_cpus:
                raise InsufficientResources("vcpus", vcpus, self._host_cpus)
            self._get(vm_id).vcpus = vcpus

    def set_memory(self, vm_id: UUID, memory_mib: int) -> None:
        with self._lock:
            domain = self._get(vm_id)
            free = self._free_memory_mib() + domain.memory_mib
            if memory_mib > free:
                raise InsufficientResources("memory_mib", memory_mib, free)
            domain.memory_mib = memory_mib

    def pin_vcpu(self, vm_id: UUID, vcpu: int, pcpus: list[int]) -> None:
        with self._lock:
            self._get(vm_id).pins[vcpu] = list(pcpus)

    def set_sched_weight(self, vm_id: UUID, weight: int) -> None:
        with self._lock:
            self._get(vm_id).weight = weight

    def get_stats(self, vm_id: UUID) -> dict[str, Any]:
        with self._lock:
            domain = self._get(vm_id)
            now = self._clock()
            elapsed = max(0.0, now - domain.started_at)
            return {
                "vm_id": str(vm_id),
                "state": domain.state.value,
                "vcpus": domain.vcpus,
                "memory_mib": domain.memory_mib,
                "cpu_time_s": round(elapsed * domain.load * domain.vcpus, 3),
                "net_rx_bytes": domain.net_rx_bytes,
                "net_tx_bytes": domain.net_tx_bytes,
                "sampled_at": now,
            }

    # ---- 장애·부하 주입 ----

    def inject_crash(self, vm_id: UUID) -> None:
        with self._lock:
            self._get(vm_id).state = VmState.crashed
            self.events.append(("crash", vm_id, {}))

    def inject_hang(self, vm_id: UUID) -> None:
        with self._lock:
            self._get(vm_id).hang = True

    def inject_load(self, vm_id: UUID, load: float) -> None:
        with self._lock:
            self._get(vm_id).load = max(0.0, min(1.0, load))

    def inject_next_create(self, fault: str) -> None:
        if fault not in ("timeout", "insufficient"):
            raise ValueError("fault must be 'timeout' or 'insufficient'")
        self._next_create_fault = fault

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy

    def seed_samples(self) -> None:
        for name, (vm_id, vcpus, memory_mib) in SAMPLE_VMS.items():
            if vm_id in self._domains:
                continue
            root = Path("/mock/instances") / str(vm_id)
            self.create(CreateSpec(vm_id=vm_id, vcpus=vcpus, memory_mib=memory_mib, instance=InstancePaths(vm_id, root)))
            self._domains[vm_id].name = name

    # ---- 내부 ----

    def _get(self, vm_id: UUID) -> _MockDomain:
        domain = self._domains.get(vm_id)
        if domain is None:
            raise VMNotFound(f"domain {vm_id} not found")
        return domain

    def _free_memory_mib(self) -> int:
        return self._total_memory_mib - sum(d.memory_mib for d in self._domains.values())

    @staticmethod
    def _to_domain(d: _MockDomain) -> Domain:
        return Domain(vm_id=d.vm_id, state=d.state, vcpus=d.vcpus, memory_mib=d.memory_mib, domid=d.domid, name=d.name)
