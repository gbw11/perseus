"""S의 단일 M 계산 감시. heartbeat와 원 frame 작업의 실제 진전을 분리한다.

기존 PROGRESS payload를 사용한다. request_id는 원 job UUID, source_sequence는 원 RGB,
MAPPER_STARTED.reason은 원 만료 decimal이다. 다른 reason은 실제 phase/terminal 사유다.
이 값은 지도·관측·NN 준비·주행 권한을 발급하지 않는다.
"""

from dataclasses import dataclass, replace
from uuid import UUID


STAGES=frozenset(('MAPPER_STARTED','MAPPER_PROGRESS','MAPPER_COMPLETED'))
MAX_SOURCE_JOB_US=500_000


def _uint(value):
    if type(value) is not int or not 0<=value<2**64:
        raise ValueError('mapper progress requires uint64 time/sequence')
    return value


@dataclass(frozen=True)
class ActiveMapperJob:
    job_id: str
    source_sequence: int
    started_us: int
    expires_us: int
    progressed_us: int


class MapperJobWatch:
    """현재 job 하나만 보유한다. 동일 event의 재전달은 기한/진전을 갱신하지 않는다."""

    def __init__(self):
        self.active=None
        self.last_terminal=None

    def observe(self, job_id, stage, sequence, completed_us, reason, *, now_us):
        if type(job_id) is not str or str(UUID(job_id))!=job_id or not UUID(job_id).int:
            raise ValueError('original canonical compute job UUID required')
        if stage not in STAGES:
            raise ValueError('unsupported mapper job stage')
        _uint(sequence);_uint(completed_us);_uint(now_us)
        if completed_us>now_us:
            raise ValueError('mapper phase cannot complete in the future')
        if stage=='MAPPER_STARTED':
            if (type(reason) is not str or not 1<=len(reason)<=20 or not reason.isascii()
                    or not reason.isdecimal() or str(int(reason))!=reason):
                raise ValueError('mapper start must retain original expiry decimal')
            expiry=_uint(int(reason))
            if not completed_us<expiry<=completed_us+MAX_SOURCE_JOB_US:
                raise ValueError('mapper start renewed or exceeded original source budget')
            original=ActiveMapperJob(job_id,sequence,completed_us,expiry,completed_us)
            if self.active is not None:
                if (self.active.job_id,self.active.source_sequence,self.active.started_us,self.active.expires_us)!=(
                        original.job_id,original.source_sequence,original.started_us,original.expires_us):
                    raise ValueError('active compute job cannot be replaced by progress')
                return False
            if self.last_terminal is not None and self.last_terminal[0]==job_id:
                return False
            self.active=original
            return True
        current=self.active
        if current is None:
            terminal=(job_id,sequence,completed_us,reason)
            if stage=='MAPPER_COMPLETED' and terminal==self.last_terminal:
                return False
            raise ValueError('mapper phase has no original active job')
        if (current.job_id,current.source_sequence)!=(job_id,sequence) or completed_us<current.progressed_us:
            raise ValueError('mapper phase differs from original job/source/order')
        if type(reason) is not str or not 1<=len(reason)<=64 or not reason.isascii():
            raise ValueError('bounded actual phase or terminal reason required')
        if stage=='MAPPER_PROGRESS':
            if completed_us>=current.expires_us:
                raise ValueError('expired computation cannot claim new progress')
            self.active=replace(current,progressed_us=completed_us)
        else:
            if reason=='COMPLETED' and completed_us>=current.expires_us:
                raise ValueError('late geometry cannot become a current success')
            self.last_terminal=(job_id,sequence,completed_us,reason)
            self.active=None
        return True

    def stalled(self, now_us):
        """원 기한을 넘겼고 실제 진전도500ms 없으면 고장이다. heartbeat는 입력이 아니다."""
        _uint(now_us)
        current=self.active
        return bool(current is not None and now_us>=current.expires_us
                    and now_us-current.progressed_us>=MAX_SOURCE_JOB_US)
