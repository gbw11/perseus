"""Existing Engine completion -> bootstrap receipts -> leased execution binding.

Runs inside Recovery Worker, independently of its long recovery executor. No
VM command, force escalation, authority issuance or app-memory replication is
implemented here. App-ready observation never means platform restart success.
"""
from __future__ import annotations
import fcntl,importlib,logging,os,queue,stat,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xenova_mqtt.drive_binding import BindingCache,atomic_json,canonical,private_json
from xenova_mqtt.readiness import parse as parse_readiness

log=logging.getLogger(__name__)
CONTEXT=('vm_id','host_boot_id','execution_id','domid','xen_unique_id')

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
                    channel=channel,guest_boot_receipt=home/'telemetry.json' if channel=='drive' else None)
            finally:
                if store:store.close()
                os.close(lock)
        telemetry=private_json(home/'telemetry.json',self.uid);drive=private_json(home/'drive.json',self.uid)
        if (telemetry['phase']!='applied' or drive['phase']!='applied' or telemetry['context']!=context or
            drive['context']!=context or telemetry['boot_id']!=drive['boot_id']):raise ValueError('applied receipt mismatch')
        return {'boot_id':canonical(drive['boot_id']),'app_instance_id':canonical(drive['publisher_id'])}

class RejoinCoordinator:
    def __init__(self,config,observe,provision,host_boot_id,*,clock=time.monotonic,uid=0):
        self.config=config;self.observe=observe;self.provision=provision;self.clock=clock;self.uid=uid
        self.host=canonical(host_boot_id);self.vm_ids=tuple(canonical(v) for v in config['vm_ids'])
        if len(self.vm_ids)!=2 or len(set(self.vm_ids))!=2:raise ValueError('two distinct drive VMs required')
        self.cache=BindingCache(self.host,clock=clock);self._lock=threading.RLock();self._stop=threading.Event()
        self._executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='drive-rejoin')
        self._futures={};self._due={};self._rows={};self._completed={};self._progress={}
        self._reports=queue.Queue(maxsize=64)
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

    def _work(self,vm):
        if self._stop.is_set():raise RuntimeError('rejoin stopping')
        row=self.observe(vm)
        if row['status']!='observed':
            self._record(vm,{'stage':row['status'].upper(),'execution_id':None});return row
        if row['host_boot_id']!=self.host:raise ValueError('host changed')
        execution=row['execution_id']
        with self._lock:
            completed=self._completed.get(vm);previous=self.records.get(vm,{})
            old=self._rows.get(vm,{})
        if row.get('execution_owner_kind')=='recovery' and not (
            (completed and completed['execution_id']==execution and completed['recovery_attempt_id']==row.get('recovery_attempt_id')) or
            (previous.get('execution_id')==execution and previous.get('recovery_attempt_id')==row.get('recovery_attempt_id') and
             previous.get('stage') in ('PROVISIONED','PREPARING','READY_OBSERVED'))):
            return {**row,'status':'provisioning','reason':'ENGINE_COMPLETION_PENDING'}
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
        return {**row,**{k:old[k] for k in ('boot_id','app_instance_id')}}

    def pump(self):
        now=self.clock()
        with self._lock:
            for vm,future in list(self._futures.items()):
                if not future.done():continue
                del self._futures[vm];self._due[vm]=now+1
                try:self._rows[vm]=future.result()
                except Exception as exc:
                    self._rows[vm]={'status':'unavailable','reason':type(exc).__name__}
                    log.warning('drive binding unavailable vm=%s cause=%s',vm,type(exc).__name__)
            for vm in self.vm_ids:
                if not self._stop.is_set() and vm not in self._futures and now>=self._due.get(vm,0):
                    self._futures[vm]=self._executor.submit(self._work,vm)
            value={'schema_version':'1.0-drive-bindings','host_boot_id':self.host,
                'observed_monotonic_ms':int(now*1000),'expires_monotonic_ms':int(now*1000)+3000,
                'vms':{vm:self._rows.get(vm,{'status':'unavailable'}) for vm in self.vm_ids}}
            try:
                atomic_json(Path(self.config['binding_file']),value);self.cache.install(value)
            except (OSError,ValueError):self.cache.invalidate();raise
        for _ in range(64):
            try:message=self._reports.get_nowait()
            except queue.Empty:break
            self._apply_readiness(message)
        with self._lock:
            for key,progress in list(self._progress.items()):
                if now-progress[1]>.3 or self.cache.current(key[0],now) is None:
                    del self._progress[key]
                    entry=self.records.get(key[0],{})
                    if entry.get('stage')=='READY_OBSERVED':
                        self._record(key[0],{**entry,'stage':'PREPARING','reason':'READINESS_EXPIRED'})

    def on_readiness(self,message):
        # Paho callback never waits on receipt fsync or authority reads.
        try:self._reports.put_nowait(message)
        except queue.Full:pass  # Dropped evidence expires; it never renews readiness.

    def _apply_readiness(self,message):
        from xenova_mqtt import topics
        info=topics.parse(message.topic);env=message.envelope;t=message.received_monotonic
        if (getattr(message,'retained',False) or message.type!='app.readiness' or info.app_id not in ('autonomous','manual') or
            info.vm_id!=env.get('vm_id') or not self.cache.accepts(info.vm_id,env.get('boot_id'),env.get('agent_instance_id'),t)):return
        try:ready=parse_readiness(message.payload);authority=private_json(Path(self.config['authority_file']),self.uid)
        except (ValueError,OSError,KeyError,TypeError):return
        if ready.generation!=authority.get('generation'):return
        mode=authority.get('mode')
        if mode not in ('SAFE_STOP','MANUAL','AUTO_READY','AUTO'):return
        app='manual' if mode in ('SAFE_STOP','MANUAL','AUTO_READY') else 'autonomous'
        if info.app_id!=app:return
        row=self.cache.current(info.vm_id,t)
        if row is None:return
        key=(info.vm_id,row['execution_id'],app,ready.generation)
        with self._lock:
            for old_key in list(self._progress):
                if old_key[0]==info.vm_id and old_key!=key:del self._progress[old_key]
            old=self._progress.get(key)
            if old and ready.progress<old[0]:return
            if old and ready.progress==old[0] and ready.state=='READY':return
            since=old[2] if old and 0<=t-old[1]<=.3 and old[2] is not None else t
            if ready.state!='READY':since=None
            self._progress[key]=(ready.progress,t,since)
            entry=self.records.get(info.vm_id,{})
            if entry.get('execution_id')!=row['execution_id']:return
            stage='READY_OBSERVED' if since is not None and t-since>=3 else 'PREPARING'
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
        return not any(not f.done() for f in self._futures.values())

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
    from xenova_drive_binding import observe
    host=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    platform=Platform(data=manager_data)
    coordinator=None
    def current(vm):
        if coordinator is not None and coordinator._stop.is_set():raise RuntimeError('rejoin stopping')
        return observe(platform,vm)
    bootstrap=ProductionBootstrap(config,current)
    coordinator=RejoinCoordinator(config,current,bootstrap,host)
    return coordinator
