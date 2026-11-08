"""한 M-compute owner의 명시 dirty batch와 catalog 원 요청을 일정에 올린다.

기본 Processor를 활성화하지 않는다. caller가 유한 interval을 주고 같은 compute에서
next_catalog_arguments → 실제 mapping.maintenance → step 순서로 호출한다. step이
원 terminal/delivery를 소비하므로 별도 소비자와 경쟁시키지 않는다. DB/스레드/native
자원·내구 승인·지도/scene/주행 권한은 만들지 않으며 실패를 새 job으로 자동 갱신하지 않는다.
"""

from dataclasses import dataclass
from uuid import uuid4

from .map_checkpoint import MapCheckpointNotReady
from .mapper_catalogs import CatalogNotReady
from .mapper_mapping import MapperMapping, MappingNotReady
from .mapper_storage import StorageAbort
from ..storage.contracts import CommitReceipt, LoadRequest, MAX_U63, StorageJob, uint
from ..storage.geometry_scope import same_catalog_geometry


@dataclass(frozen=True)
class StorageScheduleTerminal:
    """실제 owner가 전달한 원 결과다. current는 이동/scene 승인과 별개다."""

    request: object
    result: object
    current: bool
    observed_us: int
    reason: str


class MapperStorageSchedule:
    """interval_us는 신규 local 일정 선택이며 1..5초다. 2초를 암묵 기본값으로 넣지 않는다.

    checkpoint_arguments는 CP가 실제 due일 때만 호출하는 compute-local callback이다.
    원 scene/subsystem_state를 제공할 수 있고 반환 dict의 허용 key는 그 둘뿐이다.
    자료 미준비 revision의 큰 prepare는 새 accepted revision 전까지 다시 호출하지 않는다.
    """

    def __init__(self, mapping, *, interval_us, checkpoint_arguments=None):
        if type(mapping) is not MapperMapping:
            raise ValueError('actual single-owner MapperMapping required')
        uint(interval_us, 5_000_000, positive=True)
        if checkpoint_arguments is not None and not callable(checkpoint_arguments):
            raise ValueError('optional compute-local checkpoint arguments callback required')
        mapping._owner()
        self.mapping = mapping
        self.interval_us = interval_us
        self._arguments = checkpoint_arguments
        self._status = 'IDLE'
        self._last_terminal = None
        self._fault = None
        self._saved_revision = 0
        self._prepare_revision = None
        self._dirty_since = self._last_commit_us = None
        self._checkpoint = None
        self._checkpoint_revision = None
        self._head_request = None
        self._range_head = self._range_nonce = None
        self._provenance_job = self._provenance_nonce = None
        self._need = None
        self._last_now = None
        self._context = mapping._context

    @property
    def status(self): return self._status

    @property
    def last_terminal(self): return self._last_terminal

    def _now(self, now_us):
        self.mapping._owner(); uint(now_us)
        if self._last_now is not None and now_us < self._last_now:
            raise ValueError('compute schedule clock must not move backwards')
        self._last_now = now_us

    def _set(self, status):
        self._status = status
        return status

    def _fail(self, reason):
        if self._fault is None: self._fault = reason
        return self._set(self._fault)

    def _job(self, now_us):
        lifetime = self.mapping._job_lifetime
        if now_us > MAX_U63 - lifetime:
            raise ValueError('original storage job time overflows u63')
        return StorageJob(self.mapping._context, str(uuid4()), now_us, now_us + lifetime)

    def _idle(self):
        owner = self.mapping
        return (owner.ready and owner._staged is None and owner.pending is None
            and owner.mailbox.main_peek() is None and owner.catalogs is not None
            and owner.catalogs.pending is None and owner.catalogs._queued is None
            and owner.catalogs._transition is None)

    def _head(self, now_us):
        catalog = self.mapping.catalogs
        value = None if catalog is None else catalog._head
        if value is None: return None
        head, job = value
        return value if head.current(job, self.mapping._context.sha256, now_us) else None

    def _job_pending(self, job):
        owner = self.mapping
        entries = [owner.pending, owner.mailbox.main_peek()]
        if owner.catalogs is not None: entries.extend((owner.catalogs.pending, owner.catalogs._queued))
        return any(entry is not None and entry.request.job is job for entry in entries)

    def _sync_context(self):
        """실제 owner 전환 뒤에만 아직 사용하지 않은 선택을 버린다. 원 pending/실패는 유지한다."""
        context = self.mapping._context
        if context is self._context: return
        if self._context is None:
            self._context = context
            return
        if context is None or not same_catalog_geometry(self._context, context, 'SLOT'): return
        self._context = context
        if (self._range_head is not None and self._range_head[1].context != context
                and not self._job_pending(self._range_head[1])):
            self._range_head = self._range_nonce = None
        if (self._provenance_job is not None and self._provenance_job.context != context
                and not self._job_pending(self._provenance_job)):
            self._provenance_job = self._provenance_nonce = None
        if (self._head_request is not None and self._head_request.job.context != context
                and self.mapping._head_load is not self._head_request and not self._job_pending(self._head_request.job)):
            self._head_request = None
        # 새 full context에서 실제 준비가 달라질 수 있다. dirty/실제 COMMIT 분모는 그대로다.
        self._prepare_revision = None

    def _source_retirement(self, request, result, current, reason):
        if current: return False
        replaced = (type(result) is StorageAbort and result.reason == 'SOURCE_REPLACED'
            or result is None and reason == 'SOURCE_REPLACED')
        if not replaced: return False
        context = self.mapping.context_port.read()
        return (context is not None and request.job.context != context
            and same_catalog_geometry(request.job.context, context, 'SLOT'))

    def _forget_request(self, request):
        if request is self._checkpoint: self._checkpoint = self._checkpoint_revision = None
        if request is self._head_request: self._head_request = None
        if request.job is (None if self._range_head is None else self._range_head[1]):
            self._range_head = self._range_nonce = None
        if request.job is self._provenance_job: self._provenance_job = self._provenance_nonce = None

    def _terminal(self, request, result, current, now_us, reason):
        self._last_terminal = StorageScheduleTerminal(request, result, current, now_us, reason)
        if self._source_retirement(request, result, current, reason):
            # 정상 철회는 옛 성공이 아니다. saved/commit/dirty 시각이나 sticky fault를 바꾸지 않는다.
            self._forget_request(request)
            self._prepare_revision = None
            return None
        successful = current and type(result) is not StorageAbort
        if type(result) is CommitReceipt:
            successful = successful and result.status in ('COMMITTED', 'DUPLICATE')
        if not successful:
            return self._fail('ORIGINAL_STORAGE_TERMINAL')
        if request is self._checkpoint:
            # 실제 current COMMITTED/DUPLICATE 이외의 사건으로 dirty 분모를 줄이지 않는다.
            if type(result) is not CommitReceipt:
                return self._fail('CHECKPOINT_RESULT_NOT_COMMITTED')
            self._saved_revision = self._checkpoint_revision
            self._last_commit_us = now_us
            self._dirty_since = None
            self._checkpoint = self._checkpoint_revision = None
        if request is self._head_request: self._head_request = None
        catalog = self.mapping.catalogs
        if catalog is not None and request.job is (None if self._range_head is None else self._range_head[1]):
            self._range_head = self._range_nonce = None
        if request.job is self._provenance_job:
            self._provenance_job = self._provenance_nonce = None
        return None

    def _consume(self, now_us):
        terminal = self.mapping.take_terminal()
        if terminal is not None:
            self._terminal(terminal.request, terminal.result, terminal.current, terminal.observed_us,
                'MAPPING_TERMINAL')
        catalog = self.mapping.catalogs
        if catalog is None: return
        terminal = catalog.take_terminal()
        if terminal is not None:
            self._terminal(terminal.request, None, False, terminal.observed_us, terminal.reason)
        delivery = catalog.take_delivery()
        if delivery is not None:
            result = delivery.admitted_result
            # UNKNOWN은 실제 pending을 남긴 채 일정만 막는다. 새 nonce/기한을 만들지 않는다.
            from .storage_messages import DbMessage
            unknown = (type(result) is DbMessage and result.op == 'RANGE_RESULT'
                and result.body['result'] == 'UNKNOWN')
            self._terminal(delivery.request, result, delivery.current and not unknown,
                now_us, delivery.reason)

    def next_catalog_arguments(self, *, now_us):
        """현재 실제 head/job당 nonce 하나, parking NEEDS당 job/nonce 하나를 그대로 돌려준다."""
        self._now(now_us)
        if self.mapping.context_port.read() is not self.mapping._context: return {}
        self._sync_context()
        if self._fault is not None or not self._idle(): return {}
        if self._need == 'NEEDS_CURRENT_HEAD':
            head = self._head(now_us)
            if head is None: return {}
            if self._range_head is None:
                self._range_head, self._range_nonce = head, str(uuid4())
            elif self._range_head != head:
                self._fail('ORIGINAL_CATALOG_HEAD_CHANGED'); return {}
            return dict(reservation_nonce=self._range_nonce)
        if self._need == 'NEEDS_EXCLUSIVE_PARKING_PROVENANCE':
            if self._provenance_job is None:
                self._provenance_job, self._provenance_nonce = self._job(now_us), str(uuid4())
            if not self._provenance_job.current(self.mapping._context.sha256, now_us):
                self._fail('ORIGINAL_PARKING_PROVENANCE_EXPIRED'); return {}
            return dict(provenance_job=self._provenance_job, provenance_nonce=self._provenance_nonce)
        return {}

    def step(self, maintenance_status, *, now_us):
        """실제 maintenance 뒤 선택한다. queue는 원 request 고정이며 offer/내구 완료가 아니다."""
        self._now(now_us)
        if type(maintenance_status) is not str or not 1 <= len(maintenance_status) <= 64:
            raise ValueError('actual bounded maintenance status required')
        self._sync_context()
        self._consume(now_us)
        self._need = maintenance_status
        if self._fault is not None: return self._set(self._fault)
        owner = self.mapping
        if not self._idle(): return self._set('WAIT_ORIGINAL_STORAGE')
        if self._range_head is not None and self._head(now_us) is None:
            return self._fail('ORIGINAL_CATALOG_HEAD_EXPIRED')
        if maintenance_status == 'NEEDS_CURRENT_HEAD' and self._head(now_us) is None:
            if owner._checkpoint_ref is not None:
                if self._head_request is None:
                    try: self._head_request = owner.queue_head_load(self._job(now_us))
                    except MappingNotReady: return self._fail('HEAD_LOAD_NOT_READY')
                return self._set('HEAD_LOAD_QUEUED')
            # EMPTY의 최초 CP 뒤 owner가 실제 LOAD를 발급하는 경로를 막지 않는다.
        elif maintenance_status in ('NEEDS_CURRENT_HEAD', 'NEEDS_EXCLUSIVE_PARKING_PROVENANCE'):
            return self._set(maintenance_status)
        elif maintenance_status != 'IDLE':
            return self._set(maintenance_status)
        revision = owner._revision
        if revision <= self._saved_revision: return self._set('IDLE')
        if self._dirty_since is None: self._dirty_since = now_us
        began = self._dirty_since if self._last_commit_us is None else self._last_commit_us
        if now_us - began < self.interval_us: return self._set('DIRTY_BATCH_WAIT')
        if revision == self._prepare_revision: return self._set('CHECKPOINT_REVISION_NOT_READY')
        self._prepare_revision = revision
        arguments = {} if self._arguments is None else self._arguments()
        if type(arguments) is not dict or set(arguments) - {'scene', 'subsystem_state'}:
            raise ValueError('only original scene/subsystem_state checkpoint arguments allowed')
        try:
            request = owner.queue_checkpoint(self._job(now_us), now_us=now_us, **arguments)
        except (MappingNotReady, MapCheckpointNotReady, CatalogNotReady):
            return self._set('CHECKPOINT_REVISION_NOT_READY')
        self._checkpoint, self._checkpoint_revision = request, revision
        return self._set('CHECKPOINT_QUEUED')
