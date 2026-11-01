"""실제 Gateway 출력 순서와 logging handler의 비용·포화·회복을 분리한다."""
import io
import logging
import threading
import time
from unittest.mock import patch
import pytest
from xenova_gateway.frame import DriveFrame,StopFrame
from xenova_gateway.machine import ForwardDrive,Measure,PublishEvent,ForwardStop
from xenova_gateway.sink import SerialSink,MockSink
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from test_verified import World,Client,A,B,message
from test_runner import make


class SlowHandler(logging.Handler):
    def __init__(self):
        super().__init__();self.entered=threading.Event();self.resume=threading.Event();self.rows=[]
    def emit(self,record):
        self.entered.set()
        if not self.resume.wait(10):raise RuntimeError('owned diagnostic handler not released')
        self.rows.append(record.getMessage())


@pytest.mark.parametrize('failure,stop_fails',[(OSError(5,'owned serial EIO'),False),
    (OSError(5,'owned serial EIO'),True),(TypeError('owned serial internal defect'),False),(None,False)])
def test_serial_failure_attempts_actual_runner_stop_before_log_sink(tmp_path,failure,stop_fails):
    w=World(tmp_path/'authority');normal=MockSink();runner=VerifiedGatewayRunner(Client(),normal,
        authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m;w.arm()
    runner._enqueue(message(w,A,'autonomous','drive.command',{'generation':w.m.generation,'v_mps':.1,'steer_rad':0}))
    runner.run_once(0);source=next(row['source'] for row in runner.submissions if row.get('source'))
    attempted,stopped=threading.Event(),threading.Event();writes=[]
    class Port:
        def write(self,data):
            writes.append(data)
            if len(writes)==1:
                attempted.set()
                if failure is None:return len(data)-1
                raise failure
            stopped.set()
            if stop_fails:raise OSError(28,'owned STOP storage error')
            return len(data)
    serial=SerialSink.__new__(SerialSink);serial._serial=Port();serial.failures=0;serial.first_failure=None
    runner._sink=serial;logger=logging.getLogger('xenova_gateway');handler=SlowHandler();logger.addHandler(handler)
    worker=threading.Thread(target=lambda:runner._submit(ForwardDrive('autonomous',w.m.generation,.1,0,source=source)))
    worker.start()
    try:
        assert attempted.wait(3)
        assert stopped.wait(.4),'STOP waited for auxiliary handler'
        assert not w.m.output_permitted
    finally:handler.resume.set();worker.join(10);logger.removeHandler(handler)
    assert not worker.is_alive();assert serial.failures==(2 if stop_fails else 1)
    assert serial.first_failure=={'cause_type':type(failure).__name__ if failure is not None else 'SinkError','errno':getattr(failure,'errno',None)}
    assert len(writes)==2 and runner.submissions[-1]['serial_result']==('failed' if stop_fails else 'accepted')
    failed=[row for row in runner.submissions if row['serial_result']=='failed']
    assert failed[0]['failure_cause']['message']==(str(failure) if failure is not None else 'partial serial write')
    if stop_fails:assert [row['failure_cause']['errno'] for row in failed]==[5,28]
    if failure is None or isinstance(failure,OSError):assert all('failure_traceback' not in row for row in failed)
    else:assert 'TypeError: owned serial internal defect' in failed[0]['failure_traceback']


def test_disabled_measure_does_not_iterate_or_format_fields():
    runner,*_=make()
    class Expensive(dict):
        def items(self):raise AssertionError('disabled log formatted its arguments')
    with patch('xenova_gateway.runner.log.isEnabledFor',return_value=False):
        runner._emit([Measure('transition',Expensive())])


def test_bounded_gateway_handler_saturation_failure_and_recovery_preserve_snapshot():
    from xenova_gateway.diagnostics import GatewayDiagnostics
    logger=logging.getLogger('xenova_gateway');handler=SlowHandler();old=list(logger.handlers);prop=logger.propagate;level=logger.level
    logger.handlers=[handler];logger.setLevel(logging.INFO)
    channel=GatewayDiagnostics(logger)
    try:
        value=['first'];logger.info('snapshot=%s',value)
        assert handler.entered.wait(3);value[0]='changed'
        for _ in range(1000):logger.warning('reason=KNOWN_CODE count=%d',1)
        state=channel.snapshot();assert state['pending']<=128 and state['dropped']>0
        before=time.monotonic();channel.close();assert time.monotonic()-before<1
        assert channel.snapshot()['worker_alive']  # 미확정 종료를 완료로 가장하지 않는다.
    finally:
        handler.resume.set();channel.worker.join(5);channel.close()
        logger.handlers=old;logger.propagate=prop;logger.setLevel(level)
    assert not channel.worker.is_alive();assert 'snapshot=[\'first\']' in handler.rows[0]
    assert all('changed' not in row for row in handler.rows)


def test_gateway_handler_failure_is_not_recursive_and_rare_stack_is_preserved():
    from xenova_gateway.diagnostics import GatewayDiagnostics
    logger=logging.getLogger('xenova_gateway');old=list(logger.handlers);prop=logger.propagate;level=logger.level
    logger.setLevel(logging.INFO)
    class Failing(logging.Handler):
        def emit(self,record):raise OSError(28,'owned full sink')
    logger.handlers=[Failing()];channel=GatewayDiagnostics(logger)
    try:
        for _ in range(50):logger.warning('KNOWN_FAILURE')
        channel.close();assert channel.failed>0;assert channel.first_failure=='OSError'
    finally:channel.close();logger.handlers=old;logger.propagate=prop;logger.setLevel(level)
    stream=io.StringIO();handler=logging.StreamHandler(stream);logger.handlers=[handler];channel=GatewayDiagnostics(logger)
    try:
        try:raise RuntimeError('specific fatal cause')
        except RuntimeError:logger.exception('fatal invariant')
        assert 'RuntimeError: specific fatal cause' in stream.getvalue()
        assert channel.submitted==0
    finally:channel.close();logger.handlers=old;logger.propagate=prop


def test_aux_offline_and_publish_exception_remain_distinct_without_repeated_stacks(tmp_path):
    w=World(tmp_path/'authority');client=Client();runner=VerifiedGatewayRunner(client,MockSink(),
        authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    client.connected=False
    with patch('traceback.format_exception',side_effect=AssertionError('offline built a stack')):
        for _ in range(100):runner._emit([PublishEvent('drive.mode.changed',{})])
    assert runner.aux_offline==100 and runner.aux_publish_exceptions==0
    client.connected=True
    def fail(*args,**kwargs):raise TypeError('specific internal publish defect')
    client.publish=fail
    import traceback
    with patch('traceback.format_exception',wraps=traceback.format_exception) as stacks:
        for _ in range(100):runner._emit([PublishEvent('drive.mode.changed',{})])
    assert stacks.call_count==1 and runner.aux_publish_exceptions==100
    assert runner.aux_first_exception['cause_type']=='TypeError'
    assert 'specific internal publish defect' in runner.aux_first_exception['traceback']
    assert runner.aux_offline==100 and runner.aux_publish_failures==200


def test_distinct_transport_codes_internal_causes_and_nested_fatal_are_preserved(tmp_path):
    w=World(tmp_path/'authority');client=Client();runner=VerifiedGatewayRunner(client,MockSink(),
        authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    def submit(error):
        def fail(*args,**kwargs):raise error
        client.publish=fail;runner._emit([PublishEvent('drive.mode.changed',{})])
    for error in (OSError(5,'read source failed'),OSError(28,'storage full'),
                  TypeError('internal serializer'),KeyError('internal field')):submit(error)
    assert runner.aux_transport_errors==2
    assert [row['cause_type'] for row in runner.aux_internal_errors]==['TypeError','KeyError']
    assert 'internal serializer' in runner.aux_internal_errors[0]['traceback']
    assert 'internal field' in runner.aux_internal_errors[1]['traceback']
    assert runner.aux_internal_repeated==0
    assert [row['errno'] for row in runner.aux_transport_details]==[5,28]
    try:
        try:raise RuntimeError('specific nested invariant')
        except RuntimeError as cause:raise OSError(5,'wrapper transport-looking error') from cause
    except OSError as error:submit(error)
    assert runner.aux_transport_errors==2
    assert 'specific nested invariant' in runner.aux_internal_errors[-1]['traceback']


def test_real_stream_ENOSPC_is_counted_without_stderr_error_recursion(monkeypatch):
    from xenova_gateway.diagnostics import GatewayDiagnostics
    class Broken:
        def write(self,*args):raise OSError(28,'owned log storage full')
        def flush(self):pass
    logger=logging.getLogger('xenova_gateway');old=list(logger.handlers);level=logger.level;prop=logger.propagate
    original=logging.StreamHandler(Broken());logger.handlers=[original];logger.setLevel(logging.INFO)
    channel=GatewayDiagnostics(logger);stderr=io.StringIO()
    try:
        with patch('sys.stderr',stderr):
            for _ in range(50):logger.warning('reason=KNOWN_ERROR')
            channel.close()
        assert channel.failed==50 and channel.first_sink_error=={'cause_type':'OSError','errno':28}
        assert channel.last_sink_error==channel.first_sink_error and stderr.getvalue()==''
    finally:channel.close();logger.handlers=old;logger.setLevel(level);logger.propagate=prop


def test_gateway_diagnostic_worker_start_failure_restores_handlers(monkeypatch):
    from xenova_gateway.diagnostics import GatewayDiagnostics
    logger=logging.getLogger('xenova_gateway');old=list(logger.handlers);prop=logger.propagate
    with patch.object(threading.Thread,'start',side_effect=RuntimeError('owned worker start failure')):
        with pytest.raises(RuntimeError,match='worker start failure'):GatewayDiagnostics(logger)
    assert logger.handlers==old and logger.propagate==prop


def test_rare_defect_survives_failing_custom_sink():
    from xenova_gateway.diagnostics import GatewayDiagnostics
    class Broken(logging.Handler):
        def emit(self,record):raise OSError(28,'diagnostic sink failed')
    logger=logging.getLogger('xenova_gateway');old=list(logger.handlers);prop=logger.propagate
    logger.handlers=[Broken()];channel=GatewayDiagnostics(logger)
    try:
        try:raise TypeError('rare invariant needs its original cause')
        except TypeError:logger.exception('fatal original defect')
        assert 'TypeError: rare invariant needs its original cause' in channel.last_critical
        assert channel.first_sink_error['errno']==28
    finally:channel.close();logger.handlers=old;logger.propagate=prop


def test_entrypoint_closes_owned_resources_before_fatal_output():
    from types import SimpleNamespace
    from unittest.mock import Mock
    import xenova_gateway.__main__ as entry
    order=[];lease=Mock();lease.close.side_effect=lambda:order.append('lease closed')
    channel=Mock();channel.close.side_effect=lambda:order.append('diagnostic drain')
    settings=SimpleNamespace(log_level='WARNING',profile='legacy')
    def fatal(*args,**kwargs):
        assert kwargs['exc_info'][0] is OSError and kwargs['exc_info'][1].errno==5
        assert order==['lease closed','diagnostic drain'];order.append('fatal output')
    with patch.object(entry,'parse',return_value=settings),patch.object(entry,'_windows_timer_resolution'), \
         patch.object(entry.signal,'signal'),patch.object(entry.logging,'basicConfig'), \
         patch.object(entry,'GatewayDiagnostics',return_value=channel), \
         patch.object(entry,'GatewayLease',return_value=Mock(acquire=Mock(return_value=lease))), \
         patch.object(entry,'open_sink',side_effect=OSError(5,'specific output opening failure')), \
         patch.object(entry.log,'error',side_effect=fatal):
        assert entry.main([])==2
    assert order[-1]=='fatal output'


def test_entrypoint_cleanup_failure_preserves_first_cause_and_closes_other_owners():
    from types import SimpleNamespace
    from unittest.mock import Mock
    import xenova_gateway.__main__ as entry
    order=[];sink=Mock(spec=['close']);sink.close.side_effect=lambda:order.append('sink closed')
    lease=Mock();lease.close.side_effect=lambda:order.append('lease closed')
    channel=Mock();channel.close.side_effect=lambda:order.append('diagnostic drain')
    client=Mock();client.stop.side_effect=OSError(28,'specific client cleanup failure')
    settings=SimpleNamespace(log_level='WARNING',profile='legacy',broker_host='fixture',broker_port=1,
        username=None,password=None,start_mode='SAFE_STOP',retained_wait_s=0)
    causes=[]
    def fatal(*args,**kwargs):
        assert order==['sink closed','lease closed','diagnostic drain']
        causes.append(kwargs['exc_info'][1])
    with patch.object(entry,'parse',return_value=settings),patch.object(entry,'_windows_timer_resolution'), \
         patch.object(entry.signal,'signal'),patch.object(entry.logging,'basicConfig'), \
         patch.object(entry,'GatewayDiagnostics',return_value=channel), \
         patch.object(entry,'GatewayLease',return_value=Mock(acquire=Mock(return_value=lease))), \
         patch('xenova_mqtt.XenovaClient',return_value=client),patch.object(entry,'open_sink',return_value=sink), \
         patch.object(entry,'GatewayRunner',side_effect=ValueError('specific original construction defect')), \
         patch.object(entry.log,'error',side_effect=fatal):
        assert entry.main([])==2
    assert type(causes[0]) is ValueError and str(causes[0])=='specific original construction defect'
    assert isinstance(causes[1],OSError) and causes[1].errno==28
