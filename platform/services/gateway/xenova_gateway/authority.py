"""Single-dom0 control authority. Persist before publishing; never derive a
generation from MQTT absence. Stored preference does not restore output power.
"""
from __future__ import annotations
import json,os,stat,threading
from pathlib import Path
from xenova_mqtt.drive_binding import atomic_json,private_json,canonical
from .machine import Mode,ACTIVE_APP

class AuthorityError(RuntimeError):pass

class GatewayLease:
    """The Worker's nonblocking flock ownership pattern, scoped to drive output."""
    def __init__(self,path: Path):self.path=Path(path);self.fd=None
    def acquire(self):
        import fcntl
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
                raise AuthorityError('private owned Gateway lock required')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:os.close(fd);raise AuthorityError('another Gateway owns control/output') from None
        self.fd=fd;return self
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None

class AuthorityStore:
    def __init__(self,directory: Path,host_boot_id: str,*,uid=0,mountpoint: Path | None=Path('/srv/xenova')):
        self.directory=Path(directory);self.host=canonical(host_boot_id);self.uid=uid;self.error=None
        self._lock=threading.RLock()
        if not self.directory.is_absolute() or self.directory.resolve()!=self.directory:
            raise AuthorityError('canonical absolute authority directory required')
        if mountpoint is not None and (not os.path.ismount(mountpoint) or not self.directory.is_relative_to(mountpoint)):
            raise AuthorityError('mounted persistent Xenova data required')
        if mountpoint is not None:
            try:
                from xenova_vm import storage_check
                storage_check(Path(mountpoint))
            except (ImportError,RuntimeError,OSError,ValueError) as exc:
                raise AuthorityError('verified Xenova ext4 storage/installation required') from exc
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        s=self.directory.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid or s.st_mode&0o077:
            raise AuthorityError('private owned authority directory required')
        self.path=self.directory/'control.json';self.marker=self.directory/'initialized'
        self.value=None

    def initialize(self,owner: str):
        canonical(owner)
        if self.path.exists() or self.path.is_symlink() or self.marker.exists() or self.marker.is_symlink():
            raise AuthorityError('authority already initialized; no overwrite')
        fd=os.open(self.marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:stream.write('1\n');stream.flush();os.fsync(stream.fileno())
        directory=os.open(self.directory,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        self.save({'schema_version':'1.0-drive-authority','generation':1,'mode':'SAFE_STOP','owner':owner,
            'reason':'first_install','phase':'DISARMED','pending':None,'rearm_required':True,'requests':[]})

    def load(self):
        if not self.marker.is_file():raise AuthorityError('explicit first-install initialization required')
        try:self.value=private_json(self.path,self.uid);self.validate(self.value)
        except (OSError,ValueError,KeyError,TypeError) as exc:raise AuthorityError('authority missing/corrupt; output forbidden') from exc
        return self.value

    @staticmethod
    def validate(value):
        if value.get('schema_version')!='1.0-drive-authority':raise ValueError('authority schema invalid')
        if type(value['generation']) is not int or not 1<=value['generation']<=0xffffffff:raise ValueError('generation invalid')
        Mode(value['mode']);canonical(value['owner'])
        if type(value['rearm_required']) is not bool or not isinstance(value['requests'],list) or len(value['requests'])>128:
            raise ValueError('authority request state invalid')
        if value['phase'] not in ('DISARMED','PENDING','APPLIED','FAILED'):raise ValueError('authority phase invalid')
        if len(set(value['requests']))!=len(value['requests']):raise ValueError('duplicate request identity')
        for request in value['requests']:canonical(request)
        if not isinstance(value['reason'],str) or len(value['reason'])>128:raise ValueError('invalid authority reason')
        pending=value['pending']
        if pending is not None:
            canonical(pending['host_boot_id'])
            if (type(pending['generation']) is not int or pending['generation']!=value['generation'] or type(pending['deadline_ms']) is not int or
                pending['deadline_ms']<0 or type(pending['arm_after_apply']) is not bool):raise ValueError('pending context invalid')
        if value['phase']=='PENDING' and pending is None:raise ValueError('pending deadline required')

    def save(self,value):
        self.validate(value)
        with self._lock:
            # replace may have succeeded before parent fsync failed. Do not
            # overwrite that higher generation using our old memory snapshot.
            if self.error:raise AuthorityError(self.error)
            try:atomic_json(self.path,value)
            except (OSError,ValueError) as exc:
                self.error='AUTHORITY_STORAGE_UNCERTAIN'
                raise AuthorityError(self.error) from exc
            self.value=json.loads(json.dumps(value))
        return self.value

    def verify(self):
        """Background read: loss or uncoordinated replacement blocks output."""
        with self._lock:
            try:
                current=private_json(self.path,self.uid);self.validate(current)
                if current!=self.value or not self.marker.is_file():raise ValueError('authority replaced/lost')
            except (OSError,ValueError,KeyError,TypeError):self.error='AUTHORITY_STORAGE_UNCERTAIN'
        return self.error is None

    def update(self,**fields):
        value={**self.value,**fields};return self.save(value)

    def issue(self,mode: Mode,owner: str,reason: str,now: float,timeout: float,*,arm: bool):
        if self.error:raise AuthorityError(self.error)
        canonical(owner)
        if self.value['generation']==0xffffffff:raise AuthorityError('generation exhausted; no wrap/reset')
        generation=self.value['generation']+1
        return self.update(generation=generation,mode=mode.value,owner=owner,reason=reason,phase='PENDING',
            pending={'generation':generation,'host_boot_id':self.host,'deadline_ms':int((now+timeout)*1000),
                     'arm_after_apply':arm},requests=[])

    def consume_request(self,message_id: str,expected_generation: int):
        canonical(message_id)
        if type(expected_generation) is not int or expected_generation!=self.value['generation']:
            return False
        if message_id in self.value['requests'] or len(self.value['requests'])>=128:return False
        self.update(requests=[*self.value['requests'],message_id]);return True

    def payload(self):
        mode=Mode(self.value['mode'])
        return {'generation':self.value['generation'],'mode':mode.value,'active_app':ACTIVE_APP[mode],
                'active_vm_id':self.value['owner'] if ACTIVE_APP[mode] else None,'reason':self.value['reason']}

    def reconcile(self,payload: dict):
        """An older delivery can be republished from the ledger; conflict cannot issue authority."""
        value=payload.get('generation')
        if type(value) is not int or not 1<=value<=self.value['generation']:
            raise AuthorityError('retained generation conflicts with authority')
        if value==self.value['generation'] and any(payload.get(k)!=self.payload()[k] for k in ('mode','active_app','active_vm_id')):
            raise AuthorityError('retained selection conflicts with authority')
