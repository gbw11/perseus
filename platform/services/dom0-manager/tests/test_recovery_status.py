"""Worker 자가진단 상태 (S15P31E110-354). 상태 계산·전환 로그·원자적 기록·읽는 쪽의 STALE/UNAVAILABLE 판정."""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

import pytest

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
    assert read_status(tmp_path, now=10.0)["overall"] == STALE  # 이전 부팅의 단조 시각


def test_reader_reports_missing_unreadable_and_malformed_files_as_unavailable(tmp_path):
    assert read_status(tmp_path, now=0)["overall"] == UNAVAILABLE
    path = tmp_path / STATUS_FILE
    path.write_text("{", encoding="utf-8")
    assert "not valid JSON" in read_status(tmp_path, now=0)["reason"]
    env = Env(tmp_path)
    good = env.report([_obs()])
    for damage in ({"overall": "FINE"}, {"version": 2}, {"updated_monotonic": "soon"}, {"updated_monotonic": -1}, {"stale_after_s": 0},
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
    worker.run_once()
    assert read_status(tmp_path, now=1000.0)["overall"] == OPERABLE
    observations[0] = None
    worker.run_once()
    assert read_status(tmp_path, now=1000.0)["hold_reasons"] == ["OBSERVATION_FAILED"]
    env.reporter.report = lambda observations: 1 / 0
    observations[0] = [_obs()]
    worker.run_once()
    assert worker.ticks == 2
