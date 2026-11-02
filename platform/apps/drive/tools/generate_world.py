#!/usr/bin/env python3
"""Regenerate the shared wire from explicit frozen local tools; no downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools',type=Path,required=True)
    args=parser.parse_args();tools=args.tools.resolve()
    protoc=tools/'protoc/bin/protoc';nano=tools/'nanopb'
    if subprocess.check_output([str(protoc),'--version'],text=True).strip()!='libprotoc 3.20.3':
        raise ValueError('fixed protoc 3.20.3 required')
    contract=ROOT/'docs/contracts/drive-world';home=ROOT/'platform/apps/drive/world'
    generated=home/'generated';python=home/'python/xenova_drive_world'
    generated.mkdir(exist_ok=True);python.mkdir(exist_ok=True)
    env=dict(os.environ,PYTHONPATH=str(tools/'python'),PATH=str(protoc.parent)+':'+os.environ['PATH'],
             PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION='python',PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([str(protoc),'-I'+str(contract),'--python_out='+str(python),str(contract/'world.proto')],check=True,env=env)
    subprocess.run(['python3',str(nano/'generator/nanopb_generator.py'),'-I'+str(contract),
                    '-D'+str(generated),str(contract/'world.proto')],check=True,env=env)
    vendor=home/'nanopb';vendor.mkdir(exist_ok=True)
    names=('pb.h','pb_common.c','pb_common.h','pb_decode.c','pb_decode.h','pb_encode.c','pb_encode.h','LICENSE.txt')
    hashes={}
    for name in names:
        source=nano/name
        hashes[name]=hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(source,vendor/name)
    lock=dict(nanopb='0.4.9.1',nanopb_archive_sha256='4575944a468718ef25f05eb01d994364650b581563089a9841986bb1e460eac3',
        protoc='3.20.3',protoc_archive_sha256='44a6b498e996b845edef83864734c0e52f42197e85c9d567af55f4e3ff09d755',
        python_protobuf='3.20.3',runtime_files=hashes)
    (home/'dependencies.lock.json').write_text(json.dumps(lock,indent=2)+'\n')

if __name__=='__main__':main()
