"""compute와 M-main 사이의 고정 한 D 작업/완료 경로다.

actor/arena/semaphore·mutable grid/catalog를 보유하지 않는다. lock 안에서는
불변 참조와 작은 상태만 바꾼다. 완료 수신과 실제 copy 소유권 반환은 별개다.
MANUAL 전환/정상 clock renewal은 source replacement가 아니므로 fence하지 않는다.
"""

from dataclasses import dataclass, replace
from threading import Lock

from ..storage.contracts import (StorageJob, ProvenanceRequest, ProvenanceRange, LoadRequest,
    CommitReceipt, digest, uint)
from ..storage.port import CheckpointRequest
from ..storage.records import LoadedCheckpoint


@dataclass(frozen=True)
class StorageDispatch:
    """M 내부 identity handle이다. wire ID/새 lease·송신 완료·D 승인과 동일하지 않다."""

    request: object
    source_sha256: str
    request_digest: str
    control_payload_bytes: bytes = b''


@dataclass(frozen=True)
class EncodeRequest:
    """실제 S writer lease를 받은 원 dispatch다. compute에 native 객체는 넘기지 않는다."""
    dispatch: StorageDispatch
    lease: object
    issued_us: int
    expires_us: int


@dataclass(frozen=True)
class EncodedCopy:
    request: EncodeRequest
    encoding: object
    error: str | None
    completed_us: int


@dataclass(frozen=True)
class StorageAbort:
    """main의 보수 종료 통지다. COMMITTED·arena FREE·프로세스 종료 증명은 아니다."""

    reason: str
    original_notice: object = None

    def __post_init__(self):
        if self.reason not in ('SOURCE_REPLACED', 'EXPIRED', 'OWNER_EXITED', 'CANCELLED'):
            raise ValueError('bounded abort reason required')


@dataclass(frozen=True)
class StorageCompletion:
    dispatch: StorageDispatch
    result: object
    admitted_result: object
    completed_us: int

    @property
    def request(self):
        return self.dispatch.request


@dataclass(frozen=True)
class StorageDelivery:
    """current는 원 작업의 현재 결과 자격이며 목표/추진/메모리 반환 권한이 아니다."""

    completion: StorageCompletion
    current: bool
    reason: str

    @property
    def request(self):
        return self.completion.request

    @property
    def result(self):
        return self.completion.result

    @property
    def admitted_result(self):
        return self.completion.admitted_result


def _request(value):
    if type(value) not in (CheckpointRequest, LoadRequest, ProvenanceRequest) or type(value.job) is not StorageJob:
        raise ValueError('immutable checkpoint/LOAD/provenance request required')
    return value


def _admit(dispatch, result, completed_us):
    """큰 JSON/hash 검사는 lock 밖에서 한다. 원 결과 참조는 바꾸지 않는다."""
    from .parking_bridge import provenance_wire_grant, WireProvenanceGrant, ParkingNotReady
    request = dispatch.request; job = request.job
    if type(result) is StorageAbort:
        return result
    if type(request) is CheckpointRequest or type(result) is CommitReceipt:
        if type(result) is not CommitReceipt or (result.database_uuid, result.job_id, result.job_digest, result.origin_context_sha256) != (
                job.context.database_uuid, job.job_id, dispatch.request_digest, dispatch.source_sha256):
            raise ValueError('result differs from the original job/digest/source')
        return result
    if type(request) is LoadRequest:
        from .storage_copy import OwnedCopy
        if type(result) is OwnedCopy:
            ready = result.ready
            if (ready.get('database_uuid'), ready.get('job_id'), ready.get('job_digest')) != (
                    job.context.database_uuid, job.job_id, dispatch.request_digest):
                raise ValueError('physical copy differs from original LOAD job/digest')
            return result
        if type(result) is not LoadedCheckpoint or result.request is not request:
            raise ValueError('LOAD completion lost the exact original request')
        return result
    if type(result) is ProvenanceRange:
        if (result.reservation_nonce != request.reservation_nonce or result.original_job.context != job.context
                or result.end_exclusive - result.start_inclusive != request.block_count):
            raise ValueError('range completion differs from original owner/nonce/count')
        return result
    if type(result) is WireProvenanceGrant:
        if result.request is not request or result.received_us > completed_us:
            raise ValueError('wire range lost its original request/arrival reference')
        return result
    from .storage_messages import DbMessage, validate_body
    if type(result) is not DbMessage or result.op != 'RANGE_RESULT':
        raise ValueError('original admitted RANGE_RESULT required')
    body = result.body; validate_body(result.op, body)
    if (body['database_uuid'], body['mapper_worker'], body['reservation_nonce']) != (
            job.context.database_uuid, job.context.mapper_worker, request.reservation_nonce):
        raise ValueError('wire range completion differs from original owner/nonce')
    try:
        return provenance_wire_grant(request, result, now_us=completed_us)
    except (ValueError, ParkingNotReady):
        # 원 body/시각을 그대로 전달한다. 역사적/미확정 결과에 새 budget을 주지 않는다.
        return result


def _current(completion, context_sha256, now_us):
    from .parking_bridge import WireProvenanceGrant
    request = completion.request; job = request.job; result = completion.admitted_result
    if not job.current(context_sha256, now_us) or not job.issued_us <= completion.completed_us <= now_us < job.expires_us:
        return False
    if type(result) is CommitReceipt:
        return type(request) is CheckpointRequest and result.current(job, request.payload, context_sha256, now_us)
    if type(result) is LoadedCheckpoint:
        return result.current(request, context_sha256, now_us)
    if type(result) is ProvenanceRange:
        return result.current(job, context_sha256, now_us)
    if type(result) is WireProvenanceGrant:
        return result.current(request, context_sha256, now_us)
    return False


def _unconfirmed(result):
    if type(result) is CommitReceipt:
        return result.status == 'UNKNOWN'
    from .storage_messages import DbMessage
    return type(result) is DbMessage and result.op == 'RANGE_RESULT' and result.body['result'] == 'UNKNOWN'


class MapperStorageMailbox:
    """compute_offer/take와 main_take/complete/release의 단일 producer 경계다.

    main_release는 caller가 실제 copy FSM/owner cleanup을 끝낸 뒤만 호출한다.
    이 클래스는 token을 건드리지 않으며 UNKNOWN을 읽었다고 작업을 해제하지 않는다.
    """

    def __init__(self, context_sha256):
        digest(context_sha256)
        self._source = context_sha256; self._scope = object(); self._lock = Lock()
        self._dispatch = self._completion = None
        self._encode_request = self._encode_result = None
        self._encoding = self._encode_delivered = False
        self._taken = self._released = self._seen = self._retired = False

    @property
    def context_sha256(self):
        """원 context 참조만 조회한다. 이 값 조회는 storage 사용 승인/진도가 아니다."""
        with self._lock:return self._source

    def compute_offer(self, request, *, now_us):
        _request(request); uint(now_us)
        source = request.job.context.sha256
        if not request.job.current(source, now_us):
            return False
        from .storage_messages import prepare_payload, db_payload
        from ..storage.contracts import canonical
        if type(request) is ProvenanceRequest:
            key = request.digest
            control = db_payload('RESERVE_PROVENANCE', dict(database_uuid=request.job.context.database_uuid,
                reservation_nonce=request.reservation_nonce, mapper_worker=request.job.context.mapper_worker,
                block_count=request.block_count))
        else:
            control = prepare_payload(request); key = control['body']['job_digest']
        candidate = StorageDispatch(request, source, key, canonical(control))
        with self._lock:
            if source != self._source:
                return False
            if self._dispatch is not None:
                return self._dispatch.request is request
            self._dispatch = candidate
            self._taken = self._released = self._seen = self._retired = False
            return True

    def main_peek(self):
        """읽기 전용 원 요청이다. 조회만으로 dispatch/native 소유권을 얻지 않는다."""
        with self._lock:
            return self._dispatch

    def main_take(self, *, current_context_sha256, now_us):
        digest(current_context_sha256); uint(now_us)
        with self._lock:
            dispatch, scope = self._dispatch, self._scope
            if dispatch is None or self._taken:
                return None
        current = dispatch.source_sha256 == current_context_sha256 and dispatch.request.job.current(current_context_sha256, now_us)
        drop = StorageCompletion(dispatch, StorageAbort('EXPIRED' if dispatch.source_sha256 == current_context_sha256 else 'SOURCE_REPLACED'),
            None, now_us) if not current else None
        with self._lock:
            if self._dispatch is not dispatch or self._scope is not scope or self._taken:
                return None
            self._taken = True
            if drop is not None or self._retired or self._source != current_context_sha256:
                self._completion = drop or StorageCompletion(dispatch, StorageAbort('SOURCE_REPLACED'), None, now_us)
                self._released = True
                return None
            return dispatch

    def main_request_encode(self, dispatch, lease, *, issued_us, expires_us):
        """작은 S grant를 compute에 게시한다. 여기서 CP payload/hash를 읽지 않는다."""
        from .storage_copy import CopyLease
        uint(issued_us); uint(expires_us)
        if (type(lease) is not CopyLease or lease.read_token is not None
                or type(dispatch.request) is not CheckpointRequest
                or lease.producer.worker_instance != dispatch.request.job.context.mapper_worker
                or not dispatch.request.job.issued_us <= issued_us < expires_us == dispatch.request.job.expires_us):
            raise ValueError('original checkpoint writer grant/lifetime required')
        request = EncodeRequest(dispatch, lease, issued_us, expires_us)
        with self._lock:
            if self._dispatch is not dispatch or not self._taken or self._retired or self._completion is not None:
                raise ValueError('encode grant no longer belongs to current dispatch')
            if self._encode_request is not None:
                original = self._encode_request
                if (original.dispatch is not dispatch or original.lease != lease
                        or (original.issued_us, original.expires_us) != (issued_us, expires_us)):
                    raise ValueError('one original writer lease cannot be replaced')
                return self._encode_request
            self._encode_request = request
        return request

    def compute_encode_pending(self, *, current_context_sha256, now_us, clock_us=None):
        """compute 회차에서만 호출한다. 큰 encode/hash는 lock 밖이며 원 기한은 바꾸지 않는다."""
        from .storage_copy import encode_checkpoint
        import time
        digest(current_context_sha256); uint(now_us)
        clock_us = (lambda: time.monotonic_ns() // 1000) if clock_us is None else clock_us
        with self._lock:
            request = self._encode_request
            if request is None or self._encoding or self._encode_delivered or self._encode_result is not None:
                return None
            self._encoding = True; retired = self._retired or self._source != current_context_sha256
        encoding = None; error = None
        if retired or not request.dispatch.request.job.current(current_context_sha256, now_us):
            error = 'STALE_OR_EXPIRED'
        else:
            try:
                encoding = encode_checkpoint(request.dispatch.request, request.lease)
            except Exception as failure:
                error = type(failure).__name__[:64]
        completed = clock_us(); uint(completed)
        if completed < now_us:
            raise ValueError('encode completion precedes its original compute start')
        with self._lock:
            if self._encode_request is not request:
                return None
            if self._retired or self._source != current_context_sha256 or completed >= request.expires_us:
                encoding = None; error = 'STALE_OR_EXPIRED'
            result = EncodedCopy(request, encoding, error, completed)
            self._encode_result = result
            return result

    def main_take_encoding(self, dispatch):
        """큰 bytes의 immutable 참조만 받는다. validation/hash는 compute가 끝냈다."""
        with self._lock:
            if self._dispatch is not dispatch or self._encode_result is None or self._encode_delivered:
                return None
            result = self._encode_result; self._encode_result = None; self._encode_delivered = True
            return result

    def main_complete(self, dispatch, result, *, completed_us):
        uint(completed_us)
        with self._lock:
            if self._dispatch is not dispatch or not self._taken:
                return False
        if completed_us < dispatch.request.job.issued_us:
            raise ValueError('completion precedes its original job')
        admitted = _admit(dispatch, result, completed_us)
        unconfirmed = _unconfirmed(result)
        completion = StorageCompletion(dispatch, result, admitted, completed_us)
        with self._lock:
            if self._dispatch is not dispatch:
                return False
            if self._completion is not None:
                if self._completion.result is result and self._completion.completed_us == completed_us:
                    return True
                raise ValueError('one original completion cannot be replaced')
            self._completion = completion
            if type(result) is StorageAbort:
                self._encode_request = self._encode_result = None
            if unconfirmed:
                self._released = False
            return True

    def main_abort(self, dispatch, notice):
        """실제 S의 terminal notice를 원 작업에 결박한다. wire RESULT/current 성공은 만들지 않는다.

        caller는 원 S channel을 먼저 수락한다. QUERY는 원 job/digest의 역사 조회이며
        조회 transport 시각을 원 작업의 새 lease로 사용하지 않는다.
        """
        from .resident_db import DbAbort
        from .storage_messages import prepare_payload
        from ..storage.contracts import canonical
        if type(notice) is not DbAbort:
            raise ValueError('original admitted DB terminal notice required')
        with self._lock:
            if self._dispatch is not dispatch or not self._taken:
                return False
            previous = self._completion
        request = dispatch.request; job = request.job; body = notice.body
        if (notice.mapper.worker_instance != job.context.mapper_worker
                or notice.origin_context_sha256 != dispatch.source_sha256 or notice.observed_us < job.issued_us):
            raise ValueError('terminal notice differs from original job/mapper/source')
        if notice.operation == 'QUERY':
            expected = dict(database_uuid=job.context.database_uuid, job_id=job.job_id, job_digest=dispatch.request_digest)
            if body != expected or notice.issued_us < job.issued_us:
                raise ValueError('historical query differs from original job/digest')
        else:
            if (notice.request_id != job.job_id
                    or (notice.issued_us, notice.expires_us) != (job.issued_us, job.expires_us)):
                raise ValueError('terminal notice renewed the original operation lifetime')
            if type(request) is ProvenanceRequest:
                operation = 'RESERVE_PROVENANCE'
                expected = dict(database_uuid=job.context.database_uuid, reservation_nonce=request.reservation_nonce,
                    mapper_worker=job.context.mapper_worker, block_count=request.block_count)
            else:
                operation = 'CHECKPOINT' if type(request) is CheckpointRequest else 'LOAD'
                from ..storage.contracts import json_bytes
                expected = json_bytes(dispatch.control_payload_bytes, 3500)['body']
            if notice.operation != operation or notice.body_bytes != canonical(expected):
                raise ValueError('terminal notice differs from the exact original request body')
        abort = StorageAbort('CANCELLED', notice)
        if previous is not None and type(previous.result) is StorageAbort:
            if previous.result.original_notice == notice and previous.completed_us == notice.observed_us:
                return True
            raise ValueError('terminal retry changed its original notice')
        if previous is not None and _unconfirmed(previous.result):
            return self.main_resolve(dispatch, abort, completed_us=notice.observed_us)
        return self.main_complete(dispatch, abort, completed_us=notice.observed_us)

    def main_release(self, dispatch):
        """실제 main의 종료/반납 확인 뒤 reference 보유를 푼다. OS 사실을 생성하지 않는다."""
        with self._lock:
            if self._dispatch is not dispatch or not self._taken:
                return False
            completion = self._completion
        if completion is not None and _unconfirmed(completion.result):
            return False
        with self._lock:
            if self._dispatch is not dispatch or self._completion is not completion:
                return False
            self._released = True
            if self._seen:
                self._clear()
            return True

    def main_resolve(self, dispatch, result, *, completed_us):
        """원 UNKNOWN의 실제 QUERY/owner 정리 결과다. 원 job/기한은 바뀌지 않는다."""
        uint(completed_us)
        with self._lock:
            previous = self._completion
            if self._dispatch is not dispatch or previous is None:
                return False
        if not _unconfirmed(previous.result) or _unconfirmed(result):
            raise ValueError('only an unresolved original operation can receive a resolution')
        if completed_us < previous.completed_us:
            raise ValueError('resolution precedes the original uncertainty report')
        admitted = _admit(dispatch, result, completed_us)
        completion = StorageCompletion(dispatch, result, admitted, completed_us)
        with self._lock:
            if self._dispatch is not dispatch or self._completion is not previous:
                return False
            self._completion = completion; self._seen = False; self._released = False
            if type(result) is StorageAbort:
                self._encode_request = self._encode_result = None
            return True

    def compute_take(self, *, current_context_sha256, now_us):
        digest(current_context_sha256); uint(now_us)
        with self._lock:
            completion, scope = self._completion, self._scope
            retired = self._retired or current_context_sha256 != self._source
            if completion is None or self._seen:
                return None
        admitted_completion = completion
        from .storage_copy import OwnedCopy
        if type(completion.admitted_result) is OwnedCopy:
            # 큰 SHA/JSON/closure 검사는 compute만 수행한다. main은 물리 사본/반납만 한다.
            decoded = completion.admitted_result.decode()
            if (type(decoded) is not LoadedCheckpoint or decoded.request != completion.request
                    or completion.dispatch.request_digest != decoded.request.digest):
                raise ValueError('decoded copy differs from the exact original LOAD request')
            decoded = replace(decoded, request=completion.request)
            admitted_completion = replace(completion, admitted_result=decoded)
        eligible = not retired and _current(admitted_completion, current_context_sha256, now_us)
        reason = 'CURRENT' if eligible else 'SOURCE_REPLACED' if retired else 'HISTORICAL_OR_UNCONFIRMED'
        delivery = StorageDelivery(admitted_completion, eligible, reason)
        with self._lock:
            if self._completion is not completion or self._scope is not scope or self._seen:
                return None
            self._seen = True
            if self._released:
                self._clear()
            return delivery

    def fence(self, new_context_sha256, *, now_us):
        """실제 source replacement 전용이다. 같은 source/normal renewal은 no-op이다."""
        digest(new_context_sha256); uint(now_us)
        scope = object()
        with self._lock:
            if new_context_sha256 == self._source:
                return None
            self._source = new_context_sha256; self._scope = scope; self._retired = True
            dispatch = self._dispatch
            if dispatch is not None and not self._taken:
                self._taken = self._released = True
                self._completion = StorageCompletion(dispatch, StorageAbort('SOURCE_REPLACED'), None, now_us)
                return dispatch.request
            return None

    def _clear(self):
        self._dispatch = self._completion = None
        self._encode_request = self._encode_result = None
        self._encoding = self._encode_delivered = False
        self._taken = self._released = self._seen = self._retired = False
