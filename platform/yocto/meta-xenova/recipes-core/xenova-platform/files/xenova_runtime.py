"""조기 서비스와 CLI의 잠금·원자적 메타데이터 쓰기. /run은 부팅 동안 유지한다."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import sys
import time
from xenova_trace import span

RUN = Path('/run/xenova')
OBSERVATION_SLOTS = 4  # 초기 지원 상한. 보드 부하/QPS 수락과 별도로 검증한다.


class PlatformError(RuntimeError):
    """준비·계약·생명주기 오류."""


class OperationBusy(PlatformError):
    """공통 작업 잠금의 미수락 또는 종료 drain 시간 초과."""


class _OperationLock:
    """실제 flock 보유 구간만 trace한다. 대상 소유자를 가진 lifecycle만 suspend한다."""
    def __init__(self, fd):
        self.fd, self.scope = fd, None

    def begin(self):
        self.scope = span('lock.hold')
        self.scope.__enter__()

    def end(self, *error):
        if self.scope is not None:
            scope, self.scope = self.scope, None
            scope.__exit__(*(error or (None,None,None)))

    @contextmanager
    def outside(self, platform):
        self.end()
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        try:
            with span('lock.released.lifecycle'):
                yield
        finally:
            end = platform.clock()+15
            with span('lock.wait', blocking=True):
                while True:
                    try:
                        fcntl.flock(self.fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if platform.clock() >= end:
                            raise OperationBusy('another platform operation is in progress; lifecycle result requires replay')
                        platform.sleep(min(.02, max(0, end-platform.clock())))
            self.begin()


@contextmanager
def observation_slot(runtime, *, deadline, clock=time.monotonic):
    """읽기 전용 외부 관측만 제한한다. 변경/원 작업 조회는 이 슬롯을 기다리지 않는다.

    파일은 삭제하지 않는다. 호출자는 FD를 소유 helper에도 전달해 부모 소멸을 슬롯 회수로
    오인하지 않는다. 신규 대기열/재시도는 없고 같은 runtime의 CLI/프로세스도 참여한다.
    """
    directory=ensure_runtime(runtime)/'locks'
    directory.mkdir(mode=0o700,exist_ok=True)
    if directory.is_symlink() or directory.resolve()!=directory:
        raise PlatformError('invalid observation slot directory')
    held=None
    try:
        for number in range(OBSERVATION_SLOTS):
            if clock()>=deadline:
                from xenova_execution import refuse
                refuse('DEADLINE_EXCEEDED','observation admission deadline expired')
            fd=os.open(directory/('observation-slot-'+str(number)+'.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                info=os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid()
                        or stat.S_IMODE(info.st_mode)!=0o600):
                    raise PlatformError('invalid observation slot file')
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            held=fd
            break
        if held is None:
            raise OperationBusy('another observation operation is in progress; bounded capacity exhausted')
        if clock()>=deadline:
            from xenova_execution import refuse
            refuse('DEADLINE_EXCEEDED','observation admitted after deadline')
        yield held
    finally:
        if held is not None:os.close(held)


def ensure_runtime(runtime=RUN):
    runtime = Path(runtime)
    runtime.mkdir(mode=0o755, parents=True, exist_ok=True)
    if runtime.is_symlink() or runtime.resolve() != runtime:
        raise PlatformError('runtime directory must be canonical and unlinked')
    return runtime


@contextmanager
def operation_lock(runtime=RUN, *, blocking=False, deadline=None, clock=time.monotonic, sleep=time.sleep):
    """물리 변경·공유 예약의 단일 잠금. 조회는 별도 정합성을 확인한다. unlock 때 파일을 지우지 않는다."""
    directory = ensure_runtime(runtime) / 'locks'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise PlatformError('invalid platform lock directory')
    fd = os.open(directory / 'platform.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise PlatformError('platform lock must be an unshared regular file')
        with span('lock.wait', blocking=blocking):
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking and deadline is None else fcntl.LOCK_NB))
                    break
                except BlockingIOError as exc:
                    if deadline is None or clock() >= deadline:
                        raise OperationBusy('another platform operation is in progress; retry after it completes') from exc
                    sleep(min(0.1, max(0, deadline-clock())))
        held = _OperationLock(fd)
        held.begin()
        try:
            yield held
        finally:
            held.end(*sys.exc_info())
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with span('storage.directory_fsync', required=True):
            os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, content, mode=0o600):
    """같은 부모의 새 파일을 fsync·rename·부모 fsync 순서로 게시한다."""
    path = Path(path)
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + os.urandom(6).hex())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with span('storage.atomic_write', required=True):
            with os.fdopen(fd, 'w') as stream:
                with span('storage.file_write', required=True):
                    stream.write(content)
                    stream.flush()
                    os.fchmod(stream.fileno(), mode)
                with span('storage.file_fsync', required=True):
                    os.fsync(stream.fileno())
            with span('storage.replace', required=True):
                os.replace(temp, path)
            sync_directory(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


def boot_progress(kind, runtime=RUN, **context):
    """선택적 표시 모듈의 부재·고장도 관리 작업에 전파하지 않는 생산자 경계."""
    try:
        from xenova_boot_progress import Progress
        writer = Progress(kind, runtime, **context)
    except Exception:
        writer = None
    def emit(phase, **facts):
        if writer is not None:
            try:
                return writer.emit(phase, **facts)
            except Exception:
                pass
        return False
    return emit
