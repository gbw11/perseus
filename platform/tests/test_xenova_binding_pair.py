"""실제 paired binding/게시/guard를 검증한다. Xen helper 출력만 외부 대역이다."""
from contextlib import contextmanager
import json
from unittest.mock import patch

import pytest

from test_xenova_batch_read_queries import pair,batch_commands
import xenova_drive_binding as binding
from xenova_execution import Execution,ExecutionError,ReadChanged,short_lock
from xenova_management import Manager
from xenova_runtime import operation_lock
from xenova_vm import PlatformError


def identities(case):
    return tuple(record['uuid'] for record in case.p.records(validate_assets=False).values())


@pytest.mark.parametrize('count',[1,2])
def test_pair_shares_two_full_reads_and_native_checks_without_global_configuration(pair,monkeypatch,count):
    ids=identities(pair)[:count];command,calls=batch_commands(pair)
    monkeypatch.delenv('XENOVA_IDENTITY_BATCH_READS',raising=False)
    records=pair.p.records;tombs=Manager._tombs;locks=[];gate=Execution.gate
    @contextmanager
    def lock(runtime,name,**kwargs):
        locks.append(name)
        with short_lock(runtime,name,**kwargs):yield
    def gated(owner):
        with pytest.raises(ExecutionError) as failure:
            with short_lock(pair.p.runtime,'admission'):pass
        assert failure.value.error['code']=='OPERATION_IN_PROGRESS'
        return gate(owner)
    with (patch.object(pair.p,'timed_command',command),
          patch.object(pair.p,'records',wraps=records) as scans,
          patch.object(Manager,'_tombs',autospec=True,side_effect=tombs) as lives,
          patch.object(binding,'short_lock',lock),patch.object(Execution,'gate',gated),
          operation_lock(pair.p.runtime)):
        result=binding.observe_pair(pair.p,ids,timeout_s=5)
    assert set(result)==set(ids)
    assert all(value['status']=='observed' for value in result.values())
    assert scans.call_count==2 and lives.call_count==2
    assert sum(c==['xl','list','-v'] for c in calls)==2
    assert sum(c[0]=='/usr/libexec/xenova-domain-identities-native' for c in calls)==2
    assert not any(c[0]=='xenova-domain-identity' for c in calls)
    assert locks==['admission']
    assert not hasattr(pair.p,'response_deadline')
    for identity,value in result.items():
        stored=json.loads((pair.p.runtime/'observations'/(identity+'.json')).read_text())
        assert stored['last_success']['monotonic_ms']==value['observed_monotonic_ms']


def test_pair_uses_final_batch_start_clock_not_metadata_completion(pair):
    ids=identities(pair);now=[pair.p.clock()+10];pair.p.clock=lambda:now[0]
    before=[]
    def delay(rows,phase):
        if phase==2:before.append(now[0]);now[0]+=.4
    command,_=batch_commands(pair,delay)
    with patch.object(pair.p,'timed_command',command):result=binding.observe_pair(pair.p,ids)
    assert {v['observed_monotonic_ms'] for v in result.values()}=={int(before[0]*1000)}
    assert now[0]>before[0]


@pytest.mark.parametrize('state',['stopped','absent','retired'])
def test_pair_does_not_invent_a_live_execution(pair,state):
    ids=identities(pair);target=ids[0]
    if state in ('stopped','retired'):
        with patch('xenova_vm.os.sync'):
            answer=pair.m.call('stop',{'vm_id':target},idempotency_key='pair-stop')
        assert answer['ok'],answer
    if state=='retired':
        answer=pair.m.call('retire',{'vm_id':target},execute=True,idempotency_key='pair-retire')
        assert answer['ok'],answer
    if state=='absent':
        pair.f.xen.rows=[row for row in pair.f.xen.rows if row['config']['c_info']['uuid']!=target]
    command,calls=batch_commands(pair)
    with patch.object(pair.p,'timed_command',command):
        if state=='retired':
            with pytest.raises(PlatformError,match='not registered'):binding.observe_pair(pair.p,ids)
            assert not calls
        else:
            result=binding.observe_pair(pair.p,ids)
            assert result[target]['status']==state
            assert result[ids[1]]['status']=='observed'
            assert 'execution_id' not in result[target]


@pytest.mark.parametrize('field',['run','intent','record-type'])
def test_pair_current_target_changes_before_publication_are_rejected(pair,field):
    ids=identities(pair);target=ids[0]
    record=next(r for r in pair.p.records(validate_assets=False).values() if r['uuid']==target)
    if field=='record-type':path=pair.p.data/'instances'/record['id']/'instance.json'
    else:path=(pair.p.runtime/'executions' if field=='run' else pair.p.data/'control')/(target+'.json')
    saved=path.read_bytes()
    def changed(rows,phase):
        if phase==2:
            value=json.loads(saved)
            if field=='record-type':value['vcpus']=float(value['vcpus'])
            elif field=='run':value['execution_id']='550e8400-e29b-41d4-a716-446655440099'
            else:value['intent_revision']+=1
            path.write_text(json.dumps(value))
    command,_=batch_commands(pair,changed)
    try:
        with patch.object(pair.p,'timed_command',command),pytest.raises(PlatformError):
            binding.observe_pair(pair.p,ids)
    finally:path.write_bytes(saved)


def test_pair_final_admission_rejects_change_after_publication(pair):
    ids=identities(pair);target=ids[0];original=Execution.publish_observation
    path=pair.p.data/'control'/(target+'.json');saved=path.read_bytes()
    command,_=batch_commands(pair)
    def publish(owner,record,*args,**kwargs):
        value=original(owner,record,*args,**kwargs)
        if record['uuid']==ids[-1]:
            changed=json.loads(saved);changed['intent_revision']+=1;path.write_text(json.dumps(changed))
        return value
    try:
        with patch.object(pair.p,'timed_command',command),patch.object(Execution,'publish_observation',publish):
            with pytest.raises(ReadChanged):binding.observe_pair(pair.p,ids)
    finally:path.write_bytes(saved)


def test_pair_second_full_scan_rejects_duplicate_registry_identity(pair):
    ids=identities(pair)
    record=next(r for r in pair.p.records(validate_assets=False).values() if r['uuid']==ids[1])
    path=pair.p.data/'instances'/record['id']/'instance.json';saved=path.read_bytes()
    def duplicate(rows,phase):
        if phase==2:
            value=json.loads(saved);value['uuid']=ids[0];path.write_text(json.dumps(value))
    command,_=batch_commands(pair,duplicate)
    try:
        with patch.object(pair.p,'timed_command',command),pytest.raises(PlatformError):
            binding.observe_pair(pair.p,ids)
    finally:path.write_bytes(saved)


def test_pair_preserves_newer_probe_failure(pair):
    ids=identities(pair);now=[pair.p.clock()+10];pair.p.clock=lambda:now[0]
    def changed(rows,phase):
        if phase==2:
            now[0]+=.1;pair.p.execution().probe_error(ids[0],'OBSERVATION_UNAVAILABLE')
    command,_=batch_commands(pair,changed)
    with patch.object(pair.p,'timed_command',command),pytest.raises(ReadChanged):
        binding.observe_pair(pair.p,ids)
    stored=json.loads((pair.p.runtime/'observations'/(ids[0]+'.json')).read_text())
    assert stored['last_error']['code']=='OBSERVATION_UNAVAILABLE'


@pytest.mark.parametrize('cause',['host','deadline','identity','missing-helper'])
def test_pair_fails_closed_without_extra_identity_fallback(pair,cause):
    ids=identities(pair);now=[pair.p.clock()+10];origin=now[0];pair.p.clock=lambda:now[0]
    pair.p.response_deadline=origin+50;host=pair.p.boot_id_reader()
    changed_host=[host];pair.p.boot_id_reader=lambda:changed_host[0]
    def change(rows,phase):
        if cause=='missing-helper':raise FileNotFoundError('fixture native helper absent')
        if phase==2:
            if cause=='host':changed_host[0]='20000000-1111-4222-8333-123456789abc'
            elif cause=='deadline':now[0]=origin+2.1
            else:rows[0]['xen_unique_id']='999999'
    command,calls=batch_commands(pair,change)
    with patch.object(pair.p,'timed_command',command),pytest.raises(ExecutionError) as failure:
        binding.observe_pair(pair.p,ids)
    assert failure.value.error['code']==('DEADLINE_EXCEEDED' if cause=='deadline' else
        'OBSERVATION_UNAVAILABLE' if cause=='missing-helper' else 'TARGET_CHANGED')
    assert pair.p.response_deadline==origin+50
    assert not any(c[0]=='xenova-domain-identity' for c in calls)


def test_pair_unknown_scheduler_sample_rechecks_once_in_original_budget(pair):
    ids=identities(pair);now=[pair.p.clock()+10];pair.p.clock=lambda:now[0]
    pair.p.sleep=lambda seconds:now.__setitem__(0,now[0]+seconds)
    pair.f.xen.states={pair.f.xen.rows[0]['domid']:'------'}
    def changed(rows,phase):
        if phase==2:pair.f.xen.states={}
    command,calls=batch_commands(pair,changed)
    with patch.object(pair.p,'timed_command',command):result=binding.observe_pair(pair.p,ids)
    assert all(value['status']=='observed' for value in result.values())
    assert sum(c==['xl','list','-v'] for c in calls)==4
    assert sum(c[0]=='/usr/libexec/xenova-domain-identities-native' for c in calls)==4
    assert {value['observed_monotonic_ms'] for value in result.values()}=={int(now[0]*1000)}


@pytest.mark.parametrize('unavailable',['unmanaged','paused','unscheduled'])
def test_pair_target_local_failure_preserves_verified_other_execution(pair,unavailable):
    ids=identities(pair);target=ids[0]
    record=next(r for r in pair.p.records(validate_assets=False).values() if r['uuid']==target)
    path=pair.p.runtime/'executions'/(target+'.json');saved=path.read_bytes()
    if unavailable=='unmanaged':
        run=json.loads(saved);run['phase']='launching';path.write_text(json.dumps(run))
    else:
        row=next(row for row in pair.f.xen.rows if row['config']['c_info']['uuid']==target)
        pair.f.xen.states={row['domid']:'--p---' if unavailable=='paused' else '------'}
    command,calls=batch_commands(pair)
    try:
        with patch.object(pair.p,'timed_command',command):result=binding.observe_pair(pair.p,ids,timeout_s=5)
        assert isinstance(result[target],PlatformError)
        assert str(result[target])=='managed live execution required'
        healthy=result[ids[1]]
        assert healthy['status']=='observed'
        stored=json.loads((pair.p.runtime/'observations'/(ids[1]+'.json')).read_text())
        assert healthy['observed_monotonic_ms']==stored['last_success']['monotonic_ms']
        assert sum(c==['xl','list','-v'] for c in calls)==(4 if unavailable=='unscheduled' else 2)
    finally:path.write_bytes(saved)


@pytest.mark.parametrize('corruption',['unrelated-run','unrelated-intent','unrelated-record','target-run'])
def test_pair_selects_execution_inputs_but_keeps_complete_registry_validation(pair,corruption):
    ids=identities(pair)
    other='650e8400-e29b-41d4-a716-446655440055'
    spec={**pair.f.spec,'vm_id':other,'name':'binding-unrelated','ipv4':'192.168.77.55'}
    made=pair.m.call('create',spec,execute=True,idempotency_key='binding-unrelated-create')
    assert made['ok'],made
    record=next(r for r in pair.p.records(validate_assets=False).values() if r['uuid']==other)
    if corruption=='unrelated-record':path=pair.p.data/'instances'/record['id']/'instance.json'
    elif corruption=='unrelated-intent':path=pair.p.data/'control'/(other+'.json')
    else:path=pair.p.runtime/'executions'/((ids[0] if corruption=='target-run' else other)+'.json')
    saved=path.read_bytes() if path.exists() else None
    command,calls=batch_commands(pair)
    try:
        path.write_text('{fixture corrupt JSON')
        with patch.object(pair.p,'timed_command',command):
            if corruption in ('unrelated-record','target-run'):
                with pytest.raises(PlatformError):binding.observe_pair(pair.p,ids,timeout_s=5)
                assert not calls
            else:
                result=binding.observe_pair(pair.p,ids,timeout_s=5)
                assert all(value['status']=='observed' for value in result.values())
    finally:
        if saved is None:path.unlink()
        else:path.write_bytes(saved)
