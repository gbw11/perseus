"""Trusted dom0 execution/receipt snapshot shared by Worker and Gateway.

This is not a guest claim or a control authority. Only a root-owned producer
may write it after Manager observation and applied bootstrap receipts. All
monotonic fields belong to the same dom0 host_boot_id.
"""
from __future__ import annotations
import json,os,stat,threading,time
from pathlib import Path
from uuid import UUID,uuid4

DRIVE_APPS=('autonomous','manual')

def app_instance(row: dict, app: str | None=None) -> str | None:
    """New bindings require an explicit app. No shared-ID fallback for that profile."""
    if 'app_instances' in row:
        return row['app_instances'].get(app) if app in DRIVE_APPS and row.get('app_status',{}).get(app,'observed')=='observed' else None
    return row.get('app_instance_id')

def canonical(value: object) -> str:
    if not isinstance(value,str) or str(UUID(value))!=value or not UUID(value).int:
        raise ValueError('canonical nonzero UUID required')
    return value

def atomic_json(path: Path,value: dict) -> None:
    """Same-directory replace + file and parent fsync; no success after a failed sync."""
    path=Path(path);temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,separators=(',',':'),allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if temp.exists():temp.unlink()

def private_json(path: Path,uid: int=0) -> dict:
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        s=os.fstat(stream.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=uid or s.st_mode&0o077 or s.st_size>65536:
            raise ValueError('private owned regular JSON required')
        value=json.load(stream)
    if not isinstance(value,dict):raise ValueError('JSON object required')
    return value

class BindingCache:
    """Read-only memory copy. Failure/expiry revokes acceptance, never invents a session."""
    def __init__(self,host_boot_id: str,*,clock=time.monotonic):
        self.host_boot_id=canonical(host_boot_id);self.clock=clock
        self._lock=threading.Lock();self._value=None;self.error='BINDING_UNAVAILABLE'

    def install(self,value: dict) -> None:
        try:
            if value.get('schema_version')!='1.0-drive-bindings' or value.get('host_boot_id')!=self.host_boot_id:
                raise ValueError('binding host/schema mismatch')
            observed,expires=value['observed_monotonic_ms'],value['expires_monotonic_ms']
            if (type(observed) is not int or type(expires) is not int or observed<0 or
                not 0<expires-observed<=3000 or not isinstance(value['vms'],dict)):
                raise ValueError('binding lease invalid')
            for vm,row in value['vms'].items():
                canonical(vm)
                if not isinstance(row,dict):raise ValueError('binding row object required')
                if row.get('status')=='observed':
                    for key in ('execution_id','boot_id','app_instance_id'):canonical(row[key])
                    if row.get('profile','verified-drive-1') not in ('verified-drive-1','warm-drive-1'):
                        raise ValueError('unknown binding profile')
                    if row.get('profile')=='warm-drive-1' and 'app_instances' not in row:
                        raise ValueError('warm app bindings missing')
                    if 'app_instances' in row:
                        if row.get('profile')!='warm-drive-1' or not isinstance(row['app_instances'],dict) or set(row['app_instances'])!=set(DRIVE_APPS):
                            raise ValueError('explicit warm app bindings required')
                        for identity in row['app_instances'].values():canonical(identity)
                        if len(set(row['app_instances'].values()))!=2:raise ValueError('distinct app instances required')
                        if 'app_status' in row and (not isinstance(row['app_status'],dict) or set(row['app_status'])!=set(DRIVE_APPS) or
                            any(v not in ('observed','provisioning','unavailable') for v in row['app_status'].values())):
                            raise ValueError('invalid per-app binding status')
                    elif 'app_status' in row:raise ValueError('per-app status requires app identities')
                    if 'app_state_models' in row and (not isinstance(row['app_state_models'],dict) or
                        set(row['app_state_models'])!=set(DRIVE_APPS) or
                        any(v not in ('stateless','stateful') for v in row['app_state_models'].values())):
                        raise ValueError('explicit app state models required')
                    for key in ('intent_revision','lifecycle_generation','domid'):
                        if type(row[key]) is not int or row[key]<0:raise ValueError('binding integer invalid')
                    if type(row['observed_monotonic_ms']) is not int or row['observed_monotonic_ms']<0:
                        raise ValueError('per-execution observation time required')
                    if not isinstance(row['xen_unique_id'],str) or not row['xen_unique_id'].isascii() or not row['xen_unique_id'].isdecimal() or int(row['xen_unique_id'])>2**64-1:
                        raise ValueError('Xen incarnation invalid')
                elif row.get('status') not in ('stopped','absent','unavailable','provisioning'):
                    raise ValueError('unknown execution status')
        except (KeyError,TypeError,ValueError):self.invalidate();raise
        with self._lock:self._value=json.loads(json.dumps(value));self.error=None

    def invalidate(self):
        with self._lock:self._value=None;self.error='BINDING_UNAVAILABLE'

    def guard(self,vm_ids):
        """Reject old/mismatched app traffic before generic seq/latest admission."""
        from . import topics
        identities=frozenset(vm_ids)
        def check(topic,envelope,retained):
            info=topics.parse(topic)
            if info.app_id in DRIVE_APPS and (info.vm_id in identities or envelope.get('vm_id') in identities):
                return bool(not retained and info.vm_id in identities and info.vm_id==envelope.get('vm_id') and
                    info.app_id==envelope.get('app_id') and self.accepts(info.vm_id,envelope.get('boot_id'),
                        envelope.get('agent_instance_id'),app=info.app_id))
            return True
        return check

    def current(self,vm: str,now: float | None=None) -> dict | None:
        now=self.clock() if now is None else now
        with self._lock:
            v=self._value
            if v is None or not v['observed_monotonic_ms']<=int(now*1000)<=v['expires_monotonic_ms']:return None
            row=v['vms'].get(vm)
            if not (row and row['status']=='observed' and 0<=int(now*1000)-row['observed_monotonic_ms']<=3000):return None
            return {**row,**({'app_instances':dict(row['app_instances'])} if 'app_instances' in row else {}),
                    **({'app_status':dict(row['app_status'])} if 'app_status' in row else {}),
                    **({'app_state_models':dict(row['app_state_models'])} if 'app_state_models' in row else {})}

    def accepts(self,vm: str,boot: str,instance: str,now: float | None=None,*,app: str | None=None) -> bool:
        row=self.current(vm,now)
        expected=app_instance(row,app) if row else None
        return bool(row and expected is not None and row['boot_id']==boot and expected==instance)

class BindingFile(BindingCache):
    """A small file reader thread; Manager/receipt I/O never runs per drive message."""
    def __init__(self,path: Path,host_boot_id: str,*,clock=time.monotonic,uid=0):
        super().__init__(host_boot_id,clock=clock);self.path=Path(path);self.uid=uid
        self._stop=threading.Event();self._thread=None

    def refresh(self):
        try:self.install(private_json(self.path,self.uid))
        except (OSError,ValueError):self.invalidate()

    def start(self):
        def read():
            while not self._stop.is_set():self.refresh();self._stop.wait(.1)
        self.refresh();self._thread=threading.Thread(target=read,name='drive-binding',daemon=True);self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:self._thread.join(timeout=1)
        if self._thread and self._thread.is_alive():raise TimeoutError('binding reader did not stop')
