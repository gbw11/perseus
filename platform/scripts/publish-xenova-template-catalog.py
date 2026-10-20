#!/usr/bin/env python3
"""검증된 저장소 내부 불변 번들에 ID/버전 descriptor를 새로 게시한다. payload는 수정하지 않는다."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'platform/first-install'),
              str(ROOT/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files')]
from artifact_io import publish
from first_install_contract import uuid4
from xenova_catalog import describe, plain
from xenova_runtime import atomic_write, operation_lock


def register(data, descriptor, *, execute=False, runtime=Path('/run/xenova')):
    plain(data,directory=True)
    with operation_lock(runtime):
        info=describe(data,descriptor,full=True)
        target=data/'catalog'/descriptor['template_id']/(descriptor['template_version']+'.json')
        if target.exists() or target.is_symlink():
            existing=json.loads(plain(target).read_text())
            if existing!=descriptor:
                raise ValueError('same template ID/version cannot reference different content')
            return {'published':True,'replayed':True,'template':info}
        if execute:
            parent=data/'catalog'
            for directory in (parent,target.parent):
                if not directory.exists():
                    directory.mkdir(mode=0o755)
                plain(directory,directory=True)
            stage=target.with_name('.catalog-'+uuid4())
            atomic_write(stage,json.dumps(descriptor,indent=2)+'\n',0o444)
            publish(stage,target)
        return {'published':execute,'replayed':False,'template':info}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root',required=True,type=Path)
    p.add_argument('--descriptor',required=True,type=Path)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args(argv)
    print(json.dumps(register(a.data_root,json.loads(a.descriptor.read_text()),execute=a.execute),indent=2))


if __name__=='__main__':
    try:
        main()
    except (ValueError,OSError,RuntimeError) as exc:
        raise SystemExit(str(exc))
