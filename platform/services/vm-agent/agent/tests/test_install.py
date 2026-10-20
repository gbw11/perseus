"""rootfs 설치: paho 사전 검사, 두 패키지 복사, 유닛 설치·enable, 신원 drop-in, 심볼릭 링크 거부."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import install_into_rootfs as installer  # noqa: E402

SOURCE = Path(__file__).resolve().parents[2]  # services/vm-agent
UNIT = "usr/lib/systemd/system/xenova-agent.service"
LINK = "etc/systemd/system/multi-user.target.wants/xenova-agent.service"


def _symlinks_supported(tmp_path: Path) -> bool:
    try:
        (tmp_path / "probe-link").symlink_to("probe-target")
    except (OSError, NotImplementedError):
        return False
    return True


def _rootfs(tmp_path: Path, *, paho: bool = True) -> Path:
    root = tmp_path / "rootfs"
    root.mkdir()
    if paho:
        client = root / "usr/lib/python3/dist-packages/paho/mqtt/client.py"
        client.parent.mkdir(parents=True)
        client.write_text("# apt python3-paho-mqtt\n", encoding="utf-8")
    return root


def test_install_copies_packages_and_unit(tmp_path):
    root = _rootfs(tmp_path)
    enable = _symlinks_supported(tmp_path)
    installed = installer.install(root, SOURCE, enable=enable)

    assert (root / "opt/xenova/mqtt-client/perseus_mqtt/__init__.py").is_file()
    assert (root / "opt/xenova/agent/xenova_agent/heartbeat.py").is_file()
    assert not (root / "opt/xenova/agent/xenova_agent/tests").exists()
    assert not list(root.glob("opt/xenova/**/__pycache__"))
    text = (root / UNIT).read_text(encoding="utf-8")
    directives = [line for line in text.splitlines() if line and not line.startswith("#")]
    # 일반 생성 VM(prepare_instance)에는 신원 유닛이 없으므로 기본 유닛은 순서만 두고 Requires= 는 두지 않는다
    assert not any(line.startswith("Requires=") for line in directives)
    assert "After=network-online.target xenova-guest-identity.service" in directives
    assert "ConditionPathExists=/etc/xenova/instance-id" in directives
    assert "ExecStart=/usr/bin/python3 -m xenova_agent" in directives
    assert "Environment=PYTHONPATH=/opt/xenova/mqtt-client:/opt/xenova/agent" in directives
    assert root / UNIT in installed
    assert not (root / "etc/systemd/system/xenova-agent.service.d").exists()
    if enable:
        assert (root / LINK).is_symlink() and os.readlink(root / LINK) == "/usr/lib/systemd/system/xenova-agent.service"


def test_install_is_idempotent(tmp_path):
    root = _rootfs(tmp_path)
    enable = _symlinks_supported(tmp_path)
    first = installer.install(root, SOURCE, enable=enable)
    second = installer.install(root, SOURCE, enable=enable)  # 자기가 만든 enable 링크를 위험한 링크로 보지 않는다
    assert first == second
    if enable:
        assert os.readlink(root / LINK) == "/usr/lib/systemd/system/xenova-agent.service"


def test_existing_correct_enable_link_is_kept(tmp_path):
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlinks unavailable on this host")
    root = _rootfs(tmp_path)
    (root / LINK).parent.mkdir(parents=True)
    (root / LINK).symlink_to("/usr/lib/systemd/system/xenova-agent.service")
    installed = installer.install(root, SOURCE, enable=True)
    assert root / LINK in installed and os.readlink(root / LINK) == "/usr/lib/systemd/system/xenova-agent.service"


def test_enable_link_to_a_different_target_is_rejected_before_install(tmp_path):
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlinks unavailable on this host")
    root = _rootfs(tmp_path)
    (root / LINK).parent.mkdir(parents=True)
    (root / LINK).symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="unexpected enable link target"):
        installer.install(root, SOURCE, enable=True)
    assert not (root / "opt").exists() and not (root / UNIT).exists()


def test_regular_file_at_enable_link_is_rejected_before_install(tmp_path):
    root = _rootfs(tmp_path)
    (root / LINK).parent.mkdir(parents=True)
    (root / LINK).write_text("not a link\n", encoding="utf-8")
    with pytest.raises(ValueError, match="regular file"):
        installer.install(root, SOURCE, enable=True)
    assert not (root / "opt").exists() and not (root / UNIT).exists()
    assert (root / LINK).read_text(encoding="utf-8") == "not a link\n"


def test_missing_paho_installs_nothing(tmp_path):
    root = _rootfs(tmp_path, paho=False)
    with pytest.raises(ValueError, match="python3-paho-mqtt"):
        installer.install(root, SOURCE, enable=False)
    assert not (root / "opt").exists() and not (root / UNIT).exists()
    installer.install(root, SOURCE, enable=False, require_paho=False)  # 명시적으로 허용하면 설치한다
    assert (root / UNIT).is_file()


def test_paho_check_needs_the_real_module_file(tmp_path):
    root = tmp_path / "rootfs"
    (root / "usr/lib/python3/dist-packages/paho").mkdir(parents=True)
    assert installer.paho_installed(root) is False  # 빈 paho/ 디렉터리는 설치가 아니다
    (root / "usr/lib/python3/dist-packages/paho/mqtt").mkdir()
    (root / "usr/lib/python3/dist-packages/paho/mqtt/client.py").write_text("", encoding="utf-8")
    assert installer.paho_installed(root) is True


def test_identity_dropin_only_where_identity_unit_exists(tmp_path):
    """deployment seed 경로(신원 유닛 있음)에서만 Requires=xenova-guest-identity.service 를 drop-in 으로 붙인다."""
    root = _rootfs(tmp_path)
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    assert installer.install_identity_dropin(root) is None
    (root / "usr/lib/systemd/system/xenova-guest-identity.service").write_text("[Unit]\n", encoding="utf-8")
    dropin = installer.install_identity_dropin(root)
    assert dropin == root / "etc/systemd/system/xenova-agent.service.d/10-xenova-identity.conf"
    assert dropin.read_text(encoding="utf-8") == "[Unit]\nRequires=xenova-guest-identity.service\nAfter=xenova-guest-identity.service\n"
    assert dropin in installer.install(root, SOURCE, enable=False)  # 전체 설치를 돌려도 같은 drop-in 이 들어간다


def test_refuses_to_write_through_symlink(tmp_path):
    """손상된 rootfs 의 유닛 자리가 호스트 파일로 가는 링크여도 따라가지 않는다. 아무것도 설치하지 않는다."""
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlinks unavailable on this host")
    root = _rootfs(tmp_path)
    outside = tmp_path / "host-file"
    outside.write_text("host\n", encoding="utf-8")
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    (root / UNIT).symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        installer.install(root, SOURCE, enable=False)
    assert outside.read_text(encoding="utf-8") == "host\n"
    assert not (root / "opt").exists()


def test_dropin_symlink_is_checked_before_anything_is_installed(tmp_path):
    """신원 유닛이 있는 rootfs 에서 drop-in 자리가 링크면, 패키지·유닛을 쓰기 전에 거부한다 (부분 설치 없음)."""
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlinks unavailable on this host")
    root = _rootfs(tmp_path)
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    (root / "usr/lib/systemd/system/xenova-guest-identity.service").write_text("[Unit]\n", encoding="utf-8")
    outside = tmp_path / "host-dropin"
    outside.write_text("host\n", encoding="utf-8")
    (root / "etc/systemd/system/xenova-agent.service.d").mkdir(parents=True)
    (root / "etc/systemd/system/xenova-agent.service.d/10-xenova-identity.conf").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        installer.install(root, SOURCE, enable=False)
    assert outside.read_text(encoding="utf-8") == "host\n"
    assert not (root / "opt").exists() and not (root / UNIT).exists()


def test_unit_runs_without_privileges(tmp_path):
    root = _rootfs(tmp_path)
    installer.install(root, SOURCE, enable=False)
    directives = [line for line in (root / UNIT).read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    for expected in ("CapabilityBoundingSet=", "NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true"):
        assert expected in directives


def test_cli_exit_codes(tmp_path, capsys):
    root = _rootfs(tmp_path, paho=False)
    assert installer.main([str(root), "--source", str(SOURCE), "--no-enable"]) == 1  # paho 없음: 설치 안 함
    assert "python3-paho-mqtt" in capsys.readouterr().err and not (root / UNIT).exists()
    assert installer.main([str(root), "--source", str(SOURCE), "--no-enable", "--allow-missing-paho"]) == 0
    assert installer.main([str(tmp_path / "missing"), "--source", str(SOURCE)]) == 2


def test_rejects_source_without_packages(tmp_path):
    root = _rootfs(tmp_path)
    with pytest.raises(ValueError, match="package source missing"):
        installer.install(root, tmp_path / "empty-source", enable=False)
