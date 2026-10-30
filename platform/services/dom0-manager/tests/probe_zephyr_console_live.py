"""Opt-in public Spring WS acceptance for the MO-04 Zephyr console-test firmware.

Read {"access_token": "..."} from stdin. Never print credentials. This probe does
not replace firmware or start/stop VMs; the lab operator owns setup and recovery.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


class Console:
    def __init__(self, base, identifier, token):
        self.ws = connect(base.rstrip('/') + f'/ws/v1/vms/{identifier}/console',
                          open_timeout=20, close_timeout=3).__enter__()
        try:
            self.ws.send(json.dumps({'type': 'auth', 'access_token': token}))
            frame = json.loads(self.ws.recv(timeout=20))
            if frame != {'type': 'connected', 'vm_id': identifier}:
                raise RuntimeError('unexpected connected control')
            self.exchange(b'\x03info\r', b'MO04_CONSOLE_TEST 1.0.0')
        except BaseException:
            self.ws.close()
            raise

    def until(self, expected, timeout=30):
        output = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.ws.recv(timeout=max(.1, deadline - time.monotonic()))
            if not isinstance(chunk, bytes):
                raise RuntimeError('terminal output must be binary')
            output += chunk
            if len(output) > 65536:
                raise RuntimeError('unexpected output volume')
            if expected in output and output.endswith(b'mo04> '):
                return output
        raise TimeoutError('response not observed')

    def exchange(self, payload, expected):
        self.ws.send(payload)
        return self.until(expected)

    def detach(self):
        self.ws.send(json.dumps({'type': 'disconnect'}))
        try:
            while True:
                self.ws.recv(timeout=10)
        except ConnectionClosed as exc:
            if not exc.rcvd or exc.rcvd.code != 1000:
                raise RuntimeError('detach was not acknowledged normally') from exc

    def close(self):
        self.ws.close()


def verify(base, identifiers, token):
    evidence = {'scope': 'QEMU; public Spring WS -> FastAPI -> PTY -> Xen PV -> real Zephyr firmware',
                'vms': {}, 'isolation': {}, 'status': 'RUNNING'}
    with ExitStack() as resources:
        peers = {}
        for identifier in identifiers:
            peer = Console(base, identifier, token)
            resources.callback(peer.close)
            peers[identifier] = peer
            nonce = uuid4().hex[:12]
            results = {}
            results['ping'] = peer.exchange(f'ping {nonce}\r'.encode(), f'pong {nonce}'.encode()).decode()
            text = '한글-UTF8-' + nonce
            payload = ('echo ' + text + '\r').encode()
            peer.ws.send(payload[:6])  # Split inside the first three-byte Korean codepoint.
            peer.ws.send(payload[6:])
            results['fragmented_utf8'] = peer.until(('echo: ' + text).encode()).decode()
            results['backspace'] = peer.exchange(f'pinx\x7fg {nonce}\r'.encode(), f'pong {nonce}'.encode()).decode()
            results['crlf_and_lf'] = peer.exchange(f'ping CRLF-{nonce}\r\nping LF-{nonce}\n'.encode(),
                                                  f'pong LF-{nonce}'.encode()).decode()
            assert f'pong CRLF-{nonce}' in results['crlf_and_lf']
            results['cancel'] = peer.exchange(b'ping CANCELLED\x03' + f'ping CANCEL-{nonce}\r'.encode(),
                                              f'pong CANCEL-{nonce}'.encode()).decode()
            assert 'pong CANCELLED' not in results['cancel']
            results['unknown'] = peer.exchange(b'unknown\r', b'ERR unknown-command').decode()
            results['max_line_127_bytes'] = peer.exchange(b'echo ' + b'a' * 122 + b'\r',
                                                         b'echo: ' + b'a' * 122).decode()
            results['overflow_and_recovery'] = peer.exchange(b'x' * 128 + b'ping REJECTED\r' +
                f'ping RECOVER-{nonce}\r'.encode(), f'pong RECOVER-{nonce}'.encode()).decode()
            assert 'ERR line-too-long' in results['overflow_and_recovery']
            assert 'pong REJECTED' not in results['overflow_and_recovery']
            evidence['vms'][identifier] = {'checks': results, 'status': 'PASS'}

        for identifier, peer in peers.items():
            nonce = uuid4().hex[:12]
            peer.exchange(f'ping ONLY-{nonce}\r'.encode(), f'pong ONLY-{nonce}'.encode())
            for other_id, other in peers.items():
                if identifier == other_id:
                    continue
                try:
                    unexpected = other.ws.recv(timeout=.8)
                except TimeoutError:
                    continue
                raise RuntimeError(f'unexpected output on idle peer {other_id}: {unexpected!r}')
            evidence['isolation'][identifier] = 'PASS: response only on selected VM'
        for peer in peers.values():
            peer.detach()

    # Reconnect immediately after the cleanup acknowledgement, without sleeps or retry.
    for identifier in identifiers:
        peer = Console(base, identifier, token)
        try:
            nonce = uuid4().hex[:12]
            output = peer.exchange(f'ping RECONNECT-{nonce}\r'.encode(), f'pong RECONNECT-{nonce}'.encode())
            evidence['vms'][identifier]['reconnect'] = output.decode()
            peer.detach()
        finally:
            peer.close()
    evidence['status'] = 'PASS'
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--base-url', required=True, help='ws://host:port (public Spring endpoint)')
    parser.add_argument('--vm-id', action='append', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.exit(message='Use --execute against isolated QEMU with console-test firmware.\n')
    token = json.loads(sys.stdin.readline())['access_token']
    try:
        result = verify(args.base_url, args.vm_id, token)
    except Exception as exc:
        result = {'status': 'FAIL', 'error': str(exc)}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('Zephyr public WebSocket acceptance: ' + result['status'], flush=True)
    sys.exit(0 if result['status'] == 'PASS' else 1)
