"""Both app supervisors are required; fresh functional restrictions are not VM death."""
from dataclasses import replace
from uuid import uuid4
from xenova_mqtt.lifecycle import parse
from dom0_manager.recovery.model import HealthState
from test_recovery_watcher import World,obs,ZEPHYR

def setup():
    world=World();boot=str(uuid4());instances={app:str(uuid4()) for app in ('autonomous','manual')}
    observation=replace(obs(ZEPHYR,kind='zephyr',expected=True),execution_binding_available=True,
        expected_app_boot_id=boot,expected_app_instances=instances)
    world.tick(observation);return world,boot,instances,observation

def report(world,boot,instances,app,check,*,progress=0,phase='WAITING',state='QUALIFIED',reason=None):
    value=parse(dict(profile='warm-drive-1',applied_generation=42,check_seq=str(check),progress_seq=str(progress),
        phase=phase,readiness=state,reason=reason))
    return world.apps.record_lifecycle(ZEPHYR,app,boot,instances[app],value,world.clock.now)

def test_waiting_without_any_calculation_is_healthy_for_both_apps():
    w,boot,ids,o=setup()
    for n in range(1,1001):
        w.clock.now+=.1
        for app in ids:assert report(w,boot,ids,app,n)
        assert w.tick(o)==[] and w.health(ZEPHYR).state is HealthState.HEALTHY
    assert w.apps.last(ZEPHYR) is None  # no synthetic heartbeat
    assert all(value.progress==0 for value in w.apps.supervision_pair(ZEPHYR,boot,ids))

def test_one_app_cannot_cover_missing_app_or_new_instance():
    w,boot,ids,o=setup();report(w,boot,ids,'manual',1)
    assert w.tick(o)==[] and w.health(ZEPHYR).state is HealthState.OBSERVATION_UNAVAILABLE
    report(w,boot,ids,'autonomous',1);assert w.tick(o)==[] and w.health(ZEPHYR).ok
    new={**ids,'autonomous':str(uuid4())};o=replace(o,expected_app_instances=new)
    w.clock.now+=.1;report(w,boot,ids,'manual',2)
    assert w.tick(o)==[] and w.health(ZEPHYR).state is HealthState.OBSERVATION_UNAVAILABLE
    report(w,boot,new,'autonomous',1);assert w.tick(o)==[] and w.health(ZEPHYR).ok

def test_fresh_supervision_cannot_hide_stalled_work_or_function_restriction():
    w,boot,ids,o=setup()
    for n in range(1,12):
        w.clock.now+=.1;report(w,boot,ids,'manual',n)
        report(w,boot,ids,'autonomous',n,progress=1,phase='EXECUTING',state='READY')
        assert not w.tick(o)
    assert w.health(ZEPHYR).state is HealthState.OBSERVATION_UNAVAILABLE
    for n in range(12,20):
        w.clock.now+=.1;report(w,boot,ids,'manual',n)
        assert report(w,boot,ids,'autonomous',n,progress=1,state='NOT_READY',reason='INPUT_STALE')
        assert not w.tick(o)  # input failure cannot authorize VM recovery
        assert w.health(ZEPHYR).state is HealthState.OBSERVATION_UNAVAILABLE

def test_duplicate_check_does_not_refresh_and_xen_failure_still_wins():
    w,boot,ids,o=setup()
    for app in ids:assert report(w,boot,ids,app,1)
    first=w.apps.supervision_pair(ZEPHYR,boot,ids)[0].received_monotonic
    w.clock.now+=1
    for app in ids:assert not report(w,boot,ids,app,1)
    assert w.apps.supervision_pair(ZEPHYR,boot,ids)[0].received_monotonic==first
    from dom0_manager.models import VmState
    result=w.tick(replace(o,xen_state=VmState.crashed))
    assert result and w.health(ZEPHYR).state is HealthState.UNHEALTHY
