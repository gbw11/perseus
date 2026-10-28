"""v2 Telemetry 발행 (S15P31E110-310): 에이전트 자체 상태를 Zephyr Publisher 와 같은 형식으로 낸다.

계약은 platform/guests/zephyr/telemetry/contract/CONTRACT.md 2.1 이고 토픽은 xenova/v2/vms/{vm_id}/telemetry/{stream} 이다.
- inventory: QoS 1 retained. 연결될 때마다 보내고, 그 뒤 inventory_refresh_s 마다 다시 읽어 바뀌었을 때만 보낸다. 보내지 못한
  것(크기 초과·제출 실패)도 다음 갱신 주기나 재연결 때 다시 시도한다. heartbeat 주기마다 같은 실패를 되풀이하지 않는다
- heartbeat: QoS 0, 2초. publisher_state 와 마지막 수집 성공 시각, 실패 누계. 앱 정상을 선언하지 않는다(app_health_asserted=false)
- metrics family publisher: QoS 0, 5초. publisher.* 카운터. heartbeat 와 따로 예정해 inventory 에 적은 주기(limits)대로 나간다

v1 agent.heartbeat 는 에이전트 프로세스 생존이고, 이 heartbeat 는 수집·발행 루프(Reporter)가 진행 중인가다. 둘은 다른 스레드에서
나가므로 Reporter 가 멈추면 v1 heartbeat 는 계속 나가고 여기서는 degraded 가 된다. 이 스레드가 멈추면 v2 heartbeat 가 끊겨 받는 쪽
lease(계약 6장, 6초)가 만료된다. Recovery Worker 는 v2 를 읽지 않는다(VM 재시작 근거가 아니다).

- 봉투: schema_version 2.1, 스트림별 seq(1부터, 실제로 확정한 메시지만 번호를 쓴다), message_id = vm_id/boot_id/publisher_instance_id/stream/seq.
  boot_id 는 v1 봉투와 같은 게스트 부팅 ID, publisher_instance_id 는 이 발행기(에이전트 프로세스)마다 새로 만든다
- context 는 unbound. Linux 게스트에는 host 가 검증해 주입한 실행 receipt 가 없다. 꾸며 넣지 않는다
- clock: sent_uptime_ms 는 CLOCK_BOOTTIME(게스트 부팅 뒤 ms). UTC 는 계약 6.1 의 검증(dom0 adjtimex 교환)을 하지 않으므로 unavailable 이고
  sent_at·source·uncertainty 는 null 이다. 게스트 시계가 맞아 보여도 synchronized 로 꾸미지 않는다
- 끊긴 동안은 보내지 않고 쌓지도 않는다. 재연결 뒤 새 수집부터 보내며 seq 는 이어진다(빠진 번호는 손실)
- 봉투 포함 4096 바이트를 넘으면 보내지 않고 payload_oversize 로 센다
- u64 값은 계약대로 십진 문자열이다
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .reporter import ReporterStatus

log = logging.getLogger("xenova_agent.telemetry")

SCHEMA_VERSION = "2.1"
METRIC_CATALOG = "xenova-telemetry-core-2.1"
PUBLISHER_NAME = "xenova.agent"
MAX_PAYLOAD_BYTES = 4096  # 계약 11장: 봉투 포함 UTF-8 wire payload
HEARTBEAT_PERIOD_S = 2.0
PUBLISHER_METRICS_PERIOD_S = 5.0
INVENTORY_REFRESH_S = 60.0
STALL_INTERVALS = 3  # Reporter 틱이 자기 주기의 이 배수 동안 없으면 정체(degraded)
DUE_TOLERANCE_S = 0.001  # 대기에서 깬 시각이 예정 시각보다 이만큼 이르면 지금 보낸다(한 바퀴 더 돌지 않게)
UNSPECIFIED_RC = -1  # 예외에 errno 가 없을 때. 계약은 실패 rc 가 음수이기만 요구한다
MAX_INTERFACES = 4  # 계약 inventory.interfaces 상한. 넘치면 주소 없는 것(vif 등)부터 빼고 interfaces_omitted 로 센다
MAX_ADDRESSES = 4  # 계약 inventory.interfaces[].addresses 상한
QOS = {"inventory": 1, "heartbeat": 0, "metrics": 0}
RETAIN = {"inventory": True, "heartbeat": False, "metrics": False}
# 계약 8장 capability. Zephyr Publisher 의 id 는 Linux 에이전트가 제공하지 않음을 밝힌다. Linux 의 CPU·메모리는 v1 agent.metrics 에 있다
CAPABILITIES = [
    {"id": "publisher_counters", "support": "available", "reason": None},
    {"id": "loop", "support": "not_registered", "reason": "no observed task registered"},
    {"id": "kernel_heap", "support": "unsupported", "reason": "linux agent reports memory via v1 agent.metrics"},
    {"id": "thread_stack", "support": "unsupported", "reason": "not provided by the linux agent"},
    {"id": "timing", "support": "unsupported", "reason": "not provided by the linux agent"},
    {"id": "cpu_runtime", "support": "unsupported", "reason": "linux agent reports cpu via v1 agent.metrics"},
    {"id": "memory_pools", "support": "unsupported", "reason": "not provided by the linux agent"},
    {"id": "persistent_crash", "support": "unsupported", "reason": "no verified external capture configuration"},
]
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAC_PATTERN = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def boot_uptime_ms() -> int:
    """게스트 부팅 뒤 경과 ms. Linux 는 CLOCK_BOOTTIME(절전 포함), 없으면 단조 시계."""
    clock_id = getattr(time, "CLOCK_BOOTTIME", None)
    seconds = time.clock_gettime(clock_id) if clock_id is not None else time.monotonic()
    return int(seconds * 1000)


def topic(vm_id: str, stream: str) -> str:
    return f"xenova/v2/vms/{vm_id}/telemetry/{stream}"


class Publisher(Protocol):
    @property
    def connected(self) -> bool: ...

    def publish_raw(self, topic: str, data: bytes, *, qos: int, retain: bool, wait_s: float | None = None) -> None: ...


@dataclass(frozen=True)
class Collection:
    """한 번의 수집 구간(계약 payload.collection). 전송 시각과 구별한다."""

    sample_id: str
    start_uptime_ms: int
    end_uptime_ms: int
    elapsed_us: int
    status: str  # ok | collection_failed
    source_rc: int

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id, "start_uptime_ms": str(self.start_uptime_ms), "end_uptime_ms": str(self.end_uptime_ms),
            "elapsed_us": str(self.elapsed_us), "status": self.status, "source_rc": self.source_rc,
        }


class TelemetryPublisher:
    def __init__(
        self,
        client: Publisher,
        vm_id: str,
        *,
        boot_id: str,
        agent_version: str,
        collect_inventory: Callable[[], dict[str, Any]],
        reporter_status: Callable[[], ReporterStatus | None],
        reporter_alive: Callable[[], bool],
        v1_publish_failures: Callable[[], int] = lambda: 0,
        uptime_ms: Callable[[], int] = boot_uptime_ms,
        clock: Callable[[], float] = time.monotonic,
        publisher_instance_id: str | None = None,
        new_id: Callable[[], Any] = uuid.uuid4,
        heartbeat_period_s: float = HEARTBEAT_PERIOD_S,
        metrics_period_s: float = PUBLISHER_METRICS_PERIOD_S,
        inventory_refresh_s: float = INVENTORY_REFRESH_S,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
    ) -> None:
        """clock 은 Reporter 와 같은 단조 시계여야 한다(ReporterStatus 의 시각을 여기 시각과 빼서 uptime 으로 옮긴다)."""
        self._client = client
        self._vm_id = vm_id
        self._boot_id = boot_id
        self._agent_version = agent_version
        self._collect_inventory = collect_inventory
        self._reporter_status = reporter_status
        self._reporter_alive = reporter_alive
        self._v1_publish_failures = v1_publish_failures
        self._uptime_ms = uptime_ms
        self._clock = clock
        self._new_id = new_id
        self.publisher_instance_id = publisher_instance_id or str(new_id())
        self._heartbeat_period = float(heartbeat_period_s)
        self._metrics_period = float(metrics_period_s)
        self._inventory_refresh = float(inventory_refresh_s)
        self._max_payload = int(max_payload_bytes)
        self._started_at = clock()
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}
        self._seen_connections: int | None = None
        self._inventory_sent: dict[str, Any] | None = None  # collection 을 뺀 마지막 inventory payload
        self._inventory_checked_at: float | None = None
        self._next_metrics_at = self._started_at
        self.stopping = False
        self.sent = {"inventory": 0, "heartbeat": 0, "metrics": 0}
        self.skipped_offline = 0  # 끊겨 있어 보내지 않은 틱
        self.collection_failures = 0  # 이 발행기 자신의 수집 실패(inventory·Reporter 상태 읽기)
        self.submit_failures = 0  # 이 발행기 자신의 발행 제출 실패
        self.oversized = 0

    # ---- 수집 ----

    def _collect(self, fn: Callable[[], Any]) -> tuple[Collection, Any]:
        start = self._uptime_ms()
        t0 = self._clock()
        try:
            result, status, rc = fn(), "ok", 0
        except Exception as exc:  # noqa: BLE001 - 수집 실패도 계약대로 보고한다
            errno_value = getattr(exc, "errno", None)
            result, status, rc = None, "collection_failed", (-errno_value if isinstance(errno_value, int) and errno_value > 0 else UNSPECIFIED_RC)
            self.collection_failures += 1
            log.warning("telemetry collection failed: %s", type(exc).__name__)
        end = max(start, self._uptime_ms())
        elapsed_us = max(0, int((self._clock() - t0) * 1_000_000))
        return Collection(str(self._new_id()), start, end, elapsed_us, status, rc), result

    # ---- payload ----

    def _publisher_state(self, status: ReporterStatus | None, now: float) -> str:
        """계약 9.4: 최근 필수 수집 결과로 running/degraded 를 오가고 다음 정상 수집에서 복귀한다. 멈춘 루프도 degraded 다."""
        if self.stopping:
            return "stopping"
        if status is None or not self._reporter_alive():
            return "degraded"
        last = status.last_tick_at if status.last_tick_at is not None else self._started_at
        if now - last > STALL_INTERVALS * status.interval_s or status.last_tick_ok is False:
            return "degraded"
        return "running"

    def _collection_failures_total(self, status: ReporterStatus | None) -> int:
        return self.collection_failures + (status.collect_errors if status is not None else 0)

    def _submit_failures_total(self, status: ReporterStatus | None) -> int:
        return self.submit_failures + (status.publish_errors if status is not None else 0) + int(self._v1_publish_failures())

    def _oversize_total(self, status: ReporterStatus | None) -> int:
        return self.oversized + (status.oversized if status is not None else 0)

    def _heartbeat_payload(self) -> dict[str, Any]:
        collection, status = self._collect(self._reporter_status)
        now = self._clock()
        last_ok = None
        if status is not None and status.last_collect_ok_at is not None:
            age_ms = max(0, int((now - status.last_collect_ok_at) * 1000))
            last_ok = max(0, collection.end_uptime_ms - age_ms)  # 수집 구간 끝보다 미래가 되지 않게
        return {
            "collection": collection.to_json(),
            "publisher_state": self._publisher_state(status, now),
            "last_collection_success_uptime_ms": None if last_ok is None else str(last_ok),
            "collection_failures_total": str(self._collection_failures_total(status)),
            "connection_attempts_total": str(int(getattr(self._client, "connection_attempts", 0))),
            "publish_submit_failures_total": str(self._submit_failures_total(status)),
            "events_dropped_total": "0",  # 사건 스트림이 없다. metrics 의 publisher.events_dropped_total 은 unsupported 로 낸다
            "app_health_asserted": False,
        }

    def _publisher_metrics_payload(self) -> dict[str, Any]:
        collection, status = self._collect(self._reporter_status)
        return {
            "collection": collection.to_json(),
            "family": "publisher",
            "samples": [
                _counter("publisher.collection_failures_total", self._collection_failures_total(status), collection),
                _counter("publisher.publish_submit_failures_total", self._submit_failures_total(status), collection),
                _unsupported("publisher.events_dropped_total", "no event stream in the linux agent"),
                _counter("publisher.payload_oversize_total", self._oversize_total(status), collection),
                _unsupported("publisher.puback_timeouts_total", "PUBACK timeouts are not tracked by this publisher"),
            ],
        }

    def _inventory_payload(self, inventory: dict[str, Any]) -> dict[str, Any]:
        """v1 inventory(xenova_agent.inventory.collect) → 계약 8장 inventory. 모르는 값은 null 이고 꾸미지 않는다."""
        os_info = inventory.get("os") or {}
        os_id = str(os_info.get("id") or "linux").lower()
        if not _ID_PATTERN.fullmatch(os_id):
            os_id = "linux"
        hostname = inventory.get("hostname")
        hostname = None if not hostname or hostname == "unknown" else str(hostname)[:63]
        all_interfaces = list(inventory.get("interfaces") or [])
        all_interfaces.sort(key=_interface_rank)  # IPv4 있는 것 → 주소 있는 것 → 없는 것. 안정 정렬이라 같은 급은 이름 순
        interfaces = []
        for iface in all_interfaces[:MAX_INTERFACES]:
            mac = str(iface.get("mac") or "").lower()
            addresses = [
                {"family": a["family"], "address": str(a["address"])[:64]}
                for a in (iface.get("addresses") or [])[:MAX_ADDRESSES] if a.get("family") in ("ipv4", "ipv6")
            ]
            interfaces.append({"name": str(iface.get("name") or "")[:31], "mac": mac if _MAC_PATTERN.fullmatch(mac) else None, "addresses": addresses})
        return {
            "os": {
                "id": os_id,
                "version": str(os_info.get("version_id") or "unknown")[:64],
                "kernel_release": str(inventory.get("kernel_release") or "unknown")[:128],
            },
            "hostname": hostname,
            "display_name": None,  # 플랫폼 표시 이름은 게스트가 모른다
            "firmware": {"build_id": f"xenova-agent {self._agent_version}"[:128], "artifact_sha256": None, "measurement_profile": "linux_agent"},
            "publisher": {"name": PUBLISHER_NAME, "version": self._agent_version[:32], "metric_catalog": METRIC_CATALOG},
            "interfaces": interfaces,
            "interfaces_omitted": max(0, len(all_interfaces) - MAX_INTERFACES),
            "capabilities": CAPABILITIES,
            "capabilities_omitted": 0,
            "clock_domains": {"uptime": "guest_boot_milliseconds", "loop_counter": "local_hw_cycles_converted_us", "frequency_hz": None, "same_epoch": False},
            "limits": {
                "max_payload_bytes": self._max_payload,
                "heartbeat_period_ms": int(self._heartbeat_period * 1000),
                "metrics_period_ms": int(self._metrics_period * 1000),
                "event_queue_capacity": 0,
                "diagnostic_stack_scan_default": "off",
            },
        }

    # ---- 발행 ----

    def _send(self, stream: str, payload: dict[str, Any]) -> bool:
        sent_ms = max(int(payload["collection"]["end_uptime_ms"]), self._uptime_ms())  # start ≤ end ≤ sent
        with self._lock:
            seq = self._seq.get(stream, 0) + 1
        message = {
            "schema_version": SCHEMA_VERSION,
            "type": f"telemetry.{stream}",
            "message_id": f"{self._vm_id}/{self._boot_id}/{self.publisher_instance_id}/{stream}/{seq}",
            "vm_id": self._vm_id,
            "boot_id": self._boot_id,
            "publisher_instance_id": self.publisher_instance_id,
            "seq": str(seq),
            "context": {"binding": "unbound", "host_boot_id": None, "execution_id": None, "receipt_id": None},
            "clock": {"sent_uptime_ms": str(sent_ms), "sent_at": None, "utc_quality": "unavailable", "utc_source": None, "uncertainty_ms": None},
            "payload": payload,
        }
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) > self._max_payload:
            self.oversized += 1
            log.error("telemetry %s not published: %d bytes > %d", stream, len(data), self._max_payload)
            return False
        with self._lock:
            self._seq[stream] = seq  # 여기서 메시지가 확정된다. 제출에 실패해도 번호는 쓴 것이다(빠진 번호 = 손실)
        try:
            self._client.publish_raw(topic(self._vm_id, stream), data, qos=QOS[stream], retain=RETAIN[stream], wait_s=0)
        except Exception as exc:  # noqa: BLE001
            self.submit_failures += 1
            log.warning("telemetry %s publish failed: %s", stream, exc)
            return False
        self.sent[stream] += 1
        return True

    def _maybe_send_inventory(self, now: float, reconnected: bool) -> None:
        due = self._inventory_checked_at is None or now - self._inventory_checked_at >= self._inventory_refresh
        if not (reconnected or due):
            return
        self._inventory_checked_at = now
        collection, inventory = self._collect(self._collect_inventory)
        if inventory is None:
            return  # 읽기 실패: 옛 값을 새 수집처럼 다시 보내지 않는다(retained 는 브로커에 남아 있다)
        payload = self._inventory_payload(inventory)
        if not reconnected and payload == self._inventory_sent:
            return
        if self._send("inventory", {"collection": collection.to_json(), **payload}):
            self._inventory_sent = payload

    def tick(self, *, heartbeat: bool = True) -> None:
        """heartbeat 와, 예정 시각이 된 metrics 를 보낸다. metrics 예정은 끊겨 있어도 넘겨 두어 재연결 뒤 밀린 표본을 몰아 보내지 않는다."""
        now = self._clock()
        metrics = now >= self._next_metrics_at - DUE_TOLERANCE_S
        if metrics:
            self._next_metrics_at = _advance(self._next_metrics_at, now, self._metrics_period)
        if not bool(self._client.connected):
            self.skipped_offline += 1
            return
        connections = getattr(self._client, "connections", None)
        if connections is None:  # 연결 횟수를 모르는 클라이언트: 첫 틱에만 inventory 를 보낸다
            reconnected = self._seen_connections is None
            self._seen_connections = -1
        else:
            reconnected = connections != self._seen_connections
            self._seen_connections = int(connections)
        self._maybe_send_inventory(now, reconnected)
        if heartbeat:
            self._send("heartbeat", self._heartbeat_payload())
        if metrics:
            self._send("metrics", self._publisher_metrics_payload())

    def run(self, stop: threading.Event) -> None:
        """heartbeat 와 metrics 의 예정 시각을 따로 두고 둘 중 가까운 쪽까지 기다린다."""
        heartbeat_due = self._clock()
        while True:
            now = self._clock()
            heartbeat = now >= heartbeat_due - DUE_TOLERANCE_S
            try:
                self.tick(heartbeat=heartbeat)
            except Exception:  # noqa: BLE001 - 한 틱의 예외로 스레드가 끝나지 않게
                log.exception("telemetry tick failed")
            if heartbeat:
                heartbeat_due = _advance(heartbeat_due, now, self._heartbeat_period)
            now = self._clock()
            if stop.wait(max(0.0, min(heartbeat_due, self._next_metrics_at) - now)):
                break
        self.stopping = True
        if bool(self._client.connected):
            try:
                self._send("heartbeat", self._heartbeat_payload())  # 종료 요청 상태를 한 번 알린다(계약 9.4 stopping)
            except Exception:  # noqa: BLE001
                log.exception("final telemetry heartbeat failed")

    def start(self, stop: threading.Event) -> threading.Thread:
        thread = threading.Thread(target=self.run, args=(stop,), name="xenova-agent-telemetry", daemon=True)
        thread.start()
        return thread


def _interface_rank(iface: dict[str, Any]) -> tuple[bool, bool]:
    """상한을 넘을 때 남길 순서. dom0 의 vif 는 링크 로컬 IPv6 만 있어 IPv4 가 있는 브리지·게스트 eth0 보다 뒤로 간다."""
    addresses = iface.get("addresses") or []
    return (not any(a.get("family") == "ipv4" for a in addresses), not addresses)


def _advance(due: float, now: float, period: float) -> float:
    """다음 예정 시각. 한 주기 넘게 밀렸으면 밀린 틱을 몰아 돌지 않고 지금부터 다시 센다."""
    due += period
    if now - due >= period:
        due = now + period
    return due


def _counter(name: str, value: int, collection: Collection) -> dict[str, Any]:
    base = {"name": name, "unit": "count", "kind": "counter", "encoding": "u64_decimal", "labels": {}}
    if collection.status != "ok":
        return {**base, "value": None, "status": "collection_failed", "reason": "reporter status unavailable", "source_rc": collection.source_rc}
    return {**base, "value": str(max(0, value)), "status": "ok", "reason": None, "source_rc": 0}


def _unsupported(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "unit": "count", "kind": "counter", "encoding": "u64_decimal", "value": None, "status": "unsupported",
            "reason": reason, "source_rc": None, "labels": {}}
