"""`python -m xenova_gateway`: 설정을 읽고 MQTT·ESP32 출력을 연 뒤 판정 루프를 돈다. SIGTERM·SIGINT 로 멈춘다."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from collections.abc import Sequence

from .config import SOURCE_ID, GatewaySettings, parse
from .runner import GatewayRunner
from .sink import MockSink, SerialSink, Sink, SinkError

log = logging.getLogger("xenova_gateway")


def open_sink(settings: GatewaySettings) -> Sink:
    if settings.sink == "serial":
        return SerialSink(settings.serial_port, settings.serial_baud)
    if settings.frame_log == "-":
        return MockSink(sys.stdout)
    if settings.frame_log:
        return MockSink(open(settings.frame_log, "a", encoding="utf-8"))
    return MockSink()


def _windows_timer_resolution() -> None:
    """개발 PC(Windows) 전용. 기본 타이머가 15.6 ms 단위라 큐 대기가 그만큼 늦게 깬다. dom0(Linux)에서는 하지 않는다."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)


def main(argv: Sequence[str] | None = None) -> int:
    settings = parse(argv)
    _windows_timer_resolution()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        sink = open_sink(settings)
    except SinkError as exc:
        print(f"xenova-gateway: {exc}", file=sys.stderr)
        return 2

    # paho 는 여기서만 필요하다
    from xenova_mqtt import Identity, XenovaClient

    client = XenovaClient(Identity(source_id=SOURCE_ID), settings.broker_host, settings.broker_port,
                          username=settings.username, password=settings.password)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    runner = GatewayRunner(client, sink, start_mode=settings.start_mode, retained_wait_s=settings.retained_wait_s)
    log.info("xenova-gateway 시작 broker=%s:%s sink=%s", settings.broker_host, settings.broker_port, settings.sink)
    try:
        runner.run(stop)
    finally:
        client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
