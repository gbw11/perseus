"""`python -m xenova_agent`: 설정을 읽고 MQTT 클라이언트를 띄운 뒤 heartbeat 루프를 돈다. SIGTERM·SIGINT 로 멈춘다."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

from .config import AgentSettings
from .heartbeat import HeartbeatFailed, HeartbeatSender

log = logging.getLogger("xenova_agent")


def build_client(settings: AgentSettings) -> Any:
    """perseus_mqtt.PerseusClient 를 만든다. paho 는 여기서만 필요하므로 늦게 import 한다."""
    from perseus_mqtt import Identity, PerseusClient

    return PerseusClient(
        Identity(vm_id=settings.vm_id),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.username,
        password=settings.password,
    )


def run(
    settings: AgentSettings,
    *,
    client_factory: Callable[[AgentSettings], Any] = build_client,
    stop: threading.Event | None = None,
    install_signals: bool = True,
) -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = stop or threading.Event()
    if install_signals:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())

    client = client_factory(settings)
    # 브로커가 없어도 start 는 실패하지 않고 뒤에서 재시도한다. 첫 heartbeat 를 바로 보내도록 한 주기만큼만 연결을 기다린다
    client.start(wait_s=settings.heartbeat_interval_s)
    log.info(
        "xenova-agent 시작 vm_id=%s broker=%s:%s interval=%.1fs connected=%s",
        settings.vm_id, settings.broker_host, settings.broker_port, settings.heartbeat_interval_s, client.connected,
    )
    sender = HeartbeatSender(client, settings.vm_id, interval_s=settings.heartbeat_interval_s)
    try:
        sender.run(stop)
    except HeartbeatFailed as exc:
        # 고정된 오류다. 서비스를 active 로 두지 않고 실패로 끝내 systemd 가 로그에 남기고 Restart= 정책대로 다시 띄운다
        log.error("xenova-agent 실패: %s (보냄 %d, 건너뜀 %d)", exc, sender.sent, sender.skipped)
        return 1
    finally:
        client.stop()
    log.info("xenova-agent 종료: 보냄 %d, 연결 없어 건너뜀 %d, 실패 %d", sender.sent, sender.skipped, sender.failed)
    return 0


def main() -> int:
    try:
        settings = AgentSettings.from_env()
    except ValueError as exc:
        print(f"xenova-agent: {exc}", file=sys.stderr)
        return 2
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
