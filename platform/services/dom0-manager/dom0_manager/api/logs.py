"""Read-only log API. Console input and log ingestion are deliberately separate."""
from pathlib import Path
import hashlib
import hmac
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from ..runtime_logs import LogStore
from ..errors import InvalidArgument, PlatformUnavailable
from ..models import Error
from ..runtime_logs import IDENTIFIER

router = APIRouter(tags=["logs"])


def store(request):
    path = request.app.state.settings.runtime_log_db
    # Querying must not create an apparently healthy empty collector database.
    if not path or not Path(path).is_file():
        raise PlatformUnavailable("runtime log collector is not configured or has not started")
    return LogStore(path)


ERRORS = {400: {"model": Error}, 401: {"model": Error}, 503: {"model": Error}}


@router.get("/logs", operation_id="listRuntimeLogs", responses=ERRORS)
def list_logs(request: Request, after: int | None = Query(None, ge=0),
              source_id: str | None = Query(None, max_length=128), vm_id: str | None = None,
              component: str | None = Query(None, max_length=128),
              level: str | None = Query(None, pattern="^(DEBUG|INFO|WARN|ERROR|FATAL)$"),
              limit: int = Query(200, ge=1, le=500)):
    try:
        return store(request).query(after=after, source_id=source_id, vm_id=vm_id,
                                    component=component, level=level, limit=limit)
    except ValueError as error:
        raise InvalidArgument(str(error)) from None


@router.get("/logs/sources", operation_id="listRuntimeLogSources", responses=ERRORS)
def sources(request: Request):
    current = store(request)
    items = current.sources()
    configured_path = request.app.state.settings.runtime_log_ingest_config
    if configured_path:
        try:
            configured = json.loads(Path(configured_path).read_text(encoding="utf-8"))["sources"]
            known = {s["source_id"] for s in items}
            for name, value in configured.items():
                if name not in known:
                    items.append({"source_id": name, "vm_id": value.get("vm_id"),
                                  "components": value["components"], "state": "unavailable",
                                  "updated_at": None, "last_record_at": None})
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            raise PlatformUnavailable("log source configuration unavailable") from None
    return {"epoch": current.epoch, "items": items}


@router.post("/logs/ingest", operation_id="ingestRuntimeLogs", responses=ERRORS,
             openapi_extra={"security": [{"LogProducerToken": []}], "parameters": [
                 {"name": "X-Log-Source", "in": "header", "required": True, "schema": {"type": "string"}}]})
async def ingest(request: Request):
    """Private producer endpoint, intentionally not proxied by Spring's browser API."""
    config_path = request.app.state.settings.runtime_log_ingest_config
    if not config_path:
        raise PlatformUnavailable("log producers are not configured")
    try:
        configured = json.loads(Path(config_path).read_text(encoding="utf-8"))["sources"]
    except (OSError, ValueError, KeyError):
        raise PlatformUnavailable("log source configuration unavailable") from None
    source_id = request.headers.get("x-log-source", "")
    credential = configured.get(source_id) if isinstance(configured, dict) else None
    raw_auth = request.headers.get("authorization", "")
    supplied = raw_auth[7:] if raw_auth.startswith("Bearer ") else ""
    expected = credential.get("token_sha256", "") if isinstance(credential, dict) else ""
    if not expected or not supplied or not hmac.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), expected):
        raise HTTPException(401, "source authentication required")
    if not IDENTIFIER.fullmatch(source_id):
        raise InvalidArgument("invalid source identity")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise InvalidArgument("log batch exceeds 64KiB")
        body.extend(chunk)
    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict) or set(envelope) != {"records"}:
            raise ValueError()
        records = envelope["records"]
        if not isinstance(records, list) or len(records) > 32:
            raise ValueError()
        components = credential["components"]
        if not isinstance(components, list) or not 1 <= len(components) <= 32 or any(not isinstance(c, str) or not IDENTIFIER.fullmatch(c) for c in components):
            raise ValueError()
        vm_id = credential.get("vm_id")
        if vm_id is not None:
            vm_id = str(UUID(vm_id))
        normalized = []
        for record in records:
            if not isinstance(record, dict) or set(record) != {"component", "boot_id", "observed_at", "level", "message", "native_id"}:
                raise ValueError()
            if (record["component"] not in components or not IDENTIFIER.fullmatch(record["boot_id"])
                    or record["level"] not in {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}
                    or not isinstance(record["message"], str) or len(record["message"]) > 8192
                    or not isinstance(record["observed_at"], str) or len(record["observed_at"]) > 64
                    or not isinstance(record["native_id"], str) or not 1 <= len(record["native_id"]) <= 512):
                raise ValueError()
            stamp = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError()
            normalized.append({**record, "source_id": source_id, "vm_id": vm_id})
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidArgument("invalid log batch or component is not allowed for this source") from None
    current = store(request)
    count = sum(current.append(**record) for record in normalized)
    current.source(source_id, components, vm_id=vm_id, recorded=bool(records))
    return {"accepted": count, "duplicates": len(records) - count}
