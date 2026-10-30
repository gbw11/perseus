"""복구 정책(S15P31E110-125): 재시작 여부·재시도 횟수·대기·시한. 환경변수 XENOVA_RECOVERY_* 로 바꾼다.

정책은 restart / none 둘뿐이다. failover 는 없다(활성 → 대기 승격은 게이트웨이의 일).
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

POLICIES = ("restart", "none")
PLATFORM_DEADLINE_MAX_S = 120.0  # 플랫폼 recover_once 가 허용하는 같은 host deadline 상한


@dataclass(frozen=True)
class RecoveryPolicy:
    # restart: FAILED 판정된 VM 을 플랫폼 recover_once 로 재시작. none: 판정·이벤트만 내고 실행하지 않는다(관측 전용 배포)
    policy: str = "restart"
    # 장애 한 건(recovery_id)에 허용하는 논리적 시도 수. 넘으면 final failed(degraded) 뒤 중단
    max_attempts: int = 3
    # n번째 시도가 실패한 뒤 다음 시도까지 대기. 횟수가 넘치면 마지막 값
    backoff_s: tuple[float, ...] = (5.0, 15.0, 30.0)
    # recover_once 한 번의 deadline. 정지 대기(90초)를 품어야 하고 플랫폼 상한 120초 안이어야 한다
    attempt_deadline_s: float = 110.0
    # 플랫폼이 TARGET_CHANGED·INTENT_MISMATCH 로 대상 변경을 알린 뒤 감시기의 HEALTHY·STOPPED 판단을 기다리는 시간(Linux 부팅 유예 90초 + 한 창)
    verify_timeout_s: float = 150.0
    # 잠금·barrier 같은 일시 거절이나 응답 유실 뒤 같은 시도(같은 attempt id)를 다시 보내는 간격
    transient_retry_s: float = 5.0
    # graceful-restart 가 정상 종료 실패로 끝나면 다음 시도를 force-restart 로 올린다. 실행 중인 VM 을 파괴하는 동작이라 기본은 끔:
    # 켜려면 XENOVA_RECOVERY_ESCALATE_TO_FORCE=1 (플랫폼 force_restart_vm_ids 승인도 있어야 실제로 실행된다). 꺼져 있으면 그 시도는
    # 일반 실패로 소비되고 다음 시도도 graceful-restart 다
    escalate_to_force: bool = False

    def __post_init__(self) -> None:
        if self.policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}, got {self.policy!r}")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError(f"max_attempts must be a positive integer, got {self.max_attempts!r}")
        if not self.backoff_s or any(not (math.isfinite(b) and b >= 0) for b in self.backoff_s):
            raise ValueError(f"backoff_s must be non-negative finite seconds, got {self.backoff_s!r}")
        for name in ("attempt_deadline_s", "verify_timeout_s", "transient_retry_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a finite positive number of seconds, got {value!r}")
        if self.attempt_deadline_s > PLATFORM_DEADLINE_MAX_S:
            raise ValueError(f"attempt_deadline_s must be at most {PLATFORM_DEADLINE_MAX_S:g}s (platform recover_once limit)")

    @property
    def enabled(self) -> bool:
        return self.policy == "restart"

    def backoff_after(self, failures: int) -> float:
        """failures 번째 실패 뒤 대기(1부터)."""
        index = min(max(failures, 1), len(self.backoff_s)) - 1
        return self.backoff_s[index]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RecoveryPolicy":
        env = os.environ if env is None else env

        def read(name: str, default, cast):
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            return cast(raw)

        def backoff(raw: str) -> tuple[float, ...]:
            return tuple(float(part) for part in raw.split(",") if part.strip())

        return cls(
            policy=read("XENOVA_RECOVERY_POLICY", cls.policy, str).strip().lower(),
            max_attempts=read("XENOVA_RECOVERY_MAX_ATTEMPTS", cls.max_attempts, int),
            backoff_s=read("XENOVA_RECOVERY_BACKOFF", cls.backoff_s, backoff),
            attempt_deadline_s=read("XENOVA_RECOVERY_ATTEMPT_DEADLINE", cls.attempt_deadline_s, float),
            verify_timeout_s=read("XENOVA_RECOVERY_VERIFY_TIMEOUT", cls.verify_timeout_s, float),
            transient_retry_s=read("XENOVA_RECOVERY_TRANSIENT_RETRY", cls.transient_retry_s, float),
            escalate_to_force=read("XENOVA_RECOVERY_ESCALATE_TO_FORCE", "0", str).strip().lower() in ("1", "true", "yes"),
        )
