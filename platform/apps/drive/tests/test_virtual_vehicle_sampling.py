"""실제 PTY에 묶인 probe도 각 실제 물리 관측 시각을 보존해야 한다."""
import os
import select
import threading
import time
import tty
from types import SimpleNamespace
from uuid import UUID, uuid4

import run_lattice_pair as pair
from xenova_gateway.feedback import command, packet
from xenova_gateway.frame import DriveFrame
from xenova_gateway.odometry import (
    ODOM_PROBE, ODOM_REPORT, PROBE, SAMPLE, OdometryConfig, VehicleOdometry,
)


def test_batched_probes_capture_real_times_and_keep_consumer_latest(monkeypatch):
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(slave, False)
    stop = threading.Event()
    hold_read = threading.Event()
    read_paused = threading.Event()
    release_read = threading.Event()
    errors = []
    vehicle = pair.Vehicle(master, stop)
    pi = str(uuid4())
    config = OdometryConfig('rc-test-1', 'encoder-lab-1', .00005, .2, .25,
                            1., 20000, 200000, 20, 64, .005, .01, .02, .02)
    consumer = VehicleOdometry(config, str(uuid4()))
    consumer.device_session(vehicle.device)
    parser = pair.Framer()
    captures = []
    reads = []
    advance = vehicle._advance
    read = os.read

    def observed_advance():
        before = time.monotonic()
        now = advance()
        after = time.monotonic()
        assert before <= now <= after
        captures.append((int(now * 1e6) + 7000000, round(vehicle.left),
                         round(vehicle.right), round(vehicle.steer * 1000), now))
        return now

    def observed_read(fd, size):
        if fd == master and hold_read.is_set():
            read_paused.set()
            assert release_read.wait(2)
            hold_read.clear()
        data = read(fd, size)
        if fd == master:
            reads.append(data)
        return data

    monkeypatch.setattr(vehicle, '_advance', observed_advance)
    monkeypatch.setattr(pair.os, 'read', observed_read)

    def queue_probe(prefix=b''):
        sent_us = int(time.monotonic() * 1e6)
        payload = consumer.probe(pi, sent_us)
        assert payload is not None
        wire = prefix + packet(ODOM_PROBE, payload)
        assert os.write(slave, wire) == len(wire)
        return wire

    def receive_samples(count):
        result = []
        deadline = time.monotonic() + 1
        while len(result) < count:
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([slave], [], [], remaining)[0], errors
            data = os.read(slave, 4096)
            received_us = int(time.monotonic() * 1e6)
            result.extend((payload, received_us) for kind, payload in parser.feed(data)
                          if kind == ODOM_REPORT)
        assert len(result) == count
        return result

    def run():
        try:
            vehicle.run()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, name='vehicle-sampling-regression')
    thread.start()

    try:
        # 실제 serial DRIVE로 움직인 encoder 값이며 명령값을 odom으로 쓰지 않는다.
        queue_probe(command(pi, vehicle.device, 1, DriveFrame(42, 0, .3, .1)))
        payload, received = receive_samples(1)[0]
        consumer.receive(pi, payload, received)
        time.sleep(.021)
        queue_probe()
        payload, received = receive_samples(1)[0]
        consumer.receive(pi, payload, received)
        assert consumer.current(received) is not None
        assert consumer.current(received)['left_ticks'] > 0

        time.sleep(.021)
        hold_read.set()
        assert read_paused.wait(1)
        first = queue_probe()
        time.sleep(.021)
        second = queue_probe()
        reads.clear()
        capture_start = len(captures)
        release_read.set()
        received_samples = receive_samples(2)
        assert reads == [first + second]  # 두 probe가 실제 한 read에서 완성됐다.
        samples = [SAMPLE.unpack(payload) for payload, _ in received_samples]
        assert samples[0][2] < samples[1][2]
        assert samples[0][3] < samples[1][3]
        assert samples[0][4] < samples[1][4]
        for (payload, received), sample in zip(received_samples, samples):
            observed = next(row for row in captures[capture_start:] if row[0] == sample[3])
            assert (sample[5], sample[6], sample[8]) == observed[1:4]
            assert consumer.receive(pi, payload, received) is not None
            assert consumer.current(received)['source_sample'] == sample[4]
        assert not consumer.errors
        assert vehicle.last_command in [row[4] for row in captures]
        assert vehicle.last_command > captures[0][4]
    finally:
        release_read.set()
        stop.set()
        thread.join(2)
        os.close(master)
        os.close(slave)
        assert not thread.is_alive()
        assert not errors


def test_coarse_device_clock_does_not_invent_another_sample(monkeypatch):
    master, slave = os.openpty()
    tty.setraw(slave)

    class OneIteration:
        calls = 0

        def wait(self, _):
            self.calls += 1
            return self.calls > 1

    vehicle = pair.Vehicle(master, OneIteration())
    pi = UUID(str(uuid4())).bytes
    device = UUID(vehicle.device).bytes
    wire = b''.join(packet(ODOM_PROBE, PROBE.pack(pi, device, echo)) for echo in (1, 2))
    try:
        assert os.write(slave, wire) == len(wire)
        # 명시적인 coarse-clock 경계: 실제로 구별되지 않는 시각을 +1로 꾸미지 않는다.
        monkeypatch.setattr(pair, 'time', SimpleNamespace(monotonic=lambda: 1.))
        vehicle.run()
        assert select.select([slave], [], [], 1)[0]
        frames = pair.Framer().feed(os.read(slave, 4096))
        assert len(frames) == 1 and frames[0][0] == ODOM_REPORT
        fields = SAMPLE.unpack(frames[0][1])
        assert fields[3] == 8000000 and fields[4] == 1
        assert vehicle.sample == 1
    finally:
        os.close(master)
        os.close(slave)
