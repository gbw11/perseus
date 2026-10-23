"""통계 엔드포인트 (S15P31E110-112, 계약 4.6). 동기 함수라 스레드풀에서 실행된다."""

from fastapi import APIRouter, Depends

from ..models import StatsResponse
from ..service import VmService
from . import get_service
from .vms import _DRIVER_ERRS

router = APIRouter(tags=["Stats"])


@router.get(
    "/stats",
    operation_id="getStats",
    summary="실행 중인 VM 의 xentop 원시값",
    response_model=StatsResponse,
    responses=_DRIVER_ERRS,
)
def get_stats(service: VmService = Depends(get_service)) -> StatsResponse:
    sample = service.stats()
    return StatsResponse(sampled_at=sample.sampled_at, vms=[d.to_model() for d in sample.domains])
