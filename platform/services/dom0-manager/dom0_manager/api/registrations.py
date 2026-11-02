"""등록 metadata 조회와 UUID 단건 Spring 통지. VM 생성·제어와 독립된 경로다."""
from __future__ import annotations

from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt
from typing import Literal

from .. import budget
from ..errors import InvalidArgument, NotSupportedByPlatform, PlatformUnavailable
from ..models import Error

router = APIRouter(prefix="/vm-registrations", tags=["VM registrations"])


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    vm_id: str
    name: str
    guest_kind: Literal["linux", "zephyr"]
    template_id: str
    template_version: str | None
    vcpus: StrictInt
    memory_mib: StrictInt
    assigned_ipv4: str | None


class Notification(BaseModel):
    vm_id: str
    request_id: str
    state: str
    confirmed: bool
    http_status: int | None = None
    upstream_code: str | None = None
    cause_type: str | None = None


async def authenticated_registration(request: Request):
    # 기존 mock 개발 앱은 무인증을 허용하지만 이 등록 API에는 익명 fallback이 없다.
    if not request.app.state.settings.service_token:
        raise PlatformUnavailable("registration API requires a service token")
    async for chunk in request.stream():
        if chunk:
            raise InvalidArgument("registration requests do not accept a body")
    if request.query_params:
        raise InvalidArgument("registration requests accept only the UUID path")


def read(request, vm_id=None):
    callback = getattr(request.app.state.driver, "registrations", None)
    if not callable(callback):
        raise NotSupportedByPlatform("registered metadata requires the platform driver")
    return callback(vm_id)


@router.get("", response_model=list[Registration], operation_id="listVmRegistrations",
            responses={code:{"model":Error} for code in (400,401,500,501,503,504)},
            dependencies=[Depends(authenticated_registration)])
def registrations(request: Request):
    return read(request)


@router.post("/{vm_id}/notify", response_model=Notification, response_model_exclude_none=True,
             responses={**{code:{"model":Error} for code in (400,401,404,500,501)},
                        502:{"model":Notification},
                        **{code:{"model":Notification | Error} for code in (503,504)}},
             operation_id="notifyVmRegistration", dependencies=[Depends(authenticated_registration)])
def notify(vm_id: UUID, request: Request):
    """조회와 송신이 같은 호출 예산을 쓴다. 응답 유실 후 자동 재송신하지 않는다."""
    token = None
    if budget.current() is None:
        token = budget.start(15000, reserve_s=request.app.state.settings.response_reserve_s)
    try:
        registration = Registration.model_validate(read(request, vm_id)).model_dump()
        current = budget.current()
        budget.ensure("registration sender")
        request_id = str(uuid4())
        envelope = {"schema_version": "1.0", "ok": True, "request_id": request_id,
                    "operation_id": None, "replayed": False,
                    "data": {"plan": True, "execute_required": True,
                             "registration": {"schema_version": "1.0", **registration}}, "error": None}
        outcome = request.app.state.registration_sender.send(envelope, current.deadline - current.reserve_s)
        body = {"vm_id": str(vm_id), "request_id": request_id, **outcome}
        state = outcome["state"]
        status = (200 if outcome["confirmed"] else
                  502 if state in ("rejected", "redirect-refused", "response-invalid", "response-unconfirmed", "peer-verification-failed") else
                  504 if state in ("delivery-unknown", "budget-exhausted") else 503)
        return JSONResponse(status_code=status, content=body)
    finally:
        if token is not None:
            budget.reset(token)
