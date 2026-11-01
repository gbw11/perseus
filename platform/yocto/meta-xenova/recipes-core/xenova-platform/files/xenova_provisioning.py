"""영구 VM 자격의 확보·private 전달. 인증 성공/실행 binding·건강 판정과는 별개다."""
import json
import os
from pathlib import Path
import stat
import time

import xenova_broker as broker
from xenova_guest_profile import validate
from xenova_runtime import atomic_write


def private(path, *, directory=False):
    info=path.lstat()
    if (info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=(0o700 if directory else 0o600)
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode) or info.st_nlink!=1)):
        raise ValueError('private provisioning ownership/mode invalid')


def put(path, content):
    """private adapter 사본만 원자 교체한다. 기존 자격의 새 발급·원본 교체가 아니다."""
    if path.exists() or path.is_symlink():
        private(path)
        if path.read_text()==content:return False
    atomic_write(path,content,mode=0o600)
    return True


def home(data, identity, *, create=False):
    broker.vm_id(identity)
    parent=Path(data)/'provisioning'
    for path in (parent,parent/identity):
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700,exist_ok=True);broker.sync_directory(path.parent)
        private(path,directory=True)
    return parent/identity


def ensure(data, record, *, gid=None, rules=None, reload=None, deadline=None, clock=time.monotonic, retry_reload=False):
    """검증된 원 대상/프로필의 필요한 계정만 확보한다. 비밀 없는 단계 요약을 반환한다."""
    profile=validate(record['provisioning_profile'],guest_kind=record.get('guest_kind'))
    channels=profile['channels'];identity=broker.vm_id(record['uuid'])
    if not channels:return {'state':'not-requested','declaration':profile['source'],'channels':[]}
    destination=home(data,identity,create=True)
    accounts=([identity] if set(channels)&{'agent','drive'} else [])+(['telemetry-'+identity] if 'telemetry' in channels else [])
    from xenova_preparation import _current, check_creation
    check_creation()
    current=_current()
    if deadline is None and current is not None and current.creation_deadline is not None:
        deadline,clock=current.creation_deadline,current.creation_clock
    remaining=20 if deadline is None else min(20,deadline-clock())
    if remaining<=0:raise TimeoutError('credential preparation budget expired')
    try:
        result,value=broker.ensure_accounts(accounts,data=Path(data),gid=gid,rules=rules,
                     _pass_fds=getattr(current,'fds',()),_hash_timeout=remaining)
    except broker.SetupError as error:
        from xenova_execution import ExecutionError
        raise ExecutionError('CREDENTIAL_REVOKED' if isinstance(error,broker.CredentialRevoked) else 'CREDENTIAL_SOURCE_UNAVAILABLE',
                             str(error),details={'vm_id':identity}) from error
    check_creation()
    if deadline is not None and clock()>=deadline:raise TimeoutError('credential preparation budget expired after storage')
    summary={'schema_version':'1.0','vm_id':identity,'channels':channels,
             'accounts':accounts,'broker_revision':result['revision'],'credential_state':'stored',
             'broker_application':'unconfirmed','guest_application':'not-applied','authentication':'unobserved'}
    old=destination/'status.json'
    if old.exists():
        private(old);prior=json.loads(old.read_text())
        if prior.get('vm_id')!=identity:raise ValueError('provisioning status identity mismatch')
        if result['reused'] and prior.get('broker_revision')==result['revision']:
            summary.update({k:prior[k] for k in ('broker_application','guest_application')})
    for channel in channels:
        username='telemetry-'+identity if channel=='telemetry' else identity
        secret=value['secrets'][username]
        if channel=='agent':
            content=(f'XENOVA_AGENT_VM_ID={identity}\nXENOVA_AGENT_BROKER_HOST=192.168.77.1\n'
                     f'XENOVA_AGENT_BROKER_PORT=1883\nXENOVA_AGENT_BROKER_USERNAME={username}\n'
                     f'XENOVA_AGENT_BROKER_PASSWORD={secret}\n')
            put(destination/'agent.env',content)
        else:
            payload={'vm_id':identity,'broker_ipv4':'192.168.77.1','port':1883,
                     'username':username,'password':secret,'trusted_internal_plaintext':True}
            previous=destination/(channel+'.json')
            if channel=='drive' and previous.exists():
                parsed,_=bootstrap_api().credentials(previous,identity,'drive')
                if 'world' in parsed:payload['world']=parsed['world']
            put(destination/(channel+'.json'),json.dumps(payload,sort_keys=True)+'\n')
    if not result['reused'] or retry_reload and summary['broker_application']=='unconfirmed':
        try:summary['broker_application']=(reload or broker.reload_broker)()
        except (broker.SetupError,OSError):summary['broker_application']='unconfirmed'
    put(old,json.dumps(summary,sort_keys=True)+'\n')
    return summary


def bootstrap_api():
    """설치된 제공자와 소스 트리의 동일 codec을 쓴다. import에 외부 효과는 없다."""
    import importlib.util
    path=Path(__file__).with_name('provision_xenova_telemetry.py')
    if not path.exists():path=Path(__file__).resolve().parents[5]/'scripts/provision-xenova-telemetry.py'
    spec=importlib.util.spec_from_file_location('xenova_supplied_bootstrap',path)
    api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
    return api


def install_world(data, identity, source):
    """승인받은 별도 broker 자격을 root 전용 파일에서 설치한다. 계정 발급/접속은 하지 않는다.

    source는 vm_id와 world만 담은 승인 입력이다. Pi 자격은 바꾸지 않고
    별도 world 부분만 채택한다. 새 실행 전에 호출하며 기존 receipt를 재작성하지 않는다.
    """
    api=bootstrap_api();broker.vm_id(identity);incoming,_=api.private_json(Path(source))
    if not isinstance(incoming,dict) or set(incoming)!={'vm_id','world'} or incoming['vm_id']!=identity:
        raise ValueError('separate world credential and matching VM required')
    target=credential_file(data,identity,'drive');current,_=api.credentials(target,identity,'drive')
    merged=api.validate_credentials({**current,'world':incoming['world']},identity,'drive')
    put(target,json.dumps(merged,sort_keys=True)+'\n')
    return {'vm_id':identity,'world_profile':incoming['world']['profile'],'state':'private-configured',
            'authentication':'unobserved'}


def credential_file(data, identity, channel):
    """Worker/Bootstrap용 privileged private 경로. 조회만 하며 생성·reload하지 않는다."""
    if channel not in ('agent','telemetry','drive'):raise ValueError('unsupported credential channel')
    directory=home(data,identity)
    private(directory/'status.json');status=json.loads((directory/'status.json').read_text())
    if status.get('vm_id')!=identity or channel not in status.get('channels',[]):raise ValueError('credential channel not supplied')
    path=directory/('agent.env' if channel=='agent' else channel+'.json');private(path)
    return path


def receipt_directory(data,identity,execution,*,create=False):
    """기존 ProductionBootstrap의 receipt_dir/VM/execution 배치를 공유한다. 새 신원 원본은 없다."""
    broker.vm_id(identity);broker.vm_id(execution)
    parent=Path(data)/'drive-rejoin'
    for path in (parent,parent/identity,parent/identity/execution):
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700,exist_ok=True);broker.sync_directory(path.parent)
        private(path,directory=True)
    return parent/identity/execution


def apply_linux(data, record, root, *, factory=False, **kwargs):
    """기존 private root mount에서만 호출. 공용 template/seed 빌드에는 호출하지 않는다."""
    from prepare_rootfs import inside, write
    summary=ensure(data,record,**kwargs)
    if 'agent' not in summary['channels']:return summary
    root=Path(root)
    if not inside(root,'usr/lib/systemd/system/xenova-agent.service',create_parents=False).is_file():
        raise ValueError('declared Agent adapter is not installed')
    if factory:
        write(root,'etc/xenova/platform-identity.json',json.dumps({'schema':1,'vm_id':record['uuid']})+'\n',0o600)
    elif inside(root,'etc/xenova/instance-id').read_text().strip()!=record['uuid']:
        raise ValueError('guest identity differs from credential target')
    target=inside(root,'etc/xenova/agent.env',follow_final=False)
    put(target,credential_file(data,record['uuid'],'agent').read_text())
    write(root,'etc/systemd/system/xenova-agent.service.d/20-xenova-provisioning.conf',
          '[Unit]\nConditionPathExists=/etc/xenova/agent.env\n',0o644)
    summary['guest_application']='private-root-configured'
    put(home(data,record['uuid'])/'status.json',json.dumps(summary,sort_keys=True)+'\n')
    return summary


def status(data, identity):
    """공개 가능한 단계 요약. 비밀 파일·broker 원본·실행 관측을 읽지 않는다."""
    path=Path(data)/'provisioning'/broker.vm_id(identity)/'status.json'
    if not path.exists() and not path.is_symlink():return {'vm_id':identity,'state':'not-supplied'}
    home(data,identity);private(path);value=json.loads(path.read_text())
    if value.get('vm_id')!=identity:raise ValueError('provisioning status identity mismatch')
    allowed={'schema_version','vm_id','channels','accounts','broker_revision','credential_state',
             'broker_application','guest_application','authentication'}
    if set(value)!=allowed:raise ValueError('invalid provisioning status')
    return value


def apply_stopped_linux(platform, record, *, factory=False, **kwargs):
    """호출자는 기존 대상 lease·정지/미사용 guard를 유지해야 한다. 자기 mount만 정리한다."""
    from prepare_instance import Workspace
    work=Workspace(platform.data)
    try:
        root=work.path/'root'
        work.mount(work.loop(platform.data/'instances'/record['id']/'root.raw'),root)
        return apply_linux(platform.data,record,root,factory=factory,**kwargs)
    finally:work.close()


def bootstrap_execution(platform, record, *, deadline, store_factory=None, observe=None):
    """현재 실행에 기존 Bootstrap/receipt를 적용한다. Store 게시와 guest 소비/건강은 별개다."""
    from xenova_drive_binding import observe as binding
    api=bootstrap_api()
    fields=('vm_id','host_boot_id','execution_id','domid','xen_unique_id')
    applied=[]
    def current():
        if platform.clock()>=deadline:raise TimeoutError('execution supply budget expired')
        row=(observe or (lambda:binding(platform,record['uuid'],timeout_s=min(2,deadline-platform.clock()))))()
        if row.get('status')!='observed':raise ValueError('current execution is not eligible for provisioning')
        if row.get('signal_profile') is None:raise ValueError('loaded execution profile unavailable; no latest-profile inference')
        profile=validate(row['signal_profile'])
        if applied and applied[0]!=profile:raise ValueError('loaded execution profile changed')
        if not applied:applied.append(profile)
        return {key:row[key] for key in fields}
    context=current();profile=applied[0]
    directory=receipt_directory(platform.data,record['uuid'],context['execution_id'],create=True)
    channels=[c for c in ('telemetry','drive') if c in profile['channels']]
    for channel in channels:
        credential,source=api.credentials(credential_file(platform.data,record['uuid'],channel),record['uuid'],channel)
        path=directory/(channel+'.json')
        with api.receipt_lock(path):
            store=(store_factory or api.XenStore)(channel)
            try:
                api.provision(context,credential,source,path,store=store,observe=current,
                              channel=channel,metadata=None,
                              guest_boot_receipt=directory/'telemetry.json' if channel=='drive' else None,
                              drive_profile=('warm-drive-1' if profile['apps'].get('autonomous',{}).get('standby_progress')=='idle'
                                             else 'verified-drive-1') if channel=='drive' else None)
            finally:store.close()
    result=status(platform.data,record['uuid'])
    result.update(guest_application='store-published',execution_id=context['execution_id'])
    # 현재 실행 근거는 원 receipt가 소유한다. 영구 상태 요약에 실행 신원을 중복 저장하지 않는다.
    saved={k:v for k,v in result.items() if k!='execution_id'}
    put(home(platform.data,record['uuid'])/'status.json',json.dumps(saved,sort_keys=True)+'\n')
    return result
