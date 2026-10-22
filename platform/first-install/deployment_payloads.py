"""dom0 API·MQTT 설치 소스 계약. 조립 시 파일 누락과 과거 이미지 재사용을 거부한다."""
from pathlib import Path
import stat

from prepare_rootfs import inside

ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / "platform/yocto/meta-xenova/recipes-core"
API = ROOT / "platform/services/dom0-manager"
MQTT = ROOT / "platform/services/vm-agent/mqtt-client"


def source_files(site_packages):
    """배포 대상 소스와 rootfs 경로를 연결한다. 실행·파일 변경은 하지 않는다."""
    result = {}
    for project, package in ((API, "dom0_manager"), (MQTT, "xenova_mqtt")):
        if not (project/package/"__init__.py").is_file():
            raise ValueError("required deployment source package missing: " + package)
        for source in sorted((project/package).rglob("*.py")):
            result[str(Path(site_packages)/package/source.relative_to(project/package))] = source
    result.update({
        "usr/lib/systemd/system/xenova-dom0-manager.service": RECIPES/"xenova-dom0-manager/files/xenova-dom0-manager.service",
        "usr/lib/systemd/system/xenova-recovery-worker.service": API/"deploy/xenova-recovery-worker.service",
        "etc/xenova/dom0-manager.env": RECIPES/"xenova-dom0-manager/files/dom0-manager.env",
        "usr/share/xenova/dom0-manager-pyproject.toml": API/"pyproject.toml",
        "usr/share/xenova/mqtt-client-pyproject.toml": MQTT/"pyproject.toml",
    })
    return result


def installed_sources(root):
    """필수 런타임·enable 링크를 확인하고 소스 일치 검사 목록을 반환한다."""
    def path(name):
        return inside(root, name, create_parents=False)

    sites = []
    for candidate in path("usr/lib").glob("python3*/site-packages"):
        relative = str(candidate.relative_to(root))
        if path(relative+"/dom0_manager/__init__.py").is_file() and path(relative+"/xenova_mqtt/__init__.py").is_file():
            sites.append(relative)
    if len(sites) != 1:
        raise ValueError("required dom0 API/MQTT payload missing or ambiguous; rebuild dom0")
    site = sites[0]
    for name in ("fastapi/__init__.py", "uvicorn/__init__.py", "paho/mqtt/client.py",
                 "pydantic/__init__.py", "starlette/__init__.py", "anyio/__init__.py"):
        if not path(site+"/"+name).is_file():
            raise ValueError("required API/MQTT runtime dependency missing: "+name)
    if not path("usr/bin/python3").is_file() or not path("usr/bin/python3").stat().st_mode & 0o111:
        raise ValueError("required Python runtime missing")
    for unit, source in (("xenova-dom0-manager.service", RECIPES/"xenova-dom0-manager/files/xenova-dom0-manager.service"),
                         ("xenova-recovery-worker.service", API/"deploy/xenova-recovery-worker.service")):
        link = root/"etc/systemd/system/multi-user.target.wants"/unit
        if not link.is_symlink() or path(str(link.relative_to(root))).read_bytes() != source.read_bytes():
            raise ValueError("required dom0 service not enabled: " + unit)
    if stat.S_IMODE(path("etc/xenova/dom0-manager.env").stat().st_mode) != 0o600:
        raise ValueError("dom0 API environment must be root-only (0600)")
    if (root/"etc/xenova/dom0-manager-token.env").exists() or (root/"etc/xenova/dom0-manager-token.env").is_symlink():
        raise ValueError("common image must not contain a board service token")
    return source_files(site)
