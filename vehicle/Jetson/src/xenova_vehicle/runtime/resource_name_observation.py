"""원 KNOWN 이름의 Linux tmpfs backing entry를 비추적 stat으로 관측한다.

공개 ProcessObserver 두 결과 사이에서 자기 proc/root/namespace FD를 독립 보유한다.
leaf open·생성·payload/mapping·token·정리를 하지 않으며 borrowed store를 닫지 않는다.
결과는 현재 entry의 관측이고 creator 출처·남은 참조·CLEAN/생성/READY 권한이 아니다.
동기 I/O의 반환시간이나 전체 OS 원자성을 보장하지 않으며 새 기한을 발행하지 않는다.
"""

from dataclasses import dataclass
import errno
import os
import stat
import sys
import threading

from .process_observation import (
    ObservationBatch, ObservationUnavailable, ObservationUsageError, ProcessObserver,
)
from .procfs_formats import (
    ProcFormatError, parse_boot_id, parse_fdinfo, parse_mountinfo, parse_proc_stat,
    validate_self_status,
)
from .resource_guard import GuardInputError, NameObservation
from .resource_manifest import ManifestError, ProcessBirth, RuntimeScope
from .resource_store import ResourceStore, StoreError, StoreUsageError


class NameObservationUsageError(ValueError):
    """exact 입력·원 PID/Thread·비재진입/이전·순수 결과 관계 계약 위반이다."""


class NameObservationUnavailable(RuntimeError):
    """실제 출처·중간 backing profile·store 수명·자기 FD 반환을 확인하지 못했다."""


@dataclass(frozen=True)
class NameObservationBatch:
    """두 원 batch와 원 이름 진술. 생성자는 순수 값 관계만 검사하며 OS proof를 인증하지 않는다."""

    before: ObservationBatch
    after: ObservationBatch
    names: tuple[NameObservation, ...]

    def __post_init__(self):
        if type(self.before) is not ObservationBatch or type(self.after) is not ObservationBatch:
            raise NameObservationUsageError('expected exact original observation batches')
        if type(self.names) is not tuple or len(self.names) > 12:
            raise NameObservationUsageError('expected bounded exact name tuple')
        if any(type(item) is not NameObservation for item in self.names):
            raise NameObservationUsageError('expected exact name observations')
        loads = (self.before.original_load, self.before.latest_load,
                 self.after.original_load, self.after.latest_load)
        if any(load.outcome.kind != 'VALID' for load in loads):
            raise NameObservationUsageError('name observations require four VALID loads')
        if len({load.outcome.load_id for load in loads}) != 4:
            raise NameObservationUsageError('four actual loads must have distinct IDs')
        if (self.before.guard != self.after.guard
                or any(load.lock_nonce != self.before.guard.guard_id for load in loads)):
            raise NameObservationUsageError('endpoint guard lifetime differs')
        original = self.before.original_load.outcome.manifest
        if any(load.outcome.manifest.ref != original.ref
               or load.outcome.manifest.raw_bytes != original.raw_bytes for load in loads):
            raise NameObservationUsageError('original raw source differs across endpoints')
        if self.before.completed_ns > self.after.started_ns:
            raise NameObservationUsageError('endpoint observation intervals overlap or regress')
        if tuple(item.resource for item in self.names) != original.known_resources:
            raise NameObservationUsageError('names must cover original known resources in order')
        if any(item.ref != original.ref or item.guard != self.before.guard for item in self.names):
            raise NameObservationUsageError('name statement lost original ref or guard')
        if (original.runtime != self.before.guard.runtime
                and any(item.outcome != 'UNKNOWN' for item in self.names)):
            raise NameObservationUsageError('old backing scope permits UNKNOWN statements only')

    @property
    def original_load(self):
        return self.before.original_load

    @property
    def latest_load(self):
        return self.after.latest_load

    @property
    def guard(self):
        return self.before.guard

    @property
    def started_ns(self):
        return self.before.started_ns

    @property
    def completed_ns(self):
        return self.after.completed_ns


def _identity(value):
    return value.st_dev, value.st_ino


def _directory(value):
    if not stat.S_ISDIR(value.st_mode):
        raise NameObservationUnavailable('expected actual directory')
    return _identity(value)


class _Descriptors:
    """이번 호출의 FD만 보유한다. 반납 불명은 sticky이며 같은 번호를 다시 닫지 않는다."""

    def __init__(self):
        self._owned = []
        self._close_failed = False

    def open(self, path, flags, *, parent=None):
        descriptor = os.open(path, flags, **({} if parent is None else {'dir_fd': parent}))
        self._owned.append(descriptor)
        return descriptor

    def close(self, descriptor):
        self._owned.remove(descriptor)
        try:
            os.close(descriptor)
        except OSError as error:
            self._close_failed = True
            raise NameObservationUnavailable('owned descriptor close was not confirmed') from error

    def close_all(self):
        for descriptor in tuple(reversed(self._owned)):
            try:
                self.close(descriptor)
            except NameObservationUnavailable:
                pass
        if self._close_failed:
            raise NameObservationUnavailable('owned descriptor return was not confirmed')


def _read_metadata(descriptors, parent, path, cap):
    descriptor = descriptors.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                   parent=parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NameObservationUnavailable('expected regular proc pseudo file')
        raw = bytearray()
        while len(raw) <= cap:
            requested = cap + 1 - len(raw)
            chunk = os.read(descriptor, requested)
            if type(chunk) is not bytes or len(chunk) > requested:
                raise NameObservationUnavailable('metadata read violated bytes bound')
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > cap:
            raise NameObservationUnavailable('metadata exceeds fixed profile cap')
        return bytes(raw)
    finally:
        descriptors.close(descriptor)


def _read_link(parent, path):
    raw = os.readlink(path, dir_fd=parent)
    if type(raw) is not bytes or not 1 <= len(raw) <= 256:
        raise NameObservationUnavailable('kernel self link exceeds fixed profile')
    return raw


@dataclass(frozen=True)
class _Root:
    path: str
    descriptor: int
    identity: tuple[int, int]

    def check(self):
        actual = os.fstat(self.descriptor)
        named = os.stat(self.path, follow_symlinks=False)
        if _directory(actual) != self.identity or _directory(named) != self.identity:
            raise NameObservationUnavailable('held root and named root identity differ')
        return actual


def _root(descriptors, path):
    identity = _directory(os.stat(path, follow_symlinks=False))
    descriptor = descriptors.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    result = _Root(path, descriptor, identity)
    result.check()
    return result


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
                or _read_link(parent, self.path) != self.link):
            raise NameObservationUnavailable('current self namespace differs from held namespace')
        return actual.st_ino


def _namespace(descriptors, parent, path):
    link = _read_link(parent, path)
    # 고정 kernel namespace magic link만 의도적으로 따라간다. 일반 metadata 입력에는 NOFOLLOW를 쓴다.
    descriptor = descriptors.open(path, os.O_RDONLY | os.O_CLOEXEC, parent=parent)
    actual = os.fstat(descriptor)
    if not stat.S_ISREG(actual.st_mode):
        raise NameObservationUnavailable('expected kernel namespace handle')
    result = _Namespace(path, descriptor, _identity(actual), link)
    result.check(parent)
    return result


def _select_mount(records, mount_id, root, filesystem):
    matching = tuple(record for record in records if record.mount_id == mount_id)
    if len(matching) != 1:
        raise NameObservationUnavailable('actual root mount has no unique record')
    record = matching[0]
    actual = root.check()
    if (record.root != b'/' or record.mountpoint != root.path.encode('ascii')
            or record.fs_type != filesystem
            or (record.major, record.minor) != (os.major(actual.st_dev), os.minor(actual.st_dev))):
        raise NameObservationUnavailable('selected root mount profile differs')
    if filesystem == b'proc':
        options = [option for option in record.mount_options + record.super_options
                   if option == b'hidepid' or option.startswith(b'hidepid=')]
        if options not in ([], [b'hidepid=0']):
            raise NameObservationUnavailable('proc profile requires unambiguous hidepid zero')
    return record


class _MiddleProbe:
    """B0/B1과 별도의 실제 FD 수명이다. 두 endpoint가 닫은 private FD를 재사용하지 않는다."""

    def __init__(self, descriptors, guard):
        self.descriptors = descriptors
        self.guard = guard
        self.proc = _root(descriptors, '/proc')
        self.shm = _root(descriptors, '/dev/shm')
        self.pid = os.getpid()
        self.self_link = _read_link(self.proc.descriptor, b'self')
        if self.self_link != str(self.pid).encode('ascii'):
            raise NameObservationUnavailable('kernel self link differs from current PID')
        # self도 승인한 procfs의 고정 magic link다. 실제 status PID-view를 별도로 확인한다.
        self.self_fd = descriptors.open('self', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                                        parent=self.proc.descriptor)
        self.self_identity = _directory(os.fstat(self.self_fd))
        self.pid_ns = _namespace(descriptors, self.self_fd, b'ns/pid')
        self.mnt_ns = _namespace(descriptors, self.self_fd, b'ns/mnt')
        self._first_records = self.snapshot()

    def _named_mount(self, root, expected_mount_id):
        descriptor = self.descriptors.open(root.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if _directory(os.fstat(descriptor)) != root.identity:
                raise NameObservationUnavailable('reopened named root identity differs')
            mount_id = parse_fdinfo(_read_metadata(self.descriptors, self.self_fd,
                                                   'fdinfo/' + str(descriptor), 8192))
            if mount_id != expected_mount_id:
                raise NameObservationUnavailable('named root mount differs from held root mount')
        finally:
            self.descriptors.close(descriptor)

    def snapshot(self):
        self.proc.check()
        shm = self.shm.check()
        if (os.getpid() != self.pid or _read_link(self.proc.descriptor, b'self') != self.self_link
                or _directory(os.fstat(self.self_fd)) != self.self_identity
                or _directory(os.stat('self', dir_fd=self.proc.descriptor)) != self.self_identity):
            raise NameObservationUnavailable('current self directory changed')
        pid_ns_inode = self.pid_ns.check(self.self_fd)
        mnt_ns_inode = self.mnt_ns.check(self.self_fd)
        boot = parse_boot_id(_read_metadata(self.descriptors, self.proc.descriptor,
                                            'sys/kernel/random/boot_id', 64))
        validate_self_status(_read_metadata(self.descriptors, self.self_fd, 'status', 65536), self.pid)
        current = parse_proc_stat(_read_metadata(self.descriptors, self.self_fd, 'stat', 4096), self.pid)
        records = parse_mountinfo(_read_metadata(self.descriptors, self.self_fd, 'mountinfo', 1048576))
        proc_mount_id = parse_fdinfo(_read_metadata(self.descriptors, self.self_fd,
                                                   'fdinfo/' + str(self.proc.descriptor), 8192))
        shm_mount_id = parse_fdinfo(_read_metadata(self.descriptors, self.self_fd,
                                                  'fdinfo/' + str(self.shm.descriptor), 8192))
        proc_record = _select_mount(records, proc_mount_id, self.proc, b'proc')
        shm_record = _select_mount(records, shm_mount_id, self.shm, b'tmpfs')
        self._named_mount(self.proc, proc_mount_id)
        self._named_mount(self.shm, shm_mount_id)
        runtime = RuntimeScope(boot, pid_ns_inode, mnt_ns_inode, shm_mount_id, shm.st_dev, shm.st_ino, 'tmpfs')
        if runtime != self.guard.runtime or ProcessBirth(current.pid, current.start_ticks) != self.guard.birth:
            raise NameObservationUnavailable('middle current runtime or birth differs from B0 guard')
        return proc_record, shm_record

    def finish(self):
        if self.snapshot() != self._first_records:
            raise NameObservationUnavailable('selected mount record changed during leaf observation')

    def lookup(self, resource):
        leaf = resource.name[1:] if resource.kind == 'SHM' else 'sem.' + resource.name[1:]
        try:
            value = os.stat(leaf, dir_fd=self.shm.descriptor, follow_symlinks=False)
        except OSError as error:
            return 'ABSENT' if error.errno == errno.ENOENT else 'UNKNOWN'
        return 'PRESENT' if stat.S_ISREG(value.st_mode) else 'UNKNOWN'


class ResourceNameObserver:
    """동일 actual store의 공개 observer 한 개를 보유한다. 생성자는 파일 I/O를 하지 않는다.

    매 observe에서 새 중간 FD를 모두 반환한 다음 B1을 얻는다. individual leaf UNKNOWN은
    보존하지만 source/profile/close 불명은 전체 unavailable이다. borrowed store는 닫지 않는다.
    """

    def __init__(self, store, service_instance):
        if type(store) is not ResourceStore:
            raise NameObservationUsageError('expected exact borrowed ResourceStore')
        try:
            self._observer = ProcessObserver(store, service_instance)
        except ObservationUsageError as error:
            raise NameObservationUsageError('invalid observer input or startup UUID') from error
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._active = False

    def __copy__(self):
        raise NameObservationUsageError('name producer copying is unsupported')

    def __deepcopy__(self, memo):
        raise NameObservationUsageError('name producer copying is unsupported')

    def __reduce__(self):
        raise NameObservationUsageError('name producer transfer is unsupported')

    def observe(self):
        """네 actual VALID load와 원 B0/B1을 유지한다. leaf 존재를 creator/CLEAN 권한으로 바꾸지 않는다."""
        if os.getpid() != self._pid or threading.current_thread() is not self._thread or self._active:
            raise NameObservationUsageError('name observation requires idle original PID and Thread')
        self._active = True
        try:
            if sys.platform != 'linux':
                raise NameObservationUnavailable('actual backing observation supports Linux only')
            before = self._observer.observe()
            if type(before) is not ObservationBatch or before.original_load.outcome.kind != 'VALID':
                raise NameObservationUnavailable('B0 requires original VALID source')
            original = before.original_load.outcome.manifest
            descriptors = _Descriptors()
            try:
                middle = _MiddleProbe(descriptors, before.guard)
                same_runtime = original.runtime == before.guard.runtime
                names = tuple(NameObservation(original.ref, before.guard, resource,
                                              middle.lookup(resource) if same_runtime else 'UNKNOWN')
                              for resource in original.known_resources)
                middle.finish()
            finally:
                descriptors.close_all()
            after = self._observer.observe()
            return NameObservationBatch(before, after, names)
        except (ObservationUnavailable, ObservationUsageError, ProcFormatError, ManifestError,
                GuardInputError, StoreError, StoreUsageError, NameObservationUsageError, OSError) as error:
            raise NameObservationUnavailable('endpoint source or native backing observation is unavailable') from error
        finally:
            self._active = False
