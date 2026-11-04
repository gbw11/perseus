"""`python -m dom0_manager` 로 서비스를 띄운다. 설정은 환경변수(XENOVA_*)에서 읽는다."""

import os
import socket

import uvicorn

from .config import Settings


class ReadyServer(uvicorn.Server):
    """ASGI startup과 실제 listen 완료 뒤에만 systemd의 후속 작업을 허용한다."""

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        endpoint = os.environ.get("NOTIFY_SOCKET")
        if self.started and endpoint:
            address = "\0" + endpoint[1:] if endpoint.startswith("@") else endpoint
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                # 준비 통지는 작은 로컬 datagram이다. sink 정체로 event loop를 기다리게 하지 않는다.
                notify.setblocking(False)
                notify.sendto(b"READY=1", address)


def main() -> None:
    settings = Settings.from_env()
    server = ReadyServer(uvicorn.Config(
        "dom0_manager.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        ws_max_size=65536,
        ws_max_queue=4,
    ))
    server.run()


if __name__ == "__main__":
    main()
