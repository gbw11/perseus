#!/usr/bin/env python3
"""실제 systemd/agetty 격리 수락. 소유 transient unit·PTY·임시 경로만 사용한다.

root로 실행한다. agetty 시험은 private mount namespace의 /run에서 동작한다.
제품 unit/VT/로그인/계정/VM은 변경하지 않는다. 모형 Xen 성공을 주장하지 않는다.
"""
import argparse
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
sys.path.insert(0, str(MODULES))
import xenova_boot_status as boot


def call(argv, allow=(0,)):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=12)
    if p.returncode not in allow:
        raise RuntimeError(str(argv[:4])+': '+str(p.returncode)+' '+p.stderr[-300:])
    return p


def pty_test(out):
    """생산 agetty가 issue 갱신을 입력 전까지만 처리하는지 실제 PTY에서 확인한다."""
    assert os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt')
    call(['mount', '-t', 'tmpfs', 'tmpfs', '/run'])
    call(['mount', '-t', 'tmpfs', 'tmpfs', '/var/log'])
    issue = out/'pty.issue'
    issue.write_text('XENOVA_INITIAL\n')
    login = out/'login-boundary.py'
    login.write_text('#!/usr/bin/python3\nimport sys\nprint("PASSWORD_BOUNDARY",flush=True)\ninput()\nprint("SESSION_BOUNDARY",flush=True)\n')
    login.chmod(0o700)
    pid, master = pty.fork()
    if pid == 0:
        os.execvp('agetty', ['agetty', '-L', '-8', '--noclear', '--nohostname',
                            '--issue-file', str(issue), '--login-program', str(login), '-', '115200', 'vt100'])
    class Child:
        result = None
        def poll(self):
            if self.result is None:
                found, state = os.waitpid(pid, os.WNOHANG)
                if found:
                    self.result = os.waitstatus_to_exitcode(state)
            return self.result
        def wait(self, timeout):
            end = time.monotonic()+timeout
            while self.poll() is None and time.monotonic()<end:
                time.sleep(.02)
            assert self.result is not None, 'owned agetty did not exit'
            return self.result
        @property
        def returncode(self):
            return self.result
    child = Child()
    transcript = bytearray()
    def receive(marker=None, timeout=4):
        new = bytearray()
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            ready,_,_ = select.select([master],[],[],max(0,end-time.monotonic()))
            if ready:
                try:
                    chunk = os.read(master,8192)
                except OSError:
                    break
                new.extend(chunk)
                if marker and marker in new:
                    break
        transcript.extend(new)
        if marker:
            assert marker in new, (marker,bytes(new),child.poll())
        return bytes(new)
    def reload(text):
        issue.write_text(text+'\n')
        call(['agetty','--reload'])
    try:
        receive(b'login:')
        reload('XENOVA_DELAYED')
        receive(b'XENOVA_DELAYED')
        os.write(master,b'o')
        receive(b'o')
        reload('MUST_NOT_OVERWRITE_USERNAME')
        assert b'MUST_NOT_OVERWRITE' not in receive(timeout=.3)
        os.write(master,b'perator\n')
        receive(b'PASSWORD_BOUNDARY')
        reload('MUST_NOT_OVERWRITE_PASSWORD')
        assert b'MUST_NOT_OVERWRITE' not in receive(timeout=.3)
        os.write(master,b'fixture-only\n')
        receive(b'SESSION_BOUNDARY')
        child.wait(timeout=4)
        assert child.returncode == 0
        (out/'agetty-public.log').write_bytes(bytes(transcript).replace(b'fixture-only',b'<test-input>'))
        return {'prompt_reload':True,'username_not_overwritten':True,'password_boundary_not_overwritten':True,
                'session_boundary_reached':True,'physical_hdmi':False,'real_authentication_tested':False}
    finally:
        if child.poll() is None:
            os.kill(pid,signal.SIGTERM)
            child.wait(timeout=4)
        os.close(master)


def systemd_test(out):
    token = 'xenova-boot-probe-'+uuid.uuid4().hex[:12]
    names = [token+'-'+v+'.service' for v in ('work','display','login')]
    worker,indicator,login = names
    runtime = out/'runtime'
    producer = out/'producer.py'
    producer.write_text('import sys,time\nsys.path.insert(0,'+repr(str(MODULES))+')\n'
                        'from xenova_boot_progress import Progress\n'
                        'from pathlib import Path\np=Progress("registration",Path('+repr(str(runtime))+'))\n'
                        'p.emit("asset-verification",mode="existing-install-verification")\n'
                        'time.sleep(.4)\nraise SystemExit(7)\n')
    renderer = out/'renderer.py'
    renderer.write_text('import sys,time\nsys.path.insert(0,'+repr(str(MODULES))+')\n'
                        'from pathlib import Path\nimport xenova_boot_status as b\n'
                        'b.STAGES=(("registration","Registration",'+repr(worker)+'),)\n'
                        'for i in range(15):\n b.refresh(Path('+repr(str(runtime))+'),Path('+repr(str(out/'unit.issue'))+'),Path('+repr(str(out/'reload'))+'))\n time.sleep(.2)\n'
                        'raise SystemExit(9)\n')
    evidence = {}
    try:
        call(['systemd-run','--unit',login,'--property=Type=simple','/usr/bin/sleep','60'])
        call(['systemd-run','--no-block','--unit',worker,'--property=Type=oneshot','--property=RemainAfterExit=yes',sys.executable,str(producer)])
        call(['systemd-run','--unit',indicator,'--property=Type=simple','--property=DefaultDependencies=no',sys.executable,str(renderer)])
        end = time.monotonic()+8
        while time.monotonic()<end:
            state,_=boot.systemd_units([indicator])
            if state.get(indicator,{}).get('ActiveState')=='failed':
                break
            time.sleep(.2)
        else:
            raise AssertionError('indicator fixture did not reach its explicit failure')
        state,error=boot.systemd_units(names)
        assert error is None
        assert state[worker]['Result']=='exit-code'
        assert state[indicator]['Result']=='exit-code'
        assert state[login]['ActiveState']=='active'
        snap,_=boot.read_json(runtime/'boot-display.json')
        assert snap['stages'][0]['state']=='failed'
        assert snap['failure_history'][0]['unit']==worker
        assert 'journalctl -b -u '+worker in (out/'unit.issue').read_text()
        evidence['failure_and_login_independence']=state
        call(['systemctl','reset-failed',indicator])
        call(['systemd-run','--unit',indicator,'--property=Type=simple','--property=DefaultDependencies=no',sys.executable,str(renderer)])
        time.sleep(.3)
        call(['systemctl','stop',indicator])
        state,_=boot.systemd_units([login,worker])
        assert state[login]['ActiveState']=='active'
        assert state[worker]['ActiveState']=='failed'
        evidence['indicator_stop_restart_did_not_change_other_units']=True
        # 같은 boot의 서비스 재시도는 새 invocation으로 이전 기록을 거절해야 한다.
        before=state[worker]['InvocationID']
        call(['systemctl','reset-failed',worker])
        call(['systemd-run','--no-block','--unit',worker,'--property=Type=oneshot','--property=RemainAfterExit=yes',sys.executable,str(producer)])
        end=time.monotonic()+3
        while time.monotonic()<end:
            state,_=boot.systemd_units([worker])
            if state[worker]['InvocationID']!=before:
                break
            time.sleep(.05)
        assert state[worker]['InvocationID']!=before
        evidence['retry_new_invocation']=True
        return evidence
    finally:
        call(['systemctl','stop',*names],allow=(0,5))
        call(['systemctl','reset-failed',*names],allow=(0,1))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pty-child',action='store_true')
    args=p.parse_args()
    assert os.getuid()==0,'isolated systemd/mount test needs root'
    if args.pty_child:
        (args.output/'pty-result.json').write_text(json.dumps(pty_test(args.output),indent=2)+'\n')
        return
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    result={'systemd_version':call(['systemctl','--version']).stdout.splitlines()[0],
            'agetty_version':call(['agetty','--version']).stdout.strip(),
            'systemd':systemd_test(args.output)}
    call(['unshare','--mount','--propagation','private',sys.executable,str(Path(__file__).resolve()),
          '--output',str(args.output),'--pty-child'])
    result['pty']=json.loads((args.output/'pty-result.json').read_text())
    (args.output/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS actual systemd failure/indicator independence/retry and actual agetty PTY input/handoff')


if __name__=='__main__':
    main()
