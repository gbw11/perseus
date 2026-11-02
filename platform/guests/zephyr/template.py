"""Zephyr 불변 펌웨어 번들 검증. Linux root.raw·인스턴스 레지스트리와 섞지 않는다."""
import json
from pathlib import Path
import struct

from artifact_io import checked, regular, sha

HERE = Path(__file__).resolve().parent
FILES = {'zephyr.bin', 'zephyr.elf', 'zephyr.config', 'zephyr.dts', 'xen.cfg.in', 'LICENSE'}
REQUIRED_CONFIG = ('CONFIG_ARM64=y', 'CONFIG_XEN=y', 'CONFIG_GIC_V2=y',
                   'CONFIG_UART_XEN_HVC=y', 'CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=y',
                   'CONFIG_SRAM_BASE_ADDRESS=0x40000000', 'CONFIG_SRAM_SIZE=16384')


def source_lock():
    return json.loads((HERE / 'source.lock.json').read_text())


def payload_ok(directory):
    """펌웨어 형식과 빌드된 Xen/GICv2/메모리 계약을 확인한다. 실부팅 증명은 아니다."""
    config = regular(directory / 'zephyr.config').read_text().splitlines()
    if any(option not in config for option in REQUIRED_CONFIG):
        raise ValueError('Zephyr build lacks the frozen Xen/GICv2/timer/memory contract')
    binary = regular(directory / 'zephyr.bin').read_bytes()
    elf = regular(directory / 'zephyr.elf').read_bytes()
    if len(binary) < 64 or binary[56:60] != b'ARM\x64':
        raise ValueError('expected an ARM64 Image header in Zephyr firmware')
    if (len(elf) < 64 or elf[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', elf, 18)[0] != 183
            or not 0x40000000 <= struct.unpack_from('<Q', elf, 24)[0] < 0x41000000):
        raise ValueError('expected AArch64 ELF in the 16MiB Xen guest RAM range')
    if regular(directory / 'xen.cfg.in').read_bytes() != (HERE / 'xen.cfg.in').read_bytes():
        raise ValueError('Xen firmware configuration differs from frozen contract')


def info(directory, expected_manifest):
    """전체 파일 hash·권한을 검사하고 실측 용량을 반환한다. 쓰기·다운로드는 하지 않는다."""
    directory = Path(directory)
    if not directory.is_absolute() or directory.resolve() != directory or directory.is_symlink():
        raise ValueError('RTOS template path must be canonical without links')
    if (directory / 'firmware.json').exists():
        # 신규 Starter 카탈로그는 앱 출처를 가진 firmware.json을 그대로 사용한다.
        # 기존 synchronization의 엄격한 source.lock 검증은 아래에서 유지한다.
        import xenova_zephyr as zephyr
        info = zephyr.bundle_info(directory, expected_manifest)
        meta = json.loads((directory / 'firmware.json').read_text())
        if meta.get('id')=='xenova-zephyr-drive-lattice-lab-1.0.0':
            selected=json.loads((HERE.parent/'default-templates.lock.json').read_text())['templates']
            matches=[item for item in selected if item['id']==meta['id']]
            if (len(matches)!=1 or matches[0]['manifest_sha256']!=expected_manifest
                    or meta.get('template_id')!=matches[0]['template_id']
                    or meta.get('template_version')!=matches[0]['version']
                    or meta['source'].get('app')!='xenova-drive'
                    or meta['source'].get('drive_profile')!='warm-lattice-lab'
                    or info['provisioning_profile']['apps']!={
                        'autonomous':{'model':'stateful','standby_progress':'idle'},
                        'manual':{'model':'stateless','standby_progress':'idle'}}):
                raise ValueError('actual selected drive build/provenance required')
            return {'id':meta['id'],'kind':'zephyr-xen-firmware','template':str(directory),
                    'template_manifest_sha256':expected_manifest,'metadata':meta,
                    'logical_bytes':info['logical_bytes'],'payloads':info['payloads'],'bundle_manifest':'firmware.json'}
        lock_file = 'network-starter.lock.json' if meta.get('hardware_profile') == zephyr.NET_PROFILE else 'starter.lock.json'
        lock = json.loads((HERE / lock_file).read_text())
        if (meta.get('id') != lock['id'] or meta.get('kind') != 'zephyr-xen-firmware'
                or meta.get('version') != lock['version']
                or any(meta['source'].get(k) != lock[k] for k in ('app', 'app_files', 'app_source_sha256', 'zephyr_commit', 'sdk_version'))
                or meta['source'].get('source_revision') != 'sha256:' + lock['app_source_sha256']):
            raise ValueError('unexpected Starter catalog provenance')
        return {'id': lock['id'], 'kind': 'zephyr-xen-firmware', 'template': str(directory),
                'template_manifest_sha256': expected_manifest, 'logical_bytes': info['logical_bytes'],
                'allocated_bytes': sum(p.stat().st_blocks * 512 for p in directory.iterdir()),
                'metadata': meta, 'autostart': False, 'storage_directory': 'rtos-templates',
                'runtime_hardware_verified': False, 'bundle_manifest': info['manifest'], 'payloads': info['payloads']}
    checked(directory / 'template.json', expected_manifest)
    meta = json.loads((directory / 'template.json').read_text())
    lock = source_lock()
    if (meta.get('schema') != 1 or meta.get('kind') != lock['kind'] or meta.get('id') != lock['id']
            or meta.get('source') != lock or set(meta.get('files', {})) != FILES
            or set(p.name for p in directory.iterdir()) != FILES | {'template.json'}):
        raise ValueError('unexpected RTOS template provenance or payload')
    policy = {'memory_mib': 16, 'vcpus': 1, 'gic_version': 'v2', 'autostart': False,
              'network': 'unsupported', 'root_disk': None, 'runtime_hardware_verified': False}
    if any(key not in meta or type(meta[key]) is not type(value) or meta[key] != value for key, value in policy.items()):
        raise ValueError('unsupported RTOS runtime policy or unverified capability claim')
    logical = allocated = 0
    for name in sorted(FILES | {'template.json'}):
        file = regular(directory / name)
        if file.stat().st_mode & 0o222:
            raise ValueError('RTOS template files must be read-only')
        if name != 'template.json':
            checked(file, meta['files'][name]['sha256'])
            if file.stat().st_size != meta['files'][name]['bytes']:
                raise ValueError('RTOS file size mismatch')
        logical += file.stat().st_size
        allocated += file.stat().st_blocks * 512
    if logical > 64 * 1024 ** 2:
        raise ValueError('RTOS bundle exceeds the bounded 64MiB catalog size')
    payload_ok(directory)
    return {'id': lock['id'], 'kind': lock['kind'], 'template': str(directory),
            'template_manifest_sha256': expected_manifest, 'logical_bytes': logical,
            'allocated_bytes': allocated, 'metadata': meta, 'autostart': False,
            'storage_directory': 'rtos-templates', 'runtime_hardware_verified': False}
