"""Gateway 부가 로그만 유한하게 전달한다. 권위/원 요청/시리얼 출력은 큐에 넣지 않는다."""
import logging
import copy
import sys
from logging.handlers import QueueHandler
import queue
import threading

_CONTROLS={i:'\\x'+format(i,'02x') for i in (*range(32),127)}

class GatewayDiagnostics(QueueHandler):
    """표준 QueueHandler의 사본을 기존 handler로 전달한다. 종료는 유한 대기다.

    Gateway 실행 진입점이 소유한다. 막힌 handler를 강제 종료하거나 worker를 재시작하지 않는다.
    snapshot의 손실/실패/잔존을 성공 drain과 구별한다. 일반 메시지는 4096문자에서
    자른 뒤 제어문자를 escape한다(최대 16384문자). 희귀 ERROR/stack은 자르지 않는다.
    """
    capacity=128
    def __init__(self,logger):
        super().__init__(queue.Queue(maxsize=self.capacity))
        self.logger=logger;self.previous=list(logger.handlers);self.propagate=logger.propagate
        targets=self.previous or list(logging.getLogger().handlers)
        self.targets=[]
        self.submitted=0;self.dropped=0;self.truncated=0;self.failed=0;self.first_failure=None
        self.closing=False;self._sentinel=object();self._admission=threading.Lock()
        self.first_sink_error=None;self.last_sink_error=None
        self.last_critical=None
        for target in targets:
            if type(target) is logging.StreamHandler:
                # 기본 StreamHandler.handleError의 stderr 재출력/stack 증폭을 피한다.
                # 원 handler의 설정/수명은 바꾸지 않고 같은 stream의 진단 사본만 쓴다.
                target=copy.copy(target);target.createLock();target.handleError=self._stream_failure
            self.targets.append(target)
        self.worker=threading.Thread(target=self._run,name='gateway-diagnostics',daemon=True)
        logger.handlers=[self];logger.propagate=False
        try:self.worker.start()
        except BaseException:
            self.closing=True;logger.handlers=self.previous;logger.propagate=self.propagate
            super().close();raise

    def _stream_failure(self,record):
        self._sink_failure(sys.exc_info()[1])

    def _sink_failure(self,error):
        self.failed+=1
        detail={'cause_type':type(error).__name__,'errno':getattr(error,'errno',None)}
        if self.first_failure is None:self.first_failure=detail['cause_type']
        if self.first_sink_error is None:self.first_sink_error=detail
        self.last_sink_error=detail

    def emit(self,record):
        if record.levelno>=logging.ERROR or record.exc_info:
            # 희귀 결함의 원 cause/stack은 요약·포화 폐기 대상으로 바꾸지 않는다.
            # 현 Gateway의 fatal 출력은 제어/소유 자원을 종료한 뒤 호출한다.
            self.last_critical=logging.Formatter().format(record)
            for target in self.targets:
                if record.levelno>=target.level:
                    try:target.handle(record)
                    except Exception as error:self._sink_failure(error)
            return
        # 포화 때 prepare/예외 문자열·stack 형식화부터 다시 만들지 않는다.
        with self._admission:
            if self.closing or self.queue.full():self.dropped+=1;return
        try:
            prepared=self.prepare(record)  # mutable args/예외 객체를 나중에 참조하지 않는 표준 사본
            text=prepared.msg
            if len(text)>4096:self.truncated+=1;text=text[:4096]
            prepared.msg=text.translate(_CONTROLS)
            with self._admission:
                if self.closing:self.dropped+=1;return
                try:self.queue.put_nowait(prepared);self.submitted+=1
                except queue.Full:self.dropped+=1
        except queue.Full:self.dropped+=1
        except Exception as error:
            self.failed+=1
            if self.first_failure is None:self.first_failure=type(error).__name__

    def _run(self):
        while True:
            record=self.queue.get()
            try:
                if record is self._sentinel:return
                for target in self.targets:
                    if record.levelno<target.level:continue
                    try:target.handle(record)
                    except Exception as error:self._sink_failure(error)
            finally:self.queue.task_done()

    def snapshot(self):
        return {'submitted':self.submitted,'dropped':self.dropped,'truncated':self.truncated,
                'failed':self.failed,'first_failure':self.first_failure,'pending':self.queue.qsize(),
                'first_sink_error':self.first_sink_error,'last_sink_error':self.last_sink_error,
                'worker_alive':self.worker.is_alive(),'closing':self.closing}

    def close(self):
        if not getattr(self,'closing',True):
            with self._admission:
                if self.closing:return
                self.closing=True;self.logger.handlers=self.previous;self.logger.propagate=self.propagate
                try:self.queue.put_nowait(self._sentinel)
                except queue.Full:
                    # 종료 표식의 자리는 부가 기록 하나로 한정한다. 필수 저장은 이 큐에 없다.
                    try:self.queue.get_nowait();self.queue.task_done();self.dropped+=1
                    except queue.Empty:pass
                    self.queue.put_nowait(self._sentinel)
            self.worker.join(timeout=.25)
        super().close()
