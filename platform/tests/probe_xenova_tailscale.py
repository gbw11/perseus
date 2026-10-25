#!/usr/bin/env python3
"""실제 ARM64 binary·systemd·격리 TUN과 소유 ext4에서 미인증 state 보존/복원을 검증한다."""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'platform/yocto/meta-xenova/recipes-connectivity/tailscale/files/xenova-tailscale-state'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--cli-binary',type=Path,help='separate official CLI; omit only for the merged recipe binary')
    p.add_argument('--sysroot',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    assert os.geteuid()==0,'root required for owned ext4 and isolated systemd unit'
    out=args.output.resolve();assert not out.exists()
    assert not any(c.isspace() for c in str(out))
    out.mkdir(parents=True,mode=0o700);os.umask(0o077)
    report={'location':'PC WSL, isolated systemd/network namespace and owned loop ext4',
        'authenticated_tailnet':False,'physical_board':False,'commands':[]}
    unit='xenova-tailscale-test-'+secrets.token_hex(4)+'.service'
    buddy=unit.replace('test-','buddy-')
    unitfile=Path('/run/systemd/system')/unit
    assert not unitfile.exists()
    devices=[];mounted=False;child=None
    data=out/'data';data.mkdir()
    device=out/'data-device'
    serial=out/'board-serial';serial.write_bytes(b'1000000012345678\x00')
    qemu=shutil.which('qemu-aarch64-static');assert qemu
    binary=out/'tailscaled';shutil.copyfile(args.binary,binary);binary.chmod(0o700)
    cli=out/'tailscale'
    if args.cli_binary:
        shutil.copyfile(args.cli_binary,cli);cli.chmod(0o700)
    else:cli.symlink_to('tailscaled')
    daemon=(qemu,'-L',str(args.sysroot),str(binary))
    client=[qemu,'-L',str(args.sysroot),str(cli),'--socket='+str(out/'tailscaled.sock')]
    loader=importlib.machinery.SourceFileLoader('tailscale_state_probe',str(SOURCE))
    spec=importlib.util.spec_from_loader(loader.name,loader);state=importlib.util.module_from_spec(spec);loader.exec_module(state)

    def run(argv,*,check=True,timeout=30,input=None):
        start=time.monotonic();result=subprocess.run(list(map(str,argv)),input=input,capture_output=True,timeout=timeout)
        report['commands'].append({'argv':list(map(str,argv)),'rc':result.returncode,'elapsed_s':round(time.monotonic()-start,3)})
        if check and result.returncode:raise RuntimeError('isolated test command failed: '+Path(str(argv[0])).name+' rc='+str(result.returncode))
        return result
    def properties(name):
        result=run(['systemctl','show',name,'--no-pager','--property=ActiveState,Result,MainPID,Job'])
        return dict(row.split('=',1) for row in result.stdout.decode().splitlines() if '=' in row)
    def guard():
        current=properties(unit)
        if current.get('ActiveState') not in ('inactive','failed') or current.get('Job')!='':
            raise state.StateError('owned daemon must be stopped')
    def attach(label):
        image=out/(label+'.ext4')
        with image.open('xb') as f:f.truncate(64*1024*1024)
        run(['mkfs.ext4','-q','-F',image])  # regular owned file, never an SD/block device
        value=run(['losetup','--find','--show',image]).stdout.decode().strip()
        assert value.startswith('/dev/loop');devices.append(value)
        return value
    def status():
        result=run(client+['status','--json'],check=False,timeout=15)
        parsed=json.loads(result.stdout)
        return {'backend_state':parsed.get('BackendState')}
    def crypt(raw,*,decrypt=False,passphrase):
        fd_r,fd_w=os.pipe()
        try:
            os.write(fd_w,passphrase+b'\n');os.close(fd_w);fd_w=-1
            argv=['gpg','--no-options','--homedir',str(out/'gnupg'),'--batch','--pinentry-mode','loopback',
                  '--no-symkey-cache','--passphrase-fd',str(fd_r)]
            argv+=['--decrypt'] if decrypt else ['--cipher-algo','AES256','--symmetric']
            result=subprocess.run(argv,input=raw,capture_output=True,pass_fds=(fd_r,),timeout=30)
            report['commands'].append({'argv':argv,'rc':result.returncode,'private_output_saved':False})
            return result
        finally:
            os.close(fd_r)
            if fd_w!=-1:os.close(fd_w)
    try:
        assert Path('/dev/net/tun').is_char_device(),'native TUN unavailable; do not substitute userspace success'
        report['arm_version']=run([*daemon,'--version']).stdout.decode().strip()
        report['cli_version']=run([*client,'version']).stdout.decode().strip()
        report['binary_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
        dev=attach('before-reinstall');device.symlink_to(dev)
        try:state.prepare(data,device=device)
        except (OSError,state.StateError):pass
        else:raise AssertionError('missing mount accepted')
        assert not (data/'tailscale').exists();report['no_rootfs_fallback']=True
        run(['mount','-t','ext4','-o','rw,nodev,nosuid,noexec',dev,data]);mounted=True
        before_uuid=run(['blkid','-s','UUID','-o','value',dev]).stdout.decode().strip()
        preflight=out/'preflight.py'
        preflight.write_text('import importlib.machinery,importlib.util,sys\nfrom pathlib import Path\n'
            f'loader=importlib.machinery.SourceFileLoader("guard",{str(SOURCE)!r})\n'
            's=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(s);loader.exec_module(m)\n'
            f'kw={{"data":Path({str(data)!r}),"device":Path({str(device)!r})}}\n'
            f'm.cleanup(daemon={daemon!r},**kw) if sys.argv[1]=="cleanup" else m.prepare(**kw)\n')
        unitfile.write_text('[Unit]\nDescription=Owned Xenova Tailscale acceptance probe\n'
            '[Service]\nType=notify\nPrivateNetwork=yes\nUMask=0077\nRestart=no\nTimeoutStartSec=20s\nTimeoutStopSec=20s\n'
            'MemoryMax=512M\nCPUQuota=100%\n'
            f'ExecStartPre=/usr/bin/python3 -B {preflight} prepare\n'
            f'ExecStart={" ".join(daemon)} --state={data}/tailscale/tailscaled.state --statedir={data}/tailscale '
            f'--socket={out}/tailscaled.sock --port=0 --no-logs-no-support\n'
            f'ExecStopPost=/usr/bin/python3 -B {preflight} cleanup\n')
        run(['systemctl','daemon-reload'])
        run(['systemd-run','--unit='+buddy,'--property=Type=simple','/usr/bin/sleep','600'])
        buddy_before=properties(buddy)
        run(['systemctl','start',unit]);report['first_install']=status()
        assert report['first_install']['backend_state']=='NeedsLogin'
        pid=properties(unit)['MainPID']
        tun=run(['nsenter','--target',pid,'--net','ip','-details','-json','link','show','tailscale0'],check=False)
        report['observed_tun_interface']=tun.returncode==0
        if tun.returncode==0:
            report['tun_interface_name']=json.loads(tun.stdout)[0]['ifname']
        run(['systemctl','restart',unit]);report['restart']=status()
        assert report['restart']['backend_state']=='NeedsLogin'
        # No up/login/auth key is used. Only opaque unauthenticated local state is tested here.
        run(['systemctl','stop',unit]);guard()
        bundle=json.dumps(state.backup(data=data,device=device,serial_path=serial,guard=guard)).encode()
        original=(data/'tailscale/tailscaled.state').read_bytes()
        (out/'gnupg').mkdir(mode=0o700);passphrase=secrets.token_hex(32).encode()
        encrypted=crypt(bundle,passphrase=passphrase);assert encrypted.returncode==0
        decoded=crypt(encrypted.stdout,decrypt=True,passphrase=passphrase);assert decoded.returncode==0 and decoded.stdout==bundle
        damaged=bytearray(encrypted.stdout);damaged[-1]^=1
        bad=crypt(bytes(damaged),decrypt=True,passphrase=passphrase)
        assert bad.returncode!=0  # never publish any partial plaintext from a failed decrypt
        report['encrypted_backup_integrity']=True
        run(['umount',data]);mounted=False
        dev2=attach('after-reinstall');device.unlink();device.symlink_to(dev2)
        run(['mount','-t','ext4','-o','rw,nodev,nosuid,noexec',dev2,data]);mounted=True
        after_uuid=run(['blkid','-s','UUID','-o','value',dev2]).stdout.decode().strip()
        assert before_uuid!=after_uuid
        serial.write_text('1000000087654321')
        try:state.restore(decoded.stdout,data=data,device=device,serial_path=serial,guard=guard)
        except state.StateError:pass
        else:raise AssertionError('different board accepted')
        assert not (data/'tailscale').exists();report['different_board_refused']=True
        serial.write_text('1000000012345678')
        # Real process termination after the first durable file, before publication.
        fd_r,fd_w=os.pipe();child=os.fork()
        if child==0:
            os.close(fd_r)
            real_fsync=state.os.fsync
            def checkpoint(fd):
                real_fsync(fd);os.write(fd_w,b'R')
                while True:signal.pause()
            state.os.fsync=checkpoint
            state.restore(decoded.stdout,data=data,device=device,serial_path=serial,guard=guard)
            os._exit(2)
        os.close(fd_w)
        import select
        assert select.select([fd_r],[],[],15)[0] and os.read(fd_r,1)==b'R'
        os.close(fd_r);os.kill(child,signal.SIGKILL);_,child_result=os.waitpid(child,0);child=None
        assert os.WIFSIGNALED(child_result)
        assert not (data/'tailscale').exists()
        staging=list(data.glob('.tailscale-restore-*'));assert len(staging)==1
        assert stat.S_IMODE(staging[0].stat().st_mode)==0o700
        try:state.prepare(data,device=device)
        except state.StateError:pass
        else:raise AssertionError('interrupted restore was ignored')
        staging[0].rename(data/'operator-preserved-interrupted-restore')
        restored=state.restore(decoded.stdout,data=data,device=device,serial_path=serial,guard=guard)
        assert restored['restored'] and (data/'tailscale/tailscaled.state').read_bytes()==original
        report['fresh_ext4_same_board_restore']=True;report['interrupted_restore_fail_closed']=True
        run(['systemctl','start',unit]);report['after_restore']=status()
        assert report['after_restore']['backend_state']=='NeedsLogin'
        run(['systemctl','stop',unit])
        # A wrong verified device must stop the real service before daemon execution.
        device.unlink();device.symlink_to('/dev/xenova-probe-intentionally-absent')
        failed=run(['systemctl','start',unit],check=False)
        report['bad_storage_service']=properties(unit)
        assert failed.returncode!=0 and report['bad_storage_service']['ActiveState']=='failed'
        buddy_after=properties(buddy)
        assert buddy_after['ActiveState']=='active' and buddy_after['MainPID']==buddy_before['MainPID']
        report['independent_service_preserved']=True
        report['passed']=True
    except BaseException as error:
        report['passed']=False;report['failure_type']=type(error).__name__;report['failure']=str(error)
        raise
    finally:
        if child is not None:
            try:
                observed,_=os.waitpid(child,os.WNOHANG)
                if observed==0:
                    os.kill(child,signal.SIGKILL);os.waitpid(child,0)
            except ChildProcessError:pass
        for name in (unit,buddy):
            run(['systemctl','stop',name],check=False)
            run(['systemctl','reset-failed',name],check=False)
        if unitfile.exists():unitfile.unlink()
        run(['systemctl','daemon-reload'],check=False)
        cleanup_errors=[]
        if mounted and run(['umount',data],check=False).returncode:cleanup_errors.append('owned mount')
        for dev in reversed(devices):
            if run(['losetup','-d',dev],check=False).returncode:cleanup_errors.append('owned loop')
        if shutil.which('gpgconf') and (out/'gnupg').exists():
            run(['gpgconf','--homedir',str(out/'gnupg'),'--kill','all'],check=False)
        report['cleanup_errors']=cleanup_errors
        if cleanup_errors:report['passed']=False
        (out/'RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='commands'}))


if __name__=='__main__':main()
