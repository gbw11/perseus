"""Xen 드라이버 (S15P31E110-36 의 조회 골격).

이번 단계는 읽기 전용이다: list/get/host_info/health. create/shutdown/destroy 는 S15P31E110-115 에서 구현한다.

vm_id ↔ Xen 도메인 매핑: 플랫폼 도구(xenova_vm.py)가 xl 설정에 `uuid = <vm_id>` 를 기록하고 `xl shutdown <uuid>` 로
도메인을 지정하므로, 여기서도 `xl list -l` 의 config.c_info.uuid 를 vm_id 로 쓴다(계약 13장 1안).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ..errors import DriverError, DriverTimeout
from .base import CreateSpec, Domain, Driver, HostInfo, InstancePaths, Provisioner
from .xl import Xl, map_state

DOM0_ID = 0
DOM0_NAME = "Domain-0"


class XenDriver(Driver):
    name = "xen"

    def __init__(self, xl: Xl) -> None:
        self._xl = xl

    # ---- 조회 ----

    def list_domains(self) -> list[Domain]:
        rows = self._xl.list()
        long_by_domid = {entry.get("domid"): entry for entry in self._xl.list_long()}
        domains: list[Domain] = []
        for row in rows:
            if row.domid == DOM0_ID or row.name == DOM0_NAME:
                continue  # Domain-0 은 관리 대상이 아니다. domid 0 또는 이름 Domain-0 둘 중 하나만 맞아도 뺀다
            entry = long_by_domid.get(row.domid)
            uuid_text = ((entry or {}).get("config") or {}).get("c_info", {}).get("uuid")
            if not uuid_text:
                raise DriverError(f"domain {row.name} (domid {row.domid}) has no uuid in xl list -l")
            try:
                vm_id = UUID(uuid_text)
            except ValueError as exc:
                raise DriverError(f"domain {row.name} has non-UUID uuid", detail=uuid_text[:80]) from exc
            domains.append(
                Domain(
                    vm_id=vm_id,
                    state=map_state(row.flags),
                    vcpus=row.vcpus,
                    memory_mib=row.mem_mib,
                    domid=row.domid,
                    name=row.name,
                )
            )
        return domains

    def host_info(self) -> HostInfo:
        return self._xl.info()

    def health(self) -> bool:
        try:
            self._xl.info()
        except (DriverTimeout, DriverError):
            return False
        return True

    # ---- 명령: S15P31E110-115 ----

    def create(self, spec: CreateSpec) -> Domain:
        raise NotImplementedError("XenDriver.create: S15P31E110-115")

    def shutdown(self, vm_id: UUID, *, grace_s: float) -> None:
        raise NotImplementedError("XenDriver.shutdown: S15P31E110-115")

    def destroy(self, vm_id: UUID) -> None:
        raise NotImplementedError("XenDriver.destroy: S15P31E110-115")


class UnavailableProvisioner(Provisioner):
    """P2(플랫폼) 인스턴스 생성 기능을 연결하기 전의 자리 표시자. 디스크를 만들거나 지우지 않는다."""

    def __init__(self, instance_root: str) -> None:
        self._root = Path(instance_root)

    def ensure_instance(
        self, vm_id: UUID, template_id: str, template_version: str, *, name: str, vcpus: int, memory_mib: int
    ) -> InstancePaths:
        raise NotImplementedError("Provisioner: xen 모드는 조회 전용. 생성은 XENOVA_DRIVER=platform 에서 지원한다")

    def instance_exists(self, vm_id: UUID) -> bool:
        return (self._root / str(vm_id)).is_dir()

    def delete_instance(self, vm_id: UUID) -> None:
        raise NotImplementedError("Provisioner: xen 모드는 조회 전용. 보관(retire)은 XENOVA_DRIVER=platform 에서 지원한다")
