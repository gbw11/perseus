"""복구 이벤트 재보충 (S15P31E110-125, 계약 4.7). Recovery Worker 가 dom0 에 남긴 이벤트를 오래된 순으로 돌려준다."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Query, Request

from ..errors import PlatformUnavailable
from ..models import Error, RecoveryEvent, RecoveryEventsResponse
from ..recovery.events import EventLog, EventLogUnavailable

router = APIRouter(tags=["Recovery"])

EVENTS_FILE = "recovery-events.jsonl"


@router.get(
    "/recovery/events",
    operation_id="listRecoveryEvents",
    summary="복구 이벤트 재보충 (오래된 순)",
    response_model=RecoveryEventsResponse,
    responses={
        400: {"model": Error, "description": "InvalidArgument: after_event_id 가 UUID 가 아니거나 limit 범위 밖"},
        401: {"model": Error, "description": "Unauthorized: Bearer 서비스 토큰 없음·불일치 (미들웨어)"},
        503: {"model": Error, "description": "PlatformUnavailable: dom0 의 복구 이벤트 파일을 읽을 수 없음. 빈 목록으로 숨기지 않는다. 재시도 가능"},
    },
)
def list_recovery_events(
    request: Request,
    after_event_id: UUID | None = Query(None, description="이 이벤트 다음부터. 생략하면 보관된 가장 오래된 것부터"),
    limit: int = Query(200, ge=1, le=1000, description="최대 1000"),
) -> RecoveryEventsResponse:
    settings = request.app.state.settings
    try:
        page = EventLog(Path(settings.recovery_state_dir) / EVENTS_FILE).after(after_event_id, limit)  # 읽을 때 검증됨. 손상은 gap 으로 알린다
    except EventLogUnavailable as exc:
        raise PlatformUnavailable("recovery event log is unavailable; retry later", detail=str(exc)) from None
    return RecoveryEventsResponse(
        events=[RecoveryEvent.model_validate(event) for event in page["events"]],
        next_after_event_id=page["next_after_event_id"],
        oldest_event_id=page["oldest_event_id"],
        gap=page["gap"],
    )
