#!/usr/bin/env python3
"""WIC의 실제 FAT 부팅 파일을 읽어 deploy 파일과 비교한다. 장치·마운트는 사용하지 않는다."""
import argparse
import bz2
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zlib


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def inspect(deploy, image, output):
    """일반 파일인 WIC/bz2만 읽고, 새 output 디렉터리에 추출 파일과 결과를 보존한다."""
    if not image.is_file():
        raise ValueError(f'WIC is not a regular file: {image}')
    for tool in ['mdir', 'mcopy']:
        if not shutil.which(tool):
            raise ValueError(f'Required host tool is missing: {tool}')
    metadata = json.loads((deploy / 'xenova-xen-bringup-image-raspberrypi4-64.rootfs.testdata.json').read_text())
    if metadata['MACHINE'] != 'raspberrypi4-64' or metadata['RPI_USE_U_BOOT'] != '1':
        raise ValueError('Expected Raspberry Pi 4 U-Boot image metadata')
    output.mkdir(parents=True, exist_ok=False)
    checks, hashes = [], {}

    def check(condition, message):
        if not condition:
            raise AssertionError(message)
        checks.append(message)
        print('PASS ' + message, flush=True)

    # 첫 FAT 파티션까지만 풀어 읽는다. SD 장치에 연결하거나 rootfs를 마운트하지 않는다.
    prefix = output / 'wic-boot-prefix.img'
    opener = bz2.open if image.suffix == '.bz2' else open
    with opener(image, 'rb') as stream, prefix.open('wb') as destination:
        mbr = stream.read(512)
        check(len(mbr) == 512 and mbr[510:] == b'\x55\xaa', 'WIC has a valid MBR signature')
        partition = mbr[446:462]
        start, sectors = struct.unpack_from('<II', partition, 8)
        end = (start + sectors) * 512
        check(partition[4] in (0x0B, 0x0C, 0x0E) and start > 0 and sectors > 0
              and end <= 256 * 1024 * 1024, 'first partition is bounded FAT')
        destination.write(mbr)
        left = end - len(mbr)
        while left:
            block = stream.read(min(left, 1024 * 1024))
            if not block:
                raise ValueError('Truncated WIC boot partition')
            destination.write(block)
            left -= len(block)
    volume = f'{prefix}@@{start * 512}'
    listing = subprocess.run(['mdir', '-i', volume, '::'], text=True, capture_output=True, check=True)
    (output / 'fat-directory.log').write_text(listing.stdout + listing.stderr)

    def extract(name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise ValueError(f'Unsafe FAT filename: {name}')
        target = output / (name + '.from-wic')
        result = subprocess.run(['mcopy', '-i', volume, '::' + name, str(target)], text=True, capture_output=True)
        check(result.returncode == 0, f'actual WIC FAT contains {name}: {result.stderr.strip()}')
        return target

    def compare(name, deployed_name):
        extracted = extract(name)
        expected = deploy / deployed_name
        check(expected.is_file(), 'deploy file exists: ' + deployed_name)
        hashes[name] = {'wic_sha256': digest(extracted), 'deploy_sha256': digest(expected), 'deploy_file': str(expected)}
        check(hashes[name]['wic_sha256'] == hashes[name]['deploy_sha256'], 'WIC matches deploy: ' + name)
        return extracted

    try:
        boot = compare('boot.scr', 'boot.scr').read_bytes()
        compare('xen', 'xen-' + metadata['MACHINE'])
        compare('Image', 'Image')
        config = extract('config.txt').read_text()
        explicit = re.findall(r'^\s*kernel\s*=\s*([^#\s]+)', config, re.M)
        loader = explicit[-1].strip('"') if explicit else metadata['SDIMG_KERNELIMAGE']
        compare(loader, 'u-boot.bin')
        check('enable_uart=1' in config.splitlines() and 'enable_gic=1' in config.splitlines(), 'WIC config enables UART and GIC')

        check(len(boot) >= 72, 'WIC boot.scr contains a legacy script header')
        header = struct.unpack('>7I4B32s', boot[:64])
        check(header[0] == 0x27051956 and header[9:11] == (6, 0), 'WIC boot.scr is an uncompressed U-Boot script')
        check(zlib.crc32(boot[:4] + b'\0' * 4 + boot[8:64]) & 0xFFFFFFFF == header[1], 'WIC boot.scr header CRC matches')
        payload = boot[64:]
        check(len(payload) == header[3] and zlib.crc32(payload) & 0xFFFFFFFF == header[6], 'WIC boot.scr payload size and CRC match')
        length, terminator = struct.unpack('>2I', payload[:8])
        check(terminator == 0 and length <= len(payload) - 8, 'WIC boot.scr script length is valid')
        script = payload[8:8 + length].decode()
        (output / 'boot.cmd.extracted').write_text(script)
        for marker in ['setenv devnum 0', 'fdt move ${fdtcontroladdr} ${fdt_addr_r}',
                       'fatload mmc ${devnum}:1 ${xen_loadaddr} xen',
                       '0x00200000', '0x00400000', 'booti ${xen_loadaddr} - ${fdt_addr_r}']:
            check(marker in script, 'WIC boot script contains ' + marker)
        check('@@' not in script and 'mmc 1:' not in script, 'WIC boot substitutions are complete')
    except (AssertionError, ValueError) as error:
        (output / 'result.json').write_text(json.dumps({'image': str(image.resolve()), 'passed': checks, 'hashes': hashes, 'error': str(error)}, indent=2) + '\n')
        raise
    (output / 'result.json').write_text(json.dumps({'image': str(image.resolve()), 'boot_offset': start * 512, 'passed': checks, 'hashes': hashes}, indent=2) + '\n')
    print(f'PASS {len(checks)} WIC artifact checks; evidence: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy-dir', required=True, type=Path)
    parser.add_argument('--image', type=Path, help='default: deploy-dir/xenova-xen-bringup-image-raspberrypi4-64.rootfs.wic.bz2')
    parser.add_argument('--output', required=True, type=Path, help='new directory for extracted files and logs')
    args = parser.parse_args()
    try:
        inspect(args.deploy_dir, args.image or args.deploy_dir / 'xenova-xen-bringup-image-raspberrypi4-64.rootfs.wic.bz2', args.output)
    except (AssertionError, ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'FAIL {error}\n')
