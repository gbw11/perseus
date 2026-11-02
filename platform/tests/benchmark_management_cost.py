"""소유 격리 fixture에서 실제 Manager의 read/parse/fsync 계수를 비교한다.

완료 이력 규모는 현재 검증기를 통과하는 합성 기록이다. 실제 Xen/guest·보드 시간은 측정하지 않는다.
"""
from collections import Counter
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

from test_xenova_creation_reconcile import CreationReconcileTest
import xenova_creation as creation
import xenova_management as management
import xenova_runtime as runtime


def one(action,n,kind):
    case=CreationReconcileTest('runTest');case.setUp()
    try:
        if kind=='settled':
            op=case.fail_copy();request=case.request(op)
            result=case.m.call('operation-reconcile',request,execute=True)
            assert result['ok'],result
        else:
            assert case.m.call('create',case.spec,execute=True)['ok']
        template=case.m._operations()[0]
        # 완료 이력만 늘린다. UUID/MAC과 원 결과의 대응을 유지하며 실제 실행 용량을 늘리지 않는다.
        for i in range(n):
            row=copy.deepcopy(template)
            old=row['vm_id'];identity='950e8400-e29b-41d4-a716-'+f'{i:012x}'
            opid='960e8400-e29b-41d4-a716-'+f'{i:012x}'
            old_mac=row['assigned']['mac'];new_mac='02:95:00:00:'+f'{i//256:02x}:'+f'{i%256:02x}'
            def replace(value):
                if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
                if isinstance(value,list):return [replace(v) for v in value]
                if isinstance(value,str):return value.replace(old,identity).replace(management.internal_name(old),management.internal_name(identity)).replace(row['operation_id'],opid).replace(old_mac,new_mac)
                return value
            row=replace(row);row['operation_id']=opid
            row['idempotency_key']='create:'+identity;row['owned_workspace']='instances/.operation-'+opid
            row['assigned']['mac']=new_mac
            row['spec_hash']=management.digest(row['initial_spec'])
            if 'reconciliation' in row:
                row['reconciliation']['request']['expected_spec_sha256']=row['spec_hash']
            case.m._write(case.f.data/'operations'/(opid+'.json'),row)
        case.m._operations()  # Validate complete synthetic history before measuring.
        records=case.p.records(validate_assets=False)
        counts=Counter();by_path=Counter();read=Path.read_text;loads=json.loads;fsync=os.fsync
        def text(path,*a,**kw):
            value=read(path,*a,**kw);counts['text_reads']+=1;counts['text_bytes']+=len(value.encode())
            if path.parent==case.f.data/'operations':counts['operation_reads']+=1
            if path.parent==case.f.data/'tombstones':counts['tombstone_reads']+=1
            by_path[str(path.relative_to(case.f.data)) if path.is_relative_to(case.f.data) else path.name]+=1
            return value
        def parse(*a,**kw):counts['json_parses']+=1;return loads(*a,**kw)
        def sync(fd):
            counts['fsync']+=1
            name=os.readlink('/proc/self/fd/'+str(fd))
            if name.startswith(str(case.f.data/'operations')):counts['operation_fsync']+=1
            return fsync(fd)
        command=case.p.command
        def external(args,**kw):
            counts['external_commands']+=1
            if args[0]=='e2fsck':counts['fsck']+=1;return ''
            return command(args,**kw)
        if action=='restore':
            retired=case.m.call('retire',{'vm_id':case.spec['vm_id']},execute=True,idempotency_key='cost-retire')
            assert retired['ok'],retired
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,'read_text',text))
            stack.enter_context(patch.object(json,'loads',parse))
            stack.enter_context(patch.object(os,'fsync',sync))
            stack.enter_context(patch.object(case.p,'command',external))
            started=time.perf_counter()
            if action=='ip':
                with runtime.operation_lock(case.p.runtime):result=case.m._ip_inventory(records)
            elif action=='plan':
                spec={**case.spec,'vm_id':'970e8400-e29b-41d4-a716-000000000001'};spec.pop('ipv4',None)
                result=case.m.call('create',spec,execute=False)
                assert result['ok'],result
            elif action=='operation':
                result=case.m.call('operation',{'idempotency_key':template['idempotency_key']})
                assert result['ok'],result
            else:
                result=case.m.call('restore',{'vm_id':case.spec['vm_id']},execute=True,idempotency_key='cost-restore')
                assert result['ok'],result
            elapsed=time.perf_counter()-started
        return {'action':action,'extra_history':n,'kind':kind,'counts':dict(counts),'reads_by_path':dict(by_path),
                'wall_s_instrumented':elapsed,'trace':case.m.last_trace,'success':True}
    finally:case.doCleanups()


if __name__=='__main__':
    output=Path(sys.argv[1]);assert not output.exists()
    rows=[one(a,n,k) for k in ('success','settled') for a in ('ip','plan','operation') for n in (0,1,10,100)]
    rows.append(one('restore',0,'success'))
    output.write_text(json.dumps({'samples':rows,'limits':{'max_extra_history':100,'repeats':1},
        'boundary':'real files/Manager/fsync; fake Xen/guest; instrumented host time not board performance'},indent=2)+'\n')
    print(json.dumps([{k:r[k] for k in ('action','extra_history','kind','counts')} for r in rows]))
