"""E4의 작은 관측 근거와 연속 안정 확인. 실행·저장·출력 권한은 소유하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


def host_boot_id() -> str:
    value = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError('invalid recovery budget host boot identity')
    return value


@dataclass(frozen=True)
class StabilitySample:
    """owner가 확인한 현재 문맥·원 수신 시각. valid_until은 기존 신호/건강 lease의 교집합이다."""

    context: tuple
    observed_at: float
    valid_until: float
    progress: tuple[float, ...]


@dataclass
class StableRun:
    since: float
    last: StabilitySample

    @classmethod
    def start(cls, sample: StabilitySample) -> StableRun:
        return cls(sample.observed_at, sample)

    def advance(self, sample: StabilitySample, required_s: float) -> bool:
        old = self.last
        if (sample.context != old.context or sample.observed_at <= old.observed_at
                or sample.observed_at > old.valid_until or len(sample.progress) != len(old.progress)
                or any(new < previous for new, previous in zip(sample.progress, old.progress))):
            self.since = sample.observed_at
        self.last = sample
        # 저장된 같은 heartbeat의 재조회로는 진행하지 않는다. 여러 앱은 모두의 원 수신이 진행한 범위만 인정한다.
        return min(sample.progress) - self.since >= required_s
