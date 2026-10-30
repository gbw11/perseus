"""Zephyr 펌웨어 검증·독립 복사·원자적 활성 버전 전환. 고정 Starter 및 별도 네트워크 프로필을 검증한다."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct

from first_install_contract import uuid4
from artifact_io import publish
from xenova_runtime import PlatformError, atomic_write, sync_directory

PAYLOADS = {'zephyr.bin', 'zephyr.elf', 'zephyr.config', 'zephyr.dts', 'xen.cfg.in', 'LICENSE'}
CAPABILITIES = {'persistent_registration': True, 'independent_firmware': True,
                'pv_console': True, 'autostart': True, 'graceful_shutdown': False,
                'ssh': False, 'dhcp': False, 'guest_writable_persistent_storage': False,
                'app_readiness': False}
PROFILE = {
    'id': 'xenvm-arm64-gicv2-16m-v1', 'vcpus': 1, 'memory_mib': 16,
    'ram': [0, 0x40000000, 0, 0x1000000],
    'gic': {'version': 'v2', 'interrupt_cells': 4,
            'reg': [0, 0x3001000, 0, 0x1000, 0, 0x3002000, 0, 0x2000]},
    'grant_table': {'reg': [0, 0x38000000, 0, 0x1000000]},
    'event_channel': {'irq': 31, 'cells': [1, 15, 4, 160], 'trigger': 'edge', 'encoding': 'Zephyr IRQ_TYPE_EDGE=BIT(2)'},
    'timer': {'irqs': [29, 30, 27], 'cells': [1, 13, 2, 160, 1, 14, 2, 160, 1, 11, 2, 160],
              'trigger': 'level', 'encoding': 'Zephyr IRQ_TYPE_LEVEL=BIT(1)', 'frequency': 'runtime-register'},
    'xen_source_comparison': {'interrupt_cells': 3, 'ppi_cpumask': 15,
                              'timer_flags': 0xf08, 'event_channel_flags': 0xf08,
                              'encoding': 'standard DT: (cpumask << 8) | level-low(8)',
                              'event_trigger_difference': 'Zephyr=edge; Xen source=level-low; board validation required'},
    'validation': 'static-firmware-checked', 'runtime_hardware_verified': False,
}
# Separate active profile; the legacy Starter and its immutable bundle stay valid.
NET_PROFILE = copy.deepcopy(PROFILE)
NET_PROFILE.update(id='xenvm-arm64-gicv2-net-16m-v1')
NET_PROFILE['event_channel'].update(cells=[1,15,2,160],trigger='level',
                                    encoding='Zephyr IRQ_TYPE_LEVEL=BIT(1)')
NET_PROFILE['xen_source_comparison']['event_trigger_difference'] = 'Zephyr level IRQ; board validation required'
NET_CAPABILITIES = {**CAPABILITIES, 'dhcp':True, 'graceful_shutdown':True,
                    'ethernet':True, 'ipv4':True, 'icmpv4':True, 'cooperative_reboot':True}


def networked(record):
    return (record.get('hardware_profile') == NET_PROFILE and
            record.get('capabilities') == NET_CAPABILITIES)


REQUIRED_CONFIG = ('CONFIG_ARM64=y', 'CONFIG_XEN=y', 'CONFIG_GIC_V2=y',
                   'CONFIG_UART_XEN_HVC=y', 'CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=y',
                   'CONFIG_SRAM_BASE_ADDRESS=0x40000000', 'CONFIG_SRAM_SIZE=16384')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}\Z')


def plain(directory):
    directory = Path(directory)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
        raise PlatformError('firmware directory must be canonical and unlinked')
    return directory


def asset(directory, name):
    path = directory / name
    if path.is_symlink() or not path.is_file() or not path.stat().st_size or path.stat().st_nlink != 1:
        raise PlatformError('firmware asset missing/linked/shared: ' + str(path))
    return path


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _cells(node, prop):
    match = re.search(r'(?<![\w-])' + re.escape(prop) + r'\s*=\s*<([^>]+)>\s*;', node)
    if not match:
        raise PlatformError('static hardware property missing: ' + prop)
    try:
        return [int(token, 0) for token in match[1].split()]
    except ValueError as exc:
        raise PlatformError('unsupported static hardware encoding: ' + prop) from exc


def payload_ok(directory, *, config_name='zephyr.config', profile=None):
    """빌드 결과의 RAM·GIC·IRQ cell/flag·grant·timer 계약을 검사한다. 실보드 일치는 미검증이다."""
    profile = PROFILE if profile is None else profile
    config = asset(directory, config_name).read_text().splitlines()
    if profile == NET_PROFILE and any(line not in config for line in (
            'CONFIG_XENOVA_RUNTIME=y','CONFIG_XENOVA_NETFRONT=y','CONFIG_NETWORKING=y',
            'CONFIG_NET_L2_ETHERNET=y','CONFIG_NET_IPV4=y','CONFIG_NET_ARP=y','CONFIG_NET_DHCPV4=y',
            '# CONFIG_XEN_GRANT_TABLE is not set')):
        raise PlatformError('network firmware lacks bounded netfront/shared Store profile')
    if any(option not in config for option in REQUIRED_CONFIG):
        raise PlatformError('firmware lacks fixed Xen/GICv2/16MiB/runtime timer configuration')
    binary, elf = asset(directory, 'zephyr.bin').read_bytes(), asset(directory, 'zephyr.elf').read_bytes()
    if len(binary) < 64 or binary[56:60] != b'ARM\x64':
        raise PlatformError('firmware requires ARM64 Image header')
    if (len(elf) < 64 or elf[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', elf, 18)[0] != 183
            or not 0x40000000 <= struct.unpack_from('<Q', elf, 24)[0] < 0x41000000):
        raise PlatformError('firmware ELF architecture/entry outside fixed RAM')
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', asset(directory, 'zephyr.dts').read_text(), flags=re.S)
    for node, properties in [
        ('memory@40000000', {'reg': profile['ram']}),
        ('interrupt-controller@3001000', {'reg': profile['gic']['reg'], '#interrupt-cells': [4]}),
        ('hypervisor@38000000', {'reg': profile['grant_table']['reg'], 'interrupts': profile['event_channel']['cells']}),
        ('timer', {'interrupts': profile['timer']['cells']}),
    ]:
        matches = re.findall(r'(?<![\w@-])' + node + r'\s*\{([^{}]*)\}', text)
        if len(matches) != 1:
            raise PlatformError('static hardware node missing/ambiguous: ' + node)
        for prop, expected in properties.items():
            if _cells(matches[0], prop) != expected:
                raise PlatformError('static hardware profile mismatch: ' + node + '/' + prop)
    if 'xen,hvc-uart' not in text or not re.search(r'cpu@0\s*\{', text) or re.search(r'cpu@[1-9a-fA-F]', text):
        raise PlatformError('firmware PV console or single-vCPU profile mismatch')


def bundle_info(directory, expected=None):
    """기존 불변 template.json 또는 팀 앱용 firmware.json 번들을 전체 검증한다."""
    directory = plain(directory)
    names = {p.name for p in directory.iterdir()}
    manifest = 'firmware.json' if 'firmware.json' in names else 'template.json'
    raw_meta = json.loads(asset(directory,manifest).read_text())
    network = raw_meta.get('hardware_profile') == NET_PROFILE
    payloads = PAYLOADS | ({'zephyr.version.h'} if manifest == 'firmware.json' else set()) | ({'zephyr.map','XEN-ABI-LICENSE'} if network else set())
    if names != payloads | {manifest}:
        raise PlatformError('unexpected firmware bundle files')
    path = asset(directory, manifest)
    digest = sha(path)
    if expected is not None and digest != expected:
        raise PlatformError('firmware manifest hash mismatch')
    meta = json.loads(path.read_text())
    if not isinstance(meta, dict) or meta.get('schema') != 1 or not isinstance(meta.get('files'), dict) or set(meta['files']) != payloads:
        raise PlatformError('unsupported firmware manifest')
    if manifest == 'template.json':
        source = meta.get('source', {})
        if (not isinstance(source, dict) or meta.get('kind') != 'zephyr-xen-firmware' or source.get('version') != '4.4.0'
                or meta.get('memory_mib') != 16 or meta.get('vcpus') != 1 or meta.get('gic_version') != 'v2'
                or meta.get('network') != 'unsupported' or meta.get('root_disk') is not None
                or meta.get('runtime_hardware_verified') is not False):
            raise PlatformError('unsupported legacy Zephyr template policy')
        version = source['version']
    else:
        source = meta.get('source', {})
        version = meta.get('version', '')
        if (meta.get('guest_kind') != 'zephyr' or meta.get('hardware_profile') != (NET_PROFILE if network else PROFILE)
                or meta.get('capabilities') != (NET_CAPABILITIES if network else CAPABILITIES) or not TOKEN.fullmatch(version)
                or not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k].strip()
                                                       for k in ('zephyr_version', 'app', 'source_revision', 'build_id'))
                or source['zephyr_version'] != '4.4.0'):
            raise PlatformError('unsupported firmware provenance/profile/capabilities')
    total = 0
    for name in sorted(names):
        p = asset(directory, name)
        size = p.stat().st_size
        total += size
        if p.stat().st_mode & 0o222 or total > 64 * 1024 ** 2:
            raise PlatformError('firmware must be read-only and bounded to 64MiB')
        if name != manifest:
            spec = meta['files'][name]
            if not isinstance(spec, dict) or type(spec.get('bytes')) is not int or spec['bytes'] != size or sha(p) != spec.get('sha256'):
                raise PlatformError('firmware payload hash/size mismatch: ' + name)
    payload_ok(directory, profile=NET_PROFILE if network else PROFILE)
    ids=set(re.findall(rb'xenova-build:(sha256:[0-9a-f]{64})',asset(directory,'zephyr.bin').read_bytes()))
    if len(ids)>1 or (ids and source.get('build_id') != next(iter(ids)).decode('ascii')):
        raise PlatformError('firmware embedded build identity/manifest mismatch')
    if manifest == 'firmware.json' and not re.search(
            r'^#define KERNEL_VERSION_STRING\s+"4\.4\.0"\s*$', asset(directory, 'zephyr.version.h').read_text(), re.M):
        raise PlatformError('firmware build version header must declare Zephyr 4.4.0')
    return {'manifest': manifest, 'revision': digest, 'version': version,
            'sha256': meta['files']['zephyr.bin']['sha256'], 'source': source,
            'logical_bytes': total, 'payloads': sorted(payloads),
            **({'hardware_profile':NET_PROFILE, 'capabilities':NET_CAPABILITIES} if network else {})}


def active_directory(directory, record):
    active = record.get('active_firmware', {})
    revision = active.get('revision', '') if isinstance(active, dict) else ''
    if not isinstance(active, dict) or not DIGEST.fullmatch(revision):
        raise PlatformError('invalid active firmware revision')
    plain(directory / 'firmware')
    return plain(directory / 'firmware' / revision)


def validate_instance(record, directory):
    network = networked(record)
    if (record.get('schema') != 2 or record.get('guest_kind') != 'zephyr'
            or record.get('hardware_profile') != (NET_PROFILE if network else PROFILE)
            or record.get('capabilities') != (NET_CAPABILITIES if network else CAPABILITIES)
            or record.get('shutdown_policy') != ('cooperative-xenstore' if network else 'explicit-force')
            or type(record.get('memory_mib')) is not int or record['memory_mib'] != 16
            or type(record.get('vcpus')) is not int or record['vcpus'] != 1):
        raise PlatformError('invalid persistent Zephyr profile/capabilities/resources')
    origin = record.get('creation_origin', {})
    if (not isinstance(origin, dict) or not TOKEN.fullmatch(record.get('template', '')) or origin.get('template') != record['template']
            or not DIGEST.fullmatch(origin.get('manifest_sha256', '')) or not isinstance(origin.get('source'), dict)):
        raise PlatformError('invalid Zephyr creation origin')
    info = bundle_info(active_directory(directory, record), record['active_firmware']['revision'])
    if network != ('hardware_profile' in info):
        raise PlatformError('active firmware/profile capability mismatch')
    if record['active_firmware'] != {k: info[k] for k in ('manifest', 'revision', 'version', 'sha256')}:
        raise PlatformError('active firmware metadata/hash mismatch')
    return record


def copy_revision(source, parent, expected):
    """복사 중단은 숨김 stage만 남긴다. 이미 활성인 revision을 덮어쓰지 않는다."""
    info = bundle_info(source, expected)
    parent.mkdir(exist_ok=True)
    plain(parent)
    target = parent / info['revision']
    if target.exists() or target.is_symlink():
        bundle_info(target, expected)
        return info
    stage = parent / ('.staging-' + uuid4())
    stage.mkdir(mode=0o700)
    for name in sorted(set(info['payloads']) | {info['manifest']}):
        shutil.copyfile(asset(source, name), stage / name)
        (stage / name).chmod(0o444)
        with (stage / name).open('rb') as stream:
            os.fsync(stream.fileno())
    bundle_info(stage, expected)
    sync_directory(stage)
    publish(stage, target)
    sync_directory(parent)
    return info


def new_instance(data, key, template, records, *, autostart=False, board_shutdown_policy='refuse', registry='instances', seed=False, identity=None, workspace_path=None, metadata=None):
    """신규 인스턴스만 생성한다. seed는 새 조립 저장소에서만 호출한다."""
    if board_shutdown_policy not in ('graceful', 'refuse', 'force-allowed'):
        raise PlatformError('invalid Zephyr board shutdown policy')
    data, template = Path(data), plain(template)
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', key) or key in records:
        raise PlatformError('new valid instance id required')
    plain(data / registry)
    output = data / registry / key
    if output.exists() or output.is_symlink():
        raise PlatformError('instance output already exists')
    info = bundle_info(template)
    meta = json.loads(asset(template, info['manifest']).read_text())
    if meta.get('kind') != 'zephyr-xen-firmware' or not TOKEN.fullmatch(meta.get('id', '')):
        raise PlatformError('create requires an immutable catalog template')
    identity = identity or uuid4()
    if any(r['uuid'] == identity or r['name'] == key for r in records.values()):
        raise PlatformError('new instance UUID/name conflicts with registry')
    network = 'hardware_profile' in info
    record = {'schema': 2, 'guest_kind': 'zephyr', 'id': key, 'name': key, 'uuid': identity,
              'template': meta['id'], 'autostart': bool(autostart), 'memory_mib': 16, 'vcpus': 1,
              'hardware_profile': copy.deepcopy(NET_PROFILE if network else PROFILE), 'capabilities': dict(NET_CAPABILITIES if network else CAPABILITIES),
              'shutdown_policy': 'cooperative-xenstore' if network else 'explicit-force', 'board_shutdown_policy': board_shutdown_policy,
              'creation_origin': {'template': meta['id'], 'manifest_sha256': info['revision'], 'source': info['source']},
              'active_firmware': {k: info[k] for k in ('manifest', 'revision', 'version', 'sha256')}}
    if metadata:
        record.update(metadata)
    if network and not all(record.get(k) for k in ('mac','ipv4','hostname')):
        raise PlatformError('network firmware requires platform-assigned MAC/IP/hostname')
    if not network and board_shutdown_policy == 'graceful':
        raise PlatformError('legacy Starter has no cooperative shutdown')
    if seed:
        record.update(uuid='00000000-0000-4000-8000-000000000000', identity_state='pending')
    staging = Path(workspace_path) if workspace_path is not None else data / registry / ('.preparing-' + uuid4())
    staging.mkdir(mode=0o700)
    stage = staging / key
    stage.mkdir()
    copy_revision(template, stage / 'firmware', info['revision'])
    atomic_write(stage / 'instance.json', json.dumps(record, indent=2) + '\n')
    validate_instance(record, stage)
    sync_directory(stage)
    publish(stage, output)
    sync_directory(output.parent)
    return record


def replace_firmware(directory, record, source, expected):
    """호출자가 공통 잠금과 정지 상태를 확인한 뒤 호출한다. 실패 시 기존 참조를 보존한다."""
    if not DIGEST.fullmatch(expected or ''):
        raise PlatformError('firmware replacement requires --sha256 of the manifest')
    validate_instance(record, directory)
    info = copy_revision(Path(source), directory / 'firmware', expected)
    updated = copy.deepcopy(record)
    updated['active_firmware'] = {k: info[k] for k in ('manifest', 'revision', 'version', 'sha256')}
    validate_instance(updated, directory)
    atomic_write(directory / 'instance.json', json.dumps(updated, indent=2) + '\n')
    return updated


def _config(record, directory):
    kernel = active_directory(directory, record) / 'zephyr.bin'
    return '\n'.join([f"name = '{record['name']}'", f"uuid = '{record['uuid']}'",
                      f"kernel = '{kernel}'", 'vcpus = 1', 'memory = 16', "gic_version = 'v2'",
                      "on_poweroff = 'destroy'", "on_reboot = 'destroy'", "on_crash = 'preserve'",
                      *([f"vif = [ 'mac={record['mac']},bridge=xenbr0,script=vif-bridge' ]"] if networked(record) else []), ''])


def config(record, directory, *, cpu_context=None):
    """CPU placement is separate from the unchanged RAM/GIC/firmware profile."""
    from xenova_cpu import affinity_text
    return _config(record, directory)+"\n"+affinity_text(cpu_context)
