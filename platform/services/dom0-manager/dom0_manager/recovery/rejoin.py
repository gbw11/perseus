"""Existing Engine completion -> bootstrap receipts -> leased execution binding.

Runs inside Recovery Worker, independently of its long recovery executor. No
VM command, force escalation, authority issuance or app-memory replication is
implemented here. App-ready observation never means platform restart success.
"""
from __future__ import annotations
import fcntl,importlib,logging,os,queue,stat,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xenova_mqtt.drive_binding import BindingCache,app_instance,atomic_json,canonical,private_json
from xenova_mqtt.readiness import parse as parse_readiness

log=logging.getLogger(__name__)
CONTEXT=('vm_id','host_boot_id','execution_id','domid','xen_unique_id')

def valid_vm_trust_payload(payload):
    """Validate the legacy FW32 envelope, not permission for VM recovery."""
    if not isinstance(payload,dict) or payload.get('trust')!='UNTRUSTED':return False
    command_id=payload.get('command_id')
    if not isinstance(command_id,str) or not command_id or len(command_id)>128:return False
    return (payload.get('reason')=='AUTO_DEADLINE_MISS' and
            type(payload.get('consecutive_misses')) is int and payload['consecutive_misses']>=5)

class ProductionBootstrap:
    def __init__(self,config,observe,*,uid=0):
        self.config=config;self.observe=observe;self.uid=uid
        self.api=importlib.import_module('provision_xenova_telemetry')
    def __call__(self,row):
        vm=row['vm_id'];context={k:row[k] for k in CONTEXT}
        home=Path(self.config['receipt_dir'])/vm/row['execution_id'];home.mkdir(parents=True,exist_ok=True,mode=0o700)
        if home.resolve()!=home or home.stat().st_uid!=self.uid or home.stat().st_mode&0o077:
            raise ValueError('private receipt directory required')
        def current():
            now=self.observe(vm)
            if now['status']!='observed':raise ValueError('execution not eligible')
            return {k:now[k] for k in CONTEXT}
        # A Worker restart must not turn an app-only uncertain update into an
        # automatic Store retry or discard the other app's verified baseline.
        if (home/'drive.json').exists() and (home/'telemetry.json').exists():
            saved=private_json(home/'drive.json',self.uid)
            if (saved.get('drive_profile')=='warm-drive-1' and saved.get('phase') in ('prepared','uncertain') and
                saved.get('pending_app') in ('autonomous','manual')):
                for channel in ('drive','telemetry'):
                    _,source=self.api.credentials(Path(self.config['credentials'][vm][channel]),vm)
                    if private_json(home/(channel+'.json'),self.uid).get('credential_source')!=source:
                        raise ValueError('credential source changed during partial rejoin')
                return self.refresh(row)
        for channel in ('telemetry','drive'):
            credential,identity=self.api.credentials(Path(self.config['credentials'][vm][channel]),vm)
            receipt=home/(channel+'.json')
            lock=os.open(str(receipt)+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);store=None
            try:
                info=os.fstat(lock)
                if info.st_uid!=self.uid or info.st_mode&0o077 or not stat.S_ISREG(info.st_mode):
                    raise ValueError('private provisioning lock required')
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                store=self.api.XenStore(channel)
                self.api.provision(context,credential,identity,receipt,store=store,observe=current,
                    channel=channel,guest_boot_receipt=home/'telemetry.json' if channel=='drive' else None,
                    drive_profile=self.config.get('readiness_profile') if channel=='drive' else None)
            finally:
                if store:store.close()
                os.close(lock)
        return self.refresh(row)

    def refresh(self,row):
        """Read only current applied receipt identities; no reinjection or UUID minting."""
        context={k:row[k] for k in CONTEXT}
        home=Path(self.config['receipt_dir'])/row['vm_id']/row['execution_id']
        telemetry=private_json(home/'telemetry.json',self.uid);drive=private_json(home/'drive.json',self.uid)
        if (telemetry['phase']!='applied' or telemetry['context']!=context or
            drive['context']!=context or telemetry['boot_id']!=drive['boot_id']):raise ValueError('applied receipt mismatch')
        value={'boot_id':canonical(drive['boot_id']),'app_instance_id':canonical(drive['publisher_id'])}
        if drive['phase']!='applied' and drive.get('drive_profile')!='warm-drive-1':raise ValueError('applied receipt required')
        if drive.get('drive_profile')=='warm-drive-1':
            apps=drive['drive_apps']
            if set(apps)!={'autonomous','manual'}:raise ValueError('warm app set mismatch')
            value.update(profile='warm-drive-1',app_instances={k:canonical(v) for k,v in apps.items()})
            models=self.config.get('app_state_models')
            if models is not None:
                if (not isinstance(models,dict) or set(models)!=set(apps) or
                    any(v not in ('stateless','stateful') for v in models.values())):raise ValueError('app state model configuration')
                value['app_state_models']=dict(models)
            if drive['phase']!='applied':
                target=drive.get('pending_app');base=drive.get('app_update_base',{})
                change=drive.get('app_changes',{}).get(target,{})
                if (drive['phase'] not in ('prepared','uncertain') or target not in apps or set(base)!=set(apps) or
                    change.get('app')!=target or change.get('expected_instance')!=base.get(target) or
                    any(apps[app]!=base[app] for app in apps if app!=target)):
                    raise ValueError('no verified unaffected app baseline')
                for instance in base.values():canonical(instance)
                value['app_status']={app:'provisioning' if app==target else 'observed' for app in apps}
        if self.config.get('readiness_profile','verified-drive-1')!=value.get('profile','verified-drive-1'):
            raise ValueError('configured/receipt readiness profile differs')
        return value

class RejoinCoordinator:
    def __init__(self,config,observe,provision,host_boot_id,*,clock=time.monotonic,uid=0,observe_many=None):
        self.config=config;self.observe=observe;self.provision=provision;self.clock=clock;self.uid=uid
        self.observe_many=observe_many
        self.host=canonical(host_boot_id);self.vm_ids=tuple(canonical(v) for v in config['vm_ids'])
        if len(self.vm_ids)!=2 or len(set(self.vm_ids))!=2:raise ValueError('two distinct drive VMs required')
        self.cache=BindingCache(self.host,clock=clock);self._lock=threading.RLock();self._stop=threading.Event()
        self._executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='drive-rejoin')
        self._futures={};self._due={};self._rows={};self._completed={};self._progress={}
        self._batch=None;self._batch_targets=()
        self._published_rows=None
        self._reports=queue.Queue(maxsize=64)
        self._trust_reports=queue.Queue(maxsize=16)
        self._legacy_faults={}  # bounded by the two configured VMs and two legacy reasons
        self.path=Path(config['receipt_dir'])/'rejoin.json';self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.path.parent.resolve()!=self.path.parent or self.path.parent.stat().st_uid!=uid or self.path.parent.stat().st_mode&0o077:
            raise ValueError('canonical private receipt directory required')
        self.records={}
        if self.path.exists():
            value=private_json(self.path,uid)
            if value.get('schema_version')!='1.0-drive-rejoin':raise ValueError('rejoin journal schema invalid')
            self.records=value['vms']
        self._thread=None

    def completed(self,records):
        with self._lock:
            for record in records:
                if record['vm_id'] in self.vm_ids:self._completed[record['vm_id']]=dict(record)

    def _record(self,vm,value):
        with self._lock:
            records={**self.records,vm:value}
            atomic_json(self.path,{'schema_version':'1.0-drive-rejoin','vms':records})
            self.records=records

    def _work(self,vm,*,initial_row=None):
        if self._stop.is_set():raise RuntimeError('rejoin stopping')
        if isinstance(initial_row,Exception):raise initial_row
        with self._lock:
            old=self._rows.get(vm,{})
        refreshed=None;refresh_error=None
        if (old.get('status')=='observed' and old.get('app_instance_id') and
                hasattr(self.provision,'refresh')):
            # Receipt reads have no guest effect. Read them before the fresh
            # incarnation observation so an unchanged binding needs one full
            # Xen observation, while new provisioning keeps its before/after guards.
            try:refreshed=self.provision.refresh(old)
            except (OSError,ValueError) as exc:refresh_error=exc
        # A pair observation already validated the same host/target snapshots.
        # Receipt refresh has no physical effect and never renews its timestamp.
        # New provisioning still performs its own existing effect-time guards.
        row=self.observe(vm) if initial_row is None else initial_row
        if row['status']!='observed':
            self._record(vm,{'stage':row['status'].upper(),'execution_id':None});return row
        if row['host_boot_id']!=self.host:raise ValueError('host changed')
        execution=row['execution_id']
        with self._lock:
            completed=self._completed.get(vm);previous=self.records.get(vm,{})
        if row.get('execution_owner_kind')=='recovery' and not (
            (completed and completed['execution_id']==execution and completed['recovery_attempt_id']==row.get('recovery_attempt_id')) or
            (previous.get('execution_id')==execution and previous.get('recovery_attempt_id')==row.get('recovery_attempt_id') and
             previous.get('stage') in ('PROVISIONED','PREPARING','READY_OBSERVED','QUALIFIED_OBSERVED'))):
            return {**row,'status':'provisioning','reason':'ENGINE_COMPLETION_PENDING'}
        if (old.get('status')=='observed' and old.get('app_instance_id') and
                all(old.get(k)==row.get(k) for k in CONTEXT)):
            if refresh_error is not None:raise refresh_error
            if refreshed is not None:
                if self._stop.is_set():raise RuntimeError('rejoin stopped after refresh; no binding')
                return {**row,**refreshed}
        if old.get('execution_id')!=execution or not old.get('app_instance_id'):
            source={'execution_id':execution,'stage':'PROVISIONING',
                'recovery_attempt_id':row.get('recovery_attempt_id'),'recovery_id':(completed or {}).get('recovery_id')}
            self._record(vm,source)
            identity=self.provision(row)
            after=self.observe(vm)
            if any(after.get(k)!=row.get(k) for k in (*CONTEXT,'intent_revision','lifecycle_generation','status')):
                raise ValueError('execution/intent changed during provisioning')
            if self._stop.is_set():raise RuntimeError('rejoin stopped after provisioning; no binding')
            self._record(vm,{**source,'stage':'PROVISIONED',**identity})
            return {**after,**identity}
        identity=self.provision.refresh(row) if hasattr(self.provision,'refresh') else {
            k:old[k] for k in ('boot_id','app_instance_id','profile','app_instances','app_status') if k in old}
        after=self.observe(vm)
        if any(after.get(k)!=row.get(k) for k in (*CONTEXT,'intent_revision','lifecycle_generation','status')):
            raise ValueError('execution/intent changed during identity refresh')
        return {**after,**identity}

    def pump(self):
        if self._stop.is_set():return
        now=self.clock()
        with self._lock:
            if self._stop.is_set():return
            observed=False
            for vm,future in list(self._futures.items()):
                if not future.done():continue
                observed=True
                release_due=self._due.get(vm,now+1)
                del self._futures[vm];self._due[vm]=now+1
                try:
                    self._rows[vm]=future.result()
                    if self._rows[vm].get('status')=='observed':
                        self._due[vm]=max(release_due,now)
                except Exception as exc:
                    self._rows[vm]={'status':'unavailable','reason':type(exc).__name__}
                    log.warning('drive binding unavailable vm=%s cause=%s',vm,type(exc).__name__)
            if self._batch is not None and self._batch.done():
                future,targets=self._batch,self._batch_targets
                self._batch=None;self._batch_targets=()
                try:
                    rows=future.result()
                    if (not isinstance(rows,dict) or set(rows)!=set(targets) or
                        any(not isinstance(rows[vm],Exception)and(not isinstance(rows[vm],dict)or
                            rows[vm].get('vm_id')!=vm)for vm in targets)):
                        raise ValueError('batch observation target set invalid')
                except Exception as exc:
                    observed=True
                    for vm in targets:
                        self._rows[vm]={'status':'unavailable','reason':type(exc).__name__};self._due[vm]=now+1
                        log.warning('drive binding unavailable vm=%s cause=%s',vm,type(exc).__name__)
                else:
                    for vm in targets:
                        if not self._stop.is_set():
                            self._futures[vm]=self._executor.submit(self._work,vm,initial_row=rows[vm])
            if self.observe_many is not None:
                if not self._stop.is_set() and self._batch is None:
                    targets=tuple(vm for vm in self.vm_ids if vm not in self._futures and now>=self._due.get(vm,0))
                    if targets:
                        # One request-local snapshot uses the existing executor;
                        # due subsets preserve each VM's failure backoff.
                        released=self.clock()
                        for vm in targets:self._due[vm]=released+1
                        self._batch_targets=targets;self._batch=self._executor.submit(self.observe_many,targets)
            else:
                for vm in self.vm_ids:
                    if not self._stop.is_set() and vm not in self._futures and now>=self._due.get(vm,0):
                        # Successful probes keep a one-second release cadence. An
                        # overrun coalesces to one next probe; failures back off above.
                        self._due[vm]=self.clock()+1
                        self._futures[vm]=self._executor.submit(self._work,vm)
            value={'schema_version':'1.0-drive-bindings','host_boot_id':self.host,
                'observed_monotonic_ms':int(now*1000),'expires_monotonic_ms':int(now*1000)+3000,
                'vms':{vm:self._rows.get(vm,{'status':'unavailable'}) for vm in self.vm_ids}}
            try:
                # Pump readiness at 100ms, but publish only new observation rows.
                # Rewriting unchanged rows cannot extend their 3s lease and used
                # to repeat serialization/fsync while waiting for Xen observations.
                if not self._stop.is_set() and(observed or self._published_rows!=value['vms']):
                    atomic_json(Path(self.config['binding_file']),value)
                    if self._stop.is_set():self.cache.invalidate();return
                    self.cache.install(value)
                    self._published_rows=value['vms']
            except (OSError,ValueError):
                self._published_rows=None;self.cache.invalidate();raise
        for _ in range(64):
            try:message=self._reports.get_nowait()
            except queue.Empty:break
            self._apply_readiness(message)
        with self._lock:
            for key,progress in list(self._progress.items()):
                if now-progress[1]>.3 or self.cache.current(key[0],now) is None:
                    del self._progress[key]
                    entry=self.records.get(key[0],{})
                    if entry.get('stage') in ('READY_OBSERVED','QUALIFIED_OBSERVED'):
                        self._record(key[0],{**entry,'stage':'PREPARING','reason':'READINESS_EXPIRED'})

    def on_readiness(self,message):
        # Paho callback never waits on receipt fsync or authority reads.
        try:self._reports.put_nowait(message)
        except queue.Full:pass  # Dropped evidence expires; it never renews readiness.

    def on_trust(self,message):
        """Keep AUTO deadline evidence without promoting it to VM-wide failure.

        AUTO deadline misses restrict that function; its lifecycle is authoritative
        for Gateway selection. Xen/fatal/liveness recovery keeps its own path.
        """
        from xenova_mqtt import topics
        info=topics.parse(message.topic);payload=message.payload;now=self.clock()
        row=self.cache.current(info.vm_id,now)
        if (getattr(message,'retained',False) or message.type!='vm.trust' or info.app_id!='autonomous' or
            not valid_vm_trust_payload(payload) or row is None):return
        env=message.envelope
        if row['boot_id']!=env.get('boot_id') or app_instance(row,info.app_id)!=env.get('agent_instance_id'):return
        scope='function'
        key=(info.vm_id,payload['reason']);previous=self._legacy_faults.get(key,{})
        value={'vm_id':info.vm_id,'execution_id':row['execution_id'],'reason':payload['reason'],
               'scope':scope,'command_id':payload['command_id'],'received_monotonic':message.received_monotonic,
               'evidence':{key:payload[key] for key in ('trust','consecutive_misses') if key in payload},
               'reports':min(previous.get('reports',0)+1,2**64-1)}
        # One MQTT owner publishes a small immutable snapshot. Diagnostics never
        # waits on the coordinator's receipt/fsync lock and creates no per-key cache.
        self._legacy_faults={**self._legacy_faults,key:value}
        try:self._trust_reports.put_nowait({'vm_id':info.vm_id,'execution_id':row['execution_id'],
            'command_id':payload['command_id'],'scope':scope,'reason':payload['reason'],
            'received_monotonic':message.received_monotonic})
        except queue.Full:pass

    def legacy_fault_reports(self):
        """Coordinator-lifetime bounded diagnostics; never grants recovery."""
        snapshot=self._legacy_faults
        return [{**value,'evidence':dict(value['evidence'])} for value in snapshot.values()]

    def trust_events(self):
        values=[]
        while len(values)<16:
            try:values.append(self._trust_reports.get_nowait())
            except queue.Empty:break
        return values

    def _apply_readiness(self,message):
        from xenova_mqtt import topics
        info=topics.parse(message.topic);env=message.envelope;t=message.received_monotonic;now=self.clock()
        if (getattr(message,'retained',False) or message.type not in ('app.readiness','app.lifecycle') or info.app_id not in ('autonomous','manual') or
            not 0<=now-t<=.3 or info.vm_id!=env.get('vm_id') or
            not self.cache.accepts(info.vm_id,env.get('boot_id'),env.get('agent_instance_id'),now,app=info.app_id)):return
        warm=self.config.get('readiness_profile')=='warm-drive-1'
        if warm!=(message.type=='app.lifecycle'):return
        try:
            if warm:
                from xenova_mqtt.lifecycle import parse
                ready=parse(message.payload)
            else:ready=parse_readiness(message.payload)
            authority=private_json(Path(self.config['authority_file']),self.uid)
        except (ValueError,OSError,KeyError,TypeError):return
        if ready.generation!=authority.get('generation'):return
        mode=authority.get('mode')
        if mode not in ('SAFE_STOP','MANUAL','AUTO_READY','AUTO'):return
        app='manual' if mode in ('SAFE_STOP','MANUAL','AUTO_READY') else 'autonomous'
        if info.app_id!=app:return
        row=self.cache.current(info.vm_id,now)
        if row is None:return
        key=(info.vm_id,row['execution_id'],app,env.get('agent_instance_id'),ready.generation)
        with self._lock:
            for old_key in list(self._progress):
                if old_key[0]==info.vm_id and old_key[2]==app and old_key!=key:del self._progress[old_key]
            old=self._progress.get(key)
            sequence=ready.check if warm else ready.progress
            state=ready.readiness if warm else ready.state
            if old and sequence<old[0]:return
            if old and sequence==old[0] and (warm or state=='READY'):return
            if warm and state=='READY' and old and ready.progress<=old[3]:return
            since=old[2] if old and 0<=t-old[1]<=.3 and old[2] is not None else t
            if state not in ('READY','QUALIFIED'):since=None
            self._progress[key]=(sequence,t,since,ready.progress)
            entry=self.records.get(info.vm_id,{})
            if entry.get('execution_id')!=row['execution_id']:return
            stage=('QUALIFIED_OBSERVED' if warm and state=='QUALIFIED' else 'READY_OBSERVED') if since is not None and t-since>=3 else 'PREPARING'
            if entry.get('stage')!=stage:self._record(info.vm_id,{**entry,'stage':stage,'applied_generation':ready.generation,
                'reason':ready.reason})

    def start(self):
        def loop():
            while not self._stop.is_set():
                try:self.pump()
                except Exception:log.exception('drive rejoin publication failed')
                self._stop.wait(.1)
        self._thread=threading.Thread(target=loop,name='drive-rejoin-publisher',daemon=True);self._thread.start()

    def stop(self):
        self._stop.set();self.cache.invalidate()
        if self._thread:self._thread.join(timeout=1)
        self._executor.shutdown(wait=False,cancel_futures=True)
        pending=list(self._futures.values())
        if self._batch is not None:pending.append(self._batch)
        return(self._thread is None or not self._thread.is_alive())and all(f.done()for f in pending)

def production(config_file,manager_data):
    config=private_json(Path(config_file))
    if config.get('schema_version')!='1.0-drive-rejoin-config':raise ValueError('drive config schema invalid')
    data=Path(manager_data)
    if not os.path.ismount(data):raise ValueError('mounted Xenova persistent data required before receipt creation')
    for name in ('receipt_dir','authority_file'):
        path=Path(config[name])
        if not path.is_absolute() or path.resolve()!=path or not path.is_relative_to(data):
            raise ValueError(f'{name} must be a canonical path in mounted persistent data')
    binding=Path(config['binding_file'])
    if not binding.is_absolute() or binding.resolve()!=binding or not binding.is_relative_to('/run/xenova'):
        raise ValueError('binding_file must be in canonical /run/xenova')
    for vm in config['vm_ids']:
        canonical(vm)
        for channel in ('drive','telemetry'):private_json(Path(config['credentials'][vm][channel]))
    from xenova_vm import Platform,storage_check
    storage_check(data)  # Reuse the ext4/marker/install guard, not an arbitrary bind mount.
    from xenova_drive_binding import observe,observe_pair
    host=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    platform=Platform(data=manager_data)
    coordinator=None
    def current(vm):
        if coordinator is not None and coordinator._stop.is_set():raise RuntimeError('rejoin stopping')
        return observe(platform,vm)
    def current_many(identities):
        if coordinator is not None and coordinator._stop.is_set():raise RuntimeError('rejoin stopping')
        return observe_pair(platform,identities)
    bootstrap=ProductionBootstrap(config,current)
    coordinator=RejoinCoordinator(config,current,bootstrap,host,observe_many=current_many)
    return coordinator
