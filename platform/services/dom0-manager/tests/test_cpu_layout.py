"""CPU 배치 조회 (S15P31E110-353): 플랫폼 cpu-layout 응답(platform/tests/fixtures/cpu-layout-responses.json)을 그대로 옮기는지,
요청 예산을 timeout_ms 로만 줄이는지, 긴 변경 작업과 줄 서지 않는지, 오류를 계약 코드로 내는지 본다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from conftest import VM_A
from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.drivers.platform import SerializedManager
from dom0_manager.main import create_app
from test_control_operations import Manager, app_for, failure

FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "cpu-layout-responses.json").read_text(encoding="utf-8"))
FIXTURE_VM = "e158a936-304e-42b8-bbc1-e2353175f43e"


@pytest.fixture
def manager():
    manager = Manager()
    manager.responses["cpu-layout"] = FIXTURES["running"]
    return manager


@pytest.fixture
def client(manager):
    return TestClient(app_for(manager))


def layout(client, vm_id=None, **headers):
    path = "/api/v1/cpu-layout" if vm_id is None else f"/api/v1/vms/{vm_id}/cpu-layout"
    return client.get(path, headers=headers)


def cpu_calls(manager):
    return [c for c in manager.calls if c["action"] == "cpu-layout"]


# ---- 응답 의미 보존 ----


@pytest.mark.parametrize("name", ["unconfigured", "stopped", "running", "mismatch"])
def test_platform_layout_is_returned_field_for_field(client, manager, name):
    manager.responses["cpu-layout"] = FIXTURES[name]
    response = layout(client)
    assert response.status_code == 200, response.text
    body, source = response.json(), FIXTURES[name]["data"]
    for key in ("configured", "policy_revision", "host_boot_id", "observed_monotonic_ms", "host"):
        assert body[key] == source[key], key
    [vm], [original] = body["vms"], source["vms"]
    assert vm == original  # null(정지·공유·미설정)을 true/false 로 채우지 않는다
    assert body["issues"] == source["issues"]  # 코드마다 쓰는 필드만 있다


def test_unconfigured_policy_keeps_nulls_and_not_ready(client, manager):
    manager.responses["cpu-layout"] = FIXTURES["unconfigured"]
    body = layout(client).json()
    assert body["configured"] is False and body["policy_revision"] is None and body["host"]["ready"] is False
    vm = body["vms"][0]
    assert (vm["allocation"], vm["desired_hard_affinity"], vm["affinity_matches"], vm["exclusive_verified"]) == (None, None, None, None)


def test_mismatch_is_200_with_issues_not_an_error(client, manager):
    manager.responses["cpu-layout"] = FIXTURES["mismatch"]
    body = layout(client).json()
    assert body["host"]["ready"] is False
    vm = body["vms"][0]
    assert vm["affinity_matches"] is True and vm["exclusive_verified"] is False
    [conflict] = vm["issues"][0]["conflicts"]
    assert (conflict["domid"], conflict["vm_id"], conflict["name"], conflict["pcpus"]) == (0, None, "Domain-0", [1])
    assert [i["code"] for i in body["issues"]][:2] == ["CPU_NOT_READY", "EXCLUSIVE_CONFLICT"]


def test_policy_2_reservations_and_1_0_reservations_have_the_same_shape(client, manager):
    data = copy.deepcopy(FIXTURES["running"])
    data["data"]["policy_schema_version"] = "2.0"
    data["data"]["reservations"] = [{"vm_id": FIXTURE_VM, "mode": "exclusive", "pcpus": [1, 2]}]
    manager.responses["cpu-layout"] = data
    assert layout(client).json()["reservations"] == [{"vm_id": FIXTURE_VM, "pcpus": [1, 2]}]
    manager.responses["cpu-layout"] = FIXTURES["running"]  # 1.0: {vm_id, pcpu}
    assert layout(client).json()["reservations"][1] == {"vm_id": FIXTURE_VM, "pcpus": [1]}


def test_unknown_platform_fields_are_not_passed_through(client, manager):
    data = copy.deepcopy(FIXTURES["running"])
    data["data"]["receipt_path"] = "/srv/xenova/cpu/receipt.json"
    data["data"]["vms"][0]["internal"] = {"journal": "x"}
    manager.responses["cpu-layout"] = data
    body = layout(client).json()
    assert "receipt_path" not in body and "internal" not in body["vms"][0]


@pytest.mark.parametrize("damage", [
    lambda d: d["data"]["vms"][0].__setitem__("affinity_matches", "true"),  # 문자열 불리언을 바꿔 받지 않는다
    lambda d: d["data"].pop("host"),
    lambda d: d["data"]["host"].__setitem__("observed_dom0_hard_affinity", None),
    lambda d: d["data"]["vms"][0]["observed"].__setitem__("vcpus", "1"),
    lambda d: d.__setitem__("data", None),
    lambda d: d["data"].__setitem__("observed_monotonic_ms", float("nan")),  # JSON 수치가 아닌 값
    lambda d: d["data"].__setitem__("host_boot_id", {"set"}),  # JSON 으로 옮길 수 없는 값(json.dumps TypeError)
    lambda d: d["data"]["vms"][0]["observed"].__setitem__("presence", "gone"),
    lambda d: d["data"]["vms"][0]["observed"].__setitem__("presence", "absent"),  # 관측값이 남은 absent
    lambda d: d["data"]["vms"][0]["observed"].__setitem__("domid", None),  # 관측값이 빠진 present
    lambda d: d["data"]["vms"][0].__setitem__("allocation", "dedicated"),
    lambda d: d["data"]["issues"].append({"code": "SOMETHING_ELSE"}),
    lambda d: d["data"].__setitem__("reservations", [{"vm_id": FIXTURE_VM, "mode": "shared", "pcpus": [1]}]),  # 공유를 전용으로 바꾸지 않는다
    lambda d: d["data"].__setitem__("reservations", [{"vm_id": FIXTURE_VM, "pcpus": [1]}]),  # 2.0 인데 mode 없음
    lambda d: d["data"].__setitem__("reservations", [{"vm_id": FIXTURE_VM, "pcpu": 1, "pcpus": [1]}]),  # 두 형식이 섞임
    lambda d: d["data"].__setitem__("reservations", [{"vm_id": FIXTURE_VM, "pcpu": 1, "mode": "exclusive"}]),
])
def test_malformed_platform_layout_is_503_not_a_guess(client, manager, damage):
    data = copy.deepcopy(FIXTURES["running"])
    damage(data)
    manager.responses["cpu-layout"] = data
    response = layout(client)
    assert response.status_code == 503 and response.json()["code"] == "PlatformUnavailable"
    assert "malformed" in response.json()["message"]


@pytest.mark.parametrize("vms", [
    lambda vm: [],  # 빈 목록
    lambda vm: [{**vm, "vm_id": "22222222-2222-4222-8222-222222222222"}],  # 다른 VM
    lambda vm: [vm, {**vm, "vm_id": "22222222-2222-4222-8222-222222222222"}],  # 여러 VM
])
def test_single_vm_layout_must_be_exactly_the_requested_vm(client, manager, vms):
    data = copy.deepcopy(FIXTURES["running"])
    data["data"]["vms"] = vms({**data["data"]["vms"][0], "vm_id": str(VM_A)})
    manager.responses["cpu-layout"] = data
    response = layout(client, VM_A)
    assert response.status_code == 503 and response.json()["code"] == "PlatformUnavailable"
    assert "not this VM" in response.json()["message"]


def test_single_vm_layout_of_the_requested_vm_is_200(client, manager):
    data = copy.deepcopy(FIXTURES["running"])
    data["data"]["vms"][0]["vm_id"] = str(VM_A)
    manager.responses["cpu-layout"] = data
    response = layout(client, VM_A)
    assert response.status_code == 200 and [vm["vm_id"] for vm in response.json()["vms"]] == [str(VM_A)]


# ---- 하위 호출 ----


def test_whole_and_single_vm_specs(client, manager):
    assert layout(client).status_code == 200
    assert layout(client, FIXTURE_VM).status_code == 200
    assert [c["spec"] for c in cpu_calls(manager)] == [{"timeout_ms": 5000}, {"vm_id": FIXTURE_VM, "timeout_ms": 5000}]
    assert all(c["budget_ms"] is None for c in cpu_calls(manager))


def test_request_budget_only_shortens_timeout_ms(manager):
    """하위 cpu-layout 은 budget_ms 를 받지 않는다(넘기면 INVALID_SPEC). 요청 예산은 timeout_ms 로만 줄인다."""
    client = TestClient(app_for(manager, response_reserve_s=0.5))
    assert layout(client, **{"X-Request-Budget-Ms": "3000"}).status_code == 200
    [call] = cpu_calls(manager)
    assert call["budget_ms"] is None and 0 < call["spec"]["timeout_ms"] <= 2500


def test_exhausted_budget_sends_nothing(manager):
    client = TestClient(app_for(manager, response_reserve_s=0.5))
    response = layout(client, **{"X-Request-Budget-Ms": "100"})
    assert response.status_code == 504 and cpu_calls(manager) == []


def test_layout_does_not_queue_behind_a_long_mutation_with_read_fork():
    inner = Manager()
    inner.responses["cpu-layout"] = FIXTURES["running"]
    serialized = SerializedManager(inner, busy_timeout_s=0.2, read_fork=True)
    client = TestClient(app_for(serialized))
    assert serialized._lock.acquire(timeout=1)  # noqa: SLF001 - 긴 stop·start 가 공유 Manager 를 쥐고 있다
    try:
        assert layout(client).status_code == 200
        assert [c["forked"] for c in cpu_calls(inner)] == [True]
    finally:
        serialized._lock.release()  # noqa: SLF001


# ---- 오류 ----


@pytest.mark.parametrize("response_env,status,code,retryable", [
    (FIXTURES["observation_failure"], 503, "PlatformUnavailable", False),  # 빈 마스크·0 으로 성공을 대체하지 않는다
    (failure("OPERATION_IN_PROGRESS", retryable=True), 503, "PlatformUnavailable", True),  # 플랫폼은 잠금을 기다리지 않는다
    (failure("TARGET_CHANGED"), 503, "PlatformUnavailable", True),  # 관측 중 domain set 이 바뀜: 다시 읽는다(409 아님)
    (failure("DEADLINE_EXCEEDED"), 504, "DriverTimeout", False),
    (failure("VM_NOT_FOUND"), 404, "VMNotFound", False),
    (failure("INVALID_SPEC"), 501, "NotSupported", None),  # cpu-layout 을 모르는 옛 Manager
    (failure("CPU_POLICY_INVALID"), 500, "DriverError", False),  # 손상 정책: 재시도로 풀리지 않는다
])
def test_platform_errors_are_contract_errors(client, manager, response_env, status, code, retryable):
    manager.responses["cpu-layout"] = response_env
    response = layout(client, VM_A)
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code and body["platform_code"] == response_env["error"]["code"]
    if retryable is not None:
        assert body["retryable"] is retryable


def test_domain_0_is_400_and_nothing_is_sent(client, manager):
    response = layout(client, UUID(int=0))
    assert response.status_code == 400 and cpu_calls(manager) == []


def test_drivers_without_cpu_layout_are_501():
    client = TestClient(create_app(Settings(driver="mock", log_level="WARNING"), driver=MockDriver(host_cpus=4, total_memory_mib=1024),
                                   provisioner=MockProvisioner()))
    for vm_id in (None, VM_A):
        response = layout(client, vm_id)
        assert response.status_code == 501 and response.json()["code"] == "NotSupported"


def test_layout_never_controls_anything(client, manager):
    layout(client)
    layout(client, VM_A)
    assert {c["action"] for c in manager.calls} == {"cpu-layout"}
