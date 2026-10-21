"""수동주행 앱 설정. 환경변수 XENOVA_MANUAL_* 와 플랫폼이 게스트에 기록한 /etc/xenova/instance-id 에서 읽는다."""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# 에이전트와 같은 파일·같은 브로커 (platform/services/vm-agent/agent/xenova_agent/config.py)
INSTANCE_ID_PATH = Path("/etc/xenova/instance-id")
DEFAULT_BROKER_HOST = "192.168.77.1"
# 브로커 ACL 은 username = app_id 로 apps/{app_id}/events 쓰기를 허용한다 (MQTT 계약 v1 9장)
APP_ID = "manual"
# MQTT 계약 v1 5.3: drive.command 50 Hz. 받는 쪽(게이트웨이 100 ms, ESP32 300 ms)이 이 주기를 전제로 끊김을 판정한다
COMMAND_RATE_HZ = 50.0
# MQTT 계약 v1 7장: 백엔드는 키를 누르는 동안 50 ms 마다 drive.input 을 다시 보낸다. 4번 놓치면 손을 뗀 것으로 본다
DEFAULT_INPUT_TIMEOUT_MS = 200.0
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def canonical_vm_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"vm_id must be a UUID, got {value!r}") from exc


def _positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")


@dataclass(frozen=True)
class ManualSettings:
    vm_id: str
    broker_host: str = DEFAULT_BROKER_HOST
    broker_port: int = 1883
    username: str | None = APP_ID
    password: str | None = None
    # 실차 측정 전 값. 1/10 RC카 실내 시연 기준으로 낮게 잡는다
    max_speed_mps: float = 1.0
    max_reverse_mps: float = 0.5
    max_steer_rad: float = 0.4
    input_timeout_ms: float = DEFAULT_INPUT_TIMEOUT_MS
    command_rate_hz: float = COMMAND_RATE_HZ
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        object.__setattr__(self, "vm_id", canonical_vm_id(self.vm_id))
        for name in ("max_speed_mps", "max_reverse_mps", "max_steer_rad", "input_timeout_ms", "command_rate_hz"):
            _positive(name, getattr(self, name))
        if not 1 <= self.broker_port <= 65535:
            raise ValueError(f"broker_port must be 1..65535, got {self.broker_port!r}")
        if not self.broker_host:
            raise ValueError("broker_host must not be empty")
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {LOG_LEVELS}, got {self.log_level!r}")

    @property
    def period_s(self) -> float:
        return 1.0 / self.command_rate_hz

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, instance_id_path: Path = INSTANCE_ID_PATH) -> "ManualSettings":
        """XENOVA_MANUAL_VM_ID 가 비어 있으면 instance-id 파일을 읽는다. 둘 다 없으면 설정 오류로 시작하지 않는다.

        주기(50 Hz)는 계약값이라 환경변수로 바꾸지 않는다. 시험용 값은 생성자 인자로만 준다.
        """
        env = os.environ if env is None else env

        def read(name: str, default, cast):
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return cast(raw)
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from exc

        vm_id = read("XENOVA_MANUAL_VM_ID", "", str)
        if not vm_id:
            try:
                vm_id = instance_id_path.read_text(encoding="ascii").strip()
            except OSError as exc:
                raise ValueError(f"vm_id is not set: XENOVA_MANUAL_VM_ID is empty and {instance_id_path} is unreadable ({exc})") from exc
        return cls(
            vm_id=vm_id,
            broker_host=read("XENOVA_MANUAL_BROKER_HOST", cls.broker_host, str),
            broker_port=read("XENOVA_MANUAL_BROKER_PORT", cls.broker_port, int),
            username=read("XENOVA_MANUAL_BROKER_USERNAME", cls.username, str),
            password=read("XENOVA_MANUAL_BROKER_PASSWORD", None, str),
            max_speed_mps=read("XENOVA_MANUAL_MAX_SPEED_MPS", cls.max_speed_mps, float),
            max_reverse_mps=read("XENOVA_MANUAL_MAX_REVERSE_MPS", cls.max_reverse_mps, float),
            max_steer_rad=read("XENOVA_MANUAL_MAX_STEER_RAD", cls.max_steer_rad, float),
            input_timeout_ms=read("XENOVA_MANUAL_INPUT_TIMEOUT_MS", cls.input_timeout_ms, float),
            log_level=read("XENOVA_MANUAL_LOG_LEVEL", cls.log_level, str).upper(),
        )
