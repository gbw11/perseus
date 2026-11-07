"""S 소유 IPC 생성과 worker의 bounded raw 복사. guard/실종료 판정은 caller 소유다.

named constructor는 실제 INTENT StoreAck 뒤 한 번만 진입한다. fixed6 semaphore는
spawn 참조로 전달하며 private unlink/rebuild는 사용하지 않는다. raw reader는
SHM view를 반환한 뒤 owned bytes만 내보내므로 parser가 slot 수명을 늘리지 않는다.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import sysconfig
import time
import uuid

from .procfs_formats import parse_boot_id, parse_fdinfo, parse_mountinfo
from .raw_leases import (ARENA_BYTES, SLOT_BYTES, FilledInfo, RawScope, ReadLease,
                         RawOwnerExitBarrier, RawOwnerExitEvidence, RawRecoveryBarrier,
                         RawRecoveryEvidence, ViewReturnFact, WriteGrant)
from .raw_slot import ExpectedSlot, SlotEncoding, decode_owned, prefix
from .resource_manifest import ResourceManifest, ResourceRecord
from .resource_store import StoreAck


DB_COPY_BYTES = 13 * 1024 * 1024 + 20 * 1024
TOKEN_SLOTS = ('RAW_0_WRITABLE', 'RAW_0_READY', 'RAW_1_WRITABLE', 'RAW_1_READY',
               'DB_WRITABLE', 'DB_READY')


class NativeResourceError(RuntimeError):
    """생성·매핑·token 상태가 불명이다. caller는 새 생성/사용을 중단한다."""


def _now():
    return time.monotonic_ns() // 1000


def _exact(value, expected):
    if type(value) is not expected:
        raise NativeResourceError('expected exact ' + expected.__name__)


def _identifier(value):
    if type(value) is not str:
        raise NativeResourceError('expected canonical UUID')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError as error:
        raise NativeResourceError('expected canonical UUID') from error


def _identity(value):
    return value.st_dev, value.st_ino


def _small(path, cap):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        output = bytearray()
        while len(output) <= cap:
            chunk = os.read(fd, cap + 1 - len(output))
            if not chunk:
                break
            output.extend(chunk)
        if len(output) > cap:
            raise NativeResourceError('native profile metadata exceeds cap')
        return bytes(output)
    finally:
        os.close(fd)


def _shared_memory_module():
    from multiprocessing import shared_memory
    return shared_memory


def _creator_profile():
    if (sys.platform != 'linux' or sys.implementation.name != 'cpython'
            or sys.version_info[:3] != (3, 11, 18)
            or os.confstr('CS_GNU_LIBC_VERSION') != 'glibc 2.27'):
        raise NativeResourceError('creator requires pinned CPython3.11.18/glibc2.27 Linux')
    from multiprocessing import shared_memory, synchronize
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    for module in (shared_memory, synchronize):
        source = Path(module.__file__).resolve()
        if not source.is_relative_to(stdlib):
            raise NativeResourceError('creator is not the pinned standard library origin')


@dataclass(frozen=True)
class ArenaSpec:
    arena_id: str
    name: str
    size_bytes: int
    role: str

    def __post_init__(self):
        _identifier(self.arena_id)
        expected = '/xnv_raw_' if self.role == 'RAW' else '/xnv_db_'
        size = ARENA_BYTES if self.role == 'RAW' else DB_COPY_BYTES
        if (type(self.role) is not str or self.role not in ('RAW', 'DB_COPY')
                or type(self.name) is not str or self.name != expected + self.arena_id
                or type(self.size_bytes) is not int or self.size_bytes != size):
            raise NativeResourceError('arena differs from the fixed resource profile')


@dataclass(frozen=True)
class NativeHandles:
    raw: ArenaSpec
    db_copy: ArenaSpec
    raw_tokens: tuple
    db_tokens: tuple


@dataclass(frozen=True)
class RetiredArenaCleanup:
    """S가 원 retired mapping을 닫고 정확 이름 부재를 확인한 실제 완료 값."""

    operation_id: str
    resource: ResourceRecord
    completed_us: int

    def __post_init__(self):
        _identifier(self.operation_id)
        _exact(self.resource, ResourceRecord)
        if (self.resource.slot != 'RAW_RETIRED' or self.resource.state != 'KNOWN'
                or type(self.completed_us) is not int or not 0 <= self.completed_us <= 2**64 - 1):
            raise NativeResourceError('invalid retired cleanup identity')


class NativeResources:
    """S의 native 소유자. create_one은 단일 INTENT ACK, spawn_handles는 KNOWN ACK다.

    close_after_children는 caller가 모든 실제 child를 reap한 뒤만 호출한다.
    이 클래스는 child exit를 추측하거나 guard/StoreAck의 실제 출처를 생산하지 않는다.
    """

    def __init__(self, ctx):
        if ctx.get_start_method() != 'spawn':
            raise NativeResourceError('resource family must use spawn')
        self._ctx = ctx
        self._pid = os.getpid()
        self._binding = None
        self._created = {}
        self._attempted = set()
        self._shms = {}
        self._tokens = {}
        self._closed = False
        self._cleanup_error = None
        self._recovery_error = None
        self._raw_attempt = None
        self._normalization = None
        self._retirement = None
        self._retired_cleanup_error = None

    @property
    def creation_attempted(self):
        """현재 owner가 실제 constructor에 진입했는지 보존한다. guard 허가 자체는 아니다."""
        if os.getpid() != self._pid:
            raise NativeResourceError('native inventory belongs to another process')
        return bool(self._attempted or self._raw_attempt is not None or self._created or self._shms or self._tokens)

    def _healthy(self):
        if self._recovery_error is not None:
            raise NativeResourceError('native recovery remains failed') from self._recovery_error

    def _close_root(self, fd, *, retired=False):
        try:
            os.close(fd)
        except OSError as error:
            self._recovery_error = error
            if retired:
                self._retired_cleanup_error = error
            raise NativeResourceError('native backing descriptor close was not confirmed') from error

    def _manifest(self, ack):
        _exact(ack, StoreAck)
        if self._closed or os.getpid() != self._pid:
            raise NativeResourceError('native owner is closed or belongs to another process')
        manifest = ResourceManifest(ack.proposal.target_bytes)
        if manifest.ref != ack.target_ref or manifest.state != 'DIRTY':
            raise NativeResourceError('creation needs original DIRTY target ACK')
        binding = (manifest.service_instance, manifest.resource_epoch, manifest.runtime)
        if self._binding is None:
            self._binding = binding
        if binding != self._binding:
            raise NativeResourceError('resource epoch or runtime changed')
        return manifest

    def _root(self, runtime):
        fd = os.open('/dev/shm', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self._check_root(fd, runtime)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _check_root(self, fd, runtime):
        opened = os.fstat(fd)
        named = os.stat('/dev/shm', follow_symlinks=False)
        mount_id = parse_fdinfo(_small('/proc/self/fdinfo/' + str(fd), 8192))
        rows = parse_mountinfo(_small('/proc/self/mountinfo', 1024 * 1024))
        selected = [r for r in rows if r.mount_id == mount_id]
        if (not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(named)
                or _identity(opened) != (runtime.shm_dev, runtime.shm_root_inode)
                or mount_id != runtime.shm_mount_id or len(selected) != 1
                or selected[0].fs_type != b'tmpfs' or selected[0].mountpoint != b'/dev/shm'
                or selected[0].root != b'/'
                or parse_boot_id(_small('/proc/sys/kernel/random/boot_id', 64)) != runtime.boot_id
                or os.stat('/proc/self/ns/pid').st_ino != runtime.pid_ns_inode
                or os.stat('/proc/self/ns/mnt').st_ino != runtime.mnt_ns_inode):
            raise NativeResourceError('actual IPC backing runtime differs from INTENT')

    def create_one(self, intent_ack):
        """constructor 진입 전 원 ACK를 검사한다. 실패한 slot은 재시도하지 않는다."""
        manifest = self._manifest(intent_ack)
        self._healthy()
        if len(manifest.intents) != 1:
            raise NativeResourceError('creation requires exactly one pending intent')
        intent = manifest.intents[0]
        if manifest.resource(intent.slot).state != 'INTENT':
            raise NativeResourceError('UNKNOWN inventory cannot retry a constructor')
        if intent.slot != 'RAW_CURRENT' and intent.slot in self._attempted:
            raise NativeResourceError('constructor was already attempted for this slot')
        if intent.slot not in ('RAW_CURRENT', 'DB_COPY_0') + TOKEN_SLOTS:
            raise NativeResourceError('only one raw/copy arena and fixed6 tokens are supported')
        if intent.slot == 'RAW_CURRENT':
            if 'RAW_CURRENT' in self._shms or 'RAW_RETIRED' in self._shms:
                raise NativeResourceError('current or unresolved retired arena still exists')
            if manifest.resource('RAW_RETIRED').state != 'NOT_ATTEMPTED':
                raise NativeResourceError('new raw creation requires durable cleared retired inventory')
            if self._raw_attempt is not None:
                previous_id, previous_revision = self._raw_attempt
                if (intent.planned_object_id == previous_id or manifest.revision <= previous_revision
                        or self._retirement is None or self._retirement[2] is None):
                    raise NativeResourceError('new physical raw attempt lacks completed retirement')
        _creator_profile()
        root_fd = self._root(manifest.runtime)
        try:
            if intent.slot == 'RAW_CURRENT':
                self._raw_attempt = (intent.planned_object_id, manifest.revision)
            else:
                self._attempted.add(intent.slot)
            if intent.slot in TOKEN_SLOTS:
                sem = self._ctx.Semaphore(1 if intent.slot.endswith('WRITABLE') else 0)
                self._tokens[intent.slot] = sem
                # 고정 CPython3.11의 read-only inventory만 읽는다.
                name = sem._semlock.name
                if type(name) is not str or not name.startswith('/'):
                    raise NativeResourceError('named spawn semaphore inventory is unavailable')
                entry = os.stat('sem.' + name[1:], dir_fd=root_fd, follow_symlinks=False)
                if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid():
                    raise NativeResourceError('semaphore backing entry is not owned regular storage')
                record = ResourceRecord(intent.slot, 'KNOWN', name, None, None)
            else:
                size = ARENA_BYTES if intent.slot == 'RAW_CURRENT' else DB_COPY_BYTES
                if intent.planned_size_bytes != size:
                    raise NativeResourceError('planned arena size differs from fixed profile')
                leaf = intent.planned_name[1:]
                try:
                    os.stat(leaf, dir_fd=root_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise NativeResourceError('planned exclusive arena name is already present')
                shm = _shared_memory_module().SharedMemory(name=leaf, create=True, size=size)
                self._shms[intent.slot] = shm
                entry = os.stat(leaf, dir_fd=root_fd, follow_symlinks=False)
                # 고정 stdlib의 read-only fd를 실제 backing entry와 결합한다.
                mapped = os.fstat(shm._fd)
                if (shm.name != leaf or shm.size != size or not stat.S_ISREG(entry.st_mode)
                        or entry.st_uid != os.geteuid() or entry.st_nlink != 1
                        or entry.st_size != size or _identity(mapped) != _identity(entry)):
                    raise NativeResourceError('created arena mapping differs from recorded backing entry')
                record = ResourceRecord(intent.slot, 'KNOWN', intent.planned_name,
                                        intent.planned_object_id, size)
            self._check_root(root_fd, manifest.runtime)
            self._created[intent.slot] = record
            return record
        except Exception as error:
            self._recovery_error = error
            raise NativeResourceError('creation entered or failed; retain unresolved DIRTY inventory') from error
        finally:
            self._close_root(root_fd)

    def spawn_handles(self, final_known_ack):
        manifest = self._manifest(final_known_ack)
        self._healthy()
        needed = ('RAW_CURRENT', 'DB_COPY_0') + TOKEN_SLOTS
        if (manifest.intents or any(slot not in self._created or manifest.resource(slot) != self._created[slot]
                                    for slot in needed)):
            raise NativeResourceError('spawn requires durable KNOWN inventory for all eight resources')
        raw, db = self._created['RAW_CURRENT'], self._created['DB_COPY_0']
        return NativeHandles(ArenaSpec(raw.object_id, raw.name, raw.size_bytes, 'RAW'),
                             ArenaSpec(db.object_id, db.name, db.size_bytes, 'DB_COPY'),
                             tuple((self._tokens['RAW_' + str(i) + '_WRITABLE'],
                                    self._tokens['RAW_' + str(i) + '_READY']) for i in (0, 1)),
                             (self._tokens['DB_WRITABLE'], self._tokens['DB_READY']))

    def normalize_raw(self, current_ack, operation_id, barrier):
        """원 PAUSED+M reap 또는 A/M reap 뒤만 호출한다. 그 실제 근거는 S가 검증한다.

        동일 operation과 원문에는 원 evidence만 돌려주며 token을 다시 만지지 않는다.
        각 token은 최대1개만 drain하고 두 번째가 있으면 복구를 sticky 실패로 닫는다.
        """
        manifest = self._manifest(current_ack)
        _identifier(operation_id)
        if type(barrier) not in (RawRecoveryBarrier, RawOwnerExitBarrier):
            raise NativeResourceError('exact raw recovery barrier is required')
        fingerprint = (manifest.ref, barrier)
        previous = self._normalization
        if previous is not None and previous[0] == operation_id:
            if previous[1] != fingerprint:
                raise NativeResourceError('normalization operation changed its original fields')
            self._healthy()
            return previous[2]
        self._healthy()
        if previous is not None and manifest.revision <= previous[1][0].revision:
            raise NativeResourceError('normalization requires a newer durable recovery revision')
        raw = self._created.get('RAW_CURRENT')
        scope = barrier.scope
        if (manifest.intents or raw is None or manifest.resource('RAW_CURRENT') != raw
                or 'RAW_RETIRED' in self._shms
                or scope.service_instance != manifest.service_instance or scope.arena_id != raw.object_id
                or scope.arena_name != raw.name or scope.arena_size_bytes != raw.size_bytes
                or any(manifest.resource(slot) != self._created.get(slot) for slot in TOKEN_SLOTS)):
            raise NativeResourceError('recovery barrier differs from durable current raw/token inventory')
        event_times = ((barrier.paused_us, barrier.old_reader_exited_us)
                       if type(barrier) is RawRecoveryBarrier else
                       (barrier.writer_exited_us, barrier.reader_exited_us))
        if _now() < max(event_times):
            raise NativeResourceError('quiescence evidence is in the future')
        root_fd = self._root(manifest.runtime)
        try:
            for slot in TOKEN_SLOTS[:4]:
                token = self._tokens[slot]
                token.acquire(False)
                if token.acquire(False):
                    raise NativeResourceError('publication token count exceeds one')
            self._tokens['RAW_0_WRITABLE'].release()
            self._tokens['RAW_1_WRITABLE'].release()
            self._check_root(root_fd, manifest.runtime)
            evidence_type = RawRecoveryEvidence if type(barrier) is RawRecoveryBarrier else RawOwnerExitEvidence
            evidence = evidence_type(barrier, operation_id, _now())
            self._normalization = (operation_id, fingerprint, evidence)
            return evidence
        except Exception as error:
            self._recovery_error = error
            raise NativeResourceError('raw token normalization failed; no repeat or new arena is permitted') from error
        finally:
            self._close_root(root_fd)

    def retire_raw(self, retired_ack, operation_id):
        """원 A/M actual reap과 owner-exit 정규화 뒤의 retired ACK만 처리한다.

        DB arena/token은 유지한다. close/unlink를 재시도하지 않으며 실패한 retired
        set은 최대1개로 보존해 다음 물리 arena 할당을 막는다.
        """
        manifest = self._manifest(retired_ack)
        _identifier(operation_id)
        previous = self._retirement
        if previous is not None and previous[0] == operation_id:
            if previous[1] != manifest.ref:
                raise NativeResourceError('retirement operation changed original ACK')
            self._healthy()
            return previous[2]
        self._healthy()
        current = self._created.get('RAW_CURRENT')
        retired = manifest.resource('RAW_RETIRED')
        source = retired_ack.proposal.expected.outcome.manifest
        normalized = None if self._normalization is None else self._normalization[2]
        if (current is None or source is None or source.resource('RAW_CURRENT') != current
                or manifest.resource('RAW_CURRENT').state != 'NOT_ATTEMPTED' or manifest.intents
                or retired != ResourceRecord('RAW_RETIRED', 'KNOWN', current.name,
                                             current.object_id, current.size_bytes)
                or 'RAW_RETIRED' in self._shms or type(normalized) is not RawOwnerExitEvidence
                or normalized.barrier.scope.arena_id != current.object_id
                or self._normalization[1][0].revision > source.revision):
            raise NativeResourceError('retirement lacks exact original raw and owner-exit normalization')
        shm = self._shms.pop('RAW_CURRENT')
        self._shms['RAW_RETIRED'] = shm
        self._created.pop('RAW_CURRENT')
        self._created['RAW_RETIRED'] = retired
        self._retirement = (operation_id, manifest.ref, None)
        errors = []
        root_fd = None
        try:
            root_fd = self._root(manifest.runtime)
            entry = os.stat(retired.name[1:], dir_fd=root_fd, follow_symlinks=False)
            if _identity(entry) != _identity(os.fstat(shm._fd)):
                raise NativeResourceError('retired exact name no longer identifies owned mapping')
            try:
                shm.close()
            except Exception as error:
                errors.append(error)
            try:
                shm.unlink()
            except Exception as error:
                errors.append(error)
            if errors:
                raise errors[0]
            try:
                os.stat(retired.name[1:], dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise NativeResourceError('retired backing name remains present')
            self._check_root(root_fd, manifest.runtime)
            result = RetiredArenaCleanup(operation_id, retired, _now())
            self._shms.pop('RAW_RETIRED')
            self._retirement = (operation_id, manifest.ref, result)
            return result
        except Exception as error:
            self._retired_cleanup_error = self._recovery_error = error
            raise NativeResourceError('retired cleanup remains unknown; additional allocation is forbidden') from error
        finally:
            if root_fd is not None:
                self._close_root(root_fd, retired=True)

    def close_after_children(self):
        """SHM만 S가 unlink한다. 첫 실패는 sticky이며 반복 호출이 성공으로 바꾸지 않는다."""
        if os.getpid() != self._pid:
            raise NativeResourceError('only the creating process can unlink resources')
        if self._closed:
            if self._cleanup_error is not None:
                raise NativeResourceError('native cleanup was not fully confirmed') from self._cleanup_error
            return
        self._closed = True
        errors = [] if self._retired_cleanup_error is None else [self._retired_cleanup_error]
        for slot, shm in self._shms.items():
            if slot == 'RAW_RETIRED' and self._retired_cleanup_error is not None:
                continue
            try:
                shm.close()
            except Exception as error:
                errors.append(error)
            try:
                shm.unlink()
            except Exception as error:
                errors.append(error)
        if errors:
            self._cleanup_error = errors[0]
            raise NativeResourceError('native cleanup was not fully confirmed') from self._cleanup_error


def _tokens(tokens):
    from multiprocessing.synchronize import Semaphore
    if (type(tokens) is not tuple or len(tokens) != 2
            or any(type(pair) is not tuple or len(pair) != 2 for pair in tokens)
            or any(type(token) is not Semaphore for pair in tokens for token in pair)):
        raise NativeResourceError('raw tokens require exactly two spawn Semaphore pairs')
    return tokens


class _RawAttachment:
    def __init__(self, scope, tokens):
        _exact(scope, RawScope)
        self._scope = scope
        self._tokens = _tokens(tokens)
        spec = ArenaSpec(scope.arena_id, scope.arena_name, scope.arena_size_bytes, 'RAW')
        self._shm = _shared_memory_module().SharedMemory(name=spec.name[1:], create=False)
        if self._shm.size != spec.size_bytes:
            self._shm.close()
            raise NativeResourceError('attached arena size differs from registered scope')
        self._closed = False
        self._seen = [None, None]

    def _check(self, key):
        if self._closed or key.scope != self._scope:
            raise NativeResourceError('raw key differs from live attachment')

    def _owned(self, offset, length):
        view = self._shm.buf[offset:offset + length]
        try:
            return bytes(view)
        finally:
            view.release()

    def close(self):
        if not self._closed:
            self._closed = True
            self._shm.close()


class RawArenaWriter(_RawAttachment):
    """A 전용. 실제 grant와 writable token을 모두 얻은 뒤 bounded bytes를 게시한다."""

    def publish(self, grant, encoding):
        _exact(grant, WriteGrant)
        _exact(encoding, SlotEncoding)
        self._check(grant.key)
        if encoding.context.write_key != grant.key:
            raise NativeResourceError('encoding differs from original write grant')
        slot = grant.key.slot
        digest = hashlib.sha256(encoding.raw).digest()
        previous = self._seen[slot]
        if previous is not None:
            if previous[0] == grant and previous[1] == digest:
                return previous[2]
            if grant.key.generation <= previous[0].key.generation:
                raise NativeResourceError('write generation was already consumed')
        now = _now()
        grant.request.check(now)
        if now < grant.granted_us:
            raise NativeResourceError('write grant has not reached its reservation time')
        writable, ready = self._tokens[slot]
        if not writable.acquire(False):
            raise NativeResourceError('writable token missing for valid grant')
        start = slot * SLOT_BYTES
        view = self._shm.buf[start:start + len(encoding.raw)]
        try:
            view[:] = encoding.raw
        finally:
            view.release()
        grant.request.check(_now())
        ready.release()
        completed = _now()
        grant.request.check(completed)
        info = FilledInfo(grant.key, encoding.payload_length, encoding.descriptor_sha256, completed)
        self._seen[slot] = (grant, digest, info)
        return info


class RawArenaReader(_RawAttachment):
    """M 전용. ready 취득 뒤 bounded owned copy를 만들고 view/post 사실을 반환한다."""

    def copy_owned(self, lease, expected):
        _exact(lease, ReadLease)
        _exact(expected, ExpectedSlot)
        key = lease.key.write_key
        self._check(key)
        if (expected.context.write_key != key
                or expected.original_filled_info != lease.read_request.offer.content):
            raise NativeResourceError('reader expected slot differs from original lease/FILLED')
        slot = key.slot
        previous = self._seen[slot]
        if previous is not None and key.generation <= previous.key.write_key.generation:
            raise NativeResourceError('read generation was already consumed')
        now = _now()
        lease.read_request.request.check(now)
        if now < lease.reserved_us:
            raise NativeResourceError('read lease has not reached its reservation time')
        writable, ready = self._tokens[slot]
        if not ready.acquire(False):
            raise NativeResourceError('ready token missing for valid acquired lease')
        self._seen[slot] = lease
        start = slot * SLOT_BYTES
        try:
            header = prefix(self._owned(start, 128), expected.context)
            owned = decode_owned(self._owned(start, header.used_length), expected, header)
        finally:
            views_ended = _now()
            writable.release()
            posted = _now()
        lease.read_request.request.check(posted)
        return owned, ViewReturnFact(lease, views_ended, posted)
