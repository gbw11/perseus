"""Execution-bound public metadata, bounded clock responder and local evidence.

No Manager action, recovery/health decision, console reader or secret export.
"""
from __future__ import annotations
import ctypes as C
import json
import os
from pathlib import Path
import re
import time

def label(value, limit):
    if value is None:return ''
    if (not isinstance(value,str) or len(value.encode('utf-8'))>limit
            or any(ord(c)<32 or 127<=ord(c)<160 for c in value)):
        raise ValueError('invalid instance label')
    return value

def display_label(value):
    # Manager accepts 63 Unicode characters, including four-byte UTF-8 scalars.
    result=label(value,63*4)
    if len(result)>63:raise ValueError('display name exceeds platform character limit')
    return result

def metadata(context, *, runtime=Path('/run/xenova'), data=Path('/srv/xenova')):
    from xenova_vm import storage_check
    storage_check(data)
    path=runtime/'executions'/f"{context['vm_id']}.json"
    run=json.loads(path.read_text())
    if (any(str(run.get(k))!=str(context[k]) for k in ('vm_id','host_boot_id','execution_id','domid','xen_unique_id'))
            or run.get('phase')!='observed'):
        raise ValueError('metadata belongs to another execution')
    loaded=run.get('loaded_artifact')
    if loaded is None:return {} # Pre-upgrade execution has no loader receipt.
    if (loaded.get('file')!='zephyr.bin' or not re.fullmatch('[0-9a-f]{64}',loaded.get('artifact_sha256',''))
            or not re.fullmatch('[0-9a-f]{64}',loaded.get('manifest_sha256',''))):
        raise ValueError('invalid loader receipt')
    build=loaded.get('build_id')
    if build is None:build=''
    return {'display_name':display_label(loaded.get('display_name')),
            'hostname':label(loaded.get('hostname'),63),'build_id':label(build,128),
            'artifact_sha256':loaded['artifact_sha256'], 'persistent_crash':capture_supported()}

def capture_supported():
    import subprocess
    try:
        if subprocess.run(['systemctl','is-enabled','--quiet','xenova-telemetry-evidence.timer'],timeout=1).returncode:
            return False
        pid=int(subprocess.check_output(['systemctl','show','xenconsoled.service','-p','MainPID','--value'],timeout=1))
        if pid<=1:return False
        env=Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        # Read only the allowlisted values internally; never return environ.
        needed=(b'XENOVA_CONSOLE_EVIDENCE=1',b'XENCONSOLED_TRACE=guest',
                b'XENCONSOLED_LOG_DIR=/run/xenova-console-evidence')
        source=Path('/run/xenova-console-evidence')
        return (all(v in env for v in needed) and b'XENOVA_CONSOLE_EVIDENCE' in Path(f'/proc/{pid}/exe').read_bytes()
                and not source.is_symlink() and source.stat().st_uid==0 and not source.stat().st_mode&0o077)
    except (OSError,ValueError,subprocess.SubprocessError):return False

def register_capture(context, receipt_path):
    from xenova_runtime import atomic_write
    run=json.loads((Path('/run/xenova/executions')/(context['vm_id']+'.json')).read_text())
    loaded=run.get('loaded_artifact') or {}
    if run.get('execution_id')!=context['execution_id']:raise ValueError('capture execution changed')
    name=loaded.get('internal_name','')
    if not re.fullmatch('[a-z][a-z0-9-]{0,63}',name):raise ValueError('capture domain name invalid')
    source=Path('/run/xenova-console-evidence')/('guest-'+name+'.log')
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        from xenova_telemetry_evidence import stream_header
        st=os.fstat(stream.fileno());generation,_=stream_header(stream)
    directory=Path('/run/xenova/telemetry-capture');directory.mkdir(mode=0o700,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode&0o077:raise ValueError('private capture registry required')
    path=directory/(context['vm_id']+'.json')
    if path.exists():
        old=json.loads(path.read_text())
        if old['context']==context:return
    atomic_write(path,json.dumps({'context':context,'receipt':str(receipt_path.resolve()),
        'source':str(source),'cursor':{'context':context,'identity':[st.st_dev,st.st_ino],
        'stream_generation':generation,'offset':st.st_size,'bound':False}})+'\n',0o600)

def capture_once():
    from provision_xenova_telemetry import private_json
    from xenova_runtime import atomic_write
    from xenova_telemetry_evidence import capture
    root=Path('/run/xenova/telemetry-capture');handled=0
    for path in sorted(root.glob('*.json'))[:32]:
        config,_=private_json(path);receipt,_=private_json(Path(config['receipt']))
        if receipt['context']!=config['context'] or receipt['phase']!='applied':continue
        current=json.loads((Path('/run/xenova/executions')/(config['context']['vm_id']+'.json')).read_text())
        if current.get('execution_id')!=config['context']['execution_id']:continue
        # Historical execution files keep their original binding after a guest
        # restart. No new receipt is synthesized by this observer.
        archive=Path('/srv/xenova/diagnostics/telemetry')/config['context']['vm_id']/config['context']['execution_id']
        updated=capture(Path(config['source']),archive,receipt,cursor=config['cursor'])
        if updated!=config['cursor']:
            config['cursor']=updated;atomic_write(path,json.dumps(config)+'\n',0o600)
        handled+=1
    return handled

class Timex(C.Structure):
    # Linux ARM64/x86_64 timex ABI. The kernel supplies maxerror/tolerance;
    # this module never changes time or synchronization settings.
    _fields_=[('modes',C.c_uint),('offset',C.c_long),('freq',C.c_long),
        ('maxerror',C.c_long),('esterror',C.c_long),('status',C.c_int),
        ('constant',C.c_long),('precision',C.c_long),('tolerance',C.c_long),
        ('time_sec',C.c_long),('time_usec',C.c_long),('tick',C.c_long),
        ('ppsfreq',C.c_long),('jitter',C.c_long),('shift',C.c_int),
        ('stabil',C.c_long),('jitcnt',C.c_long),('calcnt',C.c_long),
        ('errcnt',C.c_long),('stbcnt',C.c_long),('tai',C.c_int),('padding',C.c_int*11)]

def clock_sample():
    if os.uname().machine!='aarch64' or Path('/sys/devices/system/clocksource/clocksource0/current_clocksource').read_text().strip()!='arch_sys_counter':
        raise ValueError('UTC correspondence requires the fixed ARM architectural clock profile')
    tx=Timex();lib=C.CDLL(None,use_errno=True)
    before=time.monotonic_ns()
    state=lib.adjtimex(C.byref(tx))
    if state<0 or state==5 or tx.status&0x1040 or not 0<=tx.maxerror<=10000000:
        raise ValueError('host UTC is not synchronized within the supported bound')
    utc=time.time_ns();after=time.monotonic_ns()
    return {'unix_ms':utc//1000000,'source_error_ms':(tx.maxerror+999)//1000+(after-before+999999)//1000000,
            'drift_ppm':(abs(tx.tolerance)+65535)//65536,'source':'dom0_adjtimex'}

def clock_response(context, request, sample):
    if (set(request)!={'nonce','execution_id'} or request['execution_id']!=context['execution_id']
            or not re.fullmatch('[0-9]{1,20}',request.get('nonce',''))):
        raise ValueError('clock request context invalid')
    return {**sample,'unix_ms':str(sample['unix_ms']),'nonce':request['nonce'],'host_boot_id':context['host_boot_id'],
            'execution_id':context['execution_id']}

def clock_once():
    from xenova_vm import Platform,storage_check
    from xenova_drive_binding import observe
    from provision_xenova_telemetry import XenStore
    storage_check()
    platform=Platform();end=platform.clock()+6
    store=XenStore();handled=0
    try:
        for record in platform.records(validate_assets=False).values():
            if record.get('guest_kind')!='zephyr' or platform.clock()>=end:continue
            context=observe(platform,record['uuid'],timeout_s=min(1,end-platform.clock()))
            if context['status']!='observed':continue
            parent=f"/local/domain/{context['domid']}/data/xenova-telemetry/clock"
            tx=store.lib.xs_transaction_start(store.handle)
            if not tx:raise OSError('clock transaction unavailable')
            open_tx=True
            try:
                raw=store.read(tx,parent+'/request')
                if not raw:continue
                own=store.read(tx,f"/local/domain/{context['domid']}/vm")
                if own!=('/vm/'+record['uuid']).encode():raise ValueError('clock target changed')
                answer=clock_response(context,json.loads(raw),clock_sample())
                wire=json.dumps(answer,separators=(',',':')).encode()
                if not store.lib.xs_write(store.handle,tx,(parent+'/response').encode(),wire,len(wire)):
                    raise OSError('clock response write failed')
                if platform.clock()>=end:raise TimeoutError('clock response deadline')
                if not store.lib.xs_transaction_end(store.handle,tx,False):raise OSError('clock commit uncertain')
                open_tx=False;handled+=1
            finally:
                if open_tx:store.lib.xs_transaction_end(store.handle,tx,True)
    finally:store.close()
    return handled

def main(argv=None):
    import argparse
    from xenova_vm import storage_check
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('console-environment','clock-once','capture-once','evidence-capture','evidence-list'))
    p.add_argument('--source',type=Path);p.add_argument('--receipt',type=Path)
    p.add_argument('--archive',type=Path);p.add_argument('--cursor',type=Path)
    a=p.parse_args(argv)
    if os.geteuid():p.error('root required')
    try:
        if a.action=='console-environment':
            from xenova_runtime import atomic_write
            binary=Path('/usr/sbin/xenconsoled').read_bytes()
            mode='guest' if b'XENOVA_CONSOLE_EVIDENCE' in binary else 'none'
            atomic_write(Path('/run/xenova-console-evidence/enable.env'),'XENCONSOLED_TRACE='+mode+'\n',0o600)
            return 0
        storage_check()
        if a.action=='clock-once':print(json.dumps({'responses':clock_once()}));return 0
        if a.action=='capture-once':print(json.dumps({'sources':capture_once()}));return 0
        if a.archive is None:p.error('--archive required')
        base=Path('/srv/xenova/diagnostics/telemetry')
        if not a.archive.resolve().is_relative_to(base):p.error('archive must stay in persistent diagnostics')
        from xenova_telemetry_evidence import capture,query
        if a.action=='evidence-list':print(json.dumps(query(a.archive),ensure_ascii=False));return 0
        if a.source is None or a.receipt is None or a.cursor is None:p.error('--source --receipt --cursor required')
        from provision_xenova_telemetry import private_json
        from xenova_runtime import atomic_write
        receipt,_=private_json(a.receipt)
        # Explicit command must still prove the receipt's current incarnation.
        from xenova_drive_binding import observe
        from xenova_vm import Platform
        now=observe(Platform(),receipt['context']['vm_id'])
        if any(str(now.get(k))!=str(v) for k,v in receipt['context'].items()):raise ValueError('capture execution changed')
        cursor=private_json(a.cursor)[0] if a.cursor.exists() else None
        position=capture(a.source,a.archive,receipt,cursor=cursor)
        atomic_write(a.cursor,json.dumps(position)+'\n',0o600)
        print(json.dumps(position));return 0
    except (OSError,ValueError,KeyError) as error:
        # No raw request, environment or credential values in public diagnostics.
        print(json.dumps({'ok':False,'error':type(error).__name__,'errno':getattr(error,'errno',None),
            'stage':a.action}),file=__import__('sys').stderr);return 1
