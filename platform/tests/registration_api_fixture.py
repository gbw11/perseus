"""실제 FastAPI 프로세스용 소유 fixture. Xen/guest 디스크/외부 Spring만 시험 경계다."""
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from test_xenova_recovery_foundation import FoundationTest
from test_xenova_platform import fixture, record
from dom0_manager.config import Settings
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner, SerializedManager
from dom0_manager.drivers.base import HostInfo
from dom0_manager.main import create_app
import xenova_registration_hook as hook


def application():
    root=Path(os.environ['REGISTRATION_FIXTURE'])
    previous=json.loads((root/'metadata.json').read_text()) if (root/'metadata.json').exists() else None
    directory=tempfile.TemporaryDirectory
    with patch.object(tempfile,'TemporaryDirectory',side_effect=lambda *a,**kw:directory(*a,dir=root,**kw)):
        f=FoundationTest('runTest');f.setUp();f.start()
    # API 재시작에도 원 등록/원장이 남는다. 부모 시험의 소유 root cleanup이 최종 정리한다.
    f.f.tmp._finalizer.detach()
    f.p.clock=time.monotonic
    # host/network 명령은 기존 FakeXen. 부모 시험은 물리 stop 호출을 이 파일로 관측한다.
    original=f.p.command
    def command(args,**kw):
        with (root/'xen.jsonl').open('a') as stream:stream.write(json.dumps(args)+'\n')
        if args[:2]==['xl','create']:
            f.xen.records=f.p.records(validate_assets=False)
        return original(args,**kw)
    f.p.command=command
    other=record('debian-dev');other['display_name']=f.r.get('display_name',f.r['name'])
    other.pop('template_version',None);other_directory=fixture(f.p.data,other)
    with (other_directory/'root.raw').open('r+b') as stream:
        stream.seek(1080);stream.write(b'\x53\xef')
    from test_xenova_zephyr import bundle
    from test_xenova_lifecycle import application_bundle
    import xenova_zephyr as zephyr
    source=application_bundle(bundle(f.p.data/'z-source'),f.p.data/'rtos-templates/z-registration')
    manifest=source/'firmware.json';meta=json.loads(manifest.read_text())
    meta.update(id='z-registration',kind='zephyr-xen-firmware',version='1.0.0')
    manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
    zid='850e8400-e29b-41d4-a716-446655440001'
    z=zephyr.new_instance(f.p.data,'z-registration',source,{},identity=zid)
    z.update(ipv4='192.168.77.90',mac='02:16:3e:00:00:90',hostname='z-registration')
    (f.p.data/'instances/z-registration/instance.json').write_text(json.dumps(z))
    if os.environ.get('REGISTRATION_FACTORY_DATA'):
        f.p.data=Path(os.environ['REGISTRATION_FACTORY_DATA']);f.m.data=f.p.data
    elif previous is not None:
        f.p.data=Path(previous['data']);f.m.data=f.p.data
        f.p.runtime=Path(previous['run'])
    hook.API_ENV=root/'local.env';hook.TOKEN_FILE=root/'token.env'
    # 코드의 자동 hook/실제 subprocess를 사용한다. 기본 fixture setup의 이미 소비된 기회는 되살리지 않는다.
    settings=Settings(driver='platform',service_token=os.environ['XENOVA_SERVICE_TOKEN'],log_level='CRITICAL',
        registration_url=os.environ.get('XENOVA_REGISTRATION_URL',''),
        registration_token=os.environ.get('VM_REGISTRATION_SERVICE_TOKEN',''),
        registration_timeout_s=float(os.environ.get('XENOVA_REGISTRATION_TIMEOUT','1')),
        registration_slot_dir=str(root/'sender-slots'))
    driver=PlatformDriver(SerializedManager(f.m),xl=SimpleNamespace(info=lambda:HostInfo(4096,8192,4)))
    app=create_app(settings,driver=driver,provisioner=PlatformProvisioner(driver.manager))
    app.state.fixture=f  # 시험 프로세스의 실제 자산/patch 수명 유지
    if os.environ.get('REGISTRATION_SLOW_DIAGNOSTIC'):
        class SlowDiagnostic(logging.Handler):
            def emit(self,record):
                (root/'diagnostic-entered').write_text('entered')
                time.sleep(.6)  # 부가 sink의 명시적 시험 지연
        logger=logging.getLogger('dom0_manager.api.errors');logger.setLevel(logging.WARNING)
        logger.addHandler(SlowDiagnostic())
    (root/'metadata.json').write_text(json.dumps({'data':str(f.p.data),'run':str(f.p.runtime),
        'vm_id':f.vm_id,'debian_id':other['uuid'],'zephyr_id':zid,'spec':f.f.spec}))
    return app


if __name__=='__main__':
    import uvicorn
    from dom0_manager.__main__ import ReadyServer
    barrier=os.environ.get('REGISTRATION_START_BARRIER')
    if barrier:
        # 시험만의 지연 기동. 제품 READY는 실제 lifespan/listen 뒤 ReadyServer가 보낸다.
        end=time.monotonic()+40
        while not Path(barrier).exists():
            if time.monotonic()>=end:raise SystemExit('fixture startup barrier expired')
            time.sleep(.02)
    ReadyServer(uvicorn.Config(application(),host='127.0.0.1',port=int(sys.argv[1]),
                              log_level='critical',access_log=False)).run()
