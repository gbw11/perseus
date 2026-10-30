"""Linux 준비의 단일 I/O 슬롯·원 실행 소유권·요청별 제한 출력.

새 작업 원장이나 실행기는 아니다. 실행 허가는 Manager의 기존 operation/gate가 결정한다.
슬롯과 원 실행 잠금은 별개이며, 자식에 두 FD를 전달해 부모 종료를 자원 소멸로 오인하지 않는다.
"""
import builtins
from _thread import _local
from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import tempfile
import time

from xenova_runtime import PlatformError, OperationBusy, ensure_runtime, sync_directory

_OUTPUT = _local()
LIMIT = 64 * 1024


def _current():
    return getattr(_OUTPUT,'value',None)


class RequestOutput:
    def __init__(self):
        self.content = bytearray()
        self.dropped = 0
        self.stack = ExitStack()
        self.fds = ()

    def write(self, value):
        value = value.encode('utf-8',errors='replace') if isinstance(value,str) else value
        count = min(len(value), LIMIT-len(self.content))
        self.content.extend(value[:count])
        self.dropped += len(value)-count


@contextmanager
def request_output():
    """FD/stdout/cwd/env는 변경하지 않는다. 중첩 호출도 같은 요청의 제한된 sink를 쓴다."""
    current = _current()
    if current is not None:
        yield current
        return
    current = RequestOutput()
    _OUTPUT.value=current
    try:
        with current.stack:
            yield current
    finally:
        _OUTPUT.value=None


def diagnostic(*values, sep=' ', end='\n', file=None, flush=False):
    current = _current()
    if current is None:
        return builtins.print(*values,sep=sep,end=end,file=file,flush=flush)
    current.write(sep.join(str(value) for value in values)+end)


def managed_preparation():
    current=_current()
    return current is not None and len(current.fds)==2


def run(args, *, capture=False, **kwargs):
    """파이프를 끝까지 비우되 메모리는 제한한다. 데이터 출력 잘림은 정상값으로 반환하지 않는다."""
    current = _current()
    if current is None:
        if capture:
            kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return subprocess.run(args,**kwargs)
    timeout = kwargs.pop('timeout',None)
    check = kwargs.pop('check',False)
    text = kwargs.pop('text',False)
    data = kwargs.pop('input',None)
    if isinstance(data,str):data=data.encode()
    if data is not None and len(data)>LIMIT:
        raise ValueError('preparation command input limit exceeded')
    for name in ('stdout','stderr'):
        value = kwargs.pop(name,None)
        if value not in (None,subprocess.PIPE):
            raise ValueError('request output requires captured command streams')
    inherited = tuple(sorted(set(kwargs.pop('pass_fds',())) | set(current.fds)))
    proc = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            pass_fds=inherited,**kwargs)
    buffers = {'stdout':bytearray(),'stderr':bytearray()}
    totals = {'stdout':0,'stderr':0}
    end = time.monotonic()+timeout if timeout is not None else None
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                stream = getattr(proc,name)
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_READ,name)
            if data:
                os.set_blocking(proc.stdin.fileno(),False)
                selector.register(proc.stdin,selectors.EVENT_WRITE,'stdin')
            elif proc.stdin is not None:
                proc.stdin.close()
            pending = memoryview(data or b'')
            while selector.get_map():
                if end is not None and time.monotonic()>=end:
                    proc.kill()  # subprocess.run의 기존 timeout과 같은 소유 자식 처리다.
                    proc.wait(timeout=1)
                    raise subprocess.TimeoutExpired(args,timeout)
                wait = min(.1,max(0,end-time.monotonic())) if end is not None else .1
                for key,_ in selector.select(wait):
                    if key.data=='stdin':
                        try:count=os.write(key.fd,pending)
                        except BrokenPipeError:count=len(pending)
                        pending=pending[count:]
                        if not pending:
                            selector.unregister(key.fileobj);key.fileobj.close()
                        continue
                    chunk=os.read(key.fd,16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    totals[key.data]+=len(chunk)
                    room=LIMIT-len(buffers[key.data])
                    buffers[key.data].extend(chunk[:room])
                    current.write(chunk)
            remaining=max(0,end-time.monotonic()) if end is not None else None
            proc.wait(timeout=remaining)
        output,error=(bytes(buffers[name]) for name in ('stdout','stderr'))
        if capture and totals['stdout']>LIMIT:
            raise ValueError('preparation command data output limit exceeded')
        if text:
            output,error=output.decode(errors='replace'),error.decode(errors='replace')
        if check and proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode,args,output=output,stderr=error)
        return subprocess.CompletedProcess(args,proc.returncode,output if capture else None,error)
    finally:
        for stream in (proc.stdout,proc.stderr,proc.stdin):
            if stream is not None:stream.close()


def check_output(args, **kwargs):
    if _current() is None:
        return subprocess.check_output(args,**kwargs)
    return run(args,capture=True,check=True,**kwargs).stdout


def _plain(fd):
    value=os.fstat(fd)
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink!=1
            or stat.S_IMODE(value.st_mode)!=0o600 or value.st_uid!=os.geteuid()):
        raise PlatformError('invalid Linux preparation ownership file')
    return value


def _start(pid):
    # comm에 공백/괄호가 있어도 마지막 ')' 다음의 필드 22(starttime)를 읽는다.
    return Path('/proc')/str(pid)/'stat'


def _ticks(pid):
    return _start(pid).read_text().rsplit(')',1)[1].split()[19]


def _binding(op):
    return {'operation_id':op['operation_id'],'vm_id':op['vm_id'],'spec_hash':op['spec_hash'],
            'workspace':op['owned_workspace'],'manifest_sha256':op['template']['manifest_sha256']}


def _owners(runtime):
    path=ensure_runtime(runtime)/'locks'
    path.mkdir(mode=0o700,exist_ok=True)
    if path.is_symlink() or path.resolve()!=path or not path.is_dir():
        raise PlatformError('invalid preparation lock directory')
    return path


class LinuxSlot:
    """슬롯 획득은 미수락 단계다. claim은 공통 잠금 안에서 기록한 원 작업에만 결합한다."""
    def __init__(self, platform):
        self.p=platform
        self.fd=self.owner_fd=None

    def __enter__(self):
        self.fd=os.open(_owners(self.p.runtime)/'linux-prepare.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            _plain(self.fd)
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.fd);self.fd=None
            raise OperationBusy('Linux preparation slot is in progress; request not accepted') from error
        except BaseException:
            os.close(self.fd);self.fd=None
            raise
        return self

    def claim(self, op):
        directory=_owners(self.p.runtime)
        fd,name=tempfile.mkstemp(prefix='.linux-owner-',dir=directory)
        self.owner_fd=fd
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            value={'schema':1,**_binding(op),'host_boot_id':self.p.boot_id_reader(),
                   'pid':os.getpid(),'start_ticks':_ticks(os.getpid()),'fd':fd}
            raw=json.dumps(value,sort_keys=True).encode()
            with os.fdopen(os.dup(fd),'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(fd)
            os.replace(name,directory/('linux-owner-'+op['operation_id']+'.lock'))
            sync_directory(directory)
            _current().fds=(self.fd,fd)
        except BaseException:
            # 원장/임시 소유 흔적은 보존하고 fd만 요청 수명에서 회수한다.
            raise

    def __exit__(self,*args):
        current=_current()
        if current is not None:current.fds=()
        for fd in (self.owner_fd,self.fd):
            if fd is not None:os.close(fd)


def owner_state(platform, op):
    """공통 잠금 안의 읽기. active/residual/absent만 반환하며 관측 오류는 숨기지 않는다."""
    path=Path(platform.runtime)/'locks'/('linux-owner-'+op['operation_id']+'.lock')
    if path.parent.exists() and (path.parent.is_symlink() or path.parent.resolve()!=path.parent):
        raise PlatformError('linked preparation ownership directory')
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return 'absent'
    try:
        info=_plain(fd)
        raw=os.read(fd,8193)
        if len(raw)>8192:raise ValueError('preparation owner size exceeded')
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get('schema')!=1 or any(value.get(k)!=v for k,v in _binding(op).items()):
            raise ValueError('preparation owner and original operation disagree')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            if value.get('host_boot_id')!=platform.boot_id_reader():
                raise ValueError('preparation owner host identity changed')
            if type(value.get('pid')) is not int or type(value.get('fd')) is not int:
                raise ValueError('invalid preparation process identity')
            try:
                if _ticks(value['pid'])!=value['start_ticks']:
                    return 'residual'
            except FileNotFoundError:return 'residual'
            owned=os.stat(Path('/proc')/str(value['pid'])/'fd'/str(value['fd']))
            if (owned.st_dev,owned.st_ino)!=(info.st_dev,info.st_ino):
                raise ValueError('preparation owner fd identity changed')
            return 'active'
        return 'absent'
    finally:os.close(fd)


def ensure_quiet(platform):
    """저장소 해제 guard. 슬롯 부재만으로 불확실한 workspace 자원을 해제하지 않는다."""
    path=Path(platform.runtime)/'locks/linux-prepare.lock'
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:fd=None
    if fd is not None:
        try:
            _plain(fd)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise OperationBusy('Linux preparation must drain before storage shutdown') from error
        finally:os.close(fd)
    from xenova_management import Manager
    for op in Manager(platform)._operations():
        if op['action']!='create' or op['status']=='SUCCEEDED' or op['template']['guest_kind']!='linux':continue
        workspace=platform.data/op['owned_workspace']
        if not workspace.exists() and not workspace.is_symlink():continue
        marker=workspace/'.preparation-resources.json'
        if workspace.is_symlink() or marker.is_symlink():
            raise PlatformError('preparation resources cannot be verified for shutdown')
        try:
            fd=os.open(marker,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                _plain(fd)
                raw=os.read(fd,8193)
            finally:os.close(fd)
            if len(raw)>8192:raise ValueError('preparation resource receipt size exceeded')
            value=json.loads(raw)
            if (not isinstance(value,dict) or value.get('schema')!=1 or value.get('workspace')!=str(workspace)
                    or value.get('clean') is not True or value.get('pending') is not False
                    or value.get('mounts')!=[] or value.get('loops')!=[]):
                raise ValueError('preparation cleanup unconfirmed')
        except (OSError,ValueError) as error:
            raise PlatformError('preparation resources remain unconfirmed; storage shutdown refused') from error


def drain(platform, deadline):
    """호출자는 먼저 gate를 닫고 공통 잠금 밖에서 기존 종료 예산을 공유한다."""
    while True:
        try:
            ensure_quiet(platform)
            return
        except OperationBusy:
            if platform.clock()>=deadline:raise
            platform.sleep(min(.02,max(0,deadline-platform.clock())))
