#!/usr/bin/env python3
"""새 network namespace의 실제 Mosquitto로 계정 변경·기존 연결·ACL을 검증한다.

실제 /srv, systemd 서비스, broker, VM에는 접근하지 않는다. mount/device만 대역이며
비밀번호 해시와 broker의 인증·SIGHUP·독립 수신은 실제 프로세스를 사용한다.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
import signal
import socket
import struct
import subprocess
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'
PORT = 18883


def field(value):
    raw = value.encode()
    return struct.pack('!H', len(raw)) + raw


def packet(kind, body):
    length, encoded = len(body), bytearray()
    while True:
        digit, length = length % 128, length // 128
        encoded.append(digit | (128 if length else 0))
        if not length:
            return bytes([kind]) + encoded + body


class Client:
    def __init__(self, username=None, password=None):
        self.sock = socket.create_connection(('127.0.0.1', PORT), timeout=1)
        body = field('MQTT') + bytes([4, 2 | (0xc0 if username else 0)]) + struct.pack('!H', 10) + field('account-probe-' + secrets.token_hex(8))
        if username:
            body += field(username) + field(password)
        self.sock.sendall(packet(0x10, body))
        _, value = self.receive()
        self.accepted = value == b'\0\0'

    def read(self, size):
        value = b''
        while len(value) < size:
            part = self.sock.recv(size - len(value))
            if not part:
                raise ConnectionError('closed')
            value += part
        return value

    def receive(self):
        kind, size, factor = self.read(1)[0], 0, 1
        while True:
            value = self.read(1)[0]
            size += (value & 127) * factor
            if not value & 128:
                return kind, self.read(size)
            factor *= 128

    def subscribe(self, topic):
        self.sock.sendall(packet(0x82, b'\0\1' + field(topic) + b'\0'))
        assert self.receive()[0] == 0x90

    def publish(self, topic, message):
        self.sock.sendall(packet(0x30, field(topic) + message))

    def close(self):
        self.sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', required=True)
    args = parser.parse_args()
    if os.geteuid() or os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        parser.error('root in a new network namespace required')
    if args.output.exists():
        parser.error('new result file required')
    os.environ['PATH'] = str(args.tools_root / 'usr/bin') + ':' + os.environ['PATH']
    os.environ['LD_LIBRARY_PATH'] = str(args.tools_root / 'usr/lib/x86_64-linux-gnu')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    spec = importlib.util.spec_from_file_location('account_probe', ROOT / 'platform/deploy/mosquitto/scripts/xenova_broker.py')
    broker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(broker)
    account = pwd.getpwnam('nobody')
    report = {'passed': [], 'boundary': 'actual native broker; fake persistent mount; isolated namespace; no product unit'}
    children, handles, clients = [], [], []
    with tempfile.TemporaryDirectory(prefix='xenova-account-broker-') as temporary:
        data = Path(temporary)
        data.chmod(0o755)
        rules = broker.policy()
        with patch.object(broker, 'storage', lambda data: data / 'mqtt'):
            def change(action='initialize', name=None):
                return broker.change(action, name, execute=True, data=data, gid=account.pw_gid, rules=rules)

            def current():
                return broker.load(data / 'mqtt', account.pw_gid, rules)

            def client(name=None, password=None):
                value = Client(name, password if password is not None else current()['secrets'].get(name))
                clients.append(value)
                return value

            def ready(name, allowed=True, password=None):
                end = time.monotonic() + 5
                while True:
                    value = client(name, password if password is not None else current()['secrets'].get(name))
                    if value.accepted == allowed:
                        return value
                    value.close()
                    if time.monotonic() >= end:
                        raise AssertionError('authentication result not observed')
                    time.sleep(.02)

            def delivered(pub, topic, allowed=True, reader='observer'):
                sub = client(reader)
                assert sub.accepted
                sub.subscribe(topic)
                sub.sock.settimeout(.25 if not allowed else 1)
                message = secrets.token_hex(12).encode()
                try:
                    pub.publish(topic, message)
                except (ConnectionError, OSError):
                    if allowed:
                        raise
                try:
                    kind, body = sub.receive()
                    received = kind == 0x30 and body.endswith(message)
                except (socket.timeout, ConnectionError):
                    received = False
                sub.close()
                assert received == allowed, 'independent delivery assertion failed'

            def start():
                log = (data / ('broker-' + str(len(children)) + '.log')).open('wb')
                handles.append(log)
                process = subprocess.Popen([str(args.tools_root / 'usr/sbin/mosquitto'), '-c', str(data / 'broker.conf')],
                                           stdout=log, stderr=log)
                children.append(process)
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if process.poll() is not None:
                        raise AssertionError('private broker failed to start')
                    try:
                        value = client('backend')
                        assert value.accepted
                        assert '\nUid:\t' + str(account.pw_uid) + '\t' in Path('/proc', str(process.pid), 'status').read_text()
                        return process
                    except (ConnectionRefusedError, ConnectionError):
                        time.sleep(.02)
                raise AssertionError('private broker readiness unavailable')

            try:
                change()
                original = current()
                (data / 'broker.conf').write_text(f'listener {PORT} 127.0.0.1\nuser nobody\nallow_anonymous false\n'
                    f'include_dir {data / "mqtt/current/config.d"}\npersistence false\nlog_type error\n')
                (data / 'broker.conf').chmod(0o644)
                process = start()
                assert not client().accepted
                assert not client('backend', 'WRONG-SYNTHETIC').accepted
                report['passed'].append('actual anonymous and wrong-password rejection')
                backend = client('backend')
                delivered(backend, 'xenova/v1/probes/account-test')
                change('add-account', A)
                process.send_signal(signal.SIGHUP)
                first = ready(A)
                delivered(first, f'xenova/v1/vms/{A}/heartbeat')
                delivered(first, f'xenova/v1/vms/{B}/heartbeat', False)
                delivered(first, 'xenova/v1/vehicle/control', False)
                report['passed'].append('live add and own-VM-only v1 writes')
                change('add-account', 'telemetry-' + A)
                process.send_signal(signal.SIGHUP)
                telemetry = ready('telemetry-' + A)
                delivered(telemetry, f'xenova/v2/vms/{A}/telemetry/heartbeat', reader='telemetry-reference-subscriber')
                delivered(telemetry, f'xenova/v2/vms/{B}/telemetry/heartbeat', False, 'telemetry-reference-subscriber')
                delivered(telemetry, 'xenova/v1/probes/account-test', False)
                delivered(first, f'xenova/v2/vms/{A}/telemetry/heartbeat', False, 'telemetry-reference-subscriber')
                report['passed'].append('actual v1/v2 account and cross-VM isolation')
                delivered(client('observer'), 'xenova/v1/probes/account-test', False)
                delivered(client('telemetry-reference-subscriber'), f'xenova/v2/vms/{A}/telemetry/heartbeat', False,
                          'telemetry-reference-subscriber')
                report['passed'].append('both observer profiles are read-only')
                revoked_password = current()['secrets'][A]
                change('remove-account', A)
                process.send_signal(signal.SIGHUP)
                ready(A, False, revoked_password)
                delivered(first, f'xenova/v1/vms/{A}/heartbeat', False)
                report['passed'].append('removed account denied on reconnect and on preexisting connection')
                delivered(backend, 'xenova/v1/probes/account-test')
                after = current()
                for name in original['secrets']:
                    assert after['secrets'][name] == original['secrets'][name]
                    assert after['hashes'][name] == original['hashes'][name]
                report['passed'].append('unrelated password hashes and connected backend survive reloads')
                process.terminate()
                process.wait(timeout=5)
                process = start()
                delivered(client('backend'), 'xenova/v1/probes/account-test')
                assert not client(A, revoked_password).accepted
                assert current()['secrets'] == after['secrets']
                report['passed'].append('restart reuses published generation and revocation')
                version = subprocess.run([str(args.tools_root / 'usr/sbin/mosquitto'), '-h'], capture_output=True, text=True)
                report['broker_version'] = version.stdout.splitlines()[0]
            finally:
                for value in clients:
                    value.close()
                for process in children:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                for stream in handles:
                    stream.close()
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': len(report['passed']), 'boundary': report['boundary']}))


if __name__ == '__main__':
    main()
