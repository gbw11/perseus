"""Gateway actor의 실제 Scene owner·원 journal 완료·전달 연결.

공개 operator API나 feedback3 생산자는 아니다. 내부 요청은 전역 operator 예약을 마친
SceneRequest와 실제 ingress의 SceneGate를 받는다. worker 하나가 기존 AuthorityStore의
원 journal만 저장하며, actor는 완료 전에도 입력·원 기한·STOP을 처리한다.
"""

from dataclasses import dataclass
import json
import queue
import threading

from xenova_drive_world import decode, schema
from .scene_authority import (SceneAuthority, SceneAuthorityError, SceneGate, ScenePolicy,
    SceneRequest, SceneJournalAck, SceneJournalProposal, execute_scene_journal)


@dataclass(frozen=True, slots=True)
class SceneSubmission:
    """이미 예약한 원 요청. ID는 begin에서만 필요하며 이 포트가 새 ID를 발급하지 않는다."""
    request: SceneRequest
    gate: SceneGate | None
    admission_id: str | None = None
    survey_id: str | None = None

    def __post_init__(self):
        if type(self.request) is not SceneRequest or self.gate is not None and type(self.gate) is not SceneGate:
            raise SceneAuthorityError('original typed scene request/gate required')
        if self.request.action == 'scene.survey.begin':
            from .scene_authority import _uuid
            _uuid(self.admission_id); _uuid(self.survey_id)
        elif self.admission_id is not None or self.survey_id is not None:
            raise SceneAuthorityError('only begin carries original scene IDs')


@dataclass(frozen=True, slots=True)
class _JournalResult:
    proposal: SceneJournalProposal
    ack: SceneJournalAck | None
    unknown: bool
    error: str | None


class SceneJournalWorker:
    """동일 Authority의 worker1/job1/result1. fsync 동안 actor가 lock을 기다리지 않는다.

    busy는 actor의 submit 전에 설정하고 execute가 원 lock을 반납한 뒤에만 내린다.
    실제 durable 진입은 runner/machine의 busy guard가 지연하며 데이터/STOP 경로는 계속 돈다.
    """
    def __init__(self, store, clock_us):
        self.store, self.clock_us = store, clock_us
        self._jobs = queue.Queue(maxsize=1); self._results = queue.Queue(maxsize=1)
        self._busy = threading.Event(); self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='gateway-scene-journal', daemon=False)
        self.thread.start()

    @property
    def busy(self): return self._busy.is_set()

    def submit(self, proposal):
        if (type(proposal) is not SceneJournalProposal or self._stop.is_set()
                or self.busy or not self._results.empty()):
            raise SceneAuthorityError('one live original scene journal')
        self._busy.set()
        try: self._jobs.put_nowait(proposal)
        except BaseException:
            self._busy.clear(); raise

    def _run(self):
        while not self._stop.is_set() or not self._jobs.empty():
            try: proposal = self._jobs.get(timeout=.05)
            except queue.Empty: continue
            try:
                ack = execute_scene_journal(self.store, proposal, clock_us=self.clock_us)
                result = _JournalResult(proposal, ack, False, None)
            except BaseException as error:
                from .authority import AuthorityError
                current = self.store.value.get('current_scene')
                written = current is not None and json.dumps(current, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=True).encode('ascii') == proposal.record
                unknown = bool(self.store.error or written or not isinstance(error, AuthorityError))
                result = _JournalResult(proposal, None, unknown, type(error).__name__)
            try: self._results.put_nowait(result)
            finally:
                # execute's with-store-lock has unwound before this publication.
                self._busy.clear()

    def take(self):
        try: return self._results.get_nowait()
        except queue.Empty: return None

    def close(self):
        self._stop.set(); self.thread.join(1.)
        if self.thread.is_alive():
            raise RuntimeError('SCENE_JOURNAL_OWNER_TERMINATION_UNCONFIRMED')


class GatewaySceneRuntime:
    """actor-only Scene lifecycle. 새 clock reply·원 gate·원 journal 결과만 소비한다."""
    def __init__(self, runner, policy):
        if type(policy) is not ScenePolicy:
            raise SceneAuthorityError('explicit installed scene policy required')
        self.runner, self.policy = runner, policy
        self.owner = None; self.lease = None; self._gate = None
        self._inputs = queue.Queue(maxsize=4); self._gate_lock = threading.Lock(); self._next_gate = None
        self._submitted = self._unknown = None; self._sent_mapper = self._sent_vm = None
        self._mapper_channel = None; self._repeat_after_us = 0
        self._last_fence = None; self.closed = False
        self._semantic_dispatch = False; self._advanced_negative = None
        self.rejected = self.failed = self.journal_unknown = self.fenced = 0
        self.worker = SceneJournalWorker(runner.authority, lambda: int(runner._clock()*1_000_000))

    @property
    def thread(self): return self.worker.thread

    @property
    def durable_available(self): return not self.worker.busy

    def submit(self, submission):
        """실제 operator 예약 owner용 유한 내부 포트. 수락은 내구 성공이나 이동 허가가 아니다."""
        if type(submission) is not SceneSubmission or self.closed:
            raise SceneAuthorityError('typed live scene submission required')
        from concurrent.futures import Future
        accepted = Future()
        self._inputs.put_nowait((submission, accepted))
        return accepted

    def update_gate(self, gate):
        """feedback3/operator owner의 최신 원 gate. 시각/STOP 표본을 새로 만들지 않는다."""
        if type(gate) is not SceneGate or self.closed:
            raise SceneAuthorityError('actual current scene gate required')
        with self._gate_lock: self._next_gate = gate

    def _current_gate(self):
        from .machine import Mode
        m = self.runner.machine; gate = self._gate
        if m is None or gate is None or m.failure or self.runner._output_broken: return None
        mode = 'MANUAL' if m.mode is Mode.MANUAL else 'HOLD' if m.mode is Mode.SAFE_STOP else None
        if (mode != gate.mode or gate.generation != m.generation
                or bool(m._countdown) != gate.countdown_active): return None
        device = self.runner.device_view()
        if (not device.get('feedback_supported') or device.get('pi_session') != gate.pi_session
                or device.get('device_session') != gate.device_session): return None
        return gate

    def _clock_current(self, now):
        uplink = self.runner.mapper_uplink
        return bool(uplink is not None and self.lease is not None and uplink.owner.trust(now) is not None
            and self.lease.source_revision == uplink.owner.source_revision
            and now < self.lease.expires_pi_us)

    def accept_reply(self, raw, lease, now):
        """GatewayMapperUplink가 실제 plan/nonce/P4를 검산한 바로 그 reply/lease다."""
        if self.closed: return
        from .scene_authority import _json
        if self.owner is None:
            record = self.runner.authority.value.get('current_scene')
            self.owner = SceneAuthority(lease.source, self.policy,
                pi_host_boot=self.runner.authority.host, gateway_instance=self.runner.drive_link.instance,
                fence_stop=self._fence, journal_record=None if record is None else _json(record))
        else: self.owner.source_changed(lease.source)
        self.lease = lease
        try: self.owner.accept_reply(raw, lease, self._current_gate(), now_us=now)
        except SceneAuthorityError:
            self.rejected += 1

    def _fence(self, reason):
        """Scene 의존 AUTO의 RAM fence/STOP. MANUAL/일반 live-only 모드는 바꾸지 않는다."""
        self.fenced += 1; self._last_fence = reason; self._sent_vm = None
        negative = None if self.owner is None else self.owner.negative_bytes
        if (negative is not None and negative != self._advanced_negative
                and not self._semantic_dispatch and not self.closed):
            from hashlib import sha256
            # 이 event는 과거 권한의 원 revision을 보존한다. 다음 입장만 새 의미를 쓴다.
            try: self.runner.mapper_uplink.owner.advance_scene_context(sha256(b'scene-fence\0'+negative).digest())
            except ValueError: self.rejected += 1  # counter exhaustion cannot delay RAM STOP
            self._advanced_negative = negative
        provider = self.runner.drive_link
        if provider is not None: provider.clear_scene()
        m = self.runner.machine
        if not self.closed and m is not None and self._mission_uses_scene():
            from .machine import ForwardStop
            self.runner._deferred_drive = None
            self.runner._submit(ForwardStop(m.generation), wait_for_stop=False)

    def _mission_uses_scene(self):
        m = self.runner.machine; provider = self.runner.drive_link
        if m is None or m.active_app != 'autonomous' or provider is None: return False
        raw = provider.missions.current_grant(m.active_vm)
        return bool(raw is not None and decode(raw,schema.MissionGrant).scene_admission_id != bytes(16))

    def motion_grant(self, now):
        owner = self.owner
        if self.closed or owner is None or not owner.eligible or not self._clock_current(now) or owner.grant_bytes is None:
            return None
        grant = decode(owner.grant_bytes, schema.SceneGrant)
        if grant.lifecycle_state != 2 or not grant.issued_pi_us <= now < grant.valid_until_pi_us:
            return None
        return grant

    def mission_allowed(self, mission, now):
        """발급자의 원 mission을 변경하지 않고 현재 Scene pair 사용권을 대조한다."""
        if mission.scene_admission_id == bytes(16): return True
        grant = self.motion_grant(now)
        return bool(grant is not None and mission.scene_admission_id == grant.scene.admission_id
            and mission.scene_revision == grant.scene.admission_revision)

    def command_allowed(self, source, now):
        if source.get('app_id') != 'autonomous': return True
        provider = self.runner.drive_link
        raw = None if provider is None else provider.missions.current_grant(source['vm_id'])
        if raw is None:return True
        original = decode(raw,schema.MissionGrant)
        if original.scene_admission_id == bytes(16):return True
        mission = provider._current_mission(source['vm_id'])
        return mission is not None and self.mission_allowed(mission, now)

    def _dispatch(self, submission, now):
        request = submission.request
        if self.owner is None:
            raise SceneAuthorityError('actual mapper source not ready')
        if not self.owner._reserve(request):return
        if not request.issued_us<=now<request.expires_us:
            raise SceneAuthorityError('original scene request deadline expired')
        if request.action != 'scene.revoke' and not self._clock_current(now):
            raise SceneAuthorityError('actual mapper clock/source not ready')
        if submission.gate is not None: self._gate = submission.gate
        gate = self._current_gate(); revision = self.runner.mapper_uplink.owner.source_revision
        if request.action in ('scene.survey.begin','scene.restore','scene.revoke'):
            from hashlib import sha256
            from uuid import UUID
            from .scene_authority import _json
            if request.action!='scene.revoke':self.owner._gate(gate,request,now)
            if self.owner.take_journal() is not None and request.action!='scene.revoke':
                raise SceneAuthorityError('one unresolved original scene journal')
            if request.action=='scene.survey.begin' and self.owner.scene_commit_hold:
                raise SceneAuthorityError('original scene commit unresolved')
            if (request.action=='scene.survey.begin' and self.owner._grant is not None
                    and UUID(submission.admission_id).bytes==decode(self.owner._grant,schema.SceneGrant).scene.admission_id):
                raise SceneAuthorityError('new original scene ID required')
            if request.action=='scene.revoke' and self.owner._deferred is not None:
                raise SceneAuthorityError('one original deferred revoke')
            if request.action!='scene.survey.begin':
                if self.owner._grant is None:raise SceneAuthorityError('current original scene required')
                current=decode(self.owner._grant,schema.SceneGrant)
                if request.action=='scene.revoke':
                    if UUID(json.loads(request.args_bytes)['scene_id']).bytes!=current.scene.admission_id:
                        raise SceneAuthorityError('revoke target changed')
                elif (self.owner._restart or self.owner.blocked_reason!='SOURCE_CHANGED'
                        or not current.scene.allow_restore_after_mapper_restart
                        or not current.HasField('frozen_evidence_sha256') or now>=current.valid_until_pi_us
                        or any(getattr(current,name)!=self.owner._source[name] for name in
                            ('vehicle','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))):
                    raise SceneAuthorityError('explicit current controlled restore required')
            semantic=dict(source=self.lease.source,request=request.fingerprint,
                admission_id=submission.admission_id,survey_id=submission.survey_id,action=request.action)
            revision=self.runner.mapper_uplink.owner.advance_scene_context(sha256(_json(semantic)).digest())
        self._semantic_dispatch = request.action in ('scene.survey.begin','scene.restore','scene.revoke')
        try:
            if request.action == 'scene.survey.begin':
                self.owner.begin(request, gate, admission_id=submission.admission_id,
                    survey_id=submission.survey_id, source_admission_revision=revision, now_us=now)
            elif request.action == 'scene.survey.commit': self.owner.commit(request, gate, now_us=now)
            elif request.action == 'scene.restore': self.owner.restore(request, gate, source_admission_revision=revision, now_us=now)
            else: self.owner.revoke(request, source_admission_revision=revision, now_us=now,
                cancel_fence=0 if gate is None else gate.cancel_fence)
        finally:
            self._semantic_dispatch = False
            if request.action in ('scene.survey.begin','scene.revoke'):
                self._advanced_negative = self.owner.negative_bytes

    def tick(self):
        if self.closed: return
        now = int(self.runner._clock()*1_000_000)
        with self._gate_lock:
            if self._next_gate is not None: self._gate, self._next_gate = self._next_gate, None
        outcome = self.worker.take()
        if outcome is not None:
            if outcome.proposal is not self._submitted:
                raise RuntimeError('SCENE_JOURNAL_ORIGINAL_RESULT_MISMATCH')
            self._submitted = None
            if outcome.ack is None:
                self.owner.journal_failed(outcome.proposal, unknown=outcome.unknown)
                if outcome.unknown: self._unknown = outcome.proposal
                self.failed += 1; self.journal_unknown += int(outcome.unknown)
            else: self.owner.complete_journal(outcome.ack, self._current_gate(), now_us=now)
        try: incoming = self._inputs.get_nowait()
        except queue.Empty: incoming = None
        if incoming is not None:
            submission, accepted = incoming
            try:
                self._dispatch(submission, now)
                accepted.set_result(self.owner.state)
            except (SceneAuthorityError, ValueError) as error:
                self.rejected += 1; accepted.set_exception(error)
        owner = self.owner
        if owner is None: return
        owner.tick(now_us=now)
        raw = owner.grant_bytes if owner.grant_bytes is not None else owner.negative_bytes
        if raw is not None:
            grant = decode(raw, schema.SceneGrant)
            ready = grant.lifecycle_state in (3,4) or self._clock_current(now)
            if ready and raw != self._sent_mapper:
                try:
                    if owner.grant_bytes is None:self.runner.publish_mapper_scene_after_fence(raw)
                    else:self.runner.publish_mapper_scene_after_commit(raw)
                    self._sent_mapper = raw; self._repeat_after_us = now + 1_000_000
                except ValueError: self.rejected += 1
            uplink = self.runner.mapper_uplink
            if (ready and raw == self._sent_mapper and uplink is not None
                    and (self._mapper_channel != uplink.channel_epoch or now >= self._repeat_after_us)):
                try:
                    uplink.republish_scene()
                    self._mapper_channel = uplink.channel_epoch; self._repeat_after_us = now + 1_000_000
                except ValueError: self.rejected += 1
            # ADMITTED must also have the actual mapper APPLIED before a VM may use it.
            vm_raw = raw if ready and (grant.lifecycle_state != 2 or owner.eligible) else None
            if vm_raw is not None and vm_raw != self._sent_vm:
                if self.runner.drive_link.publish_scene(vm_raw): self._sent_vm = vm_raw
        if self._mission_uses_scene() and self.motion_grant(now) is None and self._last_fence != 'CURRENT_SCENE_UNAVAILABLE':
            self._fence('CURRENT_SCENE_UNAVAILABLE')
        elif self.motion_grant(now) is not None: self._last_fence = None
        proposal = owner.take_journal()
        if proposal is not None and self._submitted is None and self._unknown is None:
            self._submitted = proposal
            self.worker.submit(proposal)

    def close(self):
        if self.closed:
            if self.thread.is_alive(): raise RuntimeError('SCENE_JOURNAL_OWNER_TERMINATION_UNCONFIRMED')
            return
        self.closed = True
        if self.owner is not None: self.owner._ram_fence('SHUTDOWN')
        while True:
            try: _, accepted = self._inputs.get_nowait()
            except queue.Empty: break
            accepted.set_exception(SceneAuthorityError('scene runtime closed before acceptance'))
        self.worker.close()
