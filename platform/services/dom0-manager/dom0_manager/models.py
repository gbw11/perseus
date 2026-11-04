"""내부 API 요청·응답 모델. docs/contracts/dom0-manager-openapi.yaml 의 components.schemas 와 1:1 로 맞춘다.

계약 테스트(tests/test_contract.py)가 생성된 OpenAPI 와 yaml 을 비교하므로, 여기를 바꾸면 yaml 도 같이 바꾼다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class VmState(str, Enum):
    """xl list 에서 읽히는 실제 Xen 상태. '꺼짐'은 상태가 아니라 도메인의 부재다."""

    running = "running"
    stopping = "stopping"
    paused = "paused"
    crashed = "crashed"


class Vm(BaseModel):
    """HTTP VM 응답은 Xen 관측 필드만 담는다. create의 name 전달과 응답 필드는 별개다."""

    model_config = ConfigDict(extra="forbid")

    vm_id: UUID
    state: VmState
    vcpus: int = Field(ge=1)
    memory_mib: int = Field(ge=1)


class CreateVmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vm_id: UUID = Field(description="Spring 이 한 번 생성해 재시도에도 재사용한다")
    name: str = Field(min_length=1, max_length=63, description="Spring의 표시 이름. 실행 이름·경로로 사용하지 않는다")
    template_id: str = Field(min_length=1, description="예 debian, ubuntu, alpine")
    template_version: str = Field(min_length=1)
    vcpus: int = Field(ge=1, le=4)
    memory_mib: int = Field(
        ge=16,
        le=1024,
        description="공통 하한 16(Zephyr 고정 프로필). 템플릿별 하한·상한은 플랫폼 템플릿 resource_limits 와 Spring 설정이 검사한다",
    )


    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        # Java Pattern의 기본 \s는 ASCII 공백만 검사한다. 입력 이름은 변경하지 않는다.
        if value != value.strip(" \t\n\r\f\v") or any(ord(char) < 32 or 0x7f <= ord(char) <= 0x9f or char in ("\u2028", "\u2029") for char in value):
            raise ValueError("name must be one line without control characters or surrounding whitespace")
        return value


class UpdateResourcesRequest(BaseModel):
    """정지한 Linux VM 의 자원 변경(S15P31E110-368). 다음 시작부터 적용된다. true·2.0·"2" 를 정수로 바꾸지 않는다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    vcpus: int = Field(ge=1, le=4)
    memory_mib: int = Field(ge=128, le=1024, description="플랫폼 하한 128, API 상한 1024. 템플릿별 하한은 Spring 이 검사한다")


class VmResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vcpus: int = Field(ge=1)
    memory_mib: int = Field(ge=1)


class ResourceUpdate(BaseModel):
    """자원 변경 결과. 같은 키 재전송도 원 결과(같은 operation_id·before·after)다."""

    model_config = ConfigDict(extra="forbid")

    vm_id: UUID
    operation_id: UUID | None = Field(description="하위 원 자산 작업. 작업 기록이 없는 드라이버(mock)면 null")
    before: VmResources
    after: VmResources
    applies_on_next_start: bool = Field(description="항상 true. 실행 중인 게스트를 바꾸지 않고 다음 시작부터 적용된다")


class ResourceName(str, Enum):
    vcpus = "vcpus"
    memory_mib = "memory_mib"
    ipv4 = "ipv4"  # Linux VM IP 풀(192.168.77.2..99) 고갈


class ResourceError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ResourceName
    requested: int
    available: int


class ErrorCode(str, Enum):
    InvalidArgument = "InvalidArgument"
    VMNotFound = "VMNotFound"
    VMExists = "VMExists"
    InsufficientResources = "InsufficientResources"
    InvalidState = "InvalidState"  # 현재 생명주기에서 수행할 수 없음 (실행 중 VM 의 DELETE, 플랫폼 VM_NOT_STOPPED 등)
    # 드라이버·플랫폼 실패. HTTP 상태와 1:1 로 둔다 (Spring 이 code 로 매핑하므로 상태만으로 구분하지 않는다)
    DriverError = "DriverError"  # 500 그 외 실행 오류
    DriverTimeout = "DriverTimeout"  # 504 xl·플랫폼 시한 초과
    PlatformUnavailable = "PlatformUnavailable"  # 503 registry·저장소·Xen 일시 장애
    NotSupported = "NotSupported"  # 501 플랫폼이 아직 제공하지 않는 동작
    Unauthorized = "Unauthorized"  # 401 Bearer 서비스 토큰 없음·불일치. 미들웨어가 내며 /healthz 포함
    OperationNotFound = "OperationNotFound"  # 404 요청 키로 원 제어 작업을 찾지 못함 (검색 부재, 미수락 증거 아님)


class ControlStatus(str, Enum):
    """제어 요청(현재 stop)의 상태. 실제 VM 상태(GET /vms/{id})와 별개다."""

    pending = "pending"  # 정지 의도를 내구 수락했고 실행이 남음. 같은 키로 재전달하면 원 작업을 이어 간다
    completed = "completed"  # 원 작업 완료
    failed = "failed"  # 원 작업 실패 확정. 수락된 의도(stopped)는 되돌리지 않는다
    uncertain = "uncertain"  # 플랫폼이 결과를 안전하게 확정하지 못함. 자동 재시도·새 키 금지, 원 기록 조사
    superseded = "superseded"  # 이후 사용자 의도가 수락돼 이 요청은 대체됨. 원 결과로 현재 상태를 덮지 않는다
    unknown = "unknown"  # 수락 여부를 확인하지 못함(응답 유실·수락 저장 미확인). 미수락으로 추측하지 않는다
    rejected = "rejected"  # 수락 전에 거절됨. 의도는 바뀌지 않았다


class ControlOperation(BaseModel):
    """제어 요청 하나의 상태(S15P31E110-305). 하위 원 작업 기록 중 허용된 필드만 담는다(raw journal·경로·인증정보 없음)."""

    model_config = ConfigDict(extra="forbid")

    vm_id: UUID
    action: str = Field(description="stop 또는 reboot")
    request_key: str | None = Field(default=None, description="최초 제출 전에 호출자가 정한 Idempotency-Key")
    operation_id: UUID | None = Field(default=None, description="하위 원 제어 작업. 수락 전 거절·작업 기록 없는 드라이버면 null")
    status: ControlStatus
    accepted: bool | None = Field(default=None, description="의도 수락 여부. 확인하지 못했으면 null")
    phase: str | None = Field(default=None, description="원 작업 단계(prepared·accepted·executing·completed·failed·uncertain)")
    intent_revision: int | None = Field(default=None, description="내구 수락한 의도의 순서")
    host_boot_id: str | None = Field(default=None, description="수락한 host 부팅")
    lifecycle_generation: int | None = None
    deadline_monotonic_ms: int | None = Field(default=None, description="원 실행 기한(그 host 의 단조 시각 ms). 다른 시계와 비교하지 않는다")
    execution_deadline_expired: bool | None = Field(default=None, description="같은 host 에서 원 기한이 지났는가. 확인할 수 없으면 null")
    next_action: str | None = Field(default=None, description="플랫폼이 권하는 다음 동작(진단 문자열, 고정 열거값 아님)")
    error_code: str | None = Field(default=None, description="원 작업의 플랫폼 오류 코드")
    warnings: list[str] = Field(default_factory=list, description="진단 경고 코드(DIAGNOSTIC_UNAVAILABLE 등). 결과를 바꾸지 않는다")


class Error(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    detail: str | None = Field(default=None, description="백엔드 로그용 Xen 상세. 프론트에 노출하지 않는다")
    resource: ResourceError | None = Field(default=None, description="InsufficientResources 에만 있다")
    platform_code: str | None = Field(
        default=None,
        description="플랫폼 Manager 가 준 원본 오류 코드(HOST_QUIESCING 등). 플랫폼 오류에만 있다. 분기는 detail 문자열이 아니라 이 값으로 한다",
    )
    retryable: bool | None = Field(default=None, description="플랫폼이 준 원본 재시도 가능 여부. 코드만으로 재시도 정책을 만들지 않는다")
    operation: ControlOperation | None = Field(default=None, description="제어 요청이 받아들여진 뒤의 오류에만 있다. 원 작업 문맥")


class HealthStatus(str, Enum):
    ok = "ok"
    xen_unavailable = "xen_unavailable"


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: HealthStatus


class StatsSource(str, Enum):
    """Spring 메트릭 필터의 허용값과 같은 문자열. 에이전트 보고(guest_agent)와 구분한다."""

    xen = "xen"


class UnavailableReason(str, Enum):
    NO_DEVICE = "NO_DEVICE"  # NETS·VBDS 가 0 (Zephyr 등)
    DEVICE_ERROR = "DEVICE_ERROR"  # xentop 이 '-' 로 표시
    NOT_REPORTED = "NOT_REPORTED"  # n/a, no limit
    PARSE_ERROR = "PARSE_ERROR"


class VmStats(BaseModel):
    """계약 2.4 통계 객체. xentop 누적 카운터·현재값만 담고 사용률은 Spring 이 연속 표본으로 계산한다.
    측정하지 못한 필드는 0 이 아니라 null 이며 unavailable 에 사유가 있다."""

    model_config = ConfigDict(extra="forbid")

    vm_id: UUID
    domid: int = Field(ge=1, description="재실행마다 바뀐다. 누적값 초기화(재시작) 판정에 쓴다")
    source: StatsSource = StatsSource.xen
    sampled_at: datetime
    state: VmState
    vcpus: int = Field(ge=1)
    cpu_time_s: int = Field(ge=0, description="도메인 시작 후 누적 CPU 시간(초)")
    memory_mib: int = Field(ge=0)
    max_memory_mib: int | None = Field(description="상한이 없으면 null (NOT_REPORTED)")
    net_rx_bytes: int | None = Field(description="도메인 시작 후 누적. 장치가 없으면 null (NO_DEVICE)")
    net_tx_bytes: int | None
    disk_rd_bytes: int | None = Field(description="섹터 × 512 누적. 장치가 없으면 null (NO_DEVICE), xentop 오류면 null (DEVICE_ERROR)")
    disk_wr_bytes: int | None
    unavailable: dict[str, UnavailableReason] = Field(default_factory=dict, description="측정하지 못한 필드 → 사유")


class StatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sampled_at: datetime
    vms: list[VmStats]


class RecoveryEventType(str, Enum):
    """계약 2.5. Spring EventType 의 문자열과 같다."""

    started = "recovery.started"
    completed = "recovery.completed"  # Worker 의 시도로 새 Xen 실행이 관측됨
    recovered = "recovery.recovered"  # 시도가 완료되기 전에 다른 경로(사용자 조작·플랫폼 쪽 재시작·신호 복귀)로 건강해짐
    cancelled = "recovery.cancelled"  # 사용자 stop·삭제·의도 변경으로 대상이 아니게 됨
    failed = "recovery.failed"  # 실제 복구 실패(final 이면 더 시도하지 않음)


class RecoveryEventError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, description="Worker 의 결과 코드. 플랫폼 응답이면 platform_code 와 같고, 그 밖에 TARGET_NOT_ELIGIBLE(cancelled)·REOBSERVE_TIMEOUT·RECONCILE_REQUIRED·INTERNAL_ERROR")
    platform_code: str | None = Field(default=None, description="플랫폼 원본 코드(8.1절)만. Worker 가 스스로 끝낸 경우 null")
    message: str | None = None


class RecoveryEvent(BaseModel):
    """계약 2.5 복구 이벤트 객체. Recovery Worker(125) 가 시도마다 남긴다."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    type: RecoveryEventType
    vm_id: UUID
    recovery_id: UUID = Field(description="장애 한 건. 최초 판정부터 최종 성공·포기까지 같은 값")
    recovery_attempt_id: UUID | None = Field(default=None, description="논리적 시도. 재전달·결과 조회는 같은 값 재사용")
    attempt: int = Field(ge=0)
    reason: str
    mode: str | None = None
    detected_at: datetime
    occurred_at: datetime
    attempt_duration_ms: int | None = None
    recovery_duration_ms: int | None = None
    execution_id: str | None = None
    final: bool | None = None
    next_retry_at: datetime | None = None
    error: RecoveryEventError | None = None

    @model_validator(mode="after")
    def _type_rules(self) -> "RecoveryEvent":
        """계약 2.5 의 유형별 필드 규칙. 타입은 맞지만 의미가 어긋난 이벤트(손상된 상태 파일 등)를 내보내지 않는다."""
        if self.attempt < 1 or not self.mode:
            raise ValueError("recovery event requires attempt >= 1 and mode")
        if self.recovery_attempt_id is None:
            # 끝난 시도 뒤(backoff 중)의 recovered·cancelled 만 장애 단위로 시도 ID 가 없다
            if self.type not in (RecoveryEventType.recovered, RecoveryEventType.cancelled) or self.attempt_duration_ms is not None:
                raise ValueError("only a recovery-level recovered/cancelled may omit recovery_attempt_id, and then attempt_duration_ms is null")
        if self.type == RecoveryEventType.started:
            extra = [n for n in ("attempt_duration_ms", "recovery_duration_ms", "execution_id", "final", "next_retry_at", "error") if getattr(self, n) is not None]
            if extra:
                raise ValueError(f"recovery.started must not carry {extra}")
        elif self.type == RecoveryEventType.completed:
            if not self.execution_id or self.recovery_duration_ms is None:
                raise ValueError("recovery.completed requires execution_id and recovery_duration_ms")
            if self.final is not None or self.next_retry_at is not None or self.error is not None:
                raise ValueError("recovery.completed must not carry final, next_retry_at or error")
        elif self.type in (RecoveryEventType.recovered, RecoveryEventType.cancelled):
            if self.recovery_duration_ms is None:
                raise ValueError(f"{self.type.value} requires recovery_duration_ms")
            if self.execution_id is not None or self.final is not None or self.next_retry_at is not None:
                raise ValueError(f"{self.type.value} must not carry execution_id, final or next_retry_at")
            if self.type == RecoveryEventType.recovered and self.error is not None:
                raise ValueError("recovery.recovered must not carry error")
            if self.type == RecoveryEventType.cancelled and self.error is None:
                raise ValueError("recovery.cancelled requires error (the cancel cause)")
        else:
            if self.final is None or self.error is None or self.execution_id is not None:
                raise ValueError("recovery.failed requires final and error and no execution_id")
            if self.final and (self.recovery_duration_ms is None or self.next_retry_at is not None):
                raise ValueError("final recovery.failed requires recovery_duration_ms and no next_retry_at")
            if not self.final and (self.next_retry_at is None or self.recovery_duration_ms is not None):
                raise ValueError("non-final recovery.failed requires next_retry_at and no recovery_duration_ms")
        return self


class RecoveryEventsResponse(BaseModel):
    """계약 4.7."""

    model_config = ConfigDict(extra="forbid")

    events: list[RecoveryEvent]
    next_after_event_id: UUID | None = Field(default=None, description="돌려준 마지막 이벤트의 event_id. events 가 비어 있으면 null")
    oldest_event_id: UUID | None = Field(default=None, description="보관된 가장 오래된 이벤트(진단용)")
    gap: bool = Field(description="after_event_id 가 보관 범위 밖이거나 커서 뒤의 보관 줄이 손상돼 그 이후 이력을 잃었을 수 있음")


# ---- CPU 배치 조회 (S15P31E110-353, 계약 4.11) ----
# 플랫폼 cpu-layout 응답의 공개 필드만 옮긴다(모르는 필드는 버린다). null 은 '해당 없음·모름'이며 true/false 로 채우지 않는다.


class _CpuModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class VcpuAffinity(_CpuModel):
    vcpu: int = Field(ge=0)
    pcpus: list[int] = Field(description="이 vCPU 가 돌 수 있는 물리 CPU(hard affinity)")


class CpuHost(_CpuModel):
    online_pcpus: list[int]
    max_cpu_id: int
    pool_name: str
    scheduler: str
    desired_dom0_pcpus: list[int] | None = Field(description="정책의 공유 집합. 정책 미설정이면 null")
    observed_dom0_hard_affinity: list[VcpuAffinity] = Field(description="실제 dom0 vCPU 마스크 전부")
    ready: bool = Field(description="현재 host 부팅·정책 revision 의 준비 기록과 실제 배치가 일치")


class CpuReservation(_CpuModel):
    """전용 코어 예약. 정책 1.0 의 {vm_id, pcpu} 와 2.0 의 {vm_id, mode: exclusive, pcpus} 를 같은 모양으로 맞춘다.
    두 형식이 섞였거나 2.0 의 mode 가 exclusive 가 아니면 전용 예약으로 내보내지 않고 거부한다."""

    vm_id: UUID
    pcpus: list[int]

    @model_validator(mode="before")
    @classmethod
    def _one_shape(cls, value):
        if not isinstance(value, dict):
            return value
        if "pcpu" in value:
            if "pcpus" in value or "mode" in value:
                raise ValueError("reservation mixes the 1.0 pcpu and 2.0 mode/pcpus shapes")
            return {"vm_id": value.get("vm_id"), "pcpus": [value["pcpu"]]}
        if value.get("mode") != "exclusive":
            raise ValueError(f"reservation mode must be exclusive, got {value.get('mode')!r}")
        return {k: v for k, v in value.items() if k != "mode"}


class CpuObserved(_CpuModel):
    presence: Literal["present", "absent"] = Field(description="absent 면 나머지는 null(정지는 불일치가 아니다)")
    domid: int | None
    xen_unique_id: str | None
    vcpus: int | None
    hard_affinity: list[VcpuAffinity] | None

    @model_validator(mode="after")
    def _matches_presence(self):
        values = (self.domid, self.xen_unique_id, self.vcpus, self.hard_affinity)
        if self.presence == "absent" and any(v is not None for v in values):
            raise ValueError("absent domain must not carry observed domid/identity/vcpus/affinity")
        if self.presence == "present" and any(v is None for v in values):
            raise ValueError("present domain must carry observed domid/identity/vcpus/affinity")
        return self


class CpuConflict(_CpuModel):
    domid: int
    vm_id: UUID | None = Field(default=None, description="관리 VM 이 아니면(Domain-0 등) null")
    name: str | None = None
    vcpu: int
    pcpus: list[int] = Field(description="겹친 물리 CPU")


class CpuIssue(_CpuModel):
    """AFFINITY_MISMATCH · EXCLUSIVE_CONFLICT · CPU_NOT_READY · POLICY_PUBLICATION_PENDING. 코드마다 쓰는 필드가 다르다."""

    code: Literal["AFFINITY_MISMATCH", "EXCLUSIVE_CONFLICT", "CPU_NOT_READY", "POLICY_PUBLICATION_PENDING"]
    vm_id: UUID | None = None
    domid: int | None = None
    name: str | None = None
    expected: list[VcpuAffinity] | None = None
    actual: list[VcpuAffinity] | None = None
    conflicts: list[CpuConflict] | None = None
    operation_id: str | None = None

    @model_serializer(mode="wrap")
    def _present_fields_only(self, handler):
        """코드가 쓰지 않는 필드는 내지 않는다(플랫폼 issue 와 같은 모양). 다른 모델의 null 은 의미가 있어 그대로 둔다."""
        return {k: v for k, v in handler(self).items() if v is not None}


class VmCpuLayout(_CpuModel):
    vm_id: UUID
    execution_id: str | None = Field(description="현재 Xen 실행과 결합한 실행 ID. 미관리·부재는 null")
    vcpus: int
    allocation: Literal["exclusive", "shared"] | None = Field(description="정책 미설정이면 null")
    desired_hard_affinity: list[VcpuAffinity] | None
    observed: CpuObserved
    affinity_matches: bool | None = Field(description="실제 수량·마스크가 정책과 일치. 정지·미설정이면 null")
    exclusive_verified: bool | None = Field(description="전용 VM 의 마스크 일치 + 다른 도메인과 중복 없음. 공유·정지·미설정이면 null")
    issues: list[CpuIssue]


class CpuLayout(_CpuModel):
    """조회 성공이 배치 준수를 뜻하지 않는다. host.ready·affinity_matches·exclusive_verified·issues 를 함께 본다."""

    configured: bool
    policy_revision: int | None
    policy_schema_version: str | None = None
    host_boot_id: str
    observed_monotonic_ms: int = Field(description="관측한 host 의 단조 시각 ms. UTC 아님, 다른 시계와 빼지 않는다")
    host: CpuHost
    reservations: list[CpuReservation]
    vms: list[VmCpuLayout] = Field(description="전체 조회면 등록된 모든 VM, 한 VM 조회면 그 VM 하나")
    issues: list[CpuIssue]
