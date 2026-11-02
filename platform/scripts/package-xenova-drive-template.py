#!/usr/bin/env python3
"""실제 warm-lattice-lab 빌드만 새 불변 공개 template로 포장한다. 보드/운영 프로필 수락은 별개다."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'platform/first-install'),str(ROOT/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files')]
import xenova_zephyr as zephyr
from artifact_io import new_path,publish
from first_install_contract import uuid4

ID='xenova-zephyr-drive-lattice-lab-1.0.0'
TEMPLATE='zephyr-drive-lattice-lab-arm64'
VERSION='1.0.0-lab.1'


def package(build,output):
    zephyr.plain(build);new_path(output)
    receipt=json.loads(zephyr.asset(build,'BUILD_RECEIPT.json').read_text())
    spec=importlib.util.spec_from_file_location('drive_builder',ROOT/'platform/scripts/build-xenova-drive-app.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if (receipt.get('app')!='drive' or receipt.get('profile')!='warm-lattice-lab'
            or receipt.get('source_files')!=module.fingerprint('drive')
            or receipt.get('acceptance',{}).get('M1_arm_build') is not True):
        raise ValueError('current real drive build/profile/source receipt required')
    source=build/'firmware';info=zephyr.bundle_info(source)
    for name,expected in receipt['files'].items():
        path=zephyr.asset(source,name)
        if expected!={'sha256':zephyr.sha(path),'bytes':path.stat().st_size}:raise ValueError('drive artifact differs from build receipt')
    meta=json.loads((source/'firmware.json').read_text())
    if meta['source']['app']!='xenova-drive' or info['provisioning_profile']['apps']!={
        'autonomous':{'model':'stateful','standby_progress':'idle'},'manual':{'model':'stateless','standby_progress':'idle'}}:
        raise ValueError('actual stateful AUTO/independent MANUAL profile required')
    stage=output.parent/('.drive-template-'+uuid4());shutil.copytree(source,stage)
    meta.update(id=ID,kind='zephyr-xen-firmware',template_id=TEMPLATE,template_version=VERSION)
    meta['source']['build_receipt_sha256']=zephyr.sha(build/'BUILD_RECEIPT.json')
    meta['source'].update(zephyr_commit=receipt['zephyr_commit'],sdk_version=receipt['sdk_version'],drive_profile=receipt['profile'])
    # 원 firmware/manifest를 덮지 않고 새 배포 envelope만 생성한다.
    target=stage/'firmware.json';target.chmod(0o644);target.write_text(json.dumps(meta,indent=2)+'\n');target.chmod(0o444)
    result=zephyr.bundle_info(stage);publish(stage,output)
    return {'id':ID,'template_id':TEMPLATE,'version':VERSION,'directory':f'zephyr/{ID}/{VERSION}',
            'manifest_sha256':result['revision'],'guest_kind':'zephyr','firmware_sha256':result['sha256']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv)
    print(json.dumps(package(args.build_root,args.output),indent=2))


if __name__=='__main__':main()
