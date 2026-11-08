"""원 COMPLETE의 최대4 LOAD를 기존 M-compute와 S broker에 연결한다.

main은 작은 원 요청/보고 참조만 교환한다. 큰 decode/closure/hash는 compute에서만
수행하며 자료 완성은 현재 metric/scene/restore 사용권이 아니다. 실패한 원 작업을
자동 재발급하거나 corrupt/만료를 EMPTY로 바꾸지 않는다.
"""

from dataclasses import asdict,dataclass
from hashlib import sha256
from threading import Lock
from uuid import uuid4

from ..storage.contracts import (LoadRequest,canonical,decimal,digest,json_bytes,selected_checkpoint_reference,uint)
from .storage_messages import make_load_request,prepare_payload


LOAD_BODY_CAP=2048
COMPLETE_BODY_CAP=8192


def material_bytes(material):
    if type(material) is not tuple or len(material)!=2:
        raise ValueError('original D binding/bootstrap material required')
    raw=canonical(dict(binding=material[0],bootstrap=material[1]))
    if len(raw)>2048:raise ValueError('bounded original D material required')
    return raw


def _selected(raw):
    material=json_bytes(raw,2048)
    if type(material) is not dict or set(material)!={'binding','bootstrap'}:raise ValueError('exact original D material required')
    bootstrap,binding=material['bootstrap'],material['binding']
    if (type(bootstrap) is not dict or type(binding) is not dict or bootstrap.get('status')!='COMPLETE'
            or bootstrap.get('allocator_ready') is not True or type(bootstrap.get('schema_version')) is not int or bootstrap['schema_version']!=1
            or binding.get('database_uuid')!=bootstrap.get('database_uuid')):
        raise ValueError('actual admitted COMPLETE D material required')
    reference=canonical(bootstrap['selected_checkpoint']);selected=selected_checkpoint_reference(reference)
    if selected['tile_count']>128:raise ValueError('bootstrap source exceeds128 tiles')
    return binding,reference


def load_body(request):
    if type(request) is not LoadRequest or request.purpose!='BOOTSTRAP_RESTORE':
        raise ValueError('original bootstrap LOAD required')
    raw=canonical(dict(origin_mapper_instance=request.job.context.origin_mapper_instance,
        checkpoint_ref=json_bytes(request.checkpoint_ref,2048),selection=json_bytes(request.selection,512)))
    if len(raw)>LOAD_BODY_CAP:raise ValueError('bootstrap LOAD local body cap')
    return raw


def _ready_sha(ready):
    # S의 원 D-writer READY와 M에게 준 reader READY는 read_token 하나만 다르다.
    value=dict(ready);value['copy_lease']=dict(value['copy_lease']);value['copy_lease']['read_token']=None
    return sha256(canonical(value)).hexdigest()


@dataclass(frozen=True,init=False)
class BootstrapReport:
    """실제 CheckpointPages 완료의 작은 보고다. 큰 SourceComplete는 compute에 남는다."""
    body: bytes
    request_id: str
    issued_us: int
    expires_us: int
    material_sha256: str
    mapper_worker: str
    mapper_instance: str

    def __new__(cls):raise ValueError('bootstrap report requires actual complete LOAD pages')

    @classmethod
    def _complete(cls,body,request,material_sha):
        if len(body)>COMPLETE_BODY_CAP:raise ValueError('bootstrap complete local body cap')
        result=object.__new__(cls)
        for name,value in dict(body=body,request_id=request.job.job_id,issued_us=request.job.issued_us,
                expires_us=request.job.expires_us,material_sha256=material_sha,
                mapper_worker=request.job.context.mapper_worker,
                mapper_instance=request.job.context.origin_mapper_instance).items():object.__setattr__(result,name,value)
        return result


@dataclass(frozen=True,init=False)
class BootstrapIdentityEvidence:
    """현재 S가 원 요청/물리 완료와 현재 M의 semantic 보고를 결합한 자료 사실이다."""
    peer: object
    mapper_instance: str
    database_uuid: str
    bootstrap_sha256: str
    checkpoint_ref: bytes
    material_sha256: str
    expires_us: int
    report_sha256: str

    def __new__(cls):raise ValueError('bootstrap identity needs matched original S and M facts')

    def current(self,bootstrap,peer,mapper_instance,now_us):
        return (self.peer==peer and self.mapper_instance==mapper_instance and now_us<self.expires_us
            and bootstrap.get('database_uuid')==self.database_uuid
            and sha256(canonical(bootstrap)).hexdigest()==self.bootstrap_sha256
            and canonical(bootstrap.get('selected_checkpoint'))==self.checkpoint_ref)


class MapperBootstrap:
    """기존 compute maintenance용 owner. main port의 lock에는 작은 참조만 둔다.

    attach_processor의 선택 accept_bootstrap_source(complete)는 같은 compute thread에서
    한 번 호출한다. 이 hook이 없어도 data COMPLETE이며 지도 사용권을 대신 만들지 않는다.
    """

    def __init__(self,mailbox,*,mapper_worker,mapper_instance,cap_us,clock_us):
        from .mapper_storage import MapperStorageMailbox
        from ..storage.contracts import uuid
        if type(mailbox) is not MapperStorageMailbox or not callable(clock_us):raise ValueError('original M storage mailbox/clock required')
        uuid(mapper_worker);uuid(mapper_instance);uint(cap_us)
        self.mailbox,self.worker,self.instance,self.cap,self.clock=mailbox,mapper_worker,mapper_instance,cap_us,clock_us
        self._lock=Lock();self._material=None;self._out_request=self._out_report=None;self._failure=None;self._cancel=None
        self._original=None;self._request=None;self._first=None;self._pages=None;self._receipts=[]
        self._complete=None;self._processor=None;self._delivered=None;self._empty=False

    @property
    def failure(self):
        with self._lock:return self._failure

    def offer_material(self,material):
        raw=material_bytes(material)
        with self._lock:self._material=raw

    def take_request(self):
        with self._lock:
            result=self._out_request;self._out_request=None;return result

    def take_complete(self):
        with self._lock:
            result=self._out_report;self._out_report=None;return result

    def cancel(self,reason):
        if type(reason) is not str or not 1<=len(reason)<=64:raise ValueError('bounded bootstrap cancellation required')
        with self._lock:self._cancel=reason

    def attach_processor(self,processor):
        self._processor=processor;self._deliver_processor();return processor

    def _deliver_processor(self):
        if self._complete is not None and self._processor is not None and self._delivered is not self._processor:
            hook=getattr(self._processor,'accept_bootstrap_source',None)
            if hook is not None:hook(self._complete);self._delivered=self._processor

    def _begin_page(self,binding,reference,cursor):
        now=self.clock()
        issued=now;expires=min(self.cap,issued+5_000_000)
        if not issued<=now<expires:raise ValueError('original bootstrap LOAD expired')
        request=make_load_request(binding,mapper_worker=self.worker,origin_mapper_instance=self.instance,
            job_id=str(uuid4()),issued_us=issued,expires_us=expires,checkpoint_ref=json_bytes(reference,2048),
            selection=dict(kind='CHECKPOINT_PAGE',cursor=cursor))
        self.mailbox.fence(request.job.context.sha256,now_us=now)
        if not self.mailbox.compute_offer(request,now_us=now):raise ValueError('bootstrap has another original storage owner')
        self._request=request
        if self._first is None:self._first=request
        with self._lock:
            if self._out_request is not None:raise ValueError('one bootstrap request report required')
            self._out_request=request

    def compute_step(self):
        """큰 실제 decode는 여기만 수행한다. STOP/source/expiry는 원 요청을 보존한 실패다."""
        with self._lock:raw,cancel=self._material,self._cancel
        if self._failure is not None or self._empty:return
        try:
            if cancel is not None:raise ValueError('BOOTSTRAP_'+cancel)
            if raw is None:return
            if self._original is not None and raw!=self._original:raise ValueError('bootstrap D source replaced')
            if self._complete is not None:self._deliver_processor();return
            if self._original is None:
                material=json_bytes(raw,2048)
                if material['bootstrap']['status']=='EMPTY':self._empty=True;return
                binding,reference=_selected(raw);self._original=raw
                self._begin_page(binding,reference,0)
                from .map_checkpoint import CheckpointPages
                self._pages=CheckpointPages(reference,self._request.job.context)
                return
            request=self._request;now=self.clock()
            if not request.job.current(request.job.context.sha256,now):raise ValueError('original bootstrap LOAD expired')
            delivery=self.mailbox.compute_take(current_context_sha256=request.job.context.sha256,now_us=now)
            if delivery is None:return
            from ..storage.records import LoadedCheckpoint
            from .storage_copy import OwnedCopy
            if (not delivery.current or delivery.request is not request
                    or type(delivery.admitted_result) is not LoadedCheckpoint
                    or type(delivery.result) is not OwnedCopy):raise ValueError('actual current decoded bootstrap copy required')
            accepted=self.clock()
            complete=self._pages.accept(delivery.admitted_result,now_us=accepted)
            self._receipts.append(dict(job_id=request.job.job_id,job_digest=request.digest,
                context_sha256=request.job.context.sha256,cursor=json_bytes(request.selection,512)['cursor'],
                completed_us=str(delivery.admitted_result.completed_us),accepted_us=str(accepted),
                physical_ready_sha256=_ready_sha(delivery.result.ready),
                catalog_head_sha256=sha256(canonical(asdict(delivery.admitted_result.heads))).hexdigest()))
            if complete is None:
                binding,reference=_selected(raw);self._begin_page(binding,reference,len(self._receipts)*32);return
            # 실제 immutable 자료 전체의 fingerprint다. S는 이 작은 M 보고로 재검산했다고 주장하지 않는다.
            closure=sha256(complete.manifest)
            for tile in complete.tiles:closure.update(canonical(tile.reference));closure.update(tile.payload)
            closure.update(complete.evidence.bytes)
            if complete.state is not None:closure.update(complete.state)
            completed=self.clock()
            if not request.job.current(request.job.context.sha256,completed):raise ValueError('bootstrap closure missed original deadline')
            body=canonical(dict(version=1,material_sha256=sha256(raw).hexdigest(),
                checkpoint_ref=json_bytes(complete.checkpoint_ref,2048),mapper_instance=self.instance,
                pages=self._receipts,closure_sha256=closure.hexdigest(),completed_us=str(completed),
                tile_count=len(complete.tiles),provenance_count=len(complete.evidence.provenance),context_count=len(complete.evidence.contexts)))
            report=BootstrapReport._complete(body,request,sha256(raw).hexdigest())
            with self._lock:
                if self._cancel is not None or self._material!=raw:raise ValueError('bootstrap source fenced during closure')
                self._complete=complete;self._out_report=report
            self._deliver_processor()
        except Exception as error:
            with self._lock:self._failure=type(error).__name__+': '+str(error)[:160]


class SupervisorBootstrap:
    """S의 최대4 원 요청/실제 broker 물리 완료/M semantic 완료 결박이다."""

    def __init__(self,peer,mapper_instance):
        self.peer,self.instance=peer,mapper_instance
        self.requests=[];self.physical={};self.pending=None;self.evidence=None;self.original=None

    def _registered(self,packet,kind):
        from .data_channel import DataPacket
        if type(packet) is not DataPacket or packet.kind!=kind:raise ValueError('original typed bootstrap packet required')
        header=packet.header
        if (header['service_instance'],header['mapper_worker'],header['channel_epoch'],header['sender'],header['metadata'])!=(
                self.peer.service_instance,self.peer.mapper_worker,self.peer.channel_epoch,'M',{}):
            raise ValueError('bootstrap report belongs to another registered M/channel')

    def offer_load(self,packet,material):
        self._registered(packet,'SOURCE_BOOTSTRAP_LOAD')
        raw=material_bytes(material);binding,reference=_selected(raw)
        value=json_bytes(packet.body,LOAD_BODY_CAP)
        if set(value)!={'origin_mapper_instance','checkpoint_ref','selection'} or value['origin_mapper_instance']!=self.instance:
            raise ValueError('bootstrap LOAD changed original M incarnation')
        if canonical(value['checkpoint_ref'])!=reference:raise ValueError('bootstrap LOAD changed selected reference')
        request=make_load_request(binding,mapper_worker=self.peer.mapper_worker,origin_mapper_instance=self.instance,
            job_id=packet.header['request_id'],issued_us=packet.issued_us,expires_us=packet.expires_us,
            checkpoint_ref=value['checkpoint_ref'],selection=value['selection'])
        for old in self.requests:
            if old.job.job_id==request.job.job_id:
                if old!=request:raise ValueError('bootstrap retry changed original request')
                return
        if self.pending is not None:
            if self.pending!=request:raise ValueError('one original bootstrap LOAD pending')
            return
        if self.original is not None and raw!=self.original:raise ValueError('bootstrap material changed during original plan')
        page_count=max(1,(selected_checkpoint_reference(reference)['tile_count']+31)//32)
        if (len(self.requests)>=page_count or value['selection']!={'kind':'CHECKPOINT_PAGE','cursor':len(self.requests)*32}
                or self.requests and packet.issued_us<self.requests[-1].job.issued_us):
            raise ValueError('bootstrap pages changed original order/budget')
        self.original=raw;self.pending=request

    def observe(self,completion,now_us):
        if completion is None:return
        from .resident_db import DbCompletion
        if type(completion) is not DbCompletion:raise ValueError('actual broker completion required')
        for request in self.requests:
            if completion.request_id!=request.job.job_id:continue
            if (completion.request_bytes!=canonical(prepare_payload(request)['body'])
                    or (completion.issued_us,completion.expires_us)!=(request.job.issued_us,request.job.expires_us)
                    or not request.job.issued_us<=completion.received_us<request.job.expires_us
                    or completion.received_us>now_us):
                raise ValueError('bootstrap physical completion changed original request/time')
            ready=completion.result
            if (ready.get('job_id'),ready.get('job_digest'))!=(request.job.job_id,request.digest):
                raise ValueError('bootstrap physical READY differs from original LOAD')
            value=_ready_sha(ready);old=self.physical.get(completion.request_id)
            if old is not None and old!=value:raise ValueError('original physical LOAD completion changed')
            if old is None and now_us>=request.job.expires_us:return
            self.physical[completion.request_id]=value

    def dispatch(self,callback,now_us):
        if self.pending is None:return
        request=self.pending
        if not request.job.current(request.job.context.sha256,now_us):raise ValueError('original bootstrap dispatch expired')
        if self.requests and self.requests[-1].job.job_id not in self.physical:return
        if callback is None or callback(request) is False:return
        self.requests.append(request);self.pending=None

    def accept_complete(self,packet,material,now_us):
        self._registered(packet,'SOURCE_BOOTSTRAP_COMPLETE')
        raw=material_bytes(material);_,reference=_selected(raw);value=json_bytes(packet.body,COMPLETE_BODY_CAP)
        names={'version','material_sha256','checkpoint_ref','mapper_instance','pages','closure_sha256','completed_us',
               'tile_count','provenance_count','context_count'}
        if (type(value) is not dict or set(value)!=names or type(value['version']) is not int or value['version']!=1
                or self.original!=raw or value['material_sha256']!=sha256(raw).hexdigest()
                or canonical(value['checkpoint_ref'])!=reference or value['mapper_instance']!=self.instance
                or not self.requests or self.pending is not None):raise ValueError('complete source has no exact original bootstrap plan')
        selected=selected_checkpoint_reference(reference);last=self.requests[-1]
        expected_count=max(1,(selected['tile_count']+31)//32)
        if (type(value['pages']) is not list or len(value['pages'])!=len(self.requests) or len(self.requests)!=expected_count
                or packet.header['request_id']!=last.job.job_id
                or (packet.issued_us,packet.expires_us)!=(last.job.issued_us,last.job.expires_us)
                or not last.job.issued_us<=decimal(value['completed_us'])<=now_us<last.job.expires_us):
            raise ValueError('complete source omitted pages or renewed original deadline')
        for request,page in zip(self.requests,value['pages']):
            names={'job_id','job_digest','context_sha256','cursor','completed_us','accepted_us','physical_ready_sha256','catalog_head_sha256'}
            if (type(page) is not dict or set(page)!=names or page['job_id']!=request.job.job_id
                    or page['job_digest']!=request.digest or page['context_sha256']!=request.job.context.sha256
                    or type(page['cursor']) is not int or page['cursor']!=json_bytes(request.selection,512)['cursor']
                    or self.physical.get(request.job.job_id)!=page['physical_ready_sha256']
                    or not request.job.issued_us<=decimal(page['completed_us'])<=decimal(page['accepted_us'])<request.job.expires_us
                    or decimal(page['accepted_us'])>decimal(value['completed_us'])):
                raise ValueError('complete source changed original page/physical/semantic facts')
            digest(page['catalog_head_sha256'])
        digest(value['closure_sha256'])
        for name,maximum in (('tile_count',128),('provenance_count',16384),('context_count',64)):
            if type(value[name]) is not int or not 0<=value[name]<=maximum:raise ValueError('complete closure count outside bound')
        if value['tile_count']!=selected['tile_count']:raise ValueError('complete source omitted selected tiles')
        evidence=object.__new__(BootstrapIdentityEvidence)
        for name,item in dict(peer=self.peer,mapper_instance=self.instance,checkpoint_ref=reference,
                database_uuid=material[0]['database_uuid'],bootstrap_sha256=sha256(canonical(material[1])).hexdigest(),
                material_sha256=sha256(raw).hexdigest(),expires_us=last.job.expires_us,
                report_sha256=sha256(packet.body).hexdigest()).items():object.__setattr__(evidence,name,item)
        if self.evidence is not None and self.evidence!=evidence:raise ValueError('one original complete report cannot change')
        self.evidence=evidence;return evidence
