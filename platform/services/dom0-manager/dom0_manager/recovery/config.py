"""Recovery Worker 설정. 환경변수 XENOVA_RECOVERY_* 에서 읽는다. 플랫폼·xl 설정은 dom0_manager.config.Settings 를 그대로 쓴다."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

HEARTBEAT_PERIOD_S = 5.0  # MQTT-01 agent.heartbeat 주기(고정). 판정 시간은 이 배수여야 의미가 있다
APP_HEARTBEAT_PERIOD_S = 1.0  # MQTT-06 app.heartbeat 주기. 받는 쪽 끊김 표시는 3초(6.1장)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
HEALTH_LEASE_PERIODS = 3  # vm.health 수명 = 재발행 주기 × 3 (MQTT 계약 11.1). 받는 쪽은 이 안에 못 받으면 관측 불가로 본다


@dataclass(frozen=True)
class RecoverySettings:
    # 감시 주기. heartbeat 주기와 같게 두면 판정 지연이 최대 한 주기다
    interval_s: float = HEARTBEAT_PERIOD_S
    # 플랫폼 registry 의도(Manager.list)를 다시 읽는 주기. list 는 플랫폼 잠금을 쥐고 모든 VM 을 Xen 관측하므로 5초마다 부르지 않는다.
    # 판정 직전에는 그 VM 만 get 으로 재확인하므로 이 캐시가 오래돼도 사용자가 끈 VM 을 재시작하지 않는다
    intent_refresh_s: float = 60.0
    # Linux VM: 이 시간 동안 agent.heartbeat 가 없으면 HEARTBEAT_LOST. 백엔드의 OFFLINE(30초)과 같고 계약 2.5 와 같다.
    # 3×주기(15초)는 STALE 이라 한 번 밀린 heartbeat 로 재시작하지 않도록 하한만 15초로 둔다
    heartbeat_lost_s: float = 30.0
    # Zephyr 주행 VM: 이 시간 동안 app.heartbeat 가 없으면 APP_HEARTBEAT_LOST (시나리오 D8). 표시용 끊김(3초)보다 보수적으로 둔다
    app_heartbeat_lost_s: float = 15.0
    # 도메인이 새로 보인 뒤(새 domid) 이 시간 동안은 생존 신호 손실을 판정하지 않는다. Linux 는 게스트 부팅·에이전트 기동 시간
    boot_grace_s: float = 90.0
    zephyr_boot_grace_s: float = 30.0
    # desired_state 가 running 인데 도메인이 없을 때 이 시간이 지나야 DOMAIN_ABSENT. 플랫폼 start·복구가 진행 중일 수 있다
    absent_grace_s: float = 60.0
    # 건강 스냅샷(vm.health)을 내용이 같아도 다시 내는 주기. retained 값이 오래된 채 남지 않도록 받는 쪽 lease 를 갱신한다
    health_refresh_s: float = 15.0
    # dom0 Mosquitto. Worker 는 dom0 안에서 돌므로 기본 loopback
    broker_host: str = "127.0.0.1"
    broker_port: int = 1883
    broker_username: str | None = None
    broker_password: str | None = None
    # 봉투의 source_id (MQTT 계약 2장). 계정 이름도 같다 (9장)
    source_id: str = "dom0-server"
    # Worker 재시작 뒤에도 남길 상태(armed 등). /run/xenova 는 플랫폼 runtime 디렉터리(tmpfs)라 host 재부팅이면 비워진다
    state_dir: str = "/run/xenova/recovery"
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for name in ("interval_s", "intent_refresh_s", "heartbeat_lost_s", "app_heartbeat_lost_s", "boot_grace_s", "zephyr_boot_grace_s", "absent_grace_s", "health_refresh_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a finite positive number of seconds, got {value!r}")
        if self.heartbeat_lost_s < 3 * HEARTBEAT_PERIOD_S:
            raise ValueError(f"heartbeat_lost_s must be at least {3 * HEARTBEAT_PERIOD_S:g}s (3 heartbeat periods), got {self.heartbeat_lost_s!r}")
        if self.app_heartbeat_lost_s < 3 * APP_HEARTBEAT_PERIOD_S:
            raise ValueError(f"app_heartbeat_lost_s must be at least {3 * APP_HEARTBEAT_PERIOD_S:g}s (3 app heartbeat periods), got {self.app_heartbeat_lost_s!r}")
        if self.boot_grace_s < self.heartbeat_lost_s:
            raise ValueError("boot_grace_s must be at least heartbeat_lost_s; a fresh guest needs time to start its agent")
        if self.zephyr_boot_grace_s < self.app_heartbeat_lost_s:
            raise ValueError("zephyr_boot_grace_s must be at least app_heartbeat_lost_s")
        if self.health_refresh_s < self.interval_s:
            raise ValueError("health_refresh_s must be at least interval_s; snapshots are republished at most once per cycle")
        if self.intent_refresh_s < 3 * HEARTBEAT_PERIOD_S:
            raise ValueError(f"intent_refresh_s must be at least {3 * HEARTBEAT_PERIOD_S:g}s; Manager.list holds the platform lock and observes every VM")
        if not 0 < self.broker_port < 65536:
            raise ValueError(f"broker_port out of range: {self.broker_port!r}")
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {LOG_LEVELS}, got {self.log_level!r}")
        if not self.source_id or any(c in self.source_id for c in "/+# \t"):
            raise ValueError(f"source_id must be a single MQTT topic segment, got {self.source_id!r}")

    @property
    def health_lease_s(self) -> float:
        return HEALTH_LEASE_PERIODS * self.health_refresh_s

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RecoverySettings":
        env = os.environ if env is None else env

        def read(name: str, default, cast):
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            return cast(raw)

        return cls(
            interval_s=read("XENOVA_RECOVERY_INTERVAL", cls.interval_s, float),
            intent_refresh_s=read("XENOVA_RECOVERY_INTENT_REFRESH", cls.intent_refresh_s, float),
            heartbeat_lost_s=read("XENOVA_RECOVERY_HEARTBEAT_LOST", cls.heartbeat_lost_s, float),
            app_heartbeat_lost_s=read("XENOVA_RECOVERY_APP_HEARTBEAT_LOST", cls.app_heartbeat_lost_s, float),
            boot_grace_s=read("XENOVA_RECOVERY_BOOT_GRACE", cls.boot_grace_s, float),
            zephyr_boot_grace_s=read("XENOVA_RECOVERY_ZEPHYR_BOOT_GRACE", cls.zephyr_boot_grace_s, float),
            absent_grace_s=read("XENOVA_RECOVERY_ABSENT_GRACE", cls.absent_grace_s, float),
            health_refresh_s=read("XENOVA_RECOVERY_HEALTH_REFRESH", cls.health_refresh_s, float),
            broker_host=read("XENOVA_RECOVERY_BROKER_HOST", cls.broker_host, str),
            broker_port=read("XENOVA_RECOVERY_BROKER_PORT", cls.broker_port, int),
            broker_username=read("XENOVA_RECOVERY_BROKER_USERNAME", None, str),
            broker_password=read("XENOVA_RECOVERY_BROKER_PASSWORD", None, str),
            source_id=read("XENOVA_RECOVERY_SOURCE_ID", cls.source_id, str).strip(),
            state_dir=read("XENOVA_RECOVERY_STATE_DIR", cls.state_dir, str),
            log_level=read("XENOVA_RECOVERY_LOG_LEVEL", cls.log_level, str).upper(),
        )
