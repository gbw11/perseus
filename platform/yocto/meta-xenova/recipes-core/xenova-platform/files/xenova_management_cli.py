"""명령·명시 옵션을 검증하고 한 제품 결과만 렌더링한다. 출력은 실행 경로를 고르지 않는다."""
import argparse
import json
from pathlib import Path
import sys
import time
from first_install_contract import uuid4
from xenova_management import Manager, ManagementError, exit_code, normalize_uuid
from xenova_runtime import OperationBusy
from xenova_creation import MAX_CALL_MS
import xenova_vm as vm

ALIASES = {'delete':'retire', 'recover-once':'recover_once', 'status':'list'}
LEGACY = {'check-xen','check-network','dhcp-ready','autostart','shutdown-all','poweroff',
          'reboot-board','console','firmware-replace','diagnose','host-init','host-quiesce'}
TARGETS = {'get','intents','provision','provisioning','retire','restore','purge','start','stop','reboot','destroy',
           'autostart-set','board-shutdown-set','config','snapshot','probe','execution-context',
           'firmware-transition','update-resources','cpu-layout'}
QUERIES = {'get','list','intents','provisioning','probe','execution-context','operation','control-operation','recovery-operation'}
BUDGETED = QUERIES | {'provision','templates','create','operation-reconcile','start','stop','reboot','destroy','recovery-reconcile'}
PLANNED = {'create','retire','restore','purge','firmware-transition','update-resources','recover_once',
           'provision','cpu-policy-set','cpu-host-prepare','operation-reconcile','recovery-reconcile','firmware-replace'}
# 생략한 기본값이 아니라 명시한 옵션의 허용 범위다.
OPTIONS = {
    'create': {'vm_id','name','template_id','template_version','vcpus','memory_mib','ipv4','auto_ip','idempotency_key'},
    'list': {'lifecycle'}, 'get': {'vm_id'}, 'intents': {'vm_id','lifecycle'},
    'provision': {'vm_id'}, 'provisioning': {'vm_id'},
    'operation': {'operation_id','idempotency_key'},
    'control-operation': {'operation_id','idempotency_key','vm_id'},
    'recovery-operation': {'recovery_attempt_id'}, 'recovery-reconcile': {'recovery_attempt_id'},
    'recover_once': {'spec_file'}, 'cpu-policy-set': {'spec_file','idempotency_key'},
    'operation-reconcile': {'spec_file'}, 'cpu-layout': {'vm_id'}, 'cpu-host-prepare': {'restore'},
    'host-resume': {'barrier_id'}, 'snapshot': {'vm_id','max_age_ms'},
    'firmware-transition': {'vm_id','bundle','sha256','board_shutdown_policy','expected_lifecycle_generation','expected_configuration_sha256','idempotency_key'},
    'update-resources': {'vm_id','vcpus','memory_mib','expected_lifecycle_generation','expected_configuration_sha256','idempotency_key'},
    'purge': {'vm_id','confirm_vm_id','idempotency_key'},
    'retire': {'vm_id','idempotency_key'}, 'restore': {'vm_id','idempotency_key'},
    'start': {'vm_id','idempotency_key'},
    'stop': {'vm_id','idempotency_key','force'}, 'reboot': {'vm_id','idempotency_key','force'},
    'destroy': {'vm_id','idempotency_key','force'},
    'autostart-set': {'vm_id'}, 'board-shutdown-set': {'vm_id'}, 'config': {'vm_id'},
    'probe': {'vm_id'}, 'execution-context': {'vm_id'}, 'templates': set(),
    **{action:set() for action in LEGACY},
}
OPTIONS.update({'firmware-replace':{'bundle','sha256'}, 'diagnose':{'details'},
                **{action:{'force'} for action in ('shutdown-all','poweroff','reboot-board')}})

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ManagementError('INVALID_SPEC', message)

class Once(argparse.Action):
    """같은 값의 반복은 허용하되 서로 다른 명시값을 마지막 값으로 덮지 않는다."""
    def __call__(self, parser, namespace, value, option_string=None):
        if hasattr(namespace,self.dest) and getattr(namespace,self.dest)!=value:
            parser.error('conflicting repeated option: '+option_string)
        setattr(namespace,self.dest,value)

def _parser():
    parser=Parser(description=__doc__,argument_default=argparse.SUPPRESS,allow_abbrev=False,
                  epilog='status=list; intents=stored intent only; diagnose --details=asset/time diagnostics. '
                         'Options are checked per action. --json changes formatting only.')
    parser.add_argument('action',choices=sorted(set(OPTIONS)|set(ALIASES)))
    parser.add_argument('id',nargs='?')
    parser.add_argument('value',nargs='?')
    output=parser.add_mutually_exclusive_group()
    output.add_argument('--json',action='store_true')
    output.add_argument('--short',action='store_true',help='list/status: NAME, INTERNAL_NAME, UUID, STATE and IP')
    for name in ('execute','force','restore','details','autostart'):
        parser.add_argument('--'+name,action='store_true')
    for name in ('name','vm-id','template-version','board-shutdown-policy','sha256',
                 'expected-configuration-sha256','ipv4','idempotency-key','operation-id',
                 'confirm-vm-id','lifecycle','recovery-attempt-id','barrier-id'):
        parser.add_argument('--'+name,action=Once)
    parser.add_argument('--template-id','--template',dest='template_id',action=Once)
    for name in ('vcpus','memory-mib','expected-lifecycle-generation','max-age-ms','timeout-ms','budget-ms'):
        parser.add_argument('--'+name,type=int,action=Once)
    for name in ('bundle','spec-file'):
        parser.add_argument('--'+name,type=Path,action=Once)
    parser.add_argument('--auto-ip',action='store_true',help='create: use platform automatic allocation')
    return parser

def _validate(args):
    action=ALIASES.get(args['action'],args['action'])
    allowed=OPTIONS[action]|{'action','json','short'}
    if action in TARGETS or action in ('create','console','firmware-replace','dhcp-ready'):allowed.add('id')
    if action in ('autostart-set','board-shutdown-set'):allowed.add('value')
    if action in PLANNED:allowed.add('execute')
    if action in BUDGETED:allowed.add('budget_ms')
    if action in QUERIES or action in ('provision','cpu-layout','cpu-host-prepare','recovery-reconcile'):allowed.add('timeout_ms')
    unexpected=set(args)-allowed
    if unexpected:
        raise ManagementError('INVALID_SPEC','unsupported arguments for '+action+': '+', '.join(sorted(unexpected)))
    if args.get('short') and action!='list':
        raise ManagementError('INVALID_SPEC','--short is only supported for list/status')
    if action in LEGACY and action!='diagnose' and args.get('json'):
        raise ManagementError('INVALID_SPEC',action+' does not support --json; it keeps its boot/PTY output contract')
    if args.get('auto_ip') and 'ipv4' in args:
        raise ManagementError('INVALID_SPEC','--auto-ip and --ipv4 are mutually exclusive')
    if 'timeout_ms' in args and not 0<args['timeout_ms']<=15000:
        raise ManagementError('INVALID_SPEC','timeout_ms must be 1..15000')
    if 'budget_ms' in args and not 0<args['budget_ms']<=(MAX_CALL_MS if action=='create' else 105000):
        raise ManagementError('INVALID_SPEC','budget_ms is outside the supported call range')
    if action in ('control-operation','operation') and sum(k in args for k in ('operation_id','idempotency_key'))!=1:
        raise ManagementError('INVALID_SPEC','exactly one operation ID or original key is required')
    if action=='destroy' and not args.get('force'):
        raise ManagementError('INVALID_SPEC','destroy requires --force')
    if action in ('autostart-set','board-shutdown-set'):
        choices=('on','off') if action=='autostart-set' else ('graceful','refuse','force-allowed')
        if args.get('value') not in choices:raise ManagementError('INVALID_SPEC','invalid policy value')
    if action in ('recover_once','cpu-policy-set','operation-reconcile') and 'spec_file' not in args:
        raise ManagementError('INVALID_SPEC',action+' requires --spec-file')
    if action in ('console','firmware-replace') and not vm.ID.fullmatch(args.get('id','')):
        raise ManagementError('INVALID_SPEC',action+' requires a registered internal name')
    if action=='firmware-replace' and ('bundle' not in args or not args['bundle'].is_absolute()
            or '..' in args['bundle'].parts or not vm.zephyr.DIGEST.fullmatch(args.get('sha256',''))):
        raise ManagementError('INVALID_SPEC','firmware-replace requires an absolute bundle and manifest SHA256')
    if action=='dhcp-ready' and (not args.get('id','').isdigit() or int(args['id'])<1):
        raise ManagementError('INVALID_SPEC','dhcp-ready requires a positive process ID')
    return action

def _target(service, values, deadline):
    """UUID는 바로 사용하고 내부 이름은 같은 예산으로 한 번만 등록부를 읽는다."""
    records=None
    def resolve(value):
        nonlocal records
        try:return normalize_uuid(value)
        except ManagementError:
            if not isinstance(value,str) or not vm.ID.fullmatch(value):raise
        if service.p.clock()>=deadline:raise ManagementError('DEADLINE_EXCEEDED','target lookup budget expired')
        if records is None:
            vm.storage_check(service.data)
            records=vm.load_records(service.data,validate_assets=False,deadline=deadline,clock=service.p.clock)
        if service.p.clock()>=deadline:raise ManagementError('DEADLINE_EXCEEDED','target lookup budget expired')
        if value not in records:raise ManagementError('VM_NOT_FOUND','registered internal name not found; archived targets require UUID')
        return records[value]['uuid']
    identities=[resolve(v) for v in values]
    if len(set(identities))!=1:raise ManagementError('INVALID_SPEC','positional target and --vm-id select different VMs')
    return identities[0]

def _spec(service, action, args, deadline):
    if 'spec_file' in args:
        try:raw=args['spec_file'].read_text(encoding='utf-8')
        except OSError:raise ManagementError('STORAGE_UNAVAILABLE','cannot read spec file')
        except UnicodeError:raise ManagementError('INVALID_SPEC','spec file must be UTF-8')
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ManagementError('INVALID_SPEC','duplicate spec field: '+key)
                result[key]=value
            return result
        def invalid_constant(value):raise ManagementError('INVALID_SPEC','non-finite JSON number')
        try:return json.loads(raw,object_pairs_hook=unique,parse_constant=invalid_constant)
        except ValueError:raise ManagementError('INVALID_SPEC','spec file must contain valid JSON')
    spec={}
    if action=='create':
        spec={k:args[k] for k in ('vm_id','template_id','template_version','vcpus','memory_mib','ipv4') if k in args}
        if 'name' in args and 'id' in args and args['name']!=args['id']:
            raise ManagementError('INVALID_SPEC','positional name and --name differ')
        spec['name']=args.get('name',args.get('id'))
        if 'idempotency_key' in args:
            identity=normalize_uuid(args.get('vm_id'))
            if args['idempotency_key']!='create:'+identity:
                raise ManagementError('INVALID_SPEC','create key is fixed to create:<VM UUID>; omit the option or use that key')
    elif action in TARGETS:
        values=[args[k] for k in ('id','vm_id') if k in args]
        if values:spec['vm_id']=_target(service,values,deadline)
        elif action not in ('intents','cpu-layout'):spec['vm_id']=None
        if action in ('autostart-set','board-shutdown-set'):spec['value']=args['value']
    for name in ('lifecycle','operation_id','recovery_attempt_id','confirm_vm_id','barrier_id','max_age_ms'):
        if name in args:spec[name]=args[name]
    if action in QUERIES|{'provision'} and 'timeout_ms' in args:spec['timeout_ms']=args['timeout_ms']
    if action=='control-operation' and 'vm_id' in args:spec['vm_id']=normalize_uuid(args['vm_id'])
    if action in ('operation','control-operation') and 'idempotency_key' in args:spec['idempotency_key']=args['idempotency_key']
    if action in ('stop','reboot','destroy'):spec['force']=args.get('force',False)
    if action in ('cpu-layout','cpu-host-prepare','probe','recovery-reconcile'):spec.setdefault('timeout_ms',args.get('timeout_ms',15000))
    if action=='cpu-host-prepare':spec['restore']=args.get('restore',False)
    if action in ('firmware-transition','update-resources'):
        spec.update({k:args.get(k) for k in ('expected_lifecycle_generation','expected_configuration_sha256')})
        if action=='update-resources':spec.update({k:args.get(k) for k in ('vcpus','memory_mib')})
        else:spec.update(bundle=str(args['bundle']) if 'bundle' in args else None,
                         manifest_sha256=args.get('sha256'),board_shutdown_policy=args.get('board_shutdown_policy'))
    return spec

def _short_list(vms):
    rows=['NAME | INTERNAL_NAME | UUID | STATE | IP']
    for item in vms:
        configuration=item.get('configuration') or {}
        values=(item.get('name'),configuration.get('id'),item.get('vm_id'),item.get('xen_state'),item.get('ipv4'))
        rows.append(' | '.join('-' if value is None else str(value) for value in values))
    return '\n'.join(rows)

def main(argv=None, manager=None):
    clock=getattr(getattr(manager,'p',None),'clock',time.monotonic)
    entered=clock()
    request_id=uuid4();args={}
    try:
        args=vars(_parser().parse_intermixed_args(sys.argv[1:] if argv is None else argv))
        action=_validate(args)
        if action in LEGACY:
            result=vm.legacy_command(action,args,platform=manager.p if manager is not None else None)
            if result is not None:
                try:print(json.dumps(result,ensure_ascii=False,indent=None if args.get('json') else 2))
                except BrokenPipeError:raise SystemExit(1) from None
            return result
        service=manager or Manager()
        if manager is None:
            clock=getattr(getattr(service,'p',None),'clock',time.monotonic)
            entered=clock()-(time.monotonic()-entered)
        default=90000 if action in ('start','stop','reboot','destroy') else args.get('timeout_ms',15000)
        deadline=entered+args.get('budget_ms',default)/1000
        spec=_spec(service,action,args,deadline)
        options={}
        if action in BUDGETED and ('budget_ms' in args or action in QUERIES or action in ('provision','start','stop','reboot','destroy')):
            left=int((deadline-clock())*1000)
            if left<=0:raise ManagementError('DEADLINE_EXCEEDED','CLI call budget expired before submission')
            options['budget_ms']=left
        key=None if action in ('operation','control-operation') else args.get('idempotency_key')
        result=service.call(action,spec,execute=args.get('execute',False),idempotency_key=key,request_id=request_id,**options)
    except ManagementError as exc:
        result={'schema_version':'1.0','ok':False,'request_id':request_id,'operation_id':None,
                'replayed':False,'data':None,'error':exc.error}
    except (OSError,vm.PlatformError) as exc:
        error=getattr(exc,'error',None) or {
            'code':'OPERATION_IN_PROGRESS' if isinstance(exc,OperationBusy) else
                   'STORAGE_UNAVAILABLE' if isinstance(exc,OSError) else 'ARTIFACT_INVALID',
            'message':str(exc),'retryable':isinstance(exc,OperationBusy),'details':{}}
        result={'schema_version':'1.0','ok':False,'request_id':request_id,'operation_id':None,
                'replayed':False,'data':None,'error':error}
    try:
        print(_short_list(result['data']['vms']) if args.get('short') and result['ok'] else
              json.dumps(result,ensure_ascii=False,indent=None if args.get('json') else 2))
    except BrokenPipeError:
        raise SystemExit(1) from None  # 출력 실패로 원 작업을 재실행하거나 취소하지 않는다.
    code=exit_code(result)
    if code:raise SystemExit(code)
    return result
