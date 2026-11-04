"""주행 VM Gateway 보호 수명주기 (S15P31E110-346). 가짜 Gateway(fake_gateway.py)와 엔진 시험 도구로 보호→복구→정산을 돌린다."""

import json
import sys
from pathlib import Path
from uuid import UUID

import pytest

from dom0_manager.errors import DriverError, InvalidState
from dom0_manager.recovery.config import RecoverySettings
from dom0_manager.recovery.engine import KEEP_CLOSED
from dom0_manager.recovery.model import HealthState
from dom0_manager.recovery.policy import RecoveryPolicy
from dom0_manager.recovery.protection import GatewayProtection, GatewayRefused, GatewayUncertain
from dom0_manager.recovery.state import RecoveryStatus, RecoveryStore
from fake_gateway import FakeGateway, lost
from test_recovery_engine import APP, EXEC, HOST, VM, VM2, held, judgment, make, view, zephyr_judgment

NEW = "7d1e2f3a-4b5c-4d6e-8f70-81a2b3c4d5e6"  # 복구가 만든 새 실행
OTHER_HOST = "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5"


def drive(tmp_path, *, bound=True, **options):
    """주행 VM(VM) 하나, 지금 실행 EXEC 의 app heartbeat 손실, Gateway binding 은 EXEC."""
    w = make(tmp_path, drive_vm_ids=[VM, VM2], **options)
    w.gateway = FakeGateway(HOST, lambda: w.clock[0])
    if bound:
        w.gateway.bind(str(VM), EXEC)
    w.engine._protection = GatewayProtection(w.gateway, deadline_s=5.0, call_budget_s=1.0, clock=lambda: w.clock[0])  # noqa: SLF001
    w.platform.results = [{"attempt": {}, "original_result": {"execution_id": NEW}}]
    w.watcher.failing(VM, APP, execution_id=EXEC)
    return w


def platform_down():
    return DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")  # 시도 실패 → BACKOFF


def final_failure():
    return InvalidState("reconcile", platform_code="RECONCILE_REQUIRED")  # 최종 실패


def stored(w):
    return json.loads(w.store.path.read_text(encoding="utf-8"))["recoveries"][0]


def entry(w):
    return w.record().protection


# ---- 보호 → 복구 → finish ----


def test_protection_is_recorded_before_submit_and_force_runs_only_after_protected(tmp_path):
    w = drive(tmp_path)
    seen = {}
    submit = w.gateway.submit

    def recorded_submit(request_id, spec):
        seen["stored"] = stored(w)["protection"]  # 키·원 spec 은 첫 송신 전에 저장돼 있다
        return submit(request_id, spec)

    w.gateway.submit = recorded_submit
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    key = seen["stored"]["request_id"]
    assert seen["stored"]["phase"] == "PLANNED" and key.startswith(f"g1:{HOST}:")
    spec = seen["stored"]["spec"]
    assert (spec["target_vm"], spec["target_execution_id"], spec["intent_revision"], spec["lifecycle_generation"]) == (str(VM), EXEC, 3, 0)
    assert (spec["expected_owner"], spec["expected_generation"], spec["purpose"]) == (w.gateway.owner, 7, "protect")
    assert spec["deadline_ms"] == 1_005_000 and spec["parent_deadline_ms"] == 1_110_000  # 보호 기한은 시도 기한 안
    (_, recover, _), = w.platform.recover_calls()
    assert recover["mode"] == "force-restart" and w.record().status == RecoveryStatus.COMPLETED
    assert entry(w)["phase"] == "PROTECTED" and w.gateway.phase(key) == "PROTECTED"  # 새 실행 binding 전에는 풀지 않는다

    w.advance(5)
    w.tick()
    assert w.gateway.phase(key) == "PROTECTED" and w.gateway.count("finish") == 0  # 아직 원 실행 binding: 기다린다
    w.gateway.bind(str(VM), NEW)  # 재합류 provisioning 뒤 새 실행 binding
    w.advance(5)
    w.tick()
    assert w.gateway.phase(key) == "RELEASED" and entry(w)["phase"] == "RELEASED"
    (_, (_, context)), = [c for c in w.gateway.calls if c[0] == "finish"]
    assert context == {"host_boot_id": HOST, "expected_generation": 7, "target_execution_id": NEW}
    assert stored(w)["protection"]["phase"] == "RELEASED" and w.gateway.issued == 1


def test_lost_submit_response_is_resolved_with_the_same_key(tmp_path):
    w = drive(tmp_path)
    w.gateway.fail("submit", lost(), when="after")  # 보호는 생겼지만 응답을 잃었다
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w) and entry(w)["phase"] == "UNKNOWN"
    key = entry(w)["request_id"]
    w.advance(5)
    w.tick()
    assert w.platform.recover_calls()[0][1]["mode"] == "force-restart"
    assert entry(w)["request_id"] == key and w.gateway.issued == 1 and list(w.gateway.rows) == [key]  # 새 키 0


@pytest.mark.parametrize("failure", [GatewayUncertain("UNAVAILABLE"), GatewayRefused("BUSY_NOT_ACCEPTED")])
def test_unreachable_gateway_holds_and_retries_the_same_key(tmp_path, failure):
    w = drive(tmp_path, policy=RecoveryPolicy(transient_retry_s=2.0))
    w.gateway.fail("submit", failure)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w) and entry(w)["phase"] == "UNKNOWN"
    w.advance(2)  # 원 기한(5초) 안에서 같은 키로 다시
    w.tick()
    assert len(w.platform.recover_calls()) == 1 and w.gateway.issued == 1


def test_expired_key_is_replaced_only_after_a_fresh_judgment_and_within_the_request_cap(tmp_path):
    """원 기한이 지나 같은 키가 DEADLINE_EXCEEDED 로 거절되면 그 키는 끝이다. 다음 차례에 현재 증거·view 를 다시 판단한 뒤에만
    새 키를 만들고, 복구당 보호 요청 수는 정책 시도 수(3)를 넘지 않는다. 넘으면 최종 실패다."""
    w = drive(tmp_path)
    for _ in range(3):
        w.gateway.fail("submit", GatewayUncertain("UNAVAILABLE"))
        w.gateway.fail("submit", GatewayRefused("DEADLINE_EXCEEDED"))
    w.engine.on_judgment(zephyr_judgment())
    keys = []
    for _ in range(3):
        w.tick()  # 새 키 발급 → 결과 모름
        keys.append(entry(w)["request_id"])
        w.advance(6)
        w.tick()  # 같은 키 재전달 → 기한 경과
        assert held(w) and entry(w)["phase"] == "ABANDONED" and entry(w)["request_id"] == keys[-1]
        w.advance(5)
    w.tick()
    record = w.record()
    assert len(set(keys)) == 3 and w.gateway.issued == 3 and record.protection_requests == 3
    assert record.status == RecoveryStatus.FAILED and record.final and record.last_error["code"] == "PROTECTION_UNAVAILABLE"
    assert w.platform.recover_calls() == []


def test_no_gateway_binding_holds_without_issuing_a_key(tmp_path):
    """crashed·미합류 실행은 Gateway binding 이 없어 보호를 받지 않는다. 보내지 않고 보류한다."""
    w = drive(tmp_path, bound=False)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w) and entry(w) is None and w.gateway.issued == 0 and w.gateway.count("submit") == 0
    assert "no current binding" in w.engine._held[w.record().recovery_id]  # noqa: SLF001


@pytest.mark.parametrize("change", ["owner", "binding", "host", "storage"])
def test_gateway_context_that_does_not_match_holds(tmp_path, change):
    w = drive(tmp_path)
    if change == "owner":
        w.gateway.owner = None
    elif change == "binding":
        w.gateway.bind(str(VM), EXEC, revision=4)  # binding 의 의도가 플랫폼 view 와 다르다
    elif change == "host":
        w.gateway.host = OTHER_HOST
    else:
        w.gateway.storage_uncertain = True
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w) and w.gateway.count("submit") == 0


@pytest.mark.parametrize("code", ["AUTHORITY_CHANGED", "TARGET_CHANGED", "HOST_CHANGED"])
def test_context_refusal_keeps_the_key_while_the_gateway_context_is_unchanged(tmp_path, code):
    w = drive(tmp_path, policy=RecoveryPolicy(transient_retry_s=2.0))
    w.gateway.fail("submit", GatewayRefused(code))  # 거절됐지만 다시 읽은 문맥은 원 spec 그대로다
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    first = entry(w)["request_id"]
    assert held(w) and entry(w)["phase"] == "UNKNOWN" and "same key kept" in entry(w)["note"]
    w.advance(2)
    w.tick()
    assert entry(w)["request_id"] == first and w.gateway.issued == 1 and len(w.platform.recover_calls()) == 1


def test_context_refusal_with_a_changed_context_plans_a_new_request(tmp_path):
    w = drive(tmp_path)
    submit = w.gateway.submit

    def authority_moves(request_id, spec):
        w.gateway.generation = 8  # 요청을 만든 뒤 Gateway generation 이 바뀌었다
        return submit(request_id, spec)

    w.gateway.submit = authority_moves
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    first = entry(w)["request_id"]
    assert held(w) and entry(w)["phase"] == "ABANDONED" and "generation 7 -> 8" in entry(w)["note"]
    w.gateway.submit = submit
    w.advance(5)
    w.tick()
    assert entry(w)["request_id"] != first and entry(w)["spec"]["expected_generation"] == 8 and len(w.platform.recover_calls()) == 1


@pytest.mark.parametrize("code", ["REQUEST_WINDOW_EXPIRED", "REQUEST_DEADLINE_OUTSIDE_WINDOW", "HISTORY_POLICY_REQUIRED", "INVALID_INPUT",
                                  "TARGET_NOT_MANAGED"])
def test_contract_or_configuration_refusal_fails_without_another_key(tmp_path, code):
    w = drive(tmp_path)
    w.gateway.fail("submit", GatewayRefused(code))
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.FAILED and record.final and record.last_error["code"] == "PROTECTION_UNAVAILABLE"
    assert code in record.last_error["message"] and entry(w)["phase"] == "ABANDONED"
    w.advance(30)
    w.tick()
    assert w.gateway.issued == 1 and w.gateway.count("submit") == 1 and w.platform.recover_calls() == []


@pytest.mark.parametrize("code", ["HISTORY_FULL", "HISTORY_BYTES_RESERVED", "TARGET_ALREADY_PROTECTED", "SOMETHING_NEW"])
def test_capacity_and_unknown_refusals_wait_with_the_same_key(tmp_path, code):
    w = drive(tmp_path, policy=RecoveryPolicy(transient_retry_s=2.0))
    w.gateway.fail("submit", GatewayRefused(code))
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w) and entry(w)["phase"] == "UNKNOWN"
    w.advance(2)
    w.tick()  # 용량이 돌아왔다
    assert w.gateway.issued == 1 and len(w.platform.recover_calls()) == 1


def test_idempotency_conflict_fails_and_keeps_the_key_for_settlement(tmp_path):
    w = drive(tmp_path)
    w.gateway.fail("submit", GatewayRefused("IDEMPOTENCY_CONFLICT"))
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.FAILED and entry(w)["phase"] == "UNKNOWN"  # 같은 키가 있을 수 있어 정산 대상으로 남는다
    assert w.gateway.issued == 1 and w.platform.recover_calls() == []


@pytest.mark.parametrize("change", ["stopped", "intent", "execution"])
def test_platform_view_is_read_again_after_protection(tmp_path, change):
    """보호를 얻는 동안 Manager 쪽이 바뀌었으면 옛 view 로 시도하지 않는다(attempt·recovery.started 0)."""
    w = drive(tmp_path)
    submit = w.gateway.submit

    def manager_moves(request_id, spec):
        result = submit(request_id, spec)
        if change == "stopped":
            w.platform.views[VM] = view(desired="stopped")
        elif change == "intent":
            w.platform.views[VM] = view(revision=4)
        else:
            w.platform.views[VM] = {**view(), "execution_id": NEW, "domid": 6}
        return result

    w.gateway.submit = manager_moves
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    record = w.record()
    assert w.platform.recover_calls() == [] and record.attempt == 0 and w.events == []
    if change == "stopped":
        assert record.status == RecoveryStatus.CANCELLED
    else:
        assert record.open and entry(w)["phase"] == "PROTECTED"  # 보호는 원 실행의 것: 다음 차례에 정산·재판단한다


@pytest.mark.parametrize("damage", ["target", "storage", "execution", "durable", "excluded"])
def test_protected_view_must_match_the_original_and_current_state(tmp_path, damage):
    w = drive(tmp_path)
    view_of = w.gateway.view

    def damaged(request_id):
        data = view_of(request_id)
        if damage == "target":
            data["protection"]["current_target_matches"] = False
        elif damage == "storage":
            data["storage_uncertain"] = True
        elif damage == "execution":
            data["target_execution_id"] = NEW
        elif damage == "durable":
            data["acceptance"] = "queued"
        else:
            data["protection"]["owner_excluded"] = False
        return data

    w.gateway.view = damaged
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w)  # recover_once 호출 0


def test_reused_protection_is_rechecked_with_get_before_the_next_attempt(tmp_path):
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.BACKOFF and entry(w)["phase"] == "PROTECTED"
    w.platform.results = [{"attempt": {}, "original_result": {"execution_id": NEW}}]
    w.advance(5)
    w.tick()
    key = entry(w)["request_id"]
    assert w.gateway.count("submit") == 1 and w.gateway.calls[2] == ("get", key) and record.status == RecoveryStatus.COMPLETED


# ---- 복구 전 종료 → cancel / 실패 → 막아 둠 ----


def test_user_stop_after_protection_cancels_the_original_execution(tmp_path):
    w = drive(tmp_path)
    w.watcher.failing(VM, APP, execution_id=EXEC)
    record = w.engine.on_judgment(zephyr_judgment())
    submit = w.gateway.submit

    def stop_meanwhile(request_id, spec):
        result = submit(request_id, spec)
        record.cancel_requested = True  # 보호를 받는 사이 사용자가 껐다
        return result

    w.gateway.submit = stop_meanwhile
    w.tick()
    key = entry(w)["request_id"]
    assert record.status == RecoveryStatus.CANCELLED and w.platform.recover_calls() == []
    w.advance(5)
    w.tick()
    (_, (_, context)), = [c for c in w.gateway.calls if c[0] == "cancel"]
    assert context["target_execution_id"] == EXEC and w.gateway.phase(key) == "CANCELLED" and entry(w)["phase"] == "CANCELLED"


def test_recovered_vm_cancels_its_protection(tmp_path):
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    w.watcher.set(VM, HealthState.HEALTHY, execution_id=EXEC)
    w.advance(2)
    w.tick()
    assert w.record().status == RecoveryStatus.RECOVERED
    w.advance(5)
    w.tick()
    assert entry(w)["phase"] == "CANCELLED"


def test_failed_recovery_keeps_the_broken_execution_excluded_until_a_new_binding(tmp_path):
    w = drive(tmp_path)
    w.platform.results = [final_failure()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    key = entry(w)["request_id"]
    assert w.record().status == RecoveryStatus.FAILED
    for _ in range(3):
        w.advance(5)
        w.tick()
    assert w.gateway.count("cancel") == 0 and w.gateway.phase(key) == "PROTECTED"  # 고장 난 실행의 재선택 차단을 풀지 않는다
    w.gateway.bind(str(VM), NEW)  # 운영자가 새로 띄워 합류했다
    w.advance(5)
    w.tick()
    assert w.gateway.phase(key) == "RELEASED"


# ---- 정산의 불확실성 ----


def test_lost_finish_response_and_authority_change_are_rechecked(tmp_path):
    w = drive(tmp_path)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    key = entry(w)["request_id"]
    w.gateway.bind(str(VM), NEW)
    w.gateway.fail("finish", GatewayRefused("AUTHORITY_CHANGED"))
    w.advance(5)
    w.tick()
    assert entry(w)["phase"] == "PROTECTED" and "AUTHORITY_CHANGED" in entry(w)["note"]
    w.gateway.generation = 8  # 다시 관측한 generation 으로만 종결한다
    w.gateway.fail("finish", lost(), when="after")  # 종결됐지만 응답을 잃었다
    w.advance(5)
    w.tick()
    assert w.gateway.phase(key) == "RELEASED" and entry(w)["phase"] == "PROTECTED"
    w.advance(5)
    w.tick()
    assert entry(w)["phase"] == "RELEASED" and w.gateway.count("finish") == 2  # get 으로 원 결과를 읽는다(다시 종결하지 않음)
    assert [c[1][1]["expected_generation"] for c in w.gateway.calls if c[0] == "finish"] == [7, 8]


def test_unknown_submit_on_a_closed_recovery_is_abandoned_only_after_the_original_deadline(tmp_path):
    w = drive(tmp_path)
    w.gateway.fail("submit", lost())  # 보냈는지 모른다(실제로는 도달 전)
    record = w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert entry(w)["phase"] == "UNKNOWN"
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.advance(1)
    w.tick()
    assert record.status == RecoveryStatus.CANCELLED and entry(w)["phase"] == "UNKNOWN"  # mailbox 에 남은 송신이 아직 수락될 수 있다
    assert w.gateway.count("submit") == 1  # 닫힌 복구는 다시 보내지 않고 get 으로만 본다
    w.advance(10)
    w.tick()
    assert entry(w)["phase"] == "ABANDONED"


def test_unsettled_protection_survives_pruning_and_a_worker_restart(tmp_path):
    w = drive(tmp_path)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    key = entry(w)["request_id"]
    for n in range(KEEP_CLOSED + 3):  # 다른 VM 의 닫힌 복구가 쌓여도
        other = w.engine.on_judgment(judgment(vm_id=VM2, recovery_id=UUID(int=500 + n)))
        w.engine._close(other, RecoveryStatus.CANCELLED, w.clock[0] + n)  # noqa: SLF001
    w.tick()
    assert any(r.protection and r.protection["request_id"] == key for r in w.engine.records())

    restarted = make(tmp_path, store=RecoveryStore(w.store.path), drive_vm_ids=[VM, VM2], id_start=2000)
    restarted.engine._protection = GatewayProtection(w.gateway, deadline_s=5.0, call_budget_s=1.0, clock=lambda: w.clock[0])  # noqa: SLF001
    w.gateway.bind(str(VM), NEW)
    restarted.tick()
    assert w.gateway.phase(key) == "RELEASED"  # 재시작 뒤에도 정산을 이어 갔다
    # 정산이 끝나면 보통의 닫힌 기록이다: 가장 오래된 것이라 보존 수(KEEP_CLOSED)를 넘어 정리된다
    assert not any(r.protection and r.protection["request_id"] == key for r in restarted.engine.records())
    assert len(restarted.engine.records()) == KEEP_CLOSED


def test_planned_but_unsent_protection_is_sent_with_the_same_key_after_a_restart(tmp_path):
    w = drive(tmp_path)
    w.gateway.fail("submit", RuntimeError("worker died before sending"))
    w.engine.on_judgment(zephyr_judgment())
    with pytest.raises(RuntimeError):
        w.engine._protect(w.record(), view())  # noqa: SLF001 - 저장 직후 죽음
    key = stored(w)["protection"]["request_id"]
    assert stored(w)["protection"]["phase"] == "PLANNED"

    restarted = make(tmp_path, store=RecoveryStore(w.store.path), drive_vm_ids=[VM, VM2], id_start=2000)
    restarted.engine._protection = GatewayProtection(w.gateway, deadline_s=5.0, call_budget_s=1.0, clock=lambda: restarted.clock[0])  # noqa: SLF001
    restarted.watcher.failing(VM, APP, execution_id=EXEC)
    restarted.tick()
    assert restarted.record().protection["request_id"] == key and w.gateway.issued == 1
    assert restarted.platform.recover_calls()[0][1]["mode"] == "force-restart"


# ---- 다른 실행·다른 host 의 보호 ----


def test_protection_of_an_earlier_execution_is_settled_before_a_new_one(tmp_path):
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    first = entry(w)["request_id"]
    # 그 사이 다른 경로로 새 실행 NEW 가 떠서 합류했고, 그 실행도 heartbeat 를 잃었다
    w.platform.views[VM] = {**view(), "execution_id": NEW, "domid": 6}
    w.watcher.failing(VM, APP, execution_id=NEW, domid=6)
    w.gateway.bind(str(VM), NEW)
    w.platform.results = [{"attempt": {}, "original_result": {"execution_id": EXEC}}]
    w.advance(5)
    w.tick()
    assert w.gateway.phase(first) == "RELEASED"  # 앞 보호를 새 실행 binding 으로 finish 한 뒤
    assert entry(w)["request_id"] != first and entry(w)["spec"]["target_execution_id"] == NEW  # 새 실행을 새 키로 보호
    assert w.platform.recover_calls()[-1][1]["expected_execution"] == {"execution_id": NEW}


def test_unsettled_earlier_protection_is_kept_and_holds_the_new_execution(tmp_path):
    """앞 실행의 보호를 아직 정산하지 못했으면 그 기록을 덮어쓰지 않고 보류한다. 새 키도 만들지 않는다."""
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    first = entry(w)["request_id"]
    w.platform.views[VM] = {**view(), "execution_id": NEW, "domid": 6}
    w.watcher.failing(VM, APP, execution_id=NEW, domid=6)
    w.gateway.bind(str(VM), NEW)
    w.gateway.fail("finish", GatewayRefused("AUTHORITY_CHANGED"))  # 앞 보호의 finish 가 이번에는 경합으로 거절됐다
    w.advance(5)
    w.tick()
    assert entry(w)["request_id"] == first and entry(w)["phase"] == "PROTECTED" and w.gateway.issued == 1
    assert len(w.platform.recover_calls()) == 1 and w.gateway.count("submit") == 1
    w.advance(5)
    w.tick()  # 다음 차례에 앞 보호를 finish 한 뒤 새 실행을 보호한다
    assert w.gateway.phase(first) == "RELEASED" and entry(w)["request_id"] != first and len(w.platform.recover_calls()) == 2


@pytest.mark.parametrize("cancel_lost", [False, True])
def test_intent_change_on_the_same_execution_cancels_the_old_protection_then_protects_again(tmp_path, cancel_lost):
    """의도 변경 → 옛 보호 cancel → 새 키로 보호 → force. cancel 결과를 모르면 새 키 없이 같은 키로 다시 본다."""
    w = drive(tmp_path)
    w.platform.results = [platform_down(), {"attempt": {}, "original_result": {"execution_id": NEW}}]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    first = entry(w)["request_id"]
    w.platform.views[VM] = view(revision=4)  # 같은 실행 EXEC, 의도 revision 만 바뀜
    w.gateway.bind(str(VM), EXEC, revision=4)
    if cancel_lost:
        w.gateway.fail("cancel", lost(), when="after")  # 취소됐지만 응답을 잃었다
        w.advance(5)
        w.tick()
        assert entry(w)["request_id"] == first and w.gateway.issued == 1 and len(w.platform.recover_calls()) == 1
        assert w.gateway.phase(first) == "CANCELLED" and entry(w)["phase"] == "PROTECTED"
    w.advance(5)
    w.tick()
    assert w.gateway.phase(first) == "CANCELLED" and w.gateway.count("cancel") == 1
    (_, (_, context)), = [c for c in w.gateway.calls if c[0] == "cancel"]
    assert context["target_execution_id"] == EXEC  # 원 실행에 대한 취소
    assert entry(w)["request_id"] != first and entry(w)["spec"]["intent_revision"] == 4 and w.gateway.issued == 2
    (_, spec, _) = w.platform.recover_calls()[-1]
    assert spec["mode"] == "force-restart" and spec["expected_intent_revision"] == 4 and len(w.platform.recover_calls()) == 2


def test_protection_from_an_earlier_host_boot_is_left_as_history(tmp_path):
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    first = entry(w)["request_id"]
    # host 재부팅(상태 디렉터리를 영속으로 둔 배치): 새 host, 같은 VM 의 새 실행
    w.gateway.host = OTHER_HOST
    w.platform.views[VM] = {**view(), "host_boot_id": OTHER_HOST, "execution_id": NEW}
    w.watcher.failing(VM, APP, execution_id=NEW)
    w.gateway.bind(str(VM), NEW)
    w.advance(5)
    w.tick()
    assert w.gateway.count("finish") == 0 and w.gateway.count("cancel") == 0  # 이전 host 보호는 정산하지 않는다
    assert entry(w)["request_id"].startswith(f"g1:{OTHER_HOST}:") and entry(w)["phase"] == "PROTECTED"
    assert w.gateway.rows[first]["phase"] == "PROTECTED"


def test_closed_recovery_with_an_earlier_host_protection_becomes_history(tmp_path):
    w = drive(tmp_path)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    key = entry(w)["request_id"]
    w.gateway.host = OTHER_HOST
    w.gateway.bind(str(VM), NEW)
    w.advance(5)
    w.tick()
    assert entry(w)["phase"] == "HISTORICAL" and w.gateway.count("finish") == 0 and w.gateway.rows[key]["phase"] == "PROTECTED"


def test_no_provider_holds_drive_force(tmp_path):
    w = drive(tmp_path)
    w.engine._protection = None  # noqa: SLF001 - Gateway 클라이언트가 없는 배치
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w)


# ---- 저장·설정 ----


@pytest.mark.parametrize("damage", [
    lambda p: p.pop("note"),
    lambda p: p.update(phase="DONE"),
    lambda p: p["spec"].update(target_vm=str(VM2)),
    lambda p: p["spec"].update(purpose="handoff"),
    lambda p: p["spec"].update(expected_generation="7"),
    lambda p: p["spec"].update(deadline_ms=p["spec"]["parent_deadline_ms"] + 1),
    lambda p: p["spec"].pop("expected_owner"),
])
def test_damaged_protection_record_blocks_the_store(tmp_path, damage):
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    data = json.loads(w.store.path.read_text(encoding="utf-8"))
    damage(data["recoveries"][0]["protection"])
    w.store.path.write_text(json.dumps(data), encoding="utf-8")
    store = RecoveryStore(w.store.path)
    assert store.load() == {} and store.history_lost


@pytest.mark.parametrize("count", [0, -1, "1", True])
def test_protection_request_count_must_cover_the_recorded_protection(tmp_path, count):
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    data = json.loads(w.store.path.read_text(encoding="utf-8"))
    data["recoveries"][0]["protection_requests"] = count  # 상한을 우회하는 손상
    w.store.path.write_text(json.dumps(data), encoding="utf-8")
    store = RecoveryStore(w.store.path)
    assert store.load() == {} and store.history_lost


def test_gateway_settings_are_read_and_bounded():
    settings = RecoverySettings.from_env({"XENOVA_RECOVERY_GATEWAY_SOCKET": "/run/lab/gw.sock", "XENOVA_RECOVERY_GATEWAY_CALL_BUDGET": "0.5",
                                          "XENOVA_RECOVERY_GATEWAY_PROTECT_DEADLINE": "3"})
    assert (settings.gateway_socket, settings.gateway_call_budget_s, settings.gateway_protect_deadline_s) == ("/run/lab/gw.sock", 0.5, 3.0)
    defaults = RecoverySettings()
    assert (defaults.gateway_socket, defaults.gateway_call_budget_s, defaults.gateway_protect_deadline_s) == (
        "/run/xenova-gateway/control.sock", 1.0, 5.0)
    for bad in ({"gateway_call_budget_s": 0}, {"gateway_call_budget_s": 11}, {"gateway_protect_deadline_s": 1.0},
                {"gateway_protect_deadline_s": 61}, {"gateway_socket": "run/gw.sock"}):
        with pytest.raises(ValueError):
            RecoverySettings(**bad)


def test_built_spec_is_accepted_by_the_gateway_normalizer(tmp_path):
    """실제 Gateway provider 의 입력 정규화가 Worker 의 원 spec 을 그대로 받는다(Linux 전용 모듈이라 없으면 건너뜀)."""
    root = Path(__file__).resolve().parents[2] / "gateway"
    sys.path.insert(0, str(root))
    try:
        provider = pytest.importorskip("xenova_gateway.provider")
        ipc = pytest.importorskip("xenova_gateway.ipc_contract")
    finally:
        sys.path.remove(str(root))
    w = drive(tmp_path)
    w.platform.results = [platform_down()]
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    spec = entry(w)["spec"]
    assert provider.normalize(dict(spec)) == spec
    assert ipc.request_key(ipc.new_request_id(HOST))[0] == HOST
