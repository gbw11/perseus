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
from dom0_manager.recovery.rejoin import RejoinCoordinator,ProductionBootstrap,production
from xenova_mqtt import Identity,topics
from xenova_mqtt.client import Message
from xenova_mqtt.envelope import build
from xenova_mqtt.drive_binding import atomic_json

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'platform/tests'))
import test_xenova_network_zephyr as network
from xenova_drive_binding import observe
import xenova_vm

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
