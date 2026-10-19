"""내부 API 요청·응답 모델. contracts/dom0-manager-openapi.yaml 의 components.schemas 와 1:1 로 맞춘다.

계약 테스트(tests/test_contract.py)가 생성된 OpenAPI 와 yaml 을 비교하므로, 여기를 바꾸면 yaml 도 같이 바꾼다.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VmState(str, Enum):
    """xl list 에서 읽히는 실제 Xen 상태. '꺼짐'은 상태가 아니라 도메인의 부재다."""

    running = "running"
    stopping = "stopping"
    paused = "paused"
    crashed = "crashed"


class Vm(BaseModel):
    """dom0-manager 가 Xen 에서 직접 읽는 필드만. name·template 은 Spring 이 보유한다."""

    model_config = ConfigDict(extra="forbid")

    vm_id: UUID
    state: VmState
    vcpus: int = Field(ge=1)
    memory_mib: int = Field(ge=1)


class CreateVmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vm_id: UUID = Field(description="Spring 이 한 번 생성해 재시도에도 재사용한다")
    template_id: str = Field(min_length=1, description="예 debian, ubuntu, alpine")
    template_version: str = Field(min_length=1)
    vcpus: int = Field(ge=1, le=4)
    memory_mib: int = Field(
        ge=16,
        le=1024,
        description="공통 하한 16(Zephyr 고정 프로필). 템플릿별 하한·상한은 플랫폼 템플릿 resource_limits 와 Spring 설정이 검사한다",
    )


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


class HealthStatus(str, Enum):
    ok = "ok"
    xen_unavailable = "xen_unavailable"


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: HealthStatus
