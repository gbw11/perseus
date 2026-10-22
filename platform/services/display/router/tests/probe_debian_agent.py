"""Opt-in real QEMU Debian Agent -> dom0 broker/feed acceptance.

Stages existing code and ARM64 wheels in unique temporary directories only.
No apt/pip on guests, systemd changes, firmware changes or synthetic publishers.
Credentials stay in memory; temporary broker uses account/topic restrictions.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import zipfile

import httpx
from passlib.hash import sha512_crypt
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[5]
SSH = ['ssh', '-F', '/home/ssafy/xenova-qemu-28/keys/ssh_config',
       '-o', 'ConnectTimeout=8', '-o', 'NumberOfPasswordPrompts=1']
VM = '33c1f55b-55dc-4b4c-9f7b-ddc247e6d5d6'

SERVER = r'''
import sys,json,pathlib,asyncio,socket,threading
root=pathlib.Path(__file__).parent
sys.path[:0]=[str(root/'deps'),str(root/'source')]
config=json.loads(sys.stdin.readline())
from amqtt.broker import Broker
import paho.mqtt.client as mqtt
import uvicorn
from dom0_manager.config import Settings
from dom0_manager.drivers import build_driver
from xenova_display.config import Binding,FeedSettings,DISPLAY_DRIVE_TOPIC
from xenova_display.main import create_app
bindings=tuple(Binding.model_validate(b) for b in config['bindings'])
topic=config.get('observation_topic','xenova/v1/vms/'+config['vm_id']+'/heartbeat')
topics=[*[f'xenova/v1/apps/{b.app_id}/events' for b in bindings],
        *[t for b in bindings if b.agent_supported for t in
          (f'xenova/v1/vms/{b.vm_id}/heartbeat',f'xenova/v1/vms/{b.vm_id}/inventory')],DISPLAY_DRIVE_TOPIC]
passwords=root/'passwords'
passwords.write_text('\n'.join(k+':'+v for k,v in config['hashes'].items())+'\n')
passwords.chmod(0o600)
port_socket=socket.socket();port_socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
port_socket.bind(('192.168.77.1',config.get('broker_port',0)))
port=port_socket.getsockname()[1];port_socket.close()
app=create_app(FeedSettings(bindings,config['token'],mqtt_host='192.168.77.1',mqtt_port=port,
    mqtt_username='feed',mqtt_password=config['passwords']['feed'],binding_scope='candidate'),
    driver=build_driver(Settings(driver='xen')))
lock=threading.Lock();received=[]
observer=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='display253-observer')
observer.username_pw_set('observer',config['passwords']['observer'])
def on_connect(client,userdata,flags,reason,properties):
 if not reason.is_failure:client.subscribe(topic,qos=1)
def on_message(client,userdata,message):
 try:
  envelope=json.loads(message.payload)
  with lock:received.append({'topic':message.topic,'retained':message.retain,'envelope':envelope})
 except (ValueError,UnicodeError):pass
observer.on_connect=on_connect;observer.on_message=on_message
@app.get('/test/received')
def observations():
 with lock:return list(received)
async def main():
 broker=Broker({'listeners':{'default':{'type':'tcp','bind':f'192.168.77.1:{port}'}},
  'plugins':{'amqtt.plugins.authentication.FileAuthPlugin':{'password_file':str(passwords)},
   'amqtt.plugins.topic_checking.TopicAccessControlListPlugin':{
    'acl':{'feed':topics,'observer':[topic]},'publish_acl':{config.get('publisher_user','agent'):[topic]}}}})
 server=None
 try:
  await broker.start()
  observer.connect('192.168.77.1',port);observer.loop_start()
  sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
  server=uvicorn.Server(uvicorn.Config(app,log_level='warning',timeout_graceful_shutdown=3))
  async def shutdown():
   await asyncio.to_thread(sys.stdin.read);server.should_exit=True
  watcher=asyncio.create_task(shutdown())
  print(json.dumps({'broker_port':port,'feed_port':sock.getsockname()[1]}),flush=True)
  try:await server.serve(sockets=[sock])
  finally:watcher.cancel()
 finally:
  observer.disconnect();observer.loop_stop()
  await broker.shutdown()
  passwords.unlink(missing_ok=True)
asyncio.run(main())
'''

AGENT = r'''
import json,sys,pathlib,threading,socket
root=pathlib.Path(__file__).parent
sys.path[:0]=[str(root/'deps'),str(root/'source')]
config=json.loads(sys.stdin.readline())
from xenova_agent.config import AgentSettings
from xenova_agent.__main__ import run
settings=AgentSettings.from_env({'XENOVA_AGENT_BROKER_HOST':'192.168.77.1',
 'XENOVA_AGENT_BROKER_PORT':str(config['port']),
 'XENOVA_AGENT_BROKER_USERNAME':'agent','XENOVA_AGENT_BROKER_PASSWORD':config['password']})
assert settings.vm_id==config['vm_id']
stop=threading.Event()
def shutdown():sys.stdin.read();stop.set()
threading.Thread(target=shutdown,daemon=True).start()
print(json.dumps({'vm_id':settings.vm_id,'hostname':socket.gethostname(),
 'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'period_seconds':settings.heartbeat_interval_s}),flush=True)
raise SystemExit(run(settings,stop=stop))
'''


def command(alias, code, *, env=None, data=None, check=True):
    return subprocess.run(SSH+['-o','BatchMode='+('no' if env else 'yes'),alias,code],
                          input=data,capture_output=True,env=env,check=check,timeout=90)


def stop_remote_runner(alias,directory,env=None):
    """SSH exit alone does not prove its remote Python process exited."""
    if not re.fullmatch(r'/tmp/display253-[A-Za-z0-9]+',directory):raise ValueError('invalid trial path')
    code=r'''import os,pathlib,signal,time,json
target=TARGET
def matches(pid):
 try:return target.encode() in pathlib.Path('/proc',str(pid),'cmdline').read_bytes().split(bytes([0]))
 except OSError:return False
pids=[int(d) for d in os.listdir('/proc') if d.isdigit() and matches(d)]
for pid in pids:
 if matches(pid):
  try:os.kill(pid,signal.SIGTERM)
  except ProcessLookupError:pass
end=time.monotonic()+3
while time.monotonic()<end and any(matches(pid) for pid in pids):time.sleep(.1)
for pid in pids:
 if matches(pid):
  try:os.kill(pid,signal.SIGKILL)
  except ProcessLookupError:pass
time.sleep(.2)
print(json.dumps({'runner_absent':not any(matches(pid) for pid in pids)}))
'''.replace('TARGET',repr(directory+'/runner.py'))
    result=json.loads(command(alias,'python3 -B -',env=env,data=code.encode()).stdout)
    if not result['runner_absent']:raise RuntimeError('temporary remote runner remains')
    return result


def archive(wheels, sources, runner, only_paho=False):
    with tempfile.TemporaryDirectory(prefix='display253-stage-') as name:
        stage=Path(name)
        for wheel in wheels.glob('*.whl'):
            if only_paho and not wheel.name.startswith('paho_mqtt-'):continue
            with zipfile.ZipFile(wheel) as package:package.extractall(stage/'deps')
        for source in sources:
            shutil.copytree(source,stage/'source'/source.name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (stage/'runner.py').write_text(runner)
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:gz') as tar:
            for child in stage.iterdir():tar.add(child,arcname=child.name)
        return out.getvalue()


async def until(fn, timeout=60):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=await fn()
            if value:return value
        except (httpx.HTTPError,OSError):pass
        await asyncio.sleep(.5)
    raise TimeoutError('actual guest acceptance condition not reached')


async def run(args):
    if command('xenova-qemu-dom0','hostname').stdout.strip()!=b'xenova-qemu-dom0':
        raise RuntimeError('isolated QEMU required')
    vm_rows=json.loads(command('xenova-qemu-dom0','xl list -l').stdout)
    before={r['config']['c_info'].get('uuid'):r['domid'] for r in vm_rows if r['domid']!=0}
    bindings=json.loads((Path(__file__).parents[1]/'examples/qemu-bindings.json').read_text())
    assert set(before)=={b['vm_id'] for b in bindings}
    # UUIDs are real, but app/primary-backup firmware mapping is still only a candidate.
    bindings[0]['app_id']='autonomous';bindings[1]['app_id']='manual'
    evidence={'scope':'real Debian existing Agent -> real dom0 temporary authenticated broker/feed over xenbr0; no synthetic reports or firmware changes',
              'cases':[],'source_commit':subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'wheel_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.wheels.glob('*.whl')}}
    remote_dirs=[];processes=[];logs=[];guest=None;server=None
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='display253-control-') as scratch:
        scratch=Path(scratch)
        askpass=scratch/'askpass'
        askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$DISPLAY253_GUEST_PASSWORD"\n');askpass.chmod(0o700)
        password=re.search(r'root / (\d+)',(ROOT/'docs/build-guides/perseus-persistent-platform.md').read_text()).group(1)
        env={**os.environ,'SSH_ASKPASS':str(askpass),'SSH_ASKPASS_REQUIRE':'force',
             'DISPLAY':':253','DISPLAY253_GUEST_PASSWORD':password}
        secrets_by_user={name:secrets.token_urlsafe(32) for name in ('agent','feed','observer')}
        token=secrets.token_urlsafe(32)
        try:
            # Confirm Python ABI before copying architecture-specific dependencies.
            for alias,auth in [('xenova-qemu-dom0',None),('xenova-qemu-debian',env)]:
                version=command(alias,"python3 -c 'import sys,platform;print(sys.version_info[:2],platform.machine())'",env=auth).stdout.decode().strip()
                assert version=="(3, 13) aarch64",version
            sources=[ROOT/'platform/services/dom0-manager/dom0_manager',
                     ROOT/'platform/services/vm-agent/mqtt-client/xenova_mqtt',
                     Path(__file__).parents[1]/'xenova_display']
            for alias,auth,payload in [
                ('xenova-qemu-dom0',None,archive(args.wheels,sources,SERVER)),
                ('xenova-qemu-debian',env,archive(args.wheels,[ROOT/'platform/services/vm-agent/agent/xenova_agent',sources[1]],AGENT,True))]:
                directory=command(alias,'mktemp -d /tmp/display253-XXXXXXXX',env=auth).stdout.decode().strip()
                assert re.fullmatch(r'/tmp/display253-[A-Za-z0-9]+',directory)
                remote_dirs.append((alias,auth,directory))
                command(alias,'tar -xz -C '+directory,env=auth,data=payload)
            server_log=(scratch/'server.log').open('w+');logs.append(server_log)
            server=subprocess.Popen(SSH+['-o','BatchMode=yes','xenova-qemu-dom0','python3 -u -B '+remote_dirs[0][2]+'/runner.py'],
                                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=server_log,text=True)
            processes.append(server)
            server.stdin.write(json.dumps({'bindings':bindings,'token':token,'vm_id':VM,
                'passwords':secrets_by_user,'hashes':{k:sha512_crypt.hash(v,rounds=5000) for k,v in secrets_by_user.items()}})+'\n');server.stdin.flush()
            line=await asyncio.wait_for(asyncio.to_thread(server.stdout.readline),60)
            if not line:
                server_log.seek(0);raise RuntimeError('dom0 runner failed: '+server_log.read()[-2000:])
            ready=json.loads(line)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));local_port=sock.getsockname()[1]
            tunnel=subprocess.Popen(SSH+['-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-N','-L',
                f'127.0.0.1:{local_port}:127.0.0.1:{ready["feed_port"]}','xenova-qemu-dom0'],
                stdout=subprocess.DEVNULL,stderr=server_log)
            processes.append(tunnel)
            headers={'Authorization':'Bearer '+token}
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{local_port}',headers=headers,timeout=12) as client:
                async def state():
                    response=await client.get('/api/v1/display/state');response.raise_for_status();return response.json()
                async def subscribed():
                    s=await state()
                    return s if s['mqtt_subscriptions'] and all(v=='granted' for v in s['mqtt_subscriptions'].values()) else None
                await until(subscribed)
                evidence['cases'].append('real_dom0_xen_uuid_and_topic_subscriptions_confirmed')
                guest_log=(scratch/'agent.log').open('w+');logs.append(guest_log)
                guest=subprocess.Popen(SSH+['-o','BatchMode=no','xenova-qemu-debian','python3 -u -B '+remote_dirs[1][2]+'/runner.py'],
                                       env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=guest_log,text=True)
                processes.append(guest)
                guest.stdin.write(json.dumps({'port':ready['broker_port'],'password':secrets_by_user['agent'],'vm_id':VM})+'\n');guest.stdin.flush()
                line=await asyncio.wait_for(asyncio.to_thread(guest.stdout.readline),45)
                if not line:raise RuntimeError('existing guest Agent runner did not start')
                identity=json.loads(line);assert identity['vm_id']==VM and identity['period_seconds']==5
                evidence['guest_identity']=identity
                async def reports():
                    response=await client.get('/test/received');response.raise_for_status();rows=response.json()
                    return rows if len(rows)>=3 else None
                rows=await until(reports,50)
                envelopes=[r['envelope'] for r in rows]
                assert all(not r['retained'] and r['topic']==f'xenova/v1/vms/{VM}/heartbeat' for r in rows)
                assert all(e['vm_id']==VM and e['type']=='agent.heartbeat' and e['boot_id']==identity['boot_id'] for e in envelopes)
                assert len({e['agent_instance_id'] for e in envelopes})==1
                assert all(b['seq']>a['seq'] for a,b in zip(envelopes,envelopes[1:]))
                evidence['reports']=rows
                evidence['cases'].append('three_existing_agent_reports_arrive_over_real_guest_network_with_increasing_sequence')
                s=await state();vm=next(v for v in s['vms'] if v['vm_id']==VM)
                assert vm['sources']['agent']['status']=='fresh'
                assert vm['sources']['agent']['sequence']==envelopes[-1]['seq']
                assert vm['os']['observed'] is None and vm['app_state']=='unknown' and not vm['os']['current_run_verified']
                evidence['agent_source']=vm['sources']['agent']
                evidence['cases'].append('dom0_rest_marks_only_agent_liveness_fresh_without_invented_os_or_app_readiness')
                async with connect(f'ws://127.0.0.1:{local_port}/ws/v1/display/state',additional_headers=headers) as ws:
                    message=json.loads(await asyncio.wait_for(ws.recv(),15))
                    ws_vm=next(v for v in message['vms'] if v['vm_id']==VM)
                    assert ws_vm['sources']['agent']['source_epoch']==vm['sources']['agent']['source_epoch']
                    assert ws_vm['sources']['agent']['status']=='fresh'
                evidence['cases'].append('authenticated_dom0_websocket_has_same_real_guest_source_epoch')
                p=(await client.get('/api/v1/display/presentation')).json()
                assert p['decisions'][VM]['kind']=='app_waiting'
                evidence['presentation_kind']=p['decisions'][VM]['kind']
                evidence['cases'].append('hdmi_presentation_keeps_app_unconfirmed_despite_live_agent')
                guest.stdin.close();await asyncio.to_thread(guest.wait,20)
                assert guest.returncode==0
                evidence['cases'].append('temporary_existing_agent_exits_cleanly_without_service_changes')
            evidence['passed']=True
        finally:
            if guest and guest.poll() is None:
                guest.stdin.close()
                try:await asyncio.to_thread(guest.wait,20)
                except subprocess.TimeoutExpired:guest.terminate();await asyncio.to_thread(guest.wait,10)
            if server and server.poll() is None:
                server.stdin.close()
                try:await asyncio.to_thread(server.wait,25)
                except subprocess.TimeoutExpired:server.terminate();await asyncio.to_thread(server.wait,10)
            for proc in reversed(processes):
                if proc.poll() is None:proc.terminate();await asyncio.to_thread(proc.wait,10)
            cleanup=[]
            for alias,auth,directory in reversed(remote_dirs):
                stop_remote_runner(alias,directory,env=auth)
                # Each exact mktemp path was validated above; no system directory is removed.
                result=command(alias,'rm -rf -- '+shlex.quote(directory),env=auth,check=False)
                cleanup.append({'host':alias,'temporary_directory_removed':result.returncode==0})
            for log in logs:log.close()
            evidence['cleanup']=cleanup
            evidence['process_returncodes']={
                'agent':guest.returncode if guest else None,
                'dom0_runner':server.returncode if server else None}
            after_rows=json.loads(command('xenova-qemu-dom0','xl list -l').stdout)
            after={r['config']['c_info'].get('uuid'):r['domid'] for r in after_rows if r['domid']!=0}
            evidence['domain_ids_unchanged']=before==after
            evidence['completed_at']=datetime.now(timezone.utc).isoformat()
            args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':evidence.get('passed',False),'cases':evidence['cases'],
                          'cleanup':evidence['cleanup'],'domain_ids_unchanged':evidence['domain_ids_unchanged']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--wheels',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute:parser.error('--execute required; temporary deployment must be authorized')
    asyncio.run(run(args))
