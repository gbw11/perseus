"""D 영속 job의 불변 값. SQL·fsync·IPC 자원 생성이나 scene/출력 승인은 하지 않는다."""

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from uuid import UUID


MAX_U63=(1<<63)-1
MAX_FIXED32_END=1<<32
JOB_CAP_US=5_000_000


class PersistenceError(ValueError):pass
class PersistenceConflict(PersistenceError):pass
class PersistenceUnknown(RuntimeError):pass
class PersistenceCorrupt(PersistenceError):pass


def uint(value,maximum=MAX_U63,*,positive=False):
    if type(value) is not int or not int(positive)<=value<=maximum:raise PersistenceError('bounded builtin unsigned integer required')
    return value


def uuid(value):
    if type(value) is not str:raise PersistenceError('non-nil canonical UUID required')
    try:parsed=UUID(value)
    except ValueError as error:raise PersistenceError('non-nil canonical UUID required') from error
    if str(parsed)!=value or parsed.int==0:raise PersistenceError('non-nil canonical UUID required')
    return value


def decimal(value,*,positive=False):
    if (type(value) is not str or not 1<=len(value)<=20 or not value.isascii() or not value.isdecimal()
            or str(int(value))!=value):raise PersistenceError('canonical decimal string required')
    return uint(int(value),positive=positive)


def digest(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise PersistenceError('lowercase SHA256 required')
    return value


def text(value):
    if type(value) is not str or not 1<=len(value)<=64 or any(not 33<=ord(c)<=126 for c in value):
        raise PersistenceError('bounded printable ASCII identity required')
    return value


def _normal(value,depth=0):
    if depth>12:raise PersistenceError('canonical JSON depth cap')
    if value is None or type(value) is bool:return value
    if type(value) is int:
        if not -MAX_U63<=value<=MAX_U63:raise PersistenceError('canonical integer range')
        return value
    if type(value) is float:
        if not math.isfinite(value):raise PersistenceError('nonfinite JSON number')
        return 0. if value==0 else value
    if type(value) is str:
        if not value.isascii():raise PersistenceError('ASCII JSON required')
        return value
    if type(value) in (list,tuple):
        if len(value)>512:raise PersistenceError('canonical sequence cap')
        return [_normal(item,depth+1) for item in value]
    if type(value) is dict:
        if len(value)>128 or any(type(key) is not str or not key.isascii() for key in value):
            raise PersistenceError('canonical object cap/keys')
        return {key:_normal(item,depth+1) for key,item in value.items()}
    raise PersistenceError('unsupported canonical JSON value')


def canonical(value):
    return json.dumps(_normal(value),sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')


def json_bytes(value,cap):
    if type(value) is not bytes or len(value)>cap:raise PersistenceError('bounded canonical bytes required')
    def pairs(items):
        result={}
        for key,item in items:
            if key in result:raise PersistenceError('duplicate JSON key')
            result[key]=item
        return result
    try:
        parsed=json.loads(value,object_pairs_hook=pairs,parse_constant=lambda value:(_ for _ in ()).throw(PersistenceError('nonfinite JSON')))
    except (UnicodeError,json.JSONDecodeError,RecursionError) as error:
        raise PersistenceError('invalid canonical JSON') from error
    if canonical(parsed)!=value:raise PersistenceError('noncanonical JSON bytes')
    return parsed


@dataclass(frozen=True,slots=True)
class StorageContext:
    """호출자가 고정한 원 binding. 정상 frame/registration/time-lease 진전을 source 교체로 만들지 않는다.

    source_binding은 승인된 PublicationBinding 또는 pre-publication LOAD의 원 provisional
    request context다. constructor의 hash는 원 사본 식별이며 source/센서 사용권 승인이 아니다.
    """
    database_uuid: str
    mapper_worker: str
    origin_mapper_instance: str
    map_epoch: str
    frame_epoch: str
    odom_epoch: str
    pi_host_boot: str
    configuration: str
    calibration: str
    primitive_sha256: str
    source_binding: bytes
    geometry_origin: bytes | dict | None=None

    def __post_init__(self):
        for name in ('database_uuid','mapper_worker','origin_mapper_instance','map_epoch','frame_epoch','odom_epoch','pi_host_boot'):
            uuid(getattr(self,name))
        text(self.configuration);text(self.calibration);digest(self.primitive_sha256)
        if type(json_bytes(self.source_binding,2048)) is not dict:raise PersistenceError('original source binding object required')
        if self.geometry_origin is not None:
            from .geometry_scope import PiGeometryOrigin
            raw=canonical(self.geometry_origin) if type(self.geometry_origin) is dict else self.geometry_origin
            origin=PiGeometryOrigin.from_fields(json_bytes(raw,1024))
            if (origin.pi_host_boot,origin.odom_epoch,origin.calibration)!=(self.pi_host_boot,self.odom_epoch,self.calibration):
                raise PersistenceError('geometry origin differs from original Pi boot/odom/calibration')
            object.__setattr__(self,'geometry_origin',raw)
        if len(self.bytes)>4096:raise PersistenceError('storage context exceeds4096 bytes')

    @property
    def fields(self):
        fields={**{name:getattr(self,name) for name in self.__dataclass_fields__ if name not in ('source_binding','geometry_origin')},
                'source_binding':json_bytes(self.source_binding,2048)}
        if self.geometry_origin is not None:fields['geometry_origin']=self.geometry_origin_fields
        return fields

    @property
    def geometry_origin_fields(self):return None if self.geometry_origin is None else json_bytes(self.geometry_origin,1024)

    @property
    def bytes(self):return canonical(self.fields)

    @property
    def sha256(self):return sha256(self.bytes).hexdigest()

    @classmethod
    def from_fields(cls,value):
        old=set(cls.__dataclass_fields__)-{'geometry_origin'}
        if type(value) is not dict or set(value) not in (old,old|{'geometry_origin'}):raise PersistenceCorrupt('stored context fields')
        if 'geometry_origin' in value and type(value['geometry_origin']) is not dict:raise PersistenceCorrupt('explicit geometry origin must be a versioned object')
        fields=dict(value);fields['source_binding']=canonical(fields['source_binding'])
        return cls(**fields)


@dataclass(frozen=True,slots=True)
class StorageJob:
    context: StorageContext
    job_id: str
    issued_us: int
    expires_us: int
    scene_commit_nonce: str | None = None

    def __post_init__(self):
        if type(self.context) is not StorageContext:raise PersistenceError('exact storage context required')
        uuid(self.job_id);uint(self.issued_us);uint(self.expires_us)
        if not self.issued_us<self.expires_us<=self.issued_us+JOB_CAP_US:raise PersistenceError('original DB job lifetime exceeds five seconds')
        if self.scene_commit_nonce is not None:uuid(self.scene_commit_nonce)

    @property
    def fields(self):
        return dict(context=self.context.fields,job_id=self.job_id,issued_us=str(self.issued_us),
                    expires_us=str(self.expires_us),scene_commit_nonce=self.scene_commit_nonce)

    def digest(self,payload):
        return sha256(canonical(dict(job=self.fields,payload_sha256=payload.sha256))).hexdigest()

    def current(self,context_sha256,now_us):
        return context_sha256==self.context.sha256 and self.issued_us<=uint(now_us)<self.expires_us

    @classmethod
    def from_fields(cls,value):
        if type(value) is not dict or set(value)!= {'context','job_id','issued_us','expires_us','scene_commit_nonce'}:
            raise PersistenceCorrupt('stored job fields')
        return cls(StorageContext.from_fields(value['context']),value['job_id'],decimal(value['issued_us']),
                   decimal(value['expires_us']),value['scene_commit_nonce'])


@dataclass(frozen=True,slots=True)
class CatalogReservation:
    database_uuid: str
    map_epoch: str
    kind: str
    expected_hwm: int
    start_inclusive: int
    end_exclusive: int
    reservation_nonce: str
    mapper_worker: str
    context_sha256: str

    def __post_init__(self):
        for value in (self.database_uuid,self.map_epoch,self.reservation_nonce,self.mapper_worker):uuid(value)
        digest(self.context_sha256)
        if self.kind not in ('LANE','SLOT'):raise PersistenceError('catalog kind must be LANE/SLOT')
        for value in (self.expected_hwm,self.start_inclusive,self.end_exclusive):uint(value,MAX_FIXED32_END,positive=True)
        if self.start_inclusive!=self.expected_hwm or not self.start_inclusive<self.end_exclusive:
            raise PersistenceError('catalog range must start at its exact expected highwater')

    def local_fields(self):
        return dict(kind=self.kind,expected_hwm=str(self.expected_hwm),start_inclusive=str(self.start_inclusive),
                    end_exclusive=str(self.end_exclusive),reservation_nonce=self.reservation_nonce)

    def matches(self,job):
        context=job.context
        return (self.database_uuid,self.map_epoch,self.mapper_worker,self.context_sha256)==(
            context.database_uuid,context.map_epoch,context.mapper_worker,context.sha256)


@dataclass(frozen=True,slots=True)
class CommitReceipt:
    """원 transaction의 불변 receipt.

    committed_j_us는 COMMIT 직전 sampled commit stamp이며 fsync 반환 관측시각이 아니다.
    현재 성공은 실제 COMMIT 반환/원 outer 송신과 수신 actual-now/원 expiry를 함께 요구한다.
    """
    database_uuid: str
    job_id: str
    job_digest: str
    status: str
    durable_revision: int | None
    committed_j_us: int | None
    origin_context_sha256: str

    def __post_init__(self):
        uuid(self.database_uuid);uuid(self.job_id);digest(self.job_digest);digest(self.origin_context_sha256)
        if self.status not in ('COMMITTED','DUPLICATE','ABSENT','UNKNOWN','FAILED'):raise PersistenceError('unknown DB result status')
        if self.status in ('COMMITTED','DUPLICATE'):
            uint(self.durable_revision,positive=True);uint(self.committed_j_us)
        elif self.durable_revision is not None or self.committed_j_us is not None:
            raise PersistenceError('uncommitted result cannot carry a durable revision/time')

    @property
    def fields(self):
        return dict(database_uuid=self.database_uuid,job_id=self.job_id,job_digest=self.job_digest,status=self.status,
            durable_revision=None if self.durable_revision is None else str(self.durable_revision),
            committed_j_us=None if self.committed_j_us is None else str(self.committed_j_us),origin_context_sha256=self.origin_context_sha256)

    @classmethod
    def from_fields(cls,value):
        names=('database_uuid','job_id','job_digest','status','durable_revision','committed_j_us','origin_context_sha256')
        if type(value) is not dict or set(value)!=set(names):raise PersistenceCorrupt('invalid stored receipt fields')
        converted=dict(value)
        for name in ('durable_revision','committed_j_us'):
            raw=converted[name]
            if raw is not None:
                if type(raw) is not str or not raw.isascii() or not raw.isdecimal() or str(int(raw))!=raw:
                    raise PersistenceCorrupt('invalid stored receipt integer')
                converted[name]=int(raw)
        return cls(**converted)

    def current(self,job,payload,context_sha256,now_us):
        return (self.status in ('COMMITTED','DUPLICATE') and job.current(context_sha256,now_us)
            and (self.database_uuid,self.job_id,self.job_digest,self.origin_context_sha256)==(
                job.context.database_uuid,job.job_id,job.digest(payload),job.context.sha256)
            and job.issued_us<=self.committed_j_us<=now_us<job.expires_us)


@dataclass(frozen=True,slots=True)
class CatalogHeadSnapshot:
    job_id: str
    context_sha256: str
    database_uuid: str
    map_epoch: str
    heads: tuple
    observed_us: int
    expires_us: int

    def __post_init__(self):
        uuid(self.job_id);digest(self.context_sha256);uuid(self.database_uuid);uuid(self.map_epoch)
        uint(self.observed_us);uint(self.expires_us)
        if type(self.heads) is not tuple or len(self.heads)!=2 or tuple(item[0] for item in self.heads)!=('LANE','SLOT'):
            raise PersistenceError('both current catalog heads required')
        for kind,hwm,revision in self.heads:uint(hwm,MAX_FIXED32_END,positive=True);uint(revision)

    def current(self,job,context_sha256,now_us):
        return (job.current(context_sha256,now_us) and self.job_id==job.job_id
            and self.context_sha256==job.context.sha256 and self.database_uuid==job.context.database_uuid
            and self.map_epoch==job.context.map_epoch and self.expires_us==job.expires_us
            and job.issued_us<=self.observed_us<=now_us)


@dataclass(frozen=True,slots=True)
class LoadRequest:
    job: StorageJob
    checkpoint_ref: bytes
    selection: bytes
    purpose: str = 'CURRENT_MAP'

    def __post_init__(self):
        if type(self.job) is not StorageJob or self.purpose not in ('CURRENT_MAP','BOOTSTRAP_RESTORE'):
            raise PersistenceError('original bounded LOAD job/purpose required')
        reference=json_bytes(self.checkpoint_ref,2048)
        names={'checkpoint_job_id','durable_revision','manifest_sha256','manifest_length','tile_count',
               'map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration',
               'primitive_sha256','origin_mapper_instance'}
        if type(reference) is not dict or set(reference)!=names:raise PersistenceError('exact selected checkpoint reference required')
        for name in ('checkpoint_job_id','map_epoch','frame_epoch','odom_epoch','pi_host_boot','origin_mapper_instance'):uuid(reference[name])
        decimal(reference['durable_revision'],positive=True);digest(reference['manifest_sha256']);digest(reference['primitive_sha256'])
        uint(reference['manifest_length'],16384,positive=True);uint(reference['tile_count'],128)
        text(reference['configuration']);text(reference['calibration'])
        selection=json_bytes(self.selection,512)
        if type(selection) is not dict or selection.get('kind') not in ('CHECKPOINT_PAGE','TILE'):
            raise PersistenceError('LOAD selection must be a checkpoint page or exact tile')
        if selection['kind']=='CHECKPOINT_PAGE':
            if set(selection)!= {'kind','cursor'}:raise PersistenceError('page selection fields')
            uint(selection['cursor'],127)
        else:
            if set(selection)!= {'kind','map_epoch','tile_x','tile_y','tile_revision'}:raise PersistenceError('tile selection fields')
            uuid(selection['map_epoch']);decimal(selection['tile_revision'],positive=True)
            for key in ('tile_x','tile_y'):
                if type(selection[key]) is not int or not -(1<<31)<=selection[key]<(1<<31):raise PersistenceError('tile selection coordinate')

    @property
    def digest(self):
        return sha256(canonical(dict(job=self.job.fields,checkpoint_ref=json_bytes(self.checkpoint_ref,2048),
            selection=json_bytes(self.selection,512),purpose=self.purpose))).hexdigest()


@dataclass(frozen=True,slots=True)
class ProvenanceRange:
    original_job: StorageJob
    reservation_nonce: str
    start_inclusive: int
    end_exclusive: int
    highwater_after: int
    committed_j_us: int
    result: str='GRANTED'

    def __post_init__(self):
        uuid(self.reservation_nonce)
        if type(self.original_job) is not StorageJob or self.result not in ('GRANTED','REPLAY'):raise PersistenceError('original provenance range job required')
        for value in (self.start_inclusive,self.end_exclusive,self.highwater_after):uint(value,positive=True)
        uint(self.committed_j_us)
        if not self.start_inclusive<self.end_exclusive==self.highwater_after:raise PersistenceError('invalid durable provenance range')

    @property
    def fields(self):
        return dict(database_uuid=self.original_job.context.database_uuid,reservation_nonce=self.reservation_nonce,
            mapper_worker=self.original_job.context.mapper_worker,start_inclusive=str(self.start_inclusive),
            end_exclusive=str(self.end_exclusive),highwater_after=str(self.highwater_after),result=self.result)

    def current(self,job,context_sha256,now_us):
        return (job==self.original_job and job.current(context_sha256,now_us)
                and job.issued_us<=self.committed_j_us<=now_us<job.expires_us)


@dataclass(frozen=True,slots=True)
class ProvenanceRequest:
    job: StorageJob
    reservation_nonce: str
    block_count: int=1<<20

    def __post_init__(self):
        if type(self.job) is not StorageJob:raise PersistenceError('original provenance job required')
        uuid(self.reservation_nonce);uint(self.block_count,1<<20,positive=True)

    @property
    def digest(self):
        return sha256(canonical(dict(job=self.job.fields,reservation_nonce=self.reservation_nonce,block_count=self.block_count))).hexdigest()
