"""서비스 전역 예외. API 계층이 HTTP 상태 코드와 계약의 Error 본문으로 변환한다.

계약 8.1 장(v1.3): 400 InvalidArgument / 404 VMNotFound / 409 VMExists·InvalidState / 507 InsufficientResources /
501 NotSupported / 503 PlatformUnavailable / 504 DriverTimeout / 500 DriverError. 401 Unauthorized 는 미들웨어가 낸다.
"""

from __future__ import annotations

from typing import Any


class Dom0ManagerError(Exception):
    status_code: int = 500
    code: str = "DriverError"

    def __init__(self, message: str, *, detail: str | None = None, resource: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.resource = resource

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        if self.resource is not None:
            out["resource"] = self.resource
        return out


class InvalidArgument(Dom0ManagerError):
    status_code = 400
    code = "InvalidArgument"


class VMNotFound(Dom0ManagerError):
    status_code = 404
    code = "VMNotFound"


class VMExists(Dom0ManagerError):
    status_code = 409
    code = "VMExists"


class InvalidState(Dom0ManagerError):
    status_code = 409
    code = "InvalidState"


class InsufficientResources(Dom0ManagerError):
    status_code = 507
    code = "InsufficientResources"

    def __init__(self, name: str, requested: int, available: int):
        super().__init__(
            f"insufficient {name}: requested {requested}, available {available}",
            resource={"name": name, "requested": requested, "available": available},
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
