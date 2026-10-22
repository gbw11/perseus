"""내부 API 요청·응답 모델. docs/contracts/dom0-manager-openapi.yaml 의 components.schemas 와 1:1 로 맞춘다.

계약 테스트(tests/test_contract.py)가 생성된 OpenAPI 와 yaml 을 비교하므로, 여기를 바꾸면 yaml 도 같이 바꾼다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
