"""기존 DB_COPY arena의 유한 checkpoint/LOAD codec과 token 취득·반환.

S가 full lease를 발급한다. 이 helper는 IPC를 생성/unlink하거나 S 원장을 대신하지 않는다.
STORAGE SLOT의 producer/consumer는 channel+worker Endpoint object이며 RAW SLOT은 바꾸지 않는다.
LOAD metadata는 legacy exact8 또는 원 commit descriptor를 포함한 exact9다. CHECKPOINT와
기존 prefix/영역/제어 packet은 그대로이며 descriptor 부재를 원 commit 증명으로 채우지 않는다.
"""

from dataclasses import asdict,dataclass,field,replace
from hashlib import sha256
import struct
from uuid import UUID
import zlib

from .native_resources import ArenaSpec,DB_COPY_BYTES
from .raw_leases import Endpoint
from .resident_transport import j_us
from ..storage.contracts import (CatalogHeadSnapshot,LoadRequest,PersistenceError,StorageJob,
    canonical,digest,json_bytes,uint,uuid)
from ..storage.port import CheckpointRequest
from ..storage.records import CheckpointCommitOrigin,CheckpointPayload,EvidenceTable,LoadedCheckpoint,TileVersion


TILE_REGION=8*1024*1024
EVIDENCE_OFFSET=TILE_REGION
EVIDENCE_REGION=5*1024*1024
DESCRIPTOR_OFFSET=13*1024*1024
MANIFEST_OFFSET=DESCRIPTOR_OFFSET+4096
ROW=struct.Struct('<16siiQIIHH32sI16s32s')
PREFIX=struct.Struct('<8s8I32s32s32s120s')
MAGIC=b'XNVDBC01'
_META_CAP=16384


def _endpoint(value):
    if type(value) is not dict or set(value)!= {'channel_epoch','worker_instance'}:raise PersistenceError('copy endpoint fields')
    return Endpoint(value['channel_epoch'],value['worker_instance'])


@dataclass(frozen=True,slots=True)
class CopyLease:
    service_instance: str
    arena_id: str
    generation: int
    producer: Endpoint
    consumer: Endpoint
    write_token: str
    read_token: str | None=None
    raw_binding_sha256: str | None=None
    publication_binding_sha256: str | None=None

    def __post_init__(self):
        for value in (self.service_instance,self.arena_id,self.write_token):uuid(value)
        uint(self.generation,(1<<64)-1,positive=True)
        if type(self.producer) is not Endpoint or type(self.consumer) is not Endpoint or self.producer==self.consumer:
            raise PersistenceError('distinct registered copy endpoints required')
        if self.read_token is not None:uuid(self.read_token)
        for value in (self.raw_binding_sha256,self.publication_binding_sha256):
            if value is not None:digest(value)

    @property
    def fields(self):
        return dict(service_instance=self.service_instance,arena_id=self.arena_id,slot=0,generation=str(self.generation),
            producer=asdict(self.producer),consumer=asdict(self.consumer),write_token=self.write_token,read_token=self.read_token,
            raw_binding_sha256=self.raw_binding_sha256,publication_binding_sha256=self.publication_binding_sha256)

    @property
    def writer(self):return replace(self,read_token=None)

    @property
    def writer_sha256(self):return sha256(canonical(self.writer.fields)).hexdigest()

    @classmethod
    def from_fields(cls,value):
        names={'service_instance','arena_id','slot','generation','producer','consumer','write_token','read_token',
               'raw_binding_sha256','publication_binding_sha256'}
        if type(value) is not dict or set(value)!=names or type(value['slot']) is not int or value['slot']!=0:
            raise PersistenceError('exact DB_COPY full key required')
        fields=dict(value);fields.pop('slot');generation=fields['generation']
        if type(generation) is not str or not generation.isascii() or not generation.isdecimal() or str(int(generation))!=generation:
            raise PersistenceError('copy generation must be canonical u64')
        fields['generation']=int(generation)
        for name in ('producer','consumer'):fields[name]=_endpoint(fields[name])
        return cls(**fields)

    def slot_payload(self,op,*,payload_length=None,descriptor_sha256=None):
        if op not in ('GRANT','FILLED','ACQUIRED','RELEASE','REVOKE'):raise PersistenceError('unsupported DB_COPY SLOT operation')
        return dict(op=op,arena_id=self.arena_id,slot=0,generation=str(self.generation),write_token=self.write_token,
            read_token=self.read_token,producer=asdict(self.producer),consumer=asdict(self.consumer),
            raw_binding_sha256=self.raw_binding_sha256,publication_binding_sha256=self.publication_binding_sha256,
            purpose=None,payload_length=payload_length,descriptor_sha256=descriptor_sha256)

    @classmethod
    def from_slot(cls,service_instance,payload):
        names={'op','arena_id','slot','generation','write_token','read_token','producer','consumer',
               'raw_binding_sha256','publication_binding_sha256','purpose','payload_length','descriptor_sha256'}
        if type(payload) is not dict or set(payload)!=names or payload['purpose'] is not None:raise PersistenceError('DB_COPY SLOT fields')
        fields={name:payload[name] for name in names-{'op','purpose','payload_length','descriptor_sha256'}}
        return cls.from_fields(dict(service_instance=service_instance,**fields))


@dataclass(frozen=True,slots=True)
class CopyEncoding:
    lease: CopyLease
    job_id: str
    job_digest: str
    database_uuid: str
    tiles: tuple
    evidence: bytes
    descriptors: bytes
    manifest: bytes
    provenance_count: int
    context_count: int
    _ready_bytes: bytes=field(init=False,repr=False,compare=False)

    def __post_init__(self):
        if type(self.lease) is not CopyLease or self.lease.read_token is not None:raise PersistenceError('copy encoding requires writer projection')
        uuid(self.job_id);uuid(self.database_uuid);digest(self.job_digest)
        if (type(self.tiles) is not tuple or len(self.tiles)>32 or any(type(tile) is not bytes or len(tile)!=262144 for tile in self.tiles)
                or type(self.evidence) is not bytes or not 256<=len(self.evidence)<=EVIDENCE_REGION
                or type(self.descriptors) is not bytes or len(self.descriptors)!=4096
                or type(self.manifest) is not bytes or not 0<len(self.manifest)<=16384):raise PersistenceError('copy encoding region bounds')
        uint(self.provenance_count,16384);uint(self.context_count,64)
        object.__setattr__(self,'_ready_bytes',canonical(dict(database_uuid=self.database_uuid,job_id=self.job_id,job_digest=self.job_digest,
            copy_lease=self.lease.fields,manifest_length=len(self.manifest),manifest_sha256=sha256(self.manifest).hexdigest(),
            provenance_length=len(self.evidence),provenance_sha256=sha256(self.evidence).hexdigest(),
            provenance_count=self.provenance_count,context_count=self.context_count)))

    @property
    def ready(self):
        return json_bytes(self._ready_bytes,3500)

    @property
    def descriptor_sha256(self):return sha256(self.descriptors).hexdigest()

    @property
    def used_bytes(self):return sum(len(tile) for tile in self.tiles)+len(self.evidence)+4096+len(self.manifest)


def _encode(lease,job,payload,job_digest,metadata):
    if type(lease) is not CopyLease or lease.read_token is not None:raise PersistenceError('original writer lease required')
    meta=canonical(metadata)
    if len(meta)>_META_CAP:raise PersistenceError('copy job metadata cap')
    table=payload.evidence.bytes
    prefix=PREFIX.pack(MAGIC,1,1 if metadata['direction']=='CHECKPOINT' else 2,len(meta),len(table),len(payload.tiles),
        len(payload.manifest),len(payload.evidence.provenance),len(payload.evidence.contexts),
        sha256(meta).digest(),sha256(table).digest(),bytes.fromhex(lease.writer_sha256),bytes(120))
    evidence=prefix+meta+table
    if len(evidence)>EVIDENCE_REGION:raise PersistenceError('copy proof/context/job metadata exceeds5MiB')
    descriptors=[]
    for index,tile in enumerate(payload.tiles):
        descriptors.append(ROW.pack(UUID(tile.map_epoch).bytes,tile.tile_x,tile.tile_y,tile.revision,index*262144,262144,1,0,
            bytes.fromhex(tile.sha256),tile.crc32,UUID(job.job_id).bytes,bytes(32)))
    return CopyEncoding(lease,job.job_id,job_digest,job.context.database_uuid,tuple(tile.payload for tile in payload.tiles),
        evidence,b''.join(descriptors).ljust(4096,b'\0'),payload.manifest,len(payload.evidence.provenance),len(payload.evidence.contexts))


def encode_checkpoint(request,lease):
    if type(request) is not CheckpointRequest:raise PersistenceError('exact checkpoint request required')
    return _encode(lease,request.job,request.payload,request.digest,dict(direction='CHECKPOINT',job=request.job.fields,
        original_job=None,checkpoint_ref=None,selection=None,purpose=None,heads=None,completed_us=None))


def encode_load(result,lease):
    if type(result) is not LoadedCheckpoint:raise PersistenceError('exact original LOAD response required')
    request=result.request
    metadata=dict(direction='LOAD',job=request.job.fields,
        original_job=result.original_job.fields,checkpoint_ref=json_bytes(request.checkpoint_ref,2048),
        selection=json_bytes(request.selection,512),purpose=request.purpose,heads=asdict(result.heads),completed_us=result.completed_us)
    if result.original_commit is not None:metadata['original_commit']=result.original_commit.fields
    return _encode(lease,request.job,result.payload,request.digest,metadata)


def decode_copy(lease,ready,evidence,descriptors,manifest,tiles):
    if type(lease) is not CopyLease or lease.read_token is None:raise PersistenceError('actual read lease required')
    if (type(evidence) is not bytes or not 256<=len(evidence)<=EVIDENCE_REGION or type(descriptors) is not bytes or len(descriptors)!=4096
            or type(manifest) is not bytes or len(manifest)>16384 or type(tiles) is not tuple or len(tiles)>32):
        raise PersistenceError('bounded owned DB_COPY regions required')
    magic,version,direction,ml,el,nt,nm,np,nc,mh,eh,wh,pad=PREFIX.unpack_from(evidence)
    if (magic!=MAGIC or version!=1 or direction not in (1,2) or ml>_META_CAP or el>5*1024*1024 or nt>32 or nm>16384
            or np>16384 or nc>64 or any(pad) or len(evidence)!=256+ml+el or nt!=len(tiles) or nm!=len(manifest)
            or wh.hex()!=lease.writer_sha256):raise PersistenceError('DB_COPY prefix/source/count mismatch')
    meta_raw=evidence[256:256+ml];table_raw=evidence[256+ml:]
    if sha256(meta_raw).digest()!=mh or sha256(table_raw).digest()!=eh:raise PersistenceError('DB_COPY metadata/evidence hash')
    metadata=json_bytes(meta_raw,_META_CAP)
    names={'direction','job','original_job','checkpoint_ref','selection','purpose','heads','completed_us'}
    if (type(metadata) is not dict or set(metadata)!=names
            and not (direction==2 and set(metadata)==names|{'original_commit'})):
        raise PersistenceError('DB_COPY metadata exact fields')
    job=StorageJob.from_fields(metadata['job'])
    original=job if direction==1 else StorageJob.from_fields(metadata['original_job'])
    versions=[]
    for index,raw in enumerate(tiles):
        row=ROW.unpack_from(descriptors,index*128)
        epoch,x,y,revision,offset,length,record_version,reserved,key,crc,job_id,padding=row
        if (type(raw) is not bytes or len(raw)!=262144 or offset!=index*262144 or length!=262144 or record_version!=1
                or reserved or any(padding) or job_id!=UUID(job.job_id).bytes or sha256(raw).digest()!=key or zlib.crc32(raw)!=crc):
            raise PersistenceError('DB_COPY tile descriptor/hash/CRC mismatch')
        versions.append(TileVersion(str(UUID(bytes=epoch)),x,y,revision,raw))
    if any(descriptors[nt*128:]):raise PersistenceError('unused DB_COPY descriptor rows must be zero')
    table=EvidenceTable.decode(table_raw,original)
    if (np,nc)!=(len(table.provenance),len(table.contexts)):raise PersistenceError('DB_COPY proof counts differ')
    payload=CheckpointPayload(manifest,tuple(versions),table)
    if direction==1:
        if metadata['direction']!='CHECKPOINT' or any(metadata[key] is not None for key in ('original_job','checkpoint_ref','selection','purpose','heads','completed_us')):
            raise PersistenceError('checkpoint copy contains LOAD metadata')
        result=CheckpointRequest(job,payload);job_digest=result.digest
    else:
        if metadata['direction']!='LOAD':raise PersistenceError('LOAD direction mismatch')
        request=LoadRequest(job,canonical(metadata['checkpoint_ref']),canonical(metadata['selection']),metadata['purpose'])
        head=dict(metadata['heads']);head['heads']=tuple(tuple(item) for item in head['heads'])
        origin=None if 'original_commit' not in metadata else CheckpointCommitOrigin.from_fields(metadata['original_commit'])
        result=LoadedCheckpoint(request,original,request.checkpoint_ref,payload,CatalogHeadSnapshot(**head),metadata['completed_us'],origin)
        job_digest=request.digest
    expected=dict(database_uuid=job.context.database_uuid,job_id=job.job_id,job_digest=job_digest,copy_lease=lease.fields,
        manifest_length=len(manifest),manifest_sha256=sha256(manifest).hexdigest(),provenance_length=len(evidence),
        provenance_sha256=sha256(evidence).hexdigest(),provenance_count=np,context_count=nc)
    if ready!=expected:raise PersistenceError('READY differs from original owned copy/job')
    return result


@dataclass(frozen=True,slots=True)
class CopyReturned:
    lease: CopyLease
    views_ended_us: int
    posted_us: int


@dataclass(frozen=True,slots=True)
class OwnedCopy:
    """native view 없는 원 사본. physical 반환만 뜻하며 LOAD/COMMIT/source 검산 성공은 아니다."""
    lease: CopyLease
    ready_bytes: bytes
    evidence: bytes
    descriptors: bytes
    manifest: bytes
    tiles: tuple

    def __post_init__(self):
        if (type(self.lease) is not CopyLease or self.lease.read_token is None
                or type(self.ready_bytes) is not bytes or len(self.ready_bytes)>3500
                or type(self.evidence) is not bytes or not 256<=len(self.evidence)<=EVIDENCE_REGION
                or type(self.descriptors) is not bytes or len(self.descriptors)!=4096
                or type(self.manifest) is not bytes or len(self.manifest)>16384
                or type(self.tiles) is not tuple or len(self.tiles)>32
                or any(type(tile) is not bytes or len(tile)!=262144 for tile in self.tiles)):
            raise PersistenceError('immutable bounded physical DB copy required')

    @property
    def ready(self):return json_bytes(self.ready_bytes,3500)

    def decode(self):
        """M-compute 또는 D owner가 원 hash/source/payload를 검산한다. 현재성은 원 job 소비자가 검사한다."""
        return decode_copy(self.lease,self.ready,self.evidence,self.descriptors,self.manifest,self.tiles)


class CopyArena:
    """spawn으로 받은 한 pair만 사용한다. caller는 S의 실제 grant/lease registry를 먼저 확인한다."""

    def __init__(self,spec,tokens,local_endpoint,service_instance,*,clock_us=j_us):
        from multiprocessing import shared_memory
        from multiprocessing.synchronize import Semaphore
        if type(spec) is not ArenaSpec or spec.role!='DB_COPY' or spec.size_bytes!=DB_COPY_BYTES:
            raise PersistenceError('registered existing DB_COPY arena required')
        if type(tokens) is not tuple or len(tokens)!=2 or any(type(token) is not Semaphore for token in tokens):
            raise PersistenceError('one actual spawn semaphore pair required')
        if local_endpoint is not None and type(local_endpoint) is not Endpoint:raise PersistenceError('registered local endpoint required')
        uuid(service_instance)
        self.spec=spec;self.tokens=tokens;self.endpoint=local_endpoint;self.service=service_instance;self.clock=clock_us
        self.shm=shared_memory.SharedMemory(name=spec.name[1:],create=False)
        if self.shm.size!=DB_COPY_BYTES:self.shm.close();raise PersistenceError('actual copy arena size mismatch')
        self.closed=False;self.seen_write=0;self.seen_read=0;self.held=False

    def _lease(self,lease):
        if (self.closed or type(lease) is not CopyLease or lease.service_instance!=self.service or lease.arena_id!=self.spec.arena_id):
            raise PersistenceError('copy lease differs from registered live arena')

    def _owned(self,offset,length):
        view=self.shm.buf[offset:offset+length]
        try:return bytes(view)
        finally:view.release()

    def _write(self,offset,value):
        view=self.shm.buf[offset:offset+len(value)]
        try:view[:]=value
        finally:view.release()

    def publish(self,lease,encoding,*,expires_us):
        self._lease(lease)
        if (type(encoding) is not CopyEncoding or encoding.lease!=lease or lease.read_token is not None
                or lease.producer!=self.endpoint or lease.generation<=self.seen_write or self.clock()>=expires_us):
            raise PersistenceError('new original timely copy writer grant required')
        if not self.tokens[0].acquire(False):raise PersistenceError('DB writable token missing')
        self.held=True;self.seen_write=lease.generation
        for index,tile in enumerate(encoding.tiles):self._write(index*262144,tile)
        self._write(EVIDENCE_OFFSET,encoding.evidence);self._write(DESCRIPTOR_OFFSET,encoding.descriptors)
        self._write(MANIFEST_OFFSET,encoding.manifest)
        if self.clock()>=expires_us:raise PersistenceError('original copy write deadline expired before ready post')
        self.tokens[1].release();self.held=False
        return encoding.ready

    def copy_raw_owned(self,lease,ready,*,expires_us):
        """유한 physical copy만 수행한다. 큰 payload의 hash/semantic decode는 반환 뒤 compute 책임이다."""
        self._lease(lease)
        if (lease.read_token is None or lease.consumer!=self.endpoint or lease.generation<=self.seen_read or self.clock()>=expires_us):
            raise PersistenceError('new original timely copy reader lease required')
        if not self.tokens[1].acquire(False):raise PersistenceError('DB ready token missing')
        self.held=True;self.seen_read=lease.generation
        try:
            header=self._owned(EVIDENCE_OFFSET,256)
            values=PREFIX.unpack(header)
            _,_,_,ml,el,nt,nm,np,nc,*_=values
            if ml>_META_CAP or el>EVIDENCE_REGION-256-ml or nt>32 or nm>16384 or np>16384 or nc>64:
                raise PersistenceError('copy prefix exceeds original arena bounds')
            evidence=self._owned(EVIDENCE_OFFSET,256+ml+el);descriptors=self._owned(DESCRIPTOR_OFFSET,4096)
            manifest=self._owned(MANIFEST_OFFSET,nm);tiles=tuple(self._owned(index*262144,262144) for index in range(nt))
        finally:
            ended=self.clock();self.tokens[0].release();posted=self.clock();self.held=False
        return OwnedCopy(lease,canonical(ready),evidence,descriptors,manifest,tiles),CopyReturned(lease,ended,posted)

    def copy_owned(self,lease,ready,*,expires_us):
        """기존 D API. native 반환을 먼저 마친 뒤 원 semantic decode를 실행한다."""
        owned,returned=self.copy_raw_owned(lease,ready,expires_us=expires_us)
        return owned.decode(),returned

    def discard_ready(self,lease):
        """S가 READY·미claim을 확정한 원본만 cleanup한다. 실행 중 reader/writer의 회수 API가 아니다."""
        self._lease(lease)
        if self.endpoint is not None or lease.read_token is not None:raise PersistenceError('sole S READY cleanup requires writer projection')
        if not self.tokens[1].acquire(False):raise PersistenceError('unclaimed READY token missing')
        if self.tokens[0].acquire(False):raise PersistenceError('inconsistent DB token pair requires service')
        ended=self.clock();self.tokens[0].release()
        return CopyReturned(lease,ended,self.clock())

    def close(self):
        if self.held:raise PersistenceError('copy token/view owner not released; parent must confirm exit')
        if not self.closed:self.closed=True;self.shm.close()
