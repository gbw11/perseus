"""실제 한정 HTTP 자식의 DNS 정체·종료·slot 반환. 네트워크는 loopback fixture다."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from dom0_manager.config import Settings
from dom0_manager.registration_sender import RegistrationSender, configuration, decode

VM='850e8400-e29b-41d4-a716-446655440001'


@pytest.fixture
def sender(tmp_path):
    release=threading.Event();received=threading.Event()
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));received.set();release.wait(3)
            raw=json.dumps({'vm_id':VM,'registration':'confirmed'}).encode()
            self.send_response(201);self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (OSError,ValueError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    instance=RegistrationSender(Settings(registration_url=f'http://127.0.0.1:{server.server_port}/api/v1/vms/registrations',
        registration_token='fixture-outbound',registration_timeout_s=.4,registration_slot_dir=str(tmp_path/'slots')))
    yield instance,release,received,tmp_path
    release.set();instance.close();server.shutdown();server.server_close()


def body():return {'data':{'registration':{'vm_id':VM}}}


def test_real_dns_stall_terminated_then_same_sender_normal_recovery(sender):
    instance,release,received,root=sender
    library=root/'slow-resolver.so'
    source='#include <netdb.h>\n#include <unistd.h>\nint getaddrinfo(const char *n,const char *s,const struct addrinfo *h,struct addrinfo **r){(void)n;(void)s;(void)h;(void)r;sleep(5);return EAI_AGAIN;}\n'
    subprocess.run(['cc','-shared','-fPIC','-x','c','-','-o',str(library)],input=source,text=True,check=True,capture_output=True)
    before=time.monotonic()
    with patch.dict(os.environ,{'LD_PRELOAD':str(library)}):result=instance.send(body(),time.monotonic()+.4)
    assert result['state']=='delivery-unknown'
    assert time.monotonic()-before<2 and not received.is_set() and not instance._children
    release.set();assert instance.send(body(),time.monotonic()+1)['confirmed']


def test_shutdown_kills_owned_children_releases_slots_and_does_not_retry(sender):
    instance,release,received,root=sender
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(instance.send,body(),time.monotonic()+2)
        assert received.wait(1)
        pids=[child.pid for child in instance._children];assert len(pids)==1
        instance.close();assert not pending.result()['confirmed']
    assert not instance._children
    assert all(not Path('/proc',str(pid)).exists() for pid in pids)
    assert instance.send(body(),time.monotonic()+1)['state']=='sender-closed'


@pytest.mark.parametrize('timeout',['bad','0','nan','inf','-1','16'])
def test_invalid_outbound_configuration_does_not_break_settings_or_other_features(timeout):
    settings=Settings.from_env({'XENOVA_REGISTRATION_TIMEOUT':timeout,
        'XENOVA_REGISTRATION_URL':'http://127.0.0.1/api/v1/vms/registrations',
        'VM_REGISTRATION_SERVICE_TOKEN':'fixture-outbound'})
    assert settings.driver=='mock'
    with pytest.raises(ValueError):configuration(settings)


def test_duplicate_json_and_nonfinite_remain_errors():
    for raw in ('{"a":1,"a":2}','{"a":NaN}'):
        with pytest.raises(ValueError):decode(raw)


def test_notify_default_budget_starts_before_async_dependency_and_sync_thread_queue():
    from fastapi.testclient import TestClient
    from dom0_manager.main import create_app
    from dom0_manager import budget
    from dom0_manager.api.registrations import authenticated_registration
    app=create_app(Settings(service_token='fixture-api-token',log_level='CRITICAL'))
    seen=[]
    async def probe():seen.append(budget.current())
    app.dependency_overrides[authenticated_registration]=probe
    with TestClient(app) as client:
        response=client.post('/api/v1/vm-registrations/'+VM+'/notify',headers={'Authorization':'Bearer fixture-api-token'})
    assert response.status_code==501  # mock driver has no registered-metadata implementation
    assert len(seen)==1 and seen[0] is not None and 0<seen[0].remaining_s()<=15
