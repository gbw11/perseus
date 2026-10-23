"""`python -m dom0_manager.recovery`: Recovery Worker 프로세스. dom0-manager HTTP 와 별도 systemd 유닛으로 돈다.

플랫폼·xl 설정은 XENOVA_*(dom0_manager.config.Settings), Worker 설정은 XENOVA_RECOVERY_* 다. XENOVA_DRIVER 는 platform 이어야 한다
(desired_state 는 플랫폼 registry 에만 있다). SIGTERM·SIGINT 로 멈춘다. 종료 코드: 설정 오류 2, 정상 0.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

from ..config import Settings
from ..drivers import _xl
from ..drivers.platform import load_manager
from .arming import ArmingStore
from .config import RecoverySettings
from .heartbeats import AppHeartbeatTable, HeartbeatTable
from .mqtt import HealthPublisher, build_client, link_state, subscribe_liveness
from .observe import PlatformObserver
from .watcher import Watcher
from .worker import RecoveryWorker

log = logging.getLogger("dom0_manager.recovery")


def main() -> int:
    try:
        settings = RecoverySettings.from_env()
        platform_settings = Settings.from_env()
    except ValueError as exc:
        print(f"xenova-recovery-worker: {exc}", file=sys.stderr)
        return 2
    if platform_settings.driver != "platform":
        print("xenova-recovery-worker: XENOVA_DRIVER must be platform (desired_state lives in the platform registry)", file=sys.stderr)
        return 2
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    manager = load_manager(platform_settings.platform_lib, busy_timeout_s=platform_settings.platform_busy_timeout_s)
    observer = PlatformObserver(manager, _xl(platform_settings), intent_refresh_s=settings.intent_refresh_s)
    heartbeats, app_heartbeats = HeartbeatTable(), AppHeartbeatTable()
    client = build_client(settings)
    subscribe_liveness(client, heartbeats, app_heartbeats)
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
    worker = RecoveryWorker(
        settings, observer, watcher, link=lambda: link_state(client), health_publisher=HealthPublisher(client, refresh_s=settings.health_refresh_s),
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
