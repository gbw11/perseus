"""게이트웨이 설정. 명령행 인자가 환경변수 XENOVA_GATEWAY_* 보다 우선한다. 비밀번호는 저장소에 두지 않는다."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .machine import Mode

# 게이트웨이는 dom0 에서 브로커와 같이 돈다
DEFAULT_BROKER_HOST = "127.0.0.1"
# 브로커 ACL: user gateway → vehicle/control, apps/vehicle/events 쓰기 (MQTT 계약 v1 9장)
DEFAULT_USERNAME = "gateway"
SOURCE_ID = "dom0-gateway"
EVENT_APP_ID = "vehicle"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class GatewaySettings:
    broker_host: str = DEFAULT_BROKER_HOST
    broker_port: int = 1883
    username: str | None = DEFAULT_USERNAME
    password: str | None = None
    sink: str = "mock"                  # mock | serial
    serial_port: str = "/dev/ttyUSB0"
    serial_baud: int = 115200
    frame_log: str | None = None        # 목 싱크 기록 파일. "-" 는 표준출력, 없으면 메모리에만
    start_mode: Mode = Mode.AUTO
    retained_wait_s: float = 1.0        # 시작 시 이전 vehicle/control(retained) 을 기다리는 시간
    log_level: str = "INFO"
    vm_a: str | None = None
    vm_b: str | None = None
    initial_active_vm: str | None = None
    profile: str = "verified"
    authority_dir: str = "/srv/xenova/drive-control"
    binding_file: str = "/run/xenova/drive-bindings.json"
    initialize_control_state: bool = False


def _env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(f"XENOVA_GATEWAY_{name}", "").strip()
    return value or None


def parse(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> GatewaySettings:
    env = os.environ if env is None else env
    d = GatewaySettings()
    p = argparse.ArgumentParser(prog="xenova_gateway", description="dom0 게이트웨이: 제어권 판정·ESP32 전달")
    p.add_argument("--host", default=_env(env, "BROKER_HOST") or d.broker_host)
    p.add_argument("--port", type=int, default=int(_env(env, "BROKER_PORT") or d.broker_port))
    p.add_argument("--username", default=_env(env, "BROKER_USERNAME") or d.username)
    p.add_argument("--password", default=_env(env, "BROKER_PASSWORD"),
                   help="가능하면 환경변수 XENOVA_GATEWAY_BROKER_PASSWORD 로 준다 (ps 에 보이지 않게)")
    p.add_argument("--sink", choices=("mock", "serial"), default=_env(env, "SINK") or d.sink)
    p.add_argument("--serial-port", default=_env(env, "SERIAL_PORT") or d.serial_port,
                   help="장치 경로(/dev/ttyUSB0) 또는 pyserial URL. socket://<PC>:7600 이면 같은 프레임을 TCP 로 시뮬레이터에 보낸다")
    p.add_argument("--serial-baud", type=int, default=int(_env(env, "SERIAL_BAUD") or d.serial_baud))
    p.add_argument("--frame-log", default=_env(env, "FRAME_LOG"))
    p.add_argument("--start-mode", choices=("AUTO", "SAFE_STOP"), default=_env(env, "START_MODE"),help="legacy profile only")
    p.add_argument("--retained-wait-s", type=float, default=float(_env(env, "RETAINED_WAIT_S") or d.retained_wait_s))
    p.add_argument("--log-level", choices=LOG_LEVELS, default=(_env(env, "LOG_LEVEL") or d.log_level).upper())
    p.add_argument("--vm-a", default=_env(env, "VM_A"), help="공통 펌웨어를 실행하는 VM UUID, --vm-b와 함께 지정")
    p.add_argument("--vm-b", default=_env(env, "VM_B"))
    p.add_argument("--initial-active-vm", default=_env(env, "INITIAL_ACTIVE_VM"), help="최초 활성 UUID, 생략하면 --vm-a")
    p.add_argument("--profile",choices=("verified","warm","legacy"),default=_env(env,"PROFILE") or d.profile)
    p.add_argument("--authority-dir",default=_env(env,"AUTHORITY_DIR") or d.authority_dir)
    p.add_argument("--binding-file",default=_env(env,"BINDING_FILE") or d.binding_file)
    p.add_argument("--initialize-control-state",action="store_true",help="확인한 최초 설치에서만 정지 상태 원본을 생성")
    a = p.parse_args(argv)
    if a.profile in ("verified","warm") and not (a.vm_a and a.vm_b):p.error("verified/warm profile requires --vm-a and --vm-b")
    if a.profile=="legacy" and (a.vm_a or a.vm_b or a.initialize_control_state):p.error("legacy profile is for separate apps; no paired authority fallback")
    if a.profile in ("verified","warm") and a.start_mode:p.error("verified mode/owner come from the durable authority, not --start-mode")
    if a.initial_active_vm and not a.initialize_control_state:p.error("--initial-active-vm is only valid during explicit initialization")
    if a.vm_a or a.vm_b or a.initial_active_vm:
        from xenova_mqtt.drive_binding import canonical as vm_uuid
        try:
            pair = (vm_uuid(a.vm_a), vm_uuid(a.vm_b))
            if pair[0] == pair[1] or (a.initial_active_vm and a.initial_active_vm not in pair):
                raise ValueError("distinct pair / initial member required")
        except (ValueError, TypeError, AttributeError):
            p.error("--vm-a/--vm-b는 서로 다른 표준 UUID 쌍이어야 하며 최초 활성 UUID는 그중 하나여야 합니다")
    if not 1 <= a.port <= 65535:
        p.error(f"--port 범위 밖: {a.port}")
    return GatewaySettings(
        broker_host=a.host, broker_port=a.port, username=a.username or None, password=a.password,
        sink=a.sink, serial_port=a.serial_port, serial_baud=a.serial_baud, frame_log=a.frame_log,
        start_mode=Mode(a.start_mode or d.start_mode), retained_wait_s=max(0.0, a.retained_wait_s), log_level=a.log_level,
        vm_a=a.vm_a, vm_b=a.vm_b, initial_active_vm=a.initial_active_vm,
        profile=a.profile,authority_dir=a.authority_dir,binding_file=a.binding_file,
        initialize_control_state=a.initialize_control_state,
    )
