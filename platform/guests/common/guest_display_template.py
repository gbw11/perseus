"""새 Linux 템플릿에 display domU 계정과 Plan A Python 모듈을 준비한다."""
import hashlib
from pathlib import Path
import subprocess

from prepare_rootfs import inside


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "platform/services/display/router/xenova_display"
MODULES = ("__init__.py", "capture_limits.py", "mjpeg.py", "guest_sender.py", "guest_app.py")
POLICY = {"account": "xenova-display", "python_modules": list(MODULES)}
ACCOUNT = "xenova-display"
HOME = "/nonexistent"
SHELL = "/usr/sbin/nologin"


def validate_policy(policy):
    if policy != POLICY:
        raise ValueError("unsupported display guest policy")


def source_files():
    """VM3에 복사하는 Plan A Python 입력을 source lock에 포함한다."""
    files = [SOURCE / name for name in MODULES]
    if any(path.is_symlink() or not path.is_file() or path.resolve() != path for path in files):
        raise ValueError("display guest Python input must be a canonical regular file")
    return files


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


def prepare(root, policy, *, command=subprocess.run):
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
    for source in source_files():
        target = destination / source.name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("display guest Python target is linked or non-regular")
        data = source.read_bytes()
        target.write_bytes(data)
        target.chmod(0o644)
        installed[source.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return {"account": account, "python_root": "/opt/xenova/display-guest",
            "python_modules": installed}
