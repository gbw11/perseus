"""감시 루프. 주기마다 관측 → 판정 → 복구 큐·건강 스냅샷. 복구 실행(S15P31E110-125)은 큐의 소비자가 한다."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from ..errors import Dom0ManagerError
from .config import RecoverySettings
from .model import Judgment, VmObservation
from .engine import RecoveryEngine
from .mqtt import HealthPublisher, LinkState
from .watcher import Watcher

log = logging.getLogger(__name__)

Observer = Callable[[], list[VmObservation]]


class RecoveryWorker:
    def __init__(
        self,
        settings: RecoverySettings,
        observer: Observer,
        watcher: Watcher,
        *,
        link: Callable[[], LinkState],
        judgments: "queue.Queue[Judgment] | None" = None,
        health_publisher: HealthPublisher | None = None,
        engine: RecoveryEngine | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._observer = observer
        self._watcher = watcher
        self._link = link
        self.judgments: queue.Queue[Judgment] = judgments or queue.Queue()
        self._health = health_publisher
        self._engine = engine
        self._clock = clock
        self.ticks = 0
        self.skipped = 0

    def run_once(self) -> list[Judgment]:
        """한 주기. 관측에 실패하면(플랫폼 잠금·xl 오류) 판정하지 않고 다음 주기를 기다린다. 오래된 관측으로 판정하지 않는다."""
        if self._health is not None:
            self._health.retry_clears()  # MQTT 정리는 플랫폼·Xen 관측과 무관하게 매 주기
        try:
            observations = self._observer()
        except Dom0ManagerError as exc:
            self.skipped += 1
            log.warning("observation skipped: %s %s", exc.code, exc.message)
            if self._engine is not None:
                self._engine.collect(self._clock())  # 판정은 못 해도 끝난 복구 시도의 결과는 회수한다
            return []
        self.ticks += 1
        now = self._clock()
        judgments = self._watcher.tick(observations, now, link=self._link())
        for judgment in judgments:
            log.warning(
                "vm %s judged %s (desired=%s domid=%s xen=%s signal_age=%s) recovery_id=%s",
                judgment.vm_id, judgment.reason.value, judgment.desired_state, judgment.domid,
                judgment.xen_state.value if judgment.xen_state else None,
                None if judgment.heartbeat_age_s is None else round(judgment.heartbeat_age_s, 1), judgment.recovery_id,
            )
            self.judgments.put(judgment)
            if self._engine is not None:
                self._engine.on_judgment(judgment, now)
        if self._health is not None:
            for vm_id in self._watcher.removed_vm_ids():
                self._health.clear(vm_id)
            for health in self._watcher.healths():
                self._health.publish(health, now)
        if self._engine is not None:
            self._engine.tick(now)  # 건강 스냅샷이 갱신된 뒤 복구 상태를 전진시킨다. 플랫폼 호출은 엔진의 실행 스레드에서
        return judgments

    def shutdown(self) -> None:
        """멈추기 전에 남은 retained 건강 스냅샷을 관측 불가로 바꾼다. 비정상 종료면 받는 쪽의 lease 만료가 같은 일을 한다."""
        if self._engine is not None:
            self._engine.shutdown()
        if self._health is not None:
            healths = self._watcher.healths()
            count = self._health.publish_unavailable(healths, self._clock())
            if count < len(healths):
                log.warning("health snapshots: only %d of %d marked OBSERVATION_UNAVAILABLE before stop; receivers fall back to lease expiry", count, len(healths))
            else:
                log.info("health snapshots marked OBSERVATION_UNAVAILABLE for %d VM(s)", count)

    def run(self, stop: threading.Event) -> None:
        """단조 시계 기준 주기. 지연이 쌓이면 몰아 돌지 않고 다음 눈금으로 넘어간다."""
        interval = self._settings.interval_s
        started = self._clock()
        tick = 0
        while not stop.is_set():
            self.run_once()
            tick += 1
            due = started + tick * interval
            now = self._clock()
            if now - due >= interval:  # 크게 밀렸으면 기준을 다시 잡는다
                started, tick = now, 0
                due = now
            if stop.wait(max(0.0, due - now)):
                break
        self.shutdown()
