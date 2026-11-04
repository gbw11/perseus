"""에이전트 설정. 환경변수 XENOVA_AGENT_* 와 플랫폼이 게스트에 기록한 /etc/xenova/instance-id 에서 읽는다."""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# 플랫폼이 게스트 준비 때 VM uuid 를 적어 두는 파일 (platform/guests/common/prepare_rootfs.py). registry·Xen UUID 와 같다
INSTANCE_ID_PATH = Path("/etc/xenova/instance-id")
# dom0 의 xenbr0 주소. 브로커는 dom0 Mosquitto 다 (MQTT 계약 v1 1장)
DEFAULT_BROKER_HOST = "192.168.77.1"
# MQTT 계약 v1 3장: heartbeat 주기 5초 고정. 받는 쪽이 15초 STALE, 30초 OFFLINE 으로 판정하므로 운영 설정으로 바꾸지 않는다.
# 시험용 짧은 주기는 생성자 인자로만 준다
DEFAULT_HEARTBEAT_INTERVAL_S = 5.0
# MQTT-03 metrics 주기(S15P31E110-122). 계약은 주기를 고정하지 않으며 화면 갱신과 수집 비용 사이 기본값
DEFAULT_METRICS_INTERVAL_S = 2.0
DEFAULT_TOP_PROCESSES = 10  # processes[] 개수. 64KB 메시지 상한 안쪽을 지키는 기본값이며 계약상 고정값은 아니다
DEFAULT_INVENTORY_REFRESH_S = 60.0  # inventory 를 다시 읽어 바뀌었는지 보는 간격
# 명령 기록(같은 command_id 재실행 방지). 유닛의 RuntimeDirectory 라 부팅마다 비워진다
DEFAULT_STATE_DIR = Path("/run/xenova-agent")
MAX_TOP_PROCESSES = 50
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
NIL_UUID = str(uuid.UUID(int=0))


def canonical_vm_id(value: str) -> str:
    """소문자·하이픈 표준형으로 정규화한다. 토픽과 봉투의 vm_id 가 Spring·registry 의 UUID 문자열과 글자까지 같아야 한다."""
    try:
        parsed = uuid.UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"vm_id must be a UUID, got {value!r}") from exc
    canonical = str(parsed)
    if canonical == NIL_UUID:
        raise ValueError("vm_id must not be the nil UUID (reserved for Domain-0)")
    return canonical


@dataclass(frozen=True)
class AgentSettings:
    vm_id: str
    broker_host: str = DEFAULT_BROKER_HOST
    broker_port: int = 1883
    username: str | None = None
    password: str | None = None
    heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S
    metrics_interval_s: float = DEFAULT_METRICS_INTERVAL_S
    top_processes: int = DEFAULT_TOP_PROCESSES
    inventory_refresh_s: float = DEFAULT_INVENTORY_REFRESH_S
    # v2 Telemetry(에이전트 자체 상태, xenova_agent.telemetry) 발행. 브로커 ACL 이 v2 쓰기를 막으면 조용히 버려질 뿐 v1 에는 영향이 없다
    telemetry_enabled: bool = True
    # MQTT-04 데모 명령(stress·agent_exit). 시연용 이미지에서만 켠다. 꺼져 있으면 명령을 DEMO_DISABLED 로 거절한다
    demo_commands: bool = False
    state_dir: Path | None = DEFAULT_STATE_DIR
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        object.__setattr__(self, "vm_id", canonical_vm_id(self.vm_id))
        if not (math.isfinite(self.heartbeat_interval_s) and self.heartbeat_interval_s > 0):
            raise ValueError(f"heartbeat_interval_s must be a finite positive number, got {self.heartbeat_interval_s!r}")
        for name in ("metrics_interval_s", "inventory_refresh_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a finite positive number, got {value!r}")
        if type(self.top_processes) is not int or not 0 <= self.top_processes <= MAX_TOP_PROCESSES:
            raise ValueError(f"top_processes must be 0..{MAX_TOP_PROCESSES}, got {self.top_processes!r}")
        if not 1 <= self.broker_port <= 65535:
            raise ValueError(f"broker_port must be 1..65535, got {self.broker_port!r}")
        if not self.broker_host:
            raise ValueError("broker_host must not be empty")
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {LOG_LEVELS}, got {self.log_level!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, instance_id_path: Path = INSTANCE_ID_PATH) -> "AgentSettings":
        """XENOVA_AGENT_VM_ID 가 비어 있으면 instance-id 파일을 읽는다. 둘 다 없으면 설정 오류로 시작하지 않는다."""
        env = os.environ if env is None else env

        def read(name: str, default, cast):
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return default
            return cast(raw.strip())

        vm_id = read("XENOVA_AGENT_VM_ID", "", str)
        if not vm_id:
            try:
                vm_id = instance_id_path.read_text(encoding="ascii").strip()
            except OSError as exc:
                raise ValueError(f"vm_id is not set: XENOVA_AGENT_VM_ID is empty and {instance_id_path} is unreadable ({exc})") from exc
        if read("XENOVA_AGENT_HEARTBEAT_INTERVAL", "", str):
            # 주기는 계약값이라 운영 설정으로 바꿀 수 없다. 조용히 무시하면 팀원이 실제 동작을 오해하므로 시작을 거부한다
            raise ValueError("XENOVA_AGENT_HEARTBEAT_INTERVAL is not configurable: the heartbeat period is fixed at 5s by the MQTT contract")
        return cls(
            vm_id=vm_id,
            broker_host=read("XENOVA_AGENT_BROKER_HOST", cls.broker_host, str),
            broker_port=read("XENOVA_AGENT_BROKER_PORT", cls.broker_port, int),
            username=read("XENOVA_AGENT_BROKER_USERNAME", None, str),
            password=read("XENOVA_AGENT_BROKER_PASSWORD", None, str),
            metrics_interval_s=read("XENOVA_AGENT_METRICS_INTERVAL", cls.metrics_interval_s, float),
            top_processes=read("XENOVA_AGENT_TOP_PROCESSES", cls.top_processes, int),
            telemetry_enabled=read("XENOVA_AGENT_TELEMETRY", cls.telemetry_enabled, _as_bool),
            demo_commands=read("XENOVA_AGENT_DEMO_COMMANDS", cls.demo_commands, _as_bool),
            log_level=read("XENOVA_AGENT_LOG_LEVEL", cls.log_level, str).upper(),
        )


def _as_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"expected a boolean (1/0, true/false, yes/no, on/off), got {raw!r}")
