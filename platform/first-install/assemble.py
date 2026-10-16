"""공통 SD 조립기. plan은 읽기만 하고, assemble --execute만 새 파일·자기 loop를 만든다.

기존 플랫폼 파티션 바이트는 보존한다. 게스트 변환·초기화는 기존 준비 도구에 위임한다.
"""
import argparse
import bz2
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
import zlib

from first_install_contract import FEATURES, GIB, MIB, SECTOR, align, capacity, layout
from artifact_io import sha, regular, checked, new_path, publish
from completed_artifacts import completed_input

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'platform/yocto/meta-perseus/recipes-core/perseus-platform/files'
sys.path.insert(0, str(ROOT / 'platform/guests/common'))
from prepare_rootfs import inside

spec = importlib.util.spec_from_file_location('perseus_prepare_existing', ROOT / 'scripts/prepare-perseus-guests.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)

rtos_spec = importlib.util.spec_from_file_location('perseus_rtos_template', ROOT / 'platform/guests/zephyr/template.py')
rtos = importlib.util.module_from_spec(rtos_spec)
rtos_spec.loader.exec_module(rtos)



def read_layout(image):
    opener = bz2.open if image.suffix == '.bz2' else open
    total = 0
    with opener(image, 'rb') as stream:
        mbr = stream.read(SECTOR)
        total = len(mbr)
        for block in iter(lambda: stream.read(8 * MIB), b''):
            total += len(block)
    return mbr, total, layout(mbr, total)


def sources():
    return json.loads((ROOT / 'platform/guests/sources.lock.json').read_text())


def template_info(directory, source_id, expected):
    directory = Path(directory)
    if not directory.is_absolute() or directory.resolve() != directory or directory.is_symlink():
        raise ValueError('template directory must be explicit without links')
    checked(directory / 'template.json', expected)
    meta = json.loads((directory / 'template.json').read_text())
    boot = json.loads(regular(directory / 'boot.json').read_text())
    if meta.get('id') != source_id or meta.get('source') != sources()[source_id] or boot.get('source') != meta['source']:
        raise ValueError('template provenance differs from frozen source lock')
    root = checked(directory / 'root.raw', meta['root_sha256'])
    if root.stat().st_mode & 0o222:
        raise ValueError('template root must be published read-only, never a used writable VM disk')
    if type(meta.get('size_gib')) is not int or not 4 <= meta['size_gib'] <= 32:
        raise ValueError('template size must be an integer 4..32 GiB')
    if root.stat().st_size != meta['size_gib'] * GIB:
        raise ValueError('template logical disk size differs from manifest')
    boot_bytes = 0
    for name, field in [('Image', 'kernel_sha256'), ('initrd', 'initrd_sha256'), ('kernel.config', 'config_sha256')]:
        path = checked(directory / name, boot[field])
        boot_bytes += path.stat().st_size
    return {'logical_bytes': root.stat().st_size, 'allocated_bytes': root.stat().st_blocks * SECTOR,
            'boot_bytes': boot_bytes + 64 * 1024, 'preparation_scratch_bytes': 0,
            'source_id': source_id, 'template': str(directory), 'template_manifest_sha256': expected, 'root_sha256': meta['root_sha256'], 'boot': boot}


def guest_info(config):
    defaults = json.loads((FILES / 'defaults.json').read_text())['instances']
    guests = config['guests']
    if len(guests) != 2 or {r['id'] for r in guests} != {'ubuntu-dev', 'debian-dev'}:
        raise ValueError('exactly ubuntu-dev and debian-dev must be configured')
    result = []
    for guest in guests:
        default = next(d for d in defaults if d['id'] == guest['id'])
        source_id = default['template']
        if guest.get('template_dir'):
            info = template_info(guest['template_dir'], source_id, guest['template_manifest_sha256'])
        else:
            source = sources()[source_id]
            download = guest.get('download', False)
            if type(download) is not bool:
                raise ValueError('download permission must be boolean')
            input_path = Path(guest['input'])
            limit = guest.get('source_virtual_limit_bytes', 64 * GIB)
            if type(limit) is not int or not 0 < limit <= 64 * GIB:
                raise ValueError('source virtual size limit must be at most 64 GiB')
            if input_path.exists():
                regular(input_path)
                if prepare.digest(input_path, source['hash_algorithm']) != source['digest']:
                    raise ValueError('official source hash mismatch')
                with input_path.open('rb') as stream:
                    header = stream.read(72)
                if header[:4] != b'QFI\xfb' or len(header) < 32:
                    raise ValueError('expected the pinned QCOW2 source')
                virtual = struct.unpack_from('>Q', header, 24)[0]
                if not 0 < virtual <= limit:
                    raise ValueError('source virtual size exceeds the bounded preparation space')
                downloaded = input_path.stat().st_size
            else:
                if not download:
                    raise ValueError('source absent; explicit download permission needed in config and --execute')
                new_path(input_path)
                virtual, downloaded = limit, limit
            size_gib = guest.get('size_gib', 8)
            if type(size_gib) is not int or not 4 <= size_gib <= 32:
                raise ValueError('new template size must be an integer 4..32 GiB')
            size = size_gib * GIB
            info = {'source_id': source_id, 'logical_bytes': size, 'allocated_bytes': size,
                    'boot_bytes': 512 * MIB, 'preparation_scratch_bytes': virtual + downloaded + 2 * size,
                    'input': str(input_path), 'source': source, 'allow_download': download}
        info['id'], info['default'] = guest['id'], default
        result.append(info)
    return result



def rtos_info(config):
    """추가 RTOS는 명시적으로 검증한 불변 펌웨어만 보관한다. 기본 VM/seed는 추가하지 않는다."""
    entries = config.get('rtos_templates', [])
    if not isinstance(entries, list) or len(entries) > 1:
        raise ValueError('at most one pinned Zephyr firmware template is supported')
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'template_dir', 'template_manifest_sha256'}:
            raise ValueError('RTOS template needs an explicit directory and manifest hash')
        result.append(rtos.info(entry['template_dir'], entry['template_manifest_sha256']))
    return result


def copy_rtos_templates(data, entries):
    """Linux 인스턴스 레지스트리와 별도 위치에 검증된 펌웨어만 복사하고 다시 검증한다."""
    records = []
    if entries:
        directory = data / 'rtos-templates'
        directory.mkdir()
    for entry in entries:
        source = Path(entry['template'])
        verified = rtos.info(source, entry['template_manifest_sha256'])
        if verified != entry:
            raise ValueError('RTOS source changed after planning')
        destination = directory / entry['id']
        destination.mkdir()
        for name in sorted(rtos.FILES | {'template.json'}):
            shutil.copyfile(regular(source / name), destination / name)
            (destination / name).chmod(0o444)
        rtos.info(destination, entry['template_manifest_sha256'])
        records.append({key: entry[key] for key in ('id', 'kind', 'template_manifest_sha256', 'metadata', 'logical_bytes')})
    return records


def plan(config, image, record, output, work, card_bytes):
    if config.get('schema') != 1:
        raise ValueError('unsupported assembly config schema')
    if set(config) - {'schema','platform','guests','template_policy','operating_reserve_bytes','rtos_templates'}:
        raise ValueError('unknown deployment configuration fields')
    for path in (output, work):
        new_path(path)
        if path.is_relative_to(image.parent):
            raise ValueError('output/work must be outside the published platform release')
        if path.is_relative_to(Path(record['build_directory'])):
            raise ValueError('output/work must be outside the source build directory')
    if output == work or output.is_relative_to(work) or work.is_relative_to(output):
        raise ValueError('output/work paths must be separate dedicated directories')
    mbr, platform_bytes, table = read_layout(image)
    guests = guest_info(config)
    firmware_templates = rtos_info(config)
    for entry in firmware_templates:
        for path in (output, work):
            if path.is_relative_to(Path(entry['template'])):
                raise ValueError('output/work cannot be inside an RTOS input template')
    for guest in guests:
        if guest.get('template'):
            for path in (output, work):
                if path.is_relative_to(Path(guest['template'])):
                    raise ValueError('output/work cannot be inside an input template')
    reserve = config.get('operating_reserve_bytes', 2 * GIB)
    if type(reserve) is not int or reserve < 2 * GIB:
        raise ValueError('at least 2 GiB operating reserve is required')
    totals = capacity(platform_bytes, guests, template_policy=config.get('template_policy', 'full'),
                      card_bytes=card_bytes, reserve_bytes=reserve,
                      retained_firmware_bytes=sum(item['logical_bytes'] for item in firmware_templates))
    if totals['image_bytes'] > 2 ** 32 * SECTOR:
        raise ValueError('DOS image exceeds 2 TiB')
    return {'schema': 1, 'action': 'plan', 'platform': config['platform'], 'platform_source_commit': record['source_commit'],
            'layout': table, 'guests': guests, 'rtos_templates': firmware_templates, 'capacity': totals, 'features_required': FEATURES,
            'platform_features_verified': False,
            'execution_gate': 'RO-mount and verify actual platform rootfs capabilities before any guest preparation; old images are rejected',
            'output_directory': str(output), 'work_directory': str(work),
            'deployment_config': config, 'deployment_config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'network': 'isolated 192.168.77.0/24; per-card random locally administered MACs; unchanged IP reservations',
            'never_executed_by_plan': ['download', 'mount', 'image creation', 'guest initialization', 'SD access', 'BitBake']}


def platform_root_ok(root):
    """마커만 믿지 않고 실제 모듈·unit·enable/drop-in·확장 도구·클론 ID 정책을 검사한다."""
    def path(name):
        return inside(root, name, create_parents=False)
    capability = path('usr/share/perseus/first-install-capabilities.json')
    if not capability.is_file() or json.loads(capability.read_text()) != FEATURES:
        raise ValueError('platform lacks first-install capabilities; rebuild dom0 from the new Yocto source, no rootfs injection allowed')
    expected = {'usr/lib/perseus/perseus_vm.py': FILES / 'perseus_vm.py',
                'etc/perseus/defaults.json': FILES / 'defaults.json',
                'usr/lib/perseus/perseus_first_boot.py': FILES / 'perseus_first_boot.py',
                'usr/lib/perseus/first_install_contract.py': ROOT / 'platform/first-install/first_install_contract.py',
                'usr/sbin/perseus-first-boot': FILES / 'perseus-first-boot'}
    for name in ('prepare_instance.py', 'prepare_rootfs.py', 'guest_identity.py'):
        expected['usr/lib/perseus/' + name] = ROOT / 'platform/guests/common' / name
    units = ['perseus-data-prepare.service', 'perseus-first-boot.service', 'srv-perseus.mount',
             'perseus-vms.service', 'perseus-vm-shutdown.service', 'perseus-xen-ready.service']
    expected.update({'usr/lib/systemd/system/' + u: FILES / u for u in units})
    for relative, source in expected.items():
        if not path(relative).is_file() or path(relative).read_bytes() != source.read_bytes():
            raise ValueError('platform source/installed contract mismatch: ' + relative)
    for name in ['sfdisk', 'partx', 'blockdev', 'blkid', 'e2fsck', 'resize2fs', 'dumpe2fs', 'tune2fs', 'losetup', 'mount', 'umount', 'findmnt', 'cp', 'chroot', 'ssh-keygen']:
        if not any(path(prefix + name).is_file() and path(prefix + name).stat().st_mode & 0o111
                   for prefix in ('usr/sbin/', 'usr/bin/', 'sbin/', 'bin/')):
            raise ValueError('required platform growth tool missing: ' + name)
    if path('etc/machine-id').read_text() != 'uninitialized\n':
        raise ValueError('common dom0 machine-id must be uninitialized for PID1')
    if any(path('etc/ssh').glob('ssh_host_*_key')):
        raise ValueError('common dom0 must not contain shared SSH host keys')
    for name in ('sshd.service', 'sshd@.service', 'sshd.socket'):
        dropin = path('etc/systemd/system/' + name + '.d/10-perseus-keys.conf')
        if dropin.read_bytes() != (FILES / 'perseus-sshd-keys.conf').read_bytes():
            raise ValueError('SSH host-key gate missing')
    enabled = ['perseus-first-boot.service', 'perseus-vms.service', 'perseus-vm-shutdown.service', 'srv-perseus.mount']
    for unit in enabled:
        link = root / 'etc/systemd/system/multi-user.target.wants' / unit
        if not link.is_symlink() or path('etc/systemd/system/multi-user.target.wants/' + unit).read_bytes() != (FILES / unit).read_bytes():
            raise ValueError('required platform unit not enabled: ' + unit)
    return {relative: sha(path(relative)) for relative in expected}


def platform_mount(work, image):
    loop = work.loop(image, readonly=True, partitions=True)
    root = Path(loop + 'p2')
    for _ in range(40):
        if root.exists():
            break
        time.sleep(0.25)
    else:
        raise ValueError('private platform loop partition 2 not ready')
    kind = subprocess.check_output(['blkid', '-p', '-s', 'TYPE', '-o', 'value', str(root)], text=True).strip()
    if kind not in ('ext3', 'ext4'):
        raise ValueError('platform partition 2 is not ext3/ext4')
    target = work.path / 'platform-root'
    work.mount(root, target, readonly=True)
    return target


def platform_boot_ok(image, table, output):
    """MBR로 찾은 실제 FAT의 Xen 부팅 세트를 검사하고 해시를 기록한다."""
    volume = f"{image}@@{table['partitions'][0]['start'] * SECTOR}"
    result = {}
    for name in ('xen', 'boot.scr', 'Image', 'kernel8.img', 'config.txt', 'bcm2711-rpi-4-b.dtb'):
        target = output / ('platform-' + name)
        prepare.command(['mcopy', '-i', volume, '::' + name, target])
        regular(target)
        result[name] = sha(target)
    boot = (output / 'platform-boot.scr').read_bytes()
    if len(boot) < 72:
        raise ValueError('truncated U-Boot script')
    header = struct.unpack('>7I4B32s', boot[:64])
    if (header[0] != 0x27051956 or header[9:11] != (6, 0)
            or zlib.crc32(boot[:4] + b'\0' * 4 + boot[8:64]) & 0xFFFFFFFF != header[1]
            or len(boot[64:]) != header[3] or zlib.crc32(boot[64:]) & 0xFFFFFFFF != header[6]):
        raise ValueError('U-Boot script header/payload CRC mismatch')
    length, end = struct.unpack('>2I', boot[64:72])
    if end or length > len(boot) - 72:
        raise ValueError('invalid U-Boot script length')
    script = boot[72:72 + length].decode()
    for marker in ('part uuid mmc ${devnum}:2', 'fatload mmc ${devnum}:1 ${xen_loadaddr} xen',
                   'fdt move ${fdtcontroladdr} ${fdt_addr_r}', 'booti ${xen_loadaddr} - ${fdt_addr_r}'):
        if marker not in script:
            raise ValueError('platform lacks validated Xen boot/PARTUUID contract: ' + marker)
    return result


def copy_range(source, target, offset, length):
    with source.open('rb') as inp, target.open('r+b') as out:
        out.seek(offset)
        left = length
        while left:
            block = inp.read(min(left, 8 * MIB))
            if not block:
                raise ValueError('truncated prepared data filesystem')
            out.write(block)
            left -= len(block)
        out.flush()
        os.fsync(out.fileno())


def region_sha(path, start, count):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        stream.seek(start)
        left = count
        while left:
            block = stream.read(min(left, 8 * MIB))
            if not block:
                raise ValueError('truncated partition region')
            value.update(block)
            left -= len(block)
    return value.hexdigest()


def verify(image, manifest):
    regular(image)
    if image.stat().st_size != manifest['capacity']['image_bytes'] or sha(image) != manifest['image_sha256']:
        raise ValueError('assembled image hash/size mismatch')
    with image.open('rb') as stream:
        actual = layout(stream.read(SECTOR), image.stat().st_size, with_data=True)
    if actual != manifest['layout']:
        raise ValueError('assembled partition layout mismatch')
    entries = manifest['partition_hashes']
    if len(entries) != 3 or [e['number'] for e in entries] != [1,2,3]:
        raise ValueError('verification requires all three partition digests')
    for entry, part in zip(entries, actual['partitions']):
        if entry['start_bytes'] != part['start'] * SECTOR or entry['bytes'] != part['size'] * SECTOR:
            raise ValueError('partition digest range disagrees with layout')
        if region_sha(image, entry['start_bytes'], entry['bytes']) != entry['sha256']:
            raise ValueError('assembled partition content mismatch')
    if manifest.get('features_required') != FEATURES:
        raise ValueError('unsupported verification capabilities')
    if manifest.get('platform_features_verified') is not True or manifest.get('assembly_complete') is not True:
        raise ValueError('incomplete assembly manifest')
    return {'image_sha256': manifest['image_sha256'], 'partition_count': 3, 'verified': True,
            'not_tested': ['hardware boot', 'first-boot partition growth', 'Ubuntu/Debian boot/SSH/persistence']}


def download_source(guest):
    """명시적 실제 조립 시에만 호출한다. 고정 URL·해시와 새 파일 경로를 사용한다."""
    import urllib.request
    target = Path(guest['input'])
    new_path(target)
    temp = target.with_name('.' + target.name + '.download-' + uuid.uuid4().hex)
    h = hashlib.new(guest['source']['hash_algorithm'])
    received = 0
    with urllib.request.urlopen(guest['source']['url'], timeout=60) as response, temp.open('xb') as out:
        if response.geturl() != guest['source']['url']:
            raise ValueError('upstream redirect rejected; inspect/update provenance explicitly')
        for chunk in iter(lambda: response.read(MIB), b''):
            received += len(chunk)
            if received > 64 * GIB:
                raise ValueError('download exceeds the bounded source size')
            h.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if h.hexdigest() != guest['source']['digest']:
        raise ValueError('downloaded official source digest mismatch; partial file retained')
    publish(temp, target)


def source_fingerprints():
    paths = [ROOT / 'scripts/assemble-perseus-first-install.py', ROOT / 'scripts/prepare-perseus-guests.py',
             ROOT / 'platform/first-install/assemble.py', ROOT / 'platform/first-install/artifact_io.py',
             ROOT / 'platform/first-install/completed_artifacts.py', ROOT / 'scripts/publish-perseus-platform.py',
             ROOT / 'platform/first-install/first_install_contract.py',
             ROOT / 'platform/guests/sources.lock.json', *sorted((ROOT / 'platform/guests/common').glob('*.py')),
             *(p for p in sorted((ROOT / 'platform/guests/zephyr').glob('*')) if p.is_file())]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def assemble(config, image, record, planned):
    if os.geteuid() != 0:
        raise ValueError('explicit assembly needs an approved Linux root preparation environment')
    source_files = source_fingerprints()
    output, workdir = Path(planned['output_directory']), Path(planned['work_directory'])
    for tool in ('losetup', 'mount', 'umount', 'mkfs.ext4', 'tune2fs', 'chroot', 'cp', 'blkid', 'findmnt', 'mcopy'):
        if not shutil.which(tool):
            raise ValueError('missing host tool; install only after separate approval: ' + tool)
    required = planned['capacity']['wsl_scratch_upper_bound_bytes']
    if shutil.disk_usage(workdir.parent).free < required or workdir.parent.stat().st_dev != output.parent.stat().st_dev:
        raise ValueError('insufficient bounded WSL scratch space or output/work not on the same filesystem')
    new_path(workdir)
    workdir.mkdir(mode=0o700)
    work = prepare.Workspace(workdir)
    stage = work.path / 'publish'
    stage.mkdir()
    base = work.path / 'platform.img'
    opener = bz2.open if image.suffix == '.bz2' else open
    with opener(image, 'rb') as inp, base.open('xb') as out:
        shutil.copyfileobj(inp, out, 8 * MIB)
    try:
        platform_boot = platform_boot_ok(base, planned['layout'], work.path)
        root = platform_mount(work, base)
        platform_checks = platform_root_ok(root)  # 게스트/디스크 준비보다 먼저 검사하며 입력 rootfs에 파일을 추가하지 않는다.
        work.close()
        templates, verified_templates = {}, {}
        for guest in planned['guests']:
            if guest.get('template'):
                templates[guest['id']] = Path(guest['template'])
            else:
                if not Path(guest['input']).exists():
                    if not guest.get('allow_download'):
                        raise ValueError('cached source disappeared; no download was authorized')
                    download_source(guest)
                source = guest['source']
                template_dir = workdir / ('template-' + guest['source_id'])
                args = argparse.Namespace(input=Path(guest['input']), output=template_dir, size_gib=guest['logical_bytes'] // GIB, source=guest['source_id'])
                prepare.make_template(args, source)
                template_info(template_dir, guest['source_id'], sha(template_dir / 'template.json'))
                templates[guest['id']] = template_dir
            verified_templates[guest['id']] = guest.get('template_manifest_sha256') or sha(templates[guest['id']] / 'template.json')
        datafile = work.path / 'data.ext4'
        with datafile.open('xb') as stream:
            stream.truncate(planned['capacity']['data_bytes'])
        prepare.command(['mkfs.ext4', '-F', '-m', '0', '-L', 'perseus-data', datafile])
        data = work.path / 'data'
        work.mount(work.loop(datafile), data)
        prepare.initialize_storage(data, seed=True)
        bundles, template_records = [], []
        for guest in planned['guests']:
            template = templates[guest['id']]
            tmeta = json.loads((template / 'template.json').read_text())
            catalog = data / 'templates' / guest['source_id']
            catalog.mkdir()
            for source in template.iterdir():
                if source.name not in ('root.raw', 'template.json', 'boot.json', 'Image', 'initrd', 'kernel.config'):
                    raise ValueError('unexpected template bundle file')
                regular(source)
                if source.name == 'root.raw':
                    prepare.command(['cp', '--sparse=always', '--reflink=never', '--', source, catalog / source.name])
                else:
                    shutil.copyfile(source, catalog / source.name)
                (catalog / source.name).chmod(0o444)
            prepare.make_instance(argparse.Namespace(data_root=data, id=guest['id'], spec=None, deployment_seed=True, template_root=template))
            seed = data / 'seeds' / guest['id']
            r = json.loads((seed / 'instance.json').read_text())
            boot = json.loads((seed / 'boot.json').read_text())
            bundles.append({'record': r, 'root_bytes': (seed / 'root.raw').stat().st_size,
                            'seed_root_sha256': sha(seed / 'root.raw'), 'boot': boot})
            template_records.append({'id': guest['source_id'], 'metadata': tmeta, 'root_bytes': (template / 'root.raw').stat().st_size, 'template_manifest_sha256': sha(template / 'template.json'),
                                     'retained_root': planned['capacity']['template_policy'] == 'full'})
        firmware_records = copy_rtos_templates(data, planned.get('rtos_templates', []))
        factory = {'schema': 1, 'features': FEATURES, 'factory_id': str(uuid.uuid4()), 'instances': bundles,
                   'templates': template_records, 'rtos_templates': firmware_records,
                   'template_policy': planned['capacity']['template_policy']}
        (data / '.perseus-install.json').write_text(json.dumps(factory, indent=2) + '\n')
        stats = os.statvfs(data)
        actual_free = stats.f_bavail * stats.f_frsize
        vm_holes = sum(max(0, (data / 'seeds' / b['record']['id'] / 'root.raw').stat().st_size
                           - (data / 'seeds' / b['record']['id'] / 'root.raw').stat().st_blocks * SECTOR) for b in bundles)
        if actual_free < vm_holes + planned['capacity']['operating_reserve_bytes']:
            raise ValueError('populated data filesystem cannot honor VM growth and operating reserve')
        actual_usage = {'free_bytes': actual_free, 'vm_future_growth_bytes': vm_holes,
                        'operating_reserve_bytes': planned['capacity']['operating_reserve_bytes']}
        os.sync()
        work.close()
        # 출판 전 원래 입력을 재검증한다. 게스트 자산 원본도 해시 계약으로 재확인한다.
        checked(image, config['platform']['sha256'])
        for guest in planned['guests']:
            template = templates[guest['id']]
            template_info(template, guest['source_id'], verified_templates[guest['id']])
        for entry in planned.get('rtos_templates', []):
            rtos.info(entry['template'], entry['template_manifest_sha256'])
        final = stage / 'perseus-first-install.img'
        shutil.copyfile(base, final)
        with final.open('r+b') as stream:
            stream.truncate(planned['capacity']['image_bytes'])
            stream.seek(478)
            start = planned['capacity']['data_start_bytes'] // SECTOR
            count = planned['capacity']['data_bytes'] // SECTOR
            stream.write(struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', 0x83, b'\xfe\xff\xff', start, count))
            stream.flush()
            os.fsync(stream.fileno())
        copy_range(datafile, final, start * SECTOR, count * SECTOR)
        with final.open('rb') as stream:
            final_layout = layout(stream.read(SECTOR), final.stat().st_size, with_data=True)
        partition_hashes = []
        for part in final_layout['partitions']:
            offset, length = part['start'] * SECTOR, part['size'] * SECTOR
            expected = region_sha(base, offset, length) if part['number'] < 3 else sha(datafile)
            if region_sha(final, offset, length) != expected:
                raise ValueError('partition copy changed validated content')
            partition_hashes.append({'number': part['number'], 'start_bytes': offset, 'bytes': length, 'sha256': expected})
        manifest = {**planned, 'action': 'assembled', 'assembly_complete': True, 'platform_features_verified': True,
                    'platform_files': platform_checks, 'platform_boot_sha256': platform_boot, 'actual_data_usage': actual_usage, 'factory': factory, 'layout': final_layout,
                    'partition_hashes': partition_hashes, 'image_sha256': sha(final),
                    'assembly_source_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                    'assembly_source_dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'])),
                    'assembly_source_files': source_files,
                    'verified_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'unperformed': ['64GB/128GB device boot/growth', 'Xen/guest boot', 'console/SSH', 'persistence', 'SD recording']}
        verify(final, manifest)
        target_manifest = stage / 'perseus-first-install.manifest.json'
        target_manifest.write_text(json.dumps(manifest, indent=2) + '\n')
        (stage / 'SHA256SUMS').write_text(f"{manifest['image_sha256']}  {final.name}\n{sha(target_manifest)}  {target_manifest.name}\n")
        os.sync()
        if source_fingerprints() != source_files:
            raise ValueError('assembler source changed during preparation; output not published')
        publish(stage, output)
        return manifest
    finally:
        if work.mounts or work.loops:
            work.close()
        print('Dedicated scratch retained:', workdir, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='plan', choices=['plan', 'assemble', 'verify'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--card-bytes', type=int, help='actual measured capacity in bytes, never a commercial GB label')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'verify':
        regular(args.manifest)
        manifest = json.loads(args.manifest.read_text())
        checksums = (args.manifest.parent / 'SHA256SUMS').read_text().splitlines()
        if f'{sha(args.manifest)}  {args.manifest.name}' not in checksums:
            raise ValueError('verification manifest digest mismatch')
        print(json.dumps(verify(args.manifest.parent / 'perseus-first-install.img', manifest), indent=2))
        return
    if not all((args.config, args.output_dir, args.work_dir, args.card_bytes)):
        raise ValueError('--config, --output-dir, --work-dir and measured --card-bytes are required')
    regular(args.config)
    config = json.loads(args.config.read_text())
    with completed_input(config) as (image, record):
        planned = plan(config, image, record, args.output_dir, args.work_dir, args.card_bytes)
        if args.action == 'assemble' and args.execute:
            result = assemble(config, image, record, planned)
        else:
            result = planned
        print(json.dumps(result, indent=2))
