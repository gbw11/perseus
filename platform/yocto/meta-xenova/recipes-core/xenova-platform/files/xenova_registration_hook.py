"""등록 확정 후의 선택적 로컬 FastAPI 1회 호출. Spring 자격/URL은 취급하지 않는다.

자동 기회는 원 create/factory 기록에서 이미 소비한다. 이 helper는 재시도 큐가 아니다.
factory는 현재 등록 사건의 일시 systemd job만 예약한다. API READY 뒤 실행 예산을
시작하며 재부팅/API 재시작에 목록을 훑거나 job을 재생성하지 않는다.
"""
import fcntl
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time

from xenova_runtime import atomic_write

API_ENV = Path('/etc/xenova/dom0-manager.env')
TOKEN_FILE = Path('/etc/xenova/dom0-manager-token.env')
MAX_CHILDREN = 4
CALL_SECONDS = 15
API_UNIT = 'xenova-dom0-manager.service'


def private_text(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            raise ValueError('local API configuration must be private')
        value=stream.read(16385)
    if len(value)>16384:raise ValueError('local API configuration too large')
    return value


def endpoint(path):
    selected={}
    for line in private_text(path).splitlines():
        key,separator,value=line.partition('=')
        if separator and key in ('XENOVA_HOST','XENOVA_PORT','XENOVA_SERVICE_TOKEN'):selected[key]=value
    address=selected.get('XENOVA_HOST','127.0.0.1')
    ip=ipaddress.ip_address(address)
    # Wildcard bind가 있으면 같은 namespace의 loopback으로 접속한다.
    if ip.is_unspecified:address='127.0.0.1' if ip.version==4 else '::1'
    port=int(selected.get('XENOVA_PORT','8081'))
    if not 1<=port<=65535:raise ValueError('invalid local API port')
    return address,port,selected.get('XENOVA_SERVICE_TOKEN')


def call_once(identity,api_env,token_file,end,ready):
    connection=None;phase='configuration'
    try:
        host,port,token=endpoint(api_env)
        if ready:
            phase='readiness'
            while time.monotonic()<end:
                try:
                    connection=http.client.HTTPConnection(host,port,timeout=min(.25,max(.001,end-time.monotonic())))
                    connection.request('GET','/openapi.json')  # Xen/health/등록 조회를 수행하지 않는 준비 경계
                    response=connection.getresponse()
                    available=response.status==200
                except (OSError,http.client.HTTPException):available=False
                finally:
                    if connection is not None:connection.close();connection=None
                if available:break
                time.sleep(min(.1,max(0,end-time.monotonic())))
            else:return {'state':'api-unavailable','confirmed':False}
        phase='authentication'
        if token is None:
            raw=private_text(token_file)
            match=re.fullmatch(r'XENOVA_SERVICE_TOKEN=([A-Za-z0-9._~+/-]{32,256}={0,2})\n',raw)
            if not match:raise ValueError('invalid local API token')
            token=match[1]
        if not 1<=len(token)<=2048 or any(not 33<=ord(c)<=126 for c in token):raise ValueError('invalid local API token')
        remaining=end-time.monotonic()
        if remaining<=.05:return {'state':'api-unavailable','confirmed':False}
        connection=http.client.HTTPConnection(host,port,timeout=remaining)
        phase='connect';connection.connect();phase='request'
        connection.request('POST','/api/v1/vm-registrations/'+identity+'/notify',b'',
                           {'Authorization':'Bearer '+token,'X-Request-Budget-Ms':str(max(1,int(remaining*1000)))})
        response=connection.getresponse();raw=response.read(4097)
        if len(raw)>4096:return {'state':'response-invalid','confirmed':False}
        from json import JSONDecodeError
        try:body=json.loads(raw)
        except (JSONDecodeError,UnicodeError):return {'state':'response-invalid','confirmed':False}
        if isinstance(body,dict) and body.get('vm_id')==identity and type(body.get('confirmed')) is bool:
            if body['confirmed'] and response.status!=200:return {'state':'response-invalid','confirmed':False,'http_status':response.status}
            # FastAPI는 비밀/원 본문 없는 제한된 결과만 제공한다.
            allowed=('state','confirmed','http_status','upstream_code','cause_type','request_id','vm_id')
            return {k:body[k] for k in allowed if k in body}
        safe={}
        if isinstance(body,dict):
            for key in ('code','platform_code'):
                value=body.get(key)
                if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}',value):safe['api_'+key]=value
        return {'state':'api-rejected','confirmed':False,'http_status':response.status,**safe}
    except (OSError,ValueError,http.client.HTTPException) as error:
        state=('local-configuration-invalid' if phase in ('configuration','authentication') else
               'api-unavailable' if phase in ('readiness','connect') else 'delivery-unknown')
        return {'state':state,'confirmed':False,'cause_type':type(error).__name__}
    finally:
        if connection is not None:connection.close()


def callback_slot(runtime):
    """이미 사용하던 유한 실행 slot. 대기 job은 factory가 확정한 UUID에만 존재한다."""
    slots=Path(runtime)/'registration-notices'
    slots.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=slots.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise ValueError('invalid local callback slots')
    for index in range(MAX_CHILDREN):
        candidate=os.open(slots/str(index),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        info=os.fstat(candidate)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            os.close(candidate);raise ValueError('invalid local callback slot')
        try:fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(candidate);continue
        return candidate
    return None


def automatic(data,runtime,record,*,wait_ready=False,api_env=None,token_file=None):
    """원 잠금/lease 해제 후 호출한다. slot/launch 실패도 원 등록을 변경하지 않는다."""
    api_env=API_ENV if api_env is None else Path(api_env)
    token_file=TOKEN_FILE if token_file is None else Path(token_file)
    if not api_env.exists():return {'state':'api-not-installed','confirmed':False}
    fd=None
    try:
        fd=callback_slot(runtime)
        if fd is None:return {'state':'capacity-skipped','confirmed':False}
        identity=record['uuid']
        from xenova_vm import UUID
        if not isinstance(identity,str) or not UUID.fullmatch(identity):raise ValueError('VM UUID required')
        end=time.monotonic()+CALL_SECONDS
        # API 내 create에서도 호출된다. Spring 송신 token/URL이나 다른 서비스 자격을
        # 로컬 후속 프로세스의 환경으로 복제하지 않는다. 실행에 필요한 환경만 보존한다.
        environment={key:value for key,value in os.environ.items()
                     if key in ('PATH','PYTHONPATH','PYTHONHOME','PYTHONNOUSERSITE','PYTHONDONTWRITEBYTECODE',
                                'LD_LIBRARY_PATH','LANG','TZ') or key.startswith('LC_')}
        arguments=([sys.executable,str(Path(__file__).resolve()),'defer',str(data),str(runtime),
                    str(api_env),str(token_file),str(end),identity,str(fd)] if wait_ready else
                   [sys.executable,str(Path(__file__).resolve()),str(Path(data)/'registration-notices'/(identity+'.json')),
                    str(api_env),str(token_file),str(end),'0',str(fd),identity])
        child=subprocess.Popen(arguments,
                               stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               pass_fds=(fd,),close_fds=True,start_new_session=True,env=environment)
        threading.Thread(target=child.wait,name='registration-reaper',daemon=True).start()
        return {'state':'attempt-started','confirmed':False}
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'state':'launch-unconfirmed','confirmed':False}
    finally:
        if fd is not None:os.close(fd)


def defer_factory(data,runtime,api_env,token_file,end,identity,*,api_unit=API_UNIT,unit_prefix='xenova-registration-'):
    """등록이 만든 job 하나를 비동기 제출한다. systemd가 API 실패/종료와 job 수명을 소유한다."""
    command=['systemd-run','--quiet','--no-block','--collect','--service-type=exec',
             '--unit='+unit_prefix+identity,
             '--property=Requires='+api_unit,'--property=After='+api_unit,
             '--property=Restart=no','--property=UMask=0077',
             '--property=RuntimeMaxSec='+str(CALL_SECONDS)+'s',
             '--property=TimeoutStopSec='+str(CALL_SECONDS)+'s','--property=KillMode=control-group',
             '--property=StandardOutput=null','--property=StandardError=null',
             sys.executable,str(Path(__file__).resolve()),'after-api',str(data),str(runtime),
             str(api_env),str(token_file),identity]
    try:
        # --no-block은 등록/부팅과 API 활성화의 순환 대기를 만들지 않는다.
        completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 close_fds=True,timeout=max(.001,end-time.monotonic()))
        if completed.returncode==0:return
        outcome={'state':'schedule-rejected','confirmed':False}
    except (OSError,subprocess.SubprocessError) as error:
        outcome={'state':'schedule-unconfirmed','confirmed':False,'cause_type':type(error).__name__}
    destination=Path(data)/'registration-notices'/(identity+'.json')
    destination.parent.mkdir(mode=0o700,exist_ok=True)
    atomic_write(destination,json.dumps({'automatic':True,**outcome})+'\n',mode=0o600)


def after_api(data,runtime,api_env,token_file,identity):
    """API의 READY 뒤 실행되는 일회 작업. 송신 기한은 여기서 시작하고 재시도하지 않는다."""
    fd=callback_slot(runtime)
    destination=Path(data)/'registration-notices'/(identity+'.json')
    try:
        # Requires 관계의 명시 restart는 아직 남은 dependent job을 다시 시작할 수 있다.
        # 원 등록의 내구 소비와 별개로 같은 boot의 실행 재진입만 O_EXCL로 차단한다.
        # /run 표식은 작업 큐가 아니며 API 재시작/실패에서 지우거나 재시도하지 않는다.
        try:
            claim=os.open(Path(runtime)/'registration-notices'/('factory-started-'+identity),
                          os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        except FileExistsError:return
        os.close(claim)
        if fd is None:
            destination.parent.mkdir(mode=0o700,exist_ok=True)
            atomic_write(destination,json.dumps({'automatic':True,'state':'capacity-skipped','confirmed':False})+'\n',mode=0o600)
            return
        supervise(destination,Path(api_env),Path(token_file),time.monotonic()+CALL_SECONDS,False,identity)
    finally:
        if fd is not None:os.close(fd)


def supervise(destination,api_env,token_file,end,ready,identity):
    """별도 supervisor가 slow DNS/HTTP 자식을 종료·회수한다. 부가 fsync는 호출자 밖이다."""
    read,write=os.pipe()
    child=os.fork()
    if child==0:
        import ctypes
        parent=os.getppid()
        if ctypes.CDLL(None).prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent:os._exit(1)
        os.close(read)
        outcome=call_once(identity,api_env,token_file,end,ready)
        os.write(write,json.dumps(outcome).encode());os.close(write);os._exit(0)
    os.close(write)
    outcome={'state':'delivery-unknown','confirmed':False}
    while time.monotonic()<end:
        pid,status=os.waitpid(child,os.WNOHANG)
        if pid:
            if os.WIFEXITED(status) and os.WEXITSTATUS(status)==0:
                try:outcome=json.loads(os.read(read,4096))
                except (ValueError,UnicodeError):pass
            break
        time.sleep(.01)
    else:
        os.kill(child,signal.SIGKILL);os.waitpid(child,0)
    os.close(read)
    destination.parent.mkdir(mode=0o700,exist_ok=True)
    atomic_write(destination,json.dumps({'automatic':True,**outcome})+'\n',mode=0o600)


if __name__=='__main__':
    if len(sys.argv)==9 and sys.argv[1]=='defer':
        defer_factory(Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]),Path(sys.argv[5]),float(sys.argv[6]),sys.argv[7])
        raise SystemExit(0)
    if len(sys.argv)==7 and sys.argv[1]=='after-api':
        after_api(Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]),Path(sys.argv[5]),sys.argv[6])
        raise SystemExit(0)
    if len(sys.argv)!=8:raise SystemExit(2)
    supervise(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),float(sys.argv[4]),bool(int(sys.argv[5])),sys.argv[7])
