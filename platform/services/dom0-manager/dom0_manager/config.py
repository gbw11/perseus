"""환경변수 기반 설정. 외부 라이브러리 없이 dataclass 로 둔다 (dom0 이미지 의존성 최소화)."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

DRIVERS = ("mock", "xen", "platform")
# 플랫폼 stop 은 대기 시간을 입력받지 않고(Manager stop 명세는 vm_id·force 뿐) 이 시간을 기다린다. xenova_vm.stop_many 의
# timeout=90 이며 제거 관측도 같은 deadline 을 공유하므로 Manager 호출은 이 시간 안팎에서 끝난다. platform 모드의 shutdown_grace_s 는
# 이 값과 같아야 한다. xl_command_timeout_s 는 xl 서브프로세스에만 걸리고 Manager 호출을 제한하지 않는다
PLATFORM_STOP_WAIT_S = 90.0


@dataclass(frozen=True)
class Settings:
    driver: str = "mock"
    host: str = "127.0.0.1"
    port: int = 8081
    # 계약 v1.1: 조회 드라이버 한도 5초(Spring 대기 6초 안), 정지는 플랫폼이 최대 90초 동기 대기하고 자동 강제 종료 없음.
    # xl_command_timeout_s 는 xl 서브프로세스(create/shutdown/destroy)에만 걸린다. platform 모드의 정지 대기는 PLATFORM_STOP_WAIT_S 고정
    xl_query_timeout_s: float = 5.0
    xl_command_timeout_s: float = 95.0
    # 비어 있지 않으면 xl 을 로컬 대신 SSH 로 부른다 (예: root@100.64.0.1). 개발 PC 에서 실보드 조회를 대조할 때만 쓴다
    xl_ssh_target: str = ""
    shutdown_grace_s: float = 90.0
    # Bearer 서비스 토큰. 비어 있으면 검사하지 않는다(로컬 Mock 개발). Tailscale 바인드 배포에서는 반드시 설정
    service_token: str = ""
    # 내부 게이트웨이용 WebSocket. 명시적으로 활성화하고 서비스 토큰을 설정해야 한다.
    console_enabled: bool = False
    console_lock_dir: str = "/run/xenova/console-locks"
    # RPi4 물리 코어 수. vcpus 상한 검사에 쓴다. xl info 의 nr_cpus 를 읽을 수 있으면 그 값을 우선한다.
    host_cpus: int = 4
    instance_root: str = "/srv/xenova/instances"
    # platform 드라이버: xenova_management 모듈 경로. 보드는 /usr/lib/xenova. 여러 개면 os.pathsep 로 구분
    platform_lib: str = "/usr/lib/xenova"
    # platform 드라이버: 다른 요청이 Manager 를 쓰는 동안 기다릴 최대 초. 넘으면 503 PlatformUnavailable(재시도 가능).
    # 정지가 최대 90초 잠금을 쥐는 동안 조회가 스레드풀에 쌓이지 않게 한다. 조회 드라이버 한도 5초와 맞춘다
    platform_busy_timeout_s: float = 5.0
    # 호출자가 X-Request-Budget-Ms 로 남은 대기 시간을 줄 때, 하위 호출에 넘기기 전에 응답을 돌려줄 몫으로 남겨 둘 초.
    # 실측 전 임시값이다. 0 이면 남은 예산을 전부 하위에 준다
    response_reserve_s: float = 0.5
    # Recovery Worker 가 복구 상태·이벤트를 남기는 곳. GET /api/v1/recovery/events 가 같은 파일을 읽는다 (Worker 의 XENOVA_RECOVERY_STATE_DIR 와 같아야 함)
    recovery_state_dir: str = "/run/xenova/recovery"
    mock_samples: bool = False
    log_level: str = "INFO"
    # Separate bounded execution-log database; empty means the log collector is not deployed.
    runtime_log_db: str = ""
    runtime_log_ingest_config: str = ""

    def __post_init__(self) -> None:
        if self.console_enabled and not self.service_token:
            raise ValueError("console requires XENOVA_SERVICE_TOKEN")
        if self.console_enabled and self.xl_ssh_target:
            raise ValueError("console must run on local dom0, not XENOVA_XL_SSH")
        # 시간 값은 유한한 양수여야 한다. 음수·NaN·inf 는 Lock.acquire/타임아웃에서 예외나 무한 대기가 되므로 시작 시점에 거부한다
        for name in ("xl_query_timeout_s", "xl_command_timeout_s", "shutdown_grace_s", "platform_busy_timeout_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a finite positive number of seconds, got {value!r}")
        if not (math.isfinite(self.response_reserve_s) and self.response_reserve_s >= 0):
            raise ValueError(f"response_reserve_s must be a finite non-negative number of seconds, got {self.response_reserve_s!r}")
        if self.driver == "platform" and self.shutdown_grace_s != PLATFORM_STOP_WAIT_S:
            # 플랫폼은 설정과 무관하게 90초를 기다린다. 다른 값은 운영자가 짧은 정지를 기대하게 만들므로 시작 시점에 거부한다
            raise ValueError(
                f"platform stop waits a fixed {PLATFORM_STOP_WAIT_S:g}s and takes no grace input; "
                f"XENOVA_SHUTDOWN_GRACE must be {PLATFORM_STOP_WAIT_S:g} (or unset) in platform mode, got {self.shutdown_grace_s!r}"
            )

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
            console_enabled=read("XENOVA_CONSOLE_ENABLED", cls.console_enabled, as_bool),
            console_lock_dir=read("XENOVA_CONSOLE_LOCK_DIR", cls.console_lock_dir, str),
            host_cpus=read("XENOVA_HOST_CPUS", cls.host_cpus, int),
            instance_root=read("XENOVA_INSTANCE_ROOT", cls.instance_root, str),
            platform_lib=read("XENOVA_PLATFORM_LIB", cls.platform_lib, str),
            platform_busy_timeout_s=read("XENOVA_PLATFORM_BUSY_TIMEOUT", cls.platform_busy_timeout_s, float),
            response_reserve_s=read("XENOVA_RESPONSE_RESERVE", cls.response_reserve_s, float),
            recovery_state_dir=read("XENOVA_RECOVERY_STATE_DIR", cls.recovery_state_dir, str),
            mock_samples=read("XENOVA_MOCK_SAMPLES", cls.mock_samples, as_bool),
            log_level=read("XENOVA_LOG_LEVEL", cls.log_level, str).upper(),
            runtime_log_db=read("XENOVA_RUNTIME_LOG_DB", cls.runtime_log_db, str),
            runtime_log_ingest_config=read("XENOVA_RUNTIME_LOG_INGEST_CONFIG", cls.runtime_log_ingest_config, str),
        )
