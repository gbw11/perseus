"""H01-H11: real IPC/actor/store; only app/Xen/device inputs are fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from xenova_gateway.authority import AuthorityStore, AuthorityError
from xenova_gateway.client import GatewayClient, GatewayRejected, new_request_id
from xenova_gateway.history import HistoryPolicy, candidate, room
from xenova_gateway.ipc_contract import request_key
from test_provider_ipc import actor_server, submission, LAB, A, B


def enable(lab, retry=100, retention=20):
    lab.auth.history_policy=HistoryPolicy(retry,retention)


def complete(client, target=B, *, deadline=60):
    context=client.context(); identity=new_request_id(context['host_boot_id'])
    spec=submission(client,target)
    spec['deadline_ms']=int(time.monotonic()*1000)+deadline
    original=client.submit(identity,spec)
    assert client.submit(identity,{**spec,'parent_deadline_ms':spec['deadline_ms']})['request_id']==identity
    current=client.context()
    ended=client.cancel(identity,context=dict(host_boot_id=current['host_boot_id'],
        expected_generation=current['generation'],target_execution_id=spec['target_execution_id']))
    assert ended['phase']=='CANCELLED'
    return identity,spec,original


def test_h01_separate_process_repeats_without_reset_and_restarts(tmp_path):
    root=tmp_path/'xenova-gateway-lab-history'
    argv=[sys.executable,'-B',str(LAB),'--directory',str(root),
          '--history-retry-ms','180','--history-retention-ms','30']
    def start():
        proc=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        info=json.loads(proc.stdout.readline())
        return proc,GatewayClient(info['endpoint'],uid=info['uid'])
    proc,client=start()
    try:
        generation=client.context()['generation']
        first=None
        for index in range(100):
            identity,spec,_=complete(client,deadline=100)
            if first is None:first=identity,spec
            assert client.history()['serialized_bytes']<65536
            assert client.context()['generation']==generation
            time.sleep(.025)
        assert client.history()['records']['protections']['retained']<16
        with pytest.raises(GatewayRejected) as expired:
            client.submit(first[0],{**first[1],'deadline_ms':int(time.monotonic()*1000)+100})
        assert expired.value.code=='REQUEST_WINDOW_EXPIRED'
        with pytest.raises(GatewayRejected) as removed:client.get(first[0])
        assert removed.value.code=='RESULT_NOT_RETAINED_OR_UNKNOWN'
        last=identity
        proc.terminate();proc.wait(5);proc.stdout.close();proc.stderr.close()
        proc,client=start()
        assert client.get(last)['phase']=='CANCELLED'
        assert not client.context()['output_permitted']
        complete(client)
    finally:
        if proc.poll() is None:proc.terminate()
        proc.wait(5);proc.stdout.close();proc.stderr.close()


def test_h02_active_expired_exclusion_survives_other_cleanup(actor_server):
    lab,client=actor_server;enable(lab,retry=120)
    old=client.context();held=new_request_id(old['host_boot_id'])
    spec=submission(client,B);spec['deadline_ms']=int(time.monotonic()*1000)+60
    client.submit(held,spec)
    time.sleep(.16)
    assert client.get(held)['protection']['held']
    fields=candidate(lab.auth,int(time.monotonic()*1000))
    assert any(row['request_id']==held for row in fields['protections'])
    assert lab.auth.protected(B) and client.context()['owner']==A
    client.cancel(held,context=dict(host_boot_id=old['host_boot_id'],expected_generation=old['generation'],
                                  target_execution_id=spec['target_execution_id']))
    time.sleep(.025)
    complete(client)
    assert not lab.auth.protected(B)


def test_h01_transition_history_collects_then_real_commit_can_progress(actor_server):
    lab,client=actor_server;enable(lab,retry=300,retention=50)
    first=None;generation=client.context()['generation']
    for _ in range(70):
        identity=new_request_id(client.context()['host_boot_id']);spec=submission(client,B,'handoff')
        spec['deadline_ms']=int(time.monotonic()*1000)+200
        client.submit(identity,spec)
        assert client.cancel(identity)['phase']=='CANCELLED'
        if first is None:first=identity,spec
        assert client.context()['generation']==generation
        time.sleep(.04)
    assert client.history()['records']['transitions']['retained']<32
    with pytest.raises(GatewayRejected) as old:
        client.submit(first[0],{**first[1],'deadline_ms':int(time.monotonic()*1000)+200})
    assert old.value.code=='REQUEST_WINDOW_EXPIRED'
    identity=new_request_id(client.context()['host_boot_id']);spec=submission(client,B,'handoff')
    spec['deadline_ms']=int(time.monotonic()*1000)+200
    client.submit(identity,spec)
    result=client.commit(identity)
    assert result['phase']=='COMMITTED' and result['committed_generation']==generation+1
    end=time.monotonic()+1
    while time.monotonic()<end and not client.get(identity)['app_applied']:time.sleep(.005)
    assert client.get(identity)['app_applied'] and client.context()['owner']==B


def test_h03_semantic_replay_original_deadline_and_real_conflict(actor_server):
    lab,client=actor_server;enable(lab,retry=1000)
    identity,spec,accepted=complete(client,deadline=500)
    before=lab.auth.path.read_bytes()
    assert client.submit(identity,{**spec,'parent_deadline_ms':spec['deadline_ms'],'purpose':'protect'})['phase']=='CANCELLED'
    assert lab.auth.path.read_bytes()==before
    with pytest.raises(GatewayRejected) as changed:
        client.submit(identity,{**spec,'intent_revision':spec['intent_revision']+1})
    assert changed.value.code=='IDEMPOTENCY_CONFLICT'
    assert client.get(identity)['retry_until_ms']==accepted['retry_until_ms']


def test_h05_failed_collection_frees_nothing_and_cold_read_preserves_old(actor_server,monkeypatch):
    lab,client=actor_server;enable(lab,retry=80,retention=10)
    identity,_,_=complete(client,deadline=40);time.sleep(.1)
    before=lab.auth.path.read_bytes();ram=lab.auth.value
    from xenova_gateway import authority
    def eio(*args):raise OSError(5,'owned fixture EIO')
    monkeypatch.setattr(authority,'atomic_json',eio)
    with pytest.raises(GatewayRejected) as failed:complete(client,deadline=40)
    assert failed.value.code=='STORAGE_UNCERTAIN'
    assert lab.auth.value is ram and lab.auth.path.read_bytes()==before
    cold=AuthorityStore(lab.auth.directory,lab.auth.host,uid=os.geteuid(),mountpoint=None,history_policy=lab.auth.history_policy)
    cold.load()
    assert any(row['request_id']==identity for row in cold.value['protections'])
    monkeypatch.undo()
    lab.auth=cold;lab.runner.authority=cold;lab.runner.machine.authority=cold
    complete(client,deadline=40)


def test_h06_host_and_policy_changes_cannot_reopen_a_retired_key(actor_server):
    lab,client=actor_server;enable(lab,retry=80,retention=10)
    identity,spec,_=complete(client,deadline=40);time.sleep(.1);complete(client,deadline=40)
    lab.auth.history_policy=HistoryPolicy(10000,10000)
    with pytest.raises(GatewayRejected) as old:
        client.submit(identity,{**spec,'deadline_ms':int(time.monotonic()*1000)+2000})
    assert old.value.code=='REQUEST_WINDOW_EXPIRED'
    host,issued=request_key(identity)
    with pytest.raises(GatewayRejected) as other:
        client.submit(identity.replace(host,str(uuid4())),{**spec,'deadline_ms':int(time.monotonic()*1000)+2000})
    assert other.value.code=='HOST_CHANGED'


def test_h07_terminal_transition_pending_reference_is_not_collected(actor_server):
    lab,client=actor_server
    identity=str(uuid4());spec=submission(client,B,'handoff')
    client.submit(identity,spec);client.commit(identity)
    enable(lab,retry=1,retention=1)
    # Read the actual persisted transition and current pending reference. A
    # deliberately far logical instant cannot delete an in-flight application.
    pending=lab.auth.value.get('pending') or dict(generation=lab.auth.value['generation'],
        host_boot_id=lab.auth.host,deadline_ms=spec['deadline_ms'],arm_after_apply=False)
    lab.auth.update(phase='PENDING',pending={**pending,'transition_id':identity})
    fields=candidate(lab.auth,int(time.monotonic()*1000)+10000)
    assert identity in [row['request_id'] for row in fields['transitions']]


def test_h08_capacity_reserves_evidence_before_acceptance(actor_server):
    lab,client=actor_server
    assert room(lab.auth.value)
    oversized={**lab.auth.value,'reason':'x'*65000}
    assert not room(oversized)
    # One maximum supported row reserve applies to all later app/device stages.
    assert not room({**lab.auth.value,'protections':[{'padding':'x'*4097}]})


def test_h09_retention_full_then_normal_recovery_without_reset(actor_server):
    lab,client=actor_server;enable(lab,retry=1500,retention=1)
    accepted=[]
    for index in range(20):
        try:accepted.append(complete(client,deadline=1000)[0])
        except GatewayRejected as error:
            assert error.code=='HISTORY_BYTES_RESERVED';break
    else:pytest.fail('supported evidence reservation did not limit admission')
    assert accepted and client.get(accepted[-1])['phase']=='CANCELLED'
    time.sleep(1.55)
    complete(client,deadline=1000)
    assert client.history()['records']['protections']['retained']==1


def test_expired_preparation_requires_supported_cancel_then_admission_can_collect(actor_server):
    lab,client=actor_server;enable(lab,retry=600,retention=1)
    key=new_request_id(lab.auth.host);spec=submission(client,B,'handoff')
    spec['deadline_ms']=int(time.monotonic()*1000)+200
    spec.pop('parent_deadline_ms',None)
    original=client.submit(key,spec);assert original['phase']=='PREPARING'
    time.sleep(.65)
    # Age alone cannot delete a preparation or manufacture a terminal result.
    assert client.get(key)['phase']=='PREPARING'
    assert client.history()['records']['transitions']['eligible']==0
    assert client.cancel(key)['phase']=='CANCELLED'
    assert client.history()['records']['transitions']['eligible']==1
    complete(client,deadline=300)
    assert client.history()['records']['transitions']['retained']==0
    with pytest.raises(GatewayRejected) as old:client.submit(key,{**spec,'deadline_ms':int(time.monotonic()*1000)+300})
    assert old.value.code=='REQUEST_WINDOW_EXPIRED'


def test_h11_legacy_original_replay_but_no_new_opaque_keys_after_opt_in(actor_server):
    lab,client=actor_server
    identity=str(uuid4());spec=submission(client,B)
    client.submit(identity,spec);current=client.context()
    client.cancel(identity,context=dict(host_boot_id=current['host_boot_id'],expected_generation=current['generation'],
                                      target_execution_id=spec['target_execution_id']))
    original=lab.auth.path.read_bytes();enable(lab,retry=1000)
    assert client.submit(identity,spec)['phase']=='CANCELLED'
    assert lab.auth.path.read_bytes()==original
    with pytest.raises(GatewayRejected) as legacy:client.submit(str(uuid4()),spec)
    assert legacy.value.code=='LEGACY_KEY_NOT_ADMISSIBLE'
    complete(client)


def test_cleanup_uses_new_admission_save_without_replay_or_query_writes(actor_server,monkeypatch):
    from xenova_gateway import authority
    lab,client=actor_server;enable(lab,retry=400,retention=1)
    old,_,_=complete(client,deadline=200)
    time.sleep(.42)
    writes=[];syncs=[]
    atomic=authority.atomic_json;fsync=os.fsync
    def counted(path,value):
        writes.append(len(json.dumps(value,separators=(',',':')).encode()))
        return atomic(path,value)
    def synced(fd):syncs.append(fd);return fsync(fd)
    monkeypatch.setattr(authority,'atomic_json',counted)
    monkeypatch.setattr(os,'fsync',synced)
    key=new_request_id(lab.auth.host);spec=submission(client,B)
    spec['deadline_ms']=int(time.monotonic()*1000)+200
    result=client.submit(key,spec)
    assert result['phase']=='PROTECTED' and len(writes)==1 and len(syncs)==2
    assert old not in [row['request_id'] for row in lab.auth.value['protections']]
    for _ in range(5):
        assert client.get(key)['phase']=='PROTECTED'
        assert client.submit(key,{**spec,'purpose':'protect'})['request_id']==key
        assert client.history()['records']['protections']['retained']==1
    assert len(writes)==1 and len(syncs)==2
    metric=os.environ.get('XENOVA_PROVIDER_METRICS')
    if metric:Path(metric).with_name('HISTORY_COST.json').write_text(json.dumps(dict(
        admission_saves=1,admission_fsyncs=2,serialized_bytes=writes[0],
        replay_lookup_status_additional_saves=0,replay_lookup_status_additional_fsyncs=0),indent=2))


def test_policy_is_explicit_and_strict(tmp_path):
    for args in ((0,1),(True,1),(1,None),(1,-1)):
        with pytest.raises(ValueError):HistoryPolicy(*args)
    path=tmp_path/'policy.json';path.write_text('{"retry_ms":100,"retention_ms":200}');path.chmod(0o600)
    assert HistoryPolicy.load(path,uid=os.geteuid())==HistoryPolicy(100,200)
    path.write_text('{"retry_ms":null,"retention_ms":200}')
    with pytest.raises(ValueError):HistoryPolicy.load(path,uid=os.geteuid())
