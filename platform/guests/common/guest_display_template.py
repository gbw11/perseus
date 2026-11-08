"""새 Linux 템플릿에 display domU 계정과 Plan A Python 모듈을 준비한다."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from prepare_rootfs import inside


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "platform/services/display/router/xenova_display"
DEPLOY = ROOT / "platform/services/display/router/deploy/guest-display/domu"
CONFIG_SOURCE = DEPLOY / "guest-display-domu.json.example"
SERVICE_SOURCE = DEPLOY / "xenova-guest-display-app.service"
MODULES = ("__init__.py", "capture_limits.py", "mjpeg.py", "guest_sender.py", "guest_app.py")
POLICY = {"account": "xenova-display", "python_modules": list(MODULES),
          "web_assets": "external-vm-screens-dist",
          "config": "/etc/xenova/guest-display-domu.json",
          "service": "xenova-guest-display-app.service"}
ACCOUNT = "xenova-display"
HOME = "/nonexistent"
SHELL = "/usr/sbin/nologin"


def validate_policy(policy):
    if policy != POLICY:
        raise ValueError("unsupported display guest policy")


def source_files():
    """VM3에 복사하는 Plan A Python 입력을 source lock에 포함한다."""
    files = [SOURCE / name for name in MODULES] + [CONFIG_SOURCE, SERVICE_SOURCE]
    if any(path.is_symlink() or not path.is_file() or path.resolve() != path for path in files):
        raise ValueError("display guest Python input must be a canonical regular file")
    return files


def install_config(root):
    """Plan A VM3 주소·허용 클라이언트·데이터 경로를 고정한 비밀 없는 설정을 설치한다."""
    raw = CONFIG_SOURCE.read_bytes()
    config = json.loads(raw)
    expected = {
        "web_root": "/opt/xenova/display-web", "bind": "192.168.77.20", "app_port": 8086,
        "allowed_clients": ["192.168.77.1"], "data_host": "192.168.77.1", "data_port": 8085,
        "data_token_file": "/etc/xenova/guest-display/guest-data.token", "page": "/display/mobile",
    }
    if config != expected:
        raise ValueError("display guest config example differs from the approved Plan A contract")
    target = inside(root, "etc/xenova/guest-display-domu.json", follow_final=False)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("display guest config target is linked or non-regular")
    target.write_bytes(raw)
    target.chmod(0o644)
    return {"path": "/etc/xenova/guest-display-domu.json",
            "sha256": hashlib.sha256(raw).hexdigest(), "mode": "0644"}


def install_service(root):
    """전용 계정으로 guest_app을 실행하는 hardened systemd unit을 설치한다."""
    raw = SERVICE_SOURCE.read_bytes()
    text = raw.decode("utf-8")
    required = (
        "User=xenova-display", "Group=xenova-display",
        "Environment=PYTHONPATH=/opt/xenova/display-guest",
        "ExecStart=/usr/bin/python3 -m xenova_display.guest_app --config /etc/xenova/guest-display-domu.json",
        "Restart=on-failure", "NoNewPrivileges=true", "ProtectSystem=strict",
    )
    if any(value not in text.splitlines() for value in required):
        raise ValueError("display guest service differs from the approved Plan A contract")
    target = inside(root, "usr/lib/systemd/system/xenova-guest-display-app.service", follow_final=False)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("display guest service target is linked or non-regular")
    target.write_bytes(raw)
    target.chmod(0o644)
    return {"path": "/usr/lib/systemd/system/xenova-guest-display-app.service",
            "sha256": hashlib.sha256(raw).hexdigest(), "mode": "0644"}


def install_web_assets(root, source, revision):
    """검증한 vm-screens dist를 복사하고 재현 가능한 파일·트리 해시를 반환한다."""
    if source is None:
        raise ValueError("display web assets require a canonical dist directory and git revision")
    source = Path(source)
    if (not source.is_absolute() or source.is_symlink() or not source.is_dir()
            or source.resolve() != source or not re.fullmatch(r"[0-9a-f]{7,40}", revision or "")):
        raise ValueError("display web assets require a canonical dist directory and git revision")
    index = source / "index.html"
    assets = source / "assets"
    if not index.is_file() or index.is_symlink() or not assets.is_dir() or assets.is_symlink():
        raise ValueError("display web assets require index.html and assets directory")
    files = []
    total = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("display web assets may contain regular files and directories only")
        if path.is_file():
            relative = path.relative_to(source)
            if len(relative.parts) > 8 or len(str(relative)) > 240:
                raise ValueError("display web asset path is too deep or long")
            size = path.stat().st_size
            total += size
            if size > 8 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise ValueError("display web assets exceed the image size limit")
            files.append((relative, path, size))
    if not files or not any(relative.parts[0] == "assets" for relative, _, _ in files):
        raise ValueError("display web assets directory is empty")
    destination = inside(root, "opt/xenova/display-web")
    if destination.exists():
        raise ValueError("display web destination already exists")
    destination.mkdir(parents=True, mode=0o755)
    manifest = {}
    tree = hashlib.sha256()
    for relative, path, size in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.parent.chmod(0o755)
        data = path.read_bytes()
        target.write_bytes(data)
        target.chmod(0o644)
        digest = hashlib.sha256(data).hexdigest()
        name = relative.as_posix()
        manifest[name] = {"sha256": digest, "bytes": size}
        tree.update(name.encode() + b"\0" + digest.encode() + b"\0" + str(size).encode() + b"\n")
    return {"root": "/opt/xenova/display-web", "revision": revision,
            "tree_sha256": tree.hexdigest(), "bytes": total, "files": manifest}


def _records(root, relative):
    rows = {}
    for line in inside(root, relative, create_parents=False).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            fields = line.split(":")
            rows[fields[0]] = fields
    return rows


def verify(root):
    """계정이 로그인 불가 시스템 계정이고 전용 그룹과 홈 미생성을 지켰는지 확인한다."""
    passwd = _records(root, "etc/passwd").get(ACCOUNT)
    shadow = _records(root, "etc/shadow").get(ACCOUNT)
    group = _records(root, "etc/group").get(ACCOUNT)
    if passwd is None or len(passwd) != 7 or shadow is None or len(shadow) < 2 or group is None or len(group) != 4:
        raise ValueError("display guest account records are incomplete")
    uid, gid = int(passwd[2]), int(passwd[3])
    if (uid <= 0 or uid >= 1000 or int(group[2]) != gid or passwd[5] != HOME or passwd[6] != SHELL
            or shadow[1] not in ("!", "!!", "*") or group[3]):
        raise ValueError("display guest account is not a locked dedicated system account")
    if inside(root, HOME.lstrip("/"), create_parents=False).exists():
        raise ValueError("display guest account home must not be created")
    return {"account": ACCOUNT, "uid": uid, "gid": gid, "home": HOME, "shell": SHELL,
            "password_locked": True}


def prepare(root, policy, *, web_assets=None, web_revision=None, command=subprocess.run):
    """새 템플릿에 계정을 만들고 고정한 Plan A Python 모듈을 설치한다."""
    validate_policy(policy)
    root = Path(root)
    if (not inside(root, "etc/xenova/template-prepared", create_parents=False).is_file()
            or inside(root, "etc/xenova/instance-id", create_parents=False).exists()
            or inside(root, "etc/xenova/deployment-seed.json", create_parents=False).exists()):
        raise ValueError("display account creation requires a new prepared template")
    for relative in ("etc/passwd", "etc/shadow", "etc/group"):
        if ACCOUNT in _records(root, relative):
            raise ValueError("display guest account already exists")
    command(["chroot", str(root), "/usr/sbin/useradd", "--system", "--user-group",
             "--home-dir", HOME, "--no-create-home", "--shell", SHELL,
             "--password", "!", ACCOUNT], check=True, timeout=60)
    account = verify(root)
    destination = inside(root, "opt/xenova/display-guest/xenova_display")
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise ValueError("display guest Python destination is not a directory")
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o755)
    installed = {}
    for source in (SOURCE / name for name in MODULES):
        target = destination / source.name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("display guest Python target is linked or non-regular")
        data = source.read_bytes()
        target.write_bytes(data)
        target.chmod(0o644)
        installed[source.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    web = install_web_assets(root, web_assets, web_revision)
    config = install_config(root)
    service = install_service(root)
    return {"account": account, "python_root": "/opt/xenova/display-guest",
            "python_modules": installed, "web": web, "config": config, "service": service}
