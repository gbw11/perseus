"""실제 Linux runtime과 원 manifest의 known owner를 동기로 관측한다.

같은 PID·Thread의 borrowed store를 처음과 끝에 실제 조회한다. 고정 kernel procfs와
협력 실행을 전제로 하며 전체 OS snapshot·건강·CLEAN·정리/생성 허가는 생산하지 않는다.
동기 I/O 반환시간을 보장하거나 새 기한을 발급하지 않으며 store는 닫지 않는다.
"""

from dataclasses import dataclass
import errno
import os
import stat
import sys
import threading
import time

from .procfs_formats import (
    ProcFormatError, parse_boot_id, parse_fdinfo, parse_mountinfo, parse_proc_stat,
    validate_self_status,
)
from .resource_guard import CurrentGuardScope, GuardInputError, OwnerObservation
from .resource_manifest import ManifestError, ProcessBirth, RuntimeScope, _uuid
from .resource_store import ResourceStore, StoreError, StoreLoad, StoreUsageError


class ObservationUsageError(ValueError):
    """입력 타입·시작 UUID·실제 PID/Thread·비재진입 사용 계약 위반이다."""


class ObservationUnavailable(RuntimeError):
    """원 출처·지원 profile·runtime·store 수명·관측 FD 반환을 확인하지 못했다."""


def _clock():
    value = time.monotonic_ns()
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ObservationUnavailable('monotonic clock is outside u64')
    return value


def _identity(value):
    return value.st_dev, value.st_ino


def _same_source(original, latest):
    if original.lock_nonce != latest.lock_nonce or original.outcome.kind != latest.outcome.kind:
        return False
    if original.outcome.kind == 'MISSING':
        return True
    if original.outcome.kind != 'VALID':
        return False
    before, after = original.outcome.manifest, latest.outcome.manifest
    return before.ref == after.ref and before.raw_bytes == after.raw_bytes


@dataclass(frozen=True)
class ObservationBatch:
    """원 L1·최신 실제 L2와 관측 구간의 진술. 생성자는 값의 관계만 검사하며 OS를 증명하지 않는다."""

    original_load: StoreLoad
    latest_load: StoreLoad
    guard: CurrentGuardScope
    owners: tuple[OwnerObservation, ...]
    started_ns: int
    completed_ns: int

    def __post_init__(self):
        if (type(self.original_load) is not StoreLoad or type(self.latest_load) is not StoreLoad
                or type(self.guard) is not CurrentGuardScope or type(self.owners) is not tuple
                or len(self.owners) > 9 or any(type(owner) is not OwnerObservation for owner in self.owners)):
            raise ObservationUsageError('invalid exact observation batch values')
        if (type(self.started_ns) is not int or type(self.completed_ns) is not int
                or not 0 <= self.started_ns <= self.completed_ns <= 2**64 - 1):
            raise ObservationUsageError('invalid monotonic observation bracket')
        if not _same_source(self.original_load, self.latest_load):
            raise ObservationUsageError('batch source is inconsistent')
        if (self.original_load.outcome.load_id == self.latest_load.outcome.load_id
                or self.guard.guard_id != self.original_load.lock_nonce
                or self.guard.guard_id != self.latest_load.lock_nonce):
            raise ObservationUsageError('batch requires separate loads in the same guard lifetime')
        manifest = self.original_load.outcome.manifest
        expected = () if manifest is None else manifest.known_owners
        if tuple(observation.owner for observation in self.owners) != expected:
            raise ObservationUsageError('batch must cover original known owners in order')
        if any(observation.ref != manifest.ref or observation.guard != self.guard
               for observation in self.owners):
            raise ObservationUsageError('owner observations must retain original ref and guard')


class _OwnedFds:
    def __init__(self):
        self._fds = []
        self.close_failed = False

    def open(self, path, flags, *, parent=None):
        descriptor = os.open(path, flags, **({} if parent is None else {'dir_fd': parent}))
        self._fds.append(descriptor)
        return descriptor

    def close(self, descriptor):
        # close가 이미 성공한 뒤 오류가 보고될 수도 있다. 먼저 소유 목록에서 제거하고 재시도하지 않는다.
        self._fds.remove(descriptor)
        try:
            os.close(descriptor)
        except OSError as error:
            self.close_failed = True
            raise ObservationUnavailable('owned descriptor close was not confirmed') from error

    def close_all(self):
        for descriptor in tuple(reversed(self._fds)):
            try:
                self.close(descriptor)
            except ObservationUnavailable:
                pass
        if self.close_failed:
            raise ObservationUnavailable('owned descriptor return was not confirmed')


def _read_at(owned, parent, path, maximum):
    descriptor = owned.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                            parent=parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ObservationUnavailable('proc input is not a regular pseudo file')
        output = bytearray()
        while len(output) <= maximum:
            amount = maximum + 1 - len(output)
            chunk = os.read(descriptor, amount)
            if type(chunk) is not bytes or len(chunk) > amount:
                raise ObservationUnavailable('proc read exceeded native bytes contract')
            if not chunk:
                break
            output.extend(chunk)
        if len(output) > maximum:
            raise ObservationUnavailable('proc input exceeds bounded profile')
        return bytes(output)
    finally:
        owned.close(descriptor)


def _directory(value):
    if not stat.S_ISDIR(value.st_mode):
        raise ObservationUnavailable('expected actual directory')
    return _identity(value)


def _link(parent, path):
    value = os.readlink(path, dir_fd=parent)
    if type(value) is not bytes or not 1 <= len(value) <= 256:
        raise ObservationUnavailable('kernel self link exceeds bounded profile')
    return value


@dataclass(frozen=True)
class _Root:
    path: str
    descriptor: int
    identity: tuple[int, int]

    def check(self):
        actual = os.fstat(self.descriptor)
        named = os.stat(self.path, follow_symlinks=False)
        if _directory(actual) != self.identity or _directory(named) != self.identity:
            raise ObservationUnavailable('held root and current path differ')
        return actual


def _open_root(owned, path):
    before = os.stat(path, follow_symlinks=False)
    expected = _directory(before)
    descriptor = owned.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    root = _Root(path, descriptor, expected)
    root.check()
    return root


@dataclass(frozen=True)
class _Namespace:
    path: bytes
    descriptor: int
    identity: tuple[int, int]
    link: bytes

    def check(self, parent):
        actual = os.fstat(self.descriptor)
        named = os.stat(self.path, dir_fd=parent)
        if (_identity(actual) != self.identity or _identity(named) != self.identity
                or _link(parent, self.path) != self.link):
            raise ObservationUnavailable('held namespace and current self namespace differ')
        return actual.st_ino


def _open_namespace(owned, parent, path):
    # 이 고정 kernel magic link는 의도적으로 따라간다. 일반 proc data 입력에는 NOFOLLOW를 쓴다.
    link = _link(parent, path)
    descriptor = owned.open(path, os.O_RDONLY | os.O_CLOEXEC, parent=parent)
    value = os.fstat(descriptor)
    if not stat.S_ISREG(value.st_mode):
        raise ObservationUnavailable('expected namespace handle')
    namespace = _Namespace(path, descriptor, _identity(value), link)
    namespace.check(parent)
    return namespace


def _selected_mount(records, mount_id, root, filesystem):
    selected = [record for record in records if record.mount_id == mount_id]
    if len(selected) != 1:
        raise ObservationUnavailable('actual root mount ID has no unique record')
    record = selected[0]
    actual = root.check()
    if (record.root != b'/' or record.mountpoint != root.path.encode('ascii')
            or record.fs_type != filesystem
            or (record.major, record.minor) != (os.major(actual.st_dev), os.minor(actual.st_dev))):
        raise ObservationUnavailable('root mount identity or filesystem is unsupported')
    if filesystem == b'proc':
        visibility = [option for option in record.mount_options + record.super_options
                      if option == b'hidepid' or option.startswith(b'hidepid=')]
        if visibility not in ([], [b'hidepid=0']):
            raise ObservationUnavailable('proc visibility requires unambiguous hidepid zero')
    return record


@dataclass(frozen=True)
class _Snapshot:
    runtime: RuntimeScope
    birth: ProcessBirth
    proc_mount: object
    shm_mount: object


class _ScopePins:
    def __init__(self, owned):
        self.owned = owned
        self.proc = _open_root(owned, '/proc')
        self.shm = _open_root(owned, '/dev/shm')
        self.pid = os.getpid()
        self.self_link = _link(self.proc.descriptor, b'self')
        if self.self_link != str(self.pid).encode('ascii'):
            raise ObservationUnavailable('kernel self link disagrees with current process')
        # self는 승인한 kernel proc root의 고정 magic link이며 status의 실제 PID-view도 따로 검사한다.
        self.self_fd = owned.open('self', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                                 parent=self.proc.descriptor)
        self.self_identity = _directory(os.fstat(self.self_fd))
        self.pid_ns = _open_namespace(owned, self.self_fd, b'ns/pid')
        self.mnt_ns = _open_namespace(owned, self.self_fd, b'ns/mnt')

    def _check_named_mount(self, root, mount_id):
        # 같은 inode를 다른 mount로 다시 노출한 경우도 구별하려면 현재 이름을 연 FD가 필요하다.
        descriptor = self.owned.open(root.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if _directory(os.fstat(descriptor)) != root.identity:
                raise ObservationUnavailable('reopened root does not identify held directory')
            current_mount = parse_fdinfo(_read_at(self.owned, self.self_fd,
                                                 'fdinfo/' + str(descriptor), 8192))
            if current_mount != mount_id:
                raise ObservationUnavailable('named root mount differs from held root mount')
        finally:
            self.owned.close(descriptor)

    def snapshot(self):
        self.proc.check()
        shm_stat = self.shm.check()
        if (os.getpid() != self.pid or _link(self.proc.descriptor, b'self') != self.self_link
                or _directory(os.fstat(self.self_fd)) != self.self_identity
                or _directory(os.stat('self', dir_fd=self.proc.descriptor)) != self.self_identity):
            raise ObservationUnavailable('current process directory changed')
        pid_inode = self.pid_ns.check(self.self_fd)
        mnt_inode = self.mnt_ns.check(self.self_fd)
        boot = parse_boot_id(_read_at(self.owned, self.proc.descriptor, 'sys/kernel/random/boot_id', 64))
        validate_self_status(_read_at(self.owned, self.self_fd, 'status', 65536), self.pid)
        current = parse_proc_stat(_read_at(self.owned, self.self_fd, 'stat', 4096), self.pid)
        records = parse_mountinfo(_read_at(self.owned, self.self_fd, 'mountinfo', 1048576))
        proc_mount_id = parse_fdinfo(_read_at(self.owned, self.self_fd,
                                            'fdinfo/' + str(self.proc.descriptor), 8192))
        shm_mount_id = parse_fdinfo(_read_at(self.owned, self.self_fd,
                                           'fdinfo/' + str(self.shm.descriptor), 8192))
        proc_mount = _selected_mount(records, proc_mount_id, self.proc, b'proc')
        shm_mount = _selected_mount(records, shm_mount_id, self.shm, b'tmpfs')
        self._check_named_mount(self.proc, proc_mount_id)
        self._check_named_mount(self.shm, shm_mount_id)
        runtime = RuntimeScope(boot, pid_inode, mnt_inode, shm_mount_id, shm_stat.st_dev,
                               shm_stat.st_ino, 'tmpfs')
        return _Snapshot(runtime, ProcessBirth(current.pid, current.start_ticks), proc_mount, shm_mount)


def _owner_outcome(owned, proc_fd, owner, original_runtime, current):
    if (original_runtime.boot_id != current.runtime.boot_id
            or original_runtime.pid_ns_inode != current.runtime.pid_ns_inode):
        return 'UNKNOWN'
    if owner.birth == current.birth:
        # 현재 scope가 실제 확인한 자기 birth를 재사용한다. 숫자 lookup 오류로 자기 EXITED를 만들지 않는다.
        return 'ALIVE'
    try:
        descriptor = owned.open(str(owner.pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                parent=proc_fd)
    except OSError as error:
        return 'EXITED' if error.errno == errno.ENOENT else 'UNKNOWN'
    try:
        identity = _directory(os.fstat(descriptor))
        raw = _read_at(owned, descriptor, 'stat', 4096)
        parsed = parse_proc_stat(raw, owner.pid)
        if _directory(os.fstat(descriptor)) != identity:
            return 'UNKNOWN'
        return 'ALIVE' if parsed.start_ticks == owner.start_ticks else 'EXITED'
    except (OSError, ProcFormatError, ObservationUnavailable):
        if owned.close_failed:
            raise ObservationUnavailable('owner descriptor close was not confirmed')
        return 'UNKNOWN'
    finally:
        owned.close(descriptor)


class ProcessObserver:
    """한 PID·Thread에서 고정 Linux profile을 관측한다. 생성자는 파일 I/O를 하지 않는다.

    store는 빌린 실제 객체다. observe는 원 L1과 마지막 실제 최신 L2를 별도로 반환하며
    원 source가 바뀌거나 owned FD 반환이 불명이면 ObservationUnavailable로 끝난다.
    """

    def __init__(self, store, service_instance):
        if type(store) is not ResourceStore:
            raise ObservationUsageError('expected exact ResourceStore')
        try:
            _uuid(service_instance)
        except ManifestError as error:
            raise ObservationUsageError('expected canonical service UUID') from error
        self._store = store
        self._service = service_instance
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._active = False

    def __copy__(self):
        raise ObservationUsageError('observer copying is unsupported')

    def __deepcopy__(self, memo):
        raise ObservationUsageError('observer copying is unsupported')

    def __reduce__(self):
        raise ObservationUsageError('observer transfer is unsupported')

    def observe(self):
        """실제 두 load 사이의 known-owner 진술을 만든다. 이 호출은 기한/정리 권한을 발행하지 않는다."""
        if (os.getpid() != self._pid or threading.current_thread() is not self._thread or self._active):
            raise ObservationUsageError('observation requires idle original PID and Thread')
        self._active = True
        try:
            if sys.platform != 'linux':
                raise ObservationUnavailable('actual observation supports Linux only')
            started = _clock()
            original = self._store.load()
            if original.outcome.kind not in ('VALID', 'MISSING'):
                raise ObservationUnavailable('original source is unavailable')
            owned = _OwnedFds()
            try:
                pins = _ScopePins(owned)
                first = pins.snapshot()
                guard = CurrentGuardScope(original.lock_nonce, self._service, first.runtime, first.birth)
                manifest = original.outcome.manifest
                owners = () if manifest is None else tuple(
                    OwnerObservation(manifest.ref, guard, owner,
                                     _owner_outcome(owned, pins.proc.descriptor, owner, manifest.runtime, first))
                    for owner in manifest.known_owners)
                if pins.snapshot() != first:
                    raise ObservationUnavailable('runtime or current birth changed during observation')
            finally:
                owned.close_all()
            latest = self._store.load()
            if not _same_source(original, latest):
                raise ObservationUnavailable('original source changed before final load')
            completed = _clock()
            if completed < started:
                raise ObservationUnavailable('monotonic observation bracket regressed')
            return ObservationBatch(original, latest, guard, owners, started, completed)
        except (OSError, ProcFormatError, ManifestError, GuardInputError, StoreError, StoreUsageError) as error:
            raise ObservationUnavailable('source or native observation could not be confirmed') from error
        finally:
            self._active = False
