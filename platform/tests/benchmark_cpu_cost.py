"""같은 CPU 2.0 fixture의 실제 파일 파싱·원장 스캔·전체 Xen 관측 계수."""
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch
import test_xenova_cpu as fixture
import test_xenova_recovery_foundation as foundation
from test_xenova_cpu_cross import CPUCrossTest
from xenova_cpu import CPU


def one(action,n,pinned):
    test=CPUCrossTest('runTest');c=test.case()
    try:
        test.resize(c,n,'setup-resource')
        c.ok(c.policy(test.spec(c,pinned=pinned)));c.ok(c.prepare())
        if action in ('get','reboot','recover_once'):c.ok(c.control(c.linux,key='setup-start'))
        c.vm_id=c.linux['uuid']
        recovery=foundation.FoundationTest.spec(c) if action=='recover_once' else None
        c.cooperative_removes=False
        counts=Counter();by_kind=Counter();active=[]
        read=CPU.read;loads=json.loads;operations=CPU.operations;observe=CPU.observe
        command=c.p.command
        def read_cpu(cpu,path):
            kind=('policy' if path==cpu.policy_path else 'anchor' if path==cpu.anchor else
                  'receipt' if path==cpu.receipt_path else 'journal')
            by_kind[kind+'.read_attempt']+=1;active.append(kind)
            try:return read(cpu,path)
            finally:active.pop()
        def parse(*args,**kwargs):
            if active:by_kind[active[-1]+'.parse']+=1
            return loads(*args,**kwargs)
        def scan(cpu):counts['history_scans']+=1;return operations(cpu)
        def observation(cpu,deadline):counts['full_cpu_observations']+=1;return observe(cpu,deadline)
        def external(args,**kwargs):
            counts['command.'+'.'.join(args[:2])]+=1
            return command(args,**kwargs)
        with ExitStack() as stack:
            for owner,name,replacement in ((CPU,'read',read_cpu),(CPU,'operations',scan),
                (CPU,'observe',observation),(json,'loads',parse),(c.p,'command',external)):
                stack.enter_context(patch.object(owner,name,replacement))
            if action=='host-prepare':result=c.prepare()
            elif action=='recover_once':result=c.m.call(action,recovery,execute=True)
            elif action=='get':result=c.m.call(action,{'vm_id':c.linux['uuid']})
            else:result=c.control(c.linux,action,key='measure-'+action)
            c.ok(result)
        return {'action':action,'vcpus':n,'placement':'explicit-shared' if pinned else 'default',
                'counts':dict(counts),'cpu_files':dict(by_kind),'ok':True}
    finally:test.doCleanups()


if __name__=='__main__':
    output=Path(sys.argv[1]);assert not output.exists()
    rows=[one(a,n,p) for a in ('start','get','reboot','recover_once','host-prepare') for n in (1,4) for p in (False,True)]
    output.write_text(json.dumps({'schema':'local-fixture-counts-1','rows':rows},indent=2)+'\n')
    print(json.dumps(rows))
