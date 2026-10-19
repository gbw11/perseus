#!/usr/bin/env python3
"""SSH 수집이 성공하고 내용까지 검증된 경우에만 비교 증거 파일을 원자적으로 확정한다."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

REMOTE = """import hashlib, json, pathlib, sys
keys = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in pathlib.Path('/etc/ssh').glob('ssh_host_*_key.pub')}
files = {p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sys.argv[1:]}
print(json.dumps({'schema': 1, 'machine_id': pathlib.Path('/etc/machine-id').read_text().strip(),
                  'ssh_public_keys': keys, 'files': files}))
"""


def collect(host, output, files=(), command=subprocess.run):
    """실패·빈 출력·잘못된 JSON은 기존 증거를 보존하며 비교 대상으로 게시하지 않는다."""
    output = Path(output)
    if output.is_symlink() or not output.is_absolute() or not output.parent.is_dir() or output.parent.resolve() != output.parent:
        raise ValueError('absolute unlinked output with existing parent required')
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+', host) or host.startswith('-'):
        raise ValueError('invalid SSH host')
    if any(not Path(name).is_absolute() for name in files):
        raise ValueError('guest evidence file paths must be absolute')
    target = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.xenova-collect-', dir=output.parent, delete=False) as stream:
            target = Path(stream.name)
            remote = shlex.join(['python3', '-c', REMOTE, *files])
            result = command(['ssh', '-o', 'ConnectTimeout=10', '--', host, remote],
                             stdout=stream, stderr=subprocess.PIPE, timeout=45, check=False)
            if result.returncode:
                raise ValueError('SSH evidence collection failed; prior comparison file retained')
            stream.flush(); os.fsync(stream.fileno())
        data = json.loads(target.read_text())
        digest = re.compile(r'[0-9a-f]{64}\Z')
        if (not isinstance(data, dict) or data.get('schema') != 1
                or not isinstance(data.get('machine_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', data['machine_id'])
                or not isinstance(data.get('ssh_public_keys'), dict) or not data['ssh_public_keys']
                or any(not isinstance(value, str) or not digest.fullmatch(value) for value in data['ssh_public_keys'].values())
                or not isinstance(data.get('files'), dict) or set(data['files']) != set(files)
                or any(not isinstance(value, str) or not digest.fullmatch(value) for value in data['files'].values())):
            raise ValueError('incomplete guest evidence; prior comparison file retained')
        os.replace(target, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        return data
    finally:
        if target is not None:
            target.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--guest-file', action='append', default=[])
    args = parser.parse_args(argv)
    collect(args.host, args.output, args.guest_file)
    print('guest evidence saved:', args.output)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('[xenova-evidence] ERROR:', exc, file=sys.stderr)
        raise SystemExit(1)
