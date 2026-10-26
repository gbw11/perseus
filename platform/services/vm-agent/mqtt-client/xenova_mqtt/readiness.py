"""Verified-drive readiness payload. Reports describe app work, never authority."""
from __future__ import annotations
from dataclasses import dataclass

REASONS=frozenset({'CONTROL_UNAPPLIED','INPUT_UNAVAILABLE','INPUT_STALE','INPUT_INVALID',
                   'STATE_PREPARING','CALCULATION_FAILED','OUTPUT_INVALID'})

@dataclass(frozen=True)
class Readiness:
    generation: int
    progress: int
    state: str
    reason: str | None


def parse(payload: object) -> Readiness:
    if not isinstance(payload,dict) or set(payload)!={'applied_generation','progress_seq','readiness','reason'}:
        raise ValueError('invalid readiness fields')
    generation,progress=payload['applied_generation'],payload['progress_seq']
    if type(generation) is not int or not 0<=generation<=0xffffffff:
        raise ValueError('invalid applied generation')
    if (not isinstance(progress,str) or not progress.isascii() or not progress.isdecimal() or
        len(progress)>20 or (len(progress)>1 and progress[0]=='0') or int(progress)>0xffffffffffffffff):
        raise ValueError('progress_seq must be a canonical uint64 decimal string')
    state,reason=payload['readiness'],payload['reason']
    if state not in ('PREPARING','READY','NOT_READY') or ((state=='READY') != (reason is None)):
        raise ValueError('readiness state/reason conflict')
    if reason is not None and reason not in REASONS:raise ValueError('unknown readiness reason')
    if state=='READY' and (generation==0 or int(progress)==0):raise ValueError('READY requires applied control and completed work')
    return Readiness(generation,int(progress),state,reason)
