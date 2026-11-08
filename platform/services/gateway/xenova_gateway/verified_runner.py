"""Bounded verified-profile delivery loop. External I/O acknowledgement and
serial acceptance remain distinct from app application and physical output.
"""
from __future__ import annotations
import time
from collections import deque
from xenova_mqtt import topics
from .authority import AuthorityError
from .machine import DRIVE_APPS,ForwardDrive,ForwardStop,PublishControl,PublishEvent,Measure
from .runner import GatewayRunner,CONSOLE_RESUME,RESUME_REQUESTED,RESUME_CANCEL_REQUESTED
from .verified import VerifiedDriveMachine
from .frame import encode,STOP_SAFE_STOP
from .sink import SinkError
from .output_owner import PendingStop

class VerifiedGatewayRunner(GatewayRunner):
    drain_limit=64
    drain_budget_s=.001

    def __init__(self,client,sink,*,authority,bindings,vm_ids,clock=time.monotonic,readiness_profile='verified-drive-1',**kwargs):
        super().__init__(client,sink,clock=clock,**kwargs)
        self.authority=authority;self.bindings=bindings;self.pair=vm_ids
        if readiness_profile not in ('verified-drive-1','warm-drive-1'):raise ValueError('unknown readiness profile')
        self.readiness_profile=readiness_profile
        self._control=None;self._control_message_id=None;self._connection=None;self._output_broken=False
        self.submissions=deque(maxlen=8192);self.serial_accepted=0;self.serial_failed=0;self.serial_context_rejected=0
        self._latest_commands={};self._command_lock=__import__('threading').Lock();self.aux_publish_failures=0
        self.aux_offline=0;self.aux_publish_exceptions=0;self.aux_first_exception=None
        self.aux_transport_errors=0;self.aux_internal_errors=deque(maxlen=16)
        self.aux_internal_repeated=0;self.aux_internal_dropped=0;self._aux_last_internal=None;self._aux_last_transport=None
        self.aux_transport_details=deque(maxlen=16);self.aux_first_transport=None;self.aux_transport_dropped=0
        # Local integration seam, deliberately not an HTTP/MQTT API. Enqueue is
        # nonblocking; durable acceptance is reported later by the actor.
        self._transitions=__import__('queue').Queue(maxsize=8)
        from .provider import GatewayProvider
        self.provider=GatewayProvider(self)
        self.drive_link=None
        self.mapper_uplink=None
        self.scene_runtime=None
        self.operator=None;self._authority_worker=None
        self._durable_events=deque(maxlen=8);self._durable_feedback=deque(maxlen=64)
        self.durable_event_dropped=self.durable_feedback_dropped=0
        self._pending_stop = None
        self._deferred_drive=None;self.serial_deferred=0
        self._manual_activation=None;self.manual_withheld=0;self._manual_stop_try=0.
        from .handover_evidence import HandoverEvidence
        self.handover_evidence=HandoverEvidence()
        queue=__import__('queue')
        self._provider_queues=[queue.Queue(maxsize=n) for n in (4,4,8)]
        self._provider_turn=0;self.provider_calls=0;self.provider_high_water=0
        self._state_messages=queue.Queue(maxsize=4);self.state_message_dropped=0

    def submit_provider(self,call):
        import queue
        if self.readiness_profile!='warm-drive-1':raise ValueError('provider requires warm profile')
        lane=0 if call['action'] in ('cancel','finish') else 1 if call['action'] in ('get','context','history') else 2
        result=queue.Queue(maxsize=1)
        self._provider_queues[lane].put_nowait((call,result))
        self.provider_high_water=max(self.provider_high_water,sum(q.qsize() for q in self._provider_queues))
        return result

    def device_view(self):
        view=getattr(self._sink,'device_view',None)
        if callable(view):return view()
        return {'feedback_supported':False,'accepted':None,'applied':None,'physical_stop':None}

    def _provider_once(self):
        if not self._durable_available():return
        import queue
        from .provider import ProviderError
        from .ipc_contract import VERSION
        for offset in range(3):
            lane=(self._provider_turn+offset)%3
            try:call,result=self._provider_queues[lane].get_nowait()
            except queue.Empty:continue
            self._provider_turn=(lane+1)%3;self.provider_calls+=1
            try:response=dict(version=VERSION,ok=True,data=self.provider.handle(call))
            except ProviderError as exc:response=dict(version=VERSION,ok=False,error={'code':exc.code})
            except ValueError:response=dict(version=VERSION,ok=False,error={'code':'INVALID_INPUT'})
            except AuthorityError as exc:
                code='STORAGE_UNCERTAIN' if self.authority.error else 'TRANSITION_REJECTED'
                if not self.authority.error and str(exc) in ('HISTORY_BYTES_RESERVED','REQUEST_WINDOW_EXPIRED',
                        'REQUEST_TIME_INVALID','REQUEST_DEADLINE_OUTSIDE_WINDOW','LEGACY_KEY_NOT_ADMISSIBLE','HOST_CHANGED','HISTORY_POLICY_REQUIRED'):
                    code=str(exc)
                response=dict(version=VERSION,ok=False,error={'code':code,'reason':str(exc)[:192]})
            except Exception as exc:
                self.machine.output_permitted=False;self.machine._authorized=False
                self._submit(ForwardStop(self.machine.generation))
                import traceback
                detail={'cause_type':type(exc).__name__,'traceback':''.join(traceback.format_exception(exc))}
                if self.aux_first_exception is None:self.aux_first_exception=detail
                self.aux_internal_errors.append(detail)
                response=dict(version=VERSION,ok=False,error={'code':'INTERNAL_ERROR'})
            result.put_nowait(response)
            return

    def submit_transition(self,action,request_id,**arguments):
        import queue
        if self.readiness_profile!='warm-drive-1':raise ValueError('planned preparation requires warm profile')
        if action not in ('prepare','refresh','commit','cancel','get'):raise ValueError('unknown transition action')
        result=queue.Queue(maxsize=1)
        self._transitions.put_nowait((action,request_id,dict(arguments),result))
        return result

    def _transition_once(self):
        if not self._durable_available():return
        import queue
        try:action,request_id,arguments,result=self._transitions.get_nowait()
        except queue.Empty:return
        try:
            planning=self.machine.planning;actions=[]
            if action=='prepare':row=planning.begin(request_id,**arguments)
            elif action=='commit':row,actions=planning.commit(request_id)
            elif action=='get':row=self.authority.transition(request_id)
            elif action=='refresh':row=planning.refresh(request_id)
            else:row=planning.cancel(request_id)
            self._emit(actions)
            result.put_nowait(dict(ok=True,record=row))
        except (AuthorityError,ValueError,KeyError,TypeError) as exc:
            result.put_nowait(dict(ok=False,error=str(exc)))

    def subscribe(self):
        if hasattr(self._client,'set_receive_guard'):self._client.set_receive_guard(self.bindings.guard(self.pair))
        elif self.readiness_profile=='warm-drive-1':raise ValueError('warm profile requires pre-sequence binding guard')
        self._app_filters=[]
        for vm in self.pair:
            topic=f'{topics.PREFIX}/vms/{vm}/apps/+/events';self._app_filters.append(topic)
            self._client.subscribe(topic,self._enqueue,qos=1 if self.readiness_profile=='warm-drive-1' else 0)
        self._client.subscribe(CONSOLE_RESUME,self._enqueue,qos=1)
        self._client.subscribe(topics.app_events('interaction'),self._enqueue,qos=1)
        self._client.subscribe(topics.vehicle_control(),self._enqueue,qos=1)
        if self.readiness_profile=='warm-drive-1':self._client.subscribe(topics.manual_input(),self._enqueue,qos=0)

    def _enqueue(self,message):
        info=topics.parse(message.topic)
        if message.type in ('drive.command','app.heartbeat','app.readiness','app.lifecycle','app.state.snapshot','app.state.applied','vm.trust'):
            if (info.vm_id not in self.pair or info.app_id not in DRIVE_APPS or
                info.vm_id!=message.envelope.get('vm_id') or info.app_id!=message.envelope.get('app_id') or
                getattr(message,'retained',False)):return
            if not self.bindings.accepts(info.vm_id,message.envelope.get('boot_id'),
                                         message.envelope.get('agent_instance_id'),self._clock(),app=info.app_id):return
            if (message.type=='app.lifecycle')!=(self.readiness_profile=='warm-drive-1') and message.type in ('app.lifecycle','app.readiness'):return
            if message.type=='drive.command':
                # Admit only the current control into the bounded latest-value
                # mailbox. Old-context traffic cannot replace a valid candidate.
                if self.machine is None or message.payload.get('generation')!=self.machine.generation:return
                key=(info.vm_id,info.app_id)
                with self._command_lock:self._latest_commands[key]=(self._clock(),message)
                return
        if message.type in ('app.state.snapshot','app.state.applied'):
            if self.readiness_profile!='warm-drive-1':return
            try:self._state_messages.put_nowait(message)
            except __import__('queue').Full:self.state_message_dropped+=1
            return
        super()._enqueue(message)

    def _feed(self,t,message):
        if getattr(message,'retained',False) and message.type!='vehicle.control':return
        if (self.readiness_profile=='warm-drive-1' and message.type=='drive.input' and message.topic==topics.manual_input()):
            if self.operator is not None:return  # actual local registry already supplied its immutable source
            self.machine.on_manual_input(message.payload,message.received_monotonic);return
        if message.type=='vehicle.control':
            try:self.authority.reconcile(message.payload)
            except (AuthorityError,ValueError,TypeError):self.machine._fail('CONTROL_AUTHORITY_CONFLICT',t)
            return
        info=topics.parse(message.topic);envelope=message.envelope;vm=info.vm_id;app=info.app_id
        if app in DRIVE_APPS and vm in self.pair:
            if self.readiness_profile=='warm-drive-1':t=message.received_monotonic
            trust_at=self._clock() if self.readiness_profile=='warm-drive-1' else t
            if not self.machine.accept_session(vm,envelope.get('boot_id'),envelope.get('agent_instance_id'),trust_at,app=app):return
            if message.type=='app.readiness':self.machine.on_readiness(app,vm,message.payload,t)
            elif message.type=='app.lifecycle' and self.readiness_profile=='warm-drive-1':self.machine.on_lifecycle(app,vm,message.payload,t)
            elif message.type=='app.heartbeat':self.machine.on_heartbeat(app,vm,message.payload,t)
            elif message.type=='vm.trust':self._emit(self.machine.on_vm_untrusted(vm,app,message.payload,t))
            elif message.type=='drive.command':
                binding=self.bindings.current(vm,trust_at)
                if binding is None:return
                ref={k:envelope.get(k) for k in ('message_id','boot_id','agent_instance_id','seq')}
                ref.update(vm_id=vm,app_id=app,execution_id=binding['execution_id'],received_monotonic=t)
                if self.operator is not None:
                    try:ref=self.operator.admit_command(vm,app,message.payload,ref)
                    except ValueError:return
                    if ref is None:return
                    if message.payload['command_kind']=='STOP':
                        self.machine.on_command(app,vm,message.payload,t,ref)
                        if vm==self.machine.active_vm and app==self.machine.active_app:
                            self._submit(ForwardStop(self.machine.generation,ref),wait_for_stop=False)
                        return
                elif message.payload.get('profile') in ('drive-command-2','drive-command-3'):return
                self.machine.on_command(app,vm,message.payload,t,ref)
        elif (message.type in (RESUME_REQUESTED,RESUME_CANCEL_REQUESTED) and message.topic in (CONSOLE_RESUME,topics.app_events('interaction'))
              and not self._output_broken and self._control_message_id is not None
              and envelope.get('causation_id')==self._control_message_id):
            if message.type==RESUME_REQUESTED and not self._durable_available():
                if len(self._durable_events)==self._durable_events.maxlen:self.durable_event_dropped+=1
                else:self._durable_events.append((t,message))
                return
            handler=self.machine.on_resume if message.type==RESUME_REQUESTED else self.machine.on_cancel_resume
            requested_by=message.payload.get('requested_by')
            handler(t,requested_by if isinstance(requested_by,str) else None,
                expected_generation=message.payload.get('expected_generation'),message_id=envelope.get('message_id'))

    def _drain(self,first=None):
        import queue
        end=time.perf_counter()+self.drain_budget_s;count=0;item=first
        # Process the already dequeued first item even if the scheduler paused
        # us before entering the loop; do not silently lose an operator event.
        while count<self.drain_limit and (count==0 or time.perf_counter()<end):
            if item is None:
                try:item=self._queue.get_nowait()
                except queue.Empty:break
            self._feed(*item);item=None;count+=1
        # At most four latest drive candidates. Commands cannot fill the event
        # queue or prevent this iteration's safety decision.
        with self._command_lock:commands=self._latest_commands;self._latest_commands={}
        for received,message in commands.values():self._feed(received,message)
        if self.readiness_profile=='warm-drive-1':
            try:state=self._state_messages.get_nowait()
            except queue.Empty:pass
            else:
                if self.drive_link is not None and state.envelope.get('app_id')=='autonomous':
                    self.drive_link.reject('STATE_LEGACY_PROFILE')
                else:self.machine.states.receive(state)
        self._transition_once()

    def begin(self):
        from .warm import WarmDriveMachine
        machine=WarmDriveMachine if self.readiness_profile=='warm-drive-1' else VerifiedDriveMachine
        self.machine=machine(vm_ids=self.pair,authority=self.authority,bindings=self.bindings,
            clock=self._clock,timing=self._timing)
        self.machine.durable_write_available=self._durable_available
        self.machine.manual_registry=None if self.operator is None else self.operator.sources
        self.machine.continuous_handoff=self.drive_link is not None
        retained=self._client.latest(topics.vehicle_control())
        if retained is not None:
            try:self.authority.reconcile(retained.payload)
            except (AuthorityError,ValueError,TypeError):self.machine._fail('CONTROL_AUTHORITY_CONFLICT',self._clock())
        self._emit(self.machine.start(self._clock()));self._drain()

    def _transport(self):
        return bool(self._client.connected and all(self._client.subscribed(f) for f in
            [*self._app_filters,topics.vehicle_control(),CONSOLE_RESUME]))

    def _publish_control(self,payload,now):
        self._control={'payload':dict(payload),'generation':payload['generation'],'ticket':None,
            'attempts':0,'next_retry':now}
        self._control_message_id=None

    def _delivery(self,now):
        connected=self._transport();epoch=getattr(self._client,'connections',None)
        if self._connection is not None and epoch!=self._connection:
            self.machine.block('TRANSPORT_RECONNECTED',now);self._control_message_id=None
            self._publish_control(self.authority.payload(),now)
        self._connection=epoch;self.machine.note_transport(connected,now)
        if not connected:self._control_message_id=None;return
        c=self._control
        if c is None:return
        if c['generation']!=self.machine.generation:return
        ticket=c['ticket'];status=ticket.status(self._client) if ticket else None
        if status=='acknowledged':
            self._control_message_id=ticket.envelope['message_id']
            self.machine.note_delivery(c['generation'],status,now);return
        if status in ('failed','disconnected'):self.machine.note_delivery(c['generation'],status,now)
        pending=self.authority.value.get('pending')
        deadline=pending['deadline_ms']/1000 if self.machine.phase=='PENDING' and pending else now+1
        if ticket and status=='pending':return
        if c['attempts']>=3 or now<c['next_retry'] or now>=deadline:return
        c['attempts']+=1;c['next_retry']=now+.2
        try:c['ticket']=self._client.publish_tracked(topics.vehicle_control(),'vehicle.control',c['payload'])
        except Exception:
            c['ticket']=None;self.machine.note_delivery(c['generation'],'failed',now)

    def _manual_can_move(self,action,device,now):
        """Current control's STOP observation, never a historical recovery grant."""
        if self.drive_link is None or action.source['app_id']!='manual':return True
        gate=self._manual_activation;pending=self.authority.value.get('pending') or {}
        session=(device.get('pi_session'),device.get('device_session'))
        if (gate and gate['generation']!=action.generation and gate['ready'] and gate['session']==session and
            pending.get('manual_policy')=='CONTINUE_SESSION' and gate['driver']==pending.get('manual_session') and
            self.machine._handoff_grace_until>now):
            gate={**gate,'generation':action.generation,'kind':'same_session_continuation'};self._manual_activation=gate
        if gate and gate['generation']==action.generation and gate['session']==session and gate['ready']:return True
        self.manual_withheld+=1;self._deferred_drive=None
        if gate and gate['generation']==action.generation and now*1000>=gate['deadline_ms']:
            self.machine.output_permitted=False;self.machine._authorized=False
            self._submit(ForwardStop(action.generation));self.machine._fail('PHYSICAL_STOP_UNCONFIRMED',now)
        elif now-self._manual_stop_try>=self.machine.timing.stop_resend_s:
            self._manual_stop_try=now;self._submit(ForwardStop(action.generation))
        return False

    def _manual_observe(self,events):
        gate=self._manual_activation
        if gate is None or gate['ready']:return
        for event in events:
            if (event.get('command_generation')==gate['generation'] and event.get('command_kind')=='STOP' and
                event.get('status')=='APPLIED' and event.get('physical_stop') is True and
                (event.get('pi_session'),event.get('device_session'))==gate['session'] and
                event.get('command_sequence',0)>=gate['first_stop'] and
                event.get('observed_monotonic',float('inf'))*1000<=gate['deadline_ms']):
                gate['ready']=True;gate['observed_monotonic_ms']=int(event['observed_monotonic']*1000);break

    def _accept_submission(self, action, record, accepted):
        if hasattr(accepted,'frame'):
            actual=accepted.frame
            record.update(frame_counter=actual.counter,encoded_hex=encode(actual).hex())
            if accepted.metadata:
                record.update(accepted.metadata);record.setdefault('serial_profile','feedback-1')
        self.serial_accepted+=1;record['serial_result']='accepted';record['accepted_bytes']=accepted
        record['output_kind']='DRIVE' if isinstance(action,ForwardDrive) else 'STOP'
        if isinstance(action,ForwardDrive):
            end=(action.source or {}).get('valid_until_pi_us')
            if end is not None:
                record['original_expires_pi_us']=min(end,record.get('device_deadline_pi_us',end),
                    int((record['submitted_monotonic']+self.machine.timing.command_timeout_s)*1000000))
        self.handover_evidence.submission(record)
        if (self.drive_link is not None and isinstance(action,ForwardStop) and action.generation==self.machine.generation and self.machine.active_app=='manual' and
            (self._manual_activation is None or self._manual_activation['generation']!=action.generation)):
            pending=self.authority.value.get('pending') or {}
            if pending.get('deadline_ms'):
                self._manual_activation=dict(generation=action.generation,deadline_ms=pending['deadline_ms'],ready=False,
                    session=(record.get('pi_session'),record.get('device_session')),driver=pending.get('manual_session'),
                    first_stop=record.get('command_sequence',2**64),kind='observed_stop')

    def _defer_stop(self, submission, action, record):
        record['serial_result'] = 'pending'
        record['serial_wait'] = submission.reason
        self.submissions.append(record)
        self._pending_stop = (submission, action, record)

    def _finish_stop(self):
        if self._pending_stop is None:
            return
        pending, action, record = self._pending_stop
        try:
            accepted = pending.outcome()
        except (SinkError, OSError) as error:
            cause = error.__cause__ or error
            record.update(serial_result='failed', failure_reason='OUTPUT_SUBMISSION_FAILED',
                          failure_cause={'cause_type': type(cause).__name__, 'errno': getattr(cause, 'errno', None),
                                         'message': str(cause)})
            self.serial_failed += 1
            self._output_broken = True
            self.machine.output_permitted = False
            self.machine._authorized = False
            self.machine._fail('OUTPUT_PATH_FAILED', self._clock())
        else:
            if accepted is None:
                return
            self._accept_submission(action, record, accepted)
            record['serial_completed_monotonic'] = self._clock()
        self._pending_stop = None

    def _submit(self,action,*,wait_for_stop=True):
        self._finish_stop()
        if self._pending_stop is not None:
            if wait_for_stop and isinstance(action, ForwardStop):
                self._pending_stop[0].event.wait(.01)
                self._finish_stop()
            # One original STOP remains owned. Do not resubmit it or allow
            # DRIVE to overtake its unresolved physical submission.
            return
        now=self._clock();source=action.source
        device=self.device_view()
        if device['feedback_supported'] and not device['ready']:
            self.submissions.append(dict(source=source,generation=action.generation,device_applied=None,
                serial_result='not_submitted',failure_reason='DEVICE_NOT_READY',submitted_monotonic=now))
            if isinstance(action,ForwardDrive):
                self.machine.output_permitted=False;self.machine._authorized=False
            return
        if isinstance(action,ForwardDrive):
            if not self.machine.output_permitted or action.generation!=self.machine.generation or not source:return
            if source.get('command',{}).get('command_kind')=='STOP':
                self._submit(ForwardStop(action.generation,source),wait_for_stop=False);return
            if self.operator is not None and not self.operator.output_allowed(source):
                self._deferred_drive=None;self._submit(ForwardStop(action.generation),wait_for_stop=False);return
            if source.get('valid_until_pi_us') is not None and int(now*1e6)>=source['valid_until_pi_us']:
                self.serial_context_rejected+=1;return
            if self.scene_runtime is not None and not self.scene_runtime.command_allowed(source,int(now*1e6)):
                self.serial_context_rejected+=1;self._deferred_drive=None
                self._submit(ForwardStop(action.generation),wait_for_stop=False)
                return
            row=self.bindings.current(source['vm_id'],now)
            if (not row or row['execution_id']!=source['execution_id'] or
                not self.bindings.accepts(source['vm_id'],source['boot_id'],source['agent_instance_id'],now,app=source['app_id']) or
                not 0<=now-source['received_monotonic']<=self.machine.timing.command_timeout_s or
                not self.machine._ready(source['vm_id'],source['app_id'],now)):
                self.serial_context_rejected+=1;return
            if not self._manual_can_move(action,device,now):return
            frame=self._encoder.drive(action.generation,action.v_mps,action.steer_rad)
            if source.get('valid_until_pi_us') is not None:
                from dataclasses import replace
                frame=replace(frame,issued_pi_us=source.get('issued_pi_us'),expires_pi_us=source['valid_until_pi_us'],witness=source)
            if self.drive_link is not None:
                ready=getattr(self._sink,'drive_ready_at',lambda f:0.)(frame)
                if ready>now:
                    # One active-output mailbox, original receive time. STOP
                    # clears it; replacement never renews a source command.
                    expires=source['received_monotonic']+self.machine.timing.command_timeout_s
                    self._deferred_drive=(action,min(ready,expires));self.serial_deferred+=1
                    return
            self._deferred_drive=None
        else:
            self._deferred_drive=None;frame=self._encoder.stop(action.generation,STOP_SAFE_STOP)
            if self.operator is not None or source is not None:
                from dataclasses import replace
                source={} if source is None else dict(source)
                source.setdefault('generation',action.generation)
                source.setdefault('gateway_instance',self.drive_link.instance if self.drive_link else self._client.identity.agent_instance_id)
                frame=replace(frame,witness=source)
        data=encode(frame)
        record={'source':source,'frame_counter':frame.counter,'generation':frame.generation,
                'encoded_hex':data.hex(),'submitted_monotonic':now,'device_applied':None}
        if isinstance(action,ForwardDrive):record.update(v_mps=action.v_mps,steer_rad=action.steer_rad)
        serial_error=None
        try:
            submit_stop = getattr(self._sink, 'submit_stop', None)
            if isinstance(action, ForwardStop) and not wait_for_stop and submit_stop is not None:
                submission = submit_stop(frame, now)
                accepted = submission.outcome()
                if accepted is None:
                    self._defer_stop(submission, action, record)
                    return
            else:
                accepted = self._sink.write(frame, now)
            if accepted!=len(data):raise SinkError('serial acceptance length unavailable/partial')
            self._accept_submission(action, record, accepted)
        except PendingStop as pending:
            self._defer_stop(pending.submission, action, record)
            return
        except (SinkError,OSError) as error:
            serial_error=error;cause=error.__cause__ or error
            record['failure_cause']={'cause_type':type(cause).__name__,'errno':getattr(cause,'errno',None)}
            self.serial_failed+=1;record['serial_result']='failed';self._output_broken=True
            record['failure_reason']=getattr(self._sink,'failed_reason',None) or 'OUTPUT_SUBMISSION_FAILED'
            self.machine.output_permitted=False;self.machine._authorized=False
        self.submissions.append(record)
        if record['serial_result']=='failed':
            if isinstance(action,ForwardDrive):
                # Fence in RAM, then attempt STOP before failure-journal fsync.
                # A failed STOP does not recurse; device application stays unknown.
                self._submit(ForwardStop(action.generation))
            if serial_error is not None:
                cause=serial_error.__cause__ or serial_error
                record['failure_cause']['message']=str(cause)
                if not isinstance(cause,(OSError,SinkError)):
                    import traceback
                    record['failure_traceback']=''.join(traceback.format_exception(serial_error))
            self.machine._fail('OUTPUT_PATH_FAILED',now)

    def _emit(self,actions):
        if self.machine is not None:self.handover_evidence.transition(self.machine,int(self._clock()*1000000))
        for action in actions:
            if isinstance(action,(ForwardDrive,ForwardStop)):self._submit(action,wait_for_stop=False)
            elif isinstance(action,PublishControl):self._publish_control(action.payload,self._clock())
            elif isinstance(action,PublishEvent):
                if not self._client.connected:
                    self.aux_publish_failures+=1;self.aux_offline+=1
                    if self.aux_offline & (self.aux_offline-1)==0:
                        __import__('logging').getLogger(__name__).warning('auxiliary event unavailable reason=OFFLINE count=%d',self.aux_offline)
                    continue  # 정상 오프라인은 예외를 만들어 잡는 경로가 아니다.
                try:
                    super()._emit([action])
                except Exception as error:
                    self.aux_publish_failures+=1;self.aux_publish_exceptions+=1
                    chain=error.__cause__ or (error.__context__ if not error.__suppress_context__ else None)
                    if isinstance(error,OSError) and chain is None:
                        self.aux_transport_errors+=1
                        signature=(type(error).__name__,error.errno,action.type,str(error))
                        if signature!=self._aux_last_transport or self.aux_transport_errors & (self.aux_transport_errors-1)==0:
                            __import__('logging').getLogger(__name__).warning('auxiliary event unavailable reason=TRANSPORT_ERROR cause=%s errno=%s event=%s count=%d',
                                signature[0],signature[1],signature[2],self.aux_transport_errors)
                        if signature!=self._aux_last_transport:
                            detail={'cause_type':signature[0],'errno':signature[1],'event':signature[2],'message':signature[3]}
                            if self.aux_first_transport is None:self.aux_first_transport=detail
                            if len(self.aux_transport_details)==self.aux_transport_details.maxlen:self.aux_transport_dropped+=1
                            self.aux_transport_details.append(detail)
                        self._aux_last_transport=signature
                        continue
                    # 서로 다른 내부 결함을 transport/pending으로 합치지 않는다.
                    # 같은 cause/type/message/발생 위치의 연속 반복만 stack 재구성을 생략한다.
                    import traceback
                    location=tuple((f.f_code.co_filename,n,f.f_code.co_name) for f,n in traceback.walk_tb(error.__traceback__))
                    signature=(type(error).__name__,str(error),location) if chain is None else object()
                    if signature==self._aux_last_internal:self.aux_internal_repeated+=1
                    else:
                        detail={'cause_type':type(error).__name__,'traceback':''.join(traceback.format_exception(type(error),error,error.__traceback__))}
                        if self.aux_first_exception is None:self.aux_first_exception=detail
                        if len(self.aux_internal_errors)==self.aux_internal_errors.maxlen:self.aux_internal_dropped+=1
                        self.aux_internal_errors.append(detail);self._aux_last_internal=signature
                        __import__('logging').getLogger(__name__).warning('auxiliary event unavailable reason=PUBLISH_EXCEPTION cause=%s count=%d',
                            type(error).__name__,self.aux_publish_exceptions)
            else:super()._emit([action])

    def run_once(self,timeout_s=.005):
        self._finish_stop()
        if self.drive_link is not None:self.drive_link.tick()
        if self.mapper_uplink is not None:self.mapper_uplink.tick(self.drive_link)
        if self.operator is not None:self.operator.tick()
        if self.scene_runtime is not None:self.scene_runtime.tick()
        if self._durable_available():
            for _ in range(len(self._durable_events)):
                self._feed(*self._durable_events.popleft())
        import queue
        try:first=self._queue.get(timeout=timeout_s) if timeout_s>0 else self._queue.get_nowait()
        except queue.Empty:first=None
        self._provider_once()
        device=self.device_view()
        if device['feedback_supported'] and device['fault'] and not self._output_broken:
            self._output_broken=True
            self.machine.output_permitted=False;self.machine._authorized=False
            self._submit(ForwardStop(self.machine.generation))
            self.machine._fail(device['fault'],self._clock())
        self._delivery(self._clock());self._drain(first)
        self._emit(self.machine.step(self._clock()))
        held=self._deferred_drive
        if held is not None and self._clock()>=held[1]:
            self._deferred_drive=None;self._submit(held[0])
        events=getattr(self._sink,'feedback_events',lambda:[])()
        for event in events:self.handover_evidence.feedback(event,self.machine)
        self.handover_evidence.tick(int(self._clock()*1000000))
        self._manual_observe(events)
        if not self._durable_available():
            for event in events:
                if len(self._durable_feedback)==self._durable_feedback.maxlen:self.durable_feedback_dropped+=1
                else:self._durable_feedback.append(dict(event))
            return
        events=[*self._durable_feedback,*events];self._durable_feedback.clear()
        try:self.provider.capture(events)
        except AuthorityError:
            self.machine.output_permitted=False;self.machine._authorized=False
            self._submit(ForwardStop(self.machine.generation))

    def _durable_available(self):
        # 새 Authority bytes가 저장됐어도 원 ACK를 actor가 소비하기 전에는
        # old generation의 READY/delivery로 새 PENDING을 적용할 수 없다.
        worker=self._authority_worker
        return worker is None or not worker.busy and worker._results.empty()

    def authority_worker(self):
        """Scene/operator가 동일 Authority job1 owner를 공유한다."""
        if self._authority_worker is None:
            from .authority_journal import AuthorityJournalWorker
            self._authority_worker=AuthorityJournalWorker(self.authority,lambda:int(self._clock()*1e6))
        return self._authority_worker

    def configure_operator(self):
        if self.operator is not None or self.readiness_profile!='warm-drive-1':
            raise ValueError('one operator owner requires warm profile')
        from .operator_service import OperatorService
        self.operator=OperatorService(self)

    def submit_operator(self,call):
        if self.operator is None:raise ValueError('operator profile not configured')
        return self.operator.submit(call)

    def matched_stop(self):
        """feedback3 원 ingress만 stationary 증거가 된다. feedback2/write ACK는 변환하지 않는다."""
        device=getattr(self._sink,'device',None)
        value=getattr(device,'matched_stop',None)
        policy=getattr(device,'stationary_policy',None)
        sample=self.device_view().get('vehicle_sample')
        if value is None or policy is None or sample is None or self.machine is None:return None
        try:
            value.check(dict(vehicle=sample['vehicle'],calibration=sample['calibration'],odom_epoch=sample['odom_epoch']),
                self.authority.host,self.drive_link.instance if self.drive_link else self._client.identity.agent_instance_id,
                self.machine.generation,self.device_view()['pi_session'],self.device_view()['device_session'],policy,int(self._clock()*1e6))
        except ValueError:return None
        return value

    def control_facts(self):
        """원 serial owner의 불변 meter/STOP 사실. current clock나 권위를 발급하지 않는다."""
        from .control_facts import build_control_facts
        return build_control_facts(self)

    def current_scene_gate(self):
        from .scene_authority import SceneGate
        from .machine import Mode
        m=self.machine;stop=self.matched_stop()
        if m is None or stop is None or m.mode not in (Mode.MANUAL,Mode.SAFE_STOP):return None
        device=self.device_view();operator=self.operator
        mission=None if operator is None else operator.missions.current()
        return SceneGate('MANUAL' if m.mode is Mode.MANUAL else 'HOLD',m.generation,
            device['pi_session'],device['device_session'],0 if operator is None else operator.fence_revision,
            stop,pulse_active=bool(operator and operator.sources and operator.sources.moving),
            parking_active=bool(mission and mission['kind']==2 and mission['phase']!='HOLD'),countdown_active=bool(m._countdown))

    def configure_scene(self,policy):
        """명시 설치 profile의 actual owner를 연결한다. 기본은 기존 live-only 동작이다."""
        if self.scene_runtime is not None or self.readiness_profile!='warm-drive-1' or self.drive_link is None or self.mapper_uplink is None:
            raise ValueError('one warm scene runtime with actual mapper/state owners required')
        from .scene_runtime import GatewaySceneRuntime
        self.scene_runtime=GatewaySceneRuntime(self,policy)
        self.mapper_uplink.scene_reply=self.scene_runtime.accept_reply

    def submit_scene(self,submission):
        """operator2 원 예약 결과와 feedback3 gate용 내부 포트. 공개 IPC가 아니다."""
        if self.scene_runtime is None:raise ValueError('scene profile not configured')
        return self.scene_runtime.submit(submission)

    def update_scene_gate(self,gate):
        """실제 feedback/operator owner가 만든 원 gate만 전달한다."""
        if self.scene_runtime is None:raise ValueError('scene profile not configured')
        self.scene_runtime.update_gate(gate)

    def submit_mapper_tof(self,raw):
        """실제 센서 owner의 원 bytes용 유한 입력. source/clock 승인은 actor 회차에서 한다."""
        return False if self.mapper_uplink is None else self.mapper_uplink.submit_tof(raw)

    def publish_mapper_scene_after_commit(self,raw):
        """내구 scene owner의 완료 뒤 actor에서 호출한다. 여기서 scene 권한을 발급하지 않는다."""
        return False if self.mapper_uplink is None else self.mapper_uplink.publish_scene_after_commit(raw)

    def publish_mapper_scene_after_fence(self,raw):
        """현재 Scene owner의 실제 negative event는 원 fsync 완료보다 먼저 전달할 수 있다."""
        return False if self.mapper_uplink is None else self.mapper_uplink.publish_scene_after_fence(raw)

    def run(self,stop,*,connect_wait_s=5):
        try:
            self.subscribe();self._client.start(wait_s=connect_wait_s)
            self.begin()
            while not stop.is_set():self.run_once()
        finally:self.shutdown()

    def shutdown(self):
        try:
            if self.machine:self._submit(ForwardStop(self.machine.generation))
        finally:
            try:
                self._sink.close()
                self._finish_stop()
            finally:
                if self.scene_runtime is not None:self.scene_runtime.close()
                if self._authority_worker is not None:self._authority_worker.close()
