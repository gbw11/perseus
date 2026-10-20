"""환경변수 기반 설정. 외부 라이브러리 없이 dataclass 로 둔다 (dom0 이미지 의존성 최소화)."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

DRIVERS = ("mock", "xen", "platform")


@dataclass(frozen=True)
class Settings:
    driver: str = "mock"
    host: str = "127.0.0.1"
    port: int = 8081
    # 계약 v1.1: 조회 드라이버 한도 5초(Spring 대기 6초 안), 정지는 플랫폼이 최대 90초 동기 대기하고 자동 강제 종료 없음
    xl_query_timeout_s: float = 5.0
    xl_command_timeout_s: float = 95.0
    # 비어 있지 않으면 xl 을 로컬 대신 SSH 로 부른다 (예: root@100.64.0.1). 개발 PC 에서 실보드 조회를 대조할 때만 쓴다
    xl_ssh_target: str = ""
    shutdown_grace_s: float = 90.0
    # Bearer 서비스 토큰. 비어 있으면 검사하지 않는다(로컬 Mock 개발). Tailscale 바인드 배포에서는 반드시 설정
    service_token: str = ""
    # RPi4 물리 코어 수. vcpus 상한 검사에 쓴다. xl info 의 nr_cpus 를 읽을 수 있으면 그 값을 우선한다.
    host_cpus: int = 4
    instance_root: str = "/srv/xenova/instances"
    # platform 드라이버: xenova_management 모듈 경로. 보드는 /usr/lib/xenova. 여러 개면 os.pathsep 로 구분
    platform_lib: str = "/usr/lib/xenova"
    # platform 드라이버: 다른 요청이 Manager 를 쓰는 동안 기다릴 최대 초. 넘으면 503 PlatformUnavailable(재시도 가능).
    # 정지가 최대 90초 잠금을 쥐는 동안 조회가 스레드풀에 쌓이지 않게 한다. 조회 드라이버 한도 5초와 맞춘다
    platform_busy_timeout_s: float = 5.0
    mock_samples: bool = False
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        # 시간 값은 유한한 양수여야 한다. 음수·NaN·inf 는 Lock.acquire/타임아웃에서 예외나 무한 대기가 되므로 시작 시점에 거부한다
        for name in ("xl_query_timeout_s", "xl_command_timeout_s", "shutdown_grace_s", "platform_busy_timeout_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a finite positive number of seconds, got {value!r}")

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

        driver = read("XENOVA_DRIVER", cls.driver, str).strip().lower()
        if driver not in DRIVERS:
            raise ValueError(f"XENOVA_DRIVER must be one of {DRIVERS}, got {driver!r}")

        return cls(
            driver=driver,
            host=read("XENOVA_HOST", cls.host, str),
            port=read("XENOVA_PORT", cls.port, int),
            xl_query_timeout_s=read("XENOVA_XL_QUERY_TIMEOUT", cls.xl_query_timeout_s, float),
            xl_command_timeout_s=read("XENOVA_XL_COMMAND_TIMEOUT", cls.xl_command_timeout_s, float),
            xl_ssh_target=read("XENOVA_XL_SSH", cls.xl_ssh_target, str).strip(),
            shutdown_grace_s=read("XENOVA_SHUTDOWN_GRACE", cls.shutdown_grace_s, float),
            service_token=read("XENOVA_SERVICE_TOKEN", cls.service_token, str),
            host_cpus=read("XENOVA_HOST_CPUS", cls.host_cpus, int),
            instance_root=read("XENOVA_INSTANCE_ROOT", cls.instance_root, str),
            platform_lib=read("XENOVA_PLATFORM_LIB", cls.platform_lib, str),
            platform_busy_timeout_s=read("XENOVA_PLATFORM_BUSY_TIMEOUT", cls.platform_busy_timeout_s, float),
            mock_samples=read("XENOVA_MOCK_SAMPLES", cls.mock_samples, as_bool),
            log_level=read("XENOVA_LOG_LEVEL", cls.log_level, str).upper(),
        )
