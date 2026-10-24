#!/usr/bin/env python3
"""실제 Manager/Platform의 CPU-on Zephyr reboot를 고정 FakeXen으로 비교한다.

--source-root로 보존한 기준 소스 또는 현재 소스를 선택한다. 외부 명령과 fsync의
가상 비용은 동일한 입력 모델일 뿐 실보드 지연/90초 원인의 측정값이 아니다.
모든 Xen·저장소 자산은 기존 테스트 fixture의 임시 디렉터리에만 생성한다.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager, redirect_stdout
from functools import wraps
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import time
from unittest.mock import patch


def run(source_root, repeats, command_seconds, fsync_seconds):
    sys.path.insert(0, str(source_root/'platform/tests'))
    import test_xenova_cpu as fixture
    import xenova_runtime as runtime
    import xenova_cpu as cpu
    import xenova_execution as execution
    import xenova_vm as vm
    result = []
    for repeat in range(repeats):
        case = fixture.CPUTest('runTest')
        case.setUp()
        try:
            current = case.ok(case.m.call('get', {'vm_id':case.zid}))['vm']
            spec = {'vm_id':case.zid, 'bundle':str(case.fw),
                    'manifest_sha256':fixture.zephyr.sha(case.fw/'firmware.json'),
                    'expected_lifecycle_generation':current['generation'],
                    'expected_configuration_sha256':current['configuration_sha256'],
                    'board_shutdown_policy':'graceful'}
            case.ok(case.m.call('firmware-transition', spec, execute=True, idempotency_key='benchmark-fw'))
            case.z = case.p.records(validate_assets=False)[case.z['id']]
            case.xen.records[case.z['id']] = case.z
            case.activate()
            case.ok(case.control(case.z, 'start', 'benchmark-start'))
            command_counts, io_counts, summaries, storage_costs = Counter(), Counter(), {}, {}
            write_destinations, write_events = Counter(), []
            write_impl = execution.Execution.write
            def execution_write(owner, path, value):
                """저장 payload 없이 journal 종류·단계별 실제 내구 저장을 구분한다."""
                location = ('runtime/' if path.is_relative_to(owner.runtime) else 'data/') + path.parent.name
                result = write_impl(owner, path, value)
                write_destinations[location] += 1
                write_events.append({'destination':location, 'phase':value.get('phase'),
                    'reboot_stage':value.get('reboot_stage'),
                    'cpu_verification':value.get('cpu_verification')})
                return result
            command_impl = case.p.command
            fsync_impl = runtime.os.fsync
            replace_impl = runtime.os.replace
            def command(argv, **kwargs):
                name = argv[0] + ('.'+argv[1] if argv[0]=='xl' and len(argv)>1 else '')
                if argv[:2] == ['xl','list']:
                    name += '.' + ('detail' if '-l' in argv else 'pool' if '-c' in argv else 'short')
                command_counts[name] += 1
                case.ticks += command_seconds
                return command_impl(argv, **kwargs)
            def fsync(fd):
                name = 'directory_fsync' if stat.S_ISDIR(runtime.os.fstat(fd).st_mode) else 'file_fsync'
                io_counts[name] += 1
                before, wall = case.ticks, time.perf_counter()
                try:
                    case.ticks += fsync_seconds
                    return fsync_impl(fd)
                finally:
                    cost = storage_costs.setdefault(name, {'model_seconds':0.0, 'wall_seconds':0.0})
                    cost['model_seconds'] += case.ticks-before
                    cost['wall_seconds'] += time.perf_counter()-wall
            def replace(*args, **kwargs):
                io_counts['replace'] += 1
                wall = time.perf_counter()
                try:
                    return replace_impl(*args, **kwargs)
                finally:
                    storage_costs.setdefault('replace', {'wall_seconds':0.0})['wall_seconds'] += time.perf_counter()-wall
            stack = []
            @contextmanager
            def lock(*args, **kwargs):
                before, wall_before = case.ticks, time.perf_counter()
                with lock_impl(*args, **kwargs):
                    acquired, wall_acquired = case.ticks, time.perf_counter()
                    wait = summaries.setdefault('lock.wait', {'calls':0,'model_seconds':0.0,'wall_seconds':0.0})
                    wait['calls'] += 1
                    wait['model_seconds'] += acquired-before
                    wait['wall_seconds'] += wall_acquired-wall_before
                    try:
                        yield
                    finally:
                        hold = summaries.setdefault('lock.hold', {'calls':0,'model_seconds':0.0,'wall_seconds':0.0})
                        hold['calls'] += 1
                        hold['model_seconds'] += case.ticks-acquired
                        hold['wall_seconds'] += time.perf_counter()-wall_acquired
            def measured(owner, attribute, name):
                original = getattr(owner, attribute)
                @wraps(original)
                def wrapper(*args, **kwargs):
                    frame = [case.ticks, time.perf_counter(), 0.0, 0.0]
                    stack.append(frame)
                    try:
                        return original(*args, **kwargs)
                    finally:
                        virtual = case.ticks-frame[0]; wall = time.perf_counter()-frame[1]
                        stack.pop()
                        if stack:
                            stack[-1][2] += virtual;stack[-1][3] += wall
                        entry = summaries.setdefault(name, {'calls':0,'model_inclusive_seconds':0.0,
                            'model_exclusive_seconds':0.0,'wall_inclusive_seconds':0.0,'wall_exclusive_seconds':0.0})
                        entry['calls'] += 1
                        entry['model_inclusive_seconds'] += virtual
                        entry['model_exclusive_seconds'] += max(0.0,virtual-frame[2])
                        entry['wall_inclusive_seconds'] += wall
                        entry['wall_exclusive_seconds'] += max(0.0,wall-frame[3])
                return patch.object(owner, attribute, wrapper)
            with ExitStack() as patches:
                lock_impl = runtime.operation_lock
                # Modules bind operation_lock on import; patch each existing binding
                # to the same real context manager, without replacing lock behavior.
                for module in tuple(sys.modules.values()):
                    if getattr(module, '__name__', '').startswith('xenova_') and getattr(module, 'operation_lock', None) is lock_impl:
                        patches.enter_context(patch.object(module, 'operation_lock', lock))
                patches.enter_context(patch.object(case.p, 'command', command))
                patches.enter_context(patch.object(runtime.os, 'fsync', fsync))
                patches.enter_context(patch.object(runtime.os, 'replace', replace))
                patches.enter_context(patch.object(execution.Execution, 'write', execution_write))
                for owner, attribute, name in ((cpu.CPU,'sample','cpu.sample'),
                        (cpu.CPU,'observe','cpu.observe'),(cpu.CPU,'require','cpu.require'),
                        (execution.Execution,'observe','execution.observe'),
                        (execution.Execution,'prepare_launch','execution.prepare_launch'),
                        (execution.Execution,'bind_launch','execution.bind_launch'),
                        (vm.Platform,'domains','platform.domains')):
                    patches.enter_context(measured(owner,attribute,name))
                before = case.ticks; wall = time.perf_counter()
                response = case.control(case.z, 'reboot', 'benchmark-reboot')
                elapsed = case.ticks-before; wall = time.perf_counter()-wall
            case.ok(response)
            result.append({'repeat':repeat+1, 'ok':response['ok'],
                'model_elapsed_seconds':elapsed,'wall_elapsed_seconds':wall,
                'command_counts':dict(sorted(command_counts.items())),
                'storage_counts':dict(io_counts), 'storage_costs':storage_costs, 'method_costs':summaries,
                'execution_write_destinations':dict(write_destinations), 'execution_write_events':write_events,
                'trace':getattr(case.m,'last_trace', None),
                'result_fields':sorted(response['data']['vm']),
                'physical_reboots':command_counts['xl.reboot'],'physical_creates':command_counts['xl.create']})
        finally:
            case.doCleanups()
    return {'schema_version':'1.0','scenario':'cpu-enabled-cooperative-zephyr-reboot',
        'source_root':str(source_root),'source_sha256':{
            str(p.relative_to(source_root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source_root/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files').glob('*.py'))},
        'model':{'command_seconds':command_seconds,'fsync_seconds':fsync_seconds,
                 'note':'Synthetic latency model; actual filesystem writes/fsync remain enabled; no board result.'},
        'domain_fixture':'dom0 + one active network Zephyr; second reserved Zephyr and Linux registered/stopped',
        'samples':result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--command-seconds',type=float,default=0.01)
    parser.add_argument('--fsync-seconds',type=float,default=0.001)
    args=parser.parse_args()
    if not 1<=args.repeats<=10 or not 0<=args.command_seconds<=0.02 or not 0<=args.fsync_seconds<=0.01:
        parser.error('bounded repeat/latency parameters required')
    with redirect_stdout(io.StringIO()):
        report=run(args.source_root.resolve(),args.repeats,args.command_seconds,args.fsync_seconds)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'repeats':len(report['samples']),
                      'commands':[sum(s['command_counts'].values()) for s in report['samples']]}))


if __name__=='__main__':
    main()
