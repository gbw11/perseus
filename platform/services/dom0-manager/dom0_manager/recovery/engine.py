"""복구 정책 엔진(S15P31E110-125): 감시기의 Judgment 를 받아 플랫폼 recover_once 로 그 VM 만 재시작한다.

원칙
- VM 마다 열린 복구(recovery_id)는 하나. 같은 VM 의 새 판정은 열린 복구에 흡수된다. recovery_id 는 Worker 재시작 뒤에도 저장소에서 이어 간다
- 저장 뒤에만 플랫폼을 부른다. 응답을 잃거나 일시 거절되면 같은 recovery_attempt_id·같은 spec 을 다시 보낸다(플랫폼은 journal 로 멱등).
  실패가 확정되고 backoff 가 끝났을 때만 새 attempt id 를 발급한다
- 복구 성공 = 플랫폼이 새 Xen 실행을 관측하고 completed 를 돌려준 것(계약 2.5). 생존 신호(heartbeat)의 복귀는 감시기의 건강 상태로만
  따로 보고하며, 돌아오지 않는다고 다시 재시작하지 않는다(브로커·에이전트 문제로 멀쩡한 VM 을 또 재시작하게 된다)
- 결과를 모르는 시도(실행 중, 응답 유실, 일시 거절 뒤 재전달 대기, Worker 재시작 뒤 복원)는 감시기의 HEALTHY·STOPPED 만으로 닫지 않는다.
  journal 을 먼저 조회하고 결과를 받은 뒤 닫는다. 사용자가 그 사이 껐으면 취소 요청으로 적어 두고, 플랫폼이 완료했으면 완료로, 아니면 취소로 닫는다
- 장애 한 건의 끝은 하나다(계약 2.5): recovery.completed(이 시도로 플랫폼이 새 execution_id 를 돌려줌) / recovery.recovered(시도가 완료되기
  전에 VM 이 건강해짐: 사용자 조작·플랫폼 쪽 재시작·신호 복귀, 더 시도하지 않음) / recovery.cancelled(사용자 stop·삭제·의도 변경) /
  final 인 recovery.failed(실제 복구 실패)
- 시도의 spec(기대 revision·execution·deadline)은 실행 슬롯을 얻은 뒤 그 시점의 view 와 시계로 만든다. 큐에서 기다린 시간이 deadline 을
  갉아먹지 않는다. 실행 스레드는 하나라 플랫폼 호출은 한 번에 하나다
- 이벤트(계약 2.5)는 상태 전이와 함께 레코드에 적어 recoveries.json 에 저장한 뒤에만 outbox(recovery-events.jsonl)로 옮기고 발행한다.
  상태 저장에 실패하면 이벤트를 내지 않고 다음 주기에 다시 저장한다. outbox 로 못 옮긴 이벤트는 레코드에 남아 Worker 재시작 뒤에도
  같은 event_id 로 저장·발행된다(수신 쪽은 event_id 로 중복을 거른다)
- 정책 none 이거나 상태 파일이 깨졌으면 실행하지 않고 판정·건강만 낸다
- force-restart 는 지금 실행에 대한 지금의 장애 증거가 있을 때만 고른다(S15P31E110-347). 판정 당시 사유·실행과 비교하지 않는다: 감시기는
  장애 한 건에 판정을 한 번만 내므로, 실행이나 사유가 바뀐 뒤 장애가 이어지면 판정 당시 값과 영원히 어긋난다. 대신 감시기의 현재 건강이
  의도를 읽은 UNHEALTHY 이고, 그 증거가 force 를 정당화하며, 플랫폼 view 와 같은 실행을 본 것이어야 한다.
  · DOMAIN_CRASHED: 플랫폼 view 도 지금 crashed 여야 한다. 어떤 최초 사유보다 강한 근거다(heartbeat 손실 뒤 crash 포함)
  · APP_HEARTBEAT_LOST: Zephyr 만. 동료 채널 증거가 있어야 UNHEALTHY 다(S15P31E110-335)
  · graceful 실패 승격: 실패했던 실행이 지금도 돌고 있고 최초 사유와 같은 현재 증거일 때만. force 여부 자체도 현재 view·실행으로 정한다
    (Zephyr, 지금 crashed, 같은 실행의 graceful 실패). 옛 실행의 crash·graceful 실패를 새 실행의 force 근거로 쓰지 않는다
  · 실행 대조: 주행 VM 은 binding execution_id 가 반드시 있어야 하고 view 와 같아야 한다. 주행이 아닌 VM 은 관측에 execution_id 가 없어
    domid 로 대조한다(Xen 은 한 host 부팅 안에서 domid 를 다시 쓰지 않는다. 제한 정책)
  · 주행 VM 은 현재 Gateway 보호 근거(protection)도 있어야 한다. drive 분류를 모르면(drive config 없음) Zephyr force 는 모두 보류한다
  근거가 부족하면 attempt 를 소비하지 않고 recovery.started 도 내지 않은 채 잠시 뒤 다시 본다. 플랫폼 per-VM force 승인은 플랫폼이 최종 확인한다
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from ..errors import Dom0ManagerError, DriverTimeout
from .model import FailureReason, HealthState, Judgment, VmHealth
from .platform_recovery import RecoveryPlatform
from .policy import RecoveryPolicy
from .state import RecoveryRecord, RecoveryStatus, RecoveryStore

log = logging.getLogger(__name__)

# 플랫폼 오류 분류 (xenova-recovery-foundation.md "오류와 관측")
TRANSIENT_CODES = frozenset({  # 같은 시도를 잠시 뒤 다시 보낸다. attempt 를 소비하지 않는다
    "OPERATION_IN_PROGRESS", "HOST_QUIESCING", "BOOT_ADMISSION_REQUIRED", "OBSERVATION_UNAVAILABLE",
    "STORAGE_UNAVAILABLE", "INTENT_RECONCILIATION_REQUIRED",
})
REOBSERVE_CODES = frozenset({"TARGET_CHANGED", "INTENT_MISMATCH"})  # 대상이 바뀌었다. 감시기의 다음 판단(HEALTHY·STOPPED)을 기다린다
FINAL_CODES = frozenset({"RECONCILE_REQUIRED", "IDEMPOTENCY_CONFLICT", "INVALID_SPEC"})  # 자동 재시도 금지. 운영자 재판정
GONE_CODES = frozenset({"VM_NOT_FOUND", "VM_PURGED"})
# graceful-restart 가 "정상 종료에 실패"했다고 플랫폼이 명시한 경우에만 force-restart 로 올린다. 정체 모를 오류로는 올리지 않는다
GRACEFUL_FAILURE_CODES = frozenset({"RECOVERY_MODE_UNSUPPORTED", "DEADLINE_EXCEEDED"})
KEEP_CLOSED = 20  # 닫힌 복구를 저장소에 남기는 수(진단용)


class WatcherLike(Protocol):
    def health(self, vm_id: UUID) -> VmHealth | None: ...


EventSink = Callable[[dict[str, Any]], Any]  # 이벤트를 outbox 에 저장(하고 발행). 저장하지 못했으면 False 를 돌려준다


class _NotEligible(Exception):
    """시도 직전 재확인에서 대상이 아니다(사용자 stop·보관·의도 변경)."""


class _Cancelled(Exception):
    """플랫폼이 시도를 받기 전에 취소 요청이 들어왔다."""


class _Recovered(Exception):
    """플랫폼이 시도를 받기 전에 감시기가 HEALTHY 를 냈다. 건강한 VM 을 재시작하지 않는다."""


class _NotSaved(Exception):
    """시도를 저장하지 못해 플랫폼을 부르지 않았다."""


class _Held(Exception):
    """force-restart 근거가 부족하다. attempt 를 소비하지 않고 잠시 뒤 다시 본다."""


@dataclass(frozen=True)
class ProtectionQuery:
    """보호 근거 조회에 넘기는 읽기 전용 사본. 변경 가능한 레코드·view 를 잠금 밖으로 넘기지 않도록 스칼라만 담는다."""

    recovery_id: UUID
    vm_id: UUID
    attempt: int
    host_boot_id: str | None
    execution_id: str | None
    intent_revision: int | None
    lifecycle_generation: int | None
    domid: int | None
    xen_state: str | None


# 주행 VM 의 현재 Gateway 보호 근거. 보호가 지금 유효한가. 보호 수명주기(S15P31E110-346) 전에는 없다.
# I/O(Gateway get 등)를 해도 된다: 엔진 잠금 밖에서 부르고, 잠근 뒤 레코드 열림·취소·현재 증거를 다시 확인한다. 상태를 바꾸지 않는다
ProtectionEvidence = Callable[[ProtectionQuery], bool]


class RecoveryEngine:
    def __init__(
        self,
        policy: RecoveryPolicy,
        platform: RecoveryPlatform,
        watcher: WatcherLike,
        store: RecoveryStore,
        *,
        on_event: EventSink | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        new_id: Callable[[], UUID] = uuid4,
        inline: bool = False,
        drive_vm_ids: Iterable[UUID | str] | None = None,
        protection: ProtectionEvidence | None = None,
    ) -> None:
        self._policy = policy
        self._platform = platform
        self._watcher = watcher
        self._store = store
        self._on_event = on_event
        self._clock = clock
        self._wall = wall
        self._new_id = new_id
        self._inline = inline
        # None: 주행 VM 이 어느 것인지 모른다(drive config 없음). Zephyr force 를 모두 보류한다. 빈 집합은 "주행 VM 없음"이 확인된 것
        self._drive_vm_ids = None if drive_vm_ids is None else frozenset(str(UUID(str(v))) for v in drive_vm_ids)
        self._protection = protection
        self._held: dict[UUID, str] = {}  # 근거 부족으로 보류 중인 복구의 마지막 사유. 바뀔 때만 로그한다
        self._executor: ThreadPoolExecutor | None = None if inline else ThreadPoolExecutor(max_workers=1, thread_name_prefix="recovery")
        self._futures: dict[UUID, Future] = {}
        self._lock = threading.RLock()
        self._records: dict[UUID, RecoveryRecord] = store.load()
        self._lost = store.history_lost
        self._lost_warned = False
        self._dirty = False  # 메모리의 상태가 저장소에 아직 반영되지 않았다(저장 실패). 다음 주기에 다시 저장한다
        self._skipped: set[UUID] = set()  # 정책 none 으로 실행하지 않는다고 이미 로그한 복구
        now = clock()
        restored = 0
        for record in self._records.values():
            if record.open:
                restored += 1
                if record.status == RecoveryStatus.EXECUTING:
                    record.replay = True  # 호출 결과를 모른다. 감시기로 닫지 않고 먼저 journal 을 조회한다
                    record.due_monotonic = now
        if restored:
            log.warning("restored %d open recovery(ies) from %s", restored, store.path)
        pending = sum(len(r.pending_events) for r in self._records.values())
        if pending:
            log.warning("restored %d recovery event(s) not yet stored in the outbox; they will be stored and published with their original event_id", pending)
        if self._lost:
            log.error("recovery history lost: no recovery will be executed; preserve %s and its evidence for investigation", store.path)

    # ---- 조회 ----

    def open_for(self, vm_id: UUID) -> RecoveryRecord | None:
        with self._lock:
            return next((r for r in self._records.values() if r.vm_id == vm_id and r.open), None)

    def records(self) -> list[RecoveryRecord]:
        with self._lock:
            return list(self._records.values())

    def completed_executions(self) -> list[dict[str,str]]:
        """Durably completed platform results for post-recovery provisioning.

        App readiness is deliberately not part of COMPLETED. Repeated delivery
        is allowed; the recipient keys work by VM/execution/attempt, not by tick.
        """
        with self._lock:
            if self._dirty:return []
            return [{'vm_id':str(r.vm_id),'execution_id':r.execution_id,
                     'recovery_id':str(r.recovery_id),'recovery_attempt_id':str(r.recovery_attempt_id)}
                    for r in self._records.values() if r.status==RecoveryStatus.COMPLETED and r.execution_id]

    # ---- 입력 ----

    def on_judgment(self, judgment: Judgment, now: float | None = None) -> RecoveryRecord:
        """감시기의 판정. VM 에 열린 복구가 있으면 그것을 이어 간다(같은 recovery_id). 없으면 새로 연다."""
        now = self._clock() if now is None else now
        with self._lock:
            existing = self.open_for(judgment.vm_id)
            if existing is not None:
                if existing.recovery_id != judgment.recovery_id:
                    log.info("vm %s: judgment %s absorbed into open recovery %s", judgment.vm_id, judgment.recovery_id, existing.recovery_id)
                return existing
            record = RecoveryRecord(
                recovery_id=judgment.recovery_id, vm_id=judgment.vm_id, reason=judgment.reason.value, guest_kind=judgment.guest_kind,
                status=RecoveryStatus.DETECTED, detected_at=self._iso(self._wall()), detected_monotonic=now,
            )
            self._records[record.recovery_id] = record
            self._save()
            log.warning("vm %s: recovery %s opened (%s)", record.vm_id, record.recovery_id, record.reason)
            return record

    # ---- 주기 ----

    def collect(self, now: float | None = None) -> None:
        """끝난 실행 결과를 회수하고, 저장 못 한 상태·이벤트를 다시 저장한다. 플랫폼·Xen 관측에 실패한 주기에도 부른다."""
        now = self._clock() if now is None else now
        with self._lock:
            self._retry_pending()
            for recovery_id, future in list(self._futures.items()):
                if not future.done():
                    continue
                del self._futures[recovery_id]
                record = self._records.get(recovery_id)
                if record is not None:
                    self._finish(record, future, now)

    def tick(self, now: float | None = None) -> None:
        """관측이 성공한 주기: 결과 회수 → 감시기 대조 → 시도 시작·backoff·재관측 만료."""
        now = self._clock() if now is None else now
        self.collect(now)
        with self._lock:
            for record in [r for r in self._records.values() if r.open]:
                in_flight = record.recovery_id in self._futures
                if in_flight or record.status == RecoveryStatus.EXECUTING:
                    # 결과를 모르는 시도: 감시기로 닫지 않는다. 사용자 stop 은 취소 요청으로만 적고, 실행 중이 아니면(응답 유실·재전달 대기·
                    # Worker 재시작 뒤 복원) 때가 됐을 때 journal 조회(replay)로 결과를 받아 닫는다
                    self._watch_in_flight(record)
                    if not in_flight and self._due(record, now):
                        self._start(record, now)
                    continue
                self._check_watcher(record, now)
                if not record.open:
                    continue
                if record.status in (RecoveryStatus.DETECTED, RecoveryStatus.BACKOFF) and self._due(record, now):
                    self._start(record, now)
                elif record.status == RecoveryStatus.VERIFYING and self._due(record, now):
                    self._attempt_failed(record, now, {"code": "REOBSERVE_TIMEOUT", "platform_code": None,
                                                       "message": f"target changed but neither HEALTHY nor STOPPED within {self._policy.verify_timeout_s:g}s"})
            self._prune()

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ---- 감시기와의 대조 ----

    def _watch_in_flight(self, record: RecoveryRecord) -> None:
        """결과를 모르는 시도: 닫지 않는다. 사용자가 끈 것만 취소 요청으로 적어 둔다."""
        health = self._watcher.health(record.vm_id)
        if health is None or record.cancel_requested or not health.intent_known:
            return  # 의도를 못 읽은 주기의 desired_state 는 마지막으로 읽은 값이다. 취소 근거가 아니다
        if health.state == HealthState.STOPPED or health.desired_state != "running":
            record.cancel_requested = True
            self._save()
            log.warning("vm %s: recovery %s cancel requested while attempt %d is running (desired=%s)", record.vm_id, record.recovery_id, record.attempt, health.desired_state)

    def _check_watcher(self, record: RecoveryRecord, now: float) -> None:
        health = self._watcher.health(record.vm_id)
        if health is None or not health.intent_known:
            return  # 의도를 못 읽었다: 오래된 desired_state 로 닫지 않고 다음 주기를 기다린다
        if health.state == HealthState.HEALTHY:
            # 우리 시도가 완료되지 않았는데 건강해졌다(사용자 조작·플랫폼 쪽 재시작·신호 복귀). 더 시도하지 않고 닫되,
            # completed(이 시도로 새 Xen 실행을 관측, 계약 2.5)로 보고하지 않는다
            self._close(record, RecoveryStatus.RECOVERED, now, error={"code": "TARGET_HEALTHY", "platform_code": None,
                                                                      "message": f"VM became healthy before attempt {record.attempt} completed"})
        elif health.state == HealthState.STOPPED or health.desired_state != "running":
            self._close(record, RecoveryStatus.CANCELLED, now, error={"code": "TARGET_NOT_ELIGIBLE", "platform_code": None,
                                                                      "message": f"desired_state={health.desired_state}, state={health.state.value}"})

    # ---- 시도 ----

    def _start(self, record: RecoveryRecord, now: float) -> None:
        if self._lost:
            if not self._lost_warned:
                self._lost_warned = True
                log.error("vm %s: recovery %s not executed: recovery history lost; preserve %s and its evidence for investigation", record.vm_id, record.recovery_id, self._store.path)
            return
        if not self._policy.enabled:
            if record.recovery_id not in self._skipped:
                self._skipped.add(record.recovery_id)
                log.warning("vm %s: recovery %s not executed: policy is none", record.vm_id, record.recovery_id)
            return
        if record.replay and record.request is not None:
            record.status = RecoveryStatus.EXECUTING
            record.replay = False
            record.due_monotonic = None
            self._save()
            self._submit(record, lambda: self._task_replay(record), now)
        else:
            self._submit(record, lambda: self._task_attempt(record), now)

    def _task_attempt(self, record: RecoveryRecord) -> dict[str, Any]:
        """실행 스레드. 슬롯을 얻은 지금의 view 와 시계로 spec 을 만들고, 저장한 뒤 플랫폼을 부른다."""
        view = self._platform.view(record.vm_id)
        if view.get("lifecycle") != "REGISTERED":
            raise _NotEligible(f"lifecycle={view.get('lifecycle')}")
        if view.get("desired_state") != "running" or not view.get("intent_applicable"):
            raise _NotEligible(f"desired_state={view.get('desired_state')} applicable={view.get('intent_applicable')}")
        hold = self._evidence_hold(record, view)
        if hold is None and self._drive_force(record, view):
            with self._lock:
                query = ProtectionQuery(record.recovery_id, record.vm_id, record.attempt, view.get("host_boot_id"), view.get("execution_id"),
                                        view.get("intent_revision"), view.get("generation"), view.get("domid"), view.get("xen_state"))
            if self._protection is None or not self._protection(query):  # I/O 일 수 있어 엔진 잠금 밖에서 한다
                hold = "drive VM without current Gateway protection"
        with self._lock:
            if record.cancel_requested or not record.open:
                raise _Cancelled()
            hold = hold or self._evidence_hold(record, view)  # 보호를 조회하는 동안 현재 증거가 바뀌었을 수 있다
            if hold is not None:
                raise _Held(hold)
            self._held.pop(record.recovery_id, None)
            now = self._clock()
            spec = self._build_spec(record, view, now)  # ValueError: 관측으로는 spec 을 만들 수 없음 → 최종 실패
            previous_closed = record.attempt_closed
            record.attempt += 1
            record.attempt_closed = False
            record.recovery_attempt_id = UUID(spec["recovery_attempt_id"])
            record.request = spec
            record.mode = spec["mode"]
            record.attempt_started_monotonic = now
            record.due_monotonic = None
            record.replay = False
            record.status = RecoveryStatus.EXECUTING
            self._emit(record, "recovery.started", now)
            if not self._save():
                record.pending_events.pop()
                record.attempt -= 1
                record.attempt_closed = previous_closed
                record.recovery_attempt_id, record.request, record.mode, record.attempt_started_monotonic = None, None, None, None
                record.status = RecoveryStatus.DETECTED if record.attempt == 0 else RecoveryStatus.BACKOFF
                raise _NotSaved()
            self._flush_pending(record)
            log.warning("vm %s: recovery %s attempt %d %s (attempt_id %s)", record.vm_id, record.recovery_id, record.attempt, record.mode, record.recovery_attempt_id)
        return self._platform.recover_once(spec, execute=True)

    def _task_replay(self, record: RecoveryRecord) -> dict[str, Any]:
        """같은 attempt id 로 결과를 조회하고, journal 이 없으면 같은 spec 을 다시 보낸다. 새 attempt id 는 만들지 않는다."""
        with self._lock:
            spec, attempt_id = dict(record.request or {}), record.recovery_attempt_id
        assert attempt_id is not None
        log.info("vm %s: recovery %s replaying attempt %d (%s)", record.vm_id, record.recovery_id, record.attempt, attempt_id)
        try:
            attempt = self._platform.attempt(attempt_id)
        except Dom0ManagerError as exc:
            if (exc.platform_code or "") not in GONE_CODES:
                raise
            attempt = None  # 플랫폼에 도달한 적이 없다
        if attempt is not None and attempt.get("phase") == "completed":
            return {"attempt": attempt, "original_result": attempt.get("result") or {}}
        if attempt is not None and attempt.get("phase") == "failed":
            error = attempt.get("error") or {}
            raise _platform_error(error.get("code", "XEN_EXECUTION_FAILED"), error.get("message", "recovery attempt failed"))
        if attempt is None:
            # 플랫폼에 닿은 적이 없다: 다시 보내기 전에 지금 상태를 본다. 그 사이 건강해졌거나 꺼졌으면 건강한 VM 을 재시작하지 않는다
            with self._lock:
                if record.cancel_requested or not record.open:
                    raise _Cancelled()
                health = self._watcher.health(record.vm_id)
            if health is not None and health.state == HealthState.HEALTHY:
                raise _Recovered(f"VM became healthy before the platform accepted attempt {record.attempt}")
            if health is not None and health.intent_known and (health.state == HealthState.STOPPED or health.desired_state != "running"):
                raise _NotEligible(f"desired_state={health.desired_state}, state={health.state.value}")
        # 의도를 못 읽은 건강 스냅샷으로는 취소하지 않는다. 원 spec 의 expected_intent_revision 을 플랫폼이 다시 확인한다
        return self._platform.recover_once(spec, execute=True)  # 진행 중·불확실·미도달: 플랫폼이 멱등하게 이어 가거나 RECONCILE_REQUIRED

    def _submit(self, record: RecoveryRecord, work: Callable[[], dict[str, Any]], now: float) -> None:
        if self._inline or self._executor is None:
            future: Future = Future()
            try:
                future.set_result(work())
            except BaseException as exc:  # noqa: BLE001 - Future 에 담아 같은 경로로 처리
                future.set_exception(exc)
            self._finish(record, future, now)
            return
        self._futures[record.recovery_id] = self._executor.submit(work)

    def _finish(self, record: RecoveryRecord, future: Future, now: float) -> None:
        try:
            result = future.result()
        except _NotEligible as exc:
            self._close(record, RecoveryStatus.CANCELLED, now, error={"code": "TARGET_NOT_ELIGIBLE", "platform_code": None, "message": str(exc)})
        except _Cancelled:
            self._close(record, RecoveryStatus.CANCELLED, now, error={"code": "TARGET_NOT_ELIGIBLE", "platform_code": None, "message": "cancelled before the platform accepted the attempt"})
        except _Recovered as exc:
            self._close(record, RecoveryStatus.RECOVERED, now, error={"code": "TARGET_HEALTHY", "platform_code": None, "message": str(exc)})
        except _Held as exc:
            record.due_monotonic = now + self._policy.transient_retry_s  # attempt·상태·이벤트는 그대로다
            if self._held.get(record.recovery_id) != str(exc):
                self._held[record.recovery_id] = str(exc)
                log.warning("vm %s: recovery %s force-restart held: %s; rechecking every %.0fs", record.vm_id, record.recovery_id, exc,
                            self._policy.transient_retry_s)
        except _NotSaved:
            record.due_monotonic = now + self._policy.transient_retry_s  # 상태는 _task_attempt 가 되돌렸다
            if record.status == RecoveryStatus.DETECTED:
                record.status = RecoveryStatus.BACKOFF if record.attempt > 0 else RecoveryStatus.DETECTED
            log.error("vm %s: recovery %s attempt not recorded; retrying in %.0fs", record.vm_id, record.recovery_id, self._policy.transient_retry_s)
        except Dom0ManagerError as exc:
            confirmed_failure = exc.platform_code in (GONE_CODES | REOBSERVE_CODES | {'XEN_EXECUTION_FAILED','DEADLINE_EXCEEDED','RECOVERY_MODE_UNSUPPORTED'})
            if record.cancel_requested and confirmed_failure:
                self._close(record, RecoveryStatus.CANCELLED, now, error={"code": "TARGET_NOT_ELIGIBLE", "platform_code": exc.platform_code, "message": f"cancelled; last platform error: {exc.message}"})
            else:
                self._on_error(record, exc, now)
        except ValueError as exc:  # spec 을 만들 수 없는 관측 (unmanaged 실행 등)
            self._close(record, RecoveryStatus.FAILED, now, error={"code": "RECONCILE_REQUIRED", "platform_code": None, "message": str(exc)}, final=True)
        except Exception as exc:  # noqa: BLE001 - 정체 모를 예외: 더 파괴적인 시도로 올리지 않고 운영자에게 넘긴다
            log.exception("vm %s: recovery %s attempt raised", record.vm_id, record.recovery_id)
            if record.request is not None and record.status == RecoveryStatus.EXECUTING:
                # A lost response is not proof of failure, nor of cancellation.
                # Reconcile only the original attempt; never escalate this error.
                self._on_error(record, DriverTimeout("attempt outcome unavailable; consult original journal"), now)
            else:
                self._close(record, RecoveryStatus.FAILED, now, error={"code": "INTERNAL_ERROR", "platform_code": None, "message": f"{type(exc).__name__}: {exc}"}, final=True)
        else:
            if record.cancel_requested:  # 플랫폼이 의도 변경 전에 새 실행을 올렸다. 사실대로 완료로 닫는다(멈춤 의도는 플랫폼·게이트웨이가 따로 적용)
                log.warning("vm %s: recovery %s attempt %d completed although a cancel was requested meanwhile", record.vm_id, record.recovery_id, record.attempt)
            self._on_result(record, result, now)

    def _build_spec(self, record: RecoveryRecord, view: dict[str, Any], now: float) -> dict[str, Any]:
        present = view.get("domid") is not None
        if not present:
            revision = view.get("observation_revision")
            if type(revision) is not int or revision < 1:
                raise ValueError("domain absent but the platform has no observation_revision; cannot plan start-if-absent")
            mode, expected = "start-if-absent", {"absent_observation_revision": revision}
        else:
            execution_id = view.get("execution_id")
            if not execution_id:
                raise ValueError("domain present but not a managed execution (no execution_id); recovery would not be correlated")
            mode, expected = ("force-restart" if self._wants_force(record, view) else "graceful-restart"), {"execution_id": str(execution_id)}
        for name in ("host_boot_id", "generation", "intent_revision"):
            if view.get(name) is None:
                raise ValueError(f"platform view lacks {name}")
        return {
            "vm_id": str(record.vm_id),
            "recovery_attempt_id": str(self._new_id()),
            "expected_host_boot_id": str(view["host_boot_id"]),
            "expected_lifecycle_generation": int(view["generation"]),
            "expected_intent_revision": int(view["intent_revision"]),
            "expected_execution": expected,
            "mode": mode,
            "deadline_monotonic_ms": int((now + self._policy.attempt_deadline_s) * 1000),
        }

    @staticmethod
    def _wants_force(record: RecoveryRecord, view: dict[str, Any]) -> bool:
        """현재 view·실행으로 정한다: Zephyr(graceful 없음), 지금 crashed, 지금 실행이 graceful 에 실패했던 그 실행."""
        return (record.guest_kind == "zephyr" or view.get("xen_state") == "crashed"
                or (record.graceful_failed_execution_id is not None and record.graceful_failed_execution_id == view.get("execution_id")))

    def _drive_force(self, record: RecoveryRecord, view: dict[str, Any]) -> bool:
        """이 시도가 주행 VM 의 force-restart 인가(Gateway 보호 근거가 필요하다)."""
        return (view.get("domid") is not None and self._wants_force(record, view)
                and self._drive_vm_ids is not None and str(record.vm_id) in self._drive_vm_ids)

    def _evidence_hold(self, record: RecoveryRecord, view: dict[str, Any]) -> str | None:
        """force-restart 를 고를 시도의 현재 증거가 부족하면 그 이유. 도메인이 없거나(start-if-absent) graceful 이면 None. I/O 없음."""
        if view.get("domid") is None or not self._wants_force(record, view):
            return None
        zephyr = record.guest_kind == "zephyr"
        if zephyr and self._drive_vm_ids is None:
            return "drive VMs are unknown (no drive config); every Zephyr force-restart is held"
        health = self._watcher.health(record.vm_id)
        if health is None or not health.intent_known:
            return "no current health with a known intent"
        if health.state != HealthState.UNHEALTHY or health.reason is None:
            return f"current health is {health.state.value}, not UNHEALTHY"
        evidence = health.reason
        if evidence == FailureReason.DOMAIN_CRASHED:
            if view.get("xen_state") != "crashed":
                return f"crash evidence but the platform now observes {view.get('xen_state')}"
        elif not ((evidence == FailureReason.APP_HEARTBEAT_LOST and zephyr) or
                  (record.graceful_failed_execution_id == view.get("execution_id") and evidence.value == record.reason)):
            return f"current evidence {evidence.value} does not justify force-restart"
        if health.domid != view.get("domid"):
            return f"evidence is for domid {health.domid}, platform runs domid {view.get('domid')}"
        drive = self._drive_vm_ids is not None and str(record.vm_id) in self._drive_vm_ids
        if drive and (not health.execution_id or health.execution_id != view.get("execution_id")):
            return f"drive VM evidence for execution {health.execution_id}, platform runs {view.get('execution_id')}"
        if not drive and health.execution_id is not None and health.execution_id != view.get("execution_id"):
            return f"evidence for execution {health.execution_id}, platform runs {view.get('execution_id')}"
        return None

    def _on_result(self, record: RecoveryRecord, result: dict[str, Any], now: float) -> None:
        original = result.get("original_result") or {}
        execution_id = original.get("execution_id")
        previous = ((record.request or {}).get("expected_execution") or {}).get("execution_id")
        if not isinstance(execution_id, str) or not execution_id or execution_id == previous:
            # completed 인데 새 실행 ID 가 없다: 계약 2.5 의 완료 조건(이 시도로 새 Xen 실행 관측)을 확인할 수 없다. 재시작하지 않고 운영자에게 넘긴다
            self._close(record, RecoveryStatus.FAILED, now, final=True, error={
                "code": "RECONCILE_REQUIRED", "platform_code": None,
                "message": f"platform reported completed without a new execution_id (got {execution_id!r}, expected other than {previous!r})"})
            return
        record.execution_id = execution_id
        record.last_error = None
        self._close(record, RecoveryStatus.COMPLETED, now)

    def _on_error(self, record: RecoveryRecord, exc: Dom0ManagerError, now: float) -> None:
        code = exc.platform_code or exc.code
        error = {"code": code, "platform_code": exc.platform_code, "message": exc.message}
        log.warning("vm %s: recovery %s attempt %d: %s (%s)", record.vm_id, record.recovery_id, record.attempt, code, exc.message)
        if code in GONE_CODES:
            self._close(record, RecoveryStatus.CANCELLED, now, error=error)
        elif code in TRANSIENT_CODES or (exc.platform_code is None and
                (isinstance(exc, DriverTimeout) or (record.request is not None and record.status==RecoveryStatus.EXECUTING))):
            # 일시 거절 또는 응답 유실: 결과를 모르는 시도로 두고(EXECUTING) journal 을 먼저 조회한 뒤 같은 시도를 다시 보낸다.
            # spec 의 deadline 이 지났으면 플랫폼이 DEADLINE_EXCEEDED 로 닫아 준다
            if record.request is not None and now*1000 >= record.request['deadline_monotonic_ms'] + self._policy.verify_timeout_s*1000:
                self._close(record, RecoveryStatus.FAILED, now, final=True, error={
                    'code':'RECONCILE_REQUIRED','platform_code':None,
                    'message':'original attempt outcome unresolved after bounded journal recheck; no new physical attempt'})
                return
            record.last_error = error
            if record.status == RecoveryStatus.EXECUTING and record.request is not None:
                record.replay = True
            elif record.status == RecoveryStatus.DETECTED and record.attempt == 0:
                pass  # 시도 전 view 가 거절됨: DETECTED 로 두고 잠시 뒤 다시 시작
            else:
                record.status = RecoveryStatus.BACKOFF
            record.due_monotonic = now + self._policy.transient_retry_s
            self._save()
        elif code in REOBSERVE_CODES:
            record.last_error = error
            record.status = RecoveryStatus.VERIFYING  # 대상이 바뀌었다: 사용자 조작이면 STOPPED 로 취소되고, 살아났으면 HEALTHY 로 닫힌다
            record.due_monotonic = now + self._policy.verify_timeout_s
            self._save()
        elif code in FINAL_CODES:
            self._close(record, RecoveryStatus.FAILED, now, error=error, final=True)
        elif code == "RECOVERY_MODE_UNSUPPORTED" and not (record.mode == "graceful-restart" and self._policy.escalate_to_force):
            self._close(record, RecoveryStatus.FAILED, now, error=error, final=True)
        else:
            graceful_failure = code in GRACEFUL_FAILURE_CODES or (isinstance(exc, DriverTimeout) and exc.platform_code == "XEN_EXECUTION_FAILED")
            if record.mode == "graceful-restart" and self._policy.escalate_to_force and graceful_failure:
                record.graceful_failed_execution_id = ((record.request or {}).get("expected_execution") or {}).get("execution_id")
            self._attempt_failed(record, now, error)

    def _attempt_failed(self, record: RecoveryRecord, now: float, error: dict[str, Any]) -> None:
        record.last_error = error
        if record.attempt >= self._policy.max_attempts:
            self._close(record, RecoveryStatus.FAILED, now, error=error, final=True)
            return
        wait = self._policy.backoff_after(record.attempt)
        record.status = RecoveryStatus.BACKOFF
        record.replay = False
        record.due_monotonic = now + wait
        self._emit(record, "recovery.failed", now, final=False, next_retry_at=self._iso(self._wall() + timedelta(seconds=wait)))
        record.attempt_closed = True
        self._commit(record)
        log.warning("vm %s: recovery %s attempt %d failed (%s); next attempt in %.0fs", record.vm_id, record.recovery_id, record.attempt,
                    error.get("platform_code") or error.get("code"), wait)

    def _close(self, record: RecoveryRecord, status: RecoveryStatus, now: float, *, error: dict[str, Any] | None = None, final: bool = False) -> None:
        record.status = status
        record.closed_monotonic = now
        record.due_monotonic = None
        record.replay = False
        record.final = final or status in (RecoveryStatus.FAILED, RecoveryStatus.CANCELLED, RecoveryStatus.RECOVERED)
        self._held.pop(record.recovery_id, None)
        if error is not None:
            record.last_error = error
        self._futures.pop(record.recovery_id, None)
        if status == RecoveryStatus.COMPLETED:
            log.warning("vm %s: recovery %s completed after %d attempt(s) in %.0fs (execution %s)", record.vm_id, record.recovery_id, record.attempt,
                        now - record.detected_monotonic, record.execution_id)
            if record.attempt > 0:
                self._emit(record, "recovery.completed", now)
        else:
            level = log.error if status == RecoveryStatus.FAILED else log.warning
            level("vm %s: recovery %s %s after %d attempt(s): %s", record.vm_id, record.recovery_id, status.value.lower(), record.attempt,
                  (error or {}).get("message"))
            if record.attempt > 0:  # 시도가 있었던 장애만 종료 이벤트를 낸다(시도 없이 닫히면 started 도 없었다)
                if status == RecoveryStatus.RECOVERED:
                    self._emit(record, "recovery.recovered", now)
                elif status == RecoveryStatus.CANCELLED:
                    self._emit(record, "recovery.cancelled", now)
                else:
                    self._emit(record, "recovery.failed", now, final=True)
        self._commit(record)

    # ---- 이벤트 (계약 2.5) ----

    def _emit(self, record: RecoveryRecord, event_type: str, now: float, *, final: bool | None = None, next_retry_at: str | None = None) -> None:
        """이벤트를 레코드의 발행 대기 목록에 넣는다(event_id 는 여기서 고정). 레코드가 저장된 뒤 _flush_pending 이 outbox 로 옮긴다."""
        attempt_ms = None if record.attempt_started_monotonic is None else int((now - record.attempt_started_monotonic) * 1000)
        completed = event_type == "recovery.completed"
        # 이미 종료 이벤트를 낸 시도 뒤(backoff 중)에 온 recovered·cancelled 는 장애 단위 종료다. 시도에 붙이면 한 시도에 종료가 둘이 된다
        detached = event_type in ("recovery.recovered", "recovery.cancelled") and record.attempt_closed
        ends_recovery = event_type in ("recovery.completed", "recovery.recovered", "recovery.cancelled") or bool(final)
        carries_cause = event_type in ("recovery.failed", "recovery.cancelled")
        event = {
            "event_id": str(self._new_id()),
            "type": event_type,
            "vm_id": str(record.vm_id),
            "recovery_id": str(record.recovery_id),
            "recovery_attempt_id": None if record.recovery_attempt_id is None or detached else str(record.recovery_attempt_id),
            "attempt": record.attempt,
            "reason": record.reason,
            "mode": record.mode,
            "detected_at": record.detected_at,
            "occurred_at": self._iso(self._wall()),
            "attempt_duration_ms": None if event_type == "recovery.started" or detached else attempt_ms,
            "recovery_duration_ms": int((now - record.detected_monotonic) * 1000) if ends_recovery else None,
            "execution_id": record.execution_id if completed else None,
            "final": final if event_type == "recovery.failed" else None,
            "next_retry_at": next_retry_at,
            "error": None if not carries_cause or record.last_error is None else {  # failed 는 실패 사유, cancelled 는 취소 사유
                "code": record.last_error.get("code"),  # Worker 의 결과 코드(TARGET_NOT_ELIGIBLE 등). 플랫폼 응답이면 platform_code 와 같다
                "platform_code": record.last_error.get("platform_code"),  # 플랫폼 원본 코드(8.1절)만. Worker 가 스스로 끝냈으면 null
                "message": record.last_error.get("message"),
            },
        }
        record.pending_events.append(event)

    def _commit(self, record: RecoveryRecord) -> bool:
        """상태 전이를 저장하고, 저장됐을 때만 대기 중인 이벤트를 outbox 로 옮긴다."""
        if not self._save():
            return False
        self._flush_pending(record)
        return True

    def _flush_pending(self, record: RecoveryRecord) -> None:
        if not record.pending_events:
            return
        if self._on_event is None:
            record.pending_events.clear()
            return
        moved = 0
        for event in list(record.pending_events):
            try:
                stored = self._on_event(event)
            except Exception:  # noqa: BLE001 - outbox 실패는 복구를 막지 않는다. 이벤트는 레코드에 남아 다음 주기에 다시
                log.exception("recovery event sink failed")
                stored = False
            if stored is False:
                break  # 기록 효과는 미확정. pending을 보존하고 같은 ID/내용으로 재시도한다.
            record.pending_events.pop(0)
            moved += 1
        if moved:
            # 옮긴 이벤트를 대기 목록에서 지운 상태를 남긴다. 여기서 실패하거나 그 전에 죽으면 재시작 뒤 같은 event_id 로 한 번 더
            # 저장·발행된다. outbox 는 event_id 로 중복을 걸러 읽고 수신 쪽(Spring)도 event_id 로 거른다
            self._save()

    def _retry_pending(self) -> None:
        """저장 못 한 상태와 outbox 로 못 옮긴 이벤트를 다시 시도한다. 매 주기."""
        if self._dirty and not self._save():
            return  # 상태가 저장되기 전에는 이벤트를 내지 않는다
        for record in [r for r in self._records.values() if r.pending_events]:
            self._flush_pending(record)
        left = sum(len(r.pending_events) for r in self._records.values())
        if left:
            log.error("recovery worker degraded: %d recovery event(s) not stored in the outbox yet; kept in %s and retried every cycle", left, self._store.path)

    # ---- 보조 ----

    def _save(self) -> bool:
        saved = self._store.save(self._records)
        self._dirty = not saved
        return saved

    @staticmethod
    def _due(record: RecoveryRecord, now: float) -> bool:
        return record.due_monotonic is None or now >= record.due_monotonic

    def _prune(self) -> None:
        closed = sorted((r for r in self._records.values() if not r.open and not r.pending_events), key=lambda r: r.closed_monotonic or 0.0)
        stale = closed[:-KEEP_CLOSED] if len(closed) > KEEP_CLOSED else []
        for record in stale:
            del self._records[record.recovery_id]
        if stale:
            self._save()

    @staticmethod
    def _iso(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _platform_error(code: str, message: str) -> Dom0ManagerError:
    """journal 의 failed 기록을 recover_once 가 냈을 것과 같은 예외로."""
    from ..drivers.platform import raise_for_envelope

    try:
        raise_for_envelope({"ok": False, "error": {"code": code, "message": message}}, context="recovery-operation")
    except Dom0ManagerError as exc:
        return exc
    raise AssertionError("unreachable")
