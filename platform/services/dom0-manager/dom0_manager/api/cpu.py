"""CPU 배치 조회 (S15P31E110-353, 계약 4.11). 조회 전용이며 정책·affinity·VM 을 바꾸지 않는다."""

from uuid import UUID

from fastapi import APIRouter, Depends

from ..models import CpuLayout, Error
from ..service import VmService
from . import get_service
from .vms import _COMMAND_ERRS

router = APIRouter(tags=["Resources"])

_DESCRIPTION = (
    "플랫폼 cpu-layout. 200 은 조회 성공이지 배치 준수가 아니다: host.ready·affinity_matches·exclusive_verified·issues 를 함께 본다. "
    "null 은 해당 없음·모름(정지·공유·정책 미설정)이며 성공이 아니다. 긴 변경 작업이 플랫폼 잠금을 쥐고 있으면 기다리지 않고 503(재시도 가능)"
)


@router.get("/cpu-layout", operation_id="getCpuLayout", summary="전체 VM 의 CPU 배치(정책·실제 Xen)", description=_DESCRIPTION,
            response_model=CpuLayout, responses=_COMMAND_ERRS)
def get_cpu_layout(service: VmService = Depends(get_service)) -> CpuLayout:
    return service.cpu_layout()


@router.get("/vms/{vm_id}/cpu-layout", operation_id="getVmCpuLayout", summary="한 VM 의 CPU 배치(정책·실제 Xen)", description=_DESCRIPTION,
            response_model=CpuLayout, responses={400: {"model": Error}, 404: {"model": Error}, **_COMMAND_ERRS})
def get_vm_cpu_layout(vm_id: UUID, service: VmService = Depends(get_service)) -> CpuLayout:
    return service.cpu_layout(vm_id)
