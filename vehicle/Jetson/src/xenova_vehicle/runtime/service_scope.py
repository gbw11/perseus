"""고정 systemd237 실행자의 실제 invocation과 legacy cgroup 수명을 관측한다.

PID1의 고정 unit 직렬화·전용 tree의 root 쓰기 권한·S의 생성 전 상태를 함께
검사한다. 두 번 빈 목록을 읽었다는 이유만으로 COMPLETE를 생산하지 않는다.
COOPERATIVE_UID_EXECUTION은 같은 UID의 협력 code를 전제한다. 다른 user cgroup의
쓰기 권한은 바꾸지 않으며 비협력 same-UID migration/privileged root는 범위 밖이다.
"""

from dataclasses import asdict, dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from .procfs_formats import parse_boot_id, parse_fdinfo, parse_mountinfo, parse_proc_stat
from .resource_guard import CgroupObservation
from .resource_manifest import CgroupIdentity, ProcessBirth, ResourceManifest


UNIT = 'xenova-vehicle.service'
SLICE = 'xenovavehicle.slice'
SLICE_PATH = '/' + SLICE
LEAF_PATH = SLICE_PATH + '/' + UNIT
CGROUP_ROOT = '/sys/fs/cgroup/systemd'
SYSTEMCTL = '/bin/systemctl'
EXECUTION_PROFILE = 'COOPERATIVE_UID_EXECUTION'
_PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'Type', 'MainPID', 'ControlPID',
               'InvocationID', 'ControlGroup', 'Slice', 'KillMode', 'KillSignal', 'Delegate', 'UID', 'FragmentPath', 'DropInPaths')


class ServiceScopeUnavailable(RuntimeError):
    """현재 실행자를 완전하게 결박하지 못했다. cgroup 부재/불명도 성공이 아니다."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _identity(value):
    return value.st_dev, value.st_ino


def _read(path, cap):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        raw = bytearray()
        while len(raw) <= cap:
            part = os.read(fd, cap + 1 - len(raw))
            if not part:
                break
            raw.extend(part)
        if len(raw) > cap:
            raise ServiceScopeUnavailable('scope metadata exceeds its bound: ' + path)
        return bytes(raw)
    finally:
        os.close(fd)


def _protected(path, *, directory=False):
    value = os.stat(path, follow_symlinks=False)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022
            or os.access(path, os.W_OK, effective_ids=True)):
        raise ServiceScopeUnavailable('scope path is not root-only writable: ' + path)
    return value


def _root_file(path):
    if not path.startswith('/') or any(p in ('.', '..') for p in path.split('/')):
        raise ServiceScopeUnavailable('manager path is not an absolute fixed file')
    for parent in reversed(Path(path).parents):
        _protected(str(parent), directory=True)
    before = _protected(path)
    raw = _read(path, 65_536)
    after = _protected(path)
    if (_identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            _identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ServiceScopeUnavailable('root-owned manager file changed during observation')
    return dict(path=path, device=before.st_dev, inode=before.st_ino,
                sha256=hashlib.sha256(raw).hexdigest())


def _show(unit, properties):
    binary = os.path.realpath(SYSTEMCTL)
    # 배포 binary도 unprivileged PATH/LD_* 또는 다른 DBus 주소로 바꾸지 않는다.
    for parent in reversed(Path(binary).parents):
        _protected(str(parent), directory=True)
    _protected(binary)
    command = [binary, '--system', 'show'] + ([] if unit is None else [unit])
    command += ['--no-pager', '--all'] + ['--property=' + name for name in properties]
    result = subprocess.run(command, capture_output=True, check=False, timeout=2,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    if result.returncode != 0 or result.stderr or len(result.stdout) > 16_384:
        raise ServiceScopeUnavailable('actual systemctl show did not produce bounded successful metadata')
    values = {}
    for line in result.stdout.decode('ascii').splitlines():
        if '=' not in line:
            raise ServiceScopeUnavailable('invalid systemctl property output')
        name, value = line.split('=', 1)
        if name in values or name not in properties:
            raise ServiceScopeUnavailable('duplicate or unsupported systemctl property')
        values[name] = value
    if set(values) != set(properties):
        raise ServiceScopeUnavailable('required systemctl properties are missing')
    return values


def _uid(raw, expected):
    lines = [line.split()[1:] for line in raw.splitlines() if line.startswith(b'Uid:')]
    if lines != [[str(expected).encode()] * 4]:
        raise ServiceScopeUnavailable('actual process UID differs from its manager execution identity')


def _manager(guard, *, starting):
    if os.geteuid() != 1000 or os.getuid() != 1000:
        raise ServiceScopeUnavailable('the supported service execution UID is 1000')
    version = _show(None, ('Version',))['Version']
    if re.fullmatch(r'237(?:[^0-9].*)?', version) is None:
        raise ServiceScopeUnavailable('the actual system manager is not the pinned systemd237 profile')
    if _read('/proc/1/comm', 32) != b'systemd\n':
        raise ServiceScopeUnavailable('actual PID1 is not systemd')
    _uid(_read('/proc/1/status', 65_536), 0)
    pid1 = parse_proc_stat(_read('/proc/1/stat', 4096), 1)
    current = parse_proc_stat(_read('/proc/self/stat', 4096), os.getpid())
    if (ProcessBirth(current.pid, current.start_ticks) != guard.birth
            or parse_boot_id(_read('/proc/sys/kernel/random/boot_id', 64)) != guard.runtime.boot_id
            or os.stat('/proc/self/ns/pid').st_ino != guard.runtime.pid_ns_inode
            or os.stat('/proc/self/ns/mnt').st_ino != guard.runtime.mnt_ns_inode):
        raise ServiceScopeUnavailable('current guard process/runtime changed')
    status = _read('/proc/self/status', 65_536)
    _uid(status, 1000)
    for label in (b'CapEff:', b'CapPrm:', b'CapAmb:'):
        values = [line.split()[1] for line in status.splitlines() if line.startswith(label)]
        if len(values) != 1 or int(values[0], 16) != 0:
            raise ServiceScopeUnavailable('service must not hold cgroup/DAC override capabilities')
    values = _show(UNIT, _PROPERTIES)
    expected = dict(Id=UNIT, LoadState='loaded', Type='simple', MainPID=str(os.getpid()), ControlPID='0',
                    ControlGroup=LEAF_PATH, Slice=SLICE, KillMode='control-group', KillSignal='2', Delegate='no', UID='1000')
    if any(values[key] != value for key, value in expected.items()):
        raise ServiceScopeUnavailable('actual manager execution differs from the fixed service profile')
    if (values['ActiveState'] not in (('active',) if starting else ('active', 'deactivating'))
            or starting and values['SubState'] != 'running'
            or re.fullmatch('[0-9a-f]{32}', values['InvocationID']) is None
            or values['InvocationID'] == '0' * 32):
        raise ServiceScopeUnavailable('actual invocation is not a live fixed-unit execution')
    fragment = _root_file(values['FragmentPath'])
    drops = []
    for name in values['DropInPaths'].split():
        drops.append(_root_file(name))
    slice_values = _show(SLICE, ('Id', 'LoadState', 'ActiveState', 'ControlGroup', 'StopWhenUnneeded', 'FragmentPath', 'DropInPaths'))
    if any(slice_values[key] != value for key, value in dict(Id=SLICE, LoadState='loaded', ActiveState='active',
            ControlGroup=SLICE_PATH, StopWhenUnneeded='no').items()):
        raise ServiceScopeUnavailable('dedicated persistent slice is not active and fixed')
    slice_fragment = _root_file(slice_values['FragmentPath'])
    slice_drops = [_root_file(name) for name in slice_values['DropInPaths'].split()]
    return dict(unit=UNIT, slice=SLICE, invocation_id=values['InvocationID'], boot_id=guard.runtime.boot_id,
                main_birth=asdict(guard.birth), manager_birth=asdict(ProcessBirth(1, pid1.start_ticks)), uid=1000,
                control_group=LEAF_PATH, properties=values, fragment=fragment, dropins=drops,
                slice_fragment=slice_fragment, slice_dropins=slice_drops)


def _membership(pid):
    raw = _read('/proc/' + str(pid) + '/cgroup', 16_384)
    rows = [line.split(b':', 2) for line in raw.splitlines()]
    selected = [row[2] for row in rows if len(row) == 3 and row[1] == b'name=systemd']
    if len(selected) != 1:
        raise ServiceScopeUnavailable('legacy systemd membership is ambiguous')
    return selected[0].decode('ascii')


def _mount_root(descriptor):
    root = os.fstat(descriptor)
    if not stat.S_ISDIR(root.st_mode) or _identity(root) != _identity(os.stat(CGROUP_ROOT, follow_symlinks=False)):
        raise ServiceScopeUnavailable('held cgroup mount root changed')
    mount_id = parse_fdinfo(_read('/proc/self/fdinfo/' + str(descriptor), 8192))
    rows = [row for row in parse_mountinfo(_read('/proc/self/mountinfo', 1024 * 1024)) if row.mount_id == mount_id]
    if (len(rows) != 1 or rows[0].fs_type != b'cgroup' or rows[0].root != b'/'
            or rows[0].mountpoint != CGROUP_ROOT.encode() or b'name=systemd' not in rows[0].super_options
            or (rows[0].major, rows[0].minor) != (os.major(root.st_dev), os.minor(root.st_dev))):
        raise ServiceScopeUnavailable('actual legacy cgroup mount does not match its held root')
    return _identity(root), mount_id


def _pids(raw):
    values = set()
    for token in raw.split():
        if not token.isdigit() or len(token) > 10 or not 0 < int(token) <= 2**32 - 1:
            raise ServiceScopeUnavailable('cgroup member PID is invalid')
        values.add(int(token))
    if len(values) > 16:
        raise ServiceScopeUnavailable('cgroup exceeds 16 process births')
    return values


class _LegacyScopePin:
    """살아있는 원 session cgroup/basefile FD를 보유하고 실제 ENODEV만 기다린다.

    Linux4.9 kernfs getattr는 dir nlink를 subdirs+2로 재계산하므로 nlink=0을 쓰지
    않는다. cgroup_destroy_locked의 populated/live-child 검사와 CSS_ONLINE 해제 뒤
    kernfs_remove가 원 basefile을 비활성화하며 seq_start는 실제 ENODEV를 돌려준다.
    """

    def __init__(self, manifest, guard):
        self.manifest, self.guard = manifest, guard
        self.root_fd = self.directory_fd = self.procs_fd = None
        try:
            self.root_fd = os.open(CGROUP_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.root_identity = _mount_root(self.root_fd)
            path = CGROUP_ROOT + manifest.cgroup.path
            named = _protected(path, directory=True)
            self.directory_fd = os.open(manifest.cgroup.path[1:],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.root_fd)
            self.directory_identity = _identity(os.fstat(self.directory_fd))
            if (self.directory_identity != (manifest.cgroup.device, manifest.cgroup.inode)
                    or self.directory_identity != _identity(named)):
                raise ServiceScopeUnavailable('legacy live cgroup incarnation differs from the original catalogue')
            _protected(path + '/cgroup.procs')
            self.procs_fd = os.open('cgroup.procs', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                    dir_fd=self.directory_fd)
            self.procs_identity = _identity(os.fstat(self.procs_fd))
            if (self.procs_identity != _identity(os.stat('cgroup.procs', dir_fd=self.directory_fd, follow_symlinks=False))
                    or parse_fdinfo(_read('/proc/self/fdinfo/' + str(self.procs_fd), 8192)) != self.root_identity[1]):
                raise ServiceScopeUnavailable('legacy basefile is not on the held cgroup mount')
            self.initial_members = []
            for pid in sorted(_pids(self._read_members())):
                value = parse_proc_stat(_read('/proc/' + str(pid) + '/stat', 4096), pid)
                if _membership(pid) != manifest.cgroup.path:
                    raise ServiceScopeUnavailable('legacy initial member changed its cgroup')
                self.initial_members.append(asdict(ProcessBirth(pid, value.start_ticks)))
            if _identity(os.stat(path, follow_symlinks=False)) != self.directory_identity:
                raise ServiceScopeUnavailable('legacy cgroup changed before its first valid basefile read')
            self._check()
        except BaseException:
            self.close()
            raise

    def _check(self):
        if (_mount_root(self.root_fd) != self.root_identity
                or _identity(os.fstat(self.directory_fd)) != self.directory_identity
                or _identity(os.fstat(self.procs_fd)) != self.procs_identity):
            raise ServiceScopeUnavailable('held legacy cgroup/basefile incarnation changed')

    def _read_members(self):
        os.lseek(self.procs_fd, 0, os.SEEK_SET)
        raw = bytearray()
        while len(raw) <= 16_384:
            part = os.read(self.procs_fd, 16_385 - len(raw))
            if not part:
                return bytes(raw)
            raw.extend(part)
        raise ServiceScopeUnavailable('legacy cgroup basefile exceeds its fixed bound')

    def wait_dead(self, directory, *, request_id, issued_us, expires_us):
        from .runtime_bootstrap import write_legacy_handover

        def remaining():
            now = time.monotonic_ns() // 1000
            if not issued_us <= now < expires_us:
                raise ServiceScopeUnavailable('original legacy handover deadline elapsed')
            return (expires_us - now) / 1_000_000

        remaining()
        self._check()
        write_legacy_handover(directory, dict(version=1, state='PINNED', request_id=request_id,
            guard_id=self.guard.guard_id, service_instance=self.guard.service_instance,
            ref=asdict(self.manifest.ref), cgroup=asdict(self.manifest.cgroup),
            root_identity=self.root_identity, directory_identity=self.directory_identity,
            basefile_identity=self.procs_identity, initial_members=self.initial_members,
            issued_us=issued_us, expires_us=expires_us))
        while True:
            remaining()
            self._check()
            try:
                _pids(self._read_members())
            except OSError as error:
                if error.errno != errno.ENODEV:
                    raise ServiceScopeUnavailable('legacy basefile failure is not actual inactive ENODEV') from error
                self._check()
                try:
                    os.stat(self.manifest.cgroup.path[1:], dir_fd=self.root_fd, follow_symlinks=False)
                except FileNotFoundError:
                    remaining()
                    return CgroupObservation(self.manifest.ref, self.guard, self.manifest.cgroup, 'COMPLETE', ())
                # basefile inactive와 원 directory 제거 사이의 kernel 진행만 기다린다.
            time.sleep(min(0.02, remaining()))

    def close(self):
        failure = None
        for name in ('procs_fd', 'directory_fd', 'root_fd'):
            descriptor = getattr(self, name)
            setattr(self, name, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as error:
                    failure = error
        if failure is not None:
            raise ServiceScopeUnavailable('legacy held descriptor return was not confirmed') from failure

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def _tree(guard):
    root_fd = os.open(CGROUP_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        root_identity = _mount_root(root_fd)
        _protected(CGROUP_ROOT, directory=True)
        stack, directories, members = [SLICE_PATH], [], {}
        while stack:
            relative = stack.pop()
            if len(directories) >= 16:
                raise ServiceScopeUnavailable('dedicated slice exceeds 16 directories')
            if relative != SLICE_PATH and not (relative == LEAF_PATH or relative.startswith(LEAF_PATH + '/')):
                raise ServiceScopeUnavailable('another execution unit entered the dedicated slice')
            path = CGROUP_ROOT + relative
            directory = _protected(path, directory=True)
            procs = _protected(path + '/cgroup.procs')
            _protected(path + '/tasks')
            raw = _read(path + '/cgroup.procs', 16_384)
            pids = _pids(raw)
            if len(set(members) | pids) > 16:
                raise ServiceScopeUnavailable('dedicated slice exceeds 16 process births')
            for pid in pids:
                value = parse_proc_stat(_read('/proc/' + str(pid) + '/stat', 4096), pid)
                birth = ProcessBirth(pid, value.start_ticks)
                if _membership(pid) != relative or pid in members and members[pid] != birth:
                    raise ServiceScopeUnavailable('actual cgroup process membership or birth changed')
                members[pid] = birth
            directories.append((relative, directory.st_dev, directory.st_ino, procs.st_dev, procs.st_ino, tuple(sorted(pids))))
            with os.scandir(path) as entries:
                children = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
            stack.extend(relative + '/' + name for name in children)
            if _identity(os.stat(path, follow_symlinks=False)) != _identity(directory):
                raise ServiceScopeUnavailable('cgroup directory changed during scan')
        if tuple(sorted(members.values(), key=lambda value: value.pid)) != (guard.birth,):
            raise ServiceScopeUnavailable('guard phase must contain only the current supervisor birth')
        if _mount_root(root_fd) != root_identity:
            raise ServiceScopeUnavailable('held cgroup mount root changed')
        return tuple(sorted(directories)), tuple(members.values())
    finally:
        os.close(root_fd)


@dataclass(frozen=True)
class ServiceScopeFacts:
    canonical_bytes: bytes

    @property
    def fields(self):
        return json.loads(self.canonical_bytes)

    @property
    def cgroup(self):
        value = self.fields['cgroup']
        return CgroupIdentity(value['mode'], value['path'], value['device'], value['inode'])


class SystemdServiceScope:
    """고정 manager-native observer. 생성자는 I/O나 새로운 process를 만들지 않는다."""

    def __init__(self):
        self._guard = self._facts = None

    def observe(self, guard, native_owner):
        from .native_resources import NativeResources

        if type(native_owner) is not NativeResources or native_owner.creation_attempted:
            raise ServiceScopeUnavailable('guard must precede every native constructor attempt')
        first_manager = _manager(guard, starting=True)
        first_tree, first_members = _tree(guard)
        second_manager = _manager(guard, starting=True)
        second_tree, second_members = _tree(guard)
        if (first_manager != second_manager or first_tree != second_tree or first_members != second_members
                or native_owner.creation_attempted):
            raise ServiceScopeUnavailable('manager/tree/membership changed during admission')
        top = next(row for row in second_tree if row[0] == SLICE_PATH)
        value = dict(second_manager, execution_profile=EXECUTION_PROFILE,
                     cgroup=asdict(CgroupIdentity('SYSTEMD_V1', SLICE_PATH, top[1], top[2])),
                     members=[asdict(member) for member in second_members],
                     tree_sha256=hashlib.sha256(_canonical(second_tree)).hexdigest())
        self._guard, self._facts = guard, ServiceScopeFacts(_canonical(value))
        return self._facts

    def pin_legacy(self, manifest, guard):
        """원 live SSH session scope만 pin한다. 없음/다른 inode/재생성 path는 허가하지 않는다."""
        if (self._guard != guard or self._facts is None or type(manifest) is not ResourceManifest
                or manifest.runtime != guard.runtime or manifest.cgroup.mode != 'SYSTEMD_V1'
                or re.fullmatch(r'/user\.slice/user-1000\.slice/session-[0-9]+\.scope', manifest.cgroup.path) is None
                or not manifest.complete_inventory):
            raise ServiceScopeUnavailable('legacy handover requires complete original same-runtime SSH scope')
        return _LegacyScopePin(manifest, guard)

    def bound_publication_facts(self, manifest):
        """캐시된 원 admission과 manifest의 불변 관계만 검사한다. I/O/process 생성은 없다."""
        if self._guard is None or self._facts is None:
            raise ServiceScopeUnavailable('publication lacks its actual precreation execution observation')
        owner = next(item for item in manifest.owners if item.role == 'S')
        if (manifest.service_instance != self._guard.service_instance or manifest.runtime != self._guard.runtime
                or owner.birth != self._guard.birth or manifest.cgroup != self._facts.cgroup):
            raise ServiceScopeUnavailable('published resource generation differs from its execution owner')
        return self._facts

    def publication_facts(self, manifest):
        """최초 PREPARED→PUBLISHED 때만 실제 manager invocation을 다시 확인한다."""
        self.bound_publication_facts(manifest)
        current = _manager(self._guard, starting=False)
        original = self._facts.fields
        for key in ('unit', 'slice', 'invocation_id', 'boot_id', 'main_birth', 'manager_birth', 'uid', 'control_group'):
            if current[key] != original[key]:
                raise ServiceScopeUnavailable('manager invocation changed during publication')
        return self._facts
