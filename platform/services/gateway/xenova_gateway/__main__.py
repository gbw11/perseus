"""`python -m xenova_gateway`: 설정을 읽고 MQTT·ESP32 출력을 연 뒤 판정 루프를 돈다. SIGTERM·SIGINT 로 멈춘다."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import os
from pathlib import Path
from collections.abc import Sequence

from .config import SOURCE_ID, GatewaySettings, parse
from .runner import GatewayRunner
from .sink import MockSink, SerialSink, Sink, SinkError
from .authority import AuthorityStore,GatewayLease,AuthorityError,GATEWAY_LOCK
from .verified_runner import VerifiedGatewayRunner
from .diagnostics import GatewayDiagnostics

log = logging.getLogger("xenova_gateway")


def open_sink(settings: GatewaySettings) -> Sink:
    from .output_guard import VehicleLimits,LimitedSink
    # Validate before opening a real output device. Mock-only tests may opt in
    # with controlled limits; this does not provide measured vehicle values.
    limits=VehicleLimits.load(Path(settings.vehicle_limits)) if settings.vehicle_limits else None
    if settings.sink=='serial' and limits is None:raise SinkError('serial output requires vehicle limits')
    if settings.sink == "serial":
        if settings.serial_profile=='feedback-1':
            from .feedback import FeedbackSerialSink
            sink=FeedbackSerialSink(settings.serial_port,settings.serial_baud,timeout_ms=settings.feedback_timeout_ms)
        else:sink=SerialSink(settings.serial_port, settings.serial_baud)
    elif settings.frame_log == "-":sink=MockSink(sys.stdout)
    elif settings.frame_log:sink=MockSink(open(settings.frame_log, "a", encoding="utf-8"))
    else:sink=MockSink()
    return LimitedSink(sink,limits,__import__('time').monotonic()) if limits else sink


def _release_lease(lease,sink,ipc):
    """A live device/I/O owner retains the process lease until process exit."""
    if any(getattr(owner,'thread',None) is not None and owner.thread.is_alive() for owner in (sink,ipc)):
        raise AuthorityError('Gateway owner termination unconfirmed; lease retained')
    lease.close()


def _windows_timer_resolution() -> None:
    """개발 PC(Windows) 전용. 기본 타이머가 15.6 ms 단위라 큐 대기가 그만큼 늦게 깬다. dom0(Linux)에서는 하지 않는다."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)


def main(argv: Sequence[str] | None = None) -> int:
    settings = parse(argv)
    _windows_timer_resolution()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    diagnostics=GatewayDiagnostics(log)
    lease=None;client=None;monitor=None;runner=None;sink=None;ipc=None;failure=None;cleanup_errors=[];runner_started=False
    try:
        from xenova_mqtt import Identity,XenovaClient
        from xenova_mqtt.drive_binding import BindingFile
        if os.name=='posix':lease=GatewayLease(GATEWAY_LOCK).acquire()
        if settings.profile in ("verified","warm"):
            if os.geteuid()!=0:raise AuthorityError('verified dom0 Gateway must run as root')
            # The lower platform installs private libraries here, outside the
            # site-packages containing Gateway and xenova_mqtt.
            sys.path.insert(0,'/usr/lib/xenova')
            host=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            authority=AuthorityStore(Path(settings.authority_dir),host)
            if settings.initialize_control_state:
                authority.initialize(settings.initial_active_vm or settings.vm_a)
                print('drive authority initialized in SAFE_STOP; authentication and output remain separate')
                return 0
            authority.load()
            bindings=BindingFile(Path(settings.binding_file),host)
            def observe_trust():
                while not stop.is_set():
                    bindings.refresh();authority.verify();stop.wait(.1)
            monitor=threading.Thread(target=observe_trust,name='drive-trust',daemon=True);monitor.start()
        sink=open_sink(settings)  # only the lease owner may open the output device
        if settings.profile=='warm':
            from .output_owner import OutputOwner
            from .machine import Timing
            sink=OutputOwner(sink,command_timeout_s=Timing().command_timeout_s)
        client=XenovaClient(Identity(source_id=SOURCE_ID),settings.broker_host,settings.broker_port,
                           username=settings.username,password=settings.password)
        if settings.profile in ("verified","warm"):
            runner=VerifiedGatewayRunner(client,sink,authority=authority,bindings=bindings,
                                          vm_ids=(settings.vm_a,settings.vm_b),
                                          readiness_profile='warm-drive-1' if settings.profile=='warm' else 'verified-drive-1')
        else:runner=GatewayRunner(client,sink,start_mode=settings.start_mode,retained_wait_s=settings.retained_wait_s)
        if settings.profile=='warm':
            from .ipc import GatewayServer
            ipc=GatewayServer(runner,lease,settings.ipc_socket).start()
        log.info('Gateway profile=%s sink=%s',settings.profile,settings.sink)
        runner_started=True
        runner.run(stop)
    except (AuthorityError,SinkError,OSError,ValueError) as exc:
        failure=exc
    finally:
        active_failure=sys.exc_info()[1]
        stop.set()
        # 한 보조 종료 실패가 나머지 소유 반환이나 최초 실행 오류를 덮지 않게 한다.
        for finish in (ipc.close if ipc else None,client.stop if client else None,
                       runner.shutdown if runner and not runner_started else None,
                       sink.close if sink and runner is None else None,
                       (lambda:monitor.join(timeout=1)) if monitor else None,
                       (lambda:_release_lease(lease,sink,ipc)) if lease else None,diagnostics.close):
            if finish is not None:
                try:finish()
                except Exception as error:cleanup_errors.append(error)
        if active_failure is not None:
            import traceback
            for error in cleanup_errors:
                active_failure.add_note('Gateway cleanup also failed:\n'+''.join(traceback.format_exception(error)))
    if failure is not None:
        log.error('Gateway stopped: %s',failure,exc_info=(type(failure),failure,failure.__traceback__))
    for error in cleanup_errors:
        log.error('Gateway cleanup failed: %s',type(error).__name__,exc_info=(type(error),error,error.__traceback__))
    if failure is not None or cleanup_errors:return 2
    if monitor and monitor.is_alive():log.error('trust reader did not finish');return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
