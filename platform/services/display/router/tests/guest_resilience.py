"""Stage-4 lab fault controls and host checks. Never included in the product app.

Only exact VM2 heartbeat replay is allowed. No driving command, console or broad
network/service stop. VM2's temporary VIF and firmware must be authorized first.
"""
import asyncio
from copy import deepcopy
import json
import subprocess
import time
from uuid import uuid4


class FaultDriver:
    def __init__(self,driver):self.driver=driver;self.fail=False
    def list_domains(self):
        if self.fail:raise RuntimeError('injected test-only Xen observation failure')
        return self.driver.list_domains()


class MqttControl:
    def __init__(self):
        from threading import Lock
        self.events=[];self.lock=Lock()
    def factory(self,store):
        from xenova_display.mqtt import MqttSource
        self.source=MqttSource(store)
        original=self.source.client.on_message
        def tracked(client,userdata,message):
            original(client,userdata,message)
            try:envelope=json.loads(message.payload)
            except (ValueError,UnicodeError):return
            if not isinstance(envelope,dict):return
            with self.lock:
                self.events.append({'message_id':envelope.get('message_id'),'seq':envelope.get('seq'),
                    'retained':bool(message.retain),'mqtt_generation':store.mqtt_generation})
                self.events[:]=self.events[-256:]
        self.source.client.on_message=tracked
        return self.source


def attach_controls(app,driver,control,config,port,topic):
    import threading
    import paho.mqtt.client as mqtt
    from fastapi import HTTPException,Request

    def publish(envelope,retained,clear):
        connected=threading.Event()
        client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='display253-replay-'+uuid4().hex[:12])
        client.username_pw_set('replay',config['passwords']['replay'])
        def ready(c,u,f,r,p):
            if not r.is_failure:connected.set()
        client.on_connect=ready
        try:
            client.connect('192.168.77.1',port);client.loop_start()
            if not connected.wait(8):raise RuntimeError('test replay authentication failed')
            info=client.publish(topic,b'' if clear else json.dumps(envelope),qos=1,retain=retained)
            info.wait_for_publish(8)
            if not info.is_published():raise RuntimeError('test replay not acknowledged')
        finally:client.disconnect();client.loop_stop()

    @app.post('/test/replay')
    async def replay(request:Request):
        body=await request.json();envelope=body.get('envelope');clear=body.get('clear') is True
        if not clear and (not isinstance(envelope,dict) or envelope.get('vm_id')!=config['vm_id']
                          or envelope.get('app_id')!='manual' or envelope.get('type')!='app.heartbeat'):
            raise HTTPException(400,'only VM2 manual heartbeat replay is permitted')
        retained=clear or body.get('retained') is True
        await asyncio.to_thread(publish,envelope,retained,clear)
        return {'published':True,'retained':retained}

    @app.post('/test/mqtt/{action}')
    async def mqtt_control(action:str):
        if action=='stop':await asyncio.to_thread(control.source.stop)
        elif action=='start':await asyncio.to_thread(control.source.start)
        else:raise HTTPException(400,'unsupported fault action')
        return {'mqtt_connected':control.source.store.mqtt_connected}

    @app.get('/test/mqtt/observations')
    async def mqtt_observations():
        with control.lock:return list(control.events)

    @app.post('/test/xen/{action}')
    async def xen_control(action:str):
        if action not in ('fail','recover'):raise HTTPException(400,'unsupported fault action')
        driver.fail=action=='fail'
        return {'injected_observation_failure':driver.fail}


async def run_checks(client,vm,topic,trial_cfg,control_trial,domains,evidence=None):
    from probe_debian_agent import command,until
    dom0='xenova-qemu-dom0'
    cases=[];observations=[];link_down=False;paused=False;xen_failed=False;feed_stopped=False
    result={'scope':'actual QEMU VM2 VIF loss, feed MQTT reconnect, pause/restart; wire replay derived from actual packets; Xen-read failure is injected',
            'cases':cases,'observations':observations,'limitations':[],'passed':False}
    if evidence is not None:evidence['resilience']=result

    async def post(path,body=None):
        response=await client.post(path,json=body or {});response.raise_for_status();return response.json()

    async def current():
        response=await client.get('/api/v1/display/presentation');response.raise_for_status()
        p=response.json();row=next(v for v in p['state']['vms'] if v['vm_id']==vm)
        return row,p

    async def wait_for(predicate,timeout=60):
        async def check():
            row,p=await current()
            return (row,p) if predicate(row,p) else None
        return await until(check,timeout)

    def vif(action):
        row=domains()[vm];domid=row['domid']
        assert row['config']['c_info']['name']=='zephyr-backup' and len(row['config'].get('nics',[]))==1
        backend=command(dom0,f'xenstore-read /local/domain/{domid}/device/vif/0/backend').stdout.decode().strip()
        assert backend==f'/local/domain/0/backend/vif/{domid}/0'
        command(dom0,f'ip link set dev vif{domid}.0 {action}')

    def console_clients():
        code=r'''import pathlib,os,json
rows=[]
for pid in os.listdir('/proc'):
 if not pid.isdigit():continue
 try:args=pathlib.Path('/proc',pid,'cmdline').read_bytes().split(bytes([0]))
 except OSError:continue
 if args and pathlib.Path(args[0].decode(errors='replace')).name=='xl' and b'console' in args:
  rows.append(int(pid))
print(json.dumps(sorted(rows)))
'''
        return json.loads(command(dom0,'python3 -B -',data=code.encode()).stdout)

    def record(name,row,p):
        observations.append({'case':name,'xen_state':row['xen_state'],'app_state':row['app_state'],
            'app_source':row['sources']['app'],'run_id':row['run_id'],
            'decision':p['decisions'][vm]['kind']})
        print(json.dumps({'stage4':name,'xen':row['xen_state'],'app':row['app_state'],
                          'source':row['sources']['app']['status'],'decision':p['decisions'][vm]['kind']}),flush=True)

    async def replay(envelope,*,retained=False):
        replayed=deepcopy(envelope);replayed['message_id']='test-replay-'+uuid4().hex
        await post('/test/replay',{'envelope':replayed,'retained':retained})
        async def seen():
            response=await client.get('/test/received');response.raise_for_status()
            return any(r['envelope'].get('message_id')==replayed['message_id'] for r in response.json())
        await until(seen,15)
        return replayed

    baseline_consoles=console_clients()
    try:
        # Stop only this temporary VIF; Xen continues to observe VM2 as running.
        vif('down');link_down=True
        row,p=await wait_for(lambda r,p:r['xen_state']=='running' and r['sources']['app']['status']=='stale')
        assert row['app_state']=='unknown' and p['decisions'][vm]['kind']=='app_waiting'
        record('actual_vm2_network_loss',row,p)
        cases.append('vif_loss_expires_app_after_three_seconds_without_claiming_vm_failure')
        original_source=deepcopy(row['sources']['app'])
        rows=(await client.get('/test/received')).json()
        envelope=next(r['envelope'] for r in reversed(rows)
            if r['envelope'].get('seq')==original_source['sequence']
            and [r['envelope'].get('boot_id'),r['envelope'].get('agent_instance_id')]==original_source['source_epoch'])
        for label,seq,retained in [('duplicate',envelope['seq'],False),
                                    ('out_of_order',max(1,envelope['seq']-1),False)]:
            copy=deepcopy(envelope);copy['seq']=seq
            await replay(copy,retained=retained)
            row,p=await current()
            assert row['app_state']=='unknown' and row['sources']['app']['status']=='stale'
            for key in ('received_at','sequence','source_epoch'):
                assert row['sources']['app'][key]==original_source[key]
            record(label,row,p)
            cases.append(label+'_wire_replay_does_not_refresh_or_replace_accepted_observation')

        await post('/test/mqtt/stop');feed_stopped=True
        row,p=await wait_for(lambda r,p:r['sources']['app']['subscription_status']=='unavailable')
        assert row['xen_state']=='running' and row['app_state']=='unknown'
        assert p['decisions'][vm]['kind']=='app_source_unavailable'
        record('feed_mqtt_disconnected',row,p)
        cases.append('real_feed_disconnect_keeps_vm_running_and_marks_receive_path_unavailable')
        # MQTT 3.1.1 clears RETAIN on live forwarding. Seed the cache while the
        # feed is disconnected; assert the retained flag on its new subscription.
        retained=deepcopy(envelope);retained['seq']+=100
        cached=await replay(retained,retained=True)
        await post('/test/mqtt/start');feed_stopped=False
        row,p=await wait_for(lambda r,p:r['sources']['app']['subscription_status']=='granted')
        async def retained_delivered():
            response=await client.get('/test/mqtt/observations');response.raise_for_status()
            return any(e['message_id']==cached['message_id'] and e['retained'] for e in response.json())
        await until(retained_delivered,15)
        row,p=await current()
        assert row['sources']['app']['status']=='disconnected' and row['app_state']=='unknown'
        assert row['sources']['app']['sequence']==original_source['sequence']
        record('suback_and_retained_without_new_guest_report',row,p)
        cases.append('resuback_and_retained_delivery_cannot_revive_pre_disconnect_cache')
        await post('/test/replay',{'clear':True})
        vif('up');link_down=False
        recovery_started=time.monotonic()
        try:
            row,p=await wait_for(lambda r,p:r['app_state']=='ready' and r['sources']['app']['status']=='fresh',90)
        except TimeoutError:
            result['vif_up_to_timeout_seconds']=round(time.monotonic()-recovery_started,3)
            row,p=await current()
            assert row['app_state']=='unknown'
            record('vif_up_without_fresh_report_after_90_seconds',row,p)
            result['limitations'].append('Guest heartbeat did not recover within 90 seconds after VIF down/up; lower-layer/sender live recovery is not accepted. Explicit VM2 trial restart was needed.')
            try:control_trial('shutdown')
            except subprocess.CalledProcessError:control_trial('destroy')
            async def gone():return vm not in domains()
            try:await until(gone,25)
            except TimeoutError:control_trial('destroy');await until(gone,15)
            await wait_for(lambda r,p:r['xen_state']=='stopped')
            command(dom0,'xl create '+trial_cfg)
            row,p=await wait_for(lambda r,p:r['app_state']=='ready' and r['sources']['app']['status']=='fresh',90)
            record('explicit_trial_restart_after_network_loss',row,p)
            cases.append('no_false_ready_after_unrecovered_vif_and_explicit_trial_restart_restores_real_reports')
        else:
            result['vif_up_to_fresh_report_seconds']=round(time.monotonic()-recovery_started,3)
            assert row['sources']['app']['sequence']>original_source['sequence']
            record('actual_network_report_recovery',row,p)
            cases.append('restored_vif_and_fresh_real_guest_report_restore_app_status')

        control_trial('pause');paused=True
        row,p=await wait_for(lambda r,p:r['xen_state']=='paused')
        assert row['app_state']=='unknown' and p['decisions'][vm]['kind']=='paused'
        record('actual_xen_pause',row,p)
        control_trial('unpause');paused=False
        await wait_for(lambda r,p:r['xen_state']=='running' and r['app_state']=='ready')
        cases.append('actual_xen_pause_overrides_app_report_and_unpause_recovers')

        await post('/test/xen/fail');xen_failed=True
        row,p=await wait_for(lambda r,p:r['sources']['xen']['status']=='disconnected')
        assert row['app_state']=='unknown' and p['decisions'][vm]['kind']=='stale'
        record('injected_xen_read_failure',row,p)
        await post('/test/xen/recover');xen_failed=False
        await wait_for(lambda r,p:r['sources']['xen']['status']=='fresh' and r['app_state']=='ready')
        cases.append('test_driver_read_failure_removes_normal_badge_and_real_xen_read_recovery_restores_it')

        # Reboot the same trial image, never VM1 or Debian. Capture actual old/new publisher IDs.
        row,p=await current();old_epoch=row['sources']['app']['source_epoch'];old_run=row['run_id']
        raw=(await client.get('/test/received')).json()
        old=next(r['envelope'] for r in reversed(raw)
            if [r['envelope'].get('boot_id'),r['envelope'].get('agent_instance_id')]==old_epoch
            and not r['envelope'].get('message_id','').startswith('test-replay-'))
        try:control_trial('shutdown')
        except subprocess.CalledProcessError:control_trial('destroy')
        async def stopped():return vm not in domains()
        try:await until(stopped,25)
        except TimeoutError:control_trial('destroy');await until(stopped,15)
        row,p=await wait_for(lambda r,p:r['xen_state']=='stopped')
        assert row['app_state']=='unknown' and p['decisions'][vm]['kind']=='stopped'
        record('actual_trial_stopped',row,p)
        await replay(old)
        row,p=await current();assert row['sources']['app']['status']=='unknown'
        command(dom0,'xl create '+trial_cfg)
        row,p=await wait_for(lambda r,p:r['app_state']=='ready' and r['sources']['app']['source_epoch']!=old_epoch,90)
        assert row['run_id']!=old_run
        new_source=deepcopy(row['sources']['app']);record('actual_trial_restarted',row,p)
        await replay(old)
        row,p=await current()
        assert row['sources']['app']['source_epoch']==new_source['source_epoch']
        assert row['sources']['app']['sequence']>=new_source['sequence']
        record('retired_publisher_wire_replay',row,p)
        cases.append('actual_vm2_restart_changes_execution_and_publisher_epoch_and_rejects_old_boot_replay')
        assert console_clients()==baseline_consoles
        cases.append('no_new_xl_console_client_observed_during_fault_or_restart_checks')
        result.update(console_clients_before_after=baseline_consoles,old_epoch=old_epoch,
                      new_epoch=new_source['source_epoch'],display_safety_checks_passed=True,
                      live_vif_recovery_passed=not result['limitations'],
                      verification_completed=True,passed=not result['limitations'])
        return result
    finally:
        if xen_failed:await post('/test/xen/recover')
        if feed_stopped:await post('/test/mqtt/start')
        if paused:control_trial('unpause')
        if link_down and vm in domains():vif('up')
