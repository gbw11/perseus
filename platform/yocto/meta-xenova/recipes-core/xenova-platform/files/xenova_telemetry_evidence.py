"""Bounded, durable execution evidence from the existing console owner's log.

Never opens a PV console, captures arbitrary text, interprets a lost VM as a
crash, or turns archive queries into VM actions. Missing capture is explicit.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
import errno
import fcntl
from contextlib import contextmanager
from xenova_runtime import atomic_write

LIMIT=16*1024
RETAIN=128
ARCHIVE_ROOT=Path('/srv/xenova/diagnostics/telemetry')

def stream_header(stream):
    """Return the filtered scratch generation; legacy files have no marker."""
    stream.seek(0)
    first=stream.readline(64)
    match=re.fullmatch(rb'XDIAG_STREAM ([0-9]{1,20})\n',first)
    if match and 0<int(match[1])<=2**64-1:return match[1].decode(),len(first)
    if first.startswith(b'XDIAG_STREAM '):raise BlockingIOError(errno.EAGAIN,'stream header incomplete')
    return None,0

@contextmanager
def retention_lock(root):
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=root.lstat()
    if root.is_symlink() or st.st_uid!=os.geteuid() or st.st_mode&0o077:
        raise ValueError('private owned evidence namespace required')
    fd=os.open(root/'.retention.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)

def _committed(root):
    rows=[]
    for p in root.rglob('*.json'):
        if p.name=='retention.json' or p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):continue
        try:record=json.loads(p.read_text())
        except (ValueError,OSError):continue
        if (record.get('schema_version')!='1.0-evidence' or not isinstance(record.get('events'),list)
                or not isinstance(record.get('context'),dict) or 'end_offset' not in record):continue
        try:
            for key in ('vm_id','host_boot_id','execution_id'):uuid.UUID(record['context'][key])
        except (ValueError,KeyError,TypeError):continue
        if p.stat().st_uid==os.geteuid() and not p.stat().st_mode&0o077:rows.append((p,record))
    return sorted(rows,key=lambda item:(item[0].stat().st_mtime_ns,str(item[0])))

def _prune(root, archive, reserve_bytes, *, max_bytes=64*1024*1024,max_records=4096,per_execution=RETAIN):
    """Caller holds the dedicated lock. Planned and confirmed removals differ;
    uncertain/partial files and unrelated formats are never deletion candidates."""
    journal=root/'retention.json'
    if reserve_bytes>max_bytes:raise OSError(errno.EFBIG,'one evidence record exceeds capacity')
    if journal.is_symlink():raise ValueError('retention journal must not be linked')
    state=json.loads(journal.read_text()) if journal.exists() else {
        'schema_version':'1.0-evidence-retention','removed_total':0,'recent':[],'pending':[]}
    if state.get('schema_version')!='1.0-evidence-retention':raise ValueError('invalid retention journal')
    while True:
        if not state['pending']:
            all_files=[p for p in root.rglob('*.json') if p.name!='retention.json' and not p.is_symlink()]
            size=sum(p.stat().st_size for p in all_files);count=len(all_files)
            local=sum(p.parent==archive for p in all_files)
            wanted=1 if reserve_bytes else 0
            if size+reserve_bytes<=max_bytes and count+wanted<=max_records and local+wanted<=per_execution:return
            selected=[]
            for path,record in _committed(root):
                if size+reserve_bytes<=max_bytes and count+wanted<=max_records and local+wanted<=per_execution:break
                if size+reserve_bytes<=max_bytes and count+wanted<=max_records and path.parent!=archive:continue
                selected.append({'path':str(path.relative_to(root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'bytes':path.stat().st_size,'context':record['context'],'reason':'retention_limit',
                    'capture_complete':record.get('capture_complete'),'events':len(record['events'])})
                size-=path.stat().st_size;count-=1;local-=path.parent==archive
                if len(selected)==128:break
            if not selected:raise OSError(errno.ENOSPC,'only incomplete/unowned evidence remains above capacity')
            state['pending']=selected
            atomic_write(journal,json.dumps(state)+'\n',0o600)
        results=[]
        for row in state['pending']:
            path=root/row['path']
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('retention path escaped namespace')
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('planned evidence changed; no deletion')
                path.unlink()
                fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
                row={**row,'outcome':'removed'};state['removed_total']+=1
            else:row={**row,'outcome':'absent_after_plan'}
            results.append(row)
        state['recent']=(state['recent']+results)[-128:];state['pending']=[]
        atomic_write(journal,json.dumps(state)+'\n',0o600)

def parse_line(line):
    # Only structural numeric facts/validated stage labels survive. Arbitrary
    # console output, registers, command lines and credentials are never archived.
    fatal=re.fullmatch(rb'XDIAG_FATAL ([0-9]{1,4})',line)
    if fatal:return {'category':'fatal_observed','code':int(fatal[1]),'stage':'zephyr-fatal',
                     'occurred_uptime_ms':None,'clock_domain':'unconfirmed'}
    fact=re.fullmatch(rb'XDIAG_FACT ([0-9]{1,20}) ([0-9]{1,20}) (-[0-9]{1,10})',line)
    if fact:return {'category':'error_observed','code':int(fact[3]),'stage':'diagnostics-core',
        'event_seq':int(fact[1]),'occurred_uptime_ms':int(fact[2]),'clock_domain':'guest_uptime_ms'}
    prefix=b'XDIAG_EVENT '
    pos=line.find(prefix)
    if pos<0:return None
    try:row=json.loads(line[pos+len(prefix):])
    except (ValueError,UnicodeError):return None
    if (set(row)!={'seq','uptime_ms','code','stage_hex','boot'} or type(row['code']) is not int
            or not -2147483648<=row['code']<0 or type(row['seq']) is not int or row['seq']<1
            or type(row['uptime_ms']) is not int or row['uptime_ms']<0
            or type(row['boot']) is not bool or not re.fullmatch('[0-9a-f]{2,62}',row['stage_hex'])):
        return None
    # Stages are producer-supplied, so retain only a digest rather than arbitrary
    # text in the persistent archive. The current guest RAM history has labels.
    return {'category':'error_observed','code':row['code'],'stage':'sha256:'+hashlib.sha256(bytes.fromhex(row['stage_hex'])).hexdigest(),
            'event_seq':row['seq'],'occurred_uptime_ms':row['uptime_ms'],'clock_domain':'guest_uptime_ms'}

def capture(source, archive, receipt, *, cursor=None, storage_guard=None):
    if storage_guard is None:
        from xenova_vm import storage_check
        storage_check()
    else:storage_guard()
    if receipt.get('phase')!='applied':raise ValueError('applied execution receipt required')
    context=receipt['context']
    for key in ('vm_id','host_boot_id','execution_id'):uuid.UUID(context[key])
    archive=Path(archive);archive.mkdir(mode=0o700,parents=True,exist_ok=True)
    if archive.is_symlink() or archive.stat().st_mode&0o077:raise ValueError('private archive required')
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if info.st_uid!=os.geteuid():raise ValueError('console source owner differs')
        identity=[info.st_dev,info.st_ino]
        generation,header_end=stream_header(stream)
        offset=(cursor or {}).get('offset',info.st_size) # first attach never assigns old text to a new execution
        same_context=bool(cursor and cursor.get('context')==context)
        rotated=bool(same_context and generation is not None and generation!=cursor.get('stream_generation'))
        continuity=bool(same_context and cursor.get('identity')==identity and offset<=info.st_size and not rotated)
        # First delayed creation starts at the header, but remains unbound until
        # this receipt's explicit execution/boot marker is read.
        pending=bool(same_context and cursor.get('pending_source') and cursor.get('identity') is None)
        if not continuity:offset=header_end if rotated or pending else info.st_size
        stream.seek(offset);raw=stream.read(LIMIT)
        after_generation,_=stream_header(stream)
        if after_generation!=generation or os.fstat(stream.fileno()).st_size<offset+len(raw):
            raise BlockingIOError(errno.EAGAIN,'filtered stream rotated during capture')
        # Partial final lines are not accepted or skipped permanently.
        last=raw.rfind(b'\n');complete=raw[:last+1] if last>=0 else b''
        bound=bool(continuity and cursor.get('bound'));events=[]
        for line in complete.splitlines():
            marker=re.search(rb'XDIAG_BIND ([0-9a-f-]{36}) ([0-9a-f-]{36})',line)
            if marker:
                bound=(marker[1].decode()==context['execution_id'] and marker[2].decode()==receipt.get('boot_id'))
            elif bound and (fact:=parse_line(line)) is not None:events.append(fact)
        result={'schema_version':'1.0-evidence','context':context,'guest_boot_id':receipt.get('boot_id'),
            'firmware':receipt.get('metadata',{}),'collected_unix_ns':time.time_ns(),
            'source_identity':identity,'start_offset':offset,'end_offset':offset+len(complete),
            'stream_generation':generation,
            'continuity':'observed' if continuity else 'rotation_gap' if rotated else 'attachment_gap',
            'capture_complete':offset+len(complete)==info.st_size,
            'capture_scope':'filtered_available_bytes_only','source_loss':'not_quantified',
            'source_limit_near':info.st_size>=256*1024-256,
            'events':events}
    position={'identity':identity,'stream_generation':generation,'offset':result['end_offset'],'context':context,'bound':bound,'durable':False}
    if not events and continuity:return position
    key=[context,identity,generation,result['start_offset'],result['end_offset']]
    path=archive/(hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()+'.json')
    root=ARCHIVE_ROOT if archive.resolve().is_relative_to(ARCHIVE_ROOT) else archive
    with retention_lock(root):
        if path.exists():
            saved=json.loads(path.read_text())
            if any(saved.get(k)!=result[k] for k in ('context','events','source_identity','start_offset','end_offset')):
                raise ValueError('existing evidence conflicts; no overwrite')
        else:
            wire=json.dumps(result,ensure_ascii=False)+'\n'
            _prune(root,archive,len(wire.encode()))
            atomic_write(path,wire,0o600)
    return {**position,'last_record':path.name,'durable':True}

def query(archive):
    result=[]
    for path in sorted(Path(archive).glob('*.json')):
        try:value=json.loads(path.read_text())
        except (ValueError,OSError):result.append({'file':path.name,'status':'incomplete'});continue
        result.append({'file':path.name,'status':'stored','record':value})
    return result
