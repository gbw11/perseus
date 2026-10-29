"""Verified drive profile: execution, progress, applied control and output
permission are separate evidence. This class never restarts a VM or provisions
credentials. All generations originate in AuthorityStore before publication.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from xenova_mqtt.readiness import parse as parse_readiness
from .authority import AuthorityError,AuthorityStore
from .machine import (ControlMachine,Mode,Timing,DRIVE_APPS,AUTONOMOUS,MANUAL_APP,
    AppTrack,ForwardDrive,ForwardStop,PublishControl,PublishEvent,Measure,valid_command)

@dataclass
class Prepared:
    progress: int = -1
    generation: int = 0
    state: str = 'PREPARING'
    reason: str | None = 'CONTROL_UNAPPLIED'
    observed: float | None = None
    since: float | None = None

class VerifiedDriveMachine(ControlMachine):
    readiness_profile='verified-drive-1'
    readiness_lease_s=.300
    apply_timeout_s=1.0  # control delivery/application, NOT command liveness or a VM restart budget

    def __init__(self,*,vm_ids: tuple[str,str],authority: AuthorityStore,bindings,
                 timing: Timing=Timing(),clock):
        from xenova_mqtt.drive_binding import canonical
        self.vm_ids=tuple(canonical(v) for v in vm_ids)
        if len(self.vm_ids)!=2 or self.vm_ids[0]==self.vm_ids[1]:raise ValueError('distinct VM pair required')
        self.authority=authority;self.bindings=bindings;self.clock=clock
        value=authority.value or authority.load()
        if value['owner'] not in self.vm_ids:raise AuthorityError('stored owner outside configured pair')
        super().__init__(generation=value['generation'],start_mode=Mode(value['mode']),timing=timing)
        self.active_vm=value['owner']
        self.nodes={vm:{app:AppTrack(app,vm_id=vm) for app in DRIVE_APPS} for vm in self.vm_ids}
        self.apps=self.nodes[self.active_vm]
        self.prepared={(vm,app):Prepared() for vm in self.vm_ids for app in DRIVE_APPS}
        self.sessions={};self.streams={};self.references={}
        self.output_permitted=False;self._authorized=False;self.transport_ok=False;self.delivered=False
        self.failure=None;self._resume_authorized=False
        self._resume_vm=self.active_vm
        # Restart restores preference, never output permission, even if the last
        # process persisted an in-flight command with arm_after_apply=true.
        self.authority.update(rearm_required=True)
        self.authority.abandon_preparations('GATEWAY_RESTARTED')

    @property
    def phase(self):return self.authority.value['phase']

    def accept_session(self,vm,boot,instance,now=None,*,app=None):
        now=self.clock() if now is None else now
        if vm not in self.nodes or not self.bindings.accepts(vm,boot,instance,now,app=app):return False
        binding=self.bindings.current(vm,now)
        if binding is None:return False
        if binding.get('profile','verified-drive-1')!=self.readiness_profile:return False
        identity=(binding['execution_id'],boot,instance)
        for target in (DRIVE_APPS if app is None else (app,)):
            if target not in DRIVE_APPS:return False
            if self.sessions.get((vm,target))!=identity:
                self.sessions[vm,target]=identity
                self.nodes[vm][target]=AppTrack(target,vm_id=vm)
                self.prepared[vm,target]=Prepared();self.streams.pop((vm,target),None);self.references.pop((vm,target),None)
        self.apps=self.nodes[self.active_vm]
        return True

    def _bound(self,vm,now,app=None):
        from xenova_mqtt.drive_binding import app_instance
        row=self.bindings.current(vm,now)
        if not row:return False
        return all(self.sessions.get((vm,target))==(row['execution_id'],row['boot_id'],app_instance(row,target))
                   for target in (DRIVE_APPS if app is None else (app,)))

    def on_readiness(self,app,vm,payload,t):
        if vm not in self.nodes or app not in DRIVE_APPS or not self._bound(vm,t,app):return False
        try:value=parse_readiness(payload)
        except (ValueError,TypeError):return False
        p=self.prepared[vm,app]
        if value.progress<p.progress:return False
        if value.generation!=self.generation:return False
        if value.progress==p.progress:
            # A repeated READY cannot renew liveness. A negative report may
            # revoke readiness even when the calculation itself did not finish.
            if value.state!='READY':p.state=value.state;p.reason=value.reason;p.since=None
            return False
        continuous=(p.state=='READY' and p.generation==value.generation and p.observed is not None and
                    0<=t-p.observed<=self.readiness_lease_s)
        p.progress=value.progress;p.generation=value.generation;p.state=value.state;p.reason=value.reason;p.observed=t
        p.since=(p.since if continuous else t) if value.state=='READY' else None
        return True

    def on_command(self,app,vm,payload,t,reference=None):
        if vm not in self.nodes or app not in DRIVE_APPS or not self._bound(vm,t,app):return False
        command=valid_command(payload)
        if command is None or command[0]>self.generation:return False
        self.streams[vm,app]=t
        if command[0]!=self.generation:return False
        if not isinstance(reference,dict) or not reference.get('message_id'):return False
        track=self.nodes[vm][app];track.last_command_t=t;track.pending=(*command,t)
        self.references[vm,app]=dict(reference);return True

    def on_heartbeat(self,app,vm,payload,t):
        # Heartbeat is diagnostic progress only. It cannot create Prepared.
        if vm in self.nodes and app in DRIVE_APPS and self._bound(vm,t,app):
            self.nodes[vm][app].last_heartbeat_t=t

    def _ready(self,vm,app,now,held=False):
        p=self.prepared[vm,app]
        return bool(self._bound(vm,now,app) and p.generation==self.generation and p.state=='READY' and
            p.observed is not None and 0<=now-p.observed<=self.readiness_lease_s and
            (not held or (p.since is not None and now-p.since>=self.timing.ready_hold_s)))

    def _applied(self,vm,app,now):
        p=self.prepared[vm,app]
        return bool(self._bound(vm,now,app) and p.generation==self.generation and p.observed is not None and
                    0<=now-p.observed<=self.readiness_lease_s)

    def _qualified(self,vm,app,now,held=False):
        return self._ready(vm,app,now,held=held)

    def node_status(self,vm,now):
        app=self.active_app or MANUAL_APP
        if vm==self.active_vm and self.active_app and self.output_permitted and self._ready(vm,app,now):
            return 'active'
        if not self._qualified(vm,app,now,held=True):return 'unready'
        if vm==self.active_vm and self.active_app:
            return 'active' if self.output_permitted else 'ready_disarmed'
        return 'standby'

    def control_payload(self,reason=None):return self.authority.payload()

    def start(self,now):
        self._started=True
        pending=self.authority.value['pending']
        if pending and (pending['host_boot_id']!=self.authority.host or now*1000>=pending['deadline_ms']):
            self._fail('APPLY_DEADLINE_EXCEEDED',now)
        return [ForwardStop(self.generation),PublishControl(self.control_payload())]

    def note_delivery(self,generation,status,now):
        if generation!=self.generation:return
        if status=='acknowledged':self.delivered=True
        elif status in ('failed','disconnected'):self.delivered=False

    def note_transport(self,connected,now):
        if self.transport_ok and not connected:self.block('TRANSPORT_UNAVAILABLE',now)
        self.transport_ok=connected

    def block(self,reason,now):
        self.output_permitted=False;self._authorized=False;self._countdown=None;self._resume_requested.clear()
        for p in self.prepared.values():p.since=None;p.observed=None
        try:self.authority.update(rearm_required=True)
        except AuthorityError:self.failure='AUTHORITY_STORAGE_UNCERTAIN'
        return [ForwardStop(self.generation),Measure('output_blocked',{'reason':reason,'gen':self.generation,'t':now})]

    def _fail(self,reason,now):
        self.output_permitted=False;self._authorized=False;self.failure=reason;self._countdown=None
        try:self.authority.update(phase='FAILED',rearm_required=True,
            failure={'reason':reason,'generation':self.generation,'observed_monotonic_ms':int(now*1000)})
        except AuthorityError:self.failure='AUTHORITY_STORAGE_UNCERTAIN'

    def _change(self,mode,vm,reason,now,*,arm=False,manual=None):
        cancelled=[] if reason=='resume_requested' else self._cancel_countdown(reason,now)
        self._countdown=None;self._resume_requested.clear()
        self.output_permitted=False
        try:self.authority.issue(mode,vm,reason,now,self.apply_timeout_s,arm=arm,manual=manual)
        except AuthorityError as exc:
            self._fail(str(exc),now)
            return [ForwardStop(self.generation)]
        return self._adopt_control(mode,vm,reason,now,cancelled)

    def _adopt_control(self,mode,vm,reason,now,cancelled=()):
        """Adopt an already durable authority change; never issues another generation."""
        old_mode,old_vm=self.mode,self.active_vm
        self.mode=mode;self.active_vm=vm;self.apps=self.nodes[vm];self.generation=self.authority.value['generation']
        self.delivered=False;self.failure=None;self.transitions+=1
        for tracks in self.nodes.values():
            for track in tracks.values():track.pending=None
        for p in self.prepared.values():p.since=None;p.observed=None
        actions=[ForwardStop(self.generation),*cancelled,PublishControl(self.control_payload()),
            Measure('control_requested',{'mode':mode.value,'owner':vm,'gen':self.generation,'reason':reason,'t':now})]
        if old_mode!=mode:actions.append(PublishEvent('drive.mode.changed',{'from':old_mode.value,'to':mode.value,
            'generation':self.generation,'reason':reason}))
        if old_vm!=vm:actions.append(Measure('vm_failover',{'from_vm':old_vm,'to_vm':vm,'gen':self.generation,'t':now}))
        return actions

    def on_resume(self,t,requested_by=None,*,expected_generation=None,message_id=None):
        target=self.active_vm
        if self.mode is Mode.SAFE_STOP:
            target=next((vm for vm in self.vm_ids if self._qualified(vm,MANUAL_APP,t,held=True)),None)
        if not target or not self.transport_ok or not self._qualified(target,self.active_app or MANUAL_APP,t,held=True):return False
        if self.mode is Mode.AUTO_READY and not self._qualified(self.active_vm,AUTONOMOUS,t,held=True):return False
        if self.output_permitted and self.mode is not Mode.AUTO_READY:return False
        if self._countdown is not None:return False
        try:
            if not self.authority.consume_request(message_id,expected_generation):return False
        except (ValueError,AuthorityError):return False
        # Reuse the existing countdown implementation; rearm of a restored
        # AUTO/MANUAL preference is an explicit verified-profile operation.
        self._resume_requested.append((t,requested_by));self._resume_authorized=True;self._resume_vm=target;return True

    def _resume(self,now):
        if not self._resume_requested:return []
        from .machine import _Countdown
        _,requested_by=self._resume_requested.pop(0)
        self._countdown=_Countdown(now,self.generation,requested_by)
        return [PublishEvent('drive.resume.accepted',{'switch_in_ms':round(self.timing.resume_countdown_s*1000),
                'generation':self.generation,'requested_by':requested_by})]

    def _command_fresh(self,now):
        track=self.apps.get(self.active_app)
        return bool(track and track.last_command_t is not None and 0<=now-track.last_command_t<=self.timing.command_timeout_s)

    def _output(self,now):
        track=self.apps.get(self.active_app);pending=track.pending if track else None
        for tracks in self.nodes.values():
            for item in tracks.values():item.pending=None
        if not self.authority.error and self.output_permitted and pending and self._command_fresh(now) and self._ready(self.active_vm,self.active_app,now):
            ref=self.references[self.active_vm,self.active_app]
            self.forwarded+=1
            return [ForwardDrive(self.active_app,self.generation,pending[1],pending[2],source=ref)]
        if not self.output_permitted and (self._last_stop_t is None or now-self._last_stop_t>=self.timing.stop_resend_s):
            self._last_stop_t=now;return [ForwardStop(self.generation)]
        return []

    def step(self,now):
        if not self._started:raise RuntimeError('start required')
        if self.authority.error:
            self.output_permitted=False;self._authorized=False;self.failure=self.authority.error
            return self._output(now)
        actions=[];app=self.active_app;other=next(vm for vm in self.vm_ids if vm!=self.active_vm)
        if not self._bound(self.active_vm,now,app) and self.output_permitted:
            if app and self._qualified(other,app,now,held=True):actions=self._change(self.mode,other,'command_timeout',now,arm=True)
            else:actions=self._change(Mode.SAFE_STOP,self.active_vm,'no_fresh_standby',now);self._authorized=False
        if self.phase=='PENDING':
            pending=self.authority.value['pending']
            if pending['host_boot_id']!=self.authority.host or now*1000>=pending['deadline_ms']:
                self._fail('CONTROL_PUBLISH_UNCONFIRMED' if not self.delivered else 'CONTROL_APPLY_UNCONFIRMED',now)
            elif self.delivered and app is None:
                # SAFE_STOP has no app owner. This confirms only publication and
                # disarmed Gateway state, never an absent guest's application.
                try:self.authority.update(phase='DISARMED',rearm_required=True)
                except AuthorityError:self._fail('AUTHORITY_STORAGE_UNCERTAIN',now)
            elif (self.delivered and app and self._ready(self.active_vm,app,now) and
                  (self.mode is not Mode.AUTO_READY or self._applied(self.active_vm,AUTONOMOUS,now))):
                try:self.authority.update(phase='APPLIED',rearm_required=not self._authorized)
                except AuthorityError:self._fail('AUTHORITY_STORAGE_UNCERTAIN',now)
                else:
                    self.output_permitted=bool(self._authorized and pending['arm_after_apply'] and self.transport_ok)
                    actions.append(Measure('control_applied',{'gen':self.generation,'owner':self.active_vm,
                        'mode':self.mode.value,'output_permitted':self.output_permitted,'t':now}))
            return actions+self._output(now)
        if (self.phase=='FAILED' or self.failure) and not (self._resume_requested or self._countdown):return self._output(now)
        if app and self.output_permitted:
            p=self.prepared[self.active_vm,app]
            if self.mode is Mode.AUTO and p.state!='READY' and p.reason in ('INPUT_UNAVAILABLE','INPUT_STALE','INPUT_INVALID') and self._qualified(self.active_vm,MANUAL_APP,now,held=True):
                actions=self._change(Mode.MANUAL,self.active_vm,'autonomous_lost',now,arm=True)
            elif not self._ready(self.active_vm,app,now) or not self._command_fresh(now):
                track=self.apps[app];p=self.prepared[self.active_vm,app]
                actions.append(Measure('output_evidence_lost',{'vm_id':self.active_vm,'app':app,'gen':self.generation,
                    'binding':self._bound(self.active_vm,now),'readiness_state':p.state,'readiness_reason':p.reason,
                    'readiness_age_ms':None if p.observed is None else (now-p.observed)*1000,
                    'command_age_ms':None if track.last_command_t is None else (now-track.last_command_t)*1000}))
                if self._qualified(other,app,now,held=True):actions+=self._change(self.mode,other,'command_timeout',now,arm=True)
                else:actions+=self._change(Mode.SAFE_STOP,self.active_vm,'no_fresh_standby',now);self._authorized=False
            elif self.mode is Mode.MANUAL and self._qualified(self.active_vm,AUTONOMOUS,now,held=True):
                actions=self._change(Mode.AUTO_READY,self.active_vm,'autonomous_ready',now,arm=True)
            elif self.mode is Mode.AUTO_READY and not self._qualified(self.active_vm,AUTONOMOUS,now):
                actions=self._change(Mode.MANUAL,self.active_vm,'autonomous_lost',now,arm=True)
        elif self.mode is Mode.SAFE_STOP and self.transport_ok and not self.failure:
            candidate=next((vm for vm in (self.active_vm,other) if self._qualified(vm,MANUAL_APP,now,held=True)),None)
            if candidate:actions=self._change(Mode.MANUAL,candidate,'manual_fresh',now,arm=False)
        if self._countdown:
            expected_app=AUTONOMOUS if self.mode is Mode.AUTO_READY else app or MANUAL_APP
            if self._countdown.generation!=self.generation or not self._qualified(self._resume_vm,expected_app,now,held=True):
                actions+=self._cancel_countdown('readiness_lost',now)
            elif now-self._countdown.start_t>=self.timing.resume_countdown_s:
                self._authorized=True
                target_mode=Mode.AUTO if self.mode is Mode.AUTO_READY else Mode.MANUAL if self.mode is Mode.SAFE_STOP else self.mode
                actions=self._change(target_mode,self._resume_vm,'resume_requested',now,arm=True)
        if not actions:actions+=self._resume(now)
        return actions+self._output(now)
