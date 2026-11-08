"""operator2 actor·전역 원장·실제 임무 발급. transport는 여기로 원 요청만 인계한다."""
from copy import deepcopy
import queue
from uuid import UUID,uuid4

from xenova_drive_world import encode,decode,schema
from .authority_journal import AuthorityProposal,canonical
from .ipc_contract import request_key
from .operator_contract import request,fingerprint,error,OperatorError,SAFETY,VOLATILE,capacity
from .mission_authority import MissionAuthority
from .machine import Mode,ForwardStop


class OperatorService:
    def __init__(self,runner):
        self.runner=runner;self.worker=runner.authority_worker();self.missions=MissionAuthority(runner)
        self.queues=[queue.Queue(maxsize=n) for n in (2,8,4)]
        self.pending={};runner.authority.operator_reservations={};self.job=None;self.continuation=None;self.unknown=None
        self.started=False;self.ready=False;self.hold=True;self.hold_reason='STARTUP'
        self.fence_revision=0;self._waiting=[];self._effects={};self._control=None
        self.scene_requests={};self.budget_pending=None;self.ram_floor=0;self.mission_fence=None
        from .manual_source import DriverRegistry
        self.sources=DriverRegistry(self,getattr(runner,'manual_profile',None))
        from .guest_clock import GuestClockOwner
        self.clocks=GuestClockOwner(getattr(runner,'guest_clock_policy',None),runner.authority.host,self.instance)
        self.command_hwm={}
        from .keyboard_actor import KeyboardActor
        self.keyboard=KeyboardActor(self)
        if runner.drive_link is not None:runner.drive_link.missions.enable_budget_input()

    @property
    def now(self):return int(self.runner._clock()*1e6)

    @property
    def instance(self):
        provider=self.runner.drive_link
        return provider.instance if provider is not None else self.runner._client.identity.agent_instance_id

    def submit(self,call):
        request(call);lane=2 if call['action'] in ('status','request.get') else 0 if call['action'] in SAFETY else 1
        result=queue.Queue(maxsize=1);self.queues[lane].put_nowait((deepcopy(call),result));return result

    def _stop(self,reason):
        self.hold=True;self.hold_reason=reason
        self.fence_revision+=1
        m=self.runner.machine;m.output_permitted=False;m._authorized=False;m._countdown=None
        m._resume_requested.clear();self.runner._deferred_drive=None
        if self.sources is not None:self.sources.fence(reason)
        self.runner._submit(ForwardStop(m.generation),wait_for_stop=False)

    def _stop_driver(self):
        self.runner._deferred_drive=None
        self.runner._submit(ForwardStop(self.runner.machine.generation),wait_for_stop=False)

    def keyboard_stop(self,source):
        """원 K fence를 기존 global operator 원key에 한 번 결박한다. K는 권위를 쓰지 않는다."""
        issued=int(source['issued_pi_us'])//1000;m=self.runner.machine
        call=dict(version=2,action='stop',request_id=f'g1:{self.runner.authority.host}:{issued}:{source["request_id"]}',
            host_boot_id=self.runner.authority.host,gateway_instance_id=self.instance,expected_generation=m.generation,
            expected_owner=m.active_vm,deadline_pi_ms=issued+10000,args={})
        result=queue.Queue(maxsize=1)
        self._accept(call,result)

    def bind_driver(self,call,expires,completion,current=lambda:True):
        m=self.runner.machine;generation=m.generation;fence=self.fence_revision
        if (not self.stationary() or m.mode is not Mode.MANUAL or self.now>=expires
                or call['payload']['expected_generation']!=m.generation or call['payload']['expected_owner']!=m.active_vm):return False
        fields=self.runner.authority.issue_fields(Mode.MANUAL,m.active_vm,'keyboard_attach',self.runner._clock(),
            min(m.apply_timeout_s,(expires-self.now)/1e6),arm=True,
            manual=dict(manual_session=self.sources.sid,manual_policy='NEUTRAL_THEN_ACTION'))
        fields.update(manual_hold=True,manual_input_profile='manual-input-3')
        fields['pending']['keyboard_attach']=deepcopy(call)
        def committed(ok,unknown):
            if not ok:completion(False,generation);return
            active=current() and fence==self.fence_revision and self.now<expires
            m._authorized=active
            self.runner._emit(m._adopt_control(Mode.MANUAL,m.active_vm,'keyboard_attach',self.runner._clock()))
            self.sources.neutral_candidate()
            completion(active,m.generation)
        return self._proposal('keyboard:'+call['request_id'],fields,'driver_bind',expires,committed)

    def _rows(self):return self.runner.authority.value.get('operator_ledger',[])

    def _known(self,key):
        if key in self.pending:return self.pending[key]
        return next((row for row in self._rows() if row['key']==key),None)

    def _view(self,row,acceptance='DURABLE'):
        return dict(request_id=row['key'],acceptance=acceptance,phase=row['phase'],terminal=row['terminal'],
            result=row['result'],original=deepcopy(row['original']),current=self.status())

    def status(self):
        r=self.runner;m=r.machine;device=r.device_view();mission=self.missions.current()
        scene=None;runtime=r.scene_runtime
        if runtime is not None and runtime.owner is not None and runtime.owner._grant is not None:
            owner=runtime.owner;g=decode(owner._grant,schema.SceneGrant)
            scene=dict(state=owner.state,admission_id=str(UUID(bytes=g.scene.admission_id)),
                admission_revision=str(g.scene.admission_revision),survey_id=str(UUID(bytes=g.scene.survey_id)),
                survey_revision=str(g.scene.survey_revision),policy='STATIC_FLAT_SURVEY_1',
                allow_mapper_restore=g.scene.allow_restore_after_mapper_restart,expires_pi_us=str(g.valid_until_pi_us),
                lifecycle_revision=str(g.lifecycle_revision),applied=owner.eligible,blocked_reason=owner.blocked_reason,
                origin_request_key=next((r['key'] for r in owner._requests if r['nonce']==str(UUID(bytes=g.request_nonce))),None),
                evidence_sha256=g.frozen_evidence_sha256.hex() if g.HasField('frozen_evidence_sha256') else None,
                observation_station_count=None,positive_cell_count=None)
        return dict(host_boot_id=r.authority.host,gateway_instance_id=self.instance,pi_now_ms=self.now//1000,
            mode=m.mode.value if m else 'SAFE_STOP',owner=None if m is None or m.mode is Mode.SAFE_STOP else m.active_vm,
            generation=0 if m is None else m.generation,output_permitted=bool(m and m.output_permitted and not self.hold),
            hold=dict(active=self.hold,reason=self.hold_reason),driver=None if self.sources is None else self.sources.status(),
            mission=None if mission is None else dict(id=mission['id'],revision=str(mission['revision']),kind=mission['kind'],
                phase=mission['phase'],result=mission['result'],deadline_pi_us=str(mission['deadline'])),
            device={key:device.get(key) for key in ('pi_session','device_session','profile','ready','clock_epoch',
                'clock_lease_revision','command_sequence','applied','stationary','observation_age_us')},
            capacity=dict(operator_used=len(self._rows()),operator_limit=16,
                authority_bytes=len(canonical(r.authority.value)),authority_limit=65536),scene=scene)

    def _proposal(self,key,changes,operation,expires,continuation):
        if self.job is not None or self.worker.busy or not self.worker._results.empty():return False
        value={**self.runner.authority.value,**changes}
        if not capacity(value):raise OperatorError('HISTORY_FULL')
        self.job=AuthorityProposal(key,canonical(self.runner.authority.value),canonical(changes),self.now,
            expires,operation)
        self.continuation=continuation;self.worker.submit(self.job);return True

    def _record(self,call):
        host,issued=request_key(call['request_id']);now=self.now//1000;m=self.runner.machine
        if host!=self.runner.authority.host or call['host_boot_id']!=host:raise OperatorError('HOST_CHANGED')
        if call['gateway_instance_id']!=self.instance:raise OperatorError('INSTANCE_CHANGED')
        if not issued<=now<call['deadline_pi_ms']:raise OperatorError('DEADLINE_EXCEEDED')
        store=self.runner.authority.value;same_boot=store.get('operator_host_boot_id')==host
        floor=max(self.ram_floor,self.sources.retired_ms,store.get('operator_floor_ms',0) if same_boot else 0,
            store.get('operator_startup_ms',0) if same_boot else 0)
        if issued<=floor:raise OperatorError('NOT_FOUND_OR_NOT_RETAINED')
        if any(row['request_id']==call['request_id'] for name in ('transitions','protections')
                for row in self.runner.authority.value.get(name,[])):raise OperatorError('IDEMPOTENCY_CONFLICT')
        if call['action'] not in ('stop','parking.cancel','scene.revoke') and (
                call['expected_generation']!=m.generation or call['expected_owner']!=(None if m.mode is Mode.SAFE_STOP else m.active_vm)):
            raise OperatorError('CONTEXT_CHANGED')
        mission=self.missions.current();target=None if mission is None else dict(id=mission['id'],revision=mission['revision'])
        if call['action']=='scene.revoke':
            runtime=self.runner.scene_runtime
            if (runtime is None or runtime.owner is None or runtime.owner._grant is None
                    or decode(runtime.owner._grant,schema.SceneGrant).scene.admission_id!=UUID(call['args']['scene_id']).bytes):
                raise OperatorError('CONTEXT_CHANGED')
        if call['action']=='parking.start' and m.mode is not Mode.AUTO:raise OperatorError('WRONG_MODE')
        if call['action'] not in SAFETY|{'auto.cancel','manual.neutral'} and any(not row['terminal'] for row in self.pending.values()):
            raise OperatorError('BUSY_NOT_ACCEPTED')
        if call['action'] not in VOLATILE and len(self._waiting)>=2:raise OperatorError('BUSY_NOT_ACCEPTED')
        saved={row['key'] for row in self._rows()}
        if call['action'] not in VOLATILE and sum(key not in saved and row['request']['action'] not in VOLATILE for key,row in self.pending.items())>=2:
            raise OperatorError('HISTORY_FULL','UNKNOWN' if call['action'] in SAFETY else 'NOT_ACCEPTED')
        if call['action']=='auto.cancel':
            candidate=next((k for k,e in self._effects.items() if e.get('countdown') is not None
                and self.pending[k]['request']['action']=='auto.resume' and not self.pending[k]['terminal']),None)
            target=None if candidate is None else {'countdown_key':candidate,'generation':m.generation}
        original=dict(action=call['action'],host_boot_id=host,issued_pi_ms=issued,deadline_pi_ms=call['deadline_pi_ms'],
            mission_id=None if mission is None else mission['id'],mission_revision=None if mission is None else str(mission['revision']),
            commit_generation=None,app_applied=None,device_applied=None,physical_stop=None)
        return dict(key=call['request_id'],domain='operator.v2',fingerprint=fingerprint(call),request=deepcopy(call),
            target=target,phase='ACCEPTED',result='NONE',terminal=False,original=original,
            retain_until_ms=max(call['deadline_pi_ms']+60000,issued+60000))

    def _ledger(self,row):
        now=self.now//1000;rows=[];store=self.runner.authority.value
        floor=store.get('operator_floor_ms',0) if store.get('operator_host_boot_id')==self.runner.authority.host else 0
        mission=self.missions.current()
        for old in self._rows():
            if old['key']==row['key']:continue
            pinned=not old['terminal'] or mission is not None and mission['origin']==old['key'] and mission['phase']!='HOLD'
            if not pinned and now>=old['retain_until_ms']:
                if old['request']['host_boot_id']==self.runner.authority.host:floor=max(floor,request_key(old['key'])[1])
                continue
            rows.append(old)
        if len(rows)>=16:raise OperatorError('HISTORY_FULL')
        return dict(operator_ledger=[*rows,deepcopy(row)],operator_floor_ms=floor)

    def _terminal(self,row,result,*,failed=False):
        value=deepcopy(row);value.update(phase='FAILED' if failed else 'DONE',result=result,terminal=True)
        return value

    def _accept(self,call,result):
        key=call.get('request_id');action=call['action']
        if action=='status':result.put(dict(version=2,ok=True,data=self.status()));return
        known=self._known(key)
        if action=='request.get':
            result.put(error('NOT_FOUND_OR_NOT_RETAINED',key) if known is None else
                dict(version=2,ok=True,data=self._view(known)));return
        if known is not None:
            result.put(error('IDEMPOTENCY_CONFLICT',key) if known['fingerprint']!=fingerprint(call) else
                error('OUTCOME_UNKNOWN',key,'UNKNOWN') if self.unknown is not None or action not in VOLATILE and
                    not any(saved['key']==key for saved in self._rows()) else
                dict(version=2,ok=True,data=self._view(known,'VOLATILE' if action in VOLATILE else 'DURABLE')));return
        try:row=self._record(call)
        except OperatorError as exc:
            if action in SAFETY and exc.code=='HISTORY_FULL':self._stop(action)
            raise
        if action in VOLATILE:
            if self.sources is None:raise OperatorError('NOT_READY')
            self.sources.operator(call);row=self._terminal(row,'APPLIED')
            self.pending[key]=row;self.runner.authority.operator_reservations[key]=row['fingerprint'];result.put(dict(version=2,ok=True,data=self._view(row,'VOLATILE')));return
        if not self.ready or self.unknown is not None:raise OperatorError('OUTCOME_UNKNOWN','UNKNOWN')
        self.pending[key]=row;self.runner.authority.operator_reservations[key]=row['fingerprint']
        if action in SAFETY:self._stop(action)
        # Original reservation pins target/key before storage. A busy safety request
        # stays in this bounded slot; ordinary work is never queued for later launch.
        self._waiting.append((row,result))

    def _continue(self,row):
        call=row['request'];action=call['action'];m=self.runner.machine
        if self.now>=call['deadline_pi_ms']*1000:
            terminal=self._terminal(row,'FAILED',failed=True);return terminal,self._ledger(terminal)
        if action in ('stop','parking.cancel'):
            current=self.missions.current()
            if row['target'] is not None and current is not None and row['target']['id']!=current['id']:
                terminal=self._terminal(row,'FAILED',failed=True);return terminal,self._ledger(terminal)
            record=self.missions.fence();confirmed=self.stationary()
            row=self._terminal(row,'NO_ACTIVE_PARKING' if action=='parking.cancel' and
                (current is None or current['kind']!=2) else 'HOLD_CONFIRMED' if confirmed else 'STOP_UNCONFIRMED')
            if confirmed:row['original'].update(device_applied=True,physical_stop=True)
            changes=self._ledger(row)
            if record is not None:changes['current_mission']=record
            return row,changes
        if action.startswith('scene.'):
            if row['key'] not in self.scene_requests:
                from .scene_runtime import SceneSubmission
                from .scene_authority import SceneRequest
                runtime=self.runner.scene_runtime
                if runtime is None:raise OperatorError('NOT_READY')
                req=SceneRequest(row['key'],row['key'].split(':')[-1],row['original']['issued_pi_ms']*1000,
                    call['deadline_pi_ms']*1000,call['expected_generation'],action,canonical(call['args']))
                gate=self.runner.current_scene_gate()
                submission=SceneSubmission(req,gate,str(uuid4()) if action.endswith('begin') else None,
                    str(uuid4()) if action.endswith('begin') else None)
                self.scene_requests[row['key']]=self.runner.submit_scene(submission)
            future=self.scene_requests[row['key']]
            if not future.done():return None
            future.result();runtime=self.runner.scene_runtime
            if runtime.owner.take_journal() is not None:return None
            if runtime.owner.blocked_reason in ('PERSISTENCE_UNKNOWN','PERSISTENCE_FAILED'):
                raise OperatorError('STORAGE_UNCERTAIN','UNKNOWN')
            if action.endswith('commit') and not runtime.owner.eligible:return None
            row=self._terminal(row,'APPLIED');return row,self._ledger(row)
        if action=='auto.cancel':
            target=row['target'];effect=None if target is None else self._effects.get(target['countdown_key'])
            active=effect is not None and target['generation']==m.generation and effect['fence']==self.fence_revision and effect['stage'] not in ('COMMITTING','APPLYING')
            if active:
                effect['cancelled']=True
                self.hold=effect['previous_hold'];self.hold_reason=effect['previous_hold_reason']
                m._authorized=effect['previous_authorized'];m.output_permitted=False
                # Current readiness/input/source guards decide whether the old
                # MANUAL control may continue; cancellation issues no new arm.
            row=self._terminal(row,'CANCELLED' if active else 'NO_COUNTDOWN');changes=self._ledger(row)
            mission=self.missions.current()
            if active and mission is not None and mission['origin']==target['countdown_key']:
                changes['current_mission']=self.missions.fence()
            return row,changes
        if action in ('mode.manual','auto.resume','parking.start'):
            effect=self._effects.setdefault(row['key'],dict(stage='PREPARING',generation=m.generation,
                fence=self.fence_revision,countdown=None,previous_hold=self.hold,previous_hold_reason=self.hold_reason,
                previous_authorized=m._authorized))
            if effect.get('cancelled') or effect.get('failed'):
                terminal=self._terminal(row,'FAILED',failed=True);return terminal,self._ledger(terminal)
            if effect['stage']=='APPLYING':
                if m.generation!=effect['committed'] or m.failure:
                    terminal=self._terminal(row,'FAILED',failed=True);return terminal,self._ledger(terminal)
                if m.phase=='APPLIED' and m.output_permitted:
                    self.hold=False;self.hold_reason=None
                    terminal=self._terminal(row,'APPLIED');terminal['original']['app_applied']=True
                    return terminal,self._ledger(terminal)
                return None
            if effect['stage']=='COMMITTING':return None
            if effect['stage']=='PREPARING_MISSION':return None
            if effect['generation']!=m.generation or effect['fence']!=self.fence_revision:
                terminal=self._terminal(row,'FAILED',failed=True);return terminal,self._ledger(terminal)
            if action=='auto.resume' and m.mode is Mode.SAFE_STOP:raise OperatorError('WRONG_MODE')
            if effect['stage']=='PREPARING':
                self.hold=True;self.hold_reason=action;self._stop_driver()
                m.output_permitted=False;m._authorized=False
                self.sources.neutral_candidate(preserve=action=='auto.resume' and m.active_app=='manual');effect['stage']='WAIT_READY'
            if action!='mode.manual' and self.stationary():
                current=self.missions.current()
                if current is None or current['origin']!=row['key']:
                    prepared=self.missions.start(call,1 if action=='auto.resume' else 2,admitted=True)
                    def mission_prepared(ok,unknown):
                        if ok:self.missions.publish();effect['stage']='WAIT_READY'
                        elif not unknown:effect['failed']=True
                    if self._proposal(row['key'],{'current_mission':prepared},'mission_prepare',
                            call['deadline_pi_ms']*1000,mission_prepared):effect['stage']='PREPARING_MISSION'
                    return None
            app='manual' if action=='mode.manual' else 'autonomous'
            if not self.stationary() or not m._qualified(m.active_vm,app,self.runner._clock(),held=True):return None
            if action=='auto.resume':
                if effect['countdown'] is None:effect['countdown']=self.now+int(m.timing.resume_countdown_s*1e6)
                if self.now<effect['countdown']:return None
            mode=Mode.MANUAL if action=='mode.manual' else Mode.AUTO
            mission=self.missions.fence() if mode is Mode.MANUAL else self.missions.current()
            timeout=min(m.apply_timeout_s,max(.001,(call['deadline_pi_ms']*1000-self.now)/1e6))
            manual=dict(manual_session=self.sources.sid,manual_policy='NEUTRAL_THEN_ACTION') if mode is Mode.MANUAL else None
            fields=self.runner.authority.issue_fields(mode,m.active_vm,'operator_'+action,self.runner._clock(),timeout,
                arm=True,manual=manual)
            if mission is not None:
                mission=self.missions.rebind(mission,fields['generation'],m.active_vm)
                fields['current_mission']=mission
            value=deepcopy(row);value['phase']='APPLYING';value['original']['commit_generation']=fields['generation']
            fields.update(self._ledger(value));fields['manual_hold']=mode is Mode.MANUAL
            def committed(ok,unknown):
                if not ok:
                    if not unknown:effect['failed']=True
                    return
                current=(self.now<call['deadline_pi_ms']*1000 and effect['fence']==self.fence_revision)
                m._authorized=current
                self.runner._emit(m._adopt_control(mode,m.active_vm,'operator_'+action,self.runner._clock()))
                self.pending[value['key']]=value;effect.update(stage='APPLYING',committed=fields['generation'])
                if mode is Mode.MANUAL:self.sources.enable_pulse()
                self.missions.publish()
            if self._proposal(row['key'],fields,'control',call['deadline_pi_ms']*1000,committed):effect['stage']='COMMITTING'
        return None

    def stationary(self):
        stop=self.runner.matched_stop()
        return stop is not None

    def tick(self):
        if self.runner.machine is None:return
        outcome=self.worker.take('operator')
        if outcome is not None:
            if outcome.proposal is not self.job:raise RuntimeError('OPERATOR_ORIGINAL_JOB_MISMATCH')
            job,continuation=self.job,self.continuation;self.job=self.continuation=None
            if outcome.ack is None:
                if outcome.unknown:self.unknown=job;self._stop('STORAGE_UNCERTAIN')
                if continuation is not None:continuation(False,outcome.unknown)
            elif continuation is not None:continuation(True,False)
        if not self.started:
            current=self.missions.fence('FAILED')
            rows=deepcopy(self._rows())
            for row in rows:
                if not row['terminal']:
                    row.update(phase='FAILED',result='FAILED',terminal=True)
                if row['request']['host_boot_id']!=self.runner.authority.host:
                    row['retain_until_ms']=self.now//1000+60000
            # Restart never completes an old effect or rewrites unknown app/
            # physical evidence; the interrupted operation ends in disarmed HOLD.
            same_boot=self.runner.authority.value.get('operator_host_boot_id')==self.runner.authority.host
            changes=dict(operator_ledger=rows,operator_host_boot_id=self.runner.authority.host,
                operator_floor_ms=self.runner.authority.value.get('operator_floor_ms',0) if same_boot else 0,
                operator_startup_ms=self.now//1000,rearm_required=True,current_mission=current,manual_input_profile='manual-input-2')
            def started(ok,unknown):
                self.ready=ok
                if not ok and not unknown:self.started=False
            if self._proposal('startup',changes,'startup',self.now+10_000_000,started):self.started=True
        # Volatile result retention is exactly ring8; durable rows are queried
        # from their one owner rather than duplicated indefinitely in actor RAM.
        saved={row['key'] for row in self._rows()}
        for key,row in tuple(self.pending.items()):
            if row['terminal'] and (key in saved or row['request']['action'] in VOLATILE and key not in self.sources.input_results):
                del self.pending[key];self.runner.authority.operator_reservations.pop(key,None)
                self._effects.pop(key,None);self.scene_requests.pop(key,None)
            elif row['terminal'] and self.now//1000>=row['retain_until_ms']:
                self.ram_floor=max(self.ram_floor,request_key(key)[1]);del self.pending[key]
                self.runner.authority.operator_reservations.pop(key,None)
        self.runner.authority.operator_ram_floor=max(self.ram_floor,self.sources.retired_ms)
        self.sources.tick()
        self.keyboard.tick()
        device=getattr(self.runner._sink,'device',None)
        config=getattr(getattr(device,'odometry',None),'config',None)
        self.missions.observe_vehicle(self.runner.device_view().get('vehicle_sample'),
            getattr(config,'metres_per_tick',None),self.now)
        self._mission_tick()
        for lane in (0,2,1):
            try:call,result=self.queues[lane].get_nowait()
            except queue.Empty:continue
            try:self._accept(call,result)
            except (OperatorError,ValueError,KeyError,TypeError) as exc:
                result.put(error(getattr(exc,'code','INVALID_INPUT'),call.get('request_id'),getattr(exc,'outcome','NOT_ACCEPTED')))
            break
        waiting=self._waiting[0] if self._waiting else None
        if waiting is not None and self.job is None:
            row,result=waiting
            def accepted(ok,unknown):
                if not ok and not unknown:
                    self.pending[row['key']]=self._terminal(row,'FAILED',failed=True)
                result.put(error('STORAGE_UNCERTAIN' if unknown else 'NOT_ACCEPTED',row['key'],'UNKNOWN' if unknown else 'NOT_ACCEPTED') if not ok else
                    dict(version=2,ok=True,data=self._view(row)))
            try:changes=self._ledger(row)
            except OperatorError as exc:
                self._waiting.pop(0)
                if row['request']['action'] in SAFETY:
                    self.pending[row['key']]=self._terminal(row,'STOP_UNCONFIRMED',failed=True)
                    result.put(error(exc.code,row['key'],'UNKNOWN'))
                else:
                    self.pending.pop(row['key'],None);self.runner.authority.operator_reservations.pop(row['key'],None)
                    result.put(error(exc.code,row['key']))
            else:
                if self._proposal(row['key'],changes,'admission',row['request']['deadline_pi_ms']*1000,accepted):
                    self._waiting.pop(0)
        if self.job is None and self.unknown is None:
            for row in tuple(self.pending.values()):
                if row['terminal'] or not any(saved['key']==row['key'] for saved in self._rows()):continue
                try:next_state=self._continue(row)
                except (OperatorError,ValueError) as exc:
                    self._stop(getattr(exc,'code','CONTEXT_CHANGED'))
                    terminal=self._terminal(row,'FAILED',failed=True);next_state=terminal,self._ledger(terminal)
                if isinstance(next_state,tuple):
                    terminal,changes=next_state
                    def completed(ok,unknown,terminal=terminal):
                        if ok:self.pending[terminal['key']]=terminal;self.missions.publish()
                    self._proposal(row['key'],changes,'completion',max(self.now+10_000_000,row['request']['deadline_pi_ms']*1000),
                        completed)
                    break
        if self.job is None and self.unknown is None and self.runner.drive_link is not None:
            if self.budget_pending is None:self.budget_pending=self.runner.drive_link.take_budget_request()
            envelope=self.budget_pending
            if envelope is not None:
                reply,mission=self.missions.budget(envelope,self.now,self.stationary())
                if mission is None:
                    self.runner.drive_link.complete_budget_request(envelope,reply);self.budget_pending=None
                else:
                    checkpoint=self.missions.distance
                    def reserved(ok,unknown):
                        if not ok:
                            result=schema.MissionBudgetReply(major=1,request_nonce=envelope.request_nonce,
                                request_sha256=envelope.request_sha256,result=5,reason=9)
                        else:
                            result=reply
                            if self.now>=envelope.valid_until_pi_us or not self.stationary():
                                result=schema.MissionBudgetReply(major=1,request_nonce=envelope.request_nonce,
                                    request_sha256=envelope.request_sha256,result=3,reason=2)
                            self.missions.distance=max(0.,self.missions.distance-checkpoint);self.missions.publish()
                        self.runner.drive_link.complete_budget_request(envelope,result);self.budget_pending=None
                    self._proposal(envelope.request_nonce.hex(),{'current_mission':mission},'budget',envelope.valid_until_pi_us,reserved)

    def admit_command(self,vm,app,payload,reference):
        from .command_contract import parse
        command=parse(payload,app);m=self.runner.machine;now=self.now
        if command['generation']!=m.generation:return None
        source={**reference,'command':command,'generation':m.generation,'gateway_instance':self.instance}
        key=(vm,app,reference['execution_id'],reference['agent_instance_id'],m.generation)
        sequence=int(command['calculation_sequence'])
        previous=self.command_hwm.get(key,0)
        if sequence and sequence<=previous:return None
        if command['command_kind']=='STOP':
            if sequence:self.command_hwm[key]=sequence
            source['direction_intent_admitted']=False
            if (app=='autonomous' and sequence and command.get('next_direction') in (-1,1)
                    and command.get('clock_probe_sequence')!='0' and command['profile']=='drive-command-3'
                    and command['driver_session']=='00000000-0000-0000-0000-000000000000'
                    and not int(command['driver_action_seq']) and not int(command['driver_state_seq'])
                    and all(int(command[n]) for n in ('plan_revision','phase_revision','world_sequence','vehicle_sequence'))
                    and self.command_allowed(command)):
                interval=self.clocks.interval((vm,reference['execution_id'],reference['agent_instance_id']),
                    int(command['clock_probe_sequence']),int(command['completed_guest_us']),now,m.generation)
                raw=self.runner.drive_link.vehicle_history.get(int(command['vehicle_sequence']))
                current=self.runner.device_view().get('vehicle_sample')
                sample=None if raw is None else decode(raw,schema.VehicleSample)
                registered=sample is not None and sample.quality==3 and current is not None and all(
                    getattr(sample,k)==current.get(k) for k in ('host_boot','device_session','odom_epoch','pi_session','calibration'))
                if registered and interval is not None and now<min(int(command['valid_until_pi_us']),interval[0]+100000,interval[2]):
                    source['direction_intent_admitted']=True
                    mission=self.missions.current();caps=[v for v in (mission['deadline'],mission['execution_deadline']) if v]
                    if caps:source['phase_expires_pi_us']=min(caps)
            return source
        if command['profile']!='drive-command-3' or not self.command_allowed(command):return None
        peer=(vm,reference['execution_id'],reference['agent_instance_id'])
        interval=self.clocks.interval(peer,int(command['clock_probe_sequence']),int(command['completed_guest_us']),now,m.generation)
        if interval is None:return None
        lower,upper,clock_end=interval
        provider=self.runner.drive_link
        raw=None if provider is None else provider.vehicle_history.get(int(command['vehicle_sequence']))
        if raw is None:return None
        sample=decode(raw,schema.VehicleSample);current=self.runner.device_view().get('vehicle_sample')
        maximum=getattr(self.runner._sink,'_vehicle_maximum_age_us',None)
        if (current is None or maximum is None or sample.quality!=3 or any(getattr(sample,k)!=current.get(k)
                for k in ('host_boot','device_session','odom_epoch','pi_session','calibration'))):return None
        end=min(int(command['valid_until_pi_us']),lower+100000,clock_end,
            sample.pi_sample_us-sample.clock_error_us+maximum,now+100000)
        if app=='manual':
            record=self.sources.command_record(command)
            if record is None:return None
            if not record['keys']:
                if command['v_mps'] or command['steer_rad']:return None
            else:
                anchor=self.sources.current_anchor()
                if anchor is None or int(record['action_seq'])<=anchor['action']:return None
                profile=self.sources.profile
                if profile is None:return None
                keys=set(record['keys']);direction=1 if 'up' in keys else -1 if 'down' in keys else 0
                limit=profile.forward_mps if direction>0 else profile.reverse_mps
                if (not direction or command['v_mps']*direction<0 or abs(command['v_mps'])>limit
                        or abs(command['steer_rad'])>profile.steer_rad or 'left' not in keys and 'right' not in keys and command['steer_rad']):return None
                if ('left' in keys and command['steer_rad']<0) or ('right' in keys and command['steer_rad']>0):return None
                end=min(end,int(record['action_expires_pi_us']))
        else:
            mission=self.missions.current()
            if mission is None:return None
            for field in ('deadline','execution_deadline','search_deadline'):
                if mission[field]:end=min(end,mission[field])
        if now>=end:return None
        self.command_hwm={k:v for k,v in self.command_hwm.items() if k[:2]!=(vm,app)}
        self.command_hwm[key]=sequence
        source.update(issued_pi_us=lower,valid_until_pi_us=end)
        if app=='autonomous':
            phase=[v for v in (mission['deadline'],mission['execution_deadline']) if v]
            if phase:source['phase_expires_pi_us']=min(phase)
        return source

    def command_allowed(self,payload):
        if self.hold:return False
        if payload.get('producer_role')=='MANUAL':return self.sources.command_record(payload) is not None
        current=self.missions.current()
        if (current is None or not current['admitted'] or current['phase']=='HOLD'
                or current['generation']!=self.runner.machine.generation or current['owner']!=self.runner.machine.active_vm
                or self.missions.exhausted(self.now) is not None):return False
        if (payload.get('mission_id')!=current['id'] or int(payload.get('mission_revision','0'))!=current['revision']
                or int(payload.get('grant_revision','0'))!=current['grant_revision']):return False
        runtime=self.runner.scene_runtime
        return current['scene_id'] is None or runtime is not None and runtime.mission_allowed(self.missions.grant(),self.now)

    def _mission_tick(self):
        current=self.missions.current();m=self.runner.machine
        if current is None:return
        reason=self.missions.exhausted(self.now)
        if reason is not None and self.mission_fence is None:
            self._stop(reason);self.mission_fence=self.missions.fence('FAILED')
        if self.job is not None or self.worker.busy or self.unknown is not None:return
        if self.mission_fence is not None:
            record=self.mission_fence
            def fenced(ok,unknown):
                if ok:self.mission_fence=None;self.missions.publish()
            self._proposal('mission-fence:'+record['id'],{'current_mission':record},'mission_fence',self.now+10_000_000,fenced)
            return
        if (current['admitted'] and m.mode is Mode.AUTO and m.active_app=='autonomous'
                and (current['generation']!=m.generation or current['owner']!=m.active_vm)):
            rebound=self.missions.rebind(current,m.generation,m.active_vm)
            self._proposal('mission-binding:'+current['id'],{'current_mission':rebound},'mission_binding',self.now+10_000_000,
                lambda ok,unknown:self.missions.publish() if ok else None)

    def direction_witness(self,vm,role):
        device=getattr(self.runner._sink,'device',None)
        producer=getattr(device,'direction_witness',None)
        if producer is None:return None
        binding=self.runner.bindings.current(vm,self.runner._clock())
        if binding is None:return None
        from xenova_mqtt.drive_binding import app_instance
        peer=(vm,binding['execution_id'],app_instance(binding,role))
        if not any(value.peer==peer and self.now<value.expires for value in self.clocks.confirmed.values()):return None
        return producer(vm,role,self.runner.machine,self.now,self.sources if role=='manual' else None)

    def close(self):self._stop('SHUTDOWN');self.worker.close()
