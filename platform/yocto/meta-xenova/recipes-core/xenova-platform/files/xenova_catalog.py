"""명시적 템플릿 ID/배포 버전과 불변 payload manifest를 연결한다. 원본 출처는 바꾸지 않는다."""
import hashlib
import json
from pathlib import Path
import re
import stat
import time
from xenova_runtime import PlatformError

TEMPLATE_ID = re.compile(r'[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z')
VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[a-z0-9.-]+)?\Z')
DEFAULT_IDS = {'ubuntu-24.04-20260926': 'ubuntu-24.04-arm64',
               'debian-13-20261015': 'debian-13-arm64',
               'xenova-zephyr-starter-1.0.0': 'zephyr-xenvm-starter-arm64',
               'xenova-zephyr-network-starter-1.1.0':'zephyr-xenvm-starter-arm64'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def plain(path, *, directory=False):
    path = Path(path)
    if path.resolve() != path or path.is_symlink():
        raise PlatformError('linked/noncanonical asset path')
    st = path.stat()
    if directory:
        if not stat.S_ISDIR(st.st_mode):
            raise PlatformError('asset directory required')
    elif not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise PlatformError('unshared regular asset required')
    return path


def identity(template_id, version):
    if not isinstance(template_id, str) or not TEMPLATE_ID.fullmatch(template_id):
        raise PlatformError('invalid normalized template ID')
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PlatformError('explicit semantic template version required')


def deployment_version(meta):
    """공개 배포 버전은 명시적 template_version 우선, 없을 때만 legacy version을 사용한다."""
    return meta.get('template_version', meta.get('version'))


def describe(data, descriptor, *, full=False):
    required = {'schema_version', 'template_id', 'template_version', 'guest_kind',
                'asset_directory', 'manifest_sha256', 'source_template_id'}
    if not isinstance(descriptor, dict) or set(descriptor) != required or descriptor['schema_version'] != '1.0':
        raise PlatformError('invalid template catalog descriptor')
    identity(descriptor['template_id'], descriptor['template_version'])
    rel = Path(descriptor['asset_directory'])
    if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in ('templates', 'rtos-templates'):
        raise PlatformError('catalog asset escapes template storage')
    directory = plain(Path(data) / rel, directory=True)
    if descriptor['guest_kind'] not in ('linux', 'zephyr'):
        raise PlatformError('unsupported catalog guest kind')
    manifest = 'firmware.json' if (directory / 'firmware.json').exists() else 'template.json'
    path = plain(directory / manifest)
    if (not isinstance(descriptor['manifest_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', descriptor['manifest_sha256'])
            or sha(path) != descriptor['manifest_sha256'] or path.stat().st_mode & 0o222):
        raise PlatformError('catalog manifest hash/immutability mismatch')
    meta = json.loads(path.read_text())
    if not isinstance(meta,dict) or not isinstance(meta.get('source'),dict):
        raise PlatformError('template metadata/source must be objects')
    if meta.get('id') != descriptor['source_template_id'] or deployment_version(meta) != descriptor['template_version']:
        raise PlatformError('catalog deployment identity differs from source manifest')
    declared = meta.get('template_id',DEFAULT_IDS.get(descriptor['source_template_id']))
    if declared is not None and declared != descriptor['template_id']:
        raise PlatformError('public template identity differs from declared artifact mapping')
    linux = descriptor['guest_kind'] == 'linux'
    if linux:
        if meta.get('source', {}).get('distribution') not in ('ubuntu', 'debian'):
            raise PlatformError('unsupported Linux source')
        names = {'root.raw', 'Image', 'initrd', 'kernel.config', 'boot.json'}
        if set(meta.get('files', {})) != names:
            raise PlatformError('complete immutable Linux payload manifest required')
        boot = json.loads(plain(directory / 'boot.json').read_text())
        if not isinstance(boot,dict) or boot.get('source') != meta.get('source'):
            raise PlatformError('boot/root provenance mismatch')
        for expected in meta['files'].values():
            if (not isinstance(expected, dict) or type(expected.get('bytes')) is not int or expected['bytes'] < 0
                    or not isinstance(expected.get('sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', expected['sha256'])):
                raise PlatformError('Linux payload metadata invalid')
        if meta['files']['root.raw']['sha256'] != meta.get('root_sha256'):
            raise PlatformError('Linux root digest mismatch')
        for name, field in [('Image','kernel_sha256'),('initrd','initrd_sha256'),('kernel.config','config_sha256')]:
            if meta['files'][name]['sha256'] != boot.get(field):
                raise PlatformError('Linux boot manifest mismatch')
        for name in names:
            asset = plain(directory / name)
            expected = meta['files'][name]
            if asset.stat().st_mode & 0o222 or asset.stat().st_size != expected['bytes']:
                raise PlatformError('Linux payload size/immutability mismatch')
            if full:
                actual = sha(asset)
                if actual != expected['sha256']:
                    raise PlatformError('Linux payload digest mismatch')
                # 두 manifest 항목은 같은 단계에서 읽은 원본의 동일 digest와 대조한다.
                if name == 'root.raw' and actual != meta.get('root_sha256'):
                    raise PlatformError('Linux root digest mismatch')
        defaults = {'vcpus': 1, 'memory_mib': 512}
        limits = {'vcpus': [1, 4], 'memory_mib': [128, 8192]}
        capabilities = {'graceful_shutdown': True, 'ssh': True, 'dhcp': True, 'guest_writable_persistent_storage': True}
    else:
        import xenova_zephyr as zephyr
        if meta.get('kind') != 'zephyr-xen-firmware':
            raise PlatformError('Zephyr catalog firmware required')
        if full:
            zephyr.bundle_info(directory, descriptor['manifest_sha256'])
        defaults = {'vcpus': 1, 'memory_mib': 16}
        limits = {'vcpus': [1, 1], 'memory_mib': [16, 16]}
        capabilities = dict(zephyr.NET_CAPABILITIES if meta.get("hardware_profile")==zephyr.NET_PROFILE else zephyr.CAPABILITIES)
        # 기존 불변 firmware manifest는 유지하고 확인된 Starter의 관측 계약만 보완한다.
        if meta["source"].get("app") in ("xenova-starter", "xenova-network-starter"):
            capabilities["app_heartbeat"] = False
    from xenova_guest_profile import from_manifest
    profile=from_manifest(meta,config=(directory/'zephyr.config').read_text() if not linux else '')
    return {**descriptor, 'default_resources': defaults, 'resource_limits': limits,'provisioning_profile':profile,
            'capabilities': capabilities, 'source': meta['source'], 'directory': str(directory)}


def entries(data, *, deadline=None, clock=time.monotonic):
    def check():
        if deadline is not None and clock()>=deadline:
            from xenova_execution import refuse
            refuse('DEADLINE_EXCEEDED','catalog read budget expired')
    check()
    root = Path(data) / 'catalog'
    if not root.exists() and not root.is_symlink():
        return []
    plain(root, directory=True)
    result = []
    for parent in sorted(root.iterdir()):
        check()
        plain(parent, directory=True)
        for path in sorted(parent.iterdir()):
            check()
            plain(path)
            meta = json.loads(path.read_text())
            if not isinstance(meta,dict):
                raise PlatformError('catalog descriptor object required')
            if parent.name != meta.get('template_id') or path.name != meta.get('template_version', '') + '.json':
                raise PlatformError('catalog identity/path mismatch')
            result.append(describe(data, meta))
    check()
    return result
