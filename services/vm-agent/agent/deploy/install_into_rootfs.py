"""게스트 rootfs 에 에이전트를 설치한다.

플랫폼의 Linux 템플릿 준비(platform/guests/common/prepare_rootfs.py)에서 부르거나 손으로 쓴다. 이 저장소의 두 패키지를 복사하고
systemd 유닛을 넣어 enable 한다. python3 와 python3-paho-mqtt 는 템플릿 준비가 apt 로 설치한다 (여기서는 있는지만 확인한다).

    python3 services/vm-agent/agent/deploy/install_into_rootfs.py <rootfs> [--source services/vm-agent]

설치 결과:
    /opt/xenova/mqtt-client/perseus_mqtt/        MQTT 계약 v1 클라이언트
    /opt/xenova/agent/xenova_agent/              에이전트 본체
    /usr/lib/systemd/system/xenova-agent.service
    /etc/systemd/system/multi-user.target.wants/xenova-agent.service -> /usr/lib/systemd/system/xenova-agent.service
    /etc/systemd/system/xenova-agent.service.d/10-xenova-identity.conf   (rootfs 에 xenova-guest-identity.service 가 있을 때만)

신원 유닛(xenova-guest-identity.service)은 deployment seed 경로(prepare_deployment_seed)에만 설치되고 일반 생성 VM(prepare_instance)에는
없다. 그래서 기본 유닛은 순서(After=)만 두고, Requires= 는 그 유닛이 있는 rootfs 에만 drop-in 으로 붙인다. seed 준비가 템플릿 설치 뒤에
신원 유닛을 넣는다면 그 시점에 install_identity_dropin(root) 를 한 번 더 부른다.
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
IDENTITY_UNIT = "usr/lib/systemd/system/xenova-guest-identity.service"
IDENTITY_DROPIN = "etc/systemd/system/xenova-agent.service.d/10-xenova-identity.conf"
IDENTITY_DROPIN_SRC = HERE / "xenova-agent-identity.conf"
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


def install_identity_dropin(root: Path, *, force: bool = False) -> Path | None:
    """신원 유닛이 있는 rootfs 에만 Requires=xenova-guest-identity.service drop-in 을 둔다. 없으면 None."""
    root = Path(root)
    if not force and not (root / IDENTITY_UNIT).is_file():
        return None
    dropin = _inside(root, IDENTITY_DROPIN)
    dropin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(IDENTITY_DROPIN_SRC, dropin)
    os.chmod(dropin, 0o644)
    return dropin


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
    dropin = install_identity_dropin(root)
    if dropin is not None:
        installed.append(dropin)
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
