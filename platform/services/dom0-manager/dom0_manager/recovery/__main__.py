"""`python -m dom0_manager.recovery`: Recovery Worker 프로세스. dom0-manager HTTP 와 별도 systemd 유닛으로 돈다.

플랫폼·xl 설정은 XENOVA_*(dom0_manager.config.Settings.platform_from_env, HTTP 전용 값은 읽지 않음), Worker 설정은 XENOVA_RECOVERY_* 다. XENOVA_DRIVER 는 platform 이어야 한다
(desired_state 는 플랫폼 registry 에만 있다). SIGTERM·SIGINT 로 멈춘다. 종료 코드: 설정 오류 2, 정상 0.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import os
from pathlib import Path

from ..config import Settings
from ..drivers import _xl
from ..drivers.platform import load_manager
from .arming import ArmingStore
from .config import RecoverySettings
from .engine import RecoveryEngine
from .events import EventLog
from .lock import EXIT_LOCKED, AlreadyRunning, acquire
from .platform_recovery import PlatformRecovery
from .policy import RecoveryPolicy
from .state import RecoveryStore
from .heartbeats import AppHeartbeatTable, HeartbeatTable
from .mqtt import RECOVERY_EVENTS_TOPIC, HealthPublisher, build_client, link_state, subscribe_liveness
from .observe import PlatformObserver
from .watcher import Watcher
from .worker import RecoveryWorker

log = logging.getLogger("dom0_manager.recovery")


def main() -> int:
    try:
        settings = RecoverySettings.from_env()
        platform_settings = Settings.platform_from_env()
        policy = RecoveryPolicy.from_env()
    except ValueError as exc:
        print(f"xenova-recovery-worker: {exc}", file=sys.stderr)
        return 2
    if platform_settings.driver != "platform":
        print("xenova-recovery-worker: XENOVA_DRIVER must be platform (desired_state lives in the platform registry)", file=sys.stderr)
        return 2
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        lock_fd = acquire(settings.state_dir)  # noqa: F841 - 프로세스가 끝날 때까지 쥔다
    except AlreadyRunning as exc:
        log.error("xenova-recovery-worker not started: %s", exc)
        return EXIT_LOCKED

    manager = load_manager(
        platform_settings.platform_lib, busy_timeout_s=platform_settings.platform_busy_timeout_s, read_fork=platform_settings.platform_read_fork
    )
    rejoin=None
    if os.environ.get('XENOVA_RECOVERY_DRIVE_CONFIG'):
        from .rejoin import production
        rejoin=production(os.environ['XENOVA_RECOVERY_DRIVE_CONFIG'],manager.data)
    observer = PlatformObserver(manager, _xl(platform_settings), intent_refresh_s=settings.intent_refresh_s,
        drive_bindings=rejoin.cache if rejoin else None,drive_vm_ids=rejoin.vm_ids if rejoin else ())
    heartbeats, app_heartbeats = HeartbeatTable(), AppHeartbeatTable()
    client = build_client(settings)
    subscribe_liveness(client, heartbeats, app_heartbeats,rejoin=rejoin)
    # 브로커가 없어도 시작은 한다. 필터별 구독 승인 상태가 매 주기 감시기에 넘어가며, 승인되지 않은 동안 그 신호의 손실 판정은
    # 보류된다(브로커·ACL 문제는 VM 장애가 아니다). crashed·absent 판정은 Xen 관측이라 계속된다
    connected = client.start(wait_s=settings.interval_s)
    log.info(
        "xenova-recovery-worker 시작 broker=%s:%s connected=%s interval=%.0fs heartbeat_lost=%.0fs app_heartbeat_lost=%.0fs boot_grace=%.0fs absent_grace=%.0fs intent_refresh=%.0fs health_refresh=%.0fs state_dir=%s",
        settings.broker_host, settings.broker_port, connected, settings.interval_s, settings.heartbeat_lost_s,
        settings.app_heartbeat_lost_s, settings.boot_grace_s, settings.absent_grace_s, settings.intent_refresh_s, settings.health_refresh_s, settings.state_dir,
    )
    arming = ArmingStore(Path(settings.state_dir) / "armed.json")
    watcher = Watcher(settings, heartbeats, app_heartbeats, confirm=observer.confirm, arming=arming)

    event_log = EventLog(Path(settings.state_dir) / "recovery-events.jsonl")

    def publish_event(event: dict) -> bool:
        """계약 2.5: 로컬 outbox 기록 확인 뒤 MQTT-12로 제출한다(4.7 재보충 원천).
        False는 기록 효과 미확정: 엔진이 pending의 원 ID/내용으로 재시도한다.
        게시 예외도 로컬 원천이 있으면 True이며 PUBACK·소비자 적용을 뜻하지 않는다.
        """
        if not event_log.append(event):
            return False
        try:
            client.publish(RECOVERY_EVENTS_TOPIC, event["type"], event, qos=1, retain=False, wait_s=0)
        except Exception as exc:  # noqa: BLE001 - 발행 실패는 복구를 막지 않는다. Spring 은 4.7 로 채운다
            log.warning("recovery event publish failed (stored for GET /api/v1/recovery/events): %s", exc)
        return True

    # 주행 VM 의 force-restart 는 Gateway 보호 근거가 있어야 한다. 보호 수명주기(S15P31E110-346) 전에는 근거가 없어 보류된다.
    # drive config 가 없으면 주행 VM 이 어느 것인지 모르므로(None) 엔진이 Zephyr force 를 모두 보류한다
    engine = RecoveryEngine(policy, PlatformRecovery(manager), watcher, RecoveryStore(Path(settings.state_dir) / "recoveries.json"), on_event=publish_event,
                            drive_vm_ids=rejoin.vm_ids if rejoin else None)
    log.info("recovery policy=%s max_attempts=%d backoff=%s attempt_deadline=%.0fs verify_timeout=%.0fs", policy.policy, policy.max_attempts,
             ",".join(f"{b:g}" for b in policy.backoff_s), policy.attempt_deadline_s, policy.verify_timeout_s)
    worker = RecoveryWorker(
        settings, observer, watcher, link=lambda: link_state(client,verified=rejoin is not None), health_publisher=HealthPublisher(client, refresh_s=settings.health_refresh_s),
        engine=engine,rejoin=rejoin,
    )

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        worker.run(stop)
    finally:
        client.stop()
    log.info("xenova-recovery-worker 종료: 주기 %d, 건너뜀 %d, 판정 %d", worker.ticks, worker.skipped, worker.judgments.qsize())
    return 0


if __name__ == "__main__":
    sys.exit(main())
