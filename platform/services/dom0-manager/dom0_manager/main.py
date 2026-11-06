"""FastAPI 앱 조립. `create_app()` 으로 테스트에서 드라이버를 주입하고, 모듈 수준 `app` 은 uvicorn 용이다."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from . import __version__
from .api import console, cpu, health, logs, recovery, registrations, stats, vms
from .api.errors import install_error_handlers
from .config import Settings
from .console import ConsoleSession
from .drivers import build_driver, build_provisioner
from .drivers.base import Driver, Provisioner
from .service import VmService

DESCRIPTION = (
    "Spring Boot 관리 서버와 Python dom0-manager 사이의 내부 계약. "
    "기준 문서는 docs/contracts/vm-api-contract-v1.md, 기계 판독 원본은 docs/contracts/dom0-manager-openapi.yaml."
)


def create_app(
    settings: Settings | None = None,
    *,
    driver: Driver | None = None,
    provisioner: Provisioner | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    driver = driver or build_driver(settings)
    provisioner = provisioner or build_provisioner(settings, driver)

    # 문서 경로(/docs·/redoc·/openapi.json)는 기본으로 열지 않는다(계약: 모든 엔드포인트 토큰 검사, 2026-11-06 결정).
    # XENOVA_DOM0_API_DOCS=1 은 개발용이며, 켜도 서비스 토큰이 설정돼 있으면 아래 미들웨어가 토큰을 검사한다.
    # 계약 시험은 경로가 아니라 app.openapi() 를 직접 부르므로 경로를 꺼도 스키마 비교는 그대로다
    docs = settings.api_docs
    app = FastAPI(
        title="Xenova dom0 manager API",
        version="1.0.0",
        description=DESCRIPTION,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.driver = driver
    app.state.service = VmService(driver, provisioner, settings)
    app.state.console_factory = ConsoleSession
    from .registration_sender import RegistrationSender
    app.state.registration_sender = RegistrationSender(settings)
    # 예상된 등록 입력 거절의 유한 부가 진단. 원 operation/등록 기록을 대체하지 않는다.
    app.state.registration_validation = {"rejected": 0, "first_detail": None}
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def registration_lifespan(application):
        async with previous(application):
            try:
                yield
            finally:
                app.state.registration_sender.close()

    app.router.lifespan_context = registration_lifespan
    from .observations import install_observations
    install_observations(app)

    app.include_router(health.router)
    app.include_router(vms.router, prefix="/api/v1")
    app.include_router(stats.router, prefix="/api/v1")
    app.include_router(cpu.router, prefix="/api/v1")
    app.include_router(console.router, prefix="/api/v1")
    app.include_router(recovery.router, prefix="/api/v1")
    app.include_router(logs.router, prefix="/api/v1")
    app.include_router(registrations.router, prefix="/api/v1")
    install_error_handlers(app)
    _install_request_budget(app, settings.response_reserve_s)
    if settings.service_token:
        _install_bearer_auth(app, settings.service_token)
    app.openapi = _contract_openapi(app)  # type: ignore[method-assign]

    logging.getLogger(__name__).info("dom0-manager %s driver=%s", __version__, driver.name)
    return app


def _install_request_budget(app: FastAPI, reserve_s: float) -> None:
    """X-Request-Budget-Ms 가 있으면 요청이 도착한 지금(스레드풀 대기 전)부터 기한을 잰다(budget.py). /healthz 는 쓰지 않는다."""
    from fastapi.responses import JSONResponse

    from . import budget

    @app.middleware("http")
    async def request_budget(request, call_next):
        raw = request.headers.get(budget.HEADER)
        if (raw is None and request.method == "POST" and request.url.path.startswith("/api/v1/vm-registrations/")
                and request.url.path.endswith("/notify")):
            raw = "15000"  # 기본 notify 기한도 threadpool 대기 전에 한 번만 시작한다.
        if raw is None or request.url.path in budget.EXEMPT_PATHS:
            return await call_next(request)
        try:
            ms = budget.parse(raw)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"code": "InvalidArgument", "message": str(exc)})
        token = budget.start(ms, reserve_s=reserve_s)
        try:
            return await call_next(request)
        finally:
            budget.reset(token)


def _install_bearer_auth(app: FastAPI, token: str) -> None:
    """계약 v1.1: /healthz 를 포함한 모든 내부 API 에서 Bearer 서비스 토큰을 검사한다. 토큰 값은 로그에 남기지 않는다."""
    import hmac

    from fastapi.responses import JSONResponse

    expected = f"Bearer {token}"

    @app.middleware("http")
    async def require_service_token(request, call_next):
        # 문서 경로도 예외가 아니다. 예외는 자체 토큰으로 인증하는 로그 수신 하나뿐이다
        if request.url.path == "/api/v1/logs/ingest":
            # Log producers authenticate with their own source-scoped token in the route.
            # They must never receive the root dom0 control service token.
            return await call_next(request)
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return JSONResponse(status_code=401, content={"code": "Unauthorized", "message": "missing or invalid service token"})
        return await call_next(request)


def _contract_openapi(app: FastAPI):
    """생성 스키마에서 422 응답과 HTTPValidationError 모델을 제거하고(계약 8.1) Bearer 보안 스킴을 전역으로 선언한다.
    계약 테스트가 이 결과를 yaml 과 비교한다."""

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        components = schema.get("components", {}).get("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        # 인증은 미들웨어라 라우트에서 보이지 않는다. 계약(yaml)과 같은 ServiceToken 보안 스킴을 전역 security 로 선언한다
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["ServiceToken"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque service token",
        }
        schema["security"] = [{"ServiceToken": []}]
        schema["components"]["securitySchemes"]["LogProducerToken"] = {
            "type": "http", "scheme": "bearer", "bearerFormat": "source-scoped log producer token",
        }
        # 요청 예산도 미들웨어라 라우트에서 보이지 않는다. /healthz 를 뺀 모든 operation 에 헤더 파라미터로 선언한다(budget.py)
        from . import budget

        schema["components"].setdefault("parameters", {})["RequestBudget"] = {
            "name": budget.HEADER,
            "in": "header",
            "required": False,
            "description": "호출자가 이 요청을 기다릴 남은 시간(ms). 다 쓰면 실행·제출하지 않고 504 (계약 4.9절)",
            "schema": {"type": "integer", "minimum": 1, "maximum": budget.MAX_MS},
        }
        for path, path_item in schema.get("paths", {}).items():
            if path in budget.EXEMPT_PATHS:
                continue
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("parameters", []).append({"$ref": "#/components/parameters/RequestBudget"})
        app.openapi_schema = schema
        return schema

    return custom_openapi


app = create_app()
