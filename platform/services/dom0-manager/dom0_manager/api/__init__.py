from fastapi import Request

from ..service import VmService


def get_service(request: Request) -> VmService:
    return request.app.state.service
