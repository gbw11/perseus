"""Binary app provider, reusing actor-owned G2 acceptance and application proof.

Large complete paths use a separate bounded MQTT owner. That owner does no
authority mutation or serial I/O; a blocked broker cannot hold the actor/STOP.
"""
from collections import OrderedDict,deque
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import os
import queue
import select
import socket
import stat
import threading
import time
from uuid import UUID

from xenova_drive_world import schema,encode,decode,validate,MAX_BYTES,PreparedClock
from xenova_mqtt.drive_binding import app_instance
from .planning import context

PREFIX='xenova/v1'
VEHICLE_TOPIC=PREFIX+'/vehicle/state.pb'


def inbound(vm):return f'{PREFIX}/vms/{vm}/drive/link/in'
def outbound(vm):return f'{PREFIX}/vms/{vm}/drive/link/out'


def load_trust(path,host_boot,*,uid=0):
    """Approved source/clock profile, not values inferred from an input map."""
    from .ipc_contract import decode as json_decode
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode&0o077 or info.st_size>4096:
            raise ValueError('WORLD_TRUST_STORAGE')
        value=json_decode(stream.read(4097))
    if set(value)!={'host_boot','trust'} or value['host_boot']!=host_boot:raise ValueError('WORLD_TRUST_HOST')
    raw=value['trust'];fields=schema.WorldTrust.DESCRIPTOR.fields
    if not isinstance(raw,dict) or set(raw)!={f.name for f in fields}:raise ValueError('WORLD_TRUST_FIELDS')
    for field in fields:
        if type(raw[field.name]) is not (str if field.type==field.TYPE_STRING else int):raise ValueError('WORLD_TRUST_TYPE')
    from google.protobuf.json_format import ParseDict
    trust=ParseDict(value['trust'],schema.WorldTrust(),ignore_unknown_fields=False)
    if not trust.IsInitialized() or trust.lower_offset_us>trust.upper_offset_us or not trust.max_error_us:
        raise ValueError('WORLD_TRUST_CONFIGURATION')
    validate(trust)
    for name in ('mapper_boot','mapper_instance','map_epoch','frame_epoch','odom_epoch','clock_epoch'):
        if str(UUID(getattr(trust,name)))!=getattr(trust,name):raise ValueError('WORLD_TRUST_ID')
    if len(trust.primitive)!=64 or any(c not in '0123456789abcdef' for c in trust.primitive):raise ValueError('WORLD_PRIMITIVE_HASH')
    return trust


def stamp(*parts):
    # Existing G2 context uses numeric revision stamps. This is a context
    # fingerprint, never a replacement for the app's full geometry/ID checks.
    return str(int.from_bytes(sha256('\0'.join(map(str,parts)).encode()).digest()[:8],'big')&((1<<63)-1))


def state_context(value):
    return dict(frame=stamp(value.frame_epoch),configuration=stamp(value.configuration,value.calibration,value.primitive),
        maneuver=stamp(value.reference_id,value.maneuver_revision),odom_epoch=value.odom_epoch,
        road_revision=str(value.reference_revision),route_revision=str(value.path_revision))


class DriveLinkProvider:
    """Actor-only source/target checks. The full body stays outside Authority."""
    def __init__(self,runner,io,trust,gateway_instance):
        self.runner=runner;self.io=io;self.trust=trust;self.instance=str(UUID(gateway_instance))
        self.copies=OrderedDict();self.inflight={};self.accepted=0;self.rejected=0;self.evicted=0
        self.reasons={};self.last_vehicle=None
        self.application_rejections={}
        self.application_order={}
        self.world_failure=None
        io.configure(runner.authority.host,self.instance)

    def reject(self,reason):
        self.rejected+=1;self.reasons[reason]=self.reasons.get(reason,0)+1
        return False

    def _message(self,vm,binding,kind,payload,message_id=None):
        return SimpleNamespace(retained=False,type=kind,payload=payload,envelope=dict(vm_id=vm,app_id='autonomous',
            boot_id=binding['boot_id'],agent_instance_id=app_instance(binding,'autonomous'),message_id=message_id))

    def _send_copy(self,vm):
        m=self.runner.machine;binding=m.bindings.current(vm,m.clock())
        self._prune()
        if not binding or app_instance(binding,'autonomous') is None or vm==m.active_vm:return
        held=self.inflight.get(vm)
        if held is None and self.copies:
            identity=next(reversed(self.copies))
            held=dict(identity=identity,target=context(binding,'autonomous'));self.inflight[vm]=held
        if held is not None:
            m.states.pending_copies={r['identity'] for r in self.inflight.values()}
            saved=self.copies.get(held['identity'])
            if saved is not None:self.io.offer(inbound(vm),saved[0],'state')

    def _prune(self):
        m=self.runner.machine
        for vm,held in list(self.inflight.items()):
            if not m.states.retains_reference(vm,'autonomous',held['target']):del self.inflight[vm]
        m.states.pending_copies={r['identity'] for r in self.inflight.values()}

    def _pinned(self):
        self._prune()
        return self.runner.machine.states.referenced()|{r['identity'] for r in self.inflight.values()}

    def receive(self,vm,link,received,accepted_wire=None):
        m=self.runner.machine;now=self.runner._clock();kind=link.WhichOneof('payload')
        if not m or vm not in m.vm_ids or not 0<=now-received<=.5:return self.reject('LINK_STALE')
        binding=m.bindings.current(vm,now)
        if not binding or binding.get('app_state_models',{}).get('autonomous')!='stateful':return self.reject('LINK_BINDING')
        body=getattr(link,kind)
        execution=getattr(body,'source_execution',getattr(body,'execution',None))
        instance=getattr(body,'source_app_instance',getattr(body,'app_instance',None))
        named_vm=getattr(body,'source_vm',getattr(body,'vm',None))
        if (named_vm!=vm or execution!=binding['execution_id'] or instance!=app_instance(binding,'autonomous')):
            return self.reject('LINK_EXECUTION')
        if kind=='probe':
            if not body.sequence or not body.guest_send_us:return self.reject('LINK_CLOCK_PROBE')
            reply=schema.DriveLink(major=1)
            reply.clock.probe.CopyFrom(body);reply.clock.host_boot=m.authority.host
            reply.clock.gateway_instance=self.instance;reply.clock.pi_receive_us=int(received*1e6)
            reply.clock.pi_send_us=int(now*1e6)
            if self.trust is not None:reply.clock.trust.CopyFrom(self.trust)
            current=self.runner.device_view().get('vehicle_sample')
            if not current:return self.reject('LINK_VEHICLE_UNAVAILABLE')
            reply.clock.vehicle.CopyFrom(schema.VehicleSample(major=1,**{**current,'gateway_instance':self.instance}))
            if self.trust is not None:
                if current['calibration']!=self.trust.calibration:return self.reject('LINK_CALIBRATION')
                reply.clock.trust.odom_epoch=current['odom_epoch']
            # An unavailable/expired mapper clock blocks AUTO world adoption,
            # not the independent Pi vehicle clock or MANUAL input path.
            self.io.offer(inbound(vm),PreparedClock(reply),'clock');self._send_copy(vm)
        elif kind=='snapshot':
            if self.trust is None:return self.reject('WORLD_PROFILE_UNAVAILABLE')
            if (m.active_vm!=vm or m.active_app!='autonomous' or not m.output_permitted or
                    m.authority.protected(vm) or body.source_generation!=m.generation):return self.reject('STATE_SOURCE_AUTHORITY')
            if body.configuration!=self.trust.configuration or body.primitive!=self.trust.primitive or body.calibration!=self.trust.calibration:
                return self.reject('STATE_CONFIGURATION')
            try:identity=str(UUID(body.message_id))
            except ValueError:return self.reject('STATE_MESSAGE_ID')
            old=self.copies.get(identity)
            if accepted_wire is None:return self.reject('STATE_PREPARATION_MISSING')
            if old is not None:
                if old[0]!=accepted_wire:return self.reject('STATE_ID_CONFLICT')
                return True
            removable=None
            if len(self.copies)>=4:
                pinned=self._pinned();removable=next((key for key in self.copies if key not in pinned),None)
                if removable is None:return self.reject('STATE_COPY_REFERENCED_FULL')
            payload=dict(profile='drive-state-1',execution_id=binding['execution_id'],
                intent_revision=binding['intent_revision'],lifecycle_generation=binding['lifecycle_generation'],
                source_generation=body.source_generation,state_revision=str(body.state_revision),context=state_context(body),
                maneuver=dict(kind='AVOID' if body.direction else 'FOLLOW',side={-1:'RIGHT',0:'NONE',1:'LEFT'}[body.direction],
                    phase=('FOLLOW','PASS','REJOIN')[body.maneuver_phase] if body.direction else 'FOLLOW',merge_segment=body.reference_id))
            if not m.states.receive(self._message(vm,binding,'app.state.snapshot',payload,identity)):return self.reject('STATE_SOURCE_REJECTED')
            self.copies[identity]=(accepted_wire,(body.path_revision,body.primitive,body.calibration,body.configuration,body.maneuver_revision))
            if removable is not None:del self.copies[removable];self.evicted+=1
            for target in m.vm_ids:
                if target!=vm:self._send_copy(target)
        elif kind=='applied':
            if body.host_boot!=m.authority.host or body.gateway_instance!=self.instance:return self.reject('STATE_GATEWAY_CHANGED')
            target=context(binding,'autonomous');previous=self.application_order.get(vm)
            signature=tuple((f.number,v) for f,v in body.ListFields())
            if previous is not None and previous[0]==target:
                if body.application_sequence<previous[1]:return self.reject('STATE_APPLICATION_OLD')
                if body.application_sequence==previous[1]:
                    return True if signature==previous[2] else self.reject('STATE_APPLICATION_CONFLICT')
            if body.HasField('copy_message_id'):
                saved=self.copies.get(body.copy_message_id)
                if not saved or saved[1]!=(body.path_revision,body.primitive,body.calibration,body.configuration,body.maneuver_revision):return self.reject('STATE_PATH_CONTEXT')
            payload=dict(profile='drive-state-1',execution_id=binding['execution_id'],intent_revision=binding['intent_revision'],
                lifecycle_generation=binding['lifecycle_generation'],copy_message_id=body.copy_message_id if body.HasField('copy_message_id') else None,
                state_revision=str(body.state_revision),context=state_context(body),
                origin='rebuilt' if body.rebuilt else 'handoff',phase='APPLIED' if body.applied else 'REJECTED')
            received=m.states.receive(self._message(vm,binding,'app.state.applied',payload))
            held=self.inflight.get(vm)
            if held is not None and held['identity']==body.copy_message_id:
                self.inflight.pop(vm);self._prune()
            if not body.applied:
                self.application_rejections[vm]=dict(code='STATE_REJECTED',reason=body.reason if body.HasField('reason') else None,
                    copy_message_id=body.copy_message_id if body.HasField('copy_message_id') else None,
                    execution_id=binding['execution_id'],app_instance_id=app_instance(binding,'autonomous'))
            else:self.application_rejections.pop(vm,None)
            if received or not body.applied:self.application_order[vm]=(target,body.application_sequence,signature)
            if not received:return self.reject('STATE_APPLICATION_REJECTED')
        else:return self.reject('LINK_DIRECTION')
        self.accepted+=1;return True

    def tick(self):
        for _ in range(2):
            item=self.io.take()
            if item is None:break
            self.receive(*item)
        sample=self.runner.device_view().get('vehicle_sample')
        identity=None if sample is None else tuple(sample.get(k) for k in ('host_boot','device_session','odom_epoch','pi_session','sequence'))
        if sample and identity!=self.last_vehicle:
            body=schema.VehicleSample(major=1,**{**sample,'gateway_instance':self.instance})
            self.io.offer(VEHICLE_TOPIC,encode(body),'vehicle');self.last_vehicle=identity

    def status(self):
        """Small current diagnostic view. It is not output/recovery permission."""
        return dict(profile='drive-world-1',copies=len(self.copies),copy_limit=4,
            world=dict(state='invalid' if self.world_failure is not None else 'ready' if self.trust is not None else 'not_configured',
                cause_type=type(self.world_failure).__name__ if self.world_failure is not None else None,
                errno=getattr(self.world_failure,'errno',None)),
            pending_applications=len(self.inflight),accepted=self.accepted,rejected=self.rejected,
            evicted=self.evicted,reasons=dict(self.reasons),application_rejections=dict(self.application_rejections),
            io=dict(received=self.io.received,dropped=self.io.dropped,sent=self.io.sent,failed=self.io.failed,
                    outgoing_high_water=self.io.high_water,oversize=self.io.oversize,
                    reasons=dict(self.io.drop_reasons),first_decode_error=self.io.first_decode_error,
                    recent_decode_errors=list(self.io.decode_errors),decode_details_lost=self.io.decode_details_lost))


class DriveLinkIO:
    """Single bounded binary MQTT owner; no worker is created per message."""
    def __init__(self,host,port,username,password,vms,*,clock=time.monotonic):
        import ipaddress
        if str(ipaddress.IPv4Address(host))!=host:raise ValueError('LINK_NUMERIC_ADDRESS')
        self.host,self.port,self.username,self.password=host,port,username,password
        self.vms=tuple(vms);self.clock=clock;self.incoming=queue.Queue(maxsize=4)
        self.lock=threading.Lock();self.pending={};self.stop=threading.Event();self.thread=None
        self.received=self.dropped=self.sent=self.failed=self.high_water=self.oversize=0
        self.first_error=None;self.failure=None;self.first_transport=None;self.connected=False
        self.host_boot=None;self.instance=None
        self.drop_reasons={};self.first_decode_error=None;self.decode_errors=deque(maxlen=8);self.decode_details_lost=0

    def _drop(self,reason,error=None):
        # Fixed reason vocabulary and eight bounded codec diagnostics. Expected
        # invalid traffic never formats a stack or writes to the actor's sink.
        self.dropped+=1;self.drop_reasons[reason]=self.drop_reasons.get(reason,0)+1
        if error is not None:
            detail=dict(cause_type=type(error).__name__,reason=str(error)[:160])
            if self.first_decode_error is None:self.first_decode_error=detail
            if not self.decode_errors or self.decode_errors[-1]!=detail:
                if len(self.decode_errors)==self.decode_errors.maxlen:self.decode_details_lost+=1
                self.decode_errors.append(detail)

    def configure(self,host_boot,instance):
        if self.thread is not None:raise RuntimeError('LINK_ALREADY_STARTED')
        self.host_boot=str(UUID(host_boot));self.instance=str(UUID(instance))

    def prepare_snapshot(self,value):
        """I/O owner only: encode a candidate before actor admission. It has no
        authority until the actor actually selects this immutable byte string."""
        if self.host_boot is None or self.instance is None:raise ValueError('LINK_NOT_CONFIGURED')
        candidate=schema.DriveLink(major=1)
        candidate.accepted.host_boot=self.host_boot;candidate.accepted.gateway_instance=self.instance
        candidate.accepted.state.CopyFrom(value.snapshot)
        return encode(candidate)

    def offer(self,topic,data,kind):
        if len(data)>MAX_BYTES:raise ValueError('LINK_BYTES')
        # At most one clock and one state reply per installed VM plus one
        # vehicle sample. Replacing optional copies never drops a G1 operation.
        if kind not in ('clock','state','vehicle') or topic not in (*map(inbound,self.vms),VEHICLE_TOPIC):raise ValueError('LINK_OUTPUT')
        if kind=='clock' and (not isinstance(data,PreparedClock) or data.vm not in self.vms or topic!=inbound(data.vm)):
            raise ValueError('LINK_CLOCK_OUTPUT')
        with self.lock:
            self.pending[topic,kind]=data if kind=='clock' else bytes(data)
            self.high_water=max(self.high_water,len(self.pending))

    def take(self):
        try:return self.incoming.get_nowait()
        except queue.Empty:return None

    def start(self):
        if self.thread:raise RuntimeError('LINK_OWNER_EXISTS')
        self.thread=threading.Thread(target=self._run,name='gateway-drive-link',daemon=True);self.thread.start();return self

    def _network_once(self,client,mqtt):
        # All Paho calls belong to this owner. Its external-loop API avoids
        # creating a cross-thread wake socket for the same thread to signal.
        sock=client.socket()
        if sock is None:return mqtt.MQTT_ERR_NO_CONN
        try:
            readable,writable,_=select.select([sock],[sock] if client.want_write() else [],[],.002)
        except InterruptedError:
            readable=writable=[]
        except (OSError,ValueError) as error:
            if self.first_transport is None:self.first_transport=error
            return mqtt.MQTT_ERR_CONN_LOST
        if readable:
            rc=client.loop_read()
            if rc not in (mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_AGAIN):return rc
        if writable and client.socket() is sock:
            rc=client.loop_write()
            if rc not in (mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_AGAIN):return rc
        return client.loop_misc()

    def _run(self):
        import paho.mqtt.client as mqtt
        import errno
        owner=self
        class BoundedClient(mqtt.Client):
            # The frozen Paho parser establishes remaining_length before its
            # first body recv. Check there, before allocating/appending a body.
            # This private boundary is covered by raw-socket oversize tests and
            # must be rechecked when the packaged Paho implementation changes.
            def _sock_recv(self,size):
                if self._in_packet['have_remaining'] and self._in_packet['remaining_length']>MAX_BYTES+256:
                    owner._drop('OVERSIZE');owner.oversize+=1;raise OSError(errno.EMSGSIZE,'drive link packet exceeds bounded profile')
                return super()._sock_recv(size)
        args=dict(client_id='gateway-drive-link-'+str(os.getpid()),protocol=mqtt.MQTTv311)
        if hasattr(mqtt,'CallbackAPIVersion'):args['callback_api_version']=mqtt.CallbackAPIVersion.VERSION2
        client=BoundedClient(**args);client.max_queued_messages_set(2);client.max_inflight_messages_set(1);client.connect_timeout=1.
        if self.username is not None:client.username_pw_set(self.username,self.password)
        def connected(c,u,f,rc,*extra):
            if getattr(rc,'value',rc)==0:
                self.connected=True
                for vm in self.vms:c.subscribe(outbound(vm),qos=0)
        def message(c,u,msg):
            received=self.clock()
            if msg.retain:self._drop('RETAINED');return
            if msg.qos!=0:self._drop('QOS');return
            if len(msg.payload)>MAX_BYTES:self._drop('OVERSIZE');return
            try:
                vm=next(vm for vm in self.vms if msg.topic==outbound(vm))
                value=decode(bytes(msg.payload),schema.DriveLink)
                if value.WhichOneof('payload') not in ('probe','snapshot','applied'):self._drop('DIRECTION');return
                prepared=self.prepare_snapshot(value) if value.WhichOneof('payload')=='snapshot' else None
                self.incoming.put_nowait((vm,value,received,prepared));self.received+=1
            except StopIteration:self._drop('UNBOUND_TOPIC')
            except queue.Full:self._drop('INBOX_FULL')
            except ValueError as error:self._drop('INVALID_WIRE',error)
        client.on_connect=connected;client.on_message=message
        try:
            reconnect=True
            while not self.stop.is_set():
                if reconnect:
                    try:
                        client.connect(self.host,self.port,keepalive=10)
                        # Paho 2.1 leaves Nagle enabled. A clock response must
                        # not wait behind a previous small vehicle publish's
                        # delayed ACK; the original clock budget is unchanged.
                        client.socket().setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                        reconnect=False
                    except OSError as exc:
                        self.failed+=1
                        if self.first_transport is None:self.first_transport=exc
                        self.stop.wait(.1);continue
                rc=self._network_once(client,mqtt)
                if rc not in (mqtt.MQTT_ERR_SUCCESS,mqtt.MQTT_ERR_AGAIN):
                    self.failed+=1;self.connected=False;reconnect=True
                    if self.stop.wait(.1):break
                    continue
                # Paho's max_queued_messages applies to QoS>0, not this QoS0
                # wire. Drain its existing packet before taking another latest
                # slot, so a slow peer cannot grow Paho's outgoing deque.
                if client.want_write():continue
                with self.lock:
                    key=next((k for k in self.pending if k[1]=='clock'),next(iter(self.pending),None))
                    data=self.pending.pop(key) if key else None
                if data is not None:
                    if key[1]=='clock':
                        # Exclude known encode/actor/mailbox time from the
                        # guest's network uncertainty interval. No protobuf
                        # parse/encode or actor lock is needed at this boundary.
                        try:data=data.stamp(int(self.clock()*1e6))
                        except (ValueError,OverflowError) as error:
                            self.failed+=1;self._drop('CLOCK_SEND_TIME',error);continue
                    info=client.publish(key[0],data,qos=0,retain=False)
                    if info.rc==mqtt.MQTT_ERR_SUCCESS:self.sent+=1
                    else:self.failed+=1
        except Exception as exc:
            import traceback
            self.failure=exc;self.first_error=''.join(traceback.format_exception(exc))
        finally:client.disconnect()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(1)
        if self.thread and self.thread.is_alive():raise RuntimeError('LINK_OWNER_TERMINATION_UNCONFIRMED')
        if self.failure is not None:raise RuntimeError('LINK_OWNER_FAILED') from self.failure
