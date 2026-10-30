"""Opt-in QEMU acceptance probe for the public Spring console WebSocket.

Never run against a production board. --allow-vm-restart authorizes stop/start of
one explicitly named guest. Credentials are read from stdin, never argv or output.
Optional lab commands affect only the isolated preview supervisor, not system services.
"""
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

SNAPSHOT = '''import json,os,pathlib,subprocess
rows=json.loads(subprocess.check_output(['xl','list','-l']))
guests={r['config']['c_info']['name']:{'domid':r['domid'],'uuid':r['config']['c_info'].get('uuid')} for r in rows}
consoles=[]
servers=[]
for d in pathlib.Path('/proc').iterdir():
 if not d.name.isdecimal(): continue
 try:
  args=(d/'cmdline').read_bytes().decode(errors='replace').strip('\\0').split('\\0')
  program=pathlib.Path(args[0]).name
  if program=='xenconsole' or (program=='xl' and 'console' in args):
   consoles.append({'pid':int(d.name),'argv':args})
  if 'uvicorn' in args and b'/tmp/mo04-step5-' in (d/'environ').read_bytes():
   servers.append({'pid':int(d.name),'fd_count':len(list((d/'fd').iterdir()))})
 except (OSError,IndexError): pass
print(json.dumps({'guests':guests,'console_processes':consoles,'servers':servers}))
'''


class Probe:
    def __init__(self, args, credentials):
        self.args = args
        self.credentials = credentials
        self.ssh = ['ssh', '-F', args.ssh_config, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', args.ssh_alias]
        self.result = {'scope': 'isolated QEMU; Spring public WS -> real Xen guest',
                       'started_unix': time.time(), 'cases': []}
        self.vm_id = args.vm_id
        self.base = args.base_url.rstrip('/')
        self.ws_base = self.base.replace('http://', 'ws://').replace('https://', 'wss://')

    def remote(self, command, timeout=30):
        return subprocess.run(self.ssh + [command], text=True, capture_output=True, timeout=timeout)

    def snapshot(self):
        output = self.remote('python3 -c ' + shlex.quote(SNAPSHOT))
        if output.returncode:
            raise RuntimeError('snapshot failed: ' + output.stderr[:300])
        return json.loads(output.stdout)

    def request(self, path, body=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(self.base + path,
            data=json.dumps(body or {}).encode() if body is not None else None, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def login(self):
        return self.request('/api/v1/auth/login', {key: self.credentials[key] for key in ('username', 'password')})['access_token']

    def open(self, token=None, vm_id=None, auth=True):
        ws = connect(self.ws_base + f'/ws/v1/vms/{vm_id or self.vm_id}/console', open_timeout=20, close_timeout=2).__enter__()
        if auth:
            ws.send(json.dumps({'type': 'auth', 'access_token': token or self.login()}))
            first = ws.recv(timeout=20)
            if not isinstance(first, str) or json.loads(first).get('type') != 'connected':
                ws.close()
                raise RuntimeError('expected connected control frame')
        return ws

    def until(self, ws, *markers, timeout=25):
        data = b''
        query_tail = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            if not isinstance(chunk, bytes):
                raise RuntimeError('unexpected non-binary console output')
            # agetty probes cursor position; emulate the terminal response, not a shell command.
            scan = query_tail + chunk
            for _ in range(scan.count(b'\x1b[6n')):
                ws.send(b'\x1b[24;80R')
            query_tail = scan[-3:]
            data += chunk
            for marker in markers:
                if marker in data:
                    return marker, data
            if len(data) > 262144:
                raise RuntimeError('unexpected console output volume')
        raise TimeoutError('expected console output not observed')

    def guest_login(self, ws, timeout=120):
        ws.send(b'\r')
        prompt = b'root@debian-dev:~# '
        marker, _ = self.until(ws, b'debian-dev login: ', prompt, b'Password:', timeout=timeout)
        if marker == b'debian-dev login: ':
            ws.send(b'root\r')
            self.until(ws, b'Password:')
        if marker != prompt:
            ws.send((self.credentials['guest_password'] + '\r').encode())
            marker, _ = self.until(ws, prompt, b'Login incorrect')
            if marker != prompt:
                raise RuntimeError('guest credential rejected')

    def closed(self, ws, codes, timeout=20):
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                ws.recv(timeout=max(0.1, deadline - time.monotonic()))
        except ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd else 1006
            if code not in codes:
                raise RuntimeError(f'unexpected close code: {code}') from exc
            return code
        raise TimeoutError('socket did not close')

    def clean(self, timeout=12):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            snap = self.snapshot()
            if not snap['console_processes']:
                return {'cleanup_seconds': round(time.monotonic() - start, 3), 'snapshot': snap}
            time.sleep(0.15)
        raise RuntimeError('console processes remain: ' + json.dumps(snap['console_processes']))

    def control(self, operation):
        directory = Path(self.args.lab_control_directory)
        request_id = str(uuid4())
        path = directory / 'step5-lab-command.json'
        response = directory / 'step5-lab-response.json'
        temporary = directory / f'.step5-command-{request_id}.json'
        temporary.write_text(json.dumps({'op': operation, 'request_id': request_id}))
        temporary.replace(path)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if response.exists():
                try:
                    result = json.loads(response.read_text())
                    if result.get('request_id') == request_id:
                        if result.get('status') != 'OK' or result.get('result', {}).get('error'):
                            raise RuntimeError('lab operation failed: ' + str(result))
                        return result
                except json.JSONDecodeError:
                    pass
            time.sleep(0.1)
        raise TimeoutError('lab operation timed out: ' + operation)

    def case(self, name, action):
        if self.args.only and name.split()[0] not in self.args.only.split(','):
            return
        start = time.monotonic()
        row = {'name': name}
        try:
            row.update(status='PASS', evidence=action())
        except Exception as exc:
            row.update(status='FAIL', error=str(exc)[:2000])
        row['seconds'] = round(time.monotonic() - start, 3)
        self.result['cases'].append(row)
        Path(self.args.output).write_text(json.dumps(self.result, ensure_ascii=False, indent=2))
        print(name + ': ' + row['status'], flush=True)

    def auth_checks(self):
        results = {}
        for mode in ('invalid-token', 'binary-before-auth', 'silent-auth-timeout'):
            with self.open(auth=False) as ws:
                if mode == 'invalid-token':
                    ws.send(json.dumps({'type': 'auth', 'access_token': 'invalid'}))
                elif mode == 'binary-before-auth':
                    ws.send(b'\r')
                results[mode] = self.closed(ws, {4401}, timeout=15)
        results['cleanup'] = self.clean()
        return results

    def invalid_targets(self):
        results = {}
        token = self.login()
        for identifier, code in (('not-a-uuid', 4400), ('00000000-0000-0000-0000-000000000000', 4400),
                                  ('00000000-0000-4000-8000-000000000042', 4404)):
            with self.open(auth=False, vm_id=identifier) as ws:
                ws.send(json.dumps({'type': 'auth', 'access_token': token}))
                results[identifier] = self.closed(ws, {code})
        results['cleanup'] = self.clean()
        return results

    def io_and_isolation(self):
        token = self.login()
        baseline = self.snapshot()['guests']
        with self.open(token) as linux:
            self.guest_login(linux)
            others = []
            try:
                for name in ('zephyr-dev', 'zephyr-backup'):
                    others.append(self.open(token, baseline[name]['uuid']))
                marker = 'ISOLATION_' + uuid4().hex[:12]
                linux.send(("printf '%s_%s\\n' MO04 " + marker + "; hostname; tty\r").encode())
                _, output = self.until(linux, b'root@debian-dev:~# ')
                if ('MO04_' + marker).encode() not in output or b'/dev/hvc0' not in output:
                    raise RuntimeError('Debian command output mismatch')
                for ws in others:
                    try:
                        other_output = ws.recv(timeout=0.5)
                        if marker.encode() in other_output:
                            raise RuntimeError('console output crossed VM boundary')
                    except TimeoutError:
                        pass
                processes = self.snapshot()['console_processes']
                if len(processes) != 3:
                    raise RuntimeError('expected three independently attached native console processes')
                with self.open(token, auth=False) as duplicate:
                    duplicate.send(json.dumps({'type': 'auth', 'access_token': token}))
                    self.closed(duplicate, {4409})
                linux.send(b"printf 'MO04_%s\\n' ORIGINAL_ALIVE\r")
                _, alive = self.until(linux, b'root@debian-dev:~# ')
                if b'MO04_ORIGINAL_ALIVE' not in alive:
                    raise RuntimeError('duplicate attempt disrupted original console')
                linux.send(b'exit\r')
                self.until(linux, b'debian-dev login: ')
            finally:
                for ws in others:
                    ws.close()
        return {'native_console_processes': processes, 'debian_output': output.decode(errors='replace'),
                'zephyr_input': 'NOT_VERIFIED: starter has no input handler', 'cleanup': self.clean()}

    def revoked_token(self):
        token = self.login()
        with self.open(token) as ws:
            ws.send(b'\r')
            self.until(ws, b'debian-dev login: ')
            self.request('/api/v1/auth/logout', {}, token)
            ws.send(b'\r')
            code = self.closed(ws, {4401})
        with self.open(auth=False) as denied:
            denied.send(json.dumps({'type': 'auth', 'access_token': token}))
            self.closed(denied, {4401})
        return {'revoked_input_close': code, 'reconnect_with_revoked_token': 'denied', 'cleanup': self.clean()}

    def expired_idle(self):
        # Test-only signing key belongs to the isolated development backend, never a production key.
        original = self.login()
        header, payload, _ = original.split('.')
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        claims.update(iat=int(time.time()), exp=int(time.time()) + 5, jti=str(uuid4()))
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b'=').decode()
        message = (header + '.' + encoded).encode()
        signature = base64.urlsafe_b64encode(hmac.new(self.credentials['lab_jwt_secret'].encode(), message, hashlib.sha256).digest()).rstrip(b'=')
        token = message.decode() + '.' + signature.decode()
        started = time.monotonic()
        with self.open(token) as ws:
            code = self.closed(ws, {4401}, timeout=12)
        return {'idle_expiry_close': code, 'elapsed_seconds': round(time.monotonic() - started, 3), 'cleanup': self.clean()}

    def abrupt_client(self):
        with self.open() as ws:
            ws.send(b'\r')
            self.until(ws, b'debian-dev login: ')
            ws.socket.shutdown(socket.SHUT_RDWR)
            ws.socket.close()
        cleaned = self.clean()
        with self.open() as again:
            again.send(b'\r')
            self.until(again, b'debian-dev login: ')
        return {'after_tcp_abort': cleaned, 'reconnect': 'PASS', 'final_cleanup': self.clean()}

    def repeated_connections(self):
        before = self.snapshot()
        token = self.login()
        for _ in range(12):
            with self.open(token) as ws:
                ws.send(b'\r')
                self.until(ws, b'debian-dev login: ')
            self.clean()
        after = self.clean()['snapshot']
        before_fds = sum(server['fd_count'] for server in before['servers'])
        after_fds = sum(server['fd_count'] for server in after['servers'])
        if after_fds > before_fds + 4:
            raise RuntimeError(f'server descriptor growth: {before_fds} -> {after_fds}')
        return {'cycles': 12, 'server_fds_before': before_fds, 'server_fds_after': after_fds,
                'native_console_processes_after': after['console_processes']}

    def upstream_crash(self):
        before = self.snapshot()['guests']
        ws = self.open()
        try:
            ws.send(b'\r')
            self.until(ws, b'debian-dev login: ')
            self.control('upstream-crash')
            code = self.closed(ws, {1011, 1001, 1006})
            cleaned = self.clean()
        finally:
            ws.close()
            self.control('upstream-start')
        with self.open() as again:
            again.send(b'\r')
            self.until(again, b'debian-dev login: ')
        after = self.clean()['snapshot']['guests']
        if before != after:
            raise RuntimeError('console server crash changed guest identity')
        return {'close_code': code, 'cleanup_after_SIGKILL': cleaned, 'restored': 'PASS', 'guests_unchanged': True}

    def immediate_reconnect(self):
        token = self.login()
        for _ in range(12):
            with self.open(token) as ws:
                ws.send(json.dumps({'type': 'disconnect'}))
                self.closed(ws, {1000})
            # No sleep, polling for process cleanup, or retry between connections.
        return {'cycles': 12, 'between_connections': 'server close acknowledgement only',
                'cleanup': self.clean()}

    def backend_crash(self):
        before = self.snapshot()['guests']
        ws = self.open()
        try:
            ws.send(b'\r')
            self.until(ws, b'debian-dev login: ')
            self.control('backend-crash')
            code = self.closed(ws, {1006, 1001, 1011})
            cleaned = self.clean()
        finally:
            ws.close()
            self.control('backend-start')
        with self.open() as again:
            again.send(b'\r')
            self.until(again, b'debian-dev login: ')
        after = self.clean()['snapshot']['guests']
        if before != after:
            raise RuntimeError('Spring crash changed guest identity')
        return {'close_code': code, 'cleanup_after_SIGKILL': cleaned, 'restored': 'PASS', 'guests_unchanged': True}

    def vm_stop_start(self):
        before = self.snapshot()['guests']
        if before[self.args.vm_name]['uuid'] != self.vm_id:
            raise RuntimeError('target name and UUID do not match; refusing VM mutation')
        ws = self.open()
        try:
            ws.send(b'\r')
            self.until(ws, b'debian-dev login: ')
            stopped = self.remote('xenova-vm stop ' + shlex.quote(self.args.vm_name), timeout=110)
            if stopped.returncode:
                raise RuntimeError('graceful VM stop failed: ' + stopped.stderr[:500])
            code = self.closed(ws, {1000, 1001, 1011, 4404, 4409}, timeout=20)
            if self.args.vm_name in self.snapshot()['guests']:
                raise RuntimeError('VM is still running after graceful stop')
            with self.open(auth=False) as denied:
                denied.send(json.dumps({'type': 'auth', 'access_token': self.login()}))
                self.closed(denied, {4409})
        finally:
            ws.close()
            if self.args.vm_name not in self.snapshot()['guests']:
                started = self.remote('xenova-vm start ' + shlex.quote(self.args.vm_name), timeout=110)
                if started.returncode:
                    raise RuntimeError('VM restoration failed: ' + started.stderr[:500])
        after = self.snapshot()['guests']
        for name in ('zephyr-dev', 'zephyr-backup'):
            if before[name] != after[name]:
                raise RuntimeError('Zephyr guest changed during Debian test')
        if after[self.args.vm_name]['uuid'] != self.vm_id or after[self.args.vm_name]['domid'] == before[self.args.vm_name]['domid']:
            raise RuntimeError('expected new domid with preserved UUID')
        with self.open() as restored:
            self.guest_login(restored)
            restored.send(b"printf 'MO04_%s\\n' RESTORED_AFTER_RESTART; hostname; tty\r")
            _, output = self.until(restored, b'root@debian-dev:~# ', timeout=45)
            if b'MO04_RESTORED_AFTER_RESTART' not in output:
                raise RuntimeError('restored console did not execute command')
            restored.send(b'exit\r')
            self.until(restored, b'debian-dev login: ')
        return {'old_close_code': code, 'before': before, 'after': after,
                'restored_console_output': output.decode(errors='replace'), 'cleanup': self.clean()}

    def run(self):
        baseline = self.snapshot()
        self.result['before'] = baseline
        if baseline['console_processes']:
            raise RuntimeError('console already in use; refusing disruptive acceptance probe')
        if baseline['guests'].get(self.args.vm_name, {}).get('uuid') != self.vm_id:
            raise RuntimeError('VM target identity mismatch')
        self.case('AUTH-01 invalid / missing / silent authentication', self.auth_checks)
        self.case('AUTH-02 invalid / dom0 / unknown VM target', self.invalid_targets)
        self.case('IO-01 Debian IO + three VM separation + duplicate rejection', self.io_and_isolation)
        self.case('AUTH-03 revoked JWT input and reconnect denial', self.revoked_token)
        if self.credentials.get('lab_jwt_secret'):
            self.case('AUTH-04 real signed short-lived JWT idle expiry', self.expired_idle)
        self.case('LIFE-01 abrupt TCP client disconnect', self.abrupt_client)
        self.case('LIFE-02 repeated attach/detach resource cleanup', self.repeated_connections)
        self.case('LIFE-03 immediate reconnect after acknowledged detach', self.immediate_reconnect)
        if self.args.lab_control_directory:
            self.case('FAULT-01 FastAPI SIGKILL and recovery', self.upstream_crash)
            self.case('FAULT-02 Spring SIGKILL and recovery', self.backend_crash)
        if self.args.allow_vm_restart:
            self.case('FAULT-03 Debian graceful stop + new domid + recovery', self.vm_stop_start)
        self.result['after'] = self.snapshot()
        self.result['status'] = 'PASS' if all(row['status'] == 'PASS' for row in self.result['cases']) else 'FAIL'
        Path(self.args.output).write_text(json.dumps(self.result, ensure_ascii=False, indent=2))
        return self.result['status'] == 'PASS'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-vm-restart', action='store_true')
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--vm-id', required=True)
    parser.add_argument('--vm-name', default='debian-dev')
    parser.add_argument('--ssh-config', required=True)
    parser.add_argument('--ssh-alias', required=True)
    parser.add_argument('--lab-control-directory')
    parser.add_argument('--output', required=True)
    parser.add_argument('--only', default='', help='comma-separated case IDs for targeted reruns, e.g. FAULT-03')
    args = parser.parse_args()
    if not args.execute:
        parser.exit(message='Use --execute; --allow-vm-restart explicitly permits the named guest stop/start.\n')
    credentials = json.loads(sys.stdin.readline())
    sys.exit(0 if Probe(args, credentials).run() else 1)
