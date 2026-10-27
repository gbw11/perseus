#!/usr/bin/env python3
"""Audit-only counterexamples against product code; no board/network/device use.

Exit zero means the audit probes ran, not that every contract passed. The JSON
keeps observed behaviour and unmet guarantees separate. All writes use a new
temporary tree; only Xen/Store/clock/output boundaries are explicit substitutes.
"""
from pathlib import Path
import argparse,copy,importlib.util,json,os,sys,tempfile,threading,time
from dataclasses import replace
from uuid import UUID,uuid4
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
for p in ('platform/tests','platform/services/gateway','platform/services/dom0-manager',
          'platform/services/vm-agent/mqtt-client','platform/yocto/meta-xenova/recipes-core/xenova-platform/files'):
    sys.path.insert(0,str(ROOT/p))

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);return module

def probe_delivery(_):
    t=load('audit_watcher_fixture','platform/services/dom0-manager/tests/test_recovery_watcher.py')
    output={}
    for kind,verified in (('linux',False),('zephyr',False),('zephyr',True)):
        w=t.World();v=t.obs(kind=kind,expected=kind=='zephyr')
        if verified:v=replace(v,execution_binding_available=True,execution_id=str(uuid4()),
                              expected_app_boot_id=str(uuid4()),expected_app_instance_id=str(uuid4()))
        w.tick(v);w.prove('agent' if kind=='linux' else 'app')
        judgments=w.run(v,100)
        output[f'{kind}_{"verified" if verified else "legacy"}']={
            'target_ever_signaled':False,'other_vm_proved_channel':True,
            'judgments':[j.reason.value for j in judgments],'health':w.health().state.value}
    return {'guarantee':'per-VM delivery failure is not inferred from another VM','observed':output,
            'result':'GAP_LEGACY_LINUX; VERIFIED_FIRST_SIGNAL_GUARD_PASSES'}

def probe_modes(path):
    t=load('audit_verified_fixture','platform/services/gateway/tests/test_verified.py')
    w=t.World(path/'authority',mode='MANUAL');w.perception=False;w.arm()
    assert w.m.output_permitted and not w.m._ready(t.A,'autonomous',w.t)
    w.m._change(t.Mode.AUTO_READY,t.A,'audit',w.t,arm=True);generation=w.m.generation
    w.m.note_delivery(generation,'acknowledged',w.t)
    for app,state,reason in [('autonomous','NOT_READY','INPUT_STALE'),('manual','READY',None)]:
        w.m.on_readiness(app,t.A,{'applied_generation':generation,'progress_seq':'999999',
                                'readiness':state,'reason':reason},w.t)
    w.m.step(w.t)
    assert w.m.phase=='APPLIED' and not w.m._ready(t.A,'autonomous',w.t)
    return {'result':'PASS','manual_operates_without_auto_ready':True,'control_phase':w.m.phase,
            'auto_function_ready':False,'generation_bounce':False,
            'boundary':'real state machine, fixture-controlled app input/completion'}

def probe_binding(path):
    t=load('audit_binding_fixture','platform/services/gateway/tests/test_verified.py');w=t.World(path/'authority')
    results={}
    for status in ('stopped','absent','unavailable','provisioning'):
        w.bind();assert w.cache.current(t.A)
        value=copy.deepcopy(w.cache._value);value['vms'][t.A]={'status':status};w.cache.install(value)
        results[status]=w.cache.current(t.A) is None
    assert all(results.values());return {'result':'PASS','immediate_invalidations':results}

def probe_http_stop(path):
    import test_xenova_recovery_foundation as t
    from xenova_runtime import operation_lock
    from dom0_manager.main import create_app
    from dom0_manager.config import Settings
    from dom0_manager.drivers.platform import PlatformDriver
    from dom0_manager.drivers.mock import MockProvisioner
    from fastapi.testclient import TestClient
    f=t.FoundationTest('runTest');f.setUp()
    try:
        f.start();before=f.p.execution().intent(f.r)['intent_revision']
        app=create_app(Settings(driver='platform'),driver=PlatformDriver(f.m),provisioner=MockProvisioner())
        with TestClient(app) as client,operation_lock(f.p.runtime):
            first=client.post(f'/api/v1/vms/{f.vm_id}/stop')
            intent1=copy.deepcopy(f.p.execution().intent(f.r))
            second=client.post(f'/api/v1/vms/{f.vm_id}/stop')
            intent2=copy.deepcopy(f.p.execution().intent(f.r))
        assert intent1['desired_state']==intent2['desired_state']=='stopped'
        assert first.status_code==second.status_code==503
        assert 'accepted' not in first.json() and 'operation_id' not in first.json()
        return {'result':'FAIL_HTTP_PENDING_CONTRACT','http_status':first.status_code,'body':first.json(),
            'intent_saved_before_physical_stop':True,'revisions':[before,intent1['intent_revision'],intent2['intent_revision']],
            'operation_changed_on_same_http_replay':intent1['accepted_operation_id']!=intent2['accepted_operation_id'],
            'physical_shutdowns':len(f.physical('shutdown')),
            'boundary':'real FastAPI/Service/PlatformDriver/Manager/Execution; actual execution flock; FakeXen only'}
    finally:f.doCleanups()

def probe_stopping(_):
    import test_xenova_recovery_foundation as t
    f=t.FoundationTest('runTest');f.setUp()
    try:
        f.start();spec=f.spec();f.checkpoint('recovery-stopping')
        try:f.recover(spec)
        except t.Interrupted:pass
        f.p.execution_checkpoint=lambda *a:None
        initial=f.call('recovery-operation',{'recovery_attempt_id':spec['recovery_attempt_id']})
        assert initial['data']['attempt']['phase']=='stopping'
        f.ticks=100;out={}
        for present in (True,False):
            if not present:f.xen.rows.clear()
            result=f.recover(spec);out[str(present)]=result['error']['code']
        final=f.call('recovery-operation',{'recovery_attempt_id':spec['recovery_attempt_id']})
        return {'result':'PROTECTION_PASS_OPERATOR_CLOSURE_GAP','original_phase':'stopping',
            'expired_original_replay_codes_by_presence':out,'journal_phase_after':final['data']['attempt']['phase'],
            'new_create_count':len(f.physical('create'))-1,
            'boundary':'real Manager/Recovery/Execution journals; interruption hook before physical shutdown; FakeXen presence'}
    finally:f.doCleanups()

def probe_uncertain_receipt(path):
    api=load('audit_provision','platform/scripts/provision-xenova-telemetry.py');effects=[]
    class Store:
        def apply(self,*args):effects.append(1);raise OSError('injected lost commit response')
    ctx={'vm_id':str(uuid4()),'host_boot_id':str(uuid4()),'execution_id':str(uuid4()),'domid':7,'xen_unique_id':'55'}
    credential={'vm_id':ctx['vm_id'],'username':ctx['vm_id'],'password':'synthetic-test-only',
                'broker_ipv4':'192.0.2.1','port':1883,'trusted_internal_plaintext':True}
    receipt=path/'receipt.json'
    try:api.provision(ctx,credential,{'inode':1},receipt,store=Store(),observe=lambda:ctx)
    except OSError:pass
    before=json.loads(receipt.read_text());retry=None
    try:api.provision(ctx,credential,{'inode':1},receipt,store=Store(),observe=lambda:ctx)
    except ValueError as error:retry=str(error)
    after=json.loads(receipt.read_text());assert before==after and len(effects)==1
    return {'result':'PROTECTION_PASS_OPERATOR_CLOSURE_GAP','phase':after['phase'],'store_calls':len(effects),
            'identities_preserved':True,'retry':retry,'private_receipt_exported':False}

def probe_rejoin_slots(path):
    from dom0_manager.recovery.rejoin import RejoinCoordinator
    from xenova_mqtt.drive_binding import atomic_json
    vms=[str(uuid4()),str(uuid4())];host=str(uuid4());gate=threading.Event();entered=threading.Event()
    authority=path/'authority.json';atomic_json(authority,{'generation':42,'mode':'AUTO'})
    rows={vm:{'status':'observed','vm_id':vm,'host_boot_id':host,'execution_id':str(uuid4()),
        'domid':i+1,'xen_unique_id':str(i+10),'intent_revision':1,'lifecycle_generation':1,
        'observed_monotonic_ms':100000} for i,vm in enumerate(vms)}
    def provision(row):
        if row['vm_id']==vms[0]:entered.set();assert gate.wait(3)
        return {'boot_id':str(uuid4()),'app_instance_id':str(uuid4())}
    c=RejoinCoordinator({'vm_ids':vms,'receipt_dir':str(path/'receipts'),'binding_file':str(path/'bindings.json'),
                        'authority_file':str(authority)},lambda vm:dict(rows[vm]),provision,host,clock=lambda:100,uid=os.geteuid())
    try:
        c.pump();assert entered.wait(1);end=time.monotonic()+2
        while c.cache.current(vms[1]) is None:
            assert time.monotonic()<end;time.sleep(.005);c.pump()
        other_ready=True;clean=c.stop();assert clean is False
        return {'result':'PASS','other_vm_binding_observed_while_one_store_blocked':other_ready,
                'stop_does_not_claim_joined':not clean,'boundary':'real Coordinator/executor/cache; external provisioning gate'}
    finally:gate.set();c.stop()

def probe_resume(path):
    t=load('audit_resume_fixture','platform/services/gateway/tests/test_verified.py')
    from xenova_mqtt import Identity
    from xenova_mqtt.envelope import build
    from xenova_mqtt.client import Message
    from xenova_gateway.runner import CONSOLE_RESUME
    source=(ROOT/'backend/src/main/java/com/xenova/backend/drive/application/DriveResumeRequester.java').read_text()
    assert 'payload.put("requested_by", requestedBy)' in source
    assert 'expected_generation' not in source and 'causation_id' not in source
    w=t.World(path/'authority');w.arm();w.m._change(t.Mode.AUTO_READY,t.A,'audit',w.t,arm=True);w.tick(3.4)
    runner=t.VerifiedGatewayRunner(t.Client(),t.MockSink(),authority=w.auth,bindings=w.cache,vm_ids=(t.A,t.B),clock=lambda:w.t)
    runner.machine=w.m;runner._control_message_id=str(uuid4())
    env=build(Identity(source_id='web-console'),'drive.resume.requested',{'requested_by':'operator'},1)
    runner._feed(w.t,Message(CONSOLE_RESUME,env,w.t));rejected=not w.m._resume_requested
    env['payload']['expected_generation']=w.m.generation;env['causation_id']=runner._control_message_id
    runner._feed(w.t,Message(CONSOLE_RESUME,env,w.t));accepted=bool(w.m._resume_requested)
    assert rejected and accepted
    return {'result':'FAIL_SPRING_VERIFIED_RESUME_CONNECTION','current_payload_rejected':rejected,
        'context_bound_request_accepted':accepted,'boundary':'actual Gateway; payload shape inspected from Java, no Spring runtime executed'}

PROBES={'delivery':probe_delivery,'mode-readiness':probe_modes,'binding-invalidation':probe_binding,
        'http-stop':probe_http_stop,'stopping-owner':probe_stopping,'uncertain-receipt':probe_uncertain_receipt,
        'rejoin-slots':probe_rejoin_slots,'spring-resume-shape':probe_resume}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.resolve().is_relative_to(ROOT):p.error('audit evidence must stay outside product source')
    results={}
    for name,probe in PROBES.items():
        with tempfile.TemporaryDirectory(prefix='xenova-audit-') as directory:
            results[name]=probe(Path(directory));print(name,results[name]['result'],flush=True)
    a.output.write_text(json.dumps({'audit_probes_executed':True,'all_contracts_passed':False,'probes':results},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
