"""Opt-in manual-session-1: source samples/actions are not MQTT send counters.

The producer confirms a live driver session. Repeating transport publications
cannot renew the original sample age or invent an action after neutral.
"""
from dataclasses import dataclass
from .drive_binding import canonical
from .lifecycle import counter

PROFILE='manual-session-1'
KEYS=frozenset({'up','down','left','right','brake'})

@dataclass(frozen=True)
class Sample:
    session: str
    sample: int
    action: int
    generation: int
    keys: frozenset[str]
    valid: bool
    age_ms: int

def parse(payload):
    fields={'profile','session_id','sample_seq','action_seq','generation','keys','valid','age_ms'}
    if not isinstance(payload,dict) or set(payload)!=fields or payload['profile']!=PROFILE:
        raise ValueError('manual-session-1 fields required')
    session=canonical(payload['session_id']);sample=counter(payload['sample_seq']);action=counter(payload['action_seq'])
    generation=payload['generation'];age=payload['age_ms'];keys=payload['keys']
    if (not sample or type(generation) is not int or not 1<=generation<=0xffffffff or
        type(age) is not int or not 0<=age<=0x7fffffff or type(payload['valid']) is not bool or
        not isinstance(keys,list) or len(keys)>5 or any(not isinstance(k,str) or k not in KEYS for k in keys) or len(set(keys))!=len(keys)):
        raise ValueError('invalid manual source sample')
    return Sample(session,sample,action,generation,frozenset(keys),payload['valid'],age)

class SessionEvidence:
    """Actor-owned latest source sample; a duplicate never refreshes its age."""
    def __init__(self):self.value=None;self.received=None
    def observe(self,sample,received):
        old=self.value
        if old and old.session==sample.session:
            if sample.sample<old.sample or sample.action<old.action:return False
            if sample.sample==old.sample:return False
            if sample.valid and old.valid and sample.action==old.action and sample.keys!=old.keys:return False
        self.value=sample;self.received=received;return True
    def current(self,now):
        value=self.value
        return value if value and value.valid and self.received is not None and 0<=now-self.received and (
            value.age_ms+(now-self.received)*1000<=200) else None

class MovementGate:
    """Transition-local causal latch; no input history or permanent message queue."""
    def __init__(self,generation,session,policy):
        if policy not in ('CONTINUE_SESSION','NEUTRAL_THEN_ACTION'):raise ValueError('manual policy')
        self.generation=generation;self.session=canonical(session);self.policy=policy
        self.neutral_action=None;self.last=None;self.blocked=False;self.allowed=False
    def observe(self,sample):
        if self.blocked:return False
        if sample.session!=self.session or sample.generation!=self.generation:return False
        if not sample.valid:self.blocked=True;self.allowed=False;return False
        old=self.last
        if old and (sample.sample<old.sample or sample.action<old.action):return False
        if old and sample.sample==old.sample:return sample==old
        if old and sample.action==old.action and sample.keys!=old.keys:return False
        self.last=sample
        if self.policy=='CONTINUE_SESSION':self.allowed=True
        elif not sample.keys:self.neutral_action=sample.action;self.allowed=False
        elif self.neutral_action is not None and sample.action>self.neutral_action:self.allowed=True
        return True
