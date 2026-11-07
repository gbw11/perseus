"""S-log 한 스레드의 실제 store/observer 소유와 유한 비동기 요청·완료 통로.

PREPARE callback은 로컬 release 확인, BOOTSTRAP callback(store,batch)은 명시 설치
증거를 처리한다. WRITE는 fresh 원 receipt/ref CAS와 durable ACK 뒤 실제 post-load를
반환한다. 늦은 완료도 역사적 사실로 보존하며 timely()가 현재 사용 허가를 대신하지 않는다.
"""

from dataclasses import dataclass
import os
import queue
import sys
import threading
import time
import uuid

from .process_observation import ProcessObserver
from .procfs_formats import parse_proc_stat
from .resource_manifest import ManifestRef, OwnerRecord, ResourceManifest
from .resource_name_observation import ResourceNameObserver
from .resource_store import ResourceStore, StoreAck, StoreLoad, StoreResult


OPERATIONS = frozenset(('PREPARE', 'OBSERVE', 'NAMES', 'BOOTSTRAP', 'WRITE',
                        'CREATE', 'HANDLES', 'CLEANUP', 'BIRTHS'))


class ResourceLogError(RuntimeError):
    """S-log 시작·종료·원 요청 처리가 확인되지 않았다."""


def _now():
    return time.monotonic_ns() // 1000


def _uuid(value):
    if type(value) is not str:
        raise ValueError('expected canonical UUID')
    if str(uuid.UUID(value)) != value:
        raise ValueError('expected canonical UUID')


def _time(value):
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError('expected exact u64 time')


@dataclass(frozen=True)
class ResourceLogRequest:
    request_id: str
    operation: str
    issued_us: int
    expires_us: int
    manifest: ResourceManifest | None = None
    expected_ref: ManifestRef | None = None
    intent_ack: StoreAck | None = None
    birth_pids: tuple = ()

    def __post_init__(self):
        _uuid(self.request_id)
        _time(self.issued_us)
        _time(self.expires_us)
        if (type(self.operation) is not str or self.operation not in OPERATIONS
                or self.issued_us >= self.expires_us):
            raise ValueError('unsupported operation or empty original lifetime')
        if self.operation == 'WRITE':
            if type(self.manifest) is not ResourceManifest:
                raise ValueError('WRITE requires an immutable manifest')
            if self.expected_ref is not None and type(self.expected_ref) is not ManifestRef:
                raise ValueError('expected actual original manifest ref or MISSING null')
        elif self.manifest is not None or self.expected_ref is not None:
            raise ValueError('only WRITE accepts source ref and manifest')
        if self.operation in ('CREATE', 'HANDLES'):
            if type(self.intent_ack) is not StoreAck:
                raise ValueError('native creation/handles require original StoreAck')
        elif self.intent_ack is not None:
            raise ValueError('only CREATE/HANDLES accept a native ACK')
        if type(self.birth_pids) is not tuple or len(self.birth_pids) > 3:
            raise ValueError('bounded immutable A/M/D birth targets are required')
        if self.operation != 'BIRTHS' and self.birth_pids:
            raise ValueError('only BIRTHS accepts spawned PID targets')
        roles, pids = set(), set()
        for item in self.birth_pids:
            if (type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                    or item[0] not in ('A', 'M', 'D') or type(item[1]) is not int
                    or not 1 <= item[1] <= 2**32 - 1 or item[0] in roles or item[1] in pids):
                raise ValueError('spawn birth targets differ from exact unique A/M/D PIDs')
            roles.add(item[0])
            pids.add(item[1])


@dataclass(frozen=True)
class ResourceLogCompletion:
    request_id: str
    operation: str
    issued_us: int
    expires_us: int
    completed_us: int
    value: object = None
    error: str | None = None
    ack: object = None
    receipt: object = None
    store_result: object = None
    owner_births: tuple = ()

    def __post_init__(self):
        _uuid(self.request_id)
        for value in (self.issued_us, self.expires_us, self.completed_us):
            _time(value)
        if (type(self.operation) is not str or self.operation not in OPERATIONS
                or self.issued_us >= self.expires_us
                or self.error is not None and (type(self.error) is not str or len(self.error) > 400)):
            raise ValueError('invalid bounded original completion identity')
        for value, expected in ((self.ack, StoreAck), (self.receipt, StoreLoad),
                                (self.store_result, StoreResult)):
            if value is not None and type(value) is not expected:
                raise ValueError('completion requires original typed receipt/ACK/result')
        if (type(self.owner_births) is not tuple or len(self.owner_births) > 4
                or any(type(value) is not OwnerRecord for value in self.owner_births)
                or len({value.role for value in self.owner_births}) != len(self.owner_births)):
            raise ValueError('completion birth inventory is not an exact bounded tuple')

    @property
    def late(self):
        return self.completed_us >= self.expires_us

    def timely(self, now_us):
        _time(now_us)
        return (self.error is None and self.issued_us <= self.completed_us < self.expires_us
                and self.completed_us <= now_us < self.expires_us)


class AsyncResourceLog:
    """queue1/completion1. 실행 중·미회수 완료가 있으면 submit은 False다.

    모든 store/observer 호출과 callback은 원 S-log Thread에서 수행한다. WRITE의
    UNCONFIRMED는 같은 request 원문만 재확인할 수 있으며 새 제안으로 덮지 않는다.
    close(timeout)는 syscall을 중단하지 못하므로 timeout이면 명시 오류를 반환한다.
    """

    def __init__(self, directory, service_instance, *, prepare=None, bootstrap=None, native=None):
        if type(directory) is not str:
            raise ValueError('directory must be an exact absolute string')
        _uuid(service_instance)
        for callback in (prepare, bootstrap):
            if callback is not None and not callable(callback):
                raise ValueError('callbacks must be callable or null')
        self._directory = directory
        self._service = service_instance
        self._prepare = prepare
        self._bootstrap = bootstrap
        self._native = native
        self._requests = queue.Queue(maxsize=1)
        self._completed = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._busy = False
        self._started = False
        self._closed = False
        self._shutdown_error = None
        self._terminal_error = None
        self._thread = threading.Thread(target=self._run, name='S-log', daemon=False)

    def start(self):
        with self._state_lock:
            if self._started or self._closed:
                raise ResourceLogError('S-log cannot be restarted')
            self._started = True
            self._thread.start()
        return self

    def submit(self, request_id, operation, *, issued_us, expires_us, manifest=None, expected_ref=None,
               intent_ack=None, birth_pids=()):
        request = ResourceLogRequest(request_id, operation, issued_us, expires_us, manifest,
                                     expected_ref, intent_ack, birth_pids)
        with self._state_lock:
            if not self._started or self._closed:
                raise ResourceLogError('S-log is not active')
            if self._busy:
                return False
            self._busy = True
            self._requests.put_nowait(request)
            return True

    def poll(self):
        try:
            completed = self._completed.get_nowait()
        except queue.Empty:
            return None
        with self._state_lock:
            self._busy = False
        return completed

    def close(self, timeout=5.0):
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 <= timeout <= 30:
            raise ValueError('close timeout must be bounded seconds')
        self.request_stop()
        started = self._started
        if started:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise ResourceLogError('S-log did not exit within the caller stop budget')
        if self._shutdown_error is not None:
            raise ResourceLogError('S-log store close was not confirmed') from self._shutdown_error

    def request_stop(self):
        """S-main이 selector를 계속 돌며 실제 S-log 종료를 관측하는 비차단 요청."""
        with self._state_lock:
            self._closed = True
            self._stop.set()

    @property
    def is_alive(self):
        return self._thread.is_alive()

    @property
    def terminal_error(self):
        error = self._terminal_error or self._shutdown_error
        return None if error is None else type(error).__name__ + ': ' + str(error)[:320]

    def _births(self, store, targets):
        """같은 실제 runtime의 두 관측 사이에서 원 PID/startticks를 다시 읽는다."""
        from multiprocessing import resource_tracker
        if sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 11, 18):
            raise ResourceLogError('tracker inventory requires pinned CPython3.11.18')
        tracker_pid = resource_tracker._resource_tracker._pid
        if type(tracker_pid) is not int or tracker_pid <= 0:
            raise ResourceLogError('actual resource tracker birth is unavailable')
        targets = targets + (('TRACKER', tracker_pid),)
        before = ProcessObserver(store, self._service).observe()
        if (before.latest_load.outcome.kind != 'VALID'
                or before.latest_load.outcome.manifest.runtime != before.guard.runtime):
            raise ResourceLogError('birth observation requires current actual manifest runtime')

        def read(role, pid):
            fd = os.open('/proc/' + str(pid) + '/stat',
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                raw = bytearray()
                while len(raw) <= 4096:
                    piece = os.read(fd, 4097 - len(raw))
                    if not piece:
                        break
                    raw.extend(piece)
                parsed = parse_proc_stat(bytes(raw), pid)
                return OwnerRecord(role, 'CURRENT', pid, parsed.start_ticks)
            finally:
                os.close(fd)

        births = tuple(read(role, pid) for role, pid in targets)
        after = ProcessObserver(store, self._service).observe()
        if (after.guard != before.guard
                or after.original_load.outcome.manifest.ref != before.original_load.outcome.manifest.ref
                or tuple(read(role, pid) for role, pid in targets) != births):
            raise ResourceLogError('process birth or actual source changed during observation')
        return births, after.latest_load

    def _run(self):
        store = None
        startup_error = None
        pending_write = None
        last_request = None
        last_completion = None
        blocked_error = None
        try:
            try:
                store = ResourceStore(self._directory)
            except Exception as error:
                startup_error = error
            while not self._stop.is_set() or not self._requests.empty():
                try:
                    request = self._requests.get(timeout=0.05)
                except queue.Empty:
                    continue
                value = ack = receipt = store_result = None
                owner_births = ()
                error_text = None
                try:
                    if self._stop.is_set():
                        raise ResourceLogError('S-log stop canceled queued work before execution')
                    if last_request is not None and request.request_id == last_request.request_id:
                        if request != last_request:
                            raise ResourceLogError('same request ID changed original fields')
                        if last_completion is not None and pending_write is None:
                            # 원 완료 시각/ACK를 보존한다. 재조회나 새 작업으로 만들지 않는다.
                            self._completed.put_nowait(last_completion)
                            continue
                    if not request.issued_us <= _now() < request.expires_us:
                        raise ResourceLogError('original request lifetime elapsed before execution')
                    if startup_error is not None and request.operation != 'CLEANUP':
                        raise ResourceLogError('actual ResourceStore could not start') from startup_error
                    if blocked_error is not None and request.operation != 'CLEANUP':
                        raise ResourceLogError('bootstrap failed; original store must remain fenced') from blocked_error
                    if request.operation == 'CLEANUP':
                        # 실제 child reap은 S-main 책임이다. 정리는 미확정 출판을 승인/삭제하지 않는다.
                        if self._native is None:
                            raise ResourceLogError('native owner is not configured')
                        value = self._native.close_after_children()
                    elif pending_write is not None:
                        original_request, proposal, original_value = pending_write
                        if request != original_request:
                            raise ResourceLogError('unconfirmed publication requires identical original request')
                        value = original_value
                        store_result = store.reconcile(proposal)
                    elif request.operation == 'PREPARE':
                        if self._prepare is None:
                            raise ResourceLogError('PREPARE callback is not configured')
                        value = self._prepare()
                    elif request.operation == 'OBSERVE':
                        value = ProcessObserver(store, self._service).observe()
                        receipt = value.latest_load
                    elif request.operation == 'NAMES':
                        value = ResourceNameObserver(store, self._service).observe()
                        receipt = value.latest_load
                    elif request.operation == 'BOOTSTRAP':
                        if self._bootstrap is None:
                            raise ResourceLogError('BOOTSTRAP callback is not configured')
                        batch = ProcessObserver(store, self._service).observe()
                        value = self._bootstrap(store, batch)
                        proposal = value.proposal
                        if proposal.request_id != request.request_id:
                            raise ResourceLogError('bootstrap proposal differs from original request ID')
                        pending_write = (request, proposal, value)
                        if not request.issued_us <= _now() < request.expires_us:
                            raise ResourceLogError('bootstrap consumed marker after original deadline')
                        store_result = store.publish(proposal)
                    elif request.operation in ('CREATE', 'HANDLES'):
                        if self._native is None:
                            raise ResourceLogError('native owner is not configured')
                        receipt = store.load()
                        if (receipt.outcome.kind != 'VALID'
                                or receipt.outcome.manifest.ref != request.intent_ack.target_ref):
                            raise ResourceLogError('native ACK is historical rather than current source')
                        if not request.issued_us <= _now() < request.expires_us:
                            raise ResourceLogError('native source check exceeded original deadline')
                        if request.operation == 'CREATE':
                            value = self._native.create_one(request.intent_ack)
                            owner_births, receipt = self._births(store, ())
                        else:
                            value = self._native.spawn_handles(request.intent_ack)
                    elif request.operation == 'BIRTHS':
                        owner_births, receipt = self._births(store, request.birth_pids)
                        value = owner_births
                    else:
                        original = store.load()
                        original_ref = (None if original.outcome.manifest is None
                                        else original.outcome.manifest.ref)
                        if original_ref != request.expected_ref:
                            raise ResourceLogError('WRITE original raw ref changed')
                        proposal = store.propose(original, request.manifest, request.request_id)
                        pending_write = (request, proposal, None)
                        if not request.issued_us <= _now() < request.expires_us:
                            raise ResourceLogError('WRITE source checking exceeded original deadline')
                        store_result = store.publish(proposal)
                    if store_result is not None:
                        if request.operation == 'WRITE':
                            value = store_result
                        ack = store_result.ack
                        if store_result.kind == 'DURABLE':
                            pending_write = None
                            receipt = store.load()
                            if (receipt.outcome.kind != 'VALID'
                                    or receipt.outcome.manifest.ref != ack.target_ref):
                                raise ResourceLogError('durable target changed before actual post-load')
                except Exception as error:
                    error_text = type(error).__name__ + ': ' + str(error)[:320]
                    if request.operation == 'BOOTSTRAP' and pending_write is None and ack is None:
                        blocked_error = error
                completed = ResourceLogCompletion(request.request_id, request.operation,
                                                  request.issued_us, request.expires_us, _now(),
                                                  value, error_text, ack, receipt, store_result, owner_births)
                last_request, last_completion = request, completed
                self._completed.put_nowait(completed)
        except BaseException as error:
            self._terminal_error = error
        finally:
            if store is not None:
                try:
                    store.close()
                except Exception as error:
                    self._shutdown_error = error
