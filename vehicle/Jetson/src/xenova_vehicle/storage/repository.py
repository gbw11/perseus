"""기존 D connection의 additive persistence profile1. 새 DB/open/lock은 만들지 않는다.

원 schema1 UUID/provenance HWM과 기존 rows를 보존한다. 단일 D writer만 호출하며
rollback journal/FULL COMMIT 반환 뒤에만 성공을 돌려준다. commit stamp는 COMMIT
직전 원 transaction에 고정하고, actual 반환 시각/원 expiry의 수락은 별도로 검사한다.
"""

from dataclasses import replace
from hashlib import sha256
import time

from .contracts import (CatalogHeadSnapshot,CatalogReservation,CommitReceipt,LoadRequest,MAX_U63,PersistenceConflict,
    PersistenceCorrupt,PersistenceError,PersistenceUnknown,ProvenanceRange,StorageJob,canonical,
    decimal,digest,json_bytes,uint,uuid)
from .records import (CELL,CheckpointPayload,ContextRecord,EvidenceTable,LoadedCheckpoint,ProvenanceRecord,TileVersion)
from .geometry_scope import CatalogGeometryScope


PROFILE='xenova-checkpoint-1'
EXTENSION_TABLES=frozenset(('persistence_profile','provenance_ranges','source_contexts','provenance_records',
    'tile_versions','catalog_heads','catalog_ranges','catalog_versions','persistence_jobs'))
_DDL=(
 'CREATE TABLE persistence_profile (singleton INTEGER PRIMARY KEY CHECK(singleton=1),format_tag TEXT NOT NULL,next_revision INTEGER NOT NULL)',
 'CREATE TABLE provenance_ranges (reservation_nonce TEXT PRIMARY KEY,body_sha256 TEXT NOT NULL,mapper_worker TEXT NOT NULL,start_id INTEGER NOT NULL,end_id INTEGER NOT NULL,job_json BLOB NOT NULL,commit_stamp INTEGER NOT NULL)',
 'CREATE TABLE source_contexts (context_sha256 TEXT PRIMARY KEY,payload BLOB NOT NULL)',
 'CREATE TABLE provenance_records (provenance_id INTEGER PRIMARY KEY,payload_sha256 TEXT NOT NULL,payload BLOB NOT NULL)',
 'CREATE TABLE tile_versions (map_epoch TEXT NOT NULL,tile_x INTEGER NOT NULL,tile_y INTEGER NOT NULL,revision INTEGER NOT NULL,payload_sha256 TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(map_epoch,tile_x,tile_y,revision))',
 'CREATE TABLE catalog_heads (map_epoch TEXT NOT NULL,kind TEXT NOT NULL,highwater INTEGER NOT NULL,reservation_revision INTEGER NOT NULL,PRIMARY KEY(map_epoch,kind))',
 'CREATE TABLE catalog_ranges (map_epoch TEXT NOT NULL,kind TEXT NOT NULL,reservation_nonce TEXT NOT NULL,expected_hwm INTEGER NOT NULL,start_id INTEGER NOT NULL,end_id INTEGER NOT NULL,mapper_worker TEXT NOT NULL,context_sha256 TEXT NOT NULL,job_id TEXT NOT NULL,PRIMARY KEY(map_epoch,kind,reservation_nonce))',
 'CREATE TABLE catalog_versions (map_epoch TEXT NOT NULL,kind TEXT NOT NULL,stable_id INTEGER NOT NULL,geometry_revision INTEGER NOT NULL,occupancy_revision INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(map_epoch,kind,stable_id,geometry_revision,occupancy_revision))',
 'CREATE TABLE persistence_jobs (job_id TEXT PRIMARY KEY,job_digest TEXT NOT NULL,payload_sha256 TEXT NOT NULL,job_json BLOB NOT NULL,receipt_json BLOB NOT NULL,durable_revision INTEGER,manifest BLOB,evidence BLOB,copy_refs BLOB,error_code TEXT)',
 'CREATE INDEX provenance_range_start ON provenance_ranges(start_id)',
 'CREATE INDEX catalog_range_start ON catalog_ranges(map_epoch,kind,start_id)',
)


def _now():return time.monotonic_ns()//1000


def _context_dependencies(context):
    """알려진 parking-originals-1의 ContextRecord edge만 읽는다. asset SHA는 추적하지 않는다."""
    value=json_bytes(context.payload,4096);body=value['body']
    if body.get('profile')!='parking-originals-1':return ()
    names={'profile','method_context_sha256','storage_context_sha256','catalog_revision','sources','entries'}
    if (value['kind']!='METRIC' or set(body)!=names or type(body['sources']) is not list
            or type(body['entries']) is not list):raise PersistenceCorrupt('invalid exact parking originals context dependency')
    digest(body['method_context_sha256']);digest(body['storage_context_sha256']);decimal(body['catalog_revision'],positive=True)
    return ((body['method_context_sha256'],body['storage_context_sha256']),)


class PersistenceStore:
    """D가 이미 lock/open한 connection을 빌린다. close와 native owner 회수는 기존 D 책임이다."""

    def __init__(self,connection,database_uuid,*,expires_us,clock_us=_now):
        import sqlite3
        if not isinstance(connection,sqlite3.Connection) or connection.in_transaction:
            raise PersistenceError('idle existing D SQLite connection required')
        self.connection=connection;self.database_uuid=uuid(database_uuid);self.clock=clock_us;self.sqlite=sqlite3
        self._deadline(expires_us)
        try:
            self._metadata()
            if connection.execute('PRAGMA quick_check(1)').fetchone()!=('ok',):raise PersistenceCorrupt('SQLite integrity failure')
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 32")}
            base={'storage_metadata','checkpoints'}
            if tables==base:
                if connection.execute('SELECT COUNT(*) FROM checkpoints WHERE complete=1').fetchone()!=(0,):
                    raise PersistenceCorrupt('legacy complete checkpoint has no supported payload; preserve it for explicit migration')
                connection.execute('BEGIN IMMEDIATE')
                try:
                    for statement in _DDL:connection.execute(statement)
                    connection.execute('INSERT INTO persistence_profile VALUES (1,?,1)',(PROFILE,))
                    self._timely(expires_us);connection.execute('COMMIT')
                except BaseException:
                    self._rollback();raise
            elif tables!=base|EXTENSION_TABLES:raise PersistenceCorrupt('unknown or partial additive schema')
            row=connection.execute('SELECT singleton,format_tag,next_revision FROM persistence_profile LIMIT 2').fetchall()
            if len(row)!=1 or row[0][:2]!=(1,PROFILE):raise PersistenceCorrupt('persistence profile marker mismatch')
            uint(row[0][2],positive=True)
            self._has_extension=True
            self._metadata()
            self._next_revision()
            self._timely(expires_us)
        except sqlite3.DatabaseError as error:
            raise PersistenceCorrupt('existing SQLite rejected persistence migration/validation') from error
        finally:connection.set_progress_handler(None,0)

    def _metadata(self):
        c=self.connection
        if (c.execute('PRAGMA application_id').fetchone()!=(0x584E5631,) or c.execute('PRAGMA user_version').fetchone()!=(1,)
                or c.execute('PRAGMA journal_mode').fetchone()!=('delete',) or c.execute('PRAGMA synchronous').fetchone()!=(2,)):
            raise PersistenceCorrupt('expected existing schema1 rollback-journal/FULL owner connection')
        sizes=c.execute('SELECT length(format_tag),length(database_uuid),length(allocator_highwater) FROM storage_metadata LIMIT 2').fetchall()
        if len(sizes)!=1 or any(value is None or not 0<value<=cap for value,cap in zip(sizes[0],(64,36,20))):
            raise PersistenceCorrupt('metadata text size cap')
        rows=c.execute('SELECT singleton,format_tag,database_uuid,schema_version,allocator_highwater FROM storage_metadata LIMIT 2').fetchall()
        if len(rows)!=1 or rows[0][:4]!=(1,'xenova-storage-bootstrap-1',self.database_uuid,1):raise PersistenceCorrupt('DB identity/schema changed')
        raw=rows[0][4]
        if type(raw) is not str or not raw.isascii() or not raw.isdecimal() or str(int(raw))!=raw:raise PersistenceCorrupt('invalid durable provenance HWM')
        highwater=uint(int(raw),positive=True)
        if getattr(self,'_has_extension',False):
            maximum=c.execute('SELECT MAX(end_id) FROM provenance_ranges').fetchone()[0]
            if maximum is not None and highwater<maximum:raise PersistenceCorrupt('durable provenance head regressed below reserved ranges')
        return highwater

    def _timely(self,expires_us):
        uint(expires_us)
        if self.clock()>=expires_us:raise PersistenceUnknown('original DB operation deadline expired')

    def _next_revision(self):
        row=self.connection.execute('SELECT next_revision FROM persistence_profile WHERE singleton=1').fetchone()
        if row is None:raise PersistenceCorrupt('durable revision head missing')
        value=uint(row[0],positive=True)
        maximum=self.connection.execute('SELECT MAX(durable_revision) FROM persistence_jobs').fetchone()[0]
        if value!=(1 if maximum is None else maximum+1):raise PersistenceCorrupt('durable revision head regressed or split')
        return value

    def _deadline(self,expires_us):
        self._timely(expires_us)
        self.connection.set_progress_handler(lambda:int(self.clock()>=expires_us),1000)

    def _rollback(self):
        self.connection.set_progress_handler(None,0)
        if self.connection.in_transaction:self.connection.execute('ROLLBACK')

    def _original(self,job,current_context_sha256):
        if type(job) is not StorageJob or job.context.database_uuid!=self.database_uuid:raise PersistenceError('original DB job identity mismatch')
        if not job.current(current_context_sha256,self.clock()):raise PersistenceError('source revoked or original DB job expired before execution')
        self._metadata();self._deadline(job.expires_us)

    def query(self,job_id,job_digest,*,expires_us):
        """실제 SQL 원 receipt 조회. 역사 결과를 새 source 성공이나 새 시각으로 바꾸지 않는다."""
        uuid(job_id);digest(job_digest);self._deadline(expires_us)
        try:
            size=self.connection.execute('SELECT length(receipt_json) FROM persistence_jobs WHERE job_id=?',(job_id,)).fetchone()
            if size is None:return None
            if size[0] is None or not 0<size[0]<=2048:raise PersistenceCorrupt('stored receipt size cap')
            row=self.connection.execute('SELECT job_digest,receipt_json FROM persistence_jobs WHERE job_id=?',(job_id,)).fetchone()
            self._timely(expires_us)
            if row is None:return None
            if row[0]!=job_digest:raise PersistenceConflict('job ID reused with different immutable digest')
            receipt=CommitReceipt.from_fields(json_bytes(bytes(row[1]),2048))
            if (receipt.database_uuid,receipt.job_id,receipt.job_digest)!=(self.database_uuid,job_id,job_digest):
                raise PersistenceCorrupt('stored receipt differs from original QUERY key')
            return receipt
        except self.sqlite.DatabaseError as error:raise PersistenceUnknown('SQLite job result lookup unconfirmed') from error
        finally:self.connection.set_progress_handler(None,0)

    def reserve_provenance(self,job,reservation_nonce,*,block_count=1<<20,current_context_sha256):
        """별도 provenance namespace를 실제 durable HWM에서 먼저 예약한다."""
        uuid(reservation_nonce);uint(block_count,1<<20,positive=True)
        if type(job) is not StorageJob or job.context.database_uuid!=self.database_uuid:raise PersistenceError('provenance DB context mismatch')
        body_sha=sha256(canonical(dict(database_uuid=self.database_uuid,reservation_nonce=reservation_nonce,
            mapper_worker=job.context.mapper_worker,block_count=block_count,context_sha256=job.context.sha256))).hexdigest()
        self._deadline(job.expires_us)
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            size=self.connection.execute('SELECT length(job_json) FROM provenance_ranges WHERE reservation_nonce=?',(reservation_nonce,)).fetchone()
            if size is not None and (size[0] is None or not 0<size[0]<=8192):raise PersistenceCorrupt('stored range job size cap')
            row=self.connection.execute('SELECT body_sha256,start_id,end_id,job_json,commit_stamp FROM provenance_ranges WHERE reservation_nonce=?',
                (reservation_nonce,)).fetchone()
            if row:
                if row[0]!=body_sha:raise PersistenceConflict('provenance reservation nonce conflict')
                original=StorageJob.from_fields(json_bytes(bytes(row[3]),8192))
                self.connection.execute('ROLLBACK')
                return ProvenanceRange(original,reservation_nonce,row[1],row[2],row[2],row[4],'REPLAY')
            self._original(job,current_context_sha256)
            start=self._metadata();end=start+block_count
            if end>MAX_U63:raise PersistenceConflict('PROVENANCE_EXHAUSTED')
            stamp=self.clock();self._timely(job.expires_us)
            self.connection.execute('INSERT INTO provenance_ranges VALUES (?,?,?,?,?,?,?)',
                (reservation_nonce,body_sha,job.context.mapper_worker,start,end,canonical(job.fields),stamp))
            self.connection.execute('UPDATE storage_metadata SET allocator_highwater=? WHERE singleton=1',(str(end),))
            self.connection.execute('COMMIT')
            return ProvenanceRange(job,reservation_nonce,start,end,end,stamp)
        except self.sqlite.DatabaseError as error:
            self._rollback();raise PersistenceUnknown('provenance COMMIT outcome requires original nonce lookup') from error
        except BaseException:self._rollback();raise
        finally:self.connection.set_progress_handler(None,0)

    def reserve_provenance_wire(self,body,*,request_id,issued_us,expires_us):
        """pre-publication RESERVE 본문을 보존한다. 아직 없는 map/Pi identity를 만들지 않는다."""
        names={'database_uuid','reservation_nonce','mapper_worker','block_count'}
        if type(body) is not dict or set(body)!=names or body['database_uuid']!=self.database_uuid:
            raise PersistenceError('exact wire RESERVE body required')
        for name in ('database_uuid','reservation_nonce','mapper_worker'):uuid(body[name])
        uuid(request_id);uint(issued_us);uint(expires_us);uint(body['block_count'],1<<20,positive=True)
        if not issued_us<expires_us<=issued_us+5_000_000:raise PersistenceError('original wire reserve deadline')
        immutable=canonical(body);fingerprint=sha256(immutable).hexdigest();self._deadline(expires_us)
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            size=self.connection.execute('SELECT length(job_json) FROM provenance_ranges WHERE reservation_nonce=?',(body['reservation_nonce'],)).fetchone()
            if size is not None and (size[0] is None or not 0<size[0]<=8192):raise PersistenceCorrupt('wire range original size cap')
            old=self.connection.execute('SELECT body_sha256,start_id,end_id,job_json FROM provenance_ranges WHERE reservation_nonce=?',
                (body['reservation_nonce'],)).fetchone()
            if old:
                if old[0]!=fingerprint:raise PersistenceConflict('wire provenance reservation nonce conflict')
                original=json_bytes(bytes(old[3]),8192)
                if (type(original) is not dict or original.get('format')!='storage-reserve-wire-1'
                        or original.get('body')!=body):raise PersistenceCorrupt('wire range original body mismatch')
                self.connection.execute('ROLLBACK')
                return dict(database_uuid=self.database_uuid,reservation_nonce=body['reservation_nonce'],mapper_worker=body['mapper_worker'],
                    start_inclusive=str(old[1]),end_exclusive=str(old[2]),highwater_after=str(old[2]),result='REPLAY')
            if not issued_us<=self.clock()<expires_us:raise PersistenceError('wire RESERVE outside original lifetime')
            start=self._metadata();end=start+body['block_count']
            if end>MAX_U63:raise PersistenceConflict('PROVENANCE_EXHAUSTED')
            original=canonical(dict(format='storage-reserve-wire-1',body=body,request_id=request_id,
                issued_us=str(issued_us),expires_us=str(expires_us)))
            self.connection.execute('INSERT INTO provenance_ranges VALUES (?,?,?,?,?,?,?)',
                (body['reservation_nonce'],fingerprint,body['mapper_worker'],start,end,original,self.clock()))
            self.connection.execute('UPDATE storage_metadata SET allocator_highwater=? WHERE singleton=1',(str(end),))
            self._timely(expires_us);self.connection.execute('COMMIT')
            return dict(database_uuid=self.database_uuid,reservation_nonce=body['reservation_nonce'],mapper_worker=body['mapper_worker'],
                start_inclusive=str(start),end_exclusive=str(end),highwater_after=str(end),result='GRANTED')
        except self.sqlite.DatabaseError as error:
            self._rollback();raise PersistenceUnknown('wire reservation outcome requires original nonce lookup') from error
        except BaseException:self._rollback();raise
        finally:self.connection.set_progress_handler(None,0)

    def catalog_heads(self,job,*,current_context_sha256):
        """원 current LOAD/request에 결박한 실제 heads. 과거 geometry의 작은 HWM은 사용하지 않는다."""
        self._original(job,current_context_sha256)
        try:
            heads=[]
            for kind in ('LANE','SLOT'):
                head,revision=self._catalog_head(job.context.map_epoch,kind)
                heads.append((kind,head,revision))
            observed=self.clock();self._timely(job.expires_us)
            return CatalogHeadSnapshot(job.job_id,job.context.sha256,self.database_uuid,job.context.map_epoch,
                tuple(heads),observed,job.expires_us)
        except self.sqlite.DatabaseError as error:raise PersistenceUnknown('current catalog head read unconfirmed') from error
        finally:self.connection.set_progress_handler(None,0)

    def _catalog_head(self,map_epoch,kind):
        row=self.connection.execute('SELECT highwater,reservation_revision FROM catalog_heads WHERE map_epoch=? AND kind=?',
            (map_epoch,kind)).fetchone()
        maximum,count=self.connection.execute('SELECT MAX(end_id),COUNT(*) FROM catalog_ranges WHERE map_epoch=? AND kind=?',
            (map_epoch,kind)).fetchone()
        if row is None:
            if count:raise PersistenceCorrupt('catalog head missing for existing reservations')
            return 1,0
        head,revision=row
        uint(head,1<<32,positive=True);uint(revision,positive=True)
        if maximum!=head or count!=revision:raise PersistenceCorrupt('catalog head/range revision mismatch')
        return head,revision

    def _ranges(self,job,payload):
        for reservation in payload.evidence.reservations:
            head,revision=self._catalog_head(reservation.map_epoch,reservation.kind)
            if head!=reservation.expected_hwm:raise PersistenceConflict('CATALOG_HWM_CAS')
            old=self.connection.execute('SELECT job_id FROM catalog_ranges WHERE map_epoch=? AND kind=? AND reservation_nonce=?',
                (reservation.map_epoch,reservation.kind,reservation.reservation_nonce)).fetchone()
            if old:raise PersistenceConflict('catalog nonce belongs to an original job; QUERY that job')
            overlap=self.connection.execute('SELECT 1 FROM catalog_ranges WHERE map_epoch=? AND kind=? AND start_id<? AND end_id>? LIMIT 1',
                (reservation.map_epoch,reservation.kind,reservation.end_exclusive,reservation.start_inclusive)).fetchone()
            if overlap:raise PersistenceConflict('catalog range overlap')
            self.connection.execute('INSERT INTO catalog_ranges VALUES (?,?,?,?,?,?,?,?,?)',
                (reservation.map_epoch,reservation.kind,reservation.reservation_nonce,reservation.expected_hwm,
                 reservation.start_inclusive,reservation.end_exclusive,reservation.mapper_worker,reservation.context_sha256,job.job_id))
            if revision==0:self.connection.execute('INSERT INTO catalog_heads VALUES (?,?,?,?)',
                (reservation.map_epoch,reservation.kind,reservation.end_exclusive,revision+1))
            else:self.connection.execute('UPDATE catalog_heads SET highwater=?,reservation_revision=? WHERE map_epoch=? AND kind=?',
                (reservation.end_exclusive,revision+1,reservation.map_epoch,reservation.kind))

    def _immutable(self,table,key_names,key_values,payload,column='payload'):
        where=' AND '.join(name+'=?' for name in key_names)
        size=self.connection.execute('SELECT length('+column+') FROM '+table+' WHERE '+where,tuple(key_values)).fetchone()
        if size is None:return True
        cap={'source_contexts':4096,'provenance_records':256,'tile_versions':262144,'catalog_versions':256}[table]
        if size[0] is None or not 0<size[0]<=cap:raise PersistenceCorrupt('stored immutable record size cap')
        if size[0]!=len(payload):raise PersistenceConflict('immutable '+table+' record length conflict')
        row=self.connection.execute('SELECT '+column+' FROM '+table+' WHERE '+where,tuple(key_values)).fetchone()
        if row is not None:
            if bytes(row[0])!=payload:raise PersistenceConflict('immutable '+table+' record conflict')
            return False
        return True

    def _closure(self,identities,provided,contexts):
        queue=list(identities);records={};used_contexts={}
        while queue:
            identity=queue.pop()
            if identity in records:continue
            if len(records)>=16384:raise PersistenceConflict('provenance closure exceeds copy cap')
            record=provided.get(identity)
            if record is None:
                size=self.connection.execute('SELECT length(payload) FROM provenance_records WHERE provenance_id=?',(identity,)).fetchone()
                if size is not None and size[0]!=256:raise PersistenceCorrupt('stored provenance size cap')
                row=self.connection.execute('SELECT payload_sha256,payload FROM provenance_records WHERE provenance_id=?',(identity,)).fetchone()
                if row is None:raise PersistenceConflict('missing provenance parent/cell reference')
                if sha256(bytes(row[1])).hexdigest()!=row[0]:raise PersistenceCorrupt('stored provenance hash mismatch')
                record=ProvenanceRecord(identity,bytes(row[1]))
            records[identity]=record;queue.extend(record.parents)
            key=record.context_sha256
            if key not in used_contexts:
                context=contexts.get(key)
                if context is None:
                    size=self.connection.execute('SELECT length(payload) FROM source_contexts WHERE context_sha256=?',(key,)).fetchone()
                    if size is not None and (size[0] is None or not 0<size[0]<=4096):raise PersistenceCorrupt('stored context size cap')
                    row=self.connection.execute('SELECT payload FROM source_contexts WHERE context_sha256=?',(key,)).fetchone()
                    if row is None:raise PersistenceConflict('missing source context closure')
                    context=ContextRecord(bytes(row[0]))
                if context.sha256!=key or len(used_contexts)>=64:raise PersistenceConflict('context closure mismatch/cap')
                used_contexts[key]=context
        # Provenance가 참조한 context의 명시 ContextRecord edge도 같은64개 상한에서 닫는다.
        pending=list(used_contexts)
        while pending:
            source=used_contexts[pending.pop()]
            for key,storage_sha in _context_dependencies(source):
                context=used_contexts.get(key) or contexts.get(key)
                if context is None:
                    size=self.connection.execute('SELECT length(payload) FROM source_contexts WHERE context_sha256=?',(key,)).fetchone()
                    if size is not None and (size[0] is None or not 0<size[0]<=4096):raise PersistenceCorrupt('stored dependent context size cap')
                    row=self.connection.execute('SELECT payload FROM source_contexts WHERE context_sha256=?',(key,)).fetchone()
                    if row is None:raise PersistenceConflict('missing original method context closure')
                    context=ContextRecord(bytes(row[0]))
                value=json_bytes(context.payload,4096);body=value['body']
                names={'profile','method_manifest_sha256','storage_context_sha256','calibration_profile_sha256',
                       'vehicle_bundle_sha256','coordinate','map_frame_id','body','geometry_profile','association_profile'}
                if (context.sha256!=key or value['kind']!='METRIC' or set(body)!=names
                        or body.get('profile')!='parking-method-1' or body['storage_context_sha256']!=storage_sha):
                    raise PersistenceCorrupt('original method context hash/profile/source differs')
                if key not in used_contexts:
                    if len(used_contexts)>=64:raise PersistenceConflict('context dependency closure exceeds copy cap')
                    used_contexts[key]=context;pending.append(key)
        return records,used_contexts

    def _write_payload(self,job,payload):
        evidence=payload.evidence;provided={record.provenance_id:record for record in evidence.provenance}
        contexts={record.sha256:record for record in evidence.contexts}
        identities=set(provided)
        from .mapper_state import decode_mapper_state,mapper_state_roots
        state,_=decode_mapper_state(json_bytes(payload.manifest,16384)['scene'],evidence.contexts)
        identities.update(mapper_state_roots(state))
        for tile in payload.tiles:identities.update(tile.provenance_ids)
        identities.update(record.provenance_id for record in evidence.catalog)
        records,_=self._closure(identities,provided,contexts)
        for context in evidence.contexts:
            if self._immutable('source_contexts',('context_sha256',),(context.sha256,),context.payload):
                self.connection.execute('INSERT INTO source_contexts VALUES (?,?)',(context.sha256,context.payload))
        for record in evidence.provenance:
            if self._immutable('provenance_records',('provenance_id',),(record.provenance_id,),record.payload):
                owner=self.connection.execute('SELECT mapper_worker,end_id FROM provenance_ranges WHERE start_id<=? ORDER BY start_id DESC LIMIT 1',
                    (record.provenance_id,)).fetchone()
                if owner is None or owner[0]!=job.context.mapper_worker or record.provenance_id>=owner[1]:
                    raise PersistenceConflict('unreserved provenance ID or different original mapper')
                self.connection.execute('INSERT INTO provenance_records VALUES (?,?,?)',
                    (record.provenance_id,sha256(record.payload).hexdigest(),record.payload))
        for tile in payload.tiles:
            for values in CELL.iter_unpack(tile.payload):
                if values[6]:
                    record=records[values[6]]
                    if (values[2]&record.source_mask!=values[2] or values[7:10]!=(record.pi_lower,record.pi_upper,record.observation_sequence)):
                        raise PersistenceConflict('cell source/time differs from its immutable provenance')
            key=(tile.map_epoch,tile.tile_x,tile.tile_y,tile.revision)
            if self._immutable('tile_versions',('map_epoch','tile_x','tile_y','revision'),key,tile.payload):
                self.connection.execute('INSERT INTO tile_versions VALUES (?,?,?,?,?,?)',(*key,tile.sha256,tile.payload))
        for x,y,revision,key in payload.tile_references:self._raw_tile(job.context.map_epoch,x,y,revision,key)
        range_scopes={}
        for record in evidence.catalog:
            authorized=self.connection.execute('SELECT expected_hwm,start_id,end_id,reservation_nonce,mapper_worker,context_sha256,job_id FROM catalog_ranges WHERE map_epoch=? AND kind=? AND start_id<=? ORDER BY start_id DESC LIMIT 1',
                (job.context.map_epoch,record.kind,record.stable_id)).fetchone()
            if authorized is None or record.stable_id>=authorized[2]:raise PersistenceConflict('unreserved catalog ID')
            durable=self.connection.execute('SELECT 1 FROM catalog_versions WHERE map_epoch=? AND kind=? AND stable_id=? LIMIT 1',
                (job.context.map_epoch,record.kind,record.stable_id)).fetchone()
            if durable is None and (authorized[4],authorized[5])!=(job.context.mapper_worker,job.context.sha256):
                if not self._same_range_geometry(job,record.kind,authorized,range_scopes):
                    raise PersistenceConflict('unused catalog ID belongs to a different original mapper/geometry range')
            key=(job.context.map_epoch,record.kind,record.stable_id,record.geometry_revision,record.occupancy_revision)
            if self._immutable('catalog_versions',('map_epoch','kind','stable_id','geometry_revision','occupancy_revision'),key,record.encode()):
                self.connection.execute('INSERT INTO catalog_versions VALUES (?,?,?,?,?,?)',(*key,record.encode()))

    def _same_range_geometry(self,job,kind,row,cache):
        """원 예약 job/receipt를 검산한다. proof 없는 legacy를 새 scene context로 승격하지 않는다."""
        current=job.context
        if row[4]!=current.mapper_worker or current.geometry_origin is None:return False
        key=kind,row[6]
        if key not in cache:
            original,payload,receipt=self._stored_payload(row[6])
            reservation=CatalogReservation(self.database_uuid,current.map_epoch,kind,row[0],row[1],row[2],row[3],row[4],row[5])
            if (receipt.database_uuid!=self.database_uuid or original.context.sha256!=row[5]
                    or not reservation.matches(original) or reservation not in payload.evidence.reservations):
                raise PersistenceCorrupt('catalog range differs from its immutable committed original job')
            try:cache[key]=None if original.context.geometry_origin is None else CatalogGeometryScope.from_context(original.context,kind)
            except PersistenceError:cache[key]=None
        reserved=cache[key]
        if reserved is None:return False
        try:return reserved.same_geometry(CatalogGeometryScope.from_context(current,kind))
        except PersistenceError:return False

    def commit(self,job,payload,*,current_context_sha256):
        """한 transaction에 payload/단조 heads/원 receipt를 기록한다. logical 실패도 원 job 결과로 고정한다."""
        if type(payload) is not CheckpointPayload:raise PersistenceError('exact complete checkpoint copy required')
        payload.validate_job(job);self._original(job,current_context_sha256)
        job_digest=job.digest(payload)
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            size=self.connection.execute('SELECT length(receipt_json) FROM persistence_jobs WHERE job_id=?',(job.job_id,)).fetchone()
            if size is not None and (size[0] is None or not 0<size[0]<=2048):raise PersistenceCorrupt('stored duplicate receipt size cap')
            old=self.connection.execute('SELECT job_digest,receipt_json FROM persistence_jobs WHERE job_id=?',(job.job_id,)).fetchone()
            if old:
                if old[0]!=job_digest:raise PersistenceConflict('job ID/digest conflict')
                receipt=CommitReceipt.from_fields(json_bytes(bytes(old[1]),2048))
                self.connection.execute('ROLLBACK')
                return replace(receipt,status='DUPLICATE') if receipt.status=='COMMITTED' else receipt
            self.connection.execute('SAVEPOINT payload')
            error=None
            try:self._ranges(job,payload);self._write_payload(job,payload)
            except PersistenceConflict as conflict:
                self.connection.execute('ROLLBACK TO payload');error=str(conflict)[:160]
            self.connection.execute('RELEASE payload')
            revision=stamp=None
            if error is None:
                revision=self._next_revision()
                if not 0<revision<MAX_U63:raise PersistenceConflict('durable revision exhausted')
                self.connection.execute('UPDATE persistence_profile SET next_revision=? WHERE singleton=1',(revision+1,))
                stamp=self.clock()
            receipt=CommitReceipt(self.database_uuid,job.job_id,job_digest,'FAILED' if error else 'COMMITTED',revision,stamp,job.context.sha256)
            self.connection.execute('INSERT INTO persistence_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                (job.job_id,job_digest,payload.sha256,canonical(job.fields),canonical(receipt.fields),revision,
                 None if error else payload.manifest,None if error else payload.evidence.bytes,
                 None if error else canonical([tile.reference for tile in payload.tiles]),error))
            if error is None and payload.intent=='CHECKPOINT':
                reference=self._reference(job,payload,revision)
                self.connection.execute('INSERT INTO checkpoints VALUES (?,1,?)',(job.job_id,canonical(reference).decode('ascii')))
            self._timely(job.expires_us)
            self.connection.execute('COMMIT')
            return receipt
        except self.sqlite.DatabaseError as error:
            self._rollback();raise PersistenceUnknown('COMMIT outcome requires original job/digest QUERY') from error
        except BaseException:self._rollback();raise
        finally:self.connection.set_progress_handler(None,0)

    @staticmethod
    def _reference(job,payload,revision):
        context=job.context
        return dict(checkpoint_job_id=job.job_id,durable_revision=str(revision),manifest_sha256=sha256(payload.manifest).hexdigest(),
            manifest_length=len(payload.manifest),tile_count=len(payload.tile_references),map_epoch=context.map_epoch,
            frame_epoch=context.frame_epoch,odom_epoch=context.odom_epoch,pi_host_boot=context.pi_host_boot,
            configuration=context.configuration,calibration=context.calibration,primitive_sha256=context.primitive_sha256,
            origin_mapper_instance=context.origin_mapper_instance)

    def _raw_tile(self,map_epoch,x,y,revision,expected_sha256):
        key=(map_epoch,x,y,revision)
        row=self.connection.execute('SELECT payload_sha256,length(payload) FROM tile_versions WHERE map_epoch=? AND tile_x=? AND tile_y=? AND revision=?',key).fetchone()
        if row is None:raise PersistenceConflict('complete manifest references missing tile')
        if row!=(expected_sha256,262144):raise PersistenceCorrupt('tile metadata/hash/length mismatch')
        raw=bytes(self.connection.execute('SELECT payload FROM tile_versions WHERE map_epoch=? AND tile_x=? AND tile_y=? AND revision=?',key).fetchone()[0])
        if sha256(raw).hexdigest()!=expected_sha256:raise PersistenceCorrupt('tile payload hash mismatch')
        return raw

    def _stored_payload(self,job_id):
        bounds=self.connection.execute('SELECT length(job_json),length(receipt_json),length(manifest),length(evidence),length(copy_refs) FROM persistence_jobs WHERE job_id=?',
            (job_id,)).fetchone()
        if bounds is None:raise PersistenceCorrupt('checkpoint has no original job receipt')
        if any(value is None or value<0 or value>cap for value,cap in zip(bounds,(8192,2048,16384,5*1024*1024,8192))):
            raise PersistenceCorrupt('stored checkpoint payload size cap')
        row=self.connection.execute('SELECT job_digest,payload_sha256,job_json,receipt_json,manifest,evidence,copy_refs FROM persistence_jobs WHERE job_id=?',
            (job_id,)).fetchone()
        original=StorageJob.from_fields(json_bytes(bytes(row[2]),8192))
        receipt=CommitReceipt.from_fields(json_bytes(bytes(row[3]),2048))
        if (original.job_id!=job_id or original.context.database_uuid!=self.database_uuid or receipt.status!='COMMITTED'
                or receipt.job_id!=job_id or receipt.origin_context_sha256!=original.context.sha256 or receipt.job_digest!=row[0]):
            raise PersistenceCorrupt('stored checkpoint receipt/source mismatch')
        refs=json_bytes(bytes(row[6]),8192)
        if type(refs) is not list or len(refs)>32:raise PersistenceCorrupt('stored copy descriptor cap')
        tiles=tuple(TileVersion(original.context.map_epoch,x,y,revision,self._raw_tile(original.context.map_epoch,x,y,revision,key))
                    for x,y,revision,key in refs)
        evidence=EvidenceTable.decode(bytes(row[5]),original)
        payload=CheckpointPayload(bytes(row[4]),tiles,evidence);payload.validate_job(original)
        if payload.sha256!=row[1] or original.digest(payload)!=row[0]:raise PersistenceCorrupt('stored complete job digest mismatch')
        # 선택 manifest의 모든 원 tile bytes를 확인하며 한 번에 전체 atlas를 올리지 않는다.
        for x,y,revision,key in payload.tile_references:
            raw=self._raw_tile(original.context.map_epoch,x,y,revision,key)
            identities={values[6] for values in CELL.iter_unpack(raw) if values[6]}
            try:self._closure(identities,{},{})
            except PersistenceConflict as error:raise PersistenceCorrupt('selected checkpoint provenance/context closure is incomplete') from error
        try:self._closure({record.provenance_id for record in payload.evidence.catalog},{},{})
        except PersistenceConflict as error:raise PersistenceCorrupt('selected catalog provenance/context closure is incomplete') from error
        from .mapper_state import decode_mapper_state,mapper_state_roots
        state,_=decode_mapper_state(json_bytes(payload.manifest,16384)['scene'],payload.evidence.contexts)
        try:self._closure(set(mapper_state_roots(state)),{},{})
        except PersistenceConflict as error:raise PersistenceCorrupt('selected mapper metadata proof closure is incomplete') from error
        return original,payload,receipt

    def selected_checkpoint(self,job_id,*,expires_us):
        """원 완전 ref를 반환한다. 누락/손상을 EMPTY나 새 DB로 바꾸지 않는다."""
        uuid(job_id);self._deadline(expires_us)
        try:
            size=self.connection.execute('SELECT length(metadata_json) FROM checkpoints WHERE checkpoint_job_id=?',(job_id,)).fetchone()
            if size is None:return None
            if size[0] is None or not 0<size[0]<=2048:raise PersistenceCorrupt('selected checkpoint metadata size cap')
            row=self.connection.execute('SELECT complete,metadata_json FROM checkpoints WHERE checkpoint_job_id=?',(job_id,)).fetchone()
            if row is None:return None
            if row[0]!=1:raise PersistenceCorrupt('selected checkpoint is not complete')
            original,payload,receipt=self._stored_payload(job_id)
            reference=self._reference(original,payload,receipt.durable_revision)
            if json_bytes(row[1].encode('ascii'),2048)!=reference:raise PersistenceCorrupt('selected ref differs from original complete receipt')
            self._timely(expires_us)
            return reference
        except self.sqlite.DatabaseError as error:raise PersistenceUnknown('selected checkpoint SQL read unconfirmed') from error
        finally:self.connection.set_progress_handler(None,0)

    def latest_complete(self,*,expires_us):
        self._deadline(expires_us)
        try:
            orphan=self.connection.execute('SELECT 1 FROM checkpoints c LEFT JOIN persistence_jobs j ON c.checkpoint_job_id=j.job_id WHERE c.complete=1 AND j.job_id IS NULL LIMIT 1').fetchone()
            if orphan:raise PersistenceCorrupt('complete checkpoint has no supported original payload')
            row=self.connection.execute('SELECT c.checkpoint_job_id FROM checkpoints c JOIN persistence_jobs j ON c.checkpoint_job_id=j.job_id WHERE c.complete=1 ORDER BY j.durable_revision DESC LIMIT 1').fetchone()
            return None if row is None else self.selected_checkpoint(row[0],expires_us=expires_us)
        except self.sqlite.DatabaseError as error:raise PersistenceUnknown('latest complete selection unconfirmed') from error
        finally:self.connection.set_progress_handler(None,0)

    def load(self,request,*,current_context_sha256):
        """원 checkpoint page/정확 tile와 실제 proof/context를 역방향 복사한다. scene 승인은 없다."""
        if type(request) is not LoadRequest:raise PersistenceError('original exact load request required')
        job=request.job;self._original(job,current_context_sha256)
        try:
            reference=json_bytes(request.checkpoint_ref,2048)
            if 'checkpoint_job_id' not in reference:raise PersistenceError('selected checkpoint job required')
            actual=self.selected_checkpoint(reference['checkpoint_job_id'],expires_us=job.expires_us)
            self._deadline(job.expires_us)
            if actual is None:return CommitReceipt(self.database_uuid,job.job_id,request.digest,'ABSENT',None,None,job.context.sha256)
            if actual!=reference:raise PersistenceConflict('LOAD ref differs from original selected checkpoint')
            original,payload,_=self._stored_payload(reference['checkpoint_job_id'])
            if request.purpose=='CURRENT_MAP' and original.context.map_epoch!=job.context.map_epoch:
                raise PersistenceConflict('current map LOAD crosses map epoch')
            selection=json_bytes(request.selection,512)
            if selection['kind']=='CHECKPOINT_PAGE':
                cursor=selection['cursor']
                if cursor>=len(payload.tile_references) and (cursor or payload.tile_references):
                    return CommitReceipt(self.database_uuid,job.job_id,request.digest,'ABSENT',None,None,job.context.sha256)
                refs=payload.tile_references[cursor:cursor+32]
            else:
                wanted=(selection['tile_x'],selection['tile_y'],int(selection['tile_revision']))
                if selection['map_epoch']!=original.context.map_epoch:
                    return CommitReceipt(self.database_uuid,job.job_id,request.digest,'ABSENT',None,None,job.context.sha256)
                refs=tuple(ref for ref in payload.tile_references if ref[:3]==wanted)
                if not refs:return CommitReceipt(self.database_uuid,job.job_id,request.digest,'ABSENT',None,None,job.context.sha256)
            tiles=tuple(TileVersion(original.context.map_epoch,x,y,revision,self._raw_tile(original.context.map_epoch,x,y,revision,key))
                        for x,y,revision,key in refs)
            identities={record.provenance_id for record in payload.evidence.catalog}
            for tile in tiles:identities.update(tile.provenance_ids)
            # META는 original manifest의 exact SHA 참조로만 LOAD에 포함한다.
            # unreferenced context를 임의 복구하거나 현재 source 사실로 승격하지 않는다.
            from .mapper_state import decode_mapper_state,mapper_state_roots
            scene=json_bytes(payload.manifest,16384)['scene']
            state,metadata=decode_mapper_state(scene,payload.evidence.contexts)
            identities.update(mapper_state_roots(state))
            records,contexts=self._closure(identities,{},{})
            for item in metadata:contexts[item.sha256]=item
            evidence=EvidenceTable(tuple(records[key] for key in sorted(records)),tuple(contexts[key] for key in sorted(contexts)),
                payload.evidence.catalog,payload.evidence.reservations,payload.evidence.catalog_highwaters)
            copy=CheckpointPayload(payload.manifest,tiles,evidence)
            heads=self.catalog_heads(job,current_context_sha256=current_context_sha256)
            completed=self.clock();self._timely(job.expires_us)
            return LoadedCheckpoint(request,original,request.checkpoint_ref,copy,heads,completed)
        except self.sqlite.DatabaseError as error:raise PersistenceUnknown('LOAD SQL outcome unconfirmed') from error
        finally:self.connection.set_progress_handler(None,0)
