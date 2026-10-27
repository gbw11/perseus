"""Trusted dom0 execution/receipt snapshot shared by Worker and Gateway.

This is not a guest claim or a control authority. Only a root-owned producer
may write it after Manager observation and applied bootstrap receipts. All
monotonic fields belong to the same dom0 host_boot_id.
"""
from __future__ import annotations
import json,os,stat,threading,time
from pathlib import Path
from uuid import UUID,uuid4

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
                if row.get('status')=='observed':
                    for key in ('execution_id','boot_id','app_instance_id'):canonical(row[key])
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

    def current(self,vm: str,now: float | None=None) -> dict | None:
        now=self.clock() if now is None else now
        with self._lock:
            v=self._value
            if v is None or not v['observed_monotonic_ms']<=int(now*1000)<=v['expires_monotonic_ms']:return None
            row=v['vms'].get(vm)
            return dict(row) if (row and row['status']=='observed' and
                0<=int(now*1000)-row['observed_monotonic_ms']<=3000) else None

    def accepts(self,vm: str,boot: str,instance: str,now: float | None=None) -> bool:
        row=self.current(vm,now)
        return bool(row and row['boot_id']==boot and row['app_instance_id']==instance)

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
