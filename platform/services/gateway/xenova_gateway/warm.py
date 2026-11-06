"""warm-drive-1 consumer. Qualification may select a target, never permit output.

The existing Authority issue/apply/rearm boundary remains authoritative. A new
owner must actually calculate in the newly issued context before output resumes.
"""
from xenova_mqtt.lifecycle import parse
from .verified import VerifiedDriveMachine
from .machine import DRIVE_APPS,ForwardStop
from .authority import AuthorityError

class WarmDriveMachine(VerifiedDriveMachine):
    readiness_profile='warm-drive-1'
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.checks={};self.qualification={}
        self.evidence_not_before=0.
        from .planning import PlannedTransition
        self.planning=PlannedTransition(self)
        from .state_transfer import StateCopies
        self.states=StateCopies(self)
        from xenova_mqtt.manual_session import SessionEvidence
        self.manual_input=SessionEvidence();self.movement=None

    def on_manual_input(self,payload,received):
        from xenova_mqtt.manual_session import parse
        try:sample=parse(payload)
        except (ValueError,TypeError):return False
        if not 0<=self.clock()-received<=.2:return False
        if not self.manual_input.observe(sample,received):return False
        if sample.valid and self.manual_input.current(self.clock()) is None:return False
        if self.movement:self.movement.observe(sample)
        return True

    def block(self,reason,now):
        self._abandon(reason)
        self.qualification.clear();self.evidence_not_before=now
        return super().block(reason,now)

    def _abandon(self,reason):
        try:self.authority.abandon_preparations(reason)
        except AuthorityError as exc:
            self.output_permitted=False;self._authorized=False
            self.failure=self.authority.error or str(exc);return False
        return True

    def _bound(self,vm,at,app=None):
        # Trust is checked now. The caller separately retains the original
        # receive time for freshness; a newer cache stamp is not a new session.
        return super()._bound(vm,self.clock(),app)

    def _ready(self,vm,app,now,held=False):
        binding=self.bindings.current(vm,self.clock())
        if (binding and binding.get('app_state_models',{}).get(app)=='stateful' and
                self.states.evidence(vm,app) is None):return False
        pending=self.authority.value.get('pending') or {}
        if vm==self.active_vm and app=='manual' and self.movement:
            sample=self.manual_input.current(now)
            if sample is None or sample.session!=self.movement.session or sample.generation!=self.generation or self.movement.blocked:
                return False
        if pending.get('target') and vm==self.active_vm and app==self.active_app:
            from .planning import context
            row=self.bindings.current(vm,now)
            if row is None or context(row,app)!=pending['target']:return False
        return super()._ready(vm,app,now,held)

    def _change(self,mode,vm,reason,now,*,arm=False,distrust=None):
        from .machine import Mode
        if not self._abandon('CONTROL_CONTEXT_CHANGED'):return [ForwardStop(self.generation)]
        manual=None
        if self.authority.value.get('manual_input_profile')=='manual-session-1' and mode in (Mode.MANUAL,Mode.AUTO_READY):
            sample=self.manual_input.current(now)
            if sample is None or sample.generation!=self.generation:
                mode=Mode.SAFE_STOP;reason='driver_session_unavailable';arm=False;self._authorized=False
            else:manual=dict(manual_session=sample.session,manual_policy=(
                'CONTINUE_SESSION' if self.active_app=='manual' else 'NEUTRAL_THEN_ACTION'))
            previous=self.authority.value.get('pending') or {}
            if manual and reason=='resume_requested' and self.authority.value['rearm_required'] and previous.get('manual_policy')=='NEUTRAL_THEN_ACTION':
                manual['manual_policy']='NEUTRAL_THEN_ACTION'
        return super()._change(mode,vm,reason,now,arm=arm,manual=manual,distrust=distrust)

    def _adopt_control(self,*args,**kwargs):
        actions=super()._adopt_control(*args,**kwargs)
        pending=self.authority.value.get('pending') or {}
        if 'manual_policy' in pending:
            from xenova_mqtt.manual_session import MovementGate
            self.movement=MovementGate(self.generation,pending['manual_session'],pending['manual_policy'])
        else:self.movement=None
        return actions

    def on_command(self,app,vm,payload,t,reference=None):
        if not 0<=self.clock()-t<=self.timing.command_timeout_s:return False
        if app=='manual' and vm==self.active_vm and self.movement:
            if not isinstance(payload,dict):return False
            sample=self.manual_input.current(self.clock())
            if (sample is None or sample.session!=self.movement.session or
                sample.generation!=self.generation or self.movement.blocked):return False
            if not self.movement.allowed and (payload.get('v_mps')!=0 or payload.get('steer_rad')!=0):return False
        return super().on_command(app,vm,payload,t,reference)

    def _output(self,now):
        if self.active_app=='manual' and self.movement and self.output_permitted:
            sample=self.manual_input.current(now)
            if sample is None or sample.session!=self.movement.session or sample.generation!=self.generation or self.movement.blocked:
                self._fail('MANUAL_INPUT_UNAVAILABLE',now)
        return super()._output(now)

    def accept_session(self,vm,boot,instance,now=None,*,app=None):
        row=self.bindings.current(vm,self.clock() if now is None else now)
        if not row or row.get('profile')!='warm-drive-1' or app not in DRIVE_APPS:return False
        before=self.sessions.get((vm,app))
        if not super().accept_session(vm,boot,instance,now,app=app):return False
        if before!=self.sessions.get((vm,app)):
            self.checks.pop((vm,app),None);self.qualification.pop((vm,app),None)
            self.states.invalidate(vm,app)
        return True

    def on_readiness(self,*args):return False  # no legacy READY fallback

    def on_lifecycle(self,app,vm,payload,t):
        now=self.clock()
        if (t<self.evidence_not_before or not 0<=now-t<=self.readiness_lease_s or vm not in self.nodes or
            app not in DRIVE_APPS or not self._bound(vm,now,app)):return False
        try:value=parse(payload)
        except (ValueError,TypeError):return False
        key=vm,app;old=self.checks.get(key);p=self.prepared[key]
        if (value.generation!=self.generation or (old and value.check<=old[0]) or value.progress<p.progress):return False
        self.checks[key]=(value.check,t)
        valid=value.readiness=='QUALIFIED' or (value.readiness=='READY' and
            (value.progress>p.progress or (p.observed is not None and 0<=t-p.observed<=self.readiness_lease_s)))
        previous=self.qualification.get(key)
        continuous=bool(valid and previous and previous[0]==self.generation and 0<=t-previous[1]<=self.readiness_lease_s)
        self.qualification[key]=(self.generation,t,previous[2] if continuous else t) if valid else None
        # Renew calculation evidence only after new completed work. Supervision
        # can revoke or qualify waiting; it cannot make a stalled calculation live.
        if value.readiness=='READY' and value.progress>p.progress:
            continuous_ready=p.state=='READY' and p.generation==value.generation and p.observed is not None and 0<=t-p.observed<=self.readiness_lease_s
            p.since=p.since if continuous_ready else t;p.observed=t
        elif value.readiness!='READY':p.observed=None;p.since=None
        p.progress=value.progress;p.generation=value.generation
        p.state='READY' if value.readiness=='READY' else 'NOT_READY';p.reason=value.reason
        if value.readiness=='NOT_READY':self.states.invalidate(vm,app)
        return True

    def _qualified(self,vm,app,now,held=False):
        binding=self.bindings.current(vm,self.clock())
        if (binding and binding.get('app_state_models',{}).get(app)=='stateful' and
                self.states.evidence(vm,app) is None):return False
        if app=='manual' and self.authority.value.get('manual_input_profile')=='manual-session-1':
            sample=self.manual_input.current(now)
            if sample is None or sample.generation!=self.generation:return False
        q=self.qualification.get((vm,app))
        return bool(q and self._bound(vm,now,app) and q[0]==self.generation and
            0<=now-q[1]<=self.readiness_lease_s and (not held or now-q[2]>=self.timing.ready_hold_s))

    def _applied(self,vm,app,now):
        return self._qualified(vm,app,now)

    def step(self,now):
        # Input/session loss is an output protection event, not a VM failure.
        # Clear the selected control so a new driver session can prepare without
        # first satisfying the old session's movement gate. No automatic rearm.
        if self.active_app=='manual' and self.movement and self.output_permitted:
            sample=self.manual_input.current(now)
            if sample is None or sample.session!=self.movement.session or sample.generation!=self.generation or self.movement.blocked:
                from .machine import Mode
                self._authorized=False
                return self._change(Mode.SAFE_STOP,self.active_vm,'driver_session_unavailable',now)+self._output(now)
        return super().step(now)
