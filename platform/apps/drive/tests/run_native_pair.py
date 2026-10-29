#!/usr/bin/env python3
"""Same Zephyr executable twice, real TCP/MQTT/Gateway and independent receiver.

Run as root in a NEW network namespace. Inputs are controlled, calculations are
the existing C algorithms, and output is the real serial encoder with a file
sink. No board, image assembly, CPU-policy change or physical vehicle output.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'platform/services/gateway'),str(ROOT/'platform/services/vm-agent/mqtt-client')]
from xenova_gateway.machine import Mode
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.authority import AuthorityStore
from xenova_mqtt.drive_binding import BindingCache
from xenova_gateway.sink import MockSink
from xenova_gateway.frame import DriveFrame
from xenova_mqtt import Identity, XenovaClient, topics
from xenova_mqtt.envelope import validate
import paho.mqtt.client as mqtt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--tools-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--initial',choices=('a','b'),default='a')
    p.add_argument('--run-label',default='')
    p.add_argument('--readiness-profile',choices=('verified-drive-1','warm-drive-1'),default='verified-drive-1')
    p.add_argument('--manual-session-contract',action='store_true',help='controlled source for manual-session-1; not a Spring acceptance')
    args=p.parse_args()
    if args.manual_session_contract and args.readiness_profile!='warm-drive-1':p.error('session contract requires warm profile')
    logging.basicConfig(level=logging.INFO)
    if os.geteuid() or os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net'):
        p.error('root in a new isolated network namespace required')
    args.output.mkdir(mode=0o700)
    private=args.output.with_name(args.output.name+'-private');private.mkdir(mode=0o700)
    tools=args.tools_root/'root'
    env=dict(os.environ,LD_LIBRARY_PATH=str(tools/'usr/lib/x86_64-linux-gnu'),PYTHONDONTWRITEBYTECODE='1')
    children=[];handles=[];clients=[];threads=[];stop=threading.Event();errors=[];stages=[]
    records=[];wire_lock=threading.Lock();sessions=[];inputs_enabled=threading.Event();inputs_enabled.set()
    vms={label:str(uuid.uuid4()) for label in ('a','b')}
    passwords={key:secrets.token_urlsafe(32) for key in ('a','b','gateway','input','subscriber')}
    guests={};bootstrap_paths={}
    fixture_host_boot=str(uuid.uuid4())
    driver_session=str(uuid.uuid4());driver_state=(['up'],1)
    def write_private(name,value):
        fd=os.open(private/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(value if isinstance(value,str) else json.dumps(value))
        return private/name
    def run(argv):subprocess.run(list(map(str,argv)),env=env,check=True,capture_output=True,timeout=10)
    def spawn(label,argv,settings=env):
        out=(args.output/(label+'.stdout')).open('wb');err=(args.output/(label+'.stderr')).open('wb');handles.extend((out,err))
        proc=subprocess.Popen(list(map(str,argv)),env=settings,stdout=out,stderr=err);children.append(proc);return proc
    def worker(fn):
        try:fn()
        except BaseException as exc:errors.append(repr(exc));stop.set()
    def until(label,predicate,timeout=12):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if errors:raise RuntimeError(errors[0])
            if predicate():
                stages.append({'stage':label,'t':time.monotonic(),'mode':runner.machine.mode.value,
                    'active_vm':runner.machine.active_vm,'generation':runner.machine.generation})
                return
            time.sleep(.01)
        raise TimeoutError(label)
    def client(name,identity):
        c=XenovaClient(identity,host='192.0.2.1',port=18884,username=name,password=passwords[name])
        clients.append(c);return c
    def start_guest(label):
        old=guests.get(label)
        assert old is None or old.poll() is not None
        count=sum(s['label']==label for s in sessions)
        boot=str(uuid.uuid4())
        common={'vm_id':vms[label],'boot_id':boot,'host_boot_id':'','execution_id':'','receipt_id':'',
                'broker_ipv4':'192.0.2.1','username':vms[label],'password':passwords[label],'trusted_internal_plaintext':True}
        paths={}
        entry={'label':label,'vm_id':vms[label],'boot_id':boot,'binary_sha256':digest,'execution_id':str(uuid.uuid4())}
        # Synthetic native execution context, not a Manager/receipt acceptance
        # test. Let the actual guest source retain the same explicit identity.
        common.update(host_boot_id=fixture_host_boot,execution_id=entry['execution_id'])
        for channel,port in (('drive',18884),('telemetry',18883)):
            instance=str(uuid.uuid4());entry[channel+'_instance']=instance
            payload={**common,'publisher_id':instance,'receipt_id':str(uuid.uuid4()),'port':port}
            if channel=='drive' and args.readiness_profile=='warm-drive-1':
                entry['app_instances']={app:str(uuid.uuid4()) for app in ('autonomous','manual')}
                payload.update(drive_profile=args.readiness_profile,auto_instance_id=entry['app_instances']['autonomous'],
                               manual_instance_id=entry['app_instances']['manual'])
            paths[channel]=write_private(f'{label}-{count}-{channel}.json',payload)
        sessions.append(entry)
        bootstrap_paths[label]=paths['drive']
        child_env=dict(env,XT_TEST_BOOTSTRAP=str(paths['telemetry']),XD_TEST_APP_BOOTSTRAP=str(paths['drive']),
                       XT_TEST_INSTANCE_ID=vms[label],XD_TEST_DURATION_MS='120000')
        i=0 if label=='a' else 1
        guests[label]=spawn(f'{label}-{count}',[args.binary,'-rt','-no-color','-eth-if=xdtap'+str(i),
            f'-mac-addr=02:00:5e:00:53:0{i+2}',f'-ipv4-addr=192.0.2.{i+2}',
            '-ipv4-gw=192.0.2.1','-ipv4-nm=255.255.255.0'],child_env)
    runner=None;receivers=[];passed=False
    digest=hashlib.sha256(args.binary.read_bytes()).hexdigest()
    try:
        for argv in (['ip','link','set','lo','up'],['ip','link','add','xdbr','type','bridge'],
                     ['ip','address','add','192.0.2.1/24','dev','xdbr'],['ip','link','set','xdbr','up']):run(argv)
        for i in range(2):
            for argv in (['ip','tuntap','add','dev','xdtap'+str(i),'mode','tap'],
                         ['ip','link','set','xdtap'+str(i),'master','xdbr'],['ip','link','set','xdtap'+str(i),'up']):run(argv)
        passwd=''.join((vms.get(name,name)+':'+value+'\n') for name,value in passwords.items())
        password_file=write_private('passwords',passwd);run([tools/'usr/bin/mosquitto_passwd','-U',password_file])
        acl=('user subscriber\ntopic read xenova/#\nuser gateway\ntopic read xenova/v1/#\n'
             'topic write xenova/v1/vehicle/control\ntopic write xenova/v1/apps/vehicle/events\n'
             'user input\ntopic write xenova/v1/apps/manual/input\ntopic write xenova/v1/apps/perception/events\n'
             'topic write xenova/v1/console/resume\n')
        for label,vm in vms.items():
            acl+=(f'user {vm}\ntopic write xenova/v1/vms/{vm}/apps/autonomous/events\ntopic write xenova/v1/vms/{vm}/apps/manual/events\n'
                  'topic read xenova/v1/vehicle/control\ntopic read xenova/v1/apps/manual/input\n'
                  'topic read xenova/v1/apps/perception/events\n'+f'topic write xenova/v2/vms/{vm}/telemetry/+\n')
        acl_file=write_private('acl',acl)
        config=write_private('broker.conf',f'listener 18884 192.0.2.1\nlistener 18883 192.0.2.1\nuser root\n'
            f'allow_anonymous false\npassword_file {password_file}\nacl_file {acl_file}\npersistence false\nlog_type error\n')
        broker=spawn('broker',[tools/'usr/sbin/mosquitto','-c',config]);time.sleep(.3);assert broker.poll() is None
        # Separate socket and raw payload collector, independent of Gateway's latest cache.
        spec=importlib.util.spec_from_file_location('pair_telemetry_oracle',ROOT/'platform/guests/zephyr/telemetry/contract/tools/validate_contract.py')
        oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
        def on_connect(c,u,f,rc,props):
            if rc.is_failure:errors.append('independent receiver authentication failed');return
            c.subscribe(u,qos=1)
        def on_message(c,u,message):
            try:
                value=json.loads(message.payload)
                if message.topic.startswith('xenova/v2/'):
                    oracle.validate(value,message.topic,message.qos,value['type']=='telemetry.inventory',len(message.payload))
                else:validate(message.topic,value)
                with wire_lock:records.append({'t':time.monotonic(),'topic':message.topic,'message':value})
            except Exception as exc:errors.append('independent receiver: '+repr(exc))
        for version,port in ((1,18884),(2,18883)):
            receiver=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=f'independent-pair-receiver-{version}',userdata=f'xenova/v{version}/#')
            receiver.username_pw_set('subscriber',passwords['subscriber'])
            receiver.on_connect=on_connect;receiver.on_message=on_message
            receiver.connect('192.0.2.1',port);receiver.loop_start();receivers.append(receiver)
        sender=client('input',Identity(source_id='jetson'));assert sender.start(wait_s=3)
        attacker=XenovaClient(Identity(vm_id=vms['b'],app_id='manual'),host='192.0.2.1',port=18884,
            username=vms['a'],password=passwords['a'],client_id='acl-negative-test')
        clients.append(attacker);assert attacker.start(wait_s=3)
        forbidden=[]
        for topic in (topics.vm_app_events(vms['b'],'manual'),topics.app_events('manual')):
            sent=attacker.publish(topic,'app.heartbeat',{'state':'standby','uptime_s':0})
            forbidden.append(sent['message_id'])
        time.sleep(.3)
        assert not any(r['message']['message_id'] in forbidden for r in records)
        attacker.stop()
        gw=client('gateway',Identity(source_id='dom0-gateway'))
        sink=MockSink((args.output/'frames.log').open('w'))
        host=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        authority=AuthorityStore(private/'authority',host,mountpoint=None);authority.initialize(vms[args.initial])
        # Test fixture: restoring a previous AUTO preference must still rearm.
        authority.update(mode='AUTO',reason='isolated_restore_fixture')
        bindings=BindingCache(host)
        runner=VerifiedGatewayRunner(gw,sink,authority=authority,bindings=bindings,vm_ids=(vms['a'],vms['b']),
                                     readiness_profile=args.readiness_profile)
        def bind_current():
            while not stop.wait(.05):
                now=int(time.monotonic()*1000);rows={}
                for label in ('a','b'):
                    child=guests.get(label)
                    if child is None or child.poll() is not None:continue
                    session=next(s for s in reversed(sessions) if s['label']==label)
                    rows[vms[label]]={'status':'observed','execution_id':session['execution_id'],
                        'boot_id':session['boot_id'],'app_instance_id':session['drive_instance'],
                        'intent_revision':1,'lifecycle_generation':0,'domid':1 if label=='a' else 2,
                        'xen_unique_id':'1','observed_monotonic_ms':now}
                    if args.readiness_profile=='warm-drive-1':
                        rows[vms[label]].update(profile=args.readiness_profile,app_instances=session['app_instances'],
                            app_state_models={'autonomous':'stateless','manual':'stateless'})
                bindings.install({'schema_version':'1.0-drive-bindings','host_boot_id':host,
                    'observed_monotonic_ms':now,'expires_monotonic_ms':now+3000,'vms':rows})
        th=threading.Thread(target=worker,args=(bind_current,),daemon=True);th.start();threads.append(th)
        def send_inputs():
            sample=0
            while not stop.wait(.05):
                keys,action=driver_state;payload={'keys':keys}
                if args.manual_session_contract:
                    # This fixture genuinely samples its controlled input state.
                    # It is not evidence of a live product browser/WS session.
                    sample+=1
                    generation=runner.machine.generation if runner.machine else authority.value['generation']
                    payload.update(profile='manual-session-1',session_id=driver_session,sample_seq=str(sample),
                        action_seq=str(action),generation=generation,valid=True,age_ms=0)
                sender.publish(topics.manual_input(),'drive.input',payload)
                if inputs_enabled.is_set():
                    sender.publish(topics.app_events('perception'),'perception.result',
                        {'lane_offset_m':0,'heading_err_rad':0,'stop':False,'obstacles':[]})
        th=threading.Thread(target=worker,args=(send_inputs,),daemon=True);th.start();threads.append(th)
        start_guest('a');start_guest('b')
        th=threading.Thread(target=worker,args=(lambda:runner.run(stop),),daemon=True);th.start();threads.append(th)
        first=args.initial;other='b' if first=='a' else 'a'
        until('initial-disarmed-ready',lambda:runner.machine is not None and
            runner.machine.node_status(vms[first],time.monotonic())=='ready_disarmed' and
            runner.machine.node_status(vms[other],time.monotonic())=='standby',18)
        assert not runner.machine.output_permitted and not [f for _,f,_ in sink.frames if isinstance(f,DriveFrame)]
        def resume():
            until('context-bound-resume-available',lambda:runner._control_message_id is not None)
            sender.publish('xenova/v1/console/resume','drive.resume.requested',
                {'requested_by':'isolated-test','expected_generation':runner.machine.generation},
                causation_id=runner._control_message_id)
        resume()
        until('initial-active-auto',lambda:runner.machine.mode is Mode.AUTO and runner.machine.active_vm==vms[first] and
            runner.machine.node_status(vms[first],time.monotonic())=='active')
        until('standby-prepared',lambda:runner.machine.node_status(vms[other],time.monotonic())=='standby')
        def drive_seen(generation,owner,*,moving):
            return any(r.get('serial_result')=='accepted' and r.get('source') and r['generation']==generation and
                r['source']['vm_id']==owner and (r.get('v_mps',0)>0 if moving else r.get('v_mps')==0)
                for r in list(runner.submissions))
        def neutral_then_action(label):
            nonlocal driver_state
            generation=runner.machine.generation;owner=runner.machine.active_vm
            until(label+'-held-key-stays-stopped',lambda:runner.machine.output_permitted and drive_seen(generation,owner,moving=False))
            assert not drive_seen(generation,owner,moving=True)
            driver_state=([],driver_state[1]+1)
            until(label+'-current-neutral',lambda:runner.machine.movement and runner.machine.movement.neutral_action is not None)
            driver_state=(['up'],driver_state[1]+1)
            until(label+'-new-action-moves',lambda:drive_seen(generation,owner,moving=True))
        if args.manual_session_contract:
            request=str(uuid.uuid4());generation=runner.machine.generation
            for action,arguments in [('prepare',dict(mode='MANUAL',target_vm=vms[first],expected_owner=vms[first],
                    expected_generation=generation,deadline_ms=int(time.monotonic()*1000)+5000)),('refresh',{}),('commit',{})]:
                response=runner.submit_transition(action,request,**arguments)
                until('session-'+action,lambda:not response.empty())
                result=response.get_nowait();assert result['ok'],result
            neutral_then_action('planned-auto-manual')
            until('session-auto-ready',lambda:runner.machine.mode is Mode.AUTO_READY and all(
                runner.machine._qualified(vms[first],app,time.monotonic(),held=True) for app in ('autonomous','manual')))
            resume();until('session-back-auto',lambda:runner.machine.mode is Mode.AUTO and runner.machine.output_permitted)
            until('session-new-context-standby',lambda:runner.machine.node_status(vms[other],time.monotonic())=='standby')
        if args.readiness_profile=='warm-drive-1':
            # Actual actor + durable Authority; guest uses current stateless
            # qualification, then calculates anew after each committed control.
            for label in (other,first):
                request=str(uuid.uuid4());generation=runner.machine.generation;owner=runner.machine.active_vm
                def transition(action,**arguments):
                    response=runner.submit_transition(action,request,**arguments)
                    until('planned-'+label+'-'+action,lambda:not response.empty())
                    result=response.get_nowait();assert result['ok'],result
                    return result['record']
                row=transition('prepare',mode='AUTO',target_vm=vms[label],expected_owner=owner,
                    expected_generation=generation,deadline_ms=int(time.monotonic()*1000)+5000)
                assert row['phase']=='PREPARING' and runner.machine.generation==generation
                assert runner.machine.output_permitted and runner.machine.active_vm==owner
                def owner_outputs():return sum(1 for r in list(runner.submissions) if r.get('serial_result')=='accepted' and
                    r.get('source') and r['source']['vm_id']==owner and r.get('generation')==generation)
                before=owner_outputs()
                until('planned-'+label+'-old-owner-progress',lambda:owner_outputs()>=before+3)
                assert runner.machine.generation==generation and runner.machine.active_vm==owner
                assert transition('refresh')['phase']=='PREPARED'
                row=transition('commit');assert row['committed_generation']==generation+1
                assert transition('commit')==row
                until('planned-'+label+'-active',lambda:runner.machine.active_vm==vms[label] and runner.machine.output_permitted)
                until('planned-'+label+'-standby',lambda:runner.machine.node_status(
                    vms[first if label==other else other],time.monotonic())=='standby')
        inputs_enabled.clear()
        until('perception-loss-same-vm-manual',lambda:runner.machine.mode is Mode.MANUAL and runner.machine.active_vm==vms[first])
        if args.manual_session_contract:neutral_then_action('fallback-auto-manual')
        until('manual-standby-prepared',lambda:runner.machine.node_status(vms[other],time.monotonic())=='standby')
        guests[first].terminate();guests[first].wait(timeout=5)
        until('other-active-manual',lambda:runner.machine.mode is Mode.MANUAL and runner.machine.active_vm==vms[other])
        if args.manual_session_contract:
            action=driver_state[1];generation=runner.machine.generation
            until('manual-takeover-held-key',lambda:drive_seen(generation,vms[other],moving=True))
            assert driver_state[1]==action and runner.authority.payload()['manual_policy']=='CONTINUE_SESSION'
        inputs_enabled.set()
        until('recovered-perception-auto-ready',lambda:runner.machine.mode is Mode.AUTO_READY and runner.machine.active_vm==vms[other])
        # Merely becoming ready must not drive AUTO.
        time.sleep(.3);assert runner.machine.mode is Mode.AUTO_READY
        until('auto-ready-held',lambda:all(runner.machine._qualified(vms[other],app,time.monotonic(),held=True)
                                         for app in ('autonomous','manual')))
        resume()
        until('other-active-auto',lambda:runner.machine.mode is Mode.AUTO and runner.machine.active_vm==vms[other] and
            runner.machine.node_status(vms[other],time.monotonic())=='active')
        start_guest(first)
        until('rejoined-standby',lambda:runner.machine.node_status(vms[first],time.monotonic())=='standby',15)
        assert runner.machine.active_vm==vms[other] and runner.machine.mode is Mode.AUTO
        if args.readiness_profile=='warm-drive-1':
            # Native fixture replaces only the root/Store boundary. Exercise real
            # refresh -> cancel/join -> new envelope -> Gateway without reboot.
            session=next(s for s in reversed(sessions) if s['label']==first)
            manual=runner.machine.prepared[vms[first],'manual'];manual_identity=session['app_instances']['manual']
            old_auto=session['app_instances']['autonomous'];new_auto=str(uuid.uuid4())
            payload=json.loads(bootstrap_paths[first].read_text());payload['auto_instance_id']=new_auto
            session['app_instances']['autonomous']=new_auto
            from xenova_mqtt.drive_binding import atomic_json
            atomic_json(bootstrap_paths[first],payload)
            until('app-reinitialized-standby',lambda:runner.machine.sessions.get((vms[first],'autonomous'),(None,None,None))[2]==new_auto and
                runner.machine.node_status(vms[first],time.monotonic())=='standby')
            assert runner.machine.prepared[vms[first],'manual'] is manual and session['app_instances']['manual']==manual_identity
            assert runner.machine.active_vm==vms[other] and runner.machine.output_permitted
            delayed=XenovaClient(Identity(vm_id=vms[first],app_id='autonomous',boot_id=session['boot_id'],agent_instance_id=old_auto),
                host='192.0.2.1',port=18884,username=vms[first],password=passwords[first],client_id='delayed-old-app')
            clients.append(delayed);assert delayed.start(wait_s=3)
            delayed.publish(topics.vm_app_events(vms[first],'autonomous'),'app.lifecycle',dict(profile='warm-drive-1',
                applied_generation=runner.machine.generation,check_seq='999999',progress_seq='999999',phase='EXECUTING',readiness='READY',reason=None))
            time.sleep(.15);delayed.stop()
            assert runner.machine.sessions[vms[first],'autonomous'][2]==new_auto
        old=sessions[0 if first=='a' else 1]
        if args.readiness_profile=='warm-drive-1':
            for app,instance in old['app_instances'].items():
                assert not runner.machine.accept_session(vms[first],old['boot_id'],instance,app=app)
        else:assert not runner.machine.accept_session(vms[first],old['boot_id'],old['drive_instance'])
        guests[other].terminate();guests[other].wait(timeout=5)
        until('rejoined-active-auto',lambda:runner.machine.mode is Mode.AUTO and runner.machine.active_vm==vms[first] and
            runner.machine.node_status(vms[first],time.monotonic())=='active')
        time.sleep(.3)
        stop.set()
        for thread in threads:thread.join(timeout=5)
        assert not any(thread.is_alive() for thread in threads)
        time.sleep(.1)  # allow the independent socket to receive final submitted bytes
        with wire_lock:wire=list(records)
        controls=[r['message']['payload'] for r in wire if r['message']['type']=='vehicle.control']
        commands=[r for r in wire if r['message']['type']=='drive.command']
        # Firmware's documented message_id is session-local, not a global UUID.
        def source_key(value):return tuple(value[k] for k in ('vm_id','boot_id','agent_instance_id','message_id'))
        by_id={source_key(r['message']):r['message'] for r in commands}
        provenance=[]
        for row in runner.submissions:
            if not row['source']:continue
            source=row['source'];message=by_id[source_key(source)]
            assert message['vm_id']==source['vm_id'] and message['app_id']==source['app_id']
            assert message['boot_id']==source['boot_id'] and message['agent_instance_id']==source['agent_instance_id']
            assert message['payload']['generation']==row['generation']
            assert message['payload']['v_mps']==row['v_mps'] and message['payload']['steer_rad']==row['steer_rad']
            assert row['serial_result']=='accepted' and row['device_applied'] is None
            provenance.append(row)
        assert provenance
        (args.output/'submissions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in provenance))
        assert all(s['binary_sha256']==digest for s in sessions)
        for vm in vms.values():
            kinds={r['message']['type'] for r in wire if r['message'].get('vm_id')==vm}
            assert {'telemetry.inventory','telemetry.metrics','telemetry.heartbeat'}<=kinds
        if errors:raise RuntimeError(errors[0])
        passed=True
    finally:
        stop.set()
        for thread in threads:thread.join(timeout=5)
        for receiver in receivers:receiver.loop_stop();receiver.disconnect()
        for c in clients:c.stop()
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        for h in handles:h.close()
        (args.output/'wire.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
        (args.output/'result.json').write_text(json.dumps({'passed':passed,'initial':args.initial,'readiness_profile':args.readiness_profile,
            'manual_session_contract':args.manual_session_contract,
            'same_binary_sha256':digest,'stages':stages,'sessions':sessions,'errors':errors,
            'scope':'native TCP/MQTT and mock serial only; no Xen/board/image/performance acceptance'},indent=2))
    if not passed:raise RuntimeError('native pair acceptance failed')
    print(json.dumps({'passed':True,'stages':len(stages),'same_binary_sha256':digest}))

if __name__=='__main__':main()
