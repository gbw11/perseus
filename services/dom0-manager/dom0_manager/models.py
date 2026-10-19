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
    memory_mib: int = Field(ge=128, le=1024, description="배수 제약 없음. 64 단위는 화면 입력 규칙일 뿐이다")


class ResourceName(str, Enum):
    vcpus = "vcpus"
    memory_mib = "memory_mib"


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
    InvalidState = "InvalidState"  # 예비. pause/unpause 엔드포인트가 생기기 전까지 반환하지 않는다


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
