#!/usr/bin/env python3
"""Package only a verified partial network Starter build; never publish/overwrite a release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"platform/guests/zephyr"
FILES=ROOT/"platform/yocto/meta-xenova/recipes-core/xenova-platform/files"
sys.path[:0]=[str(FILES),str(ROOT/"platform/first-install")]
import xenova_zephyr as zephyr
from artifact_io import new_path,sha


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    root=args.build_root
    if not root.is_absolute() or root.resolve()!=root or root.is_symlink():
        raise ValueError("canonical build root required")
    new_path(args.output)
    receipt_path=root/"firmware/network-starter-build.json"
    receipt=json.loads(receipt_path.read_text())
    for name,spec in receipt["files"].items():
        p=root/"firmware"/name
        if sha(p)!=spec["sha256"] or p.stat().st_size!=spec["bytes"]:
            raise ValueError("partial build artifact changed: "+name)
    # Current source must match the immutable build snapshot. A platform Python
    # change does not change this firmware; any module/app change does.
    for name,spec in receipt["source_files"].items():
        p=BASE/name
        if sha(p)!=spec["sha256"] or p.stat().st_size!=spec["bytes"]:
            raise ValueError("firmware source differs from build: "+name)
    lock=json.loads((BASE/"network-starter.lock.json").read_text())
    actual={str(p.relative_to(BASE/"network-starter-app")):{"sha256":sha(p),"bytes":p.stat().st_size}
            for p in sorted((BASE/"network-starter-app").rglob("*")) if p.is_file()}
    if actual!=lock["app_files"]:
        raise ValueError("app source lock differs")
    args.output.mkdir(mode=0o755)
    sources={name:root/"firmware"/name for name in
             ("zephyr.bin","zephyr.elf","zephyr.map","zephyr.config","zephyr.dts","XEN-ABI-LICENSE")}
    sources.update({"LICENSE":root/"firmware/ZEPHYR-LICENSE",
                    "zephyr.version.h":root/"build/zephyr/include/generated/zephyr/version.h",
                    "xen.cfg.in":BASE/"network-xen.cfg.in"})
    for name,p in sources.items():
        shutil.copyfile(p,args.output/name);(args.output/name).chmod(0o444)
    source={k:lock[k] for k in ("app","app_files","app_source_sha256","zephyr_commit","sdk_version")}
    source.update(zephyr_version="4.4.0",source_revision="sha256:"+lock["app_source_sha256"],
                  build_id=root.name,build_receipt_sha256=sha(receipt_path),
                  runtime_source_sha256=receipt["source_sha256"])
    meta={"schema":1,"id":lock["id"],"kind":"zephyr-xen-firmware","guest_kind":"zephyr",
          "version":lock["version"],"template_id":"zephyr-xenvm-starter-arm64",
          "template_version":"1.1.0","source":source,
          "hardware_profile":zephyr.NET_PROFILE,"capabilities":zephyr.NET_CAPABILITIES,
          "files":{p.name:{"sha256":sha(p),"bytes":p.stat().st_size}
                   for p in args.output.iterdir()}}
    path=args.output/"firmware.json"
    path.write_text(json.dumps(meta,indent=2)+"\n");path.chmod(0o444)
    info=zephyr.bundle_info(args.output,sha(path))
    print(json.dumps({"bundle":str(args.output),"manifest_sha256":sha(path),
                      "firmware_sha256":info["sha256"],"runtime_hardware_verified":False,
                      "publication":"candidate only"},indent=2))


if __name__=="__main__":main()
