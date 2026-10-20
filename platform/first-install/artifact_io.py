"""호스트 산출물의 경로·해시 검증과 기존 출력을 덮어쓰지 않는 원자적 게시."""
import ctypes
import hashlib
import os
from pathlib import Path
import re

def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def regular(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError(f'input must be an explicit nonempty regular file, not a latest symlink: {path}')
    if path.resolve() != path or str(path).startswith(('/dev/', '/proc/', '/sys/')):
        raise ValueError('input path contains a symlink or device namespace')
    return path


def checked(path, expected):
    path = regular(path)
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected) or sha(path) != expected:
        raise ValueError(f'input SHA-256 mismatch: {path}')
    return path


def new_path(path, *, allow_hidden=False):
    if not path.is_absolute() or path.is_symlink() or path.exists():
        raise ValueError("output must be a new absolute path")
    if (path.name.startswith(".") and not allow_hidden) or not path.parent.is_dir():
        raise ValueError("output requires an existing parent and a non-hidden name")
    if str(path.resolve()).startswith(("/dev/", "/proc/", "/sys/")):
        raise ValueError("output cannot be a device or virtual filesystem")
    if path.parent.resolve() != path.parent:
        raise ValueError('output/work parent must not contain symlinks')


def publish(source, target, *, allow_hidden=False):
    """같은 파일시스템의 renameat2(NOREPLACE)로 게시해 경합 시에도 기존 출력을 보존한다."""
    new_path(target, allow_hidden=allow_hidden)
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise ValueError('Linux renameat2 is required for no-replace publication')
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
