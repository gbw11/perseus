from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..models import Health, HealthStatus
from ..service import VmService
from . import get_service

router = APIRouter(tags=["Health"])


@router.get(
    "/healthz",
    operation_id="healthz",
    summary="dom0-manager 와 Xen 응답 여부",
    response_model=Health,
    responses={503: {"model": Health, "description": "Xen 이 응답하지 않음"}},
)
def healthz(service: VmService = Depends(get_service)):
    if service.health():
        return Health(status=HealthStatus.ok)
    return JSONResponse(status_code=503, content={"status": HealthStatus.xen_unavailable.value})
