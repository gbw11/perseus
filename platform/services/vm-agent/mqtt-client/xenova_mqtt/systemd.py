"""systemd 서비스 통지(sd_notify)를 표준 라이브러리로 보낸다. libsystemd·python-systemd 없이 쓴다.

`Type=notify` 유닛이면 `ready()` 로 준비 완료를, `WatchdogSec=` 유닛이면 `watchdog()` 으로 살아 있음을 알린다.
systemd 밖(개발 PC, 시험)에서는 NOTIFY_SOCKET 이 없어 아무 일도 하지 않고 False 를 돌려준다. 통지 실패가 앱을 멈추지 않는다.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Mapping


def notify(state: str, *, env: Mapping[str, str] | None = None, factory: Callable[[], socket.socket] | None = None) -> bool:
    env = os.environ if env is None else env
    address = env.get("NOTIFY_SOCKET")
    if not address or address[0] not in "/@":
        return False
    target = "\0" + address[1:] if address[0] == "@" else address  # @ 는 추상 네임스페이스
    try:
        with (factory or _unix_datagram)() as sock:
            sock.sendto(state.encode("utf-8"), target)
    except OSError:
        return False
    return True


def ready(**kwargs) -> bool:
    return notify("READY=1", **kwargs)


def watchdog(**kwargs) -> bool:
    return notify("WATCHDOG=1", **kwargs)


def watchdog_interval_s(env: Mapping[str, str] | None = None) -> float | None:
    """유닛의 WatchdogSec. 이 프로세스가 대상이 아니거나 설정이 없으면 None."""
    env = os.environ if env is None else env
    usec, pid = env.get("WATCHDOG_USEC"), env.get("WATCHDOG_PID")
    if not usec:
        return None
    if pid and pid != str(os.getpid()):
        return None
    try:
        value = int(usec)
    except ValueError:
        return None
    return value / 1_000_000 if value > 0 else None


def _unix_datagram() -> socket.socket:
    return socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
