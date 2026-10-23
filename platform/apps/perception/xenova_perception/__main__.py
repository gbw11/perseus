"""`python -m xenova_perception`: 젯슨 대신 perception.result 를 15 Hz 로 낸다.

    python -m xenova_perception --source synthetic --scenario approach
    python -m xenova_perception --source webcam --device 0 --show
    python -m xenova_perception --source video --video samples/road.mp4 --show

브로커 비밀번호는 환경변수 XENOVA_PERCEPTION_BROKER_PASSWORD 로 넘긴다 (명령행 --password 도 되지만 프로세스 목록에 보인다).
"""

from __future__ import annotations

import argparse
import logging
import math
import signal
import sys
import threading
from collections.abc import Callable, Mapping
from typing import Any

from .config import (
    APP_ID, DEFAULT_USERNAME, LOG_LEVELS, SOURCE_ID, SOURCES, Camera, PerceptionSettings, StopZone, broker_from_env,
)
from .publisher import TOPIC, LatestResult, Publisher
from .synthetic import SCENARIOS, SyntheticSource

log = logging.getLogger("xenova_perception")


def build_client(settings: PerceptionSettings) -> Any:
    """xenova_mqtt.XenovaClient. paho 는 여기서만 필요하므로 늦게 import 한다."""
    from xenova_mqtt import Identity, XenovaClient

    return XenovaClient(
        Identity(source_id=SOURCE_ID, app_id=APP_ID),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.username,
        password=settings.password,
    )


def parse_args(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> PerceptionSettings:
    base = broker_from_env(env)
    cam, zone = Camera(), StopZone()
    p = argparse.ArgumentParser(prog="xenova_perception", description="Xenova 젯슨 대역 인식 (S15P31E110-156)")
    p.add_argument("--source", choices=SOURCES, default="synthetic")
    p.add_argument("--device", type=int, default=0, help="webcam 장치 번호")
    p.add_argument("--video", help="video 파일 경로 (끝나면 처음부터 반복)")
    p.add_argument("--scenario", choices=tuple(SCENARIOS), default="approach", help="synthetic 시나리오")
    p.add_argument("--host", default=base.get("broker_host", PerceptionSettings.broker_host))
    p.add_argument("--port", type=int, default=base.get("broker_port", PerceptionSettings.broker_port))
    p.add_argument("--username", default=base.get("username", DEFAULT_USERNAME), help="브로커 계정. 무인증 브로커면 빈 문자열")
    p.add_argument("--password", default=base.get("password"), help="권장: 환경변수 XENOVA_PERCEPTION_BROKER_PASSWORD")
    p.add_argument("--camera-height", type=float, default=cam.height_m, help="카메라 높이 m")
    p.add_argument("--camera-pitch", type=float, default=math.degrees(cam.pitch_rad), help="아래로 숙인 각 deg")
    p.add_argument("--hfov", type=float, default=math.degrees(cam.hfov_rad), help="수평 화각 deg")
    p.add_argument("--stop-distance", type=float, default=zone.distance_m, help="정지 영역 전방 거리 m")
    p.add_argument("--vehicle-width", type=float, default=zone.vehicle_width_m, help="차폭 m")
    p.add_argument("--margin", type=float, default=zone.margin_m, help="정지 영역 좌우 여유 m")
    p.add_argument("--show", action="store_true", help="탐지 박스 미리보기 창 (webcam·video)")
    p.add_argument("--duration", type=float, help="이 시간(s) 뒤 종료")
    p.add_argument("--log-level", choices=LOG_LEVELS, default=base.get("log_level", "INFO"))
    a = p.parse_args(argv)
    return PerceptionSettings(
        source=a.source, device=a.device, video_path=a.video, scenario=a.scenario,
        broker_host=a.host, broker_port=a.port, username=a.username or None, password=a.password or None,
        camera=Camera(height_m=a.camera_height, pitch_rad=math.radians(a.camera_pitch), hfov_rad=math.radians(a.hfov)),
        stop_zone=StopZone(distance_m=a.stop_distance, vehicle_width_m=a.vehicle_width, margin_m=a.margin),
        show=a.show, duration_s=a.duration, log_level=a.log_level,
    )


def run(
    settings: PerceptionSettings,
    *,
    client_factory: Callable[[PerceptionSettings], Any] = build_client,
    stop: threading.Event | None = None,
    install_signals: bool = True,
) -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = stop or threading.Event()
    if install_signals:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
    if settings.duration_s is not None:
        timer = threading.Timer(settings.duration_s, stop.set)
        timer.daemon = True
        timer.start()

    client = client_factory(settings)
    client.start(wait_s=2.0)
    log.info(
        "xenova-perception 시작 source=%s broker=%s:%s user=%s connected=%s topic=%s",
        settings.source, settings.broker_host, settings.broker_port, settings.username, client.connected, TOPIC,
    )
    try:
        if settings.source == "synthetic":
            if settings.show:
                log.warning("--show 는 webcam·video 에서만 쓴다 (synthetic 은 화면이 없다)")
            publisher = Publisher(client, SyntheticSource(settings), settings.rate_hz)
            publisher.run(stop)
        else:
            from .frames import run_frames  # OpenCV 는 여기서부터 필요하다

            latest = LatestResult(settings.stale_s)
            publisher = Publisher(client, latest, settings.rate_hz)
            thread = threading.Thread(target=publisher.run, args=(stop,), name="publisher", daemon=True)
            thread.start()
            try:
                frames = run_frames(settings, latest, stop)
            finally:
                stop.set()
                thread.join(timeout=1.0)
            log.info("프레임 %d장 처리", frames)
    finally:
        client.stop()
    log.info("xenova-perception 종료: perception.result %d건, 건너뜀 %d주기", publisher.published, publisher.skipped)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        settings = parse_args(argv)
    except ValueError as exc:
        print(f"xenova-perception: {exc}", file=sys.stderr)
        return 2
    try:
        return run(settings)
    except ImportError as exc:
        print(f"xenova-perception: {exc} — webcam·video 는 `pip install -e .[camera]` (OpenCV) 가 필요하다", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"xenova-perception: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
