"""Worker 자가진단 상태(S15P31E110-354). Worker 가 매 주기 복구 경로를 점검해 상태 파일에 남기고, dom0-manager 가 같은 파일을 읽는다.

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
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import VmObservation
from .protection import GatewayRefused, GatewayUncertain

log = logging.getLogger(__name__)

STATUS_FILE = "worker-status.json"
STATUS_VERSION = 1

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
)
STATUS_FIELDS = (
    "version", "overall", "policy", "broker_configured", "broker_connected", "agent_subscription", "app_subscription",
    "drive_configured", "gateway_available", "intents_known", "last_successful_tick", "hold_reasons", "updated_at",
    "updated_monotonic", "stale_after_s", "pid",
)

STALE_PERIODS = 3  # 감시 주기 × 3 동안 갱신이 없으면 STALE
STALE_MIN_S = 15.0
GATEWAY_PROBE_S = 30.0  # Gateway context 확인 주기. 감시 스레드에서 호출 한 번(최대 call budget)만큼 늦어진다


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
    """정적 점검(시작 시 한 번)과 동적 점검(매 주기)으로 상태를 만들고 파일에 원자적으로 쓴다."""

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
        self._observations: list[VmObservation] = []
        self._last_success: str | None = None
        self._last: tuple[str, tuple[str, ...]] | None = None
        self._write_failed = False

    def report(self, observations: list[VmObservation] | None) -> dict[str, Any]:
        """한 주기의 상태. observations 가 None 이면 이번 주기 관측이 실패한 것이다(판정 없음)."""
        now = self._clock()
        observed = observations is not None
        if observed:
            self._observations = observations
        intent_known = observed and self._intent_known()
        if intent_known:
            self._last_success = self._wall().isoformat()
        connected = bool(self._connected())
        links = self._links(now)
        gateway = self._gateway.check() if self._gateway is not None else None
        reasons = self._hold_reasons(observed, intent_known, connected, links, gateway)
        overall = DISABLED if self._policy == "none" else DEGRADED if reasons else OPERABLE
        status = {
            "version": STATUS_VERSION,
            "overall": overall,
            "policy": self._policy,
            "broker_configured": self._broker_configured,
            "broker_connected": connected,
            "agent_subscription": links["agent"],
            "app_subscription": links["app"],
            "drive_configured": self._drive_vm_ids is not None,
            "gateway_available": gateway,
            "intents_known": intent_known,
            "last_successful_tick": self._last_success,
            "hold_reasons": reasons,
            "updated_at": self._wall().isoformat(),
            "updated_monotonic": now,
            "stale_after_s": self._stale_after_s,
            "pid": os.getpid(),
        }
        self._log_transition(overall, reasons)
        self._write(status)
        return status

    def _hold_reasons(self, observed: bool, intent_known: bool, connected: bool, links: dict[str, str], gateway: bool | None) -> list[str]:
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
            elif gateway is False:
                reasons.append("GATEWAY_UNAVAILABLE")
        reasons.extend(reason for reason in self._internal() if reason not in reasons)
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
            if not self._write_failed:
                log.error("worker status file %s could not be written (%s); readers will see it as stale", self.path, exc)
            self._write_failed = True
            return
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        if self._write_failed:
            log.info("worker status file %s written again", self.path)
            self._write_failed = False


def read_status(state_dir: str | Path, *, now: float | None = None) -> dict[str, Any]:
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
    age = now - status["updated_monotonic"]
    if age < 0 or age > status["stale_after_s"]:
        # 음수: 이전 부팅의 기록이다(/run 이 아닌 state dir). 어느 쪽이든 지금 Worker 의 상태가 아니다
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
