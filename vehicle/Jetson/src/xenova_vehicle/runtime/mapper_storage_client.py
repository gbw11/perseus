"""M-main의 단일 D job/control/물리 copy pump다.

compute mailbox의 immutable 참조만 소비한다. SQL·큰 encode/decode·catalog/grid는
만지지 않으며, 실제 RELEASE 송신과 native 소유 불명을 queue 접수로 대신하지 않는다.
actor의 모든 실제 SentPacket은 note_sent에 전달해야 한다. RAW packet은 소비하지 않는다.
"""

from dataclasses import dataclass
import os
import threading

from .mapper_storage import MapperStorageMailbox, StorageAbort
from .raw_leases import Endpoint
from .resident_transport import SentPacket, j_us
from .storage_copy import CopyArena, CopyLease
from .storage_messages import db_payload, parse_db_job
from ..storage.contracts import (CommitReceipt, LoadRequest, ProvenanceRequest, canonical,
    digest, json_bytes, uint)
from ..storage.port import CheckpointRequest


class MapperStorageClientError(RuntimeError):
    """원 작업/소유권을 유지한 채 상위 worker 회수 판단이 필요한 실패다."""


@dataclass(frozen=True)
class _Draft:
    kind: str
    payload_bytes: bytes
    request_id: str
    issued_us: int
    expires_us: int
    purpose: str


class MapperStorageClient:
    """actor·attach handle은 M-main 한 owner만 보유한다. compute에는 lease 값만 전달한다."""

    def __init__(self, actor, mailbox, copy_handles, *, clock_us=j_us, arena_factory=None):
        if type(mailbox) is not MapperStorageMailbox or actor.boot.peer.role != 'M':
            raise ValueError('registered M actor and exact mailbox required')
        self.actor, self.mailbox, self.handles = actor, mailbox, copy_handles
        self.clock = clock_us; self._arena_factory = CopyArena if arena_factory is None else arena_factory
        self.endpoint = Endpoint(actor.boot.peer.channel_epoch, actor.boot.peer.worker_instance)
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._arena = None; self.dispatch = None; self.state = 'IDLE'; self.failure = None
        self._draft = self._packet = self._actual = None
        self._writer = self._reader = self._encode = self._owned = self._returned = None
        self._release_deadline = None; self._load_ready_bytes = None; self._closed = False
        self.completed = self.copied = self.stale = 0

    def _owner(self):
        if self._closed or (os.getpid(), threading.get_ident()) != (self._pid, self._thread):
            raise ValueError('live M-main owner required')

    def _fail(self, code):
        self.failure = code
        raise MapperStorageClientError(code)

    def _native(self):
        if self._arena is None:
            if type(self.handles) is not tuple or len(self.handles) != 2:
                self._fail('MISSING_COPY_HANDLES')
            self._arena = self._arena_factory(*self.handles, self.endpoint,
                self.actor.boot.peer.service_instance, clock_us=self.clock)
        return self._arena

    def _set_draft(self, kind, payload, issued, purpose):
        if self._draft is not None:
            self._fail('ONE_ORIGINAL_STORAGE_DRAFT')
        job = self.dispatch.request.job
        self._draft = _Draft(kind, canonical(payload), job.job_id, issued, job.expires_us, purpose)

    def _lease(self, lease, *, writer=False):
        peer = self.actor.boot.peer; request = self.dispatch.request
        if (lease.service_instance != peer.service_instance or type(self.handles) is not tuple or len(self.handles) != 2
                or lease.arena_id != self.handles[0].arena_id
                or (lease.producer if writer else lease.consumer) != self.endpoint
                or (lease.read_token is None) != writer):
            raise ValueError('copy full key differs from registered M arena/endpoint')
        if type(request) is LoadRequest and request.purpose == 'BOOTSTRAP_RESTORE':
            if lease.raw_binding_sha256 is not None or lease.publication_binding_sha256 is not None:
                raise ValueError('bootstrap copy is not a current publication lease')
        else:
            from hashlib import sha256
            binding = json_bytes(request.job.context.source_binding, 4096)
            if (lease.raw_binding_sha256 != binding['raw_binding_sha256']
                    or lease.publication_binding_sha256 != sha256(request.job.context.source_binding).hexdigest()):
                raise ValueError('copy lease differs from the original publication/raw binding')

    def note_sent(self, sent):
        """Root는 actor.drain_sent의 각 실제 완료를 호출한다. 다른 packet은 무효과다."""
        if type(sent) is not SentPacket or self._packet is None or sent.packet is not self._packet:
            return False
        self._owner()
        if self._actual is not None:
            if self._actual != sent: raise ValueError('actual send fact changed')
            return False
        if not self._draft.issued_us <= sent.started_us <= sent.completed_us <= self.clock():
            raise ValueError('actual send precedes its original draft or is future')
        if self._draft.purpose != 'RELEASE' and sent.completed_us >= self._draft.expires_us:
            self._fail('ORIGINAL_SEND_EXPIRED')
        if self._draft.purpose == 'RELEASE' and sent.completed_us >= self._release_deadline:
            self._fail('ORIGINAL_CLEANUP_SEND_EXPIRED')
        self._actual = sent
        return True

    def _flush(self):
        if self._draft is None:
            return
        try:
            if self._packet is None:
                d = self._draft
                self._packet = self.actor.link.send(d.kind, json_bytes(d.payload_bytes, 3500), domain='STORAGE',
                    revision=1, request_id=d.request_id, issued_us=d.issued_us, expires_us=d.expires_us)
            self.actor.link.flush(); self.actor.drain_sent()
        except Exception:
            # send가 예외를 내도 transport가 이미 원 packet을 선택했을 수 있다.
            self._fail('TRANSPORT_SEND_OWNERSHIP_UNCONFIRMED')
        # 기존 actor와의 작은 호환 경로다. 정확한 완료를 잃지 않으려면 모든 sent observer가 필요하다.
        recent = self.actor.last_actual_sent
        if recent is not None:
            self.note_sent(recent)
        if self._actual is None:
            return
        purpose = self._draft.purpose
        actual = self._actual
        self._draft = self._packet = self._actual = None
        if purpose == 'INITIAL':
            request = self.dispatch.request
            self.state = 'WAIT_WRITER' if type(request) is CheckpointRequest else 'WAIT_LOAD' if type(request) is LoadRequest else 'WAIT_RESULT'
        elif purpose == 'READY':
            self.state = 'WAIT_RESULT'
        elif purpose == 'RELEASE':
            # physical copy가 끝나도 S에 원 RELEASE가 실제 전송되기 전에는 완료를 넘기지 않는다.
            self.mailbox.main_complete(self.dispatch, self._owned, completed_us=actual.completed_us)
            self.mailbox.main_release(self.dispatch)
            self._owned = self._returned = None; self.state = 'DELIVERED'; self.completed += 1

    def pump(self, *, current_context_sha256, now_us=None):
        """main 회차당 한 작업/한 draft만 전진시킨다. D fsync/compute encode를 기다리지 않는다."""
        self._owner(); digest(current_context_sha256)
        now = self.clock() if now_us is None else now_us; uint(now)
        if self.failure is not None:
            self._fail(self.failure)
        if self.dispatch is not None and self.mailbox.main_peek() is not self.dispatch:
            if self._draft is not None or self._owned is not None:
                self._fail('DISPATCH_DISAPPEARED_WITH_OWNERSHIP')
            self.dispatch = None; self._writer = self._reader = self._encode = None; self.state = 'IDLE'
            self._load_ready_bytes = None
        if self.dispatch is None:
            selected = self.mailbox.main_take(current_context_sha256=current_context_sha256, now_us=now)
            if selected is None:
                return
            self.dispatch = selected
            control = json_bytes(selected.control_payload_bytes, 3500)
            self._set_draft('DB_JOB', control, selected.request.job.issued_us, 'INITIAL'); self.state = 'SEND_INITIAL'
        job = self.dispatch.request.job
        current = job.current(current_context_sha256, now)
        if not current and self.state == 'SEND_INITIAL' and self._packet is None:
            self._draft = None
            reason = 'EXPIRED' if now >= job.expires_us else 'SOURCE_REPLACED'
            self.mailbox.main_complete(self.dispatch, StorageAbort(reason), completed_us=now)
            self.mailbox.main_release(self.dispatch); self.state = 'DELIVERED'; return
        if self.state == 'ENCODING':
            encoded = self.mailbox.main_take_encoding(self.dispatch)
            if encoded is not None:
                if encoded.request is not self._encode or encoded.error is not None or encoded.encoding is None:
                    self._fail('ENCODE_FAILED_OR_EXPIRED')
                if not current or encoded.completed_us >= job.expires_us:
                    self._fail('ENCODE_SOURCE_OR_DEADLINE_RETIRED')
                try:
                    ready = self._native().publish(self._writer, encoded.encoding, expires_us=job.expires_us)
                except Exception:
                    self._fail('COPY_PUBLISH_OWNERSHIP_UNCONFIRMED')
                self.copied += 1
                self._set_draft('DB_JOB', db_payload('READY', ready), self.clock(), 'READY'); self.state = 'SEND_READY'
        if self._draft is not None:
            limit = self._release_deadline if self._draft.purpose == 'RELEASE' else job.expires_us
            if now >= limit:
                self._fail('ORIGINAL_CONTROL_DEADLINE_EXPIRED')
            self._flush()
        elif now >= job.expires_us and self.state not in ('DELIVERED', 'UNKNOWN'):
            # 원 owner/예약은 유지한다. 시각만으로 acquire/post/성공을 만들지 않는다.
            self._fail('ORIGINAL_JOB_EXPIRED_WITH_PENDING_OWNER')

    def receive(self, packet):
        """ResidentLink admission 뒤 호출한다. RAW/다른 domain은 False로 원 caller에 남긴다."""
        self._owner(); fields = packet.fields
        if fields['binding_domain'] != 'STORAGE' or packet.kind not in ('DB_JOB', 'SLOT'):
            return False
        p = self.actor.boot.peer; now = self.clock()
        if (fields['service_instance'], fields['channel_epoch'], fields['sender_instance'], fields['binding_revision']) != (
                p.service_instance, p.channel_epoch, p.service_instance, '1'):
            raise ValueError('storage reply differs from registered S/M channel')
        if self.dispatch is None or fields['request_id'] != self.dispatch.request.job.job_id:
            self.stale += 1; return True
        job = self.dispatch.request.job
        if int(fields['expires_j_us']) != job.expires_us or not job.issued_us <= int(fields['issued_j_us']) <= now:
            raise ValueError('storage reply changed original operation lifetime')
        if packet.kind == 'SLOT':
            payload = packet.payload
            if (payload.get('op') != 'GRANT' or type(self.dispatch.request) is not CheckpointRequest
                    or payload.get('payload_length') is not None or payload.get('descriptor_sha256') is not None):
                raise ValueError('M expects only original storage writer GRANT')
            lease = CopyLease.from_slot(p.service_instance, payload); self._lease(lease, writer=True)
            if self._writer is not None:
                if self._writer != lease: raise ValueError('original writer lease changed')
                return True
            if self.state != 'WAIT_WRITER' or now >= job.expires_us:
                raise ValueError('writer GRANT has no actual timely PREPARE send')
            self._writer = lease
            self._encode = self.mailbox.main_request_encode(self.dispatch, lease,
                issued_us=int(fields['issued_j_us']), expires_us=job.expires_us)
            self.state = 'ENCODING'; return True
        message = parse_db_job(packet, p, database_uuid=job.context.database_uuid, now_us=now, allow_expired=True)
        body = message.body
        if message.op == 'READY':
            if self._reader is not None:
                if message.body_bytes != self._load_ready_bytes:
                    raise ValueError('original LOAD READY bytes changed')
                return True
            if type(self.dispatch.request) is not LoadRequest or self.state != 'WAIT_LOAD' or now >= job.expires_us:
                raise ValueError('LOAD READY has no actual original LOAD send')
            if (body['job_id'], body['job_digest']) != (job.job_id, self.dispatch.request_digest):
                raise ValueError('LOAD READY changed original job/digest')
            lease = CopyLease.from_fields(body['copy_lease']); self._lease(lease)
            self._reader = lease
            self._load_ready_bytes = message.body_bytes
            try:
                self._owned, self._returned = self._native().copy_raw_owned(lease, body, expires_us=job.expires_us)
            except Exception:
                self._fail('COPY_READ_OWNERSHIP_UNCONFIRMED')
            self.copied += 1
            budget = self.actor.boot.cleanup_limits.transport_budget_us
            self._release_deadline = self._returned.posted_us + budget
            self._set_draft('SLOT', lease.slot_payload('RELEASE'), self._returned.posted_us, 'RELEASE')
            self.state = 'SEND_RELEASE'; return True
        if message.op not in ('RESULT', 'RANGE_RESULT') or self.state != 'WAIT_RESULT' and self.state != 'WAIT_LOAD':
            raise ValueError('unexpected original D completion')
        if type(self.dispatch.request) is ProvenanceRequest:
            if message.op != 'RANGE_RESULT': raise ValueError('original range needs RANGE_RESULT')
            result = message; unknown = body['result'] == 'UNKNOWN'
        else:
            if message.op != 'RESULT': raise ValueError('original copy job needs RESULT')
            result = CommitReceipt.from_fields(body); unknown = result.status == 'UNKNOWN'
            if type(self.dispatch.request) is LoadRequest and result.status not in ('ABSENT', 'FAILED', 'UNKNOWN'):
                raise ValueError('a LOAD copy is not a checkpoint COMMITTED receipt')
        self.mailbox.main_complete(self.dispatch, result, completed_us=now)
        self.state = 'UNKNOWN' if unknown else 'DELIVERED'
        if not unknown:
            self.mailbox.main_release(self.dispatch); self.completed += 1
        return True

    def receive_abort(self, notice):
        """실제 S terminal local event만 받는다. 새 wire RESULT나 expiry를 생산하지 않는다."""
        self._owner()
        if notice.mapper!=self.endpoint:raise ValueError('storage terminal belongs to another registered M channel')
        if self.dispatch is None:
            return False
        if self._draft is not None or self._owned is not None or self._arena is not None and self._arena.held:
            self._fail('ABORT_WHILE_LOCAL_SEND_OR_COPY_PENDING')
        accepted = self.mailbox.main_abort(self.dispatch, notice)
        if accepted:
            self.mailbox.main_release(self.dispatch); self.state = 'DELIVERED'; self.failure = None; self.completed += 1
        return accepted

    def adopt_bootstrap_load(self, *, current_context_sha256, now_us=None):
        """S가 이미 실제 PREPARE를 보낸 원 LOAD를 붙인다. 물리 사본만으로 복원 검증을 하지 않는다.

        root는 원 S bootstrap request를 compute mailbox에 먼저 게시한 뒤 호출한다.
        이 경로는 M→S에 같은 PREPARE를 재발행하지 않는다.
        """
        self._owner()
        if self.dispatch is not None:
            raise ValueError('one original storage job required')
        selected = self.mailbox.main_take(current_context_sha256=current_context_sha256,
            now_us=self.clock() if now_us is None else now_us)
        if selected is None:
            return False
        if type(selected.request) is not LoadRequest or selected.request.purpose != 'BOOTSTRAP_RESTORE':
            self.dispatch = selected; self._fail('BOOTSTRAP_LOAD_ORIGINAL_REQUEST_REQUIRED')
        self.dispatch = selected; self.state = 'WAIT_LOAD'
        return True

    def snapshot(self):
        self._owner()
        return dict(state=self.state, job_id=None if self.dispatch is None else self.dispatch.request.job.job_id,
            send_pending=self._draft is not None, native_held=False if self._arena is None else self._arena.held,
            ownership_pinned=self.dispatch is not None and self.state != 'DELIVERED',
            copied=self.copied, completed=self.completed, stale=self.stale, failure=self.failure)

    def close(self):
        self._owner()
        if self._arena is not None:
            self._arena.close()
        self._closed = True
