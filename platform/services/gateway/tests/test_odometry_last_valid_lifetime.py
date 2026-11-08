"""Rejected probes do not renew a last good sample or erase it before its age bound.

The real PTY/binary MQTT case uses product framing, odometry and DriveLink I/O.
Only encoder input and the runner's read-only device view are fixture boundaries.
"""
from copy import deepcopy
from contextlib import contextmanager
import os
import queue
import select
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from xenova_drive_world import decode, schema
from xenova_gateway.drive_link import DriveLinkIO, DriveLinkProvider, VEHICLE_TOPIC
from xenova_gateway.feedback import (
    CAP_ACCEPT, CAP_APPLY, FeedbackSerialSink, Framer, HELLO, HELLO_PAYLOAD,
    hello_ack, packet,
)
from xenova_gateway.odometry import (
    CAP_ODOMETRY, ODOM_PROBE, ODOM_REPORT, PROBE, SAMPLE, OdometryError, VehicleOdometry,
)
from xenova_mqtt.client import _new_paho
from test_odometry import config
from test_state_transport import broker, until
from test_verified import A, B


def report(owner, pi, echo, index, source, *, left=0, right=0, quality=5, steer=0, error=10):
    return SAMPLE.pack(UUID(pi).bytes, UUID(owner.device).bytes, echo, source,
                       index, left, right, quality, steer, error)


def seeded(*, old_pending=False):
    owner = VehicleOdometry(config(), str(uuid4()))
    owner.device_session(str(uuid4()))
    pi = str(uuid4())
    if old_pending:
        assert owner.probe(pi, 900000)
    for index, echo in ((1, 1000000), (2, 1020000)):
        assert owner.probe(pi, echo)
        owner.receive(pi, report(owner, pi, echo, index, 7000000+(index-1)*20000,
                                left=index, right=index), echo+1000)
    assert owner.latest['pi_sample_us'] == 1020500
    return owner, pi


@pytest.mark.parametrize('case,code', [
    ('uncertain', 'ODOM_CLOCK_UNCERTAIN'),
    ('expired_reply', 'ODOM_PROBE_EXPIRED'),
    ('expired_submission', 'ODOM_PROBE_EXPIRED'),
    ('duplicate_echo', 'ODOM_PROBE_REFERENCE'),
    ('unknown_past_echo', 'ODOM_PROBE_REFERENCE'),
])
def test_probe_rejection_keeps_only_the_original_sample_lifetime(case, code):
    owner, pi = seeded(old_pending=case.startswith('expired'))
    original = owner.latest
    saved = deepcopy(original)
    previous, epoch, pose, sequence = owner.previous, owner.epoch, owner.pose, owner.sequence
    samples = list(owner.samples)
    keys = set(owner.__dict__)
    with pytest.raises(OdometryError, match=code):
        if case == 'uncertain':
            assert owner.probe(pi, 1040000)
            owner.mark_submitted(1040000, 1040000)
            owner.receive(pi, report(owner, pi, 1040000, 3, 7040000), 1090000)
        elif case == 'expired_reply':
            owner.receive(pi, report(owner, pi, 900000, 1, 6900000), 1110001)
        elif case == 'expired_submission':
            owner.mark_submitted(900000, 1110001)
        else:
            echo = 1020000 if case == 'duplicate_echo' else 1010000
            owner.receive(pi, report(owner, pi, echo, 2, 7020000), 1022000)
    assert owner.latest is original and owner.latest == saved
    assert (owner.previous, owner.epoch, owner.pose, owner.sequence) == (previous, epoch, pose, sequence)
    assert list(owner.samples) == samples and set(owner.__dict__) == keys
    assert owner.first_error == code and owner.errors == {code: 1}
    assert owner.current(1220000) == saved
    assert owner.current(1220001) is None
    assert 900000 not in owner.pending if case.startswith('expired') else True
    if case == 'uncertain':
        assert 1040000 not in owner.pending


def test_repeated_rejected_packets_never_renew_sample_or_hide_first_cause():
    owner, pi = seeded()
    original = deepcopy(owner.latest)
    for now in (1100000, 1200000, 1220000, 1220001):
        with pytest.raises(OdometryError, match='ODOM_PROBE_REFERENCE'):
            owner.receive(pi, report(owner, pi, 1010000, 2, 7020000), now)
        assert owner.latest == original
        assert (owner.current(now) is not None) == (now <= 1220000)
    assert owner.errors == {'ODOM_PROBE_REFERENCE': 4}
    assert owner.first_error == 'ODOM_PROBE_REFERENCE'


@pytest.mark.parametrize('case,code', [
    ('frame', 'ODOM_FRAME'), ('session', 'ODOM_SESSION'),
    ('quality', 'ODOM_SENSOR_QUALITY'), ('steering_quality', 'ODOM_STEERING_QUALITY'),
    ('steering_presence', 'ODOM_STEERING_PRESENCE'), ('counter', 'ODOM_COUNTER_RANGE'),
    ('sample_order', 'ODOM_SAMPLE_ORDER'), ('encoder', 'ODOM_ENCODER_DISCONTINUITY'),
    ('ackermann', 'ODOM_ACKERMANN_INCONSISTENT'),
    ('future_echo', 'ODOM_PROBE_REFERENCE'), ('negative_marked_rtt', 'ODOM_PROBE_REFERENCE'),
    ('unknown_future_echo', 'ODOM_PROBE_REFERENCE'),
])
def test_trust_failures_still_invalidate_a_fresh_sample_immediately(case, code):
    owner, pi = seeded()
    echo, now = 1040000, 1041000
    assert owner.probe(pi, echo)
    values = dict(index=3, source=7040000, left=3, right=3)
    if case == 'quality':
        values.update(quality=0)
    elif case == 'steering_quality':
        values.update(error=0)
    elif case == 'steering_presence':
        values.update(quality=1, steer=1)
    elif case == 'counter':
        values.update(left=2**31)
    elif case == 'sample_order':
        values.update(index=2)
    elif case == 'encoder':
        values.update(left=1000, right=1000)
    elif case == 'ackermann':
        values.update(left=1, right=3)
    elif case == 'future_echo':
        now = echo-1
    elif case == 'unknown_future_echo':
        echo = 1100000
    elif case == 'negative_marked_rtt':
        owner.mark_submitted(echo, 1060000)
        now = 1059999
    payload = (b'partial' if case == 'frame' else report(owner, str(uuid4()) if case == 'session' else pi,
                                                       echo, **values))
    with pytest.raises(OdometryError, match=code):
        owner.receive(pi, payload, now)
    assert owner.latest is None and owner.current(now) is None
    assert owner.first_error == code and owner.errors == {code: 1}


def test_new_valid_encoder_sample_and_long_gap_keep_original_epoch_rules():
    owner, pi = seeded()
    old = deepcopy(owner.latest)
    with pytest.raises(OdometryError, match='ODOM_PROBE_REFERENCE'):
        owner.receive(pi, report(owner, pi, 1020000, 2, 7020000), 1022000)
    assert owner.probe(pi, 1040000)
    current = owner.receive(pi, report(owner, pi, 1040000, 3, 7040000, left=3, right=3), 1041000)
    assert current['sequence'] == old['sequence']+1
    assert current['odom_epoch'] == old['odom_epoch']
    assert current['odom_pose']['x'] > old['odom_pose']['x']
    assert owner.first_error == 'ODOM_PROBE_REFERENCE'
    assert owner.probe(pi, 1400000)
    assert owner.receive(pi, report(owner, pi, 1400000, 4, 7400000, left=3, right=3), 1401000) is None
    assert owner.latest is None and owner.epoch != old['odom_epoch']
    assert owner.errors['ODOM_CONTINUITY_GAP'] == 1
    assert owner.probe(pi, 1420000)
    rebuilt = owner.receive(pi, report(owner, pi, 1420000, 5, 7420000, left=4, right=4), 1421000)
    assert rebuilt['odom_epoch'] == owner.epoch and rebuilt['source_sample'] == 5
    owner.device_session(str(uuid4()))
    assert owner.latest is None and owner.current(1421000) is None


@contextmanager
def actual_pty():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    now = [1.0]
    owner = VehicleOdometry(config(), str(uuid4()))
    sink = FeedbackSerialSink(os.ttyname(slave), timeout_ms=500,
                              clock=lambda: now[0], odometry=owner)
    frames = Framer()
    def outgoing():
        result = []
        while select.select([master], [], [], 0)[0]:
            result.extend(frames.feed(os.read(master, 4096)))
        return result
    def incoming(wire, at):
        now[0] = at
        assert os.write(master, wire) == len(wire)
        assert select.select([sink._serial.fileno()], [], [], 1)[0]
        sink.poll(now[0])
    def probe(at):
        now[0] = at
        sink.poll(now[0])
        probes = [data for kind, data in outgoing() if kind == ODOM_PROBE]
        assert len(probes) == 1
        return PROBE.unpack(probes[0])[2]
    try:
        sink.poll(now[0])
        first, = outgoing()
        assert first[0] == HELLO and HELLO_PAYLOAD.unpack(first[1])[0] == 2
        incoming(hello_ack(sink.pi_session, str(uuid4()), CAP_ACCEPT | CAP_APPLY | CAP_ODOMETRY, version=2), 1.0)
        initial_probe, = [data for kind, data in outgoing() if kind == ODOM_PROBE]
        echo = PROBE.unpack(initial_probe)[2]
        incoming(packet(ODOM_REPORT, report(owner, sink.pi_session, echo, 1, 7000000)), 1.001)
        echo = probe(1.02)
        incoming(packet(ODOM_REPORT, report(owner, sink.pi_session, echo, 2, 7020000, left=1, right=1)), 1.021)
        assert sink.snapshot['vehicle_sample']['source_sample'] == 2
        yield sink, owner, master, now, outgoing, incoming, probe
    finally:
        sink.close()
        os.close(master)
        os.close(slave)


def test_real_pty_bad_probe_retains_old_sample_but_quality_and_device_fence_do_not():
    with actual_pty() as (sink, owner, master, now, outgoing, incoming, probe):
        original = deepcopy(sink.snapshot['vehicle_sample'])
        echo = probe(1.04)
        incoming(packet(ODOM_REPORT, report(owner, sink.pi_session, echo, 3, 7040000)), 1.09)
        assert sink.rejected == 1 and owner.first_error == 'ODOM_CLOCK_UNCERTAIN'
        assert sink.snapshot['vehicle_sample'] == original
        retry, = [data for kind, data in outgoing() if kind == ODOM_PROBE]
        next_echo = PROBE.unpack(retry)[2]
        incoming(packet(ODOM_REPORT, report(owner, sink.pi_session, next_echo, 4, 7090000, left=2, right=2)), 1.091)
        assert sink.snapshot['vehicle_sample']['source_sample'] == 4
        assert sink.snapshot['vehicle_sample']['sequence'] == original['sequence']+1
        sink._fail('DEVICE_APPLY_TIMEOUT')
        assert sink._view()['vehicle_sample'] is not None
        assert sink._view()['accepted'] is None and sink._view()['applied'] is None
        sink._fail('DEVICE_LINK_UNAVAILABLE')
        assert sink._view()['vehicle_sample'] is None
        assert sink.fault == 'DEVICE_APPLY_TIMEOUT'
        assert owner.first_error == 'ODOM_CLOCK_UNCERTAIN'


def test_real_binary_mqtt_never_republishes_retained_sample_as_new_progress(broker):
    with actual_pty() as (sink, owner, master, now, outgoing, incoming, probe):
        runner = SimpleNamespace(authority=SimpleNamespace(host=owner.host_boot), device_view=lambda: sink.snapshot)
        io = DriveLinkIO('127.0.0.1', broker, None, None, (A, B), clock=lambda: now[0])
        provider = DriveLinkProvider(runner, io, None, str(uuid4()))
        received = queue.Queue()
        subscribed = threading.Event()
        client = _new_paho('last-valid-sample-'+str(uuid4()))
        client.on_connect = lambda c, u, f, r, *extra: c.subscribe(VEHICLE_TOPIC, qos=0)
        client.on_subscribe = lambda *args: subscribed.set()
        client.on_message = lambda c, u, m: received.put(decode(bytes(m.payload), schema.VehicleSample))
        io.start()
        client.connect('127.0.0.1', broker, 10)
        client.loop_start()
        try:
            assert subscribed.wait(3)
            until(lambda: io.connected)
            provider.tick()
            original = received.get(timeout=3)
            assert original.source_sample == 2
            echo = probe(1.04)
            incoming(packet(ODOM_REPORT, report(owner, sink.pi_session, echo, 3, 7040000)), 1.09)
            assert sink.snapshot['vehicle_sample']['pi_sample_us'] == original.pi_sample_us
            for _ in range(8):
                provider.tick()
            assert io.sent == 1 and not io.pending and received.empty()
            retry, = [data for kind, data in outgoing() if kind == ODOM_PROBE]
            incoming(packet(ODOM_REPORT, report(owner, sink.pi_session, PROBE.unpack(retry)[2], 4, 7090000,
                                                left=2, right=2)), 1.091)
            provider.tick()
            current = received.get(timeout=3)
            assert current.sequence == original.sequence+1 and current.source_sample == 4
            assert current.pi_sample_us > original.pi_sample_us
            now[0] = (current.pi_sample_us + config().maximum_sample_age_us-current.clock_error_us+1)/1e6
            sink.snapshot = sink._view()
            assert sink.snapshot['vehicle_sample'] is None
            provider.tick()
            assert io.sent == 2 and not io.pending and received.empty()
            assert owner.first_error == 'ODOM_CLOCK_UNCERTAIN'
        finally:
            client.disconnect()
            client.loop_stop()
            io.close()
        assert io.failure is None and not io.thread.is_alive()
