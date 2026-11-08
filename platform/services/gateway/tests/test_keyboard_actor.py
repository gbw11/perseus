"""실제 K actor/driver registry/Authority attach. OS/physical 입력은 명시 fixture다.

실제 evdev/seqpacket/serial은 각각 source/server/feedback3 시험에서 검사한다. 이 시험의
app lifecycle은 현재 app의 typed producer 전제이며 C planner 실행이라고 주장하지 않는다.
"""
from types import SimpleNamespace
import json
import subprocess
from pathlib import Path
from uuid import uuid4
import time

import pytest
from xenova_gateway.authority_journal import canonical
from xenova_gateway.manual_source import ManualProfile
from test_operator_runtime import world


class K:
    def __init__(self,w):
        self.w=w;self.actor=w.o.keyboard;self.seq=0;self.sample=0;self.event=0
        self.source=str(uuid4());self.channel=str(uuid4());self.device=str(uuid4());self.link=str(uuid4());self.peer=object()
        self.actor.profile_loader=lambda alias:SimpleNamespace(device_identity_sha256='a'*64,kernel_abi_sha256='b'*64,
            profile_sha256='c'*64,continuous_allowed=True)
    def packet(self,kind,payload):
        self.seq+=1;now=self.w.o.now
        return dict(version=1,kind=kind,host_boot_id=None if kind=='HELLO' else self.w.w.auth.host,
            gateway_instance_id=None if kind=='HELLO' else self.w.o.instance,source_instance=self.source,channel_epoch=self.channel,
            sequence=str(self.seq),request_id=str(uuid4()),issued_pi_us=None if kind=='HELLO' else str(now),
            expires_pi_us=None if kind=='HELLO' else str(now+5_000_000),payload=payload)
    def send(self,kind,payload,apps=False):
        call=self.packet(kind,payload);future=self.actor.submit(call,self.peer);end=time.monotonic()+5
        while future.empty():
            assert time.monotonic()<end
            self.w.tick()
            if apps:self.w.apps()
            time.sleep(.001)
        return call,future.get_nowait()
    def bind(self):
        assert self.w.apply(self.w.call('mode.manual'))['result']=='APPLIED'
        _,hello=self.send('HELLO',dict(device_profile='test-keyboard',device_identity_sha256='a'*64,kernel_abi_sha256='b'*64))
        assert hello['kind']=='STATE' and hello['payload']['accepted'] and hello['payload']['driver_session'] is None
        _,grant=self.send('ATTACH',dict(expected_generation=self.w.w.m.generation,expected_owner=self.w.w.m.active_vm,
            device_instance=self.device,link_epoch=self.link),apps=True)
        assert grant['kind']=='GRANT';return grant
    def frame(self,mask,*,event=False):
        if event:self.event+=1
        self.sample+=1;r=self.w.o.sources;now=self.w.o.now
        return self.send('FRAME',dict(driver_session=r.sid,input_epoch=r.epoch,grant_revision=str(r.grant),
            device_instance=self.device,link_epoch=self.link,source_sample_seq=str(self.sample),key_event_seq=str(self.event),
            t1_pi_us=str(now),t2_pi_us=str(now),key_mask=mask,last_event_pi_us=str(now) if self.event else None))
    def arm(self):
        self.frame(0);self.frame(0);self.frame(64,event=True);r=self.w.o.sources
        call,result=self.send('ARM',dict(driver_session=r.sid,input_epoch=r.epoch,grant_revision=str(r.grant),
            source_sample_seq=str(self.sample),arm_event_seq=str(self.event)))
        assert result['payload']['accepted'] and r.armed and not r.blocked
        self.frame(0,event=True)
        return call


@pytest.fixture(scope='session')
def manual_codec(tmp_path_factory):
    root=Path(__file__).resolve().parents[4];common=root/'platform/apps/common/xenova-mqtt-c'
    target=tmp_path_factory.mktemp('actual-manual-codec')/'test_manual_lease'
    sources=[common/'src'/name for name in ('xm_topic.c','xm_policy.c','xm_json.c','xm_envelope.c','xm_seq.c','xm_payload.c','xm_manual.c')]
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
        '-I'+str(common/'include'),'-I'+str(common/'src'),'-I'+str(common/'third_party/jsmn'),
        '-I'+str(root/'platform/apps/common/xenova-drive-runtime/include'),
        '-I'+str(root/'platform/apps/manual/zephyr/src'),
        str(root/'platform/apps/manual/zephyr/tests/test_manual_lease.c'),
        str(root/'platform/apps/manual/zephyr/src/manual.c'),*(str(p) for p in sources),'-lm','-o',str(target)],check=True)
    return target


def test_actual_attach_neutral_enter_motion_and_sticky_release(world,manual_codec,tmp_path):
    k=K(world);grant=k.bind();k.arm();r=world.o.sources
    assert r.kind=='HELD' and r.action==0
    call,result=k.frame(1,event=True)
    assert result['payload']['accepted'] and r.keys==['up'] and r.action==1
    original=(r.sample,r.source_us,r.expires);raw=world.client.published[-1][0][2]
    assert raw['profile']=='manual-input-3' and raw['action_expires_pi_us']==str(original[2])
    path=tmp_path/'actual-gateway-input.json';path.write_bytes(canonical(raw))
    consumed=json.loads(subprocess.check_output([str(manual_codec),'--input-file',str(path)],text=True))
    assert consumed['session']==r.sid and consumed['action']==str(r.action)
    assert consumed['source_pi_us']==str(r.source_us) and consumed['expires_pi_us']==str(r.expires)
    assert consumed['input_epoch']==r.epoch and consumed['grant_revision']==str(r.grant)
    k.event+=1
    _,ack=k.send('FENCE',dict(driver_session=r.sid,input_epoch=r.epoch,grant_revision=str(r.grant),
        fence_revision='1',key_event_seq=str(k.event),reason_mask=1,reason='OK'))
    assert ack['kind']=='FENCE_ACK' and not r.keys and r.armed and not r.blocked
    assert (r.sample,r.source_us,r.expires)==original
    world.pump(lambda:world.o.keyboard.last_fence is None)
    assert world.o.keyboard.take_outgoing()[1]['kind']=='STOP_READY'
    assert r.state=='ARMED_NEUTRAL'


def test_same_sid_generation_continuation_keeps_original_input_and_no_new_enter(world):
    k=K(world);k.bind();k.arm();k.frame(1,event=True);r=world.o.sources
    original=(r.sid,r.epoch,r.grant,r.action,r.sample,r.source_us,r.expires)
    m=world.w.m;fields=world.w.auth.issue_fields(m.mode,m.active_vm,'explicit-test-handoff',world.w.t,1.,arm=True,
        manual=dict(manual_session=r.sid,manual_policy='CONTINUE_SESSION'))
    done=[];assert world.o._proposal('same-session-test',fields,'test-control-precondition',world.o.now+1_000_000,
        lambda ok,unknown:done.append(ok))
    world.pump(lambda:bool(done));assert done==[True]
    world.r._emit(m._adopt_control(m.mode,m.active_vm,'explicit-test-handoff',world.w.t));world.tick()
    assert original==(r.sid,r.epoch,r.grant,r.action,r.sample,r.source_us,r.expires)
    assert r.armed and not r.blocked
    outgoing=world.o.keyboard.take_outgoing();assert outgoing[1]['kind']=='STATE'
    assert outgoing[1]['payload']['generation']==m.generation and outgoing[1]['payload']['accepted'] is None
    world.w.t=r.expires/1e6+.001;world.tick()
    assert r.blocked and not r.armed and not r.keys


def test_held_source_fault_fences_without_refunding_or_replaying_old_arm(world):
    k=K(world);k.bind();old_arm=k.arm();r=world.o.sources;k.frame(1,event=True)
    old=(r.sid,r.epoch,r.grant,r.sample,r.source_us,r.expires)
    original=r.ring[r.sid,r.action,r.sequence][0]
    start=len(world.client.published);k.actor.lost(k.peer);world.tick()
    emitted=[item[0][2] for item in world.client.published[start:]
        if len(item)==2 and isinstance(item[0],tuple) and len(item[0])==3 and item[0][1]=='drive.input']
    held=[value for value in emitted if value['session_id']==old[0] and value['producer_kind']=='HELD']
    assert held and all(not value['valid'] and not value['keys'] for value in held)
    assert all((value['sample_seq'],value['source_pi_us'])==(str(old[3]),str(old[4]))
        and 0<=int(value['action_expires_pi_us'])<=old[5] for value in held)
    # 현재 typed STOP/stationary가 확인되면 원 HELD를 닫고 별도 zero 준비
    # candidate를 만든다. 그 새 SID의 시각을 원 K 표본 갱신으로 비교하지 않는다.
    assert world.o.keyboard.scope is None and world.o.stationary()
    assert r.kind=='NEUTRAL_ONLY' and r.sid!=old[0] and r.epoch!=old[1]
    assert not r.keys and r.blocked and not r.armed and r.expires==0
    assert json.loads(original)['action_expires_pi_us']==str(old[5])
    assert world.o.sources.current_anchor() is None
    result=k.actor.submit(old_arm,k.peer)
    world.pump(lambda:not result.empty());replay=result.get_nowait()
    assert replay['payload']['accepted'] is False and replay['payload']['reason']=='SOURCE_MISMATCH'
    assert not r.keys and r.blocked and not r.armed and r.expires==0
