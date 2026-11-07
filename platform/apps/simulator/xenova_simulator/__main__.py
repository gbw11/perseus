"""`python -m xenova_simulator`: 가상 RC카를 띄우고 터미널 명령으로 시연 상황을 만든다.

    python -m xenova_simulator --input frames --frame-listen 0.0.0.0:7600 --camera 0.0.0.0:8090
    python -m xenova_simulator --input mqtt --host 127.0.0.1 --port 1883

실행 중 명령 (Enter):
    o [거리m] [cone|box|tires]   진행 방향 앞에 장애물 (기본 1.5 m, 콘)
    t <트랙id>  트랙 바꾸기 (장애물 치우고 출발선으로). t 만 치면 목록
    c           장애물 모두 치우기
    e           비상정지 켜기/끄기
    s           상태 한 줄 출력 (출발 신호·랩 포함)
    r           트랙 시작 위치로 (속도 0), 출발 신호 처음부터
    q           종료
"""

from __future__ import annotations

import argparse
import logging
import math
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from .config import DEFAULT_USERNAME, INPUTS, SOURCE_ID, SimSettings, parse_address
from .sim import Simulator
from .model import KIND_W
from .track import track_ids

log = logging.getLogger("xenova_simulator")


def build_client(settings: SimSettings) -> Any:
    """xenova_mqtt.XenovaClient. paho 는 여기서만 필요하므로 늦게 import 한다."""
    from xenova_mqtt import Identity, XenovaClient

    return XenovaClient(
        Identity(source_id=SOURCE_ID),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.username,
        password=settings.password,
    )


def build_sim_client(settings: SimSettings) -> Any:
    """sim/* 토픽용 두 번째 클라이언트 (source_id·계정 simulator, 계약 9장·12장)."""
    from xenova_mqtt import Identity, XenovaClient

    from .simtopics import SOURCE_ID as SIM_SOURCE_ID

    return XenovaClient(
        Identity(source_id=SIM_SOURCE_ID),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.sim_username,
        password=settings.sim_password,
    )


def describe(sim: Simulator) -> str:
    v = sim.vehicle
    i, off, progress = sim.track_frame()
    status = sim.status()
    flags = [name for name, on in status["flags"].items() if on]
    line = (
        f"track={sim.track.id} x={v.x:+.2f} y={v.y:+.2f} heading={math.degrees(v.heading_rad):+.0f}° "
        f"lane={off:+.3f}m {sim.track.surface_at(v.x, v.y, i)} lap={progress:.1f}/{sim.track.lap_m:.1f}m "
        f"wheel={v.wheel_rpm:+.0f}rpm enc={v.encoder_count} v={sim.measured_v_mps:+.2f}m/s "
        f"steer={v.steer_rad:+.3f}rad gen={sim.applied_generation} flags={','.join(flags) or '-'} "
        f"obstacles={len(v.obstacles)}"
    )
    r = sim.race.payload()
    line += (f" race={r['phase']}" + (f" lights={r['lights_on']}/5" if r["phase"] == "lights" else f" lap={r['lap'] + 1}")
             + (" jump" if r["jump_start"] else ""))
    if sim.frames is not None:
        f = sim.frames
        line += f" frames(ok={f.accepted} bad={f.bad_frames} dup={f.duplicates} old_gen={f.stale_generation})"
    return line


def handle(line: str, sim: Simulator, stop: threading.Event, out: TextIO) -> None:
    """터미널 명령 한 줄."""
    parts = line.split()
    if not parts:
        return
    cmd, args = parts[0].lower(), parts[1:]
    if cmd == "o":
        try:
            distance = float(args[0]) if args else 1.5
            kind = args[1] if len(args) > 1 else "cone"
            if kind not in KIND_W:
                raise ValueError(kind)
        except ValueError:
            print("사용법: o [거리m] [cone|box|tires]", file=out)
            return
        o = sim.add_obstacle_ahead(distance, kind)
        print(f"장애물 {o.id} {o.kind} ({o.x:+.2f}, {o.y:+.2f})", file=out)
    elif cmd == "t":
        if not args or args[0] not in track_ids():
            print("트랙: " + " ".join(track_ids()), file=out)
            return
        sim.select_track(args[0])
        print(f"트랙 {sim.track.id} ({sim.track.name}), 출발선으로", file=out)
    elif cmd == "c":
        sim.clear_obstacles()
        print("장애물 치움", file=out)
    elif cmd == "e":
        sim.estop = not sim.estop
        print(f"비상정지 {'켬' if sim.estop else '끔'}", file=out)
    elif cmd == "s":
        print(describe(sim), file=out)
    elif cmd == "r":
        sim.reset_to_start()
        print("트랙 시작 위치로, 출발 신호 처음부터", file=out)
    elif cmd == "q":
        stop.set()
    else:
        print("명령: o [거리m] [종류] / t [트랙] / c / e / s / r / q", file=out)


def read_commands(sim: Simulator, stop: threading.Event, stream: TextIO, out: TextIO) -> None:
    for line in stream:
        handle(line, sim, stop, out)
        if stop.is_set():
            return
    # 표준 입력이 닫혀도(백그라운드 실행) 시뮬레이터는 계속 돈다


def parse_args(argv: list[str] | None = None) -> SimSettings:
    parser = argparse.ArgumentParser(prog="xenova_simulator", description="Xenova 가상 RC카 (S15P31E110-47, -268)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="브로커 계정. 무인증 브로커면 빈 문자열")
    parser.add_argument("--password")
    parser.add_argument("--sim-username", default="simulator", help="sim/* 토픽 계정 (계약 12장). 무인증 브로커면 빈 문자열")
    parser.add_argument("--sim-password")
    parser.add_argument("--no-sim-topics", action="store_true", help="sim.pose·sim.world·sim.command 를 쓰지 않는다")
    parser.add_argument("--input", choices=INPUTS, default="mqtt",
                        help="frames = 게이트웨이 시리얼 프레임(TCP), mqtt = 앱 drive.command 직접")
    parser.add_argument("--frame-listen", default=SimSettings.frame_listen, help="frames 입력 TCP host:port")
    parser.add_argument("--camera", default=SimSettings.camera_listen,
                        help="전방 카메라 MJPEG host:port, 'off' 면 끈다")
    parser.add_argument("--track", default=SimSettings.track_id, choices=track_ids(), help="시작 트랙")
    parser.add_argument("--no-start-lights", action="store_true",
                        help="출발 신호 없이 바로 출발 (계약 12장 race, 랩 시간만 잰다)")
    parser.add_argument("--sense-range", type=float, default=SimSettings.sense_range_m, help="전방 감지 거리 m")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    a = parser.parse_args(argv)
    return SimSettings(
        broker_host=a.host, broker_port=a.port, username=a.username or None, password=a.password,
        sim_topics=not a.no_sim_topics, sim_username=a.sim_username or None, sim_password=a.sim_password,
        input=a.input, frame_listen=a.frame_listen, camera_listen=None if a.camera == "off" else a.camera,
        track_id=a.track, start_lights=not a.no_start_lights, sense_range_m=a.sense_range, log_level=a.log_level,
    )


def start_camera(settings: SimSettings, sim: Simulator) -> Any:
    from .camera import CameraService  # numpy·OpenCV 는 카메라를 켤 때만 필요하다

    host, port = parse_address(settings.camera_listen)
    service = CameraService(settings.camera, sim.camera_pose, host, port)
    service.start()
    return service


def run(
    settings: SimSettings,
    *,
    client_factory: Callable[[SimSettings], Any] = build_client,
    sim_client_factory: Callable[[SimSettings], Any] = build_sim_client,
    stop: threading.Event | None = None,
    commands: TextIO | None = sys.stdin,
    install_signals: bool = True,
) -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = stop or threading.Event()
    if install_signals:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())

    client = client_factory(settings)
    sim = Simulator(client, settings)
    sim.subscribe()
    sim_client = sim_topics = None
    if settings.sim_topics:
        from .simtopics import SimTopics

        sim_client = sim_client_factory(settings)
        sim_topics = SimTopics(sim, sim_client)
        sim_topics.subscribe()
    services: list[Any] = []
    try:
        if sim.frames is not None:
            from .frames import FrameServer

            server = FrameServer(sim.frames, *parse_address(settings.frame_listen))
            server.start()
            services.append(server)
        if settings.camera_listen is not None:
            services.append(start_camera(settings, sim))
    except ImportError as exc:
        print(f"xenova-simulator: 카메라에는 numpy·OpenCV 가 필요하다 (`pip install -e .[camera]`, 끄려면 --camera off): {exc}",
              file=sys.stderr)
        for service in services:
            service.stop()
        return 2
    except OSError as exc:
        print(f"xenova-simulator: 포트를 열 수 없다: {exc}", file=sys.stderr)
        for service in services:
            service.stop()
        return 1
    client.start(wait_s=1.0)
    if sim_client is not None:
        sim_client.start(wait_s=1.0)
    log.info("xenova-simulator 시작 input=%s track=%s broker=%s:%s connected=%s sim_topics=%s", settings.input,
             sim.track.id, settings.broker_host, settings.broker_port, client.connected,
             sim_client.connected if sim_client is not None else "off")
    if commands is not None:
        threading.Thread(target=read_commands, args=(sim, stop, commands, sys.stdout), daemon=True).start()
    try:
        sim.run(stop, after_tick=sim_topics.publish if sim_topics is not None else None)
    finally:
        for service in services:
            service.stop()
        client.stop()
        if sim_client is not None:
            sim_topics.clear()
            sim_client.stop()
    log.info("xenova-simulator 종료: vehicle.status %d건, sim.pose %d건", sim.published,
             sim_topics.pose_published if sim_topics is not None else 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        settings = parse_args(argv)
    except ValueError as exc:
        print(f"xenova-simulator: {exc}", file=sys.stderr)
        return 2
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
