#!/usr/bin/env python3
"""완료된 Zephyr 앱 빌드에서 새 배포 번들을 게시한다. 컴파일·원본 수정·다운로드는 하지 않는다."""
import argparse
import json
import re
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'platform/first-install'),
               str(ROOT / 'platform/yocto/meta-perseus/recipes-core/perseus-platform/files')]
from artifact_io import new_path, publish
from first_install_contract import uuid4
import perseus_zephyr as zephyr


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True, help='completed build/zephyr containing .config, zephyr.bin/elf/dts')
    parser.add_argument('--license', type=Path, required=True)
    parser.add_argument('--version', required=True, help='application firmware version; distinct from Zephyr version')
    parser.add_argument('--app', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--build-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    zephyr.plain(args.build_dir)
    zephyr.payload_ok(args.build_dir, config_name='.config')
    version_header = zephyr.asset(args.build_dir / 'include/generated/zephyr', 'version.h')
    if not re.search(r'^#define KERNEL_VERSION_STRING\s+"4\.4\.0"\s*$', version_header.read_text(), re.M):
        raise ValueError('completed build must use fixed Zephyr 4.4.0')
    new_path(args.output)
    if not zephyr.TOKEN.fullmatch(args.version):
        raise ValueError('invalid application firmware version')
    if args.license.is_symlink() or not args.license.is_file():
        raise ValueError('explicit regular license required')
    meta = {'schema': 1, 'guest_kind': 'zephyr', 'version': args.version,
            'source': {'zephyr_version': '4.4.0', 'app': args.app,
                       'source_revision': args.source_revision, 'build_id': args.build_id},
            'hardware_profile': zephyr.PROFILE, 'capabilities': zephyr.CAPABILITIES}
    if not all(value.strip() for value in meta['source'].values()):
        raise ValueError('firmware provenance values must not be empty')
    if not args.execute:
        print(json.dumps({'action': 'plan-firmware-bundle', 'output': str(args.output), 'metadata': meta,
                          'hardware_boot_tested': False}, indent=2))
        return
    stage = args.output.parent / ('.firmware-' + uuid4())
    stage.mkdir(mode=0o700)
    sources = {name: args.build_dir / name for name in ('zephyr.bin', 'zephyr.elf', 'zephyr.dts')}
    sources.update({'zephyr.version.h': version_header, 'zephyr.config': args.build_dir / '.config', 'LICENSE': args.license,
                    'xen.cfg.in': ROOT / 'platform/guests/zephyr/xen.cfg.in'})
    meta['files'] = {}
    for name, source in sources.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError('missing/linked input: ' + str(source))
        shutil.copyfile(source, stage / name)
        (stage / name).chmod(0o444)
        meta['files'][name] = {'sha256': zephyr.sha(stage / name), 'bytes': (stage / name).stat().st_size}
    zephyr.atomic_write(stage / 'firmware.json', json.dumps(meta, indent=2) + '\n', 0o444)
    info = zephyr.bundle_info(stage)
    publish(stage, args.output)
    print(json.dumps({'action': 'firmware-bundle', 'output': str(args.output), **info,
                      'hardware_boot_tested': False}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, zephyr.PlatformError) as exc:
        print('[perseus-firmware] ERROR:', exc, file=sys.stderr)
        sys.exit(1)
