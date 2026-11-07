"""Linux 자원 manifest의 동기 저장 adapter. OS inventory·설치·생성 권한은 판정하지 않는다.

기존 trusted 상위 경로 아래의 디렉터리에 별도 안정 lock을 보유한다. 원 raw CAS와
canonical 출판 bytes를 구별하며, 같은 실제 lock 수명의 원 제안 한 건만 재시도한다.
I/O 반환시간은 bounded하지 않으며 S-main/비동기 writer와 아직 연결하지 않는다.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
import stat
import sys
import threading
import uuid

from .resource_guard import LoadOutcome
from .resource_manifest import ManifestError, ManifestRef, ResourceManifest, _uuid


class StoreError(RuntimeError):
    """지원 platform·실제 파일/namespace/lock 조건이 맞지 않는다."""


class StoreBusyError(StoreError):
    """별도 안정 lock을 이미 다른 open-file-description이 보유한다."""


class StoreUsageError(ValueError):
    """타입·소유 수명·원 receipt/proposal·one-pending 사용 계약 위반이다."""


def _exact(value, expected):
    if type(value) is not expected:
        raise StoreUsageError('expected exact ' + expected.__name__)


def _identifier(value):
    try:
        _uuid(value)
    except ManifestError as error:
        raise StoreUsageError('expected canonical UUID') from error


@dataclass(frozen=True)
class StoreLoad:
    """값만으로 실제 load를 증명하지 않는다. propose는 이 store의 최신 원 객체만 받는다."""

    lock_nonce: str
    outcome: LoadOutcome

    def __post_init__(self):
        _identifier(self.lock_nonce)
        _exact(self.outcome, LoadOutcome)


@dataclass(frozen=True)
class StoreProposal:
    """출처 raw와 실제 canonical target을 한 번 고정한 값. 외부 복제본은 출판할 수 없다."""

    request_id: str
    lock_nonce: str
    expected: StoreLoad
    manifest: ResourceManifest
    source_raw_sha256: str
    target_bytes: bytes
    target_ref: ManifestRef

    def __post_init__(self):
        _identifier(self.request_id)
        _identifier(self.lock_nonce)
        _exact(self.expected, StoreLoad)
        _exact(self.manifest, ResourceManifest)
        _exact(self.target_bytes, bytes)
        _exact(self.target_ref, ManifestRef)
        _exact(self.source_raw_sha256, str)
        if (self.expected.lock_nonce != self.lock_nonce
                or self.source_raw_sha256 != self.manifest.raw_sha256
                or self.target_bytes != self.manifest.canonical_bytes):
            raise StoreUsageError('proposal source or lock mismatch')
        try:
            target = ResourceManifest(self.target_bytes)
        except ManifestError as error:
            raise StoreUsageError('invalid proposal target') from error
        if target.ref != self.target_ref:
            raise StoreUsageError('target ref must describe actual target bytes')


@dataclass(frozen=True, init=False)
class StoreAck:
    """원 제안의 파일 내구 완료 진술. 현재 파일·역사적 CAS·자원 생성 허가는 아니다."""

    proposal: StoreProposal
    method: str
    request_id: str
    lock_nonce: str
    expected_ref: ManifestRef | None
    target_ref: ManifestRef

    def __init__(self, proposal, method):
        _exact(proposal, StoreProposal)
        if type(method) is not str or method not in ('REPLACED', 'RECONFIRMED'):
            raise StoreUsageError('unsupported ACK method')
        original = proposal.expected.outcome.manifest
        for name, value in (
            ('proposal', proposal), ('method', method), ('request_id', proposal.request_id),
            ('lock_nonce', proposal.lock_nonce), ('expected_ref', None if original is None else original.ref),
            ('target_ref', proposal.target_ref),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class StoreResult:
    kind: str
    ack: StoreAck | None = None
    reason: str | None = None

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in ('DURABLE', 'CAS_MISMATCH', 'UNCONFIRMED'):
            raise StoreUsageError('unsupported result kind')
        if self.ack is not None:
            _exact(self.ack, StoreAck)
        if (self.kind == 'DURABLE') != (self.ack is not None):
            raise StoreUsageError('only DURABLE carries an ACK')
        if self.reason is not None and (type(self.reason) is not str or len(self.reason) > 96):
            raise StoreUsageError('invalid bounded reason')


def _identity(value):
    return value.st_dev, value.st_ino


def _fingerprint(value):
    # atime은 자기 read로도 바뀔 수 있으므로 비교하지 않는다.
    return (_identity(value), value.st_size, value.st_mtime_ns, value.st_ctime_ns,
            value.st_mode, value.st_uid, value.st_gid, value.st_nlink)


class ResourceStore:
    """생성자에서 lock을 취득한다. 같은 PID·소유 Thread의 비재진입 동기 호출만 지원한다.

    상위 경로와 동일 euid의 writer가 협력하는 배포 조건이 필요하다. 이 adapter는
    privileged 비협조 writer를 제압하는 보안 경계나 fork/fd 양도 framework가 아니다.
    """

    DATA_NAME = 'resource-set.json'
    LOCK_NAME = 'resource-set.lock'

    def __init__(self, directory):
        _exact(directory, str)
        if not directory or '\x00' in directory or not os.path.isabs(directory):
            raise StoreUsageError('directory must be an absolute native string')
        if sys.platform != 'linux':
            raise StoreError('ResourceStore supports Linux only')
        if ((directory != '/' and directory.endswith('/'))
                or any(part in ('.', '..') for part in directory.split('/'))):
            raise StoreUsageError('directory must have a distinct final path component')
        import fcntl

        self._fcntl = fcntl
        self._directory = directory
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._euid = os.geteuid()
        self._dir_fd = None
        self._lock_fd = None
        self._dir_identity = None
        self._lock_identity = None
        self._nonce = None
        self._closed = True
        self._active = False
        self._entered_context = False
        self._latest = None
        self._pending = None
        self._ack = None
        self._replace_entered = False
        self._replace_observed = False
        self._temp_name = None
        self._temp_identity = None
        try:
            before = os.stat(directory, follow_symlinks=False)
            self._check_directory(before)
            self._dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            actual = os.fstat(self._dir_fd)
            self._check_directory(actual)
            if _identity(before) != _identity(actual):
                raise StoreError('directory changed during open')
            self._dir_identity = _identity(actual)
            self._open_lock()
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise StoreBusyError('resource lock is busy') from error
                raise
            self._validate_namespace()
            self._nonce = str(uuid.uuid4())
            self._closed = False
        except BaseException as error:
            self._close_descriptors()
            if isinstance(error, StoreError):
                raise
            if isinstance(error, OSError):
                raise StoreError('cannot open trusted resource store') from error
            raise

    @property
    def lock_nonce(self):
        """원 lock 수명의 진단 식별자. 문자열 자체는 살아 있는 lock의 증거가 아니다."""
        return self._nonce

    @property
    def directory(self):
        """생성 때 고정한 보호 경로. 실제 owner/latest/namespace 확인은 propose가 수행한다."""
        return self._directory

    def _check_directory(self, value):
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != self._euid or value.st_mode & 0o022:
            raise StoreError('untrusted directory type, owner or mode')

    def _check_file(self, value):
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != self._euid
                or value.st_nlink != 1 or stat.S_IMODE(value.st_mode) != 0o600):
            raise StoreError('unsafe regular file type, owner, mode or links')

    def _stat(self, name):
        return os.stat(name, dir_fd=self._dir_fd, follow_symlinks=False)

    def _open_lock(self):
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            before = self._stat(self.LOCK_NAME)
        except FileNotFoundError:
            before = None
        if before is not None:
            self._check_file(before)
        created = False
        if before is None:
            try:
                self._lock_fd = os.open(self.LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self._dir_fd)
                created = True
            except FileExistsError:
                before = self._stat(self.LOCK_NAME)
                self._check_file(before)
        if self._lock_fd is None:
            self._lock_fd = os.open(self.LOCK_NAME, flags, dir_fd=self._dir_fd)
        if created:
            os.fchmod(self._lock_fd, 0o600)
        actual = os.fstat(self._lock_fd)
        self._check_file(actual)
        if before is not None and _identity(before) != _identity(actual):
            raise StoreError('lock changed during open')
        current = self._stat(self.LOCK_NAME)
        self._check_file(current)
        if _identity(current) != _identity(actual):
            raise StoreError('lock name does not identify held descriptor')
        self._lock_identity = _identity(actual)

    def _validate_namespace(self):
        try:
            directory = os.fstat(self._dir_fd)
            named_directory = os.stat(self._directory, follow_symlinks=False)
            self._check_directory(directory)
            self._check_directory(named_directory)
            if _identity(directory) != self._dir_identity or _identity(named_directory) != self._dir_identity:
                raise StoreError('anchored directory identity changed')
            held = os.fstat(self._lock_fd)
            named = self._stat(self.LOCK_NAME)
            self._check_file(held)
            self._check_file(named)
            if _identity(held) != self._lock_identity or _identity(named) != self._lock_identity:
                raise StoreError('stable lock name or inode changed')
        except OSError as error:
            raise StoreError('resource namespace cannot be confirmed') from error

    def _check_owner(self):
        if os.getpid() != self._pid:
            raise StoreUsageError('forked process cannot use inherited store')
        if threading.current_thread() is not self._thread:
            raise StoreUsageError('only the owning Thread may use store')
        if self._closed:
            raise StoreUsageError('store is closed')
        if self._active:
            raise StoreUsageError('store operation is not reentrant')

    @contextmanager
    def _operation(self):
        self._check_owner()
        self._active = True
        try:
            self._validate_namespace()
            yield
        finally:
            self._active = False

    def __enter__(self):
        with self._operation():
            if self._entered_context:
                raise StoreUsageError('store context is not reentrant')
            self._entered_context = True
            return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        self._entered_context = False

    def __copy__(self):
        raise StoreUsageError('store copying is unsupported')

    def __deepcopy__(self, memo):
        raise StoreUsageError('store copying is unsupported')

    def __reduce__(self):
        raise StoreUsageError('store transfer is unsupported')

    def _close_descriptors(self):
        error = None
        for attribute in ('_lock_fd', '_dir_fd'):
            descriptor = getattr(self, attribute)
            setattr(self, attribute, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as caught:
                    error = caught
        return error

    def close(self):
        """owner만 해제한다. fork 자식은 LOCK_UN 없이 자기 FD 복사만 닫고 사용 오류를 낸다."""
        if os.getpid() != self._pid:
            self._closed = True
            self._close_descriptors()
            raise StoreUsageError('inherited store was detached without unlocking parent')
        if threading.current_thread() is not self._thread or self._active:
            raise StoreUsageError('close requires idle owning Thread')
        if self._closed:
            return
        self._closed = True
        self._active = True
        error = None
        cleanup_ok = False
        close_error = None
        try:
            cleanup_ok = self._cleanup_temp()
        finally:
            try:
                self._fcntl.flock(self._lock_fd, self._fcntl.LOCK_UN)
            except OSError as caught:
                error = caught
            finally:
                close_error = self._close_descriptors()
                self._latest = self._pending = self._ack = None
                self._active = False
        if error is not None or close_error is not None or not cleanup_ok:
            raise StoreError('store close or owned temp cleanup was not confirmed') from (error or close_error)

    @staticmethod
    def _outcome(kind, manifest=None):
        return LoadOutcome(str(uuid.uuid4()), kind, manifest)

    def _read_current(self, *, keep_fd=False):
        """고정 basename을 새로 연다. 최초 leaf 조회 ENOENT만 MISSING이며 후속 소멸은 IO_ERROR다."""
        try:
            before = self._stat(self.DATA_NAME)
        except FileNotFoundError:
            return self._outcome('MISSING'), None
        except OSError:
            return self._outcome('IO_ERROR'), None
        descriptor = None
        retained = None
        outcome = None
        try:
            self._check_file(before)
            descriptor = os.open(self.DATA_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=self._dir_fd)
            initial = os.fstat(descriptor)
            self._check_file(initial)
            if _fingerprint(before) != _fingerprint(initial):
                raise StoreError('data changed during open')
            raw = bytearray()
            while len(raw) < 8193:
                chunk = os.read(descriptor, 8193 - len(raw))
                if type(chunk) is not bytes:
                    raise StoreError('read did not return native bytes')
                if not chunk:
                    break
                if len(chunk) > 8193 - len(raw):
                    raise StoreError('read exceeded requested bound')
                raw.extend(chunk)
            after = os.fstat(descriptor)
            named = self._stat(self.DATA_NAME)
            self._check_file(after)
            self._check_file(named)
            if _fingerprint(initial) != _fingerprint(after) or _fingerprint(after) != _fingerprint(named):
                raise StoreError('data changed during bounded read')
            if len(raw) <= 8192 and len(raw) != after.st_size:
                raise StoreError('data size and completed read disagree')
            try:
                parsed = ResourceManifest(bytes(raw))
            except ManifestError:
                outcome = self._outcome('CORRUPT')
            else:
                outcome = self._outcome('VALID', parsed)
                if keep_fd:
                    retained, descriptor = descriptor, None
        except (OSError, StoreError):
            outcome = self._outcome('IO_ERROR')
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    outcome = self._outcome('IO_ERROR')
        return outcome, retained

    def load(self):
        """최신 실제 receipt를 발급한다. 이것만으로 CLEAN·설치·생성 권한을 얻지 않는다."""
        with self._operation():
            outcome, _ = self._read_current()
            self._validate_namespace()
            receipt = StoreLoad(self._nonce, outcome)
            self._latest = receipt
            return receipt

    def propose(self, receipt, manifest, request_id):
        """실제 최신 receipt와 명시 target을 고정한다. counter/revision을 자동 변경하지 않는다."""
        _exact(receipt, StoreLoad)
        _exact(manifest, ResourceManifest)
        _identifier(request_id)
        with self._operation():
            if receipt is not self._latest or receipt.lock_nonce != self._nonce:
                raise StoreUsageError('propose requires latest original load receipt')
            if receipt.outcome.kind not in ('VALID', 'MISSING'):
                raise StoreUsageError('unreadable predecessor cannot be overwritten')
            if self._pending is not None:
                if self._ack is None:
                    raise StoreUsageError('unresolved proposal already exists')
                if request_id == self._pending.request_id:
                    raise StoreUsageError('completed request cannot acquire new source or target')
            target = ResourceManifest(manifest.canonical_bytes)
            original = receipt.outcome.manifest
            if original is not None and original.raw_bytes == target.raw_bytes:
                raise StoreUsageError('new no-op proposal is unsupported')
            proposal = StoreProposal(request_id, self._nonce, receipt, manifest, manifest.raw_sha256,
                                     target.raw_bytes, target.ref)
            self._pending = proposal
            self._ack = None
            self._replace_entered = self._replace_observed = False
            return proposal

    def _matches_expected(self, outcome, proposal):
        expected = proposal.expected.outcome
        if expected.kind != outcome.kind:
            return False
        if expected.kind == 'MISSING':
            return True
        return (expected.manifest.ref == outcome.manifest.ref
                and expected.manifest.raw_bytes == outcome.manifest.raw_bytes)

    def _durable(self, proposal, method):
        self._ack = StoreAck(proposal, method)
        self._latest = None
        return StoreResult('DURABLE', self._ack)

    def _confirm_target(self, proposal):
        # 같은 길이의 덮어쓰기는 파일시스템 시각 해상도만으로 구분할 수 없다.
        current, _ = self._read_current()
        if (current.kind != 'VALID' or current.manifest.raw_bytes != proposal.target_bytes
                or current.manifest.ref != proposal.target_ref):
            raise StoreError('actual canonical target cannot be confirmed')

    def _clear_temp(self):
        self._temp_name = self._temp_identity = None

    def _cleanup_temp(self):
        if self._temp_name is None:
            return True
        try:
            current = self._stat(self._temp_name)
            if self._temp_identity is None:
                return False
            if _identity(current) != self._temp_identity:
                self._clear_temp()
                return True
            self._check_file(current)
            os.unlink(self._temp_name, dir_fd=self._dir_fd)
        except FileNotFoundError:
            self._clear_temp()
            return True
        except (OSError, StoreError):
            return False
        self._clear_temp()
        return True

    def _replace(self, proposal):
        if not self._cleanup_temp():
            return StoreResult('UNCONFIRMED', reason='OWN_TEMP_UNRESOLVED')
        descriptor = None
        kind, reason, completed = 'UNCONFIRMED', 'PUBLISH_IO_ERROR', False
        self._temp_name = '.resource-set.' + str(uuid.uuid4()) + '.tmp'
        try:
            try:
                descriptor = os.open(self._temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                     | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=self._dir_fd)
            except FileExistsError:
                self._clear_temp()
                raise
            self._temp_identity = _identity(os.fstat(descriptor))
            os.fchmod(descriptor, 0o600)
            self._check_file(os.fstat(descriptor))
            position = 0
            while position < len(proposal.target_bytes):
                count = os.write(descriptor, proposal.target_bytes[position:])
                if type(count) is not int or count <= 0 or count > len(proposal.target_bytes) - position:
                    raise OSError(errno.EIO, 'write made invalid or zero progress')
                position += count
            os.fsync(descriptor)
            current, _ = self._read_current()
            if not self._matches_expected(current, proposal):
                kind = 'UNCONFIRMED' if current.kind == 'IO_ERROR' else 'CAS_MISMATCH'
                reason = 'PREDECESSOR_CHANGED_BEFORE_REPLACE'
            else:
                self._validate_namespace()
                named = self._stat(self._temp_name)
                self._check_file(named)
                if _identity(named) != self._temp_identity or _identity(os.fstat(descriptor)) != self._temp_identity:
                    raise StoreError('owned temp name changed before replace')
                self._replace_entered = True
                os.replace(self._temp_name, self.DATA_NAME, src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
                self._replace_observed = True
                self._clear_temp()
                published = os.fstat(descriptor)
                self._check_file(published)
                if _fingerprint(self._stat(self.DATA_NAME)) != _fingerprint(published):
                    raise StoreError('published name does not identify original target')
                os.fsync(self._dir_fd)
                self._validate_namespace()
                if (_fingerprint(os.fstat(descriptor)) != _fingerprint(published)
                        or _fingerprint(self._stat(self.DATA_NAME)) != _fingerprint(published)):
                    raise StoreError('published target changed during directory sync')
                self._confirm_target(proposal)
                completed = True
        except (OSError, StoreError):
            pass
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    completed = False
                    reason = 'TEMP_CLOSE_UNCONFIRMED'
            if not self._cleanup_temp():
                completed = False
                reason = 'OWN_TEMP_UNRESOLVED'
        if completed:
            return self._durable(proposal, 'REPLACED')
        return StoreResult(kind, reason=reason)

    def _reconfirm(self, proposal, descriptor):
        completed = False
        try:
            before = os.fstat(descriptor)
            os.fsync(descriptor)
            self._check_file(self._stat(self.DATA_NAME))
            if _fingerprint(os.fstat(descriptor)) != _fingerprint(before) or _fingerprint(self._stat(self.DATA_NAME)) != _fingerprint(before):
                raise StoreError('target changed during reconfirmation')
            self._validate_namespace()
            os.fsync(self._dir_fd)
            self._validate_namespace()
            if _fingerprint(self._stat(self.DATA_NAME)) != _fingerprint(before):
                raise StoreError('target changed after directory sync')
            self._confirm_target(proposal)
            completed = True
        except (OSError, StoreError):
            pass
        finally:
            try:
                os.close(descriptor)
            except OSError:
                completed = False
        if completed:
            return self._durable(proposal, 'RECONFIRMED')
        return StoreResult('UNCONFIRMED', reason='RECONFIRM_IO_ERROR')

    def _publish(self, proposal):
        _exact(proposal, StoreProposal)
        with self._operation():
            if proposal is not self._pending or proposal.lock_nonce != self._nonce:
                raise StoreUsageError('publish requires exact original pending proposal')
            if self._ack is not None:
                return StoreResult('DURABLE', self._ack)
            current, descriptor = self._read_current(keep_fd=True)
            if (current.kind == 'VALID' and current.manifest.raw_bytes == proposal.target_bytes
                    and current.manifest.ref == proposal.target_ref and self._replace_entered):
                return self._reconfirm(proposal, descriptor)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    return StoreResult('UNCONFIRMED', reason='CURRENT_CLOSE_UNCONFIRMED')
            if self._matches_expected(current, proposal):
                if self._replace_observed:
                    return StoreResult('CAS_MISMATCH', reason='OBSERVED_TARGET_REVERTED_TO_BASE')
                return self._replace(proposal)
            if current.kind == 'IO_ERROR':
                return StoreResult('UNCONFIRMED', reason='CURRENT_IO_ERROR')
            return StoreResult('CAS_MISMATCH', reason='CURRENT_RAW_DOES_NOT_MATCH_EXPECTED')

    def publish(self, proposal):
        """원 pending을 출판한다. DURABLE 재호출은 원 ACK 사실만 반환하며 파일을 재보증하지 않는다."""
        return self._publish(proposal)

    def reconcile(self, proposal):
        """같은 lock/원 제안의 불명 완료만 수습한다. 다른 guard의 과거 CAS를 재구성하지 않는다."""
        return self._publish(proposal)
