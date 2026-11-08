"""tile-record-1/provenance-1과 catalog local-profile1의 실제 유한 bytes.

catalog는32B header+224B body의256B record, 전체1KiB header+80KiB다.
이 local payload는 기존 DB_JOB 제어 fields/op를 바꾸지 않으며 저장이 품질 승인은 아니다.
"""

from dataclasses import dataclass,field
from hashlib import sha256
import math
import struct
import zlib

from .contracts import (CatalogHeadSnapshot,CatalogReservation,CommitReceipt,LoadRequest,MAX_U63,PersistenceError,StorageJob,
                        canonical,digest,json_bytes,selected_checkpoint_reference,uint,uuid)


TILE_BYTES=64*64*64
EVIDENCE_CAP=5*1024*1024
CELL=struct.Struct('<BBHfffQQQQQQ')
PROVENANCE=struct.Struct('<32sBBBB16sQQQ4Q128sI16s')
REGISTRATION=struct.Struct('<QQ16s16s16s5fI16s6f')
CATALOG=struct.Struct('<BBHIQQQ')
TABLE_HEADER=struct.Struct('<8s6I32s32s32s128s')
TABLE_MAGIC=b'XNVPV001'
KINDS={'LANE':1,'SLOT':2}


@dataclass(frozen=True,slots=True)
class ContextRecord:
    payload: bytes

    def __post_init__(self):
        value=json_bytes(self.payload,4096)
        if (type(value) is not dict or set(value)!= {'kind','body'}
                or value['kind'] not in ('RAW','TOF','METRIC') or type(value['body']) is not dict):
            raise PersistenceError('version1 source context requires RAW/TOF/METRIC body')

    @property
    def sha256(self):return sha256(self.payload).hexdigest()

    def encode(self):
        return bytes.fromhex(self.sha256)+struct.pack('<IHH',len(self.payload),1,0)+self.payload.ljust(4096,b'\0')+bytes(24)


@dataclass(frozen=True,slots=True)
class ProvenanceRecord:
    provenance_id: int
    payload: bytes
    context_sha256: str=field(init=False)
    parents: tuple=field(init=False)
    source_mask: int=field(init=False)
    pi_lower: int=field(init=False)
    pi_upper: int=field(init=False)
    observation_sequence: int=field(init=False)

    def __post_init__(self):
        uint(self.provenance_id,positive=True)
        if type(self.payload) is not bytes or len(self.payload)!=256:raise PersistenceError('provenance payload must be256 bytes')
        values=PROVENANCE.unpack(self.payload)
        context,mask,kind,raw_count,parent_count,host,lower,upper,sequence,*tail=values
        parents=tuple(tail[:4]);body,flags,reserved=tail[4:]
        if (not 1<=mask<=63 or kind>5 or raw_count>4 or parent_count>4 or host==bytes(16)
                or lower>upper or upper>MAX_U63 or not 0<sequence<=MAX_U63 or flags or any(reserved)):
            raise PersistenceError('invalid provenance header/time/flags')
        if (any(not 0<parent<self.provenance_id for parent in parents[:parent_count])
                or len(set(parents[:parent_count]))!=parent_count or any(parents[parent_count:])):
            raise PersistenceError('provenance parents must be unique smaller IDs')
        if kind==5:
            registration=REGISTRATION.unpack(body)
            if (raw_count or not 0<registration[0]<=MAX_U63 or not 0<registration[1]<=MAX_U63
                    or any(value==bytes(16) for value in registration[2:5]) or registration[11]==bytes(16)
                    or not all(math.isfinite(value) for value in registration[5:10]+registration[12:])
                    or registration[8]<0 or registration[9]<0 or not registration[10]):
                raise PersistenceError('invalid registration union')
        else:
            if not raw_count and not parent_count:raise PersistenceError('derived evidence requires original raw keys or parent records')
            if any(body[raw_count*32:]):raise PersistenceError('unused raw keys must be zero')
            for offset in range(0,raw_count*32,32):
                instance,source_sequence,source_us=struct.unpack_from('<16sQQ',body,offset)
                if instance==bytes(16) or source_sequence>MAX_U63 or source_us>MAX_U63:raise PersistenceError('invalid original raw key')
            if kind==0 and not raw_count:raise PersistenceError('SENSOR provenance requires an original raw key')
        for name,value in dict(context_sha256=context.hex(),parents=parents[:parent_count],source_mask=mask,
                pi_lower=lower,pi_upper=upper,observation_sequence=sequence).items():object.__setattr__(self,name,value)

    def encode(self):return struct.pack('<QHHI',self.provenance_id,256,1,0)+self.payload


@dataclass(frozen=True,slots=True)
class CatalogRecord:
    kind: str
    stable_id: int
    geometry_revision: int
    occupancy_revision: int
    provenance_id: int
    payload: bytes

    def __post_init__(self):
        if self.kind not in KINDS:raise PersistenceError('catalog kind')
        uint(self.stable_id,0xffffffff,positive=True);uint(self.geometry_revision,positive=True)
        uint(self.occupancy_revision);uint(self.provenance_id,positive=True)
        if type(self.payload) is not bytes or len(self.payload)>224:raise PersistenceError('catalog body exceeds224 bytes')

    def encode(self):
        return CATALOG.pack(KINDS[self.kind],1,len(self.payload),self.stable_id,self.geometry_revision,
                            self.occupancy_revision,self.provenance_id)+self.payload.ljust(224,b'\0')

    @classmethod
    def decode(cls,value):
        if type(value) is not bytes or len(value)!=256:raise PersistenceError('catalog record length')
        kind,version,length,stable_id,geometry,occupancy,provenance=CATALOG.unpack_from(value)
        if kind not in (1,2) or version!=1 or length>224 or any(value[32+length:]):raise PersistenceError('catalog header/padding')
        return cls('LANE' if kind==1 else 'SLOT',stable_id,geometry,occupancy,provenance,value[32:32+length])


@dataclass(frozen=True,slots=True)
class TileVersion:
    map_epoch: str
    tile_x: int
    tile_y: int
    revision: int
    payload: bytes
    provenance_ids: tuple=field(init=False)
    sha256: str=field(init=False)
    crc32: int=field(init=False)

    def __post_init__(self):
        uuid(self.map_epoch);uint(self.revision,positive=True)
        for coordinate in (self.tile_x,self.tile_y):
            if type(coordinate) is not int or not -(1<<31)<=coordinate<(1<<31):raise PersistenceError('int32 tile coordinate required')
        if type(self.payload) is not bytes or len(self.payload)!=TILE_BYTES:raise PersistenceError('64x64 tile-record-1 bytes required')
        ids=set()
        for values in CELL.iter_unpack(self.payload):
            occupancy,flags,mask,hmin,hmax,bound,provenance,lower,upper,sequence,geometry,reserved=values
            if occupancy not in (0,100,255) or flags&~7 or mask&~63 or reserved:raise PersistenceError('tile cell enum/reserved')
            if not provenance:
                if occupancy!=255 or flags or any(values[2:]):raise PersistenceError('unobserved cell must be UNKNOWN with zero unused fields')
                continue
            if (provenance>MAX_U63 or not mask or not all(math.isfinite(v) for v in (hmin,hmax,bound))
                    or hmin>hmax or bound<0 or lower>upper or upper>MAX_U63 or not 0<sequence<=MAX_U63
                    or geometry>MAX_U63 or not flags&1 and occupancy!=255):raise PersistenceError('invalid observed tile cell')
            ids.add(provenance)
        if len(ids)>16384:raise PersistenceError('tile provenance closure cap')
        object.__setattr__(self,'provenance_ids',tuple(sorted(ids)))
        object.__setattr__(self,'sha256',sha256(self.payload).hexdigest())
        object.__setattr__(self,'crc32',zlib.crc32(self.payload))

    @property
    def reference(self):return (self.tile_x,self.tile_y,self.revision,self.sha256)


@dataclass(frozen=True,slots=True)
class EvidenceTable:
    provenance: tuple=()
    contexts: tuple=()
    catalog: tuple=()
    reservations: tuple=()
    catalog_highwaters: tuple=(1,1)
    bytes: bytes=field(init=False,repr=False)
    catalog_bytes: bytes=field(init=False,repr=False)
    sha256: str=field(init=False)

    def __post_init__(self):
        for values,kind,cap in ((self.provenance,ProvenanceRecord,16384),(self.contexts,ContextRecord,64),
                (self.catalog,CatalogRecord,320),(self.reservations,CatalogReservation,2)):
            if type(values) is not tuple or len(values)>cap or any(type(item) is not kind for item in values):
                raise PersistenceError('bounded exact evidence records required')
        if len({p.provenance_id for p in self.provenance})!=len(self.provenance) or len({c.sha256 for c in self.contexts})!=len(self.contexts):
            raise PersistenceError('duplicate provenance/context entry')
        if (sum(c.kind=='LANE' for c in self.catalog)>64 or sum(c.kind=='SLOT' for c in self.catalog)>256
                or len({(c.kind,c.stable_id) for c in self.catalog})!=len(self.catalog)
                or len({r.kind for r in self.reservations})!=len(self.reservations)):
            raise PersistenceError('catalog count/identity cap')
        if type(self.catalog_highwaters) is not tuple or len(self.catalog_highwaters)!=2:raise PersistenceError('two catalog highwaters required')
        for value in self.catalog_highwaters:uint(value,1<<32,positive=True)
        for record in self.catalog:
            if record.stable_id>=self.catalog_highwaters[KINDS[record.kind]-1]:raise PersistenceError('catalog record exceeds snapshot HWM')
        header=canonical(dict(version=1,lane_highwater=str(self.catalog_highwaters[0]),slot_highwater=str(self.catalog_highwaters[1]),
                              reservations=[r.local_fields() for r in self.reservations]))
        if len(header)>1020:raise PersistenceError('catalog header exceeds1KiB')
        catalog=struct.pack('<I',len(header))+header.ljust(1020,b'\0')+b''.join(c.encode() for c in self.catalog)
        provenance=b''.join(p.encode() for p in self.provenance)
        contexts=b''.join(c.encode() for c in self.contexts)
        body=TABLE_HEADER.pack(TABLE_MAGIC,1,len(self.provenance),len(self.contexts),len(catalog),len(self.catalog),0,
            sha256(provenance).digest(),sha256(contexts).digest(),sha256(catalog).digest(),bytes(128))+provenance+contexts+catalog
        if len(body)>EVIDENCE_CAP:raise PersistenceError('evidence/context/catalog copy exceeds5MiB')
        object.__setattr__(self,'bytes',body);object.__setattr__(self,'catalog_bytes',catalog)
        object.__setattr__(self,'sha256',sha256(body).hexdigest())

    @classmethod
    def decode(cls,value,job):
        if type(value) is not bytes or not TABLE_HEADER.size<=len(value)<=EVIDENCE_CAP:raise PersistenceError('evidence table size')
        magic,version,np,nc,catalog_length,catalog_count,reserved,ph,ch,kh,padding=TABLE_HEADER.unpack_from(value)
        if (magic!=TABLE_MAGIC or version!=1 or np>16384 or nc>64 or catalog_count>320 or reserved or any(padding)
                or catalog_length!=1024+catalog_count*256 or len(value)!=256+np*272+nc*4160+catalog_length):
            raise PersistenceError('evidence table header/count/offset')
        at=256;pb=value[at:at+np*272];at+=len(pb);cb=value[at:at+nc*4160];at+=len(cb);kb=value[at:]
        if (sha256(pb).digest(),sha256(cb).digest(),sha256(kb).digest())!=(ph,ch,kh):raise PersistenceError('evidence table hash mismatch')
        provenance=[]
        for at in range(0,len(pb),272):
            identity,length,record_version,zero=struct.unpack_from('<QHHI',pb,at)
            if length!=256 or record_version!=1 or zero:raise PersistenceError('provenance entry header')
            provenance.append(ProvenanceRecord(identity,pb[at+16:at+272]))
        contexts=[]
        for at in range(0,len(cb),4160):
            key=cb[at:at+32];length,record_version,zero=struct.unpack_from('<IHH',cb,at+32)
            if (length>4096 or record_version!=1 or zero or any(cb[at+40+length:at+4160])):
                raise PersistenceError('context entry padding/length')
            record=ContextRecord(cb[at+40:at+40+length])
            if bytes.fromhex(record.sha256)!=key:raise PersistenceError('context entry hash')
            contexts.append(record)
        length=struct.unpack_from('<I',kb)[0]
        if length>1020 or any(kb[4+length:1024]):raise PersistenceError('catalog header padding')
        header=json_bytes(kb[4:4+length],1020)
        if type(header) is not dict or set(header)!= {'version','lane_highwater','slot_highwater','reservations'} or header['version']!=1:
            raise PersistenceError('catalog header fields/version')
        reservations=[]
        for entry in header['reservations']:
            if type(entry) is not dict or set(entry)!= {'kind','expected_hwm','start_inclusive','end_exclusive','reservation_nonce'}:
                raise PersistenceError('catalog reservation fields')
            context=job.context
            reservations.append(CatalogReservation(context.database_uuid,context.map_epoch,entry['kind'],int(entry['expected_hwm']),
                int(entry['start_inclusive']),int(entry['end_exclusive']),entry['reservation_nonce'],context.mapper_worker,context.sha256))
        result=cls(tuple(provenance),tuple(contexts),tuple(CatalogRecord.decode(kb[at:at+256]) for at in range(1024,len(kb),256)),
            tuple(reservations),(int(header['lane_highwater']),int(header['slot_highwater'])))
        if result.bytes!=value:raise PersistenceError('noncanonical evidence/catalog table')
        return result


@dataclass(frozen=True,slots=True)
class CheckpointPayload:
    manifest: bytes
    tiles: tuple
    evidence: EvidenceTable
    intent: str=field(init=False)
    tile_references: tuple=field(init=False)
    sha256: str=field(init=False)

    def __post_init__(self):
        value=json_bytes(self.manifest,16384)
        if (type(value) is not dict or set(value)!= {'profile','intent','context_sha256','tiles','catalog_sha256','scene','durable_cutoff_pi_us'}
                or value['profile']!='xenova-checkpoint-1' or value['intent'] not in ('CHECKPOINT','CATALOG_RESERVATION')):
            raise PersistenceError('checkpoint manifest exact local profile required')
        digest(value['context_sha256']);digest(value['catalog_sha256']);uint(value['durable_cutoff_pi_us'])
        if value['scene'] is not None and type(value['scene']) is not dict:raise PersistenceError('scene manifest must preserve its original object or null')
        if type(self.tiles) is not tuple or len(self.tiles)>32 or any(type(tile) is not TileVersion for tile in self.tiles):
            raise PersistenceError('checkpoint copy permits at most32 tile versions')
        if type(self.evidence) is not EvidenceTable:raise PersistenceError('exact evidence table required')
        if value['scene'] is not None and 'mapper_state' in value['scene']:
            from .mapper_state import decode_mapper_state
            decode_mapper_state(value['scene'],self.evidence.contexts)
        refs=value['tiles']
        if type(refs) is not list or len(refs)>128:raise PersistenceError('checkpoint manifest tile cap')
        references=[]
        for entry in refs:
            if type(entry) is not list or len(entry)!=4:raise PersistenceError('tile reference shape')
            x,y,revision,key=entry
            for coordinate in (x,y):
                if type(coordinate) is not int or not -(1<<31)<=coordinate<(1<<31):raise PersistenceError('tile reference coordinate')
            uint(revision,positive=True);digest(key);references.append(tuple(entry))
        if references!=sorted(references) or len({entry[:2] for entry in references})!=len(references):raise PersistenceError('unique sorted complete tile manifest required')
        if (value['catalog_sha256']!=sha256(self.evidence.catalog_bytes).hexdigest()
                or any(tile.reference not in references for tile in self.tiles)
                or len({tile.reference[:2] for tile in self.tiles})!=len(self.tiles)):
            raise PersistenceError('manifest differs from original copy bytes')
        if value['intent']=='CATALOG_RESERVATION' and (refs or self.tiles or self.evidence.catalog or not self.evidence.reservations
                or self.evidence.provenance or self.evidence.contexts or value['scene'] is not None or value['durable_cutoff_pi_us']):
            raise PersistenceError('range-only local job must not claim a complete geometry checkpoint')
        if value['intent']=='CHECKPOINT' and not value['durable_cutoff_pi_us']:raise PersistenceError('original Pi durable cutoff required')
        for name,item in dict(intent=value['intent'],tile_references=tuple(references),sha256=sha256(canonical(dict(
                manifest_sha256=sha256(self.manifest).hexdigest(),evidence_sha256=self.evidence.sha256,
                copy_tiles=[tile.reference for tile in self.tiles]))).hexdigest()).items():object.__setattr__(self,name,item)

    @classmethod
    def build(cls,job,tiles=(),evidence=None,*,tile_references=None,scene=None,durable_cutoff_pi_us=0,intent='CHECKPOINT'):
        evidence=EvidenceTable() if evidence is None else evidence
        references=tuple(sorted(tile.reference for tile in tiles)) if tile_references is None else tuple(tile_references)
        manifest=canonical(dict(profile='xenova-checkpoint-1',intent=intent,context_sha256=job.context.sha256,
            tiles=references,catalog_sha256=sha256(evidence.catalog_bytes).hexdigest(),scene=scene,durable_cutoff_pi_us=durable_cutoff_pi_us))
        return cls(manifest,tiles,evidence)

    def validate_job(self,job):
        value=json_bytes(self.manifest,16384)
        if value['context_sha256']!=job.context.sha256 or any(tile.map_epoch!=job.context.map_epoch for tile in self.tiles):
            raise PersistenceError('checkpoint copy belongs to a different original source/map')
        if any(not reservation.matches(job) for reservation in self.evidence.reservations):raise PersistenceError('range context differs from its original job')
        scene=value['scene']
        if job.scene_commit_nonce is not None and (scene is None or scene.get('scene_commit_nonce')!=job.scene_commit_nonce):
            raise PersistenceError('scene commit nonce differs from original checkpoint job')


def _commit_tile_reference(value):
    if type(value) is not tuple or len(value)!=4:raise PersistenceError('original copy tile reference required')
    x,y,revision,key=value
    if any(type(v) is not int or not -(1<<31)<=v<(1<<31) for v in (x,y)):
        raise PersistenceError('original copy tile coordinate required')
    uint(revision,positive=True);digest(key)
    return value


@dataclass(frozen=True,slots=True)
class CheckpointCommitOrigin:
    """D가 원 저장 payload로 검산한 역사적 CP descriptor. LOAD/current 사용권은 아니다.

    원 evidence hash와 복사한 tile의 순서를 보존한다. 페이지/합친 closure의 SHA는 원
    CP SHA가 아니며 committed_j_us도 새 LOAD 완료/현재 source 시각으로 바꾸지 않는다.
    """
    receipt: CommitReceipt
    evidence_sha256: str
    copy_tiles: tuple

    def __post_init__(self):
        if type(self.receipt) is not CommitReceipt or self.receipt.status!='COMMITTED':
            raise PersistenceError('original stored COMMITTED receipt required')
        digest(self.evidence_sha256)
        if type(self.copy_tiles) is not tuple or len(self.copy_tiles)>32:
            raise PersistenceError('original copy permits at most32 tile references')
        for value in self.copy_tiles:_commit_tile_reference(value)
        if len({value[:2] for value in self.copy_tiles})!=len(self.copy_tiles):
            raise PersistenceError('unique original ordered copy tiles required')

    @property
    def fields(self):
        return dict(profile='checkpoint-commit-origin-1',receipt=self.receipt.fields,
            evidence_sha256=self.evidence_sha256,copy_tiles=[list(value) for value in self.copy_tiles])

    @property
    def bytes(self):return canonical(self.fields)

    @classmethod
    def from_fields(cls,value):
        if (type(value) is not dict or set(value)!= {'profile','receipt','evidence_sha256','copy_tiles'}
                or value['profile']!='checkpoint-commit-origin-1' or type(value['copy_tiles']) is not list
                or len(value['copy_tiles'])>32 or any(type(item) is not list for item in value['copy_tiles'])):
            raise PersistenceError('exact versioned original checkpoint descriptor required')
        return cls(CommitReceipt.from_fields(value['receipt']),value['evidence_sha256'],
            tuple(tuple(item) for item in value['copy_tiles']))

    def payload_sha256(self,manifest):
        """원 manifest/evidence/copy 순서의 SHA다. 원 evidence 전체를 LOAD했다는 주장은 아니다."""
        json_bytes(manifest,16384)
        return sha256(canonical(dict(manifest_sha256=sha256(manifest).hexdigest(),
            evidence_sha256=self.evidence_sha256,copy_tiles=self.copy_tiles))).hexdigest()

    def validate(self,original_job,manifest,selected_checkpoint_ref):
        """원 job/ref/receipt/commit stamp를 검산하고 원 전체 CP SHA를 반환한다."""
        if type(original_job) is not StorageJob:raise PersistenceError('original checkpoint job required')
        reference=selected_checkpoint_reference(selected_checkpoint_ref);value=json_bytes(manifest,16384)
        names={'profile','intent','context_sha256','tiles','catalog_sha256','scene','durable_cutoff_pi_us'}
        if (type(value) is not dict or set(value)!=names or value['profile']!='xenova-checkpoint-1'
                or value['intent']!='CHECKPOINT' or value['context_sha256']!=original_job.context.sha256
                or type(value['tiles']) is not list or len(value['tiles'])>128
                or any(type(item) is not list for item in value['tiles'])
                or value['scene'] is not None and type(value['scene']) is not dict):
            raise PersistenceError('original checkpoint manifest/source required')
        digest(value['catalog_sha256']);uint(value['durable_cutoff_pi_us'],positive=True)
        refs=tuple(_commit_tile_reference(tuple(item)) for item in value['tiles'])
        if (refs!=tuple(sorted(refs)) or len({item[:2] for item in refs})!=len(refs)
                or any(item not in refs for item in self.copy_tiles)):
            raise PersistenceError('original ordered copy differs from complete manifest')
        context=original_job.context;receipt=self.receipt
        if ((receipt.database_uuid,receipt.job_id,receipt.origin_context_sha256)!=(context.database_uuid,original_job.job_id,context.sha256)
                or reference['checkpoint_job_id']!=original_job.job_id or int(reference['durable_revision'])!=receipt.durable_revision
                or reference['manifest_length']!=len(manifest) or reference['manifest_sha256']!=sha256(manifest).hexdigest()
                or reference['tile_count']!=len(refs)
                or any(reference[name]!=getattr(context,name) for name in ('map_epoch','frame_epoch','odom_epoch','pi_host_boot',
                    'configuration','calibration','primitive_sha256','origin_mapper_instance'))
                or not original_job.issued_us<=receipt.committed_j_us<original_job.expires_us
                or original_job.scene_commit_nonce is not None and (value['scene'] is None
                    or value['scene'].get('scene_commit_nonce')!=original_job.scene_commit_nonce)):
            raise PersistenceError('original checkpoint commit receipt/ref/lifetime mismatch')
        payload_sha256=self.payload_sha256(manifest)
        if receipt.job_digest!=sha256(canonical(dict(job=original_job.fields,payload_sha256=payload_sha256))).hexdigest():
            raise PersistenceError('original checkpoint full payload/job digest mismatch')
        return payload_sha256


@dataclass(frozen=True,slots=True,weakref_slot=True)
class LoadedCheckpoint:
    request: LoadRequest
    original_job: StorageJob
    checkpoint_ref: bytes
    payload: CheckpointPayload
    heads: CatalogHeadSnapshot
    completed_us: int
    original_commit: CheckpointCommitOrigin | None=None

    def __post_init__(self):
        if (type(self.request) is not LoadRequest or type(self.original_job) is not StorageJob
                or type(self.payload) is not CheckpointPayload or type(self.heads) is not CatalogHeadSnapshot
                or self.checkpoint_ref!=self.request.checkpoint_ref):raise PersistenceError('exact original LOAD response required')
        uint(self.completed_us);self.payload.validate_job(self.original_job)
        reference=json_bytes(self.checkpoint_ref,2048)
        if (reference.get('manifest_sha256')!=sha256(self.payload.manifest).hexdigest()
                or reference.get('checkpoint_job_id')!=self.original_job.job_id):raise PersistenceError('loaded manifest/reference mismatch')
        if self.original_commit is not None:
            if type(self.original_commit) is not CheckpointCommitOrigin:raise PersistenceError('typed original checkpoint commit required')
            self.original_commit.validate(self.original_job,self.payload.manifest,self.checkpoint_ref)

    def current(self,request,context_sha256,now_us):
        return (type(request) is LoadRequest and self.request==request
            and request.job.current(context_sha256,now_us) and request.job.issued_us<=self.completed_us<=now_us<request.job.expires_us
            and self.heads.current(request.job,context_sha256,now_us))
