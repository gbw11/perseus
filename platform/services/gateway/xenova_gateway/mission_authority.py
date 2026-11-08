"""Pi의 실제 임무·예약 owner. geometry를 계획하거나 앱 progress를 예산으로 쓰지 않는다.

이 모듈은 원 snapshot 변경안을 actor journal에 제출한다. 원 nonce 결과 조회는 현재
owner 검사보다 먼저이며 성공 ACK 뒤에만 MissionLinkPort에 원 grant를 공개한다.
"""
from copy import deepcopy
from hashlib import sha256
from uuid import UUID,uuid4
import math
import base64

from xenova_drive_world import schema,encode,decode
from xenova_mqtt.drive_binding import app_instance
from .operator_contract import OperatorError
from .absolute_meter import upper_distance


class MissionAuthority:
    def __init__(self,runner):
        self.runner=runner;self.previous=None;self.distance=0.;self.distance_valid=False
        self.stop_reason=None;self.last_published=None;self.execution=None
        self.execution_hwm={};self.terminal_pending=None

    def current(self):return self.runner.authority.value.get('current_mission')

    def _context(self):
        r=self.runner;m=r.machine;provider=r.drive_link
        row=m.bindings.current(m.active_vm,r._clock())
        trust=None if provider is None else provider.trust
        if row is None or trust is None:raise OperatorError('NOT_READY')
        return row,trust

    def start(self,call,kind,*,admitted=False):
        row,trust=self._context();now=int(self.runner._clock()*1e6)
        issued=int(call['request_id'].split(':')[2])*1000
        if not issued<=now<call['deadline_pi_ms']*1000:raise OperatorError('DEADLINE_EXCEEDED')
        runtime=self.runner.scene_runtime
        scene=None if runtime is None else runtime.motion_grant(now)
        if kind==2 and scene is None:raise OperatorError('NOT_READY')
        deadline=issued+120_000_000 if kind==2 else 0
        record=dict(id=str(uuid4()),revision=1,grant_revision=1,kind=kind,
            strategy=0 if kind==1 else 1 if call['args']['strategy']=='REVERSE' else 2,
            nonce=call['request_id'].split(':')[-1],origin=call['request_id'],
            issued=issued,deadline=deadline,
            search_deadline=min(deadline,issued+90_000_000) if kind==2 else 0,
            execution_deadline=0,corrections=0,execution_distance=0.,search_distance=0.,cancel_fence=0,
            phase='FOLLOW' if kind==1 else 'SEARCH',result='NONE',admitted=admitted,
            scene_id=None if scene is None else str(UUID(bytes=scene.scene.admission_id)),
            scene_revision=0 if scene is None else scene.scene.admission_revision,
            configuration=trust.configuration,calibration=trust.calibration,primitive=trust.primitive,
            vehicle=trust.vehicle,reservations=[],goal=None,owner=self.runner.machine.active_vm,
            execution=row['execution_id'],app=app_instance(row,'autonomous'),generation=self.runner.machine.generation)
        self.previous=None;self.distance=0.;self.distance_valid=False;self.stop_reason=None
        if kind==2 and getattr(self.runner,'episode_support',False):
            record['search_meter']=self._meter_anchor();record['execution_meter']=None
        return record

    def _meter_anchor(self):
        operator=getattr(self.runner,'operator',None)
        meter=None if operator is None else operator.episodes._meter()
        if meter is None:return None
        return dict(epoch=meter.meter_epoch,total_ticks=meter.total_ticks,metres_per_tick=meter.metres_per_tick,
            gap_revision=meter.gap_revision,vehicle_sequence=meter.vehicle_sequence)

    def _meter_distance(self,current):
        anchor=current.get('execution_meter') if current['execution_deadline'] else current.get('search_meter')
        meter=self._meter_anchor()
        if (anchor is None or meter is None or any(meter[key]!=anchor[key] for key in
                ('epoch','gap_revision','metres_per_tick')) or meter['total_ticks']<anchor['total_ticks']):return None
        return upper_distance(meter['total_ticks']-anchor['total_ticks'],anchor['metres_per_tick'])

    def used_distance(self,current=None):
        current=self.current() if current is None else current
        if current is not None and 'search_meter' in current:
            return self._meter_distance(current)
        return None if not self.distance_valid else self.distance+(0. if current is None else
            current['execution_distance'] if current['execution_deadline'] else current['search_distance'])

    def fence(self,result='CANCELLED'):
        current=self.current()
        if current is None:return None
        record=deepcopy(current);record['cancel_fence']+=1;record['grant_revision']+=1
        record.update(phase='HOLD',result=result,admitted=False)
        return record

    def grant(self,record=None):
        record=self.current() if record is None else record
        if record is None:return None
        r=self.runner;m=r.machine
        grant=schema.MissionGrant(major=1,vehicle=record['vehicle'],pi_host_boot=UUID(r.authority.host).bytes,
            gateway_instance=UUID(r.drive_link.instance).bytes,owner_vm=UUID(record['owner']).bytes,
            owner_execution=UUID(record['execution']).bytes,app_instance=UUID(record['app']).bytes,
            control_generation=record['generation'],mission_id=UUID(record['id']).bytes,
            mission_revision=record['revision'],grant_revision=record['grant_revision'],kind=record['kind'],
            strategy=record['strategy'],operator_request_nonce=UUID(record['nonce']).bytes,issued_pi_us=record['issued'],
            mission_deadline_pi_us=record['deadline'],cancel_fence_revision=record['cancel_fence'],
            scene_admission_id=bytes(16) if record['scene_id'] is None else UUID(record['scene_id']).bytes,
            scene_revision=record['scene_revision'],configuration=record['configuration'],calibration=record['calibration'],
            primitive=bytes.fromhex(record['primitive']),execution_deadline_pi_us=record['execution_deadline'],
            correction_attempts_used=record['corrections'],execution_travel_m=record['execution_distance'],
            max_execution_travel_m=5. if record['kind']==2 else 0.,admitted=record['admitted'],
            search_deadline_pi_us=record['search_deadline'],search_travel_m=record['search_distance'],
            max_search_travel_m=10. if record['kind']==2 else 0.)
        if record['goal'] is not None:grant.locked_goal_sha256=bytes.fromhex(record['goal'])
        encode(grant);return grant

    def publish(self):
        grant=self.grant()
        if grant is None:return False
        raw=encode(grant)
        if raw==self.last_published:return True
        if self.runner.drive_link is not None and self.runner.drive_link.publish_mission(grant):self.last_published=raw;return True
        return False

    def rebind(self,record,generation,owner):
        row=self.runner.bindings.current(owner,self.runner._clock())
        if row is None:raise OperatorError('CONTEXT_CHANGED')
        value=deepcopy(record);value.update(owner=owner,execution=row['execution_id'],
            app=app_instance(row,'autonomous'),generation=generation,grant_revision=record['grant_revision']+1)
        return value

    def observe_vehicle(self,sample,metres_per_tick,now):
        current=self.current()
        if current is not None and current['kind']==2 and 'search_meter' in current:
            distance=self._meter_distance(current);self.distance_valid=distance is not None
            self.distance=0. if distance is None else distance
            self.stop_reason=None if distance is not None else 'PARKING_METER_UNAVAILABLE'
            return
        if sample is None or metres_per_tick is None:return
        if (sample.get('quality')!=3 or not 0<=now-sample['pi_sample_us']+sample['clock_error_us']<=200_000
                or not math.isfinite(metres_per_tick) or metres_per_tick<=0):
            self.distance_valid=False;self.stop_reason='VEHICLE_UNAVAILABLE';return
        identity=tuple(sample.get(k) for k in ('host_boot','device_session','odom_epoch','calibration'))
        old=self.previous
        if old is not None and sample['sequence']==old['sequence']:return
        if old is not None:
            if (identity!=tuple(old.get(k) for k in ('host_boot','device_session','odom_epoch','calibration'))
                    or sample['sequence']<=old['sequence'] or sample['source_sample']<=old['source_sample']
                    or sample['pi_sample_us']-old['pi_sample_us']>200_000):
                self.distance_valid=False;self.stop_reason='ODOMETRY_GAP';return
            self.distance+=max(abs(sample['left_ticks']-old['left_ticks']),
                abs(sample['right_ticks']-old['right_ticks']))*metres_per_tick
            self.distance_valid=True
        self.previous=dict(sample)

    def budget(self,envelope,now,stationary):
        """원 result 또는 원 snapshot 변경안. 성공 예약3개만 mission4KiB에 남긴다."""
        body=envelope.request_message();current=self.current()
        def reply(result,reason):return schema.MissionBudgetReply(major=1,request_nonce=body.request_nonce,
            request_sha256=envelope.request_sha256,result=result,reason=reason)
        if current is None:return reply(5,1),None
        for row in current['reservations']:
            if row['nonce']==body.request_nonce.hex():
                if row['sha']!=envelope.request_sha256.hex():return reply(5,8),None
                if 'reply' in row:return decode(base64.b64decode(row['reply'],validate=True),schema.MissionBudgetReply),None
                return reply(2,0),None  # Old records did not retain an exact reply; never mint its old grant.
        m=self.runner.machine
        if (body.mission_id!=UUID(current['id']).bytes or body.mission_revision!=current['revision']
                or current['kind']!=2 or not current['admitted'] or current['phase']=='HOLD'
                or m.active_app!='autonomous' or m.mode.value!='AUTO'
                or envelope.vm!=m.active_vm or body.control_generation!=m.generation
                or body.expected_grant_revision!=current['grant_revision']
                or envelope.execution!=current['execution'] or envelope.app_instance!=current['app']
                or body.scene_admission_id!=(bytes(16) if current['scene_id'] is None else UUID(current['scene_id']).bytes)
                or body.scene_revision!=current['scene_revision']):return reply(3,1),None
        if not now<body.valid_until_pi_us<=envelope.received_pi_us+250_000:return reply(5,2),None
        clock=self.runner.operator.clocks;peer=(envelope.vm,envelope.execution,envelope.app_instance)
        intervals=[clock.interval(peer,key[-1],body.completed_guest_us,now,m.generation)
            for key in tuple(clock.confirmed) if key[:-1]==peer]
        intervals=[value for value in intervals if value is not None]
        if not intervals or not any(body.valid_until_pi_us<=min(lo+250_000,end) for lo,hi,end in intervals):return reply(5,2),None
        if not stationary:return reply(5,3),None
        if not self.distance_valid:return reply(5,1),None
        if now>=current['deadline']:return reply(5,7),None
        if self.execution is None:return reply(5,1),None
        state=decode(self.execution[0],schema.DriveState)
        from xenova_drive_world import plan_sha256,selected_goal_sha256
        if (not self.runner.drive_link._state_mission_matches(state,self.grant())
                or not 0<=now-int(self.execution[1]*1e6)<=250000
                or state.path_revision!=body.plan_revision or plan_sha256(state)!=body.plan_sha256
                or selected_goal_sha256(state.mission)!=body.selected_goal_sha256
                or state.mission.slot_id!=body.slot_id or state.mission.slot_revision!=body.slot_revision
                or state.mission.goal_revision!=body.goal_revision):return reply(5,1),None
        used=self.used_distance(current)
        if used is None:return reply(5,1),None
        value=deepcopy(current)
        if body.action==1:
            if value['execution_deadline']:return reply(5,4),None
            if state.mission.phase!=2:return reply(5,1),None
            if now>=value['search_deadline'] or used>=10:return reply(5,6),None
            value.update(execution_deadline=min(current['deadline'],now+30_000_000),
                goal=body.selected_goal_sha256.hex(),phase='EXECUTE',search_distance=used)
            if 'search_meter' in value:value['execution_meter']=self._meter_anchor()
        elif body.action==2:
            if state.mission.phase!=7:return reply(5,1),None
            if not value['execution_deadline'] or value['goal']!=body.selected_goal_sha256.hex():return reply(5,1),None
            if now>=value['execution_deadline']:return reply(5,7),None
            if value['corrections']>=2:return reply(5,5),None
            if used>=5:return reply(5,6),None
            value['corrections']+=1;value['execution_distance']=used
        else:return reply(5,1),None
        value['grant_revision']+=1
        value['reservations'].append(dict(nonce=body.request_nonce.hex(),sha=envelope.request_sha256.hex()))
        result=reply(1,0);result.grant.CopyFrom(self.grant(value))
        value['reservations'][-1]['reply']=base64.b64encode(encode(result)).decode('ascii')
        from .authority_journal import canonical
        if len(canonical(value))>4096:return reply(5,10),None
        return result,value

    def prepare_execution(self,state,received):
        current=self.current()
        if current is None or not self.runner.drive_link._state_mission_matches(state,self.grant()):return None
        if not current['admitted'] or state.source_generation!=self.runner.machine.generation:return None
        peer=(state.source_vm,state.source_execution,state.source_app_instance)
        hwm=self.execution_hwm.get(peer)
        signature=(state.state_revision,state.calculation_sequence,state.vehicle_sequence)
        if hwm is not None and any(new<old for new,old in zip(signature,hwm)):return None
        if current['goal'] is not None:
            from xenova_drive_world import selected_goal_sha256
            if selected_goal_sha256(state.mission).hex()!=current['goal']:return None
        terminal=None
        if state.mission.phase==0 and state.mission.result==1:
            facts=self.runner.control_facts();now=int(received*1e6)
            if (current['goal'] is None or facts is None or not facts.HasField('stop') or facts.stop.state!=3
                    or not state.HasField('direction_gate') or state.direction_gate.stop_command_sequence!=facts.stop.command_sequence
                    or not now<facts.stop.valid_until_pi_us or facts.stop.control_generation!=state.source_generation
                    or facts.stop.producer_role!=1 or facts.stop.source_vm!=UUID(peer[0]).bytes
                    or facts.stop.source_execution!=UUID(peer[1]).bytes or facts.stop.source_app_instance!=UUID(peer[2]).bytes
                    or facts.stop.mission_id!=UUID(current['id']).bytes or facts.stop.mission_revision!=current['revision']
                    or facts.stop.grant_revision!=current['grant_revision']
                    or facts.stop.plan_revision!=state.path_revision or facts.stop.phase_revision!=state.maneuver_revision
                    or self.exhausted(now) is not None):return None
            terminal=deepcopy(current);terminal.update(phase='HOLD',result='PARKED',admitted=False)
            terminal['cancel_fence']+=1;terminal['grant_revision']+=1
        return dict(execution=(state.SerializeToString(deterministic=True),received),peer=peer,
            signature=signature,terminal=terminal)

    def commit_execution(self,prepared):
        self.execution=prepared['execution'];self.execution_hwm={prepared['peer']:prepared['signature']}
        if prepared['terminal'] is not None:
            self.terminal_pending=prepared['terminal'];self.runner.operator._stop('PARKED')

    def observe_execution(self,state,received):
        prepared=self.prepare_execution(state,received)
        if prepared is None:return False
        self.commit_execution(prepared)
        return True

    def motion_allowed(self,command):
        current=self.current()
        if current is None or current['kind']!=2:return True
        if self.execution is None:return False
        state=decode(self.execution[0],schema.DriveState)
        if (not self.runner.drive_link._state_mission_matches(state,self.grant())
                or int(command['plan_revision'])!=state.path_revision
                or int(command['phase_revision'])!=state.maneuver_revision):return False
        if not command['v_mps']:return True
        used=self.used_distance(current)
        if used is None:return False
        if not current['execution_deadline']:return state.mission.phase==1 and used<10
        return state.mission.phase in (4,6) and used<5

    def output_deadline(self):
        current=self.current()
        if current is None or current['kind']!=2:return None
        return min(current['deadline'],current['execution_deadline'] or current['search_deadline'])

    def exhausted(self,now):
        current=self.current()
        if current is None or not current['admitted'] or current['kind']!=2:return None
        if now>=current['deadline']:return 'MISSION_DEADLINE'
        if current['execution_deadline']:
            if now>=current['execution_deadline']:return 'EXECUTION_DEADLINE'
            used=self.used_distance(current)
            if used is not None and used>=5:return 'EXECUTION_TRAVEL'
        elif now>=current['search_deadline'] or self.used_distance(current) is not None and self.used_distance(current)>=10:return 'SEARCH_BUDGET'
        if self.stop_reason is not None:return self.stop_reason
        return None


def validate_record(value):
    if value is None:return
    fields={'id','revision','grant_revision','kind','strategy','nonce','origin','issued','deadline','search_deadline',
        'execution_deadline','corrections','execution_distance','search_distance','cancel_fence','phase','result','admitted',
        'scene_id','scene_revision','configuration','calibration','primitive','vehicle','reservations','goal','owner',
        'execution','app','generation'}
    optional={'search_meter','execution_meter'}
    if type(value) is not dict or not fields<=set(value)<=fields|optional:raise ValueError('exact current Mission record')
    for key in ('id','nonce','owner','execution','app'):UUID(value[key])
    if value['scene_id'] is not None:UUID(value['scene_id'])
    if (value['kind'] not in (1,2) or type(value['admitted']) is not bool
            or value['phase'] not in ('HOLD','FOLLOW','SEARCH','EXECUTE','FINAL_VERIFY')
            or type(value['reservations']) is not list or len(value['reservations'])>3):raise ValueError('Mission state')
    for key in ('revision','grant_revision','issued','deadline','search_deadline','execution_deadline','corrections',
            'cancel_fence','scene_revision','generation','strategy'):
        if type(value[key]) is not int or not 0<=value[key]<=2**64-1:raise ValueError('Mission counter')
    if not value['revision'] or not value['grant_revision'] or not value['generation']:raise ValueError('Mission current identity')
    for key in ('execution_distance','search_distance'):
        if type(value[key]) not in (int,float) or not math.isfinite(value[key]) or value[key]<0:raise ValueError('Mission absolute travel')
    for row in value['reservations']:
        if not {'nonce','sha'}<=set(row)<={'nonce','sha','reply'} or len(bytes.fromhex(row['nonce']))!=16 or len(bytes.fromhex(row['sha']))!=32:
            raise ValueError('Mission reservation result')
        if 'reply' in row:
            raw=base64.b64decode(row['reply'],validate=True)
            if not 0<len(raw)<=1536:raise ValueError('bounded original reservation reply')
            reply=decode(raw,schema.MissionBudgetReply)
            if reply.request_nonce.hex()!=row['nonce'] or reply.request_sha256.hex()!=row['sha']:
                raise ValueError('original reservation reply mismatch')
    for name in optional:
        anchor=value.get(name)
        if anchor is not None:
            if set(anchor)!={'epoch','total_ticks','metres_per_tick','gap_revision','vehicle_sequence'}:
                raise ValueError('exact mission meter anchor')
            UUID(anchor['epoch'])
            if (any(type(anchor[key]) is not int or not 0<=anchor[key]<2**63 for key in
                    ('total_ticks','gap_revision','vehicle_sequence')) or not math.isfinite(anchor['metres_per_tick'])
                    or anchor['metres_per_tick']<=0):raise ValueError('mission meter anchor')
    if value['goal'] is not None and len(bytes.fromhex(value['goal']))!=32:raise ValueError('Mission locked goal hash')
    if len(bytes.fromhex(value['primitive']))!=32:raise ValueError('Mission primitive hash')
