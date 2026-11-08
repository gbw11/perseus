"""S의 단일 DB copy 원장과 작은 control 중계.

SQLite·큰 사본/hash·fsync·새 IPC/thread는 소유하지 않는다. caller는 등록 child의
actual send/exit와 native token 반환을 연결한다. draft 생성/encode는 권한 승인이 아니다.
"""

from dataclasses import asdict,dataclass,field,replace
from hashlib import sha256
from uuid import uuid4

from .lifecycle_messages import LifecyclePeer
from .native_resources import ArenaSpec
from .raw_leases import Endpoint
from .resident_storage import binding_payload
from .resident_transport import j_us
from .storage_copy import CopyLease,CopyReturned
from .storage_messages import (db_payload,make_load_request,parse_db_job,prepare_payload,publication_binding)
from ..storage.contracts import LoadRequest,PersistenceConflict,PersistenceError,canonical,decimal,digest,json_bytes,uint,uuid


@dataclass(frozen=True,slots=True)
class DbOutbound:
    role: str
    kind: str
    payload_bytes: bytes
    domain: str
    revision: int
    request_id: str
    issued_us: int
    expires_us: int
    purpose: str

    @property
    def payload(self):return json_bytes(self.payload_bytes,3500)

    @property
    def kwargs(self):
        return dict(kind=self.kind,payload=self.payload,domain=self.domain,revision=self.revision,
            request_id=self.request_id,issued_us=self.issued_us,expires_us=self.expires_us)


@dataclass(frozen=True,slots=True)
class DbCompletion:
    """원 job의 작은 결과 사본. current=False인 QUERY/history는 새 원천 성공이 아니다."""
    request_bytes: bytes
    result_bytes: bytes
    request_id: str
    issued_us: int
    expires_us: int
    received_us: int
    current: bool

    @property
    def result(self):return json_bytes(self.result_bytes,3500)


@dataclass(frozen=True,slots=True)
class DbAbort:
    """원 대기 해제용 local bookkeeping. SQL FAILED/COMMITTED나 새 current 성공 진술이 아니다.

    original body/UUID/기한은 불변이며 observed_us는 abort 관측일 뿐 commit stamp가 아니다.
    historical_result_bytes가 있으면 실제 원 D reply다. 이 사본으로 range를 새로 발급하지 않는다.
    """
    operation: str
    body_bytes: bytes
    request_id: str
    issued_us: int
    expires_us: int
    observed_us: int
    mapper: Endpoint
    origin_context_sha256: str | None
    reason: str
    historical_result_bytes: bytes | None=None
    current: bool=field(default=False,init=False)

    def __post_init__(self):
        uuid(self.request_id)
        for value in (self.issued_us,self.expires_us,self.observed_us):uint(value)
        if not self.issued_us<self.expires_us<=self.issued_us+5_000_000 or self.observed_us<self.issued_us:
            raise PersistenceError('abort must retain the original operation window')
        if type(self.mapper) is not Endpoint or self.operation not in ('CHECKPOINT','LOAD','QUERY','RESERVE_PROVENANCE'):
            raise PersistenceError('abort requires original registered mapper/operation')
        if self.origin_context_sha256 is not None:digest(self.origin_context_sha256)
        if type(self.reason) is not str or not 1<=len(self.reason)<=64 or any(not 33<=ord(c)<=126 for c in self.reason):
            raise PersistenceError('bounded abort reason required')
        body=self.body;op='PREPARE' if self.operation in ('CHECKPOINT','LOAD') else self.operation
        db_payload(op,body)
        if op=='PREPARE' and (body['direction']!=self.operation or body['job_id']!=self.request_id or body['context_sha256']!=self.origin_context_sha256):
            raise PersistenceConflict('abort changed original prepared job/context')
        if op=='RESERVE_PROVENANCE' and body['mapper_worker']!=self.mapper.worker_instance:raise PersistenceConflict('abort changed original range mapper')
        result=self.historical_result
        if result is not None:
            result_op='RANGE_RESULT' if op=='RESERVE_PROVENANCE' else 'RESULT'
            db_payload(result_op,result)
            keys=('database_uuid','reservation_nonce','mapper_worker') if result_op=='RANGE_RESULT' else ('database_uuid','job_id','job_digest')
            if any(body[key]!=result[key] for key in keys):raise PersistenceConflict('abort history belongs to a different original request')
            if result_op=='RESULT' and result['committed_j_us'] is not None and decimal(result['committed_j_us'])>self.observed_us:
                raise PersistenceError('abort history contains a future commit stamp')

    @property
    def body(self):return json_bytes(self.body_bytes,3500)

    @property
    def historical_result(self):return None if self.historical_result_bytes is None else json_bytes(self.historical_result_bytes,3500)

    @property
    def fields(self):
        return dict(operation=self.operation,body=self.body,request_id=self.request_id,issued_j_us=str(self.issued_us),
            expires_j_us=str(self.expires_us),observed_j_us=str(self.observed_us),mapper=asdict(self.mapper),
            origin_context_sha256=self.origin_context_sha256,reason=self.reason,historical_result=self.historical_result)

    @classmethod
    def from_fields(cls,value):
        expected={'operation','body','request_id','issued_j_us','expires_j_us','observed_j_us','mapper','origin_context_sha256','reason','historical_result'}
        if type(value) is not dict or set(value)!=expected or type(value['mapper']) is not dict or set(value['mapper'])!= {'channel_epoch','worker_instance'}:
            raise PersistenceError('exact original abort fields required')
        return cls(value['operation'],canonical(value['body']),value['request_id'],decimal(value['issued_j_us']),decimal(value['expires_j_us']),
            decimal(value['observed_j_us']),Endpoint(**value['mapper']),value['origin_context_sha256'],value['reason'],
            None if value['historical_result'] is None else canonical(value['historical_result']))


@dataclass(slots=True)
class _Pending:
    body_bytes: bytes
    request_id: str
    issued_us: int
    expires_us: int
    mapper: Endpoint
    phase: str
    operation: str
    publication_sha256: str | None
    context_sha256: str | None
    lease: CopyLease | None=None
    ready_bytes: bytes | None=None
    released: bool=False
    fenced: bool=False
    grant_sent: bool=False
    reader_sent: bool=False
    commit_sent: bool=False
    grant_selected: bool=False
    reader_selected: bool=False

    @property
    def body(self):return json_bytes(self.body_bytes,3500)


class ResidentDbBroker:
    """한 원 DB job/한 DB_COPY slot. UNKNOWN owner는 원 key를 노출하고 정규화를 추측하지 않는다.

    caller는 반환 draft를 실제 send 직전 dispatchable로 검사하고, ResidentLink의 실제
    SentPacket을 note_sent에 전달한다. 선택된 transport pending은 임의로 취소하지 않는다.
    D-only 재결합은 root가 old D actual reap/새 discovery를 확인한 뒤 수행한다.
    """

    def __init__(self,storage_peer,mapper_peer,storage_binding,copy_spec,*,generation_floor=0,clock_us=j_us):
        if (type(storage_peer) is not LifecyclePeer or storage_peer.role!='D' or type(mapper_peer) is not LifecyclePeer
                or mapper_peer.role!='M' or mapper_peer.service_instance!=storage_peer.service_instance
                or type(copy_spec) is not ArenaSpec or copy_spec.role!='DB_COPY'
                or type(generation_floor) is not int or not 0<=generation_floor<(1<<64)):
            raise PersistenceError('registered D/M/fixed copy arena required')
        binding_payload(storage_binding)
        if (storage_binding['service_instance'],storage_binding['storage_worker'])!=(storage_peer.service_instance,storage_peer.worker_instance):
            raise PersistenceError('admitted StorageBinding differs from current D')
        self.storage_peer=storage_peer;self.mapper_peer=mapper_peer;self.binding=dict(storage_binding);self.spec=copy_spec
        self.clock=clock_us;self.generation=generation_floor;self.pending=None;self.last_completion=None;self.aborted=None
        self.completed_request=None;self.completion_selected=False;self.completion_sent=False
        self.publication=None;self.publication_sha256=None;self.context_sha256=None;self.publication_acked=False
        self.geometry_origin=None
        self.bind_draft=None;self.bind_sent=False;self.fenced=False;self.fence_reason=None

    @property
    def mapper(self):return Endpoint(self.mapper_peer.channel_epoch,self.mapper_peer.worker_instance)

    @property
    def storage(self):return Endpoint(self.storage_peer.channel_epoch,self.storage_peer.worker_instance)

    @property
    def pending_deadline_us(self):return None if self.pending is None else self.pending.expires_us

    @property
    def cleanup_ready(self):
        p=self.pending
        return p.lease.writer if p is not None and p.fenced and p.phase=='READY_UNCLAIMED' else None

    @property
    def requires_quiescence(self):
        p=self.pending
        return p is not None and p.fenced and p.phase in ('M_WRITING','D_WRITING','D_READING','M_READING')

    def _emit(self,role,kind,payload,p,*,purpose,domain='STORAGE',revision=1,original_issue=False):
        issued=p.issued_us if original_issue else self.clock()
        if kind!='SLOT' or payload.get('op')!='RELEASE':
            if issued>=p.expires_us:return ()
        return (DbOutbound(role,kind,canonical(payload),domain,revision,p.request_id,issued,p.expires_us,purpose),)

    def _db(self,role,op,body,p,*,purpose,original_issue=False):
        return self._emit(role,'DB_JOB',db_payload(op,body),p,purpose=purpose,original_issue=original_issue)

    def _abort(self,p,reason,*,historical_result=None):
        notice=DbAbort(p.operation,p.body_bytes,p.request_id,p.issued_us,p.expires_us,self.clock(),p.mapper,p.context_sha256,reason,
            None if historical_result is None else canonical(historical_result))
        if self.aborted is not None:
            if (self.aborted.operation,self.aborted.body_bytes,self.aborted.request_id,self.aborted.issued_us,self.aborted.expires_us)!=(
                    notice.operation,notice.body_bytes,notice.request_id,notice.issued_us,notice.expires_us):
                raise PersistenceConflict('consume the original bounded DB abort before another one')
            return
        self.aborted=notice

    def take_aborted(self):
        """Root가 해당 원 M mailbox에 terminal event로 전달한다. wire RESULT/기한 갱신은 생성하지 않는다."""
        result=self.aborted;self.aborted=None
        return result

    def _new_lease(self,producer,consumer,p):
        if self.generation==(1<<64)-1:raise PersistenceError('DB_COPY generation exhausted')
        self.generation+=1
        bound=p.publication_sha256 is not None
        return CopyLease(self.storage_peer.service_instance,self.spec.arena_id,self.generation,producer,consumer,str(uuid4()),
            raw_binding_sha256=self.publication['raw_binding_sha256'] if bound else None,publication_binding_sha256=p.publication_sha256)

    def _current(self,p):
        return (not self.fenced and not p.fenced and self.clock()<p.expires_us and p.mapper==self.mapper
            and p.context_sha256==self.context_sha256 and p.publication_sha256==self.publication_sha256 and self.publication_acked)

    def _bind_if_idle(self):
        if (self.pending is not None or self.publication is None or self.publication_acked or self.bind_draft is not None
                or self.fenced or self.publication['mapper_worker']!=self.mapper.worker_instance):return ()
        now=self.clock();revision=int(self.publication['source_binding_revision'])
        payload=dict(binding_revision=str(revision),binding_sha256=self.publication_sha256,binding=self.publication)
        if self.geometry_origin is not None:payload['geometry_origin']=json_bytes(self.geometry_origin,1024)
        self.bind_draft=DbOutbound('D','BIND',canonical(payload),'PUBLICATION',revision,str(uuid4()),now,now+5_000_000,'PUBLICATION_BIND')
        return (self.bind_draft,)

    def set_publication(self,binding,current_context_sha256,*,geometry_origin=None):
        publication_binding(binding);digest(current_context_sha256)
        origin_raw=None
        if geometry_origin is not None:
            from ..storage.geometry_scope import PiGeometryOrigin
            fields=json_bytes(geometry_origin,1024) if type(geometry_origin) is bytes else geometry_origin
            origin=PiGeometryOrigin.from_fields(fields)
            if (origin.odom_epoch,origin.calibration)!=(binding['odom_epoch'],binding['calibration']):raise PersistenceConflict('publication origin changed original odom/calibration')
            origin_raw=canonical(origin.fields)
        if (binding['service_instance'],binding['mapper_worker'])!=(self.storage_peer.service_instance,self.mapper.worker_instance):
            raise PersistenceError('publication differs from registered mapper')
        actual=sha256(canonical(binding)).hexdigest()
        if self.publication is not None:
            old=int(self.publication['source_binding_revision']);new=int(binding['source_binding_revision'])
            if new<old or new==old and (actual!=self.publication_sha256 or current_context_sha256!=self.context_sha256 or origin_raw!=self.geometry_origin):
                raise PersistenceConflict('publication changed without a new stable source revision')
        if actual!=self.publication_sha256:
            self.fence('SOURCE_REPLACED');self.publication=json_bytes(canonical(binding),2048);self.publication_sha256=actual
            self.geometry_origin=origin_raw
            self.context_sha256=current_context_sha256;self.publication_acked=False;self.bind_draft=None;self.bind_sent=False;self.fenced=False
        return self._bind_if_idle()

    def request_bootstrap_load(self,request):
        if type(request) is not LoadRequest or request.purpose!='BOOTSTRAP_RESTORE' or self.pending is not None or self.aborted is not None:
            raise PersistenceConflict('one original provisional bootstrap LOAD required')
        expected=make_load_request(self.binding,mapper_worker=self.mapper.worker_instance,
            origin_mapper_instance=request.job.context.origin_mapper_instance,job_id=request.job.job_id,
            issued_us=request.job.issued_us,expires_us=request.job.expires_us,
            checkpoint_ref=json_bytes(request.checkpoint_ref,2048),selection=json_bytes(request.selection,512),purpose=request.purpose,
            scene_commit_nonce=request.job.scene_commit_nonce)
        if request!=expected or not request.job.issued_us<=self.clock()<request.job.expires_us:
            raise PersistenceConflict('bootstrap LOAD differs from admitted original ref/request')
        body=prepare_payload(request)['body']
        self.pending=_Pending(canonical(body),request.job.job_id,request.job.issued_us,request.job.expires_us,self.mapper,
            'D_WRITING','LOAD',None,request.job.context.sha256)
        p=self.pending;p.lease=self._new_lease(self.storage,self.mapper,p)
        return self._db('D','PREPARE',body,p,purpose='PREPARE',original_issue=True)+self._emit('D','SLOT',p.lease.slot_payload('GRANT'),p,purpose='WRITER_GRANT')

    def query_history(self,job_id,job_digest,*,request_id,issued_us,expires_us):
        """새 QUERY 기한은 조회에만 귀속한다. 원 transaction/job의 시각이나 성공을 갱신하지 않는다."""
        if self.pending is not None or self.aborted is not None:raise PersistenceConflict('one original DB operation/terminal handoff at a time')
        body=dict(database_uuid=self.binding['database_uuid'],job_id=job_id,job_digest=job_digest)
        payload=db_payload('QUERY',body)
        if not issued_us<=self.clock()<expires_us<=issued_us+5_000_000:raise PersistenceError('original bounded QUERY lifetime required')
        p=_Pending(canonical(body),request_id,issued_us,expires_us,self.mapper,'REQUESTED','QUERY',self.publication_sha256,self.context_sha256)
        self.pending=p
        return self._emit('D','DB_JOB',payload,p,purpose='QUERY',original_issue=True)

    def receive_mapper(self,packet):
        if packet.kind=='SLOT':return self._slot(packet,self.mapper_peer)
        message=parse_db_job(packet,self.mapper_peer,database_uuid=self.binding['database_uuid'],now_us=self.clock(),child_sender=True,allow_expired=True)
        body=message.body;op=message.op
        if op=='READY':return self._mapper_ready(message)
        if self.clock()>=message.expires_us:raise PersistenceConflict('expired request cannot start new D work')
        if op not in ('PREPARE','RESERVE_PROVENANCE','QUERY'):raise PersistenceError('unsupported mapper DB operation')
        if self.pending is not None:
            p=self.pending
            if (p.operation=='LOAD' and op=='PREPARE' or p.operation=='CHECKPOINT' and op=='PREPARE' or p.operation==op):
                if (p.body_bytes,p.request_id,p.issued_us,p.expires_us)==(message.body_bytes,packet.fields['request_id'],message.issued_us,message.expires_us):return ()
            raise PersistenceConflict('DB broker already owns another original job')
        if self.aborted is not None:raise PersistenceConflict('original DB terminal handoff must be consumed before another job')
        if op!='QUERY' and (self.fenced or not self.publication_acked):raise PersistenceConflict('DB source is not currently bound')
        if op=='PREPARE':
            if (packet.fields['request_id']!=body['job_id'] or body['context_sha256']!=self.context_sha256
                    or body['origin_mapper_instance']!=self.publication['mapper_instance']):raise PersistenceConflict('mapper PREPARE source context mismatch')
            if body['direction']=='LOAD' and body['load_purpose']!='CURRENT_MAP':raise PersistenceError('bootstrap LOAD must use the explicit admitted-ref entry')
            operation=body['direction'];phase='M_WRITING' if operation=='CHECKPOINT' else 'D_WRITING'
        else:
            if op=='RESERVE_PROVENANCE' and body['mapper_worker']!=self.mapper.worker_instance:raise PersistenceConflict('range request mapper changed')
            operation=op;phase='REQUESTED'
        p=_Pending(message.body_bytes,packet.fields['request_id'],message.issued_us,message.expires_us,self.mapper,phase,operation,
            self.publication_sha256,self.context_sha256)
        self.pending=p
        result=self._db('D',op,body,p,purpose=op,original_issue=True)
        if op=='PREPARE':
            p.lease=self._new_lease(self.mapper,self.storage,p) if operation=='CHECKPOINT' else self._new_lease(self.storage,self.mapper,p)
            result+=self._emit('M' if operation=='CHECKPOINT' else 'D','SLOT',p.lease.slot_payload('GRANT'),p,purpose='WRITER_GRANT')
        return result

    def _mapper_ready(self,message):
        p=self.pending;body=message.body
        if p is None or p.operation!='CHECKPOINT' or p.phase!='M_WRITING' or not p.grant_sent:raise PersistenceConflict('no actual original mapper writer grant')
        self._match(message,p)
        if CopyLease.from_fields(body['copy_lease'])!=p.lease:raise PersistenceConflict('mapper READY changed writer full key')
        p.ready_bytes=canonical(body);p.phase='READY_UNCLAIMED'
        if not self._current(p):p.fenced=True;return ()
        p.lease=replace(p.lease,read_token=str(uuid4()));body['copy_lease']=p.lease.fields
        p.phase='D_READING'
        return self._db('D','READY',body,p,purpose='READER_READY')

    def _match(self,message,p):
        body=message.body;original=p.body
        if (message.packet.fields['request_id']!=p.request_id or message.expires_us!=p.expires_us or message.issued_us<p.issued_us
                or body.get('database_uuid')!=original['database_uuid']):raise PersistenceConflict('D operation changed original request/lifetime')
        if p.operation=='RESERVE_PROVENANCE':
            if (body.get('reservation_nonce'),body.get('mapper_worker'))!=(original['reservation_nonce'],original['mapper_worker']):raise PersistenceConflict('range result changed original nonce/mapper')
        elif (body.get('job_id'),body.get('job_digest'))!=(original['job_id'],original['job_digest']):raise PersistenceConflict('DB result changed original job/digest')
        if message.op=='RESULT' and p.operation in ('CHECKPOINT','LOAD') and body['origin_context_sha256']!=p.context_sha256:
            raise PersistenceConflict('DB result changed original source context')

    def receive_storage(self,packet):
        if packet.kind=='BIND_ACK':return self._bind_ack(packet)
        if packet.kind=='SLOT':return self._slot(packet,self.storage_peer)
        message=parse_db_job(packet,self.storage_peer,database_uuid=self.binding['database_uuid'],now_us=self.clock(),child_sender=True,allow_expired=True)
        p=self.pending
        if p is None:raise PersistenceConflict('D reply has no original pending job')
        self._match(message,p);body=message.body
        if message.op=='READY':
            if p.operation=='CHECKPOINT':
                if p.phase!='D_READING' or not p.reader_sent or not p.released or CopyLease.from_fields(body['copy_lease'])!=p.lease:
                    raise PersistenceConflict('D copied READY requires original reader send and actual RELEASE')
                expected=json_bytes(p.ready_bytes,3500);expected['copy_lease']=p.lease.fields
                if body!=expected:raise PersistenceConflict('D changed original copied READY metadata')
                if not self._current(p):
                    self._abort(p,'COPIED_JOB_REVOKED');self.pending=None
                    return self._bind_if_idle()
                p.phase='COMMITTING'
                return self._db('D','COMMIT',dict(database_uuid=body['database_uuid'],job_id=body['job_id'],job_digest=body['job_digest'],copy_lease=p.lease.fields),p,purpose='COMMIT')
            if p.operation!='LOAD' or p.phase!='D_WRITING' or not p.grant_sent or CopyLease.from_fields(body['copy_lease'])!=p.lease:
                raise PersistenceConflict('D LOAD READY has no exact actual writer grant')
            p.ready_bytes=canonical(body);p.phase='READY_UNCLAIMED'
            provisional=p.body['load_purpose']=='BOOTSTRAP_RESTORE'
            if p.fenced or self.clock()>=p.expires_us or p.mapper!=self.mapper or not provisional and not self._current(p):p.fenced=True;return ()
            p.lease=replace(p.lease,read_token=str(uuid4()));body['copy_lease']=p.lease.fields;p.phase='M_READING'
            return self._db('M','READY',body,p,purpose='READER_READY')
        expected='RANGE_RESULT' if p.operation=='RESERVE_PROVENANCE' else 'RESULT'
        if message.op!=expected or p.operation=='CHECKPOINT' and (p.phase!='COMMITTING' or not p.commit_sent):
            raise PersistenceConflict('unexpected D completion phase')
        positive=body.get('status') in ('COMMITTED','DUPLICATE') or body.get('result') in ('GRANTED','REPLAY')
        current=self._current(p) and p.operation!='QUERY' and positive
        self.last_completion=DbCompletion(p.body_bytes,message.body_bytes,p.request_id,p.issued_us,p.expires_us,self.clock(),current)
        self.completed_request=p;self.completion_selected=self.completion_sent=False
        self.pending=None
        if p.fenced or self.clock()>=p.expires_us:
            self._abort(p,'HISTORICAL_D_RESULT',historical_result=body);outgoing=()
        else:outgoing=self._db('M',message.op,body,p,purpose='COMPLETION') if p.mapper==self.mapper else ()
        return outgoing+self._bind_if_idle()

    def _slot(self,packet,peer):
        fields=packet.fields;payload=fields['payload'];p=self.pending
        if (fields['binding_domain'],fields['binding_revision'],fields['service_instance'],fields['channel_epoch'],fields['sender_instance'])!=(
                'STORAGE','1',peer.service_instance,peer.channel_epoch,peer.worker_instance):raise PersistenceError('SLOT source differs from registered participant')
        lease=CopyLease.from_slot(peer.service_instance,payload)
        if (payload['op']!='RELEASE' or p is None or p.lease!=lease or fields['request_id']!=p.request_id
                or not p.issued_us<=int(fields['issued_j_us'])<=self.clock() or int(fields['expires_j_us'])!=p.expires_us
                or payload['payload_length'] is not None or payload['descriptor_sha256'] is not None):
            raise PersistenceConflict('RELEASE changed original full lease/request')
        if p.operation=='CHECKPOINT' and peer.role=='D' and p.phase=='D_READING' and p.reader_sent:
            p.released=True;return ()
        if p.operation=='LOAD' and peer.role=='M' and p.phase=='M_READING' and p.reader_sent:
            result=self._emit('D','SLOT',lease.slot_payload('RELEASE'),p,purpose='COPY_RELEASE')
            # RELEASE는 physical copy의 반환뿐이다. M-compute semantic validation을 대신하지 않는다.
            self.last_completion=DbCompletion(p.body_bytes,p.ready_bytes,p.request_id,p.issued_us,p.expires_us,self.clock(),False)
            if p.fenced or self.clock()>=p.expires_us:self._abort(p,'LOAD_COPY_REVOKED')
            self.pending=None
            return result+self._bind_if_idle()
        raise PersistenceConflict('RELEASE has no actual original reader owner')

    def _bind_ack(self,packet):
        original=self.bind_draft;fields=packet.fields;body=fields['payload'];now=self.clock()
        if original is None or not self.bind_sent:raise PersistenceConflict('publication ACK has no actual original BIND send')
        if ((fields['binding_domain'],fields['binding_revision'],fields['service_instance'],fields['channel_epoch'],fields['sender_instance'],fields['request_id'])!=(
                'PUBLICATION',str(original.revision),self.storage_peer.service_instance,self.storage_peer.channel_epoch,self.storage_peer.worker_instance,original.request_id)
                or type(body) is not dict or set(body)!= {'binding_revision','binding_sha256','accepted','state','reason'} or type(body.get('accepted')) is not bool
                or body!=dict(binding_revision=str(original.revision),binding_sha256=self.publication_sha256,accepted=True,state='IDLE',reason=None)
                or not original.issued_us<=int(fields['issued_j_us'])<=now<int(fields['expires_j_us'])<=original.expires_us):
            raise PersistenceConflict('publication ACK differs from actual original BIND')
        self.publication_acked=True;self.bind_draft=None;self.bind_sent=False
        return ()

    def dispatchable(self,draft):
        if type(draft) is not DbOutbound:return False
        if draft.kind=='SLOT' and draft.payload.get('op')=='RELEASE':return True
        if self.clock()>=draft.expires_us:return False
        if draft.purpose=='PUBLICATION_BIND':return self.bind_draft==draft and self.pending is None
        p=self.pending
        if draft.purpose=='COMPLETION':
            completed=self.completed_request
            return (completed is not None and not self.fenced and completed.mapper==self.mapper
                and completed.context_sha256==self.context_sha256 and completed.publication_sha256==self.publication_sha256
                and draft.request_id==completed.request_id)
        if p is None or draft.request_id!=p.request_id or p.fenced:return False
        if p.operation=='LOAD' and p.body['load_purpose']=='BOOTSTRAP_RESTORE':return p.mapper==self.mapper
        if p.operation=='QUERY':return p.mapper==self.mapper
        return self._current(p)

    def note_sent(self,draft,packet,completed_us):
        if type(draft) is not DbOutbound:raise PersistenceError('actual selected original draft required')
        peer=self.storage_peer if draft.role=='D' else self.mapper_peer;fields=packet.fields
        if ((fields['service_instance'],fields['channel_epoch'],fields['sender_instance'],fields['kind'],fields['binding_domain'],fields['binding_revision'],fields['request_id'],
                fields['issued_j_us'],fields['expires_j_us'],fields['payload'])!=(peer.service_instance,peer.channel_epoch,peer.service_instance,draft.kind,draft.domain,
                str(draft.revision),draft.request_id,str(draft.issued_us),str(draft.expires_us),draft.payload)
                or type(completed_us) is not int or not draft.issued_us<=completed_us<=self.clock()):raise PersistenceError('actual send completion differs from original draft')
        if draft.purpose=='PUBLICATION_BIND' and self.bind_draft==draft:self.bind_sent=True
        if draft.purpose=='COMPLETION' and self.completed_request is not None and draft.request_id==self.completed_request.request_id:
            self.completion_sent=True
        p=self.pending
        if p is not None and p.request_id==draft.request_id:
            if draft.purpose=='WRITER_GRANT':p.grant_sent=True
            elif draft.purpose=='READER_READY':p.reader_sent=True
            elif draft.purpose=='COMMIT':p.commit_sent=True

    def note_selected(self,draft,packet):
        """ResidentLink.send가 실제 원 packet을 선택한 직후 호출한다. EAGAIN도 선택은 취소하지 않는다."""
        if packet.fields['request_id']!=draft.request_id or packet.payload!=draft.payload:raise PersistenceConflict('selected packet changed original draft')
        if draft.purpose=='COMPLETION' and self.completed_request is not None and draft.request_id==self.completed_request.request_id:
            self.completion_selected=True
        p=self.pending
        if p is not None and p.request_id==draft.request_id:
            if draft.purpose=='WRITER_GRANT':p.grant_selected=True
            elif draft.purpose=='READER_READY':p.reader_selected=True

    def abandon_unselected(self,draft):
        """caller가 자기 staging에서 뺀 미선택 draft만 취소한다. transport pending/실행 owner 회수는 아니다."""
        completed=self.completed_request
        if (draft.purpose=='COMPLETION' and completed is not None and draft.request_id==completed.request_id
                and not self.completion_selected and not self.completion_sent):
            self._abort(completed,'UNSELECTED_RESULT_REVOKED',historical_result=self.last_completion.result)
            return ()
        p=self.pending
        if p is None or draft.request_id!=p.request_id or not p.fenced:return ()
        if draft.purpose=='WRITER_GRANT' and not p.grant_selected and not p.grant_sent:
            self._abort(p,'UNSELECTED_WRITER_REVOKED')
            self.pending=None;return self._bind_if_idle()
        if draft.purpose=='READER_READY' and not p.reader_selected and not p.reader_sent:
            p.lease=p.lease.writer;p.phase='READY_UNCLAIMED'
        return ()

    def fence(self,reason):
        self.fenced=True;self.fence_reason=reason
        if self.last_completion is not None:self.last_completion=replace(self.last_completion,current=False)
        p=self.pending
        if p is not None:
            p.fenced=True
            if p.phase=='REQUESTED' or p.phase=='COMMITTING' or p.operation=='CHECKPOINT' and p.released:
                # SQL은 원 job 그대로 끝나거나 history 조회로 수습한다. 새 source 성공은 없다.
                return ()
        return ()

    def note_discarded(self,returned):
        p=self.pending;expected=self.cleanup_ready
        if (type(returned) is not CopyReturned or expected is None or returned.lease!=expected
                or not p.issued_us<=returned.views_ended_us<=returned.posted_us<=self.clock()):
            raise PersistenceConflict('actual native READY cleanup differs from original unclaimed key')
        result=self._emit('D','SLOT',p.lease.writer.slot_payload('RELEASE'),p,purpose='COPY_RELEASE') if p.operation=='LOAD' else ()
        self._abort(p,'READY_COPY_DISCARDED')
        self.pending=None
        return result+self._bind_if_idle()

    def tick(self):
        p=self.pending
        if p is not None and self.clock()>=p.expires_us:p.fenced=True
        if self.last_completion is not None and self.clock()>=self.last_completion.expires_us:
            self.last_completion=replace(self.last_completion,current=False)
        return self._bind_if_idle()

    def rebind_storage(self,peer,binding):
        """caller의 actual old-D reap/새 discovery 뒤만 호출한다. 소실 owner/token을 증명하지 않는다."""
        if type(peer) is not LifecyclePeer or peer.role!='D' or peer.service_instance!=self.storage_peer.service_instance:
            raise PersistenceError('registered replacement D required')
        binding_payload(binding)
        if (binding['database_uuid'],binding['service_instance'],binding['storage_worker'])!=(self.binding['database_uuid'],peer.service_instance,peer.worker_instance):
            raise PersistenceConflict('D recovery changed database or service identity')
        p=self.pending
        if p is not None and not (p.operation in ('QUERY','RESERVE_PROVENANCE') or p.operation=='CHECKPOINT' and p.released):
            raise PersistenceConflict('copy owner remains unknown; actual quiescent cleanup is required')
        if p is not None:self._abort(p,'D_OWNER_REBOUND_UNKNOWN')
        self.pending=None;self.storage_peer=peer;self.binding=dict(binding);self.publication_acked=False;self.bind_draft=None;self.bind_sent=False;self.fenced=False
        return self._bind_if_idle()

    def replace_mapper(self,peer):
        """actual old-M reap은 caller 책임이다. 원 copy owner의 미확정 상태는 그대로 fence한다."""
        if type(peer) is not LifecyclePeer or peer.role!='M' or peer.service_instance!=self.storage_peer.service_instance:
            raise PersistenceError('registered replacement M required')
        self.fence('M_REPLACED');self.mapper_peer=peer;self.publication_acked=False;self.bind_draft=None;self.bind_sent=False
        return ()

    def summary(self):
        p=self.pending
        return dict(database_uuid=self.binding['database_uuid'],storage_worker=self.storage.worker_instance,
            mapper_worker=self.mapper.worker_instance,copy_generation=str(self.generation),publication_bound=self.publication_acked,
            pending=None if p is None else dict(operation=p.operation,phase=p.phase,request_id=p.request_id,
                issued_j_us=str(p.issued_us),expires_j_us=str(p.expires_us),fenced=p.fenced),
            requires_quiescence=self.requires_quiescence,fenced=self.fenced,fence_reason=self.fence_reason,
            abort_pending=self.aborted is not None,
            last_completion_current=None if self.last_completion is None else self.last_completion.current)
