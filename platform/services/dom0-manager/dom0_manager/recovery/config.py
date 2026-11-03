"""Recovery Worker 설정. 환경변수 XENOVA_RECOVERY_* 에서 읽는다. 플랫폼·xl 설정은 dom0_manager.config.Settings 를 그대로 쓴다."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

HEARTBEAT_PERIOD_S = 5.0  # MQTT-01 agent.heartbeat 주기(고정). 판정 시간은 이 배수여야 의미가 있다
APP_HEARTBEAT_PERIOD_S = 1.0  # MQTT-06 app.heartbeat 주기. 받는 쪽 끊김 표시는 3초(6.1장)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
SOLE_PUBLISHER_POLICIES = ("hold", "trust")
HEALTH_LEASE_PERIODS = 3  # vm.health 수명 = 재발행 주기 × 3 (MQTT 계약 11.1). 받는 쪽은 이 안에 못 받으면 관측 불가로 본다


@dataclass(frozen=True)
class RecoverySettings:
    # 감시 주기. heartbeat 주기와 같게 두면 판정 지연이 최대 한 주기다
    interval_s: float = HEARTBEAT_PERIOD_S
    # 템플릿 capability 를 다시 읽는 주기. 의도는 매 주기 Manager intents 로 읽는다. intents 를 모르는 옛 Manager 에서는 의도도
    # 이 주기로 list 로 읽는다(list 는 플랫폼 잠금을 쥐고 모든 VM 을 Xen 관측하므로 5초마다 부르지 않는다)
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
    # 같은 채널에 신호를 보내는 동료 VM 이 없을 때(단독 발행자) 신호 손실을 판정할지. 채널마다 따로 정한다. hold 는 보류한다: 동료가
    # 없으면 VM 장애와 브로커·브리지·ACL 같은 공통 경로 장애를 구별할 수 없다. trust 는 채널 전달 증거만으로 판정한다.
    # Linux VM 이 하나뿐인 보드에서는 agent 만 trust 로 연다. app 을 열면 남은 주행 VM 하나를 공통 경로 장애 중에 force-restart 할 수 있다
    agent_sole_publisher: str = "hold"
    app_sole_publisher: str = "hold"
    # 동료 발행자가 "지금 전달 중"이라고 볼 신선도 창. 손실 판정 창과 따로 둔다: 공통 경로가 끊기면 VM 마다 마지막 수신이 최대 한
    # 주기씩 어긋나므로, 손실 창을 그대로 쓰면 늦게 받은 동료가 아직 신선해 보여 먼저 받은 VM 을 개별 장애로 오판한다.
    # 손실 창 − 주기 이하면 이 어긋남으로는 오판하지 않는다. None 이면 agent min(15, 손실−5)·app min(5, 손실−1)
    agent_peer_fresh_s: float | None = None
    app_peer_fresh_s: float | None = None
    # 건강 스냅샷(vm.health)을 내용이 같아도 다시 내는 주기. retained 값이 오래된 채 남지 않도록 받는 쪽 lease 를 갱신한다
    health_refresh_s: float = 15.0
    # dom0 Mosquitto. Worker 는 dom0 안에서 돌므로 기본 loopback
    broker_host: str = "127.0.0.1"
    broker_port: int = 1883
    broker_username: str | None = None
    broker_password: str | None = None
    # 봉투의 source_id (MQTT 계약 2장). 계정 이름도 같다 (9장)
    source_id: str = "dom0-server"
    # 주행 VM 보호(S15P31E110-346)에 쓰는 Gateway 로컬 소켓(계약 gateway-provider-v1 G1). drive config 가 있을 때만 쓴다
    gateway_socket: str = "/run/xenova-gateway/control.sock"
    # Gateway 호출 한 번(연결·송신·수신)의 상한. 넘으면 결과를 모르는 것으로 보고 같은 키로 다시 묻는다. 클라이언트 허용 범위 (0, 10]
    gateway_call_budget_s: float = 1.0
    # 보호 요청의 Gateway 기한(수락·출력 철회까지). 복구 시도 기한 안으로 자른다. Gateway history 정책을 쓰면 retry_ms 이하여야 한다
    gateway_protect_deadline_s: float = 5.0
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
        if not (math.isfinite(self.gateway_call_budget_s) and 0 < self.gateway_call_budget_s <= 10):
            raise ValueError(f"gateway_call_budget_s must be in (0, 10] seconds, got {self.gateway_call_budget_s!r}")
        if not (math.isfinite(self.gateway_protect_deadline_s) and self.gateway_call_budget_s < self.gateway_protect_deadline_s <= 60):
            raise ValueError(f"gateway_protect_deadline_s must be above gateway_call_budget_s and at most 60s, got {self.gateway_protect_deadline_s!r}")
        if not self.gateway_socket.startswith("/"):
            raise ValueError(f"gateway_socket must be an absolute path, got {self.gateway_socket!r}")
        if not 0 < self.broker_port < 65536:
            raise ValueError(f"broker_port out of range: {self.broker_port!r}")
        for name, lost, period, default in (("agent_peer_fresh_s", self.heartbeat_lost_s, HEARTBEAT_PERIOD_S, 15.0),
                                            ("app_peer_fresh_s", self.app_heartbeat_lost_s, APP_HEARTBEAT_PERIOD_S, 5.0)):
            value = getattr(self, name)
            if value is None:
                value = min(default, lost - period)
                object.__setattr__(self, name, value)
            if not (math.isfinite(value) and 2 * period <= value <= lost - period):
                raise ValueError(f"{name} must be between {2 * period:g}s (2 periods) and {lost - period:g}s (loss window - 1 period), got {value!r}")
        for name in ("agent_sole_publisher", "app_sole_publisher"):
            if getattr(self, name) not in SOLE_PUBLISHER_POLICIES:
                raise ValueError(f"{name} must be one of {SOLE_PUBLISHER_POLICIES}, got {getattr(self, name)!r}")
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
            agent_sole_publisher=read("XENOVA_RECOVERY_AGENT_SOLE_PUBLISHER", cls.agent_sole_publisher, str).strip().lower(),
            app_sole_publisher=read("XENOVA_RECOVERY_APP_SOLE_PUBLISHER", cls.app_sole_publisher, str).strip().lower(),
            agent_peer_fresh_s=read("XENOVA_RECOVERY_AGENT_PEER_FRESH", None, float),
            app_peer_fresh_s=read("XENOVA_RECOVERY_APP_PEER_FRESH", None, float),
            health_refresh_s=read("XENOVA_RECOVERY_HEALTH_REFRESH", cls.health_refresh_s, float),
            broker_host=read("XENOVA_RECOVERY_BROKER_HOST", cls.broker_host, str),
            broker_port=read("XENOVA_RECOVERY_BROKER_PORT", cls.broker_port, int),
            broker_username=read("XENOVA_RECOVERY_BROKER_USERNAME", None, str),
            broker_password=read("XENOVA_RECOVERY_BROKER_PASSWORD", None, str),
            source_id=read("XENOVA_RECOVERY_SOURCE_ID", cls.source_id, str).strip(),
            gateway_socket=read("XENOVA_RECOVERY_GATEWAY_SOCKET", cls.gateway_socket, str),
            gateway_call_budget_s=read("XENOVA_RECOVERY_GATEWAY_CALL_BUDGET", cls.gateway_call_budget_s, float),
            gateway_protect_deadline_s=read("XENOVA_RECOVERY_GATEWAY_PROTECT_DEADLINE", cls.gateway_protect_deadline_s, float),
            state_dir=read("XENOVA_RECOVERY_STATE_DIR", cls.state_dir, str),
            log_level=read("XENOVA_RECOVERY_LOG_LEVEL", cls.log_level, str).upper(),
        )
