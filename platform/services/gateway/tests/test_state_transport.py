"""Real isolated broker + Paho + production state receiver/actor boundary.

The broker binary/root are explicitly supplied fixed local artifacts. No system
broker is used. Planner state and Xen bindings remain the StateWorld fixtures.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import pytest
from xenova_mqtt import XenovaClient,Identity,topics
from xenova_mqtt.handoff import snapshot
from test_state_transfer import StateWorld,CONTEXT,BODY
from test_verified import A,B,BOOT


def until(predicate):
    end=time.monotonic()+4
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def broker(tmp_path):
    native=os.environ.get('XENOVA_TEST_BROKER_BINARY')
    root=os.environ.get('XENOVA_TEST_BROKER_ROOT');qemu=os.environ.get('XENOVA_TEST_QEMU')
    if not native and (not root or not qemu):pytest.skip('explicit native broker or fixed broker/root/qemu not supplied')
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1]
    conf=tmp_path/'mosquitto.conf'
    conf.write_text(f'listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\nlog_type error\n')
    with (tmp_path/'mosquitto.log').open('wb') as log:
        command=([native,'-c',str(conf)] if native else
            [qemu,'-L',root,str(Path(root)/'usr/sbin/mosquitto'),'-c',str(conf)])
        proc=subprocess.Popen(command,stdout=log,stderr=log)
        try:
            def ready():
                if proc.poll() is not None:raise AssertionError((tmp_path/'mosquitto.log').read_text())
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.05):return True
                except OSError:return False
            until(ready);yield port
        finally:
            proc.terminate();proc.wait(timeout=5)


def test_c11_real_mqtt_complete_copy_target_apply_and_actor_consumption(broker,tmp_path):
    w=StateWorld(tmp_path/'authority');clients=[];applied=[]
    def client(vm=None):
        identity=Identity(vm_id=vm,app_id='autonomous',boot_id=BOOT,agent_instance_id=w.ids[vm]['autonomous']) if vm else Identity(source_id='gateway',app_id='gateway',boot_id=BOOT)
        c=XenovaClient(identity,port=broker)
        clients.append(c);return c
    observer=client();source=client(A);target=client(B)
    observer.set_receive_guard(w.cache.guard((A,B)))
    for vm in (A,B):observer.subscribe(topics.vm_app_events(vm,'autonomous'),w.runner._enqueue,qos=1)
    def receive(message):
        if message.type!='app.state.snapshot':return
        result=w.receiver.receive(message,control=w.auth.payload(),source_binding=w.cache.current(A,w.t),
            target_binding=w.cache.current(B,w.t),current=CONTEXT,road_segments={'lane:next'})
        # A Paho callback must not wait for the PUBACK handled by its own loop.
        target.publish_tracked(topics.vm_app_events(B,'autonomous'),'app.state.applied',result)
        applied.append(result)
    target.subscribe(topics.vm_app_events(A,'autonomous'),receive,qos=1)
    try:
        for c in clients:assert c.start(wait_s=2)
        until(lambda:target.subscribed(topics.vm_app_events(A,'autonomous')) and
            all(observer.subscribed(topics.vm_app_events(vm,'autonomous')) for vm in (A,B)))
        w.m.states.copies.clear();w.m.states.applications.pop((B,'autonomous'),None)
        source.publish(topics.vm_app_events(A,'autonomous'),'app.state.snapshot',w.snapshot.payload,wait_s=.5)
        def accepted():
            w.runner._drain();return w.m.states.evidence(B,'autonomous') is not None
        until(accepted)
        evidence=w.m.states.evidence(B,'autonomous')
        assert evidence['origin']=='handoff' and applied and w.m.active_vm==A and w.m.output_permitted
        assert w.target_adapter.progress==0 # PUBACK/application did not invent calculation
        # A duplicated key cannot enter seq/latest or qualify mixed state.
        before=w.m.states.accepted
        raw=json.dumps(w.snapshot.envelope).replace('"side": "LEFT"','"side": "RIGHT", "side": "LEFT"')
        source._paho.publish(topics.vm_app_events(A,'autonomous'),raw,qos=1).wait_for_publish(1)
        time.sleep(.03);w.runner._drain();assert w.m.states.accepted==before
        deep='['*2000+'0'+']'*2000
        source._paho.publish(topics.vm_app_events(A,'autonomous'),deep,qos=1).wait_for_publish(1)
        changed={**w.snapshot.payload,'state_revision':'2'}
        source.publish(topics.vm_app_events(A,'autonomous'),'app.state.snapshot',changed,wait_s=.5)
        until(lambda:(w.runner._drain() or True) and w.m.states.accepted>before)
        assert observer.connected and w.m.active_vm==A
    finally:
        for c in reversed(clients):c.stop(flush_s=.2)


def test_c11_python_c_complete_snapshot_vector():
    executable=os.environ.get('XENOVA_TEST_STATE_C')
    if not executable:pytest.skip('native state codec executable not supplied')
    body=dict(profile='drive-state-1',execution_id=A,intent_revision=1,lifecycle_generation=1,
              source_generation=42,state_revision=str(2**64-1),context=CONTEXT,maneuver=BODY)
    result=subprocess.run([executable,'--roundtrip'],input=json.dumps(body),text=True,capture_output=True,check=True)
    assert snapshot(json.loads(result.stdout))==snapshot(body)
