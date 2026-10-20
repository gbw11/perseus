#!/usr/bin/env python3
"""공통 Manager 명세의 시험 연결부. HTTP 서버·배포 어댑터·Spring 응답 직렬화기가 아니다.

실제 파일/registry/journal/IP 예약/공통 응답을 사용하며 mount, Xen, OS 복사만 대역이다.
stdin: [{action, spec, execute?, idempotency_key?}, ...], stdout: 완전한 JSON 배열 한 개.
"""
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'platform/tests'))
import test_xenova_management as fixtures
import xenova_management as management

# Manager 오류를 HTTP 모양으로 표현하는 시험 경계. 배포 어댑터의 스키마·상태 매핑과 구분한다.
ERRORS = {
    'INVALID_SPEC': (400, 'InvalidArgument'), 'RESOURCE_UNSUPPORTED': (400, 'InvalidArgument'),
    'TEMPLATE_NOT_FOUND': (400, 'InvalidArgument'), 'TEMPLATE_VERSION_NOT_FOUND': (400, 'InvalidArgument'),
    'IDEMPOTENCY_CONFLICT': (409, 'VMExists'), 'VM_NAME_CONFLICT': (409, 'VMExists'),
    'TARGET_CHANGED': (409, 'InvalidState'), 'VM_PURGED': (409, 'InvalidState'),
    'VM_NOT_STOPPED': (409, 'InvalidState'), 'RESTORE_CONFLICT': (409, 'InvalidState'),
    'VM_NOT_FOUND': (404, 'VMNotFound'), 'IP_POOL_EXHAUSTED': (507, 'InsufficientResources'),
    'INSUFFICIENT_STORAGE': (507, 'InsufficientResources'),
}


def transport(envelope):
    if not envelope['ok']:
        error = envelope['error']
        status, code = ERRORS.get(error['code'], (500, 'DriverError'))
        return {'status':status, 'body':{'code':code, 'message':error['message'], 'detail':None, 'resource':None}}
    data = envelope['data']
    def row(value):
        state = value['xen_state']
        mapped = {'stopped':'shutdown', 'blocked':'running', 'dying':'stopping'}.get(state, state)
        return {k:value[k] for k in ('vm_id','name','template_id','template_version',
                                    'guest_kind','vcpus','memory_mib','ipv4')} | {'state':mapped or 'shutdown'}
    if 'vms' in data:
        body = [row(value) for value in data['vms']]
    elif 'vm' in data:
        body = row(data['vm'])
    elif 'vm_id' in data:
        body = row(data)
    else:
        body = data
    return {'status':200, 'body':body}


def add_zephyr(f):
    legacy = fixtures.bundle(f.data/'old')
    source = fixtures.application_bundle(legacy, f.data/'rtos-temp')
    manifest = source/'firmware.json'
    meta = json.loads(manifest.read_text())
    meta.update(id='starter-artifact', kind='zephyr-xen-firmware', version='1.0.0')
    manifest.chmod(0o644)
    manifest.write_text(json.dumps(meta))
    manifest.chmod(0o444)
    (f.data/'rtos-templates').mkdir()
    source.rename(f.data/'rtos-templates/starter-artifact')
    descriptor = {'schema_version':'1.0','template_id':'zephyr-xenvm-starter-arm64','template_version':'1.0.0',
                  'guest_kind':'zephyr','asset_directory':'rtos-templates/starter-artifact',
                  'source_template_id':'starter-artifact',
                  'manifest_sha256':fixtures.catalog.sha(f.data/'rtos-templates/starter-artifact/firmware.json')}
    target = f.data/'catalog/zephyr-xenvm-starter-arm64'
    target.mkdir()
    (target/'1.0.0.json').write_text(json.dumps(descriptor))


def run(requests):
    f = fixtures.ManagementTest()
    f.setUp()
    add_zephyr(f)
    # Manager의 메타데이터/신원 생성은 실제 구현을 실행한다. Linux OS 준비 경계만 대역이다.
    import prepare_instance
    def copy_linux(args, **kwargs):
        f.copies += 1
        record = kwargs['record_override']
        directory = fixtures.fixture(f.data, record)
        with (directory/'root.raw').open('wb') as stream:
            stream.seek(1080)
            stream.write(bytes.fromhex('53ef'))
        (directory/'saved-user-file').write_text('keep me')
    f.m.preparer = f.m._prepare
    copy = patch.object(prepare_instance, 'make_instance', side_effect=copy_linux)
    copy.start()
    try:
        result = []
        for request in requests:
            envelope = f.m.call(request.get('action','create'), request.get('spec'),
                                execute=request.get('execute', True),
                                idempotency_key=request.get('idempotency_key'))
            result.append({'envelope':envelope, **transport(envelope)})
        return result
    finally:
        copy.stop()
        f.doCleanups()


if __name__ == '__main__':
    print(json.dumps(run(json.load(sys.stdin)), ensure_ascii=False))
