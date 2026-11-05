"""설정: 환경변수 우선, 없으면 /etc/xenova/instance-id. 잘못된 값은 시작 시점 오류."""

from __future__ import annotations

import pytest

from xenova_agent.config import DEFAULT_BROKER_HOST, AgentSettings

VM_ID = "11111111-1111-4111-8111-111111111111"


def test_env_overrides_and_defaults(tmp_path):
    s = AgentSettings.from_env(
        {"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_BROKER_PORT": "18830", "XENOVA_AGENT_LOG_LEVEL": "debug"},
        instance_id_path=tmp_path / "missing",
    )
    assert (s.vm_id, s.broker_host, s.broker_port, s.heartbeat_interval_s, s.log_level) == (VM_ID, DEFAULT_BROKER_HOST, 18830, 5.0, "DEBUG")
    assert s.username is None and s.password is None


def test_vm_id_falls_back_to_instance_id_file(tmp_path):
    path = tmp_path / "instance-id"
    path.write_text(VM_ID + "\n", encoding="ascii")
    assert AgentSettings.from_env({}, instance_id_path=path).vm_id == VM_ID


def test_missing_vm_id_is_a_startup_error(tmp_path):
    with pytest.raises(ValueError, match="vm_id is not set"):
        AgentSettings.from_env({}, instance_id_path=tmp_path / "missing")


@pytest.mark.parametrize("env", [
    {"XENOVA_AGENT_VM_ID": "not-a-uuid"},
    {"XENOVA_AGENT_VM_ID": "00000000-0000-0000-0000-000000000000"},  # nil UUID 는 Domain-0 몫
    {"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_HEARTBEAT_INTERVAL": "5"},  # 주기는 계약값이라 환경변수로 바꿀 수 없다
    {"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_HEARTBEAT_INTERVAL": "60"},
    {"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_BROKER_PORT": "70000"},
    {"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_LOG_LEVEL": "verbose"},
])
def test_invalid_values_are_rejected(env, tmp_path):
    with pytest.raises(ValueError):
        AgentSettings.from_env(env, instance_id_path=tmp_path / "missing")


@pytest.mark.parametrize("raw", [VM_ID.upper(), "{" + VM_ID + "}", VM_ID.replace("-", ""), " " + VM_ID + "\n"])
def test_vm_id_is_normalised_to_canonical_lowercase(raw, tmp_path):
    """토픽과 봉투의 vm_id 는 Spring·registry 의 문자열과 글자까지 같아야 한다."""
    assert AgentSettings.from_env({"XENOVA_AGENT_VM_ID": raw}, instance_id_path=tmp_path / "missing").vm_id == VM_ID
    assert AgentSettings(vm_id=raw).vm_id == VM_ID


def test_interval_is_the_contract_value_and_only_tests_override_it(tmp_path):
    assert AgentSettings.from_env({"XENOVA_AGENT_VM_ID": VM_ID}, instance_id_path=tmp_path / "missing").heartbeat_interval_s == 5.0
    assert AgentSettings(vm_id=VM_ID, heartbeat_interval_s=0.05).heartbeat_interval_s == 0.05
    for bad in (0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            AgentSettings(vm_id=VM_ID, heartbeat_interval_s=bad)


def test_blank_env_value_means_default(tmp_path):
    s = AgentSettings.from_env({"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_BROKER_HOST": "  "}, instance_id_path=tmp_path / "missing")
    assert s.broker_host == DEFAULT_BROKER_HOST
    with pytest.raises(ValueError):
        AgentSettings(vm_id=VM_ID, broker_host="")


def test_metrics_settings_have_defaults_and_bounds(tmp_path):
    s = AgentSettings.from_env({"XENOVA_AGENT_VM_ID": VM_ID}, instance_id_path=tmp_path / "missing")
    assert (s.metrics_interval_s, s.top_processes) == (2.0, 10)
    s = AgentSettings.from_env({"XENOVA_AGENT_VM_ID": VM_ID, "XENOVA_AGENT_METRICS_INTERVAL": "5", "XENOVA_AGENT_TOP_PROCESSES": "0"},
                               instance_id_path=tmp_path / "missing")
    assert (s.metrics_interval_s, s.top_processes) == (5.0, 0)
    for env in ({"XENOVA_AGENT_METRICS_INTERVAL": "0"}, {"XENOVA_AGENT_TOP_PROCESSES": "51"}, {"XENOVA_AGENT_TOP_PROCESSES": "-1"}):
        with pytest.raises(ValueError):
            AgentSettings.from_env({"XENOVA_AGENT_VM_ID": VM_ID, **env}, instance_id_path=tmp_path / "missing")


def test_demo_commands_default_off_and_env_switch(tmp_path):
    base = {"XENOVA_AGENT_VM_ID": VM_ID}
    assert AgentSettings.from_env(base, instance_id_path=tmp_path / "missing").demo_commands is False
    assert AgentSettings.from_env({**base, "XENOVA_AGENT_DEMO_COMMANDS": "1"}, instance_id_path=tmp_path / "missing").demo_commands is True
    with pytest.raises(ValueError, match="expected a boolean"):
        AgentSettings.from_env({**base, "XENOVA_AGENT_DEMO_COMMANDS": "maybe"}, instance_id_path=tmp_path / "missing")
