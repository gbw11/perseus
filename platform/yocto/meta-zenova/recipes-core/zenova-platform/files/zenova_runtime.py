"""조기 서비스와 CLI의 잠금·원자적 메타데이터 쓰기. /run은 부팅 동안 유지한다."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

RUN = Path('/run/zenova')


class PlatformError(RuntimeError):
    """준비·계약·생명주기 오류."""


def ensure_runtime(runtime=RUN):
    runtime = Path(runtime)
    runtime.mkdir(mode=0o755, parents=True, exist_ok=True)
    if runtime.is_symlink() or runtime.resolve() != runtime:
        raise PlatformError('runtime directory must be canonical and unlinked')
    return runtime


@contextmanager
def operation_lock(runtime=RUN, *, blocking=False):
    """모든 저장소/레지스트리 작업은 단일 잠금을 쓴다. unlock 때 파일을 지우지 않는다."""
    directory = ensure_runtime(runtime) / 'locks'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise PlatformError('invalid platform lock directory')
    fd = os.open(directory / 'platform.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise PlatformError('platform lock must be an unshared regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise PlatformError('another platform operation is in progress; retry after it completes') from exc
        yield
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, content, mode=0o600):
    """같은 부모의 새 파일을 fsync·rename·부모 fsync 순서로 게시한다."""
    path = Path(path)
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + os.urandom(6).hex())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if temp.exists():
            temp.unlink()
