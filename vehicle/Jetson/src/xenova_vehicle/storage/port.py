"""M/S/D 연결용 한 job/한 완료 slot. I/O·thread·fsync·NativeResources 회수는 없다."""

from dataclasses import dataclass

from .contracts import (CommitReceipt,LoadRequest,PersistenceConflict,PersistenceError,
                        ProvenanceRange,ProvenanceRequest,StorageJob,uint)
from .records import CheckpointPayload,LoadedCheckpoint


@dataclass(frozen=True,slots=True)
class CheckpointRequest:
    job: StorageJob
    payload: CheckpointPayload

    def __post_init__(self):
        if type(self.job) is not StorageJob or type(self.payload) is not CheckpointPayload:raise PersistenceError('original complete checkpoint request required')
        self.payload.validate_job(self.job)

    @property
    def digest(self):return self.job.digest(self.payload)


@dataclass(frozen=True,slots=True)
class StorageCompletion:
    request: object
    result: object
    completed_us: int

    def current(self,context_sha256,now_us):
        job=self.request.job
        if not job.current(context_sha256,now_us) or not job.issued_us<=self.completed_us<=now_us<job.expires_us:return False
        if type(self.request) is CheckpointRequest:
            return self.result.current(job,self.request.payload,context_sha256,now_us)
        if type(self.request) is ProvenanceRequest:return self.result.current(job,context_sha256,now_us)
        return False if type(self.result) is CommitReceipt else self.result.current(self.request,context_sha256,now_us)


class BoundedStoragePort:
    """단일 copy arena의 원 job을 유지한다. source fence가 진행 중 D transaction을 새 작업으로 바꾸지 않는다."""

    def __init__(self):self._request=None;self._taken=False;self._completion=None

    def offer(self,request):
        if type(request) not in (CheckpointRequest,LoadRequest,ProvenanceRequest):raise PersistenceError('exact bounded storage request required')
        if self._request is not None:
            if request.job.job_id==self._request.job.job_id:
                if request.digest!=self._request.digest:raise PersistenceConflict('pending original job digest conflict')
                return True
            return False
        self._request=request;self._taken=False
        return True

    def take(self,*,current_context_sha256,now_us):
        if self._request is None or self._taken or not self._request.job.current(current_context_sha256,now_us):return None
        self._taken=True
        return self._request

    def discard_unstarted(self,*,current_context_sha256,now_us):
        """미시작 stale 원본만 반환한다. caller는 원 lease를 실제 정리하며 여기서 token을 건드리지 않는다."""
        if self._request is None or self._taken or self._request.job.current(current_context_sha256,now_us):return None
        original=self._request;self._request=None
        return original

    def complete(self,request,result,*,completed_us):
        if request is not self._request or not self._taken:raise PersistenceConflict('completion differs from selected original job')
        uint(completed_us)
        if type(request) is CheckpointRequest:
            if type(result) is not CommitReceipt or (result.database_uuid,result.job_id,result.job_digest,result.origin_context_sha256)!=(
                    request.job.context.database_uuid,request.job.job_id,request.digest,request.job.context.sha256):
                raise PersistenceConflict('checkpoint result original identity mismatch')
        elif type(request) is ProvenanceRequest:
            if (type(result) is not ProvenanceRange or result.reservation_nonce!=request.reservation_nonce
                    or result.original_job.context!=request.job.context or result.end_exclusive-result.start_inclusive!=request.block_count):
                raise PersistenceConflict('range result original identity mismatch')
        else:
            if type(result) is CommitReceipt:
                if (result.status not in ('ABSENT','FAILED','UNKNOWN') or
                        (result.database_uuid,result.job_id,result.job_digest,result.origin_context_sha256)!=(
                            request.job.context.database_uuid,request.job.job_id,request.digest,request.job.context.sha256)):
                    raise PersistenceConflict('absent LOAD original identity mismatch')
            elif type(result) is not LoadedCheckpoint or result.request!=request:
                raise PersistenceConflict('load result original identity mismatch')
        completion=StorageCompletion(request,result,completed_us)
        if self._completion is not None:
            if self._completion!=completion:raise PersistenceConflict('completion retry changed its original receipt/time')
            return self._completion
        self._completion=completion
        return completion

    def take_receipt(self):
        if self._completion is None:return None
        result=self._completion
        self._request=self._completion=None;self._taken=False
        return result
