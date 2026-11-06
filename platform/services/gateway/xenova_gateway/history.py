"""Bounded terminal history, collected only at durable admission.

No timer releases an exclusion. Results last through the original deadline and
the explicitly supplied retention duration. Time-bearing IDs close the otherwise
unbounded arbitrary-UUID dedup problem. Policy values are deployment inputs.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from .ipc_contract import integer, request_key, uuid

CAPACITY = 65536
ROW_RESERVE = 4096  # Bound for supported spec + complete app/device evidence.


@dataclass(frozen=True)
class HistoryPolicy:
    retry_ms: int
    retention_ms: int

    def __post_init__(self):
        integer(self.retry_ms, minimum=1)
        integer(self.retention_ms, minimum=1)

    @classmethod
    def load(cls, path, *, uid=0):
        fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077 or info.st_size > 1024:
                raise ValueError('private owned bounded history policy required')
            from .ipc_contract import decode
            value = decode(stream.read(1025))
        if set(value) != {'retry_ms', 'retention_ms'}:
            raise ValueError('history policy fields invalid')
        return cls(**value)


def validate(value):
    item = value.get('history')
    if item is not None:
        if not isinstance(item, dict) or set(item) != {'host_boot_id', 'floor_ms', 'legacy_closed'}:
            raise ValueError('history fence invalid')
        uuid(item['host_boot_id']); integer(item['floor_ms'])
        if item['legacy_closed'] is not True:
            raise ValueError('legacy admission fence invalid')
    for name in ('transitions', 'protections'):
        for row in value.get(name, []):
            key=request_key(row['request_id'])
            if key:
                if item is None or key[0]!=row['host_boot_id']:
                    raise ValueError('request issuance host mismatch')
                integer(row['retry_until_ms'], minimum=row['spec']['deadline_ms'])
                integer(row['retain_until_ms'], minimum=row['retry_until_ms'])


def stamp(store, row):
    key=request_key(row['request_id'])
    if key:
        retry=key[1]+store.history_policy.retry_ms
        return {**row, 'retry_until_ms':retry,
                'retain_until_ms':max(retry,row['spec']['deadline_ms']+store.history_policy.retention_ms)}
    return row


def candidate(store, now_ms):
    """Return a candidate only; failed admission/write frees no RAM or disk."""
    policy = store.history_policy
    if policy is None:
        return {}
    old = store.value.get('history', {})
    floor = old.get('floor_ms', 0) if old.get('host_boot_id') == store.host else 0
    floor = max(floor, max(0, now_ms - policy.retry_ms))
    pending = (store.value.get('pending') or {}).get('transition_id')
    result = dict(history=dict(host_boot_id=store.host, floor_ms=floor, legacy_closed=True))
    for name in ('transitions', 'protections'):
        kept = []
        for row in store.value.get(name, []):
            same_host = row['host_boot_id'] == store.host
            active = row['phase'] in ('PROTECTED', 'PREPARING', 'PREPARED')
            if name == 'protections' and not same_host:
                # S15P31E110-346: an earlier host's protection no longer blocks reselection
                # (Authority.protected) and cannot be finished/cancelled, so it is history only.
                active = False
            spec = row['spec']
            deadline = spec['deadline_ms']
            # A pending committed transition still owns application evidence.
            referenced = row['request_id'] == pending and store.value['phase'] == 'PENDING'
            expired = (not same_host or now_ms >= row.get('retain_until_ms',deadline + policy.retention_ms))
            if active or referenced or not expired:
                kept.append(row)
            else:
                key=request_key(row['request_id'])
                if key and same_host:
                    result['history']['floor_ms']=max(result['history']['floor_ms'],key[1])
        result[name] = kept
    return result


def admission(store, identity, spec, now_ms, fields):
    """Validate a new key. Existing records are compared before this boundary."""
    key = request_key(identity)
    if key is None:
        if store.history_policy is not None or store.value.get('history'):
            return 'LEGACY_KEY_NOT_ADMISSIBLE'
        return None
    if store.history_policy is None:
        return 'HISTORY_POLICY_REQUIRED'
    host, issued = key
    if host != store.host:
        return 'HOST_CHANGED'
    if issued > now_ms:
        return 'REQUEST_TIME_INVALID'
    if max(issued+store.history_policy.retry_ms,
           spec['deadline_ms']+store.history_policy.retention_ms)>2**63-1:
        return 'REQUEST_TIME_INVALID'
    fence = fields.get('history', store.value.get('history', {}))
    floor = fence.get('floor_ms', 0) if fence.get('host_boot_id') == store.host else 0
    if issued <= floor or now_ms >= issued + store.history_policy.retry_ms:
        return 'REQUEST_WINDOW_EXPIRED'
    if spec['deadline_ms'] > issued + store.history_policy.retry_ms:
        return 'REQUEST_DEADLINE_OUTSIDE_WINDOW'
    return None


def room(value):
    """Reserve supported terminal evidence at admission, not after its effect."""
    base = {k: v for k, v in value.items() if k not in ('transitions', 'protections', 'pending')}
    size = len(json.dumps(base, separators=(',', ':')).encode()) + ROW_RESERVE
    for name in ('transitions', 'protections'):
        for row in value.get(name, []):
            length = len(json.dumps(row, separators=(',', ':')).encode())
            if length > ROW_RESERVE:
                return False
            size += ROW_RESERVE
    return size <= CAPACITY


def status(store, now_ms):
    proposed = candidate(store, now_ms)
    return dict(enabled=store.history_policy is not None,
                retry_ms=store.history_policy.retry_ms if store.history_policy else None,
                retention_ms=store.history_policy.retention_ms if store.history_policy else None,
                legacy_new_requests=store.history_policy is None and not store.value.get('history'),
                serialized_bytes=len(json.dumps(store.value, separators=(',', ':')).encode()),
                byte_limit=CAPACITY, record_reserve_bytes=ROW_RESERVE,
                records={name: dict(retained=len(store.value.get(name, [])),
                    eligible=len(store.value.get(name, []))-len(proposed.get(name, store.value.get(name, []))))
                    for name in ('transitions', 'protections')})
