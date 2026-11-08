"""d1의 합성 기록/진술. OS 관측·실제 DB 배치·자원 생성 증거가 아니다."""

from dataclasses import replace
import json

from xenova_vehicle.runtime.resource_manifest import ProcessBirth, ResourceManifest, RuntimeScope, SLOTS
from xenova_vehicle.runtime.resource_guard import (
    CgroupObservation, CurrentGuardScope, GuardEvidence, LoadOutcome, NameObservation, OwnerObservation,
)


def identifier(number):
    return '00000000-0000-4000-8000-{:012x}'.format(number)


def encode(fields):
    return (json.dumps(fields, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def runtime():
    return RuntimeScope(identifier(1), 11, 12, 13, 14, 15, 'tmpfs')


def minimal_fields():
    return dict(version=1, runtime=dict(boot_id=identifier(1), pid_ns_inode='11', mnt_ns_inode='12',
                                       shm_mount_id='13', shm_dev='14', shm_root_inode='15', shm_fs='tmpfs'),
                service_instance=identifier(2), resource_epoch=identifier(3), revision='1', state='DIRTY',
                owners=[dict(role='S', phase='CURRENT', pid=100, start_ticks='1000')],
                cgroup=dict(mode='SYSTEMD_V1', path='/xenova-synthetic.service', device='1', inode='2'),
                resources={s: dict(state='NOT_ATTEMPTED', name=None, object_id=None, size_bytes=None) for s in SLOTS},
                intents=[], recovery_episode=dict(episode_id=identifier(4), service_attempts=0,
                                                  role_attempts=dict(A=0, M=0, D=0)))


def full_fields():
    fields = minimal_fields()
    fields['owners'] = [dict(role=role, phase='CURRENT', pid=100 + n, start_ticks=str(1000 + n))
                        for n, role in enumerate(('S', 'A', 'M', 'D', 'TRACKER'))]
    sizes = (4194304, 4194304, 8388608, 5242880, 4096, 16384)
    for n, slot in enumerate(SLOTS):
        if n < 6:
            object_id = identifier(100 + n)
            fields['resources'][slot] = dict(state='KNOWN', object_id=object_id, size_bytes=str(sizes[n]),
                                            name=('/xnv_raw_' if slot.startswith('RAW') else '/xnv_db_') + object_id)
        else:
            fields['resources'][slot] = dict(state='KNOWN', name='/mp-synthetic_' + str(n), object_id=None, size_bytes=None)
    return fields


def pending_fields(state='INTENT', slot='RAW_CURRENT'):
    fields = full_fields()
    fields['resources'][slot] = dict(state=state, name=None, object_id=None, size_bytes=None)
    shm = slot in SLOTS[:6]
    object_id = identifier(9000) if shm else None
    fields['intents'] = [dict(intent_id=identifier(700), slot=slot, operation='CREATE', planned_object_id=object_id,
                              planned_name=(('/xnv_raw_' if slot.startswith('RAW') else '/xnv_db_') + object_id) if shm else None,
                              planned_size_bytes='4194304' if shm else None)]
    return fields


def maximum_fields():
    fields = pending_fields('UNKNOWN')
    maximum = str(2**64 - 1)
    fields['state'] = 'CLEANUP_CANDIDATE'
    fields['revision'] = maximum
    fields['runtime'].update({n: maximum for n in fields['runtime'] if n not in ('boot_id', 'shm_fs')})
    fields['cgroup'].update(path='/' + '\\' * 191, device=maximum, inode=maximum)
    owners = []
    for role in ('S', 'A', 'M', 'D', 'TRACKER'):
        for phase in (('CURRENT',) if role == 'S' else ('CURRENT', 'RETIRED')):
            owners.append(dict(role=role, phase=phase, pid=2**32 - 9 + len(owners), start_ticks=maximum))
    fields['owners'] = owners
    for n, slot in enumerate(SLOTS):
        resource = fields['resources'][slot]
        if resource['state'] == 'KNOWN':
            if n < 6:
                resource['size_bytes'] = maximum
            else:
                resource['name'] = '/' + 'a' * 126 + str(n - 6)
    fields['intents'][0]['planned_size_bytes'] = maximum
    fields['recovery_episode'].update(service_attempts=5, role_attempts=dict(A=5, M=5, D=5))
    return fields


def manifest(fields=None):
    return ResourceManifest(encode(minimal_fields() if fields is None else fields))


def guard(m=None, *, same_owner=False, scope=None, guard_number=601, service_number=600, pid=900, start_ticks=9000):
    m = manifest() if m is None else m
    current_runtime = m.runtime if scope is None else scope
    birth = next(o.birth for o in m.owners if o.role == 'S') if same_owner else ProcessBirth(pid, start_ticks)
    return CurrentGuardScope(identifier(guard_number), m.service_instance if same_owner else identifier(service_number),
                             current_runtime, birth)


def load(m=None, *, kind='VALID', load_number=701):
    return LoadOutcome(identifier(load_number), kind, (manifest() if m is None else m) if kind == 'VALID' else None)


def old_evidence(m, g):
    return GuardEvidence(owners=tuple(OwnerObservation(m.ref, g, o, 'EXITED') for o in m.known_owners),
                         cgroup=CgroupObservation(m.ref, g, m.cgroup, 'COMPLETE', (g.birth,)),
                         names=tuple(NameObservation(m.ref, g, r, 'ABSENT') for r in m.known_resources))
