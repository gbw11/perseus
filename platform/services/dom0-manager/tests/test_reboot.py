"""VM 재부팅 (S15P31E110-208): 키 없는 동기 재부팅과 키 있는 추적(정지와 같은 원장·조회·예산).

하위 Manager 봉투는 test_control_operations 와 같은 모양(cli-manager-spec 공통 계약 A)을 쓴다.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from conftest import VM_A, create_body
from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.main import create_app
from test_control_operations import HOST, KEY, OP, VIEW, Manager, accepted, app_for, envelope, failure

NEW_EXECUTION = "77777777-7777-4777-8777-777777777777"
REBOOTED = envelope(operation_id=OP, data={"vm": {**VIEW, "domid": 4, "execution_id": NEW_EXECUTION},
                                           "original_result": {"accepted": True, "intent_revision": 12}})


@pytest.fixture
def manager():
    manager = Manager()
    manager.responses["reboot"] = REBOOTED
    manager.responses["control-operation"] = failure("VM_NOT_FOUND")  # 같은 키의 원 작업 없음: 신규 요청
    return manager


def original(phase="executing"):
    record = {"operation_id": OP, "vm_id": str(VM_A), "request": {"vm_id": str(VM_A), "action": "reboot", "force": False},
              "idempotency_key": KEY, "phase": phase, "intent_revision": 12, "host_boot_id": HOST}
    return envelope(operation_id=OP, data={"operation": record, "deadline_observation": {}})


@pytest.fixture
def client(manager):
    return TestClient(app_for(manager))


def reboot(client, key=KEY, vm_id=VM_A, **headers):
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(f"/api/v1/vms/{vm_id}/reboot", headers=headers)


def actions(manager):
    return [c["action"] for c in manager.calls]


# ---- 키 없음: 동기 ----


def test_keyless_reboot_of_a_running_vm_is_204_after_the_new_execution(client, manager):
    response = reboot(client, key=None)
    assert response.status_code == 204 and response.content == b""
    [call] = [c for c in manager.calls if c["action"] == "reboot"]
    assert (call["spec"], call["key"], call["forked"]) == ({"vm_id": str(VM_A), "force": False}, None, False)  # force 는 넘기지 않는다


@pytest.mark.parametrize("view", [
    {**VIEW, "domid": None, "xen_state": "stopped"},  # 꺼짐: start 로 켠다
    {**VIEW, "xen_state": "paused"},
    {**VIEW, "xen_state": "crashed"},  # 정지 뒤 시작
    {**VIEW, "xen_state": "shutdown"},
])
def test_keyless_reboot_of_a_vm_that_is_not_running_is_409_and_nothing_is_sent(client, manager, view):
    manager.responses["get"] = envelope(data={"vm": view})
    response = reboot(client, key=None)
    assert response.status_code == 409 and response.json()["code"] == "InvalidState"
    assert "reboot" not in actions(manager)


def test_keyless_reboot_of_an_unregistered_vm_is_404(client, manager):
    manager.responses["get"] = failure("VM_NOT_FOUND")
    response = reboot(client, key=None)
    assert response.status_code == 404 and response.json()["code"] == "VMNotFound"  # stop 과 달리 목표(새 실행)를 달성할 수 없다
    assert "reboot" not in actions(manager)


def test_reboot_of_domain_0_is_400(client, manager):
    for key in (None, KEY):
        response = reboot(client, key=key, vm_id=UUID(int=0))
        assert response.status_code == 400 and response.json()["code"] == "InvalidArgument"
    assert manager.calls == []


# ---- 플랫폼의 수락 전 거절 ----


@pytest.mark.parametrize("message,status,code", [
    ("cannot reboot a stopped VM", 409, "InvalidState"),  # 확인한 뒤 그 사이 꺼졌다
    ("Zephyr graceful reboot unsupported; use reboot --force", 501, "NotSupported"),  # 협력 제어 채널이 없는 Zephyr
])
@pytest.mark.parametrize("key", [None, KEY])
def test_platform_refusals_are_contract_errors(client, manager, message, status, code, key):
    manager.responses["reboot"] = envelope(ok=False, error={"code": "ARTIFACT_INVALID", "message": message, "retryable": False, "details": {}})
    response = reboot(client, key=key)
    assert response.status_code == status and response.json()["code"] == code and response.json()["platform_code"] == "ARTIFACT_INVALID"


def test_other_artifact_invalid_messages_stay_500(client, manager):
    manager.responses["reboot"] = envelope(ok=False, error={"code": "ARTIFACT_INVALID", "message": "instance config invalid: cannot reboot a stopped VM",
                                                            "retryable": False, "details": {}})
    assert reboot(client, key=None).status_code == 500  # 문구가 앞에 올 때만 가린다


# ---- 키 있음: 추적 ----


def test_keyed_new_reboot_checks_the_original_then_the_state_then_submits(client, manager):
    response = reboot(client)
    assert response.status_code == 200
    body = response.json()
    assert (body["action"], body["status"], body["accepted"], body["operation_id"], body["request_key"]) == ("reboot", "completed", True, OP, KEY)
    assert body["intent_revision"] == 12
    assert actions(manager)[0] == "control-operation" and actions(manager)[-1] == "reboot" and "get" in actions(manager)
    submitted = manager.calls[-1]
    assert (submitted["key"], submitted["forked"], submitted["spec"]) == (KEY, True, {"vm_id": str(VM_A), "force": False})


@pytest.mark.parametrize("view", [
    {**VIEW, "domid": None, "xen_state": "stopped"},
    {**VIEW, "xen_state": "paused"},
    {**VIEW, "xen_state": "crashed"},
    {**VIEW, "xen_state": "dying"},
])
def test_key_does_not_change_which_states_can_be_rebooted(client, manager, view):
    """하위 reboot 는 도메인 존재만 본다. 신규 키 있는 요청도 키 없는 요청과 같이 실행 중이 아니면 409 이고 아무것도 보내지 않는다."""
    manager.responses["get"] = envelope(data={"vm": view})
    response = reboot(client)
    assert response.status_code == 409 and response.json()["code"] == "InvalidState"
    assert "reboot" not in actions(manager)


def test_retransmission_of_an_original_reboot_is_sent_without_the_state_check(client, manager):
    """원 작업이 있으면 재전달이다. 재부팅 중이라 도메인이 잠깐 없어도 409 로 막지 않고 원 작업을 이어 간다."""
    manager.responses["control-operation"] = original()
    manager.responses["get"] = envelope(data={"vm": {**VIEW, "domid": None, "xen_state": "stopped"}})
    manager.responses["reboot"] = accepted("OPERATION_IN_PROGRESS", completion="pending", phase="executing")
    response = reboot(client)
    assert response.status_code == 202 and response.json()["status"] == "pending"
    assert actions(manager) == ["control-operation", "reboot"]


def test_old_manager_without_lookup_is_501_and_nothing_is_sent(client, manager):
    """원 작업을 조회할 수 없으면 신규·재전달을 가를 수 없다. 신규로 가정하면 진행 중인 재부팅의 재전달을 409 로 거절하게 된다."""
    manager.responses["control-operation"] = failure("INVALID_SPEC")
    response = reboot(client)
    assert response.status_code == 501 and response.json()["code"] == "NotSupported"
    assert actions(manager) == ["control-operation"]


def test_platform_vm_not_running_code_is_409(client, manager):
    manager.responses["reboot"] = failure("VM_NOT_RUNNING")  # 확인과 제출 사이에 멈췄다(플랫폼 원자 검사)
    response = reboot(client)
    assert response.status_code == 409 and response.json()["platform_code"] == "VM_NOT_RUNNING"


def test_lookup_failure_sends_nothing(client, manager):
    manager.responses["control-operation"] = failure("OBSERVATION_UNAVAILABLE")
    response = reboot(client)
    assert response.status_code == 503 and response.json()["platform_code"] == "OBSERVATION_UNAVAILABLE"
    assert actions(manager) == ["control-operation"]  # 신규·재전달을 가르지 못한 채 보내지 않는다


def test_submitted_reboot_without_a_new_execution_is_503_not_a_timeout(client, manager):
    """명령 제출 뒤 새 실행을 확인하지 못하면 결과 미확정(RECONCILE_REQUIRED)이다. 504 로 '아무것도 안 됨' 처럼 보이지 않는다."""
    manager.responses["reboot"] = envelope(ok=False, error={"code": "RECONCILE_REQUIRED", "message": "physical control result is uncertain",
                                                            "retryable": False, "details": {}})
    response = reboot(client, key=None)
    assert response.status_code == 503 and response.json()["platform_code"] == "RECONCILE_REQUIRED"


def test_keyed_reboot_accepted_while_the_execution_lock_is_held_is_202(client, manager):
    manager.responses["reboot"] = accepted("OPERATION_IN_PROGRESS", completion="pending", phase="accepted")
    response = reboot(client)
    assert response.status_code == 202
    body = response.json()
    assert (body["action"], body["status"], body["operation_id"], body["host_boot_id"]) == ("reboot", "pending", OP, HOST)


@pytest.mark.parametrize("response_env,status,code,op_status", [
    (accepted("RECONCILE_REQUIRED", completion="pending", phase="uncertain", retryable=False), 503, "PlatformUnavailable", "uncertain"),
    (accepted("XEN_EXECUTION_FAILED", completion="failed", phase="failed", retryable=False), 500, "DriverError", "failed"),
    (accepted("OPERATION_IN_PROGRESS", completion="superseded", phase="accepted"), 409, "InvalidState", "superseded"),
])
def test_accepted_reboot_errors_carry_the_original_operation(client, manager, response_env, status, code, op_status):
    manager.responses["reboot"] = response_env
    response = reboot(client)
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code and body["operation"]["status"] == op_status and body["operation"]["action"] == "reboot"


def test_keyed_reboot_of_an_unregistered_vm_is_404(client, manager):
    manager.responses["get"] = failure("VM_NOT_FOUND")
    response = reboot(client)
    assert response.status_code == 404 and response.json()["code"] == "VMNotFound"
    assert "reboot" not in actions(manager)
    manager.responses["get"] = envelope(data={"vm": VIEW})
    manager.responses["reboot"] = failure("VM_NOT_FOUND")  # 확인한 뒤 그 사이 지워졌다
    assert reboot(client).status_code == 404


def test_keyed_reboot_passes_the_request_budget(manager):
    client = TestClient(app_for(manager, response_reserve_s=0.5))
    assert reboot(client, **{"X-Request-Budget-Ms": "10000"}).status_code == 200
    call = manager.calls[-1]
    assert call["action"] == "reboot" and 0 < call["budget_ms"] <= 9500
    assert all(c["budget_ms"] is not None and c["budget_ms"] <= 9500 for c in manager.calls)  # 원 작업 조회·상태 확인도 같은 예산 안


def test_lookup_returns_the_original_reboot(client, manager):
    manager.responses["control-operation"] = original()
    record = original()["data"]["operation"]
    response = client.get(f"/api/v1/vms/{VM_A}/control-operations", params={"request_key": KEY})
    assert response.status_code == 200
    assert (response.json()["action"], response.json()["status"]) == ("reboot", "pending")
    forced = {**record, "request": {**record["request"], "force": True}}  # 강제 재부팅 기록은 이 조회의 대상이 아니다
    manager.responses["control-operation"] = envelope(operation_id=OP, data={"operation": forced, "deadline_observation": {}})
    assert client.get(f"/api/v1/vms/{VM_A}/control-operations", params={"request_key": KEY}).status_code == 503


# ---- 작업 기록이 없는 드라이버 ----


def mock_client():
    driver, provisioner = MockDriver(host_cpus=4, total_memory_mib=1024), MockProvisioner()
    return TestClient(create_app(Settings(driver="mock", log_level="WARNING"), driver=driver, provisioner=provisioner)), driver


def test_mock_reboot_starts_a_new_domain_and_keyed_reboot_completes_without_an_operation_id():
    client, driver = mock_client()
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    before = driver.get_domain(VM_A).domid
    assert reboot(client, key=None).status_code == 204
    after = driver.get_domain(VM_A).domid
    assert after != before
    body = reboot(client).json()
    assert (body["action"], body["status"], body["operation_id"]) == ("reboot", "completed", None)
    assert driver.get_domain(VM_A).domid != after


def test_mock_reboot_of_a_stopped_or_unknown_vm():
    client, _ = mock_client()
    assert reboot(client, key=None).status_code == 404  # 등록 없음
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204
    response = reboot(client, key=None)
    assert response.status_code == 409 and response.json()["code"] == "InvalidState"


def test_driver_without_reboot_is_501():
    class NoReboot(MockDriver):
        reboot = MockDriver.__mro__[1].reboot  # 기본 Driver.reboot

    driver = NoReboot(host_cpus=4, total_memory_mib=1024)
    client = TestClient(create_app(Settings(driver="mock", log_level="WARNING"), driver=driver, provisioner=MockProvisioner()))
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    response = reboot(client, key=None)
    assert response.status_code == 501 and response.json()["code"] == "NotSupported"
