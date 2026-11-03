"""VM 엔드포인트. 동기 함수(def)로 두어 FastAPI 가 스레드풀에서 실행한다 — xl subprocess 가 이벤트 루프를 막지 않게(계약 11장)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse

from ..errors import InvalidArgument
from ..models import ControlOperation, ControlStatus, CreateVmRequest, Error, Vm
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

_KEY_MAX = 200


def _request_key(value: str, name: str) -> str:
    """Idempotency-Key·request_key: 하위 Manager 키와 같은 1..200 바이트. HTTP 헤더로 오가므로 ASCII 가시 문자만 받는다."""
    if not 1 <= len(value) <= _KEY_MAX or not all("!" <= ch <= "~" for ch in value):
        raise InvalidArgument(f"{name} must be 1..{_KEY_MAX} visible ASCII characters")
    return value


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
    responses={
        200: {"model": ControlOperation, "description": "Idempotency-Key 가 있을 때: 원 작업 완료(재전달 포함)"},
        202: {"model": ControlOperation, "description": "Idempotency-Key 가 있을 때: 정지 의도를 내구 수락했고 실행이 남음. 같은 키로 재전달·조회"},
        204: {"description": "키 없음: 정지 완료. 키 유무와 무관하게 등록이 없으면 정지 목표 달성(작업 기록 없음)"},
        400: _ERR,
        409: _ERR,
        **_COMMAND_ERRS,
    },
)
def stop_vm(
    vm_id: UUID,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key",
        description="최초 전송 전에 정한 요청 키. 재전송·조회에 같은 값을 쓴다. 없으면 기존 동기 정지(204)",
    ),
    service: VmService = Depends(get_service),
) -> Response:
    if idempotency_key is None:
        service.stop(vm_id)
        return Response(status_code=204)
    operation = service.request_stop(vm_id, _request_key(idempotency_key, "Idempotency-Key"))
    if operation is None:
        return Response(status_code=204)
    status = 202 if operation.status == ControlStatus.pending else 200
    return JSONResponse(status_code=status, content=operation.model_dump(mode="json"))


@router.post(
    "/vms/{vm_id}/reboot",
    operation_id="rebootVm",
    summary="정상 재부팅 (같은 디스크로 새 실행)",
    status_code=204,
    response_class=Response,
    responses={
        200: {"model": ControlOperation, "description": "Idempotency-Key 가 있을 때: 원 작업 완료(재전달 포함). 새 실행이 관측됨"},
        202: {"model": ControlOperation, "description": "Idempotency-Key 가 있을 때: 재부팅을 내구 수락했고 실행이 남음. 같은 키로 재전달·조회"},
        204: {"description": "키 없음: 재부팅 완료(새 실행이 관측됨)"},
        400: _ERR,
        404: _ERR,
        409: _ERR,
        **_COMMAND_ERRS,
    },
)
def reboot_vm(
    vm_id: UUID,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key",
        description="최초 전송 전에 정한 요청 키. 재전송·조회에 같은 값을 쓴다. 없으면 동기 재부팅(204)",
    ),
    service: VmService = Depends(get_service),
) -> Response:
    if idempotency_key is None:
        service.reboot(vm_id)
        return Response(status_code=204)
    operation = service.request_reboot(vm_id, _request_key(idempotency_key, "Idempotency-Key"))
    status = 202 if operation.status == ControlStatus.pending else 200
    return JSONResponse(status_code=status, content=operation.model_dump(mode="json"))


@router.get(
    "/vms/{vm_id}/control-operations",
    operation_id="getControlOperation",
    summary="요청 키로 원 제어 작업 조회 (실행하지 않음)",
    response_model=ControlOperation,
    responses={400: _ERR, 404: _ERR, **_COMMAND_ERRS},
)
def get_control_operation(
    vm_id: UUID,
    request_key: str = Query(description="최초 제출 때 보낸 Idempotency-Key"),
    service: VmService = Depends(get_service),
) -> ControlOperation:
    return service.control_operation(vm_id, _request_key(request_key, "request_key"))
