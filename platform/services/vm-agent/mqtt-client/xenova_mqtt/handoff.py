"""drive-state-1: complete logical ManeuverIntent, not RAM/path/authority copy.

Context stamps reuse the app preparation frame/configuration/maneuver revisions.
Road/route updates are compatible when the app validates the merge segment in
current input. Transport progress never stands for calculation progress.
"""
from dataclasses import dataclass
import re
from .drive_binding import canonical
from .lifecycle import counter

PROFILE='drive-state-1'
CONTEXT_KEYS={'frame','configuration','maneuver','odom_epoch','road_revision','route_revision'}
MANEUVER_KEYS={'kind','side','phase','merge_segment'}


def decode_complete(raw):
    """Strict state-message decoding before sequence/latest mutation.

    Only the low-frequency state types need this additional pass; existing
    drive/heartbeat decoding and its allocation cost remain unchanged.
    """
    import json
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate state JSON key')
            result[key]=value
        return result
    def constant(value):raise ValueError('non-finite state JSON number')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def context(value):
    if not isinstance(value,dict) or set(value)!=CONTEXT_KEYS:
        raise ValueError('state context fields')
    result={k:str(counter(value[k])) for k in CONTEXT_KEYS-{'odom_epoch'}}
    result['odom_epoch']=canonical(value['odom_epoch'])
    return result


def maneuver(value):
    if not isinstance(value,dict) or set(value)!=MANEUVER_KEYS:
        raise ValueError('complete maneuver fields')
    if (value['kind'] not in ('FOLLOW','AVOID') or value['side'] not in ('NONE','LEFT','RIGHT') or
            value['phase'] not in ('FOLLOW','PASS','REJOIN') or
            not isinstance(value['merge_segment'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}',value['merge_segment'])):
        raise ValueError('maneuver domain')
    if (value['kind']=='FOLLOW') != (value['phase']=='FOLLOW') or (value['kind']=='FOLLOW') != (value['side']=='NONE'):
        raise ValueError('maneuver phase/side')
    return dict(value)


def revision(value):
    if type(value) is not int or not 0<=value<=2**63-1:raise ValueError('revision integer')
    return value


def snapshot(payload):
    required={'profile','execution_id','intent_revision','lifecycle_generation','source_generation',
              'state_revision','context','maneuver'}
    if not isinstance(payload,dict) or set(payload)!=required or payload['profile']!=PROFILE:
        raise ValueError('complete state snapshot required')
    result=dict(payload)
    canonical(result['execution_id']);revision(result['intent_revision']);revision(result['lifecycle_generation'])
    if type(result['source_generation']) is not int or not 1<=result['source_generation']<=0xffffffff:
        raise ValueError('source generation')
    if not counter(result['state_revision']):raise ValueError('state revision required')
    result['context']=context(result['context']);result['maneuver']=maneuver(result['maneuver'])
    return result


def applied(payload):
    fields={'profile','execution_id','intent_revision','lifecycle_generation','copy_message_id',
            'state_revision','context','origin','phase'}
    if not isinstance(payload,dict) or set(payload)!=fields or payload['profile']!=PROFILE:
        raise ValueError('state application fields')
    result=dict(payload)
    canonical(result['execution_id']);revision(result['intent_revision']);revision(result['lifecycle_generation'])
    if result['origin'] not in ('handoff','rebuilt') or result['phase'] not in ('APPLIED','REJECTED'):
        raise ValueError('application origin/phase')
    if result['origin']=='handoff':canonical(result['copy_message_id'])
    elif result['copy_message_id'] is not None:raise ValueError('rebuilt is not a transferred copy')
    if not counter(result['state_revision']):raise ValueError('state revision required')
    result['context']=context(result['context'])
    return result


def compatible(saved,current,body):
    saved=context(saved);current=context(current);body=maneuver(body)
    # Numeric equality of road/map payloads is not required. The logical goal
    # still has to exist in current app input; geometry is the app's job.
    return all(saved[k]==current[k] for k in ('frame','configuration','maneuver','odom_epoch'))


class ManeuverAdapter:
    """Concrete bounded logical-state owner for a stateful app integration.

    prepare() copies only after full validation/current segment resolution.
    calculate() calls the app-owned algorithm and advances progress only after
    it returns a successful result. It does not manufacture lifecycle READY.
    """
    def __init__(self):
        self.state=None;self.prepared_context=None;self.progress=0

    def prepare(self,saved,current,road_segments):
        value=snapshot(saved);current=context(current)
        if (not compatible(value['context'],current,value['maneuver']) or
                value['maneuver']['merge_segment'] not in road_segments):
            raise ValueError('state incompatible with current app input')
        self.state=value;self.prepared_context=current
        return dict(current)

    def invalidate(self):
        self.state=None;self.prepared_context=None

    def calculate(self,current,road_segments,algorithm):
        if self.state is None:raise ValueError('state not applied')
        current=context(current)
        if (not compatible(self.prepared_context,current,self.state['maneuver']) or
                self.state['maneuver']['merge_segment'] not in road_segments):
            self.invalidate();raise ValueError('state invalidated')
        result=algorithm(dict(self.state['maneuver']),dict(current))
        self.progress+=1
        return result


class StateReceiver:
    """App-side MQTT boundary: current control provenance precedes applying.

    Gateway independently checks the source at receipt. This receiver checks the
    actual current control and binding supplied by the app's identity owner.
    The returned application payload is published under the receiver's identity.
    """
    def __init__(self,adapter,vm_id):self.adapter=adapter;self.vm_id=canonical(vm_id)

    def rebuild(self,body,*,current,road_segments,target_binding,generation,state_revision):
        local=snapshot(dict(profile=PROFILE,execution_id=target_binding['execution_id'],
            intent_revision=target_binding['intent_revision'],lifecycle_generation=target_binding['lifecycle_generation'],
            source_generation=generation,state_revision=state_revision,context=current,maneuver=body))
        accepted=self.adapter.prepare(local,current,road_segments)
        return dict(profile=PROFILE,execution_id=target_binding['execution_id'],
            intent_revision=target_binding['intent_revision'],lifecycle_generation=target_binding['lifecycle_generation'],
            copy_message_id=None,state_revision=state_revision,context=accepted,origin='rebuilt',phase='APPLIED')

    def receive(self,message,*,control,source_binding,target_binding,current,road_segments):
        from .drive_binding import app_instance
        if message.retained or message.type!='app.state.snapshot':raise ValueError('live snapshot required')
        envelope=message.envelope;body=snapshot(message.payload);source_vm=envelope.get('vm_id');app=envelope.get('app_id')
        if (source_vm==self.vm_id or source_vm!=control.get('active_vm_id') or app!=control.get('active_app') or
                body['source_generation']!=control.get('generation') or
                source_binding['execution_id']!=body['execution_id'] or
                envelope.get('boot_id')!=source_binding['boot_id'] or
                envelope.get('agent_instance_id')!=app_instance(source_binding,app) or
                any(source_binding[k]!=body[k] for k in ('intent_revision','lifecycle_generation'))):
            raise ValueError('snapshot source is not current control')
        canonical(envelope['message_id'])
        accepted=self.adapter.prepare(body,current,road_segments)
        return dict(profile=PROFILE,execution_id=target_binding['execution_id'],
                    intent_revision=target_binding['intent_revision'],lifecycle_generation=target_binding['lifecycle_generation'],
                    copy_message_id=envelope['message_id'],state_revision=body['state_revision'],
                    context=accepted,origin='handoff',phase='APPLIED')
