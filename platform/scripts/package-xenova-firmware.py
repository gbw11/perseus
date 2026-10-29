#!/usr/bin/env python3
"""완료된 Zephyr 앱 빌드에서 새 배포 번들을 게시한다. 컴파일·원본 수정·다운로드는 하지 않는다."""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'platform/first-install'),
               str(ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files')]
from artifact_io import new_path, publish
from first_install_contract import uuid4
import xenova_zephyr as zephyr


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True, help='completed build/zephyr containing .config, zephyr.bin/elf/dts')
    parser.add_argument('--license', type=Path, required=True)
    parser.add_argument('--version', required=True, help='application firmware version; distinct from Zephyr version')
    parser.add_argument('--app', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--build-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--app-source', type=Path, help='application source files to fingerprint; no build or source changes')
    parser.add_argument('--networked', action='store_true', help='validated existing netfront/cooperative 16MiB profile')
    parser.add_argument('--template-id', help='publish the verified Starter or common drive catalog entry')
    parser.add_argument('--template-version', help='catalog deployment version; distinct from application firmware version')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    zephyr.plain(args.build_dir)
    profile = zephyr.NET_PROFILE if args.networked else zephyr.PROFILE
    zephyr.payload_ok(args.build_dir, config_name='.config', profile=profile)
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
            'hardware_profile': profile, 'capabilities': zephyr.NET_CAPABILITIES if args.networked else zephyr.CAPABILITIES}
    if not all(value.strip() for value in meta['source'].values()):
        raise ValueError('firmware provenance values must not be empty')
    if args.app_source is not None:
        zephyr.plain(args.app_source)
        cache = zephyr.asset(args.build_dir.parent, 'CMakeCache.txt').read_text()
        if not re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=' + re.escape(str(args.app_source)) + r'$', cache, re.M):
            raise ValueError('application source does not match completed CMake build')
        fingerprints = {}
        for path in sorted(args.app_source.rglob('*')):
            if path.is_symlink():
                raise ValueError('linked application source is forbidden')
            if path.is_file():
                fingerprints[str(path.relative_to(args.app_source))] = {'sha256': zephyr.sha(path), 'bytes': path.stat().st_size}
        if not fingerprints:
            raise ValueError('empty application source')
        meta['source']['app_files'] = fingerprints
        meta['source']['app_source_sha256'] = hashlib.sha256(json.dumps(fingerprints, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if args.template_id == 'xenova-zephyr-drive-common':
        if not args.networked or args.app != 'xenova-drive' or args.app_source != ROOT / 'platform/apps/drive/zephyr':
            raise ValueError('common drive catalog requires the verified networked drive application')
        spec = importlib.util.spec_from_file_location('drive_build_inputs', ROOT / 'platform/scripts/build-xenova-drive-app.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        digest = hashlib.sha256(json.dumps(builder.fingerprint('drive'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        lock = json.loads((ROOT / 'platform/guests/zephyr/starter.lock.json').read_text())
        match = re.search(r'^ZEPHYR_BASE:PATH=(.+)$', cache, re.M)
        if (args.source_revision != 'sha256:' + digest or args.version != '0.1.0-drive-' + digest[:12]
                or not match or subprocess.check_output(['git', '-C', match[1], 'rev-parse', 'HEAD'], text=True).strip() != lock['zephyr_commit']):
            raise ValueError('common drive catalog source/build provenance mismatch')
        meta.update(id=args.template_id, kind='zephyr-xen-firmware', template_id='zephyr-xenvm-starter-arm64',
                    template_version=args.template_version or args.version, schema_version='1.0')
        from xenova_catalog import identity
        identity(meta['template_id'], meta['template_version'])
        meta['source'].update(zephyr_commit=lock['zephyr_commit'], sdk_version=lock['sdk_version'])
    elif args.template_id is not None:
        if args.networked:
            raise ValueError('networked application candidate is not the pinned legacy Starter catalog publication')
        lock = json.loads((ROOT / 'platform/guests/zephyr/starter.lock.json').read_text())
        if (args.template_id != lock['id'] or args.version != lock['version'] or args.app != lock['app']
                or meta['source'].get('app_files') != lock['app_files']
                or meta['source'].get('app_source_sha256') != lock['app_source_sha256']
                or args.source_revision != 'sha256:' + lock['app_source_sha256']):
            raise ValueError('catalog publication requires the pinned Starter source/version')
        match = re.search(r'^ZEPHYR_BASE:PATH=(.+)$', cache, re.M)
        if not match or subprocess.check_output(['git', '-C', match[1], 'rev-parse', 'HEAD'], text=True).strip() != lock['zephyr_commit']:
            raise ValueError('Starter build does not reference the pinned Zephyr commit')
        receipt_path = zephyr.asset(args.build_dir.parent, 'xenova-build-receipt.json')
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('schema') != 1 or any(receipt.get(key) != lock[key]
                for key in ('id', 'version', 'app_files', 'app_source_sha256', 'zephyr_commit', 'sdk_version'))):
            raise ValueError('Starter build receipt differs from pinned source/version')
        for name in ('zephyr.bin', 'zephyr.elf', 'zephyr.dts', '.config', 'include/generated/zephyr/version.h'):
            path = zephyr.asset(args.build_dir, name)
            if receipt.get('payloads', {}).get(name) != {'sha256': zephyr.sha(path), 'bytes': path.stat().st_size}:
                raise ValueError('Starter binary differs from completed build receipt')
        for name in ('zephyr.bin', 'zephyr.elf'):
            payload = (args.build_dir / name).read_bytes()
            if b'Xenova / Zephyr / Xen' not in payload or b'Perseus / Zephyr / Xen' in payload:
                raise ValueError('Starter payload does not contain the current Xenova banner')
        meta['source']['build_receipt_sha256'] = zephyr.sha(receipt_path)
        meta.update(id=lock['id'], kind='zephyr-xen-firmware', template_id='zephyr-xenvm-starter-arm64',
                    template_version=args.template_version or args.version, schema_version='1.0')
        from xenova_catalog import identity
        identity(meta['template_id'], meta['template_version'])
        meta['source'].update(zephyr_commit=lock['zephyr_commit'], sdk_version=lock['sdk_version'])
    if not args.execute:
        print(json.dumps({'action': 'plan-firmware-bundle', 'output': str(args.output), 'metadata': meta,
                          'hardware_boot_tested': False}, indent=2))
        return
    stage = args.output.parent / ('.firmware-' + uuid4())
    stage.mkdir(mode=0o700)
    sources = {name: args.build_dir / name for name in ('zephyr.bin', 'zephyr.elf', 'zephyr.dts')}
    sources.update({'zephyr.version.h': version_header, 'zephyr.config': args.build_dir / '.config', 'LICENSE': args.license,
                    'xen.cfg.in': ROOT / ('platform/guests/zephyr/network-xen.cfg.in' if args.networked else
                                         'platform/guests/zephyr/xen.cfg.in')})
    if args.networked:
        sources.update({'zephyr.map': args.build_dir / 'zephyr.map',
                        'XEN-ABI-LICENSE': ROOT / 'platform/guests/zephyr/xen-store/LICENSES/MIT-Xen'})
    # The linked Publisher carries a pre-link input digest, not a self hash.
    build_ids=set(re.findall(rb'xenova-build:(sha256:[0-9a-f]{64})',sources['zephyr.bin'].read_bytes()))
    if len(build_ids)>1:
        raise ValueError('ambiguous embedded build identity')
    if build_ids:
        meta['source']['build_id']=next(iter(build_ids)).decode('ascii')
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
    except (OSError, ValueError, subprocess.SubprocessError, zephyr.PlatformError) as exc:
        print('[xenova-firmware] ERROR:', exc, file=sys.stderr)
        sys.exit(1)
