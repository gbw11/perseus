"""rootfs 설치: 두 패키지 복사, 유닛 설치·enable, paho 존재 확인. 실제 템플릿 준비는 플랫폼 파트가 이 스크립트를 부른다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import install_into_rootfs as installer  # noqa: E402

SOURCE = Path(__file__).resolve().parents[2]  # services/vm-agent


def _symlinks_supported(tmp_path: Path) -> bool:
    try:
        (tmp_path / "probe-link").symlink_to("probe-target")
    except (OSError, NotImplementedError):
        return False
    return True


def test_install_copies_packages_and_unit(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    enable = _symlinks_supported(tmp_path)
    installed = installer.install(root, SOURCE, enable=enable)

    assert (root / "opt/xenova/mqtt-client/perseus_mqtt/__init__.py").is_file()
    assert (root / "opt/xenova/agent/xenova_agent/heartbeat.py").is_file()
    assert not (root / "opt/xenova/agent/xenova_agent/tests").exists()
    assert not list(root.glob("opt/xenova/**/__pycache__"))
    unit = root / "usr/lib/systemd/system/xenova-agent.service"
    text = unit.read_text(encoding="utf-8")
    assert "Requires=xenova-guest-identity.service" in text and "After=network-online.target xenova-guest-identity.service" in text
    assert "ExecStart=/usr/bin/python3 -m xenova_agent" in text
    assert "PYTHONPATH=/opt/xenova/mqtt-client:/opt/xenova/agent" in text
    assert unit in installed
    if enable:
        link = root / "etc/systemd/system/multi-user.target.wants/xenova-agent.service"
        assert link.is_symlink() and os.readlink(link) == "/usr/lib/systemd/system/xenova-agent.service"


def test_install_is_idempotent(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    enable = _symlinks_supported(tmp_path)
    first = installer.install(root, SOURCE, enable=enable)
    second = installer.install(root, SOURCE, enable=enable)
    assert first == second


def test_paho_check_reports_missing_then_present(tmp_path):
    root = tmp_path / "rootfs"
    (root / "usr/lib/python3/dist-packages").mkdir(parents=True)
    assert installer.paho_installed(root) is False
    (root / "usr/lib/python3/dist-packages/paho").mkdir()
    assert installer.paho_installed(root) is True


def test_cli_exit_codes(tmp_path, capsys):
    root = tmp_path / "rootfs"
    root.mkdir()
    assert installer.main([str(root), "--source", str(SOURCE), "--no-enable"]) == 1  # 설치는 됐지만 paho 없음 경고
    assert "python3-paho-mqtt" in capsys.readouterr().err
    (root / "usr/lib/python3/dist-packages/paho").mkdir(parents=True)
    assert installer.main([str(root), "--source", str(SOURCE), "--no-enable"]) == 0
    assert installer.main([str(tmp_path / "missing"), "--source", str(SOURCE)]) == 2


def test_rejects_source_without_packages(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(ValueError, match="package source missing"):
        installer.install(root, tmp_path / "empty-source", enable=False)
