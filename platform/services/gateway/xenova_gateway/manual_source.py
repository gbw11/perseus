"""Gateway의 단일 driver·원 input ring8. 재전달/제어 승계는 원 source E를 갱신하지 않는다."""
from collections import OrderedDict
from dataclasses import dataclass
from uuid import uuid4
import math

from xenova_mqtt import topics
from xenova_mqtt.manual_session import parse
from .operator_contract import OperatorError,decimal
from .authority_journal import canonical


@dataclass(frozen=True)
class ManualProfile:
    forward_mps: float
    reverse_mps: float
    steer_rad: float

    def __post_init__(self):
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in
                (self.forward_mps,self.reverse_mps,self.steer_rad)):raise ValueError('approved manual limits required')


class DriverRegistry:
    def __init__(self,operator,profile=None):
        self.operator=operator;self.runner=operator.runner;self.profile=profile
        self.sid=str(uuid4());self.kind='NEUTRAL_ONLY';self.epoch=str(uuid4());self.grant=1
        self.source=self.device=self.channel=self.link=None;self.alias=None
        self.state='ARMED_NEUTRAL';self.reason='NEUTRAL';self.sample=self.action=self.sequence=0
        self.source_us=self.expires=self.key_event=0;self.keys=[];self.valid=True
        self.last_publish=0;self.ring=OrderedDict();self.input_results=OrderedDict();self.retired_ms=0
        self.blocked=False;self.armed=False;self.all_up=0;self.arm_hwm=0;self.fence_hwm=0
        self.last_direction=0;self.stop_required=True;self.pending_fence=None
        self.predecessor=None;self.closing_until=0;self.last_raw_mask=0
        self.neutral_anchor=None;self.neutral_floor=0

    @property
    def now(self):return self.operator.now

    @property
    def moving(self):return bool(self.valid and self.keys and self.now<self.expires and not self.blocked)

    def status(self):
        return dict(session_id=self.sid,kind=self.kind,profile='manual-input-3' if self.kind=='HELD' else 'manual-input-2',
            source_instance=self.source,input_epoch=self.epoch,state=self.state,action_seq=str(self.action),
            state_seq=str(self.sequence),input_expires_pi_us=str(self.expires),reason=self.reason)

    def _next(self,name):
        current=getattr(self,name)
        if current>=2**64-1:
            self.keys=[];self.valid=False;self.blocked=True;self.armed=False
            self.neutral_anchor=None;self.operator._stop_driver()
            raise OperatorError('COUNTER_EXHAUSTED')
        setattr(self,name,current+1);return current+1

    def _payload(self,reason):
        m=self.runner.machine
        value=dict(profile='manual-input-3' if self.kind=='HELD' else 'manual-input-2',session_id=self.sid,
            producer_kind=self.kind,sample_seq=str(self.sample),action_seq=str(self.action),state_seq=str(self.sequence),
            generation=m.generation,keys=list(self.keys),valid=self.valid,source_pi_us=str(self.source_us),
            action_expires_pi_us=str(self.expires),state_reason=reason)
        if self.kind=='HELD':value.update(source_instance=self.source,input_epoch=self.epoch,
            device_instance=self.device,grant_revision=str(self.grant),key_event_seq=str(self.key_event))
        return value

    def _publish(self,reason):
        if self.kind=='NEUTRAL_ONLY':reason='NEUTRAL'
        self._next('sequence');value=self._payload(reason);parse(value)
        key=(self.sid,self.action,self.sequence);raw=canonical(value)
        if len(raw)>1024:raise OperatorError('INVALID_INPUT')
        self.ring[key]=(raw,self.expires,self.epoch,self.grant)
        while len(self.ring)>8:self.ring.popitem(last=False)
        self.runner._client.publish(topics.manual_input(),'drive.input',value)
        self.runner.machine.on_manual_input(value,self.runner._clock())
        self.last_publish=self.now
        return self.sequence

    def neutral_candidate(self,*,preserve=False):
        if preserve and self.kind=='HELD':
            self.fence('REVOKED');return
        if preserve and self.kind=='PULSE':
            self.invalidate_anchor();self._next('action');self.keys=[];self.expires=0;self.valid=True
            self.state='ARMED_NEUTRAL';self.stop_required=True;self.source_us=self.now;self._next('sample')
            self._publish('USER_NEUTRAL');return
        if self.kind=='HELD':self.fence('REVOKED')
        if self.kind!='NEUTRAL_ONLY':
            self.sid=str(uuid4());self.epoch=str(uuid4());self.ring.clear()
        self.source=self.device=self.channel=self.link=None
        self.invalidate_anchor()
        self.kind='NEUTRAL_ONLY';self.keys=[];self.action=0;self.expires=0;self.valid=True
        self.state='ARMED_NEUTRAL';self.reason='NEUTRAL';self.source_us=self.now;self._next('sample')
        self._publish('NEUTRAL')

    def enable_pulse(self):
        if self.kind=='HELD':raise OperatorError('DRIVER_BUSY')
        self.kind='PULSE';self.keys=[];self.expires=0;self.valid=True;self.blocked=False
        self.state='ARMED_NEUTRAL';self.reason='NEUTRAL';self.stop_required=True
        self.source_us=self.now;self._next('sample');self._publish('NEUTRAL')

    def invalidate_anchor(self):
        self.neutral_anchor=None;self.neutral_floor+=1

    def fence(self,reason='REVOKED',*,release=False):
        self.invalidate_anchor()
        self.keys=[];self.valid=False;self.stop_required=True
        self.blocked=not release;self.armed=self.armed if release else False
        self.state='STOP_PENDING' if release else 'REARM_REQUIRED';self.reason=reason
        # Synthetic neutral preserves the last original source sample/time/E.
        self._publish('USER_RELEASE' if release and self.kind=='HELD' else
            'REARM_REQUIRED' if self.kind=='HELD' else 'REVOKED')

    def operator(self,call):
        key=call['request_id'];old=self.input_results.get(key)
        if old is not None:return old
        args=call['args']
        if args['driver_session']!=self.sid:raise OperatorError('DRIVER_CHANGED')
        if call['action']=='manual.neutral':
            self.invalidate_anchor()
            if self.kind!='NEUTRAL_ONLY':self._next('action')
            self.keys=[];self.valid=True;self.expires=0;self.stop_required=True
            if self.kind=='HELD':self.fence('REVOKED')
            else:self._publish('USER_NEUTRAL')
            self.runner._submit(__import__('xenova_gateway.machine',fromlist=['ForwardStop']).ForwardStop(
                self.runner.machine.generation),wait_for_stop=False)
        else:
            if self.kind!='PULSE':raise OperatorError('DRIVER_BUSY')
            if self.profile is None:raise OperatorError('UNSUPPORTED_PROFILE')
            if self.runner.machine.mode.value not in ('MANUAL','AUTO_READY'):raise OperatorError('WRONG_MODE')
            if self.operator.hold or not self.runner.machine.output_permitted:raise OperatorError('NOT_READY')
            if decimal(args['expected_action_seq'])!=self.action:raise OperatorError('CONTEXT_CHANGED')
            if self.current_anchor() is None and self.operator.direction_witness(self.runner.machine.active_vm,'manual') is None:
                raise OperatorError('STOP_REQUIRED')
            direction=1 if args['direction']=='FORWARD' else -1
            if (self.stop_required or self.last_direction not in (0,direction)) and not self.operator.stationary():
                self.fence('STOP_REQUIRED');raise OperatorError('STOP_REQUIRED')
            issued=int(call['request_id'].split(':')[2])*1000;expires=issued+args['duration_ms']*1000
            if self.now>=expires:raise OperatorError('DEADLINE_EXCEEDED')
            self._next('action');self._next('sample');self.source_us=issued;self.expires=expires
            self.keys=['up' if direction>0 else 'down']
            if args['steer']!='CENTER':self.keys.append('left' if args['steer']=='LEFT' else 'right')
            self.valid=True;self.blocked=False;self.stop_required=False;self.last_direction=direction
            self.state='DRIVING';self.reason='USER_NUDGE';self._publish('USER_NUDGE')
        self.input_results[key]=(self.action,self.sequence)
        while len(self.input_results)>8:
            old,_=self.input_results.popitem(last=False);self.retired_ms=max(self.retired_ms,int(old.split(':')[2]))
        return self.input_results[key]

    def tick(self):
        now=self.now
        if self.kind=='HELD':
            if self.valid and self.keys and now>=self.expires:
                self.fence('INPUT_EXPIRED');self.operator._stop_driver()
            return
        if self.keys and now>=self.expires:
            self.invalidate_anchor()
            self.keys=[];self.state='ARMED_NEUTRAL';self.stop_required=True;self.reason='LEASE_EXPIRED'
            self._publish('LEASE_EXPIRED');self.operator._stop_driver()
        elif now-self.last_publish>=50000:
            if not self.keys:self.source_us=now;self._next('sample')
            self._publish('NEUTRAL' if not self.keys else 'USER_NUDGE')

    def command_record(self,payload):
        if payload['driver_session']!=self.sid:return None
        item=self.ring.get((self.sid,decimal(payload['driver_action_seq']),decimal(payload['driver_state_seq'])))
        if item is None:return None
        raw,expires,epoch,grant=item
        import json
        value=json.loads(raw)
        if (not value['valid'] or self.blocked or epoch!=self.epoch or grant!=self.grant
                or not self.valid or value['action_seq']!=str(self.action)
                or value['keys']!=self.keys or value['keys'] and self.now>=expires):return None
        if value['generation']!=self.runner.machine.generation:
            pending=self.runner.authority.value.get('pending') or {}
            if (pending.get('manual_policy')!='CONTINUE_SESSION' or pending.get('manual_session')!=self.sid
                    or value['generation']>=self.runner.machine.generation):return None
        # The original input record remains immutable across actual same-SID
        # CONTINUE_SESSION. Current control binding is checked separately.
        return value

    def zero_prepared(self,sample):
        return bool(sample is not None and sample.session==self.sid and sample.profile!='manual-session-1'
            and sample.state==self.sequence and sample.kind==self.kind and not sample.keys and not self.keys
            and self.state in ('REARM_REQUIRED','ARMED_NEUTRAL','STOP_PENDING'))

    def pin_neutral(self,physical):
        """원 neutral을 ring8과 별도 보존한다. positive sample/E를 만들지 않는다."""
        if self.neutral_anchor is not None:return self.neutral_anchor
        if self.keys or not self.valid or self.blocked or self.state!='ARMED_NEUTRAL':return None
        self.neutral_anchor=dict(sid=self.sid,epoch=self.epoch,grant=self.grant,action=self.action,
            state=self.sequence,floor=self.neutral_floor,physical=physical)
        return self.neutral_anchor

    def current_anchor(self):
        pin=self.neutral_anchor
        if pin is None or (pin['sid'],pin['epoch'],pin['grant'],pin['floor'])!=(
                self.sid,self.epoch,self.grant,self.neutral_floor):return None
        return pin
