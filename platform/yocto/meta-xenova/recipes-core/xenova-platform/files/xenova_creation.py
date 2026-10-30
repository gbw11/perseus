"""원 create의 유한 시간과 명시 private 폐기. 기존 operation/슬롯을 사용하며 자동 실행하지 않는다.

진단은 읽기 전용이다. 삭제는 원 작업·고정 계획·현재 소유/사용 근거를 확인한 명시 호출만 수행한다.
완료 JSON의 가독성은 내구 확인이 아니다. 예약 소비 writer가 파일/부모를 재동기화한다.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from xenova_runtime import PlatformError, OperationBusy, operation_lock, sync_directory
from xenova_preparation import LinuxSlot, creation_work

MAX_ITEMS=128
MAX_BYTES=131072
MAX_CALL_MS=86460000  # 직렬화/검증 한계이며 생성 운영 기본값이 아니다.


def fail(code,message,**kwargs):
    from xenova_management import fail as refuse
    refuse(code,message,**kwargs)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def check(m,deadline):
    if m.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','creation reconciliation budget expired')


def policy(m,required=True):
    path=getattr(m.p,'creation_policy_path',m.defaults_path.with_name('creation-policy.json'))
    try:
        value=m._read(path)
        if (set(value)!={'schema_version','call_ms','execution_ms','cleanup_ms'} or value['schema_version']!='1.0'
                or path.stat().st_mode & 0o022):raise ValueError('invalid creation policy')
        for key in ('call_ms','execution_ms','cleanup_ms'):
            if type(value[key]) is not int or not 0<value[key]<=MAX_CALL_MS:raise ValueError('finite creation policy required')
        if value['cleanup_ms']>60000 or value['call_ms']<=value['cleanup_ms']:raise ValueError('cleanup margin required')
        return value
    except (OSError,ValueError,PlatformError) as error:
        if required:fail('RECONCILE_REQUIRED','finite creation policy cannot be verified; request not accepted',
                         details={'accepted':False,'cause_type':type(error).__name__})
        return None


def accept(m,op,context):
    from xenova_vm import UUID
    limits=policy(m)
    call_ms=context.get('budget_ms') or limits['call_ms']
    if call_ms>limits['call_ms']:fail('INVALID_SPEC','create budget exceeds configured call limit')
    call_end=context['entered']+call_ms/1000
    now=m.p.clock();end=min(now+limits['execution_ms']/1000,call_end-limits['cleanup_ms']/1000)
    check(m,end)
    if int(end*1000)<=int(now*1000):fail('DEADLINE_EXCEEDED','no execution interval remains before acceptance')
    host=m.p.boot_id_reader()
    if not isinstance(host,str) or not UUID.fullmatch(host):fail('OBSERVATION_UNAVAILABLE','current host boot cannot be verified')
    op['schema_version']='1.1'
    op['creation']={'version':1,'host_boot_id':host,
        'accepted_monotonic_ms':int(now*1000),'deadline_monotonic_ms':int(end*1000),'limits':limits}
    context['create_call_deadline']=call_end


def validate(op):
    """알 수 없는 subtree/관계는 예약 0으로 해석하지 않는다."""
    timing=op.get('creation')
    if timing is not None:
        from xenova_vm import UUID
        if (not isinstance(timing,dict) or set(timing)!={'version','host_boot_id','accepted_monotonic_ms','deadline_monotonic_ms','limits'}
                or timing['version']!=1 or not UUID.fullmatch(timing.get('host_boot_id',''))
                or any(type(timing.get(k)) is not int or timing[k]<0 for k in ('accepted_monotonic_ms','deadline_monotonic_ms'))
                or timing['deadline_monotonic_ms']<=timing['accepted_monotonic_ms']):raise ValueError('invalid creation time record')
        limits=timing['limits']
        if (not isinstance(limits,dict) or set(limits)!={'schema_version','call_ms','execution_ms','cleanup_ms'}
                or limits['schema_version']!='1.0' or any(type(limits[k]) is not int or not 0<limits[k]<=MAX_CALL_MS for k in ('call_ms','execution_ms','cleanup_ms'))
                or limits['cleanup_ms']>60000 or limits['call_ms']<=limits['cleanup_ms']):raise ValueError('invalid stored creation limits')
        if timing['deadline_monotonic_ms']-timing['accepted_monotonic_ms']>min(limits['execution_ms'],limits['call_ms']-limits['cleanup_ms']):
            raise ValueError('creation deadline exceeds accepted finite limits')
    resolution=op.get('reconciliation')
    if resolution is None:
        if op.get('schema_version')=='1.1' and timing is None:raise ValueError('missing creation context')
        return
    from xenova_vm import UUID
    if op['status']=='SUCCEEDED' or not isinstance(op.get('error'),dict):raise ValueError('reconciliation must preserve an original failed create')
    required={'version','request','context_sha256','plan','plan_sha256','phase','remaining','deleting','root_pending','error','release'}
    if (not isinstance(resolution,dict) or set(resolution)!=required or resolution['version']!=1
            or resolution['phase'] not in ('planned','cleaning','complete')
            or resolution['request'].get('purpose')!='discard-private'
            or not UUID.fullmatch(resolution['request'].get('reconciliation_id',''))
            or resolution['request'].get('vm_id')!=op['vm_id']
            or resolution['request'].get('expected_spec_sha256')!=op['spec_hash']
            or resolution['plan_sha256']!=digest(resolution['plan'])
            or resolution['context_sha256']!=resolution['request'].get('expected_context_sha256')
            or len(json.dumps(resolution))>MAX_BYTES):raise ValueError('invalid reconciliation record')
    plan=resolution['plan']
    if (set(plan)!={'root','items','absent','gate'} or not isinstance(plan['items'],dict) or len(plan['items'])>MAX_ITEMS
            or type(plan['absent']) is not bool or type(resolution['root_pending']) is not bool or not isinstance(resolution['remaining'],list)
            or len(set(resolution['remaining']))!=len(resolution['remaining'])
            or any(name not in plan['items'] for name in resolution['remaining'])
            or resolution['deleting'] is not None and resolution['deleting'] not in resolution['remaining']):raise ValueError('invalid deletion progress')
    if (plan['root'] is not None and (not isinstance(plan['root'],dict) or set(plan['root'])!={'dev','ino','directory'}
            or plan['root']['directory'] is not True or any(type(plan['root'][k]) is not int or plan['root'][k]<0 for k in ('dev','ino')))):
        raise ValueError('invalid private root identity')
    if plan['absent']!=(plan['root'] is None) or plan['absent'] and plan['items']:raise ValueError('invalid absent workspace proof')
    for name,item in plan['items'].items():
        if (not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts or len(Path(name).parts)>4
                or not isinstance(item,dict) or set(item)!={'dev','ino','directory'}
                or type(item['directory']) is not bool or any(type(item[k]) is not int or item[k]<0 for k in ('dev','ino'))):raise ValueError('invalid deletion inventory')
    if resolution['phase']=='complete' and (resolution['remaining'] or resolution['deleting'] is not None or resolution['root_pending'] or resolution['release'] is not True):
        raise ValueError('incomplete reservation release')
    if resolution['phase']!='complete' and resolution['release'] is not False:raise ValueError('premature reservation release')


@contextmanager
def work(m,op,context):
    timing=op.get('creation')
    if timing is None:fail('RECONCILE_REQUIRED','legacy incomplete create has no execution deadline; diagnose original operation')
    if timing['host_boot_id']!=m.p.boot_id_reader():fail('TARGET_CHANGED','original creation belongs to another host boot; no resume')
    limits=timing['limits']
    call_end=context.get('create_call_deadline',context['entered']+(context.get('budget_ms') or limits['call_ms'])/1000)
    end=min(timing['deadline_monotonic_ms']/1000,call_end-limits['cleanup_ms']/1000)
    check(m,end)
    with creation_work(end,m.p.clock,limits['cleanup_ms'],state=context):yield end


def released(m,op,*,durable=False):
    validate(op)
    resolution=op.get('reconciliation')
    if not resolution or resolution['phase']!='complete':return False
    root=m.data/op['owned_workspace']
    if root.exists() or root.is_symlink():fail('RECONCILE_REQUIRED','settled workspace unexpectedly exists')
    if durable:
        # 호출 writer는 기존 공통 실행 잠금을 보유한다. 읽기 조회는 이 동기화를 수행하지 않는다.
        path=m.data/'operations'/(op['operation_id']+'.json')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('unsafe reconciliation journal')
            os.fsync(fd)
        finally:os.close(fd)
        sync_directory(path.parent)
        if m._read(path)!=op:fail('TARGET_CHANGED','reconciliation changed during durability confirmation')
    return True


def holds(m,op):
    return op['status']!='SUCCEEDED' and not released(m,op,durable=True)


def validate_request(m,spec,key):
    from xenova_vm import UUID
    if key is not None:fail('INVALID_SPEC','reconciliation uses original selector and stable reconciliation_id')
    fields={'operation_id','idempotency_key','vm_id','expected_spec_sha256','purpose','expected_context_sha256','reconciliation_id','timeout_ms'}
    if (set(spec)-fields or len([k for k in ('operation_id','idempotency_key') if k in spec])!=1
            or not isinstance(spec.get('vm_id'),str) or not UUID.fullmatch(spec['vm_id']) or spec.get('purpose')!='discard-private'
            or not isinstance(spec.get('expected_spec_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',spec['expected_spec_sha256'])):fail('INVALID_SPEC','original create identity, spec hash and discard-private required')
    if 'expected_context_sha256' in spec and (not isinstance(spec['expected_context_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',spec['expected_context_sha256'])):
        fail('INVALID_SPEC','invalid diagnostic context hash')
    for name in ('operation_id','reconciliation_id'):
        if name in spec and (not isinstance(spec[name],str) or not UUID.fullmatch(spec[name])):fail('INVALID_SPEC','invalid reconciliation selector')
    if 'idempotency_key' in spec and not m._text_token(spec['idempotency_key']):fail('INVALID_SPEC','invalid original key')
    timeout=spec.get('timeout_ms',15000)
    if type(timeout) is not int or not 0<timeout<=105000:fail('INVALID_SPEC','reconciliation timeout_ms must be 1..105000')
    return {**spec,'timeout_ms':timeout}


def original(m,spec,deadline):
    check(m,deadline)
    field='operation_id' if 'operation_id' in spec else 'idempotency_key'
    found=[op for op in m._operations(deadline=deadline) if op.get(field)==spec[field]]
    if len(found)>1:fail('RECONCILE_REQUIRED','original creation selector is ambiguous')
    if not found:fail('VM_NOT_FOUND','original create not found')
    op=found[0]
    if op['action']!='create' or op['template']['guest_kind']!='linux' or op['vm_id']!=spec['vm_id'] or op['spec_hash']!=spec['expected_spec_sha256']:
        fail('TARGET_CHANGED','reconciliation does not match the original Linux create')
    return op


def identity(info):return {'dev':info.st_dev,'ino':info.st_ino,'directory':stat.S_ISDIR(info.st_mode)}


def inventory(m,op,deadline):
    root=m.data/op['owned_workspace'];parent=root.parent
    m._plain_dir(parent)
    if root.is_symlink():fail('ASSET_IN_USE','linked private workspace')
    if not root.exists():return {'root':None,'items':{},'absent':True,'gate':m.p.execution().barrier()}
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.geteuid():fail('ASSET_IN_USE','unsafe private workspace owner/mode')
    result={};name=op['assigned']['internal_name']
    allowed={'.preparation-resources.json','root',name}|{name+'/'+p for p in ('root.raw','Image','initrd','boot.json','kernel.config','instance.json')}
    def unreadable(error):raise error
    for base,dirs,files in os.walk(root,followlinks=False,onerror=unreadable):
        check(m,deadline)
        for child in dirs+files:
            path=Path(base)/child;relative=str(path.relative_to(root));value=path.lstat()
            if (relative not in allowed or value.st_dev!=info.st_dev or path.is_symlink() or value.st_uid!=os.geteuid()
                    or value.st_mode & 0o022 or not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode))
                    or stat.S_ISREG(value.st_mode) and value.st_nlink!=1):fail('ASSET_IN_USE','unexpected/shared/mounted private asset')
            if stat.S_ISDIR(value.st_mode) and relative not in ('root',name):fail('ASSET_IN_USE','unexpected private directory')
            result[relative]=identity(value)
            if len(result)>MAX_ITEMS:fail('RECONCILE_REQUIRED','private deletion inventory exceeded')
    return {'root':identity(info),'items':result,'absent':False,'gate':m.p.execution().barrier()}


def usage(m,op,plan,deadline):
    """저장한 inode까지 검사해 unlink 뒤 열린 파일을 정상 공간 반환으로 오인하지 않는다."""
    check(m,deadline)
    root=m.data/op['owned_workspace'];prefix=str(root)+'/'
    inodes={(v['dev'],v['ino']) for v in plan['items'].values()}
    if plan['root']:inodes.add((plan['root']['dev'],plan['root']['ino']))
    def owns(path):return str(path).removesuffix(' (deleted)')==str(root) or str(path).startswith(prefix)
    for row in m.p.domains(deadline=deadline):
        for disk in row.get('config',{}).get('disks',[]):
            if owns(disk.get('pdev_path','')):fail('ASSET_IN_USE','private workspace attached to Xen')
    for row in Path('/proc/self/mountinfo').read_text().splitlines():
        check(m,deadline);parts=row.split(' - ')
        if len(parts)!=2 or len(parts[0].split())<6:fail('OBSERVATION_UNAVAILABLE','mount inventory is incomplete')
        target=parts[0].split()[4]
        for encoded,decoded in ((r'\040',' '),(r'\011','\t'),(r'\012','\n'),(r'\134','\\')):target=target.replace(encoded,decoded)
        if owns(target):fail('ASSET_IN_USE','private workspace is mounted')
    loops=json.loads(m.p.timed_command(['losetup','--json','--list','--output','NAME,BACK-FILE'],deadline))
    if not isinstance(loops.get('loopdevices'),list):fail('OBSERVATION_UNAVAILABLE','loop inventory is incomplete')
    for row in loops['loopdevices']:
        if not isinstance(row.get('back-file'),str):fail('OBSERVATION_UNAVAILABLE','loop backing is unknown')
        if owns(row['back-file']):fail('ASSET_IN_USE','private workspace is a loop backing file')
    for process in Path('/proc').iterdir():
        check(m,deadline)
        if not process.name.isdigit():continue
        try:
            for descriptor in (process/'fd').iterdir():
                check(m,deadline)
                try:
                    value=descriptor.stat()
                    if (value.st_dev,value.st_ino) in inodes:
                        target=os.readlink(descriptor)
                        # 삭제돼 해제된 inode는 다음 수습의 다른 파일에 재사용될 수 있다.
                        # 원 private 참조 또는 아직 열린 unlinked inode는 계속 차단한다.
                        if owns(target) or value.st_nlink==0:fail('ASSET_IN_USE','private inode is held open')
                except FileNotFoundError:pass
        except FileNotFoundError:pass


def diagnose(m,op,deadline):
    if op['status']=='SUCCEEDED':fail('TARGET_CHANGED','completed create cannot be discarded as failed private assets')
    target=m.data/'instances'/op['assigned']['internal_name']
    if target.exists() or target.is_symlink() or op['vm_id'] in m._tombs():fail('TARGET_CHANGED','public/registered create cannot discard private assets')
    owner=m._owner_state(op)
    if owner in ('active','residual','reconciling'):fail('OPERATION_IN_PROGRESS','original preparation or reconciliation still owns this VM',retryable=True)
    plan=inventory(m,op,deadline)
    usage(m,op,plan,deadline)
    binding={k:op[k] for k in ('operation_id','vm_id','idempotency_key','spec_hash','assigned','owned_workspace','template')}
    return plan,digest({'original':binding,'host':m.p.boot_id_reader(),'plan':plan})


def reconcile(m,spec,execute,context,deadline,output):
    from xenova_vm import storage_check
    from xenova_execution import short_lock
    try:storage_check(m.data)
    except (OSError,ValueError,PlatformError):fail('STORAGE_UNAVAILABLE','creation storage cannot be verified')
    op=original(m,spec,deadline);context['operation_id']=op['operation_id']
    resolution=op.get('reconciliation')
    call_gate=m.p.execution().barrier()
    request_fields=('vm_id','expected_spec_sha256','purpose','expected_context_sha256','reconciliation_id')
    if execute and resolution and resolution['request']!={k:spec.get(k) for k in request_fields}:
        fail('IDEMPOTENCY_CONFLICT','another reconciliation request owns original create')
    if resolution and resolution['phase']=='complete':
        if execute:
            with operation_lock(m.p.runtime):released(m,op,durable=True)
        return {'operation':op,'effective_status':'FAILED','recovery_required':False,'reconciliation':resolution}
    if not execute:
        plan,fingerprint=diagnose(m,op,deadline)
        return {'operation_id':op['operation_id'],'vm_id':op['vm_id'],'spec_sha256':op['spec_hash'],
            'purpose':'discard-private','context_sha256':fingerprint,'private_items':len(plan['items']),
            'private_absent':plan['absent'],'reservation_released':False}
    from xenova_vm import UUID
    if (not UUID.fullmatch(spec.get('reconciliation_id','')) or len(spec.get('expected_context_sha256',''))!=64):
        fail('INVALID_SPEC','explicit reconciliation_id and diagnostic context hash required')
    request={k:spec[k] for k in request_fields}
    try:slot=output.stack.enter_context(LinuxSlot(m.p))
    except OperationBusy:
        fail('OPERATION_IN_PROGRESS','Linux preparation slot is busy; reconciliation not accepted',
             retryable=True,details={'accepted':False,'operation_id':op['operation_id']})
    try:
        if resolution:
            if resolution['request']!=request:fail('IDEMPOTENCY_CONFLICT','another reconciliation request owns original create')
            plan=resolution['plan']
            # 자기 진행 revision은 고정 수습 요청을 바꾸지 않는다. 원 실행 부재를 다시 확인한다.
            if m._owner_state(op)!='absent':fail('OPERATION_IN_PROGRESS','reconciliation owner remains')
            usage(m,op,plan,deadline)
        else:
            plan,fingerprint=diagnose(m,op,deadline)
            if fingerprint!=request['expected_context_sha256']:fail('TARGET_CHANGED','private context changed; diagnose again')
        with operation_lock(m.p.runtime):
            current=original(m,spec,deadline)
            if current!=op:fail('TARGET_CHANGED','original create changed before reconciliation acceptance')
            m._writable();slot.claim(op,role='discard-private',reconciliation_id=request['reconciliation_id'])
            if resolution is None:
                resolution={'version':1,'request':request,'context_sha256':request['expected_context_sha256'],
                    'plan':plan,'plan_sha256':digest(plan),'phase':'planned','remaining':list(plan['items']),
                    'deleting':None,'root_pending':False,'error':None,'release':False}
                op['schema_version']='1.1';op['reconciliation']=resolution
                if op['error'] is None:op['error']={'code':'RECOVERY_REQUIRED','message':'original create explicitly abandoned before completion','retryable':False,'details':{}}
                op['status']='RECOVERY_REQUIRED'
            # 이전 계획 저장의 fsync 효과 미확정도 재적용 전 같은 계획을 다시 내구 확정한다.
            m._save_op(op)
        root=m.data/op['owned_workspace'];saved=digest(op)
        def save():
            nonlocal saved
            check(m,deadline)
            with operation_lock(m.p.runtime):
                if digest(original(m,spec,deadline))!=saved:fail('TARGET_CHANGED','original changed while reconciliation owns it')
                m._save_op(op)
                saved=digest(op)
        # root 밖 원 journal과 안정 lock은 지우지 않는다. unlink는 직전 저장한 계획에만 한정한다.
        for name in sorted(plan['items'],key=lambda n:(len(Path(n).parts),n),reverse=True):
            if name not in resolution['remaining']:continue
            check(m,deadline)
            if m.p.execution().barrier()!=call_gate:fail('TARGET_CHANGED','host gate changed during reconciliation')
            m._writable()
            before=inventory(m,op,deadline)
            if before['root']!=plan['root']:fail('TARGET_CHANGED','private workspace inode changed')
            if any(n not in plan['items'] or v!=plan['items'][n] for n,v in before['items'].items()):fail('TARGET_CHANGED','private deletion inventory changed')
            expected=set(resolution['remaining'])
            if set(before['items']) not in (expected,expected-{resolution['deleting']}):
                fail('TARGET_CHANGED','private namespace no longer matches saved deletion progress')
            if name not in before['items']:
                if resolution['deleting']!=name:fail('TARGET_CHANGED','unexplained missing private asset')
                sync_directory((root/name).parent)
            else:
                usage(m,op,plan,deadline)
                resolution['phase']='cleaning';resolution['deleting']=name;save()
                m.checkpoint('reconcile-before-delete',op)
                fd=os.open(root,os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    if identity(os.fstat(fd))!=plan['root']:fail('TARGET_CHANGED','private root fd changed')
                    relative=[]
                    for part in Path(name).parts[:-1]:
                        child=os.open(part,os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
                        relative.append(part)
                        if identity(os.fstat(fd))!=plan['items'].get('/'.join(relative)):fail('TARGET_CHANGED','private parent inode changed')
                    value=os.stat(Path(name).name,dir_fd=fd,follow_symlinks=False)
                    if identity(value)!=plan['items'][name] or stat.S_ISREG(value.st_mode) and value.st_nlink!=1:fail('TARGET_CHANGED','private deletion inode changed')
                    with short_lock(m.p.runtime,'admission',deadline=deadline,clock=m.p.clock,sleep=m.p.sleep):
                        m._writable()
                        if m.p.execution().barrier()!=call_gate:fail('TARGET_CHANGED','host gate changed before private unlink')
                        check(m,deadline)
                        if plan['items'][name]['directory']:os.rmdir(Path(name).name,dir_fd=fd)
                        else:os.unlink(Path(name).name,dir_fd=fd)
                        os.fsync(fd)
                finally:os.close(fd)
                m.checkpoint('reconcile-after-delete',op)
            resolution['remaining'].remove(name);resolution['deleting']=None;save()
        check(m,deadline)
        if root.exists():
            if identity(root.lstat())!=plan['root']:fail('TARGET_CHANGED','private root changed')
            usage(m,op,plan,deadline)
            if list(root.iterdir()):fail('ASSET_IN_USE','private root is not empty')
            # root 삭제도 먼저 기록한다. 응답 유실 뒤 같은 계획으로만 부재를 재확인한다.
            resolution['root_pending']=True;save()
            with short_lock(m.p.runtime,'admission',deadline=deadline,clock=m.p.clock,sleep=m.p.sleep):
                m._writable()
                if m.p.execution().barrier()!=call_gate:fail('TARGET_CHANGED','host gate changed before private root removal')
                check(m,deadline)
                if identity(root.lstat())!=plan['root']:fail('TARGET_CHANGED','private root changed before removal')
                root.rmdir();sync_directory(root.parent)
        elif not plan['absent'] and (resolution['remaining'] or not resolution['root_pending']):
            fail('TARGET_CHANGED','private root disappeared before planned cleanup')
        sync_directory(root.parent)
        usage(m,op,plan,deadline)
        m.checkpoint('reconcile-before-complete',op)
        with operation_lock(m.p.runtime):
            if digest(original(m,spec,deadline))!=saved:fail('TARGET_CHANGED','original changed before completion')
            m._writable()
            if m.p.execution().barrier()!=call_gate:fail('TARGET_CHANGED','host gate changed before reconciliation completion')
            target=m.data/'instances'/op['assigned']['internal_name']
            if target.exists() or target.is_symlink() or op['vm_id'] in m._tombs():fail('TARGET_CHANGED','public creation appeared during cleanup')
            resolution.update(phase='complete',release=True,root_pending=False,error=None)
            m._save_op(op)
            released(m,op,durable=True)
        return {'operation':op,'effective_status':'FAILED','recovery_required':False,'reconciliation':resolution}
    except Exception as error:
        if resolution is not None:
            # 최초 create error는 불변이다. 완료 저장의 효과 미확정을 여기서 성공으로 만들지 않는다.
            resolution['error']={'cause_type':type(error).__name__}
            try:
                with operation_lock(m.p.runtime):
                    latest=original(m,spec,deadline)
                    if latest.get('reconciliation',{}).get('request')==request and latest['reconciliation']['phase']!='complete':
                        latest['reconciliation']['error']=resolution['error'];m._save_op(latest)
            except Exception:pass
        raise
