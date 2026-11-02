#!/usr/bin/env python3
"""소유 tmp fixture의 실제 broker writer 작업량. 비밀·해시 값은 출력하지 않는다."""
from collections import Counter
import json
from pathlib import Path
import time
from unittest.mock import patch

import test_xenova_provisioning as fixture
import xenova_provisioning as provider
import xenova_broker as broker


def main():
    f=fixture.ProvisioningTest('runTest');f.setUp()
    rows=[]
    try:
        for name,call in (
                ('first-supply',lambda:provider.ensure(f.data,f.record)),
                ('same-vm-supply',lambda:provider.ensure(f.data,f.record)),
                ('public-status',lambda:provider.status(f.data,f.record['uuid']))):
            counts=Counter();read=Path.read_text;loads=json.loads;fsync=broker.os.fsync
            hasher=broker.hash_new;publish=broker.publish
            def measured(key,function):
                def run(*args,**kwargs):counts[key]+=1;return function(*args,**kwargs)
                return run
            with patch.object(Path,'read_text',measured('text_read',read)),\
                 patch.object(json,'loads',measured('json_parse',loads)),\
                 patch.object(broker.os,'fsync',measured('required_fsync',fsync)),\
                 patch.object(broker,'hash_new',measured('password_hash_batch',hasher)),\
                 patch.object(broker,'publish',measured('broker_generation_publish',publish)),\
                 patch.object(broker,'reload_broker',measured('broker_reload',lambda:'next_start')):
                start=time.monotonic();result=call();elapsed=time.monotonic()-start
            rows.append({'case':name,'counts':dict(counts),'seconds':elapsed,
                         'credential_state':result['credential_state'],'authentication':result['authentication']})
        assert rows[0]['counts']['password_hash_batch']==1
        for row in rows[1:]:
            assert all(row['counts'].get(key,0)==0 for key in ('password_hash_batch','broker_generation_publish','broker_reload'))
        # 기존 writer가 다른 호출의 게시 내구성을 재확인하는 directory fsync는 보존한다.
        assert rows[1]['counts']['required_fsync']==1
        assert rows[2]['counts'].get('required_fsync',0)==0
        print(json.dumps({'scope':'native local tmp; hash tool real, service/mount external fixtures; no storage timing guarantee',
                          'samples_per_case':1,'rows':rows},indent=2))
    finally:f.doCleanups()


if __name__=='__main__':main()
