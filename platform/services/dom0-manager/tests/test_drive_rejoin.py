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
from dom0_manager.recovery.model import Judgment,FailureReason
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
def test_engine_completion_provisions_current_execution_then_standby_readiness(fixture,tmp_path,monkeypatch,present):
    f=fixture;vm=f.zid;other=str(uuid4());before=f.state()['execution_id']
    # External Xen boundary: the original domain has crashed/disappeared.
    if not present:f.xen.rows=[]
    else:
        policy=tmp_path/'execution-policy.json'
        policy.write_text(__import__('json').dumps({'schema_version':'1.0','autostart_stopped':None,
            'manual_running':None,'force_restart_vm_ids':[vm]}))
        f.p.execution_policy_path=policy  # isolated FakeXen approval, never a board policy
    engine=RecoveryEngine(RecoveryPolicy(),PlatformRecovery(f.m),SimpleNamespace(health=lambda _:None),
        RecoveryStore(tmp_path/'recoveries.json'),clock=lambda:f.ticks,inline=True)
    engine.on_judgment(Judgment(uuid4(),UUID(vm),FailureReason.DOMAIN_ABSENT,f.ticks,'zephyr','running',
        f.state()['intent_revision'],None,None,None))
    with patch.object(xenova_vm.os,'sync'):engine.tick()
    record=engine.records()[0]
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
        def apply(self,context,bootstrap,previous):applied.append((self.channel,dict(context),dict(bootstrap)))
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
        assert binding['execution_id']==record.execution_id and len(applied)==2
        assert applied[0][2]['boot_id']==applied[1][2]['boot_id']
        assert applied[0][2]['publisher_id']!=applied[1][2]['publisher_id']
        for i in range(34):
            f.ticks+=.1;coordinator._rows[vm]={**binding,'observed_monotonic_ms':int(f.ticks*1000)}
            coordinator._due={vm:f.ticks+1,other:f.ticks+1};coordinator.pump()
            identity=Identity(vm_id=vm,app_id='autonomous',boot_id=binding['boot_id'],agent_instance_id=binding['app_instance_id'])
            env=build(identity,'app.readiness',{'applied_generation':42,'progress_seq':str(i+1),'readiness':'READY','reason':None},i+1)
            coordinator.on_readiness(Message(topics.vm_app_events(vm,'autonomous'),env,f.ticks));coordinator.pump()
        assert coordinator.records[vm]['stage']=='READY_OBSERVED'
        assert record.status is RecoveryStatus.COMPLETED  # meaning did not become app/ownership completion
        assert 'active_vm' not in coordinator.records[vm]
        f.ticks+=.31;coordinator.pump();assert coordinator.records[vm]['stage']=='PREPARING'
        with patch.object(xenova_vm.os,'sync'):f.control('stop')
        assert coordinator._work(vm)['status']=='stopped'
        assert len(applied)==2  # no re-provision/restart just because input/readiness expires
    finally:coordinator.stop();engine.shutdown()

def test_missing_mount_is_rejected_before_receipt_creation(tmp_path,monkeypatch):
    config=tmp_path/'config.json';atomic_json(config,{'schema_version':'1.0-drive-rejoin-config'})
    monkeypatch.setattr('dom0_manager.recovery.rejoin.private_json',lambda _: {'schema_version':'1.0-drive-rejoin-config'})
    monkeypatch.setattr('os.path.ismount',lambda _:False)
    with pytest.raises(ValueError,match='mounted'):production(config,tmp_path/'missing-data')
    assert not (tmp_path/'missing-data').exists()
