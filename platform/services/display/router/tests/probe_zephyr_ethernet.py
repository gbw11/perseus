"""Opt-in VM2-only QEMU Ethernet/MQTT firmware trial with original restoration.

Requires explicit permission to stop/restart zephyr-backup and boot a trial image.
Uses the real manual heartbeat sender, no command/input publisher or console reader.
Original registry/firmware remain intact; a saved original copy remains in /var/tmp.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import tempfile

import httpx
from passlib.hash import sha512_crypt
from websockets.asyncio.client import connect

from probe_debian_agent import ROOT,SSH,SERVER,archive,command,until,stop_remote_runner

VM='820a0522-243f-42f1-90e9-9b74e1522965'
DOM0='xenova-qemu-dom0'
TOPIC='xenova/v1/apps/manual/events'

SNAPSHOT=r'''
import sys,pathlib,json,shutil,re,hashlib,subprocess
sys.path.insert(0,'/usr/lib/xenova')
import xenova_vm
p=xenova_vm.Platform();r=p.records()['zephyr-backup']
assert r['uuid']=='820a0522-243f-42f1-90e9-9b74e1522965'
base=pathlib.Path(sys.argv[1]);assert re.fullmatch('/var/tmp/display253-vm2-[A-Za-z0-9]+',str(base))
instance=p.data/'instances'/r['id']
cfg=p.runtime/(r['id']+'.cfg');text=cfg.read_text()
kernel=re.search(r"^kernel\s*=\s*['\"]([^'\"]+)['\"]",text,re.M).group(1)
shutil.copytree(pathlib.Path(kernel).parent,base/'original-firmware')
shutil.copy2(instance/'instance.json',base/'original-instance.json')
(base/'original.cfg').write_text(text)
trial=re.sub(r"^kernel\s*=.*$","kernel = '"+str(base/'trial.bin')+"'",text,flags=re.M)
assert not re.search(r'^vif\s*=',trial,re.M)
trial+="vif = [ 'mac=02:16:3e:77:00:32,bridge=xenbr0,script=vif-bridge' ]\n"
(base/'trial.cfg').write_text(trial)
print(json.dumps({'backup_path':str(base),'original_kernel':kernel,
'original_sha256':hashlib.sha256(pathlib.Path(kernel).read_bytes()).hexdigest(),
'record_sha256':hashlib.sha256((instance/'instance.json').read_bytes()).hexdigest(),
'cfg_sha256':hashlib.sha256(text.encode()).hexdigest()}))
'''


def domains():
    rows=json.loads(command(DOM0,'xl list -l').stdout)
    return {r['config']['c_info'].get('uuid'):r for r in rows if r['domid']!=0}


def control_trial(action):
    row=domains().get(VM)
    if row is None:return
    assert row['config']['c_info']['name']=='zephyr-backup'
    # xl accepts name/domid here, not a UUID string. Resolve UUID immediately first.
    command(DOM0,'xl '+action+' '+str(row['domid']))


def managed(action,key):
    spec={'vm_id':VM}
    if action=='stop':spec['force']=True
    code="import sys,json;sys.path.insert(0,'/usr/lib/xenova');import xenova_vm;from xenova_management import Manager;result=Manager(xenova_vm.Platform()).call("+repr(action)+','+repr(spec)+',execute=True,idempotency_key='+repr(key)+");print(json.dumps(result));raise SystemExit(0 if result['ok'] else 1)"
    result=command(DOM0,'python3 -B -',data=code.encode(),check=False)
    if result.returncode:
        raise RuntimeError('VM2 Manager '+action+' failed: '+result.stdout.decode()[-2500:]+' '+result.stderr.decode()[-1000:])
    return json.loads(result.stdout)


async def run(args):
    before=domains();assert VM in before
    assert before[VM]['config']['c_info']['name']=='zephyr-backup'
    assert len(before)==3
    secrets_file=args.build/'credential.json'
    manual_password=json.loads(secrets_file.read_text())['manual']
    source=json.loads((args.build/'app/source-receipt.json').read_text())
    firmware=args.build/'build/zephyr/zephyr.bin'
    assert source['broker_port']==51883 and firmware.is_file()
    args.build.chmod(0o700)
    evidence={'scope':'real QEMU VM2 manual heartbeat-only trial over Ethernet; no driving input/command or graphics',
              'cases':[],'build_source':source,'firmware_sha256':hashlib.sha256(firmware.read_bytes()).hexdigest(),
              'zephyr_boot_id_is_os_verified':False,'test_password_hash_rounds':5000,'restored':False}
    priority_keys=('CONFIG_MAIN_THREAD_PRIORITY','CONFIG_XENOVA_NETFRONT_THREAD_PRIORITY','CONFIG_XENOVA_SHUTDOWN_THREAD_PRIORITY')
    evidence['compiled_thread_priorities']={line.split('=',1)[0]:int(line.split('=',1)[1])
        for line in (args.build/'build/zephyr/.config').read_text().splitlines()
        if any(line.startswith(key+'=') for key in priority_keys)}
    resilience=getattr(args,'resilience',False)
    trial_dir=None;server_dir=None;server=None;tunnel=None;changed=False
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='display253-zephyr-control-') as scratch:
        log=(Path(scratch)/'server.log').open('w+')
        try:
            trial_dir=command(DOM0,'mktemp -d /var/tmp/display253-vm2-XXXXXXXX').stdout.decode().strip()
            assert re.fullmatch(r'/var/tmp/display253-vm2-[A-Za-z0-9]+',trial_dir)
            snapshot=json.loads(command(DOM0,'python3 -B - '+trial_dir,data=SNAPSHOT.encode()).stdout)
            evidence['original']=snapshot
            command(DOM0,'cat > '+trial_dir+'/trial.bin',data=firmware.read_bytes())
            server_dir=command(DOM0,'mktemp -d /tmp/display253-XXXXXXXX').stdout.decode().strip()
            assert re.fullmatch(r'/tmp/display253-[A-Za-z0-9]+',server_dir)
            sources=[ROOT/'platform/services/dom0-manager/dom0_manager',
                ROOT/'platform/services/vm-agent/mqtt-client/xenova_mqtt',Path(__file__).parents[1]/'xenova_display']
            if resilience:sources.append(Path(__file__).with_name('guest_resilience.py'))
            payload=archive(args.wheels,sources,SERVER)
            command(DOM0,'tar -xz -C '+server_dir,data=payload)
            passwords={'manual':manual_password,'feed':secrets.token_urlsafe(32),'observer':secrets.token_urlsafe(32)}
            if resilience:passwords['replay']=secrets.token_urlsafe(32)
            token=secrets.token_urlsafe(32)
            bindings=json.loads((Path(__file__).parents[1]/'examples/qemu-bindings.json').read_text())
            bindings[0]['app_id']='autonomous';bindings[1]['app_id']='manual'
            server=subprocess.Popen(SSH+['-o','BatchMode=yes',DOM0,'python3 -u -B '+server_dir+'/runner.py'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
            server.stdin.write(json.dumps({'vm_id':VM,'bindings':bindings,'token':token,'broker_port':51883,
                'observation_topic':TOPIC,'publisher_user':'manual','passwords':passwords,'resilience':resilience,
                # Lab-only high-entropy temporary credentials. Default passlib rounds take
                # ~7s under TCG, exceeding the existing sender's 5s CONNACK deadline.
                # This changes no production broker/account policy or sender timeout.
                'hashes':{k:sha512_crypt.hash(v,rounds=5000) for k,v in passwords.items()}})+'\n');server.stdin.flush()
            line=await asyncio.wait_for(asyncio.to_thread(server.stdout.readline),60)
            if not line:
                log.seek(0);raise RuntimeError('dom0 runner failed: '+log.read()[-2000:])
            ready=json.loads(line)
            import socket
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            tunnel=subprocess.Popen(SSH+['-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-N','-L',
                f'127.0.0.1:{port}:127.0.0.1:{ready["feed_port"]}',DOM0],stdout=subprocess.DEVNULL,stderr=log)
            headers={'Authorization':'Bearer '+token}
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',headers=headers,timeout=15) as client:
                async def state():
                    response=await client.get('/api/v1/display/state');response.raise_for_status();return response.json()
                async def subscribed():
                    s=await state();return s if s['mqtt_subscriptions'].get(TOPIC)=='granted' else None
                await until(subscribed)
                evidence['cases'].append('original_vm2_config_and_firmware_saved_before_trial')
                # The registered original has no cooperative handler. Explicit VM2-only force stop was authorized.
                changed=True
                try:
                    managed('stop','display253-'+Path(trial_dir).name+'-stop')
                except RuntimeError:
                    # This old lab Manager may reject its removal observation after destroy.
                    # Proceed only with two fresh full Xen snapshots proving target absence
                    # and unchanged identities of every other guest. Never destroy a replacement.
                    snapshots=[domains(),domains()]
                    if not all(VM not in sample and all(sample.get(v,{}).get('domid')==r['domid']
                        for v,r in before.items() if v!=VM) for sample in snapshots):raise
                    evidence['control_observation_warning']='legacy Manager stop returned an error; two fresh Xen snapshots confirmed VM2 absent and all other domain IDs unchanged'
                command(DOM0,'xl create '+trial_dir+'/trial.cfg')
                trial=domains()[VM]
                assert trial['config'].get('nics')
                evidence['trial_domid']=trial['domid']
                assert all(domains()[v]['domid']==r['domid'] for v,r in before.items() if v!=VM)
                evidence['cases'].append('only_vm2_restarted_with_trial_firmware_and_one_vif')
                async def reports():
                    response=await client.get('/test/received');response.raise_for_status();rows=response.json()
                    return rows if len(rows)>=3 else None
                rows=await until(reports,90)
                envelopes=[r['envelope'] for r in rows]
                assert all(r['topic']==TOPIC and not r['retained'] for r in rows)
                assert all(e['vm_id']==VM and e['app_id']=='manual' and e['type']=='app.heartbeat' for e in envelopes)
                assert all(b['seq']>a['seq'] for a,b in zip(envelopes,envelopes[1:]))
                assert len({(e['boot_id'],e['agent_instance_id']) for e in envelopes})==1
                evidence['reports']=rows
                evidence['cases'].append('real_zephyr_manual_heartbeats_reach_dom0_with_uuid_and_increasing_sequence')
                evidence['cases'].append('no_drive_commands_or_input_subscriptions_in_trial_build')
                s=await state();vm=next(v for v in s['vms'] if v['vm_id']==VM)
                assert vm['xen_state']=='running' and vm['app_state']=='ready' and vm['sources']['app']['status']=='fresh'
                assert vm['os']['observed'] is None and not vm['os']['current_run_verified']
                assert s['drive']['active_vm_id'] is None and s['drive']['mode']=='unknown'
                evidence['app_source']=vm['sources']['app']
                evidence['cases'].append('rest_app_liveness_fresh_without_invented_os_driving_mode_or_active_vm')
                async with connect(f'ws://127.0.0.1:{port}/ws/v1/display/state',additional_headers=headers) as ws:
                    s=json.loads(await asyncio.wait_for(ws.recv(),15))
                    row=next(v for v in s['vms'] if v['vm_id']==VM)
                    assert row['sources']['app']['source_epoch']==vm['sources']['app']['source_epoch']
                    assert row['sources']['app']['status']=='fresh'
                evidence['cases'].append('authenticated_dom0_websocket_has_same_real_vm2_app_source')
                p=(await client.get('/api/v1/display/presentation')).json()
                assert p['decisions'][VM]['kind']=='status'
                evidence['presentation_kind']=p['decisions'][VM]['kind']
                evidence['cases'].append('hdmi_presentation_uses_vm2_status_panel_without_graphics_claim')
                if resilience:
                    from guest_resilience import run_checks
                    evidence['resilience']=await run_checks(client,VM,TOPIC,trial_dir+'/trial.cfg',control_trial,domains,evidence)
                evidence['verification_completed']=True
                evidence['passed']=not resilience or evidence['resilience']['passed']
        except BaseException as error:
            evidence['failure_type']=type(error).__name__
            if changed:
                evidence['guest_diagnostics']=command(DOM0,"xenstore-ls /local/domain/"+str(domains().get(VM,{}).get('domid',-1))+"/data/display253",check=False).stdout.decode()[:3000]
            raise
        finally:
            try:
                if changed:
                    if VM in domains():
                        try:control_trial('shutdown')
                        except subprocess.CalledProcessError:
                            # Explicitly authorized lab-only VM2 cleanup, not product recovery escalation.
                            if VM in domains():control_trial('destroy')
                        async def stopped():return VM not in domains()
                        try:await until(stopped,25)
                        except TimeoutError:control_trial('destroy')
                    managed('start','display253-'+Path(trial_dir).name+'-restore')
                    after=domains()
                    assert VM in after
                    evidence['other_domain_ids_unchanged']=all(after[v]['domid']==r['domid'] for v,r in before.items() if v!=VM)
                    code="import pathlib,hashlib,json; print(json.dumps({'record_sha256':hashlib.sha256(pathlib.Path('/srv/xenova/instances/zephyr-backup/instance.json').read_bytes()).hexdigest(),'kernel_sha256':hashlib.sha256(pathlib.Path("+repr(evidence['original']['original_kernel'])+").read_bytes()).hexdigest(),'cfg_sha256':hashlib.sha256(pathlib.Path('/run/xenova/zephyr-backup.cfg').read_bytes()).hexdigest()}))"
                    restored=json.loads(command(DOM0,'python3 -B -',data=code.encode()).stdout)
                    evidence['restored']=restored['record_sha256']==snapshot['record_sha256'] and restored['kernel_sha256']==snapshot['original_sha256'] and restored['cfg_sha256']==snapshot['cfg_sha256'] and not after[VM]['config'].get('nics')
                    command(DOM0,'rm -f -- '+trial_dir+'/trial.bin '+trial_dir+'/trial.cfg')
            finally:
                if server and server.poll() is None:
                    server.stdin.close()
                    try:await asyncio.to_thread(server.wait,25)
                    except subprocess.TimeoutExpired:server.terminate();await asyncio.to_thread(server.wait,10)
                if tunnel and tunnel.poll() is None:tunnel.terminate();await asyncio.to_thread(tunnel.wait,10)
                if server_dir:
                    stop_remote_runner(DOM0,server_dir)
                    command(DOM0,'rm -rf -- '+shlex.quote(server_dir))
                evidence['temporary_broker_stopped']=server is None or server.poll() is not None
                evidence['completed_at']=datetime.now(timezone.utc).isoformat()
                args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
                log.close()
        assert evidence['restored'] and evidence['other_domain_ids_unchanged']
        print(json.dumps({k:evidence[k] for k in ('passed','cases','restored','other_domain_ids_unchanged','temporary_broker_stopped')}))
        if resilience:print(json.dumps({k:evidence['resilience'].get(k) for k in
            ('passed','display_safety_checks_passed','live_vif_recovery_passed','cases','limitations')}))
        return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resilience',action='store_true',help='authorized VM2 VIF loss, pause, restart and test-only replay controls')
    parser.add_argument('--build',type=Path,required=True)
    parser.add_argument('--wheels',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute:parser.error('--execute and prior VM2-only modification authorization required')
    result=asyncio.run(run(args))
    if not result['passed']:raise SystemExit(1)
