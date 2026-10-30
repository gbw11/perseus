"""Bounded verified-profile delivery loop. External I/O acknowledgement and
serial acceptance remain distinct from app application and physical output.
"""
from __future__ import annotations
import time
from collections import deque
from xenova_mqtt import topics
from .authority import AuthorityError
from .machine import DRIVE_APPS,ForwardDrive,ForwardStop,PublishControl,PublishEvent,Measure
from .runner import GatewayRunner,CONSOLE_RESUME,RESUME_REQUESTED
from .verified import VerifiedDriveMachine
from .frame import encode,STOP_SAFE_STOP
from .sink import SinkError

class VerifiedGatewayRunner(GatewayRunner):
    drain_limit=64
    drain_budget_s=.001

    def __init__(self,client,sink,*,authority,bindings,vm_ids,clock=time.monotonic,**kwargs):
        super().__init__(client,sink,clock=clock,**kwargs)
        self.authority=authority;self.bindings=bindings;self.pair=vm_ids
        self._control=None;self._control_message_id=None;self._connection=None;self._output_broken=False
        self.submissions=deque(maxlen=8192);self.serial_accepted=0;self.serial_failed=0;self.serial_context_rejected=0
        self._latest_commands={};self._command_lock=__import__('threading').Lock();self.aux_publish_failures=0

    def subscribe(self):
        self._app_filters=[]
        for vm in self.pair:
            topic=f'{topics.PREFIX}/vms/{vm}/apps/+/events';self._app_filters.append(topic)
            self._client.subscribe(topic,self._enqueue,qos=0)
        self._client.subscribe(CONSOLE_RESUME,self._enqueue,qos=1)
        self._client.subscribe(topics.app_events('message-board'),self._enqueue,qos=1)
        self._client.subscribe(topics.vehicle_control(),self._enqueue,qos=1)

    def _enqueue(self,message):
        info=topics.parse(message.topic)
        if message.type in ('drive.command','app.heartbeat','app.readiness'):
            if (info.vm_id not in self.pair or info.app_id not in DRIVE_APPS or
                info.vm_id!=message.envelope.get('vm_id') or info.app_id!=message.envelope.get('app_id') or
                getattr(message,'retained',False)):return
            if not self.bindings.accepts(info.vm_id,message.envelope.get('boot_id'),
                                         message.envelope.get('agent_instance_id'),self._clock()):return
            if message.type=='drive.command':
                # Admit only the current control into the bounded latest-value
                # mailbox. Old-context traffic cannot replace a valid candidate.
                if self.machine is None or message.payload.get('generation')!=self.machine.generation:return
                key=(info.vm_id,info.app_id)
                with self._command_lock:self._latest_commands[key]=(self._clock(),message)
                return
        super()._enqueue(message)

    def _feed(self,t,message):
        if getattr(message,'retained',False) and message.type!='vehicle.control':return
        if message.type=='vehicle.control':
            try:self.authority.reconcile(message.payload)
            except (AuthorityError,ValueError,TypeError):self.machine._fail('CONTROL_AUTHORITY_CONFLICT',t)
            return
        info=topics.parse(message.topic);envelope=message.envelope;vm=info.vm_id;app=info.app_id
        if app in DRIVE_APPS and vm in self.pair:
            if not self.machine.accept_session(vm,envelope.get('boot_id'),envelope.get('agent_instance_id'),t):return
            if message.type=='app.readiness':self.machine.on_readiness(app,vm,message.payload,t)
            elif message.type=='app.heartbeat':self.machine.on_heartbeat(app,vm,message.payload,t)
            elif message.type=='drive.command':
                binding=self.bindings.current(vm,t)
                ref={k:envelope.get(k) for k in ('message_id','boot_id','agent_instance_id','seq')}
                ref.update(vm_id=vm,app_id=app,execution_id=binding['execution_id'],received_monotonic=t)
                self.machine.on_command(app,vm,message.payload,t,ref)
        elif (message.type==RESUME_REQUESTED and message.topic in (CONSOLE_RESUME,topics.app_events('message-board'))
              and not self._output_broken and self._control_message_id is not None
              and envelope.get('causation_id')==self._control_message_id):
            self.machine.on_resume(t,message.payload.get('requested_by'),
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

    def begin(self):
        self.machine=VerifiedDriveMachine(vm_ids=self.pair,authority=self.authority,bindings=self.bindings,
            clock=self._clock,timing=self._timing)
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

    def _submit(self,action):
        now=self._clock();source=action.source if isinstance(action,ForwardDrive) else None
        if isinstance(action,ForwardDrive):
            if not self.machine.output_permitted or action.generation!=self.machine.generation or not source:return
            row=self.bindings.current(source['vm_id'],now)
            if (not row or row['execution_id']!=source['execution_id'] or
                not self.bindings.accepts(source['vm_id'],source['boot_id'],source['agent_instance_id'],now) or
                not 0<=now-source['received_monotonic']<=self.machine.timing.command_timeout_s or
                not self.machine._ready(source['vm_id'],source['app_id'],now)):
                self.serial_context_rejected+=1;return
            frame=self._encoder.drive(action.generation,action.v_mps,action.steer_rad)
        else:frame=self._encoder.stop(action.generation,STOP_SAFE_STOP)
        data=encode(frame)
        record={'source':source,'frame_counter':frame.counter,'generation':frame.generation,
                'encoded_hex':data.hex(),'submitted_monotonic':now,'device_applied':None}
        if isinstance(action,ForwardDrive):record.update(v_mps=action.v_mps,steer_rad=action.steer_rad)
        try:
            accepted=self._sink.write(frame,now)
            if accepted!=len(data):raise SinkError('serial acceptance length unavailable/partial')
            self.serial_accepted+=1;record['serial_result']='accepted';record['accepted_bytes']=accepted
        except (SinkError,OSError):
            self.serial_failed+=1;record['serial_result']='failed';self._output_broken=True
            self.machine._fail('OUTPUT_PATH_FAILED',now)
        self.submissions.append(record)

    def _emit(self,actions):
        for action in actions:
            if isinstance(action,(ForwardDrive,ForwardStop)):self._submit(action)
            elif isinstance(action,PublishControl):self._publish_control(action.payload,self._clock())
            elif isinstance(action,PublishEvent):
                try:
                    if not self._client.connected:raise ConnectionError('auxiliary publisher offline')
                    super()._emit([action])
                except Exception:
                    self.aux_publish_failures+=1
                    __import__('logging').getLogger(__name__).warning('auxiliary event submission unavailable type=%s',action.type)
            else:super()._emit([action])

    def run_once(self,timeout_s=.005):
        import queue
        try:first=self._queue.get(timeout=timeout_s) if timeout_s>0 else self._queue.get_nowait()
        except queue.Empty:first=None
        self._delivery(self._clock());self._drain(first)
        self._emit(self.machine.step(self._clock()))

    def run(self,stop,*,connect_wait_s=5):
        try:
            self.subscribe();self._client.start(wait_s=connect_wait_s)
            self.begin()
            while not stop.is_set():self.run_once()
        finally:self.shutdown()

    def shutdown(self):
        try:
            if self.machine:self._submit(ForwardStop(self.machine.generation))
        finally:self._sink.close()
