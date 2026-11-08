"""Opt-in ordinary list batches only process launches, never identity checks."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import test_xenova_read_progress as progress
from xenova_execution import Execution,ExecutionError


@pytest.fixture
def pair():
    case=progress.ReadProgressTest('runTest');case.setUp()
    try:
        case.pair()
        yield case
    finally:case.doCleanups()


def batch_commands(case, change=None):
    original=case.p.timed_command;calls=[]
    def command(argv,deadline):
        calls.append(argv)
        if argv[0]=='/usr/libexec/xenova-domain-identities-native':
            rows=[json.loads(original(['xenova-domain-identity','--domid',d],deadline)) for d in argv[2].split(',')]
            if change:change(rows,len([c for c in calls if c[0]==argv[0]]))
            return json.dumps({'schema_version':1,'items':rows})
        return original(argv,deadline)
    return command,calls


def test_list_reduces_launches_but_keeps_two_lists_and_two_identities_per_guest(pair,monkeypatch):
    monkeypatch.setenv('XENOVA_IDENTITY_BATCH_READS','1')
    command,calls=batch_commands(pair)
    with patch.object(pair.p,'timed_command',command):
        result=pair.m.fork().call('list',budget_ms=5000)
    assert result['ok'],result
    assert sum(c[0]=='/usr/libexec/xenova-domain-identities-native' for c in calls)==2
    assert sum(c==['xl','list','-v'] for c in calls)==2
    assert not any(c[0]=='xenova-domain-identity' for c in calls)
    assert len(result['data']['vms'])==2


def test_single_get_keeps_existing_helper(pair,monkeypatch):
    monkeypatch.setenv('XENOVA_IDENTITY_BATCH_READS','1')
    command,calls=batch_commands(pair)
    with patch.object(pair.p,'timed_command',command):
        result=pair.m.fork().call('get',{'vm_id':pair.f.spec['vm_id']},budget_ms=5000)
    assert result['ok'],result
    assert sum(c[0]=='xenova-domain-identity' for c in calls)==2
    assert not any(c[0]=='/usr/libexec/xenova-domain-identities-native' for c in calls)


def test_non_read_context_keeps_both_single_checks_for_every_guest(pair,monkeypatch):
    monkeypatch.setenv('XENOVA_IDENTITY_BATCH_READS','1')
    command,calls=batch_commands(pair)
    records=list(pair.p.records(validate_assets=False).values())
    with patch.object(pair.p,'timed_command',command):
        values=pair.p.execution().observe_many(records,pair.p.clock()+5)
    assert len(values)==2
    assert sum(c[0]=='xenova-domain-identity' for c in calls)==4
    assert not any(c[0]=='/usr/libexec/xenova-domain-identities-native' for c in calls)


def test_changed_incarnation_in_second_batch_is_rejected(pair,monkeypatch):
    monkeypatch.setenv('XENOVA_IDENTITY_BATCH_READS','1')
    def change(rows,phase):
        if phase==2:rows[0]['xen_unique_id']='999999'
    command,_=batch_commands(pair,change)
    with patch.object(pair.p,'timed_command',command):
        result=pair.m.fork().call('list',budget_ms=5000)
    assert not result['ok'] and result['error']['code']=='TARGET_CHANGED'


def test_conservative_timestamp_predates_delayed_final_batch(pair,monkeypatch):
    monkeypatch.setenv('XENOVA_IDENTITY_BATCH_READS','1')
    now=[100.0];pair.p.clock=lambda:now[0];before=[]
    def delay(rows,phase):
        if phase==2:before.append(now[0]);now[0]+=1
    command,_=batch_commands(pair,delay)
    with patch.object(pair.p,'timed_command',command):
        result=pair.m.fork().call('list',budget_ms=5000)
    assert result['ok'],result
    assert {v['observed_monotonic_ms'] for v in result['data']['vms']}=={int(before[0]*1000)}


@pytest.mark.parametrize('change', ['missing','duplicate','foreign','boolean','state','zero','overflow','schema','extra'])
def test_malformed_batch_does_not_fall_back_to_another_query(change,tmp_path):
    rows=[{'domid':1,'xen_unique_id':'11','state_bits':1},{'domid':2,'xen_unique_id':'22','state_bits':1}]
    value={'schema_version':1,'items':rows}
    if change=='missing':rows.pop()
    elif change=='duplicate':rows[1]=dict(rows[0])
    elif change=='foreign':rows[1]['domid']=3
    elif change=='boolean':rows[0]['domid']=True
    elif change=='state':rows[0]['state_bits']=False
    elif change=='zero':rows[0]['xen_unique_id']='0'
    elif change=='overflow':rows[0]['xen_unique_id']=str(2**64)
    elif change=='schema':value['schema_version']=True
    else:value['other']=1
    calls=[]
    def command(argv,deadline):calls.append(argv);return json.dumps(value)
    execution=Execution(SimpleNamespace(timed_command=command,data=tmp_path,runtime=tmp_path,
                                      boot_id_reader=lambda:'00000000-0000-4000-8000-000000000001'))
    with pytest.raises(ExecutionError):execution.unique_many([{'domid':1},{'domid':2}],123)
    assert len(calls)==1
