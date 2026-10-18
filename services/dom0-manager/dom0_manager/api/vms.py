"""VM 엔드포인트. 동기 함수(def)로 두어 FastAPI 가 스레드풀에서 실행한다 — xl subprocess 가 이벤트 루프를 막지 않게(계약 11장)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ..models import CreateVmRequest, Error, Vm
from ..service import VmService
from . import get_service

router = APIRouter(tags=["VMs"])

_ERR = {"model": Error}


@router.get("/vms", operation_id="listVms", summary="Xen 에 존재하는 도메인 목록", response_model=list[Vm])
def list_vms(service: VmService = Depends(get_service)) -> list[Vm]:
    return [d.to_vm() for d in service.list()]


@router.post(
    "/vms",
    operation_id="createVm",
    summary="VM 생성 또는 재시작",
    status_code=201,
    response_model=Vm,
    responses={400: _ERR, 409: _ERR, 507: _ERR},
)
def create_vm(request: CreateVmRequest, service: VmService = Depends(get_service)) -> Vm:
    return service.create(request).to_vm()


@router.get("/vms/{vm_id}", operation_id="getVm", summary="VM 단건 조회", response_model=Vm, responses={404: _ERR})
def get_vm(vm_id: UUID, service: VmService = Depends(get_service)) -> Vm:
    return service.get(vm_id).to_vm()


@router.delete(
    "/vms/{vm_id}",
    operation_id="deleteVm",
    summary="강제 종료 후 디스크 삭제",
    status_code=204,
    response_class=Response,
)
def delete_vm(vm_id: UUID, service: VmService = Depends(get_service)) -> Response:
    service.delete(vm_id)
    return Response(status_code=204)


@router.post(
    "/vms/{vm_id}/stop",
    operation_id="stopVm",
    summary="정상 종료 (디스크 보존)",
    status_code=204,
    response_class=Response,
)
def stop_vm(vm_id: UUID, service: VmService = Depends(get_service)) -> Response:
    service.stop(vm_id)
    return Response(status_code=204)
