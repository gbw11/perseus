"""요청 예산 `X-Request-Budget-Ms` (S15P31E110-305).

호출자가 이 요청을 기다릴 남은 시간(ms, 1..105000)을 헤더로 주면, 요청이 도착한 순간(스레드풀 대기 전)부터 로컬 단조 시계로
기한을 정한다. 이 기한 하나를 다음이 나눠 쓴다. 하위에는 남은 값에서 응답을 돌려줄 몫(Settings.response_reserve_s)을 뺀 만큼만
주고, 그만큼도 남지 않으면 실행·제출하지 않고 504 다.

- VM 잠금·자원 잠금 대기(service.py), Manager 직렬화 대기와 하위 Manager 호출(drivers/platform.py)
- xl·xentop 서브프로세스(drivers/xl.py·xentop.py, clamp): 자체 한도와 남은 예산 중 짧은 쪽
- /healthz 는 예산을 쓰지 않는다(자체 xl 한도 5초). 예산 만료를 "Xen 불가"로 잘못 알리지 않기 위해서다

- 받는 값은 '남은 기간'뿐이다. 다른 호스트의 단조 시각이나 절대 시각을 받지 않는다
- 헤더가 없으면 기한이 없고 기존 동작(직렬화 대기 5초, 하위 기본 90초·15초)을 그대로 쓴다
- 하위의 원 작업 기한(정지 90초)은 이 값으로 늘어나지 않는다. 같은 키의 재전달도 원 기한을 다시 받지 않는다
- create·retire 에는 아직 budget_ms 를 넘기지 않고 제출 직전 만료만 검사한다. retire 는 하위가 받지 않는다. create 는 미해결이다
  (S15P31E110-334): 예산이 지나도 원 생성이 계속될 수 있다(drivers/platform.py _BUDGET_ACTIONS 주석)

기한은 미들웨어가 contextvar 에 두고, FastAPI 가 동기 엔드포인트를 스레드풀에서 돌릴 때 context 를 복사하므로 드라이버까지 전달된다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

from .errors import DriverTimeout

HEADER = "X-Request-Budget-Ms"
EXEMPT_PATHS = ("/healthz",)
MAX_MS = 105_000  # 하위 Manager budget_ms 의 입력 상한과 같다. HTTP 를 이만큼 기다리라는 뜻이 아니다


@dataclass(frozen=True)
class Budget:
    deadline: float  # clock 기준 단조 시각(초)
    reserve_s: float = 0.0
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)

    def remaining_s(self) -> float:
        return self.deadline - self.clock()

    def downstream_ms(self) -> int | None:
        """하위 호출에 넘길 ms. 응답 몫을 빼고 1ms 도 남지 않으면 None 이다(제출하지 않는다)."""
        ms = int((self.remaining_s() - self.reserve_s) * 1000)
        return ms if ms >= 1 else None

    def wait_s(self, limit_s: float) -> float:
        """잠금 대기 한도: 원래 한도와 남은 예산 중 짧은 쪽. 음수가 되지 않는다."""
        return max(0.0, min(limit_s, self.remaining_s()))


_current: ContextVar[Budget | None] = ContextVar("dom0_request_budget", default=None)


def current() -> Budget | None:
    return _current.get()


def clamp(timeout_s: float, what: str) -> float:
    """하위 명령(xl·xentop) 한도를 남은 예산으로 줄인다. 응답 몫을 빼고 남는 게 없으면 실행하지 않고 504."""
    budget = current()
    if budget is None:
        return timeout_s
    remaining = budget.remaining_s() - budget.reserve_s
    if remaining <= 0:
        raise DriverTimeout(f"request budget exhausted before {what}; not executed")
    return min(timeout_s, remaining)


def ensure(what: str) -> None:
    """상태를 바꾸기 직전 검사. 응답 몫을 빼고 남는 예산이 없으면 바꾸지 않고 504. 드라이버 종류와 무관하게 서비스가 부른다."""
    budget = current()
    if budget is not None and budget.downstream_ms() is None:
        raise DriverTimeout(f"request budget exhausted before {what}; nothing was changed")


def acquire(lock, what: str) -> None:
    """요청 예산 안에서 잠금을 얻는다. 예산이 없으면 지금처럼 기다린다. 다 쓰면 504."""
    budget = current()
    if budget is None:
        lock.acquire()
        return
    if not lock.acquire(timeout=max(0.0, budget.remaining_s())):
        raise DriverTimeout(f"{what} is busy with another request; request budget exhausted while waiting")


def parse(value: str) -> int:
    """헤더 값 → ms. 부호·소수·공백 없는 십진 정수 1..MAX_MS 만 받는다."""
    if not value.isascii() or not value.isdigit() or (len(value) > 1 and value[0] == "0"):
        raise ValueError(f"{HEADER} must be a decimal integer of milliseconds")
    ms = int(value)
    if not 1 <= ms <= MAX_MS:
        raise ValueError(f"{HEADER} must be 1..{MAX_MS}")
    return ms


def start(ms: int, *, reserve_s: float, clock: Callable[[], float] = time.monotonic):
    """이 context 의 예산을 정한다. 돌려준 token 으로 reset 한다."""
    return _current.set(Budget(clock() + ms / 1000, reserve_s, clock))


def reset(token) -> None:
    _current.reset(token)
