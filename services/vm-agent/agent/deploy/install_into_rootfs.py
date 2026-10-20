"""게스트 rootfs 에 에이전트를 설치한다.

플랫폼의 Linux 템플릿 준비(platform/guests/common/prepare_rootfs.py)에서 부르거나 손으로 쓴다. 이 저장소의 두 패키지를 복사하고
systemd 유닛을 넣어 enable 한다. python3 와 python3-paho-mqtt 는 템플릿 준비가 apt 로 설치한다 (여기서는 있는지만 확인한다).

    python3 services/vm-agent/agent/deploy/install_into_rootfs.py <rootfs> [--source services/vm-agent]

설치 결과:
    /opt/xenova/mqtt-client/perseus_mqtt/        MQTT 계약 v1 클라이언트
    /opt/xenova/agent/xenova_agent/              에이전트 본체
    /usr/lib/systemd/system/xenova-agent.service
    /etc/systemd/system/multi-user.target.wants/xenova-agent.service -> /usr/lib/systemd/system/xenova-agent.service
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parent.parent  # services/vm-agent
UNIT_NAME = "xenova-agent.service"
PACKAGES = (("mqtt-client", "perseus_mqtt", "opt/xenova/mqtt-client"), ("agent", "xenova_agent", "opt/xenova/agent"))
IGNORE = shutil.ignore_patterns("tests", "__pycache__", "*.pyc", "*.egg-info")


def _inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"path escapes rootfs: {relative}")
    return target


def paho_installed(root: Path) -> bool:
    """apt 의 python3-paho-mqtt 또는 pip 설치본이 rootfs 안에 있는지 (dist-packages·site-packages 아래 paho/)."""
    return any(path.is_dir() for path in root.glob("usr/lib/python3*/*-packages/paho")) or any(
        path.is_dir() for path in root.glob("usr/local/lib/python3*/*-packages/paho")
    )


def install(root: Path, source: Path = DEFAULT_SOURCE, *, enable: bool = True) -> list[Path]:
    root = Path(root)
    source = Path(source)
    if not root.is_dir():
        raise ValueError(f"rootfs is not a directory: {root}")
    installed: list[Path] = []
    for project, package, destination in PACKAGES:
        src = source / project / package
        if not (src / "__init__.py").is_file():
            raise ValueError(f"package source missing: {src}")
        dst = _inside(root, destination) / package
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=IGNORE)
        installed.append(dst)
    unit_src = HERE / UNIT_NAME
    unit_dst = _inside(root, "usr/lib/systemd/system") / UNIT_NAME
    unit_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(unit_src, unit_dst)
    os.chmod(unit_dst, 0o644)
    installed.append(unit_dst)
    if enable:
        wants = _inside(root, "etc/systemd/system/multi-user.target.wants")
        wants.mkdir(parents=True, exist_ok=True)
        link = wants / UNIT_NAME
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to("/usr/lib/systemd/system/" + UNIT_NAME)
        installed.append(link)
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rootfs", type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="services/vm-agent 경로")
    parser.add_argument("--no-enable", action="store_true", help="multi-user.target.wants 링크를 만들지 않는다")
    args = parser.parse_args(argv)
    try:
        paths = install(args.rootfs, args.source, enable=not args.no_enable)
    except (ValueError, OSError) as exc:
        print(f"install_into_rootfs: {exc}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    if not paho_installed(args.rootfs):
        print("install_into_rootfs: warning: python3-paho-mqtt not found in rootfs (apt install python3-paho-mqtt)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
