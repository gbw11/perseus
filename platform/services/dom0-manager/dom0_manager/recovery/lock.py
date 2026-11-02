"""Recovery Worker 단일 실행 잠금. 두 Worker 가 같은 recoveries.json·armed.json·outbox 를 동시에 쓰지 않게 한다.

`XENOVA_RECOVERY_STATE_DIR/worker.lock` 에 non-blocking flock 을 잡고 프로세스가 끝날 때까지 쥔다. 커널이 프로세스 종료 때
풀어 주므로 kill -9 뒤에도 남지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

LOCK_FILE = "worker.lock"
EXIT_LOCKED = 3  # 다른 Worker 가 잠금을 쥐고 있음. systemd RestartPreventExitStatus 로 재시작 루프를 막는다


class AlreadyRunning(Exception):
    pass


def acquire(state_dir: str | Path) -> int:
    """잠금 파일의 fd. 다른 프로세스가 쥐고 있으면 AlreadyRunning."""
    import fcntl  # Linux 전용

    path = Path(state_dir) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = os.read(fd, 32).decode("ascii", "replace").strip() or "?"
        os.close(fd)
        raise AlreadyRunning(f"another recovery worker holds {path} (pid {holder})") from None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    return fd
