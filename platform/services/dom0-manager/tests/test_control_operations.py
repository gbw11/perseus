"""정지 요청 추적 (S15P31E110-305): Idempotency-Key·빠른 수락·원 작업 조회·요청 예산.

하위 Manager 봉투는 docs/platform/cli-manager-spec.md 공통 계약 A 의 모양을 따른다(수락 pending·완료 replay·control-operation).
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import VM_A, create_body
from dom0_manager import budget as request_budget
from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner, SerializedManager
from dom0_manager.drivers.xentop import Xentop
from dom0_manager.drivers.xl import Xl
from dom0_manager.main import create_app
from test_xl import XL_INFO, FakeRunner

OP = "33333333-3333-4333-8333-333333333333"
HOST = "44444444-4444-4444-8444-444444444444"
KEY = "stop-7f1c"


def envelope(ok=True, data=None, error=None, operation_id=None, replayed=False):
    return {"schema_version": "1.0", "ok": ok, "request_id": "r", "operation_id": operation_id, "replayed": replayed,
            "data": data if ok else None, "error": error}


def failure(code, *, retryable=False, **details):
    return envelope(ok=False, error={"code": code, "message": f"{code.lower()} happened", "retryable": retryable, "details": details})


def accepted(code, *, completion, phase, retryable=True, **extra):
    """수락 뒤 오류 봉투(공통 계약 A '수락 pending·완료 replay')."""
    details = {"accepted": True, "completion": completion, "operation_id": OP, "intent_revision": 12, "phase": phase,
               "host_boot_id": HOST, "lifecycle_generation": 1, "deadline_monotonic_ms": 123456,
               "execution_deadline_expired": False, "next_action": "replay_original_stop_after_execution_lock_is_available", **extra}
    out = failure(code, retryable=retryable, **details)
    out["operation_id"] = OP
    return out


VIEW = {"vm_id": str(VM_A), "name": "vm", "lifecycle": "REGISTERED", "configuration": {"id": f"vm-{VM_A.hex}", "vcpus": 1, "memory_mib": 256},
        "xen_state": "running", "domid": 3}


class Manager:
    """budget_ms·fork·control-operation 을 아는 새 Manager 흉내. 호출은 (action, spec, key, budget_ms, forked) 로 남긴다."""

    data = Path(tempfile.mkdtemp(prefix="dom0-ctl-"))
    (data / "instances").mkdir()

    def __init__(self, calls=None, responses=None, forked=False):
        self.calls = [] if calls is None else calls
        self.responses = {} if responses is None else responses
        self.forked = forked

    def fork(self):
        return Manager(self.calls, self.responses, forked=True)

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None, budget_ms=None):
        self.calls.append({"action": action, "spec": dict(spec or {}), "key": idempotency_key, "budget_ms": budget_ms, "forked": self.forked})
        answer = self.responses.get(action)
        if callable(answer):
            return answer(spec)
        if answer is not None:
            return answer
        if action == "get":
            return envelope(data={"vm": VIEW})
        if action == "list":
            return envelope(data={"vms": [VIEW]})
        if action == "stop":
            return envelope(data={"vm": {**VIEW, "domid": None, "xen_state": "stopped"}}, operation_id=OP)
        return failure("INVALID_SPEC")


class OldManager:
    """budget_ms·fork·control-operation 이 없는 옛 Manager."""

    data = Manager.data

    def __init__(self):
        self.calls = []

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None):
        self.calls.append((action, dict(spec or {}), idempotency_key))
        if action == "get":
            return envelope(data={"vm": VIEW})
        if action == "stop":
            return envelope(data={"vm": VIEW}, operation_id=OP)
        return failure("INVALID_SPEC", message="unsupported management action")


def app_for(manager, **settings):
    return create_app(Settings(driver="platform", log_level="WARNING", **settings), driver=PlatformDriver(manager),
                      provisioner=PlatformProvisioner(manager))


@pytest.fixture
def manager():
    return Manager()


@pytest.fixture
def client(manager):
    return TestClient(app_for(manager))


def stop(client, key=KEY, **headers):
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(f"/api/v1/vms/{VM_A}/stop", headers=headers)


# ---- 키 있는 stop ----


def test_keyless_stop_keeps_the_old_contract(client, manager):
    response = stop(client, key=None)
    assert response.status_code == 204 and response.content == b""
    assert [(c["action"], c["key"], c["forked"]) for c in manager.calls] == [("stop", None, False)]  # 공유 Manager, 키 없음


def test_keyed_stop_completed_is_200_with_operation_and_uses_a_request_object(client, manager):
    response = stop(client)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["accepted"], body["operation_id"], body["request_key"]) == ("completed", True, OP, KEY)
    assert [(c["action"], c["key"], c["forked"], c["spec"]) for c in manager.calls] == [("stop", KEY, True, {"vm_id": str(VM_A), "force": False})]


def test_completed_replay_keeps_original_result_and_warnings(client, manager):
    manager.responses["stop"] = envelope(operation_id=OP, replayed=True, data={
        "vm": None, "original_result": {"accepted": True, "physical_result": {}, "intent_revision": 12},
        "current_observation": None, "diagnostic_warnings": [{"code": "DIAGNOSTIC_UNAVAILABLE", "sink": "current-observation"}]})
    body = stop(client).json()
    assert (body["status"], body["operation_id"], body["intent_revision"], body["warnings"]) == ("completed", OP, 12, ["DIAGNOSTIC_UNAVAILABLE"])
    assert "replayed" not in body  # 하위 replayed 는 stop 첫 요청에도 true 라 옮기지 않는다


def test_stop_accepted_while_execution_lock_is_held_is_202_pending(client, manager):
    manager.responses["stop"] = accepted("OPERATION_IN_PROGRESS", completion="pending", phase="accepted")
    response = stop(client)
    assert response.status_code == 202
    body = response.json()
    assert (body["status"], body["accepted"], body["operation_id"], body["intent_revision"], body["phase"]) == ("pending", True, OP, 12, "accepted")
    assert (body["host_boot_id"], body["deadline_monotonic_ms"], body["execution_deadline_expired"]) == (HOST, 123456, False)
    assert body["next_action"] == "replay_original_stop_after_execution_lock_is_available" and body["error_code"] == "OPERATION_IN_PROGRESS"


@pytest.mark.parametrize("response_env,status,code,op_status", [
    (accepted("OPERATION_IN_PROGRESS", completion="superseded", phase="accepted"), 409, "InvalidState", "superseded"),
    (accepted("RECONCILE_REQUIRED", completion="pending", phase="uncertain", retryable=False), 503, "PlatformUnavailable", "uncertain"),
    (accepted("OPERATION_IN_PROGRESS", completion="pending", phase="uncertain"), 503, "PlatformUnavailable", "uncertain"),
    (accepted("XEN_EXECUTION_FAILED", completion="failed", phase="failed", retryable=False), 500, "DriverError", "failed"),
    (accepted("XEN_EXECUTION_FAILED", completion="failed", phase="failed", retryable=False) | {"error": {
        "code": "XEN_EXECUTION_FAILED", "message": "graceful shutdown timed out", "retryable": False,
        "details": {"accepted": True, "completion": "failed", "phase": "failed", "operation_id": OP}}}, 504, "DriverTimeout", "failed"),
])
def test_accepted_stop_errors_carry_the_original_operation(client, manager, response_env, status, code, op_status):
    manager.responses["stop"] = response_env
    response = stop(client)
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code and body["platform_code"] == response_env["error"]["code"]
    assert body["retryable"] == response_env["error"]["retryable"]
    assert (body["operation"]["status"], body["operation"]["accepted"], body["operation"]["operation_id"]) == (op_status, True, OP)


def test_not_accepted_stop_is_rejected_or_unknown(client, manager):
    manager.responses["stop"] = failure("OPERATION_IN_PROGRESS", retryable=True, lock="admission", acceptance="not-checked")
    body = stop(client).json()
    assert body["code"] == "PlatformUnavailable" and body["retryable"] is True
    assert (body["operation"]["status"], body["operation"]["accepted"], body["operation"]["operation_id"]) == ("rejected", False, None)
    manager.responses["stop"] = failure("STORAGE_UNAVAILABLE", acceptance="unconfirmed")
    body = stop(client).json()
    assert (body["operation"]["status"], body["operation"]["accepted"]) == ("unknown", None)  # 수락 저장 미확인: 미수락으로 추측하지 않는다


def test_keyed_stop_of_unregistered_vm_is_204(client, manager):
    manager.responses["stop"] = failure("VM_NOT_FOUND")
    assert stop(client).status_code == 204


@pytest.mark.parametrize("key", ["", "has space", "x" * 201, "키"])
def test_invalid_key_is_400_and_nothing_is_sent(client, manager, key):
    response = client.post(f"/api/v1/vms/{VM_A}/stop", headers={"Idempotency-Key": key.encode("utf-8")})
    assert response.status_code == 400 and response.json()["code"] == "InvalidArgument"
    assert manager.calls == []


def test_mock_driver_completes_keyed_stop_without_an_operation_id():
    app = create_app(Settings(driver="mock", log_level="WARNING"), driver=MockDriver(host_cpus=4, total_memory_mib=1024),
                     provisioner=MockProvisioner())
    client = TestClient(app)
    body = stop(client).json()
    assert (body["status"], body["operation_id"], body["request_key"]) == ("completed", None, KEY)
    response = client.get(f"/api/v1/vms/{VM_A}/control-operations", params={"request_key": KEY})
    assert response.status_code == 501


# ---- 긴 작업과 경합 ----


def test_keyed_stop_does_not_wait_for_the_serialized_manager_or_vm_lock():
    """긴 start 가 공유 Manager 와 같은 VM 락을 쥐고 있다. 키 있는 stop 은 요청별 Manager 로 바로 수락되고,
    키 없는 stop 은 지금처럼 직렬화 대기 끝에 503 이다."""
    inner = Manager()
    inner.responses["stop"] = accepted("OPERATION_IN_PROGRESS", completion="pending", phase="accepted")
    serialized = SerializedManager(inner, busy_timeout_s=0.2)
    app = app_for(serialized)
    client = TestClient(app)
    service = app.state.service
    assert serialized._lock.acquire(timeout=1)  # start 가 Manager 를 쥐고 있다
    with service._vm_locks_guard:
        vm_lock = service._vm_locks[VM_A]
    assert vm_lock.acquire(timeout=1)  # 같은 VM 의 start 가 VM 락도 쥐고 있다
    try:
        keyed = stop(client)
        assert keyed.status_code == 202 and keyed.json()["status"] == "pending"
        assert [c["forked"] for c in inner.calls] == [True]
        vm_lock.release()  # 키 없는 stop 이 VM 락을 지나 직렬화 대기에서 막히는 것을 본다
        keyless = stop(client, key=None)
        assert keyless.status_code == 503 and keyless.json()["platform_code"] == "OPERATION_IN_PROGRESS"
        assert len(inner.calls) == 1  # 키 없는 stop 은 하위에 닿지 않았다
    finally:
        serialized._lock.release()


def test_old_manager_without_fork_uses_the_serialized_path():
    old = OldManager()
    serialized = SerializedManager(old, busy_timeout_s=0.2)
    client = TestClient(app_for(serialized))
    assert serialized.fork() is None
    response = stop(client, **{"X-Request-Budget-Ms": "5000"})
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert old.calls == [("stop", {"vm_id": str(VM_A), "force": False}, KEY)]  # budget_ms 를 모르는 Manager 에는 넘기지 않는다


# ---- 원 작업 조회 ----


def lookup(client, key=KEY, **headers):
    return client.get(f"/api/v1/vms/{VM_A}/control-operations", params={"request_key": key}, headers=headers)


@pytest.mark.parametrize("phase,status,is_accepted", [
    ("prepared", "unknown", None), ("accepted", "pending", True), ("executing", "pending", True),
    ("completed", "completed", True), ("failed", "failed", True), ("uncertain", "uncertain", True),
])
def test_lookup_maps_original_phase(client, manager, phase, status, is_accepted):
    record = {"operation_id": OP, "vm_id": str(VM_A), "request": {"vm_id": str(VM_A), "action": "stop", "force": False},
              "idempotency_key": KEY, "phase": phase, "intent_revision": 12, "host_boot_id": HOST, "lifecycle_generation": 1,
              "deadline_monotonic_ms": 123456, "error": {"code": "XEN_EXECUTION_FAILED"} if phase == "failed" else None,
              "events": [{"phase": phase}], "request_hash": "x"}
    manager.responses["control-operation"] = envelope(operation_id=OP, data={"operation": record, "deadline_observation": {
        "host_boot_id": HOST, "observed_monotonic_ms": 200000, "original_host_matches": True, "execution_deadline_expired": True}})
    response = lookup(client)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["accepted"], body["phase"], body["action"], body["request_key"]) == (status, is_accepted, phase, "stop", KEY)
    assert (body["operation_id"], body["intent_revision"], body["execution_deadline_expired"]) == (OP, 12, True)
    assert body["error_code"] == ("XEN_EXECUTION_FAILED" if phase == "failed" else None)
    assert "events" not in body and "request_hash" not in body  # raw journal 은 내지 않는다
    [call] = manager.calls
    assert call["forked"] and call["spec"] == {"vm_id": str(VM_A), "idempotency_key": KEY, "timeout_ms": 5000}


@pytest.mark.parametrize("code,status,error", [
    ("VM_NOT_FOUND", 404, "OperationNotFound"),
    ("DEADLINE_EXCEEDED", 504, "DriverTimeout"),
    ("OBSERVATION_UNAVAILABLE", 503, "PlatformUnavailable"),
    ("RECONCILE_REQUIRED", 503, "PlatformUnavailable"),
    ("INVALID_SPEC", 501, "NotSupported"),
])
def test_lookup_errors(client, manager, code, status, error):
    manager.responses["control-operation"] = failure(code)
    response = lookup(client)
    assert response.status_code == status and response.json()["code"] == error and response.json()["platform_code"] == code


def test_lookup_never_controls_the_vm(client, manager):
    manager.responses["control-operation"] = failure("VM_NOT_FOUND")
    for _ in range(3):
        lookup(client)
    assert {c["action"] for c in manager.calls} == {"control-operation"}


# ---- 요청 예산 ----


def test_budget_header_limits_downstream_budget_and_read_timeout(manager):
    client = TestClient(app_for(manager, response_reserve_s=0.5))
    manager.responses["control-operation"] = failure("VM_NOT_FOUND")
    assert stop(client, **{"X-Request-Budget-Ms": "10000"}).status_code == 200
    assert client.get(f"/api/v1/vms/{VM_A}", headers={"X-Request-Budget-Ms": "4000"}).status_code == 200
    assert lookup(client, **{"X-Request-Budget-Ms": "3000"}).status_code == 404
    stop_call, get_call, lookup_call = manager.calls
    assert 9000 <= stop_call["budget_ms"] <= 9500  # 응답 몫 0.5초를 뺀 남은 값
    assert 3000 <= get_call["budget_ms"] <= 3500 and get_call["spec"]["timeout_ms"] == get_call["budget_ms"]
    assert 2000 <= lookup_call["spec"]["timeout_ms"] <= 2500  # 조회 기본 5초보다 남은 예산이 짧다


def test_no_budget_header_sends_no_budget(client, manager):
    stop(client)
    client.get(f"/api/v1/vms/{VM_A}")
    assert [c["budget_ms"] for c in manager.calls] == [None, None]
    assert "timeout_ms" not in manager.calls[1]["spec"]


@pytest.mark.parametrize("value", ["0", "105001", "-1", "1.5", "abc", "010", " 100"])
def test_invalid_budget_header_is_400(client, manager, value):
    response = stop(client, **{"X-Request-Budget-Ms": value})
    assert response.status_code == 400 and response.json()["code"] == "InvalidArgument"
    assert manager.calls == []


def test_budget_exhausted_while_waiting_for_the_manager_is_504_and_nothing_is_submitted():
    inner = Manager()
    serialized = SerializedManager(inner, busy_timeout_s=5)
    client = TestClient(app_for(serialized, response_reserve_s=0))
    assert serialized._lock.acquire(timeout=1)
    try:
        response = client.get(f"/api/v1/vms/{VM_A}", headers={"X-Request-Budget-Ms": "100"})
    finally:
        serialized._lock.release()
    assert response.status_code == 504 and response.json()["platform_code"] == "DEADLINE_EXCEEDED"
    assert inner.calls == []  # 차례가 왔어도 예산이 끝났으면 제출하지 않는다


def test_budget_exhausted_while_waiting_for_the_vm_lock_is_504(manager):
    app = app_for(manager, response_reserve_s=0)
    client = TestClient(app)
    service = app.state.service
    with service._vm_locks_guard:
        vm_lock = service._vm_locks[VM_A]
    assert vm_lock.acquire(timeout=1)
    try:
        response = stop(client, key=None, **{"X-Request-Budget-Ms": "100"})
    finally:
        vm_lock.release()
    assert response.status_code == 504 and response.json()["code"] == "DriverTimeout"
    assert manager.calls == []


def test_reserve_larger_than_budget_submits_nothing(manager):
    client = TestClient(app_for(manager, response_reserve_s=0.5))
    response = stop(client, **{"X-Request-Budget-Ms": "200"})
    assert response.status_code == 504 and manager.calls == []


def xl_app(manager, runner, **settings):
    driver = PlatformDriver(manager, Xl(runner), Xentop(runner))
    return create_app(Settings(driver="platform", log_level="WARNING", **settings), driver=driver, provisioner=PlatformProvisioner(manager))


def test_budget_limits_xl_commands(manager):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    manager.responses["get"] = failure("VM_NOT_FOUND")  # 새 VM: 도메인 없음 → 자원 확인(xl info) → 등록
    client = TestClient(xl_app(manager, runner, response_reserve_s=0.5))
    client.post("/api/v1/vms", json=create_body(), headers={"X-Request-Budget-Ms": "2000"})
    [(args, timeout)] = runner.calls
    assert args == ("xl", "info") and 1.0 <= timeout <= 1.5  # 자체 한도 5초보다 남은 예산(2초 - 응답 몫)이 짧다


def test_budget_exhausted_while_waiting_for_the_resource_lock_is_504(manager):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    manager.responses["get"] = failure("VM_NOT_FOUND")
    app = xl_app(manager, runner, response_reserve_s=0)
    client = TestClient(app)
    resource_lock = app.state.service._resource_lock
    assert resource_lock.acquire(timeout=1)  # 다른 VM 생성이 자원 확인 중
    try:
        response = client.post("/api/v1/vms", json=create_body(), headers={"X-Request-Budget-Ms": "100"})
    finally:
        resource_lock.release()
    assert response.status_code == 504 and response.json()["code"] == "DriverTimeout"
    assert runner.calls == [] and [c["action"] for c in manager.calls] == ["get"]  # 자원 확인·등록·시작 없음


def test_stats_with_exhausted_budget_runs_nothing(manager):
    runner = FakeRunner({})
    client = TestClient(xl_app(manager, runner, response_reserve_s=0.5))
    response = client.get("/api/v1/stats", headers={"X-Request-Budget-Ms": "200"})
    assert response.status_code == 504 and runner.calls == []


def test_healthz_ignores_the_budget(manager):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    client = TestClient(xl_app(manager, runner, response_reserve_s=0.5))
    response = client.get("/healthz", headers={"X-Request-Budget-Ms": "1"})
    assert response.status_code == 200 and response.json() == {"status": "ok"}  # 예산 만료를 Xen 불가로 알리지 않는다
    assert runner.calls[0][1] == 5  # 자체 한도 그대로


# ---- 원 작업 ID 불변조건 ----


@pytest.mark.parametrize("response_env", [
    envelope(data={"vm": None}),  # 완료인데 operation_id 없음
    envelope(data={"vm": None}, operation_id="not-a-uuid"),
    accepted("OPERATION_IN_PROGRESS", completion="pending", phase="accepted") | {"operation_id": None, "error": {
        "code": "OPERATION_IN_PROGRESS", "message": "busy", "retryable": True,
        "details": {"accepted": True, "completion": "pending", "phase": "accepted", "operation_id": None}}},
])
def test_accepted_stop_without_a_valid_operation_id_is_503(client, manager, response_env):
    manager.responses["stop"] = response_env
    response = stop(client)
    assert response.status_code == 503 and response.json()["code"] == "PlatformUnavailable"
    assert "operation_id" in response.json()["message"]


OTHER_VM = "55555555-5555-4555-8555-555555555555"
OTHER_OP = "66666666-6666-4666-8666-666666666666"
STOP_REQUEST = {"vm_id": str(VM_A), "action": "stop", "force": False}


@pytest.mark.parametrize("change,envelope_op,problem", [
    ({"operation_id": None}, OP, "operation_id"),
    ({"operation_id": "x"}, OP, "operation_id"),
    ({}, OTHER_OP, "operation_id"),  # 봉투와 기록의 작업 ID 가 다르다
    ({}, None, "operation_id"),
    ({"vm_id": OTHER_VM}, OP, "vm_id"),
    ({"request": {**STOP_REQUEST, "vm_id": OTHER_VM}}, OP, "request.vm_id"),
    ({"request": {**STOP_REQUEST, "action": "start"}}, OP, "request.action"),
    ({"request": {**STOP_REQUEST, "force": True}}, OP, "request.force"),
    ({"request": {"vm_id": str(VM_A), "action": "stop"}}, OP, "request.force"),  # force 가 빠진 기록을 graceful 로 추측하지 않는다
    ({"request": None}, OP, "request.action"),
    ({"idempotency_key": "someone-else"}, OP, "idempotency_key"),
    ({"idempotency_key": None}, OP, "idempotency_key"),
    ({"phase": "dancing"}, OP, "phase"),
    ({"phase": None}, OP, "phase"),
])
def test_lookup_record_that_is_not_this_vm_key_and_stop_is_503(client, manager, change, envelope_op, problem):
    """조회는 응답 유실 뒤 원 작업을 되찾는 경계다. 다른 VM·다른 키·다른 동작의 기록을 이 stop 으로 돌려주지 않는다."""
    record = {"operation_id": OP, "vm_id": str(VM_A), "request": STOP_REQUEST, "idempotency_key": KEY, "phase": "accepted", **change}
    manager.responses["control-operation"] = envelope(operation_id=envelope_op, data={"operation": record, "deadline_observation": {}})
    response = lookup(client)
    assert response.status_code == 503 and response.json()["code"] == "PlatformUnavailable"
    assert "malformed" in response.json()["message"] and problem in response.json()["message"]


# ---- 상태를 바꾸는 하위 호출(create·retire)도 예산이 끝났으면 제출하지 않는다 ----


def slow_second_get(*, sleep_s, registered):
    """두 번째 get(제출 직전의 등록 확인)이 남은 예산을 다 쓴다. 도메인은 없고, registered 면 정지된 등록이 있다."""
    import time

    count = []

    def answer(spec):
        count.append(spec)
        if len(count) == 2:
            time.sleep(sleep_s)
        return envelope(data={"vm": {**VIEW, "domid": None, "xen_state": "stopped"}}) if registered else failure("VM_NOT_FOUND")

    return answer


def test_create_is_not_submitted_after_the_budget_ran_out(manager):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    manager.responses["get"] = slow_second_get(sleep_s=0.4, registered=False)
    client = TestClient(xl_app(manager, runner, response_reserve_s=0))
    response = client.post("/api/v1/vms", json=create_body(), headers={"X-Request-Budget-Ms": "300"})
    assert response.status_code == 504 and response.json()["platform_code"] == "DEADLINE_EXCEEDED"
    assert [c["action"] for c in manager.calls] == ["get", "get"]  # 등록(create)·시작(start) 없음


def test_retire_is_not_submitted_after_the_budget_ran_out(manager):
    manager.responses["get"] = slow_second_get(sleep_s=0.4, registered=True)  # 정지된 등록 VM: 도메인 없음, 등록 있음
    client = TestClient(app_for(manager, response_reserve_s=0))
    response = client.delete(f"/api/v1/vms/{VM_A}", headers={"X-Request-Budget-Ms": "300"})
    assert response.status_code == 504 and response.json()["code"] == "DriverTimeout"  # 서비스의 변경 직전 검사가 먼저 막는다
    assert [c["action"] for c in manager.calls] == ["get", "get"]  # 보관(retire) 없음


@pytest.mark.parametrize("submit", [
    lambda p: p.delete_instance(VM_A),
    lambda p: p.ensure_instance(VM_A, "debian", "1", name="vm", vcpus=1, memory_mib=256),
])
def test_provisioner_itself_does_not_submit_after_the_budget(manager, submit):
    """서비스 검사와 별개로 프로비저너의 create·retire 도 만료 뒤 제출하지 않는다(심층 방어)."""
    import time

    from dom0_manager import budget

    manager.responses["get"] = failure("VM_NOT_FOUND")
    token = budget.start(1, reserve_s=0)
    try:
        time.sleep(0.01)
        with pytest.raises(Exception) as caught:
            submit(PlatformProvisioner(manager))
    finally:
        budget.reset(token)
    assert type(caught.value).__name__ == "DriverTimeout"
    assert [c["action"] for c in manager.calls if c["action"] in ("create", "retire")] == []


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/v1/vms", create_body()),
    ("delete", f"/api/v1/vms/{VM_A}", None),
])
def test_budget_below_the_response_reserve_changes_nothing(manager, method, path, body):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    client = TestClient(xl_app(manager, runner, response_reserve_s=0.5))
    response = client.request(method, path, json=body, headers={"X-Request-Budget-Ms": "200"})
    assert response.status_code == 504
    assert manager.calls == [] and runner.calls == []


def test_ip_pool_exhausted_keeps_platform_retryable(manager):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    manager.responses["get"] = failure("VM_NOT_FOUND")
    manager.responses["create"] = failure("IP_POOL_EXHAUSTED", retryable=True)
    body = TestClient(xl_app(manager, runner)).post("/api/v1/vms", json=create_body()).json()
    assert (body["code"], body["resource"]["name"], body["retryable"]) == ("InsufficientResources", "ipv4", True)


def mock_app():
    driver, provisioner = MockDriver(host_cpus=4, total_memory_mib=1024), MockProvisioner()
    app = create_app(Settings(driver="mock", log_level="WARNING", response_reserve_s=0.5), driver=driver, provisioner=provisioner)
    return TestClient(app), driver, provisioner


LATE = {"X-Request-Budget-Ms": "200"}  # 응답 몫 0.5초보다 짧다


def test_mock_create_with_exhausted_budget_changes_nothing():
    client, driver, provisioner = mock_app()
    response = client.post("/api/v1/vms", json=create_body(), headers=LATE)
    assert response.status_code == 504 and response.json()["code"] == "DriverTimeout"
    assert driver.list_domains() == [] and not provisioner.instance_exists(VM_A)


@pytest.mark.parametrize("key", [None, KEY])
def test_mock_stop_with_exhausted_budget_keeps_the_domain(key):
    client, driver, _ = mock_app()
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    response = stop(client, key=key, **LATE)
    assert response.status_code == 504
    assert driver.has_domain(VM_A)


def test_mock_delete_with_exhausted_budget_keeps_the_instance():
    client, _, provisioner = mock_app()
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    assert stop(client, key=None).status_code == 204
    response = client.delete(f"/api/v1/vms/{VM_A}", headers=LATE)
    assert response.status_code == 504
    assert provisioner.instance_exists(VM_A)


def test_platform_retryable_is_kept_on_ordinary_errors(client, manager):
    manager.responses["get"] = failure("OPERATION_IN_PROGRESS", retryable=True)
    body = client.get(f"/api/v1/vms/{VM_A}").json()
    assert (body["code"], body["platform_code"], body["retryable"]) == ("PlatformUnavailable", "OPERATION_IN_PROGRESS", True)
    assert "operation" not in body  # 작업 문맥은 키 있는 제어 요청에만


# ---- 일반 조회의 요청별 Manager (S15P31E110-333) ----


class ForkingManager(Manager):
    """플랫폼 fork 흉내: 같은 요청 객체의 동시 call 은 무대기 OPERATION_IN_PROGRESS. 공유 inner 직접 호출도 기록한다."""

    def __init__(self, calls=None, responses=None, forked=False):
        super().__init__(calls, responses, forked)
        self._busy = threading.Lock()
        self.forks = []

    def fork(self):
        child = ForkingManager(self.calls, self.responses, forked=True)
        self.forks.append(child)
        return child

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None, budget_ms=None):
        if not self._busy.acquire(blocking=False):
            return failure("OPERATION_IN_PROGRESS", retryable=True)
        try:
            return super().call(action, spec, execute=execute, idempotency_key=idempotency_key, request_id=request_id, budget_ms=budget_ms)
        finally:
            self._busy.release()


def test_reads_do_not_wait_for_a_long_mutation_when_read_fork_is_on():
    """긴 create 가 직렬화를 쥔 동안 다른 VM 의 상세·목록은 요청별 Manager 로 바로 하위에 닿는다. 변경은 그대로 직렬화된다."""
    inner = ForkingManager()
    serialized = SerializedManager(inner, busy_timeout_s=0.2, read_fork=True)
    client = TestClient(app_for(serialized))
    assert serialized._lock.acquire(timeout=1)  # create 가 Manager 를 쥐고 있다
    try:
        assert client.get(f"/api/v1/vms/{VM_A}").status_code == 200
        assert client.get("/api/v1/vms").status_code == 200
        assert [(c["action"], c["forked"]) for c in inner.calls] == [("get", True), ("list", True)]
        assert len(inner.forks) == 2  # 호출마다 새 요청 객체
        keyless = stop(client, key=None)
        assert keyless.status_code == 503 and keyless.json()["platform_code"] == "OPERATION_IN_PROGRESS"
        assert len(inner.calls) == 2  # 변경은 직렬화 대기에서 막혀 하위에 닿지 않았다
    finally:
        serialized._lock.release()


def test_concurrent_reads_each_get_their_own_request_object():
    """같은 요청 객체를 공유하면 하위가 무대기 거절한다. 동시에 들어온 두 조회가 모두 진입해야 객체를 나눠 쓴 것이다."""
    inner = ForkingManager()
    entered, release = threading.Semaphore(0), threading.Event()

    def slow_get(spec):
        entered.release()
        assert release.wait(5)
        return envelope(data={"vm": VIEW})

    inner.responses["get"] = slow_get
    serialized = SerializedManager(inner, busy_timeout_s=0.2, read_fork=True)
    results = []
    threads = [threading.Thread(target=lambda: results.append(serialized.call("get", {"vm_id": str(VM_A)}))) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert entered.acquire(timeout=5) and entered.acquire(timeout=5)  # 둘 다 하위에 들어갔다
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
    assert [r["ok"] for r in results] == [True, True]


@pytest.mark.parametrize("read_fork, inner_factory", [(False, ForkingManager), (True, OldManager)])
def test_reads_stay_serialized_without_read_fork_or_platform_fork(read_fork, inner_factory):
    """설정을 끄거나 하위에 fork 가 없으면 조회도 지금처럼 직렬화 대기 끝에 503 이고 하위에 닿지 않는다."""
    inner = inner_factory()
    serialized = SerializedManager(inner, busy_timeout_s=0.1, read_fork=read_fork)
    client = TestClient(app_for(serialized))
    assert serialized._lock.acquire(timeout=1)
    try:
        response = client.get(f"/api/v1/vms/{VM_A}")
    finally:
        serialized._lock.release()
    assert response.status_code == 503 and response.json()["platform_code"] == "OPERATION_IN_PROGRESS"
    assert inner.calls == []


def test_forked_read_gets_the_remaining_budget_and_nothing_after_it_ran_out():
    inner = ForkingManager()
    serialized = SerializedManager(inner, busy_timeout_s=5, read_fork=True)
    client = TestClient(app_for(serialized, response_reserve_s=0))
    assert serialized._lock.acquire(timeout=1)  # 직렬화가 막혀 있어도 조회 예산은 그대로 하위에 간다
    try:
        assert client.get(f"/api/v1/vms/{VM_A}", headers={"X-Request-Budget-Ms": "3000"}).status_code == 200
        call = inner.calls[-1]
        assert call["forked"] and 2500 <= call["budget_ms"] <= 3000 and call["spec"]["timeout_ms"] == call["budget_ms"]
    finally:
        serialized._lock.release()
    token = request_budget.start(1, reserve_s=1)  # 남은 값이 응답 몫보다 작다
    try:
        result = serialized.call("list", {"lifecycle": "REGISTERED"})
    finally:
        request_budget.reset(token)
    assert result["error"]["code"] == "DEADLINE_EXCEEDED" and inner.calls[-1]["action"] == "get"  # list 는 제출되지 않았다


@pytest.mark.parametrize("action, forked, timeout", [
    ("intents", True, True), ("probe", True, True), ("execution-context", True, True), ("operation", True, True),
    ("recovery-operation", True, True), ("provisioning", True, True), ("registrations", True, True),
    ("templates", True, False),  # 하위 templates 는 spec timeout_ms 를 받지 않는다
    ("provision", False, True),  # 공급은 조회가 아니라 직렬화한다. 구형 통지 action은 제거됐다.
    ("recovery-reconcile", False, True),  # 원 attempt 정산은 직렬화하되 timeout_ms 도 남은 값으로 줄인다
])
def test_action_routing_budget_and_timeout(action, forked, timeout):
    inner = ForkingManager()
    inner.responses[action] = envelope(data={})
    token = request_budget.start(4000, reserve_s=0)
    try:
        SerializedManager(inner, read_fork=True).call(action, {})
    finally:
        request_budget.reset(token)
    call = inner.calls[-1]
    assert call["forked"] is forked and 3500 <= call["budget_ms"] <= 4000
    assert call["spec"].get("timeout_ms") == (call["budget_ms"] if timeout else None)


def test_reads_finish_while_a_real_create_holds_the_manager():
    """HTTP create 가 실제로 하위 create 에서 멈춘 동안(별도 스레드) 다른 VM 의 상세·목록이 요청별 Manager 로 200 이다.
    create 는 직렬화된 공유 Manager 로 불리고, 풀린 뒤 start 까지 끝난다."""
    other = "22222222-2222-4222-8222-222222222222"
    inner = ForkingManager()
    entered, release = threading.Event(), threading.Event()
    registered = {"vm": None}

    def get(spec):
        if spec["vm_id"] == str(VM_A) and registered["vm"] is None:
            return failure("VM_NOT_FOUND")
        return envelope(data={"vm": {**VIEW, "vm_id": spec["vm_id"]}})

    def create(spec):
        entered.set()
        assert release.wait(10)
        registered["vm"] = {**VIEW, "domid": None, "xen_state": None}
        return envelope(data={"vm": registered["vm"]}, operation_id=OP)

    inner.responses.update(get=get, create=create, start=envelope(data={"vm": VIEW}))
    serialized = SerializedManager(inner, busy_timeout_s=0.2, read_fork=True)
    app = create_app(Settings(driver="platform", log_level="WARNING"),
                     driver=PlatformDriver(serialized, Xl(FakeRunner({("xl", "info"): XL_INFO}))),
                     provisioner=PlatformProvisioner(serialized))
    created = []
    worker = threading.Thread(target=lambda: created.append(TestClient(app).post("/api/v1/vms", json=create_body())))
    worker.start()
    try:
        assert entered.wait(10)  # 하위 create 가 공유 Manager 를 쥐고 멈춰 있다
        client = TestClient(app)
        assert client.get(f"/api/v1/vms/{other}").status_code == 200
        assert client.get("/api/v1/vms").status_code == 200
        keyless = client.post(f"/api/v1/vms/{other}/stop")
        assert keyless.status_code == 503  # 다른 VM 이라도 변경은 여전히 직렬화된다
    finally:
        release.set()
        worker.join(10)
    assert created and created[0].status_code == 201
    shared = [c["action"] for c in inner.calls if not c["forked"]]
    assert shared == ["create", "start"]  # 조회는 하나도 공유 Manager 로 가지 않았다
