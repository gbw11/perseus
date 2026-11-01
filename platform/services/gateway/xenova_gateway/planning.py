"""Request-bound preparation on the warm actor; no MQTT/Manager/disk reads.

The Authority owns durable acceptance and conditional commit. Current stateless
apps supply continuous lifecycle qualification, not a request acknowledgement.
Stateful producers must additionally supply compatible completed state evidence;
this module never restores state or searches paths while checking readiness.
"""
from .authority import AuthorityError
from .machine import Mode,AUTONOMOUS,MANUAL_APP,ForwardStop
from xenova_mqtt.drive_binding import app_instance

def context(row,app):
    return {**{k:row[k] for k in ('execution_id','boot_id','intent_revision','lifecycle_generation')},
            'app_instance_id':app_instance(row,app),'state_model':row.get('app_state_models',{}).get(app)}

class PlannedTransition:
    def __init__(self,machine):self.machine=machine

    def begin(self,request_id,mode,target_vm,*,expected_owner,expected_generation,deadline_ms,provider_request=None):
        m=self.machine;now=m.clock();mode=Mode(mode)
        if mode not in (Mode.AUTO,Mode.MANUAL) or target_vm not in m.vm_ids:
            raise AuthorityError('unsupported planned target/mode')
        old=m.authority.transition(request_id)
        if old is not None:
            spec=old['spec']
            if any(spec[k]!=v for k,v in dict(mode=mode.value,target_vm=target_vm,owner=expected_owner,
                    generation=expected_generation,deadline_ms=deadline_ms).items()):
                raise AuthorityError('transition request content differs')
            return old
        # A planned handoff cannot replace the existing explicit AUTO-resume
        # approval/countdown. Replay above remains an original-result lookup.
        if mode is Mode.AUTO and m.mode is not Mode.AUTO:
            raise AuthorityError('AUTO return requires the explicit resume/countdown path')
        app=AUTONOMOUS if mode is Mode.AUTO else MANUAL_APP
        source=m.bindings.current(expected_owner,now);target=m.bindings.current(target_vm,now)
        if not target or (source is None and (m.output_permitted or m.phase not in ('FAILED','DISARMED'))):
            raise AuthorityError('current execution binding required')
        if target.get('app_state_models',{}).get(app)!='stateless':
            raise AuthorityError('explicit stateless declaration required; stateful preparation transport not linked')
        spec=dict(mode=mode.value,target_vm=target_vm,owner=expected_owner,generation=expected_generation,
                  deadline_ms=deadline_ms,app=app,target=context(target,app),
                  source=context(source,m.active_app or MANUAL_APP) if source else None,
                  source_app=m.active_app or MANUAL_APP,state_model='stateless')
        if provider_request is not None:spec['provider_request']=dict(provider_request)
        if mode is Mode.MANUAL:
            sample=m.manual_input.current(now)
            if sample is None or sample.generation!=m.generation:raise AuthorityError('current driver session required')
            spec.update(manual_session=sample.session,manual_policy=(
                'CONTINUE_SESSION' if m.active_app==MANUAL_APP else 'NEUTRAL_THEN_ACTION'))
        return m.authority.begin_transition(request_id,spec,int(now*1000))

    def valid(self,row):
        m=self.machine;spec=row['spec'];now=m.clock();app=spec['app']
        source=m.bindings.current(spec['owner'],now);target=m.bindings.current(spec['target_vm'],now)
        if 'manual_session' in spec:
            sample=m.manual_input.current(now)
            if sample is None or sample.session!=spec['manual_session'] or sample.generation!=m.generation:return False
        source_ok=(context(source,spec['source_app'])==spec['source']) if source else (
            spec['source'] is None and not m.output_permitted and m.phase in ('FAILED','DISARMED'))
        # Recheck persisted preparations as well: they may predate this
        # admission guard or have waited while the caller lost its response.
        return bool((spec['mode']!='AUTO' or m.mode is Mode.AUTO) and
            source_ok and target and row['host_boot_id']==m.authority.host and
            target.get('app_state_models',{}).get(app)==spec['state_model'] and
            spec['owner']==m.active_vm and spec['generation']==m.generation and now*1000<spec['deadline_ms'] and
            context(target,app)==spec['target'] and m.transport_ok and not m.authority.error and
            m._qualified(spec['target_vm'],app,now,held=True))

    def refresh(self,request_id):
        m=self.machine;row=m.authority.transition(request_id)
        if row is None:raise AuthorityError('unknown transition request')
        if row['phase'] not in ('PREPARING','PREPARED'):return row
        spec=row['spec'];now=m.clock()
        if int(now*1000)>=spec['deadline_ms']:
            return m.authority.finish_preparation(request_id,'FAILED',reason='PREPARATION_DEADLINE_EXCEEDED')
        if not self.valid(row):
            # A fresh negative report revokes PREPARED immediately. No new
            # deadline or request is generated by a status query.
            if row['phase']=='PREPARED':
                return m.authority.finish_preparation(request_id,'FAILED',reason='PREPARATION_INVALIDATED')
            return row
        if row['phase']=='PREPARED':return row
        check,received=m.checks[spec['target_vm'],spec['app']]
        evidence=dict(check_seq=str(check),received_monotonic_ms=int(received*1000),
            applied_generation=m.generation,kind='stateless_qualification')
        return m.authority.finish_preparation(request_id,'PREPARED',evidence=evidence)

    def commit(self,request_id):
        m=self.machine;old=m.authority.transition(request_id)
        if old is None:raise AuthorityError('unknown transition request')
        if old['phase']=='COMMITTED':return old,[]
        row=self.refresh(request_id)
        if row['phase']!='PREPARED':raise AuthorityError('transition not prepared')
        # The actor serializes this guard with output selection. Only the
        # authoritative JSON update is atomic; guest/device application is not.
        armed=m.output_permitted and m._authorized
        try:
            result=m.authority.commit_transition(request_id,int(m.clock()*1000),self.valid,arm=armed)
        except AuthorityError:
            if m.authority.error:
                m.output_permitted=False;m._authorized=False;m.failure=m.authority.error
            raise
        spec=result['spec'];now=m.clock()
        m.output_permitted=False;m._countdown=None;m._resume_requested.clear()
        actions=m._adopt_control(Mode(spec['mode']),spec['target_vm'],'planned_handoff',now)
        if 'manual_session' in spec:
            from xenova_mqtt.manual_session import MovementGate
            m.movement=MovementGate(result['committed_generation'],spec['manual_session'],spec['manual_policy'])
        else:m.movement=None
        return result,actions

    def cancel(self,request_id):
        # COMMITTED is returned unchanged. A later stop is a distinct intent;
        # cancellation cannot roll the owner or generation backward.
        return self.machine.authority.finish_preparation(request_id,'CANCELLED',reason='REQUEST_CANCELLED')
