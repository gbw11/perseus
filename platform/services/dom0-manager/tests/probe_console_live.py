"""명시적 Linux dom0 콘솔 시험. pytest 수집 대상이 아니며 재부팅/설정 변경 없음.

console.py와 이 파일을 같은 임시 디렉터리에 복사한 뒤 실행한다.
게스트 암호는 getpass 또는 --password-stdin으로 메모리에서만 사용한다.
"""
import argparse
import asyncio
import getpass
import json
import subprocess
import sys
import time

from console import ConsoleExited, ConsoleSession


def domains():
    rows = json.loads(subprocess.check_output(['xl', 'list', '-l'], text=True))
    return {row['config']['c_info']['name']: {
        'domid': row['domid'], 'uuid': row['config']['c_info'].get('uuid')
    } for row in rows}


async def until(session, *markers):
    data = b''
    async with asyncio.timeout(25):
        while True:
            data += await session.read()
            for marker in markers:
                if marker in data:
                    return marker, data
            if len(data) > 262144:
                raise RuntimeError('unexpected console output volume')


async def run(args, password):
    before = domains()
    result = {'scope': 'direct Linux dom0 PTY / QEMU ARM64 Xen',
              'time_unix': time.time(), 'before': before, 'checks': {}}
    # Resolve name to current domid; this probe never opens the management domain.
    session = ConsoleSession(before[args.domain]['domid'])
    async with session:
        await session.write(b'\r')
        prompt = (args.domain + ':~# ').encode()
        marker, _ = await until(session, b'login:', prompt)
        if marker == b'login:':
            await session.write(b'root\r')
            await until(session, b'Password:')
            await session.write(password.encode() + b'\r')
            marker, _ = await until(session, prompt, b'Login incorrect')
            if marker != prompt:
                raise RuntimeError('documented guest login rejected')
        await session.write(b"printf 'MO04_%s\\n' PYTHON_PTY_OK; hostname; uname -srm; tty\r")
        _, data = await until(session, prompt)
        if b'MO04_PYTHON_PTY_OK' not in data or b'/dev/hvc0' not in data:
            raise RuntimeError('console command output did not match')
        result['checks']['debian_io'] = {'status': 'PASS', 'output': data.decode(errors='replace')}
        await session.write(b'exit\r')
        await until(session, b'login:')
        pid = session.pid
    result['checks']['detach'] = {'pid': pid, 'returncode': session.returncode}
    if session.returncode != 0:
        raise RuntimeError('console did not detach normally')
    for name in ('zephyr-dev', 'zephyr-backup'):
        async with ConsoleSession(before[name]['domid']) as rtos:
            try:
                data = await rtos.read(timeout=1)
                observation = data.decode(errors='replace')
            except TimeoutError:
                observation = 'no new output during 1 second; no input handler in starter'
        result['checks'][name] = {'detach_returncode': rtos.returncode,
                                  'observation': observation, 'input_output': 'NOT_VERIFIED'}
        if rtos.returncode != 0:
            raise RuntimeError(name + ' console detach failed')
    async with ConsoleSession('mo04-no-such-guest') as missing:
        data = b''
        try:
            async with asyncio.timeout(5):
                while True:
                    data += await missing.read()
        except ConsoleExited as exc:
            if exc.returncode in (None, 0):
                raise RuntimeError('missing guest must fail')
            result['checks']['missing_guest'] = {'status': 'PASS', 'returncode': exc.returncode,
                                                'output': data.decode(errors='replace')}
    result['after'] = domains()
    if result['after'] != before:
        raise RuntimeError('domain ID/UUID changed during probe')
    result['status'] = 'PASS (Debian IO; Zephyr attach/detach only)'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--domain', default='debian-dev')
    parser.add_argument('--password-stdin', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.exit(message='Use --execute to run the console probe on Linux dom0.\n')
    password = sys.stdin.readline().rstrip('\n') if args.password_stdin else getpass.getpass('Guest password: ')
    print(json.dumps(asyncio.run(run(args, password)), ensure_ascii=False, indent=2))
