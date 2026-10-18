#!/usr/bin/env python3
"""고정 Zephyr/SDK와 현재 Starter 소스로 새 build를 만들고 게시 검증용 영수증을 쓴다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/first-install'))
from artifact_io import new_path, sha, regular


def fingerprint(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('linked Starter source is forbidden')
        if path.is_file():
            result[str(path.relative_to(directory))] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zephyr-source', type=Path, required=True)
    parser.add_argument('--toolchain-prefix', required=True)
    parser.add_argument('--toolchain-archive', type=Path, required=True)
    parser.add_argument('--python-tools', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    new_path(args.build_dir)
    for path in (args.zephyr_source, args.python_tools, args.toolchain_archive):
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError('canonical source/tools/archive paths required')
    app = ROOT / 'platform/guests/zephyr/starter-app'
    lock = json.loads((app.parent / 'starter.lock.json').read_text())
    upstream = json.loads((app.parent / 'source.lock.json').read_text())
    files = fingerprint(app)
    if files != lock['app_files']:
        raise ValueError('Starter source differs from lock; assign the intended version and refresh source hashes')
    if sha(regular(args.toolchain_archive)) != upstream['toolchain_sha256']:
        raise ValueError('fixed SDK archive digest mismatch')
    head = subprocess.check_output(['git', '-C', str(args.zephyr_source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != lock['zephyr_commit'] or subprocess.check_output(
            ['git', '-C', str(args.zephyr_source), 'status', '--porcelain'], text=True):
        raise ValueError('Zephyr source is not the pinned clean commit')
    compiler = regular(Path(args.toolchain_prefix + 'gcc'))
    env = {**os.environ, 'ZEPHYR_BASE': str(args.zephyr_source),
           'ZEPHYR_TOOLCHAIN_VARIANT': 'cross-compile', 'CROSS_COMPILE': args.toolchain_prefix,
           'PYTHONPATH': str(args.python_tools), 'PATH': str(args.python_tools / 'bin') + ':' + os.environ['PATH'],
           'XDG_CACHE_HOME': str(args.build_dir.parent / 'tool-cache')}
    commands = [[str(args.python_tools / 'bin/cmake'), '-S', str(app), '-B', str(args.build_dir),
                 '-GNinja', '-DBOARD=xenvm', '-DPython3_EXECUTABLE=/usr/bin/python3',
                 '-DWEST=' + str(args.python_tools / 'bin/west')],
                [str(args.python_tools / 'bin/cmake'), '--build', str(args.build_dir), '-j', '4']]
    print(json.dumps({'commands': commands, 'app_files': files, 'version': lock['version'],
                      'executes_build': args.execute}, indent=2), flush=True)
    if not args.execute:
        return
    for command in commands:
        subprocess.run(command, env=env, check=True)
    if fingerprint(app) != files:
        raise ValueError('Starter source changed during build; no receipt issued')
    payloads = {}
    for name in ('zephyr.bin', 'zephyr.elf', 'zephyr.dts', '.config', 'include/generated/zephyr/version.h'):
        path = regular(args.build_dir / 'zephyr' / name)
        payloads[name] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    receipt = {'schema': 1, 'id': lock['id'], 'version': lock['version'], 'app_files': files,
               'app_source_sha256': lock['app_source_sha256'], 'zephyr_commit': head,
               'sdk_version': lock['sdk_version'], 'sdk_archive_sha256': upstream['toolchain_sha256'],
               'compiler_sha256': sha(compiler), 'payloads': payloads}
    (args.build_dir / 'zenova-build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
