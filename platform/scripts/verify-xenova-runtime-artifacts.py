#!/usr/bin/env python3
"""재빌드된 dom0에서 실행할 실제 xl 파서·QEMU 옵션 검사. 게스트 생성/DTB 생성/부팅 검사가 아니다."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
sys.path[:0] = [str(FILES), str(ROOT / 'platform/first-install'), '/usr/lib/xenova']
from xenova_vm import PlatformError, guest_kind, run


def parsed_config(record, parsed, directory):
    """xl이 해석한 결과를 대조한다. 문자열에 vdev가 있다는 이유만으로 통과하지 않는다."""
    if parsed.get('c_info', {}).get('uuid') != record['uuid'] or parsed['c_info'].get('name') != record['name']:
        raise PlatformError('xl parsed identity mismatch')
    build = parsed.get('b_info', {})
    if build.get('target_memkb') != record['memory_mib'] * 1024 or build.get('max_vcpus') != record['vcpus']:
        raise PlatformError('xl parsed resource mismatch')
    disks, nics = parsed.get('disks') or [], parsed.get('nics') or []
    if guest_kind(record) == 'linux':
        if len(disks) != 1 or len(nics) != 1:
            raise PlatformError('Linux requires one parsed disk and VIF')
        expected = {'pdev_path': str(directory / 'root.raw'), 'format': 'raw',
                    'vdev': 'xvda', 'readwrite': 1, 'backend': 'qdisk'}
        if any(disks[0].get(k) != value for k, value in expected.items()):
            raise PlatformError('xl parsed Linux disk contract mismatch: ' + json.dumps(disks))
        if nics[0].get('mac') != record['mac']:
            raise PlatformError('xl parsed Linux MAC mismatch')
    else:
        active = record['active_firmware']
        kernel = directory / 'firmware' / active['revision'] / 'zephyr.bin'
        if (disks or nics or build.get('kernel') != str(kernel) or build.get('ramdisk')
                or build.get('arch_arm', {}).get('gic_version') != 'v2'):
            raise PlatformError('xl parsed Zephyr firmware/GIC/disk/VIF mismatch')
    return True


def qemu_checks(qemu):
    """none 머신에서 VNC 비활성 상태를 QMP로 확인하고 xenpv 옵션 인식을 별도로 검사한다."""
    args = [qemu, '-vnc', 'none', '-display', 'none', '-nographic',
            '-machine', 'none', '-S', '-nodefaults', '-monitor', 'none', '-serial', 'none', '-qmp', 'stdio']
    messages = [{'execute': 'qmp_capabilities', 'id': 1},
                {'execute': 'query-vnc', 'id': 2}, {'execute': 'quit', 'id': 3}]
    p = subprocess.run(args, input=''.join(json.dumps(m) + '\n' for m in messages),
                       text=True, capture_output=True, timeout=15, check=False)
    if p.returncode:
        raise PlatformError('QEMU option/QMP check failed: ' + p.stderr)
    rows = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    replies = {row.get('id'): row for row in rows if 'id' in row}
    if replies.get(2, {}).get('return', {}).get('enabled') is not False:
        raise PlatformError('QEMU did not confirm VNC disabled')
    # help는 xenpv를 초기화하지 않는다. 실제 backend 실행은 libxl의 VM 기동으로 확인해야 한다.
    machine = run([qemu, '-vnc', 'none', '-display', 'none', '-nographic', '-machine', 'xenpv,help'])
    if 'xenpv' not in machine.lower():
        raise PlatformError('QEMU xenpv machine properties unavailable')
    return {'command': args, 'qmp': rows, 'xenpv_properties': machine,
            'integration_boot_tested': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='unmodified generated /run/xenova/ID.cfg')
    parser.add_argument('--instance-json', type=Path, required=True)
    parser.add_argument('--package-manifest', type=Path, required=True, help='actual new image package manifest')
    parser.add_argument('--qemu', default='qemu-system-i386')
    args = parser.parse_args(argv)
    manifest = args.package_manifest.read_text()
    if (not re.search(r'^xen-tools-xl\s+\S+\s+4\.21\+stable', manifest, re.M)
            or not re.search(r'^qemu-system-i386\s+\S+\s+10\.2\.0', manifest, re.M)):
        raise PlatformError('expected frozen Xen 4.21+stable and QEMU 10.2.0 package manifest')
    record = json.loads(args.instance_json.read_text())
    # 설정 파일을 그대로 전달한다. 뒤에 disk/vdev 등의 명령행 보정을 붙이지 않는다.
    parsed = json.loads(run(['xl', '-N', 'create', str(args.config)], timeout=30))
    parsed_config(record, parsed, args.instance_json.parent)
    qemu = shutil.which(args.qemu)
    xl = shutil.which('xl')
    if not qemu or not xl:
        raise PlatformError('installed xl/QEMU binaries required')
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    print(json.dumps({'config_sha256': digest(args.config), 'xl_path': xl, 'xl_sha256': digest(xl),
                      'qemu_path': qemu, 'qemu_sha256': digest(qemu), 'parsed_config': parsed,
                      'qemu_checks': qemu_checks(qemu), 'xl_parser_passed': True,
                      'not_tested': ['libxl-QEMU qdisk backend integration', 'guest DTB generation',
                                     'Zephyr/Linux boot', 'application readiness']}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, PlatformError, subprocess.SubprocessError) as exc:
        print('[xenova-runtime-artifacts] ERROR:', exc, file=sys.stderr)
        sys.exit(1)
