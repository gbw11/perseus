import os
from pathlib import Path
import re
import subprocess
import shutil
from types import SimpleNamespace

import pytest

from xenova_display import mobile_access as mobile

ROOT = Path(__file__).resolve().parents[5]
DEPLOY = ROOT / "platform/services/display/router/deploy/mobile-access"


def test_install_fragment_leaves_existing_351_files_and_boot_configuration_intact(tmp_path):
    append = ROOT / "platform/yocto/meta-xenova/recipes-core/xenova-display/xenova-display_%.bbappend"
    text = append.read_text()
    extra = re.search(r'FILESEXTRAPATHS:prepend := "([^\"]+)"', text).group(1)
    assert Path(extra.replace("${THISDIR}", str(append.parent)).rstrip(":")).resolve() == DEPLOY
    body = re.search(r"do_install:append\(\) \{(.*?)\n\}", text, re.S).group(1)
    stage = tmp_path / "root"
    existing = stage / "usr/lib/systemd/system/xenova-display-hdmi.service.d/50-mobile-first.conf"
    existing.parent.mkdir(parents=True)
    existing.write_text("existing 351 owned content")
    subprocess.run(["sh", "-eu", "-c", body], check=True, timeout=5, env={**os.environ,
        "D": str(stage), "S": str(DEPLOY), "systemd_system_unitdir": "/usr/lib/systemd/system", "datadir": "/usr/share"})
    assert existing.read_text() == "existing 351 owned content"
    assert (stage / "usr/lib/systemd/system/xenova-display-mobile.service").read_text() == (DEPLOY / "xenova-display-mobile.service").read_text()
    assert (stage / "usr/share/xenova/display/mobile-access/mobile-access.README.md").exists()
    assert not (stage / "etc/xenova/display-mobile.json").exists()
    assert not list(stage.rglob("*.wants"))


def test_active_config_rejects_symlinks_and_public_permissions(tmp_path, monkeypatch):
    path = tmp_path / "mobile.json"
    path.write_text('{}')
    path.chmod(0o600)
    original = Path.lstat
    def metadata(p):
        result = original(p)
        return SimpleNamespace(st_mode=result.st_mode, st_uid=0, st_size=result.st_size)
    monkeypatch.setattr(Path, "lstat", metadata)
    assert mobile.root_config(path) == {}
    path.chmod(0o644)
    with pytest.raises(ValueError): mobile.root_config(path)
    path.chmod(0o600)
    link = path.with_suffix(".link")
    link.symlink_to(path)
    with pytest.raises(ValueError): mobile.root_config(link)


def test_pairing_code_is_private_and_removed_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "RUNTIME", tmp_path)
    mobile.publish_code("123456")
    path = tmp_path / "pairing-code"
    assert path.read_text() == "123456\n" and path.stat().st_mode & 0o777 == 0o600
    mobile.publish_code(None)
    assert not path.exists() and not (tmp_path / "pairing-code.new").exists()


def test_startup_error_does_not_remove_another_process_code(tmp_path, monkeypatch, capsys):
    import fcntl
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    code = runtime / "pairing-code"
    code.write_text("existing operator code")
    monkeypatch.setattr(mobile, "RUNTIME", runtime)
    monkeypatch.setattr(mobile.os, "geteuid", lambda: 0)
    # Ownership only; use real files and real lock allocation.
    original = Path.lstat
    def metadata(p):
        result = original(p)
        return SimpleNamespace(st_mode=result.st_mode, st_uid=0, st_size=result.st_size)
    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(fcntl, "flock", lambda *a: (_ for _ in ()).throw(BlockingIOError("private token")))
    assert mobile.main([]) == 1
    assert code.read_text() == "existing operator code"
    assert "private token" not in capsys.readouterr().out


def test_runtime_uses_signal_callback_not_reentrant_signal_pairing(tmp_path):
    # Exercise a real asyncio signal callback with a private test process only.
    script = '''import asyncio,os,signal
from xenova_display.mobile_access import Pairing,create_app
from xenova_display.mobile_network import Endpoint
from xenova_display.mobile_upstream import LocalFeed
async def run():
 p=Pairing();p.rotate();p.session="old";p.session_until=10**20
 app=create_app(Endpoint("test","127.0.0.1","127.0.0.1",8084),LocalFeed("test-only"),p,rotate_signal=True)
 async with app.router.lifespan_context(app):
  os.kill(os.getpid(),signal.SIGUSR1)
  await asyncio.sleep(.05)
  assert p.session=="" and len(p.code)==6
asyncio.run(run())
'''
    import sys
    subprocess.run([sys.executable, "-c", script], check=True, timeout=4, cwd=ROOT / "platform/services/display/router")


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze required")
def test_real_unit_verification_without_touching_system_units(tmp_path):
    stage = tmp_path / "root"
    units = stage / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    binary = stage / "usr/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    for target in ("sysinit.target", "basic.target", "shutdown.target", "network.target", "multi-user.target"):
        (units / target).write_text("[Unit]\nDescription=local test target\n")
    (units / "xenova-display-feed.service").write_text("[Service]\nExecStart=/usr/bin/python3\n")
    unit = units / "xenova-display-mobile.service"
    unit.write_text((DEPLOY / unit.name).read_text())
    result = subprocess.run(["systemd-analyze", "verify", "--man=no", "--generators=no", "--root=" + str(stage), str(unit)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
