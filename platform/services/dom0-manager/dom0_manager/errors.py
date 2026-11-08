"""서비스 전역 예외. API 계층이 HTTP 상태 코드와 계약의 Error 본문으로 변환한다.

계약 8.1 장(v1.3): 400 InvalidArgument / 404 VMNotFound / 409 VMExists·VMNameConflict·InvalidState / 507 InsufficientResources /
501 NotSupported / 503 PlatformUnavailable / 504 DriverTimeout / 500 DriverError. 401 Unauthorized 는 미들웨어가 낸다.

platform_code 는 플랫폼 Manager 가 준 원본 코드(HOST_QUIESCING 등)다. 플랫폼 오류에만 붙고 응답 본문에 그대로 나간다.
detail 의 "CODE: message" 문자열은 사람용이며, 호출자의 분기는 platform_code 로 한다.
retryable 은 플랫폼이 준 원본 값이다(코드의 고정 속성이 아니다). operation 은 제어 요청이 받아들여진 뒤의 오류에 붙는 원 작업 문맥이다.
"""

from __future__ import annotations

from typing import Any


class Dom0ManagerError(Exception):
    status_code: int = 500
    code: str = "DriverError"

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        resource: dict[str, Any] | None = None,
        platform_code: str | None = None,
        retryable: bool | None = None,
        operation: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.resource = resource
        self.platform_code = platform_code
        self.retryable = retryable
        self.operation = operation

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        if self.resource is not None:
            out["resource"] = self.resource
        if self.platform_code is not None:
            out["platform_code"] = self.platform_code
        if self.retryable is not None:
            out["retryable"] = self.retryable
        if self.operation is not None:
            out["operation"] = self.operation
        return out


class InvalidArgument(Dom0ManagerError):
    status_code = 400
    code = "InvalidArgument"


class VMNotFound(Dom0ManagerError):
    status_code = 404
    code = "VMNotFound"


class OperationNotFound(Dom0ManagerError):
    """요청 키로 원 제어 작업을 찾지 못함. 조회 시점의 검색 부재일 뿐 미수락·미실행의 증거가 아니다."""

    status_code = 404
    code = "OperationNotFound"


class VMExists(Dom0ManagerError):
    status_code = 409
    code = "VMExists"


class VMNameConflict(Dom0ManagerError):
    """같은 VM 이 아니라 플랫폼이 고른 식별(내부 실행 이름·UUID 예약·MAC 등)이 다른 VM 과 겹침 (409).

    플랫폼 VM_NAME_CONFLICT 다. VMExists(같은 vm_id 의 VM 이 이미 있음 = 생성 재전달 성공 처리)와 섞으면 Spring 이 충돌을
    생성 성공으로 기록하므로 별도 코드로 낸다(2026-11-06 결정). Spring 은 409 VM_NAME_CONFLICT 로 매핑한다.
    """

    status_code = 409
    code = "VMNameConflict"


class InvalidState(Dom0ManagerError):
    status_code = 409
    code = "InvalidState"


class InsufficientResources(Dom0ManagerError):
    status_code = 507
    code = "InsufficientResources"

    def __init__(self, name: str, requested: int, available: int, *, platform_code: str | None = None, retryable: bool | None = None):
        super().__init__(
            f"insufficient {name}: requested {requested}, available {available}",
            resource={"name": name, "requested": requested, "available": available},
            platform_code=platform_code,
            retryable=retryable,
        )


class DriverError(Dom0ManagerError):
    """xl·플랫폼 실행 실패 등 드라이버 수준 오류. 계약 미확정이라 코드는 DriverError 로 두고 상태만 구분한다."""

    status_code = 500
    code = "DriverError"


class DriverTimeout(DriverError):
    """xl·플랫폼 호출이 시한을 넘김 (504). 서비스 전체가 멈추지 않도록 호출 단위로 끊는다."""

    status_code = 504
    code = "DriverTimeout"


class PlatformUnavailable(DriverError):
    """registry·저장소·Xen 이 일시적으로 응답하지 않음 (503). 재시도 가능."""

    status_code = 503
    code = "PlatformUnavailable"


class NotSupportedByPlatform(DriverError):
    """드라이버·플랫폼이 제공하지 않는 동작 (501). 예: xen 모드(조회 전용)의 생성·정지·삭제 명령."""

    status_code = 501
    code = "NotSupported"
