"""PC와 Pi에서 공유하는 명시적 최초 인스턴스 생성. lifecycle start/stop은 호출하지 않는다."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

from first_install_contract import uuid4
from artifact_io import publish
from xenova_catalog import deployment_version
from prepare_rootfs import prepare_instance, prepare_deployment_seed, inside


def command(args, **kwargs):
    kwargs.setdefault('check', True)
    return subprocess.run([str(a) for a in args], **kwargs)


def digest(path):
    return subprocess.check_output(['sha256sum', str(path)], text=True, timeout=900).split()[0]


def require_new_output(path):
    if not path.is_absolute() or path.is_symlink() or path.exists():
        raise ValueError('output must be a new absolute path, never an existing image/instance')
    if path.name.startswith('.') or not path.parent.is_dir():
        raise ValueError('output needs an existing parent and a non-hidden name')
    if str(path.resolve()).startswith(('/dev/', '/proc/', '/sys/')):
        raise ValueError('output cannot be a device or virtual filesystem')


class Workspace:
    """전용 임시 경로·직접 할당한 loop 장치만 사용하고 해제 실패 시 보존한다."""
    def __init__(self, parent, owned_path=None):
        self.path = Path(owned_path) if owned_path is not None else Path(parent) / ('.xenova-preparing-' + uuid4())
        self.path.mkdir(mode=0o700)
        self.mounts, self.loops = [], []

    def loop(self, image, readonly=False, partitions=False):
        args = ['losetup', '--find', '--show']
        if readonly:
            args.append('--read-only')
        if partitions:
            args.append('--partscan')
        value = subprocess.check_output(args + [str(image)], text=True).strip()
        if not __import__('re').fullmatch(r'/dev/loop[0-9]+', value):
            raise ValueError('unexpected allocated loop device')
        self.loops.append(value)
        return value

    def mount(self, device, target, readonly=False):
        target.mkdir(parents=True, exist_ok=True)
        command(['mount', '-o', 'ro,noload' if readonly else 'rw', device, target])
        self.mounts.append(target)

    def close(self):
        while self.mounts:
            command(['umount', self.mounts[-1]])
            self.mounts.pop()
        while self.loops:
            command(['losetup', '--detach', self.loops[-1]])
            self.loops.pop()
        # 디버깅과 실패 분석을 위해 작업 경로를 자동 삭제하지 않는다.
        print('scratch retained for inspection:', self.path)


@contextmanager
def guest_runtime(work, root):
    """SSH 오프라인 검사에만 필요한 /dev·/run을 전용 tmpfs로 덮고 역순 해제한다.

    호스트 /dev를 bind하지 않는다. 기존 rootfs 디렉터리·키·런타임 파일은 보존한다.
    해제 실패는 호출자에게 전달하며 미해제 항목은 Workspace.close의 재시도를 위해 남긴다.
    """
    checkpoint = len(work.mounts)
    try:
        for relative, options in [('dev', 'size=1m,mode=755,nosuid,noexec'),
                                  ('run', 'size=1m,mode=755,nosuid,nodev,noexec')]:
            target = inside(root, relative, follow_final=False, create_parents=False)
            if target.is_symlink() or not target.is_dir():
                raise ValueError('guest runtime mountpoint must be an existing unlinked directory: ' + relative)
            command(['mount', '-t', 'tmpfs', '-o', options, 'tmpfs', target])
            work.mounts.append(target)
            if relative == 'dev':
                # fresh tmpfs 안의 단일 장치만 만든다. inside()의 /dev 경로 거부를 우회하는 범위도 여기뿐이다.
                null = target / 'null'
                os.mknod(null, stat.S_IFCHR | 0o666, os.makedev(1, 3))
                null.chmod(0o666)
        inside(root, 'run/sshd').mkdir(mode=0o755)
        yield
    finally:
        while len(work.mounts) > checkpoint:
            command(['umount', work.mounts[-1]])
            work.mounts.pop()


def verify_guest_ssh(root, *, seed=False):
    # -C의 Match 조건 검사는 -T가 필요하다. seed의 -G는 공유 host key 없이 설정만 검사한다.
    options = ['-G', '-T'] if seed else ['-T']
    effective = subprocess.check_output(
        ['chroot', str(root), '/usr/sbin/sshd', *options, '-C',
         'user=root,host=xenova,addr=192.168.77.1,laddr=192.168.77.10,lport=22'],
        text=True, timeout=60)
    for entry in ('permitrootlogin yes', 'passwordauthentication yes', 'permitemptypasswords no', 'authenticationmethods any'):
        if entry not in effective.splitlines():
            raise ValueError('guest SSH effective policy mismatch: ' + entry)


def seed_store_check(data):
    """조립기가 만든 새 마운트만 seed를 받는다. 기존 인스턴스 저장소는 거부한다."""
    result = json.loads(subprocess.check_output(['findmnt', '--json', '--mountpoint', str(data), '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'], text=True))['filesystems'][0]
    if (result['fstype'] != 'ext4' or result['target'] != str(data)
            or not result['source'].startswith('/dev/loop') or 'rw' not in result['options'].split(',')):
        raise ValueError('factory seed preparation requires the private writable ext4 loop mount')
    marker = data / '.xenova-seed-store.json'
    if marker.is_symlink() or json.loads(marker.read_text()) != {'schema': 1, 'purpose': 'factory-seeds'}:
        raise ValueError('not a factory seed staging store')
    if (data / '.xenova-storage.json').exists() or any((data / 'instances').iterdir()):
        raise ValueError('existing live storage/instances must never become factory seeds')


def make_instance(args, *, vm, defaults_path, record_override=None, identity=None, workspace_path=None, template_directory=None):
    seed = getattr(args, 'deployment_seed', False)
    # 새 출력의 부모도 같은 저장소 안의 일반 디렉터리여야 한다.
    for directory in [args.data_root, args.data_root / 'templates', args.data_root / 'instances', *([args.data_root / 'seeds'] if seed else [])]:
        if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
            raise ValueError('instance storage directories must be canonical and unlinked')
    if seed:
        seed_store_check(args.data_root)
    else:
        vm.storage_check(args.data_root)
    defaults = json.loads(Path(defaults_path).read_text())['instances']
    if not vm.ID.fullmatch(args.id):
        raise ValueError('invalid instance id')
    record = dict(record_override) if record_override is not None else next((dict(r) for r in defaults if r['id'] == args.id), None)
    if record is None:
        if not args.spec:
            raise ValueError('custom Linux instance requires --spec JSON with all identity/resource fields')
        record = json.loads(args.spec.read_text())
        if record.get('id') != args.id:
            raise ValueError('instance spec id differs from requested id')
    if not __import__('re').fullmatch(r'[a-z0-9][a-z0-9.+_-]{0,99}', record.get('template', '')):
        raise ValueError('invalid template id')
    external = template_directory if template_directory is not None else getattr(args, 'template_root', None)
    if external is not None and not seed and template_directory is None:
        raise ValueError('external template roots are limited to new factory seed preparation')
    template = Path(external) if external is not None else args.data_root / 'templates' / record['template']
    expected_parent = template.parent if external is not None else args.data_root / 'templates'
    if not template.is_absolute() or template.is_symlink() or template.resolve() != template or template.resolve().parent != expected_parent.resolve():
        raise ValueError('template path escapes persistent storage')
    for filename in ('root.raw', 'template.json', 'boot.json', 'Image', 'initrd', 'kernel.config'):
        vm.regular_asset(template, filename)
    meta = json.loads((template / 'template.json').read_text())
    version = deployment_version(meta)
    if 'template_version' in meta or 'version' in meta:
        if not isinstance(version, str) or not __import__('re').fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?', version):
            raise ValueError('invalid template version')
        if 'template_version' in record and record['template_version'] != version:
            raise ValueError('selected template version differs from bundle deployment version')
        record['template_version'] = version
        record['template_manifest_sha256'] = digest(template / 'template.json')
    if record.get('guest_kind', 'linux') != 'linux':
        raise ValueError('Linux preparation cannot replace the selected guest kind')
    if (meta.get('template_id') is not None and record.get('template_id') is not None
            and meta['template_id'] != record['template_id']):
        raise ValueError('selected template ID differs from bundle')
    if meta.get('id') != record['template']:
        raise ValueError('template bundle id differs from selected template')
    if (template / 'root.raw').stat().st_mode & 0o222:
        raise ValueError('template is writable, not an immutable source')
    if meta.get('source', {}).get('distribution') not in ('ubuntu', 'debian'):
        raise ValueError('unsupported Linux template adapter; RTOS firmware uses its separate bundle')
    if digest(template / 'root.raw') != meta['root_sha256']:
        raise ValueError('template root disk changed since publication')
    boot = json.loads((template / 'boot.json').read_text())
    if boot.get('source') != meta.get('source'):
        raise ValueError('template root and boot provenance disagree')
    for filename, key in [('Image', 'kernel_sha256'), ('initrd', 'initrd_sha256'), ('kernel.config', 'config_sha256')]:
        if digest(template / filename) != boot[key]:
            raise ValueError('template boot asset digest mismatch')
    output = args.data_root / ('seeds' if seed else 'instances') / args.id
    require_new_output(output)
    record.update(schema=1, guest_kind='linux', uuid='00000000-0000-4000-8000-000000000000' if seed else (identity or uuid4()), kernel_release=boot['kernel_release'])
    if seed:
        record['identity_state'] = 'pending'
    vm.validate_identity(record)
    work = Workspace(output.parent, workspace_path)
    try:
        stage = work.path / args.id
        stage.mkdir()
        for filename in ('root.raw', 'Image', 'initrd', 'boot.json', 'kernel.config'):
            if filename == 'root.raw':
                command(['cp', '--sparse=always', '--reflink=never', '--', template / filename, stage / filename])
            else:
                shutil.copyfile(template / filename, stage / filename)
        (stage / 'root.raw').chmod(0o600)
        command(['tune2fs', '-U', 'random', stage / 'root.raw'])
        root = work.path / 'root'
        work.mount(work.loop(stage / 'root.raw'), root)
        if seed:
            prepare_deployment_seed(root, record)
        else:
            prepare_instance(root, record, command=command)
        with guest_runtime(work, root):
            verify_guest_ssh(root, seed=seed)
        os.sync()
        work.close()
        work.mounts, work.loops = [], []
        vm.atomic_write(stage / 'instance.json', json.dumps(record, indent=2) + '\n')
        vm.validate_record(record, stage, allow_seed=seed)
        # 전체 registry의 식별자·디스크 중복도 게시 전에 확인한다.
        for other in output.parent.iterdir():
            if other.name.startswith('.'):
                continue
            if other.is_symlink() or not other.is_dir():
                raise ValueError('invalid existing instance registry entry')
            current = json.loads(vm.regular_asset(other, 'instance.json').read_text())
            for field in ('id', 'uuid', 'name', 'hostname', 'mac', 'ipv4'):
                if current.get(field) == record[field] and not (seed and field == 'uuid'):
                    raise ValueError(f'duplicate {field} with {other.name}')
        if digest(template / 'root.raw') != meta['root_sha256']:
            raise ValueError('immutable template changed during instance preparation')
        require_new_output(output)
        publish(stage, output)
        os.sync()
    finally:
        if work.mounts or work.loops:
            work.close()


