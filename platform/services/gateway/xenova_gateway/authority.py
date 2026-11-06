"""Single-dom0 control authority. Persist before publishing; never derive a
generation from MQTT absence. Stored preference does not restore output power.
"""
from __future__ import annotations
import json,os,stat,threading
from pathlib import Path
from xenova_mqtt.drive_binding import atomic_json,canonical
from .machine import Mode,ACTIVE_APP

class AuthorityError(RuntimeError):pass

TRANSITION_LIMIT=32  # Bounded private records; never silently evict dedup evidence.
TRANSITION_PHASES={'PREPARING','PREPARED','COMMITTED','CANCELLED','FAILED','ABANDONED'}
GATEWAY_LOCK=Path('/run/lock/xenova-drive-gateway.lock')

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
    def __init__(self,directory: Path,host_boot_id: str,*,uid=0,mountpoint: Path | None=Path('/srv/xenova'),history_policy=None):
        self.directory=Path(directory);self.host=canonical(host_boot_id);self.uid=uid;self.error=None
        self._lock=threading.RLock()
        self.history_policy=history_policy
        self.check_storage(self.directory,mountpoint)
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        s=self.directory.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid or s.st_mode&0o077:
            raise AuthorityError('private owned authority directory required')
        self.path=self.directory/'control.json';self.marker=self.directory/'initialized'
        self.value=None

    @staticmethod
    def check_storage(directory,mountpoint):
        """Check the existing storage boundary without creating an authority."""
        directory=Path(directory)
        if not directory.is_absolute() or directory.resolve()!=directory:
            raise AuthorityError('canonical absolute authority directory required')
        if mountpoint is not None and (not os.path.ismount(mountpoint) or not directory.is_relative_to(mountpoint)):
            raise AuthorityError('mounted persistent Xenova data required')
        if mountpoint is not None:
            try:
                from xenova_vm import storage_check
                storage_check(Path(mountpoint))
            except (ImportError,RuntimeError,OSError,ValueError) as exc:
                raise AuthorityError('verified Xenova ext4 storage/installation required') from exc

    def check_marker(self):
        """The first-install marker is private evidence, never a followed link."""
        fd=os.open(self.marker,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=self.uid or
                    info.st_mode&0o077 or info.st_size!=2 or stream.read(3)!=b'1\n'):
                raise AuthorityError('private initialized marker invalid')

    def initialize(self,owner: str):
        canonical(owner)
        if next(self.directory.iterdir(),None) is not None:
            raise AuthorityError('authority already initialized; no overwrite')
        fd=os.open(self.marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:stream.write('1\n');stream.flush();os.fsync(stream.fileno())
        directory=os.open(self.directory,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        self.save({'schema_version':'1.0-drive-authority','generation':1,'mode':'SAFE_STOP','owner':owner,
            'reason':'first_install','phase':'DISARMED','pending':None,'rearm_required':True,'requests':[]})

    def load(self):
        try:
            self.check_marker()
            value=self.read_current();self.validate(value)
        except FileNotFoundError as exc:
            if next(self.directory.iterdir(),None) is None:
                raise AuthorityError('explicit first-install initialization required') from exc
            raise AuthorityError('authority missing/corrupt; output forbidden') from exc
        except (OSError,ValueError,KeyError,TypeError,AuthorityError) as exc:
            raise AuthorityError('authority missing/corrupt; output forbidden') from exc
        self.value=value
        return self.value

    def read_current(self):
        """A corrupt path must not turn a FIFO/device open into an unbounded wait."""
        fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=self.uid or info.st_mode&0o077 or info.st_size>65536:
                raise AuthorityError('private bounded authority record required')
            raw=stream.read(65537)
            if len(raw.encode())>65536:raise AuthorityError('authority record too large')
            value=json.loads(raw)
            if not isinstance(value,dict):raise AuthorityError('authority record object required')
            return value

    @staticmethod
    def validate(value):
        from .history import validate as validate_history
        validate_history(value)
        if value.get('schema_version')!='1.0-drive-authority':raise ValueError('authority schema invalid')
        if type(value['generation']) is not int or not 1<=value['generation']<=0xffffffff:raise ValueError('generation invalid')
        Mode(value['mode']);canonical(value['owner'])
        if type(value['rearm_required']) is not bool or not isinstance(value['requests'],list) or len(value['requests'])>128:
            raise ValueError('authority request state invalid')
        if value['phase'] not in ('DISARMED','PENDING','APPLIED','FAILED'):raise ValueError('authority phase invalid')
        if len(set(value['requests']))!=len(value['requests']):raise ValueError('duplicate request identity')
        for request in value['requests']:canonical(request)
        if not isinstance(value['reason'],str) or len(value['reason'])>128:raise ValueError('invalid authority reason')
        if 'manual_hold' in value and type(value['manual_hold']) is not bool:raise ValueError('invalid manual selection')
        distrusted=value.get('distrusted',[])
        if not isinstance(distrusted,list) or len(distrusted)>2:raise ValueError('distrusted VM state invalid')
        seen=set()
        for row in distrusted:
            canonical(row['vm_id'])
            canonical(row['execution_id'])
            if row['vm_id'] in seen:raise ValueError('duplicate distrusted VM')
            seen.add(row['vm_id'])
            if (row.get('reason') not in ('AUTO_DEADLINE_MISS','MEMORY_PRESSURE') or not isinstance(row.get('command_id'),str) or
                not 0<len(row['command_id'])<=128 or type(row.get('generation')) is not int or
                not 1<=row['generation']<=0xffffffff or type(row.get('observed_monotonic_ms')) is not int or
                row['observed_monotonic_ms']<0):raise ValueError('distrusted VM record invalid')
        pending=value['pending']
        if pending is not None:
            canonical(pending['host_boot_id'])
            if (type(pending['generation']) is not int or pending['generation']!=value['generation'] or type(pending['deadline_ms']) is not int or
                pending['deadline_ms']<0 or type(pending['arm_after_apply']) is not bool):raise ValueError('pending context invalid')
        if value['phase']=='PENDING' and pending is None:raise ValueError('pending deadline required')
        if value.get('manual_input_profile') not in (None,'manual-session-1'):raise ValueError('manual input profile')
        if pending and ('manual_policy' in pending or 'manual_session' in pending):
            canonical(pending['manual_session'])
            if pending['manual_policy'] not in ('CONTINUE_SESSION','NEUTRAL_THEN_ACTION'):raise ValueError('manual policy')
        transitions=value.get('transitions',[])
        if not isinstance(transitions,list) or len(transitions)>TRANSITION_LIMIT:raise ValueError('transition limit')
        identities=set()
        for row in transitions:
            from .ipc_contract import request_key
            request_key(row['request_id'])
            if row['request_id'] in identities:raise ValueError('duplicate transition request')
            identities.add(row['request_id']);canonical(row['host_boot_id'])
            if row['phase'] not in TRANSITION_PHASES:raise ValueError('transition phase')
            spec=row['spec'];Mode(spec['mode']);canonical(spec['owner']);canonical(spec['target_vm'])
            if type(spec['generation']) is not int or not 1<=spec['generation']<=0xffffffff:raise ValueError('transition generation')
            if type(spec['deadline_ms']) is not int or spec['deadline_ms']<0:raise ValueError('transition deadline')
            target=spec['target']
            if spec['app'] not in ('autonomous','manual'):raise ValueError('transition app')
            if 'manual_session' in spec:
                canonical(spec['manual_session'])
                if spec['manual_policy'] not in ('CONTINUE_SESSION','NEUTRAL_THEN_ACTION'):raise ValueError('transition manual policy')
            for name in ('execution_id','boot_id','app_instance_id'):canonical(target[name])
            for name in ('intent_revision','lifecycle_generation'):
                if type(target[name]) is not int or target[name]<0:raise ValueError('transition context revision')
            source=spec.get('source')
            if source is not None:
                for name in ('execution_id','boot_id','app_instance_id'):canonical(source[name])
                for name in ('intent_revision','lifecycle_generation'):
                    if type(source[name]) is not int or source[name]<0:raise ValueError('source context revision')
            if spec.get('state_model') not in ('stateless','stateful'):raise ValueError('unsupported transition state model')
            if spec['state_model']=='stateful' and row['phase'] in ('PREPARED','COMMITTED'):
                from xenova_mqtt.handoff import context as state_context,counter
                proof=row['evidence']
                if proof.get('kind')!='state_applied' or proof.get('continuity') not in ('continuous','stop_then_resume'):
                    raise ValueError('complete state preparation proof required')
                state_context(proof['context']);counter(proof['state_revision'])
                if proof['target']!=target or proof['origin'] not in ('handoff','rebuilt'):
                    raise ValueError('state preparation target invalid')
                if proof['origin']=='handoff':canonical(proof['copy_message_id'])
                elif proof['copy_message_id'] is not None:raise ValueError('rebuilt copy reference')
            if row['phase']=='COMMITTED' and row.get('committed_generation')!=spec['generation']+1:
                raise ValueError('transition commit result')
            if 'provider_request' in spec:
                from .provider import normalize,validate_result
                submitted=spec['provider_request']
                if normalize(submitted,mode=spec['mode'])!=submitted or submitted['purpose']!='handoff':
                    raise ValueError('provider transition spec invalid')
                if any(submitted[a]!=spec[b] for a,b in (
                        ('expected_generation','generation'),('expected_owner','owner'),
                        ('deadline_ms','deadline_ms'),('target_vm','target_vm'),('mode','mode'))):
                    raise ValueError('provider transition identity mismatch')
                if (submitted['host_boot_id']!=row['host_boot_id'] or
                        submitted['target_execution_id']!=target['execution_id'] or
                        any(submitted[k]!=target[k] for k in ('intent_revision','lifecycle_generation'))):
                    raise ValueError('provider transition target mismatch')
                validate_result(row,submitted,row.get('committed_generation'))
        from .provider import validate_records
        validate_records(value)

    def save(self,value):
        self.validate(value)
        encoded=json.dumps(value,separators=(',',':'))
        if len(encoded.encode())>65536:
            raise AuthorityError('authority record size limit; no write')
        # 크기를 검사한 같은 JSON을 독립 사본으로 만든다. 호출자 가변 객체를 다시
        # 직렬화하지 않으며 저장 성공 뒤 RAM도 실제 제출한 사본과 일치시킨다.
        snapshot=json.loads(encoded)
        del encoded
        with self._lock:
            # replace may have succeeded before parent fsync failed. Do not
            # overwrite that higher generation using our old memory snapshot.
            if self.error:raise AuthorityError(self.error)
            try:atomic_json(self.path,snapshot)
            except (OSError,ValueError) as exc:
                self.error='AUTHORITY_STORAGE_UNCERTAIN'
                raise AuthorityError(self.error) from exc
            self.value=snapshot
        return self.value

    def verify(self):
        """Background read: loss or uncoordinated replacement blocks output."""
        with self._lock:
            try:
                current=self.read_current();self.validate(current)
                self.check_marker()
                if current!=self.value:raise ValueError('authority replaced/lost')
            except (OSError,ValueError,KeyError,TypeError,AuthorityError):self.error='AUTHORITY_STORAGE_UNCERTAIN'
        return self.error is None

    def update(self,**fields):
        with self._lock:
            value={**self.value,**fields};return self.save(value)

    def issue(self,mode: Mode,owner: str,reason: str,now: float,timeout: float,*,arm: bool,manual=None,distrust=None):
        if self.error:raise AuthorityError(self.error)
        canonical(owner)
        if mode is not Mode.SAFE_STOP and self.protected(owner):
            raise AuthorityError('target execution remains protected')
        if self.value['generation']==0xffffffff:raise AuthorityError('generation exhausted; no wrap/reset')
        generation=self.value['generation']+1
        fields={}
        if distrust is not None:
            rows=[row for row in self.value.get('distrusted',[]) if row['vm_id']!=distrust['vm_id']]
            fields['distrusted']=[*rows,distrust]
        return self.update(generation=generation,mode=mode.value,owner=owner,reason=reason,phase='PENDING',
            manual_hold=False if mode is Mode.AUTO else self.value.get('manual_hold',False),
            pending={'generation':generation,'host_boot_id':self.host,'deadline_ms':int((now+timeout)*1000),
                     'arm_after_apply':arm,**(manual or {})},requests=[],**fields)

    def consume_request(self,message_id: str,expected_generation: int):
        canonical(message_id)
        if type(expected_generation) is not int or expected_generation!=self.value['generation']:
            return False
        if message_id in self.value['requests'] or len(self.value['requests'])>=128:return False
        self.update(requests=[*self.value['requests'],message_id]);return True

    def transition(self,request_id):
        """Original result only. Reading a record never executes or renews it."""
        from .ipc_contract import request_key
        request_key(request_id)
        with self._lock:
            row=next((r for r in self.value.get('transitions',[]) if r['request_id']==request_id),None)
            if self.error and (row is None or row['phase']!='COMMITTED'):
                raise AuthorityError(self.error)
            return json.loads(json.dumps(row)) if row is not None else None

    def protected(self,vm_id):
        return any(row['phase']=='PROTECTED' and row['spec']['target_vm']==vm_id
                   for row in self.value.get('protections',[]))

    def protected_execution(self,vm_id):
        row=next((row for row in self.value.get('protections',[]) if
                  row['phase']=='PROTECTED' and row['spec']['target_vm']==vm_id),None)
        return None if row is None else row['spec']['target_execution_id']

    def begin_transition(self,request_id,spec,now_ms):
        """Actor entry: persist fixed intent/target/deadline before preparation."""
        from .ipc_contract import request_key
        from .history import candidate,admission,room,stamp
        request_key(request_id)
        with self._lock:
            old=self.transition(request_id)
            if old is not None:
                if old['spec']!=spec:raise AuthorityError('transition request content differs')
                return old
            if (spec['owner']!=self.value['owner'] or spec['generation']!=self.value['generation'] or
                    now_ms>=spec['deadline_ms']):raise AuthorityError('transition context expired/changed')
            fields=candidate(self,now_ms)
            submitted=spec.get('provider_request',spec)
            error=admission(self,request_id,submitted,now_ms,fields)
            if error:raise AuthorityError(error)
            rows=fields.get('transitions',self.value.get('transitions',[]))
            if len(rows)>=TRANSITION_LIMIT:raise AuthorityError('transition history full; no eviction')
            row=dict(request_id=request_id,host_boot_id=self.host,spec=spec,phase='PREPARING',reason=None)
            row=stamp(self,row)
            fields['transitions']=[*rows,row]
            if not room({**self.value,**fields}):raise AuthorityError('HISTORY_BYTES_RESERVED')
            self.update(**fields);return self.transition(request_id)

    def finish_preparation(self,request_id,phase,*,evidence=None,reason=None):
        with self._lock:
            row=self.transition(request_id)
            if row is None:raise AuthorityError('unknown transition request')
            if row['phase'] not in ('PREPARING','PREPARED'):return row
            if phase not in ('PREPARED','CANCELLED','FAILED','ABANDONED'):raise AuthorityError('invalid preparation result')
            row.update(phase=phase,reason=reason)
            if evidence is not None:row['evidence']=evidence
            self.update(transitions=[row if r['request_id']==request_id else r for r in self.value['transitions']])
            return self.transition(request_id)

    def abandon_preparations(self,reason):
        """Restart/cancel invalidates approvals, never the original commit result."""
        with self._lock:
            rows=self.value.get('transitions',[])
            changed=[{**r,'phase':'ABANDONED','reason':reason} if r['phase'] in ('PREPARING','PREPARED') else r for r in rows]
            if changed!=rows:self.update(transitions=changed)

    def commit_transition(self,request_id,now_ms,guard,*,arm,timeout_ms=1000):
        """Conditional single commit. guard is actor-owned, bounded memory-only work.

        Persistence and result are one replace. A failed sync remains uncertain;
        callers must not infer that the old owner still has authority.
        """
        with self._lock:
            if type(timeout_ms) is not int or not 0<timeout_ms<=1000:raise AuthorityError('invalid application budget')
            row=self.transition(request_id)
            if row is None:raise AuthorityError('unknown transition request')
            if row['phase']=='COMMITTED':return row
            if row['phase']!='PREPARED':raise AuthorityError('transition not prepared')
            spec=row['spec']
            if (row['host_boot_id']!=self.host or now_ms>=spec['deadline_ms'] or
                    spec['generation']!=self.value['generation'] or spec['owner']!=self.value['owner'] or not guard(row)):
                self.finish_preparation(request_id,'FAILED',reason='COMMIT_GUARD_REJECTED')
                raise AuthorityError('transition commit guard rejected')
            if self.value['generation']==0xffffffff:raise AuthorityError('generation exhausted; no wrap/reset')
            generation=self.value['generation']+1
            row.update(phase='COMMITTED',committed_generation=generation)
            self.update(generation=generation,mode=spec['mode'],owner=spec['target_vm'],reason='planned_handoff',
                manual_hold=(spec.get('provider_request',{}).get('manual_hold',self.value.get('manual_hold',False))
                             if spec['mode']=='MANUAL' else False),
                phase='PENDING',rearm_required=not arm,requests=[],
                **({'manual_input_profile':'manual-session-1'} if 'manual_session' in spec else {}),
                pending=dict(generation=generation,host_boot_id=self.host,
                    deadline_ms=min(spec['deadline_ms'],now_ms+timeout_ms),arm_after_apply=arm,
                    transition_id=request_id,target=spec['target'],
                    **{k:spec[k] for k in ('manual_session','manual_policy') if k in spec}),
                transitions=[row if r['request_id']==request_id else (
                    {**r,'phase':'ABANDONED','reason':'AUTHORITY_CHANGED'} if r['phase'] in ('PREPARING','PREPARED') else r)
                    for r in self.value['transitions']])
            return self.transition(request_id)

    def payload(self):
        mode=Mode(self.value['mode'])
        payload={'generation':self.value['generation'],'mode':mode.value,'active_app':ACTIVE_APP[mode],
                'active_vm_id':self.value['owner'] if ACTIVE_APP[mode] else None,'reason':self.value['reason']}
        pending=self.value.get('pending') or {}
        if mode in (Mode.MANUAL,Mode.AUTO_READY):
            payload.update({k:pending[k] for k in ('manual_session','manual_policy') if k in pending})
        return payload

    def reconcile(self,payload: dict):
        """An older delivery can be republished from the ledger; conflict cannot issue authority."""
        value=payload.get('generation')
        if type(value) is not int or not 1<=value<=self.value['generation']:
            raise AuthorityError('retained generation conflicts with authority')
        if value==self.value['generation'] and any(payload.get(k)!=self.payload().get(k) for k in
                ('mode','active_app','active_vm_id','manual_session','manual_policy')):
            raise AuthorityError('retained selection conflicts with authority')
