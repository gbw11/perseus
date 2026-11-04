"""정지한 Linux VM 의 자원 변경 (S15P31E110-368, 계약 4.12).

하위 update-resources·operation·control-operation 봉투는 docs/platform/cli-manager-spec.md 와 플랫폼 xenova_resources.mutate 의 모양을 따른다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import VM_A, VM_B, create_body
from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.drivers.platform import PlatformProvisioner, _deadline_envelope
from dom0_manager.errors import DriverTimeout, PlatformUnavailable
from dom0_manager.main import create_app
from test_control_operations import Manager, app_for, envelope, failure

OP = "55555555-5555-4555-8555-555555555555"
KEY = "resize-1a2b"
SHA = "a" * 64
AFTER_SHA = "b" * 64
URL = f"/api/v1/vms/{VM_A}/resources"
BODY = {"vcpus": 2, "memory_mib": 768}
STOPPED = {"vm_id": str(VM_A), "name": "vm", "lifecycle": "REGISTERED", "guest_kind": "linux", "generation": 3,
           "configuration_sha256": SHA, "configuration": {"id": f"vm-{VM_A.hex}", "vcpus": 1, "memory_mib": 512},
           "xen_state": None, "domid": None}
SPEC = {"vm_id": str(VM_A), "vcpus": 2, "memory_mib": 768, "expected_lifecycle_generation": 3, "expected_configuration_sha256": SHA}


def put(client, body=BODY, key=KEY, vm_id=VM_A, **headers):
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.put(f"/api/v1/vms/{vm_id}/resources", json=body, headers=headers)


def operation_record(spec=SPEC, *, key=KEY, action="update-resources", vm_id=VM_A, status="SUCCEEDED"):
    return {"operation_id": OP, "vm_id": str(vm_id), "idempotency_key": key, "action": action, "request": dict(spec), "status": status}


def updated(spec=SPEC, *, before=None, after=None, operation_id=OP, record=None, starts_guest=False, result_vm=VM_A):
    """update-resources 성공 봉투(xenova_resources.mutate 의 반환)."""
    result = {"vm_id": str(result_vm), "generation": 3, "before": before or {"vcpus": 1, "memory_mib": 512},
              "after": after or {"vcpus": spec["vcpus"], "memory_mib": spec["memory_mib"]},
              "configuration_sha256": AFTER_SHA, "starts_guest": starts_guest}
    return envelope(data={"operation": record or operation_record(spec), "original_result": result, "current": {}}, operation_id=operation_id)


def found(record):
    """operation 조회 성공 봉투(Manager._operation_view)."""
    return envelope(data={"operation": record, "recovery_required": False, "effective_status": record["status"]},
                    operation_id=record["operation_id"])


def platform(manager, **responses):
    manager.responses.update({"control-operation": failure("VM_NOT_FOUND"), "operation": failure("VM_NOT_FOUND"),
                              "get": envelope(data={"vm": STOPPED}), "update-resources": updated(), **responses})
    return TestClient(app_for(manager))


def actions(manager):
    return [call["action"] for call in manager.calls]


@pytest.fixture
def manager():
    return Manager()


# ---- 플랫폼: 신규·재전달 ----


def test_new_request_uses_current_generation_and_configuration_hash(manager):
    response = put(platform(manager))
    assert response.status_code == 200
    assert response.json() == {"vm_id": str(VM_A), "operation_id": OP, "before": {"vcpus": 1, "memory_mib": 512},
                               "after": {"vcpus": 2, "memory_mib": 768}, "applies_on_next_start": True}
    assert actions(manager) == ["control-operation", "operation", "get", "update-resources"]
    lookups = manager.calls[:2]
    assert all(c["spec"] == {"idempotency_key": KEY, "timeout_ms": 5000} for c in lookups)  # 두 원장 모두 VM 구분 없이 키로 찾는다
    submit = manager.calls[-1]
    assert (submit["spec"], submit["key"], submit["budget_ms"]) == (SPEC, KEY, None)


def test_replay_resends_the_original_request_without_reading_the_changed_configuration(manager):
    """변경이 반영되면 설정 해시가 바뀐다. 지금 해시로 다시 만들면 하위가 IDEMPOTENCY_CONFLICT 로 거절하므로 원 요청을 그대로 보낸다."""
    client = platform(manager, operation=found(operation_record()),
                      get=envelope(data={"vm": {**STOPPED, "configuration_sha256": AFTER_SHA, "domid": 9, "xen_state": "running"}}))
    response = put(client)
    assert response.status_code == 200 and response.json()["operation_id"] == OP
    assert actions(manager) == ["control-operation", "operation", "update-resources"]
    assert manager.calls[-1]["spec"] == SPEC


@pytest.mark.parametrize("record, why", [
    (operation_record(action="retire"), "other asset action"),
    (operation_record(vm_id=VM_B), "other vm"),
    (operation_record({**SPEC, "memory_mib": 1024}), "other values"),
])
def test_same_key_of_another_asset_request_is_409_and_nothing_is_submitted(manager, record, why):
    response = put(platform(manager, operation=found(record)))
    assert (response.status_code, response.json()["code"]) == (409, "VMExists"), why
    assert "update-resources" not in actions(manager)


def control_record(**overrides):
    """control-operation 조회 성공 봉투(Manager._query 의 control-operation)."""
    record = {"operation_id": OP, "idempotency_key": KEY, "vm_id": str(VM_B), "phase": "completed",
              "request": {"action": "stop", "vm_id": str(VM_B), "force": False}, **overrides}
    return envelope(data={"operation": record, "deadline_observation": {}}, operation_id=OP)


def test_key_used_by_a_stop_or_reboot_is_409_and_nothing_is_submitted(manager):
    response = put(platform(manager, **{"control-operation": control_record()}))
    assert (response.status_code, response.json()["code"]) == (409, "VMExists")
    assert actions(manager) == ["control-operation"]


@pytest.mark.parametrize("answer", [
    envelope(data={}, operation_id=OP),
    envelope(data={"operation": {}}, operation_id=OP),
    control_record(idempotency_key="other"),
    control_record(operation_id="66666666-6666-4666-8666-666666666666"),
    control_record(vm_id="not-a-uuid"),
])
def test_malformed_control_record_is_503_not_a_key_conflict(manager, answer):
    response = put(platform(manager, **{"control-operation": answer}))
    assert (response.status_code, response.json()["code"]) == (503, "PlatformUnavailable")
    assert "update-resources" not in actions(manager)


@pytest.mark.parametrize("ledger", ["control-operation", "operation"])
def test_manager_without_key_lookup_is_501_and_nothing_is_submitted(manager, ledger):
    response = put(platform(manager, **{ledger: failure("INVALID_SPEC")}))
    assert (response.status_code, response.json()["code"]) == (501, "NotSupported")
    assert "update-resources" not in actions(manager)


@pytest.mark.parametrize("record", [
    {**operation_record(), "operation_id": "not-a-uuid"},
    {**operation_record(), "idempotency_key": "other"},
    {**operation_record(), "request": {**SPEC, "extra": 1}},
    {**operation_record(), "request": {**SPEC, "vcpus": True}},
    {**operation_record(), "request": {**SPEC, "vm_id": str(VM_B)}},
    {**operation_record(), "request": {**SPEC, "expected_lifecycle_generation": True}},
    {**operation_record(), "request": {**SPEC, "expected_lifecycle_generation": -1}},
    {**operation_record(), "request": {**SPEC, "expected_lifecycle_generation": "3"}},
    {**operation_record(), "request": {**SPEC, "expected_configuration_sha256": "z" * 64}},
    {**operation_record(), "request": {**SPEC, "expected_configuration_sha256": "A" * 64}},
    {**operation_record(), "request": {**SPEC, "expected_configuration_sha256": "a" * 63}},
])
def test_malformed_original_record_is_503_and_nothing_is_submitted(manager, record):
    response = put(platform(manager, operation=found(record) | {"operation_id": OP}))
    assert (response.status_code, response.json()["code"]) == (503, "PlatformUnavailable")
    assert "update-resources" not in actions(manager)


# ---- 플랫폼: 신규 요청의 전제 ----


@pytest.mark.parametrize("view, status, code", [
    ({**STOPPED, "guest_kind": "zephyr"}, 400, "InvalidArgument"),
    ({**STOPPED, "domid": 4, "xen_state": "running"}, 409, "InvalidState"),
    ({**STOPPED, "generation": None}, 503, "PlatformUnavailable"),
    ({**STOPPED, "configuration_sha256": None}, 503, "PlatformUnavailable"),
    ({**STOPPED, "configuration_sha256": "z" * 64}, 503, "PlatformUnavailable"),
    ({**STOPPED, "generation": True}, 503, "PlatformUnavailable"),
])
def test_new_request_preconditions(manager, view, status, code):
    response = put(platform(manager, get=envelope(data={"vm": view})))
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert "update-resources" not in actions(manager)


def test_unregistered_vm_is_404(manager):
    response = put(platform(manager, get=failure("VM_NOT_FOUND")))
    assert (response.status_code, response.json()["code"]) == (404, "VMNotFound")
    assert "update-resources" not in actions(manager)


@pytest.mark.parametrize("code, status, name", [
    ("VM_NOT_STOPPED", 409, "InvalidState"), ("INTENT_MISMATCH", 409, "InvalidState"), ("ASSET_IN_USE", 409, "InvalidState"),
    ("RESOURCE_UNSUPPORTED", 400, "InvalidArgument"), ("IDEMPOTENCY_CONFLICT", 409, "VMExists"),
    ("OPERATION_IN_PROGRESS", 503, "PlatformUnavailable"), ("RECONCILE_REQUIRED", 503, "PlatformUnavailable"),
    ("RECOVERY_REQUIRED", 503, "PlatformUnavailable"),
])
def test_platform_refusals_keep_the_platform_code(manager, code, status, name):
    body = put(platform(manager, **{"update-resources": failure(code)})).json()
    assert (body["code"], body["platform_code"]) == (name, code)


# ---- 플랫폼: TARGET_CHANGED ----


def test_target_changed_with_a_recorded_original_finishes_with_the_same_key(manager):
    lookups = iter([failure("VM_NOT_FOUND"), found(operation_record(status="RUNNING"))])
    submits = iter([failure("TARGET_CHANGED"), updated()])
    client = platform(manager, operation=lambda spec: next(lookups), **{"update-resources": lambda spec: next(submits)})
    response = put(client)
    assert response.status_code == 200 and response.json()["operation_id"] == OP
    submitted = [c for c in manager.calls if c["action"] == "update-resources"]
    assert [(c["spec"], c["key"]) for c in submitted] == [(SPEC, KEY), (SPEC, KEY)]  # 새 키를 만들지 않는다


def test_target_changed_without_an_original_is_409_after_the_second_lookup(manager):
    response = put(platform(manager, **{"update-resources": failure("TARGET_CHANGED")}))
    body = response.json()
    assert (response.status_code, body["code"], body["platform_code"]) == (409, "InvalidState", "TARGET_CHANGED")
    assert "no operation was recorded for this key" in body["message"]
    assert actions(manager) == ["control-operation", "operation", "get", "update-resources", "control-operation", "operation"]


@pytest.mark.parametrize("second, status, code", [
    (found(operation_record({**SPEC, "memory_mib": 1024})), 409, "VMExists"),  # 그 사이 같은 키로 다른 값이 기록됨
    (failure("INVALID_SPEC"), 501, "NotSupported"),
])
def test_target_changed_keeps_409_and_501_from_the_second_lookup(manager, second, status, code):
    lookups = iter([failure("VM_NOT_FOUND"), second])
    client = platform(manager, operation=lambda spec: next(lookups), **{"update-resources": failure("TARGET_CHANGED")})
    response = put(client)
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert [c["action"] for c in manager.calls].count("update-resources") == 1


def test_target_changed_whose_lookup_fails_is_503_not_a_new_key_hint(manager):
    lookups = iter([failure("VM_NOT_FOUND"), failure("STORAGE_UNAVAILABLE")])
    client = platform(manager, operation=lambda spec: next(lookups), **{"update-resources": failure("TARGET_CHANGED")})
    body = put(client).json()
    assert (body["code"], body["platform_code"]) == ("PlatformUnavailable", "TARGET_CHANGED")
    assert "same key" in body["message"]


# ---- 플랫폼: 예산 ----


def test_lookups_get_the_budget_but_update_resources_does_not(manager):
    assert put(platform(manager), **{"X-Request-Budget-Ms": "10000"}).status_code == 200
    budgets = {c["action"]: c["budget_ms"] for c in manager.calls}
    assert budgets["control-operation"] and budgets["operation"] and budgets["update-resources"] is None


def test_exhausted_budget_before_the_request_submits_nothing(manager):
    platform(manager)
    client = TestClient(app_for(manager, response_reserve_s=0.5))
    response = client.put(URL, json=BODY, headers={"Idempotency-Key": KEY, "X-Request-Budget-Ms": "200"})
    assert (response.status_code, response.json()["code"]) == (504, "DriverTimeout")
    assert manager.calls == []


def test_deadline_only_before_submission_is_504():
    provisioner = PlatformProvisioner(Manager())
    with pytest.raises(DriverTimeout):
        provisioner._resource_result(_deadline_envelope("update-resources", None), VM_A, KEY, SPEC, "ctx")
    with pytest.raises(PlatformUnavailable):  # 제출 뒤의 시한 초과는 바뀌었을 수 있다
        provisioner._resource_result(failure("DEADLINE_EXCEEDED"), VM_A, KEY, SPEC, "ctx")


# ---- 플랫폼: 결과 strict 검증 ----


@pytest.mark.parametrize("answer", [
    updated(operation_id=None),
    updated(operation_id="66666666-6666-4666-8666-666666666666"),
    updated(record={**operation_record(), "idempotency_key": "other"}),
    updated(record={**operation_record(), "action": "retire"}),
    updated(record={**operation_record(), "vm_id": str(VM_B)}),
    updated(record=operation_record({**SPEC, "expected_configuration_sha256": AFTER_SHA})),
    updated(record=operation_record(status="RUNNING")),
    updated(after={"vcpus": 2, "memory_mib": 512}),
    updated(after={"vcpus": 2, "memory_mib": 768, "extra": 1}),
    updated(before={"vcpus": True, "memory_mib": 512}),
    updated(before={"vcpus": 1, "memory_mib": 64}),
    updated(starts_guest=True),
    updated(starts_guest=None),
    updated(result_vm=VM_B),
])
def test_malformed_platform_result_is_503(manager, answer):
    response = put(platform(manager, **{"update-resources": answer}))
    assert (response.status_code, response.json()["code"]) == (503, "PlatformUnavailable")


# ---- API 입력 ----


@pytest.mark.parametrize("body", [
    {"vcpus": 0, "memory_mib": 512}, {"vcpus": 5, "memory_mib": 512}, {"vcpus": 1, "memory_mib": 127},
    {"vcpus": 1, "memory_mib": 1025}, {"vcpus": 1}, {"vcpus": 1, "memory_mib": 512, "extra": 1},
    {"vcpus": True, "memory_mib": 512}, {"vcpus": 2.0, "memory_mib": 512}, {"vcpus": "2", "memory_mib": 512},
    {"vcpus": 2, "memory_mib": "768"}, {"vcpus": 2, "memory_mib": 768.0},
])
def test_out_of_range_body_is_400_before_the_platform(manager, body):
    response = put(platform(manager), body=body)
    assert (response.status_code, response.json()["code"]) == (400, "InvalidArgument")
    assert manager.calls == []


@pytest.mark.parametrize("key", [None, "", "한글", "x" * 201])
def test_key_is_required_and_visible_ascii(manager, key):
    client = platform(manager)
    headers = {} if key is None else {"Idempotency-Key": key.encode()}
    response = client.put(URL, json=BODY, headers=headers)
    assert (response.status_code, response.json()["code"]) == (400, "InvalidArgument")
    assert manager.calls == []


def test_nil_uuid_is_400(manager):
    response = put(platform(manager), vm_id="00000000-0000-0000-0000-000000000000")
    assert (response.status_code, response.json()["code"]) == (400, "InvalidArgument")


# ---- 재시작은 바뀐 자원값과 비교한다 (계약 4.2) ----


def test_restart_after_a_resource_update_compares_with_the_current_configuration(manager):
    view = {**STOPPED, "initial_spec": {"template_id": "debian", "template_version": "1.0.0", "vcpus": 1, "memory_mib": 512},
            "configuration": {"id": f"vm-{VM_A.hex}", "vcpus": 2, "memory_mib": 768}}
    assert PlatformProvisioner._spec_mismatch(view, "debian", "1.0.0", 2, 768) == []
    assert PlatformProvisioner._spec_mismatch(view, "debian", "1.0.0", 1, 512) == ["vcpus", "memory_mib"]


# ---- mock ----


def registered_stopped(client, **overrides):
    assert client.post("/api/v1/vms", json=create_body(**overrides)).status_code == 201
    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204


def test_mock_changes_a_stopped_vm_and_replays_the_original(client, provisioner):
    registered_stopped(client)
    first = put(client)
    assert first.status_code == 200
    assert first.json() == {"vm_id": str(VM_A), "operation_id": None, "before": {"vcpus": 1, "memory_mib": 256},
                            "after": {"vcpus": 2, "memory_mib": 768}, "applies_on_next_start": True}
    assert provisioner.resources_of(VM_A).model_dump() == BODY
    assert put(client).json() == first.json()  # 같은 키·같은 본문은 원 결과
    other = put(client, body={"vcpus": 1, "memory_mib": 512})
    assert (other.status_code, other.json()["code"]) == (409, "VMExists")
    assert provisioner.resources_of(VM_A).model_dump() == BODY


def test_mock_refuses_running_unregistered_and_zephyr(client):
    assert (put(client).status_code, put(client).json()["code"]) == (404, "VMNotFound")
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    running = put(client, key="k-running")
    assert (running.status_code, running.json()["code"]) == (409, "InvalidState")
    zephyr = TestClient(create_app(Settings(driver="mock", log_level="WARNING"), driver=MockDriver(),
                                   provisioner=MockProvisioner()))
    assert zephyr.post("/api/v1/vms", json=create_body(template_id="zephyr-xenvm-starter-arm64", memory_mib=16)).status_code == 201
    assert zephyr.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204
    refused = put(zephyr, key="k-zephyr")
    assert (refused.status_code, refused.json()["code"]) == (400, "InvalidArgument")


def test_mock_with_exhausted_budget_changes_nothing():
    driver = MockDriver(host_cpus=4, total_memory_mib=1024)
    provisioner = MockProvisioner(running=driver.has_domain)
    client = TestClient(create_app(Settings(driver="mock", log_level="WARNING", response_reserve_s=0.5), driver=driver, provisioner=provisioner))
    registered_stopped(client)
    response = put(client, **{"X-Request-Budget-Ms": "200"})
    assert (response.status_code, response.json()["code"]) == (504, "DriverTimeout")
    assert provisioner.resources_of(VM_A).model_dump() == {"vcpus": 1, "memory_mib": 256}
