"""공통 관리 결과를 단일 JSON 또는 사람용 JSON으로 출력하는 CLI 어댑터."""
import argparse
import json
from pathlib import Path
import sys
from first_install_contract import uuid4
from xenova_management import Manager, ManagementError, exit_code, normalize_uuid
import xenova_vm as vm


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ManagementError('INVALID_SPEC', message)


def main(argv=None, manager=None):
    args_list=list(sys.argv[1:] if argv is None else argv)
    request_id=uuid4()
    service=manager or Manager()
    parser=Parser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('id',nargs='?')
    parser.add_argument('value',nargs='?')
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--board-shutdown-policy')
    parser.add_argument('--name')
    parser.add_argument('--vm-id')
    parser.add_argument('--template-id','--template',dest='template_id')
    parser.add_argument('--template-version')
    parser.add_argument('--expected-lifecycle-generation',type=int)
    parser.add_argument('--expected-configuration-sha256')
    parser.add_argument('--vcpus',type=int)
    parser.add_argument('--memory-mib',type=int)
    ip = parser.add_mutually_exclusive_group()
    ip.add_argument('--ipv4')
    ip.add_argument('--auto-ip', action='store_true', help='Linux IP is selected and durably reserved by Xenova')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--operation-id')
    parser.add_argument('--confirm-vm-id')
    parser.add_argument('--lifecycle',default='REGISTERED')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--restore',action='store_true',help='internal CPU host preparation rollback')
    parser.add_argument('--spec-file', type=Path)
    parser.add_argument('--recovery-attempt-id')
    parser.add_argument('--barrier-id')
    parser.add_argument('--max-age-ms', type=int)
    parser.add_argument('--timeout-ms', type=int, default=15000)
    try:
        args=parser.parse_args(args_list)
        action='recover_once' if args.action == 'recover-once' else args.action
        spec={}
        if action in ('recover_once', 'cpu-policy-set'):
            if args.spec_file is None:
                raise ManagementError('INVALID_SPEC', 'recover-once requires --spec-file')
            spec = json.loads(args.spec_file.read_text())
        elif action == 'cpu-layout':
            spec = {'timeout_ms':args.timeout_ms}
            if args.vm_id or args.id:
                spec['vm_id'] = args.vm_id or args.id
        elif action == 'cpu-host-prepare':
            spec = {'timeout_ms':args.timeout_ms, 'restore':args.restore}
        elif action == 'host-resume':
            spec = {'barrier_id':args.barrier_id}
        elif action == 'control-operation':
            spec = {'operation_id':args.operation_id}
        elif action == 'recovery-operation':
            spec = {'recovery_attempt_id':args.recovery_attempt_id}
        elif action=='create':
            spec={'vm_id':args.vm_id, 'name':args.name or args.id,'template_id':args.template_id,
                  'template_version':args.template_version,'vcpus':args.vcpus,'memory_mib':args.memory_mib}
            if args.ipv4 is not None:
                spec['ipv4']=args.ipv4
        elif action in ('firmware-transition','update-resources','get','retire','restore','purge','delete','start','stop','reboot','autostart-set','board-shutdown-set','config','destroy','snapshot','probe','execution-context'):
            identity=args.vm_id or args.id
            if identity:
                try:
                    identity = normalize_uuid(identity)
                except ManagementError:
                    # 사람이 쓰는 기존 안전한 내부 이름은 계속 조회할 수 있다.
                    records=service.p.records(validate_assets=False)
                    if identity not in records:
                        raise ManagementError('VM_NOT_FOUND','registered internal name not found; use UUID for archived VMs')
                    identity=records[identity]['uuid']
            spec={'vm_id':identity}
            if action == 'firmware-transition':
                spec.update(bundle=str(args.bundle) if args.bundle else None,manifest_sha256=args.sha256,
                            expected_lifecycle_generation=args.expected_lifecycle_generation,
                            expected_configuration_sha256=args.expected_configuration_sha256,
                            board_shutdown_policy=args.board_shutdown_policy)
            if action == 'update-resources':
                spec.update(vcpus=args.vcpus,memory_mib=args.memory_mib,
                            expected_lifecycle_generation=args.expected_lifecycle_generation,
                            expected_configuration_sha256=args.expected_configuration_sha256)
            if action == 'snapshot':
                spec['max_age_ms']=args.max_age_ms
            if action == 'probe':
                spec['timeout_ms']=args.timeout_ms
            if action in ('stop','reboot','destroy'):
                spec['force']=args.force
            if action in ('autostart-set','board-shutdown-set'):
                spec['value']=args.value
            if action=='delete':
                action='retire'
            if action=='purge':
                spec['confirm_vm_id']=args.confirm_vm_id
        elif action=='list':
            spec={'lifecycle':args.lifecycle}
        elif action=='operation':
            spec={'operation_id':args.operation_id,'idempotency_key':args.idempotency_key}
        elif action=='status':
            action='list'
        result=service.call(action,spec,execute=args.execute,idempotency_key=args.idempotency_key,request_id=request_id)
    except ManagementError as exc:
        result={'schema_version':'1.0','ok':False,'request_id':request_id,'operation_id':None,
                'replayed':False,'data':None,'error':exc.error}
    except (OSError,ValueError,vm.PlatformError):
        result={'schema_version':'1.0','ok':False,'request_id':request_id,'operation_id':None,
                'replayed':False,'data':None,'error':{'code':'STORAGE_UNAVAILABLE',
                'message':'cannot resolve persistent VM identity','retryable':False,'details':{}}}
    print(json.dumps(result,ensure_ascii=False,indent=None if '--json' in args_list else 2))
    code=exit_code(result)
    if code:
        raise SystemExit(code)
    return result
