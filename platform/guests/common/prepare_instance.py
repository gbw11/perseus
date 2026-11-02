"""PC와 Pi에서 공유하는 명시적 최초 인스턴스 생성. lifecycle start/stop은 호출하지 않는다."""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from first_install_contract import uuid4
from artifact_io import publish
from xenova_catalog import deployment_version
from xenova_trace import span
from xenova_runtime import atomic_write
from xenova_preparation import (run as request_run, check_output, diagnostic as print,
                                managed_preparation, check_creation, cleanup_output, child_exit_unconfirmed)
from prepare_rootfs import prepare_instance, prepare_deployment_seed, inside, verify_guest_agent


def command(args, **kwargs):
    check_creation()
    kwargs.setdefault('check', True)
    return request_run([str(a) for a in args], **kwargs)


def digest(path):
    return subprocess.check_output(['sha256sum', str(path)], text=True, timeout=900).split()[0]


def _input_identity(info):
    """정상 읽기로 바뀌는 atime은 제외하고 이번 준비의 원본 연결을 비교한다."""
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('template input must be an unshared regular file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def _unchanged(path, identity):
    """한 준비 작업의 경로·원본 변경 guard. digest나 영구 검증 cache를 대신하지 않는다."""
    if _input_identity(path.lstat()) != identity:
        raise ValueError('template input changed during instance preparation: ' + path.name)


@contextmanager
def _input(path):
    if path.resolve() != path:
        raise ValueError('template input path must be canonical and unlinked')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        identity = _input_identity(os.fstat(fd))
        _unchanged(path, identity)
        yield fd, identity
        if _input_identity(os.fstat(fd)) != identity:
            raise ValueError('open template input changed during instance preparation: ' + path.name)
        _unchanged(path, identity)
    finally:
        os.close(fd)


def _metadata(path):
    """동일하게 읽은 metadata 바이트로 해시와 JSON을 얻고 원본 변경 guard를 반환한다."""
    with _input(path) as (fd, identity):
        remaining, parts = identity[2], []
        while remaining:
            check_creation()
            part = os.read(fd, min(1024 * 1024, remaining))
            if not part:
                raise ValueError('template metadata ended before its declared size')
            parts.append(part)
            remaining -= len(part)
    raw = b''.join(parts)
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), identity


def _ranges(fd, size):
    """지원되는 filesystem에서는 hole을 유지하고, 비지원 시 같은 검증 stream으로 읽는다."""
    if not hasattr(os, 'SEEK_DATA') or not hasattr(os, 'SEEK_HOLE'):
        yield 0, size, True
        return
    position = 0
    unsupported = (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP)
    while position < size:
        check_creation()
        try:
            data = os.lseek(fd, position, os.SEEK_DATA)
        except OSError as error:
            if error.errno == errno.ENXIO:
                yield position, size, False
                return
            if error.errno not in unsupported:
                raise
            yield position, size, True
            return
        if data < position:
            raise ValueError('template extent did not advance')
        if data > position:
            yield position, min(data, size), False
        if data >= size:
            return
        try:
            end = os.lseek(fd, data, os.SEEK_HOLE)
        except OSError as error:
            if error.errno not in unsupported:
                raise
            end = size
        if end <= data:
            raise ValueError('template data extent did not advance')
        yield data, min(end, size), True
        position = min(end, size)


def _copy_verified(source, target, expected, *, expected_bytes=None):
    """신규 private 파일로 복사하는 바이트 전체를 검증한다. 실패 결과는 게시·삭제하지 않는다.

    sparse hole도 논리적인 0 바이트로 hash에 포함한다. 복사본 물리 매체의 재읽기 검증이나
    전원 상실 수락은 아니다. 반환한 원본 신원은 이번 준비의 최종 변경 guard에만 사용한다.
    """
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('template input SHA-256 required')
    if expected_bytes is not None and (type(expected_bytes) is not int or expected_bytes < 0):
        raise ValueError('template input byte size required')
    check_creation()
    zero = bytes(1024 * 1024)
    value = hashlib.sha256()
    with span('prepare.verified-copy', asset=source.name), _input(source) as (fd, identity):
        size = identity[2]
        if expected_bytes is not None and size != expected_bytes:
            raise ValueError('template input byte size mismatch')
        output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            for start, end, data in _ranges(fd, size):
                check_creation()
                os.lseek(fd, start, os.SEEK_SET)
                os.lseek(output, start, os.SEEK_SET)
                remaining = end - start
                while remaining:
                    check_creation()
                    length = min(len(zero), remaining)
                    part = os.read(fd, length) if data else zero[:length]
                    if not part:
                        raise ValueError('template input ended during copy')
                    value.update(part)
                    remaining -= len(part)
                    if not data or part == zero[:len(part)]:
                        os.lseek(output, len(part), os.SEEK_CUR)
                    else:
                        pending = memoryview(part)
                        while pending:
                            check_creation()
                            count = os.write(output, pending)
                            if count <= 0:
                                raise OSError(errno.EIO, 'template copy write made no progress')
                            pending = pending[count:]
                    check_creation()
            os.ftruncate(output, size)
            if value.hexdigest() != expected:
                raise ValueError('copied template input SHA-256 mismatch: ' + source.name)
            os.fsync(output)
            check_creation()
        finally:
            os.close(output)
    return identity


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
        check_creation()
        self.path = Path(owned_path) if owned_path is not None else Path(parent) / ('.xenova-preparing-' + uuid4())
        self.path.mkdir(mode=0o700)
        self.mounts, self.loops = [], []
        self.pending = False
        self._resources(False)

    def _resources(self, clean=False):
        atomic_write(self.path/'.preparation-resources.json',json.dumps({
            'schema':1,'workspace':str(self.path),'mounts':[str(p) for p in self.mounts],
            'loops':self.loops,'pending':self.pending,'clean':clean})+'\n')

    def seal_clean(self):
        if self.pending or self.mounts or self.loops:
            raise ValueError('preparation resource cleanup is unconfirmed')
        self._resources(True)

    def loop(self, image, readonly=False, partitions=False):
        check_creation()
        args = ['losetup', '--find', '--show']
        if readonly:
            args.append('--read-only')
        if partitions:
            args.append('--partscan')
        self.pending = True
        self._resources()
        value = check_output(args + [str(image)], text=True).strip()
        if not __import__('re').fullmatch(r'/dev/loop[0-9]+', value):
            raise ValueError('unexpected allocated loop device')
        self.loops.append(value)
        self.pending = False
        self._resources()
        return value

    def mount(self, device, target, readonly=False):
        check_creation()
        target.mkdir(parents=True, exist_ok=True)
        self.pending = True
        self.mounts.append(target)
        self._resources()
        command(['mount', '-o', 'ro,noload' if readonly else 'rw', device, target])
        self.pending = False
        self._resources()

    def close(self):
        if child_exit_unconfirmed():
            raise ValueError('owned child exit unconfirmed; mount/loop cleanup deferred')
        while self.mounts:
            command(['umount', self.mounts[-1]])
            self.mounts.pop()
            self._resources()
        while self.loops:
            command(['losetup', '--detach', self.loops[-1]])
            self.loops.pop()
            self._resources()
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
        with cleanup_output():
            while len(work.mounts) > checkpoint:
                command(['umount', work.mounts[-1]])
                work.mounts.pop()


def verify_guest_ssh(root, *, seed=False):
    # -C의 Match 조건 검사는 -T가 필요하다. seed의 -G는 공유 host key 없이 설정만 검사한다.
    options = ['-G', '-T'] if seed else ['-T']
    effective = check_output(
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


class PreparedInstance:
    """private 준비의 원 바이트/manifest와 게시 대상을 결합한다. Manager가 채택 시에만 publish한다."""
    def __init__(self, vm, record, stage, output, inputs, *, seed=False):
        self.vm,self.record,self.stage,self.output,self.inputs,self.seed=vm,record,stage,output,inputs,seed
        self.assets={path:_input_identity(path.lstat()) for path in stage.iterdir()}

    def publish(self):
        check_creation()
        self.vm.validate_record(self.record,self.stage,allow_seed=self.seed)
        for path,identity in self.assets.items():
            _unchanged(path,identity)
        for other in self.output.parent.iterdir():
            if other.name.startswith('.'):
                continue
            if other.is_symlink() or not other.is_dir():
                raise ValueError('invalid existing instance registry entry')
            current=json.loads(self.vm.regular_asset(other,'instance.json').read_text())
            for field in ('id','uuid','name','hostname','mac','ipv4'):
                if current.get(field)==self.record[field] and not (self.seed and field=='uuid'):
                    raise ValueError('duplicate '+field+' with '+other.name)
        with span('prepare.source-guard'):
            for path,identity in self.inputs.items():
                _unchanged(path,identity)
        require_new_output(self.output)
        check_creation()
        publish(self.stage,self.output)


def make_instance(args, *, vm, defaults_path, record_override=None, identity=None, workspace_path=None, template_directory=None, private=False):
    check_creation()
    seed = getattr(args, 'deployment_seed', False)
    # 새 출력의 부모도 같은 저장소 안의 일반 디렉터리여야 한다.
    for directory in [args.data_root, args.data_root / 'templates', args.data_root / 'instances', *([args.data_root / 'seeds'] if seed else [])]:
        if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
            raise ValueError('instance storage directories must be canonical and unlinked')
    if seed:
        seed_store_check(args.data_root)
    else:
        vm.storage_check(args.data_root)
        if (args.data_root/'.xenova-storage.json').exists() and (not private or not managed_preparation()):
            raise ValueError('initialized live storage requires private preparation owned by Manager')
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
    meta, manifest_digest, manifest_identity = _metadata(template / 'template.json')
    from xenova_guest_profile import from_manifest
    record['provisioning_profile']=from_manifest(meta)
    if ('template_manifest_sha256' in record
            and record['template_manifest_sha256'] != manifest_digest):
        raise ValueError('selected template manifest differs from bundle')
    inputs = {template / 'template.json': manifest_identity}
    version = deployment_version(meta)
    if 'template_version' in meta or 'version' in meta:
        if not isinstance(version, str) or not __import__('re').fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?', version):
            raise ValueError('invalid template version')
        if 'template_version' in record and record['template_version'] != version:
            raise ValueError('selected template version differs from bundle deployment version')
        record['template_version'] = version
        record['template_manifest_sha256'] = manifest_digest
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
    boot, boot_digest, boot_identity = _metadata(template / 'boot.json')
    inputs[template / 'boot.json'] = boot_identity
    if boot.get('source') != meta.get('source'):
        raise ValueError('template root and boot provenance disagree')
    hashes = {'root.raw': meta['root_sha256'], 'boot.json': boot_digest}
    for filename, key in [('Image', 'kernel_sha256'), ('initrd', 'initrd_sha256'), ('kernel.config', 'config_sha256')]:
        hashes[filename] = boot[key]
    declared = meta.get('files')
    if declared is not None:
        if not isinstance(declared, dict) or set(declared) != set(hashes):
            raise ValueError('complete template input manifest required')
        for filename, expected in hashes.items():
            if declared[filename]['sha256'] != expected:
                raise ValueError('template input manifest hashes disagree')
    for filename, expected in hashes.items():
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('template input SHA-256 required')
        if declared is not None and (type(declared[filename]['bytes']) is not int or declared[filename]['bytes'] < 0):
            raise ValueError('template input byte size required')
        path = template / filename
        inputs.setdefault(path, _input_identity(path.lstat()))
        if (declared is not None or filename == 'root.raw') and inputs[path][5] & 0o222:
            raise ValueError('template input is writable, not an immutable source')
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
            check_creation()
            for path, source_identity in inputs.items():
                _unchanged(path, source_identity)
            source_identity = _copy_verified(template / filename, stage / filename,
                hashes[filename], expected_bytes=declared[filename]['bytes'] if declared is not None else None)
            if source_identity != inputs[template / filename]:
                raise ValueError('template input replaced before copy: ' + filename)
        (stage / 'root.raw').chmod(0o600)
        check_creation()
        command(['tune2fs', '-U', 'random', stage / 'root.raw'])
        root = work.path / 'root'
        work.mount(work.loop(stage / 'root.raw'), root)
        if seed:
            prepare_deployment_seed(root, record)
        else:
            prepare_instance(root, record, command=command)
            if private and managed_preparation() and record['provisioning_profile']['channels']:
                from xenova_provisioning import apply_linux
                apply_linux(args.data_root,record,root)
        with guest_runtime(work, root):
            check_creation()
            verify_guest_ssh(root, seed=seed)
            verify_guest_agent(root, seed=seed)
        os.sync()
        with cleanup_output():work.close()
        work.seal_clean()
        work.mounts, work.loops = [], []
        vm.atomic_write(stage / 'instance.json', json.dumps(record, indent=2) + '\n')
        vm.validate_record(record, stage, allow_seed=seed)
        check_creation()
        result=PreparedInstance(vm,record,stage,output,inputs,seed=seed)
        if private:
            return result
        result.publish()
        os.sync()
    except BaseException as error:
        try:
            with cleanup_output():work.close()
            work.seal_clean()
        except Exception as cleanup:
            if hasattr(error,'error'):
                error.error.setdefault('details',{})['cleanup_error']={'cause_type':type(cleanup).__name__}
                raise error from cleanup
            raise ValueError(str(error)+'; resource cleanup unconfirmed: '+type(cleanup).__name__) from error
        raise


