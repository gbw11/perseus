"""`python -m xenova_simulator`: 가상 RC카를 띄우고 터미널 명령으로 시연 상황을 만든다.

    python -m xenova_simulator --host 127.0.0.1 --port 1883

실행 중 명령 (Enter):
    o [거리m]   진행 방향 앞에 장애물 (기본 0.8 m)
    c           장애물 모두 치우기
    e           비상정지 켜기/끄기
    p [dy m]    차를 옆으로 밀기 (+ 왼쪽, 기본 0.3 m)
    s           상태 한 줄 출력
    r           트랙 시작 위치로 (속도 0)
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

from xenova_mqtt import topics

from .config import DEFAULT_USERNAME, SIM_SOURCE_ID, SIM_USERNAME, SOURCE_ID, SimSettings
from .sim import Simulator

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
    """sim/* 토픽용 두 번째 클라이언트 (source_id·계정 simulator, 계약 9장·11장)."""
    from xenova_mqtt import Identity, XenovaClient

    return XenovaClient(
        Identity(source_id=SIM_SOURCE_ID),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.sim_username,
        password=settings.sim_password,
    )


def describe(sim: Simulator) -> str:
    v = sim.vehicle
    status = sim.status()
    flags = [name for name, on in status["flags"].items() if on]
    return (
        f"x={v.x:+.2f} y={v.y:+.2f} heading={math.degrees(v.heading_rad):+.0f}° "
        f"v={v.v_mps:+.2f} m/s steer={v.steer_rad:+.2f} rad gen={sim.applied_generation} "
        f"flags={','.join(flags) or '-'} obstacles={len(v.obstacles)} world_rev={sim.world_revision}"
    )


def handle(line: str, sim: Simulator, stop: threading.Event, out: TextIO) -> None:
    """터미널 명령 한 줄. 차체 상태는 주기 스레드도 만지지만 값 하나씩 바꾸는 정도라 잠그지 않는다."""
    parts = line.split()
    if not parts:
        return
    cmd, args = parts[0].lower(), parts[1:]
    if cmd == "o":
        try:
            distance = float(args[0]) if args else 0.8
        except ValueError:
            print("사용법: o [거리m]", file=out)
            return
        o = sim.add_obstacle_ahead(distance)
        print(f"장애물 {o.id} ({o.x:+.2f}, {o.y:+.2f})", file=out)
    elif cmd == "c":
        sim.clear_obstacles()
        print("장애물 치움", file=out)
    elif cmd == "p":
        try:
            dy = float(args[0]) if args else 0.3
        except ValueError:
            print("사용법: p [dy m]", file=out)
            return
        reason = sim.apply({"op": "push", "dy_m": dy})
        print(f"차 밀기 {dy:+.2f} m" if reason is None else f"차 밀기 거부 ({reason})", file=out)
    elif cmd == "e":
        sim.estop = not sim.estop
        print(f"비상정지 {'켬' if sim.estop else '끔'}", file=out)
    elif cmd == "s":
        print(describe(sim), file=out)
    elif cmd == "r":
        sim.reset_to_start()
        print("트랙 시작 위치로", file=out)
    elif cmd == "q":
        stop.set()
    else:
        print("명령: o [거리m] / c / e / p [dy] / s / r / q", file=out)


def read_commands(sim: Simulator, stop: threading.Event, stream: TextIO, out: TextIO) -> None:
    for line in stream:
        handle(line, sim, stop, out)
        if stop.is_set():
            return
    # 표준 입력이 닫혀도(백그라운드 실행) 시뮬레이터는 계속 돈다


def parse_args(argv: list[str] | None = None) -> SimSettings:
    parser = argparse.ArgumentParser(prog="xenova_simulator", description="Xenova 가상 RC카 (S15P31E110-47)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="브로커 계정. 무인증 브로커면 빈 문자열")
    parser.add_argument("--password")
    parser.add_argument("--sim-username", default=SIM_USERNAME, help="sim/* 토픽 계정 (계약 11장). 무인증 브로커면 빈 문자열")
    parser.add_argument("--sim-password")
    parser.add_argument("--no-sim-topics", action="store_true", help="sim.pose·sim.world·sim.command 를 쓰지 않는다")
    parser.add_argument("--sense-range", type=float, default=SimSettings.sense_range_m, help="전방 감지 거리 m")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    a = parser.parse_args(argv)
    return SimSettings(
        broker_host=a.host, broker_port=a.port, username=a.username or None, password=a.password,
        sim_topics=not a.no_sim_topics, sim_username=a.sim_username or None, sim_password=a.sim_password,
        sense_range_m=a.sense_range, log_level=a.log_level,
    )


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
    sim_client = sim_client_factory(settings) if settings.sim_topics else None
    sim = Simulator(client, settings, sim_client=sim_client)
    sim.subscribe()
    client.start(wait_s=1.0)
    if sim_client is not None:
        sim_client.start(wait_s=1.0)
    log.info(
        "xenova-simulator 시작 broker=%s:%s connected=%s sim_topics=%s",
        settings.broker_host, settings.broker_port, client.connected,
        sim_client.connected if sim_client is not None else "off",
    )
    if commands is not None:
        threading.Thread(target=read_commands, args=(sim, stop, commands, sys.stdout), daemon=True).start()
    try:
        sim.run(stop)
    finally:
        client.stop()
        if sim_client is not None:
            # 꺼진 시뮬레이터의 트랙·장애물이 retained 로 남지 않게 지운다 (다음 실행은 revision 1 부터)
            sim_client.clear_retained(topics.sim_world())
            sim_client.stop()
    log.info("xenova-simulator 종료: vehicle.status %d건, sim.pose %d건", sim.published, sim.pose_published)
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
