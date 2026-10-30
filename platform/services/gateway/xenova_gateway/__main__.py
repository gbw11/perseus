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
from .authority import AuthorityStore,GatewayLease,AuthorityError
from .verified_runner import VerifiedGatewayRunner

log = logging.getLogger("xenova_gateway")


def open_sink(settings: GatewaySettings) -> Sink:
    if settings.sink == "serial":
        return SerialSink(settings.serial_port, settings.serial_baud)
    if settings.frame_log == "-":
        return MockSink(sys.stdout)
    if settings.frame_log:
        return MockSink(open(settings.frame_log, "a", encoding="utf-8"))
    return MockSink()


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
    lease=None;client=None;monitor=None;runner=None;sink=None
    try:
        from xenova_mqtt import Identity,XenovaClient
        from xenova_mqtt.drive_binding import BindingFile
        if os.name=='posix':lease=GatewayLease(Path('/run/lock/xenova-drive-gateway.lock')).acquire()
        if settings.profile=="verified":
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
        client=XenovaClient(Identity(source_id=SOURCE_ID),settings.broker_host,settings.broker_port,
                           username=settings.username,password=settings.password)
        if settings.profile=="verified":
            runner=VerifiedGatewayRunner(client,sink,authority=authority,bindings=bindings,
                                          vm_ids=(settings.vm_a,settings.vm_b))
        else:runner=GatewayRunner(client,sink,start_mode=settings.start_mode,retained_wait_s=settings.retained_wait_s)
        log.info('Gateway profile=%s sink=%s',settings.profile,settings.sink)
        runner.run(stop)
    except (AuthorityError,SinkError,OSError) as exc:
        log.error('Gateway stopped: %s',exc);return 2
    finally:
        stop.set()
        if client:client.stop()
        if sink and runner is None:sink.close()
        if monitor:monitor.join(timeout=1)
        if lease:lease.close()
    if monitor and monitor.is_alive():log.error('trust reader did not finish');return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
