"""실제 WebSocket→PTY→Debian 검증. 인증 값은 인자/URL/로그에 저장하지 않는다."""
import argparse
import getpass
import json
import time

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect


def until(ws, *markers):
    data = b''
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        chunk = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
        if not isinstance(chunk, bytes):
            raise RuntimeError('console output must be binary')
        data += chunk
        for marker in markers:
            if marker in data:
                return marker, data
        if len(data) > 262144:
            raise RuntimeError('unexpected output volume')
    raise TimeoutError('console output timeout')


def run(base_url, vm_id, token, password):
    url = base_url.rstrip('/') + f'/api/v1/vms/{vm_id}/console'
    headers = {'Authorization': 'Bearer ' + token}
    result = {}
    for supplied in ({}, {'Authorization': 'Bearer invalid'}):
        try:
            with connect(url, additional_headers=supplied, open_timeout=20):
                raise RuntimeError('unauthorized handshake was accepted')
        except InvalidStatus as exc:
            if exc.response.status_code != 403:
                raise
    result['authentication'] = 'PASS: missing/wrong token denied before upgrade'
    with connect(url, additional_headers=headers, open_timeout=20) as ws:
        ws.send(b'\r')
        marker, _ = until(ws, b'login:', b'root@debian-dev:~# ')
        if marker == b'login:':
            ws.send('root\r')
            until(ws, b'Password:')
            ws.send(password + '\r')
            marker, _ = until(ws, b'root@debian-dev:~# ', b'Login incorrect')
            if marker == b'Login incorrect':
                raise RuntimeError('guest login rejected')
        with connect(url, additional_headers=headers, open_timeout=20) as duplicate:
            try:
                duplicate.recv(timeout=20)
                raise RuntimeError('duplicate console accepted')
            except ConnectionClosed as exc:
                if exc.rcvd is None or exc.rcvd.code != 4409:
                    raise
        result['duplicate_connection'] = 'PASS: 4409; original connection retained'
        ws.send(b"printf 'MO04_%s\\n' WEBSOCKET_OK; hostname; uname -srm; tty\r")
        _, output = until(ws, b'root@debian-dev:~# ')
        if b'MO04_WEBSOCKET_OK' not in output or b'/dev/hvc0' not in output:
            raise RuntimeError('WebSocket console round trip failed')
        result['debian_io'] = {'status': 'PASS', 'output': output.decode(errors='replace')}
        ws.send(b'exit\r')
        until(ws, b'login:')
    # Close handshake can finish slightly before server-side PTY cleanup.
    for attempt in range(20):
        with connect(url, additional_headers=headers, open_timeout=20) as ws:
            try:
                ws.send(b'\r')
                until(ws, b'login:')
                result['reconnect_after_close'] = 'PASS'
                break
            except ConnectionClosed as exc:
                if exc.rcvd is None or exc.rcvd.code != 4409:
                    raise
        time.sleep(0.1)
    else:
        raise RuntimeError('console lease was not released')
    missing = base_url.rstrip('/') + '/api/v1/vms/00000000-0000-4000-8000-000000000042/console'
    with connect(missing, additional_headers=headers, open_timeout=20) as ws:
        try:
            ws.recv(timeout=20)
            raise RuntimeError('unknown VM accepted')
        except ConnectionClosed as exc:
            if exc.rcvd is None or exc.rcvd.code != 4404:
                raise
    result['unknown_vm'] = 'PASS: 4404'
    result['status'] = 'PASS'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--base-url', default='ws://127.0.0.1:8081')
    parser.add_argument('--vm-id', required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.exit(message='Use --execute for a running test server.\n')
    token = getpass.getpass('Service token: ')
    password = getpass.getpass('Debian guest password: ')
    print(json.dumps(run(args.base_url, args.vm_id, token, password), ensure_ascii=False, indent=2))
