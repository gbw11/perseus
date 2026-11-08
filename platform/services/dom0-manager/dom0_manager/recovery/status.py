"""Worker 자가진단 상태(S15P31E110-354). owner의 표본과 부가 I/O를 분리하고 boot를 확인한 reader를 제공한다.

- overall: OPERABLE(필요한 관측·복구 경로 정상) / DEGRADED(일부 경로가 막힘. 무엇이 보류되는지는 hold_reasons) /
  DISABLED(XENOVA_RECOVERY_POLICY=none). 읽는 쪽은 여기에 STALE(갱신이 끊김) / UNAVAILABLE(파일 없음·손상)을 더한다.
- 계정이 없거나 ACL 이 막혀도 Worker 는 멈추지 않는다. MQTT 가 막혀도 Xen crashed·absent 판정은 계속되고, 보류되는 것은
  heartbeat·app heartbeat 손실 판정이다. 상태가 바뀔 때만 한 번 로그한다.
- 상태 파일에는 비밀번호·계정 이름을 넣지 않는다. 계정이 있는지만 적는다.
- 신선도는 단조 시각(CLOCK_MONOTONIC, host 전체 공통)으로 판정한다. RTC 없는 보드는 부팅 뒤 벽시계가 크게 뛴다.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from .model import VmObservation
from .protection import GatewayRefused, GatewayUncertain

log = logging.getLogger(__name__)

STATUS_FILE = "worker-status.json"
STATUS_VERSION = 2

OPERABLE, DEGRADED, DISABLED = "OPERABLE", "DEGRADED", "DISABLED"
STALE, UNAVAILABLE = "STALE", "UNAVAILABLE"  # 읽는 쪽만 낸다
REPORTED = (OPERABLE, DEGRADED, DISABLED)

# 채널 상태 (Watcher.link_report)
NOT_GRANTED, WAITING, DELIVERING, SILENT = "NOT_GRANTED", "WAITING", "DELIVERING", "SILENT"
CHANNEL_STATES = (NOT_GRANTED, WAITING, DELIVERING, SILENT)
POLICIES = ("restart", "none")
HOLD_REASONS = (
    "BROKER_CREDENTIALS_MISSING", "BROKER_DISCONNECTED",
    "AGENT_SUBSCRIPTION_NOT_GRANTED", "APP_SUBSCRIPTION_NOT_GRANTED", "AGENT_DELIVERY_UNPROVEN", "APP_DELIVERY_UNPROVEN",
    "OBSERVATION_FAILED", "INTENTS_UNAVAILABLE",
    "DRIVE_CONFIG_MISSING", "DRIVE_VM_NOT_CONFIGURED", "GATEWAY_CLIENT_MISSING", "GATEWAY_UNAVAILABLE",
    "RECOVERY_HISTORY_LOST", "RECOVERY_STATE_UNSAVED", "RECOVERY_EVENTS_PENDING", "ARMED_HISTORY_LOST",
    "RECOVERY_BUDGET_UNKNOWN", "RECOVERY_BUDGET_EXHAUSTED", "RECOVERY_BUDGET_CONTEXT_UNAVAILABLE",
)
STATUS_FIELDS = (
    "version", "overall", "policy", "broker_configured", "broker_connected", "agent_subscription", "app_subscription",
    "drive_configured", "gateway_available", "intents_known", "last_successful_tick", "hold_reasons", "updated_at",
    "updated_monotonic", "stale_after_s", "pid", "host_boot_id",
)

STALE_PERIODS = 3  # 감시 주기 × 3 동안 갱신이 없으면 STALE
STALE_MIN_S = 15.0
GATEWAY_PROBE_S = 30.0
STATUS_DRAIN_S = 0.25  # 부가 기록 종료 대기. 살아 있는 실행자와 성공 drain은 구분한다.


def _valid_boot_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        identity = UUID(value)
        return str(identity) == value and identity.int != 0
    except ValueError:
        return False


def _read_host_boot_id() -> str:
    value = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if not _valid_boot_id(value):
        raise ValueError('invalid host boot identity')
    return value


@dataclass(frozen=True)
class _StatusSample:
    # owner가 만든 작은 표본만 넘긴다. Watcher/Engine 객체나 관측 목록은 넘기지 않는다.
    status: dict[str, Any] | None
    gateway_index: int = 0
    boot_error: Exception | None = None


class GatewayProbe:
    """Gateway 보호를 지금 쓸 수 있는지 context 조회로 확인한다. 주기마다 부르지 않고 probe_s 마다 한 번."""

    def __init__(self, gateway: Any, *, probe_s: float = GATEWAY_PROBE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._gateway = gateway
        self._probe_s = probe_s
        self._clock = clock
        self._at: float | None = None
        self.available: bool | None = None
        self.code: str | None = None

    def check(self) -> bool | None:
        now = self._clock()
        if self._at is not None and now - self._at < self._probe_s:
            return self.available
        self._at = now
        try:
            self._gateway.context()
        except (GatewayUncertain, GatewayRefused) as exc:
            self.available, self.code = False, exc.code
        except Exception as exc:  # noqa: BLE001 - 진단이 감시 루프를 멈추게 하지 않는다
            self.available, self.code = False, type(exc).__name__
        else:
            self.available, self.code = True, None
        return self.available


class StatusReporter:
    """owner의 표본을 한 실행자에 넘긴다. 저장 중 1개와 최신 대기 1개만 보유한다.

    submit은 감시 경로, report는 실행자를 시작하기 전의 동기 단건 호출이다.
    원 표본 시각은 저장/재시도 시 갱신하지 않는다. 필수 복구 기록은 처리하지 않는다.
    """

    def __init__(
        self,
        state_dir: str | Path,
        *,
        policy: str,
        broker_configured: bool,
        drive_vm_ids: Iterable[str] | None,
        gateway_client: bool | None,
        interval_s: float,
        connected: Callable[[], bool],
        links: Callable[[float], dict[str, str]],
        publishers: Callable[[], dict[str, bool]],
        intent_known: Callable[[], bool],
        gateway: GatewayProbe | None = None,
        internal: Callable[[], list[str]] = list,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        boot_id: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(state_dir) / STATUS_FILE
        self._policy = policy
        self._broker_configured = broker_configured
        self._drive_vm_ids = None if drive_vm_ids is None else {str(vm) for vm in drive_vm_ids}
        self._gateway_client = gateway_client  # None: drive config 가 없어 쓰지 않음. False: import 실패
        self._stale_after_s = max(STALE_PERIODS * interval_s, STALE_MIN_S)
        self._connected = connected
        self._links = links
        self._publishers = publishers  # 채널마다 지금 보내야 할 VM 이 있는가(Watcher.expected_publishers)
        self._intent_known = intent_known
        self._gateway = gateway
        self._internal = internal  # Engine·감시기 저장소 상태(이력 손실·저장 실패·outbox 대기)
        self._clock = clock
        self._wall = wall
        self._boot_id = _read_host_boot_id if boot_id is None else boot_id
        self._host_boot_id: str | None = None
        self._observations: list[VmObservation] = []
        self._last_success: str | None = None
        self._last: tuple[str, tuple[str, ...]] | None = None
        self._write_failed = False
        self._boot_failed = False
        self._condition = threading.Condition()
        self._pending: _StatusSample | None = None
        self._thread: threading.Thread | None = None
        self._closing = False
        self._in_flight = False
        self._submitted = self._coalesced = self._written = self._failures = 0
        self.first_error: Exception | None = None
        self.last_error: Exception | None = None
        self.first_internal_error: Exception | None = None
        self._last_internal_site: tuple | None = None

    def report(self, observations: list[VmObservation] | None) -> dict[str, Any]:
        """동기 단건 진단. 감시 루프는 submit을 사용한다."""
        if self._thread is not None or self._closing:
            raise RuntimeError('status publisher is asynchronous or closed; use submit')
        return self._publish(self._collect(observations))

    def submit(self, observations: list[VmObservation] | None) -> bool:
        """작은 표본의 소유권을 넘긴다. 포화 시 대기 표본만 최신 것으로 교체한다."""
        with self._condition:
            if self._closing:
                return False
        sample = self._collect(observations)
        with self._condition:
            if self._closing:
                return False
            if self._thread is None:
                thread = threading.Thread(target=self._run, name='recovery-status', daemon=True)
                thread.start()
                self._thread = thread
            self._coalesced += self._pending is not None
            self._submitted += 1
            self._pending = sample
            self._condition.notify()
        return True

    def _collect(self, observations: list[VmObservation] | None) -> _StatusSample:
        """Watcher/Engine의 작은 metadata는 해당 owner에서 읽는다. sink I/O는 수행하지 않는다."""
        if self._host_boot_id is None:
            try:
                boot = self._boot_id()
                if not _valid_boot_id(boot):
                    raise ValueError('invalid host boot identity')
                self._host_boot_id = boot
            except (OSError, ValueError) as error:
                return _StatusSample(None, boot_error=error)
        now = self._clock()
        observed = observations is not None
        if observed:
            self._observations = observations
        intent_known = observed and self._intent_known()
        if intent_known:
            self._last_success = self._wall().isoformat()
        connected = bool(self._connected())
        links = self._links(now)
        reasons = self._hold_reasons(observed, intent_known, connected, links)
        gateway_index = len(reasons)
        reasons.extend(reason for reason in self._internal() if reason not in reasons)
        overall = DISABLED if self._policy == "none" else DEGRADED if reasons else OPERABLE
        status = {
            "version": STATUS_VERSION,
            "host_boot_id": self._host_boot_id,
            "overall": overall,
            "policy": self._policy,
            "broker_configured": self._broker_configured,
            "broker_connected": connected,
            "agent_subscription": links["agent"],
            "app_subscription": links["app"],
            "drive_configured": self._drive_vm_ids is not None,
            "gateway_available": None,
            "intents_known": intent_known,
            "last_successful_tick": self._last_success,
            "hold_reasons": reasons,
            "updated_at": self._wall().isoformat(),
            "updated_monotonic": now,
            "stale_after_s": self._stale_after_s,
            "pid": os.getpid(),
        }
        return _StatusSample(status, gateway_index)

    def _publish(self, sample: _StatusSample) -> dict[str, Any]:
        if sample.boot_error is not None:
            self._record_failure(sample.boot_error)
            if not self._boot_failed:
                self._boot_failed = True
                log.error('worker status host boot context unavailable (%s, errno=%s)',
                          type(sample.boot_error).__name__, getattr(sample.boot_error, 'errno', None))
            return {'overall': UNAVAILABLE, 'reason': 'host boot context unavailable'}
        if self._boot_failed:
            self._boot_failed = False
            log.info('worker status host boot context available again')
        status = sample.status
        assert status is not None  # 소유한 표본의 불변조건이며 외부 입력 검증이 아니다.
        reasons = status['hold_reasons']
        gateway = self._gateway.check() if self._gateway is not None else None
        status['gateway_available'] = gateway
        if self._drive_vm_ids is not None and self._gateway_client is not False and gateway is False:
            reasons.insert(sample.gateway_index, 'GATEWAY_UNAVAILABLE')
        status['overall'] = DISABLED if self._policy == 'none' else DEGRADED if reasons else OPERABLE
        self._log_transition(status['overall'], reasons)
        self._write(status)
        return status

    def _record_failure(self, error: Exception) -> bool:
        with self._condition:
            first = self.first_error is None
            if first:
                self.first_error = error
            self.last_error = error
            self._failures += 1
        return first

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closing)
                if self._pending is None:
                    return
                sample, self._pending = self._pending, None
                self._in_flight = True
            try:
                self._publish(sample)
            except Exception as error:  # 진단 격리 경계. 원 exception/cause는 유한하게 보존한다.
                self._record_failure(error)
                trace = error.__traceback__
                while trace is not None and trace.tb_next is not None:
                    trace = trace.tb_next
                # 원문/대형 repr 없이 연속된 같은 내부 결함만 억제한다. 상태는 발생 위치 한 개다.
                site = (type(error), None if trace is None else trace.tb_frame.f_code,
                        None if trace is None else trace.tb_lineno, type(error.__cause__), getattr(error, 'errno', None))
                with self._condition:
                    if self.first_internal_error is None:
                        self.first_internal_error = error
                    changed = site != self._last_internal_site
                    self._last_internal_site = site
                if changed:
                    try:
                        log.exception('worker status publication failed; readers retain the original lease')
                    except Exception as sink_error:
                        self._record_failure(sink_error)  # 같은 실패 sink로 재귀 기록하지 않는다.
            else:
                with self._condition:
                    self._last_internal_site = None
            finally:
                with self._condition:
                    self._in_flight = False
                    self._condition.notify_all()

    def close(self) -> bool:
        """유한 drain 뒤 반환한다. False는 실행자가 남았다는 뜻이며 성공 정리로 표시하지 않는다."""
        with self._condition:
            self._closing = True
            thread = self._thread
            self._condition.notify_all()
        if thread is not None:
            thread.join(STATUS_DRAIN_S)
        return thread is None or not thread.is_alive()

    def sink_snapshot(self) -> dict[str, Any]:
        """부가 기록의 유한 상태. 표본/원문/stack을 공개 결과로 복제하지 않는다."""
        with self._condition:
            detail = lambda error: None if error is None else {
                'cause_type': type(error).__name__, 'errno': getattr(error, 'errno', None)}
            return {'submitted': self._submitted, 'coalesced': self._coalesced, 'written': self._written,
                    'failures': self._failures, 'pending': int(self._pending is not None),
                    'in_flight': self._in_flight, 'closing': self._closing,
                    'worker_alive': self._thread is not None and self._thread.is_alive(),
                    'first_error': detail(self.first_error), 'last_error': detail(self.last_error),
                    'first_internal_error': detail(self.first_internal_error)}

    def _hold_reasons(self, observed: bool, intent_known: bool, connected: bool, links: dict[str, str]) -> list[str]:
        reasons: list[str] = []
        if not self._broker_configured:
            reasons.append("BROKER_CREDENTIALS_MISSING")
        if not connected:
            reasons.append("BROKER_DISCONNECTED")
        else:
            # 연결이 없으면 구독도 없다. 같은 원인을 두 번 적지 않는다
            for channel, code in (("agent", "AGENT"), ("app", "APP")):
                if links[channel] == NOT_GRANTED:
                    reasons.append(f"{code}_SUBSCRIPTION_NOT_GRANTED")
            # 한 창 동안 받은 것이 없다. 보내야 할 VM 이 있을 때만 이유로 센다(Mosquitto 는 ACL 거부도 SUBACK 성공이고,
            # ACL 을 다시 읽으면 구독은 그대로인 채 전달만 멈춘다). 보내야 할 VM 이 하나뿐이면 그 VM 의 장애와 구별되지 않는다
            publishers = self._publishers()
            for channel, code in (("agent", "AGENT"), ("app", "APP")):
                if links[channel] == SILENT and publishers.get(channel):
                    reasons.append(f"{code}_DELIVERY_UNPROVEN")
        if not observed:
            reasons.append("OBSERVATION_FAILED")
        elif not intent_known:
            reasons.append("INTENTS_UNAVAILABLE")  # 이 동안은 crashed·absent 를 포함한 모든 판정이 보류된다
        zephyr = [str(obs.vm_id) for obs in self._observations if obs.guest_kind == "zephyr" and obs.lifecycle == "REGISTERED"]
        if self._drive_vm_ids is None:
            if zephyr:
                reasons.append("DRIVE_CONFIG_MISSING")  # 주행 VM 을 모르면 엔진이 Zephyr force-restart 를 모두 보류한다
        else:
            if any(vm not in self._drive_vm_ids for vm in zephyr):
                reasons.append("DRIVE_VM_NOT_CONFIGURED")  # 설정에 없는 Zephyr VM 의 force-restart 는 보류된다
            if self._gateway_client is False:
                reasons.append("GATEWAY_CLIENT_MISSING")
        return reasons

    def _log_transition(self, overall: str, reasons: list[str]) -> None:
        current = (overall, tuple(reasons))
        if current == self._last:
            return
        self._last = current
        if overall == OPERABLE:
            log.info("recovery worker OPERABLE: liveness, intent and protection paths are available")
        elif overall == DISABLED:
            log.warning("recovery worker DISABLED: XENOVA_RECOVERY_POLICY=none, judgments and events only%s",
                        f" (also {', '.join(reasons)})" if reasons else "")
        else:
            gateway = f" gateway_code={self._gateway.code}" if self._gateway is not None and self._gateway.code else ""
            log.error("recovery worker DEGRADED: %s%s; see %s", ", ".join(reasons), gateway, self.path)

    def _write(self, status: dict[str, Any]) -> None:
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{STATUS_FILE}.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(status, stream, ensure_ascii=False)
            os.replace(tmp, self.path)
            tmp = None
        except OSError as exc:
            first = not self._write_failed
            self._write_failed = True
            self._record_failure(exc)
            if first:
                log.error("worker status file %s could not be written (%s); readers will see it as stale", self.path, exc)
            return
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        with self._condition:
            self._written += 1
        if self._write_failed:
            log.info("worker status file %s written again", self.path)
            self._write_failed = False


def read_status(state_dir: str | Path, *, now: float | None = None,
                boot_id: Callable[[], str] | None = None) -> dict[str, Any]:
    """dom0-manager 쪽 읽기. Worker 가 죽으면 파일은 마지막 값으로 남으므로 갱신이 오래되면 그 값을 내세우지 않고 STALE 이다.
    파일이 없거나 읽지 못하거나 모양이 어긋나면 UNAVAILABLE 이다. 정상이면 파일 내용에 age_s 를 더해 돌려준다."""
    path = Path(state_dir) / STATUS_FILE
    now = time.monotonic() if now is None else now
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"overall": UNAVAILABLE, "reason": "worker status file is missing; the worker has not started or uses another state dir"}
    except OSError as exc:
        return {"overall": UNAVAILABLE, "reason": f"worker status file cannot be read ({exc.strerror or exc})"}
    try:
        status = json.loads(raw)
    except ValueError:
        return {"overall": UNAVAILABLE, "reason": "worker status file is not valid JSON"}
    problem = _status_problem(status)
    if problem is not None:
        return {"overall": UNAVAILABLE, "reason": f"worker status file is malformed ({problem})"}
    try:
        current_boot = (_read_host_boot_id if boot_id is None else boot_id)()
        if not _valid_boot_id(current_boot):
            raise ValueError('invalid host boot identity')
    except (OSError, ValueError) as error:
        return {'overall': UNAVAILABLE, 'reason': f'current host boot context unavailable ({type(error).__name__})'}
    if status['host_boot_id'] != current_boot:
        return {'overall': STALE, 'reason': 'host boot changed', 'last': status}
    age = now - status["updated_monotonic"]
    if age < 0 or age > status["stale_after_s"]:
        # host를 확인한 뒤에만 같은 시계 영역의 나이를 계산한다.
        return {"overall": STALE, "age_s": round(age, 1), "stale_after_s": status["stale_after_s"], "last": status}
    return {**status, "age_s": round(age, 1)}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value: Any) -> bool:
    """UTC 오프셋이 있는 ISO 8601 시각."""
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _status_problem(status: Any) -> str | None:
    """Worker 가 쓰는 모든 필드의 타입·허용값과 필드 사이 조건. 하나라도 어긋나면 그 파일의 어떤 값도 내세우지 않는다."""
    if not isinstance(status, dict):
        return "not an object"
    missing = [name for name in STATUS_FIELDS if name not in status]  # null 을 허용하는 필드도 빠지면 안 된다
    if missing:
        return f"missing {', '.join(missing)}"
    if type(status["version"]) is not int or status["version"] != STATUS_VERSION:  # True == 1 이다
        return f"version {status.get('version')!r}"
    if not _valid_boot_id(status['host_boot_id']):
        return 'host_boot_id'
    if status.get("overall") not in REPORTED:
        return f"overall {status.get('overall')!r}"
    if status.get("policy") not in POLICIES:
        return f"policy {status.get('policy')!r}"
    for name in ("broker_configured", "broker_connected", "drive_configured", "intents_known"):
        if type(status.get(name)) is not bool:
            return f"{name} {status.get(name)!r}"
    for name in ("agent_subscription", "app_subscription"):
        if status.get(name) not in CHANNEL_STATES:
            return f"{name} {status.get(name)!r}"
    gateway = status["gateway_available"]
    if (gateway is not None and type(gateway) is not bool) or (not status["drive_configured"] and gateway is not None):  # 1 == True 이다
        return f"gateway_available {status.get('gateway_available')!r}"
    if status.get("last_successful_tick") is not None and not _timestamp(status["last_successful_tick"]):
        return "last_successful_tick"
    if not _timestamp(status.get("updated_at")):
        return "updated_at"
    if not _number(status.get("updated_monotonic")) or status["updated_monotonic"] < 0:
        return "updated_monotonic"
    if not _number(status.get("stale_after_s")) or status["stale_after_s"] <= 0:
        return "stale_after_s"
    if type(status.get("pid")) is not int or status["pid"] <= 0:
        return "pid"
    reasons = status.get("hold_reasons")
    if not isinstance(reasons, list) or any(reason not in HOLD_REASONS for reason in reasons) or len(set(reasons)) != len(reasons):
        return "hold_reasons"
    if (status["overall"] == DISABLED) != (status["policy"] == "none"):
        return "overall does not match policy"
    if (status["overall"] == OPERABLE and reasons) or (status["overall"] == DEGRADED and not reasons):
        return "overall does not match hold_reasons"
    if ("BROKER_CREDENTIALS_MISSING" in reasons) == status["broker_configured"]:
        return "broker_configured does not match hold_reasons"
    if ("BROKER_DISCONNECTED" in reasons) == status["broker_connected"]:
        return "broker_connected does not match hold_reasons"
    if status["intents_known"] == bool({"INTENTS_UNAVAILABLE", "OBSERVATION_FAILED"} & set(reasons)):
        return "intents_known does not match hold_reasons"
    return None
