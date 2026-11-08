"""Pi actor의 controlled scene 사용권과 동일 Authority의 bounded journal 포트.

operator2 전역 key 예약 뒤 호출한다. 별도 daemon/DB/권위 파일은 만들지 않는다.
actor는 take_journal()의 원 제안을 기존 durable 실행 경계로 넘기고 실제 완료만
complete_journal()로 돌린다. execute_scene_journal은 동기 I/O이므로 actor에서 직접
실행하지 않는다. RAM fence/STOP callback은 항상 그 I/O보다 먼저 실행된다.

MatchedStop은 CRC/session 검사를 끝낸 feedback3 payload와 원 Vehicle window를 받는
포트다. 기존 feedback2/PUBACK/serial write를 이 포트의 증거로 변환하지 않는다.
현재 operator2/feedback3 producer의 연결은 호출자 소유이며 값 시험은 실물 증명이 아니다.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import struct
from uuid import UUID

from xenova_drive_world import schema, encode, decode
from xenova_drive_world.mapper_clock import ClockLease


SURVEY_US = 180_000_000
SCENE_US = 14_400_000_000
OPERATOR_US = 10_000_000
U63_MAX = (1 << 63)-1
SOURCE_KEYS = frozenset(('vehicle','device','mapper_boot','mapper_instance','clock_epoch','map_epoch',
    'frame_epoch','odom_epoch','configuration','calibration','primitive'))
COMMAND3 = struct.Struct('<16s16sQ16sQQQQIBBQhhBB')
FEEDBACK3 = struct.Struct('<16s16sQIBBBQQQBiiB16sQQQH')


class SceneAuthorityError(ValueError): pass


def _uint(value, minimum=0, maximum=U63_MAX):
    if type(value) is not int or not minimum <= value <= maximum: raise SceneAuthorityError('scene integer')
    return value


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or not UUID(value).int: raise SceneAuthorityError('scene UUID')
    return value


def _finite(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0: raise SceneAuthorityError('scene bound')
    return value


def _sha(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise SceneAuthorityError('scene SHA256')
    return value


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def _source(value):
    if (type(value) is not dict or set(value)!=SOURCE_KEYS or
            any(type(item) is not str or not 1<=len(item)<=64 or not item.isascii() for item in value.values())):
        raise SceneAuthorityError('registered scene source')
    for name in ('mapper_boot','mapper_instance','clock_epoch','map_epoch','frame_epoch','odom_epoch'): _uuid(value[name])
    _sha(value['calibration']); _sha(value['primitive'])
    return json.loads(_json(value))


@dataclass(frozen=True)
class StationaryPolicy:
    window_us: int
    max_gap_us: int
    max_age_us: int
    metres_per_tick: float
    max_wheel_mps: float
    max_yaw_rps: float
    unobserved_wheel_mps: float
    unobserved_yaw_rps: float

    def __post_init__(self):
        for name in ('window_us','max_gap_us','max_age_us'): _uint(getattr(self,name),1)
        for name in ('metres_per_tick','max_wheel_mps','max_yaw_rps','unobserved_wheel_mps','unobserved_yaw_rps'):
            _finite(getattr(self,name))
        if not self.metres_per_tick: raise SceneAuthorityError('actual wheel quantization required')


@dataclass(frozen=True)
class ScenePolicy:
    coverage_profile_sha256: str
    required_sensor_mask: int
    atlas_cell_capacity: int
    max_anchor_position_m: float
    max_anchor_heading_rad: float
    summary_max_age_us: int
    stationary: StationaryPolicy

    def __post_init__(self):
        _sha(self.coverage_profile_sha256); _uint(self.required_sensor_mask,1,15)
        _uint(self.atlas_cell_capacity,1); _uint(self.summary_max_age_us,1)
        _finite(self.max_anchor_position_m); _finite(self.max_anchor_heading_rad)
        if type(self.stationary) is not StationaryPolicy: raise SceneAuthorityError('explicit stationary policy required')


@dataclass(frozen=True)
class MatchedStop:
    """실제 ingress가 검증한 원 payload들. arrival로 센서 시각을 갱신하지 않는다."""
    command_payload: bytes
    feedback_payload: bytes
    vehicle_window: tuple

    def __post_init__(self):
        if (type(self.command_payload) is not bytes or len(self.command_payload)!=COMMAND3.size
                or type(self.feedback_payload) is not bytes or len(self.feedback_payload)!=FEEDBACK3.size
                or type(self.vehicle_window) is not tuple or not 2<=len(self.vehicle_window)<=256
                or any(type(raw) is not bytes or not 0<len(raw)<=2048 for raw in self.vehicle_window)):
            raise SceneAuthorityError('original feedback3/vehicle window bytes required')

    def check(self, source, host, gateway, generation, pi_session, device_session, policy, now_us):
        command=COMMAND3.unpack(self.command_payload); report=FEEDBACK3.unpack(self.feedback_payload)
        pi,device,sequence,clock,clock_seq,issued,expires,cutoff,gen,counter,kind,stop_seq,v,steer,reason,reserved=command
        (rp,rd,rseq,rgen,rcounter,rkind,status,report_seq,sample,at,flags,velocity,
            steering,stationary,rclock,rclock_seq,rcutoff,rstop_seq,rreason)=report
        if (pi!=UUID(pi_session).bytes or device!=UUID(device_session).bytes or not sequence or gen!=generation
                or kind!=2 or any((clock_seq,issued,expires,cutoff,v,steer,reserved)) or clock!=bytes(16) or reason>10
                or (rp,rd,rseq,rgen,rcounter,rkind)!=(pi,device,sequence,gen,counter,kind)
                or status!=2 or not report_seq or not sample or not at or flags&~7 or not flags&4 or stationary!=1
                or not flags&1 and velocity or not flags&2 and steering
                or (rclock,rclock_seq,rcutoff,rstop_seq)!=(clock,clock_seq,cutoff,stop_seq)):
            raise SceneAuthorityError('matched current STOP APPLIED and supported stationary required')
        values=tuple(decode(raw,schema.VehicleSample) for raw in self.vehicle_window)
        first,last=values[0],values[-1]
        for index,value in enumerate(values):
            if (value.vehicle!=source['vehicle'] or value.host_boot!=host or value.odom_epoch!=source['odom_epoch']
                    or value.calibration!=source['calibration'] or value.quality!=3
                    or not value.HasField('gateway_instance') or value.gateway_instance!=gateway
                    or not value.HasField('pi_session') or value.pi_session!=pi_session or value.device_session!=device_session):
                raise SceneAuthorityError('stationary source/session/clock/calibration mismatch')
            if index:
                old=values[index-1]
                dt=value.pi_sample_us-old.pi_sample_us-value.clock_error_us-old.clock_error_us
                if (value.sequence<=old.sequence or value.source_sample<=old.source_sample or value.source_us<=old.source_us
                        or dt<=0 or value.pi_sample_us+value.clock_error_us-old.pi_sample_us+old.clock_error_us>policy.max_gap_us):
                    raise SceneAuthorityError('stationary window order/gap')
                seconds=dt/1_000_000
                for name in ('left_ticks','right_ticks'):
                    # 양 끝 tick 양자화의 한 tick 폭을 포함한다. 평균 차량 v=0은 사용하지 않는다.
                    speed=(abs(getattr(value,name)-getattr(old,name))+1)*policy.metres_per_tick/seconds
                    if speed+policy.unobserved_wheel_mps>policy.max_wheel_mps:
                        raise SceneAuthorityError('each wheel stationary bound failed')
                yaw=abs(math.remainder(value.odom_pose.yaw-old.odom_pose.yaw,2*math.pi))
                if (yaw+value.heading_error_rad+old.heading_error_rad)/seconds+policy.unobserved_yaw_rps>policy.max_yaw_rps:
                    raise SceneAuthorityError('stationary yaw bound failed')
        if (last.source_sample!=sample or last.source_us!=at
                or last.pi_sample_us+last.clock_error_us>now_us
                or now_us-(last.pi_sample_us-last.clock_error_us)>policy.max_age_us
                or last.pi_sample_us-last.clock_error_us-first.pi_sample_us-first.clock_error_us<policy.window_us):
            raise SceneAuthorityError('fresh matched sensor window required')
        return last


@dataclass(frozen=True)
class SceneGate:
    mode: str
    generation: int
    pi_session: str
    device_session: str
    cancel_fence: int
    stop: MatchedStop | None
    pulse_active: bool = False
    parking_active: bool = False
    countdown_active: bool = False

    def __post_init__(self):
        _uint(self.generation,1,(1<<32)-1); _uint(self.cancel_fence)
        _uuid(self.pi_session); _uuid(self.device_session)
        if (self.mode not in ('MANUAL','HOLD') or self.stop is not None and type(self.stop) is not MatchedStop
                or any(type(value) is not bool for value in (self.pulse_active,self.parking_active,self.countdown_active))):
            raise SceneAuthorityError('explicit current scene gate required')


@dataclass(frozen=True)
class SceneRequest:
    """기존 operator2 전역 원장이 먼저 예약한 key/nonce/원 기한. 새 wire가 아니다."""
    key: str
    nonce: str
    issued_us: int
    expires_us: int
    expected_generation: int
    action: str
    args_bytes: bytes

    def __post_init__(self):
        from .ipc_contract import request_key
        request_key(self.key); _uuid(self.nonce); _uint(self.issued_us); _uint(self.expires_us)
        _uint(self.expected_generation,1,(1<<32)-1)
        if (not self.issued_us<self.expires_us<=self.issued_us+OPERATOR_US or type(self.args_bytes) is not bytes
                or len(self.args_bytes)>512 or self.action not in ('scene.survey.begin','scene.survey.commit','scene.revoke','scene.restore')):
            raise SceneAuthorityError('original bounded scene request required')
        args=json.loads(self.args_bytes)
        if _json(args)!=self.args_bytes: raise SceneAuthorityError('canonical scene args required')
        if self.action=='scene.survey.begin':
            if (type(args) is not dict or set(args)!= {'policy','allow_mapper_restore'} or args['policy']!='STATIC_FLAT_SURVEY_1'
                    or type(args['allow_mapper_restore']) is not bool): raise SceneAuthorityError('scene begin args')
        elif self.action in ('scene.survey.commit','scene.revoke'):
            name='survey_id' if self.action.endswith('commit') else 'scene_id'
            if type(args) is not dict or set(args)!={name}: raise SceneAuthorityError('scene target args')
            _uuid(args[name])
        elif type(args) is not dict or set(args)!= {'checkpoint_sha256'}:
            raise SceneAuthorityError('internal restore args')
        else: _sha(args['checkpoint_sha256'])

    @property
    def fingerprint(self):
        return sha256(_json(dict(key=self.key,nonce=self.nonce,issued=self.issued_us,expires=self.expires_us,
            generation=self.expected_generation,action=self.action,args=self.args_bytes.hex()))).hexdigest()


@dataclass(frozen=True)
class SceneJournalProposal:
    request: SceneRequest
    expected_record: bytes | None
    record: bytes
    actor_fence: int
    cancel_fence: int

    def __post_init__(self):
        if (type(self.request) is not SceneRequest or type(self.record) is not bytes or not 0<len(self.record)<=4096
                or self.expected_record is not None and (type(self.expected_record) is not bytes or len(self.expected_record)>4096)):
            raise SceneAuthorityError('bounded original scene journal proposal')
        _uint(self.actor_fence); _uint(self.cancel_fence)


@dataclass(frozen=True)
class SceneJournalAck:
    proposal: SceneJournalProposal
    completed_us: int

    def __post_init__(self):
        if type(self.proposal) is not SceneJournalProposal: raise SceneAuthorityError('original scene proposal required')
        _uint(self.completed_us)


def execute_scene_journal(store, proposal, *, clock_us):
    """기존 Authority durable owner에서만 실행. 성공 반환은 실제 save/fsync 뒤다.

    예외는 UNKNOWN일 수 있다. actor는 journal_failed(...,unknown=True)로 hold를
    유지하고 원 작업을 조회한다. 새 key나 백업 권위 파일로 우회하지 않는다.
    """
    from .authority import AuthorityStore, AuthorityError
    if type(store) is not AuthorityStore or type(proposal) is not SceneJournalProposal:
        raise SceneAuthorityError('same actual AuthorityStore/original proposal required')
    request=proposal.request
    with store._lock:
        now=_uint(clock_us())
        if (store.error or request.action!='scene.revoke' and store.value['generation']!=request.expected_generation or
                not request.issued_us<=now<request.expires_us): raise AuthorityError('scene journal context/deadline changed')
        current=store.value.get('current_scene'); current_raw=None if current is None else _json(current)
        if current_raw!=proposal.expected_record: raise AuthorityError('scene journal CAS mismatch')
        record=json.loads(proposal.record)
        if record['pi_host_boot']!=store.host: raise AuthorityError('scene host changed')
        store.update(current_scene=record)
        return SceneJournalAck(proposal,_uint(clock_us()))


class SceneAuthority:
    """Pi actor 단일 owner. scene1/job1/grant1/summary1, current_scene<=4KiB.

    fence_stop(reason)은 RAM 사용권 철회와 STOP 선택만 해야 하며 fsync를 기다리지 않는다.
    source_admission_revision은 실제 Pi clock owner가 전달한다. 이 모듈은 별도 source
    counter, AUTO resume, generation, 임무를 발급하지 않는다.
    """

    def __init__(self, source, policy, *, pi_host_boot, gateway_instance, fence_stop, journal_record=None):
        if type(policy) is not ScenePolicy or not callable(fence_stop): raise SceneAuthorityError('scene policy/fence port required')
        self._source=_source(source); self.policy=policy; self.host=_uuid(pi_host_boot); self.gateway=_uuid(gateway_instance)
        self._fence_stop=fence_stop; self._fence=0; self._pending=None; self._deferred=None
        self._grant=None; self._published_grant=None; self._summary=None; self._requests=(); self._durable=journal_record
        self._eligible=False; self._commit_hold=False; self.state='NONE'; self.blocked_reason='NO_SCENE'
        self._start_peer=None; self._start_pi=None; self._base_summary=None; self._restart=journal_record is not None
        if journal_record is not None:
            if type(journal_record) is not bytes or not 0<len(journal_record)<=4096: raise SceneAuthorityError('bounded actual Authority scene record')
            record=json.loads(journal_record)
            if _json(record)!=journal_record or record.get('profile')!='scene-authority-1': raise SceneAuthorityError('scene journal record')
            self._grant=bytes.fromhex(record['grant']); decode(self._grant,schema.SceneGrant)
            self._requests=tuple(record['requests']); self.state='RESTART_BLOCKED'; self.blocked_reason='GATEWAY_RESTART'
            self._commit_hold=record['state']=='COMMITTING'

    @property
    def eligible(self): return self._eligible

    @property
    def scene_commit_hold(self): return self._commit_hold

    @property
    def grant_bytes(self): return self._published_grant

    def _ram_fence(self, reason):
        self._eligible=False; self._published_grant=None; self.blocked_reason=reason; self._fence+=1; self._fence_stop(reason)

    def _gate(self, gate, request, now_us):
        if (type(gate) is not SceneGate or gate.generation!=request.expected_generation or gate.stop is None
                or gate.pulse_active or gate.parking_active or gate.countdown_active
                or not request.issued_us<=_uint(now_us)<request.expires_us):
            raise SceneAuthorityError('current MANUAL/HOLD, no pulse, original request and matched STOP required')
        gate.stop.check(self._source,self.host,self.gateway,gate.generation,gate.pi_session,gate.device_session,
                        self.policy.stationary,now_us)

    def _reserve(self, request):
        if type(request) is not SceneRequest: raise SceneAuthorityError('original operator reservation required')
        for row in self._requests:
            if row['key']==request.key:
                if row['fingerprint']!=request.fingerprint: raise SceneAuthorityError('scene key content conflict')
                return False
        if len(self._requests)>=16: raise SceneAuthorityError('bounded operator scene history capacity')
        return True

    def _record(self, grant, state):
        record=dict(profile='scene-authority-1',pi_host_boot=self.host,gateway_instance=self.gateway,
            source=self._source,grant=grant.hex(),state=state,requests=list(self._requests),
            start_peer_us=self._start_peer,start_pi=self._start_pi,
            summary=None if self._base_summary is None else self._base_summary.hex())
        raw=_json(record)
        if len(raw)>4096: raise SceneAuthorityError('current scene4KiB capacity')
        return raw

    def _queue(self, request, gate, grant, state, *, cancel_fence=None):
        if self._pending is not None: raise SceneAuthorityError('one unresolved scene journal job')
        row=dict(key=request.key,fingerprint=request.fingerprint,nonce=request.nonce,action=request.action)
        if not any(old['key']==request.key for old in self._requests): self._requests+=(row,)
        raw=self._record(grant,state)
        fence=gate.cancel_fence if type(gate) is SceneGate else _uint(cancel_fence)
        self._pending=SceneJournalProposal(request,self._durable,raw,self._fence,fence)
        self._operation=request
        self._grant=grant; self.state=state; return self._pending

    def begin(self, request, gate, *, admission_id, survey_id, source_admission_revision, now_us):
        if not self._reserve(request): return None
        if request.action!='scene.survey.begin' or self._pending is not None or self._commit_hold:
            raise SceneAuthorityError('scene begin unavailable')
        self._gate(gate,request,now_us); _uuid(admission_id); _uuid(survey_id); _uint(source_admission_revision,1)
        args=json.loads(request.args_bytes); p=self.policy; _uint(request.issued_us+SCENE_US)
        grant=schema.SceneGrant(major=1,vehicle=self._source['vehicle'],pi_host_boot=self.host,gateway_instance=self.gateway,
            source_admission_revision=source_admission_revision,mapper_device=self._source['device'],
            map_epoch=self._source['map_epoch'],frame_epoch=self._source['frame_epoch'],odom_epoch=self._source['odom_epoch'],
            configuration=self._source['configuration'],calibration=self._source['calibration'],primitive=self._source['primitive'],
            issued_pi_us=request.issued_us,valid_until_pi_us=request.issued_us+SCENE_US,lifecycle_state=0,
            request_nonce=UUID(request.nonce).bytes,survey_deadline_pi_us=request.issued_us+SURVEY_US,
            max_observation_stations=24,purpose=0,frozen_survey_revision=0,frozen_hazard_revision=0,lifecycle_revision=1)
        grant.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(admission_id).bytes,admission_revision=1,
            survey_id=UUID(survey_id).bytes,survey_revision=1,policy=1,coverage_profile_sha256=bytes.fromhex(p.coverage_profile_sha256),
            allow_restore_after_mapper_restart=args['allow_mapper_restore']))
        raw=encode(grant)
        # 새 scene은 전 scene key를 그 전역 operator 원장에 남긴다. 여기에는 현재 scene만 둔다.
        self._ram_fence('NEW_SURVEY'); self._requests=(); self._summary=self._base_summary=None
        self._start_peer=self._start_pi=None; self._restart=False
        return self._queue(request,gate,raw,'SURVEYING')

    def commit(self, request, gate, *, now_us):
        if not self._reserve(request): return None
        if (request.action!='scene.survey.commit' or self.state!='SURVEYING' or self._grant is None
                or self._pending is not None or self._restart): raise SceneAuthorityError('current applied survey required')
        self._gate(gate,request,now_us); grant=decode(self._grant,schema.SceneGrant)
        if (UUID(json.loads(request.args_bytes)['survey_id']).bytes!=grant.scene.survey_id
                or now_us>=grant.issued_pi_us+SURVEY_US or self._start_peer is None):
            raise SceneAuthorityError('current survey/collection deadline/actual mapper reflection required')
        self._commit_hold=True; self._eligible=False
        grant.lifecycle_state=1; grant.lifecycle_revision+=1; grant.request_nonce=UUID(request.nonce).bytes
        grant.survey_deadline_pi_us=min(request.expires_us,grant.valid_until_pi_us)
        return self._queue(request,gate,encode(grant),'FREEZE_PENDING')

    def take_journal(self): return self._pending

    def complete_journal(self, ack, gate, *, now_us):
        if type(ack) is not SceneJournalAck or self._pending is None or ack.proposal is not self._pending:
            raise SceneAuthorityError('exact original journal completion required')
        proposal=self._pending; self._durable=proposal.record; self._pending=None
        current=(proposal.actor_fence==self._fence
            and proposal.request.issued_us<=ack.completed_us<=now_us<proposal.request.expires_us)
        if proposal.request.action!='scene.revoke':
            current=current and type(gate) is SceneGate and gate.cancel_fence==proposal.cancel_fence
            try: self._gate(gate,proposal.request,now_us)
            except SceneAuthorityError: current=False
        if self._deferred is not None:
            request,cancel,raw=self._deferred; self._deferred=None
            self._queue(request,None,raw,'REVOKED',cancel_fence=cancel); return None
        if not current:
            self._ram_fence('HISTORICAL_SCENE_COMMIT'); return None
        record=json.loads(proposal.record); self._grant=bytes.fromhex(record['grant'])
        self.state=record['state']; self.blocked_reason=self.state
        if self.state=='COMMITTING': self.state='ADMITTED'
        if self.state in ('REVOKED','FAILED'): self._commit_hold=False
        self._published_grant=self._grant
        # grant 반환은 내구 제출 완료다. ADMITTED도 mapper APPLIED 전 eligible=false다.
        return self._grant

    def journal_failed(self, proposal, *, unknown):
        if proposal is not self._pending or type(unknown) is not bool: raise SceneAuthorityError('original journal failure required')
        self._ram_fence('PERSISTENCE_UNKNOWN' if unknown else 'PERSISTENCE_FAILED')
        if not unknown:
            self._pending=None; self.state='FAILED'; self._commit_hold=False

    def _reply(self, raw, lease, now_us):
        if type(lease) is not ClockLease or lease.completed_pi_us is None or lease.source!=self._source or lease.pi_host_boot!=self.host:
            raise SceneAuthorityError('actual currently admitted clock exchange required')
        reply=decode(raw,schema.MapperClockReply); probe=decode(lease.raw_probe_bytes,schema.MapperClockProbe)
        if (reply.pi_host_boot!=self.host or not reply.HasField('survey_summary') or
                (reply.probe_seq,reply.nonce,reply.pi_send_us,reply.pi_client_instance)!=(probe.probe_seq,probe.nonce,probe.pi_send_us,probe.pi_client_instance)
                or not lease.completed_pi_us<=now_us<lease.expires_pi_us): raise SceneAuthorityError('summary parent/outstanding probe mismatch')
        for name in SOURCE_KEYS-{'vehicle'}:
            if getattr(reply,name)!=self._source[name]: raise SceneAuthorityError('summary parent source mismatch')
        trust=decode(lease.trust_bytes,schema.WorldTrust)
        if reply.j2+trust.upper_offset_us<probe.pi_send_us or reply.j3+trust.lower_offset_us>lease.completed_pi_us:
            raise SceneAuthorityError('reply cannot follow its actual Pi receive completion')
        summary=reply.survey_summary
        if not 0<summary.started_peer_us<=summary.last_observed_peer_us<=summary.completed_peer_us<=reply.j3:
            raise SceneAuthorityError('actual summary work time must precede reply')
        return reply.survey_summary,trust

    def accept_reply(self, raw, lease, gate, *, now_us):
        if self._grant is None or self._pending is not None or self._restart: return None
        summary,trust=self._reply(raw,lease,_uint(now_us)); grant=decode(self._grant,schema.SceneGrant); p=self.policy
        if (summary.request_nonce!=grant.request_nonce or summary.survey_id!=grant.scene.survey_id
                or summary.survey_revision!=grant.scene.survey_revision or lease.source_revision!=grant.source_admission_revision
                or summary.coverage_profile_sha256.hex()!=p.coverage_profile_sha256): raise SceneAuthorityError('current survey nonce/revision/profile')
        if summary.status==3:
            # 현재 parent/nonce의 부정 보고는 추진권한을 만들지 않는다. 옛 cell freshness를
            # 이유로 철회를 버리거나 원 summary의 관측시각을 현재로 다시 쓰지 않는다.
            self._ram_fence('MAPPER_INVALID'); self.state='FAILED'; return None
        lower=summary.last_observed_peer_us-summary.timestamp_error_us+trust.lower_offset_us
        upper=summary.last_observed_peer_us+summary.timestamp_error_us+trust.upper_offset_us
        if self.state=='ADMITTED' and self._eligible and summary.status==2 and now_us<grant.valid_until_pi_us:
            expected=decode(self._base_summary,schema.SurveySummary); expected.status=2
            if encode(expected)!=encode(summary): raise SceneAuthorityError('changed already applied frozen evidence')
            return 'APPLIED'
        if (lower<lease.pi_support_lower_us or upper>now_us or now_us-lower>p.summary_max_age_us
                or lower<grant.issued_pi_us or now_us>=grant.valid_until_pi_us): raise SceneAuthorityError('original summary observation freshness/window')
        if self.state=='SURVEYING' and summary.status==0:
            start_lo=summary.started_peer_us-summary.timestamp_error_us+trust.lower_offset_us
            start_hi=summary.started_peer_us+summary.timestamp_error_us+trust.upper_offset_us
            if self._start_peer is None:
                if start_lo<max(grant.issued_pi_us,lease.pi_support_lower_us) or start_hi>now_us:
                    raise SceneAuthorityError('initial survey start not actually time-bound')
                self._start_peer=summary.started_peer_us; self._start_pi=(start_lo,start_hi)
            elif summary.started_peer_us!=self._start_peer: raise SceneAuthorityError('survey start changed')
            self._summary=encode(summary); return 'APPLIED'
        frozen_status=4 if grant.purpose else 1
        if self.state=='FREEZE_PENDING' and summary.status==frozen_status:
            if (summary.required_sensor_mask!=p.required_sensor_mask or summary.valid_sensor_mask&p.required_sensor_mask!=p.required_sensor_mask
                    or summary.valid_sensor_mask&~15 or not 0<summary.positive_cell_count<=p.atlas_cell_capacity
                    or summary.anchor_position_bound_m>p.max_anchor_position_m or summary.anchor_heading_bound_rad>p.max_anchor_heading_rad
                    or summary.evidence_sha256==bytes(32) or summary.failure_flags or not 1<=summary.observation_station_count<=24
                    or not grant.purpose and summary.started_peer_us!=self._start_peer): raise SceneAuthorityError('frozen evidence/profile/initial observation mismatch')
            request=self._current_request(); self._gate(gate,request,now_us)
            if now_us>=grant.survey_deadline_pi_us: raise SceneAuthorityError('original commit deadline expired')
            if grant.purpose and (summary.started_peer_us-summary.timestamp_error_us+trust.lower_offset_us
                    < max(request.issued_us,lease.pi_support_lower_us)):
                raise SceneAuthorityError('restore requires new original validation observations')
            if grant.purpose and summary.evidence_sha256!=grant.restore_checkpoint_sha256: raise SceneAuthorityError('selected restore checkpoint mismatch')
            self._base_summary=encode(summary); grant.lifecycle_state=2; grant.lifecycle_revision+=1
            if not grant.purpose:
                grant.frozen_evidence_sha256=summary.evidence_sha256; grant.frozen_survey_revision=summary.survey_revision
                grant.frozen_hazard_revision=summary.hazard_revision
            elif summary.hazard_revision!=grant.frozen_hazard_revision: raise SceneAuthorityError('restore hazard changed')
            return self._queue(request,gate,encode(grant),'COMMITTING')
        if self.state=='ADMITTED' and summary.status==2:
            if now_us>=grant.survey_deadline_pi_us:
                self._ram_fence('TIMEOUT'); return None
            if self._base_summary is None: raise SceneAuthorityError('no actual frozen summary')
            expected=decode(self._base_summary,schema.SurveySummary); expected.status=2
            if encode(expected)!=encode(summary): raise SceneAuthorityError('mapper APPLIED changed immutable frozen evidence')
            if self._commit_hold:
                self._eligible=True; self._commit_hold=False; self.blocked_reason=None
            self._summary=encode(summary); return 'APPLIED'
        return None

    def _current_request(self):
        if not hasattr(self,'_operation'): raise SceneAuthorityError('no original scene operation')
        return self._operation

    def revoke(self, request, *, source_admission_revision, now_us, cancel_fence=0):
        if not self._reserve(request): return None
        if request.action!='scene.revoke' or self._grant is None or not request.issued_us<=now_us<request.expires_us:
            raise SceneAuthorityError('original current-scene revoke required')
        grant=decode(self._grant,schema.SceneGrant)
        if UUID(json.loads(request.args_bytes)['scene_id']).bytes!=grant.scene.admission_id: raise SceneAuthorityError('revoke target changed')
        _uint(source_admission_revision,1); _uint(cancel_fence)
        if source_admission_revision<grant.source_admission_revision: raise SceneAuthorityError('source revision regressed')
        self._ram_fence('REVOKED'); self.state='REVOKED'; self._restart=True
        grant.lifecycle_state=3; grant.lifecycle_revision+=1; grant.scene.admission_revision+=1
        grant.source_admission_revision=source_admission_revision; grant.request_nonce=UUID(request.nonce).bytes
        grant.survey_deadline_pi_us=min(request.expires_us,grant.valid_until_pi_us)
        raw=encode(grant)
        # revoke는 STOP 선택이나 내구 기록을 위해 가짜 stationary/session을 만들지 않는다.
        if self._pending is not None:
            if self._deferred is not None: raise SceneAuthorityError('one original deferred revoke')
            self._deferred=(request,cancel_fence,raw); return None
        return self._queue(request,None,raw,'REVOKED',cancel_fence=cancel_fence)

    def source_changed(self, source):
        new=_source(source)
        if new==self._source: return False
        self._ram_fence('SOURCE_CHANGED'); self._source=new; return True

    def restore(self, request, gate, *, source_admission_revision, now_us):
        if not self._reserve(request): return None
        if (request.action!='scene.restore' or self._grant is None or self._pending is not None or self._restart
                or self.blocked_reason!='SOURCE_CHANGED'): raise SceneAuthorityError('explicit same-Pi controlled restore required')
        self._gate(gate,request,now_us); grant=decode(self._grant,schema.SceneGrant)
        if (not grant.scene.allow_restore_after_mapper_restart or not grant.HasField('frozen_evidence_sha256')
                or now_us>=grant.valid_until_pi_us or any(getattr(grant,name)!=self._source[name] for name in
                    ('vehicle','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))):
            raise SceneAuthorityError('restore source/permission/original expiry changed')
        _uint(source_admission_revision,grant.source_admission_revision)
        grant.source_admission_revision=source_admission_revision; grant.lifecycle_state=1; grant.lifecycle_revision+=1
        grant.purpose=1; grant.request_nonce=UUID(request.nonce).bytes; grant.survey_deadline_pi_us=min(request.expires_us,grant.valid_until_pi_us)
        grant.restore_checkpoint_sha256=bytes.fromhex(json.loads(request.args_bytes)['checkpoint_sha256'])
        self._commit_hold=True; self._start_peer=self._start_pi=None
        return self._queue(request,gate,encode(grant),'FREEZE_PENDING')

    def tick(self, *, now_us):
        if self._grant is None: return False
        _uint(now_us)
        grant=decode(self._grant,schema.SceneGrant)
        collection_expired=self.state=='SURVEYING' and now_us>=grant.issued_pi_us+SURVEY_US
        expired=now_us>=grant.valid_until_pi_us or collection_expired or self._commit_hold and now_us>=grant.survey_deadline_pi_us
        if expired and self.blocked_reason!='TIMEOUT':
            self._ram_fence('TIMEOUT'); self.state='FAILED' if collection_expired else 'REVOKED'
            # 불명 COMMIT은 hold를 유지한다. root가 원 job 조회/명시 revoke를 journal에 넘긴다.
            return True
        return False
