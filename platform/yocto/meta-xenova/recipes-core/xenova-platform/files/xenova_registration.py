"""선택적인 등록-only 통지. 원 등록과 독립인 유한 1회 프로세스이며 재시도하지 않는다.

수신 route는 Spring 합의 전 제공자 계약안이다. 기존 create/start API를 호출하지 않는다.
자동 기회 소비는 create journal/factory progress가 소유한다. 이 파일은 송신 결과만 보관한다.
"""
import fcntl
import http.client
import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import ssl
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from xenova_runtime import atomic_write

CONFIG=Path('/etc/xenova/registration-notification.json')
MAX_RESPONSE=4096
MAX_CHILDREN=4


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON field')
        result[key]=value
    return result


def decode(raw):
    def invalid(value):raise ValueError('non-finite JSON')
    return json.loads(raw,object_pairs_hook=unique,parse_constant=invalid)


def private_json(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            raise ValueError('notification input ownership/mode')
        raw=stream.read(8193)
    if len(raw)>8192:raise ValueError('notification input too large')
    return decode(raw)


def configuration(path=CONFIG):
    value=private_json(path)
    if (not isinstance(value,dict) or set(value)-{'schema_version','endpoint','token_file','timeout_ms','allow_loopback_http'}
            or value.get('schema_version')!='1.0' or not isinstance(value.get('endpoint'),str)
            or not isinstance(value.get('token_file'),str)
            or type(value.get('timeout_ms',2000)) is not int or not 100<=value.get('timeout_ms',2000)<=5000
            or type(value.get('allow_loopback_http',False)) is not bool):
        raise ValueError('invalid notification configuration')
    endpoint=urlsplit(value['endpoint'])
    if endpoint.port is not None and not 1<=endpoint.port<=65535:raise ValueError('notification endpoint port')
    loopback=False
    try:loopback=ipaddress.ip_address(endpoint.hostname).is_loopback
    except ValueError:pass
    if (endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment
            or not endpoint.hostname or endpoint.path!='/api/v1/vms/registrations'
            or endpoint.scheme!='https' and not (endpoint.scheme=='http' and loopback and value.get('allow_loopback_http') is True)):
        raise ValueError('registration-only endpoint required; HTTPS outside explicit loopback fixture')
    token=private_json(Path(value['token_file']))
    if (not isinstance(token,dict) or set(token)!={'token'} or not isinstance(token['token'],str)
            or not 1<=len(token['token'])<=2048 or any(not 33<=ord(c)<=126 for c in token['token'])):
        raise ValueError('invalid notification authentication input')
    return endpoint,token['token'],value.get('timeout_ms',2000)/1000


def payload(record):
    """검증된 registry record에서 공개 등록 구성만 추출한다. 현재 건강/의도는 보내지 않는다."""
    import xenova_vm as vm
    if not vm.UUID.fullmatch(record['uuid']):raise ValueError('registered VM identity required')
    value={'schema_version':'1.0','vm_id':record['uuid'],'name':record.get('display_name',record['name']),
           'guest_kind':vm.guest_kind(record),'template_id':record['template'],
           'template_version':record.get('template_version'),'vcpus':record['vcpus'],'memory_mib':record['memory_mib']}
    if vm.has_network(record):value['assigned_ipv4']=record['ipv4']
    if len(json.dumps(value).encode())>4096:raise ValueError('registration payload too large')
    return value


def send(body, *, config=CONFIG):
    """HTTP를 한 번만 보낸다. 임의 2xx/접수를 DB 등록 확정으로 바꾸지 않는다."""
    try:endpoint,token,timeout=configuration(config)
    except FileNotFoundError:return {'state':'configuration-invalid' if Path(config).exists() else 'not-configured','confirmed':False}
    except (OSError,ValueError,TypeError):return {'state':'configuration-invalid','confirmed':False}
    connection=None;phase='connect'
    try:
        cls=http.client.HTTPSConnection if endpoint.scheme=='https' else http.client.HTTPConnection
        connection=cls(endpoint.hostname,endpoint.port,timeout=timeout)
        connection.connect();phase='request'
        connection.request('POST',endpoint.path,json.dumps(body,separators=(',',':')).encode(),
                           {'Content-Type':'application/json','Authorization':'Bearer '+token})
        response=connection.getresponse();raw=response.read(MAX_RESPONSE+1)
        if len(raw)>MAX_RESPONSE:return {'state':'response-invalid','confirmed':False}
        if 300<=response.status<400:return {'state':'redirect-refused','confirmed':False,'http_status':response.status}
        if response.status>=400:return {'state':'rejected','confirmed':False,'http_status':response.status}
        try:value=decode(raw)
        except (ValueError,UnicodeError):return {'state':'response-invalid','confirmed':False,'http_status':response.status}
        if response.status in (200,201) and value=={'vm_id':body['vm_id'],'registration':'confirmed'}:
            return {'state':'confirmed','confirmed':True,'http_status':response.status}
        return {'state':'response-unconfirmed','confirmed':False,'http_status':response.status}
    except ssl.SSLCertVerificationError:
        return {'state':'peer-verification-failed','confirmed':False}
    except (OSError,http.client.HTTPException,ValueError) as error:
        # 연결 뒤 EOF/timeout은 서버 미저장의 증거가 아니다.
        return {'state':'connection-unavailable' if phase=='connect' else 'delivery-unknown',
                'confirmed':False,'cause_type':type(error).__name__}
    finally:
        if connection is not None:connection.close()


def directory(parent):
    parent.mkdir(mode=0o700,exist_ok=True)
    info=parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
        raise ValueError('notification directory ownership/mode')
    return parent


def launch(data, runtime, record, *, config=None, automatic=True, budget=5):
    """소비된 자동 기회 또는 명시 CLI가 호출한다. HTTP 자식에 관리 FD는 넘기지 않는다."""
    config=CONFIG if config is None else config
    if type(budget) not in (int,float) or not 0<budget<=15:raise ValueError('notification call budget')
    if not Path(config).exists():return {'state':'not-configured','confirmed':False}
    from uuid import uuid4
    name=record['uuid']+('' if automatic else '-manual-'+str(uuid4()))+'.json'
    destination=Path(data)/'registration-notices'/name
    slots=directory(Path(runtime)/'registration-notices');owner=None
    for index in range(MAX_CHILDREN):
        fd=os.open(slots/str(index),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        info=os.fstat(fd)
        if info.st_uid!=0 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            os.close(fd);raise ValueError('invalid notification slot')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(fd);continue
        owner=fd;break
    if owner is None:return {'state':'capacity-skipped','confirmed':False}
    try:
        body=payload(record)
        wire=(json.dumps(body,separators=(',',':'))+'\n').encode()
        if len(wire)>4096:raise ValueError('bounded notification pipe payload')
        end=time.monotonic()+min(5,budget)
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--send-once',str(destination),str(config),str(owner),str(end),str(int(automatic))],
                pass_fds=(owner,),close_fds=True,start_new_session=True,stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            # 새 pipe의 PIPE_BUF 이하 공개 body 한 번만 보낸다. optional fsync는 자식 소유다.
            os.set_blocking(child.stdin.fileno(),False)
            if os.write(child.stdin.fileno(),wire)!=len(wire):raise OSError('notification body incomplete')
        except BaseException:
            threading.Thread(target=child.wait,name='registration-reaper',daemon=True).start()
            raise
        finally:child.stdin.close()
    finally:os.close(owner)
    if automatic:
        # 최대 4 slot. reaper는 종료만 기다리며 재송신/새 효과가 없다.
        threading.Thread(target=child.wait,name='registration-reaper',daemon=True).start()
        return {'state':'attempt-started','confirmed':False}
    code=child.wait(timeout=min(5,budget)+2)
    try:result={k:v for k,v in private_json(destination).items() if k!='payload'}
    except (OSError,ValueError):result={'state':'delivery-unknown','confirmed':False,'cause':'result-unavailable'}
    if code:result={'state':'delivery-unknown','confirmed':False,'cause':'sender-exit'}
    destination.unlink(missing_ok=True)  # 이번 수동 호출이 만든 임시 결과만 반환 뒤 제거한다.
    return result


def automatic(data,runtime,record):
    """부가 통지의 설정/기록/기동 실패가 확정된 등록을 뒤집지 않는다."""
    try:return launch(data,runtime,record)
    except (OSError,ValueError,subprocess.SubprocessError):return {'state':'launch-unconfirmed','confirmed':False}


def supervise(destination,config,slot,call_end,automatic):
    """DNS를 포함한 송신을 독립 자식의 유한 수명에 둔다. 재시도/후속 GET은 없다."""
    started=time.monotonic()
    ready,_,_=select.select([0],[],[],max(0,min(1,call_end-started)))
    if not ready:return
    wire=os.read(0,4097)
    if len(wire)>4096 or not wire.endswith(b'\n'):return
    value={'state':'attempt-consumed','confirmed':False,'automatic':automatic,'payload':decode(wire)}
    directory(destination.parent)
    atomic_write(destination,json.dumps(value)+'\n',mode=0o600)
    try:timeout=configuration(config)[2]
    except (OSError,ValueError,TypeError) as error:
        # 확정된 local 설정 오류를 짧은 자식 timeout의 미확정 전송으로 바꾸지 않는다.
        state=('not-configured' if isinstance(error,FileNotFoundError) and not config.exists()
               else 'configuration-invalid')
        atomic_write(destination,json.dumps({**value,'state':state})+'\n',mode=0o600)
        return
    end=min(call_end,started+timeout)
    if time.monotonic()>=end:
        atomic_write(destination,json.dumps({**value,'state':'delivery-unknown'})+'\n',mode=0o600)
        return
    child=os.fork()
    if child==0:
        # supervisor가 갑자기 끝나도 HTTP 자식을 남기지 않는다. 고정 Linux target 전용.
        import ctypes
        parent=os.getppid()
        if ctypes.CDLL(None).prctl(1,signal.SIGKILL,0,0,0)!=0:os._exit(1)
        if os.getppid()!=parent:os._exit(1)
        result=send(value['payload'],config=config)
        atomic_write(destination,json.dumps({**value,**result})+'\n',mode=0o600)
        os._exit(0)
    while time.monotonic()<end:
        pid,status=os.waitpid(child,os.WNOHANG)
        if pid:
            if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
                atomic_write(destination,json.dumps({**value,'state':'delivery-unknown','confirmed':False,
                    'cause':'sender-exit','exit_status':os.waitstatus_to_exitcode(status)})+'\n',mode=0o600)
            return
        time.sleep(.01)
    os.kill(child,signal.SIGKILL);os.waitpid(child,0)
    atomic_write(destination,json.dumps({**value,'state':'delivery-unknown','confirmed':False})+'\n',mode=0o600)


if __name__=='__main__':
    if len(sys.argv)!=7 or sys.argv[1]!='--send-once':raise SystemExit(2)
    supervise(Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4]),float(sys.argv[5]),bool(int(sys.argv[6])))
