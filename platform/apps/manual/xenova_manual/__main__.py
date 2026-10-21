"""`python -m xenova_manual`: 설정을 읽고 MQTT 클라이언트를 띄운 뒤 drive.command 루프를 돈다. SIGTERM·SIGINT 로 멈춘다."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

from .app import ManualDriveApp
from .config import APP_ID, ManualSettings

log = logging.getLogger("xenova_manual")


def build_client(settings: ManualSettings) -> Any:
    """xenova_mqtt.XenovaClient 를 만든다. paho 는 여기서만 필요하므로 늦게 import 한다."""
    from xenova_mqtt import Identity, XenovaClient

    return XenovaClient(
        Identity(vm_id=settings.vm_id, app_id=APP_ID),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.username,
        password=settings.password,
    )


def run(
    settings: ManualSettings,
    *,
    client_factory: Callable[[ManualSettings], Any] = build_client,
    stop: threading.Event | None = None,
    install_signals: bool = True,
) -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = stop or threading.Event()
    if install_signals:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())

    client = client_factory(settings)
    app = ManualDriveApp(client, settings)
    app.subscribe()
    # 브로커가 없어도 start 는 실패하지 않고 뒤에서 재시도한다. 끊긴 동안의 QoS 0 명령은 버려진다 (받는 쪽은 끊김으로 판정)
    client.start(wait_s=1.0)
    log.info(
        "xenova-manual 시작 vm_id=%s broker=%s:%s rate=%.0fHz connected=%s",
        settings.vm_id, settings.broker_host, settings.broker_port, settings.command_rate_hz, client.connected,
    )
    try:
        app.run(stop)
    finally:
        client.stop()
    log.info("xenova-manual 종료: 발행 %d", app.sent)
    return 0


def main() -> int:
    try:
        settings = ManualSettings.from_env()
    except ValueError as exc:
        print(f"xenova-manual: {exc}", file=sys.stderr)
        return 2
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
