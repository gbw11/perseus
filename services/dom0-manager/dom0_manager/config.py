"""환경변수 기반 설정. 외부 라이브러리 없이 dataclass 로 둔다 (dom0 이미지 의존성 최소화)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DRIVERS = ("mock", "xen")


@dataclass(frozen=True)
class Settings:
    driver: str = "mock"
    host: str = "127.0.0.1"
    port: int = 8081
    # 계약 v1.1: 조회 드라이버 한도 5초(Spring 대기 6초 안), 정지는 플랫폼이 최대 90초 동기 대기하고 자동 강제 종료 없음
    xl_query_timeout_s: float = 5.0
    xl_command_timeout_s: float = 95.0
    shutdown_grace_s: float = 90.0
    # Bearer 서비스 토큰. 비어 있으면 검사하지 않는다(로컬 Mock 개발). Tailscale 바인드 배포에서는 반드시 설정
    service_token: str = ""
    # RPi4 물리 코어 수. vcpus 상한 검사에 쓴다. xl info 의 nr_cpus 를 읽을 수 있으면 그 값을 우선한다.
    host_cpus: int = 4
    instance_root: str = "/srv/perseus/instances"
    mock_samples: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        def read(name: str, default, cast):
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            return cast(raw)

        def as_bool(raw: str) -> bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")

        driver = read("PERSEUS_DRIVER", cls.driver, str).strip().lower()
        if driver not in DRIVERS:
            raise ValueError(f"PERSEUS_DRIVER must be one of {DRIVERS}, got {driver!r}")

        return cls(
            driver=driver,
            host=read("PERSEUS_HOST", cls.host, str),
            port=read("PERSEUS_PORT", cls.port, int),
            xl_query_timeout_s=read("PERSEUS_XL_QUERY_TIMEOUT", cls.xl_query_timeout_s, float),
            xl_command_timeout_s=read("PERSEUS_XL_COMMAND_TIMEOUT", cls.xl_command_timeout_s, float),
            shutdown_grace_s=read("PERSEUS_SHUTDOWN_GRACE", cls.shutdown_grace_s, float),
            service_token=read("PERSEUS_SERVICE_TOKEN", cls.service_token, str),
            host_cpus=read("PERSEUS_HOST_CPUS", cls.host_cpus, int),
            instance_root=read("PERSEUS_INSTANCE_ROOT", cls.instance_root, str),
            mock_samples=read("PERSEUS_MOCK_SAMPLES", cls.mock_samples, as_bool),
            log_level=read("PERSEUS_LOG_LEVEL", cls.log_level, str).upper(),
        )
