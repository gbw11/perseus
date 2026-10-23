"""보드별 HTTP 서비스 토큰을 최초 서비스 시작 시 준비한다. 값은 출력하지 않는다.

기존 EnvironmentFile의 토큰을 우선하며 자동 회전하지 않는다. 공통 이미지에는
토큰을 넣지 않고 systemd가 다음 ExecStart 환경을 읽기 전에 root 전용 파일을 만든다.
"""
from __future__ import annotations

import os
import fcntl
from pathlib import Path
import re
import stat
import tempfile

TOKEN_FILE = Path("/etc/xenova/dom0-manager-token.env")
_TOKEN = re.compile(rb"XENOVA_SERVICE_TOKEN=[A-Za-z0-9._~+/-]{32,256}={0,2}\n\Z")


def _validate(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or not _TOKEN.fullmatch(os.read(fd, 4096))):
            raise ValueError("service token file must be private, regular and valid")
    finally:
        os.close(fd)


def prepare(path: Path = TOKEN_FILE, *, environ=None) -> str:
    """기존 토큰을 보존하거나 256bit 토큰을 배타적으로 게시하고 안전한 상태명만 반환한다."""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        return _prepare(path, environ=environ)
    finally:
        os.close(directory)


def _prepare(path: Path, *, environ=None) -> str:
    environ = os.environ if environ is None else environ
    if path.exists() or path.is_symlink():
        _validate(path)
        return "existing"
    if environ.get("XENOVA_SERVICE_TOKEN"):
        return "environment"
    fd, name = tempfile.mkstemp(prefix=".dom0-token-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(("XENOVA_SERVICE_TOKEN=" + os.urandom(32).hex() + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # 기존/동시 생성 파일을 덮어쓰지 않는다.
        except FileExistsError:
            pass
    finally:
        temporary.unlink()
    _validate(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return "ready"


def main() -> int:
    try:
        status = prepare()
    except (OSError, ValueError):
        print("service token preparation failed; inspect root-only configuration")
        return 1
    print("service token ready (" + status + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
