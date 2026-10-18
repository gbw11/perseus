#!/usr/bin/env python3
"""새 Linux 게시본의 manifest·ext4·게스트 내부 Zenova/SSH/신원 준비 상태를 읽기 전용 검사한다."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/first-install'))
spec = importlib.util.spec_from_file_location('zenova_linux_template_verify', ROOT / 'platform/first-install/assemble.py')
assembly = importlib.util.module_from_spec(spec); spec.loader.exec_module(assembly)


def verify(directory, expected):
    directory = Path(directory)
    meta = json.loads((directory / 'template.json').read_text())
    info = assembly.template_info(directory, meta['id'], expected)
    if meta.get('platform') != 'zenova' or not meta.get('version'):
        raise ValueError('current default requires a versioned Zenova preparation')
    raw = directory / 'root.raw'
    fsck = subprocess.run(['e2fsck', '-fn', str(raw)], text=True, capture_output=True)
    if fsck.returncode != 0:
        raise ValueError('read-only filesystem check failed: ' + fsck.stdout + fsck.stderr)
    fields = dict(line.split('=', 1) for line in subprocess.check_output(
        ['blkid', '-p', '-o', 'export', str(raw)], text=True).splitlines() if '=' in line)
    if fields.get('TYPE') != 'ext4' or fields.get('LABEL') != 'zenova-root':
        raise ValueError('wrong Linux template filesystem/label')
    def query(action, path, *, absent=False):
        result = subprocess.run(['debugfs', '-R', action + ' ' + path, str(raw)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('debugfs query failed: ' + path)
        missing = 'File not found by ext2_lookup' in result.stderr
        if absent:
            if not missing:
                raise ValueError('legacy template asset still exists: ' + path)
        elif missing or any(v in result.stderr.lower() for v in ('error', 'bad magic', 'not found')):
            raise ValueError('template inspection failed: ' + path)
        if action == 'stat' and not absent and not re.search(r'Inode:\s+\d+', result.stdout):
            raise ValueError('template stat was not confirmed: ' + path)
        return result.stdout
    if query('cat', '/etc/zenova/template-prepared').strip() != '1':
        raise ValueError('Zenova preparation marker missing')
    for path in ('/etc/perseus', '/usr/lib/perseus', '/etc/ssh/perseus-auth.conf'):
        query('stat', path, absent=True)
    query('stat', '/etc/machine-id')
    if query('cat', '/etc/machine-id').strip():
        raise ValueError('template machine-id must be unassigned')
    os_release = query('cat', '/usr/lib/os-release').replace('"', '')
    if 'ID=' + meta['source']['distribution'] not in os_release.splitlines():
        raise ValueError('distribution identity changed')
    ssh_files = query('ls', '/etc/ssh')
    if 'ssh_host_' in ssh_files:
        raise ValueError('template contains shared SSH host keys')
    config = query('cat', '/etc/ssh/sshd_config')
    if config.splitlines()[0] != 'Include /etc/ssh/zenova-auth.conf' or 'perseus' in config.lower():
        raise ValueError('SSH Include does not use current Zenova policy')
    query('stat', '/etc/systemd/system/multi-user.target.wants/ssh.service')
    return {'id': meta['id'], 'version': meta['version'], 'manifest_sha256': expected,
            'root_sha256': meta['root_sha256'], 'preparation_source_sha256': meta['preparation']['source_sha256'],
            'logical_bytes': info['logical_bytes'], 'allocated_bytes': info['allocated_bytes'],
            'label': fields['LABEL'], 'filesystem_check_exit': fsck.returncode,
            'zenova_layout': True, 'shared_guest_identity_absent': True,
            'guest_boot_tested': False, 'ssh_listener_tested': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(verify(args.template, args.sha256), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        raise SystemExit(str(exc))
