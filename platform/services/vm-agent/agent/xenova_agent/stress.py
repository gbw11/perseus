"""stress 데모 명령(MQTT-04)의 부하 프로세스.

- 부하는 에이전트와 다른 프로세스에서 낸다. heartbeat·metrics 스레드가 GIL 이나 메모리 압박을 함께 받지 않고, 끝나면 프로세스가
  사라지면서 CPU·메모리가 확실히 풀린다
- CPU 부하는 워커 하나당 프로세스 하나(파이썬 한 프로세스는 코어 하나만 쓴다). nice 19 로 돌아 남는 CPU 를 다 쓰되 에이전트를 밀지 않는다
- 메모리 부하는 지정한 크기를 잡고 페이지마다 써서 실제 RSS 로 만든다. oom_score_adj 를 1000 으로 올려 메모리가 모자라면 에이전트보다
  먼저 죽는다
- 각 프로세스는 스스로 정해진 시간에 끝난다. 부모(StressRun)도 시간이 지나면 남은 프로세스를 끝내 부하가 남지 않게 한다

`python -m xenova_agent.stress cpu SECONDS` / `python -m xenova_agent.stress memory MIB SECONDS`
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

MIB = 1024 * 1024
# 부모가 시간이 지난 뒤 자식이 스스로 끝나기를 기다리는 여유. 그 뒤에도 남아 있으면 끝낸다
GRACE_S = 2.0
TERMINATE_WAIT_S = 1.0

Launcher = Callable[[Sequence[str]], "subprocess.Popen[bytes]"]


def launch(args: Sequence[str]) -> "subprocess.Popen[bytes]":
    return subprocess.Popen(
        [sys.executable, "-m", "xenova_agent.stress", *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
    )


def mem_available_mib(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    """MemAvailable(MiB). 읽지 못하면 None."""
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class StressRun:
    """한 번의 stress 실행. 시작한 프로세스들을 들고 있다가 끝나면 정리한다."""

    def __init__(
        self,
        cpu_workers: int,
        memory_mib: int,
        duration_s: int,
        *,
        launcher: Launcher = launch,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self.deadline = clock() + duration_s
        self._procs: list[subprocess.Popen[bytes]] = []
        try:
            for _ in range(cpu_workers):
                self._procs.append(launcher(["cpu", str(duration_s)]))
            if memory_mib:
                self._procs.append(launcher(["memory", str(memory_mib), str(duration_s)]))
        except Exception:
            self.stop()
            raise

    def poll(self) -> str | None:
        """None 이면 아직 도는 중, "succeeded" 는 시간을 채움, "failed" 는 어떤 프로세스가 일찍 비정상 종료(OOM 등)."""
        codes = [proc.poll() for proc in self._procs]
        if any(code not in (None, 0) for code in codes):
            self.stop()
            return "failed"
        if all(code == 0 for code in codes):
            return "succeeded"
        if self._clock() >= self.deadline + GRACE_S:
            self.stop()
            return "succeeded"
        return None

    def stop(self) -> None:
        """남은 프로세스를 끝낸다. 여러 번 불러도 된다."""
        alive = [proc for proc in self._procs if proc.poll() is None]
        for proc in alive:
            proc.terminate()
        for proc in alive:
            try:
                proc.wait(timeout=TERMINATE_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _lower_priority(oom_score_adj: int | None = None) -> None:
    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass
    if oom_score_adj is not None:
        try:
            Path("/proc/self/oom_score_adj").write_text(str(oom_score_adj), encoding="ascii")
        except OSError:
            pass


def _cpu(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for _ in range(10_000):
            pass


def _memory(mib: int, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    block = bytearray(mib * MIB)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(remaining, 1.0))
    del block


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 2 and args[0] == "cpu":
            _lower_priority()
            _cpu(float(args[1]))
            return 0
        if len(args) == 3 and args[0] == "memory":
            _lower_priority(oom_score_adj=1000)
            _memory(int(args[1]), float(args[2]))
            return 0
    except ValueError:
        pass
    print("usage: python -m xenova_agent.stress cpu SECONDS | memory MIB SECONDS", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
