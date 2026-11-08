"""기존 DB_JOB exact variants의 유한 codec. SQLite/native lease 획득이나 승인은 없다."""

from dataclasses import dataclass

from .lifecycle_messages import LifecyclePeer
from .local_envelope import StructuralPacket
from .storage_copy import CopyLease
from ..storage.contracts import (LoadRequest,StorageContext,StorageJob,canonical,decimal,digest,json_bytes,text,uint,uuid)


FIELDS={
 'RESERVE_PROVENANCE':'database_uuid reservation_nonce mapper_worker block_count',
 'RANGE_RESULT':'database_uuid reservation_nonce mapper_worker start_inclusive end_exclusive highwater_after result',
 'PREPARE':'database_uuid job_id job_digest direction tile_count context_sha256 origin_mapper_instance scene_commit_nonce load_purpose checkpoint_ref load_selection',
 'READY':'database_uuid job_id job_digest copy_lease manifest_length manifest_sha256 provenance_length provenance_sha256 provenance_count context_count',
 'COMMIT':'database_uuid job_id job_digest copy_lease',
 'QUERY':'database_uuid job_id job_digest',
 'RESULT':'database_uuid job_id job_digest status durable_revision committed_j_us origin_context_sha256',
}


def validate_body(op,body):
    if op not in FIELDS or type(body) is not dict or set(body)!=set(FIELDS[op].split()):raise ValueError('exact supported DB_JOB variant required')
    uuid(body['database_uuid'])
    if op in ('RESERVE_PROVENANCE','RANGE_RESULT'):
        uuid(body['reservation_nonce']);uuid(body['mapper_worker'])
        if op=='RESERVE_PROVENANCE':uint(body['block_count'],1<<20,positive=True)
        else:
            if body['result'] not in ('GRANTED','REPLAY','REJECTED','UNKNOWN'):raise ValueError('range result enum')
            keys=('start_inclusive','end_exclusive','highwater_after')
            if body['result'] in ('GRANTED','REPLAY'):
                start,end,head=(decimal(body[key],positive=True) for key in keys)
                if not start<end==head:raise ValueError('range result bounds')
            elif any(body[key] is not None for key in keys):raise ValueError('unapproved range has no start/end/head')
        return
    uuid(body['job_id']);digest(body['job_digest'])
    if op=='PREPARE':
        uint(body['tile_count'],32);digest(body['context_sha256']);uuid(body['origin_mapper_instance'])
        if body['scene_commit_nonce'] is not None:uuid(body['scene_commit_nonce'])
        if body['direction']=='CHECKPOINT':
            if any(body[key] is not None for key in ('load_purpose','checkpoint_ref','load_selection')):raise ValueError('checkpoint LOAD fields must be null')
        elif body['direction']=='LOAD':
            if body['load_purpose'] not in ('BOOTSTRAP_RESTORE','CURRENT_MAP') or type(body['checkpoint_ref']) is not dict or type(body['load_selection']) is not dict:
                raise ValueError('LOAD original selected ref/purpose/selection required')
        else:raise ValueError('DB direction')
    elif op in ('READY','COMMIT'):
        CopyLease.from_fields(body['copy_lease'])
        if op=='READY':
            uint(body['manifest_length'],16384,positive=True);digest(body['manifest_sha256'])
            uint(body['provenance_length'],5*1024*1024,positive=True);digest(body['provenance_sha256'])
            uint(body['provenance_count'],16384);uint(body['context_count'],64)
    elif op=='RESULT':
        if body['status'] not in ('COMMITTED','DUPLICATE','ABSENT','UNKNOWN','FAILED'):raise ValueError('DB result status')
        if body['status'] in ('COMMITTED','DUPLICATE'):
            decimal(body['durable_revision'],positive=True);decimal(body['committed_j_us']);digest(body['origin_context_sha256'])
        else:
            if body['durable_revision'] is not None or body['committed_j_us'] is not None:raise ValueError('uncommitted RESULT has no durable revision/time')
            if body['origin_context_sha256'] is not None:digest(body['origin_context_sha256'])


def db_payload(op,body):
    validate_body(op,body)
    result=dict(op=op,body=body)
    if len(canonical(result))>3500:raise ValueError('DB_JOB payload exceeds original envelope budget')
    return result


@dataclass(frozen=True,slots=True)
class DbMessage:
    packet: StructuralPacket
    op: str
    body_bytes: bytes
    issued_us: int
    expires_us: int

    @property
    def body(self):return json_bytes(self.body_bytes,3500)


def parse_db_job(packet,peer,*,database_uuid,now_us,binding_revision=1,child_sender=False,allow_expired=False):
    """allow_expired는 원 reply의 history/cleanup 해석만 허용한다. current/새 grant 검사는 별도다."""
    if type(packet) is not StructuralPacket or type(peer) is not LifecyclePeer or peer.role not in ('D','M'):raise ValueError('registered storage participant packet required')
    fields=packet.fields
    expected=('DB_JOB','STORAGE',str(binding_revision),peer.service_instance,peer.channel_epoch,
              peer.worker_instance if child_sender else peer.service_instance)
    if tuple(fields[key] for key in ('kind','binding_domain','binding_revision','service_instance','channel_epoch','sender_instance'))!=expected:
        raise ValueError('DB_JOB differs from registered peer/domain/binding')
    issued,expires=decimal(fields['issued_j_us']),decimal(fields['expires_j_us'])
    if not issued<=now_us or not issued<expires<=issued+5_000_000 or not allow_expired and now_us>=expires:
        raise ValueError('DB_JOB original lifetime expired or renewed')
    payload=fields['payload']
    if type(payload) is not dict or set(payload)!= {'op','body'}:raise ValueError('DB_JOB outer body')
    validate_body(payload['op'],payload['body'])
    if payload['body']['database_uuid']!=database_uuid:raise ValueError('DB_JOB database UUID changed')
    if payload['op']=='RESULT' and payload['body']['committed_j_us'] is not None and decimal(payload['body']['committed_j_us'])>issued:
        raise ValueError('future committed stamp is not an actual D reply')
    return DbMessage(packet,payload['op'],canonical(payload['body']),issued,expires)


def prepare_payload(request):
    from ..storage.port import CheckpointRequest
    if type(request) is CheckpointRequest:
        job=request.job;direction='CHECKPOINT';count=len(request.payload.tiles);purpose=reference=selection=None
    elif type(request) is LoadRequest:
        job=request.job;direction='LOAD';purpose=request.purpose
        reference=json_bytes(request.checkpoint_ref,2048);selection=json_bytes(request.selection,512)
        count=1 if selection['kind']=='TILE' else min(32,max(0,reference['tile_count']-selection['cursor']))
    else:raise ValueError('exact checkpoint or LOAD request required')
    return db_payload('PREPARE',dict(database_uuid=job.context.database_uuid,job_id=job.job_id,job_digest=request.digest,
        direction=direction,tile_count=count,context_sha256=job.context.sha256,origin_mapper_instance=job.context.origin_mapper_instance,
        scene_commit_nonce=job.scene_commit_nonce,load_purpose=purpose,checkpoint_ref=reference,load_selection=selection))


def publication_binding(value):
    """원 source identity만 검사한다. scene/metric/이동 사용권을 발급하지 않는다."""
    names='service_instance mapper_worker mapper_instance raw_binding_sha256 map_epoch frame_epoch odom_epoch configuration calibration primitive scene_admission_ref source_binding_revision'
    if type(value) is not dict or set(value)!=set(names.split()):raise ValueError('exact PublicationBinding fields required')
    for key in ('service_instance','mapper_worker','mapper_instance','map_epoch','frame_epoch','odom_epoch'):uuid(value[key])
    for key in ('raw_binding_sha256','primitive'):digest(value[key])
    for key in ('configuration','calibration'):text(value[key])
    decimal(value['source_binding_revision'],positive=True)
    if value['scene_admission_ref'] is not None and type(value['scene_admission_ref']) is not dict:raise ValueError('scene ref must be original object or live-only null')
    if len(canonical(value))>2048:raise ValueError('PublicationBinding cap')
    return value


def make_load_request(storage_binding,*,mapper_worker,origin_mapper_instance,job_id,issued_us,expires_us,
                      checkpoint_ref,selection,purpose='BOOTSTRAP_RESTORE',publication=None,scene_commit_nonce=None,geometry_origin=None):
    """S/M producer와 D consumer의 동일 LOAD context 생성. 현재 Pi boot 검사는 S 책임이다.

    BOOTSTRAP_RESTORE의 provisional context는 원 StorageBinding/ref/등록 M을 묶고,
    CURRENT_MAP은 이미 수락한 원 PublicationBinding을 그대로 source_binding에 보존한다.
    """
    if type(storage_binding) is not dict or set(storage_binding)!= {'service_instance','storage_worker','storage_profile_sha256','database_uuid','db_schema_version'}:
        raise ValueError('exact admitted StorageBinding required')
    for key in ('service_instance','storage_worker','database_uuid'):uuid(storage_binding[key])
    digest(storage_binding['storage_profile_sha256'])
    if storage_binding['db_schema_version']!=1:raise ValueError('storage schema1 required')
    uuid(mapper_worker);uuid(origin_mapper_instance)
    reference=checkpoint_ref
    if purpose=='CURRENT_MAP':
        binding=publication_binding(publication)
        if (binding['service_instance'],binding['mapper_worker'],binding['mapper_instance'])!=(storage_binding['service_instance'],mapper_worker,origin_mapper_instance):
            raise ValueError('LOAD publication differs from registered M')
        values={key:binding[key] for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration')}
        values.update(pi_host_boot=reference['pi_host_boot'],primitive_sha256=binding['primitive'])
        original=canonical(binding)
    elif purpose=='BOOTSTRAP_RESTORE':
        values={key:reference[key] for key in ('map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256')}
        original=canonical(dict(storage_binding=storage_binding,mapper_worker=mapper_worker,
            origin_mapper_instance=origin_mapper_instance,load_purpose=purpose,checkpoint_ref=reference,load_selection=selection))
    else:raise ValueError('unsupported LOAD purpose')
    if purpose=='BOOTSTRAP_RESTORE' and geometry_origin is not None:raise ValueError('provisional restore has no current geometry-origin authority')
    context=StorageContext(storage_binding['database_uuid'],mapper_worker,origin_mapper_instance,source_binding=original,
        geometry_origin=geometry_origin,**values)
    job=StorageJob(context,job_id,issued_us,expires_us,scene_commit_nonce)
    return LoadRequest(job,canonical(reference),canonical(selection),purpose)


def load_from_prepare(storage_binding,prepare,lease,issued_us,expires_us,*,publication=None,geometry_origin=None):
    validate_body('PREPARE',prepare)
    if prepare['direction']!='LOAD' or type(lease) is not CopyLease:raise ValueError('original LOAD writer lease required')
    request=make_load_request(storage_binding,mapper_worker=lease.consumer.worker_instance,
        origin_mapper_instance=prepare['origin_mapper_instance'],job_id=prepare['job_id'],issued_us=issued_us,expires_us=expires_us,
        checkpoint_ref=prepare['checkpoint_ref'],selection=prepare['load_selection'],purpose=prepare['load_purpose'],
        publication=publication,scene_commit_nonce=prepare['scene_commit_nonce'],
        geometry_origin=geometry_origin if prepare['load_purpose']=='CURRENT_MAP' else None)
    if request.job.context.sha256!=prepare['context_sha256'] or request.digest!=prepare['job_digest']:
        raise ValueError('LOAD context/digest differs from original request')
    return request


provisional_load_request=load_from_prepare
