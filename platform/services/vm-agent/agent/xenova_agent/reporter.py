"""MQTT-02 inventory·MQTT-03 metrics 발행 루프. heartbeat 와 다른 스레드에서 돈다.

- 수집(/proc 읽기)이 느리거나 실패해도 heartbeat(5초, Recovery Worker 의 생존 판정 근거)를 밀지 않도록 heartbeat 루프와 분리한다
- inventory: 연결될 때마다(시작·재연결) 한 번, 그 뒤 inventory_refresh_s 마다 다시 읽어 바뀌었을 때만 전체를 보낸다.
  QoS 1 retained 라 브로커가 마지막 값을 보관한다. 브로커가 재시작돼 retained 를 잃어도 재연결 때 다시 보낸다. 재연결은 클라이언트의
  연결 성공 횟수(connections)로 알아채 두 틱 사이에 끊겼다 다시 붙은 경우도 놓치지 않는다
- metrics: interval_s 마다 수집한다. 끊긴 동안에도 수집은 해서 구간 기준을 이어 가지만 발행하지 않고 버린다(QoS 0 실시간 표본을
  쌓았다가 몰아 보내지 않는다). 재연결 뒤 다음 틱의 새 표본부터 보낸다
- window_ms 는 설정 주기가 아니라 직전 표본과의 실제 간격이다. 첫 표본은 구간이 없어 null
- 직렬화한 payload 가 상한을 넘으면 processes[] 를 뒤에서부터 줄인다(Spring 은 64KB 넘는 메시지를 통째로 거부한다).
  다 줄여도 values[] 만으로 넘으면 보내지 않고 oversized 로 센다
- process_collection 은 processes[] 가 어떻게 모였는지다(processes.py 의 수집 상태·개수). 빈 목록이 첫 표본·/proc 읽기 실패·
  0개 설정·일부 읽기 실패 중 무엇인지 받는 쪽이 가를 수 있게 한다. truncated 는 top-N 이나 크기 상한으로 목록 밖으로 밀린 것이 있다는 뜻
- 수집·발행 예외는 그 틱만 버리고 계속 돈다. 고정된 발행 오류는 heartbeat 쪽이 종료 코드로 드러낸다
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from xenova_mqtt import topics

from .heartbeat import Publisher, StopSignal
from .processes import ProcessSample

log = logging.getLogger("xenova_agent.reporter")

# 봉투(약 400 바이트)를 감안한 payload 상한. 공용 클라이언트의 64KB 검사보다 먼저 줄여 메시지를 잃지 않는다
PAYLOAD_LIMIT_BYTES = 60 * 1024


def payload_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class Reporter:
    def __init__(
        self,
        client: Publisher,
        vm_id: str,
        *,
        collect_inventory: Callable[[], dict[str, Any]],
        collect_metrics: Callable[[], list[dict[str, Any]]],
        collect_processes: Callable[[], ProcessSample],
        interval_s: float = 2.0,
        inventory_refresh_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        payload_limit: int = PAYLOAD_LIMIT_BYTES,
    ) -> None:
        self._client = client
        self._inventory_topic = topics.inventory(vm_id)
        self._metrics_topic = topics.metrics(vm_id)
        self._collect_inventory = collect_inventory
        self._collect_metrics = collect_metrics
        self._collect_processes = collect_processes
        self._interval = float(interval_s)
        self._inventory_refresh = float(inventory_refresh_s)
        self._clock = clock
        self._limit = payload_limit
        self._was_connected = False
        self._seen_connections: int | None = None
        self._inventory_sent: dict[str, Any] | None = None
        self._inventory_checked_at: float | None = None
        self._last_sample_at: float | None = None
        self.metrics_sent = 0
        self.metrics_dropped = 0  # 연결이 없어 버린 표본
        self.inventory_sent = 0
        self.oversized = 0  # processes 를 모두 빼도 상한을 넘어 보내지 않은 표본
        self.errors = 0

    # ---- inventory ----

    def _maybe_send_inventory(self, now: float, reconnected: bool) -> None:
        due = self._inventory_checked_at is None or now - self._inventory_checked_at >= self._inventory_refresh
        if not (reconnected or due or self._inventory_sent is None):
            return
        self._inventory_checked_at = now
        try:
            inventory = self._collect_inventory()
        except Exception:  # noqa: BLE001 - 수집 실패는 다음 주기에 다시
            self.errors += 1
            log.exception("inventory collection failed")
            return
        if not reconnected and inventory == self._inventory_sent:
            return
        try:
            self._client.publish(self._inventory_topic, "agent.inventory", inventory, wait_s=0)
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            log.warning("inventory publish failed: %s", exc)
            return
        self._inventory_sent = inventory
        self.inventory_sent += 1
        log.info("inventory published (%s %s, %d interface(s))", (inventory.get("os") or {}).get("id"), inventory.get("kernel_release"),
                 len(inventory.get("interfaces") or []))

    # ---- metrics ----

    def _metrics_payload(self, now: float) -> dict[str, Any]:
        window_ms = None if self._last_sample_at is None else int(round((now - self._last_sample_at) * 1000))
        self._last_sample_at = now
        values = self._collect_metrics()
        sample = self._collect_processes()
        collection = {"status": sample.status, "discovered": sample.discovered, "read_ok": sample.read_ok,
                      "vanished": sample.vanished, "failed": sample.failed, "truncated": sample.truncated, "error": sample.error}
        payload = {"window_ms": window_ms, "values": values, "processes": list(sample.rows), "process_collection": collection}
        while payload["processes"] and payload_size(payload) > self._limit:
            payload["processes"] = payload["processes"][:-1]
            collection["truncated"] = True  # 크기 상한으로 줄인 것도 목록 밖으로 밀린 것이다
        return payload

    def _reconnected(self, connected: bool) -> bool:
        connections = getattr(self._client, "connections", None)
        if isinstance(connections, int):
            changed = connections != self._seen_connections
            self._seen_connections = connections
            return connected and changed
        return connected and not self._was_connected  # 연결 횟수를 모르는 클라이언트: 틱 사이의 짧은 끊김은 놓친다

    def tick(self) -> None:
        now = self._clock()
        connected = bool(self._client.connected)
        reconnected = self._reconnected(connected)
        self._was_connected = connected
        if connected:
            self._maybe_send_inventory(now, reconnected)
        try:
            payload = self._metrics_payload(now)
        except Exception:  # noqa: BLE001
            self.errors += 1
            log.exception("metrics collection failed")
            return
        if not connected:
            self.metrics_dropped += 1  # 쌓지 않는다
            return
        size = payload_size(payload)
        if size > self._limit:
            self.oversized += 1
            log.error("metrics not published: %d bytes > %d even without processes (%d values)", size, self._limit, len(payload["values"]))
            return
        try:
            self._client.publish(self._metrics_topic, "agent.metrics", payload, wait_s=0)
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            log.warning("metrics publish failed: %s", exc)
            return
        self.metrics_sent += 1

    def run(self, stop: StopSignal) -> None:
        due = self._clock()
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 예상 못 한 예외 하나로 보고 스레드가 끝나 버리지 않게 그 틱만 버린다
                self.errors += 1
                log.exception("reporter tick failed")
            due += self._interval
            now = self._clock()
            if now - due >= self._interval:  # 수집이 한 주기 넘게 밀렸다: 밀린 틱을 몰아 돌지 않고 지금부터 다시 센다
                due = now + self._interval
            if stop.wait(max(0.0, due - now)):
                return

    def start(self, stop: threading.Event) -> threading.Thread:
        thread = threading.Thread(target=self.run, args=(stop,), name="xenova-agent-reporter", daemon=True)
        thread.start()
        return thread
