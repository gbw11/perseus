"""VM 엔드포인트. 동기 함수(def)로 두어 FastAPI 가 스레드풀에서 실행한다 — xl subprocess 가 이벤트 루프를 막지 않게(계약 11장)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ..models import CreateVmRequest, Error, Vm
from ..service import VmService
from . import get_service

router = APIRouter(tags=["VMs"])

_ERR = {"model": Error}
# 401 과 드라이버·플랫폼 실패는 모든 엔드포인트에서 날 수 있다 (code 와 HTTP 상태 1:1, README "오류 코드와 HTTP 상태")
_DRIVER_ERRS = {
    401: {"model": Error, "description": "Unauthorized: Bearer 서비스 토큰 없음·불일치 (미들웨어)"},
    500: {"model": Error, "description": "DriverError: 분류되지 않은 실행 오류"},
    503: {"model": Error, "description": "PlatformUnavailable: registry·저장소·Xen 일시 장애 (재시도 가능)"},
    504: {"model": Error, "description": "DriverTimeout: xl·플랫폼 호출 시한 초과"},
}
# 명령(생성·정지·보관)은 xen 모드나 플랫폼 미지원 동작에서 501 을 더 낸다
_COMMAND_ERRS = {**_DRIVER_ERRS, 501: {"model": Error, "description": "NotSupported: 드라이버·플랫폼이 제공하지 않는 동작"}}


@router.get("/vms", operation_id="listVms", summary="Xen 에 존재하는 도메인 목록", response_model=list[Vm], responses=_DRIVER_ERRS)
def list_vms(service: VmService = Depends(get_service)) -> list[Vm]:
    return [d.to_vm() for d in service.list()]


@router.post(
    "/vms",
    operation_id="createVm",
    summary="VM 생성 또는 재시작",
    status_code=201,
    response_model=Vm,
    responses={400: _ERR, 409: _ERR, 507: _ERR, **_COMMAND_ERRS},
)
def create_vm(request: CreateVmRequest, service: VmService = Depends(get_service)) -> Vm:
    return service.create(request).to_vm()


@router.get("/vms/{vm_id}", operation_id="getVm", summary="VM 단건 조회", response_model=Vm, responses={400: _ERR, 404: _ERR, **_DRIVER_ERRS})
def get_vm(vm_id: UUID, service: VmService = Depends(get_service)) -> Vm:
    return service.get(vm_id).to_vm()


@router.delete(
    "/vms/{vm_id}",
    operation_id="deleteVm",
    summary="정지된 VM 숨김 보관",
    status_code=204,
    response_class=Response,
    responses={400: _ERR, 404: _ERR, 409: _ERR, **_COMMAND_ERRS},
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
    responses={400: _ERR, **_COMMAND_ERRS},
)
def stop_vm(vm_id: UUID, service: VmService = Depends(get_service)) -> Response:
    service.stop(vm_id)
    return Response(status_code=204)
