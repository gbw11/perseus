"""단일 Gateway actor의 bounded 회피 등록·원 예산·실제 State 결박.

registry1/최근 exact reply1/terminal1만 소유한다. 등록은 RAM 사건이며 새로운
thread/DB/fsync를 만들지 않는다. C 계산/geometry는 검산 입력이고 출력 권위는 아니다.
"""
from copy import deepcopy
from hashlib import sha256
import math
import struct
from uuid import UUID,uuid4

from xenova_drive_world import schema,encode,decode
from xenova_mqtt.drive_binding import app_instance
from .absolute_meter import upper_distance


NIL=bytes(16);ZERO=bytes(32);EPISODE=16


def copied(message):
    result=type(message)();result.CopyFrom(message);return result


def none_snapshot(epoch,revision):
    from xenova_drive_world import episode_core_sha256
    core=schema.EpisodeCore(state=1,episode_id=NIL,revision=0,phase=0,phase_revision=0,
        home_lane_id=0,target_lane_id=0,policy=0,blocker_position_bound_m=0,
        blocker_world_sequence=0,blocker_observation_sequence=0,birth_snapshot_id=NIL,
        basis_revision=0,basis_prefix_count=0,basis_prefix_sha256=ZERO,source_geometry_sha256=ZERO,
        origin_vehicle_sequence=0,started_pi_us=0,deadline_pi_us=0,phase_started_pi_us=0,
        phase_deadline_pi_us=0,left_baseline_ticks=0,right_baseline_ticks=0,meter_epoch=NIL,
        meter_baseline_total_ticks=0,max_travel_m=0,progress_mode=0,
        phase_goal=schema.Pose(x=0,y=0,yaw=0),stop_wait=False,stop_wait_started_pi_us=0)
    return schema.EpisodeSnapshot(core=core,progress=schema.EpisodeProgress(used_travel_ticks=0,
        last_progress_pi_us=0,vehicle_sequence=0,station_lower_m=0,remaining_upper_m=0,revision=0),
        acceptance=schema.EpisodeAcceptanceRef(registry_epoch=epoch,registry_revision=revision,
            request_nonce=NIL,request_sha256=ZERO,accepted_core_sha256=episode_core_sha256(core),
            request_sequence=0,origin_vm=NIL,origin_execution=NIL,origin_app_instance=NIL,
            origin_generation=0,origin_grant_revision=0))


class EpisodeOwner:
    def __init__(self,operator):
        self.o=operator;self.r=operator.runner;self.epoch=uuid4().bytes;self.revision=1
        initial=operator.missions.current()
        self.startup_key=None if initial is None else (initial['id'],initial['revision'])
        self.mission_key=None;self.registry=None;self.last_reply=None;self.terminal=None
        self.peers={};self.activated=set();self.failed=False;self.request_hwm={};self.last_key=self.last_sha=None
        self.r.machine.episode_prepare_allowed=self.prepare_allowed

    def declare(self,peer,features):
        # Binding/transport origin has already been checked by DriveLinkProvider.
        self.peers={key:value for key,value in self.peers.items()
            if self.r.bindings.current(key[0],self.r._clock()) is not None
            and self.r.bindings.current(key[0],self.r._clock())['execution_id']==key[1]}
        self.activated.intersection_update(self.peers)
        if len(self.peers)<4 or peer in self.peers:self.peers[peer]=features or 0
        machine=self.r.machine
        if self.supported(peer):
            self.activated.add(peer)
            if machine.active_vm==peer[0] and machine.active_app=='autonomous':
                machine.episode_output_evidence_required=True
        elif peer in self.activated or getattr(machine,'episode_output_evidence_required',False):
            row=self.r.bindings.current(peer[0],self.r._clock())
            if row is not None and app_instance(row,'autonomous')==peer[2]:
                machine.states.invalidate(peer[0],'autonomous')
                if self.registry is not None:
                    self.registry['applied'].pop(peer,None);self.registry['admissions'].pop(peer,None)
        self.request_hwm={key:value for key,value in self.request_hwm.items() if key in self.peers}
        if self.registry is not None:
            for name in ('applied','admissions'):
                self.registry[name]={key:value for key,value in self.registry[name].items() if key in self.peers}

    def required(self,peer):
        # Once selected, binding loss/replacement, A/B and capability omission
        # cannot turn the same service's new profile into a legacy fallback.
        return bool(getattr(self.r.machine,'episode_output_evidence_required',False)
            or peer in self.activated or self.peers.get(peer,0)&EPISODE)

    def supported(self,peer):
        features=self.peers.get(peer,0)
        return bool(getattr(self.r,'episode_support',False) and features&EPISODE
            and not features&~31)

    def prepare_allowed(self,vm):
        if not getattr(self.r.machine,'episode_output_evidence_required',False):return True
        row=self.r.bindings.current(vm,self.r._clock())
        if row is None or self.registry is None or self.failed or self.registry['state'] is None:return False
        peer=(vm,row['execution_id'],app_instance(row,'autonomous'))
        applied=self.registry['applied'].get(peer)
        return bool(self.supported(peer) and applied is not None
            and applied['core']==self.registry['snapshot'].acceptance.accepted_core_sha256)

    def sync_mission(self):
        current=self.o.missions.current()
        if not self.o.ready or not self.r._durable_available():return
        if current is None or not current['admitted'] or current['phase']=='HOLD':
            if self.registry is not None:self.failed=True
            return
        key=(current['id'],current['revision'])
        if key==self.mission_key:return  # A/B/grant/clock does not initialize NONE.
        if key==self.startup_key:return  # Restart cannot prove the old mission had no episode.
        self.epoch=uuid4().bytes;self.revision=1;self.mission_key=key;self.failed=False
        self.registry=dict(snapshot=none_snapshot(self.epoch,self.revision),source=None,request=None,
            request_key=None,request_sha=None,reply=None,state=None,applied={},admissions={},progress_anchor=(0.,0.))
        self.last_reply=self.terminal=None
        self.last_key=self.last_sha=None;self.request_hwm.clear()

    def _meter(self,sequence=None,*,historical=False):
        view=self.r.device_view();value=view.get('absolute_meter');sample=view.get('vehicle_sample')
        if value is None or sample is None:return None
        current,history=value
        anchor=current if sequence is None else next((item for item in history if item.vehicle_sequence==sequence),None)
        if anchor is None or not anchor.valid:return None
        if (anchor.host_boot!=self.r.authority.host or any(getattr(anchor,key)!=sample.get(key)
                for key in ('pi_session','device_session','odom_epoch','calibration'))
                or not 0<=self.o.now-current.pi_sample_us+current.clock_error_us<=200000):return None
        if historical:
            if (not current.valid or anchor.meter_epoch!=current.meter_epoch or anchor.gap_revision!=current.gap_revision
                    or anchor.metres_per_tick!=current.metres_per_tick or anchor.total_ticks>current.total_ticks):return None
        elif not 0<=self.o.now-anchor.pi_sample_us+anchor.clock_error_us<=200000:return None
        return anchor

    def _reply(self,request,digest,result,reason,snapshot=None):
        reply=schema.EpisodeReceipt(major=1,request_nonce=request.request_nonce,request_sha256=digest,
            result=result,reason=reason,registry_epoch=self.epoch,registry_revision=self.revision,
            request_sequence=request.request_sequence)
        if snapshot is not None:reply.snapshot.CopyFrom(snapshot)
        self.last_reply=encode(reply)
        self.last_key=(bytes(request.mission_id),request.mission_revision,bytes(request.request_nonce));self.last_sha=digest
        return reply

    def _context(self,request,peer,received):
        m=self.r.machine;mission=self.o.missions.current();now=self.o.now
        if (self.failed or self.registry is None or not self.supported(peer) or self.o.hold
                or m.mode.value!='AUTO' or m.active_vm!=peer[0] or m.active_app!='autonomous'
                or (request.vm,request.execution,request.app_instance)!=peer
                or request.control_generation!=m.generation or mission is None or not mission['admitted']
                or (str(UUID(bytes=request.mission_id)),request.mission_revision)!=self.mission_key
                or mission['generation']!=m.generation or mission['owner']!=peer[0]
                or mission['execution']!=peer[1] or mission['app']!=peer[2]
                or request.expected_grant_revision!=mission['grant_revision']
                or request.cancel_fence_revision!=mission['cancel_fence']
                or request.scene_admission_id!=(NIL if mission['scene_id'] is None else UUID(mission['scene_id']).bytes)
                or request.scene_revision!=mission['scene_revision']
                or not now<request.valid_until_pi_us<=received+250000):return None
        lease=self.o.clocks.lease(peer,request.clock_probe_sequence,now,m.generation)
        if lease is None or lease.digest!=request.clock_reply_sha256:return None
        interval=self.o.clocks.interval(peer,request.clock_probe_sequence,request.completed_guest_us,now,m.generation)
        if interval is None or request.valid_until_pi_us>min(interval[0]+250000,interval[2]):return None
        trust=self.r.drive_link.trust;source=request.source
        if trust is None or now>=trust.expires_pi_us:return None
        for key in ('vehicle','mapper_boot','mapper_instance','map_epoch','frame_epoch','clock_epoch',
                    'configuration','calibration'):
            if getattr(source,key)!=getattr(trust,key):return None
        if source.primitive!=bytes.fromhex(trust.primitive):return None
        meter=self._meter(request.vehicle_sequence)
        if meter is None or source.odom_epoch!=meter.odom_epoch:return None
        runtime=self.r.scene_runtime
        if mission['scene_id'] is not None and (runtime is None or not runtime.mission_allowed(self.o.missions.grant(),now)):
            return None
        return interval,meter

    def receive(self,request,raw,peer,received):
        """원 bytes 조회가 현재 권한/만료보다 먼저다. reply 재취득은 새 실행이 아니다."""
        from xenova_drive_world import episode_core_sha256,episode_source_sha256,episode_request_sha256
        digest=episode_request_sha256(raw)
        original=decode(raw,schema.EpisodeRequest);original.DiscardUnknownFields()
        known=copied(request);known.DiscardUnknownFields()
        if encode(original)!=encode(known):raise ValueError('episode original body mismatch')
        key=(bytes(request.mission_id),request.mission_revision,bytes(request.request_nonce))
        last=dict(request_key=self.last_key,request_sha=self.last_sha,reply=self.last_reply)
        for saved in (self.registry,self.terminal,last):
            if saved is not None and saved.get('request_key')==key:
                if saved['request_sha']!=digest:return self._reply(request,digest,2,8)
                self.last_reply=saved['reply'];self.last_key=key;self.last_sha=digest
                return decode(saved['reply'],schema.EpisodeReceipt)
        if request.request_sequence<=self.request_hwm.get(peer,0):return self._reply(request,digest,3,2)
        self.request_hwm[peer]=request.request_sequence
        context=self._context(request,peer,received)
        if context is None:return self._reply(request,digest,4 if not self.supported(peer) else 3,1)
        old=self.registry['snapshot'];candidate=copied(request.candidate);now=self.o.now
        if (request.expected_registry_epoch!=self.epoch or request.expected_registry_revision!=self.revision
                or request.expected_episode_id!=old.core.episode_id or request.expected_episode_revision!=old.core.revision):
            return self._reply(request,digest,3,2)
        interval,meter=context
        if (candidate.source_geometry_sha256!=episode_source_sha256(request.source)
                or candidate.phase_revision!=request.phase_revision or not request.plan_revision
                or not request.calculation_sequence or request.phase_state_message_id==NIL):return self._reply(request,digest,2,3)
        if request.action==1:
            if (old.core.state!=1 and old.core.phase!=5) or self.registry['state'] is None:
                return self._reply(request,digest,2,4)
            begin=self.o.clocks.interval(peer,request.clock_probe_sequence,request.began_guest_us,now,self.r.machine.generation)
            phase=self.o.clocks.interval(peer,request.clock_probe_sequence,request.phase_started_guest_us,now,self.r.machine.generation)
            if begin is None or phase is None or not begin[0]<=phase[0]<=interval[1]:return self._reply(request,digest,3,5)
            if (candidate.phase not in (1,2) or candidate.episode_id==NIL or candidate.episode_id==old.core.episode_id
                    or candidate.revision!=1):return self._reply(request,digest,2,4)
            birth=self._meter(candidate.origin_vehicle_sequence,historical=True)
            if (birth is None or birth.pi_sample_us-birth.clock_error_us>begin[1]
                    or begin[0]>birth.pi_sample_us-birth.clock_error_us+200000):return self._reply(request,digest,3,5)
            candidate.state=2;candidate.started_pi_us=begin[0];candidate.deadline_pi_us=begin[0]+40000000
            candidate.phase_started_pi_us=phase[0];candidate.phase_deadline_pi_us=min(candidate.deadline_pi_us,phase[0]+10000000)
            candidate.origin_vehicle_sequence=birth.vehicle_sequence
            candidate.left_baseline_ticks=birth.left_ticks;candidate.right_baseline_ticks=birth.right_ticks
            candidate.meter_epoch=UUID(birth.meter_epoch).bytes;candidate.meter_baseline_total_ticks=birth.total_ticks
            candidate.max_travel_m=min(6.,candidate.max_travel_m)
        else:
            if old.core.state!=2 or old.core.phase==5 or self.registry['state'] is None:
                return self._reply(request,digest,2,4)
            immutable=('episode_id','home_lane_id','target_lane_id','policy','blocker_position_bound_m',
                'blocker_world_sequence','blocker_observation_sequence','birth_snapshot_id','basis_revision',
                'basis_prefix_count','basis_prefix_sha256','source_geometry_sha256','origin_vehicle_sequence',
                'started_pi_us','deadline_pi_us','left_baseline_ticks','right_baseline_ticks','meter_epoch',
                'meter_baseline_total_ticks','max_travel_m')
            if any(getattr(candidate,key)!=getattr(old.core,key) for key in immutable) or candidate.blocker!=old.core.blocker:
                return self._reply(request,digest,2,3)
            finish_keep=request.action==3 and old.core.policy==2 and old.core.phase==3 and candidate.phase==5
            if candidate.revision!=old.core.revision+1 or candidate.phase<old.core.phase or candidate.phase>old.core.phase+1 and not finish_keep:
                return self._reply(request,digest,2,4)
            if candidate.phase==old.core.phase:
                if candidate.phase_revision!=old.core.phase_revision or candidate.phase_goal!=old.core.phase_goal:
                    return self._reply(request,digest,2,4)
                candidate.phase_started_pi_us=old.core.phase_started_pi_us
                candidate.phase_deadline_pi_us=old.core.phase_deadline_pi_us
            else:
                if candidate.phase_revision<=old.core.phase_revision:return self._reply(request,digest,2,4)
                phase=self.o.clocks.interval(peer,request.clock_probe_sequence,request.phase_started_guest_us,now,self.r.machine.generation)
                if phase is None:return self._reply(request,digest,3,5)
                candidate.phase_started_pi_us=phase[0]
                candidate.phase_deadline_pi_us=min(old.core.deadline_pi_us,phase[0]+10000000)
            if request.action==3 and (candidate.phase!=5 or not self.registry['state']):return self._reply(request,digest,2,4)
        meter=self._meter()
        if (meter is None or not candidate.max_travel_m or now>=min(candidate.deadline_pi_us,candidate.phase_deadline_pi_us)
                or UUID(meter.meter_epoch).bytes!=candidate.meter_epoch
                or meter.total_ticks<candidate.meter_baseline_total_ticks
                or upper_distance(meter.total_ticks-candidate.meter_baseline_total_ticks,meter.metres_per_tick)>=candidate.max_travel_m):
            return self._reply(request,digest,5,6)
        self.revision+=1
        acceptance=schema.EpisodeAcceptanceRef(registry_epoch=self.epoch,registry_revision=self.revision,
            request_nonce=request.request_nonce,request_sha256=digest,accepted_core_sha256=episode_core_sha256(candidate),
            request_sequence=request.request_sequence,origin_vm=UUID(peer[0]).bytes,origin_execution=UUID(peer[1]).bytes,
            origin_app_instance=UUID(peer[2]).bytes,origin_generation=request.control_generation,
            origin_grant_revision=request.expected_grant_revision)
        progress=copied(request.progress);progress.used_travel_ticks=meter.total_ticks-candidate.meter_baseline_total_ticks
        progress.vehicle_sequence=meter.vehicle_sequence
        # 같은 phase의 재계획 ACK는 실제 진전이 아니다. 새 phase의 기하 도달은
        # 새 phase clock으로 시작하되 전체 기한/거리와 이전 phase는 보존한다.
        same_phase=request.action!=1 and candidate.phase==old.core.phase
        progress.last_progress_pi_us=old.progress.last_progress_pi_us if same_phase else 0
        snapshot=schema.EpisodeSnapshot(core=candidate,progress=progress,acceptance=acceptance)
        reply=self._reply(request,digest,1,0,snapshot)
        self.registry=dict(snapshot=snapshot,source=copied(request.source),request=copied(request),
            request_key=key,request_sha=digest,reply=encode(reply),state=None,applied={},admissions={},
            progress_anchor=(progress.station_lower_m,progress.remaining_upper_m))
        if request.action==3:self.terminal=deepcopy(self.registry)
        return reply

    def prepare_state(self,state,peer,raw):
        from xenova_drive_world import episode_core_sha256,plan_sha256
        if not state.HasField('episode'):return True if not self.required(peer) else None
        if not self.supported(peer) or self.registry is None or self.failed:return None
        expected=self.registry['snapshot'];snapshot=state.episode
        if (episode_core_sha256(snapshot.core)!=expected.acceptance.accepted_core_sha256
                or snapshot.acceptance!=expected.acceptance):return None
        if expected.core.phase==0 and (state.direction or state.maneuver_phase
                or state.HasField('task_reference') and state.task_reference.changing):return None
        request=self.registry['request'];admitted=self.registry['state']
        progress_anchor=self.registry['progress_anchor']
        if request is not None:
            origin=(request.vm,request.execution,request.app_instance)
            if admitted is None and (peer!=origin or UUID(state.message_id).bytes!=request.phase_state_message_id
                    or state.calculation_sequence<=request.calculation_sequence
                    or state.path_revision!=request.plan_revision or state.maneuver_revision!=request.phase_revision
                    or plan_sha256(state)!=request.plan_sha256):return None
        if request is not None and admitted is not None and snapshot.core.phase!=5:
            if (state.path_revision!=request.plan_revision or state.maneuver_revision!=request.phase_revision
                    or plan_sha256(state)!=request.plan_sha256):return None
        if snapshot.core.state==2 and snapshot.core.phase!=5:
            meter=self._meter(state.vehicle_sequence)
            if (meter is None or UUID(meter.meter_epoch).bytes!=snapshot.core.meter_epoch
                    or meter.total_ticks<snapshot.core.meter_baseline_total_ticks
                    or snapshot.progress.vehicle_sequence!=state.vehicle_sequence
                    or snapshot.progress.used_travel_ticks!=meter.total_ticks-snapshot.core.meter_baseline_total_ticks):return None
            previous=expected.progress
            if (snapshot.progress.revision<previous.revision or snapshot.progress.used_travel_ticks<previous.used_travel_ticks
                    or snapshot.progress.last_progress_pi_us<previous.last_progress_pi_us
                    or snapshot.progress.last_progress_pi_us>self.o.now):return None
            # 새 progress는 원 표본에 결박된다. 반복 pose/State는 진전 시각을 갱신하지 않는다.
            if snapshot.progress.last_progress_pi_us>previous.last_progress_pi_us:
                policy=getattr(self.r,'planner_parameters',{}) or {};minimum=policy.get('avoid_progress_m')
                delta=snapshot.progress.station_lower_m-progress_anchor[0] if snapshot.core.progress_mode==1 else progress_anchor[1]-snapshot.progress.remaining_upper_m
                rounded=lambda value:struct.unpack('<f',struct.pack('<f',value))[0]
                if minimum is None or rounded(delta)<rounded(minimum) or snapshot.progress.vehicle_sequence<=previous.vehicle_sequence:return None
                progress_anchor=(snapshot.progress.station_lower_m,snapshot.progress.remaining_upper_m)
        return dict(registry_revision=self.revision,peer=peer,progress=copied(snapshot.progress),
            progress_anchor=progress_anchor,
            state=dict(message_id=state.message_id,sha256=sha256(raw).digest(),revision=state.state_revision,
                plan=state.path_revision,phase=state.maneuver_revision,calculation=state.calculation_sequence,vehicle=state.vehicle_sequence))

    def commit_state(self,prepared):
        """모든 StateCopies/parking 검사를 통과한 같은 actor 호출에서만 등록을 적용한다."""
        if prepared is True:return
        if self.registry is None or self.revision!=prepared['registry_revision']:
            raise RuntimeError('episode state admission lost actor revision')
        self.registry['snapshot'].progress.CopyFrom(prepared['progress'])
        self.registry['progress_anchor']=prepared['progress_anchor']
        self.registry['state']=prepared['state']
        self.registry['admissions'].setdefault(prepared['peer'],prepared['state']['calculation'])

    def applied(self,peer,identity):
        if self.registry is None or self.registry['state'] is None:return
        saved=self.r.drive_link.copies.get(identity)
        if saved is None:return
        state=decode(saved[0],schema.DriveLink).accepted.state
        if not state.HasField('episode'):return
        from xenova_drive_world import episode_core_sha256,plan_sha256
        expected=self.registry['snapshot']
        if (episode_core_sha256(state.episode.core)!=expected.acceptance.accepted_core_sha256
                or state.episode.acceptance!=expected.acceptance):return
        request=self.registry['request']
        if request is not None and expected.core.phase!=5 and plan_sha256(state)!=request.plan_sha256:return
        self.registry['applied'][peer]=dict(message_id=identity,core=expected.acceptance.accepted_core_sha256)

    def command_allowed(self,peer,command):
        if not self.required(peer):return True
        if not self.supported(peer) or self.registry is None or self.failed:return False
        state=self.registry['state'];request=self.registry['request']
        if state is None:return False
        floor=self.registry['admissions'].get(peer)
        if floor is None:
            applied=self.registry['applied'].get(peer)
            if applied is None or applied['core']!=self.registry['snapshot'].acceptance.accepted_core_sha256:return False
            floor=0
        if int(command['calculation_sequence'])<=floor:return False
        if request is not None and (int(command['plan_revision'])!=state['plan']
                or int(command['phase_revision'])!=state['phase']):return False
        return self.exhausted() is None

    def output_deadline(self):
        """현재 phase의 원 hard E. now 검산과 별개로 최종 장치 lease도 이 안에 둔다."""
        if self.registry is None:return None
        core=self.registry['snapshot'].core
        return min(core.deadline_pi_us,core.phase_deadline_pi_us) if core.state==2 else None

    def exhausted(self):
        if self.registry is None or self.failed:return 'EPISODE_UNKNOWN'
        snapshot=self.registry['snapshot'];core=snapshot.core
        if core.state!=2 or core.phase==5:return None
        now=self.o.now
        if now>=core.deadline_pi_us:return 'EPISODE_DEADLINE'
        if now>=core.phase_deadline_pi_us:return 'EPISODE_PHASE_DEADLINE'
        meter=self._meter()
        if meter is None or UUID(meter.meter_epoch).bytes!=core.meter_epoch:return 'EPISODE_METER_GAP'
        if meter.total_ticks<core.meter_baseline_total_ticks or upper_distance(meter.total_ticks-core.meter_baseline_total_ticks,meter.metres_per_tick)>=core.max_travel_m:
            return 'EPISODE_TRAVEL'
        progress_at=snapshot.progress.last_progress_pi_us or core.phase_started_pi_us
        if now-progress_at>=2000000:
            request=self.registry['request'];facts=self.r.control_facts()
            waiting=(core.stop_wait and request is not None and request.HasField('stop_command_sequence')
                and facts is not None and facts.HasField('stop') and facts.stop.state==3
                and facts.stop.command_sequence==request.stop_command_sequence and now<facts.stop.valid_until_pi_us)
            if not waiting:return 'EPISODE_PROGRESS'
        return None

    def tick(self):
        self.sync_mission()
        if self.registry is None or self.failed:return
        reason=self.exhausted()
        if reason is not None:
            self.failed=True;self.terminal=deepcopy(self.registry);self.o._stop(reason)

    def status(self,peer):
        if not self.supported(peer):return None
        registry=self.registry;mission=self.o.missions.current()
        known=(registry is not None and not self.failed and mission is not None and mission['admitted']
            and (mission['id'],mission['revision'])==self.mission_key)
        snapshot=None if not known else registry['snapshot'];state=None if not known else registry['state']
        result=schema.EpisodeStatus(major=1,state=0 if snapshot is None else snapshot.core.state,
            registry_epoch=self.epoch,registry_revision=self.revision,
            accepted_core_sha256=ZERO if snapshot is None else snapshot.acceptance.accepted_core_sha256,
            episode_id=NIL if snapshot is None else snapshot.core.episode_id,
            episode_revision=0 if snapshot is None else snapshot.core.revision,
            commit_state=0 if not known else 1 if state is None else 2,
            state_message_id=NIL if state is None else UUID(state['message_id']).bytes,
            state_sha256=ZERO if state is None else state['sha256'],state_revision=0 if state is None else state['revision'],
            plan_revision=0 if state is None else state['plan'],phase_revision=0 if state is None else state['phase'],
            calculation_hwm=0 if state is None else registry['admissions'].get(peer,0),
            vehicle_hwm=0 if state is None else state['vehicle'],
            valid_until_pi_us=self.o.now+100000 if snapshot is None or snapshot.core.state==1 or snapshot.core.phase==5 else min(
                snapshot.core.deadline_pi_us,snapshot.core.phase_deadline_pi_us))
        if self.last_reply is not None:result.receipt.CopyFrom(decode(self.last_reply,schema.EpisodeReceipt))
        return result
