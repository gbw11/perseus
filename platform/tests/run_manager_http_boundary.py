#!/usr/bin/env python3
"""현행 FastAPI/driver/service를 수정 없이 loopback에서 실행한다. 하위 Manager만 대역이다.

기존 FastAPI/uvicorn 런타임이 필요하다. HTTP 단절은 취소가 아니며 상위 두 잠금과
pending 오류 매핑은 하위 stop 수락과 다른 경계임을 검증한다. Xen/VM을 제어하지 않는다.
"""
import argparse
import http.client
import json
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'platform/services/dom0-manager'))
from dom0_manager.config import Settings
from dom0_manager.drivers.platform import PlatformDriver, SerializedManager
from dom0_manager.main import create_app
import uvicorn

IDENTITY='11111111-2222-4333-8444-555555555555'


class Inner:
    data=Path('/unused-test-data')
    def __init__(self):self.reset('none')
    def reset(self,block):
        self.block=block;self.entered=threading.Event();self.release=threading.Event()
        self.finished=threading.Event();self.calls=[];self.pending=False
    def call(self,action,spec=None,**kwargs):
        self.calls.append(action)
        if action==self.block:
            self.entered.set()
            assert self.release.wait(10),'HTTP boundary not released'
        self.finished.set()
        return {'schema_version':'1.0','ok':not self.pending,'request_id':'http-fixture',
            'operation_id':None,'replayed':False,
            'data':{'vm':{'vm_id':IDENTITY,'name':'fixture','configuration':{'vcpus':1,'memory_mib':16},'domid':1,
                         'xen_state':'running','vcpus':1,'memory_mib':16}},
            'error':{'code':'OPERATION_IN_PROGRESS','message':'intent accepted, execution pending',
                     'retryable':True,'details':{'accepted':True,'completion':'pending','operation_id':'original-stop'}} if self.pending else None}


def main(output):
    inner=Inner();serialized=SerializedManager(inner,busy_timeout_s=.1)
    driver=PlatformDriver(serialized)
    app=create_app(Settings(driver='mock'),driver=driver,provisioner=object())
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,loop='asyncio',http='h11',ws='none',log_level='critical'))
    thread=threading.Thread(target=server.run);thread.start()
    deadline=time.monotonic()+15
    while not server.started:
        assert thread.is_alive() and time.monotonic()<deadline
        time.sleep(.01)
    port=server.servers[0].sockets[0].getsockname()[1]
    def request(method,timeout=8,wait_for_entry=False):
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
        try:
            connection.request(method,'/api/v1/vms/'+IDENTITY+('/stop' if method=='POST' else ''))
            if wait_for_entry:
                # QEMU 초기 라우트 로딩 시간과 실제 하위 실행 대기 시간을 분리한다.
                assert inner.entered.wait(8)
                connection.sock.settimeout(.15)
            response=connection.getresponse()
            return response.status,response.read().decode()
        finally:connection.close()
    result={'python':sys.version,'uvicorn':uvicorn.__version__,'actual_xen':False,
            'fixture_serialization_s':.1,'production_serialization_changed':False,'cases':[]}
    try:
        inner.reset('stop')
        try:
            request('POST',wait_for_entry=True)
            raise AssertionError('outer HTTP wait must expire')
        except TimeoutError:pass
        assert inner.entered.is_set()
        inner.release.set();assert inner.finished.wait(8)
        time.sleep(.1)
        assert inner.calls==['stop']
        result['cases'].append({'case':'HTTP timeout does not cancel handler','lower_calls':inner.calls.copy(),'auto_retry':False})

        inner.reset('get');first=[]
        waiting=threading.Thread(target=lambda:first.append(request('GET')));waiting.start()
        assert inner.entered.wait(8)
        status,body=request('POST')
        assert status==503 and inner.calls==['get'],(status,body,inner.calls)
        inner.release.set();waiting.join(8);assert not waiting.is_alive()
        assert first[0][0]==200,first
        result['cases'].append({'case':'serialization blocks stop before lower acceptance','status':status,'response':json.loads(body)})

        inner.reset('stop');first=[]
        waiting=threading.Thread(target=lambda:first.append(request('POST')));waiting.start()
        assert inner.entered.wait(8)
        try:request('POST',timeout=.15);raise AssertionError('per-VM lock must remain held')
        except TimeoutError:pass
        assert inner.calls==['stop'],'second request has not reached Manager'
        inner.release.set();waiting.join(8)
        deadline=time.monotonic()+8
        while len(inner.calls)<2:
            assert time.monotonic()<deadline;time.sleep(.01)
        time.sleep(.1)
        result['cases'].append({'case':'per-VM lock precedes lower stop acceptance','lower_calls_after_release':inner.calls.copy(),
                                'client_disconnect_cancels_second':False})

        inner.reset('none');inner.pending=True
        status,body=request('POST');value=json.loads(body)
        assert status==503 and 'accepted' not in value
        result['cases'].append({'case':'current error mapper omits durable acceptance details','status':status,'response':value})
        result['scope']='HTTP boundary with fake lower Manager; durable lower journal verified by separate tests'
    finally:
        inner.release.set();server.should_exit=True;thread.join(10)
        assert not thread.is_alive()
        output.write_text(json.dumps(result,indent=2))
    print(json.dumps({'cases':len(result['cases']),'upper_changes':False,'output':str(output)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
