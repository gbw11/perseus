"""MO-04 내부 WebSocket: gateway → dom0-manager → PTY → xl console.

HTTP 인증 미들웨어는 WebSocket에 적용되지 않으므로 여기서 별도로 인증한다.
브라우저 사용자 인증/VM별 권한은 서비스 토큰을 보유한 게이트웨이의 책임이다.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..console import ConsoleError, ConsoleExited
from ..errors import Dom0ManagerError, InvalidArgument, VMNotFound
from ..models import VmState

router = APIRouter()
logger = logging.getLogger(__name__)
MAX_INPUT = 65536


class ConsoleBusy(Exception):
    pass


class EndRelay(Exception):
    def __init__(self, code: int, reason: str) -> None:
        self.code, self.reason = code, reason


@contextmanager
def claim_console(directory: str, vm_id: UUID):
    """모든 worker가 같은 디렉터리를 사용하면 VM당 한 연결. lock 파일은 삭제하지 않는다."""
    import fcntl

    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / f"{vm_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConsoleBusy from exc
        yield
    finally:
        os.close(fd)


async def live_domain(service, vm_id: UUID):
    # xl/registry 처리는 동기식이므로 관리 이벤트 루프를 막지 않는다.
    domain = await asyncio.to_thread(service.get, vm_id)
    if domain.domid is None or domain.domid <= 0 or domain.state not in (VmState.running, VmState.paused):
        raise EndRelay(4409, "VM has no usable live console")
    return domain


async def relay(websocket: WebSocket, session, service, vm_id: UUID, domid: int) -> None:
    async def receive_input():
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                return
            data = message.get('bytes')
            if data is None:
                text = message.get('text')
                if text is None:
                    raise EndRelay(1003, "binary or UTF-8 text required")
                data = text.encode('utf-8')
            if len(data) > MAX_INPUT:
                raise EndRelay(1009, "console input exceeds 65536 bytes")
            await session.write(data)

    async def send_output():
        while True:
            data = await session.read()
            # 느린 수신자가 PTY reader를 영구 점유하지 않도록 전송에 시한을 둔다.
            await asyncio.wait_for(websocket.send_bytes(data), 5)

    async def watch_domain():
        while True:
            await asyncio.sleep(2)
            domain = await live_domain(service, vm_id)
            if domain.domid != domid:
                raise EndRelay(4409, "VM incarnation changed; reconnect required")

    tasks = [asyncio.create_task(action()) for action in (receive_input, send_output, watch_domain)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket('/vms/{vm_id}/console')
async def vm_console(websocket: WebSocket, vm_id: str):
    settings = websocket.app.state.settings
    if not settings.console_enabled:
        await websocket.close(code=1008, reason="console disabled")
        return
    expected = ('Bearer ' + settings.service_token).encode()
    supplied = websocket.headers.get('authorization', '').encode()
    if not settings.service_token or not hmac.compare_digest(expected, supplied):
        await websocket.close(code=4401, reason="unauthorized")
        return
    if settings.driver == 'mock' or settings.xl_ssh_target:
        await websocket.close(code=1008, reason="local Xen driver required")
        return

    # 인증 뒤 accept하여 이후의 실패는 명확한 WebSocket 종료 코드로 전달한다.
    await websocket.accept()
    code, reason = 1000, "console disconnected"
    try:
        try:
            identifier = UUID(vm_id)
        except ValueError:
            raise EndRelay(4400, "VM identifier must be a UUID") from None
        service = websocket.app.state.service
        with claim_console(settings.console_lock_dir, identifier):
            domain = await live_domain(service, identifier)
            session = websocket.app.state.console_factory(domain.domid)
            async with session:
                # xl 실행 사이에 VM이 바뀌면 입력을 허용하지 않고 닫는다.
                current = await live_domain(service, identifier)
                if current.domid != domain.domid:
                    raise EndRelay(4409, "VM incarnation changed; reconnect required")
                await relay(websocket, session, service, identifier, domain.domid)
    except EndRelay as exc:
        code, reason = exc.code, exc.reason
    except ConsoleBusy:
        code, reason = 4409, "VM console is already connected"
    except InvalidArgument:
        code, reason = 4400, "VM is not a managed guest"
    except VMNotFound:
        code, reason = 4404, "VM not found or no longer running"
    except ConsoleExited as exc:
        code = 1000 if exc.returncode == 0 else 1011
        reason = "console process ended"
    except WebSocketDisconnect:
        return
    except (ConsoleError, Dom0ManagerError, TimeoutError, OSError):
        code, reason = 1011, "console unavailable"
        # 터미널 데이터, 암호, 서비스 토큰, 예외 detail은 로그에 남기지 않는다.
        logger.warning("console unavailable for VM %s", vm_id)
    finally:
        try:
            await asyncio.wait_for(websocket.close(code=code, reason=reason), 2)
        except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
            pass
