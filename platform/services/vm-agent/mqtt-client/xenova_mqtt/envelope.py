"""공통 봉투 생성·검사와 발행자별 seq 중복 처리 (계약 2장).

백엔드 `AgentMessageHandler` 와 같은 기준으로 검사한다. 어긋난 메시지는 예외 대신 EnvelopeError 로 알리고,
받는 쪽은 로그만 남기고 버린다.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import topics

SCHEMA_VERSION = "1.0"

# Linux 커널이 부팅마다 새로 만드는 값. 없으면(개발 PC 등) 프로세스마다 새 UUID 로 대신한다.
_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

# MQTT-04 명령은 boot_id·agent_instance_id·seq 대신 expected_boot_id·command_id 를 쓴다 (계약 2장 예외)
_COMMAND_TYPES = frozenset({"agent.command"})


class EnvelopeError(ValueError):
    """계약 위반. code 는 계약·백엔드와 같은 오류 코드다."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def read_boot_id() -> str:
    try:
        value = _BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except OSError:
        value = ""
    return value or str(uuid.uuid4())


def utc_now() -> str:
    """ISO-8601 UTC, 밀리초, `Z` 끝 (예: 2026-10-19T09:00:00.000Z)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Identity:
    """발행자 식별. VM 이면 vm_id, VM 이 아니면(dom0-gateway, jetson 등) source_id 를 쓴다."""

    vm_id: str | None = None
    source_id: str | None = None
    app_id: str | None = None
    boot_id: str = field(default_factory=read_boot_id)
    agent_instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if bool(self.vm_id) == bool(self.source_id):
            raise ValueError("vm_id 와 source_id 중 정확히 하나를 지정한다")

    @property
    def publisher(self) -> str:
        return self.vm_id or self.source_id  # type: ignore[return-value]


def build(
    identity: Identity,
    message_type: str,
    payload: dict[str, Any],
    seq: int,
    *,
    app_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("payload 는 JSON 객체(dict)여야 한다")
    envelope: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "message_id": str(uuid.uuid4())}
    if identity.vm_id:
        envelope["vm_id"] = identity.vm_id
    else:
        envelope["source_id"] = identity.source_id
    app = app_id or identity.app_id
    if app:
        envelope["app_id"] = app
    envelope.update(
        boot_id=identity.boot_id,
        agent_instance_id=identity.agent_instance_id,
        type=message_type,
        seq=seq,
        sent_at=utc_now(),
        payload=payload,
    )
    if correlation_id:
        envelope["correlation_id"] = correlation_id
    if causation_id:
        envelope["causation_id"] = causation_id
    return envelope


def _text(envelope: dict[str, Any], name: str) -> bool:
    value = envelope.get(name)
    return isinstance(value, str) and value.strip() != ""


def validate(topic: str, envelope: Any) -> None:
    """계약 2장 검사. 통과하지 못하면 EnvelopeError."""
    if not isinstance(envelope, dict):
        raise EnvelopeError("PAYLOAD_INVALID", "봉투가 JSON 객체가 아니다")
    if envelope.get("schema_version") != SCHEMA_VERSION:
        raise EnvelopeError("SCHEMA_VERSION_UNSUPPORTED", repr(envelope.get("schema_version")))

    required = ["message_id", "type", "sent_at"]
    if envelope.get("type") not in _COMMAND_TYPES:
        required += ["boot_id", "agent_instance_id"]
        seq = envelope.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise EnvelopeError("PAYLOAD_INVALID", f"seq {seq!r}")
    missing = [name for name in required if not _text(envelope, name)]
    if missing:
        raise EnvelopeError("PAYLOAD_INVALID", f"필수 필드 없음 {missing}")
    if not isinstance(envelope.get("payload"), dict):
        raise EnvelopeError("PAYLOAD_INVALID", "payload 가 JSON 객체가 아니다")
    if not _text(envelope, "vm_id") and not _text(envelope, "source_id"):
        raise EnvelopeError("PAYLOAD_INVALID", "vm_id 와 source_id 가 모두 없다")

    info = topics.parse(topic)
    if info.vm_id is not None and envelope.get("vm_id") != info.vm_id:
        raise EnvelopeError("VM_ID_MISMATCH", f"topic {info.vm_id}, body {envelope.get('vm_id')}")
    if info.app_id is not None and envelope.get("app_id") != info.app_id:
        raise EnvelopeError("APP_ID_MISMATCH", f"topic {info.app_id}, body {envelope.get('app_id')}")


class SeqTracker:
    """발행자·토픽별 마지막 seq. 같거나 작은 seq 는 중복·지연으로 버린다.

    boot_id 나 agent_instance_id 가 바뀌면 그 발행자가 다시 시작한 것이므로 기준을 새로 잡는다.
    """

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], tuple[str, str, int]] = {}
        self._lock = threading.Lock()

    def accept(self, topic: str, envelope: dict[str, Any]) -> bool:
        seq = envelope.get("seq")
        if not isinstance(seq, int):
            return True  # MQTT-04 명령처럼 seq 가 없는 봉투
        key = (topic, envelope.get("vm_id") or envelope.get("source_id") or "")
        run = (envelope.get("boot_id") or "", envelope.get("agent_instance_id") or "")
        with self._lock:
            previous = self._last.get(key)
            if previous is not None and previous[:2] == run and seq <= previous[2]:
                return False
            self._last[key] = (*run, seq)
            return True
