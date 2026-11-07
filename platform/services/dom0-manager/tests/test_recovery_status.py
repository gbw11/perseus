"""Worker 자가진단 상태 (S15P31E110-354). 상태 계산·전환 로그·원자적 기록·읽는 쪽의 STALE/UNAVAILABLE 판정."""

import json
import logging
import errno
import threading
import time
from datetime import datetime, timezone
from uuid import UUID
from pathlib import Path

import pytest
from dom0_manager.recovery import status as status_module

from dom0_manager.errors import PlatformUnavailable
from dom0_manager.models import VmState
from dom0_manager.recovery.config import RecoverySettings
from dom0_manager.recovery.heartbeats import AppHeartbeatTable, HeartbeatTable
from dom0_manager.recovery.model import VmObservation
from dom0_manager.recovery.mqtt import LinkState
from dom0_manager.recovery.protection import GatewayRefused, GatewayUncertain
from dom0_manager.recovery.status import (
    DEGRADED,
    DELIVERING,
    DISABLED,
    NOT_GRANTED,
    OPERABLE,
    SILENT,
    STALE,
    STATUS_FILE,
    UNAVAILABLE,
    WAITING,
    GatewayProbe,
    StatusReporter,
    read_status,
)
from dom0_manager.recovery.state import RecoveryStore
from dom0_manager.recovery.watcher import Watcher
from dom0_manager.recovery.worker import RecoveryWorker
from test_recovery_engine import judgment, make

LINUX = UUID("4be41d5f-b8ef-408b-9709-7fe5ac11231f")
ZEPHYR = UUID("0d950ea2-cc99-49df-9b60-a4ddf2c8e137")
SETTINGS = RecoverySettings(interval_s=5, heartbeat_lost_s=30, app_heartbeat_lost_s=15, boot_grace_s=90)
WALL = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)
SECRET = "s3cret-broker-password"
BOOT_A = '11111111-1111-4111-8111-111111111111'
BOOT_B = '22222222-2222-4222-8222-222222222222'


@pytest.fixture(autouse=True)
def host_boot(monkeypatch):
    monkeypatch.setattr(status_module, '_read_host_boot_id', lambda: BOOT_A)


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        time.sleep(.005)
    assert predicate()


def _obs(vm_id=LINUX, kind="linux", *, desired="running", present=True, expected=None):
    return VmObservation(vm_id, "vm", kind, desired, True, 2, "REGISTERED", present, VmState.running if present else None,
                         1 if present else None, app_heartbeat_expected=expected)


class Env:
    """StatusReporter 의 동적 입력을 테스트가 바꾼다."""

    def __init__(self, tmp_path, **overrides):
        self.clock = [1000.0]
        self.connected = True
        self.links = {"agent": DELIVERING, "app": DELIVERING}
        self.publishers = {"agent": False, "app": False}
        self.intent_known = True
        self.wall = [WALL]
        options = dict(policy="restart", broker_configured=True, drive_vm_ids=None, gateway_client=None, gateway=None)
        options.update(overrides)
        self.reporter = StatusReporter(
            tmp_path, interval_s=5, connected=lambda: self.connected, links=lambda now: dict(self.links),
            publishers=lambda: dict(self.publishers), intent_known=lambda: self.intent_known, clock=lambda: self.clock[0], wall=lambda: self.wall[0], **options,
        )

    def report(self, observations=()):
        return self.reporter.report(None if observations is None else list(observations))


class FakeGateway:
    def __init__(self):
        self.calls = 0
        self.error = None

    def context(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"host_boot_id": "h"}


# ---- 상태 계산 ----


def test_all_paths_available_is_operable_and_the_file_carries_every_field_without_secrets(tmp_path):
    env = Env(tmp_path)
    status = env.report([_obs()])
    assert status["overall"] == OPERABLE and status["hold_reasons"] == []
    written = json.loads((tmp_path / STATUS_FILE).read_text(encoding="utf-8"))
    assert written == status
    assert written['version'] == 2 and written['host_boot_id'] == BOOT_A
    for field in ("overall", "policy", "broker_configured", "broker_connected", "agent_subscription", "app_subscription",
                  "drive_configured", "gateway_available", "last_successful_tick", "hold_reasons"):
        assert field in written, field
    assert written["last_successful_tick"] == WALL.isoformat() and written["stale_after_s"] == 15
    assert SECRET not in (tmp_path / STATUS_FILE).read_text(encoding="utf-8")
    assert not [p for p in tmp_path.iterdir() if p.name != STATUS_FILE]  # 임시 파일이 남지 않는다


def test_missing_credentials_and_disconnect_are_degraded_without_repeating_the_same_cause(tmp_path):
    env = Env(tmp_path, broker_configured=False)
    env.connected = False
    env.links = {"agent": NOT_GRANTED, "app": NOT_GRANTED}
    status = env.report([_obs()])
    assert status["overall"] == DEGRADED
    assert status["hold_reasons"] == ["BROKER_CREDENTIALS_MISSING", "BROKER_DISCONNECTED"]  # 구독 미승인은 연결 없음의 결과다
    assert status["broker_configured"] is False and status["broker_connected"] is False


@pytest.mark.parametrize("channel, code", [("agent", "AGENT_SUBSCRIPTION_NOT_GRANTED"), ("app", "APP_SUBSCRIPTION_NOT_GRANTED")])
def test_connected_but_subscription_refused_is_degraded(tmp_path, channel, code):
    env = Env(tmp_path)
    env.links[channel] = NOT_GRANTED
    assert env.report([_obs()])["hold_reasons"] == [code]


@pytest.mark.parametrize("channel, code", [("agent", "AGENT_DELIVERY_UNPROVEN"), ("app", "APP_DELIVERY_UNPROVEN")])
def test_silent_channel_counts_only_when_a_vm_should_be_publishing(tmp_path, channel, code):
    env = Env(tmp_path)
    env.links[channel] = SILENT
    assert env.report([_obs()])["overall"] == OPERABLE  # 보낼 VM 이 없으면 조용한 것이 정상이다
    env.publishers[channel] = True
    assert env.report([_obs()])["hold_reasons"] == [code]  # ACL 이 읽기를 거르면 SUBACK 은 성공이다
    env.links[channel] = WAITING
    assert env.report([_obs()])["overall"] == OPERABLE  # 승인 뒤 한 창 전에는 판단하지 않는다


def test_failed_observation_and_unknown_intents_hold_and_keep_the_last_success_time(tmp_path):
    env = Env(tmp_path)
    first = env.report([_obs()])["last_successful_tick"]
    env.wall[0] = datetime(2026, 11, 3, 6, 1, tzinfo=timezone.utc)
    env.links["agent"] = SILENT
    env.publishers["agent"] = True
    status = env.report(None)  # 이번 주기 관측 실패: 경로 점검은 계속한다
    assert status["hold_reasons"] == ["AGENT_DELIVERY_UNPROVEN", "OBSERVATION_FAILED"] and status["intents_known"] is False
    env.links["agent"] = DELIVERING
    env.intent_known = False
    status = env.report([_obs()])
    assert status["hold_reasons"] == ["INTENTS_UNAVAILABLE"] and status["last_successful_tick"] == first
    assert status["updated_at"] != first
    env.intent_known = True
    assert env.report([_obs()])["last_successful_tick"] == env.wall[0].isoformat()


def test_drive_configuration_holds_are_reported_per_zephyr_registration(tmp_path):
    zephyr = _obs(ZEPHYR, "zephyr", expected=True)
    assert Env(tmp_path).report([_obs()])["overall"] == OPERABLE  # Zephyr 가 없으면 drive 설정은 필요 없다
    assert Env(tmp_path).report([zephyr])["hold_reasons"] == ["DRIVE_CONFIG_MISSING"]
    other = Env(tmp_path, drive_vm_ids=["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"], gateway_client=True)
    assert other.report([zephyr])["hold_reasons"] == ["DRIVE_VM_NOT_CONFIGURED"]  # 예시 설정의 자리표시 VM
    assert Env(tmp_path, drive_vm_ids=[str(ZEPHYR)], gateway_client=False).report([zephyr])["hold_reasons"] == ["GATEWAY_CLIENT_MISSING"]


def test_gateway_probe_failure_is_degraded_and_recovers(tmp_path):
    gateway = FakeGateway()
    clock = [0.0]
    probe = GatewayProbe(gateway, probe_s=30, clock=lambda: clock[0])
    env = Env(tmp_path, drive_vm_ids=[str(ZEPHYR)], gateway_client=True, gateway=probe)
    zephyr = _obs(ZEPHYR, "zephyr", expected=True)
    assert env.report([zephyr])["gateway_available"] is True
    gateway.error = GatewayUncertain("UNAVAILABLE")
    clock[0] = 31
    status = env.report([zephyr])
    assert status["hold_reasons"] == ["GATEWAY_UNAVAILABLE"] and status["gateway_available"] is False and probe.code == "UNAVAILABLE"
    gateway.error = None
    clock[0] = 62
    assert env.report([zephyr])["overall"] == OPERABLE


def test_gateway_probe_runs_once_per_period_and_treats_refusal_and_errors_as_unavailable():
    gateway, clock = FakeGateway(), [0.0]
    probe = GatewayProbe(gateway, probe_s=30, clock=lambda: clock[0])
    assert probe.check() is True and probe.check() is True and gateway.calls == 1
    gateway.error = GatewayRefused("NOT_AUTHORIZED")
    clock[0] = 30
    assert probe.check() is False and probe.code == "NOT_AUTHORIZED" and gateway.calls == 2
    gateway.error = OSError("socket gone")
    clock[0] = 60
    assert probe.check() is False and probe.code == "OSError"


def test_policy_none_is_disabled_even_with_holds(tmp_path):
    env = Env(tmp_path, policy="none", broker_configured=False)
    status = env.report([_obs()])
    assert status["overall"] == DISABLED and status["hold_reasons"] == ["BROKER_CREDENTIALS_MISSING"]
    assert read_status(tmp_path, now=1000.0)["overall"] == DISABLED


def test_state_changes_are_logged_once(tmp_path, caplog):
    env = Env(tmp_path, broker_configured=False)
    caplog.set_level(logging.INFO, logger="dom0_manager.recovery.status")
    for _ in range(3):
        env.report([_obs()])
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "BROKER_CREDENTIALS_MISSING" in errors[0].getMessage()
    env.reporter._broker_configured = True
    env.report([_obs()])
    env.report([_obs()])
    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.INFO]


def test_internal_engine_and_store_holds_are_degraded(tmp_path):
    internal = ["RECOVERY_HISTORY_LOST"]
    env = Env(tmp_path, internal=lambda: list(internal))
    assert env.report([_obs()])["hold_reasons"] == ["RECOVERY_HISTORY_LOST"]  # 이력 손실이면 복구를 하나도 실행하지 않는다
    internal.clear()
    assert env.report([_obs()])["overall"] == OPERABLE


def test_engine_reports_lost_history_unsaved_state_and_pending_events(tmp_path):
    w = make(tmp_path)
    assert w.engine.hold_reasons() == []
    w.engine.on_judgment(judgment())
    w.sink_ok = False  # outbox 에 쓸 수 없다. 상태는 저장되고 started·completed 는 레코드에 남는다
    w.tick()
    assert w.engine.hold_reasons() == ["RECOVERY_EVENTS_PENDING"]
    w.sink_ok = True
    w.advance(5)
    w.tick()
    assert w.engine.hold_reasons() == []
    w.store.save = lambda records: False  # 상태 저장 실패
    w.engine.on_judgment(judgment(recovery_id=UUID(int=2)))
    assert w.engine.hold_reasons() == ["RECOVERY_STATE_UNSAVED"]

    corrupt = tmp_path / "lost" / "recoveries.json"
    corrupt.parent.mkdir()
    corrupt.write_text("{", encoding="utf-8")
    lost = make(tmp_path / "lost", store=RecoveryStore(corrupt))
    assert lost.engine.hold_reasons() == ["RECOVERY_HISTORY_LOST"]


def test_every_reason_the_worker_can_write_is_accepted_by_the_reader(tmp_path):
    from dom0_manager.recovery.status import HOLD_REASONS
    env = Env(tmp_path, broker_configured=False, drive_vm_ids=["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"], gateway_client=False,
              internal=lambda: ["RECOVERY_HISTORY_LOST", "RECOVERY_STATE_UNSAVED", "RECOVERY_EVENTS_PENDING", "ARMED_HISTORY_LOST"])
    env.connected = False
    status = env.report(None)
    assert set(status["hold_reasons"]) <= set(HOLD_REASONS) and read_status(tmp_path, now=1000.0)["overall"] == DEGRADED
    env.connected, env.links, env.publishers = True, {"agent": SILENT, "app": NOT_GRANTED}, {"agent": True, "app": True}
    env.intent_known = False
    env.report([_obs(ZEPHYR, "zephyr", expected=True)])
    assert read_status(tmp_path, now=1000.0)["overall"] == DEGRADED
    lost = make(tmp_path / "engine", store=RecoveryStore(tmp_path / "engine.json"))
    assert set(lost.engine.hold_reasons()) <= set(HOLD_REASONS)


# ---- 기록 ----


def test_write_failure_is_logged_once_and_does_not_stop_reporting(tmp_path, caplog):
    env = Env(tmp_path / "missing")
    caplog.set_level(logging.INFO, logger="dom0_manager.recovery.status")
    assert env.report([_obs()])["overall"] == OPERABLE
    env.report([_obs()])
    assert sum("could not be written" in r.getMessage() for r in caplog.records) == 1
    (tmp_path / "missing").mkdir()
    env.report([_obs()])
    assert any("written again" in r.getMessage() for r in caplog.records)
    assert read_status(tmp_path / "missing", now=1000.0)["overall"] == OPERABLE


# ---- 읽는 쪽 ----


def test_reader_returns_fresh_status_and_stale_after_the_window(tmp_path):
    env = Env(tmp_path)
    env.report([_obs()])
    fresh = read_status(tmp_path, now=1010.0)
    assert fresh["overall"] == OPERABLE and fresh["age_s"] == 10.0
    stale = read_status(tmp_path, now=1016.0)  # Worker 가 죽어도 마지막 OPERABLE 을 내세우지 않는다
    assert stale["overall"] == STALE and stale["last"]["overall"] == OPERABLE and stale["age_s"] == 16.0
    assert read_status(tmp_path, now=10.0)["overall"] == STALE  # 같은 host라도 미래 표본은 신선하지 않다


def test_boot_change_never_compares_old_uptime_and_new_writer_recovers_without_migration(tmp_path):
    env = Env(tmp_path)
    original = env.report([_obs()])
    changed = read_status(tmp_path, now=1001, boot_id=lambda: BOOT_B)
    assert changed['overall'] == STALE and changed['reason'] == 'host boot changed'
    assert changed['last'] == original and 'age_s' not in changed
    restarted = Env(tmp_path, boot_id=lambda: BOOT_B)
    restarted.clock[0] = 1001
    restarted.report([_obs()])
    assert read_status(tmp_path, now=1001, boot_id=lambda: BOOT_B)['overall'] == OPERABLE
    legacy = {k: v for k, v in original.items() if k != 'host_boot_id'}
    legacy['version'] = 1
    (tmp_path/STATUS_FILE).write_text(json.dumps(legacy))
    assert read_status(tmp_path, now=1001)['overall'] == UNAVAILABLE
    restarted.report([_obs()])
    assert read_status(tmp_path, now=1001, boot_id=lambda: BOOT_B)['host_boot_id'] == BOOT_B


def test_boot_read_failure_is_unavailable_and_preserves_first_cause_until_recovery(tmp_path):
    failure = OSError(errno.EIO, 'fixture boot read failure')
    def unavailable():
        raise failure
    env = Env(tmp_path, boot_id=unavailable)
    assert env.report([_obs()])['overall'] == UNAVAILABLE
    assert not (tmp_path/STATUS_FILE).exists() and env.reporter.first_error is failure
    env.reporter._boot_id = lambda: BOOT_A
    env.report([_obs()])
    assert read_status(tmp_path, now=1000)['overall'] == OPERABLE
    assert read_status(tmp_path, now=1000, boot_id=unavailable)['overall'] == UNAVAILABLE
    assert env.reporter.first_error is failure


@pytest.mark.parametrize('sink', ['file', 'gateway', 'log'])
def test_slow_optional_sink_keeps_watcher_progress_bounded_copies_and_original_lease(tmp_path, monkeypatch, caplog, sink):
    entered, release = threading.Event(), threading.Event()
    def gate():
        if not entered.is_set():
            entered.set()
            assert release.wait(3), 'test must release optional sink'
    gateway = FakeGateway()
    options = {}
    if sink == 'gateway':
        gateway.context = gate
        options = dict(gateway=GatewayProbe(gateway), drive_vm_ids=[str(ZEPHYR)], gateway_client=True)
    env = Env(tmp_path, **options)
    owner = threading.get_ident()
    def owned(callback):
        def call(*args):
            assert threading.get_ident() == owner, 'Watcher/Engine metadata must stay on its owner'
            return callback(*args)
        return call
    for name in ('_connected', '_links', '_publishers', '_intent_known', '_internal'):
        setattr(env.reporter, name, owned(getattr(env.reporter, name)))
    env.connected = False
    watcher = Watcher(SETTINGS, HeartbeatTable(), clock=lambda: env.clock[0])
    worker = RecoveryWorker(SETTINGS, lambda: [_obs()], watcher, link=LinkState.down,
                            clock=lambda: env.clock[0], status=env.reporter)
    original = status_module.os.replace
    if sink == 'file':
        def replace(source, target):
            if Path(target) == env.reporter.path:
                gate()
            return original(source, target)
        monkeypatch.setattr(status_module.os, 'replace', replace)
    class SlowHandler(logging.Handler):
        def emit(self, record):
            gate()
    handler = SlowHandler()
    if sink == 'log':
        status_module.log.addHandler(handler)
        caplog.set_level(logging.INFO, logger=status_module.__name__)
    try:
        worker.run_once()
        assert entered.wait(1)
        env.connected = True
        for _ in range(24):
            env.clock[0] += 1
            worker.run_once()
        env.links['app'] = NOT_GRANTED
        env.clock[0] += 1
        worker.run_once()
        # 제출 후 원 객체를 바꿔도 대기 표본의 내용은 바뀌지 않는다.
        env.links['app'] = DELIVERING
        state = env.reporter.sink_snapshot()
        assert worker.ticks == 26 and state['submitted'] == 26 and state['coalesced'] == 24
        assert state['pending'] == 1 and state['in_flight'] and state['worker_alive']
        env.clock[0] += 20
        release.set()
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 2)
        stale = read_status(tmp_path, now=env.clock[0])
        assert stale['overall'] == STALE and stale['last']['updated_monotonic'] == 1025
        assert stale['last']['hold_reasons'] == ['APP_SUBSCRIPTION_NOT_GRANTED']
        worker.run_once()
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 3)
        assert read_status(tmp_path, now=env.clock[0])['overall'] == OPERABLE
    finally:
        release.set()
        worker.shutdown()
        status_module.log.removeHandler(handler)
    assert not env.reporter.sink_snapshot()['worker_alive']


@pytest.mark.parametrize('internal', [False, True])
def test_async_io_or_internal_failure_preserves_cause_and_normal_recovery(tmp_path, monkeypatch, caplog, internal):
    env = Env(tmp_path)
    env.report([_obs()])
    error = TypeError('fixture internal defect') if internal else OSError(errno.ENOSPC, 'fixture full')
    cause = ValueError('fixture original cause')
    error.__cause__ = cause
    original = status_module.os.replace
    remaining = [2]
    def replace(source, target):
        if Path(target) == env.reporter.path and remaining[0]:
            remaining[0] -= 1
            raise error
        return original(source, target)
    monkeypatch.setattr(status_module.os, 'replace', replace)
    try:
        for failure in (1, 2):
            env.clock[0] += 20
            env.reporter.submit([_obs()])
            wait_for(lambda: env.reporter.sink_snapshot()['failures'] == failure)
            assert read_status(tmp_path, now=env.clock[0])['overall'] == STALE
        env.reporter.submit([_obs()])
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 2)
        assert read_status(tmp_path, now=env.clock[0])['overall'] == OPERABLE
        assert env.reporter.first_error is error and env.reporter.first_error.__cause__ is cause
        marker = 'publication failed' if internal else 'could not be written'
        assert sum(marker in record.getMessage() for record in caplog.records) == 1
    finally:
        assert env.reporter.close()


def test_close_reports_live_writer_without_blocking_or_claiming_drain(tmp_path, monkeypatch):
    env = Env(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = env.reporter._write
    def write(status):
        entered.set()
        assert release.wait(3)
        return original(status)
    monkeypatch.setattr(env.reporter, '_write', write)
    env.reporter.submit([_obs()])
    try:
        assert entered.wait(1)
        assert env.reporter.close() is False
        assert env.reporter.sink_snapshot()['worker_alive']
        assert env.reporter.submit([_obs()]) is False
    finally:
        release.set()
        wait_for(lambda: not env.reporter.sink_snapshot()['worker_alive'])
        assert env.reporter.close()


def test_expected_io_failure_does_not_suppress_distinct_internal_causes(tmp_path, monkeypatch, caplog):
    env = Env(tmp_path)
    errors = [OSError(errno.ENOSPC, 'fixture full'), TypeError('fixture defect'), KeyError('fixture invariant')]
    errors[1].__cause__ = ValueError('fixture original internal cause')
    remaining = list(errors)
    original = status_module.os.replace
    def replace(source, target):
        if Path(target) == env.reporter.path and remaining:
            raise remaining.pop(0)
        return original(source, target)
    monkeypatch.setattr(status_module.os, 'replace', replace)
    try:
        for count in range(1, 4):
            env.reporter.submit([_obs()])
            wait_for(lambda: env.reporter.sink_snapshot()['failures'] == count)
        wait_for(lambda: not env.reporter.sink_snapshot()['in_flight'])
        recorded = [r.exc_info[1] for r in caplog.records if 'publication failed' in r.getMessage()]
        assert recorded == errors[1:]  # 이미 기록한 I/O 실패로 TypeError·KeyError를 숨기지 않는다.
        assert env.reporter.first_error is errors[0]
        assert env.reporter.first_internal_error is errors[1] and env.reporter.last_error is errors[2]
        env.reporter.submit([_obs()])
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 1)
        assert read_status(tmp_path, now=1000)['overall'] == OPERABLE
        remaining.append(errors[1])
        env.reporter.submit([_obs()])
        wait_for(lambda: env.reporter.sink_snapshot()['failures'] == 4 and not env.reporter.sink_snapshot()['in_flight'])
        recorded = [r.exc_info[1] for r in caplog.records if 'publication failed' in r.getMessage()]
        assert recorded == [errors[1], errors[2], errors[1]]  # 정상 회복 뒤 새 발생은 다시 진단한다.
        env.reporter.submit([_obs()])
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 2)
        assert read_status(tmp_path, now=1000)['overall'] == OPERABLE
    finally:
        assert env.reporter.close()


def test_optional_sink_failure_cannot_hide_required_recovery_store_failure(tmp_path, monkeypatch):
    w = make(tmp_path)
    normal_save = w.store.save
    w.store.save = lambda records: False
    w.engine.on_judgment(judgment())
    env = Env(tmp_path, internal=w.engine.hold_reasons)
    original = status_module.os.replace
    def full(source, target):
        if Path(target) == env.reporter.path:
            raise OSError(errno.ENOSPC, 'fixture diagnostic sink full')
        return original(source, target)
    monkeypatch.setattr(status_module.os, 'replace', full)
    try:
        env.reporter.submit([_obs()])
        wait_for(lambda: env.reporter.sink_snapshot()['failures'] == 1)
        w.tick()
        assert w.engine.hold_reasons() == ['RECOVERY_STATE_UNSAVED']
        assert w.platform.recover_calls() == []
        w.store.save = normal_save
        monkeypatch.setattr(status_module.os, 'replace', original)
        w.tick()
        assert w.platform.recover_calls() == []  # 이미 정한 transient backoff를 생략하지 않는다.
        w.advance(5)
        w.tick()
        assert len(w.platform.recover_calls()) == 1 and not w.engine.hold_reasons()
        env.reporter.submit([_obs()])
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 1)
        assert read_status(tmp_path, now=1000)['overall'] == OPERABLE
    finally:
        assert env.reporter.close()
        w.engine.shutdown()


def test_reader_reports_missing_unreadable_and_malformed_files_as_unavailable(tmp_path):
    assert read_status(tmp_path, now=0)["overall"] == UNAVAILABLE
    path = tmp_path / STATUS_FILE
    path.write_text("{", encoding="utf-8")
    assert "not valid JSON" in read_status(tmp_path, now=0)["reason"]
    env = Env(tmp_path)
    good = env.report([_obs()])
    for damage in ({"overall": "FINE"}, {"version": 3}, {"host_boot_id": None}, {"host_boot_id": True},
                   {"host_boot_id": 'not-a-uuid'}, {"host_boot_id": str(UUID(int=0))},
                   {"updated_monotonic": "soon"}, {"updated_monotonic": -1}, {"stale_after_s": 0},
                   {"hold_reasons": "none"}, {"hold_reasons": ["X"]}, {"overall": DEGRADED}, {"policy": "failover"},
                   {"broker_connected": "yes"}, {"broker_configured": 1}, {"drive_configured": None}, {"intents_known": "true"},
                   {"agent_subscription": "BOGUS"}, {"app_subscription": None}, {"gateway_available": "up"}, {"gateway_available": True},
                   {"last_successful_tick": "yesterday"}, {"last_successful_tick": "2026-11-03T06:00:00"}, {"updated_at": None},
                   {"pid": "1"}, {"pid": 0}, {"pid": True}, {"overall": DISABLED},
                   {"hold_reasons": ["DRIVE_CONFIG_MISSING", "DRIVE_CONFIG_MISSING"], "overall": DEGRADED},
                   {"hold_reasons": ["BROKER_CREDENTIALS_MISSING"], "overall": DEGRADED},
                   {"hold_reasons": ["BROKER_DISCONNECTED"], "overall": DEGRADED},
                   {"intents_known": False}, {"drive_configured": True, "gateway_available": "up"},
                   {"hold_reasons": ["SOMETHING_NEW"], "overall": DEGRADED}, {"version": True},
                   {"drive_configured": True, "gateway_available": 1},
                   {"hold_reasons": ["INTENTS_UNAVAILABLE"], "overall": DEGRADED},
                   {"hold_reasons": ["OBSERVATION_FAILED"], "overall": DEGRADED}):
        path.write_text(json.dumps({**good, **damage}), encoding="utf-8")
        result = read_status(tmp_path, now=1000.0)
        assert result["overall"] == UNAVAILABLE and "malformed" in result["reason"], damage
    for name in ("gateway_available", "last_successful_tick", "pid"):  # null 을 허용하는 필드도 빠지면 손상이다
        path.write_text(json.dumps({k: v for k, v in good.items() if k != name}), encoding="utf-8")
        result = read_status(tmp_path, now=1000.0)
        assert result["overall"] == UNAVAILABLE and name in result["reason"], name
    path.write_text("[]", encoding="utf-8")
    assert read_status(tmp_path, now=0)["overall"] == UNAVAILABLE


# ---- 감시기·Worker 연결 ----


def test_watcher_link_report_needs_recent_delivery_not_one_message_ever():
    clock = [1000.0]
    beats = HeartbeatTable()
    watcher = Watcher(SETTINGS, beats, AppHeartbeatTable(), clock=lambda: clock[0])
    beats.record(LINUX, "boot-1", 1, clock[0] - 1)  # 승인 전(이전 연결)의 수신은 지금 경로의 증거가 아니다
    watcher.tick([], link=LinkState.up())
    assert watcher.link_report()["agent"] == WAITING
    beats.record(LINUX, "boot-1", 1, clock[0] + 1)
    clock[0] += 5
    assert watcher.link_report()["agent"] == DELIVERING
    clock[0] += 30  # ACL 을 다시 읽어 전달만 멈췄다: 연결·구독은 그대로다
    assert watcher.link_report()["agent"] == SILENT
    beats.record(LINUX, "boot-1", 2, clock[0])
    assert watcher.link_report()["agent"] == DELIVERING


def test_watcher_expected_publishers_skip_stopped_booting_and_undeclared_unarmed_vms():
    clock = [1000.0]
    beats, apps = HeartbeatTable(), AppHeartbeatTable()
    watcher = Watcher(SETTINGS, beats, apps, clock=lambda: clock[0])
    linux, starter, undeclared, firmware = (_obs(), _obs(UUID(int=2), "zephyr", expected=False),
                                            _obs(UUID(int=3), "zephyr", expected=None), _obs(UUID(int=4), "zephyr", expected=True))
    watcher.tick([linux, starter, undeclared], link=LinkState.up())
    assert watcher.expected_publishers() == {"agent": False, "app": False}  # 부팅 유예 중
    clock[0] += 120
    watcher.tick([linux, starter, undeclared], link=LinkState.up())
    assert watcher.expected_publishers() == {"agent": True, "app": False}  # starter·선언 없고 armed 아닌 Zephyr 는 보내지 않는다
    watcher._armed[undeclared.vm_id] = "autonomous"  # 신호를 받은 적이 있는 선언 없는 주행 VM(실보드 주행앱 템플릿)
    assert watcher.expected_publishers()["app"] is True
    watcher.tick([_obs(desired="stopped"), firmware], link=LinkState.up())
    assert watcher.expected_publishers() == {"agent": False, "app": False}  # 사용자가 끈 VM, 부팅 유예 중인 앱 펌웨어
    clock[0] += 60
    watcher.tick([_obs(desired="stopped"), firmware], link=LinkState.up())
    assert watcher.expected_publishers() == {"agent": False, "app": True}


def test_watcher_link_report_follows_grant_window_and_first_delivery():
    clock = [1000.0]
    beats = HeartbeatTable()
    watcher = Watcher(SETTINGS, beats, AppHeartbeatTable(), clock=lambda: clock[0])
    watcher.tick([], link=LinkState.down())
    assert watcher.link_report() == {"agent": NOT_GRANTED, "app": NOT_GRANTED}
    watcher.tick([], link=LinkState.up())
    assert watcher.link_report() == {"agent": WAITING, "app": WAITING}
    clock[0] += 15
    assert watcher.link_report() == {"agent": WAITING, "app": SILENT}  # 창은 채널마다 다르다(30초·15초)
    beats.record(LINUX, "boot-1", 1, clock[0])
    assert watcher.link_report()["agent"] == DELIVERING


def test_worker_reports_on_every_cycle_and_a_failing_report_does_not_stop_it(tmp_path):
    env = Env(tmp_path)
    observations = [[_obs()]]

    def observer():
        if observations[0] is None:
            raise PlatformUnavailable("busy")
        return observations[0]

    watcher = Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: env.clock[0])
    worker = RecoveryWorker(SETTINGS, observer, watcher, link=LinkState.up, clock=lambda: env.clock[0], status=env.reporter)
    try:
        worker.run_once()
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 1)
        assert read_status(tmp_path, now=1000.0)["overall"] == OPERABLE
        observations[0] = None
        worker.run_once()
        wait_for(lambda: env.reporter.sink_snapshot()['written'] == 2)
        assert read_status(tmp_path, now=1000.0)["hold_reasons"] == ["OBSERVATION_FAILED"]
        env.reporter.submit = lambda observations: 1 / 0
        observations[0] = [_obs()]
        worker.run_once()
        assert worker.ticks == 2
    finally:
        worker.shutdown()
