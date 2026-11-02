"""등록 확정 후의 선택적 로컬 FastAPI 1회 호출. Spring 자격/URL은 취급하지 않는다.

자동 기회는 원 create/factory 기록에서 이미 소비한다. 이 helper는 재시도 큐가 아니다.
factory 준비 확인만 유한 반복하며 notify POST를 시도한 뒤에는 다시 조회/송신하지 않는다.
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


def automatic(data,runtime,record,*,wait_ready=False,api_env=None,token_file=None):
    """원 잠금/lease 해제 후 호출한다. slot/launch 실패도 원 등록을 변경하지 않는다."""
    api_env=API_ENV if api_env is None else Path(api_env)
    token_file=TOKEN_FILE if token_file is None else Path(token_file)
    if not api_env.exists():return {'state':'api-not-installed','confirmed':False}
    fd=None
    try:
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
            fd=candidate;break
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
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),str(Path(data)/'registration-notices'/(identity+'.json')),
                                str(api_env),str(token_file),str(end),str(int(wait_ready)),str(fd),identity],
                               stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               pass_fds=(fd,),close_fds=True,start_new_session=True,env=environment)
        threading.Thread(target=child.wait,name='registration-reaper',daemon=True).start()
        return {'state':'attempt-started','confirmed':False}
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'state':'launch-unconfirmed','confirmed':False}
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
    if len(sys.argv)!=8:raise SystemExit(2)
    supervise(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),float(sys.argv[4]),bool(int(sys.argv[5])),sys.argv[7])
