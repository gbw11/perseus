"""A concurrent newer scheduler sample must not force a whole live list retry."""
import threading
import time
from unittest.mock import patch

import pytest

from test_xenova_batch_read_queries import pair
from xenova_execution import same_read_execution
from xenova_management import ReadContext
from xenova_vm import xen_state


@pytest.mark.parametrize('left', ['------','r-----','-b----','rb----'])
@pytest.mark.parametrize('right', ['------','r-----','-b----','rb----'])
def test_live_scheduler_variants_keep_same_verified_execution(left, right):
    a={'xen_state':xen_state(left),'xen_state_flags':left,'execution_id':'same','domid':2}
    b={**a,'xen_state':xen_state(right),'xen_state_flags':right}
    assert same_read_execution(a,b)


@pytest.mark.parametrize('key,value', [
    ('execution_id','replacement'),('domid',3),('xen_unique_id','99'),
    ('lifecycle_generation',2),('configuration_revision','changed'),
    ('desired_state','stopped'),('intent_revision',2),('boot_admission',{'status':'denied'}),
    ('execution_status','unmanaged'),('presence','absent'),
])
def test_stable_fact_changes_still_require_recheck(key,value):
    a={'xen_state':'blocked','xen_state_flags':'-b----',key:'before'}
    assert not same_read_execution(a,{**a,key:value,'xen_state':'running','xen_state_flags':'r-----'})


@pytest.mark.parametrize('flags', ['--p---','---s--','---sc-','-----d','broken','r---c-',None])
def test_non_live_or_invalid_flags_are_not_scheduler_noise(flags):
    a={'xen_state':'blocked','xen_state_flags':'-b----'}
    assert not same_read_execution(a,{'xen_state':'running','xen_state_flags':flags})


@pytest.mark.parametrize('new_flags,expected_rounds', [('r-----',1),('--p---',2)])
def test_delayed_read_keeps_own_clock_and_preserves_newer_publication(pair,new_flags,expected_rounds):
    pair.p.clock=time.monotonic
    identity=next(iter(pair.p.records(validate_assets=False).values()))['uuid']
    initial=pair.m.fork().call('get',{'vm_id':identity},budget_ms=5000)
    assert initial['ok'],initial
    domid=initial['data']['vm']['domid']
    pair.f.xen.states={domid:'-b----'}
    entered,release=threading.Event(),threading.Event()
    original=ReadContext.validate
    request=pair.m.fork();responses=[]
    def delayed(read):
        if threading.current_thread().name=='older-read' and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(read)
    with patch.object(ReadContext,'validate',delayed):
        worker=threading.Thread(target=lambda:responses.append(request.call('get',{'vm_id':identity},budget_ms=5000)),name='older-read')
        worker.start()
        try:
            assert entered.wait(5)
            pair.f.xen.states[domid]=new_flags
            newer=pair.m.fork().call('get',{'vm_id':identity},budget_ms=5000)
            assert newer['ok'],newer
            path=pair.p.runtime/'observations'/(identity+'.json')
            newer_bytes=path.read_bytes()
        finally:
            release.set();worker.join(5)
    assert not worker.is_alive()
    assert responses[0]['ok'],responses
    assert request.last_trace['summary']['read.round']['calls']==expected_rounds
    old=responses[0]['data']['vm'];fresh=newer['data']['vm']
    if expected_rounds==1:
        assert old['xen_state']=='blocked' and fresh['xen_state']=='running'
        assert old['observed_monotonic_ms']<fresh['observed_monotonic_ms']
        assert old['observation_revision']==fresh['observation_revision']
        assert path.read_bytes()==newer_bytes
    else:
        assert old['xen_state']=='paused'
