"""G1: real processes/socket/actor/Authority; app/Xen/output boundaries are fixtures."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest

from xenova_gateway.authority import AuthorityError, GatewayLease, AuthorityStore
from xenova_gateway.client import GatewayClient, GatewayRejected, GatewayUnavailable
from xenova_gateway.ipc import GatewayServer
from xenova_gateway.ipc_contract import HEADER, MAX_BYTES, decode, encode
from xenova_gateway.machine import Mode
from xenova_gateway.provider import ProviderError

LAB = Path(__file__).resolve().parents[1]/'examples/provider_lab.py'
A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


@pytest.fixture
def process_server(tmp_path):
    root = tmp_path/'xenova-gateway-lab-process'
    proc = subprocess.Popen([sys.executable, '-B', str(LAB), '--directory', str(root)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = proc.stdout.readline()
        if not ready:
            raise AssertionError(proc.stderr.read())
        info = json.loads(ready)
        yield GatewayClient(info['endpoint'], uid=info['uid']), proc, root
    finally:
        if proc.poll() is None: proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close(); proc.stderr.close()


@pytest.fixture
def actor_server(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('owned_gateway_lab', LAB)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    lab = module.Lab(tmp_path/'xenova-gateway-lab-actor')
    stop = threading.Event()
    def loop():
        while not stop.is_set():
            lab.feed(); lab.runner.run_once(.002)
    thread = threading.Thread(target=loop); thread.start()
    try:
        yield lab, GatewayClient(lab.server.path, uid=os.geteuid())
    finally:
        stop.set(); thread.join(5)
        assert not thread.is_alive()
        lab.close()


def submission(client, target, purpose='protect', **extra):
    context = client.context(); binding = context['targets'][target]
    return dict(host_boot_id=context['host_boot_id'], expected_owner=context['owner'],
                expected_generation=context['generation'], target_vm=target,
                target_execution_id=binding['execution_id'], intent_revision=binding['intent_revision'],
                lifecycle_generation=binding['lifecycle_generation'], deadline_ms=int(time.monotonic()*1000)+2000,
                purpose=purpose, **extra)


def test_c01_separate_gateway_and_cli_processes_submit_lookup_and_cancel(process_server):
    client, proc, root = process_server
    request = str(uuid4()); spec = submission(client, B)
    specfile = root/'submit.json'; specfile.write_text(json.dumps(spec))
    argv = [sys.executable, '-B', '-m', 'xenova_gateway.client', 'submit', '--request-id', request,
            '--spec-file', str(specfile), '--endpoint', client.endpoint,'--peer-uid',str(os.geteuid())]
    # The production deployment uses root on both sides. The non-root lab calls
    # the same client with its explicitly selected peer uid in a child process.
    code = 'from xenova_gateway.client import GatewayClient;import json,os,sys;print(json.dumps(GatewayClient(sys.argv[1],uid=os.geteuid()).submit(sys.argv[2],json.load(open(sys.argv[3])))))'
    result = subprocess.run(argv,
                            capture_output=True, text=True, check=True)
    accepted = json.loads(result.stdout)
    assert accepted['phase']=='PROTECTED' and accepted['acceptance']=='durable'
    assert client.get(request)==accepted or client.get(request)['phase']=='PROTECTED'
    assert proc.poll() is None
    current = client.context()
    assert current['owner']==A and current['output_permitted']
    ended = client.cancel(request, context=dict(host_boot_id=current['host_boot_id'],
        expected_generation=current['generation'], target_execution_id=spec['target_execution_id']))
    assert ended['phase']=='CANCELLED' and not ended['protection']['held']
    assert not ended['device']['feedback_supported'] and ended['device']['physical_stop'] is None


def test_c02_real_plan_commit_and_unready_target_preserves_owner(actor_server):
    lab, client = actor_server
    request = str(uuid4()); spec = submission(client, B, 'handoff')
    first = client.submit(request, spec)
    assert first['phase']=='PREPARING' and client.context()['owner']==A
    result = client.commit(request)
    assert result['phase']=='COMMITTED' and result['committed_generation']==spec['expected_generation']+1
    end = time.monotonic()+1
    while time.monotonic()<end and not client.get(request)['app_applied']: time.sleep(.005)
    assert client.get(request)['app_applied'] and client.context()['owner']==B
    assert client.commit(request)['committed_generation']==result['committed_generation']
    # A separate unready plan is never committed by pretending queue acceptance
    # is handoff completion.
    lab.ready[A] = False
    time.sleep(.02)
    next_id = str(uuid4()); next_spec = submission(client, A, 'handoff')
    client.submit(next_id, next_spec)
    with pytest.raises(GatewayRejected): client.commit(next_id)
    assert client.context()['owner']==B and client.context()['output_permitted']
    assert client.cancel(next_id)['phase']=='CANCELLED'


def test_c03_active_protection_precedes_durable_write_and_never_rearms(actor_server, monkeypatch):
    lab, client = actor_server
    request = str(uuid4()); spec = submission(client, A)
    entered, release = threading.Event(), threading.Event()
    from xenova_gateway import authority
    original = authority.atomic_json
    def slow(path, value):
        if any(r['request_id']==request for r in value.get('protections', [])):
            entered.set(); assert release.wait(3)
        return original(path, value)
    monkeypatch.setattr(authority, 'atomic_json', slow)
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(client.submit(request,spec))); worker.start()
    try:
        assert entered.wait(1)
        assert not lab.runner.machine.output_permitted
        assert any(type(frame).__name__=='StopFrame' for _,frame,_ in list(lab.mock.frames)[-4:])
        assert worker.is_alive()
    finally:
        release.set(); worker.join(3)
    assert outcome[0]['phase']=='PROTECTED' and outcome[0]['protection']['owner_excluded']
    generation = client.context()['generation']
    with pytest.raises(AuthorityError):
        lab.auth.issue(Mode.AUTO,A,'invalid reselect',time.monotonic(),1,arm=True)
    for _ in range(3):
        context=client.context()
        try:
            cancelled=client.cancel(request,context=dict(host_boot_id=context['host_boot_id'],
                expected_generation=context['generation'],target_execution_id=spec['target_execution_id']))
            break
        except GatewayRejected as exc:
            # Existing SAFE_STOP -> disarmed MANUAL may legitimately race with
            # an explicit cancel. The SDK never auto-renews this approval.
            assert exc.code=='AUTHORITY_CHANGED' and client.get(request)['protection']['held']
    else:pytest.fail('normal cancellation did not settle')
    assert cancelled['phase']=='CANCELLED' and cancelled['current_generation']==context['generation']
    assert client.context()['generation']>=generation and not client.context()['output_permitted']


def test_c04_equivalent_concurrent_requests_one_record_and_actual_conflict(actor_server):
    lab, client = actor_server
    from concurrent.futures import ThreadPoolExecutor
    request = str(uuid4()); explicit = submission(client,B)
    omitted = {k:v for k,v in reversed(list(explicit.items())) if k!='purpose'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda s: client.submit(request,s), [explicit,omitted,explicit,omitted]))
    assert {r['phase'] for r in rows}=={'PROTECTED'}
    assert len(lab.auth.value['protections'])==1
    changed = {**explicit,'target_execution_id':str(uuid4())}
    with pytest.raises(GatewayRejected,match='IDEMPOTENCY_CONFLICT'): client.submit(request,changed)
    assert client.context()['generation']==explicit['expected_generation']


def test_c05_response_loss_get_and_restart_original_result_without_auto_rearm(process_server):
    client, proc, root = process_server
    request = str(uuid4()); spec = submission(client,B)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
        stream.connect(client.endpoint)
        stream.sendall(encode(dict(version=1,action='submit',request_id=request,spec=spec)))
    end = time.monotonic()+1
    while True:
        try: row=client.get(request);break
        except GatewayRejected as exc:
            assert exc.code=='NOT_FOUND' and time.monotonic()<end
            time.sleep(.005)
    before=(root/'authority/control.json').read_bytes()
    proc.terminate();proc.wait(3)
    restarted=subprocess.Popen([sys.executable,'-B',str(LAB),'--directory',str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert restarted.stdout.readline(),restarted.stderr.read()
        recovered=client.get(request)
        assert recovered['phase']=='PROTECTED' and recovered['deadline_ms']==row['deadline_ms']
        assert client.submit(request,spec)['target_execution_id']==spec['target_execution_id']
        assert not client.context()['output_permitted']
        assert json.loads(before)['protections']==json.loads((root/'authority/control.json').read_bytes())['protections']
    finally:
        restarted.terminate();restarted.wait(3);restarted.stdout.close();restarted.stderr.close()


def test_c08_exclusion_new_binding_finish_and_stale_completion(actor_server):
    lab, client=actor_server
    request=str(uuid4());spec=submission(client,B);client.submit(request,spec)
    current=client.context()
    with pytest.raises(GatewayRejected,match='TARGET_CHANGED'):
        client.finish(request,dict(host_boot_id=current['host_boot_id'],expected_generation=current['generation'],
                                   target_execution_id=spec['target_execution_id']))
    lab.executions[B]=str(uuid4())
    lab.instances[B]={app:str(uuid4()) for app in ('autonomous','manual')}
    time.sleep(.02)
    assert lab.auth.protected(B)
    current=client.context();end=dict(host_boot_id=current['host_boot_id'],expected_generation=current['generation'],
                                    target_execution_id=lab.executions[B])
    assert client.finish(request,end)['phase']=='RELEASED'
    assert client.finish(request,end)['phase']=='RELEASED'
    with pytest.raises(GatewayRejected,match='IDEMPOTENCY_CONFLICT'):
        client.cancel(request,context=end)
    assert not lab.auth.protected(B)
    next_request=str(uuid4())
    next_spec=submission(client,B,'handoff')
    client.submit(next_request,next_spec)
    assert client.commit(next_request)['phase']=='COMMITTED'
    end_at=time.monotonic()+1
    while time.monotonic()<end_at and not client.get(next_request)['app_applied']:time.sleep(.005)
    assert client.get(next_request)['app_applied'] # released protection permits legitimate normal progress


@pytest.mark.parametrize('payload',[
    HEADER.pack(MAX_BYTES+1), HEADER.pack(1)+b'\xff',
    HEADER.pack(23)+b'{"version":1,"version":1}',
    HEADER.pack(16)+b'{"version": NaN}',
    encode(dict(version=1,action='context'))*2,
    HEADER.pack(4001)+b'['*2000+b'0'+b']'*2000,
])
def test_c09_malformed_is_bounded_and_normal_request_recovers(process_server,payload):
    client,proc,root=process_server
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
        stream.settimeout(2);stream.connect(client.endpoint);stream.sendall(payload)
        stream.recv(MAX_BYTES)
    assert client.context()['owner']==A and proc.poll() is None


def test_c09_slow_partial_connections_do_not_block_actor_or_other_client(process_server):
    client,proc,root=process_server
    connections=[]
    try:
        for _ in range(8):
            stream=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);stream.connect(client.endpoint)
            stream.sendall(b'\x00');connections.append(stream)
        request=str(uuid4());spec=submission(client,B)
        assert client.submit(request,spec)['phase']=='PROTECTED'
        assert client.context()['output_permitted']
    finally:
        for stream in connections:stream.close()


def test_c10_endpoint_and_peer_rejection_leave_active_gateway_intact(process_server):
    client,proc,root=process_server
    inode=Path(client.endpoint).stat().st_ino
    with pytest.raises(AuthorityError): GatewayLease(root/'gateway.lock').acquire()
    with pytest.raises(GatewayUnavailable): GatewayClient(client.endpoint,uid=os.geteuid()+1).context()
    assert Path(client.endpoint).stat().st_ino==inode and client.context()['output_permitted']


def test_client_import_does_not_load_paho_or_server():
    code='import sys;from xenova_gateway.client import GatewayClient;assert "xenova_gateway.ipc" not in sys.modules;assert "paho.mqtt.client" not in sys.modules'
    subprocess.run([sys.executable,'-B','-c',code],check=True)


def test_documented_self_contained_example_is_executable():
    result=subprocess.run([sys.executable,'-B',str(LAB.with_name('provider_roundtrip.py'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout.splitlines()[0])['cancelled']['phase']=='CANCELLED'


@pytest.mark.parametrize('after_replace',[False,True])
def test_c06_storage_failure_is_uncertain_and_restart_preserves_what_reached_disk(actor_server,monkeypatch,after_replace):
    lab,client=actor_server
    request=str(uuid4());spec=submission(client,B)
    from xenova_gateway import authority
    original=authority.atomic_json
    def failed(path,value):
        if any(r['request_id']==request for r in value.get('protections',[])):
            if after_replace:original(path,value)
            raise OSError(5,'owned storage EIO')
        return original(path,value)
    monkeypatch.setattr(authority,'atomic_json',failed)
    with pytest.raises(GatewayRejected,match='STORAGE_UNCERTAIN'):client.submit(request,spec)
    assert not client.context()['output_permitted']
    if not after_replace:
        with pytest.raises(GatewayRejected,match='OUTCOME_UNKNOWN'):client.get(request)
    disk=AuthorityStore(lab.auth.directory,lab.auth.host,uid=os.geteuid(),mountpoint=None)
    disk.load()
    assert bool(disk.value.get('protections'))==after_replace
    assert lab.auth.error=='AUTHORITY_STORAGE_UNCERTAIN'


def test_c07_committed_cancel_does_not_rollback_and_stale_completion_is_rejected(actor_server):
    lab,client=actor_server
    request=str(uuid4());spec=submission(client,B,'handoff')
    client.submit(request,spec);committed=client.commit(request)
    cancelled=client.cancel(request)
    assert cancelled['phase']=='COMMITTED'
    assert cancelled['committed_generation']==committed['committed_generation']
    assert client.context()['owner']==B
    protection=str(uuid4());target=submission(client,A)
    client.submit(protection,target)
    context=client.context()
    with pytest.raises(GatewayRejected,match='AUTHORITY_CHANGED'):
        client.cancel(protection,context=dict(host_boot_id=context['host_boot_id'],
            expected_generation=context['generation']-1,target_execution_id=target['target_execution_id']))
    assert client.get(protection)['protection']['held']


def test_get_has_no_extra_store_or_external_observation_and_invalid_has_no_acceptance(actor_server,monkeypatch):
    lab,client=actor_server
    request=str(uuid4());spec=submission(client,B)
    from xenova_gateway import authority
    original=authority.atomic_json;writes=[]
    def counted(path,value):writes.append(value);return original(path,value)
    monkeypatch.setattr(authority,'atomic_json',counted)
    client.submit(request,spec)
    accepted_writes=len(writes)
    for _ in range(40):assert client.get(request)['phase']=='PROTECTED'
    assert accepted_writes==1 and len(writes)==accepted_writes
    for changed in ({'deadline_ms':None},{'intent_revision':True},{'unexpected':1},
                    {'parent_deadline_ms':spec['deadline_ms']-1}):
        with pytest.raises(GatewayRejected,match='INVALID_INPUT'):
            client.submit(str(uuid4()),{**spec,**changed})
    assert len(writes)==accepted_writes


def test_c14_command_expiry_stop_runs_while_actor_storage_is_blocked(actor_server,monkeypatch):
    lab,client=actor_server
    request=str(uuid4());spec=submission(client,B,'handoff')
    entered,release=threading.Event(),threading.Event();out=[]
    from xenova_gateway import authority
    original=authority.atomic_json
    def slow(path,value):
        if any(r['request_id']==request for r in value.get('transitions',[])):
            entered.set();assert release.wait(3)
        return original(path,value)
    monkeypatch.setattr(authority,'atomic_json',slow)
    start=lab.output.watchdog_stops
    thread=threading.Thread(target=lambda:out.append(client.submit(request,spec)));thread.start()
    try:
        assert entered.wait(1)
        end=time.monotonic()+.5
        while lab.output.watchdog_stops==start and time.monotonic()<end:time.sleep(.005)
        assert lab.output.watchdog_stops>start and thread.is_alive()
        assert type(lab.mock.frames[-1][1]).__name__=='StopFrame'
    finally:release.set();thread.join(3)
    assert out[0]['phase']=='PREPARING'


def test_unconfirmed_device_write_retains_one_stop_and_prohibits_late_new_drive():
    from xenova_gateway.output_owner import OutputOwner
    from xenova_gateway.sink import MockSink,SinkError
    from xenova_gateway.frame import DriveFrame,StopFrame
    entered,release=threading.Event(),threading.Event()
    class Slow(MockSink):
        def write(self,frame,t):
            if isinstance(frame,DriveFrame):entered.set();assert release.wait(1)
            return super().write(frame,t)
    sink=Slow();owner=OutputOwner(sink,command_timeout_s=.1)
    try:
        with pytest.raises(SinkError,match='unconfirmed'):owner.write(DriveFrame(1,1,.1,0),time.monotonic())
        assert entered.is_set()
        with pytest.raises(SinkError):owner.write(DriveFrame(1,2,.1,0),time.monotonic())
        release.set()
        end=time.monotonic()+.5
        while (not sink.frames or not isinstance(sink.frames[-1][1],StopFrame)) and time.monotonic()<end:time.sleep(.005)
        assert [type(f).__name__ for _,f,_ in sink.frames]==['DriveFrame','StopFrame']
        assert sink.frames[-1][1].counter!=sink.frames[0][1].counter
    finally:release.set();owner.close()


def test_c09_nonreaders_overload_bounded_resources_and_normal_recovery(actor_server,monkeypatch):
    lab,client=actor_server
    baseline=len(os.listdir('/proc/self/fd'));threads=threading.active_count();streams=[]
    try:
        for index in range(24):
            stream=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);stream.connect(client.endpoint)
            try:stream.sendall(b'\x00' if index%2 else encode(dict(version=1,action='context')))
            except (BrokenPipeError,ConnectionResetError):pass
            streams.append(stream) # deliberately never read responses
        end=time.monotonic()+2
        while time.monotonic()<end and lab.server.stats['expired']==0:time.sleep(.005)
    finally:
        for stream in streams:stream.close()
    assert client.context()['output_permitted'] and lab.server.failure is None
    time.sleep(.03)
    assert lab.server.stats['high_water']<=16 and threading.active_count()==threads
    assert len(os.listdir('/proc/self/fd'))<=baseline+1
    assert lab.runner.provider_high_water<=16


def test_c10_kernel_peer_identity_precedes_message_and_foreign_endpoint_is_not_removed(actor_server):
    lab,client=actor_server;before=lab.server.stats['peer_denied'];lab.server.uid=os.geteuid()+1
    try:
        with pytest.raises(GatewayUnavailable):client.context()
    finally:lab.server.uid=os.geteuid()
    assert lab.server.stats['peer_denied']==before+1 and client.context()['output_permitted']
    endpoint=lab.server.path.parent/'foreign';endpoint.write_text('owned regular fixture')
    with pytest.raises(ValueError,match='foreign endpoint'):
        GatewayServer(lab.runner,lab.lease,endpoint,uid=os.geteuid()).start()
    assert endpoint.read_text()=='owned regular fixture'


def test_provider_cost_original_acceptance_replay_and_lookup(actor_server,monkeypatch):
    import tracemalloc
    lab,client=actor_server
    from xenova_gateway import authority
    original=authority.atomic_json;writes=[];actor=[]
    def counted(path,value):writes.append(time.monotonic());return original(path,value)
    monkeypatch.setattr(authority,'atomic_json',counted)
    handle=lab.runner.provider.handle
    def timed(call):
        start=time.perf_counter_ns()
        try:return handle(call)
        finally:actor.append(dict(action=call['action'],ns=time.perf_counter_ns()-start))
    monkeypatch.setattr(lab.runner.provider,'handle',timed)
    identity=str(uuid4());spec=submission(client,B)
    start=time.perf_counter_ns();client.submit(identity,spec);submit=time.perf_counter_ns()-start
    baseline=len(writes);samples=[];tracemalloc.start()
    for _ in range(40):
        start=time.perf_counter_ns();assert client.get(identity)['phase']=='PROTECTED';samples.append(time.perf_counter_ns()-start)
    for _ in range(8):assert client.submit(identity,spec)['phase']=='PROTECTED'
    memory=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert baseline==1 and len(writes)==1
    measured=dict(fixture='owned Lab/mock apps/output; real UDS/Authority',submit_ns=submit,lookup_ns=samples,
        actor=actor,atomic_saves=len(writes),additional_lookup_replay_saves=len(writes)-baseline,
        memory_current_peak=memory,io=lab.server.stats,mailbox_high_water=lab.runner.provider_high_water,
        fd=len(os.listdir('/proc/self/fd')),threads=threading.active_count())
    path=os.environ.get('XENOVA_PROVIDER_METRICS')
    if path:Path(path).write_text(json.dumps(measured,indent=2))


def test_c14_slow_critical_diagnostic_does_not_stop_device_expiry():
    import logging
    from xenova_gateway.diagnostics import GatewayDiagnostics
    from xenova_gateway.output_owner import OutputOwner
    from xenova_gateway.sink import MockSink
    from xenova_gateway.frame import DriveFrame,StopFrame
    entered,release=threading.Event(),threading.Event()
    class Slow(logging.Handler):
        def emit(self,record):entered.set();assert release.wait(2)
    logger=logging.getLogger('gateway-fixture-critical');logger.handlers=[Slow()];logger.propagate=False
    diagnostic=GatewayDiagnostics(logger);sink=MockSink();owner=OutputOwner(sink,command_timeout_s=.05)
    producer=threading.Thread(target=lambda:logger.critical('owned rare defect; cause retained'))
    try:
        owner.write(DriveFrame(42,0,.1,0),time.monotonic());producer.start();assert entered.wait(.5)
        end=time.monotonic()+.5
        while not owner.watchdog_stops and time.monotonic()<end:time.sleep(.003)
        assert producer.is_alive() and owner.watchdog_stops==1 and isinstance(sink.frames[-1][1],StopFrame)
        assert 'owned rare defect' in diagnostic.last_critical
    finally:
        release.set();producer.join(2);owner.close();diagnostic.close();logger.handlers=[]


def test_c10_unconfirmed_device_termination_keeps_existing_gateway_lease(tmp_path):
    from xenova_gateway.__main__ import _release_lease
    from xenova_gateway.output_owner import OutputOwner
    from xenova_gateway.sink import MockSink,SinkError
    from xenova_gateway.frame import StopFrame
    entered,release=threading.Event(),threading.Event()
    class Blocked(MockSink):
        def write(self,frame,now):entered.set();assert release.wait(3);return super().write(frame,now)
    lease=GatewayLease(tmp_path/'lease').acquire();owner=OutputOwner(Blocked(),command_timeout_s=.1)
    try:
        with pytest.raises(SinkError):owner.write(StopFrame(42,0,1),time.monotonic())
        assert entered.is_set()
        with pytest.raises(SinkError,match='termination unconfirmed'):owner.close()
        with pytest.raises(AuthorityError,match='lease retained'):_release_lease(lease,owner,None)
        with pytest.raises(AuthorityError):GatewayLease(tmp_path/'lease').acquire()
    finally:release.set();owner.close();_release_lease(lease,owner,None)
    successor=GatewayLease(tmp_path/'lease').acquire();successor.close()


@pytest.mark.parametrize('committed',[False,True])
def test_c06_sigkill_after_actual_ipc_acceptance_or_commit_recovers_original(process_server,committed):
    client,proc,root=process_server;key=str(uuid4());spec=submission(client,B,'handoff')
    accepted=client.submit(key,spec)
    if committed:accepted=client.commit(key)
    proc.kill();proc.wait(timeout=3)
    disk=json.loads((root/'authority/control.json').read_text())
    original=next(row for row in disk['transitions'] if row['request_id']==key)
    assert Path(client.endpoint).exists() # stale pathname, not live ownership
    successor=subprocess.Popen([sys.executable,'-B',str(LAB),'--directory',str(root)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=successor.stdout.readline();assert line,successor.stderr.read()
        recovered=client.submit(key,spec)
        assert recovered['phase']==('COMMITTED' if committed else 'ABANDONED')
        assert recovered['deadline_ms']==accepted['deadline_ms'] and not client.context()['output_permitted']
        now=json.loads((root/'authority/control.json').read_text())
        row=next(row for row in now['transitions'] if row['request_id']==key)
        assert row['spec']==original['spec'] and len(now['transitions'])==len(disk['transitions'])
        if committed:assert recovered['committed_generation']==accepted['committed_generation']
    finally:
        successor.terminate();successor.communicate(timeout=5)
