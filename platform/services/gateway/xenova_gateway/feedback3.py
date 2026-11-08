"""부록B feedback-3의 유일 serial owner adapter. ESP32 펌웨어나 실차 수락 증거는 아니다.

원4시각·실제 latch·install ACK·STOP pin을 검사하며 clock/도착/반복 STOP으로 원 기한을
갱신하지 않는다. geometry 적분은 기존 VehicleOdometry의 명시 mapped-observation 포트를 쓴다.
"""
from collections import OrderedDict
from dataclasses import dataclass
import math
import struct
from uuid import UUID,uuid4

from xenova_drive_world import schema,encode,decode
from xenova_drive_world.mapper_clock import drift_pad
from .feedback import FeedbackSerialSink,FeedbackError,packet,HELLO,HELLO_ACK,STATUS
from .frame import DriveFrame,StopFrame,encode as legacy_encode
from .scene_authority import MatchedStop,StationaryPolicy,COMMAND3,FEEDBACK3,check_stationary_window
from .sink import SinkError

CLOCK_PROBE=0x0f10;CLOCK_REPLY=0x0f11;CLOCK_INSTALL=0x0f12;CLOCK_ACK=0x0f13
COMMAND=0x0111;REPORT=0x0f14;SENSOR=0x0f15
HELLO3=struct.Struct('<B16s32s');ACK3=struct.Struct('<B16s16sH32s32sB')
PROBE3=struct.Struct('<16s16sQQ');CLOCK3=struct.Struct('<16s16sQQQQQQqqBiH')
INSTALL3=struct.Struct('<16s16s16sQQQqqQQ');CLOCK_ACK3=struct.Struct('<16s16s16sQQBHQQ')
SENSOR3=struct.Struct('<16s16sQQqqBiH16sQ')
REQUIRED_CAPS=0x01f7


@dataclass(frozen=True)
class Feedback3Profile:
    vehicle_config_sha256: str
    firmware_sha256: str
    timestamp_error_us: int
    drift_ppm: int
    max_offset_width_us: int
    lease_us: int
    probe_timeout_us: int
    j_cutoff_us: int
    j_apply_us: int
    stationary: StationaryPolicy
    phase_budget_us: int
    steering_tolerance_rad: float
    steering_settle_us: int

    def __post_init__(self):
        for value in (self.vehicle_config_sha256,self.firmware_sha256):
            if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('registered feedback3 config/firmware hashes')
        for name in ('timestamp_error_us','drift_ppm','j_cutoff_us','j_apply_us'):
            if type(getattr(self,name)) is not int or not 0<=getattr(self,name)<1000000:raise ValueError('feedback3 measured timing')
        if (not 0<self.lease_us<=1000000 or not 0<self.probe_timeout_us<=200000 or not self.max_offset_width_us
                or type(self.stationary) is not StationaryPolicy or not 0<self.phase_budget_us<=10000000
                or not math.isfinite(self.steering_tolerance_rad) or self.steering_tolerance_rad<0
                or not 0<self.steering_settle_us<=self.phase_budget_us):raise ValueError('explicit feedback3 policy')


class Feedback3SerialSink(FeedbackSerialSink):
    def __init__(self,*args,profile,**kwargs):
        if type(profile) is not Feedback3Profile or kwargs.get('odometry') is None:raise ValueError('feedback3 measured policy/odometry required')
        self.profile=profile;self.stationary_policy=profile.stationary
        self.clock_epoch=str(uuid4());self.clock_sequence=0;self.probe_sequence=0;self.outstanding=None
        self.last_probe_us=0;self.previous_interval=None;self.streak=0;self.leases=OrderedDict();self.install=None
        self.stop_pin=None;self.report_hwm=0;self.gate_revision=0;self.last_motion=False
        self._matched_stop_snapshot=None;self._direction_snapshot=None;self.phase_key=None;self.phase_end=0
        self.hold_row=None;self.hold_start=None
        super().__init__(*args,**kwargs)
        self.protocol_version=3;self.odometry.clock_profile3=True

    def _view(self):
        value=super()._view()
        value.update(profile='feedback-3',clock_epoch=self.clock_epoch,clock_lease_revision=self.clock_sequence,
            clock_ready=self.ready and self._sensor_available() and self._lease(int(self.clock()*1e6)) is not None if hasattr(self,'clock') else False)
        if hasattr(self,'odometry'):
            self._matched_stop_snapshot=self._make_matched_stop()
            pin=self.stop_pin
            self._direction_snapshot=None if pin is None else dict(pin=pin.copy(),
                rows=tuple(dict(row) for row in self.odometry.samples),clock_ready=value['clock_ready'],
                sample=value.get('vehicle_sample'),hold=None if self.hold_row is None else self.hold_row.copy(),
                hold_start=self.hold_start,gate_revision=self.gate_revision,matched=self._matched_stop_snapshot,
                moving=bool(self.last_motion),clock_end=0 if self._lease(int(self.clock()*1e6)) is None else self._lease(int(self.clock()*1e6))['expires'])
        return value

    def _lease(self,now):
        for lease in reversed(tuple(self.leases.values())):
            if lease['ack'] and now<lease['expires']:return lease
        return None

    def _bounds(self,lease,at):
        pad=drift_pad(self.profile.drift_ppm,abs(at-lease['reference']))
        return lease['lower']-pad,lease['upper']+pad

    def _poll(self,now,*,owned_ready=None):
        if self.started is None:
            self.started=now;self._send(packet(HELLO,HELLO3.pack(3,UUID(self.pi_session).bytes,bytes.fromhex(self.profile.vehicle_config_sha256))))
        super()._poll(now,owned_ready=owned_ready)
        at=int(self.clock()*1e6)
        if self.ready and self._sensor_available():
            if self.outstanding is not None and at>=self.outstanding[1]+self.profile.probe_timeout_us:
                self.outstanding=None;self.streak=0
            if self.outstanding is None and at-self.last_probe_us>=200000:
                if self.probe_sequence>=2**63-1:self._fail('DEVICE_COUNTER_EXHAUSTED');return
                self.probe_sequence+=1;self.last_probe_us=at;self.outstanding=(self.probe_sequence,at)
                self._send(packet(CLOCK_PROBE,PROBE3.pack(UUID(self.pi_session).bytes,UUID(self.device_session).bytes,self.probe_sequence,at)))
            if self.install is not None and at<self.install['expires'] and not self.install['ack']:
                if at-self.install.get('retry',0)>=50000:
                    self.install['retry']=at;self._send(self.install['wire'])
        self.snapshot=self._view()

    def _session(self,pi,device):
        if str(UUID(bytes=pi))!=self.pi_session or str(UUID(bytes=device))!=self.device_session:
            raise FeedbackError('feedback3 original session')

    def _receive(self,kind,payload,now):
        at=int(now*1e6);p=self.profile
        if kind==HELLO_ACK:
            version,pi,device,caps,configuration,firmware,bits=ACK3.unpack(payload)
            if (version!=3 or str(UUID(bytes=pi))!=self.pi_session or UUID(bytes=device).int==0
                    or configuration.hex()!=p.vehicle_config_sha256 or firmware.hex()!=p.firmware_sha256
                    or caps&REQUIRED_CAPS!=REQUIRED_CAPS or bits!=self.odometry.config.encoder_bits):
                raise FeedbackError('feedback3 config/capability/ABI')
            session=str(UUID(bytes=device))
            if self.device_session is not None and self.device_session!=session:
                self._fail('DEVICE_RESTARTED');self.ready=False;return
            self.device_session=session;self.capabilities=caps;self.ready=True;self.odometry.device_session(session);return
        if not self.ready:raise FeedbackError('feedback3 handshake required')
        if kind==CLOCK_REPLY:
            pi,device,seq,p1,d2,latch,d3,sample,left,right,quality,steer,error=CLOCK3.unpack(payload);self._session(pi,device)
            if (self.outstanding!=(seq,p1) or not p1<=at<p1+p.probe_timeout_us or not 0<d2<=latch<=d3
                    or any(n>=2**63 for n in (seq,p1,d2,latch,d3,sample))):raise FeedbackError('clock3 original probe/latch')
            self.outstanding=None;pad=2*p.timestamp_error_us+drift_pad(p.drift_ppm,at-p1)
            lower,upper=p1-d2-pad,at-d3+pad
            if lower>upper or upper-lower>p.max_offset_width_us:raise FeedbackError('clock3 interval')
            if self.previous_interval is not None:
                lo,hi,previous=self.previous_interval;growth=drift_pad(p.drift_ppm,at-previous)
                if max(lower,lo-growth)>min(upper,hi+growth):
                    self._fail('DEVICE_CLOCK_DISCONTINUITY');self.leases.clear();return
                self.streak=min(2,self.streak+1)
            else:self.streak=1
            self.previous_interval=lower,upper,at
            if self.streak<2:return
            self.clock_sequence+=1
            if self.clock_sequence>=2**63:self._fail('DEVICE_COUNTER_EXHAUSTED');return
            expires=p1+p.lease_us;pad_end=drift_pad(p.drift_ppm,expires-at)
            cutoff=expires-(upper+pad_end)-p.j_cutoff_us
            if cutoff<=d3:raise FeedbackError('clock3 cutoff')
            lease=dict(sequence=self.clock_sequence,probe=seq,reference=at,lower=lower,upper=upper,
                expires=expires,cutoff=cutoff,ack=False)
            lease['wire']=packet(CLOCK_INSTALL,INSTALL3.pack(pi,device,UUID(self.clock_epoch).bytes,
                self.clock_sequence,seq,at,lower,upper,expires,cutoff))
            if len(self.leases)>=2:
                oldest=next(iter(self.leases));old=self.leases[oldest]
                if any(row.get('clock')==oldest and row.get('expires',0)>at for row in self.pending.values()):
                    raise FeedbackError('clock3 live lease slot pinned')
                del self.leases[oldest]
            self.leases[self.clock_sequence]=lease;self.install=lease;self._send(lease['wire'])
            # This probe's actual latch is an observation, never a cached sample.
            self._sample(sample,latch,left,right,quality,steer,error,lease,at);return
        if kind==CLOCK_ACK:
            pi,device,epoch,seq,probe,status,reason,device_now,cutoff=CLOCK_ACK3.unpack(payload);self._session(pi,device)
            lease=self.leases.get(seq)
            if (epoch!=UUID(self.clock_epoch).bytes or lease is None or probe!=lease['probe'] or cutoff!=lease['cutoff']
                    or at>=lease['expires'] or device_now>=cutoff or status!=1 or reason):raise FeedbackError('clock3 install ACK')
            lease['ack']=True;return
        if kind==SENSOR:
            pi,device,sample,stamp,left,right,quality,steer,error,epoch,seq=SENSOR3.unpack(payload);self._session(pi,device)
            lease=self.leases.get(seq)
            if epoch!=UUID(self.clock_epoch).bytes or lease is None or not lease['ack']:raise FeedbackError('sensor3 clock')
            self._sample(sample,stamp,left,right,quality,steer,error,lease,at);return
        if kind!=REPORT:raise FeedbackError('feedback3 frame kind')
        values=FEEDBACK3.unpack(payload)
        pi,device,seq,generation,counter,command_kind,status,report_seq,sample,stamp,flags,velocity,steering,stationary,epoch,clock_seq,cutoff,stop_seq,reason=values
        self._session(pi,device)
        if report_seq<=self.report_hwm:self.duplicates+=1;return
        if flags&~7 or stationary not in (0,1) or status not in STATUS:raise FeedbackError('feedback3 fields')
        if seq==0 and command_kind==0 and status==4:
            self.report_hwm=report_seq;self._fail('DEVICE_FAULT');return
        row=self.pending.get(seq)
        if row is None and self.stop_pin is not None and self.stop_pin['sequence']==seq:row=self.stop_pin
        if row is None or (generation,counter,command_kind)!=(row['frame'].generation,row['frame'].counter,row['kind']):
            raise FeedbackError('feedback3 command reference')
        command=COMMAND3.unpack(row['command'])
        if (epoch,clock_seq,cutoff,stop_seq)!=(command[3],command[4],command[7],command[11]):
            raise FeedbackError('feedback3 deadline echo')
        if row['kind']!=2 and at>=row['expires']:raise FeedbackError('feedback3 original command expired')
        if not flags&1 and velocity or not flags&2 and steering:raise FeedbackError('feedback3 unsupported measurement')
        if status in (3,4):self._fail('DEVICE_REJECTED' if status==3 else 'DEVICE_FAULT')
        self.report_hwm=report_seq;row['accepted']=status in (1,2);row['applied']=status==2
        row['feedback']=payload
        evidence=dict(accepted=row['accepted'],applied=row['applied'],physical_stop=bool(stationary) if flags&4 and status==2 and row['kind']==2 else None,
            reported_velocity_mps=velocity/1000 if flags&1 else None,reported_steering_rad=steering/1000 if flags&2 else None,
            observed_monotonic=now,command_generation=generation,command_counter=counter,command_sequence=seq,
            command_kind='STOP' if row['kind']==2 else 'HOLD_STEER' if row['kind']==3 else 'DRIVE',sample_seq=sample,
            observation_source='device_report',pi_session=self.pi_session,device_session=self.device_session,status=STATUS[status])
        if seq==self.latest_command:self.last_report=evidence
        if status==2:
            try:self.events.put_nowait(evidence)
            except __import__('queue').Full:self._fail('DEVICE_EVIDENCE_OVERFLOW')

    def _sample(self,sample,stamp,left,right,quality,steer,error,lease,now):
        if now>=lease['expires'] or sample>=2**63 or stamp>=2**63:raise FeedbackError('sensor3 lease/counter')
        lower,upper=self._bounds(lease,now)
        lo,hi=stamp+lower,min(stamp+upper,now)
        if not 0<=lo<=hi<=now or now-lo>self.odometry.config.maximum_sample_age_us:
            raise FeedbackError('sensor3 observation window')
        self.odometry.observe_mapped(self.pi_session,self.device_session,stamp,sample,left,right,quality,steer,error,lo,hi)

    @property
    def matched_stop(self):
        return self._matched_stop_snapshot

    def _make_matched_stop(self):
        pin=self.stop_pin
        if pin is None or self.last_motion or not pin.get('applied') or 'feedback' not in pin:return None
        values=FEEDBACK3.unpack(pin['feedback']);sample=values[8]
        rows=[row for row in self.odometry.samples if row['source_sample']<=sample
            and row['pi_sample_us']-row['clock_error_us']>=pin['submitted_us']]
        if rows:
            end=rows[-1]['pi_sample_us']
            while len(rows)>2 and end-rows[1]['pi_sample_us']>=self.stationary_policy.window_us+2*rows[1]['clock_error_us']:
                rows.pop(0)
        if (not rows or rows[-1]['source_sample']!=sample or self.odometry.latest is None
                or self.odometry.latest['source_sample']!=sample):return None
        # Preserve the original report/sample relation; a newer odom packet does
        # not refresh an older STOP report's stationary evidence.
        window=tuple(encode(schema.VehicleSample(major=1,**{**row,'gateway_instance':pin['gateway']})) for row in rows)
        if len(window)<2:return None
        return MatchedStop(pin['command'],pin['feedback'],window)

    def write(self,frame,t):
        if not self.ready:raise SinkError('feedback3 handshake not ready')
        now=int(t*1e6);witness=frame.witness or {};command=witness.get('command',{})
        kind=2 if isinstance(frame,StopFrame) else 3 if command.get('command_kind')=='HOLD_STEER' else 1
        if self.fault and kind!=2:raise SinkError('feedback3 source fault')
        if self.sequence>=2**63-1:raise SinkError('feedback3 sequence exhausted')
        lease=None if kind==2 else self._lease(now)
        if kind!=2 and lease is None:raise SinkError('feedback3 installed clock unavailable')
        if kind!=2 and (frame.issued_pi_us is None or frame.expires_pi_us is None
                or not frame.issued_pi_us<frame.expires_pi_us<=frame.issued_pi_us+100000
                or not now<frame.expires_pi_us<=lease['expires']):raise SinkError('feedback3 original command expiry')
        pin=self.stop_pin
        if kind==2 and pin is not None and not self.last_motion and self._same_episode(pin['witness'],witness,frame.generation):
            return self._receipt(pin['frame'],pin,0)
        if kind!=2:
            if pin is None or not pin.get('applied'):raise SinkError('feedback3 original STOP required')
            source=self.odometry.current(now)
            if source is None:raise SinkError('feedback3 current Vehicle required')
            if not self.last_motion or kind==3:
                matched=self.matched_stop
                try:
                    if matched is None:raise ValueError('STOP measurement window unavailable')
                    matched.check(dict(vehicle=source['vehicle'],calibration=source['calibration'],odom_epoch=source['odom_epoch']),
                        self.odometry.host_boot,pin['gateway'],pin['frame'].generation,self.pi_session,self.device_session,self.stationary_policy,now)
                except ValueError:
                    gate=self.direction_witness(witness.get('vm_id'),witness.get('app_id'),
                        type('Current',(),{'generation':frame.generation})(),now)
                    if gate is None or kind==1 and gate.gate.state!=4:raise SinkError('fresh original HOLD stationary evidence required')
            direction=1 if frame.v_mps>0 else -1 if frame.v_mps<0 else 0
            if kind==3 and (not self.capabilities&0x400 or direction):raise SinkError('stationary steering unsupported')
            if kind==3 and not self._hold_matches(pin,witness):raise SinkError('HOLD_STEER original cusp/source mismatch')
            if kind==1 and not self.last_motion and command.get('producer_role')=='AUTO' and pin['witness'].get('command',{}).get('next_direction'):
                gate=self.direction_witness(witness['vm_id'],witness['app_id'],type('Current',(),{'generation':frame.generation})(),now)
                if (gate is None or gate.gate.state!=4 or gate.gate.requested_direction!=direction
                        or not self._next_matches(pin,witness)):
                    raise SinkError('actual cusp READY required')
            if self.last_motion and direction and direction!=self.last_motion:raise SinkError('direction change needs a new STOP')
        self.sequence+=1;sequence=self.sequence
        if kind==2:
            clock=bytes(16);clock_seq=issued=expires=cutoff=0;stop_sequence=0;v=steer=0
        else:
            clock=UUID(self.clock_epoch).bytes;clock_seq=lease['sequence'];issued=frame.issued_pi_us;expires=frame.expires_pi_us
            _,upper=self._bounds(lease,expires);cutoff=min(lease['cutoff'],expires-upper-self.profile.j_cutoff_us)
            if cutoff<=0:raise SinkError('feedback3 cutoff underflow')
            stop_sequence=pin['sequence'];v=round(frame.v_mps*1000);steer=round(frame.steer_rad*1000)
        payload=COMMAND3.pack(UUID(self.pi_session).bytes,UUID(self.device_session).bytes,sequence,clock,clock_seq,
            issued,expires,cutoff,frame.generation,frame.counter,kind,stop_sequence,v,steer,0,0)
        wire=packet(COMMAND,payload);self._send(wire)
        # This is the first actual phase STOP budget. An individual DRIVE or
        # HOLD_STEER's <=100ms command E is a separate, shorter output lease.
        phase_key=self._phase_key(witness,frame.generation)
        if kind==2:
            if phase_key!=self.phase_key:
                self.phase_key=phase_key;self.phase_end=now+self.profile.phase_budget_us
            self.hold_row=None;self.hold_start=None
        phase_end=self.phase_end
        cap=witness.get('phase_expires_pi_us')
        if cap is not None:phase_end=min(phase_end,cap);self.phase_end=phase_end
        row=dict(sequence=sequence,frame=frame,command=payload,witness=witness,kind=kind,clock=clock_seq,
            expires=expires,deadline=t+self.timeout,accepted=False,applied=False,phase_end=phase_end,
            gateway=witness.get('gateway_instance',getattr(self,'gateway_instance',None)),submitted_us=now)
        self.pending[sequence]=row
        while len(self.pending)>16:self.pending.popitem(last=False)
        self.latest_command=sequence
        if kind==2:self.stop_pin=row;self.last_motion=False;self.gate_revision+=1
        elif kind==3:
            if self.hold_row is None or self.hold_row['frame'].steer_rad!=frame.steer_rad:self.hold_start=now
            self.hold_row=row
        elif v:self.last_motion=1 if v>0 else -1
        self.snapshot=self._view();return self._receipt(frame,row,len(wire))

    def _receipt(self,frame,row,wire_bytes):
        from .output_owner import SubmissionReceipt
        result=SubmissionReceipt(len(legacy_encode(frame)),frame)
        result.metadata=dict(serial_profile='feedback-3',wire_bytes=wire_bytes,command_sequence=row['sequence'],
            pi_session=self.pi_session,device_session=self.device_session)
        return result

    @staticmethod
    def _same_episode(old,new,generation):
        fields=('generation','producer_role','plan_revision','phase_revision','segment_index','cusp_index',
            'mission_id','mission_revision','grant_revision','driver_session','driver_action_seq','driver_state_seq','next_direction')
        a=old.get('command',{});b=new.get('command',{})
        return bool(old.get('generation',generation)==generation
            and old.get('direction_intent_admitted')==new.get('direction_intent_admitted')
            and all(a.get(k)==b.get(k) for k in fields))

    @staticmethod
    def _phase_key(witness,generation):
        c=witness.get('command',{})
        return (generation,witness.get('vm_id'),witness.get('execution_id'),witness.get('agent_instance_id'),
            c.get('mission_id'),c.get('mission_revision'),c.get('grant_revision'),
            c.get('phase_revision'),c.get('cusp_index'),c.get('driver_session'))

    @staticmethod
    def _hold_matches(pin,witness):
        a=pin['witness'];b=witness
        if any(a.get(k)!=b.get(k) for k in ('vm_id','app_id','execution_id','agent_instance_id','generation')):return False
        keys=('plan_revision','phase_revision','segment_index','cusp_index','mission_id','mission_revision',
            'grant_revision','driver_session','driver_action_seq','driver_state_seq','next_direction')
        return bool(a.get('command',{}).get('next_direction') and all(
            a['command'].get(k)==b.get('command',{}).get(k) for k in keys))

    @staticmethod
    def _next_matches(pin,witness):
        old=pin['witness'];a=old.get('command',{});b=witness.get('command',{})
        keys=('plan_revision','phase_revision','mission_id','mission_revision','grant_revision')
        return (all(old.get(k)==witness.get(k) for k in ('vm_id','app_id','execution_id','agent_instance_id','generation'))
            and all(a.get(k)==b.get(k) for k in keys) and b.get('segment_index')==a.get('segment_index',-2)+1
            and b.get('cusp_index')==a.get('cusp_index',-2)+1 and b.get('next_direction')==0)

    def direction_witness(self,vm,role,machine,now,manual=None):
        # The serial I/O owner publishes one immutable snapshot. Actor reads do
        # not traverse a concurrently mutating pending table/odometry deque.
        snapshot=self._direction_snapshot
        if snapshot is None or not snapshot['clock_ready'] or snapshot['moving']:return None
        pin=snapshot['pin'];sample=snapshot['sample'];matched=snapshot['matched']
        if ((role!='manual' and now>=pin['phase_end']) or sample is None
                or pin['frame'].generation!=machine.generation):return None
        source=pin['witness'];command=source.get('command')
        if role!='manual':
            if (not source.get('direction_intent_admitted') or command is None or source.get('vm_id')!=vm
                    or source.get('app_id')!=role or source.get('generation')!=machine.generation
                    or command.get('next_direction') not in (-1,1)):return None
            if not pin.get('applied') or matched is None and not (snapshot['hold'] and snapshot['hold'].get('applied')):
                return self._witness(pin,command,sample,machine,role,1,command['next_direction'],pin['phase_end'],snapshot['gate_revision'])
        elif not pin.get('applied'):return None
        hold=snapshot['hold']
        current=dict(vehicle=sample['vehicle'],calibration=sample['calibration'],odom_epoch=sample['odom_epoch'])
        try:
            if hold is not None and hold.get('applied') and 'feedback' in hold:
                report=FEEDBACK3.unpack(hold['feedback'])
                rows=[row for row in snapshot['rows'] if row['pi_sample_us']-row['clock_error_us']>=pin['submitted_us']]
                if rows:
                    while len(rows)>2 and rows[-1]['pi_sample_us']-rows[1]['pi_sample_us']>=self.stationary_policy.window_us+2*rows[1]['clock_error_us']:rows.pop(0)
                window=tuple(encode(schema.VehicleSample(major=1,**{**row,'gateway_instance':pin['gateway']})) for row in rows)
                if len(window)<2:return None
                check_stationary_window(window,current,self.odometry.host_boot,pin['gateway'],self.pi_session,
                    self.device_session,self.stationary_policy,now,rows[-1]['source_sample'],rows[-1]['source_us'])
            else:
                if matched is None:return None
                matched.check(current,self.odometry.host_boot,pin['gateway'],machine.generation,
                    self.pi_session,self.device_session,self.stationary_policy,now)
        except ValueError:return None
        source=pin['witness'];command=source.get('command')
        if role=='manual' and manual is not None:
            anchor=manual.current_anchor()
            if anchor is None:
                anchor=manual.pin_neutral(dict(sequence=pin['sequence'],revision=snapshot['gate_revision'],generation=machine.generation))
            if anchor is None:return None
            # The anchor is only a human-action floor, not a time lease. This
            # body's physical E derives from actual original sensor/report bytes.
            last=decode(matched.vehicle_window[-1],schema.VehicleSample)
            end=min(snapshot['clock_end'],last.pi_sample_us-last.clock_error_us+self.stationary_policy.max_age_us)
            if now>=end:return None
            command=dict(calculation_sequence='0',plan_revision='0',phase_revision='0',segment_index=0,cusp_index=0,
                mission_id=str(UUID(int=0)),mission_revision='0',grant_revision='0',driver_session=anchor['sid'],
                driver_action_seq=str(anchor['action']),driver_state_seq=str(anchor['state']))
            state=4;direction=0
        else:
            if (not source.get('direction_intent_admitted') or command is None or source.get('vm_id')!=vm or source.get('app_id')!=role
                    or source.get('generation')!=machine.generation):return None
            direction=command.get('next_direction',0)
            if direction not in (-1,1):return None
            state=2;end=pin['phase_end'];hold=snapshot['hold']
            if hold is not None and hold.get('applied'):
                target=hold['frame'].steer_rad;start=snapshot['hold_start']
                rows=[]
                for row in reversed(snapshot['rows']):
                    if (row['pi_sample_us']-row['clock_error_us']<start or row.get('measured_steering_rad') is None
                            or abs(row['measured_steering_rad']-target)+row['steering_error_rad']>self.profile.steering_tolerance_rad):break
                    if rows and rows[-1]['pi_sample_us']+rows[-1]['clock_error_us']-row['pi_sample_us']+row['clock_error_us']>self.stationary_policy.max_gap_us:break
                    rows.append(row)
                if rows:
                    first,last=rows[-1],rows[0]
                    if (last['pi_sample_us']-last['clock_error_us']-first['pi_sample_us']-first['clock_error_us']>=self.profile.steering_settle_us
                            and now-last['pi_sample_us']+last['clock_error_us']<=self.stationary_policy.max_age_us):state=4
        return self._witness(pin,command,sample,machine,role,state,direction,end,snapshot['gate_revision'])

    def _witness(self,pin,command,sample,machine,role,state,direction,end,revision):
        value=schema.DirectionGateWitness(control_generation=machine.generation,producer_role=1 if role=='autonomous' else 2,
            calculation_sequence=int(command['calculation_sequence']),plan_revision=int(command['plan_revision']),
            phase_revision=int(command['phase_revision']),segment_index=command['segment_index'],cusp_index=command['cusp_index'],
            mission_id=UUID(command['mission_id']).bytes,mission_revision=int(command['mission_revision']),grant_revision=int(command['grant_revision']),
            driver_session=UUID(command['driver_session']).bytes,driver_action_seq=int(command['driver_action_seq']),
            driver_state_seq=int(command['driver_state_seq']),vehicle_sequence=sample['sequence'],valid_until_pi_us=end,
            pi_session=self.pi_session,device_session=self.device_session)
        value.gate.CopyFrom(schema.DirectionGateRef(gate_revision=revision,stop_command_sequence=pin['sequence'],
            state=state,requested_direction=direction));return value
