"""Recovery Worker 단일 실행 잠금 (Linux 전용: fcntl)."""

import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("fcntl")

from dom0_manager.recovery.lock import EXIT_LOCKED, LOCK_FILE, AlreadyRunning, acquire  # noqa: E402


def test_second_holder_is_refused_and_the_lock_is_released_when_the_holder_exits(tmp_path):
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {os.getcwd()!r})
        from dom0_manager.recovery.lock import acquire
        acquire({str(tmp_path)!r}); print("held", flush=True); time.sleep(30)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(AlreadyRunning, match=str(holder.pid)):
            acquire(tmp_path)
    finally:
        holder.kill()  # kill -9 와 같다: 커널이 잠금을 푼다
        holder.wait()
    fd = acquire(tmp_path)
    assert (tmp_path / LOCK_FILE).read_text().strip() == str(os.getpid())
    os.close(fd)
    assert EXIT_LOCKED == 3
