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
# MQTT 계약 v1 3장: heartbeat 주기 5초
DEFAULT_HEARTBEAT_INTERVAL_S = 5.0


@dataclass(frozen=True)
class AgentSettings:
    vm_id: str
    broker_host: str = DEFAULT_BROKER_HOST
    broker_port: int = 1883
    username: str | None = None
    password: str | None = None
    heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.vm_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"vm_id must be a UUID, got {self.vm_id!r}") from exc
        if not (math.isfinite(self.heartbeat_interval_s) and self.heartbeat_interval_s > 0):
            raise ValueError(f"heartbeat_interval_s must be a finite positive number, got {self.heartbeat_interval_s!r}")
        if not 1 <= self.broker_port <= 65535:
            raise ValueError(f"broker_port must be 1..65535, got {self.broker_port!r}")
        if not self.broker_host:
            raise ValueError("broker_host must not be empty")

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
        return cls(
            vm_id=vm_id,
            broker_host=read("XENOVA_AGENT_BROKER_HOST", cls.broker_host, str),
            broker_port=read("XENOVA_AGENT_BROKER_PORT", cls.broker_port, int),
            username=read("XENOVA_AGENT_BROKER_USERNAME", None, str),
            password=read("XENOVA_AGENT_BROKER_PASSWORD", None, str),
            heartbeat_interval_s=read("XENOVA_AGENT_HEARTBEAT_INTERVAL", cls.heartbeat_interval_s, float),
            log_level=read("XENOVA_AGENT_LOG_LEVEL", cls.log_level, str).upper(),
        )
