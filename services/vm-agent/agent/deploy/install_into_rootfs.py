"""게스트 rootfs 에 에이전트를 설치한다.

플랫폼의 Linux 템플릿 준비(platform/guests/common/prepare_rootfs.py)에서 부르거나 손으로 쓴다. 이 저장소의 두 패키지를 복사하고
systemd 유닛을 넣어 enable 한다. python3 와 python3-paho-mqtt 는 템플릿 준비가 apt 로 먼저 설치한다. 여기서는 설치 전에 paho 가
rootfs 에 있는지 확인하고, 없으면 아무것도 설치하지 않는다 (실행 못 하는 서비스를 enable 해 두지 않는다).

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

rootfs 는 신뢰하지 않는다. 쓰려는 경로의 마지막 요소가 심볼릭 링크면(호스트 파일로 이어질 수 있음) 쓰지 않고 거부한다.
최종 이미지 검사는 chroot 안에서 `python3 -c "import paho.mqtt.client"` 와 `systemctl is-enabled xenova-agent.service` 로 한다.
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
ENABLE_WANTS_DIR = "etc/systemd/system/multi-user.target.wants"
ENABLE_TARGET = "/usr/lib/systemd/system/" + UNIT_NAME
IDENTITY_UNIT = "usr/lib/systemd/system/xenova-guest-identity.service"
IDENTITY_DROPIN = "etc/systemd/system/xenova-agent.service.d/10-xenova-identity.conf"
IDENTITY_DROPIN_SRC = HERE / "xenova-agent-identity.conf"
PACKAGES = (("mqtt-client", "perseus_mqtt", "opt/xenova/mqtt-client"), ("agent", "xenova_agent", "opt/xenova/agent"))
IGNORE = shutil.ignore_patterns("tests", "__pycache__", "*.pyc", "*.egg-info")
PAHO_CLIENT_GLOBS = ("usr/lib/python3*/*-packages/paho/mqtt/client.py", "usr/local/lib/python3*/*-packages/paho/mqtt/client.py")


def _inside(root: Path, relative: str) -> Path:
    """rootfs 안의 쓰기 대상 경로. 마지막 요소가 심볼릭 링크이거나 부모가 rootfs 밖을 가리키면 거부한다."""
    root = Path(root).resolve()
    target = root.joinpath(relative)
    if target.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {relative}")
    resolved = target.parent.resolve() / target.name
    if root not in resolved.parents:
        raise ValueError(f"path escapes rootfs: {relative}")
    return resolved


def _enable_link(root: Path) -> tuple[Path, bool]:
    """enable 링크 자리를 검사한다. (경로, 이미 정상 링크인지).

    이 링크는 설치 스크립트 자신이 만드는 심볼릭 링크라 _inside 의 링크 거부를 그대로 적용하면 두 번째 설치가 실패한다.
    기대한 대상(/usr/lib/systemd/system/xenova-agent.service)을 가리키면 정상으로 두고, 다른 대상을 가리키는 링크나 일반 파일이면 거부한다.
    """
    link = _inside(root, ENABLE_WANTS_DIR) / UNIT_NAME  # wants 디렉터리 자체가 링크면 여기서 거부된다
    if link.is_symlink():
        target = os.readlink(link)
        if target == ENABLE_TARGET:
            return link, True
        raise ValueError(f"unexpected enable link target at {ENABLE_WANTS_DIR}/{UNIT_NAME}: {target!r}")
    if link.exists():
        raise ValueError(f"refusing to replace a regular file at {ENABLE_WANTS_DIR}/{UNIT_NAME}")
    return link, False


def paho_installed(root: Path) -> bool:
    """apt 의 python3-paho-mqtt 또는 pip 설치본의 실제 모듈 파일(paho/mqtt/client.py)이 rootfs 안에 있는지."""
    root = Path(root)
    return any(path.is_file() for pattern in PAHO_CLIENT_GLOBS for path in root.glob(pattern))


def _identity_unit_present(root: Path) -> bool:
    return (Path(root) / IDENTITY_UNIT).is_file()


def _write_dropin(dropin: Path) -> Path:
    dropin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(IDENTITY_DROPIN_SRC, dropin)
    os.chmod(dropin, 0o644)
    return dropin


def install_identity_dropin(root: Path, *, force: bool = False) -> Path | None:
    """신원 유닛이 있는 rootfs 에만 Requires=xenova-guest-identity.service drop-in 을 둔다. 없으면 None."""
    if not force and not _identity_unit_present(root):
        return None
    return _write_dropin(_inside(root, IDENTITY_DROPIN))


def install(root: Path, source: Path = DEFAULT_SOURCE, *, enable: bool = True, require_paho: bool = True) -> list[Path]:
    root = Path(root)
    source = Path(source)
    if not root.is_dir():
        raise ValueError(f"rootfs is not a directory: {root}")
    for project, package, _ in PACKAGES:
        if not (source / project / package / "__init__.py").is_file():
            raise ValueError(f"package source missing: {source / project / package}")
    if require_paho and not paho_installed(root):
        raise ValueError("python3-paho-mqtt is not installed in the rootfs; apt install it before the agent (nothing was installed)")
    # 쓰기 대상(패키지·유닛·drop-in·enable 링크)을 먼저 전부 검사한다. 하나라도 거부되면 아무것도 바꾸지 않는다
    package_targets = [(source / project / package, _inside(root, f"{destination}/{package}")) for project, package, destination in PACKAGES]
    unit_dst = _inside(root, f"usr/lib/systemd/system/{UNIT_NAME}")
    dropin_dst = _inside(root, IDENTITY_DROPIN) if _identity_unit_present(root) else None
    link, link_ok = _enable_link(root) if enable else (None, True)

    installed: list[Path] = []
    for src, dst in package_targets:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=IGNORE)
        installed.append(dst)
    unit_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / UNIT_NAME, unit_dst)
    os.chmod(unit_dst, 0o644)
    installed.append(unit_dst)
    if dropin_dst is not None:
        installed.append(_write_dropin(dropin_dst))
    if link is not None:
        if not link_ok:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(ENABLE_TARGET)
        installed.append(link)
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rootfs", type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="services/vm-agent 경로")
    parser.add_argument("--no-enable", action="store_true", help="multi-user.target.wants 링크를 만들지 않는다")
    parser.add_argument("--allow-missing-paho", action="store_true", help="paho 가 없어도 설치한다 (뒤에 따로 설치할 때만)")
    args = parser.parse_args(argv)
    try:
        paths = install(args.rootfs, args.source, enable=not args.no_enable, require_paho=not args.allow_missing_paho)
    except (ValueError, OSError) as exc:
        print(f"install_into_rootfs: {exc}", file=sys.stderr)
        return 1 if "paho" in str(exc) else 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
