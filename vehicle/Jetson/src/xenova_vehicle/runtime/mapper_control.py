"""M-main의 실제 계산 사건을 lifecycle 진도와 연결한다. 계산·SDK·arena는 소유하지 않는다.

NN 해석 완료와 geometry 완료를 분리하고, 최초 ACK의15초와 실제 NN 시작500ms를
유지한다. source 시각은 원 NN passthrough에서만 가져오며 queue 수락은 준비가 아니다.
"""

import json

from .lifecycle_policy import SourceStamp, validate_state
from .resident_transport import j_us


_ACTIVE_STATES=frozenset(('WARMUP','WAIT_INPUT','WAIT_CLOCK','WAIT_ODOM','WAIT_STORAGE','RUNNING','DEGRADED'))
_STORAGE_WAITS=frozenset(('WAIT_MATERIAL','WAIT_MAINTENANCE','WAIT_ORIGINAL_D_JOB',
    'WAIT_PHYSICAL_RELEASE','WAIT_SOURCE_COMPLETE','WAIT_MAILBOX','PROVENANCE_OFFERED',
    'HEAD_LOAD_OFFERED','ATLAS_TILE_LOAD_OFFERED','WAIT_OTHER_D_JOB','NEEDS_CURRENT_HEAD','OFFERED'))


def _result_phase(latest):
    """원 accepted 결과의 최종 입력 사유를 분류한다. 처리 상태는 이동·World 승인이 아니다."""
    reasons=latest.get('metric_input_reasons')
    if reasons is not None:
        if type(reasons) is not list or any(type(value) is not str for value in reasons):
            raise ValueError('mapper metric input reasons must be original strings')
        if 'SOURCE_NOT_BOUND' in reasons:return 'WAIT_INPUT'
        # 과거 Vehicle/선택 ToF 행의 clock 경고는 현재 clock 부재와 다르다.
        if 'PI_J_CLOCK_UNAVAILABLE' in reasons:return 'WAIT_CLOCK'
        sequence=latest.get('source_vehicle_sequence')
        if sequence is not None and (type(sequence) is not int or not 0<=sequence<2**64):
            raise ValueError('mapper current Vehicle sequence must be original uint64')
        if sequence is None or any(value in reasons for value in (
                'ORIGINAL_VEHICLE_BRACKET_UNAVAILABLE','FULL_CAPTURE_POSE_UNAVAILABLE','ODOMETRY_MOTION_CONTRADICTION')):
            return 'WAIT_ODOM'
    if latest.get('quarantined') is True or latest.get('map_restore_issue') is not None:
        return 'DEGRADED'
    if 'mapping_ready' in latest:
        if type(latest['mapping_ready']) is not bool:
            raise ValueError('mapper mapping readiness must be an actual boolean')
        if not latest['mapping_ready']:
            status,reason=latest.get('mapping_status'),latest.get('mapping_reason')
            if status=='PROFILE_NOT_READY':return 'DEGRADED'
            if reason=='WAIT_MAINTENANCE' or status in _STORAGE_WAITS:return 'WAIT_STORAGE'
            return 'DEGRADED'
    if latest.get('localization_configured') is False:return 'DEGRADED'
    # frame-local 진단 처리도 정상 CPU 작업이다. core의 false 권위 플래그는 읽지 않는다.
    return 'RUNNING'


class MapperControl:
    """actor와 compute의 control API는 같은 M-main thread만 호출한다."""

    def __init__(self, actor, compute, parser_deadline, *, clock=j_us, nn_stall_us=5_000_000):
        if type(nn_stall_us) is not int or not 0<nn_stall_us<2**64:
            raise ValueError('original NN stall budget must be positive uint64')
        self.actor,self.compute,self.deadline,self.clock=actor,compute,parser_deadline,clock
        self.nn_stall_us=nn_stall_us
        self.generation=actor.binding.sha256
        self.nn_stream=actor.binding.fields['stream_instances']['nn_input0']
        self.previous=None
        self.streak=0
        self.ready=False
        self.last_completion=None
        self._parser=None
        self._metric_job=None
        self.latest_summary=None
        self.result_count=0
        self._result_phase=None

    def _refresh_phase(self, now):
        if not self.ready or self.actor.state not in _ACTIVE_STATES:return
        current=self._result_phase
        if current is not None and now>=current[1]:
            self._result_phase=current=None
        state='WAIT_INPUT' if current is None else current[0]
        validate_state('M',state)
        self.actor.state=state

    def _metric(self, event, stage, reason):
        self.actor.progress(stage,event.source_key[1],completed=event.completed_us,
            reason=reason,request_id=event.job_id)

    def _complete_nn(self, event, *, fresh):
        if self._parser is None or self._parser[0]!=event.job_id:
            raise RuntimeError('NN completion has no original sent parser start')
        _,stamp,started=self._parser
        if not started<=event.completed_us<=self.clock():
            raise ValueError('NN completion changed original causal time')
        self._parser=None
        if not fresh:
            if not self.ready:
                self.streak=0
            self.actor.progress('PARSER_COMPLETED',stamp.sequence,completed=event.completed_us,
                                reason='STALE_INPUT')
            return
        if not stamp.advances(self.previous):
            raise ValueError('NN completion did not advance its original source')
        self.previous=stamp
        self.streak+=1
        self.last_completion=event.completed_us
        if not self.ready and self.streak>=5:
            if self.deadline.expired(self.clock()) or self.deadline.expired(event.completed_us):
                raise TimeoutError('parser completion missed original readiness deadline')
            self.ready=True
            self._refresh_phase(self.clock())
            self.actor.progress('NN_READY',stamp.sequence,completed=event.completed_us,
                                reason='METRIC_NOT_READY')
        else:
            self.actor.progress('PARSER_COMPLETED',stamp.sequence,completed=event.completed_us,
                                reason='METRIC_NOT_READY')

    def tick(self):
        for event in self.compute.poll():
            if event.source_generation!=self.generation:
                continue
            if event.kind=='JOB_STARTED':
                if self._metric_job is not None:
                    raise RuntimeError('mapper start replaced an active job')
                self._metric_job=event.job_id
                self._metric(event,'MAPPER_STARTED',str(event.expires_us))
            elif event.kind=='PARSER_START_REQUIRED':
                if self.clock()>=event.parser_deadline_us or self.clock()>=event.expires_us:
                    continue
                stamp=SourceStamp(self.nn_stream,*event.nn_source_key)
                if not stamp.advances(self.previous):
                    self.compute.skip_parser(event.job_id)
                    continue
                if self._parser is not None:
                    raise RuntimeError('one compute owner cannot start two NN parsers')
                packet=self.actor.progress('PARSER_STARTED',stamp.sequence,completed=event.started_us,
                                           request_id=event.job_id)
                actual=self.actor.await_sent(packet,cap_us=event.parser_deadline_us)
                self._parser=(event.job_id,stamp,event.started_us)
                if not self.compute.acknowledge_parser_start(event.job_id,actual):
                    # 원 시작 진술은 이미 전송됐다. 취소 terminal로만 닫는다.
                    continue
            elif event.kind=='NN_COMPLETED':
                self._complete_nn(event,fresh=event.accepted)
            elif event.kind=='PROGRESS':
                if self._metric_job==event.job_id:
                    self._metric(event,'MAPPER_PROGRESS',event.reason)
            elif event.kind=='GEOMETRY_COMPLETED':
                if self._metric_job==event.job_id:
                    self._metric(event,'MAPPER_COMPLETED','COMPLETED' if event.accepted else event.reason)
                    self._metric_job=None
            elif event.kind in ('CANCELLED','FAILED'):
                if self._parser is not None and self._parser[0]==event.job_id:
                    self._complete_nn(event,fresh=False)
                if self._metric_job==event.job_id:
                    self._metric(event,'MAPPER_COMPLETED',event.reason)
                    self._metric_job=None
                if event.kind=='FAILED' or event.reason=='PARSER_DEADLINE':
                    raise RuntimeError('mapper computation failed: '+str(event.reason))
            elif event.kind!='NN_SKIPPED':
                raise ValueError('unsupported compute lifecycle event')
        result=self.compute.take_result()
        if result is not None and result.accepted and result.payload is not None:
            if result.source_generation==self.generation and self.clock()<result.expires_us:
                value=json.loads(result.payload)
                if (type(value) is not dict or set(value)!={'processed','latest'}
                        or type(value['latest']) is not dict):
                    raise ValueError('mapper summary has unsupported fields')
                phase=_result_phase(value['latest'])
                # JSON 해석에 쓴 시간도 원 source 수명을 소비한다. 새 기한을 만들지 않는다.
                if self.clock()<result.expires_us:
                    self.latest_summary=value
                    self.result_count+=1
                    self._result_phase=phase,result.expires_us
        if self.compute.terminal_error is not None:
            raise RuntimeError('mapper compute owner failed: '+self.compute.terminal_error)
        now=self.clock()
        self._refresh_phase(now)
        if not self.ready and self.deadline.expired(now):
            raise TimeoutError('parser5 original first-output deadline elapsed')
        if self.ready and self.last_completion is not None and now-self.last_completion>=self.nn_stall_us:
            raise TimeoutError('fresh NN parser association stalled')
