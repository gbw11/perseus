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
    profile: str = PROFILE
    state: int = 0
    source_pi_us: int = 0
    expires_pi_us: int = 0
    kind: str = ''
    input_epoch: str | None = None
    grant_revision: int = 0
    reason: str = ""

def parse(payload):
    if isinstance(payload,dict) and payload.get('profile') in ('manual-input-2','manual-input-3'):
        return parse_current(payload)
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


def parse_current(payload):
    fields={'profile','session_id','producer_kind','sample_seq','action_seq','state_seq','generation',
        'keys','valid','source_pi_us','action_expires_pi_us','state_reason'}
    held=payload['profile']=='manual-input-3'
    if held:fields|={'source_instance','input_epoch','device_instance','grant_revision','key_event_seq'}
    if set(payload)!=fields:raise ValueError('exact manual input profile fields')
    session=canonical(payload['session_id']);keys=payload['keys'];kind=payload['producer_kind']
    numbers={key:counter(payload[key]) for key in ('sample_seq','action_seq','state_seq','source_pi_us','action_expires_pi_us')}
    generation=payload['generation']
    if (type(generation) is not int or not 1<=generation<=0xffffffff or type(payload['valid']) is not bool
            or not isinstance(keys,list) or len(keys)>5 or len(set(keys))!=len(keys) or any(k not in KEYS for k in keys)
            or not numbers['state_seq'] or kind not in (('HELD',) if held else ('NEUTRAL_ONLY','PULSE'))):
        raise ValueError('manual source value')
    reasons=('NEUTRAL','USER_KEYS','HELD_SAMPLE','USER_RELEASE','SOURCE_LOST','LEASE_EXPIRED','REVOKED','REARM_REQUIRED') if held else (
        'NEUTRAL','USER_NUDGE','LEASE_EXPIRED','USER_NEUTRAL','REVOKED')
    if payload['state_reason'] not in reasons or not payload['valid'] and keys:raise ValueError('manual state reason')
    if kind=='NEUTRAL_ONLY' and (keys or numbers['action_seq'] or numbers['action_expires_pi_us'] or payload['state_reason']!='NEUTRAL'):
        raise ValueError('neutral source cannot imply a human action')
    epoch=None;grant=0
    if held:
        for key in ('source_instance','input_epoch','device_instance'):canonical(payload[key])
        epoch=payload['input_epoch'];grant=counter(payload['grant_revision']);counter(payload['key_event_seq'])
        if not grant:raise ValueError('HELD grant required')
    if keys and (not numbers['sample_seq'] or not numbers['source_pi_us'] or
            not numbers['source_pi_us']<numbers['action_expires_pi_us'] or held and
            numbers['action_expires_pi_us']>numbers['source_pi_us']+150000):raise ValueError('original source expiry')
    return Sample(session,numbers['sample_seq'],numbers['action_seq'],generation,frozenset(keys),payload['valid'],0,
        payload['profile'],numbers['state_seq'],numbers['source_pi_us'],numbers['action_expires_pi_us'],kind,epoch,grant,payload['state_reason'])

class SessionEvidence:
    """Actor-owned latest source sample; a duplicate never refreshes its age."""
    def __init__(self):self.value=None;self.received=None
    def observe(self,sample,received):
        old=self.value
        if old and old.session==sample.session:
            if sample.profile!=PROFILE:
                if sample.state<=old.state:return False
                reset=sample.grant_revision>old.grant_revision and not sample.valid and not sample.keys
                if not reset and (sample.sample<old.sample or sample.action<old.action):return False
                if sample.valid and old.valid and sample.action==old.action and sample.keys!=old.keys and sample.keys:return False
                self.value=sample;self.received=received;return True
            if sample.sample<old.sample or sample.action<old.action:return False
            if sample.sample==old.sample:return False
            if sample.valid and old.valid and sample.action==old.action and sample.keys!=old.keys:return False
        self.value=sample;self.received=received;return True
    def current(self,now):
        value=self.value
        if value and value.profile!=PROFILE:
            stamp=int(now*1e6)
            return value if value.valid and 0<=stamp-value.source_pi_us<=200000 and (
                not value.keys or stamp<value.expires_pi_us) else None
        return value if value and value.valid and self.received is not None and 0<=now-self.received and (
            value.age_ms+(now-self.received)*1000<=200) else None

class MovementGate:
    """Transition-local causal latch; no input history or permanent message queue."""
    def __init__(self,generation,session,policy):
        if policy not in ('CONTINUE_SESSION','NEUTRAL_THEN_ACTION'):raise ValueError('manual policy')
        self.generation=generation;self.session=canonical(session);self.policy=policy
        self.neutral_action=None;self.last=None;self.blocked=False;self.allowed=False
    def observe(self,sample):
        if self.blocked:
            if (sample.profile!=PROFILE and self.last is not None and sample.session==self.session
                    and sample.grant_revision>self.last.grant_revision and not sample.valid and not sample.keys):
                self.last=sample;self.blocked=False;self.allowed=False;self.neutral_action=None;return True
            return False
        if sample.session!=self.session or (sample.generation!=self.generation and not (
                self.policy=='CONTINUE_SESSION' and sample.profile!=PROFILE and sample.generation<self.generation)):
            return False
        if not sample.valid:
            if sample.profile!=PROFILE:
                self.last=sample
                if sample.reason=='USER_RELEASE' and not sample.keys:
                    self.allowed=False;self.neutral_action=sample.action;return True
            self.blocked=True;self.allowed=False;return False
        old=self.last
        if old and (sample.sample<old.sample or sample.action<old.action):return False
        if old and (sample.state<=old.state if sample.profile!=PROFILE else sample.sample==old.sample):return sample==old
        if old and sample.action==old.action and sample.keys!=old.keys and (sample.profile==PROFILE or sample.keys):return False
        self.last=sample
        if self.policy=='CONTINUE_SESSION':self.allowed=True
        elif not sample.keys:self.neutral_action=sample.action;self.allowed=False
        elif self.neutral_action is not None and sample.action>self.neutral_action:self.allowed=True
        return True
