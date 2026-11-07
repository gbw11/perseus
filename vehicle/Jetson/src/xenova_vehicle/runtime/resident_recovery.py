"""S의 선택 회복 coordinator. 실제 reap·PAUSED·내구 ACK 뒤만 다음 owner를 만든다.

장치/DB/파일 I/O는 worker와 S-log 소유다. 이 FSM은 M-only에서 기존 A/arena를,
A/M 교체에서 D와 fixed6를 유지한다. 겹친 장애·불명 토큰·늦은 완료는 service
실패로 닫으며 회복이 Pi 이동이나 metric/world 권한을 재개하지 않는다.
"""

from collections import deque
from dataclasses import dataclass, field
import hashlib
import uuid

from .raw_leases import ARENA_BYTES, Endpoint, RawOwnerExitBarrier, RawOwnerExitEvidence, RawRecoveryEvidence
from .raw_recovery import RawControlMessage, control_payload
from .resident_storage import StorageRecovery
from .resident_transport import j_us


U64_MAX = 2**64 - 1
CLEANUP_US = 25_000_000
STARTUP_US = 90_000_000
CONTROL_US = 2_000_000
HEALTHY_US = 300_000_000
BACKOFF_US = (1_000_000, 2_000_000, 4_000_000, 8_000_000, 16_000_000, 30_000_000)


class RecoveryError(RuntimeError):
    """현재 선택 회복을 계속할 실제 근거가 없다."""


def _id():
    return str(uuid.uuid4())


@dataclass
class RecoveryAttempt:
    kind: str
    roles: tuple
    old: dict
    phase: str
    began_us: int
    reason: str
    operation_id: str = field(default_factory=_id)
    startup_deadline_us: int | None = None
    backoff_until_us: int | None = None
    pending: tuple | None = None
    pause_draft: dict | None = None
    pause: object = None
    paused: object = None
    pause_sent_us: int | None = None
    resume_draft: dict | None = None
    resume: object = None
    resume_sent_us: int | None = None
    barrier: object = None
    evidence: object = None
    raw_before: str | None = None
    binding_before: str | None = None
    token_refs: tuple = ()
    old_owner_records: dict = field(default_factory=dict)


class ResidentRecovery:
    """runtime의 단일 S-main에서 호출한다. step/healthy는 대기·join·native I/O를 하지 않는다."""

    def __init__(self, runtime):
        self.r = runtime
        self.active = None
        self.receipts = deque(maxlen=5)
        self._launches = deque((runtime.began,), maxlen=5)
        self._retired_births = {}
        self._healthy_since = None
        self._health_pending = None

    @property
    def operation_cap(self):
        if self.r.stopping:
            return U64_MAX
        attempt = self.active
        if attempt is None:
            return U64_MAX if self.r.resident_started_us is not None else self.r.startup_deadline
        if attempt.phase in ('DRAIN', 'PAUSE'):
            return attempt.began_us + CLEANUP_US
        if attempt.startup_deadline_us is not None:
            return attempt.startup_deadline_us
        return U64_MAX

    def suppressed(self, role, host):
        return self.active is not None and self.active.old.get(role) is host

    def _terminal(self, reason):
        if self.active is not None and self.active.phase != 'FAILED':
            self.active.phase = 'FAILED'
            self._receipt(False, reason)
        self._healthy_since = None
        self.r.event('RECOVERY_FAILED', reason=str(reason)[:320])
        self.r.stop('recovery_failed', RecoveryError(str(reason)))

    def fail(self, role, reason, retryable=True):
        """같은 old host의 후속 EOF는 무효과이며 새 host/다른 role 장애는 합치지 않는다."""
        if self.r.stopping:
            return False
        if retryable is not True or role not in ('A', 'M', 'D'):
            self._terminal('non-retryable worker failure: ' + str(reason))
            return False
        host = self.r.workers.get(role)
        if self.active is not None:
            if host is not None and self.active.old.get(role) is host:
                return False
            self._terminal('independent or replacement worker failed during recovery: ' + role)
            return False
        kind = 'AM' if role == 'A' else role
        roles = ('A', 'M') if kind == 'AM' else (kind,)
        old = {name: self.r.workers.get(name) for name in roles}
        if (self.r.manifest is None or self.r.handles is None or any(value is None for value in old.values())
                or kind in ('M', 'AM') and self.r.broker is None):
            self._terminal('selected recovery lacks original worker/raw inventory')
            return False
        if kind == 'M':
            producer = self.r.workers.get('A')
            if producer is None or producer.eof or producer.reaped or producer.bind is None:
                self._terminal('M-only recovery has no healthy original A owner')
                return False
        if kind == 'D':
            try:
                self.r.storage_recovery = StorageRecovery(self.r.storage_profile, old['D'].bootstrap)
            except Exception as error:
                self._terminal('D recovery has no admitted EMPTY database identity: ' + str(error))
                return False
        now = j_us()
        attempt = RecoveryAttempt(kind, roles, old, 'DRAIN', now, str(reason)[:320],
            raw_before=self.r.scope.arena_id if self.r.scope is not None else None,
            binding_before=self.r.binding.sha256 if self.r.binding is not None else None,
            token_refs=tuple(token for pair in self.r.handles.raw_tokens for token in pair)
                       + tuple(self.r.handles.db_tokens))
        self.active = attempt
        self._healthy_since = None
        self.r.event('RECOVERY_BEGIN', recovery_kind=kind, roles=list(roles), reason=attempt.reason)
        if kind == 'M':
            self.r.broker.begin_pause()
            self.r.nn_ready_us = self.r.parser_deadline = self.r.last_fresh_parser_us = None
        elif kind == 'AM':
            self.r.broker.fence(now)
            self.r.capture_ready_us = self.r.nn_ready_us = self.r.parser_deadline = None
            self.r.last_capture_us = self.r.last_fresh_parser_us = None
            self.r.a_phase = None
            self.r.a_phase_deadlines.clear()
        else:
            self.r.storage_ready_us = None
        for key in tuple(self.r.progress):
            if key[0] in roles:
                self.r.progress.pop(key)
        return True

    def sent(self, host, sent):
        attempt = self.active
        if attempt is None or attempt.kind != 'M' or host is not self.r.workers.get('A'):
            return False
        if attempt.pause is not None and sent.packet.raw_bytes == attempt.pause.raw:
            if not attempt.pause.issued_us <= sent.started_us < attempt.pause.expires_us:
                raise RecoveryError('actual PAUSE send began outside its original lifetime')
            if attempt.pause_sent_us is None:
                attempt.pause_sent_us = sent.started_us
            elif attempt.pause_sent_us != sent.started_us:
                raise RecoveryError('original PAUSE was transmitted more than once')
            return True
        if attempt.resume is not None and sent.packet.raw_bytes == attempt.resume.raw:
            if not attempt.resume.issued_us <= sent.started_us < attempt.resume.expires_us:
                raise RecoveryError('actual RESUME send began outside its original lifetime')
            if attempt.resume_sent_us is None:
                attempt.resume_sent_us = sent.started_us
            return True
        return False

    def receive_control(self, role, packet):
        attempt = self.active
        if packet.kind != 'RAW_CONTROL':
            return False
        if (attempt is None or attempt.kind != 'M' or role != 'A'
                or attempt.pause is None or attempt.pause_sent_us is None):
            raise RecoveryError('PAUSED has no current selected and actually sent PAUSE')
        attempt.paused = self.r.broker.accept_paused(packet, attempt.pause,
            first_sent_us=attempt.pause_sent_us, now_us=j_us())
        self.r.event('RECOVERY_A_PAUSED', pause_request_id=attempt.pause.request_id,
                     completed_j_us=str(attempt.paused.issued_us))
        return True

    def _request(self, operation, purpose, **kwargs):
        if self.r.log_pending is not None:
            return False
        if self.r._request(operation, purpose, cap_us=self.operation_cap, **kwargs):
            self.active.pending = (purpose, self.r.log_pending[0])
            return True
        return False

    def _write(self, fields, purpose):
        if self.r.log_pending is not None:
            return False
        if self.r._write(fields, purpose, cap_us=self.operation_cap):
            self.active.pending = (purpose, self.r.log_pending[0])
            return True
        return False

    def _pause(self, attempt, now):
        if attempt.paused is not None:
            return
        if attempt.pause_draft is None and self.r.broker.pause_ready:
            request_id = _id()
            attempt.pause_draft = dict(request_id=request_id, issued_us=now,
                expires_us=min(now + CONTROL_US, attempt.began_us + CLEANUP_US))
        if attempt.pause_draft is None:
            return
        if now >= attempt.pause_draft['expires_us']:
            raise RecoveryError('original producer PAUSE deadline elapsed')
        if attempt.pause is None:
            bind = self.r.broker.writer.bind
            packet = self.r.workers['A'].link.send('RAW_CONTROL',
                control_payload('PAUSE', bind, attempt.pause_draft['request_id']),
                domain='RAW', revision=bind.binding_revision, **attempt.pause_draft)
            if packet is not None:
                attempt.pause = RawControlMessage(packet, bind)

    def _detach_old(self, attempt):
        for role, host in attempt.old.items():
            if self.r.workers.get(role) is not host or not host.reaped or not host.eof:
                raise RecoveryError('old owner changed before actual reap and EOF')
            try:
                self.r.selector.unregister(host.link.endpoint)
            except (KeyError, OSError, ValueError):
                pass
            host.link.close()
            self.r.workers.pop(role)
        for key in tuple(self.r.progress):
            if key[0] in attempt.roles:
                self.r.progress.pop(key)

    def _counter_fields(self, attempt, now):
        while self._launches and now - self._launches[0] >= HEALTHY_US:
            self._launches.popleft()
        fields = self.r.manifest.fields
        episode = fields['recovery_episode']
        if len(self._launches) >= 5 or episode['service_attempts'] >= 5:
            raise RecoveryError('global rolling or episode attempt cap reached')
        if any(episode['role_attempts'][role] >= 5 for role in attempt.roles):
            raise RecoveryError('role episode attempt cap reached')
        episode['service_attempts'] += 1
        for role in attempt.roles:
            episode['role_attempts'][role] += 1
            current = next((o for o in fields['owners'] if o['role'] == role and o['phase'] == 'CURRENT'), None)
            if current is None or current['pid'] is None or current['pid'] != attempt.old[role].pid:
                raise RecoveryError('actual old owner birth was not durably recorded')
            retired = next((o for o in fields['owners'] if o['role'] == role and o['phase'] == 'RETIRED'), None)
            if retired is not None:
                if self._retired_births.get(role) != (retired['pid'], retired['start_ticks']):
                    raise RecoveryError('previous retired owner has no local actual reap record')
                fields['owners'].remove(retired)
            moved = dict(current, phase='RETIRED')
            fields['owners'].append(moved)
            attempt.old_owner_records[role] = moved
            current['pid'] = current['start_ticks'] = None
        return fields

    def _resume(self, attempt, now):
        if attempt.resume_sent_us is not None:
            attempt.phase = 'WAIT_READY'
            return
        mapper = self.r.workers.get('M')
        if mapper is None or mapper is attempt.old['M'] or mapper.bind_ack is None:
            return
        if attempt.resume_draft is None:
            attempt.resume_draft = dict(request_id=_id(), issued_us=now,
                expires_us=min(now + CONTROL_US, attempt.startup_deadline_us))
        if now >= attempt.resume_draft['expires_us']:
            raise RecoveryError('original producer RESUME deadline elapsed')
        if attempt.resume is None:
            bind = self.r.broker.writer.bind
            packet = self.r.workers['A'].link.send('RAW_CONTROL',
                control_payload('RESUME', bind, attempt.paused.pause_request_id),
                domain='RAW', revision=bind.binding_revision, **attempt.resume_draft)
            if packet is not None:
                attempt.resume = RawControlMessage(packet, bind, attempt.paused)
                self.r.broker.resume()
                attempt.phase = 'WAIT_RESUME'
        if attempt.resume_sent_us is not None:
            attempt.phase = 'WAIT_READY'

    def completed(self, purpose, completion):
        if not purpose.startswith('RECOVERY_'):
            return False
        if purpose == 'RECOVERY_HEALTHY':
            if self._health_pending != completion.request_id:
                raise RecoveryError('healthy reset has no original request')
            self._health_pending = None
            self._healthy_since = None
            self.r.event('RECOVERY_EPISODE_RESET', episode_id=self.r.manifest.recovery_episode.episode_id)
            return True
        attempt = self.active
        if attempt is None or attempt.pending != (purpose, completion.request_id):
            raise RecoveryError('recovery completion differs from original stage request')
        attempt.pending = None
        if purpose == 'RECOVERY_COUNTER':
            self._launches.append(j_us())
            for role in attempt.roles:
                self.r.identities[role] = (_id(), _id())
                moved = attempt.old_owner_records[role]
                self._retired_births[role] = moved['pid'], moved['start_ticks']
            if attempt.kind == 'D':
                self.r.storage_profile = self.r.storage_recovery.profile
                self.r._spawn('D', deadline_us=attempt.startup_deadline_us)
                attempt.phase = 'WAIT_READY'
            else:
                attempt.phase = 'NORMALIZE'
        elif purpose == 'RECOVERY_NORMALIZE':
            evidence = completion.value
            expected = RawRecoveryEvidence if attempt.kind == 'M' else RawOwnerExitEvidence
            if (type(evidence) is not expected or evidence.barrier != attempt.barrier
                    or evidence.operation_id != completion.request_id):
                raise RecoveryError('normalization evidence differs from original barrier/operation')
            attempt.evidence = evidence
            if attempt.kind == 'M':
                worker, channel = self.r.identities['M']
                self.r.broker.rebind_reader(Endpoint(channel, worker), evidence, now_us=j_us())
                self.r._spawn('M', deadline_us=attempt.startup_deadline_us)
                attempt.phase = 'WAIT_BIND'
            else:
                fields = self.r.manifest.fields
                fields['resources']['RAW_RETIRED'] = fields['resources']['RAW_CURRENT']
                fields['resources']['RAW_CURRENT'] = dict(state='NOT_ATTEMPTED', name=None,
                    object_id=None, size_bytes=None)
                if not self._write(fields, 'RECOVERY_RETIRED'):
                    raise RecoveryError('retired declaration was not selected')
                attempt.phase = 'RETIRING'
        elif purpose == 'RECOVERY_RETIRED':
            if not self._request('RETIRE_RAW', 'RECOVERY_RETIRE', intent_ack=self.r.latest_ack):
                raise RecoveryError('actual retired cleanup was not selected')
        elif purpose == 'RECOVERY_RETIRE':
            record = completion.value.resource
            if (completion.value.operation_id != completion.request_id
                    or record != self.r.manifest.resource('RAW_RETIRED')):
                raise RecoveryError('retired cleanup differs from original durable raw record')
            fields = self.r.manifest.fields
            fields['resources']['RAW_RETIRED'] = dict(state='NOT_ATTEMPTED', name=None,
                object_id=None, size_bytes=None)
            fields['resources']['RAW_CURRENT'] = dict(state='INTENT', name=None,
                object_id=None, size_bytes=None)
            physical_id = _id()
            fields['intents'] = [dict(intent_id=_id(), slot='RAW_CURRENT', operation='CREATE',
                planned_name='/xnv_raw_' + physical_id, planned_object_id=physical_id,
                planned_size_bytes=str(ARENA_BYTES))]
            if not self._write(fields, 'RECOVERY_RAW_INTENT'):
                raise RecoveryError('new raw INTENT was not selected')
        elif purpose == 'RECOVERY_RAW_INTENT':
            if not self._request('CREATE', 'RECOVERY_RAW_CREATE', intent_ack=self.r.latest_ack):
                raise RecoveryError('new raw constructor was not selected')
        elif purpose == 'RECOVERY_RAW_CREATE':
            record = completion.value
            fields = self.r.manifest.fields
            fields['resources']['RAW_CURRENT'] = dict(state=record.state, name=record.name,
                object_id=record.object_id, size_bytes=str(record.size_bytes))
            fields['intents'] = []
            self.r._owners(fields, completion.owner_births)
            if not self._write(fields, 'RECOVERY_RAW_KNOWN'):
                raise RecoveryError('new raw KNOWN declaration was not selected')
        elif purpose == 'RECOVERY_RAW_KNOWN':
            if not self._request('HANDLES', 'RECOVERY_HANDLES', intent_ack=self.r.latest_ack):
                raise RecoveryError('new raw spawn handles were not selected')
        elif purpose == 'RECOVERY_HANDLES':
            self.r.handles = completion.value
            tokens = tuple(token for pair in self.r.handles.raw_tokens for token in pair) + tuple(self.r.handles.db_tokens)
            if len(tokens) != len(attempt.token_refs) or any(a is not b for a, b in zip(tokens, attempt.token_refs)):
                raise RecoveryError('fixed6 publication objects changed during same-S recovery')
            self.r._configure_binding()
            self.r.broker = None
            self.r._spawn('A', deadline_us=attempt.startup_deadline_us)
            attempt.phase = 'WAIT_READY'
        else:
            raise RecoveryError('unsupported recovery completion purpose')
        return True

    def step(self):
        attempt = self.active
        if attempt is None or self.r.stopping:
            return
        now = j_us()
        try:
            if attempt.phase in ('DRAIN', 'PAUSE'):
                if now >= attempt.began_us + CLEANUP_US:
                    raise RecoveryError('original selective cleanup budget elapsed')
                for host in attempt.old.values():
                    self.r._stop_one(host, attempt.began_us, 'RECOVERY_' + attempt.kind)
                if attempt.kind == 'M':
                    self._pause(attempt, now)
                if (any(not host.reaped or not host.eof for host in attempt.old.values())
                        or self.r.birth_pending or self.r.log_pending is not None
                        or attempt.kind == 'M' and attempt.paused is None):
                    return
                if any(host.exited_us is None for host in attempt.old.values()):
                    raise RecoveryError('actual reaped owner has no original exit observation')
                attempt.barrier = (self.r.broker.recovery_barrier(attempt.old['M'].exited_us)
                    if attempt.kind == 'M' else RawOwnerExitBarrier(self.r.scope,
                    attempt.old['A'].exited_us, attempt.old['M'].exited_us) if attempt.kind == 'AM' else None)
                self._detach_old(attempt)
                count = self.r.manifest.recovery_episode.service_attempts
                attempt.backoff_until_us = now + BACKOFF_US[min(max(count - 1, 0), len(BACKOFF_US) - 1)]
                attempt.phase = 'BACKOFF'
            elif attempt.phase == 'BACKOFF':
                if now < attempt.backoff_until_us or self.r.log_pending is not None:
                    return
                attempt.startup_deadline_us = now + STARTUP_US
                fields = self._counter_fields(attempt, now)
                if self._write(fields, 'RECOVERY_COUNTER'):
                    attempt.phase = 'COUNTER'
            elif attempt.phase == 'NORMALIZE':
                if self._request('NORMALIZE_RAW', 'RECOVERY_NORMALIZE',
                                 intent_ack=self.r.latest_ack, recovery_barrier=attempt.barrier):
                    attempt.phase = 'NORMALIZING'
            elif attempt.phase in ('WAIT_BIND', 'WAIT_RESUME'):
                self._resume(attempt, now)
            elif attempt.phase == 'WAIT_READY':
                required = ('D',) if attempt.kind == 'D' else ('M',) if attempt.kind == 'M' else ('A', 'M')
                hosts = [self.r.workers.get(role) for role in required]
                if any(host is None or host is attempt.old.get(role) or host.bind_ack is None
                       or host.reaped or host.eof for role, host in zip(required, hosts)):
                    return
                ready = (self.r.storage_ready_us is not None if attempt.kind == 'D' else
                         self.r.nn_ready_us is not None and (attempt.kind == 'M' or self.r.capture_ready_us is not None))
                if ready:
                    self._receipt(True)
                    self.r.event('RECOVERY_COMPLETE', recovery_kind=attempt.kind, metric_ready=False, world_valid=False)
                    self.active = None
                    self._healthy_since = None
            if (self.active is not None and attempt.startup_deadline_us is not None
                    and now >= attempt.startup_deadline_us):
                raise RecoveryError('new owner attempt missed its original startup budget')
        except Exception as error:
            self._terminal(str(error))

    def _receipt(self, complete, reason=None):
        attempt = self.active
        current = self.r.handles
        refs = (() if current is None else tuple(t for pair in current.raw_tokens for t in pair) + tuple(current.db_tokens))
        self.receipts.append(dict(operation_id=attempt.operation_id, kind=attempt.kind,
            roles=list(attempt.roles), complete=complete, reason=reason or attempt.reason,
            began_j_us=str(attempt.began_us), completed_j_us=str(j_us()),
            startup_deadline_j_us=None if attempt.startup_deadline_us is None else str(attempt.startup_deadline_us),
            old={role: dict(pid=host.pid, worker_instance=host.peer.worker_instance,
                channel_epoch=host.peer.channel_epoch, reaped=host.reaped, eof=host.eof,
                exited_j_us=None if host.exited_us is None else str(host.exited_us), exitcode=host.exitcode,
                term_sent_j_us=getattr(host, 'term_sent_us', None), kill_sent_j_us=getattr(host, 'kill_sent_us', None))
                for role, host in attempt.old.items()},
            raw_before=attempt.raw_before, raw_after=None if current is None else current.raw.arena_id,
            binding_before=attempt.binding_before,
            binding_after=None if self.r.binding is None else self.r.binding.sha256,
            new={role: None if self.r.workers.get(role) is None else dict(
                worker_instance=self.r.workers[role].peer.worker_instance,
                channel_epoch=self.r.workers[role].peer.channel_epoch, pid=self.r.workers[role].pid)
                for role in attempt.roles},
            raw_preserved=current is not None and current.raw.arena_id == attempt.raw_before,
            fixed6_preserved=len(refs) == len(attempt.token_refs) and all(a is b for a, b in zip(refs, attempt.token_refs)),
            pause_request_id=None if attempt.pause is None else attempt.pause.request_id,
            pause_ack_sha256=None if attempt.paused is None else hashlib.sha256(attempt.paused.raw).hexdigest(),
            normalization_j_us=None if attempt.evidence is None else str(attempt.evidence.normalized_us),
            metric_ready=False, world_valid=False))

    def healthy(self):
        now = j_us()
        r = self.r
        hosts = [r.workers.get(role) for role in ('A', 'M', 'D')]
        ready = (self.active is None and not r.stopping and r.resident_started_us is not None
            and r.capture_ready_us is not None and r.nn_ready_us is not None and r.storage_ready_us is not None
            and r.last_capture_us is not None and 0 <= now - r.last_capture_us < 500_000
            and r.last_fresh_parser_us is not None and 0 <= now - r.last_fresh_parser_us < 5_000_000
            and all(host is not None and not host.reaped and not host.eof and host.bind_ack is not None
                and host.last_heartbeat_us is not None and 0 <= now - host.last_heartbeat_us < 5_000_000
                for host in hosts))
        if not ready:
            self._healthy_since = None
            return False
        if self._healthy_since is None:
            self._healthy_since = now
        if (now - self._healthy_since < HEALTHY_US or self._health_pending is not None
                or r.log_pending is not None or r.manifest.recovery_episode.service_attempts == 0):
            return False
        fields = r.manifest.fields
        fields['recovery_episode'] = dict(episode_id=_id(), service_attempts=0,
                                          role_attempts=dict(A=0, M=0, D=0))
        if r._write(fields, 'RECOVERY_HEALTHY', cap_us=now + 5_000_000):
            self._health_pending = r.log_pending[0]
            return True
        return False
