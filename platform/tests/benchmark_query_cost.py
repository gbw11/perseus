"""같은 작은 등록 fixture의 조회 비용. metadata 규모이며 동시 VM 수락/보드 시간 측정이 아니다."""
from collections import Counter
from contextlib import ExitStack, contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
from test_xenova_zephyr import bundle
from test_xenova_lifecycle import application_bundle
import xenova_vm as vm
import xenova_zephyr as zephyr
import xenova_management_cli as cli
import xenova_management as management


def measure(size,action):
    f=foundation.FoundationTest('runTest');f.setUp()
    try:
        # 완전한 제품 Zephyr metadata를 원형으로 쓴다. 비대상 boot payload 부재는
        # metadata 조회가 허용하는 기존 경계다. 이 숫자의 실제 VM 실행을 요청하지 않는다.
        source=application_bundle(bundle(f.p.data/'scale-bundle'),f.p.data/'scale-application')
        manifest=source/'firmware.json';meta=json.loads(manifest.read_text())
        meta.update(id='scale-firmware',kind='zephyr-xen-firmware',version='1.0.0')
        manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
        prototype=zephyr.new_instance(f.p.data,'scale-prototype',source,{},identity=foundation.HOST_B)
        (f.p.data/'instances/scale-prototype/instance.json').rename(f.p.data/'scale-prototype.json')
        # 일반 등록부의 빈 디렉터리를 남기지 않는다. 방금 만든 소유 fixture만 이동한다.
        (f.p.data/'instances/scale-prototype').rename(f.p.data/'scale-boot-assets')
        for i in range(size-1):
            key='scale-'+str(i);directory=f.p.data/'instances'/key;directory.mkdir()
            record={**prototype,'id':key,'name':key,'uuid':'950e8400-e29b-41d4-a716-'+f'{i:012x}'}
            (directory/'instance.json').write_text(json.dumps(record))
        if action in ('control-operation','recovery-operation'):f.start('cost-key')
        if action=='recovery-operation':
            attempt=f.spec();assert f.recover(attempt)['ok']
        counts=Counter();lock_seconds=Counter()
        read=Path.read_text;loads=json.loads;sync=os.fsync;command=f.p.command;host=f.p.boot_id_reader
        def measured_lock(name,original):
            @contextmanager
            def acquire(*args,**kwargs):
                counts[name+'_attempts']+=1;began=time.perf_counter()
                with original(*args,**kwargs) as lease:
                    entered=time.perf_counter();lock_seconds[name+'_wait']+=entered-began
                    try:yield lease
                    finally:lock_seconds[name+'_hold']+=time.perf_counter()-entered
            return acquire
        def text(path,*a,**kw):
            value=read(path,*a,**kw);counts['text_reads']+=1
            if path.name=='instance.json':counts['registry_reads']+=1
            if path.parent.name in ('operations','control-requests','recovery-attempts'):counts['journal_reads']+=1
            if path.name=='template.json':counts['template_reads']+=1
            if path.parent.name=='control':counts['intent_reads']+=1
            return value
        def parse(*a,**kw):counts['json_parses']+=1;return loads(*a,**kw)
        def fsync(fd):counts['fsync']+=1;return sync(fd)
        def external(args,**kw):
            counts['external_commands']+=1
            if args[0]=='sha256sum':counts['payload_hashes']+=1
            return command(args,**kw)
        def boot():counts['host_identity_reads']+=1;return host()
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,'read_text',text));stack.enter_context(patch.object(json,'loads',parse))
            stack.enter_context(patch.object(os,'fsync',fsync));stack.enter_context(patch.object(f.p,'command',external))
            stack.enter_context(patch.object(f.p,'boot_id_reader',boot))
            stack.enter_context(patch.object(management,'operation_lock',measured_lock('common_lock',management.operation_lock)))
            stack.enter_context(patch.object(vm,'operation_lock',measured_lock('common_lock',vm.operation_lock)))
            stack.enter_context(patch.object(management,'observation_slot',measured_lock('observation_slot',management.observation_slot)))
            began=time.perf_counter()
            if action=='status':
                with (patch.object(cli,'Manager',return_value=f.m),patch.object(vm,'Platform',return_value=f.p),
                      redirect_stdout(io.StringIO())):result=vm.main(['status'])
            else:
                spec=({'vm_id':f.vm_id,'timeout_ms':1000} if action in ('get','probe') else
                      {'vm_id':f.vm_id} if action=='execution-context' else
                      {'idempotency_key':'cost-key'} if action=='control-operation' else
                      {'recovery_attempt_id':attempt['recovery_attempt_id']} if action=='recovery-operation' else {})
                result=f.m.call(action,spec)
            elapsed=time.perf_counter()-began
        ok=result.get('ok',True) if isinstance(result,dict) else result is None
        return {'metadata_vms':size,'action':action,'ok':ok,'counts':dict(counts),
                'lock_seconds':dict(lock_seconds),'elapsed_seconds':elapsed}
    finally:f.doCleanups()


if __name__=='__main__':
    actions=sys.argv[1:] or ['list','get','probe','execution-context','templates','control-operation','recovery-operation','status','intents']
    print(json.dumps([measure(n,a) for n in (1,10,100) for a in actions],indent=2))
