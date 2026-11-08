"""keyboard-source-1 actor boundary. K 원 event/frame과 실제 driver/STOP만 결합한다."""
import os
from pathlib import Path
import queue
import re
from uuid import uuid4

from .authority_journal import canonical
from .keyboard_contract import validate
from .operator_contract import OperatorError


class KeyboardActor:
    def __init__(self,operator,profile_loader=None):
        self.operator=operator;self.registry=operator.sources
        self.ordinary=queue.Queue(maxsize=4);self.fences=queue.Queue(maxsize=2)
        self.outgoing=queue.Queue(maxsize=4);self.profile_loader=profile_loader
        self.scope=None;self.attach=None;self.tx=0;self.rx=0
        self.normal_cache=None;self.fence_cache=None;self.last_fence=None;self.predecessor=None

    def submit(self,packet,peer):
        validate(packet);result=queue.Queue(maxsize=1)
        lane=self.fences if packet['kind'] in ('FENCE','DETACH') else self.ordinary
        lane.put_nowait((packet,peer,result));return result

    def lost(self,peer):
        try:self.fences.put_nowait((None,peer,None))
        except queue.Full:pass  # original input E independently expires

    def _response(self,request,kind,payload):
        self.tx+=1
        return dict(version=1,kind=kind,host_boot_id=self.operator.runner.authority.host,
            gateway_instance_id=self.operator.instance,source_instance=request['source_instance'],
            channel_epoch=request['channel_epoch'],sequence=str(self.tx),request_id=request['request_id'],
            issued_pi_us=str(self.operator.now),expires_pi_us=str(self.operator.now+5_000_000),payload=payload)

    def _state(self,request,reason='OK',accepted=False,*,asynchronous=False,detached=False):
        r=self.registry
        payload=dict(driver_session=None if detached else r.sid,input_epoch=None if detached else r.epoch,
            grant_revision='0' if detached else str(r.grant),generation=self.operator.runner.machine.generation,
            state='DETACHED' if detached else r.state,reason=reason,
            reply_to_kind=None if asynchronous else request['kind'],reply_to_sequence=None if asynchronous else request['sequence'],
            accepted=None if asynchronous else accepted,accepted_state_seq=None if detached else str(r.sequence))
        return self._response(request,'STATE',payload)

    def _profile(self,alias):
        if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}',alias):raise OperatorError('UNSUPPORTED_DEVICE')
        if self.profile_loader is not None:return self.profile_loader(alias)
        from .keyboard_source import load_device_profile
        directory=getattr(self.operator.runner,'keyboard_profile_directory','/etc/xenova/keyboards')
        return load_device_profile(Path(directory)/(alias+'.json'),expected_alias=alias,uid=os.geteuid())

    def _tuple(self):
        r=self.registry;return r.sid,r.epoch,r.grant

    def _check_tuple(self,payload,*,reduce_only=False):
        incoming=(payload['driver_session'],payload['input_epoch'],int(payload['grant_revision']))
        if incoming==self._tuple():return
        if (reduce_only and self.predecessor is not None and self.operator.now<self.predecessor['expires']
                and incoming==self.predecessor['old'] and self._tuple()==self.predecessor['new']):return
        raise OperatorError('SOURCE_MISMATCH')

    def _handle(self,call,peer,result):
        now=self.operator.now;kind=call['kind'];payload=call['payload'];r=self.registry;m=self.operator.runner.machine
        initial=kind=='HELLO' and call['host_boot_id'] is None and call['gateway_instance_id'] is None
        if not initial and (call['host_boot_id']!=self.operator.runner.authority.host or call['gateway_instance_id']!=self.operator.instance):
            raise OperatorError('SOURCE_MISMATCH')
        if not initial and (call['issued_pi_us'] is None or not int(call['issued_pi_us'])<=now<int(call['expires_pi_us'])):
            raise OperatorError('INPUT_EXPIRED')
        if kind=='HELLO':
            if self.scope is not None:
                if (peer,self.scope['source'],self.scope['channel'])!=(self.scope['peer'],call['source_instance'],call['channel_epoch']):
                    raise OperatorError('DRIVER_BUSY')
                if self.normal_cache is not None and self.normal_cache[0]==canonical(call):return self.normal_cache[1]
                raise OperatorError('STALE_SAMPLE')
            profile=self._profile(payload['device_profile'])
            if (payload['device_identity_sha256']!=profile.device_identity_sha256
                    or payload['kernel_abi_sha256']!=profile.kernel_abi_sha256):raise OperatorError('UNSUPPORTED_DEVICE')
            self.scope=dict(peer=peer,source=call['source_instance'],channel=call['channel_epoch'],profile=profile,
                attached=False,generation=m.generation,last=call)
            self.rx=int(call['sequence']);return self._state(call,'HELLO_ACCEPTED',True,detached=True)
        scope=self.scope
        if scope is None or (peer,call['source_instance'],call['channel_epoch'])!=(scope['peer'],scope['source'],scope['channel']):
            raise OperatorError('SOURCE_MISMATCH')
        raw=canonical(call);cache=self.fence_cache if kind=='FENCE' else self.normal_cache
        if cache is not None and raw==cache[0]:return cache[1]
        if int(call['sequence'])<=self.rx:raise OperatorError('STALE_SAMPLE')
        self.rx=int(call['sequence']);scope['last']=call
        if kind=='ATTACH':
            if not scope['profile'].continuous_allowed:raise OperatorError('UNVERIFIED_RADIO')
            if (m.mode.value!='MANUAL' or self.operator.hold or payload['expected_generation']!=m.generation
                    or payload['expected_owner']!=m.active_vm):raise OperatorError('WRONG_MODE')
            if scope['attached'] or self.attach is not None:raise OperatorError('DRIVER_BUSY')
            if r.kind=='HELD' or r.moving:raise OperatorError('DRIVER_BUSY')
            self.operator._stop_driver();r.neutral_candidate()
            self.attach=dict(call=call,peer=peer,result=result,stage='PREPARING',generation=m.generation,
                fence=self.operator.fence_revision,expires=min(now+5_000_000,int(call['expires_pi_us'])),
                device=payload['device_instance'],link=payload['link_epoch'])
            return None
        if not scope['attached'] or r.kind!='HELD':raise OperatorError('SOURCE_MISMATCH')
        if kind=='FRAME':
            self._check_tuple(payload)
            if (payload['device_instance'],payload['link_epoch'])!=(r.device,r.link):raise OperatorError('SOURCE_MISMATCH')
            if r.valid and r.keys and now>=r.expires:r.fence('INPUT_EXPIRED');self.operator._stop_driver()
            sample=int(payload['source_sample_seq']);event=int(payload['key_event_seq']);t1=int(payload['t1_pi_us']);t2=int(payload['t2_pi_us'])
            if sample<=r.sample or event<r.key_event:raise OperatorError('STALE_SAMPLE')
            if not 0<=t1<=t2<=now or t2-t1>20000 or now>=t1+150000:raise OperatorError('INPUT_EXPIRED')
            mask=payload['key_mask'];last=payload['last_event_pi_us']
            if last is not None and (not 0<=int(last)<=t2 or event>r.key_event and now-int(last)>100000):
                raise OperatorError('INPUT_BACKLOG')
            old_mask=r.last_raw_mask;r.last_raw_mask=mask;r.sample=sample;r.key_event=event;r.source_us=t1;r.expires=t1+150000
            r.last_frame=dict(payload)
            r.all_up=r.all_up+1 if mask==0 else r.all_up if mask==64 else 0
            if old_mask&3 and not mask&3:
                r.invalidate_anchor();r.stop_required=True;r._next('action');r.release_event_hwm=event;self.operator._stop_driver()
            if mask&128 or mask&3==3 or mask&12==12:
                r.fence('MODIFIER' if mask&128 else 'KEY_CONFLICT');self.operator._stop_driver()
            elif mask&48:
                r.fence('GLOBAL_STOP' if mask&16 else 'DETACHED');self.operator._stop_driver()
            elif not r.armed or r.blocked:
                r.keys=[];r.valid=False;r._publish('REARM_REQUIRED')
            else:
                direction=1 if mask&1 else -1 if mask&2 else 0
                make=bool(mask&3 and not old_mask&3 and event>getattr(r,'press_hwm',0))
                if direction and (r.stop_required or r.last_direction not in (0,direction)):
                    if not make or not self.operator.stationary():
                        r.fence('STOP_REQUIRED',release=True);self.operator._stop_driver()
                    else:
                        r._next('action');r.press_hwm=event;r.stop_required=False;r.last_direction=direction
                        r.keys=['up' if direction>0 else 'down'];r.valid=True;r.state='DRIVING'
                elif direction:
                    r.keys=['up' if direction>0 else 'down'];r.valid=True;r.state='DRIVING'
                else:r.keys=[];r.valid=True;r.state='ARMED_NEUTRAL'
                if r.keys:
                    if mask&12!=old_mask&12 and old_mask&3 and event>getattr(r,'press_hwm',0):
                        r._next('action');r.press_hwm=event
                    if mask&4:r.keys.append('left')
                    if mask&8:r.keys.append('right')
                r._publish('HELD_SAMPLE' if r.keys else 'NEUTRAL')
                if not r.keys:self.operator.direction_witness(m.active_vm,'manual')
            if self.predecessor is not None:self.predecessor=None
            return self._state(call,'OK',True)
        if kind=='ARM':
            self._check_tuple(payload);event=int(payload['arm_event_seq'])
            if event<=r.arm_hwm:raise OperatorError('STALE_SAMPLE')
            r.arm_hwm=event
            frame=getattr(r,'last_frame',None)
            if (frame is None or int(payload['source_sample_seq'])!=r.sample or event!=r.key_event
                    or frame['key_mask']!=64 or r.all_up<2 or event<=getattr(r,'fence_event_hwm',0)
                    or frame['last_event_pi_us'] is None or now-int(frame['last_event_pi_us'])>100000):
                raise OperatorError('NEUTRAL_REQUIRED')
            if self.operator.hold or not self.operator.stationary() or self.last_fence is not None:raise OperatorError('STOP_REQUIRED')
            old=self._tuple();r.invalidate_anchor();r.epoch=str(uuid4());r.grant+=1;r.armed=True;r.blocked=False
            r.keys=[];r.valid=False;r.state='ARMED_NEUTRAL';r.stop_required=True;r._publish('REARM_REQUIRED')
            self.predecessor=dict(old=old,new=self._tuple(),expires=now+5_000_000)
            return self._state(call,'ARMED',True)
        if kind=='FENCE':
            self._check_tuple(payload,reduce_only=True);revision=int(payload['fence_revision'])
            if revision<=r.fence_hwm:raise OperatorError('STALE_SAMPLE')
            r.fence_hwm=revision;r.fence_event_hwm=max(getattr(r,'fence_event_hwm',0),int(payload['key_event_seq']))
            mask=payload['reason_mask']
            if not mask:raise OperatorError('PROTOCOL_ERROR')
            if mask&1 and int(payload['key_event_seq'])>getattr(r,'release_event_hwm',-1):
                r._next('action');r.release_event_hwm=int(payload['key_event_seq'])
            r.fence(payload['reason'],release=mask==1);self.operator._stop_driver()
            if mask&8:self.operator.keyboard_stop(call)
            self.last_fence=dict(call=call,peer=peer,expires=now+5_000_000,ready=False,detach=bool(mask&4))
            return self._response(call,'FENCE_ACK',dict(driver_session=payload['driver_session'],input_epoch=payload['input_epoch'],
                fence_revision=payload['fence_revision'],accepted_pi_us=str(now)))
        if kind=='DETACH':
            if payload['driver_session']!=r.sid or payload['input_epoch']!=r.epoch or int(payload['final_fence_revision'])!=r.fence_hwm:
                raise OperatorError('SOURCE_MISMATCH')
            if self.last_fence is not None and not self.last_fence['ready']:raise OperatorError('UNCONFIRMED')
            r.fence('DETACHED');self.operator._stop_driver();scope['attached']=False
            response=self._state(call,'DETACHED',True,detached=True);self.scope=None;return response
        raise OperatorError('PROTOCOL_ERROR')

    def take_outgoing(self):
        try:return self.outgoing.get_nowait()
        except queue.Empty:return None

    def _send_async(self,peer,response):
        try:self.outgoing.put_nowait((peer,response))
        except queue.Full:
            self.registry.fence('LINK_LOST');self.operator._stop_driver()
            if self.scope is not None:self.scope['closing']=self.operator.now+5_000_000

    def tick(self):
        scope=self.scope;r=self.registry;m=self.operator.runner.machine
        if scope is not None and scope['attached']:
            if r.kind!='HELD' or r.source!=scope['source']:
                self._send_async(scope['peer'],self._state(scope['last'],'REVOKED',False,asynchronous=True,detached=True))
                self.scope=None
            elif scope['generation']!=m.generation:
                pending=self.operator.runner.authority.value.get('pending') or {}
                if (m.active_app=='manual' and pending.get('manual_policy')=='CONTINUE_SESSION'
                        and pending.get('manual_session')==r.sid and (not r.keys or self.operator.now<r.expires)):
                    scope['generation']=m.generation
                    self._send_async(scope['peer'],self._state(scope['last'],'OK',True,asynchronous=True))
                else:
                    r.fence('REVOKED');self.operator._stop_driver();scope['closing']=self.operator.now+5_000_000
        for lane in (self.fences,self.ordinary):
            try:call,peer,result=lane.get_nowait()
            except queue.Empty:continue
            if call is None:
                if self.scope is not None and self.scope['peer']==peer:
                    self.registry.fence('LINK_LOST');self.operator._stop_driver()
                    if self.attach is not None:
                        self.attach['cancelled']=True
                        self.attach['result'].put_nowait(self._state(self.attach['call'],'UNCONFIRMED',False,detached=True))
                        self.attach=None
                    self.scope['closing']=self.operator.now+5_000_000
                continue
            try:response=self._handle(call,peer,result)
            except (OperatorError,ValueError,OSError) as exc:
                reason=getattr(exc,'code','PROTOCOL_ERROR')
                if reason not in __import__('xenova_gateway.keyboard_contract',fromlist=['REASONS']).REASONS:reason='PROTOCOL_ERROR'
                response=self._state(call,reason,False,detached=self.scope is None or not self.scope['attached'])
            if response is not None:
                if self.scope is not None and peer==self.scope['peer']:
                    if call['kind']=='FENCE' and response['kind']=='FENCE_ACK':self.fence_cache=(canonical(call),response)
                    elif call['kind']!='FENCE':self.normal_cache=(canonical(call),response)
                result.put_nowait(response)
        self._attach_tick()
        if self.scope is not None and 'closing' in self.scope:
            if self.operator.stationary():self.registry.neutral_candidate();self.scope=None
            elif self.operator.now>=self.scope['closing']:
                self.operator._stop('KEYBOARD_STOP_UNCONFIRMED');self.scope=None
        fence=self.last_fence
        if fence is not None and not fence['ready']:
            if self.operator.stationary():
                call=fence['call'];p=call['payload'];device=self.operator.runner.device_view()
                sample=device.get('vehicle_sample') or {}
                matched=self.operator.runner.matched_stop()
                if matched is None:return
                from .scene_authority import COMMAND3
                stop_sequence=COMMAND3.unpack(matched.command_payload)[2]
                response=self._response(call,'STOP_READY',dict(driver_session=p['driver_session'],input_epoch=p['input_epoch'],
                    fence_revision=p['fence_revision'],stop_command_sequence=str(stop_sequence),
                    stationary_sample_sequence=str(sample.get('sequence',0))))
                self._send_async(fence['peer'],response);fence['ready']=True
                if not self.registry.blocked:self.registry.state='ARMED_NEUTRAL'
                if not fence['detach']:self.last_fence=None
            elif self.operator.now>=fence['expires']:
                self.operator._stop('KEYBOARD_STOP_UNCONFIRMED');self.last_fence=None

    def _attach_tick(self):
        item=self.attach
        if item is None:return
        r=self.registry;m=self.operator.runner.machine
        if (self.operator.now>=item['expires'] or item['fence']!=self.operator.fence_revision
                or m.mode.value!='MANUAL'):
            item['result'].put_nowait(self._state(item['call'],'UNCONFIRMED',False,detached=True));self.attach=None;return
        if item['stage']=='PREPARING':
            if m.generation!=item['generation']:
                item['result'].put_nowait(self._state(item['call'],'UNCONFIRMED',False,detached=True));self.attach=None;return
            if not self.operator.stationary() or not m._qualified(m.active_vm,'manual',self.operator.runner._clock(),held=True):return
            def bound(ok,generation):item.update(stage='APPLYING' if ok else 'FAILED',committed=generation)
            if self.operator.bind_driver(item['call'],item['expires'],bound,
                    current=lambda:self.attach is item and not item.get('cancelled',False)):item['stage']='COMMITTING'
        elif item['stage']=='APPLYING' and m.generation==item['committed'] and m.phase=='APPLIED':
            scope=self.scope;r.kind='HELD';r.source=scope['source'];r.channel=scope['channel'];r.device=item['device'];r.link=item['link']
            r.epoch=str(uuid4());r.grant+=1;r.valid=False;r.keys=[];r.armed=False;r.blocked=True;r.state='REARM_REQUIRED'
            r.sample=0;r.source_us=r.expires=r.key_event=0;r.all_up=0;r._publish('REARM_REQUIRED')
            scope.update(attached=True,generation=m.generation)
            response=self._response(item['call'],'GRANT',dict(driver_session=r.sid,input_epoch=r.epoch,grant_revision=str(r.grant),
                generation=m.generation,profile_sha256=scope['profile'].profile_sha256,state=r.state,reason='ATTACHED'))
            self.normal_cache=(canonical(item['call']),response);item['result'].put_nowait(response);self.attach=None
        elif item['stage']=='FAILED':
            item['result'].put_nowait(self._state(item['call'],'UNCONFIRMED',False,detached=True));self.attach=None
