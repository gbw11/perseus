#!/usr/bin/env python3
"""실제 PID1 systemd에서 batch/지속 프로세스 경계를 시험한다. Xen을 실행하지 않는다.

root로 명시 실행하는 격리 시험이다. run별 unit·scope·임시 디렉터리만 만들고 정리한다.
생산 코드의 scope 구성/PDEATHSIG를 사용하며 실제 Xen VM 보존 수락은 별도다.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'))
sys.path.insert(0,str(ROOT/'platform/first-install'))
from xenova_guest_process import exec_owned, scope_command, scope_name


def run(*argv,check=True):
    return subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          timeout=20,check=check)


def wait(check,timeout=8):
    end=time.monotonic()+timeout
    while not check():
        if time.monotonic()>=end:raise AssertionError('condition deadline exceeded')
        time.sleep(.02)


def alive(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split()[2]!='Z'
    except FileNotFoundError:return False


def process_state(pid):
    status=Path(f'/proc/{pid}/status').read_text().splitlines()
    return {'pid':pid,'ppid':next(s.split(':',1)[1].strip() for s in status if s.startswith('PPid:')),
            'cgroup':Path(f'/proc/{pid}/cgroup').read_text()}


def note(base,role):
    status=Path('/proc/self/status').read_text().splitlines()
    (base/(role+'.json')).write_text(json.dumps({'pid':os.getpid(),'ppid':os.getppid(),
        'status':{s.split(':',1)[0]:s.split(':',1)[1].strip() for s in status if s.startswith(('Name:','PPid:'))},
        'cgroup':Path('/proc/self/cgroup').read_text()}))


@contextmanager
def guard():yield


def child(args):
    base=Path(args.base)
    if args.role=='launch':
        exec_owned([sys.executable,__file__,'child','--base',str(base),'--role','xl'],args.parent,guard)
    elif args.role=='xl':
        for role in ('monitor','backend'):
            if os.fork()==0:
                signal.signal(signal.SIGTERM,signal.SIG_IGN)
                note(base,role)
                while True:signal.pause()
        wait(lambda:all((base/(role+'.json')).exists() for role in ('monitor','backend')))
        note(base,'xl')
    elif args.role=='temporary':
        signal.signal(signal.SIGTERM,signal.SIG_IGN);note(base,'temporary')
        while True:signal.pause()
    elif args.role=='pending':
        # scope 안의 미완료 명령은 Manager 사망 시 SIGKILL, fork된 지속 자식은 생존한다.
        from xenova_guest_process import arm_parent_death
        arm_parent_death(args.parent)
        signal.signal(signal.SIGTERM,signal.SIG_IGN);note(base,'pending')
        while True:signal.pause()
    elif args.role=='shutdown':
        scopes=json.loads((base/'scopes.json').read_text())
        with (base/'shutdown.jsonl').open('a') as out:
            out.write(json.dumps({'event':'guest-cleanup','monotonic_ns':time.monotonic_ns(),
                'alive_before':{role:alive(json.loads((base/(role+'.json')).read_text())['pid'])
                                for role in ('monitor','backend') if (base/(role+'.json')).exists()}})+'\n')
        (base/'fake-domain').unlink(missing_ok=True)
        for name in scopes:run('systemctl','kill','--signal=KILL','--kill-whom=all',name,check=False)
    elif args.role=='batch':
        script=[sys.executable,__file__,'child','--base',str(base)]
        if not (base/'fake-domain').exists():
            argv=scope_command(args.scope,script+['--role','launch','--parent',str(os.getpid())],shutdown_unit=args.guard)
            result=subprocess.run(argv,timeout=10)
            if result.returncode:raise RuntimeError('fake xl launch failed')
            (base/'fake-domain').write_text('one owned dummy domain, not Xen\n')
            (base/'creates').write_text(str(int((base/'creates').read_text())+1) if (base/'creates').exists() else '1')
        process=subprocess.Popen(script+['--role','temporary'])
        wait(lambda:(base/'temporary.json').exists())
        if args.case=='outer-timeout':
            pending=subprocess.Popen(scope_command(args.pending_scope,script+['--role','pending','--parent',str(os.getpid())],shutdown_unit=args.guard))
            wait(lambda:(base/'pending.json').exists())
            # 최종 SIGKILL을 실제 관측한다. 생산 unit의 KillMode는 변경하지 않는다.
            signal.signal(signal.SIGTERM,signal.SIG_IGN)
            while True:signal.pause()
        if args.case=='retry':
            process.kill();process.wait();return
        (base/'batch-result.json').write_text(json.dumps({'success':['first'],'failed':['second'] if args.case=='explicit-failure' else [],
            'not_started':['third'],'deadline_expired':args.case=='internal-deadline'}))
        raise SystemExit(7 if args.case=='explicit-failure' else 124)


def main(output):
    assert os.geteuid()==0 and Path('/proc/1/comm').read_text().strip()=='systemd'
    prefix='xenova-boundary-test-'+uuid.uuid4().hex
    results={'systemd':run('systemctl','--version').stdout.splitlines()[0],'pid1':Path('/proc/1/comm').read_text().strip(),'cases':[]}
    with tempfile.TemporaryDirectory(prefix=prefix+'-',dir='/tmp') as temporary:
        base_root=Path(temporary);units=[];scope_units=[]
        unit_dir=Path('/run/systemd/system').resolve()
        try:
            for index,case in enumerate(('explicit-failure','internal-deadline','outer-timeout')):
                base=base_root/str(index);base.mkdir()
                batch=prefix+f'-{index}.service';shutdown=prefix+f'-{index}-shutdown.service'
                scope=scope_name(str(uuid.uuid4()),str(uuid.uuid4()))
                pending_scope=scope_name(str(uuid.uuid4()),str(uuid.uuid4()))
                scope_units.extend((scope,pending_scope));(base/'scopes.json').write_text(json.dumps([scope,pending_scope]))
                cmd=f'{sys.executable} {__file__} child --base {base}'
                batch_path=unit_dir/batch;shutdown_path=unit_dir/shutdown
                for p in (batch_path,shutdown_path):assert p.parent==unit_dir and not p.exists();units.append(p)
                batch_path.write_text('[Unit]\nRequires='+shutdown+'\nAfter='+shutdown+'\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart='+cmd+f' --role batch --case {case} --scope {scope} --pending-scope {pending_scope} --guard {shutdown}\nExecStop='+cmd+' --role shutdown\nTimeoutStartSec=2s\nTimeoutStopSec=1s\nRestart=no\n')
                shutdown_path.write_text('[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\nExecStop='+cmd+' --role shutdown\nTimeoutStopSec=3s\n')
                run('systemctl','daemon-reload')
                started=run('systemctl','start',batch,check=False)
                assert started.returncode!=0
                pids={role:json.loads((base/(role+'.json')).read_text()) for role in ('monitor','backend','temporary')}
                wait(lambda:not alive(pids['temporary']['pid']))
                assert all(alive(pids[role]['pid']) for role in ('monitor','backend'))
                assert not (base/'shutdown.jsonl').exists(),'failed oneshot must not run broad ExecStop'
                assert not any(batch in row['cgroup'] for role,row in pids.items() if role!='temporary')
                assert batch in pids['temporary']['cgroup']
                before=run('systemctl','show',scope,'-p','Before','--value').stdout.split()
                assert shutdown in before,'scope must stop after the independent guest cleanup guard'
                if case=='outer-timeout':
                    pending=json.loads((base/'pending.json').read_text());wait(lambda:not alive(pending['pid']))
                    pids['pending']=pending
                record={'case':case,'start_rc':started.returncode,'processes':pids,
                        'persistent_after_failure':{role:process_state(pids[role]['pid']) for role in ('monitor','backend')},
                        'scope_properties':run('systemctl','show',scope,'-p','Before','-p','After','-p','ControlGroup','-p','ActiveState','-p','KillMode','-p','PartOf','-p','BindsTo').stdout,
                        'guard_properties':run('systemctl','show',shutdown,'-p','ExecStop','-p','ExecStopPost','-p','Before','-p','After','-p','Type','-p','RemainAfterExit','-p','KillMode').stdout,
                        'properties':run('systemctl','show',batch,'-p','Result','-p','KillMode','-p','Type','-p','RemainAfterExit','-p','Restart','-p','TimeoutStartUSec','-p','TimeoutStopUSec').stdout,
                        'journal':run('journalctl','-u',batch,'--no-pager','-n','45').stdout,
                        'successful_persistent_survived':True,'temporary_final_kill_observed':True,'fake_domain_present':(base/'fake-domain').exists()}
                if case=='explicit-failure':
                    # 같은 unit 재시도에서 기존 fake domain을 쓰고 create 횟수를 보존한다.
                    batch_path.write_text(batch_path.read_text().replace('--case explicit-failure','--case retry'))
                    (base/'temporary.json').unlink()
                    run('systemctl','daemon-reload');run('systemctl','start',batch)
                    assert (base/'creates').read_text()=='1'
                    assert all(alive(pids[role]['pid']) for role in ('monitor','backend'))
                    run('systemctl','stop',batch)
                    assert not (base/'fake-domain').exists()
                    record['retry_no_duplicate_create']=True;record['explicit_stop_cleanup']=True
                else:
                    # host 종료와 같은 역순: guard의 guest 정리 -> scope 정리.
                    run('systemctl','stop',shutdown,scope,pending_scope,check=False)
                    assert not (base/'fake-domain').exists()
                    event=json.loads((base/'shutdown.jsonl').read_text().splitlines()[0])
                    assert all(event['alive_before'].values()),event
                    record['shutdown_guard_precedes_scope_cleanup']=True
                wait(lambda:all(not alive(pids[role]['pid']) for role in ('monitor','backend')))
                record['final_cleanup_observed']=True
                results['cases'].append(record)
        finally:
            # 생성한 정확한 unit/scope만 정리한다. PID1/기존 unit·도메인에 손대지 않는다.
            for name in [p.name for p in units]+scope_units:run('systemctl','stop',name,check=False)
            for p in units:
                assert p.parent==unit_dir and p.name.startswith(prefix)
                p.unlink(missing_ok=True)
            run('systemctl','daemon-reload')
            for p in units:run('systemctl','reset-failed',p.name,check=False)
            output.write_text(json.dumps(results,indent=2))
    assert len(results['cases'])==3
    print(json.dumps({'cases':3,'retry':True,'explicit_stop':True,'host_cleanup_order':True,'actual_xen':False,'output':str(output)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    c=sub.add_parser('child');c.add_argument('--base',required=True);c.add_argument('--role',required=True)
    c.add_argument('--parent',type=int);c.add_argument('--case');c.add_argument('--scope');c.add_argument('--pending-scope');c.add_argument('--guard')
    r=sub.add_parser('run');r.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    if a.mode=='child':child(a)
    else:main(a.output)
