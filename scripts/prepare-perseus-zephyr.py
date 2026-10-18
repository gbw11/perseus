#!/usr/bin/env python3
"""완료된 공식 xenvm 빌드를 새 불변 RTOS 템플릿으로 게시한다. 기본은 계획만 출력한다.

외부 도구 설치·다운로드·VM 시작·SD 접근은 하지 않는다. 실제 빌드 절차는 Zephyr README를 따른다.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/first-install'))
from artifact_io import checked, new_path, publish, regular, sha
spec = importlib.util.spec_from_file_location('perseus_zephyr_template', ROOT / 'platform/guests/zephyr/template.py')
firmware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firmware)


def verify_source(source, spdx, toolchain):
    """공식 tag/commit·SPDX 파일별 SHA1·SDK SHA256을 검사한다. 서버 서명 검증은 별도 증거다."""
    lock = firmware.source_lock()
    checked(spdx, lock['spdx_sha256'])
    checked(toolchain, lock['toolchain_sha256'])
    for ref, expected in [('HEAD', lock['commit']), ('refs/tags/' + lock['tag'], lock['tag_object'])]:
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', ref], text=True).strip()
        if actual != expected:
            raise ValueError('Zephyr git source differs from frozen release')
    if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True):
        raise ValueError('Zephyr source has local changes')
    count, name = 0, None
    for line in spdx.read_text().splitlines():
        if line.startswith('FileName: ./'):
            name = line[len('FileName: ./'):]
        elif line.startswith('FileChecksum: SHA1: '):
            if name is None or Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('unsafe SPDX source path')
            path = regular(source / name)
            if not path.resolve().is_relative_to(source) or hashlib.sha1(path.read_bytes()).hexdigest() != line.split()[-1]:
                raise ValueError('upstream SPDX source checksum mismatch: ' + name)
            count += 1
    if count != 59501:
        raise ValueError('unexpected upstream SPDX source file coverage')
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('source', 'build-dir', 'spdx', 'toolchain-archive', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    for path in (args.source, args.build_dir, args.spdx, args.toolchain_archive):
        if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
            raise ValueError('explicit canonical input paths required')
    new_path(args.output)
    if any(args.output.is_relative_to(p) for p in (args.source, args.build_dir)):
        raise ValueError('output must be separate from source/build')
    count = verify_source(args.source, args.spdx, args.toolchain_archive)
    build = args.build_dir / 'zephyr'
    inputs = {'zephyr.bin': build / 'zephyr.bin', 'zephyr.elf': build / 'zephyr.elf',
              'zephyr.config': build / '.config', 'zephyr.dts': build / 'zephyr.dts',
              'xen.cfg.in': firmware.HERE / 'xen.cfg.in', 'LICENSE': args.source / 'LICENSE'}
    # 다른 빌드와 혼동하지 않고 입력이 변경되면 게시하지 않는다.
    cache = (args.build_dir / 'CMakeCache.txt').read_text()
    if ('ZEPHYR_BASE:PATH=' + str(args.source)) not in cache or ('samples/synchronization') not in cache:
        raise ValueError('build cache does not reference the selected upstream sample/source')
    fingerprints = {name: {'sha256': sha(regular(path)), 'bytes': path.stat().st_size} for name, path in inputs.items()}
    meta = {'schema': 1, 'id': firmware.source_lock()['id'], 'kind': firmware.source_lock()['kind'],
            'source': firmware.source_lock(), 'files': fingerprints, 'source_files_verified': count,
            'memory_mib': 16, 'vcpus': 1, 'gic_version': 'v2', 'autostart': False,
            'network': 'unsupported', 'root_disk': None, 'authentication': 'PV console; no Linux accounts or SSH',
            'runtime_hardware_verified': False}
    print(json.dumps({'action': 'publish-template' if args.execute else 'plan', 'output': str(args.output), 'metadata': meta}, indent=2))
    if not args.execute:
        return
    stage = args.output.parent / ('.zephyr-preparing-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    for name, path in inputs.items():
        shutil.copyfile(path, stage / name)
        (stage / name).chmod(0o444)
    (stage / 'template.json').write_text(json.dumps(meta, indent=2) + '\n')
    (stage / 'template.json').chmod(0o444)
    firmware.info(stage, sha(stage / 'template.json'))
    if any(sha(path) != fingerprints[name]['sha256'] for name, path in inputs.items()):
        raise ValueError('build/source input changed during packaging; staging retained')
    stage.chmod(0o755)
    publish(stage, args.output)
    print('Published immutable Zephyr firmware template:', args.output)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
