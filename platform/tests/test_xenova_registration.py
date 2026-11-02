"""실제 HTTP/socket/한정 프로세스와 등록 hook. Spring DB 제품만 계약 수신기로 대체한다."""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import xenova_registration as notice

VM='950e8400-e29b-41d4-a716-446655440001'


class RegistrationTest(unittest.TestCase):
    def setUp(self):
        if os.geteuid()!=0:self.skipTest('root-owned fixture')
        self.temp=tempfile.TemporaryDirectory(prefix='xenova-register-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.run=self.root/'run'
        self.data.mkdir();self.run.mkdir();self.calls=[];self.saved={};self.mode='normal';self.requested=threading.Event()
        owner=self
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):owner.fail('unexpected automatic GET')
            def do_POST(self):
                body=notice.decode(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append((self.path,self.headers['Authorization']=='Bearer fixture-token',body))
                owner.requested.set()
                if owner.mode=='slow':owner.release.wait(5)
                if owner.mode=='redirect':
                    self.send_response(307);self.send_header('Location','/api/v1/vms');self.end_headers();return
                if owner.mode=='rejected':self.send_response(403);self.end_headers();return
                owner.saved.setdefault(body['vm_id'],body)
                if owner.mode=='lost':self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                reply=({'vm_id':body['vm_id'],'registration':'confirmed'} if owner.mode!='accepted'
                       else {'vm_id':body['vm_id'],'registration':'accepted'})
                raw=json.dumps(reply).encode()
                if owner.mode=='invalid':raw=b'{"vm_id":1,"vm_id":2}'
                if owner.mode=='oversize':raw=b'x'*5000
                self.send_response(201);self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);self.server.daemon_threads=True
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.release=threading.Event();self.addCleanup(self.release.set)
        self.token=self.root/'token.json';self.token.write_text('{"token":"fixture-token"}');self.token.chmod(0o600)
        self.config=self.root/'notify.json'
        self.config.write_text(json.dumps({'schema_version':'1.0',
            'endpoint':f'http://127.0.0.1:{self.server.server_port}/api/v1/vms/registrations',
            'token_file':str(self.token),'timeout_ms':300,'allow_loopback_http':True}));self.config.chmod(0o600)
        self.record={'uuid':VM,'id':'vm','name':'vm','guest_kind':'linux','template':'debian-fixture',
                     'template_version':'1.0.0','vcpus':2,'memory_mib':512,'ipv4':'192.0.2.2'}
        hook=patch.object(notice,'CONFIG',self.config);hook.start();self.addCleanup(hook.stop)

    def manual(self):return notice.launch(self.data,self.run,self.record,config=self.config,automatic=False)

    def wait_result(self,identity):
        path=self.data/'registration-notices'/(identity+'.json');end=time.monotonic()+3
        while time.monotonic()<end:
            if path.exists():
                result=notice.private_json(path)
                if result['state']!='attempt-consumed':return result
            time.sleep(.01)
        self.fail('finite notification did not finish')

    def management(self):
        from test_xenova_management import ManagementTest
        fixture=ManagementTest('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        return fixture,fixture.m,fixture.spec

    def test_actual_sender_confirms_only_registration_contract_and_never_create_callback(self):
        result=self.manual();self.assertTrue(result['confirmed'])
        self.assertEqual(self.calls[0][0],'/api/v1/vms/registrations');self.assertTrue(self.calls[0][1])
        self.assertEqual(self.calls[0][2]['vm_id'],VM);self.assertEqual(len(self.calls),1)
        self.assertNotIn('token',json.dumps(self.calls[0][2]));self.assertNotIn('desired_state',self.calls[0][2])
        self.mode='accepted';self.assertEqual(self.manual()['state'],'response-unconfirmed')
        self.mode='redirect';self.assertEqual(self.manual()['state'],'redirect-refused')
        self.mode='rejected';self.assertEqual(self.manual()['state'],'rejected')
        self.mode='invalid';self.assertEqual(self.manual()['state'],'response-invalid')
        self.mode='oversize';self.assertEqual(self.manual()['state'],'response-invalid')
        self.assertEqual(len(self.calls),6)

    def test_response_loss_manual_resend_is_one_more_explicit_send_and_no_db_duplicate(self):
        self.mode='lost';self.assertEqual(self.manual()['state'],'delivery-unknown')
        self.assertEqual(len(self.calls),1);self.mode='normal'
        self.assertTrue(self.manual()['confirmed']);self.assertEqual(len(self.calls),2);self.assertEqual(len(self.saved),1)

    def test_slow_sink_is_finite_and_never_holds_management_fd_or_retries(self):
        self.mode='slow';before=time.monotonic()
        self.assertEqual(notice.launch(self.data,self.run,self.record,config=self.config)['state'],'attempt-started')
        self.assertLess(time.monotonic()-before,.25)
        self.assertEqual(self.wait_result(VM)['state'],'delivery-unknown');self.assertEqual(len(self.calls),1)
        self.release.set();self.mode='normal';self.assertTrue(self.manual()['confirmed'])

    def test_real_create_and_replay_hook_consumes_op_once_before_launch(self):
        f,manager,spec=self.management()
        # Existing product fixture substitutes guest disk prepare only; journal/registry are real.
        result=manager.call('create',spec,execute=True)
        self.assertTrue(result['ok'],result)
        path=f.data/'registration-notices'/(spec['vm_id']+'.json');end=time.monotonic()+3
        while not path.exists() and time.monotonic()<end:time.sleep(.01)
        while path.exists() and notice.private_json(path)['state']=='attempt-consumed' and time.monotonic()<end:time.sleep(.01)
        self.assertEqual(len(self.calls),1)
        with patch.object(notice,'automatic',side_effect=AssertionError('replay notification')):
            self.assertTrue(manager.fork().call('create',spec,execute=True)['ok'])
        operation=manager.call('operation',{'idempotency_key':'create:'+spec['vm_id']})
        self.assertEqual(operation['data']['operation']['registration_notice'],'consumed')

    def test_factory_first_completion_once_and_completed_boot_never_resends(self):
        from first_install_fixture import Board
        b=Board(self.root/'board');b.grow_mount();b.factory();b.p.register()
        end=time.monotonic()+3
        while len(self.calls)<2 and time.monotonic()<end:time.sleep(.01)
        self.assertEqual(len(self.calls),2)
        with patch.object(notice,'automatic',side_effect=AssertionError('completed boot resend')):b.p.register()

    def test_crash_after_journal_success_before_spawn_never_refills_opportunity(self):
        f,manager,spec=self.management()
        with patch.object(notice,'automatic',return_value={'state':'launch-unconfirmed'}):
            self.assertTrue(manager.call('create',spec,execute=True)['ok'])
        self.assertTrue(manager.fork().call('create',spec,execute=True)['ok']);self.assertEqual(len(self.calls),0)

    def test_manual_cli_uses_same_sender_and_rejects_wrong_options_and_retired(self):
        import xenova_management_cli as cli
        f,manager,spec=self.management()
        with patch.object(notice,'automatic',return_value={}):self.assertTrue(manager.call('create',spec,execute=True)['ok'])
        identity=spec['vm_id']
        for extra in ([],['--json']):
            with redirect_stdout(io.StringIO()):result=cli.main(['registration-notify',identity,'--execute',*extra],manager)
            self.assertTrue(result['data']['notification']['confirmed'])
        self.assertEqual(len(self.calls),2)
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):cli.main(['registration-notify',identity,'--force'],manager)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(manager.call('retire',{'vm_id':identity},execute=True,idempotency_key='archive-notify')['ok'])
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):cli.main(['registration-notify',identity,'--execute'],manager)
        self.assertEqual(len(self.calls),2)

    def test_capacity_is_bounded_and_recovers_without_automatic_retry(self):
        from uuid import uuid4
        self.mode='slow';active=[]
        for _ in range(notice.MAX_CHILDREN):
            record={**self.record,'uuid':str(uuid4())};active.append(record['uuid'])
            self.assertEqual(notice.launch(self.data,self.run,record,config=self.config)['state'],'attempt-started')
        self.assertEqual(notice.launch(self.data,self.run,self.record,config=self.config)['state'],'capacity-skipped')
        for identity in active:self.assertEqual(self.wait_result(identity)['state'],'delivery-unknown')
        count=len(self.calls);time.sleep(.1);self.assertEqual(len(self.calls),count)
        self.release.set();self.mode='normal';self.assertTrue(self.manual()['confirmed'])

    def test_claim_and_configuration_failures_preserve_real_registration(self):
        f,manager,spec=self.management()
        with patch.object(notice,'launch',side_effect=OSError(28,'fixture optional record full')):
            result=manager.call('create',spec,execute=True)
        self.assertTrue(result['ok'],result);self.assertEqual(len(self.calls),0)
        self.config.chmod(0o644);self.assertEqual(self.manual()['state'],'configuration-invalid')
        self.assertEqual(len(self.calls),0)
        destination=self.data/'registration-notices'/'invalid-supervisor.json'
        wire=(json.dumps(notice.payload(self.record))+'\n').encode()
        with (patch.object(notice.select,'select',return_value=([0],[],[])),
              patch.object(notice.os,'read',return_value=wire),
              patch.object(notice.os,'fork',side_effect=AssertionError('invalid config must not launch sender'))):
            notice.supervise(destination,self.config,-1,time.monotonic()+1,False)
        self.assertEqual(notice.private_json(destination)['state'],'configuration-invalid')

    def test_real_create_reply_and_other_physical_stop_finish_while_notification_waits(self):
        import test_xenova_recovery_foundation as foundation
        f=foundation.FoundationTest('runTest')
        with patch.object(notice,'automatic',return_value={}):f.setUp()
        self.addCleanup(f.doCleanups);f.start()
        config=notice.private_json(self.config);config['timeout_ms']=3000
        self.config.write_text(json.dumps(config));self.mode='slow'
        spec={**f.f.spec,'vm_id':VM,'name':'notification-A','ipv4':'192.168.77.31'}
        try:
            created=f.m.call('create',spec,execute=True);self.assertTrue(created['ok'],created)
            self.assertTrue(self.requested.wait(2));self.assertFalse(self.release.is_set())
            stopped=f.m.fork().call('stop',{'vm_id':f.vm_id},idempotency_key='under-slow-registration')
            self.assertTrue(stopped['ok'],stopped);self.assertEqual(len(f.physical('shutdown')),1)
            original=f.m.fork().call('control-operation',{'idempotency_key':'under-slow-registration'})
            self.assertTrue(original['ok'],original);self.assertEqual(original['data']['operation']['phase'],'completed')
            path=f.p.data/'registration-notices'/(VM+'.json')
            self.assertEqual(notice.private_json(path)['state'],'attempt-consumed')
        finally:self.release.set()

    def test_optional_result_fsync_is_not_in_the_registration_caller(self):
        with patch.object(notice,'atomic_write',side_effect=AssertionError('optional record in caller')):
            accepted=notice.launch(self.data,self.run,self.record,config=self.config)
        self.assertEqual(accepted['state'],'attempt-started')
        self.assertTrue(self.wait_result(VM)['confirmed'])

    def test_actual_resolver_stall_is_killed_by_sender_supervisor_then_normal_send_recovers(self):
        library=self.root/'slow-resolver.so'
        source='#include <netdb.h>\n#include <unistd.h>\nint getaddrinfo(const char *n,const char *s,const struct addrinfo *h,struct addrinfo **r){(void)n;(void)s;(void)h;(void)r;sleep(5);return EAI_AGAIN;}\n'
        subprocess.run(['cc','-shared','-fPIC','-x','c','-','-o',str(library)],input=source,text=True,check=True,capture_output=True)
        with patch.dict(os.environ,{'LD_PRELOAD':str(library)}):
            result=self.manual()
        self.assertEqual(result['state'],'delivery-unknown');self.assertEqual(len(self.calls),0)
        self.assertTrue(self.manual()['confirmed']);self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
