"""MQTT-03 metrics 의 processes[]: CPU 기준 상위 N 개 프로세스(pid, name, cpu_percent, rss_bytes).

- cpu_percent 는 top 과 같은 1 코어 기준이다. 두 표본 사이 (utime+stime 증가 ÷ CLK_TCK) ÷ 경과 초 × 100 이라 멀티스레드
  프로세스는 100 을 넘을 수 있다. VM 전체 사용률(cpu_percent_allocated)과 기준이 다르다
- 첫 표본에는 구간이 없어 빈 목록을 낸다. 표본 사이에 생긴 프로세스는 생긴 뒤의 증가만, 사라진 프로세스는 건너뛴다
- 이름은 /proc/{pid}/stat 의 괄호 안(comm). 괄호·공백이 든 이름도 마지막 ')' 기준으로 자른다
- pid 재사용은 starttime 으로 가려 새 프로세스의 누적값을 옛 프로세스의 증가로 보지 않는다
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from .procfs import Roots, clock_ticks_per_second, page_size

log = logging.getLogger("xenova_agent.processes")


class ProcessSampler:
    def __init__(
        self,
        roots: Roots = Roots(),
        *,
        top_n: int = 10,
        clk_tck: int | None = None,
        page_bytes: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._roots = roots
        self._proc = str(roots.proc)
        self.top_n = top_n
        self._clk_tck = clk_tck or clock_ticks_per_second()
        self._page = page_bytes or page_size()
        self._clock = clock or time.monotonic
        self._previous: dict[int, tuple[int, int]] = {}  # pid → (starttime, utime+stime)
        self._previous_at: float | None = None

    def _read(self, pid: int) -> tuple[str, int, int, int] | None:
        """(name, starttime, cpu_ticks, rss_bytes). 사라졌거나 읽을 수 없으면 None.

        프로세스마다 파일 하나(/proc/{pid}/stat)만 바이트로 읽는다. 2초마다 모든 pid 를 훑으므로 열기·디코딩 횟수가 에이전트 CPU 를
        좌우한다(statm 을 따로 열면 비용이 두 배). rss 는 stat 의 24번째 필드(페이지 수)다.
        """
        try:
            with open(f"{self._proc}/{pid}/stat", "rb") as fh:
                stat = fh.read()
        except OSError:
            return None
        open_paren, close_paren = stat.find(b"("), stat.rfind(b")")
        if open_paren < 0 or close_paren < open_paren:
            return None
        rest = stat[close_paren + 2:].split()  # 3번째 필드(state)부터
        try:
            utime, stime, starttime, rss_pages = int(rest[11]), int(rest[12]), int(rest[19]), int(rest[21])
        except (ValueError, IndexError):
            return None
        name = stat[open_paren + 1:close_paren].decode("utf-8", "replace")
        return name, starttime, utime + stime, max(0, rss_pages) * self._page

    def sample(self) -> list[dict[str, Any]]:
        now = self._clock()
        try:
            pids = [int(name) for name in os.listdir(self._proc) if name.isdigit()]
        except OSError as exc:
            log.warning("process list unreadable: %s", exc)
            return []
        current: dict[int, tuple[int, int]] = {}
        rows = []
        elapsed = None if self._previous_at is None else now - self._previous_at
        for pid in pids:
            info = self._read(pid)
            if info is None:
                continue
            name, starttime, ticks, rss = info
            current[pid] = (starttime, ticks)
            before = self._previous.get(pid)
            if elapsed is None or elapsed <= 0 or before is None or before[0] != starttime:
                continue  # 첫 표본, 새 프로세스, pid 재사용
            delta = max(0, ticks - before[1])
            cpu = 100.0 * delta / self._clk_tck / elapsed
            rows.append({"pid": pid, "name": name, "cpu_percent": round(cpu, 2), "rss_bytes": rss})
        self._previous, self._previous_at = current, now
        rows.sort(key=lambda row: (-row["cpu_percent"], -row["rss_bytes"], row["pid"]))
        return rows[: self.top_n]
