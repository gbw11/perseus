#!/usr/bin/env python3
"""Generate an input identity before link, including dirty sources/config/tool.

This is not the final artifact hash. No instance data or credentials are inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','zephyr','compiler','config','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--flags',default='')
    a=p.parse_args()
    groups=('platform/apps/common','platform/apps/drive/zephyr','platform/apps/manual/zephyr',
            'platform/apps/autonomous/zephyr','platform/guests/zephyr/network-starter-app',
            'platform/guests/zephyr/diagnostics','platform/guests/zephyr/telemetry',
            'platform/guests/zephyr/xen-runtime','platform/guests/zephyr/xen-store')
    files={}
    for group in groups:
        for f in sorted((a.root/group).rglob('*')):
            if f.is_file() and '__pycache__' not in f.parts and f.suffix not in ('.pyc','.pyo'):
                files[f.relative_to(a.root).as_posix()]=digest(f)
    source=subprocess.check_output(['git','-C',str(a.zephyr),'rev-parse','HEAD'],text=True).strip()
    patch=subprocess.check_output(['git','-C',str(a.zephyr),'diff','HEAD','--'])
    untracked=subprocess.check_output(['git','-C',str(a.zephyr),'ls-files','--others','--exclude-standard'],text=True).splitlines()
    library=subprocess.check_output([str(a.compiler),'-print-libgcc-file-name'],text=True).strip()
    flags=a.flags.replace(str(a.root),'<source>').replace(str(a.zephyr),'<zephyr>').replace(str(a.config.parent.parent),'<build>')
    inputs={'files':files,'zephyr_commit':source,'zephyr_patch_sha256':hashlib.sha256(patch).hexdigest(),
            'zephyr_untracked':{n:digest(a.zephyr/n) for n in untracked if (a.zephyr/n).is_file()},
            'compiler_sha256':digest(a.compiler.resolve()),'libgcc_sha256':digest(Path(library)),
            'flags':flags,'source_date_epoch':__import__('os').environ.get('SOURCE_DATE_EPOCH'),
            'tool_lock_sha256':digest(a.root/'platform/guests/zephyr/starter.lock.json'),
            'config_sha256':digest(a.config)}
    identity='sha256:'+hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    content='#define XENOVA_BUILD_ID "'+identity+'"\n'
    if not a.output.exists() or a.output.read_text()!=content:a.output.write_text(content)
    receipt=a.output.with_suffix('.json');content=json.dumps({'build_id':identity,**inputs},indent=2)+'\n'
    if not receipt.exists() or receipt.read_text()!=content:receipt.write_text(content)

if __name__=='__main__':main()
