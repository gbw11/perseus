"""M-main의 실제 계산 사건을 lifecycle 진도와 연결한다. 계산·SDK·arena는 소유하지 않는다.

NN 해석 완료와 geometry 완료를 분리하고, 최초 ACK의15초와 실제 NN 시작500ms를
유지한다. source 시각은 원 NN passthrough에서만 가져오며 queue 수락은 준비가 아니다.
"""

import json

from .lifecycle_policy import SourceStamp
from .resident_transport import j_us


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
            self.actor.state='WAIT_CLOCK'
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
            if result.source_generation!=self.generation or self.clock()>=result.expires_us:
                return
            value=json.loads(result.payload)
            if type(value) is not dict or set(value)!={'processed','latest'}:
                raise ValueError('mapper summary has unsupported fields')
            self.latest_summary=value
            self.result_count+=1
        if self.compute.terminal_error is not None:
            raise RuntimeError('mapper compute owner failed: '+self.compute.terminal_error)
        now=self.clock()
        if not self.ready and self.deadline.expired(now):
            raise TimeoutError('parser5 original first-output deadline elapsed')
        if self.ready and self.last_completion is not None and now-self.last_completion>=self.nn_stall_us:
            raise TimeoutError('fresh NN parser association stalled')
