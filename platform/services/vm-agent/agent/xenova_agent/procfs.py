"""/proc·/sys 읽기 공통 부분. psutil 없이 표준 라이브러리만 쓴다(Debian·Ubuntu 게스트 공통).

경로는 루트를 바꿀 수 있게 받아 시험에서는 가짜 트리를 쓴다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# MQTT 계약 v1 3장 metrics 값의 unavailable_reason. 0 으로 숨기지 않고 왜 없는지 적는다
FIRST_SAMPLE = "first_sample"  # 구간 값(사용률)은 두 표본이 있어야 계산된다
COUNTER_RESET = "counter_reset"  # 누적 카운터가 되돌아감(재부팅·리셋). 이번 구간은 계산하지 않고 다음 표본부터 새 기준
NOT_SUPPORTED = "not_supported"  # 커널이 그 항목을 제공하지 않음
READ_FAILED = "read_failed"  # 파일을 읽지 못함(권한·I/O)
PARSE_ERROR = "parse_error"  # 읽었지만 형식이 예상과 다름
DEVICE_NOT_FOUND = "device_not_found"  # 집계할 장치가 없음(디스크 없는 VM 등)


@dataclass(frozen=True)
class Roots:
    proc: Path = Path("/proc")
    sys: Path = Path("/sys")
    etc: Path = Path("/etc")


def clock_ticks_per_second() -> int:
    """/proc/{pid}/stat 의 utime·stime 단위. Linux 기본 100."""
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


def page_size() -> int:
    """/proc/{pid}/statm 의 페이지 크기."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):
        return 4096


def read_text(path: Path) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()
