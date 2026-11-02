"""예외 → HTTP 변환. FastAPI 기본 422 는 계약대로 400 InvalidArgument 로 정규화한다(계약 8.1)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..errors import Dom0ManagerError, DriverError

log = logging.getLogger(__name__)


def _validation_detail(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:10]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc or '<body>'}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 클라이언트 연동 디버깅용: 거부한 원문을 로그에만 남긴다 (응답에는 넣지 않는다)
        try:
            raw = b"<log payload omitted>" if request.url.path == "/api/v1/logs/ingest" else (await request.body())[:300]
        except Exception:  # noqa: BLE001 - 본문을 못 읽어도 응답은 내야 한다
            raw = b"<unreadable>"
        log.warning("400 InvalidArgument %s %s body=%r content-type=%s", request.method, request.url.path,
                    raw, request.headers.get("content-type"))
        return JSONResponse(
            status_code=400,
            content={"code": "InvalidArgument", "message": "invalid request", "detail": _validation_detail(exc)},
        )

    @app.exception_handler(Dom0ManagerError)
    async def on_service_error(_: Request, exc: Dom0ManagerError) -> JSONResponse:
        if isinstance(exc, DriverError):
            log.error("driver error: %s (%s)", exc.message, exc.detail)
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(NotImplementedError)
    async def on_not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
        return JSONResponse(status_code=501, content={"code": "NotSupported", "message": f"not implemented: {exc}"})
