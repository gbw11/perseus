"""Explicit development-FD scope, not the pending five pyserial/PTY tests.

Actual socketpair/raw PTY bytes traverse the unchanged Feedback3/OutputOwner
FSM. The external VirtualFeedback3 is a declared software device/plant leaf;
physical serial setup, ESP32 firmware and real stationary/FREE remain separate.
"""

import builtins
from collections import deque
import errno
import importlib.util
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import xenova_gateway.development_fd_transport as fd_transport
from xenova_gateway.feedback import FeedbackSerialSink, unpack
from xenova_gateway.feedback3 import (
    CLOCK_REPLY, CLOCK3, SENSOR, SENSOR3, COMMAND, Feedback3Profile, Feedback3SerialSink,
)
from xenova_gateway.frame import DriveFrame, StopFrame, encode
from xenova_gateway.odometry import OdometryConfig, VehicleOdometry
from xenova_gateway.output_owner import OutputOwner
from xenova_gateway.scene_authority import StationaryPolicy
from xenova_gateway.sink import SerialSink, SinkError

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='explicit development POSIX FD scope')


def wait(predicate, timeout=3, *, diagnostic=None):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.003)
    assert predicate(), None if diagnostic is None else diagnostic()


@pytest.fixture
def pair():
    caller, peer = socket.socketpair()
    caller.setblocking(False)
    peer.setblocking(False)
    try:
        yield caller, peer
    finally:
        caller.close()
        peer.close()


def forbid_serial(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == 'serial' or name.startswith('serial.'):
            raise AssertionError('development FD must not import pyserial')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)


def os_port(**changes):
    values = {name: getattr(os, name) for name in ('fstat', 'get_blocking', 'read', 'write', 'close')}
    values.update(changes)
    return SimpleNamespace(**values)


def test_explicit_fd_uses_one_owned_duplicate_and_never_imports_pyserial(pair, monkeypatch):
    caller, peer = pair
    forbid_serial(monkeypatch)
    before = os.fstat(caller.fileno())
    sink = FeedbackSerialSink('development-fd', development_fd=caller.fileno(), timeout_ms=500)
    owned = sink._write_fd
    try:
        assert owned != caller.fileno() and sink._owned_read_fd == owned
        assert not os.get_blocking(owned) and not os.get_blocking(caller.fileno())
        assert not os.get_inheritable(owned)
        assert os.fstat(caller.fileno()).st_ino == before.st_ino
        assert sink.ready is False and sink.device_session is None
    finally:
        sink.close()
        sink.close()
    os.fstat(caller.fileno())
    with pytest.raises(OSError):
        os.fstat(owned)
    caller.send(b'caller-still-owned')
    assert peer.recv(64) == b'caller-still-owned'
    with pytest.raises(OSError) as closed:
        sink._write_bytes(b'after-close')
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize('port,fd', (('development-fd', None), ('/dev/ttyUSB0', 0),
                                  ('development-fd', True), ('development-fd', -1)))
def test_development_fd_requires_both_explicit_guards_without_serial_import(monkeypatch, port, fd):
    forbid_serial(monkeypatch)
    with pytest.raises(SinkError):
        SerialSink(port, development_fd=fd)


def test_fd_must_already_be_nonblocking_and_regular_files_are_rejected(pair, tmp_path):
    caller, _ = pair
    caller.setblocking(True)
    with pytest.raises(SinkError):
        SerialSink('development-fd', development_fd=caller.fileno())
    assert os.get_blocking(caller.fileno())  # no silent mutation on rejection
    caller.setblocking(False)
    with (tmp_path / 'not-a-byte-device').open('w+b') as regular:
        os.set_blocking(regular.fileno(), False)
        with pytest.raises(SinkError):
            SerialSink('development-fd', development_fd=regular.fileno())
        os.fstat(regular.fileno())


def test_duplicate_survives_caller_close_and_shared_flag_change_fails_without_write(pair):
    caller, peer = pair
    sink = SerialSink('development-fd', development_fd=caller.fileno())
    try:
        caller.setblocking(True)
        with pytest.raises(OSError) as changed:
            sink._write_bytes(b'not-submitted')
        assert changed.value.errno == errno.EINVAL
        with pytest.raises(BlockingIOError):
            peer.recv(64)
        caller.setblocking(False)
        caller.close()
        assert sink._write_bytes(b'owned-copy') == 10
        assert peer.recv(64) == b'owned-copy'
    finally:
        sink.close()


def test_direct_read_is_one_bounded_syscall_and_empty_ready_read_is_eof(pair, monkeypatch):
    caller, peer = pair
    transport = fd_transport.DevelopmentFDTransport(caller.fileno())
    actual = os.read
    reads = []

    def observed(fd, size):
        reads.append((fd, size))
        return actual(fd, size)

    monkeypatch.setattr(fd_transport, 'os', os_port(read=observed))
    try:
        assert transport.read(4096) == b'' and len(reads) == 1
        peer.sendall(b'x' * 5000)
        assert transport.read(4096) == b'x' * 4096 and len(reads) == 2
        for size in (0, 4097, True):
            with pytest.raises(ValueError):
                transport.read(size)
        assert len(reads) == 2
        assert transport.read(4096) == b'x' * 904 and len(reads) == 3
        peer.shutdown(socket.SHUT_RDWR)
        with pytest.raises(OSError) as eof:
            transport.read(4096)
        assert eof.value.errno == errno.EPIPE and len(reads) == 4
        assert all(size == 4096 for _, size in reads)
    finally:
        transport.close()


@pytest.mark.parametrize('fault', ('partial', 'eagain'))
def test_one_shot_submission_never_retries_partial_or_eagain(pair, monkeypatch, fault):
    caller, peer = pair
    sink = SerialSink('development-fd', development_fd=caller.fileno())
    actual = os.write
    calls = []
    frame = StopFrame(7, 0, 1)

    def uncertain(fd, data):
        calls.append(bytes(data))
        if fault == 'eagain':
            raise BlockingIOError(errno.EAGAIN, 'explicit development write fault')
        return actual(fd, data[:-1])

    monkeypatch.setattr(fd_transport, 'os', os_port(write=uncertain))
    try:
        with pytest.raises(SinkError):
            sink.write(frame, time.monotonic())
        assert calls == [encode(frame)] and sink.failures == 1
        if fault == 'partial':
            assert peer.recv(4096) == encode(frame)[:-1]
        else:
            with pytest.raises(BlockingIOError):
                peer.recv(4096)
    finally:
        sink.close()


def test_default_still_dispatches_to_pyserial_with_original_arguments(monkeypatch):
    original = builtins.__import__
    calls = []

    class ExplicitBackendLeaf:
        def fileno(self):
            raise NotImplementedError

        def close(self):
            pass

    def opened(port, **kwargs):
        calls.append((port, kwargs))
        return ExplicitBackendLeaf()

    def imported(name, *args, **kwargs):
        return SimpleNamespace(serial_for_url=opened) if name == 'serial' else original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', imported)
    sink = SerialSink('socket://127.0.0.1:7600', development_fd=None)
    try:
        assert calls == [('socket://127.0.0.1:7600', dict(baudrate=115200, timeout=0, write_timeout=.005))]
        assert sink._write_fd is None and not sink._development_fd
    finally:
        sink.close()


@pytest.fixture(params=('socketpair', 'raw-pty'))
def development_feedback3(request):
    # Indirect zero-error cases retain the original invalid software sensor.
    channel, steering_error_mrad = ((request.param, 1) if isinstance(request.param, str) else request.param)
    if channel == 'socketpair':
        caller, remote = socket.socketpair()
        caller.setblocking(False)
        remote.setblocking(False)
        caller_fd, remote_fd = caller.fileno(), remote.fileno()
        close_caller, close_remote = caller.close, remote.close
        shutdown_remote = lambda: remote.shutdown(socket.SHUT_RDWR)
    else:
        import tty
        remote_fd, caller_fd = os.openpty()
        tty.setraw(caller_fd)
        os.set_blocking(caller_fd, False)
        os.set_blocking(remote_fd, False)
        close_caller = lambda: os.close(caller_fd)
        remote_closed = False

        def close_remote():
            nonlocal remote_closed
            if not remote_closed:
                remote_closed = True
                os.close(remote_fd)

        shutdown_remote = close_remote
    host, gateway, vm, execution, app, mission = (str(uuid4()) for _ in range(6))
    # Explicit software encoder/plant calibration; no measured hardware claim.
    config = OdometryConfig('rc-test-1', 'encoder-lab-1', .000001, .2, .25, 2.,
        20000, 200000, 20, 32, .005, .000001, .02, .02, metres_per_tick_upper=.000001)
    odometry = VehicleOdometry(config, host)
    policy = StationaryPolicy(30000, 100000, 200000, .000001, .05, .1, 0., 0.)
    profile = Feedback3Profile('a' * 64, 'b' * 64, 100, 100, 100000, 1000000,
        200000, 100, 100, policy, 10000000, .02, 20000)
    spec = importlib.util.spec_from_file_location('development_fd_virtual_feedback3',
        Path(__file__).parents[1] / 'examples/virtual_feedback3.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class ObservedPeer(module.VirtualFeedback3):
        """Bounded observer of original producer payloads; never edits bytes."""
        def __init__(self, *args, **kwargs):
            self.original_payloads = deque(maxlen=16)
            super().__init__(*args, **kwargs)

        def send(self, kind, payload):
            self.original_payloads.append((kind, bytes(payload)))
            return super().send(kind, payload)

    # A 1mrad virtual sensor bound is explicitly declared, not measured hardware.
    # Omitting this keyword exercises the original zero-error producer below.
    sensor_options = {} if steering_error_mrad == 0 else dict(steering_error_mrad=steering_error_mrad)
    peer = ObservedPeer(remote_fd, 'a' * 64, 'b' * 64, **sensor_options).start()
    sink = owner = None
    try:
        sink = Feedback3SerialSink('development-fd', development_fd=caller_fd,
            timeout_ms=1000, odometry=odometry, profile=profile)
        owner = OutputOwner(sink, command_timeout_s=.1)
        def diagnostic():
            view = owner.device_view()
            return dict(channel=channel, sensor_error_mrad=steering_error_mrad,
                peer_failure=repr(peer.failure), owner_failure=repr(owner.first_failure),
                ready=view.get('ready'), clock_ready=view.get('clock_ready'), fault=view.get('fault'),
                rejected=sink.rejected, received=sink.received, odometry_errors=dict(odometry.errors),
                clock_sequence=sink.clock_sequence, install=peer.install is not None,
                payload_kinds=[kind for kind, _ in tuple(peer.original_payloads)])

        if steering_error_mrad == 0:
            wait(lambda: owner.device_view().get('clock_ready') and
                odometry.errors.get('ODOM_STEERING_QUALITY', 0) and
                any(kind == SENSOR for kind, _ in tuple(peer.original_payloads)), diagnostic=diagnostic)
        else:
            wait(lambda: owner.device_view().get('clock_ready') and owner.device_view().get('vehicle_sample'),
                diagnostic=diagnostic)
        yield SimpleNamespace(owner=owner, sink=sink, peer=peer, caller_fd=caller_fd,
            remote_fd=remote_fd, host=host, gateway=gateway, vm=vm, execution=execution,
            app=app, mission=mission, policy=policy, channel=channel, odometry=odometry,
            shutdown_remote=shutdown_remote)
    finally:
        if owner is not None:
            owner.close()
            assert not owner.thread.is_alive()
        elif sink is not None:
            sink.close()
        peer.close()
        assert not peer.thread.is_alive()
        close_caller()
        close_remote()


def source(link, kind='STOP', calculation=1):
    return dict(vm_id=link.vm, execution_id=link.execution, agent_instance_id=link.app,
        app_id='autonomous', generation=7, gateway_instance=link.gateway,
        direction_intent_admitted=True, command=dict(generation=7, producer_role='AUTO',
            command_kind=kind, calculation_sequence=str(calculation), plan_revision='1', phase_revision='1',
            segment_index=0, cusp_index=0, mission_id=link.mission, mission_revision='1', grant_revision='1',
            driver_session=str(UUID(int=0)), driver_action_seq='0', driver_state_seq='0', next_direction=0))


def stationary(link):
    stop = link.sink.matched_stop
    sample = link.owner.device_view().get('vehicle_sample')
    if stop is None or sample is None:
        return False
    try:
        stop.check(dict(vehicle=sample['vehicle'], calibration=sample['calibration'], odom_epoch=sample['odom_epoch']),
            link.host, link.gateway, 7, link.sink.pi_session, link.sink.device_session,
            link.policy, int(time.monotonic() * 1e6))
    except ValueError:
        return False
    return True


def test_development_feedback3_real_bytes_install_ack_stop_and_original_drive_expiry(development_feedback3):
    link = development_feedback3
    owner, sink, peer = link.owner, link.sink, link.peer
    assert owner._read_fd == sink._owned_read_fd == sink._write_fd
    assert peer.pi == UUID(sink.pi_session).bytes and sink.device_session == str(UUID(bytes=peer.device))
    assert peer.install is not None and any(lease['ack'] for lease in sink.leases.values())
    sample = owner.device_view()['vehicle_sample']
    assert sample['measured_steering_rad'] == 0 and sample['steering_error_rad'] == .001
    owner.write(StopFrame(7, 0, 1, witness=source(link)), time.monotonic())
    wait(lambda: stationary(link))
    now = time.monotonic()
    issued = int(now * 1e6)
    end = issued + 80000
    receipt = owner.write(DriveFrame(7, 1, .05, 0, issued_pi_us=issued, expires_pi_us=end,
        witness=source(link, 'DRIVE', 2)), now)
    sequence = receipt.metadata['command_sequence']
    wait(lambda: any(row[2] == sequence and row[10] == 1 for row in peer.commands))
    original = next(row for row in peer.commands if row[2] == sequence)
    assert original[5:7] == (issued, end)
    wait(lambda: owner.watchdog_stops >= 1)
    wait(lambda: peer.velocity == 0 and peer.commands[-1][10] == 2)
    assert next(row for row in peer.commands if row[2] == sequence)[6] == end
    assert sink.pending[sequence]['expires'] == end
    os.fstat(link.caller_fd)  # borrowed source FD remains owned by the caller


@pytest.mark.parametrize('development_feedback3', [('socketpair', 0), ('raw-pty', 0)],
    indirect=True, ids=('socketpair-default-zero-error', 'raw-pty-default-zero-error'))
def test_development_feedback3_original_zero_error_sensor_remains_rejected(development_feedback3):
    link = development_feedback3
    payloads = tuple(link.peer.original_payloads)
    clock_rows = [CLOCK3.unpack(payload) for kind, payload in payloads if kind == CLOCK_REPLY]
    sensor_rows = [SENSOR3.unpack(payload) for kind, payload in payloads if kind == SENSOR]
    assert clock_rows and sensor_rows
    assert all(row[-3:] == (3, 0, 0) for row in clock_rows)
    assert all(row[6:9] == (3, 0, 0) for row in sensor_rows)
    assert link.peer.steering_error_mrad == 0 and link.peer.failure is None
    assert link.owner.device_view()['clock_ready']
    assert link.odometry.errors['ODOM_STEERING_QUALITY'] > 0
    assert link.owner.device_view()['vehicle_sample'] is None and link.odometry.latest is None
    assert link.owner.first_failure is None


def test_development_feedback3_peer_eof_fences_same_owner_and_sensor_view(development_feedback3):
    link = development_feedback3
    link.peer.close()
    # Stream EOF or raw-PTY HUP/EIO reaches the same owned RX failure boundary.
    link.shutdown_remote()
    wait(lambda: link.owner.first_failure is not None)
    wait(lambda: link.owner.device_view().get('fault') == 'DEVICE_LINK_UNAVAILABLE')
    view = link.owner.device_view()
    assert view['ready'] is False and view['vehicle_sample'] is None and view['physical_stop'] is None
    assert link.owner._read_fd is None  # permanently readable EOF is not spun on


@pytest.mark.parametrize('fault', ('partial', 'eagain'))
def test_development_feedback3_uncertain_submission_blocks_future_drive(development_feedback3, monkeypatch, fault):
    link = development_feedback3
    actual = os.write
    attempted = []

    def uncertain(fd, data):
        if unpack(data)[0] == COMMAND and not attempted:
            attempted.append(bytes(data))
            if fault == 'eagain':
                raise BlockingIOError(errno.EAGAIN, 'development owner submission fault')
            return actual(fd, data[:-1])
        return actual(fd, data)

    monkeypatch.setattr(fd_transport, 'os', os_port(write=uncertain))
    with pytest.raises(SinkError):
        link.owner.write(StopFrame(7, 0, 1, witness=source(link)), time.monotonic())
    wait(lambda: link.owner.first_failure is not None)
    assert len(attempted) == 1 and link.sink.matched_stop is None
    now = time.monotonic()
    with pytest.raises(SinkError, match='new DRIVE prohibited'):
        link.owner.write(DriveFrame(7, 1, .05, 0, issued_pi_us=int(now * 1e6),
            expires_pi_us=int(now * 1e6) + 80000, witness=source(link, 'DRIVE', 2)), now)
