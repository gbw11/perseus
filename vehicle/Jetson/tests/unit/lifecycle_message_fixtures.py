"""초기4kind의 합성 producer 진술. 실제 peer/send/exit 사실을 만들지 않는다."""

import json
from envelope_fixtures import fields
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer


REQUEST = '00000000-0000-4000-8000-000000000004'


def peer(role='A'):
    return LifecyclePeer('00000000-0000-4000-8000-000000000001',
                         '00000000-0000-4000-8000-000000000002',
                         '00000000-0000-4000-8000-000000000003',role,'a'*64)


def message_fields(kind, *, current=None, sequence=1, issued=100, expires=200,
                   request_id=REQUEST, revision=1, drain=180, payload=None):
    current = peer() if current is None else current
    if payload is None:
        if kind == 'HELLO':
            payload = dict(role=current.role,worker_instance=current.worker_instance,
                           release_sha256=current.release_sha256,capability_mask='0')
        elif kind == 'HEARTBEAT':
            payload = dict(worker_state='STARTING',last_progress_sequence=None,last_progress_j_us=None)
        elif kind == 'STOP':
            payload = dict(stop_revision=str(revision),reason='operator',drain_deadline_j_us=str(drain))
        elif kind == 'STOPPED':
            payload = dict(stop_revision=str(revision),leases_released=True,
                           device_closed=True if current.role=='A' else None,
                           checkpoint_job=None,status='STOPPED')
        else:
            payload = {}
    result = fields(kind,sequence,payload=payload)
    result.update(service_instance=current.service_instance,channel_epoch=current.channel_epoch,
                  sender_instance=current.service_instance if kind=='STOP' else current.worker_instance,
                  issued_j_us=str(issued),expires_j_us=str(expires),request_id=request_id)
    return result


def packet(kind, **kwargs):
    return decode_structure(json.dumps(message_fields(kind,**kwargs),sort_keys=True,ensure_ascii=False,
                                       separators=(',', ':'),allow_nan=False).encode('utf-8'))
