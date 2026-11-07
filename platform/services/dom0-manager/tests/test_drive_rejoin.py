"""Real RecoveryEngine -> Manager/Execution -> real provisioning/receipts.

Only Xen/Store and the clock are external boundaries. This is not a Xen board
or device-application test. Readiness observation cannot claim Active ownership.
"""
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID,uuid4
from unittest.mock import patch

import pytest
from dom0_manager.recovery.engine import RecoveryEngine
from dom0_manager.recovery.platform_recovery import PlatformRecovery
from dom0_manager.recovery.policy import RecoveryPolicy
from dom0_manager.recovery.state import RecoveryStore,RecoveryStatus
from dom0_manager.recovery.model import Judgment,FailureReason,HealthState,VmHealth
from dom0_manager.recovery.protection import GatewayProtection
from fake_gateway import FakeGateway
from dom0_manager.recovery.rejoin import RejoinCoordinator,ProductionBootstrap,production,valid_vm_trust_payload
from xenova_mqtt import Identity,topics
from xenova_mqtt.client import Message
from xenova_mqtt.envelope import build
from xenova_mqtt.drive_binding import atomic_json

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'platform/tests'))
import test_xenova_network_zephyr as network
from xenova_drive_binding import observe
import xenova_vm

def test_legacy_memory_pressure_wire_is_readable_without_granting_recovery():
    payload={'trust':'UNTRUSTED','reason':'MEMORY_PRESSURE','metric':'memory.kernel_heap.used_bytes',
        'used_percent':89,'threshold_percent':85,'consecutive_samples':5,'command_id':'memory-1'}
    assert valid_vm_trust_payload(payload)
    assert not valid_vm_trust_payload({**payload,'used_percent':80})
    assert not valid_vm_trust_payload({**payload,'consecutive_samples':1})

@pytest.fixture
def fixture():
    f=network.NetworkPlatformTest('runTest');f.setUp()
    try:f.upgrade_start();yield f
    finally:f.doCleanups()

@pytest.mark.parametrize('present',[False,True])
@pytest.mark.parametrize('warm',[False,True])
def test_engine_completion_provisions_current_execution_then_standby_readiness(fixture,tmp_path,monkeypatch,present,warm):
    f=fixture;vm=f.zid;other=str(uuid4());current=f.state();before=current['execution_id']
    health=None
    # External Xen boundary: the original domain disappeared, or it is still running but its app heartbeat stopped.
    if not present:f.xen.rows=[];reason=FailureReason.DOMAIN_ABSENT
    else:
        policy=tmp_path/'execution-policy.json'
        policy.write_text(__import__('json').dumps({'schema_version':'1.0','autostart_stopped':None,
            'manual_running':None,'force_restart_vm_ids':[vm]}))
        f.p.execution_policy_path=policy  # isolated FakeXen approval, never a board policy
        # Force evidence gate (S15P31E110-347): current UNHEALTHY evidence for the platform's current execution.
        reason=FailureReason.APP_HEARTBEAT_LOST
        health=VmHealth(vm_id=UUID(vm),state=HealthState.UNHEALTHY,desired_state='running',xen_state='running',heartbeat_age_s=None,
            reason=reason,manager_seq=1,intent_known=True,domid=current['domid'],execution_id=current['execution_id'])
    # Gateway boundary (S15P31E110-346): fake provider whose binding is the platform's current execution.
    platform=PlatformRecovery(f.m);view=platform.view(UUID(vm))
    gateway=FakeGateway(view['host_boot_id'],lambda:f.ticks)
    gateway.bind(vm,view['execution_id'],revision=view['intent_revision'],generation=view['generation'])
    protection=GatewayProtection(gateway,deadline_s=5.0,call_budget_s=1.0,clock=lambda:f.ticks)
    engine=RecoveryEngine(RecoveryPolicy(),platform,SimpleNamespace(health=lambda vm_id:health if str(vm_id)==vm else None),
        RecoveryStore(tmp_path/'recoveries.json'),clock=lambda:f.ticks,inline=True,drive_vm_ids=[vm,other],protection=protection)
    engine.on_judgment(Judgment(uuid4(),UUID(vm),reason,f.ticks,'zephyr','running',
        current['intent_revision'],None,None,None))
    with patch.object(xenova_vm.os,'sync'):engine.tick()
    record=engine.records()[0]
    assert gateway.count('submit') == int(present)
    protected=[row['spec']['target_execution_id'] for row in gateway.rows.values()]
    assert protected==([before] if present else [])  # start-if-absent needs no Gateway protection
    if present:assert record.protection['phase']=='PROTECTED'  # released only after the new execution binds in the Gateway
    assert record.status is RecoveryStatus.COMPLETED and record.execution_id!=before
    observed=observe(f.p,vm);assert observed['execution_id']==record.execution_id
    assert len(f.physical('destroy'))==(1 if present else 0)
    assert observed['execution_owner_kind']=='recovery'
    spec=importlib.util.spec_from_file_location('provision_xenova_telemetry',ROOT/'platform/scripts/provision-xenova-telemetry.py')
    api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
    monkeypatch.setitem(sys.modules,'provision_xenova_telemetry',api)
    applied=[]
    class Store:
        def __init__(self,channel):self.channel=channel
        def apply(self,context,bootstrap,previous=None):applied.append((self.channel,dict(context),dict(bootstrap)))
        def close(self):pass
    monkeypatch.setattr(api,'XenStore',Store)
    # Keep the production provenance loader; route its filesystem boundary to
    # this actual Manager/Execution fixture, never the workstation's /run.
    import xenova_telemetry_support as telemetry_support
    metadata=telemetry_support.metadata
    monkeypatch.setattr(telemetry_support,'metadata',lambda context:metadata(context,runtime=f.p.runtime,data=f.p.data))
    monkeypatch.setattr(telemetry_support,'capture_supported',lambda:False)
    credentials={vm:{}}
    for channel in ('drive','telemetry'):
        path=tmp_path/(channel+'.json');atomic_json(path,{'vm_id':vm,'username':vm,'password':'test-only',
            'broker_ipv4':'192.0.2.1','port':1883,'trusted_internal_plaintext':True});credentials[vm][channel]=str(path)
    authority=tmp_path/'control.json';atomic_json(authority,{'generation':42,'mode':'AUTO'})
    config={'vm_ids':[vm,other],'receipt_dir':str(tmp_path/'receipts'),'binding_file':str(tmp_path/'binding.json'),
        'authority_file':str(authority),'credentials':credentials}
    if warm:
        config['readiness_profile']='warm-drive-1'
        config['app_state_models']={'autonomous':'stateless','manual':'stateless'}
    def current(identity):return observe(f.p,identity) if identity==vm else {'status':'stopped'}
    coordinator=RejoinCoordinator(config,current,ProductionBootstrap(config,current,uid=os.geteuid()),
        observed['host_boot_id'],clock=lambda:f.ticks,uid=os.geteuid())
    try:
        assert coordinator._work(vm)['reason']=='ENGINE_COMPLETION_PENDING' and not applied
        coordinator.completed(engine.completed_executions());coordinator.pump()
        end=time.monotonic()+5
        while coordinator.cache.current(vm) is None:
            assert time.monotonic()<end;time.sleep(.01);coordinator.pump()
        binding=coordinator.cache.current(vm)
        if warm:assert binding['app_state_models']==config['app_state_models']
        assert binding['execution_id']==record.execution_id and len(applied)==2
        assert applied[0][2]['boot_id']==applied[1][2]['boot_id']
        assert applied[0][2]['publisher_id']!=applied[1][2]['publisher_id']
        for i in range(34):
            f.ticks+=.1;coordinator._rows[vm]={**binding,'observed_monotonic_ms':int(f.ticks*1000)}
            coordinator._due={vm:f.ticks+1,other:f.ticks+1};coordinator.pump()
            identity=Identity(vm_id=vm,app_id='autonomous',boot_id=binding['boot_id'],
                agent_instance_id=binding['app_instances']['autonomous'] if warm else binding['app_instance_id'])
            payload={'applied_generation':42,'progress_seq':str(i+1),'readiness':'READY','reason':None}
            if warm:payload.update(profile='warm-drive-1',check_seq=str(i+1),progress_seq='0',phase='WAITING',readiness='QUALIFIED')
            env=build(identity,'app.lifecycle' if warm else 'app.readiness',payload,i+1)
            coordinator.on_readiness(Message(topics.vm_app_events(vm,'autonomous'),env,f.ticks))
            f.ticks+=.02  # actual asynchronous queue: binding refresh occurs after receive
            coordinator.pump()
        assert coordinator.records[vm]['stage']==('QUALIFIED_OBSERVED' if warm else 'READY_OBSERVED')
        assert record.status is RecoveryStatus.COMPLETED  # meaning did not become app/ownership completion
        assert 'active_vm' not in coordinator.records[vm]
        # A valid negative observation must revoke readiness even without a
        # new calculation. Warm uses a new real supervision check instead.
        f.ticks+=.01;payload.update(readiness='NOT_READY',reason='INPUT_STALE')
        if warm:payload['check_seq']='35'
        env=build(identity,'app.lifecycle' if warm else 'app.readiness',payload,35)
        coordinator.on_readiness(Message(topics.vm_app_events(vm,'autonomous'),env,f.ticks));coordinator.pump()
        assert coordinator.records[vm]['stage']=='PREPARING'
        f.ticks+=.31;coordinator.pump();assert coordinator.records[vm]['stage']=='PREPARING'
        if warm:
            home=Path(config['receipt_dir'])/vm/record.execution_id
            receipt,_=api.private_json(home/'drive.json')
            credential,source=api.credentials(Path(credentials[vm]['drive']),vm)
            with api.receipt_lock(home/'drive.json'):
                rotated=api.reinitialize_app(receipt['context'],credential,source,home/'drive.json',
                    app='autonomous',expected_instance=binding['app_instances']['autonomous'],request_id=str(uuid4()),
                    store=Store('drive'),observe=lambda:{k:current(vm)[k] for k in receipt['context']})
            refreshed=coordinator._work(vm)
            assert refreshed['app_instances']['autonomous']==rotated['instance_id']
            assert refreshed['app_instances']['manual']==binding['app_instances']['manual']
            assert refreshed['execution_id']==binding['execution_id'] and len(applied)==3
            class Broken(Store):
                quiescent=True
                def apply(self,*args):raise OSError('uncertain app-only Store update')
            try:
                with api.receipt_lock(home/'drive.json'):
                    api.reinitialize_app(receipt['context'],credential,source,home/'drive.json',app='autonomous',
                        expected_instance=rotated['instance_id'],request_id=str(uuid4()),store=Broken('drive'),
                        observe=lambda:{k:current(vm)[k] for k in receipt['context']})
            except OSError:pass
            else:raise AssertionError('uncertain effect must be reported')
            partial=coordinator._work(vm)
            assert partial['status']=='observed' and partial['app_status']=={'autonomous':'provisioning','manual':'observed'}
            assert partial['app_instances']['manual']==binding['app_instances']['manual']
            cold=ProductionBootstrap(config,current,uid=os.geteuid())(current(vm))
            assert cold['app_status']==partial['app_status'] and cold['app_instances']==partial['app_instances']
            assert len(applied)==3  # no Store replay on Worker restart
            coordinator._rows[vm]=partial;coordinator._due={vm:f.ticks+1,other:f.ticks+1};coordinator.pump()
            assert coordinator.cache.accepts(vm,partial['boot_id'],partial['app_instances']['manual'],app='manual')
            assert not coordinator.cache.accepts(vm,partial['boot_id'],rotated['instance_id'],app='autonomous')
        with patch.object(xenova_vm.os,'sync'):f.control('stop')
        assert coordinator._work(vm)['status']=='stopped'
        assert len(applied)==(3 if warm else 2)  # only the explicit root app update adds one Store write
    finally:coordinator.stop();engine.shutdown()

def test_missing_mount_is_rejected_before_receipt_creation(tmp_path,monkeypatch):
    config=tmp_path/'config.json';atomic_json(config,{'schema_version':'1.0-drive-rejoin-config'})
    monkeypatch.setattr('dom0_manager.recovery.rejoin.private_json',lambda _: {'schema_version':'1.0-drive-rejoin-config'})
    monkeypatch.setattr('os.path.ismount',lambda _:False)
    with pytest.raises(ValueError,match='mounted'):production(config,tmp_path/'missing-data')
    assert not (tmp_path/'missing-data').exists()


@pytest.mark.parametrize('failure',[None,'receipt','internal','observe','stop','execution','intent'])
def test_bound_refresh_reads_receipt_before_one_current_incarnation_check(fixture,tmp_path,failure):
    f=fixture;vm=f.zid;row=observe(f.p,vm);calls=[]
    identity={'boot_id':str(uuid4()),'app_instance_id':str(uuid4())}
    class Bootstrap:
        def refresh(self,value):
            calls.append('receipt')
            if failure=='receipt':raise OSError('receipt read failed')
            if failure=='internal':raise TypeError('receipt implementation defect')
            return dict(identity)
        def __call__(self,value):
            calls.append('provision');return dict(identity)
    def current(target):
        calls.append('observe')
        if failure=='observe':raise xenova_vm.PlatformError('incarnation unavailable')
        if failure=='stop':return {**row,'status':'stopped'}
        if failure=='execution':return {**row,'execution_id':str(UUID(int=300))}
        if failure=='intent':return {**row,'intent_revision':row['intent_revision']+1}
        return observe(f.p,target)
    coordinator=RejoinCoordinator({'vm_ids':[vm,str(uuid4())],
        'receipt_dir':str(tmp_path/'receipts'),'binding_file':str(tmp_path/'bindings.json')},
        current,Bootstrap(),row['host_boot_id'],uid=os.geteuid())
    coordinator._rows[vm]={**row,**identity}
    try:
        if failure in ('receipt','internal','observe'):
            error={'receipt':OSError,'internal':TypeError,'observe':xenova_vm.PlatformError}[failure]
            with pytest.raises(error):coordinator._work(vm)
            assert calls==(['receipt'] if failure=='internal' else ['receipt','observe'])
        elif failure=='execution':
            result=coordinator._work(vm)
            assert result['execution_id']==str(UUID(int=300))
            assert calls==['receipt','observe','provision','observe']
        else:
            result=coordinator._work(vm)
            assert calls==['receipt','observe']
            if failure=='stop':assert result['status']=='stopped' and 'app_instance_id' not in result
            else:
                assert result['execution_id']==row['execution_id']
                assert result['intent_revision']==row['intent_revision']+int(failure=='intent')
    finally:coordinator.stop()


def test_unchanged_binding_is_not_rewritten_or_renewed_and_failed_publish_recovers(fixture,tmp_path,monkeypatch):
    f=fixture;row=observe(f.p,f.zid);now=[f.ticks];other=str(uuid4())
    config={'vm_ids':[f.zid,other],'receipt_dir':str(tmp_path/'receipts'),
            'binding_file':str(tmp_path/'bindings.json')}
    coordinator=RejoinCoordinator(config,None,None,row['host_boot_id'],clock=lambda:now[0],uid=os.geteuid())
    coordinator._rows[f.zid]={**row,'boot_id':str(uuid4()),'app_instance_id':str(uuid4())}
    coordinator._due={f.zid:now[0]+100,other:now[0]+100}
    import dom0_manager.recovery.rejoin as rejoin
    writes=[];fail=[False];atomic=rejoin.atomic_json
    def sink(path,value):
        writes.append(value)
        if fail[0]:raise OSError('trial binding sink unavailable')
        return atomic(path,value)
    monkeypatch.setattr(rejoin,'atomic_json',sink)
    try:
        coordinator.pump();before=Path(config['binding_file']).read_bytes()
        assert coordinator.cache.current(f.zid) is not None
        for _ in range(35):now[0]+=.1;coordinator.pump()
        assert len(writes)==1 and Path(config['binding_file']).read_bytes()==before
        assert coordinator.cache.current(f.zid) is None  # no timestamp laundering
        coordinator._rows[f.zid]={**coordinator._rows[f.zid],'observed_monotonic_ms':int(now[0]*1000)}
        fail[0]=True
        with pytest.raises(OSError):coordinator.pump()
        assert coordinator.cache.current(f.zid) is None
        fail[0]=False;coordinator.pump()
        assert len(writes)==3 and coordinator.cache.current(f.zid) is not None
    finally:coordinator.stop()


@pytest.mark.parametrize('status',['stopped','absent'])
def test_same_negative_row_renews_only_after_a_real_observation_finishes(fixture,tmp_path,status):
    from concurrent.futures import Future
    f=fixture;now=[f.ticks];other=str(uuid4());host=observe(f.p,f.zid)['host_boot_id']
    coordinator=RejoinCoordinator({'vm_ids':[f.zid,other],'receipt_dir':str(tmp_path/'receipts'),
        'binding_file':str(tmp_path/'bindings.json')},None,None,host,clock=lambda:now[0],uid=os.geteuid())
    coordinator._due={f.zid:now[0]+100,other:now[0]+100}
    try:
        for _ in range(6):
            now[0]+=1
            future=Future();future.set_result({'vm_id':f.zid,'status':status,'host_boot_id':host})
            coordinator._futures[f.zid]=future;coordinator.pump()
            coordinator._due[f.zid]=now[0]+100
            assert coordinator.cache.observation_status(f.zid)==status
        now[0]+=3.1;coordinator.pump()
        assert coordinator.cache.observation_status(f.zid)=='unavailable'
    finally:coordinator.stop()


@pytest.fixture
def cadence(fixture,tmp_path):
    """Control executor completion while preserving actual observed binding data."""
    from concurrent.futures import Future
    f=fixture;row=observe(f.p,f.zid);now=[f.ticks];other=str(uuid4());calls=[]
    identity={'boot_id':str(uuid4()),'app_instance_id':str(uuid4())}
    coordinator=RejoinCoordinator({'vm_ids':[f.zid,other],'receipt_dir':str(tmp_path/'receipts'),
        'binding_file':str(tmp_path/'bindings.json')},None,None,row['host_boot_id'],
        clock=lambda:now[0],uid=os.geteuid())
    coordinator._executor.shutdown(wait=False,cancel_futures=True)
    class Executor:
        def submit(self,work,vm):
            future=Future();calls.append((now[0],vm,future));return future
        def shutdown(self,**kwargs):
            for _,_,future in calls:future.cancel()
    coordinator._executor=Executor();coordinator._due[other]=now[0]+100
    try:yield SimpleNamespace(coordinator=coordinator,now=now,vm=f.zid,calls=calls,row={**row,**identity})
    finally:coordinator.stop()


def test_successful_probe_keeps_its_release_cadence_and_original_observation(cadence):
    c=cadence;coordinator=c.coordinator;released=c.now[0]
    coordinator.pump();assert len(c.calls)==1
    c.now[0]=released+.2;c.calls[0][2].set_result(c.row);coordinator.pump()
    assert coordinator._due[c.vm]==pytest.approx(released+1)
    assert len(c.calls)==1
    assert coordinator.cache.current(c.vm)['observed_monotonic_ms']==c.row['observed_monotonic_ms']
    c.now[0]=released+.99;coordinator.pump();assert len(c.calls)==1
    c.now[0]=released+1;coordinator.pump()
    assert len(c.calls)==2 and c.calls[1][0]==pytest.approx(released+1)
    assert coordinator._futures[c.vm] is c.calls[1][2]


@pytest.mark.parametrize('reason,scope', [('MEMORY_PRESSURE','warning'),('AUTO_DEADLINE_MISS','function')])
def test_fw32_advisory_keeps_original_evidence_without_vm_recovery(cadence,reason,scope):
    c=cadence;coordinator=c.coordinator
    coordinator.cache.install({'schema_version':'1.0-drive-bindings','host_boot_id':coordinator.host,
        'observed_monotonic_ms':int(c.now[0]*1000),'expires_monotonic_ms':int(c.now[0]*1000)+3000,
        'vms':{c.vm:c.row}})
    payload={'trust':'UNTRUSTED','reason':reason,'command_id':'original-command'}
    if reason=='MEMORY_PRESSURE':payload.update(metric='memory.kernel_heap.used_bytes',used_percent=88,
        threshold_percent=85,consecutive_samples=5)
    else:payload['consecutive_misses']=5
    envelope=build(Identity(vm_id=c.vm,app_id='autonomous',boot_id=c.row['boot_id'],
        agent_instance_id=c.row['app_instance_id']),'vm.trust',payload,1)
    message=Message(topics.vm_app_events(c.vm,'autonomous'),envelope,c.now[0])
    coordinator.on_trust(message)
    assert coordinator.trust_events()==[{'vm_id':c.vm,'execution_id':c.row['execution_id'],
        'command_id':'original-command','scope':scope,'reason':reason,'received_monotonic':c.now[0]}]
    diagnostic=coordinator.legacy_fault_reports()[0]
    assert diagnostic['reason']==reason and diagnostic['scope']==scope and diagnostic['reports']==1
    assert diagnostic['evidence']=={key:value for key,value in payload.items() if key not in ('reason','command_id')}
    diagnostic['evidence'].clear()
    assert coordinator.legacy_fault_reports()[0]['evidence']['trust']=='UNTRUSTED'
    # QoS duplicates and new command IDs cannot allocate unbounded diagnostic state.
    for _ in range(80):coordinator.on_trust(message)
    assert len(coordinator.legacy_fault_reports())==1
    assert coordinator.legacy_fault_reports()[0]['reports']==81
    assert all(event['scope']!='vm' for event in coordinator.trust_events())
    before=coordinator.legacy_fault_reports()
    for changed in ({'boot_id':str(uuid4())},{'agent_instance_id':str(uuid4())}):
        coordinator.on_trust(Message(message.topic,{**envelope,**changed},c.now[0]))
    coordinator.on_trust(Message(message.topic,envelope,c.now[0],retained=True))
    c.now[0]+=3.1;coordinator.on_trust(Message(message.topic,envelope,c.now[0]))
    assert coordinator.legacy_fault_reports()==before


def test_legacy_advisory_cannot_mix_two_binding_snapshots(cadence,monkeypatch):
    c=cadence;coordinator=c.coordinator
    def snapshot(row):return {'schema_version':'1.0-drive-bindings','host_boot_id':coordinator.host,
        'observed_monotonic_ms':int(c.now[0]*1000),'expires_monotonic_ms':int(c.now[0]*1000)+3000,'vms':{c.vm:row}}
    coordinator.cache.install(snapshot(c.row))
    replacement={**c.row,'execution_id':str(uuid4()),'boot_id':str(uuid4()),'app_instance_id':str(uuid4())}
    current=coordinator.cache.current;swapped=[False]
    def overlap(*args,**kwargs):
        old=current(*args,**kwargs)
        if not swapped[0]:swapped[0]=True;coordinator.cache.install(snapshot(replacement))
        return old
    monkeypatch.setattr(coordinator.cache,'current',overlap)
    envelope=build(Identity(vm_id=c.vm,app_id='autonomous',boot_id=replacement['boot_id'],
        agent_instance_id=replacement['app_instance_id']),'vm.trust',
        {'trust':'UNTRUSTED','reason':'AUTO_DEADLINE_MISS','consecutive_misses':5,'command_id':'new-boot'},1)
    coordinator.on_trust(Message(topics.vm_app_events(c.vm,'autonomous'),envelope,c.now[0]))
    assert coordinator.legacy_fault_reports()==[] and coordinator.trust_events()==[]


def test_release_anchor_uses_dispatch_time_after_other_completions(cadence):
    from concurrent.futures import Future
    c=cadence;coordinator=c.coordinator;released=c.now[0]
    other=next(vm for vm in coordinator.vm_ids if vm!=c.vm)
    future=Future();future.set_result({'vm_id':other,'status':'stopped','host_boot_id':c.row['host_boot_id']})
    result=future.result
    def delayed_result():
        c.now[0]+=.3
        return result()
    future.result=delayed_result
    coordinator._futures[other]=future;coordinator.pump()
    assert len(c.calls)==1 and c.calls[0][0]==pytest.approx(released+.3)
    assert coordinator._due[c.vm]==pytest.approx(c.calls[0][0]+1)


def test_late_success_coalesces_once_without_overlapping_or_renewing_old_data(cadence):
    c=cadence;coordinator=c.coordinator;released=c.now[0]
    coordinator.pump()
    c.now[0]=released+3.2
    for _ in range(3):coordinator.pump()
    assert len(c.calls)==1  # A pending probe owns its lane even after its due time.
    c.calls[0][2].set_result(c.row);coordinator.pump()
    assert len(c.calls)==2 and c.calls[1][0]==pytest.approx(c.now[0])
    assert coordinator.cache.current(c.vm) is None  # Completion cannot renew the original sample.
    assert coordinator._rows[c.vm]['observed_monotonic_ms']==c.row['observed_monotonic_ms']
    for _ in range(3):coordinator.pump()
    assert len(c.calls)==2
    next_due=c.now[0]+1
    c.now[0]+=.1;c.calls[1][2].set_result({**c.row,'observed_monotonic_ms':int(c.now[0]*1000)})
    coordinator.pump()
    assert len(c.calls)==2 and coordinator._due[c.vm]==pytest.approx(next_due)
    c.now[0]=next_due;coordinator.pump();assert len(c.calls)==3


@pytest.mark.parametrize('outcome',['error','stopped','absent','provisioning'])
def test_failed_or_nonobserved_probe_keeps_completion_based_backoff(cadence,outcome):
    c=cadence;coordinator=c.coordinator;released=c.now[0]
    coordinator.pump();c.now[0]=released+2.3
    future=c.calls[0][2]
    if outcome=='error':future.set_exception(OSError('probe unavailable'))
    else:future.set_result({**c.row,'status':outcome})
    coordinator.pump()
    assert len(c.calls)==1 and coordinator._due[c.vm]==pytest.approx(c.now[0]+1)
    c.now[0]+=.99;coordinator.pump();assert len(c.calls)==1
    c.now[0]=released+3.3;coordinator.pump();assert len(c.calls)==2
