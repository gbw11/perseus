"""R01~R16 제품 연결: 별도 uvicorn 프로세스와 실제 HTTP. Spring/DB만 계약 수신기다."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from first_install_fixture import Board
import xenova_registration_hook as hook

TOKEN='fixture-local-api-token-0000000000000000000000'
SPRING_TOKEN='fixture-spring-outbound-token'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


class RegistrationApiProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.calls=[];self.mode='normal';self.received=threading.Event();self.release=threading.Event()
        self.saved={};owner=self
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                owner.calls.append(('unexpected GET',None,None));self.send_error(405)
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                body=json.loads(raw);owner.calls.append((self.path,self.headers.get('Authorization'),body))
                owner.received.set();registration=body['data']['registration'];identity=registration['vm_id']
                if owner.mode=='slow':owner.release.wait(5)
                new=identity not in owner.saved
                if owner.mode=='lost':
                    owner.saved.setdefault(identity,registration);self.connection.close();return
                if owner.mode.startswith('reject-'):
                    status=int(owner.mode.split('-')[1]);reply={'code':'VM_REGISTRATION_CONFLICT' if status==409 else 'FIXTURE_REJECTED','message':'not forwarded private detail'}
                elif owner.mode=='redirect':
                    self.send_response(307);self.send_header('Location','/api/v1/vms');self.end_headers();return
                else:
                    status=201 if new else 200;owner.saved.setdefault(identity,registration)
                    reply={'vm_id':identity,'registration':'confirmed'}
                    if owner.mode=='wrong-target':reply['vm_id']=str(uuid4())
                    if owner.mode=='accepted':reply['registration']='accepted'
                raw=json.dumps(reply).encode()
                if owner.mode=='invalid':raw=b'{"vm_id":1,"vm_id":2}'
                self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        self.receiver=ThreadingHTTPServer(('127.0.0.1',0),Receiver);self.receiver.daemon_threads=True
        threading.Thread(target=self.receiver.serve_forever,daemon=True).start()
        self.addCleanup(self.receiver.server_close);self.addCleanup(self.receiver.shutdown);self.addCleanup(self.release.set)
        self.port=free_port();self.process=None
        self.write_local()
        self.start_api()

    def write_local(self):
        (self.root/'local.env').write_text('XENOVA_HOST=127.0.0.1\nXENOVA_PORT='+str(self.port)+'\n')
        (self.root/'token.env').write_text('XENOVA_SERVICE_TOKEN='+TOKEN+'\n')
        for name in ('local.env','token.env'):(self.root/name).chmod(0o600)

    def start_api(self,*,configured=True,delay=0):
        env={**os.environ,'REGISTRATION_FIXTURE':str(self.root),'XENOVA_SERVICE_TOKEN':TOKEN,
             'XENOVA_REGISTRATION_URL':f'http://127.0.0.1:{self.receiver.server_port}/api/v1/vms/registrations' if configured else '',
             'VM_REGISTRATION_SERVICE_TOKEN':SPRING_TOKEN if configured else '',
             'XENOVA_REGISTRATION_TIMEOUT':'1'}
        if delay:time.sleep(delay)
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('registration_api_fixture.py')),str(self.port)],
            env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        self.addCleanup(self.stop_api)
        end=time.monotonic()+10
        while time.monotonic()<end:
            if self.process.poll() is not None:
                self.fail(self.process.stdout.read().decode())
            try:
                status,_=self.request('GET','/openapi.json',auth=False)
                if status==200:break
            except OSError:pass
            time.sleep(.02)
        else:self.fail('real FastAPI process not ready')
        self.metadata=json.loads((self.root/'metadata.json').read_text());self.identity=self.metadata['vm_id']

    def stop_api(self):
        if self.process is not None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait();self.fail('API shutdown did not drain')
            self.process.stdout.close();self.process=None

    def request(self,method,path,body=None,*,auth=True,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=8)
        hdr={'Authorization':'Bearer '+TOKEN} if auth else {}
        if headers:hdr.update(headers)
        if body is not None:body=json.dumps(body);hdr['Content-Type']='application/json'
        try:
            conn.request(method,path,body,headers=hdr);response=conn.getresponse();raw=response.read()
            return response.status,json.loads(raw) if raw else None
        finally:conn.close()

    def notify(self,identity=None):return self.request('POST','/api/v1/vm-registrations/'+(identity or self.identity)+'/notify')

    def wait_result(self,data,identity):
        path=Path(data)/'registration-notices'/(identity+'.json');end=time.monotonic()+18
        while time.monotonic()<end:
            if path.exists():return json.loads(path.read_text())
            time.sleep(.02)
        self.fail('finite local callback result missing')

    def test_actual_get_exact_registry_scope_no_xen_and_openapi_curl(self):
        events=(self.root/'xen.jsonl').read_text() if (self.root/'xen.jsonl').exists() else ''
        status,rows=self.request('GET','/api/v1/vm-registrations');self.assertEqual(status,200);self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['name'],rows[1]['name'])
        self.assertEqual(events,(self.root/'xen.jsonl').read_text() if (self.root/'xen.jsonl').exists() else '')
        self.assertEqual(self.calls,[])
        self.assertEqual(set(rows[0]),{'vm_id','name','guest_kind','template_id','template_version','vcpus','memory_mib','assigned_ipv4'})
        z=next(row for row in rows if row['guest_kind']=='zephyr');self.assertEqual(z['assigned_ipv4'],'192.168.77.90')
        schema=self.request('GET','/openapi.json',auth=False)[1]
        self.assertEqual(schema['paths']['/api/v1/vm-registrations']['get']['responses']['200']['content']['application/json']['schema']['type'],'array')
        command=['curl','--silent','--show-error','--fail','-H','Authorization: Bearer '+TOKEN,
                 f'http://127.0.0.1:{self.port}/api/v1/vm-registrations']
        result=subprocess.run(command,check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout),rows)

    def test_manual_actual_http_full_envelope_new201_existing200_and_lost_response(self):
        status,body=self.notify();self.assertEqual(status,200);self.assertTrue(body['confirmed']);self.assertEqual(body['http_status'],201)
        wire=self.calls[0][2]
        self.assertEqual(self.calls[0][:2],('/api/v1/vms/registrations','Bearer '+SPRING_TOKEN))
        self.assertEqual(set(wire),{'schema_version','ok','request_id','operation_id','replayed','data','error'})
        self.assertTrue(wire['data']['plan']);self.assertTrue(wire['data']['execute_required'])
        self.assertIn('assigned_ipv4',wire['data']['registration']);self.assertNotIn(TOKEN,json.dumps(wire))
        self.assertEqual(self.notify()[1]['http_status'],200)
        self.mode='lost';self.assertFalse(self.notify()[1]['confirmed']);count=len(self.calls)
        time.sleep(.15);self.assertEqual(count,len(self.calls))
        self.mode='normal';self.assertTrue(self.notify()[1]['confirmed'])
        self.assertEqual(len(self.saved),1)

    def test_actual_http_current_resources_partial_read_error_retire_then_recovery(self):
        registry=Path(self.metadata['data'])/'instances'
        path=next(p for p in registry.glob('*/instance.json') if json.loads(p.read_text())['uuid']==self.metadata['debian_id'])
        record=json.loads(path.read_text());record.update(vcpus=3,memory_mib=768);path.write_text(json.dumps(record))
        status,rows=self.request('GET','/api/v1/vm-registrations')
        self.assertEqual(status,200);self.assertEqual(next(r for r in rows if r['vm_id']==record['uuid'])['vcpus'],3)
        saved=path.read_bytes();path.write_text('{broken')
        self.assertNotEqual(self.request('GET','/api/v1/vm-registrations')[0],200)
        path.write_bytes(saved);self.assertEqual(self.request('GET','/api/v1/vm-registrations')[0],200)
        status,body=self.request('DELETE','/api/v1/vms/'+record['uuid']);self.assertEqual(status,204,body)
        self.assertEqual({r['vm_id'] for r in self.request('GET','/api/v1/vm-registrations')[1]},
                         {self.identity,self.metadata['zephyr_id']})
        self.assertEqual(self.notify(record['uuid'])[0],404);self.assertEqual(self.calls,[])

    def test_response_meanings_no_redirect_retry_or_post_get_and_normal_recovery(self):
        for mode in ('invalid','wrong-target','accepted','redirect','reject-400','reject-401','reject-403','reject-409','reject-503'):
            self.mode=mode;before=len(self.calls);status,body=self.notify()
            self.assertEqual(status,502,(mode,body));self.assertFalse(body['confirmed']);self.assertEqual(len(self.calls),before+1)
            if mode.startswith('reject-'):
                self.assertEqual(body['http_status'],int(mode.split('-')[1]));self.assertIn('upstream_code',body)
            self.assertNotIn('private detail',json.dumps(body))
        self.mode='normal';self.assertTrue(self.notify()[1]['confirmed'])

    def test_auth_missing_outbound_settings_and_no_body_proxy(self):
        self.assertEqual(self.request('POST','/api/v1/vm-registrations/'+self.identity+'/notify',auth=False)[0],401)
        self.assertEqual(self.request('POST','/api/v1/vm-registrations/'+self.identity+'/notify',{'endpoint':'https://bad','token':'secret'})[0],400)
        self.assertEqual(self.calls,[])
        self.stop_api();self.start_api(configured=False)
        self.assertEqual(self.request('GET','/api/v1/vm-registrations')[0],200)
        status,body=self.notify();self.assertEqual(status,503);self.assertEqual(body['state'],'not-configured');self.assertEqual(self.calls,[])

    def test_invalid_registration_diagnostics_do_not_wait_for_slow_sink(self):
        self.stop_api()
        with patch.dict(os.environ,{'REGISTRATION_SLOW_DIAGNOSTIC':'1'}):self.start_api()
        before=time.monotonic()
        status,body=self.notify('not-a-uuid')
        self.assertEqual(status,400);self.assertEqual(body['code'],'InvalidArgument')
        self.assertLess(time.monotonic()-before,.4)
        self.assertEqual(self.request('GET','/api/v1/vm-registrations')[0],200)
        self.assertEqual(self.calls,[])

    def test_slow_sender_capacity_lookup_stop_original_result_and_resource_return(self):
        self.mode='slow'
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending=[pool.submit(self.notify) for _ in range(4)]
            self.assertTrue(self.received.wait(2));time.sleep(.1)
            status,body=self.notify();self.assertEqual(status,503);self.assertEqual(body['state'],'capacity-rejected')
            self.assertEqual(self.request('GET','/api/v1/vm-registrations')[0],200)
            status,body=self.request('POST',f'/api/v1/vms/{self.identity}/stop',headers={'Idempotency-Key':'during-registration'})
            self.assertEqual(status,200,body)
            original=self.request('GET',f'/api/v1/vms/{self.identity}/control-operations?request_key=during-registration')
            self.assertEqual(original[0],200);self.assertEqual(original[1]['status'],'completed')
            commands=[json.loads(row) for row in (self.root/'xen.jsonl').read_text().splitlines()]
            self.assertTrue(any(row[:2]==['xl','shutdown'] for row in commands))
            for future in pending:self.assertFalse(future.result()[1]['confirmed'])
        self.release.set();self.mode='normal';self.assertTrue(self.notify()[1]['confirmed'])
        self.assertEqual(len(list((self.root/'sender-slots').iterdir())),4)

    def test_api_create_to_real_lower_callback_does_not_wait_or_double_post_and_replay409(self):
        self.mode='slow';identity=str(uuid4())
        spec={**self.metadata['spec'],'vm_id':identity,'name':'auto-registration'};spec.pop('ipv4',None)
        before=time.monotonic();status,body=self.request('POST','/api/v1/vms',spec)
        self.assertEqual(status,201,body);self.assertLess(time.monotonic()-before,2)
        self.assertTrue(self.received.wait(3));self.assertFalse(self.release.is_set())
        self.assertEqual(len(self.calls),1);self.assertEqual(self.calls[0][2]['data']['registration']['vm_id'],identity)
        result=self.wait_result(self.metadata['data'],identity);self.assertFalse(result['confirmed'])
        self.assertEqual(self.request('POST','/api/v1/vms',spec)[0],409)
        self.release.set();self.mode='normal';self.assertTrue(self.notify(identity)[1]['confirmed']);self.assertEqual(len(self.calls),2)

    def test_api_cold_restart_preserves_original_create_and_does_not_refill_automatic_chance(self):
        identity=str(uuid4());spec={**self.metadata['spec'],'vm_id':identity,'name':'persisted-registration'};spec.pop('ipv4',None)
        self.assertEqual(self.request('POST','/api/v1/vms',spec)[0],201)
        self.assertTrue(self.wait_result(self.metadata['data'],identity)['confirmed']);self.assertEqual(len(self.calls),1)
        operations=Path(self.metadata['data'])/'operations'
        before={p.name:p.read_bytes() for p in operations.glob('*.json')}
        self.stop_api();self.start_api()
        self.assertIn(identity,{row['vm_id'] for row in self.request('GET','/api/v1/vm-registrations')[1]})
        self.assertEqual(before,{p.name:p.read_bytes() for p in operations.glob('*.json')})
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.notify(identity)[1]['http_status'],200);self.assertEqual(len(self.calls),2)

    def owned_api_unit(self,*,fails=False):
        """실제 로컬 systemd. production unit 대신 이 시험만 소유한 이름을 사용한다."""
        if os.environ.get('XENOVA_TEST_SYSTEMD_BOOT')!='1':
            self.skipTest('opt-in owned transient systemd fixtures: XENOVA_TEST_SYSTEMD_BOOT=1')
        prefix='xenova-registration-fixture-'+uuid4().hex
        unit=prefix+'-api.service';barrier=self.root/'api-release'
        command=['systemd-run','--quiet','--no-block','--collect','--unit='+unit,
                 '--property=Type=notify','--property=NotifyAccess=main','--property=TimeoutStartSec=40s',
                 '--property=StandardOutput=append:'+str(self.root/'systemd-api.log'),
                 '--property=StandardError=append:'+str(self.root/'systemd-api.log')]
        environment={'PYTHONPATH':os.environ['PYTHONPATH'],'REGISTRATION_FIXTURE':str(self.root),
                     'REGISTRATION_START_BARRIER':str(barrier),'XENOVA_SERVICE_TOKEN':TOKEN,
                     'XENOVA_REGISTRATION_URL':f'http://127.0.0.1:{self.receiver.server_port}/api/v1/vms/registrations',
                     'VM_REGISTRATION_SERVICE_TOKEN':SPRING_TOKEN,'XENOVA_REGISTRATION_TIMEOUT':'1'}
        if os.environ.get('REGISTRATION_FACTORY_DATA'):
            environment['REGISTRATION_FACTORY_DATA']=os.environ['REGISTRATION_FACTORY_DATA']
        command += ['--setenv='+key+'='+value for key,value in environment.items()]
        command += (['/usr/bin/false'] if fails else
                    [sys.executable,str(Path(__file__).with_name('registration_api_fixture.py')),str(self.port)])
        self.addCleanup(self.stop_owned_unit,unit)
        subprocess.run(command,check=True,capture_output=True,timeout=5)
        return unit,prefix,barrier

    def stop_owned_unit(self,unit):
        subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=8)

    def defer_to_owned_api(self,data,runtime,identity,unit,prefix):
        callback=prefix+'-'+identity+'.service';self.addCleanup(self.stop_owned_unit,callback)
        hook.defer_factory(Path(data),Path(runtime),self.root/'local.env',self.root/'token.env',
                           time.monotonic()+hook.CALL_SECONDS,identity,api_unit=unit,unit_prefix=prefix+'-')
        return callback

    def test_factory_api_failure_cancels_job_without_restart_refill_then_manual_recovers(self):
        self.stop_api();unit,prefix,barrier=self.owned_api_unit(fails=True)
        callback=self.defer_to_owned_api(self.metadata['data'],self.metadata['run'],self.identity,unit,prefix)
        end=time.monotonic()+5
        while time.monotonic()<end:
            jobs=subprocess.check_output(['systemctl','list-jobs','--no-legend'],text=True)
            if callback not in jobs:break
            time.sleep(.02)
        else:self.fail('failed API dependency left an owned notification job pending')
        self.assertEqual(self.calls,[])
        self.start_api();time.sleep(.15);self.assertEqual(self.calls,[])
        self.assertTrue(self.notify()[1]['confirmed']);self.assertEqual(len(self.calls),1)

    def test_factory_ready_after_old_budget_then_once_and_api_restart_does_not_requeue(self):
        self.stop_api()
        board=Board(self.root/'board');board.grow_mount();board.factory()
        # factory 자산을 실제 서버가 읽는 등록부로 연결한다. guest 준비/실제 장치만 Board 대역이다.
        # 새 서버에는 이 factory data를 읽는 product Manager를 선택한다.
        os.environ['REGISTRATION_FACTORY_DATA']=str(board.p.data)
        try:
            unit,prefix,barrier=self.owned_api_unit()
            import xenova_first_boot as boot
            import xenova_vm as vm
            from xenova_runtime import operation_lock
            scheduled=[]
            def automatic(data,runtime,record,*,wait_ready):
                self.assertTrue(wait_ready)
                # 부팅의 공통 잠금이 이미 반환된 실제 후속 경계다.
                with operation_lock(runtime):pass
                scheduled.append(self.defer_to_owned_api(data,runtime,record['uuid'],unit,prefix))
            before=time.monotonic()
            with (patch.object(boot,'FirstBoot',return_value=board.p),
                  patch.object(boot,'operation_lock',side_effect=lambda:operation_lock(board.p.run)),
                  patch.object(sys,'argv',['xenova-first-boot','register']),
                  patch.object(hook,'automatic',side_effect=automatic)):
                boot.main()
            self.assertLess(time.monotonic()-before,5)
            notices=list(vm.load_records(board.p.data,validate_assets=False).values())
            self.assertEqual(len(notices),2)
            # 기존 15초 전체 예산보다 늦게 API를 준비해도 송신 예산은 아직 시작하지 않았다.
            time.sleep(hook.CALL_SECONDS+1)
            self.assertEqual(self.calls,[])
            active=subprocess.check_output(['systemctl','show',unit,'--property=ActiveState','--value'],text=True).strip()
            self.assertEqual(active,'activating')
            jobs=subprocess.check_output(['systemctl','list-jobs','--no-legend'],text=True)
            self.assertTrue(all(callback in jobs for callback in scheduled))
            barrier.touch()
            for record in notices:self.assertTrue(self.wait_result(board.p.data,record['uuid'])['confirmed'])
            self.assertEqual(len(self.calls),2);self.assertEqual(board.p.register(),[])
            self.assertEqual(len(list((board.p.run/'registration-notices').glob('factory-started-*'))),2)
            subprocess.run(['systemctl','restart',unit],capture_output=True,check=True,timeout=8)
            time.sleep(.2);self.assertEqual(len(self.calls),2)
            self.assertEqual(subprocess.check_output(['systemctl','show',unit,'--property=ActiveState','--value'],text=True).strip(),'active')
        finally:os.environ.pop('REGISTRATION_FACTORY_DATA',None)


if __name__=='__main__':unittest.main()
