"""D의 실제 SQL/copy 작업 owner. S의 source·lease 원장과 실행 권한은 대신하지 않는다.

한 PREPARE와 최대32 tile의 immutable 사본만 보유한다. SQL은 D 안에서만 실행하며
M/S가 fsync를 기다리거나 shared-memory view를 SQL 완료까지 붙잡지 않게 한다.
"""

from hashlib import sha256

from .raw_leases import Endpoint
from .resident_transport import j_us
from .storage_copy import CopyArena,CopyLease,encode_load
from .storage_messages import (db_payload,load_from_prepare,parse_db_job,prepare_payload,publication_binding)
from ..storage.contracts import (CommitReceipt,PersistenceConflict,PersistenceCorrupt,PersistenceError,
    PersistenceUnknown,canonical,decimal,digest)
from ..storage.port import CheckpointRequest


class StorageJobs:
    """실제 D connection을 빌린다. arena 생성/unlink·다른 worker의 생존 추정은 하지 않는다."""

    def __init__(self,peer,binding,database,copy_handles,*,clock_us=j_us):
        self.peer=peer;self.binding=binding;self.database=database;self.handles=copy_handles;self.clock=clock_us
        self.endpoint=Endpoint(peer.channel_epoch,peer.worker_instance)
        self.arena=None;self.prepared=None;self.owned=None;self.copy_lease=None
        self.publication=None;self.publication_revision=0;self.failed=False;self.completed=0;self.copy_count=0
        self.geometry_origin=None
        self.last_commit=None

    @property
    def state(self):return 'FAILED' if self.failed else 'ACTIVE_JOB' if self.prepared is not None else 'IDLE'

    def _draft(self,kind,payload,original,*,domain='STORAGE',revision=1,cleanup=False):
        now=self.clock();fields=original.fields
        if not cleanup and now>=int(fields['expires_j_us']):return []
        return [dict(kind=kind,payload=payload,domain=domain,revision=revision,
            request_id=fields['request_id'],issued_us=now,expires_us=int(fields['expires_j_us']))]

    def _result(self,body,status):
        return dict(database_uuid=self.binding['database_uuid'],job_id=body['job_id'],job_digest=body['job_digest'],
            status=status,durable_revision=None,committed_j_us=None,origin_context_sha256=body.get('context_sha256'))

    def _before_sql(self,expires_us):
        if self.failed:raise PersistenceUnknown('D was fenced after an unknown database outcome')
        # 새 작업의 원 기한을 선택한다. 이전 SQL/transaction의 기한을 갱신하는 API는 아니다.
        self.database.expires_us=expires_us;self.database._deadline()

    def _arena(self):
        if self.arena is None:
            if type(self.handles) is not tuple or len(self.handles)!=2:raise PersistenceError('D has no inherited DB_COPY handles')
            self.arena=CopyArena(*self.handles,self.endpoint,self.peer.service_instance,clock_us=self.clock)
        return self.arena

    def _clear(self):
        self.prepared=self.owned=self.copy_lease=None

    def expire_unstarted(self):
        # LOAD READY는 S의 actual cleanup/release 전까지 남긴다. token을 추측해서 반환하지 않는다.
        if self.prepared is not None and self.clock()>=self.prepared.expires_us and (
                self.prepared.body['direction']=='CHECKPOINT' or self.copy_lease is None):self._clear()

    def bind_publication(self,packet):
        fields=packet.fields;now=self.clock();payload=fields['payload']
        if (fields['kind'],fields['binding_domain'],fields['service_instance'],fields['channel_epoch'],fields['sender_instance'])!=(
                'BIND','PUBLICATION',self.peer.service_instance,self.peer.channel_epoch,self.peer.service_instance):raise PersistenceError('publication source differs from registered S')
        issued,expires=decimal(fields['issued_j_us']),decimal(fields['expires_j_us'])
        if not issued<=now<expires<=issued+5_000_000:raise PersistenceError('publication BIND original deadline')
        base={'binding_revision','binding_sha256','binding'}
        if type(payload) is not dict or set(payload) not in (base,base|{'geometry_origin'}):raise PersistenceError('publication BIND fields')
        geometry_origin=None
        if 'geometry_origin' in payload:
            from ..storage.geometry_scope import PiGeometryOrigin
            origin=PiGeometryOrigin.from_fields(payload['geometry_origin'])
            if (origin.odom_epoch,origin.calibration)!=(payload['binding']['odom_epoch'],payload['binding']['calibration']):
                raise PersistenceConflict('publication origin differs from original odom/calibration')
            geometry_origin=canonical(origin.fields)
        revision=decimal(payload['binding_revision'],positive=True);binding=publication_binding(payload['binding'])
        actual=sha256(canonical(binding)).hexdigest();digest(payload['binding_sha256'])
        if (fields['binding_revision']!=str(revision) or binding['source_binding_revision']!=str(revision)
                or binding['service_instance']!=self.peer.service_instance or payload['binding_sha256']!=actual
                or revision<self.publication_revision or revision==self.publication_revision and (binding!=self.publication or geometry_origin!=self.geometry_origin)):
            raise PersistenceConflict('publication BIND changed its original identity/revision')
        if revision>self.publication_revision:
            if self.prepared is not None and self.prepared.body['direction']=='LOAD' and self.copy_lease is not None:
                raise PersistenceConflict('published LOAD copy must be cleaned before source replacement')
            self._clear();self.publication=binding;self.publication_revision=revision;self.geometry_origin=geometry_origin
        return self._draft('BIND_ACK',dict(binding_revision=str(revision),binding_sha256=actual,accepted=True,state=self.state,reason=None),
            packet,domain='PUBLICATION',revision=revision)

    def _source(self,job,lease):
        binding=self.publication
        if binding is None:raise PersistenceConflict('checkpoint requires the accepted original PublicationBinding')
        context=job.context
        if (context.source_binding!=canonical(binding) or context.geometry_origin!=self.geometry_origin or context.mapper_worker!=lease.producer.worker_instance
                or (context.mapper_worker,context.origin_mapper_instance,context.map_epoch,context.frame_epoch,context.odom_epoch,
                    context.configuration,context.calibration,context.primitive_sha256)!=(binding['mapper_worker'],binding['mapper_instance'],
                    binding['map_epoch'],binding['frame_epoch'],binding['odom_epoch'],binding['configuration'],binding['calibration'],binding['primitive'])
                or lease.raw_binding_sha256!=binding['raw_binding_sha256'] or lease.publication_binding_sha256!=sha256(canonical(binding)).hexdigest()):
            raise PersistenceConflict('checkpoint copy/source differs from current PublicationBinding')

    def _same_job(self,message):
        if self.prepared is None:raise PersistenceConflict('DB operation has no original PREPARE')
        original=self.prepared;body=message.body;held=original.body
        if (message.packet.fields['request_id']!=original.packet.fields['request_id']
                or message.expires_us!=original.expires_us or message.issued_us<original.issued_us
                or (body['database_uuid'],body['job_id'],body['job_digest'])!=(held['database_uuid'],held['job_id'],held['job_digest'])):
            raise PersistenceConflict('DB operation replaced original job/digest/lifetime')
        return held

    def handle_db(self,packet):
        self.expire_unstarted()
        message=parse_db_job(packet,self.peer,database_uuid=self.binding['database_uuid'],now_us=self.clock())
        body=message.body;op=message.op;result=[]
        if op=='PREPARE':
            if packet.fields['request_id']!=body['job_id']:raise PersistenceError('PREPARE request UUID must be original job UUID')
            if self.prepared is not None:
                if (message.body_bytes,message.issued_us,message.expires_us)!=(self.prepared.body_bytes,self.prepared.issued_us,self.prepared.expires_us):
                    raise PersistenceConflict('D already owns a different original PREPARE')
                return []
            if body['direction']=='CHECKPOINT' and (self.publication is None or body['origin_mapper_instance']!=self.publication['mapper_instance']):
                raise PersistenceConflict('checkpoint PREPARE has no current publication source')
            self.prepared=message
            return []
        if op=='READY':
            held=self._same_job(message)
            if held['direction']!='CHECKPOINT' or self.owned is not None:raise PersistenceConflict('READY requires one original uncopied checkpoint')
            lease=CopyLease.from_fields(body['copy_lease'])
            if lease.consumer!=self.endpoint:raise PersistenceConflict('copy reader is not current D')
            request,returned=self._arena().copy_owned(lease,body,expires_us=self.prepared.expires_us)
            self.copy_count+=1
            result+=self._draft('SLOT',lease.slot_payload('RELEASE'),packet,cleanup=True)
            if (type(request) is not CheckpointRequest or request.job.issued_us!=self.prepared.issued_us
                    or request.job.expires_us!=self.prepared.expires_us or prepare_payload(request)['body']!=held):
                raise PersistenceConflict('owned copy differs from original PREPARE')
            self._source(request.job,lease)
            if returned.posted_us>=self.prepared.expires_us:
                self._clear();return result
            self.owned=request;self.copy_lease=lease
            return result+self._draft('DB_JOB',db_payload('READY',body),packet)
        if op=='COMMIT':
            if self.prepared is None and self.last_commit is not None:
                original,old_lease,reply=self.last_commit;prior=original.body
                if (body['database_uuid'],body['job_id'],body['job_digest'],body['copy_lease'],packet.fields['request_id'],message.expires_us)!=(
                        prior['database_uuid'],prior['job_id'],prior['job_digest'],old_lease.fields,original.packet.fields['request_id'],original.expires_us):
                    raise PersistenceConflict('COMMIT replay differs from original completed transaction')
                return self._draft('DB_JOB',db_payload('RESULT',reply),packet)
            held=self._same_job(message);lease=CopyLease.from_fields(body['copy_lease'])
            if held['direction']!='CHECKPOINT' or self.owned is None or lease!=self.copy_lease:raise PersistenceConflict('COMMIT has no exact released immutable copy')
            request=self.owned;self._source(request.job,lease)
            try:
                self._before_sql(self.prepared.expires_us)
                receipt=self.database.persistence.commit(request.job,request.payload,current_context_sha256=held['context_sha256'])
                reply=receipt.fields
            except PersistenceUnknown:
                self.failed=True;reply=self._result(held,'UNKNOWN')
            except PersistenceCorrupt:
                self.failed=True;reply=self._result(held,'FAILED')
            self.last_commit=(self.prepared,lease,reply)
            self.completed+=1;self._clear()
            return self._draft('DB_JOB',db_payload('RESULT',reply),packet)
        if op=='QUERY':
            self._before_sql(message.expires_us)
            receipt=self.database.persistence.query(body['job_id'],body['job_digest'],expires_us=message.expires_us)
            reply=self._result(body,'ABSENT') if receipt is None else receipt.fields
            self.completed+=1
            return self._draft('DB_JOB',db_payload('RESULT',reply),packet)
        if op=='RESERVE_PROVENANCE':
            if self.publication is None or body['mapper_worker']!=self.publication['mapper_worker']:
                raise PersistenceConflict('provenance reserve requires the current registered M publication source')
            try:
                self._before_sql(message.expires_us)
                reply=self.database.persistence.reserve_provenance_wire(body,request_id=packet.fields['request_id'],
                    issued_us=message.issued_us,expires_us=message.expires_us)
            except PersistenceUnknown:
                self.failed=True;reply=dict(body);reply.pop('block_count');reply.update(start_inclusive=None,end_exclusive=None,highwater_after=None,result='UNKNOWN')
            except PersistenceConflict:
                reply=dict(body);reply.pop('block_count');reply.update(start_inclusive=None,end_exclusive=None,highwater_after=None,result='REJECTED')
            self.completed+=1
            return self._draft('DB_JOB',db_payload('RANGE_RESULT',reply),packet)
        raise PersistenceError('D accepts PREPARE/READY/COMMIT/QUERY/RESERVE_PROVENANCE only')

    def handle_slot(self,packet):
        fields=packet.fields;payload=fields['payload'];now=self.clock()
        if (fields['kind'],fields['binding_domain'],fields['binding_revision'],fields['service_instance'],fields['channel_epoch'],fields['sender_instance'])!=(
                'SLOT','STORAGE','1',self.peer.service_instance,self.peer.channel_epoch,self.peer.service_instance):raise PersistenceError('DB_COPY SLOT source/binding')
        lease=CopyLease.from_slot(self.peer.service_instance,payload);op=payload['op']
        if op in ('RELEASE','REVOKE'):
            if (self.copy_lease is None or self.prepared is None or self.prepared.body['direction']!='LOAD'
                    or lease.writer!=self.copy_lease.writer or fields['request_id']!=self.prepared.packet.fields['request_id']
                    or not self.prepared.issued_us<=int(fields['issued_j_us'])<=now
                    or int(fields['expires_j_us'])!=self.prepared.expires_us):raise PersistenceConflict('cleanup differs from original LOAD copy')
            # 원 consumer/S의 actual release/revoke 사실은 S registry에서 먼저 검증한다.
            self._clear();return []
        issued,expires=decimal(fields['issued_j_us']),decimal(fields['expires_j_us'])
        if (op!='GRANT' or lease.producer!=self.endpoint or lease.read_token is not None
                or not issued<=now<expires<=issued+5_000_000 or self.prepared is None
                or self.prepared.body['direction']!='LOAD' or self.copy_lease is not None
                or fields['request_id']!=self.prepared.packet.fields['request_id'] or expires!=self.prepared.expires_us
                or payload['payload_length'] is not None or payload['descriptor_sha256'] is not None):raise PersistenceConflict('LOAD requires original timely D writer GRANT')
        original=self.prepared;body=original.body
        request=load_from_prepare(self.binding,body,lease,original.issued_us,original.expires_us,publication=self.publication,geometry_origin=self.geometry_origin)
        if request.purpose=='CURRENT_MAP':
            binding=self.publication
            if lease.raw_binding_sha256!=binding['raw_binding_sha256'] or lease.publication_binding_sha256!=sha256(canonical(binding)).hexdigest():
                raise PersistenceConflict('current map LOAD lease differs from publication')
        elif lease.publication_binding_sha256 is not None:raise PersistenceConflict('bootstrap LOAD is provisional, not publication authority')
        try:
            self._before_sql(expires)
            loaded=self.database.persistence.load(request,current_context_sha256=body['context_sha256'])
            if type(loaded) is CommitReceipt:
                self._clear();return self._draft('DB_JOB',db_payload('RESULT',loaded.fields),packet)
            encoding=encode_load(loaded,lease)
            ready=self._arena().publish(lease,encoding,expires_us=expires)
        except PersistenceUnknown:
            self.failed=True;self._clear()
            return self._draft('DB_JOB',db_payload('RESULT',self._result(body,'UNKNOWN')),packet)
        self.copy_lease=lease;self.copy_count+=1;self.completed+=1
        return self._draft('DB_JOB',db_payload('READY',ready),packet)

    def close(self):
        if self.arena is not None:self.arena.close()
        self._clear()
