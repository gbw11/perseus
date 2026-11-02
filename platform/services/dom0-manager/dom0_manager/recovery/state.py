"""진행 중인 복구(recovery)의 영속 상태. Worker 가 재시작해도 같은 recovery_id·attempt 를 이어 간다 (계약 2.5).

저장 뒤에만 플랫폼 recover_once 를 부른다. 그래야 응답을 잃거나 Worker 가 죽어도 같은 recovery_attempt_id 로 결과를 조회·재전달하고,
재시도 상한을 우회하지 않는다. 파일이 깨졌거나 상태별 불변조건이 하나라도 어긋나면 저장소 전체를 잃은 것으로 보고 복구를 실행하지
않는다(fail-safe): 어떤 시도가 진행 중이었는지 모르는 채 새 시도를 내면 중복 정지가 날 수 있다.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from ..models import RecoveryEvent
from .model import FailureReason
from .protection import PHASES as PROTECTION_PHASES
from .store_files import preserve_corrupt, read_state

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MODES = ("start-if-absent", "graceful-restart", "force-restart")
GUEST_KINDS = ("linux", "zephyr")
REASONS = frozenset(reason.value for reason in FailureReason)
PROTECTION_SPEC = frozenset({"host_boot_id", "target_vm", "target_execution_id", "intent_revision", "lifecycle_generation", "expected_owner",
                             "expected_generation", "deadline_ms", "parent_deadline_ms", "purpose"})


class RecoveryStatus(str, Enum):
    DETECTED = "DETECTED"  # 판정을 받았고 아직 시도 전
    EXECUTING = "EXECUTING"  # 시도를 저장했고 recover_once 를 보냈거나 같은 시도를 재전달 대기 중
    VERIFYING = "VERIFYING"  # 플랫폼이 대상 변경(TARGET_CHANGED·INTENT_MISMATCH)을 알림. 감시기의 HEALTHY·STOPPED 를 기다림
    BACKOFF = "BACKOFF"  # 시도가 실패해 다음 시도까지 대기
    COMPLETED = "COMPLETED"  # 플랫폼이 이 시도로 새 실행을 관측하고 execution_id 를 돌려줌(계약 2.5: 복구 성공 = 새 Xen 실행 관측)
    RECOVERED = "RECOVERED"  # 시도가 완료되기 전에 VM 이 건강해짐(사용자 조작·플랫폼 쪽 재시작·신호 복귀). recovery.recovered 로 알린다
    FAILED = "FAILED"  # 최종 포기: 재시도 한도·승인 없음·운영자 재판정 필요
    CANCELLED = "CANCELLED"  # 대상이 아니게 됨: 사용자 stop·삭제·의도 변경


OPEN_STATUSES = frozenset({RecoveryStatus.DETECTED, RecoveryStatus.EXECUTING, RecoveryStatus.VERIFYING, RecoveryStatus.BACKOFF})
EVENT_TYPES = frozenset({"recovery.started", "recovery.completed", "recovery.recovered", "recovery.cancelled", "recovery.failed"})  # 계약 2.5


def validate_event(event: Any) -> None:
    """계약 2.5 복구 이벤트 객체인지(RecoveryEvent). 아니면 ValueError."""
    if not isinstance(event, dict):
        raise ValueError("recovery event must be an object")
    try:
        # strict + JSON: 문자열 숫자("1")·문자열 불리언을 바꿔 받지 않는다. UUID·시각은 JSON 표현(문자열)으로 받는다
        RecoveryEvent.model_validate_json(json.dumps(event, ensure_ascii=False), strict=True)
    except (ValidationError, TypeError) as exc:
        detail = exc.errors()[0].get("msg") if isinstance(exc, ValidationError) else str(exc)
        raise ValueError(f"recovery event {event.get('event_id')!r} does not match the contract: {detail}") from None


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass
class RecoveryRecord:
    recovery_id: UUID
    vm_id: UUID
    reason: str  # 계약 2.5 reason (FailureReason 값)
    guest_kind: str  # linux | zephyr
    status: RecoveryStatus
    detected_at: str  # ISO 8601 UTC. 계약 2.5 detected_at
    detected_monotonic: float
    attempt: int = 0  # 발급한 논리적 시도 수(계약 2.5 attempt). 재전달에서는 그대로
    recovery_attempt_id: UUID | None = None
    request: dict[str, Any] | None = None  # 마지막 recover_once spec. 재전달·recovery-operation 조회에 그대로 쓴다
    mode: str | None = None
    attempt_started_monotonic: float | None = None
    due_monotonic: float | None = None  # BACKOFF·재전달: 다음 행동 시각. VERIFYING: 만료 시각
    replay: bool = False  # 같은 spec 을 다시 보내야 한다(일시 거절·응답 유실·Worker 재시작)
    cancel_requested: bool = False  # 실행 중에 사용자가 껐다. 실행 결과를 회수한 뒤 취소로 닫는다
    execution_id: str | None = None  # 플랫폼이 결합한 새 실행 ID
    last_error: dict[str, Any] | None = None  # {code, platform_code, message}
    # graceful-restart 가 정상 종료에 실패한 실행. 같은 실행이 지금도 돌 때만 다음 시도를 force-restart 로 올린다(다른 실행에 옛 실패를 쓰지 않음)
    graceful_failed_execution_id: str | None = None
    attempt_closed: bool = False  # 현재 attempt 의 종료 이벤트(재시도 실패)를 이미 냈다. 그 뒤의 recovered·cancelled 는 시도에 붙이지 않는다
    # 주행 VM 의 Gateway 보호(S15P31E110-346): {request_id, spec(원 spec), phase, note}. 첫 송신 전에 저장한다.
    # 단계가 LIVE 이면 복구가 닫혀도 정산(finish·cancel)이 끝날 때까지 기록을 지우지 않는다
    protection: dict[str, Any] | None = None
    final: bool = False
    closed_monotonic: float | None = None
    # 아직 outbox(recovery-events.jsonl)에 옮기지 못한 이벤트(계약 2.5, event_id 고정). 상태 전이와 같은 파일에 저장돼
    # Worker 가 그 사이 죽어도 재시작 뒤 같은 event_id 로 저장·발행된다
    pending_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def open(self) -> bool:
        return self.status in OPEN_STATUSES

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["recovery_id"] = str(self.recovery_id)
        data["vm_id"] = str(self.vm_id)
        data["recovery_attempt_id"] = None if self.recovery_attempt_id is None else str(self.recovery_attempt_id)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RecoveryRecord":
        if not isinstance(data, dict):
            raise ValueError("recovery record must be an object")
        fields = dict(data)
        if "graceful_failed" in fields:
            # 실행을 적기 전의 상태 파일: 승격 근거를 그 시도가 겨냥한 원 실행으로 옮긴다. 원 실행을 모르면 승격하지 않는다
            legacy = fields.pop("graceful_failed")
            if type(legacy) is not bool:
                raise ValueError(f"graceful_failed must be a boolean, got {legacy!r}")
            expected = ((fields.get("request") or {}).get("expected_execution") or {}) if isinstance(fields.get("request"), dict) else {}
            fields.setdefault("graceful_failed_execution_id", expected.get("execution_id") if legacy is True else None)
        known = set(cls.__dataclass_fields__)
        unknown = set(fields) - known
        if unknown:
            raise ValueError(f"unknown fields {sorted(unknown)}")
        fields["recovery_id"] = UUID(str(fields["recovery_id"]))
        fields["vm_id"] = UUID(str(fields["vm_id"]))
        attempt_id = fields.get("recovery_attempt_id")
        fields["recovery_attempt_id"] = None if attempt_id is None else UUID(str(attempt_id))
        fields["status"] = RecoveryStatus(fields["status"])
        fields.setdefault("pending_events", [])
        if "attempt_closed" not in fields:
            # 필드가 생기기 전의 상태 파일: 재시도 실패를 낸 뒤 backoff 중이던 시도는 이미 끝난 것이다
            fields["attempt_closed"] = (fields.get("status") == RecoveryStatus.BACKOFF.value and not fields.get("replay", False)
                                        and isinstance(fields.get("attempt"), int) and fields["attempt"] > 0)
        record = cls(**fields)
        record.validate()
        return record

    def validate(self) -> None:
        """상태별 불변조건. 하나라도 어긋나면 저장소 전체를 믿지 않는다."""
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}")
        if self.guest_kind not in GUEST_KINDS:
            raise ValueError(f"unknown guest_kind {self.guest_kind!r}")
        if not isinstance(self.detected_at, str) or not self.detected_at:
            raise ValueError("detected_at must be a non-empty string")
        if not _finite(self.detected_monotonic):
            raise ValueError("detected_monotonic must be a finite number")
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        for name in ("attempt_started_monotonic", "due_monotonic", "closed_monotonic"):
            value = getattr(self, name)
            if value is not None and not _finite(value):
                raise ValueError(f"{name} must be a finite number or null")
        if self.mode is not None and self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}")
        if self.graceful_failed_execution_id is not None and (not isinstance(self.graceful_failed_execution_id, str) or not self.graceful_failed_execution_id):
            raise ValueError("graceful_failed_execution_id must be a non-empty string or null")
        for name in ("replay", "cancel_requested", "final", "attempt_closed"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.pending_events, list):
            raise ValueError("pending_events must be a list")
        seen: set[str] = set()
        for event in self.pending_events:
            validate_event(event)  # 계약 2.5 전체 스키마. 손상된 이벤트를 outbox 로 옮겨 영구히 잃지 않도록 복원 단계에서 거부한다
            if event["recovery_id"] != str(self.recovery_id) or event["vm_id"] != str(self.vm_id):
                raise ValueError("pending event does not belong to the record")
            if event["event_id"] in seen:
                raise ValueError(f"duplicate pending event_id {event['event_id']}")
            seen.add(event["event_id"])
        if self.protection is not None:
            self._validate_protection()
        if self.request is not None:
            if not isinstance(self.request, dict):
                raise ValueError("request must be an object")
            if self.recovery_attempt_id is None or self.request.get("recovery_attempt_id") != str(self.recovery_attempt_id):
                raise ValueError("request does not belong to recovery_attempt_id")
            if self.request.get("vm_id") != str(self.vm_id) or self.request.get("mode") != self.mode:
                raise ValueError("request does not match the record")
        if self.status == RecoveryStatus.EXECUTING:
            if self.attempt < 1 or self.recovery_attempt_id is None or self.request is None or self.mode is None or self.attempt_started_monotonic is None:
                raise ValueError("EXECUTING requires attempt, recovery_attempt_id, request, mode and attempt_started_monotonic")
        elif self.status == RecoveryStatus.VERIFYING:
            if self.attempt < 1 or self.recovery_attempt_id is None or self.due_monotonic is None:
                raise ValueError("VERIFYING requires an attempt and a due time")
        elif self.status == RecoveryStatus.BACKOFF:
            if self.attempt < 1 or self.due_monotonic is None or (self.replay and self.request is None):
                raise ValueError("BACKOFF requires an attempt and a due time (and a request when replaying)")
        elif self.status == RecoveryStatus.DETECTED:
            if self.attempt != 0 or self.replay or self.request is not None:
                raise ValueError("DETECTED must not carry an attempt")
        elif self.closed_monotonic is None:
            raise ValueError(f"{self.status.value} requires closed_monotonic")

    def _validate_protection(self) -> None:
        """손상된 보호 기록으로 다른 실행을 정산하거나 새 키를 만들지 않도록 원 spec 의 모양까지 본다."""
        entry = self.protection
        if not isinstance(entry, dict) or set(entry) != {"request_id", "spec", "phase", "note"}:
            raise ValueError("protection must be {request_id, spec, phase, note}")
        if not isinstance(entry["request_id"], str) or not entry["request_id"] or entry["phase"] not in PROTECTION_PHASES:
            raise ValueError("protection request_id/phase invalid")
        if entry["note"] is not None and not isinstance(entry["note"], str):
            raise ValueError("protection note must be a string or null")
        spec = entry["spec"]
        if not isinstance(spec, dict) or set(spec) != PROTECTION_SPEC or spec["purpose"] != "protect" or spec["target_vm"] != str(self.vm_id):
            raise ValueError("protection spec does not belong to the record")
        for name in ("host_boot_id", "target_execution_id", "expected_owner"):
            if not isinstance(spec[name], str) or str(UUID(spec[name])) != spec[name]:
                raise ValueError(f"protection spec {name} must be a canonical UUID")
        for name in ("intent_revision", "lifecycle_generation", "expected_generation", "deadline_ms", "parent_deadline_ms"):
            if type(spec[name]) is not int or spec[name] < 0:
                raise ValueError(f"protection spec {name} must be a non-negative integer")
        if spec["deadline_ms"] > spec["parent_deadline_ms"]:
            raise ValueError("protection deadline exceeds its parent deadline")


class RecoveryStore:
    """Worker 상태+pending 사건의 원자 저장. 손상 원본/격리 흔적으로 차단을 복원한다."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self.history_lost = False
        self._write_blocked = False

    @property
    def path(self) -> Path | None:
        return self._path

    def load(self) -> dict[UUID, RecoveryRecord]:
        if self._path is None:
            return {}
        try:
            text = read_state(self._path)
            if text is None:
                return {}
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
                raise ValueError(f"unsupported schema (expected version {SCHEMA_VERSION})")
            raw = data.get("recoveries")
            if not isinstance(raw, list):
                raise ValueError("'recoveries' must be a list")
            records = [RecoveryRecord.from_json(item) for item in raw]
            lost = data.get("history_lost", False)
            if not isinstance(lost, bool):
                raise ValueError("'history_lost' must be a boolean")
        except OSError as exc:
            return self._lost(f"unreadable: {exc}", preserve=False)
        except (ValueError, TypeError, KeyError) as exc:
            return self._lost(str(exc), preserve=True)
        result: dict[UUID, RecoveryRecord] = {}
        for record in records:
            if record.recovery_id in result:
                return self._lost(f"duplicate recovery_id {record.recovery_id}", preserve=True)
            result[record.recovery_id] = record
        open_vms = [r.vm_id for r in result.values() if r.open]
        if len(open_vms) != len(set(open_vms)):
            return self._lost("more than one open recovery for a VM", preserve=True)
        self.history_lost = self.history_lost or lost
        return result

    def _lost(self, why: str, *, preserve: bool) -> dict[UUID, RecoveryRecord]:
        self.history_lost = True
        log.error(
            "recovery store %s unavailable/corrupt (%s): in-flight recoveries are unknown; recovery is blocked; preserve state and evidence for investigation",
            self._path, why,
        )
        if preserve:
            try:
                preserve_corrupt(self._path)
            except OSError as exc:
                self._write_blocked = True
                log.warning("recovery store %s could not be set aside: %s", self._path, exc)
        else:
            # 읽지 못한 원본·부재 뒤 잔존 증거를 빈 파일로 덮어쓰지 않는다.
            self._write_blocked = True
        if not self._write_blocked:
            self.save({})  # False여도 원본 또는 보존 사본으로 다음 load가 차단된다.
        return {}

    def save(self, records: dict[UUID, RecoveryRecord]) -> bool:
        if self._path is None:
            return True
        if self._write_blocked:
            log.error("recovery store %s not saved: unresolved state/evidence", self._path)
            return False
        body = {
            "version": SCHEMA_VERSION,
            "history_lost": self.history_lost,
            "recoveries": [record.to_json() for record in records.values()],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except OSError as exc:
            log.error("recovery store %s not saved: %s", self._path, exc)
            return False
        return True
