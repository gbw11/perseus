"""복구 정책 엔진 (S15P31E110-125). 가짜 플랫폼·감시기·단조 시계로 상태 기계와 영속·멱등·실행 경합 규칙을 돌린다."""

import json
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fake_gateway import FakeGateway

from dom0_manager.errors import DriverError, DriverTimeout, InvalidState, NotSupportedByPlatform, PlatformUnavailable, VMNotFound
from dom0_manager.models import VmState
from dom0_manager.recovery.engine import RecoveryEngine
from dom0_manager.recovery.events import EventLog
from dom0_manager.recovery.model import FailureReason, HealthState, Judgment, VmHealth
from dom0_manager.recovery.policy import RecoveryPolicy
from dom0_manager.recovery.protection import GatewayProtection
from dom0_manager.recovery.state import RecoveryRecord, RecoveryStatus, RecoveryStore

VM = UUID("4be41d5f-b8ef-408b-9709-7fe5ac11231f")
VM2 = UUID("0d950ea2-cc99-49df-9b60-a4ddf2c8e137")
REC = UUID(int=1)
HOST = "62813d5d-2c2b-4f5e-b8c3-613aa33bff87"
EXEC = "3fc9c97d-6ef2-46f2-a3fb-b159508ef368"
WALL = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def view(vm_id=VM, *, present=True, desired="running", applicable=True, lifecycle="REGISTERED", revision=3):
    return {
        "vm_id": str(vm_id), "lifecycle": lifecycle, "generation": 0, "desired_state": desired, "intent_applicable": applicable,
        "intent_revision": revision, "host_boot_id": HOST, "execution_id": EXEC if present else None,
        "domid": 5 if present else None, "xen_state": "running" if present else "stopped", "observation_revision": 7,
    }


class FakePlatform:
    """recover_once 안에서 저장소 파일을 읽어 '저장 뒤 호출' 을 확인한다. block 을 주면 그 이벤트가 set 될 때까지 막힌다(실행 경합 테스트)."""

    def __init__(self, store_path):
        self.store_path = store_path
        self.views = {}
        self.results = []  # recover_once 응답(dict) 또는 예외를 순서대로
        self.journal = {}  # recovery_attempt_id → recovery-operation 응답
        self.calls = []
        self.block = None
        self.entered = threading.Event()

    def view(self, vm_id):
        self.calls.append(("get", vm_id))
        data = self.views.get(vm_id, view(vm_id))
        if isinstance(data, Exception):
            raise data
        return dict(data)

    def recover_once(self, spec, *, execute):
        self.calls.append(("recover_once", dict(spec), execute))
        saved = json.loads(self.store_path.read_text(encoding="utf-8"))["recoveries"]
        mine = [r for r in saved if r["recovery_attempt_id"] == spec["recovery_attempt_id"]]
        assert mine and mine[0]["status"] == "EXECUTING", "recover_once must be called only after the attempt is durably recorded"
        self.entered.set()
        if self.block is not None:
            assert self.block.wait(5), "test did not release the blocked platform call"
        item = self.results.pop(0) if self.results else {"attempt": {}, "original_result": {"execution_id": "exec-new"}}
        if isinstance(item, Exception):
            raise item
        return item

    def attempt(self, recovery_attempt_id):
        self.calls.append(("attempt", recovery_attempt_id))
        journal = self.journal.get(str(recovery_attempt_id))
        if journal is None:
            raise VMNotFound("recovery attempt not found", platform_code="VM_NOT_FOUND")
        return journal

    def recover_calls(self):
        return [c for c in self.calls if c[0] == "recover_once"]


class FakeWatcher:
    def __init__(self):
        self.healths = {}

    def health(self, vm_id):
        return self.healths.get(vm_id)

    def set(self, vm_id, state, *, desired="running", reason=None, intent_known=True, domid=5, execution_id=None):
        self.healths[vm_id] = VmHealth(vm_id=vm_id, state=state, desired_state=desired, xen_state="running", heartbeat_age_s=None, reason=reason,
                                       manager_seq=1, intent_known=intent_known, domid=domid, execution_id=execution_id)

    def failing(self, vm_id, reason, **kwargs):
        """지금 실행(가짜 플랫폼 view 의 domid 5)에 대한 지금의 장애 증거."""
        self.set(vm_id, HealthState.UNHEALTHY, reason=reason, **kwargs)


def judgment(vm_id=VM, reason=FailureReason.HEARTBEAT_LOST, recovery_id=REC, kind="linux"):
    return Judgment(recovery_id=recovery_id, vm_id=vm_id, reason=reason, detected_monotonic=1000.0, guest_kind=kind,
                    desired_state="running", intent_revision=3, domid=5, xen_state=VmState.running, heartbeat_age_s=35.0)


def make(tmp_path, policy=None, store=None, *, inline=True, outbox=None, id_start=100, **engine_options):
    """outbox 를 주면 진짜 EventLog 에 저장한다. 재시작을 흉내 낼 때는 id_start 를 달리해 새 프로세스의 UUID 가 겹치지 않게 한다."""
    store = store or RecoveryStore(tmp_path / "recoveries.json")
    clock = [1000.0]
    ids = iter(UUID(int=n) for n in range(id_start, id_start + 900))
    lock = threading.Lock()

    def new_id():
        with lock:
            return next(ids)

    world = SimpleNamespace(clock=clock, events=[], sink_ok=True, platform=FakePlatform(store.path), watcher=FakeWatcher(), store=store)

    def sink(event):  # outbox 흉내. sink_ok 가 False 면 저장 실패: 이벤트는 레코드에 남아 있어야 한다
        if not world.sink_ok:
            return False
        if outbox is not None and not outbox.append(event):
            return False
        world.events.append(event)
        return True

    engine_options.setdefault("drive_vm_ids", ())  # 주행 VM 이 없는 것이 확인된 Worker. 분류를 모르는 경우는 None 을 따로 준다
    world.engine = RecoveryEngine(
        policy or RecoveryPolicy(backoff_s=(5.0, 15.0, 30.0)), world.platform, world.watcher, store,
        on_event=sink, clock=lambda: clock[0], wall=lambda: WALL, new_id=new_id, inline=inline, **engine_options,
    )
    world.tick = lambda: world.engine.tick(clock[0])
    world.advance = lambda s: clock.__setitem__(0, clock[0] + s)
    world.record = lambda: world.engine.records()[0]
    return world


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


# ---- 열기·실행·완료 ----


def test_first_attempt_is_graceful_restart_and_platform_completed_closes_the_recovery(tmp_path):
    w = make(tmp_path)
    record = w.engine.on_judgment(judgment())
    assert record.status == RecoveryStatus.DETECTED and record.attempt == 0
    assert json.loads(w.store.path.read_text())["recoveries"][0]["status"] == "DETECTED"  # 판정은 바로 저장
    w.tick()
    (_, spec, execute), = w.platform.recover_calls()
    assert execute is True
    assert spec["mode"] == "graceful-restart" and spec["expected_execution"] == {"execution_id": EXEC}
    assert (spec["expected_intent_revision"], spec["expected_lifecycle_generation"], spec["expected_host_boot_id"]) == (3, 0, HOST)
    assert spec["deadline_monotonic_ms"] == int((1000.0 + 110.0) * 1000)
    # 플랫폼이 새 실행을 관측하고 completed 를 돌려주면 복구는 성공이다 (계약 2.5). heartbeat 복귀는 기다리지 않는다
    assert record.status == RecoveryStatus.COMPLETED and record.attempt == 1 and record.execution_id == "exec-new"
    assert [e["type"] for e in w.events] == ["recovery.started", "recovery.completed"]
    started, completed = w.events
    assert (started["attempt"], started["mode"], started["reason"], started["recovery_id"]) == (1, "graceful-restart", "HEARTBEAT_LOST", str(REC))
    assert started["detected_at"] == "2026-10-23T01:00:00.000Z" and started["error"] is None
    assert completed["execution_id"] == "exec-new" and completed["recovery_duration_ms"] == 0 and completed["final"] is None


def test_heartbeat_not_returning_after_a_completed_restart_does_not_start_another_attempt(tmp_path):
    w = make(tmp_path)
    w.engine.on_judgment(judgment())
    w.tick()
    w.watcher.set(VM, HealthState.UNHEALTHY, reason=FailureReason.HEARTBEAT_LOST)
    w.advance(600)
    w.tick()
    assert len(w.platform.recover_calls()) == 1 and w.record().status == RecoveryStatus.COMPLETED


def test_watcher_healthy_while_waiting_closes_as_recovered_without_claiming_completion(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.record().status == RecoveryStatus.BACKOFF
    w.advance(2)
    w.watcher.set(VM, HealthState.HEALTHY)  # backoff 중에 VM 이 스스로(또는 사용자 조작으로) 살아남
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.RECOVERED and record.final and len(w.platform.recover_calls()) == 1
    last = w.events[-1]  # 이 시도로 새 실행을 관측한 게 아니므로 completed 가 아니다. 실패도 아니다
    assert last["type"] == "recovery.recovered" and last["final"] is None and last["error"] is None
    assert last["recovery_duration_ms"] == 2000 and last["execution_id"] is None
    assert "recovery.completed" not in [e["type"] for e in w.events]


def test_second_judgment_for_same_vm_is_absorbed_into_open_recovery(tmp_path):
    w = make(tmp_path)
    first = w.engine.on_judgment(judgment())
    again = w.engine.on_judgment(judgment(recovery_id=UUID(int=2)))
    assert again is first and len(w.engine.records()) == 1


def test_user_stop_cancels_before_and_between_attempts(tmp_path):
    w = make(tmp_path)
    w.engine.on_judgment(judgment())
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.tick()
    assert w.record().status == RecoveryStatus.CANCELLED and w.platform.recover_calls() == [] and w.events == []

    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "second.json"))
    w2.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w2.engine.on_judgment(judgment())
    w2.tick()
    w2.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w2.tick()
    record = w2.record()
    assert record.status == RecoveryStatus.CANCELLED and record.final
    assert w2.events[-1]["type"] == "recovery.cancelled" and w2.events[-1]["final"] is None and w2.events[-1]["error"]["code"] == "TARGET_NOT_ELIGIBLE"
    assert w2.events[-1]["recovery_duration_ms"] is not None


def test_intent_no_longer_running_at_attempt_time_cancels_without_calling_platform(tmp_path):
    w = make(tmp_path)
    w.platform.views[VM] = view(desired="stopped")
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.platform.recover_calls() == [] and w.record().status == RecoveryStatus.CANCELLED


# ---- 재전달·재시도 ----


def test_transient_refusal_replays_the_same_attempt_without_consuming_it(tmp_path):
    w = make(tmp_path)
    w.platform.results = [PlatformUnavailable("busy", platform_code="OPERATION_IN_PROGRESS")]
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.EXECUTING and record.replay is True and record.attempt == 1  # 결과를 모르는 시도로 둔다
    w.advance(5)
    w.tick()  # journal 이 없으니(플랫폼에 닿지 않음) 같은 spec 을 다시 보낸다
    first, second = (c[1] for c in w.platform.recover_calls())
    assert first == second and record.attempt == 1 and record.status == RecoveryStatus.COMPLETED

    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "second.json"))
    w2.platform.results = [PlatformUnavailable("busy", platform_code="OPERATION_IN_PROGRESS")]
    w2.engine.on_judgment(judgment())
    w2.tick()
    w2.watcher.set(VM, HealthState.HEALTHY)  # 플랫폼이 받지 않은 사이 살아남
    w2.advance(5)
    w2.tick()
    assert [c[0] for c in w2.platform.calls][-1] == "attempt"  # journal 부터 조회하고, 건강한 VM 은 다시 보내지 않는다
    assert len(w2.platform.recover_calls()) == 1 and w2.record().status == RecoveryStatus.RECOVERED


def test_response_loss_queries_journal_and_finishes_without_resending(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverTimeout("manager call timed out")]  # platform_code 없는 시한 초과 = 응답 유실
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    w.platform.journal[str(record.recovery_attempt_id)] = {"phase": "completed", "result": {"execution_id": "exec-journal"}}
    w.advance(5)
    w.tick()
    assert len(w.platform.recover_calls()) == 1 and record.status == RecoveryStatus.COMPLETED and record.execution_id == "exec-journal"


def test_failures_back_off_and_stop_after_max_attempts_with_new_attempt_ids(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")] * 3
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.BACKOFF and record.due_monotonic == 1005.0
    assert w.events[-1]["type"] == "recovery.failed" and w.events[-1]["final"] is False and w.events[-1]["next_retry_at"] == "2026-10-23T01:00:05.000Z"
    w.advance(4)
    w.tick()
    assert len(w.platform.recover_calls()) == 1  # backoff 전에는 부르지 않는다
    w.advance(1)
    w.tick()
    assert len(w.platform.recover_calls()) == 2 and w.platform.recover_calls()[1][1]["mode"] == "graceful-restart"  # 정체 모를 실패로는 force 로 올리지 않는다
    assert record.due_monotonic == 1005.0 + 15.0
    w.advance(15)
    w.tick()
    assert record.status == RecoveryStatus.FAILED and record.final and record.attempt == 3
    assert len({c[1]["recovery_attempt_id"] for c in w.platform.recover_calls()}) == 3  # 시도마다 새 attempt id
    final = w.events[-1]
    assert final["type"] == "recovery.failed" and final["final"] is True and final["error"]["platform_code"] == "XEN_EXECUTION_FAILED"
    assert final["recovery_duration_ms"] == 20000


@pytest.mark.parametrize("error", [
    DriverTimeout("stop deadline", platform_code="DEADLINE_EXCEEDED"),
    DriverTimeout("graceful shutdown timed out", platform_code="XEN_EXECUTION_FAILED"),
    NotSupportedByPlatform("no cooperative shutdown", platform_code="RECOVERY_MODE_UNSUPPORTED"),
])
def test_explicit_graceful_failure_escalates_the_next_attempt_to_force_only_when_enabled(tmp_path, error):
    w = make(tmp_path, policy=RecoveryPolicy(escalate_to_force=True))
    w.platform.results = [error]
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.record().status == RecoveryStatus.BACKOFF and w.record().graceful_failed_execution_id == EXEC
    w.watcher.failing(VM, FailureReason.HEARTBEAT_LOST)  # 같은 실행이 여전히 장애다: force 근거
    w.advance(5)
    w.tick()
    assert w.platform.recover_calls()[1][1]["mode"] == "force-restart"


def test_lost_adapter_result_rechecks_original_journal_without_force_escalation(tmp_path):
    w = make(tmp_path)
    w.platform.results = [RuntimeError("adapter bug")]
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.EXECUTING and record.replay and record.graceful_failed_execution_id is None
    spec=dict(record.request)
    w.platform.journal[spec['recovery_attempt_id']]={'phase':'completed','result':{'execution_id':'confirmed-new'}}
    w.advance(5)
    w.tick()
    assert len(w.platform.recover_calls()) == 1 and record.status==RecoveryStatus.COMPLETED
    assert record.request==spec and record.execution_id=='confirmed-new'


def test_stop_during_lost_response_is_not_false_cancellation_or_new_attempt(tmp_path):
    w=make(tmp_path);w.platform.results=[DriverTimeout('lost')]
    w.engine.on_judgment(judgment());w.tick();record=w.record();spec=dict(record.request)
    w.watcher.set(VM,HealthState.STOPPED,desired='stopped')
    w.platform.attempt=lambda _:(_ for _ in ()).throw(DriverTimeout('journal unavailable'))
    w.advance(5);w.tick()
    assert record.cancel_requested and record.status==RecoveryStatus.EXECUTING
    w.advance(260);w.tick()
    assert record.status==RecoveryStatus.FAILED and record.final
    assert record.last_error['code']=='RECONCILE_REQUIRED' and record.graceful_failed_execution_id is None
    assert record.request==spec and len(w.platform.recover_calls())==1


def test_target_changed_waits_for_the_watcher_and_times_out_into_a_new_attempt(tmp_path):
    w = make(tmp_path)
    w.platform.results = [InvalidState("execution changed", platform_code="TARGET_CHANGED")]
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.VERIFYING and len(w.platform.recover_calls()) == 1
    w.advance(150)
    w.tick()
    assert record.status == RecoveryStatus.BACKOFF and record.last_error["code"] == "REOBSERVE_TIMEOUT"

    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "second.json"))
    w2.platform.results = [InvalidState("execution changed", platform_code="TARGET_CHANGED")]
    w2.engine.on_judgment(judgment())
    w2.tick()
    w2.watcher.set(VM, HealthState.HEALTHY)  # 대상이 바뀐 뒤 건강해짐: 우리 시도의 완료가 아니다
    w2.advance(5)
    w2.tick()
    assert w2.record().status == RecoveryStatus.RECOVERED
    assert w2.events[-1]["type"] == "recovery.recovered" and w2.events[-1]["error"] is None


# ---- mode 선택 ----


def test_absent_domain_uses_start_if_absent_with_observation_revision(tmp_path):
    w = make(tmp_path)
    w.platform.views[VM] = view(present=False)
    w.engine.on_judgment(judgment(reason=FailureReason.DOMAIN_ABSENT))
    w.tick()
    spec = w.platform.recover_calls()[0][1]
    assert spec["mode"] == "start-if-absent" and spec["expected_execution"] == {"absent_observation_revision": 7}


@pytest.mark.parametrize("reason, kind", [(FailureReason.APP_HEARTBEAT_LOST, "zephyr"), (FailureReason.DOMAIN_CRASHED, "linux")])
def test_zephyr_and_crashed_domains_use_force_restart(tmp_path, reason, kind):
    w = make(tmp_path, non_drive_vm_ids=[VM])
    w.watcher.failing(VM, reason)
    if reason == FailureReason.DOMAIN_CRASHED:
        w.platform.views[VM] = crashed_view()
    w.engine.on_judgment(judgment(reason=reason, kind=kind))
    w.tick()
    assert w.platform.recover_calls()[0][1]["mode"] == "force-restart"


def test_force_escalation_is_off_by_default(tmp_path):
    assert RecoveryPolicy().escalate_to_force is False and RecoveryPolicy.from_env({}).escalate_to_force is False
    w = make(tmp_path)
    w.platform.results = [DriverTimeout("stop deadline", platform_code="DEADLINE_EXCEEDED")]
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.record().status == RecoveryStatus.BACKOFF and w.record().graceful_failed_execution_id is None
    w.advance(5)
    w.tick()
    assert [c[1]["mode"] for c in w.platform.recover_calls()] == ["graceful-restart", "graceful-restart"]  # 운영자가 켜지 않으면 파괴적 재시작으로 올리지 않는다


def test_mode_unsupported_without_escalation_is_final(tmp_path):
    w = make(tmp_path)
    w.platform.results = [NotSupportedByPlatform("no approval", platform_code="RECOVERY_MODE_UNSUPPORTED")]
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.FAILED and record.final and record.attempt == 1
    assert w.events[-1]["final"] is True and w.events[-1]["error"]["platform_code"] == "RECOVERY_MODE_UNSUPPORTED"


# ---- 실행 경합 (실제 실행 스레드) ----


def test_heartbeat_recovering_during_execution_does_not_close_until_the_platform_returns(tmp_path):
    w = make(tmp_path, inline=False)
    w.platform.block = threading.Event()
    w.engine.on_judgment(judgment())
    w.tick()
    wait_until(w.platform.entered.is_set)
    w.watcher.set(VM, HealthState.HEALTHY)  # 정지 직전에 heartbeat 가 잠깐 돌아옴
    w.tick()
    assert w.record().status == RecoveryStatus.EXECUTING  # 실행 중에는 닫지 않는다
    w.platform.block.set()
    wait_until(lambda: all(f.done() for f in w.engine._futures.values()))  # noqa: SLF001 - 결과 회수 대기
    w.tick()
    assert w.record().status == RecoveryStatus.COMPLETED and w.events[-1]["type"] == "recovery.completed"
    w.engine.shutdown()


def test_user_stop_during_execution_is_recorded_and_closed_by_the_platform_result(tmp_path):
    w = make(tmp_path, inline=False)
    w.platform.block = threading.Event()
    w.engine.on_judgment(judgment())
    w.tick()
    wait_until(w.platform.entered.is_set)
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.EXECUTING and record.cancel_requested is True
    assert json.loads(w.store.path.read_text())["recoveries"][0]["cancel_requested"] is True
    w.platform.block.set()
    wait_until(lambda: all(f.done() for f in w.engine._futures.values()))  # noqa: SLF001
    w.engine.collect(w.clock[0])
    assert record.status == RecoveryStatus.COMPLETED and w.events[-1]["type"] == "recovery.completed"  # 플랫폼이 새 실행을 올렸다: 사실대로
    w.engine.shutdown()

    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "second.json"), inline=False)
    w2.platform.block = threading.Event()
    w2.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w2.engine.on_judgment(judgment())
    w2.tick()
    wait_until(w2.platform.entered.is_set)
    w2.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w2.tick()
    w2.platform.block.set()
    wait_until(lambda: all(f.done() for f in w2.engine._futures.values()))  # noqa: SLF001
    w2.engine.collect(w2.clock[0])
    record = w2.record()  # 실패했지만 사용자가 껐으니 재시도하지 않고 취소
    assert record.status == RecoveryStatus.CANCELLED and w2.events[-1]["type"] == "recovery.cancelled"
    w2.engine.shutdown()


def test_queued_attempt_deadline_is_computed_at_its_own_start(tmp_path):
    w = make(tmp_path, inline=False)
    gate = threading.Event()
    w.platform.block = gate
    w.engine.on_judgment(judgment())
    w.engine.on_judgment(judgment(vm_id=VM2, recovery_id=UUID(int=2)))
    w.tick()
    wait_until(w.platform.entered.is_set)
    w.advance(100)  # 첫 VM 의 시도가 100초 걸리는 동안 둘째는 큐에서 기다린다
    w.platform.block = None
    gate.set()
    wait_until(lambda: len(w.platform.recover_calls()) == 2)
    wait_until(lambda: all(f.done() for f in w.engine._futures.values()))  # noqa: SLF001
    first, second = (c[1] for c in w.platform.recover_calls())
    assert first["vm_id"] == str(VM) and first["deadline_monotonic_ms"] == int((1000.0 + 110.0) * 1000)
    assert second["vm_id"] == str(VM2) and second["deadline_monotonic_ms"] == int((1100.0 + 110.0) * 1000)  # 큐 대기가 deadline 을 갉아먹지 않는다
    w.engine.shutdown()


# ---- 영속·fail-safe ----


def pending_event(event_id=UUID(int=77)):
    return {
        "event_id": str(event_id), "type": "recovery.started", "vm_id": str(VM), "recovery_id": str(REC), "recovery_attempt_id": str(UUID(int=55)),
        "attempt": 1, "reason": "HEARTBEAT_LOST", "mode": "graceful-restart", "detected_at": "2026-10-23T00:59:00.000Z",
        "occurred_at": "2026-10-23T00:59:10.000Z", "attempt_duration_ms": None, "recovery_duration_ms": None, "execution_id": None,
        "final": None, "next_retry_at": None, "error": None,
    }


def test_recovered_and_cancelled_after_a_failed_attempt_are_recovery_level_events(tmp_path):
    for state, desired, expected in ((HealthState.HEALTHY, "running", "recovery.recovered"), (HealthState.STOPPED, "stopped", "recovery.cancelled")):
        w = make(tmp_path, store=RecoveryStore(tmp_path / f"{expected}.json"))
        w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
        w.engine.on_judgment(judgment())
        w.tick()  # 시도 1: started, failed(final:false)
        w.advance(2)
        w.watcher.set(VM, state, desired=desired)
        w.tick()  # backoff 중 회복·정지
        first = w.events[0]["recovery_attempt_id"]
        assert [e["type"] for e in w.events] == ["recovery.started", "recovery.failed", expected]
        assert terminal_per_attempt([e for e in w.events if e["recovery_attempt_id"] is not None]) == {first: (1, 1)}  # 시도 1 의 종료는 failed 하나
        end = w.events[-1]
        assert end["recovery_attempt_id"] is None and end["attempt_duration_ms"] is None and end["attempt"] == 1 and end["recovery_duration_ms"] == 2000


def test_state_file_without_attempt_closed_marks_a_backoff_attempt_as_closed(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w.engine.on_judgment(judgment())
    w.tick()  # 시도 1 실패 → BACKOFF
    path = tmp_path / "recoveries.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in data["recoveries"]:
        del record["attempt_closed"]  # 필드가 생기기 전 Worker 가 남긴 파일
    path.write_text(json.dumps(data), encoding="utf-8")
    assert RecoveryStore(path).load()[REC].attempt_closed is True

    replaying = saved_executing()
    replaying.status, replaying.replay, replaying.due_monotonic = RecoveryStatus.BACKOFF, True, 1005.0  # 재전달 대기: 시도는 아직 열려 있다
    legacy = replaying.to_json()
    del legacy["attempt_closed"]
    path.write_text(json.dumps({"version": 1, "history_lost": False, "recoveries": [legacy]}), encoding="utf-8")
    assert RecoveryStore(path).load()[REC].attempt_closed is False


def test_recovered_while_the_attempt_is_still_open_stays_on_that_attempt(tmp_path):
    w = make(tmp_path)
    w.platform.results = [InvalidState("execution changed", platform_code="TARGET_CHANGED")]  # 시도 1 은 종료 이벤트 없이 VERIFYING
    w.engine.on_judgment(judgment())
    w.tick()
    w.watcher.set(VM, HealthState.HEALTHY)
    w.advance(5)
    w.tick()
    started, end = w.events
    assert end["type"] == "recovery.recovered" and end["recovery_attempt_id"] == started["recovery_attempt_id"] and end["attempt_duration_ms"] is not None


def test_valid_pending_events_are_restored(tmp_path):
    record = saved_executing()
    record.pending_events = [pending_event()]
    RecoveryStore(tmp_path / "recoveries.json").save({REC: record})
    store = RecoveryStore(tmp_path / "recoveries.json")
    assert store.load()[REC].pending_events == [pending_event()] and store.history_lost is False


def saved_executing(attempt_id=UUID(int=55)):
    spec = {"vm_id": str(VM), "recovery_attempt_id": str(attempt_id), "expected_host_boot_id": HOST, "expected_lifecycle_generation": 0,
            "expected_intent_revision": 3, "expected_execution": {"execution_id": EXEC}, "mode": "graceful-restart", "deadline_monotonic_ms": 1110000}
    return RecoveryRecord(recovery_id=REC, vm_id=VM, reason="HEARTBEAT_LOST", guest_kind="linux", status=RecoveryStatus.EXECUTING,
                          detected_at="2026-10-23T00:59:00.000Z", detected_monotonic=940.0, attempt=1, recovery_attempt_id=attempt_id,
                          request=spec, mode="graceful-restart", attempt_started_monotonic=950.0)


def test_worker_restart_restores_open_recovery_and_consults_journal_before_resending(tmp_path):
    store = RecoveryStore(tmp_path / "recoveries.json")
    store.save({REC: saved_executing()})
    w = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"))
    restored = w.record()
    assert restored.recovery_id == REC and restored.attempt == 1 and restored.replay is True
    w.platform.journal[str(UUID(int=55))] = {"phase": "failed", "error": {"code": "XEN_EXECUTION_FAILED", "message": "create failed"}}
    w.tick()
    assert w.platform.recover_calls() == []  # journal 이 실패를 알려 주면 다시 보내지 않는다
    assert restored.status == RecoveryStatus.BACKOFF and restored.attempt == 1 and restored.last_error["platform_code"] == "XEN_EXECUTION_FAILED"
    w.advance(5)
    w.tick()
    assert restored.attempt == 2 and w.platform.recover_calls()[0][1]["recovery_attempt_id"] != str(UUID(int=55))


def test_restored_executing_consults_the_journal_before_the_watcher_may_close_it(tmp_path):
    store = RecoveryStore(tmp_path / "recoveries.json")
    store.save({REC: saved_executing()})
    w = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"))
    w.watcher.set(VM, HealthState.HEALTHY)  # Worker 가 죽어 있는 사이 플랫폼이 재시작을 끝냈다
    w.platform.journal[str(UUID(int=55))] = {"phase": "completed", "result": {"execution_id": "exec-journal"}}
    w.tick()
    record = w.record()
    assert [c[0] for c in w.platform.calls] == ["attempt"]  # 감시기로 닫기 전에 recovery-operation 부터
    assert record.status == RecoveryStatus.COMPLETED and record.execution_id == "exec-journal" and w.events[-1]["type"] == "recovery.completed"


def test_restored_executing_with_user_stop_still_consults_the_journal_then_cancels(tmp_path):
    store = RecoveryStore(tmp_path / "recoveries.json")
    store.save({REC: saved_executing()})
    w = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"))
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.tick()
    record = w.record()
    assert [c[0] for c in w.platform.calls] == ["attempt"] and w.platform.recover_calls() == []
    assert record.status == RecoveryStatus.CANCELLED and record.cancel_requested is True and w.events[-1]["error"]["code"] == "TARGET_NOT_ELIGIBLE"

    store2 = RecoveryStore(tmp_path / "second.json")
    store2.save({REC: saved_executing()})
    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "second.json"))
    w2.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w2.platform.journal[str(UUID(int=55))] = {"phase": "completed", "result": {"execution_id": "exec-journal"}}
    w2.tick()  # 플랫폼이 이미 새 실행을 올렸다면 사실대로 완료
    assert w2.record().status == RecoveryStatus.COMPLETED and w2.record().execution_id == "exec-journal"


def test_restored_executing_healthy_without_journal_is_recovered_not_resent(tmp_path):
    store = RecoveryStore(tmp_path / "recoveries.json")
    store.save({REC: saved_executing()})
    w = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"))
    w.watcher.set(VM, HealthState.HEALTHY)
    w.tick()
    assert [c[0] for c in w.platform.calls] == ["attempt"] and w.record().status == RecoveryStatus.RECOVERED
    assert w.events[-1]["type"] == "recovery.recovered" and w.events[-1]["error"] is None


def test_response_loss_keeps_the_attempt_open_until_the_journal_answers_even_if_healthy(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverTimeout("manager call timed out")]
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.EXECUTING and record.replay is True
    w.watcher.set(VM, HealthState.HEALTHY)  # 플랫폼이 재시작을 끝내 heartbeat 가 돌아왔을 수 있다
    w.advance(2)
    w.tick()
    assert record.status == RecoveryStatus.EXECUTING  # 결과를 모른다: 감시기로 닫지 않는다
    w.platform.journal[str(record.recovery_attempt_id)] = {"phase": "completed", "result": {"execution_id": "exec-journal"}}
    w.advance(3)
    w.tick()
    assert record.status == RecoveryStatus.COMPLETED and record.execution_id == "exec-journal" and len(w.platform.recover_calls()) == 1


def test_completed_without_a_new_execution_id_is_not_reported_as_completed(tmp_path):
    w = make(tmp_path)
    w.platform.results = [{"attempt": {}, "original_result": {"execution_id": EXEC}}]  # 기존 실행 그대로
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.FAILED and record.final and record.last_error["code"] == "RECONCILE_REQUIRED"
    assert w.events[-1]["type"] == "recovery.failed" and w.events[-1]["final"] is True and "recovery.completed" not in [e["type"] for e in w.events]


def test_transient_view_failure_before_the_first_attempt_waits_for_the_retry_interval(tmp_path):
    w = make(tmp_path)
    w.platform.views[VM] = PlatformUnavailable("busy", platform_code="OPERATION_IN_PROGRESS")
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.DETECTED and record.attempt == 0 and record.due_monotonic == 1005.0
    w.advance(2)
    w.tick()
    assert len(w.platform.calls) == 1  # due 전에는 다시 부르지 않는다
    w.platform.views[VM] = view()
    w.advance(3)
    w.tick()
    assert record.status == RecoveryStatus.COMPLETED and len(w.platform.recover_calls()) == 1


# ---- 상태 전이와 이벤트 outbox 의 일관성 ----


def test_event_not_stored_before_a_restart_is_stored_later_with_the_same_event_id(tmp_path):
    w = make(tmp_path)
    w.engine.on_judgment(judgment())
    w.sink_ok = False  # outbox 에 쓸 수 없다(디스크·권한). 상태는 저장되고 이벤트는 레코드에 남는다
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.COMPLETED and w.events == []
    saved = json.loads(w.store.path.read_text(encoding="utf-8"))["recoveries"][0]
    assert saved["status"] == "COMPLETED" and [e["type"] for e in saved["pending_events"]] == ["recovery.started", "recovery.completed"]
    expected_ids = [e["event_id"] for e in saved["pending_events"]]
    w.advance(5)
    w.tick()
    assert w.events == [] and len(w.engine.records()) == 1  # 계속 실패: 발행 없음, 레코드는 정리 대상이 아니다

    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"))  # Worker 재시작(상태 저장 직후 종료와 같다)
    w2.tick()
    assert [e["type"] for e in w2.events] == ["recovery.started", "recovery.completed"]
    assert [e["event_id"] for e in w2.events] == expected_ids  # 같은 event_id 로 발행: Spring 이 중복을 거를 수 있다
    saved = json.loads(w2.store.path.read_text(encoding="utf-8"))["recoveries"][0]
    assert saved["pending_events"] == [] and w2.platform.calls == []  # 닫힌 복구를 다시 처리하지 않는다
    w2.advance(5)
    w2.tick()
    assert len(w2.events) == 2  # 한 번만


def test_state_save_failure_holds_the_terminal_event_until_the_state_is_durable(tmp_path, monkeypatch):
    w = make(tmp_path)
    w.engine.on_judgment(judgment())
    real_save, fail = RecoveryStore.save, {"on": False}
    monkeypatch.setattr(RecoveryStore, "save", lambda self, records: False if fail["on"] else real_save(self, records))
    original = w.platform.recover_once

    def recover_once(spec, *, execute):
        result = original(spec, execute=execute)
        fail["on"] = True  # 결과를 받은 직후 상태 파일을 쓸 수 없게 된다
        return result

    w.platform.recover_once = recover_once
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.COMPLETED  # 메모리에서는 닫혔지만
    assert [e["type"] for e in w.events] == ["recovery.started"]  # 상태가 저장되기 전에는 completed 를 내지 않는다
    assert json.loads(w.store.path.read_text(encoding="utf-8"))["recoveries"][0]["status"] == "EXECUTING"  # 여기서 죽으면 재시작 뒤 journal 로 결과를 다시 받는다
    fail["on"] = False
    w.advance(5)
    w.tick()
    assert [e["type"] for e in w.events] == ["recovery.started", "recovery.completed"]
    saved = json.loads(w.store.path.read_text(encoding="utf-8"))["recoveries"][0]
    assert saved["status"] == "COMPLETED" and saved["pending_events"] == []


def test_failed_attempt_event_is_recorded_with_the_state_transition(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w.engine.on_judgment(judgment())
    w.sink_ok = False
    w.tick()
    saved = json.loads(w.store.path.read_text(encoding="utf-8"))["recoveries"][0]
    assert saved["status"] == "BACKOFF" and [e["type"] for e in saved["pending_events"]] == ["recovery.started", "recovery.failed"]
    assert saved["pending_events"][1]["error"] == {"code": "XEN_EXECUTION_FAILED", "platform_code": "XEN_EXECUTION_FAILED", "message": "xl failed"}
    w.sink_ok = True
    w.advance(1)
    w.tick()
    assert [e["type"] for e in w.events] == ["recovery.started", "recovery.failed"] and w.events[1]["final"] is False


def terminal_per_attempt(events):
    """attempt id 별 (started 수, terminal 수). terminal = completed 또는 failed."""
    result = {}
    for e in events:
        started, terminal = result.get(e["recovery_attempt_id"], (0, 0))
        if e["type"] == "recovery.started":
            started += 1
        else:
            terminal += 1
        result[e["recovery_attempt_id"]] = (started, terminal)
    return result


def test_crash_after_the_outbox_write_republishes_the_same_event_id_and_the_outbox_keeps_one(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "recovery-events.jsonl")
    w = make(tmp_path, outbox=log)
    w.engine.on_judgment(judgment())
    real_save, crashed = RecoveryStore.save, {"on": False}

    def save(self, records):
        if crashed["on"]:
            return False  # 이 뒤로는 디스크에 아무것도 남지 않는다(프로세스 종료와 같다)
        return real_save(self, records)

    monkeypatch.setattr(RecoveryStore, "save", save)
    real_append = log.append

    def append(event):
        stored = real_append(event)
        if event["type"] == "recovery.completed":
            crashed["on"] = True  # outbox 에 쓴 직후, 대기 목록을 비운 상태를 저장하기 전에 죽는다
        return stored

    log.append = append
    w.tick()
    saved = json.loads(w.store.path.read_text(encoding="utf-8"))["recoveries"][0]
    assert saved["status"] == "COMPLETED" and [e["type"] for e in saved["pending_events"]] == ["recovery.completed"]
    completed_id = saved["pending_events"][0]["event_id"]

    monkeypatch.setattr(RecoveryStore, "save", real_save)
    restarted_log = EventLog(tmp_path / "recovery-events.jsonl")
    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"), outbox=restarted_log, id_start=500)
    w2.tick()
    assert [e["event_id"] for e in w2.events] == [completed_id]  # 같은 event_id 로 다시 낸다
    assert w2.platform.calls == []  # 닫힌 복구를 다시 처리하지 않는다
    stored = restarted_log.events()  # 파일에는 두 줄이지만 같은 event_id 는 하나로 읽힌다
    assert [e["type"] for e in stored] == ["recovery.started", "recovery.completed"]
    assert terminal_per_attempt(stored) == {stored[0]["recovery_attempt_id"]: (1, 1)}
    assert json.loads(w2.store.path.read_text(encoding="utf-8"))["recoveries"][0]["pending_events"] == []


def test_every_attempt_has_exactly_one_terminal_event_across_outbox_failures_and_a_restart(tmp_path):
    log = EventLog(tmp_path / "recovery-events.jsonl")
    w = make(tmp_path, outbox=log)
    w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w.engine.on_judgment(judgment())
    w.sink_ok = False  # 첫 시도의 started·failed 를 outbox 에 쓰지 못한 채 죽는다
    w.tick()
    assert log.events() == []

    w2 = make(tmp_path, store=RecoveryStore(tmp_path / "recoveries.json"), outbox=EventLog(tmp_path / "recovery-events.jsonl"), id_start=500)
    w2.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w2.tick()  # 남은 이벤트부터 같은 event_id 로
    w2.advance(5)
    w2.tick()  # 두 번째 시도 실패
    w2.sink_ok = False
    w2.advance(15)
    w2.tick()  # 세 번째 시도 성공, completed 는 outbox 에 못 씀
    assert w2.record().status == RecoveryStatus.COMPLETED
    w2.sink_ok = True
    w2.advance(5)
    w2.tick()

    stored = EventLog(tmp_path / "recovery-events.jsonl").events()
    assert len({e["event_id"] for e in stored}) == len(stored)
    per_attempt = terminal_per_attempt(stored)
    assert len(per_attempt) == 3 and all(counts == (1, 1) for counts in per_attempt.values())
    assert [e["type"] for e in stored].count("recovery.completed") == 1
    assert [e["attempt"] for e in stored if e["type"] != "recovery.started"] == [1, 2, 3]


@pytest.mark.parametrize("mutate", [
    lambda r: r.__setattr__("request", None),  # EXECUTING 인데 spec 없음
    lambda r: r.__setattr__("recovery_attempt_id", UUID(int=56)),  # spec 의 attempt id 와 불일치
    lambda r: r.__setattr__("mode", "reboot"),
    lambda r: r.__setattr__("guest_kind", "windows"),
    lambda r: r.__setattr__("attempt_started_monotonic", float("nan")),
    lambda r: r.__setattr__("due_monotonic", float("inf")),
    lambda r: r.__setattr__("pending_events", [{"event_id": "x", "recovery_id": str(UUID(int=9)), "type": "recovery.started"}]),  # 다른 복구의 이벤트
    lambda r: r.__setattr__("pending_events", [{**pending_event(), "occurred_at": None}]),  # JSON 은 맞지만 계약 필드가 손상됨
    lambda r: r.__setattr__("pending_events", [{**pending_event(), "attempt": "1"}]),
    lambda r: r.__setattr__("pending_events", [{k: v for k, v in pending_event().items() if k != "reason"}]),
    lambda r: r.__setattr__("pending_events", [{**pending_event(), "vm_id": str(VM2)}]),  # 다른 VM
    lambda r: r.__setattr__("pending_events", [pending_event(), pending_event()]),  # 같은 event_id 두 번
])
def test_store_invariant_violation_marks_history_lost_and_blocks_execution(tmp_path, mutate):
    record = saved_executing()
    mutate(record)
    path = tmp_path / "recoveries.json"
    path.write_text(json.dumps({"version": 1, "history_lost": False, "recoveries": [record.to_json()]}), encoding="utf-8")
    store = RecoveryStore(path)
    assert store.load() == {} and store.history_lost and path.with_suffix(".json.corrupt").exists()


def test_corrupt_store_blocks_execution_but_keeps_judging(tmp_path, caplog):
    path = tmp_path / "recoveries.json"
    path.write_text("{broken", encoding="utf-8")
    w = make(tmp_path, store=RecoveryStore(path))
    assert w.store.history_lost and (tmp_path / "recoveries.json.corrupt").exists()
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.platform.calls == [] and w.record().status == RecoveryStatus.DETECTED
    assert json.loads(path.read_text())["history_lost"] is True
    assert 'remove ' not in caplog.text and 'until the file is removed' not in caplog.text


def test_policy_none_records_but_never_calls_platform(tmp_path):
    w = make(tmp_path, policy=RecoveryPolicy(policy="none"))
    w.engine.on_judgment(judgment())
    w.tick()
    w.advance(600)
    w.tick()
    assert w.platform.calls == [] and w.record().status == RecoveryStatus.DETECTED
    w.watcher.set(VM, HealthState.HEALTHY)
    w.tick()
    assert w.record().status == RecoveryStatus.RECOVERED and w.events == []  # 시도가 없었으니 completed 도, 이벤트도 없다


def test_store_rejects_two_open_recoveries_for_one_vm(tmp_path):
    path = tmp_path / "recoveries.json"
    base = RecoveryRecord(recovery_id=REC, vm_id=VM, reason="HEARTBEAT_LOST", guest_kind="linux", status=RecoveryStatus.DETECTED,
                          detected_at="2026-10-23T00:59:00.000Z", detected_monotonic=1.0)
    other = RecoveryRecord(**{**base.__dict__, "recovery_id": UUID(int=9)})
    path.write_text(json.dumps({"version": 1, "history_lost": False, "recoveries": [base.to_json(), other.to_json()]}), encoding="utf-8")
    store = RecoveryStore(path)
    assert store.load() == {} and store.history_lost


def test_pruning_closed_recoveries_is_persisted(tmp_path):
    path = tmp_path / "recoveries.json"
    closed = {}
    for n in range(1, 26):
        closed[UUID(int=n)] = RecoveryRecord(recovery_id=UUID(int=n), vm_id=UUID(int=1000 + n), reason="HEARTBEAT_LOST", guest_kind="linux",
                                              status=RecoveryStatus.COMPLETED, detected_at="2026-10-23T00:59:00.000Z", detected_monotonic=float(n),
                                              closed_monotonic=float(n), attempt=1, recovery_attempt_id=UUID(int=500 + n))
    RecoveryStore(path).save(closed)
    w = make(tmp_path, store=RecoveryStore(path))
    w.tick()
    assert len(w.engine.records()) == 20 and len(json.loads(path.read_text())["recoveries"]) == 20
    again = make(tmp_path, store=RecoveryStore(path))
    assert len(again.engine.records()) == 20 and min(r.closed_monotonic for r in again.engine.records()) == 6.0


def test_policy_from_env_and_validation():
    policy = RecoveryPolicy.from_env({"XENOVA_RECOVERY_POLICY": "none", "XENOVA_RECOVERY_MAX_ATTEMPTS": "2", "XENOVA_RECOVERY_BACKOFF": "1, 2",
                                      "XENOVA_RECOVERY_ESCALATE_TO_FORCE": "0"})
    assert (policy.enabled, policy.max_attempts, policy.backoff_s, policy.escalate_to_force) == (False, 2, (1.0, 2.0), False)
    assert policy.backoff_after(5) == 2.0
    with pytest.raises(ValueError):
        RecoveryPolicy(attempt_deadline_s=121)
    with pytest.raises(ValueError):
        RecoveryPolicy(policy="failover")



# ---- 의도를 못 읽은 주기의 건강 스냅샷 (S15P31E110-336) ----

STALE_STOPPED = dict(desired="stopped", intent_known=False)  # 마지막으로 읽은 의도는 stopped, 지금은 읽지 못함


def test_waiting_recovery_is_not_cancelled_by_a_stale_stopped_intent(tmp_path):
    w = make(tmp_path)
    w.platform.results = [DriverError("xl failed", platform_code="XEN_EXECUTION_FAILED")]
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.record().status == RecoveryStatus.BACKOFF
    w.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE, **STALE_STOPPED)
    w.advance(2)
    w.tick()
    assert w.record().status == RecoveryStatus.BACKOFF and w.record().open  # 오래된 stopped 로 닫지 않는다
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")  # 의도를 다시 읽었고 정말 stopped
    w.advance(1)
    w.tick()
    assert w.record().status == RecoveryStatus.CANCELLED and w.record().last_error["code"] == "TARGET_NOT_ELIGIBLE"


def test_running_attempt_is_not_cancel_requested_by_a_stale_stopped_intent(tmp_path):
    w = make(tmp_path, inline=False)
    w.platform.block = threading.Event()
    w.engine.on_judgment(judgment())
    w.tick()
    wait_until(w.platform.entered.is_set)
    w.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE, **STALE_STOPPED)
    w.tick()
    record = w.record()
    assert record.status == RecoveryStatus.EXECUTING and record.cancel_requested is False
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.tick()
    assert record.cancel_requested is True  # 다시 읽은 stopped 는 취소 요청이다
    w.platform.block.set()
    wait_until(lambda: all(f.done() for f in w.engine._futures.values()))  # noqa: SLF001
    w.engine.shutdown()


def test_replay_is_not_cancelled_by_a_stale_stopped_intent(tmp_path):
    """응답을 잃었고 플랫폼에 닿은 기록도 없다. 오래된 stopped 로 TARGET_NOT_ELIGIBLE 종료하지 않고 같은 spec 으로 다시 보낸다."""
    w = make(tmp_path)
    w.platform.results = [DriverTimeout("lost")]
    w.engine.on_judgment(judgment())
    w.tick()
    record = w.record()
    spec = dict(record.request)
    w.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE, **STALE_STOPPED)
    w.advance(5)
    w.tick()
    assert record.last_error is None or record.last_error.get("code") != "TARGET_NOT_ELIGIBLE"
    assert record.status != RecoveryStatus.CANCELLED
    calls = w.platform.recover_calls()
    assert len(calls) == 2 and calls[-1][1] == spec  # 같은 원 spec 재전달(플랫폼이 expected_intent_revision 을 다시 본다)



# ---- force-restart 증거 게이트 (S15P31E110-347) ----

APP = FailureReason.APP_HEARTBEAT_LOST


def crashed_view():
    return {**view(), "xen_state": "crashed"}


def zephyr_judgment():
    return judgment(reason=APP, kind="zephyr")


def protector(root):
    """현재 binding 이 없는 가짜 Gateway 의 보호 제공자. 이 시험들은 보호 전에 증거 단계에서 보류된다."""
    return GatewayProtection(FakeGateway(HOST, time.monotonic), deadline_s=5.0, call_budget_s=1.0)


def bound_gateway(w):
    """VM 의 binding 이 EXEC 인 가짜 Gateway 를 이 엔진의 보호 제공자로 붙인다."""
    gateway = FakeGateway(HOST, lambda: w.clock[0])
    gateway.bind(str(VM), EXEC)
    w.engine._protection = GatewayProtection(gateway, deadline_s=5.0, call_budget_s=1.0, clock=lambda: w.clock[0])  # noqa: SLF001
    return gateway


def held(w):
    """보류: 플랫폼 호출·attempt·이벤트 0, 복구는 열려 있고 잠시 뒤 다시 본다."""
    record = w.record()
    return (w.platform.recover_calls() == [] and record.attempt == 0 and record.open and w.events == []
            and record.due_monotonic is not None)


@pytest.mark.parametrize("health", [
    None,  # 건강 스냅샷 없음
    dict(state=HealthState.OBSERVATION_UNAVAILABLE),  # 동료 채널 증거 없음(335) 등
    dict(state=HealthState.UNHEALTHY, reason=APP, intent_known=False),  # 의도를 못 읽음
    dict(state=HealthState.UNHEALTHY, reason=FailureReason.DOMAIN_ABSENT),  # 다른 사유
    dict(state=HealthState.HEALTHY),
    dict(state=HealthState.UNHEALTHY, reason=APP, domid=9),  # 다른 실행의 증거
    dict(state=HealthState.UNHEALTHY, reason=APP, execution_id="other-execution"),
])
def test_zephyr_force_is_held_without_current_evidence_for_the_current_execution(tmp_path, health):
    w = make(tmp_path)
    if health is not None:
        w.watcher.set(VM, health.pop("state"), **health)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    if health is not None and w.record().status == RecoveryStatus.RECOVERED:
        assert w.platform.recover_calls() == []  # HEALTHY 는 기존 규칙대로 시도 전에 회복으로 닫힌다(재시작 0)
        return
    assert held(w)


def test_held_force_starts_normally_once_the_evidence_appears(tmp_path):
    w = make(tmp_path, non_drive_vm_ids=[VM])
    w.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w)
    w.advance(5)
    w.tick()
    assert held(w)  # 아직 근거 없음: 다시 봐도 시도 0
    w.watcher.failing(VM, APP, execution_id=EXEC)
    w.advance(5)
    w.tick()
    (_, spec, _), = w.platform.recover_calls()
    assert spec["mode"] == "force-restart" and w.record().attempt == 1
    assert [e["type"] for e in w.events] == ["recovery.started", "recovery.completed"]


def test_crash_force_needs_xen_evidence_on_the_same_execution_not_the_app_channel(tmp_path):
    w = make(tmp_path, non_drive_vm_ids=[VM])
    w.watcher.failing(VM, FailureReason.DOMAIN_CRASHED)
    w.platform.views[VM] = crashed_view()
    w.engine.on_judgment(judgment(reason=FailureReason.DOMAIN_CRASHED, kind="zephyr"))
    w.tick()
    assert w.platform.recover_calls()[0][1]["mode"] == "force-restart"


def test_drive_vm_force_needs_current_gateway_protection(tmp_path):
    w = make(tmp_path, drive_vm_ids=[VM])
    w.watcher.failing(VM, APP, execution_id=EXEC)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w)  # Gateway 보호 제공자가 없다. 보호를 받은 뒤의 force 는 test_recovery_protection.py


def test_zephyr_missing_from_the_drive_config_is_held_not_forced_without_protection(tmp_path):
    w = make(tmp_path, drive_vm_ids=[VM2])  # 설정에 A(VM2)만 있고 B(VM)도 등록돼 있다
    w.watcher.failing(VM, APP)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    w.advance(5)
    w.tick()
    assert held(w) and w.record().attempt == 0 and w.platform.recover_calls() == []
    assert w.events == []


def test_non_drive_vm_and_graceful_or_absent_paths_are_not_gated(tmp_path):
    w = make(tmp_path, drive_vm_ids=[VM2], non_drive_vm_ids=[VM])  # 비주행으로 선언된 Zephyr
    w.watcher.failing(VM, APP)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert w.platform.recover_calls()[0][1]["mode"] == "force-restart"

    linux = make(tmp_path / "linux", drive_vm_ids=[VM]) if (tmp_path / "linux").mkdir() is None else None
    linux.engine.on_judgment(judgment())  # 건강 스냅샷 없음: graceful 은 게이트 대상이 아니다
    linux.tick()
    assert linux.platform.recover_calls()[0][1]["mode"] == "graceful-restart"

    absent = make(tmp_path / "absent", drive_vm_ids=[VM]) if (tmp_path / "absent").mkdir() is None else None
    absent.platform.views[VM] = view(present=False)
    absent.engine.on_judgment(zephyr_judgment())
    absent.tick()
    assert absent.platform.recover_calls()[0][1]["mode"] == "start-if-absent"


def test_held_force_is_still_cancelled_by_a_user_stop(tmp_path):
    w = make(tmp_path)
    w.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w)
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.advance(5)
    w.tick()
    assert w.record().status == RecoveryStatus.CANCELLED and w.platform.recover_calls() == []



def test_unknown_drive_classification_holds_every_zephyr_force(tmp_path):
    """drive config 가 없어 주행 VM 이 어느 것인지 모르면 Zephyr force 는 모두 보류한다(fail-closed)."""
    w = make(tmp_path, drive_vm_ids=None, protection=protector(tmp_path))
    w.watcher.failing(VM, APP, execution_id=EXEC)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert held(w)
    linux = make(tmp_path / "linux", drive_vm_ids=None) if (tmp_path / "linux").mkdir() is None else None
    linux.watcher.failing(VM, FailureReason.DOMAIN_CRASHED)
    linux.platform.views[VM] = crashed_view()
    linux.engine.on_judgment(judgment(reason=FailureReason.DOMAIN_CRASHED))
    linux.tick()
    assert linux.platform.recover_calls()[0][1]["mode"] == "force-restart"  # Linux 는 주행 VM 이 아니다


@pytest.mark.parametrize("first, kind", [(APP, "zephyr"), (FailureReason.HEARTBEAT_LOST, "linux")])
def test_crash_after_another_failure_is_stronger_evidence_for_force(tmp_path, first, kind):
    """최초 사유가 생존 신호 손실이어도 같은 실행이 지금 crashed 면 그 증거로 force 한다(사유가 바뀌어도 영구 보류하지 않음)."""
    w = make(tmp_path, non_drive_vm_ids=[VM])
    w.watcher.failing(VM, FailureReason.DOMAIN_CRASHED)
    w.platform.views[VM] = crashed_view()
    w.engine.on_judgment(judgment(reason=first, kind=kind))
    w.tick()
    assert w.platform.recover_calls()[0][1]["mode"] == "force-restart"


def test_crash_evidence_is_held_when_the_platform_no_longer_sees_a_crash(tmp_path):
    w = make(tmp_path)
    w.watcher.failing(VM, FailureReason.DOMAIN_CRASHED)  # 감시기는 crashed 를 봤지만 플랫폼 재조회는 running
    w.engine.on_judgment(judgment(reason=FailureReason.DOMAIN_CRASHED, kind="zephyr"))
    w.tick()
    assert held(w)


def test_drive_vm_evidence_must_name_the_platform_execution(tmp_path):
    for execution_id in (None, "other-execution"):
        root = tmp_path / str(execution_id)
        root.mkdir()
        w = make(root, drive_vm_ids=[VM], protection=protector(root))
        w.watcher.failing(VM, APP, execution_id=execution_id)
        w.engine.on_judgment(zephyr_judgment())
        w.tick()
        assert held(w), execution_id


def test_protection_is_read_outside_the_engine_lock_and_rechecked(tmp_path):
    w = make(tmp_path, drive_vm_ids=[VM], inline=False)  # 실제 Worker 처럼 실행 스레드에서 시도한다
    w.watcher.failing(VM, APP, execution_id=EXEC)
    record = w.engine.on_judgment(zephyr_judgment())
    lock_free = threading.Event()
    gateway = bound_gateway(w)
    submit = gateway.submit

    def stop_meanwhile(request_id, spec):
        if not w.engine._lock._is_owned():  # noqa: SLF001 - 이 스레드는 Gateway I/O 동안 엔진 잠금을 쥐지 않는다
            lock_free.set()
        result = submit(request_id, spec)
        with w.engine._lock:  # noqa: SLF001 - 보호를 받는 사이 사용자 stop 이 엔진 잠금 안에서 기록됐다
            record.cancel_requested = True
        return result

    gateway.submit = stop_meanwhile
    w.tick()
    wait_until(lambda: all(f.done() for f in w.engine._futures.values()))  # noqa: SLF001
    w.engine.collect(w.clock[0])
    assert lock_free.is_set()
    assert record.status == RecoveryStatus.CANCELLED and w.platform.recover_calls() == []
    w.engine.shutdown()

    changed = make(tmp_path / "changed", drive_vm_ids=[VM]) if (tmp_path / "changed").mkdir() is None else None
    changed.watcher.failing(VM, APP, execution_id=EXEC)
    changed.engine.on_judgment(zephyr_judgment())
    gateway = bound_gateway(changed)
    submit = gateway.submit

    def evidence_changes(request_id, spec):
        changed.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE)  # 보호를 받는 사이 채널 증거가 사라졌다
        return submit(request_id, spec)

    gateway.submit = evidence_changes
    changed.tick()
    assert held(changed)


def test_held_reason_is_forgotten_when_the_recovery_closes(tmp_path):
    w = make(tmp_path)
    w.watcher.set(VM, HealthState.OBSERVATION_UNAVAILABLE)
    w.engine.on_judgment(zephyr_judgment())
    w.tick()
    assert w.engine._held  # noqa: SLF001
    w.watcher.set(VM, HealthState.STOPPED, desired="stopped")
    w.advance(5)
    w.tick()
    assert w.record().status == RecoveryStatus.CANCELLED and w.engine._held == {}  # noqa: SLF001



def test_old_crash_does_not_force_a_new_execution_with_a_heartbeat_loss(tmp_path):
    """E1 이 crashed 로 열린 복구 → 밖에서 E2 가 시작됨 → E2 heartbeat 손실: 지금 증거대로 E2 에 graceful 이다(영구 보류도 force 도 아님)."""
    w = make(tmp_path)
    w.engine.on_judgment(judgment(reason=FailureReason.DOMAIN_CRASHED))
    w.watcher.failing(VM, FailureReason.HEARTBEAT_LOST, domid=5)
    w.tick()  # 가짜 view 는 running E(EXEC) — E2
    (_, spec, _), = w.platform.recover_calls()
    assert spec["mode"] == "graceful-restart" and spec["expected_execution"] == {"execution_id": EXEC}


@pytest.mark.parametrize("still_same_execution", [True, False])
def test_graceful_failure_escalates_only_on_the_execution_that_failed(tmp_path, still_same_execution):
    w = make(tmp_path, policy=RecoveryPolicy(escalate_to_force=True))
    w.platform.results = [DriverTimeout("graceful shutdown timed out", platform_code="XEN_EXECUTION_FAILED")]
    w.engine.on_judgment(judgment())
    w.tick()
    assert w.record().graceful_failed_execution_id == EXEC
    if not still_same_execution:
        w.platform.views[VM] = {**view(), "execution_id": "exec-2", "domid": 6}  # 그 사이 다른 경로로 새 실행
    w.watcher.failing(VM, FailureReason.HEARTBEAT_LOST, domid=w.platform.views.get(VM, view())["domid"])
    w.advance(5)
    w.tick()
    mode = w.platform.recover_calls()[1][1]["mode"]
    assert mode == ("force-restart" if still_same_execution else "graceful-restart")
