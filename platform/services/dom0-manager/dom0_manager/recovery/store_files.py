"""두 Worker 상태 파일의 부재 판정과 손상 원본 보존만 담당한다.

저장 schema·복구 판단·차단 해제는 각 저장소와 호출자의 책임이다.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path


def read_state(path: Path) -> str | None:
    """진짜 최초 부재만 None. 읽기 오류·남은 격리/임시 기록은 오류다."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # dangling symlink 등 '경로는 있으나 읽지 못함'도 최초 부재가 아니다.
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise OSError(errno.EIO, "state path exists but cannot be read", str(path)) from None
        for suffix in (".corrupt", ".tmp"):
            residue = path.with_suffix(path.suffix + suffix)
            try:
                residue.lstat()
            except FileNotFoundError:
                continue
            raise OSError(errno.EIO, "state missing with retained " + suffix + " evidence", str(path)) from None
        return None


def preserve_corrupt(path: Path) -> None:
    """원본을 이동하지 않고 격리 사본을 만든다. 기존 증거는 덮어쓰지 않는다.

    같은 inode의 사본은 저장 실패 뒤 재시도일 수 있다. 다른 사본이 이미
    있거나 link 자체가 실패하면 호출자가 원본을 교체해서는 안 된다.
    """
    target = path.with_suffix(path.suffix + ".corrupt")
    try:
        os.link(path, target, follow_symlinks=False)
    except FileExistsError:
        if not os.path.samestat(path.lstat(), target.lstat()):
            raise
