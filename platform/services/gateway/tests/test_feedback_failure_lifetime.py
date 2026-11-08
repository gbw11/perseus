"""실패 이후 센서 차단과 소유자 종료를 실제 PTY/codec으로 검증한다."""
import os
import select
import threading
import time
from uuid import uuid4

import pytest

from xenova_gateway.feedback import (
    CAP_ACCEPT, CAP_APPLY, CAP_STATIONARY, FeedbackSerialSink, Framer, hello_ack, packet, report,
)
from xenova_gateway.frame import DriveFrame, StopFrame
from xenova_gateway.odometry import (
    CAP_ODOMETRY, ODOM_PROBE, ODOM_REPORT, PROBE, SAMPLE,
    OdometryConfig, VehicleOdometry,
)
from xenova_gateway.output_owner import OutputOwner
from xenova_gateway.sink import SinkError


def _receive(sink, master, wire, now):
    assert os.write(master, wire) == len(wire)
    assert select.select([sink._serial.fileno()], [], [], 1)[0]
    sink.poll(now)


def _outgoing(master, framer):
    frames = []
    while select.select([master], [], [], 0)[0]:
        frames.extend(framer.feed(os.read(master, 4096)))
    return frames


@pytest.fixture
def feedback2():
    master, slave = os.openpty()
    now = [0.]
    config = OdometryConfig('rc-test-1', 'encoder-lab-1', .001, .2, .25, 2.,
                            20000, 200000, 20, 32, .005, .01, .02, .02)
    sensor = VehicleOdometry(config, str(uuid4()))
    sink = FeedbackSerialSink(os.ttyname(slave), timeout_ms=500,
                              clock=lambda: now[0], odometry=sensor)
    device = str(uuid4())
    frames = Framer()
    try:
        sink.poll(now[0])
        _receive(sink, master, hello_ack(sink.pi_session, device,
                 CAP_ACCEPT | CAP_APPLY | CAP_ODOMETRY, version=2), now[0])
        assert sink.ready and not sink.fault
        _outgoing(master, frames)
        sink.write(StopFrame(42, 0, 1), now[0])
        now[0] = .6
        sink.poll(now[0])
        assert sink.fault == 'DEVICE_APPLY_TIMEOUT'
        yield sink, sensor, master, now, frames
    finally:
        sink.close()
        os.close(master)
        os.close(slave)


def _encoder_sample(feedback2, sample, at, *, inject_probe=False):
    sink, sensor, master, now, frames = feedback2
    now[0] = at
    sink.poll(now[0])
    probes = [payload for kind, payload in _outgoing(master, frames)
              if kind == ODOM_PROBE]
    if inject_probe:
        assert not probes  # 차단 뒤 제품은 새 센서 요청도 내보내지 않는다.
        # 세션/시각/품질이 유효한 입력도 관측을 재활성화하지 못하는지 확인한다.
        probe = sensor.probe(sink.pi_session, int(at * 1000000))
        assert probe is not None
    else:
        assert len(probes) == 1
        probe = probes[0]
    pi, device, echo = PROBE.unpack(probe)
    now[0] = at + .001
    wire = packet(ODOM_REPORT, SAMPLE.pack(pi, device, echo,
                  int(at * 1000000), sample, sample, sample, 5, 0, 10))
    _receive(sink, master, wire, now[0])


@pytest.mark.parametrize('failure', [
    'restart', 'fault', 'rejected', 'command_capability', 'odometry_capability',
])
def test_apply_timeout_does_not_hide_later_sensor_blocking_failure(feedback2, failure):
    sink, sensor, master, now, frames = feedback2
    _encoder_sample(feedback2, 1, .6)
    _encoder_sample(feedback2, 2, .62)
    assert sink.snapshot['vehicle_sample']['source_sample'] == 2
    assert sink.snapshot['fault'] == 'DEVICE_APPLY_TIMEOUT'

    now[0] = .622
    if failure == 'restart':
        previous = sink.device_session
        _receive(sink, master, hello_ack(sink.pi_session, str(uuid4()),
                 CAP_ACCEPT | CAP_APPLY | CAP_ODOMETRY, version=2), now[0])
        assert sink.device_session != previous
        samples = (1, 2)
    elif failure.endswith('_capability'):
        capabilities = (CAP_ACCEPT | CAP_ODOMETRY if failure == 'command_capability'
                        else CAP_ACCEPT | CAP_APPLY)
        _receive(sink, master, hello_ack(sink.pi_session, sink.device_session,
                 capabilities, version=2), now[0])
        samples = (3, 4)
    else:
        frame = StopFrame(42, 7, 1)
        receipt = sink.write(frame, now[0])
        _receive(sink, master, report(sink.pi_session, sink.device_session,
                 receipt.metadata['command_sequence'], frame,
                 4 if failure == 'fault' else 3, 1), now[0])
        samples = (3, 4)

    assert sink.snapshot['vehicle_sample'] is None
    _receive(sink, master, hello_ack(sink.pi_session, sink.device_session,
             CAP_ACCEPT | CAP_APPLY | CAP_ODOMETRY, version=2), now[0])
    assert sink.snapshot['vehicle_sample'] is None
    for sample, at in zip(samples, (.66, .68)):
        _encoder_sample(feedback2, sample, at, inject_probe=True)
        view = sink.snapshot
        assert view['fault'] == 'DEVICE_APPLY_TIMEOUT'
        assert view['vehicle_sample'] is None
        assert all(view[key] is None for key in ('accepted', 'applied', 'physical_stop'))
    with pytest.raises(SinkError, match='device fault'):
        sink.write(DriveFrame(42, 0, .1, 0), now[0])


def test_owner_read_failure_keeps_sensor_blocked_after_link_recovers(feedback2, monkeypatch):
    sink, sensor, master, now, frames = feedback2
    _encoder_sample(feedback2, 1, .6)
    _encoder_sample(feedback2, 2, .62)
    assert sink.snapshot['vehicle_sample']['source_sample'] == 2
    now[0] = .66
    probe = sensor.probe(sink.pi_session, 660000)
    pi, device, echo = PROBE.unpack(probe)
    read = sink._serial.read
    failed = threading.Event()
    recovered_read = threading.Event()

    def failed_then_recovered_read(size):
        if not failed.is_set():
            failed.set()
            raise OSError('injected serial read failure')
        result = read(size)
        recovered_read.set()
        return result

    monkeypatch.setattr(sink._serial, 'read', failed_then_recovered_read)
    # The owner uses the native one-shot read; after its first fault the
    # existing timed public-poll fallback still verifies later byte recovery.
    def failed_owned_read(read_ready):
        if not failed.is_set():
            failed.set()
            raise OSError('injected serial read failure')
        raise AssertionError('failed native fd must leave the readiness wait')
    monkeypatch.setattr(sink, '_read_owned', failed_owned_read)
    owner = OutputOwner(sink, command_timeout_s=.1, clock=lambda: now[0])
    try:
        assert recovered_read.wait(2)
        assert owner.first_failure is not None
        assert owner.device_view()['vehicle_sample'] is None
        before = sink.received
        now[0] = .661
        wire = hello_ack(sink.pi_session, sink.device_session,
                         CAP_ACCEPT | CAP_APPLY | CAP_ODOMETRY, version=2)
        wire += packet(ODOM_REPORT, SAMPLE.pack(pi, device, echo,
                       660000, 3, 3, 3, 5, 0, 10))
        assert os.write(master, wire) == len(wire)
        deadline = time.monotonic() + 2
        while sink.received < before + 2 and time.monotonic() < deadline:
            time.sleep(.001)
        assert sink.received >= before + 2
        assert sink.fault == 'DEVICE_APPLY_TIMEOUT'
        assert sink.snapshot['vehicle_sample'] is None
        assert owner.device_view()['vehicle_sample'] is None
        assert all(owner.device_view()[key] is None
                   for key in ('accepted', 'applied', 'physical_stop'))
        with pytest.raises(SinkError, match='new DRIVE prohibited'):
            owner.write(DriveFrame(42, 0, .1, 0), now[0])
    finally:
        owner.close()


def test_cached_vehicle_view_expires_while_its_owner_is_delayed(feedback2, monkeypatch):
    sink, sensor, master, now, frames = feedback2
    _encoder_sample(feedback2, 1, .6)
    _encoder_sample(feedback2, 2, .62)
    assert sink.snapshot['vehicle_sample']['source_sample'] == 2
    polling = threading.Event()
    release = threading.Event()
    poll = sink._poll_owned

    def delayed_poll(at,read_ready=False):
        polling.set()
        assert release.wait(2)
        return poll(at,read_ready=read_ready)

    monkeypatch.setattr(sink, '_poll_owned', delayed_poll)
    owner = OutputOwner(sink, command_timeout_s=.1, clock=lambda: now[0])
    try:
        assert polling.wait(1)
        original = sink.snapshot['vehicle_sample']
        assert owner.device_view()['vehicle_sample'] == original
        now[0] += .3
        assert owner.device_view()['vehicle_sample'] is None
        assert sink.snapshot['vehicle_sample'] is original
        assert owner.device_view()['fault'] == 'DEVICE_APPLY_TIMEOUT'
    finally:
        release.set()
        owner.close()


def test_cached_applied_stop_expires_without_waiting_for_the_owner(monkeypatch):
    master, slave = os.openpty()
    now = [0.]
    sink = FeedbackSerialSink(os.ttyname(slave), timeout_ms=500, clock=lambda: now[0])
    owner = None
    polling = threading.Event()
    release = threading.Event()
    try:
        sink.poll(now[0])
        _receive(sink, master, hello_ack(sink.pi_session, str(uuid4()),
                 CAP_ACCEPT | CAP_APPLY | CAP_STATIONARY), now[0])
        frame = StopFrame(42, 7, 1)
        receipt = sink.write(frame, now[0])
        now[0] = .1
        _receive(sink, master, report(sink.pi_session, sink.device_session,
                 receipt.metadata['command_sequence'], frame, 2, 1, stationary=True), now[0])
        assert sink.snapshot['physical_stop'] is True
        poll = sink._poll_owned
        def delayed_poll(at,read_ready=False):
            polling.set()
            assert release.wait(2)
            return poll(at,read_ready=read_ready)
        monkeypatch.setattr(sink, '_poll_owned', delayed_poll)
        owner = OutputOwner(sink, command_timeout_s=.1, clock=lambda: now[0])
        assert polling.wait(1)
        assert owner.device_view()['physical_stop'] is True
        now[0] = .7
        view = owner.device_view()
        assert all(view[key] is None for key in ('accepted', 'applied', 'physical_stop'))
        assert view['observed_monotonic'] == .1 and view['fault'] is None
        assert sink.snapshot['physical_stop'] is True
    finally:
        release.set()
        if owner is not None:owner.close()
        else:sink.close()
        os.close(master)
        os.close(slave)


def test_close_serializes_wakeup_write_with_watchdog_owner_cleanup(monkeypatch):
    master, slave = os.openpty()
    sink = FeedbackSerialSink(os.ttyname(slave), timeout_ms=500)
    owner = None
    watchdog_write = threading.Event()
    release_watchdog = threading.Event()
    cleanup_started = threading.Event()
    cleanup_finished = threading.Event()
    try:
        sink.poll(time.monotonic())
        _receive(sink, master, hello_ack(sink.pi_session, str(uuid4())), time.monotonic())
        serial_write = sink.write

        def blocked_watchdog_write(frame, now):
            if isinstance(frame, StopFrame):
                watchdog_write.set()
                assert release_watchdog.wait(2)
            return serial_write(frame, now)

        monkeypatch.setattr(sink, 'write', blocked_watchdog_write)
        owner = OutputOwner(sink, command_timeout_s=.03)
        owner.write(DriveFrame(42, 0, .1, 0), time.monotonic())
        assert watchdog_write.wait(2)
        wake_write = owner._wake_write
        assert wake_write is not None
        close_wakeup = owner._close_wakeup
        write = os.write

        def observed_cleanup():
            cleanup_started.set()
            try:
                close_wakeup()
            finally:
                cleanup_finished.set()

        def paused_wakeup_write(fd, data):
            if fd == wake_write:
                release_watchdog.set()
                assert cleanup_started.wait(2)
                # 기존 경합은 이 구간에서 FD를 닫는다. 직렬화한 종료는
                # 현재 wake write가 끝날 때까지 condition에서 기다린다.
                cleanup_finished.wait(.1)
            return write(fd, data)

        monkeypatch.setattr(owner, '_close_wakeup', observed_cleanup)
        monkeypatch.setattr('xenova_gateway.output_owner.os.write', paused_wakeup_write)
        owner.close()
        assert cleanup_finished.is_set()
        assert not owner.thread.is_alive()
        assert owner.watchdog_stops == 1
    finally:
        release_watchdog.set()
        if owner is not None:
            owner.close()
        else:
            sink.close()
        os.close(master)
        os.close(slave)
