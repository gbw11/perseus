"""Explicit warm-drive-1 evidence. Supervision is not calculation or authority."""
from __future__ import annotations
from dataclasses import dataclass
from .readiness import REASONS

PROFILE = 'warm-drive-1'
PHASES = frozenset({'WAITING', 'ACTIVATING', 'EXECUTING', 'STOPPING', 'RESIDUAL'})
LATTICE_REASONS=frozenset({'BUSY','MAP_UNAVAILABLE','WORLD_UNAVAILABLE','POSE_UNAVAILABLE',
    'PATH_UNAVAILABLE','PATH_INVALIDATED','OBSERVATION_EXPIRED','CONTEXT_CHANGED','STEERING_UNAVAILABLE',
    'CLOCK_ERROR','SEARCH_NOT_FOUND','NODE_BUDGET','WORK_BUDGET','DEADLINE','CANCELLED','NUMERIC_ERROR',
    'COLLISION','PATH_LIMIT','STATE_REJECTED','RESOURCE_LIMIT','STATE_FRAME_CHANGED',
    'STATE_CONFIGURATION_CHANGED','STATE_REFERENCE_CHANGED'})

def counter(value: object) -> int:
    if (not isinstance(value, str) or not value.isascii() or not value.isdecimal() or
        len(value)>20 or (len(value)>1 and value[0]=='0') or int(value)>2**64-1):
        raise ValueError('canonical uint64 string required')
    return int(value)

@dataclass(frozen=True)
class Lifecycle:
    generation: int
    check: int
    progress: int
    phase: str
    readiness: str
    reason: str | None

def parse(payload: object) -> Lifecycle:
    if not isinstance(payload, dict) or set(payload)!={'profile','applied_generation',
            'check_seq','progress_seq','phase','readiness','reason'} or payload['profile']!=PROFILE:
        raise ValueError('warm profile fields required')
    generation=payload['applied_generation']
    if type(generation) is not int or not 0<=generation<=0xffffffff:
        raise ValueError('invalid generation')
    check,progress=counter(payload['check_seq']),counter(payload['progress_seq'])
    phase,state,reason=payload['phase'],payload['readiness'],payload['reason']
    if phase not in PHASES or state not in ('QUALIFIED','READY','NOT_READY') or not check:
        raise ValueError('invalid lifecycle state')
    if reason is not None and reason not in REASONS|LATTICE_REASONS|{'APP_STOPPING','RESOURCE_RETAINED'}:
        raise ValueError('invalid reason')
    if (state=='NOT_READY') != (reason is not None):raise ValueError('state/reason conflict')
    if state=='QUALIFIED' and (phase!='WAITING' or not generation):
        raise ValueError('qualification requires applied waiting context')
    if state=='READY' and (phase!='EXECUTING' or not generation or not progress):
        raise ValueError('activation requires actual completed work')
    return Lifecycle(generation,check,progress,phase,state,reason)
