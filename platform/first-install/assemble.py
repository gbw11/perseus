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
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
sys.path.insert(0, str(ROOT / 'platform/guests/common'))
from prepare_rootfs import inside
sys.path.insert(0, str(FILES))
import xenova_zephyr as zephyr
import deployment_payloads

spec = importlib.util.spec_from_file_location('xenova_prepare_existing', ROOT / 'scripts/prepare-xenova-guests.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)

rtos_spec = importlib.util.spec_from_file_location('xenova_rtos_template', ROOT / 'platform/guests/zephyr/template.py')
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
    if meta.get('platform') == 'xenova':
        import template_recipe
        preparation = dict(meta.get('preparation', {}))
        expected_source = preparation.pop('source_sha256', None)
        if (not isinstance(meta.get('version'), str) or not template_recipe.VERSION.fullmatch(meta['version'])
                or preparation.get('platform') != 'xenova'
                or preparation.get('recipe', {}).get('version') != meta['version']
                or preparation.get('recipe', {}).get('source_id') != source_id
                or expected_source != hashlib.sha256(json.dumps(preparation, sort_keys=True, separators=(',', ':')).encode()).hexdigest()):
            raise ValueError('Xenova template preparation provenance/version mismatch')
        names = {'root.raw', 'Image', 'initrd', 'kernel.config', 'boot.json'}
        if set(meta.get('files', {})) != names:
            raise ValueError('Xenova template payload manifest incomplete')
        for name in names:
            asset = checked(directory / name, meta['files'][name]['sha256'])
            if asset.stat().st_size != meta['files'][name]['bytes'] or asset.stat().st_mode & 0o222:
                raise ValueError('Xenova template payload size/immutability mismatch')
    return {'logical_bytes': root.stat().st_size, 'allocated_bytes': root.stat().st_blocks * SECTOR,
            'boot_bytes': boot_bytes + 64 * 1024, 'preparation_scratch_bytes': 0,
            'source_id': source_id, 'template': str(directory), 'template_manifest_sha256': expected, 'root_sha256': meta['root_sha256'], 'boot': boot}


def linux_template_info(config):
    """보관할 Linux 템플릿 두 종은 기본 인스턴스 목록과 독립적으로 검증한다."""
    defaults = [e for e in json.loads((ROOT / 'platform/guests/default-templates.lock.json').read_text())['templates']
                if e['guest_kind'] == 'linux']
    guests = config['linux_templates']
    if (not isinstance(guests, list) or len(guests) != len(defaults)
            or any(not isinstance(entry, dict) for entry in guests)
            or {r.get('template_id') for r in guests} != {r['template_id'] for r in defaults}):
        raise ValueError('both Ubuntu and Debian template inputs are required')
    result = []
    for guest in guests:
        default = next(d for d in defaults if d['template_id'] == guest['template_id'])
        if guest.get('template_version') != default['version']:
            raise ValueError('template input differs from selected catalog version')
        source_id = default['id']
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
        info['template_id'], info['template_version'] = default['template_id'], default['version']
        result.append(info)
    return result



def guest_info(config, templates=None):
    """기본 Linux seed만 선택한다. 보관된 Ubuntu 템플릿으로 seed를 만들지 않는다."""
    templates = linux_template_info(config) if templates is None else templates
    by_source = {entry['source_id']: entry for entry in templates}
    defaults = json.loads((FILES / 'defaults.json').read_text())['instances']
    return [{**by_source[d['template']], 'id': d['id'], 'default': d} for d in defaults]


def rtos_info(config):
    """불변 Zephyr 원본을 검증한다. 독립 기본 seed 생성은 별도 공통 경로가 담당한다."""
    entries = config.get('rtos_templates', [])
    if not isinstance(entries, list) or len(entries) > 1:
        raise ValueError('at most one pinned Zephyr firmware template is supported')
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'template_dir', 'template_manifest_sha256'}:
            raise ValueError('RTOS template needs an explicit directory and manifest hash')
        zephyr.bundle_info(Path(entry['template_dir']), entry['template_manifest_sha256'])
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
        for name in sorted(set(entry.get('payloads', rtos.FILES)) | {entry.get('bundle_manifest', 'template.json')}):
            shutil.copyfile(regular(source / name), destination / name)
            (destination / name).chmod(0o444)
        rtos.info(destination, entry['template_manifest_sha256'])
        records.append({key: entry[key] for key in ('id', 'kind', 'template_manifest_sha256', 'metadata', 'logical_bytes')})
    return records


def copy_management_catalog(data):
    """새 조립 저장소에만 factory public ID/version 카탈로그를 준비한다."""
    from xenova_catalog import describe
    from artifact_io import publish
    locked=json.loads((ROOT/'platform/guests/default-templates.lock.json').read_text())['templates']
    selected={e['id']:e for e in locked}
    for kind, folder in [('linux','templates'),('zephyr','rtos-templates')]:
        parent = data / folder
        if not parent.exists():
            continue
        for directory in parent.iterdir():
            if directory.name.startswith('.') or directory.name not in selected:
                continue
            manifest = directory / ('firmware.json' if (directory / 'firmware.json').exists() else 'template.json')
            meta = json.loads(manifest.read_text())
            descriptor = {'schema_version':'1.0','template_id':selected[directory.name]['template_id'],
                          'template_version':selected[directory.name]['version'],'guest_kind':kind,
                          'asset_directory':str(directory.relative_to(data)),
                          'manifest_sha256':sha(manifest),'source_template_id':meta['id']}
            describe(data, descriptor, full=True)
            output = data / 'catalog' / descriptor['template_id']
            output.mkdir(parents=True, exist_ok=True)
            temp = output / '.descriptor'
            temp.write_text(json.dumps(descriptor, indent=2) + '\n')
            temp.chmod(0o444)
            publish(temp, output / (descriptor['template_version'] + '.json'))


def copy_rtos_seeds(data, entries):
    """새 조립 저장소에만 기본 Zephyr 독립 자산을 준비한다. 기존 저장소는 대상이 아니다."""
    if len(entries) != 1:
        raise ValueError('new first-install images require one verified Zephyr template')
    defaults = json.loads((FILES / 'defaults.json').read_text())['firmware_instances']
    if any(entry['template'] != entries[0]['id'] for entry in defaults):
        raise ValueError('default Zephyr Starter template mismatch')
    records = []
    for default in defaults:
        record = zephyr.new_instance(data, default['id'], data / 'rtos-templates' / default['template'],
                                     {}, autostart=default['autostart'],
                                     board_shutdown_policy=default['board_shutdown_policy'],
                                     registry='seeds', seed=True,
                                     metadata={k:default[k] for k in ('template_id','template_version','display_name') if k in default})
        records.append({'record': record})
    return records


def plan(config, image, record, output, work, card_bytes):
    if config.get('schema') != 1:
        raise ValueError('unsupported assembly config schema')
    if set(config) - {'schema','platform','linux_templates','template_policy','operating_reserve_bytes','rtos_templates'}:
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
    templates = linux_template_info(config)
    guests = guest_info(config, templates)
    firmware_templates = rtos_info(config)
    if len(firmware_templates) != 1:
        raise ValueError('new first-install images require the verified Zephyr template; existing stores use create')
    for entry in firmware_templates:
        for path in (output, work):
            if path.is_relative_to(Path(entry['template'])):
                raise ValueError('output/work cannot be inside an RTOS input template')
    for guest in templates:
        if guest.get('template'):
            for path in (output, work):
                if path.is_relative_to(Path(guest['template'])):
                    raise ValueError('output/work cannot be inside an input template')
    reserve = config.get('operating_reserve_bytes', 2 * GIB)
    if type(reserve) is not int or reserve < 2 * GIB:
        raise ValueError('at least 2 GiB operating reserve is required')
    firmware_count = len(json.loads((FILES / 'defaults.json').read_text())['firmware_instances'])
    totals = capacity(platform_bytes, guests, template_policy=config.get('template_policy', 'full'),
                      card_bytes=card_bytes, reserve_bytes=reserve,
                      retained_templates=templates,
                      retained_firmware_bytes=sum(item['logical_bytes'] * (1 + firmware_count)
                                                  + 64 * 1024 * firmware_count for item in firmware_templates))
    if totals['image_bytes'] > 2 ** 32 * SECTOR:
        raise ValueError('DOS image exceeds 2 TiB')
    return {'schema': 1, 'action': 'plan', 'platform': config['platform'], 'platform_source_commit': record['source_commit'],
            'layout': table, 'guests': guests, 'linux_templates': templates, 'rtos_templates': firmware_templates, 'capacity': totals, 'features_required': FEATURES,
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
    capability = path('usr/share/xenova/first-install-capabilities.json')
    if not capability.is_file() or json.loads(capability.read_text()) != FEATURES:
        raise ValueError('platform lacks first-install capabilities; rebuild dom0 from the new Yocto source, no rootfs injection allowed')
    expected = {'usr/lib/xenova/xenova_vm.py': FILES / 'xenova_vm.py',
                'etc/xenova/defaults.json': FILES / 'defaults.json',
                'usr/lib/xenova/xenova_first_boot.py': FILES / 'xenova_first_boot.py',
                'usr/lib/xenova/first_install_contract.py': ROOT / 'platform/first-install/first_install_contract.py',
                'usr/sbin/xenova-first-boot': FILES / 'xenova-first-boot',
                'usr/sbin/xenova-vm': FILES / 'xenova-vm',
                'usr/sbin/xenova-verify-artifacts': ROOT / 'scripts/verify-xenova-runtime-artifacts.py',
                'usr/lib/xenova/xenova_runtime.py': FILES / 'xenova_runtime.py',
                'usr/lib/xenova/xenova_zephyr.py': FILES / 'xenova_zephyr.py',
                'etc/systemd/journald.conf.d/20-xenova.conf': FILES / 'xenova-journald.conf',
                'etc/systemd/system.conf.d/20-xenova.conf': FILES / 'xenova-system.conf'}
    for name in ('xenova_management.py','xenova_management_cli.py','xenova_catalog.py','xenova_wifi.py',
                 'xenova_execution.py','xenova_recovery.py','xenova_domain_identity.py'):
        expected['usr/lib/xenova/'+name] = FILES/name
    expected['usr/lib/xenova/artifact_io.py'] = ROOT/'platform/first-install/artifact_io.py'
    expected['usr/sbin/xenova-wifi'] = FILES/'xenova-wifi'
    expected['usr/sbin/xenova-domain-identity'] = FILES/'xenova-domain-identity'
    expected['etc/xenova/execution-policy.json'] = FILES/'execution-policy.json'
    expected['usr/lib/systemd/system/xenova-wifi@.service'] = FILES/'xenova-wifi@.service'
    expected['usr/lib/xenova/update_guest_ssh.py'] = ROOT / 'scripts/update-xenova-guest-ssh.py'
    for name in ('prepare_instance.py', 'prepare_rootfs.py', 'guest_identity.py'):
        expected['usr/lib/xenova/' + name] = ROOT / 'platform/guests/common' / name
    units = ['xenova-data-prepare.service', 'xenova-first-boot.service', 'srv-xenova.mount',
             'xenova-vms.service', 'xenova-vm-shutdown.service', 'xenova-xen-ready.service', 'xenova-host-admission.service']
    expected.update({'usr/lib/systemd/system/' + u: FILES / u for u in units})
    expected.update(deployment_payloads.installed_sources(root))
    for relative, source in expected.items():
        if not path(relative).is_file() or path(relative).read_bytes() != source.read_bytes():
            raise ValueError('platform source/installed contract mismatch: ' + relative)
    for name in ['sfdisk', 'partx', 'blockdev', 'blkid', 'e2fsck', 'resize2fs', 'dumpe2fs', 'tune2fs', 'losetup', 'mount', 'umount', 'findmnt', 'cp', 'chroot', 'ssh-keygen', 'lsblk', 'ip', 'ss', 'timedatectl', 'wpa_supplicant', 'wpa_cli', 'iw', 'networkctl']:
        if not any(path(prefix + name).is_file() and path(prefix + name).stat().st_mode & 0o111
                   for prefix in ('usr/sbin/', 'usr/bin/', 'sbin/', 'bin/')):
            raise ValueError('required platform growth tool missing: ' + name)
    for relative in ('usr/lib/firmware/brcm/brcmfmac43455-sdio.bin',
                     'usr/lib/firmware/brcm/brcmfmac43455-sdio.clm_blob',
                     'usr/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,4-model-b.txt',
                     'usr/lib/firmware/regulatory.db','usr/lib/firmware/regulatory.db.p7s'):
        if not path(relative).is_file() or not path(relative).stat().st_size:
            raise ValueError('required management wireless firmware missing: '+relative)
    for relative, defines in [
        ('usr/share/xenova/qemu-config-host.h', ('CONFIG_VNC', 'CONFIG_XEN_BACKEND')),
        ('usr/share/xenova/qemu-i386-config-target.h', ('CONFIG_XEN',)),
    ]:
        header = path(relative)
        if not header.is_file() or any(not re.search(r'^#define ' + symbol + r'(?:\s+1)?\s*$', header.read_text(), re.M) for symbol in defines):
            raise ValueError('platform lacks built QEMU VNC/Xen configuration: ' + relative)
    if not path('usr/bin/qemu-system-i386').is_file() or not path('usr/bin/qemu-system-i386').stat().st_mode & 0o111:
        raise ValueError('required dom0 qemu-system-i386 missing')
    if not path('usr/lib/libxenmanage.so.1').is_file():
        raise ValueError('read-only Xen incarnation library missing')
    if path('etc/machine-id').read_text() != 'uninitialized\n':
        raise ValueError('common dom0 machine-id must be uninitialized for PID1')
    if any(path('etc/ssh').glob('ssh_host_*_key')):
        raise ValueError('common dom0 must not contain shared SSH host keys')
    dropin = path('etc/systemd/system/sshd.service.d/10-xenova-keys.conf')
    if dropin.read_bytes() != (FILES / 'xenova-sshd-keys.conf').read_bytes():
        raise ValueError('SSH host-key gate missing')
    service = path('usr/lib/systemd/system/sshd.service')
    keys = path('usr/lib/systemd/system/sshdgenkeys.service')
    if not service.is_file() or not keys.is_file() or not path('usr/libexec/openssh/sshd_check_keys').is_file():
        raise ValueError('dom0 SSH service/key preparation package missing')
    if any(path('usr/lib/systemd/system/' + name).exists() for name in ('sshd.socket', 'sshd@.service')):
        raise ValueError('dom0 SSH socket mode must be replaced by service mode')
    link = root / 'etc/systemd/system/multi-user.target.wants/sshd.service'
    if not link.is_symlink() or path('etc/systemd/system/multi-user.target.wants/sshd.service').read_bytes() != service.read_bytes():
        raise ValueError('dom0 SSH service not enabled')
    for directory in ('etc/systemd/system', 'usr/lib/systemd/system'):
        base = path(directory)
        for suffix in ('wants', 'requires'):
            parents = [path(str(parent.relative_to(root))) for parent in base.glob('*.' + suffix)]
            if any(p.name in ('sshd.socket', 'sshd@.service') for parent in parents for p in parent.iterdir()):
                raise ValueError('dom0 SSH socket dependency remains')
    generator = root / 'etc/systemd/system-generators/systemd-ssh-generator'
    if not generator.is_symlink() or generator.readlink() != Path('/dev/null'):
        raise ValueError('dom0 SSH generator must be disabled for the single service path')
    preset = path('usr/lib/systemd/system-preset/98-openssh-sshd.preset').read_text()
    if 'enable sshd.service' not in preset.splitlines() or 'enable sshd.socket' in preset.splitlines():
        raise ValueError('dom0 SSH preset selects wrong activation mode')
    enabled = ['xenova-host-admission.service', 'xenova-first-boot.service', 'xenova-vms.service', 'xenova-vm-shutdown.service', 'srv-xenova.mount']
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
                   'fdt move ${fdtcontroladdr} ${fdt_addr_r}', 'booti ${xen_loadaddr} - ${fdt_addr_r}',
                   'xenova_debug', 'loglevel=4', 'systemd.show_status=error'):
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
    paths = [
             ROOT / 'platform/guests/common/template_recipe.py',
             ROOT / 'scripts/build-xenova-starter.py', ROOT / 'scripts/resolve-xenova-templates.py',
             ROOT / 'scripts/verify-xenova-template.py', ROOT / 'scripts/refresh-xenova-starter-lock.py',
             ROOT / 'platform/guests/default-templates.lock.json',
             *(p for p in sorted((ROOT / 'platform/guests/linux').rglob('*')) if p.is_file()),
             ROOT / 'scripts/assemble-xenova-first-install.py', ROOT / 'scripts/prepare-xenova-guests.py',
             ROOT / 'platform/first-install/assemble.py', ROOT / 'platform/first-install/artifact_io.py',
             ROOT / 'platform/first-install/completed_artifacts.py', ROOT / 'scripts/publish-xenova-platform.py', ROOT / 'scripts/publish-xenova-template-catalog.py',
             ROOT / 'platform/first-install/first_install_contract.py',
             ROOT / 'platform/first-install/deployment_payloads.py',
             *deployment_payloads.source_files('usr/lib/python3/site-packages').values(),
             *(p for name in ('xenova-dom0-manager', 'xenova-mqtt-client')
               for p in sorted((ROOT/'platform/yocto/meta-xenova/recipes-core'/name).rglob('*')) if p.is_file()),
             *sorted(p for p in FILES.iterdir() if p.is_file()),
             ROOT / 'scripts/verify-xenova-runtime-artifacts.py', ROOT / 'scripts/package-xenova-firmware.py',
             ROOT / 'scripts/update-xenova-guest-ssh.py', ROOT / 'scripts/collect-xenova-guest-evidence.py',
             *sorted((ROOT / 'platform/guests/zephyr/starter-app').rglob('*.*')),
             ROOT / 'platform/yocto/meta-xenova/recipes-extended/xen/xen_4.21.bbappend',
             ROOT / 'platform/yocto/meta-xenova/recipes-extended/xen/files/0001-arm-rpi4-find-bcm2711-power-management-watchdog.patch',
             ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/xenova-platform_1.0.bb',
             ROOT / 'platform/yocto/meta-xenova/recipes-devtools/qemu/qemu_10.2.0.bbappend',
             ROOT / 'platform/yocto/meta-xenova/recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in',
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
        for guest in planned['linux_templates']:
            if guest.get('template'):
                templates[guest['source_id']] = Path(guest['template'])
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
                templates[guest['source_id']] = template_dir
            verified_templates[guest['source_id']] = guest.get('template_manifest_sha256') or sha(templates[guest['source_id']] / 'template.json')
        datafile = work.path / 'data.ext4'
        with datafile.open('xb') as stream:
            stream.truncate(planned['capacity']['data_bytes'])
        prepare.command(['mkfs.ext4', '-F', '-m', '0', '-L', 'xenova-data', datafile])
        data = work.path / 'data'
        work.mount(work.loop(datafile), data)
        prepare.initialize_storage(data, seed=True)
        bundles, template_records = [], []
        for guest in planned['linux_templates']:
            template = templates[guest['source_id']]
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
            default = next((d for d in planned['guests'] if d['source_id'] == guest['source_id']), None)
            if default is not None:
                prepare.make_instance(argparse.Namespace(data_root=data, id=default['id'], spec=None, deployment_seed=True, template_root=template))
                seed = data / 'seeds' / default['id']
                r = json.loads((seed / 'instance.json').read_text())
                boot = json.loads((seed / 'boot.json').read_text())
                bundles.append({'record': r, 'root_bytes': (seed / 'root.raw').stat().st_size,
                                'seed_root_sha256': sha(seed / 'root.raw'), 'boot': boot})
            template_records.append({'id': guest['source_id'], 'metadata': tmeta, 'root_bytes': (template / 'root.raw').stat().st_size, 'template_manifest_sha256': sha(template / 'template.json'),
                                     'retained_root': planned['capacity']['template_policy'] == 'full'})
        firmware_records = copy_rtos_templates(data, planned.get('rtos_templates', []))
        firmware_instances = copy_rtos_seeds(data, planned.get('rtos_templates', []))
        copy_management_catalog(data)
        factory = {'schema': 1, 'features': FEATURES, 'factory_id': str(uuid.uuid4()), 'instances': bundles,
                   'templates': template_records, 'rtos_templates': firmware_records, 'firmware_instances': firmware_instances,
                   'template_policy': planned['capacity']['template_policy']}
        (data / '.xenova-install.json').write_text(json.dumps(factory, indent=2) + '\n')
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
        for guest in planned['linux_templates']:
            template = templates[guest['source_id']]
            template_info(template, guest['source_id'], verified_templates[guest['source_id']])
        for entry in planned.get('rtos_templates', []):
            rtos.info(entry['template'], entry['template_manifest_sha256'])
        final = stage / 'xenova-first-install.img'
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
        target_manifest = stage / 'xenova-first-install.manifest.json'
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
        print(json.dumps(verify(args.manifest.parent / 'xenova-first-install.img', manifest), indent=2))
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
