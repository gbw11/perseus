from __future__ import annotations

import pytest

from xenova_manual.config import ManualSettings

VM_ID = "22222222-2222-4222-8222-222222222222"


def test_defaults_from_env():
    settings = ManualSettings.from_env({"XENOVA_MANUAL_VM_ID": VM_ID.upper()})
    assert settings.vm_id == VM_ID
    assert settings.broker_host == "192.168.77.1"
    assert settings.username == "manual"  # ACL username = app_id
    assert settings.command_rate_hz == 50.0
    assert settings.input_timeout_ms == 200.0


def test_overrides():
    settings = ManualSettings.from_env({
        "XENOVA_MANUAL_VM_ID": VM_ID,
        "XENOVA_MANUAL_BROKER_HOST": "127.0.0.1",
        "XENOVA_MANUAL_BROKER_PORT": "18830",
        "XENOVA_MANUAL_BROKER_PASSWORD": "pw",
        "XENOVA_MANUAL_MAX_SPEED_MPS": "0.3",
        "XENOVA_MANUAL_LOG_LEVEL": "debug",
    })
    assert (settings.broker_host, settings.broker_port, settings.password) == ("127.0.0.1", 18830, "pw")
    assert settings.max_speed_mps == 0.3
    assert settings.log_level == "DEBUG"


def test_vm_id_from_instance_id_file(tmp_path):
    path = tmp_path / "instance-id"
    path.write_text(VM_ID + "\n", encoding="ascii")
    assert ManualSettings.from_env({}, instance_id_path=path).vm_id == VM_ID


def test_missing_vm_id(tmp_path):
    with pytest.raises(ValueError, match="vm_id is not set"):
        ManualSettings.from_env({}, instance_id_path=tmp_path / "none")


@pytest.mark.parametrize(
    ("name", "value"),
    [("XENOVA_MANUAL_BROKER_PORT", "0"), ("XENOVA_MANUAL_MAX_SPEED_MPS", "-1"),
     ("XENOVA_MANUAL_INPUT_TIMEOUT_MS", "nan"), ("XENOVA_MANUAL_LOG_LEVEL", "LOUD"), ("XENOVA_MANUAL_VM_ID", "abc")],
)
def test_invalid(name, value):
    env = {"XENOVA_MANUAL_VM_ID": VM_ID, name: value}
    with pytest.raises(ValueError):
        ManualSettings.from_env(env)
