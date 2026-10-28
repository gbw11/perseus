"""MQTT-03 metrics 의 processes[]: CPU 기준 상위 N 개 프로세스(pid, name, cpu_percent, rss_bytes).

- cpu_percent 는 top 과 같은 1 코어 기준이다. 두 표본 사이 (utime+stime 증가 ÷ CLK_TCK) ÷ 경과 초 × 100 이라 멀티스레드
  프로세스는 100 을 넘을 수 있다. VM 전체 사용률(cpu_percent_allocated)과 기준이 다르다
- 첫 표본에는 구간이 없어 빈 목록을 낸다. 표본 사이에 생긴 프로세스는 생긴 뒤의 증가만, 사라진 프로세스는 건너뛴다
- 이름은 /proc/{pid}/stat 의 괄호 안(comm). 괄호·공백이 든 이름도 마지막 ')' 기준으로 자른다
- pid 재사용은 starttime 으로 가려 새 프로세스의 누적값을 옛 프로세스의 증가로 보지 않는다

빈 목록이 무엇을 뜻하는지는 수집 상태(ProcessSample.status)로 가른다. 빈 배열 하나로 첫 표본·/proc 읽기 실패·설정 0개를 섞지 않는다.

- disabled: top_n=0. /proc 을 훑지 않는다
- read_failed: /proc 목록 자체를 읽지 못함. 목록이 비어도 프로세스가 없다는 뜻이 아니다. 원인 종류(예외·errno 이름)를 error 에 남긴다
- partial: 일부 프로세스의 stat 을 읽지 못함(권한·형식). 목록이 그만큼 빠졌을 수 있다. 첫 표본이어도 실패가 있으면 partial 이다
- first_sample: 구간이 없어 사용률을 계산하지 못함(기준만 남긴다). 읽기 실패는 없음
- ok: 발견한 프로세스를 다 읽었다
읽는 사이 끝난 프로세스(stat 이 없어짐)는 정상이라 실패로 세지 않는다(vanished). 목록은 top-N 이라 밖으로 밀린 프로세스가
없어진 것이 아니다(truncated). 이 상태는 아직 MQTT 로 내보내지 않는다(공개 필드는 백엔드와 계약 합의 뒤).
"""

from __future__ import annotations

import errno
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .procfs import Roots, clock_ticks_per_second, page_size

log = logging.getLogger("xenova_agent.processes")

DISABLED, READ_FAILED, FIRST_SAMPLE, PARTIAL, OK = "disabled", "read_failed", "first_sample", "partial", "ok"
_VANISHED, _FAILED = object(), object()  # _read 의 실패 종류


@dataclass(frozen=True)
class ProcessSample:
    status: str
    rows: list[dict[str, Any]] = field(default_factory=list)  # 계약의 processes[] (top-N)
    discovered: int = 0  # /proc 에서 찾은 PID 수
    read_ok: int = 0  # stat 을 읽고 해석한 수
    vanished: int = 0  # 읽는 사이 끝난 프로세스(정상)
    failed: int = 0  # 권한·I/O·형식 때문에 읽지 못한 수
    candidates: int = 0  # 사용률을 계산한 프로세스 수(첫 표본·새 프로세스·pid 재사용 제외)
    error: str | None = None  # read_failed 의 원인 종류. 예외 이름과 errno 이름만(경로·메시지 없음), 예: PermissionError(EACCES)

    @property
    def truncated(self) -> bool:
        """top-N 밖으로 밀린 후보가 있다. 목록에 없다고 끝난 프로세스가 아니다."""
        return self.candidates > len(self.rows)


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
        self.last: ProcessSample | None = None  # 진단용: 마지막 수집 결과

    def _read(self, pid: int) -> tuple[str, int, int, int] | object:
        """(name, starttime, cpu_ticks, rss_bytes). 끝난 프로세스면 _VANISHED, 읽거나 해석하지 못하면 _FAILED.

        프로세스마다 파일 하나(/proc/{pid}/stat)만 바이트로 읽는다. 2초마다 모든 pid 를 훑으므로 열기·디코딩 횟수가 에이전트 CPU 를
        좌우한다(statm 을 따로 열면 비용이 두 배). rss 는 stat 의 24번째 필드(페이지 수)다.
        """
        try:
            with open(f"{self._proc}/{pid}/stat", "rb") as fh:
                stat = fh.read()
        except (FileNotFoundError, ProcessLookupError):  # 목록과 읽기 사이에 끝났다(ESRCH 는 열고 나서 끝난 경우)
            return _VANISHED
        except OSError:
            return _FAILED
        open_paren, close_paren = stat.find(b"("), stat.rfind(b")")
        if open_paren < 0 or close_paren < open_paren:
            return _FAILED
        rest = stat[close_paren + 2:].split()  # 3번째 필드(state)부터
        try:
            utime, stime, starttime, rss_pages = int(rest[11]), int(rest[12]), int(rest[19]), int(rest[21])
        except (ValueError, IndexError):
            return _FAILED
        name = stat[open_paren + 1:close_paren].decode("utf-8", "replace")
        return name, starttime, utime + stime, max(0, rss_pages) * self._page

    def collect(self) -> ProcessSample:
        if self.top_n <= 0:
            return self._record(ProcessSample(DISABLED))
        now = self._clock()
        try:
            pids = [int(name) for name in os.listdir(self._proc) if name.isdigit()]
        except OSError as exc:
            return self._record(ProcessSample(READ_FAILED, error=_error_kind(exc)))
        current: dict[int, tuple[int, int]] = {}
        rows = []
        read_ok = vanished = failed = 0
        elapsed = None if self._previous_at is None else now - self._previous_at
        for pid in pids:
            info = self._read(pid)
            if info is _VANISHED:
                vanished += 1
                continue
            if info is _FAILED:
                failed += 1
                continue
            read_ok += 1
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
        status = PARTIAL if failed else FIRST_SAMPLE if elapsed is None else OK  # 첫 표본이라고 읽기 실패를 가리지 않는다
        return self._record(ProcessSample(status, rows[: self.top_n], len(pids), read_ok, vanished, failed, len(rows)))

    def sample(self) -> list[dict[str, Any]]:
        """계약의 processes[] 만. 수집 상태는 last 에 남는다."""
        return self.collect().rows

    def _record(self, result: ProcessSample) -> ProcessSample:
        """상태가 바뀔 때만 로그를 남긴다(2초마다 같은 경고를 반복하지 않는다)."""
        previous = self.last.status if self.last is not None else None
        if result.status != previous:
            level = logging.WARNING if result.status in (READ_FAILED, PARTIAL) else logging.INFO
            log.log(level, "process collection %s: discovered=%d read_ok=%d vanished=%d failed=%d%s",
                    result.status, result.discovered, result.read_ok, result.vanished, result.failed,
                    f" error={result.error}" if result.error else "")
        self.last = result
        return result


def _error_kind(exc: OSError) -> str:
    """로그·진단에 남길 원인 종류. 경로나 메시지 없이 예외 이름과 errno 이름만."""
    name = errno.errorcode.get(exc.errno) if exc.errno is not None else None
    return f"{type(exc).__name__}({name})" if name else type(exc).__name__
