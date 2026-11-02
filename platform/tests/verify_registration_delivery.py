"""소유 task root의 두 RPM payload/모드와 ARM 설치 import를 대조한다. 운영 설치 도구가 아니다."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-root',type=Path,required=True)
    parser.add_argument('--base-root',type=Path,required=True)
    parser.add_argument('--qemu',type=Path,required=True)
    parser.add_argument('--tag',default='final')
    args=parser.parse_args();task=args.task_root.resolve()
    target=task/('target-'+args.tag);target.mkdir()  # 이전 설치/증거를 덮거나 삭제하지 않는다.
    base=args.base_root.resolve()
    def ignored(directory,names):
        relative=Path(directory).relative_to(base).as_posix()
        if relative=='.':return set(names)&{'srv','run','tmp','proc'}
        if relative in ('etc','usr/lib','usr/share'):return set(names)&{'xenova'}
        return set()
    shutil.copytree(base,target,symlinks=True,dirs_exist_ok=True,ignore=ignored)
    native=task/'package/tmp/sysroots-components/x86_64'
    env={**os.environ,'LD_LIBRARY_PATH':':'.join(str(p) for p in native.glob('*/usr/lib'))}
    rpm=native/'rpm-native/usr'
    env.update(RPM_CONFIGDIR=str(rpm/'lib/rpm'),RPM_ETCCONFIGDIR=str(rpm.parent),
               SEQUOIA_CRYPTO_POLICY=str(rpm/'share/crypto-policies/back-ends/rpm-sequoia.config'))
    tool=native/'rpm-native/usr/bin/rpm2archive.real'
    packages=task/'package/tmp/deploy/rpm'
    # 개발 PC의 sys.path로 누락을 숨기지 않는다. 실제 target pkgdata의 Python 의존성을 따른다.
    runtime=task/'package/tmp/pkgdata/raspberrypi4-64/runtime'
    queue=['xenova-platform','xenova-dom0-manager','xenova-mqtt-client','libsqlite3'];names=[]
    while queue:
        name=queue.pop()
        if name in names:continue
        names.append(name)
        for line in (runtime/name).read_text().splitlines():
            if line.startswith('RDEPENDS:'):
                dependencies=re.sub(r'\([^)]*\)','',line.split(': ',1)[1]).split()
                queue.extend(dep for dep in dependencies if dep.startswith('python3-'))
    rpm_manifest={}
    for name in names:
        candidates=list(packages.glob('*/'+name+'-[0-9]*.rpm'))
        if len(candidates)!=1:raise ValueError('expected one runtime RPM: '+name)
        package=candidates[0];rpm_manifest[package.name]=hashlib.sha256(package.read_bytes()).hexdigest()
        raw=subprocess.check_output([str(tool),str(package)],env=env)
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:*') as archive:
            if any(Path(item.name).is_absolute() or '..' in Path(item.name).parts for item in archive):
                raise ValueError('RPM archive escapes the owned target')
            # RPM의 systemd mask/절대 unit 링크는 chroot 안의 설치 의미를 보존한다.
            # 기존 링크를 따라 쓰지 않고 소유 root의 마지막 링크 자체만 교체한다.
            for member in archive:
                destination=target/member.name
                if not destination.parent.resolve().is_relative_to(target.resolve()):
                    raise ValueError('RPM parent escapes the owned target')
                if destination.is_symlink():destination.unlink()
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ValueError('unexpected RPM special file')
                if member.islnk() and (Path(member.linkname).is_absolute() or '..' in Path(member.linkname).parts):
                    raise ValueError('RPM hardlink escapes the owned target')
                archive.extract(member,target,filter='fully_trusted')
    source=task/'source/platform'
    product=source/'yocto/meta-xenova/recipes-core/xenova-platform/files'
    compared={}
    for name in ('xenova_management.py','xenova_management_cli.py','xenova_registration.py','xenova_registration_hook.py','xenova_first_boot.py'):
        expected=(product/name).read_bytes();installed=target/'usr/lib/xenova'/name
        assert installed.read_bytes()==expected and installed.stat().st_mode&0o777==0o644,name
        compared[str(installed.relative_to(target))]=hashlib.sha256(expected).hexdigest()
    api=source/'services/dom0-manager/dom0_manager'
    for path in api.rglob('*.py'):
        installed=target/'usr/lib/python3.14/site-packages/dom0_manager'/path.relative_to(api)
        assert installed.read_bytes()==path.read_bytes() and installed.stat().st_mode&0o777==0o644,path
        compared[str(installed.relative_to(target))]=hashlib.sha256(path.read_bytes()).hexdigest()
    assert not (target/'usr/share/xenova/registration-notification.json.example').exists()
    assert not (target/'etc/xenova/registration-notification.json').exists()
    assert not (target/'etc/xenova/spring-registration.env').exists()
    assert (target/'etc/xenova/dom0-manager.env').stat().st_mode&0o777==0o600
    assert (target/'usr/sbin/xenova-vm').stat().st_mode&0o777==0o755
    settings=source/'yocto/meta-xenova/recipes-core/xenova-dom0-manager/files'
    for name,destination,mode in (
            ('xenova-dom0-manager.service','usr/lib/systemd/system/xenova-dom0-manager.service',0o644),
            ('dom0-manager.env','etc/xenova/dom0-manager.env',0o600),
            ('spring-registration.env.example','usr/share/xenova/spring-registration.env.example',0o644)):
        installed=target/destination
        assert installed.read_bytes()==(settings/name).read_bytes() and installed.stat().st_mode&0o777==mode,name
        compared[destination]=hashlib.sha256(installed.read_bytes()).hexdigest()
    target.mkdir(exist_ok=True)
    shutil.copy2(args.qemu,target/'qemu-aarch64-static')
    # /srv와 mount/Xen만 소유 fixture로 대체한다. 제품 reader/projection/import는 실제 실행한다.
    script='''import json,sys
sys.path.insert(0,'/usr/lib/xenova')
import xenova_vm as vm
import xenova_registration_hook
from xenova_management import Manager
from dom0_manager.config import Settings
from dom0_manager.main import create_app
from dom0_manager.drivers.platform import PlatformDriver,SerializedManager
from dom0_manager.drivers.mock import MockProvisioner
from pathlib import Path
root=Path('/srv/xenova');(root/'instances').mkdir(parents=True)
record={"schema":1,"uuid":"850e8400-e29b-41d4-a716-446655440001","id":"fixture","name":"fixture","display_name":"saved display","hostname":"fixture","guest_kind":"linux","template":"ubuntu-fixture","template_id":"ubuntu-catalog-fixture","template_version":"1.0.0","kernel_release":"fixture","vcpus":2,"memory_mib":512,"mac":"02:16:3e:00:00:01","ipv4":"192.168.77.30","autostart":False}
directory=root/'instances/fixture';directory.mkdir();(directory/'instance.json').write_text(json.dumps(record))
vm.storage_check=lambda *a,**kw:None
p=vm.Platform(root,Path('/run/xenova'))
p.command=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('no Xen'))
p.boot_id_reader=lambda:(_ for _ in ()).throw(AssertionError('no host'))
m=Manager(p)
result=m.call('registrations');assert result['ok'],result
assert result['data']['registrations'][0]['template_id']=='ubuntu-catalog-fixture'
app=create_app(Settings(driver='platform',service_token='fixture-only-token'),driver=PlatformDriver(SerializedManager(m)),provisioner=MockProvisioner())
schema=app.openapi()
assert '/api/v1/vm-registrations' in schema['paths'] and '/api/v1/vm-registrations/{vm_id}/notify' in schema['paths']
assert not app.state.registration_sender._children
import fastapi, pydantic
print(json.dumps({'python':sys.version.split()[0],'fastapi':fastapi.__version__,'pydantic':pydantic.__version__,'registrations':result['data']['registrations'],'outbound_children':len(app.state.registration_sender._children)}))
'''
    (target/'registration-smoke.py').write_text(script)
    process=subprocess.run(['chroot',str(target),'/qemu-aarch64-static','/usr/bin/python3.14','-I','/registration-smoke.py'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    output=process.stdout
    (task/('arm-smoke-'+args.tag+'.log')).write_text(output)
    if process.returncode:raise RuntimeError('ARM smoke failed: '+output[-2500:])
    result={'rpm_sha256':rpm_manifest,'source_install_sha256':compared,'arm_smoke':json.loads(output.splitlines()[-1]),
            'target_root':str(target),'scope':'clean RPM extraction + fixed ARM/QEMU imports/metadata/OpenAPI; mount/Xen external fixture; not image/board/systemd or ARM HTTP child execution'}
    (task/('delivery-'+args.tag+'.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
