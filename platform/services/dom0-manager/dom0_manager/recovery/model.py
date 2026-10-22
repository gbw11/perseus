"""감시기의 입력(관측·heartbeat)과 출력(판정·건강 스냅샷) 자료형."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from ..models import VmState


class FailureReason(str, Enum):
    """계약 2.5 복구 이벤트의 reason. 판정 근거 하나만 적는다."""

    HEARTBEAT_LOST = "HEARTBEAT_LOST"  # Linux VM: agent.heartbeat 무수신 (기본 30초)
    APP_HEARTBEAT_LOST = "APP_HEARTBEAT_LOST"  # Zephyr VM: app.heartbeat 무수신 (시나리오 D8, 기본 15초)
    DOMAIN_ABSENT = "DOMAIN_ABSENT"  # 의도는 running 인데 Xen 에 도메인이 없음
    DOMAIN_CRASHED = "DOMAIN_CRASHED"  # xl 상태 crashed (on_crash=preserve 로 남은 도메인)


class HealthState(str, Enum):
    """건강 스냅샷의 상태. 복구 판정 유예와 "건강하다"를 분리한다."""

    HEALTHY = "HEALTHY"  # 실행 중이고 생존 신호가 신선함 (신호가 없는 종류는 Xen 실행만 근거)
    STARTING = "STARTING"  # 실행 중이지만 이번 부팅에서 아직 생존 신호를 받지 못함 (유예 안). 건강하다고 보지 않는다
    UNHEALTHY = "UNHEALTHY"  # 판정 사유가 있거나(reason), paused·stopping, 신호가 끊김
    STOPPED = "STOPPED"  # 도메인이 없고 의도가 running 이 아님
    OBSERVATION_UNAVAILABLE = "OBSERVATION_UNAVAILABLE"  # 생존 신호를 관측할 수 없음(구독 미승인, armed 기록 손상·capability 값 오류로 대상 여부 불명, Worker 종료). 장애로 보지 않는다


@dataclass(frozen=True)
class Heartbeat:
    """agent.heartbeat (MQTT-01)."""

    vm_id: UUID
    boot_id: str
    seq: int
    received_monotonic: float  # 받는 쪽 단조 시계. 보드 간 시각(sent_at)으로 판정하지 않는다 (MQTT 계약 1장)


@dataclass(frozen=True)
class AppHeartbeat:
    """app.heartbeat (MQTT-06, 6.1장). Zephyr 주행 VM 의 생존 신호다."""

    vm_id: UUID
    app_id: str
    state: str  # active | standby | idle | error
    boot_id: str
    seq: int
    received_monotonic: float


@dataclass(frozen=True)
class VmObservation:
    """한 VM 의 플랫폼 registry 의도 + Xen 실행 관측."""

    vm_id: UUID
    name: str
    guest_kind: str  # linux | zephyr
    desired_state: str  # running | stopped | unknown (플랫폼 get/list)
    intent_applicable: bool  # 의도가 현재 lifecycle·세대에 적용되는가
    intent_revision: int | None
    lifecycle: str  # REGISTERED | RETIRED | ...
    present: bool  # Xen 에 도메인이 있는가
    xen_state: VmState | None  # present 일 때만
    domid: int | None  # present 일 때만. 재실행마다 바뀌므로 "새 부팅" 판정에 쓴다
    # Zephyr: 템플릿 capability `app_heartbeat`. True 앱 펌웨어, False starter(Xen 상태만), None 선언 없음(신호를 한 번 받은 뒤 armed 로 대상)
    app_heartbeat_expected: bool | None = None
    capability_error: str | None = None  # 템플릿 capability 값이 JSON boolean 이 아니다. 대상 여부를 알 수 없으므로 판정하지 않는다

    @property
    def eligible(self) -> bool:
        """복구 판정 대상인가: 등록돼 있고, 적용 가능한 의도가 running 이다. 사용자가 끈 VM(stopped)은 절대 대상이 아니다."""
        return self.lifecycle == "REGISTERED" and self.intent_applicable and self.desired_state == "running"


@dataclass(frozen=True)
class Judgment:
    """복구 서비스(S15P31E110-125)로 넘기는 판정. 장애 한 건(episode)마다 정확히 한 번 낸다."""

    recovery_id: UUID  # 계약 2.5: 최초 판정부터 최종 성공·포기까지 같은 값
    vm_id: UUID
    reason: FailureReason
    detected_monotonic: float
    guest_kind: str
    desired_state: str
    intent_revision: int | None
    domid: int | None
    xen_state: VmState | None
    heartbeat_age_s: float | None


@dataclass(frozen=True)
class VmHealth:
    """게이트웨이·백엔드용 retained 건강 스냅샷 (주행 명세 3.3: ok, desired_state, manager_seq, ts)."""

    vm_id: UUID
    state: HealthState
    desired_state: str
    xen_state: str | None
    heartbeat_age_s: float | None
    reason: FailureReason | None
    manager_seq: int  # 같은 VM 에서 내용이 바뀔 때만 1 증가. 받는 쪽이 재시작 후 retained 값과 새 값을 구별한다

    @property
    def ok(self) -> bool:
        return self.state == HealthState.HEALTHY

    def payload(self, *, lease_s: float) -> dict:
        """MQTT 계약 11.1. lease_s 는 받는 쪽이 이 스냅샷을 믿어도 되는 시간이다(그 안에 재발행된다)."""
        return {
            "vm_id": str(self.vm_id),
            "ok": self.ok,
            "state": self.state.value,
            "desired_state": self.desired_state,
            "xen_state": self.xen_state,
            "heartbeat_age_s": None if self.heartbeat_age_s is None else round(self.heartbeat_age_s, 3),
            "reason": None if self.reason is None else self.reason.value,
            "manager_seq": self.manager_seq,
            "lease_s": lease_s,
        }
