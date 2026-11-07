"""관측 어댑터·MQTT 연결·감시 루프 (S15P31E110-124). 실제 브로커·플랫폼은 없다."""

import json
import queue
from types import SimpleNamespace
from dataclasses import replace
from uuid import UUID

import pytest

from dom0_manager.drivers.xl import Xl
from dom0_manager.errors import PlatformUnavailable
from dom0_manager.models import VmState
from dom0_manager.recovery.arming import ArmingStore
from dom0_manager.recovery.config import RecoverySettings
from dom0_manager.recovery.heartbeats import AppHeartbeatTable, HeartbeatTable
from dom0_manager.recovery.model import FailureReason, HealthState, VmObservation
from dom0_manager.recovery.mqtt import (
    APP_EVENTS_FILTER,
    HEALTH_TYPE,
    HEARTBEAT_FILTER,
    HealthPublisher,
    LinkState,
    app_heartbeat_handler,
    health_topic,
    heartbeat_handler,
    link_state,
    subscribe_liveness,
)
from dom0_manager.recovery.observe import PlatformObserver
from dom0_manager.recovery.watcher import Watcher
from dom0_manager.recovery.worker import RecoveryWorker
from test_xl import FakeRunner

DEBIAN = UUID("4be41d5f-b8ef-408b-9709-7fe5ac11231f")
ZEPHYR = UUID("0d950ea2-cc99-49df-9b60-a4ddf2c8e137")
STOPPED = UUID("cd35a7fc-5f14-456a-a129-f0afc7d91d97")
SETTINGS = RecoverySettings(interval_s=5, intent_refresh_s=60, heartbeat_lost_s=30, boot_grace_s=90, absent_grace_s=60, agent_sole_publisher="trust")

XL_LIST = """\
Name                                        ID   Mem VCPUs	State	Time(s)
Domain-0                                     0  1024     4     r-----      30.1
debian-dev                                   1   512     1     -b----      11.4
zephyr-drive-a                               2    16     1     -b----       0.0
"""
XL_LIST_LONG = json.dumps(
    [
        {"domid": 0, "config": {"c_info": {"name": "Domain-0"}}},
        {"domid": 1, "config": {"c_info": {"name": "debian-dev", "uuid": str(DEBIAN)}}},
        {"domid": 2, "config": {"c_info": {"name": "zephyr-drive-a", "uuid": str(ZEPHYR)}}},
    ]
)
TEMPLATES = [
    {"template_id": "debian-13-arm64", "template_version": "1.0.1", "guest_kind": "linux", "capabilities": {"graceful_shutdown": True}},
    {"template_id": "zephyr-drive-arm64", "template_version": "1.0.0", "guest_kind": "zephyr", "capabilities": {"app_heartbeat": True}},
    {"template_id": "zephyr-xenvm-starter-arm64", "template_version": "1.0.0", "guest_kind": "zephyr", "capabilities": {"app_heartbeat": False}},
]


def _view(vm_id, name, *, kind="linux", desired="running", applicable=True, revision=2, lifecycle="REGISTERED", xen_state=None, domid=None, template=("debian-13-arm64", "1.0.1")):
    return {"vm_id": str(vm_id), "name": name, "guest_kind": kind, "desired_state": desired, "intent_applicable": applicable,
            "intent_revision": revision, "lifecycle": lifecycle, "xen_state": xen_state, "domid": domid, "vcpus": 1, "memory_mib": 512,
            "template_id": template[0], "template_version": template[1],
            "configuration": {"id": name, "vcpus": 1, "memory_mib": 512, "guest_kind": kind}}


class FakeManager:
    data = "/srv/xenova"

    def __init__(self, views, *, fail=None, templates=None):
        self.views = views
        self.data = None  # 주면 intents·list 성공 응답의 data 를 그대로 이것으로 낸다(손상 응답 시험)
        self.fail = fail
        self.templates = TEMPLATES if templates is None else templates
        self.calls = []

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None):
        self.calls.append((action, spec))
        if self.fail and action != "templates":
            return {"ok": False, "error": {"code": self.fail, "message": "x", "retryable": True, "details": {}}}
        if action == "templates":
            if self.templates is False:
                return {"ok": False, "error": {"code": "STORAGE_UNAVAILABLE", "message": "catalog", "retryable": True, "details": {}}}
            return {"ok": True, "data": {"templates": self.templates}}
        if action == "get":
            view = next((v for v in self.views if v.get("vm_id") == spec["vm_id"]), None)
            if view is None:
                return {"ok": False, "error": {"code": "VM_NOT_FOUND", "message": "registered VM not found", "retryable": False, "details": {}}}
            return {"ok": True, "data": {"vm": view}}
        return {"ok": True, "data": {"vms": self.views} if self.data is None else self.data}


class FakeClient:
    """connected 와 필터별 구독 승인(granted)을 따로 둔다. 공용 클라이언트의 subscribed() 와 같은 의미."""

    def __init__(self, connected=True, granted=None):
        self.connected = connected
        self.granted = set(granted) if granted is not None else {HEARTBEAT_FILTER, APP_EVENTS_FILTER}
        self.subscriptions = []
        self.published = []
        self.cleared = []
        self.fail = False
        self.fail_clear = False

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.subscriptions.append((topic_filter, handler, qos))

    def subscribed(self, topic_filter):
        return self.connected and topic_filter in self.granted

    def publish(self, topic, message_type, payload, *, qos=None, retain=None, **kwargs):
        if self.fail:
            raise RuntimeError("broker gone")
        self.published.append((topic, message_type, payload, qos, retain))
        return {}

    def clear_retained(self, topic, *, wait_s=None):
        if self.fail or self.fail_clear:
            raise RuntimeError("broker gone")
        self.cleared.append(topic)


def _message(topic, received=100.0, **envelope):
    base = {"schema_version": "1.0", "message_id": "m", "boot_id": "boot-1", "agent_instance_id": "a", "seq": 1,
            "sent_at": "2026-10-21T00:00:00.000Z", "payload": {}}
    return SimpleNamespace(topic=topic, envelope={**base, **envelope}, received_monotonic=received)


def _heartbeat(vm_id, **extra):
    fields = {"vm_id": str(vm_id), "type": "agent.heartbeat", **extra}
    return _message(f"xenova/v1/vms/{vm_id}/heartbeat", **fields)


def _runner():
    return FakeRunner({("xl", "list"): XL_LIST, ("xl", "list", "-l"): XL_LIST_LONG})


def _obs(vm_id=DEBIAN, state=VmState.running, kind="linux"):
    return VmObservation(vm_id, "vm", kind, "running", True, 2, "REGISTERED", True, state, 1)


# ---- 관측 어댑터 ----


def test_platform_observer_joins_cached_intent_with_fresh_xl_domains():
    clock = [1000.0]
    manager = FakeManager([
        _view(DEBIAN, "debian-dev"), _view(ZEPHYR, "zephyr-drive-a", kind="zephyr", template=("zephyr-drive-arm64", "1.0.0")),
        _view(STOPPED, "stopped-vm", desired="stopped"),
    ])
    runner = _runner()
    observer = PlatformObserver(manager, Xl(runner), intent_refresh_s=60, clock=lambda: clock[0])
    by_id = {o.vm_id: o for o in observer()}
    assert set(by_id) == {DEBIAN, ZEPHYR, STOPPED}
    debian = by_id[DEBIAN]
    assert (debian.present, debian.xen_state, debian.domid, debian.desired_state, debian.intent_revision, debian.eligible, debian.app_heartbeat_expected) == (True, VmState.running, 1, "running", 2, True, None)
    assert (by_id[ZEPHYR].guest_kind, by_id[ZEPHYR].domid, by_id[ZEPHYR].app_heartbeat_expected) == ("zephyr", 2, True)  # 템플릿 capability
    assert (by_id[STOPPED].present, by_id[STOPPED].xen_state, by_id[STOPPED].eligible) == (False, None, False)

    clock[0] += 30
    observer()
    assert [c[0] for c in manager.calls] == ["intents", "templates", "intents"]  # 의도는 매 주기, capability 는 60초마다
    assert "list" not in [c[0] for c in manager.calls]  # 잠금·Xen 관측을 하는 list 는 부르지 않는다
    assert [args[:2] for args, _ in runner.calls] == [("xl", "list"), ("xl", "list"), ("xl", "list"), ("xl", "list")]  # xl 은 매번
    clock[0] += 30
    observer()
    assert [c[0] for c in manager.calls] == ["intents", "templates", "intents", "intents", "templates"]


def test_platform_observer_keeps_registrations_but_marks_intent_unknown_when_refresh_fails():
    manager = FakeManager([_view(DEBIAN, "debian-dev")])
    clock = [0.0]
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    manager.fail = "STORAGE_UNAVAILABLE"
    with pytest.raises(PlatformUnavailable):
        observer()  # 읽은 적이 없으면 관측 실패
    manager.fail = None
    assert [(o.vm_id, o.intent_known) for o in observer()] == [(DEBIAN, True)]
    clock[0] += 5
    manager.fail = "STORAGE_UNAVAILABLE"
    stale = observer()
    assert [(o.vm_id, o.intent_known) for o in stale] == [(DEBIAN, False)]  # 등록은 남기고(삭제 아님) 의도는 근거가 아니다
    assert stale[0].present and stale[0].domid == 1  # xl 은 최신
    clock[0] += 5
    manager.fail = None
    assert [(o.vm_id, o.intent_known) for o in observer()] == [(DEBIAN, True)]  # 다시 읽으면 재개


def test_platform_observer_falls_back_to_list_on_a_manager_without_intents():
    class OldManager(FakeManager):
        def call(self, action, spec=None, **kwargs):
            if action == "intents":
                self.calls.append((action, spec))
                return {"ok": False, "error": {"code": "INVALID_SPEC", "message": "unsupported management action", "retryable": False, "details": {}}}
            return super().call(action, spec, **kwargs)

    manager = OldManager([_view(DEBIAN, "debian-dev")])
    clock = [0.0]
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    assert [(o.vm_id, o.intent_known) for o in observer()] == [(DEBIAN, True)]
    clock[0] += 30
    observer()
    clock[0] += 30
    observer()
    assert [c[0] for c in manager.calls] == ["intents", "list", "templates", "list", "templates"]  # 한 번 확인 뒤 list 를 60초마다


def test_platform_observer_rejects_non_boolean_capability():
    templates = [dict(t) for t in TEMPLATES]
    templates[1] = {**templates[1], "capabilities": {"app_heartbeat": "false"}}  # bool("false") 는 True: 믿으면 starter 를 재시작한다
    manager = FakeManager([_view(ZEPHYR, "zephyr-drive-a", kind="zephyr", template=("zephyr-drive-arm64", "1.0.0"))], templates=templates)
    observation = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: 0.0)()[0]
    assert observation.app_heartbeat_expected is None and "not a JSON boolean" in observation.capability_error


def test_platform_observer_keeps_capabilities_when_catalog_is_unavailable():
    manager = FakeManager([_view(ZEPHYR, "zephyr-drive-a", kind="zephyr", template=("zephyr-drive-arm64", "1.0.0"))])
    clock = [0.0]
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    assert observer()[0].app_heartbeat_expected is True
    manager.templates = False
    clock[0] += 61
    assert observer()[0].app_heartbeat_expected is True  # 카탈로그 실패: 이전 capability 유지


def test_confirm_reads_one_vm_with_get_and_updates_cache():
    manager = FakeManager([_view(DEBIAN, "debian-dev"), _view(ZEPHYR, "zephyr-drive-a", kind="zephyr")])
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)
    observer()
    manager.views[0] = _view(DEBIAN, "debian-dev", desired="stopped", revision=3, xen_state="stopped")  # 사용자가 stop
    fresh = observer.confirm(DEBIAN)
    assert manager.calls[-1] == ("get", {"vm_id": str(DEBIAN)})
    assert (fresh.desired_state, fresh.intent_revision, fresh.present, fresh.eligible) == ("stopped", 3, False, False)
    assert {o.vm_id: o.desired_state for o in observer()}[DEBIAN] == "stopped"
    assert [c[0] for c in manager.calls] == ["intents", "templates", "get", "intents"]

    manager.views = [_view(ZEPHYR, "zephyr-drive-a", kind="zephyr")]
    assert observer.confirm(DEBIAN) is None  # 등록이 사라짐
    assert [o.vm_id for o in observer()] == [ZEPHYR]


def test_confirm_uses_the_observed_domain_from_get():
    manager = FakeManager([_view(DEBIAN, "debian-dev", xen_state="crashed", domid=4)])
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)
    fresh = observer.confirm(DEBIAN)
    assert (fresh.present, fresh.xen_state, fresh.domid, fresh.eligible) == (True, VmState.crashed, 4, True)


# ---- MQTT ----


def test_heartbeat_handler_records_identity_and_drops_wrong_type_payload_or_topic():
    table = HeartbeatTable()
    handle = heartbeat_handler(table)
    handle(_heartbeat(DEBIAN, seq=7, received=123.0))
    beat = table.last(DEBIAN)
    assert (beat.boot_id, beat.seq, beat.received_monotonic) == ("boot-1", 7, 123.0)
    table.forget(DEBIAN)

    handle(_heartbeat(DEBIAN, type="agent.metrics", seq=8))  # heartbeat 토픽에 잘못 올라온 다른 type
    handle(_heartbeat(DEBIAN, payload={"state": "ok"}, seq=9))  # payload 는 {} 여야 한다
    handle(_message(f"xenova/v1/vms/{DEBIAN}/metrics", vm_id=str(DEBIAN), type="agent.heartbeat"))  # 토픽 모양
    handle(_message(f"xenova/v1/vms/{ZEPHYR}/heartbeat", vm_id=str(DEBIAN), type="agent.heartbeat"))  # 토픽·봉투 vm 불일치
    handle(_message("xenova/v1/vms/not-a-uuid/heartbeat", vm_id="not-a-uuid", type="agent.heartbeat"))
    handle(SimpleNamespace(topic=f"xenova/v1/vms/{DEBIAN}/heartbeat", envelope={"vm_id": str(DEBIAN), "type": "agent.heartbeat"}, received_monotonic=1.0))  # boot_id·seq 없음
    assert len(table) == 0


def test_app_heartbeat_handler_keeps_only_valid_app_heartbeats():
    table = AppHeartbeatTable()
    handle = app_heartbeat_handler(table)
    topic = "xenova/v1/apps/autonomous/events"
    handle(_message(topic, vm_id=str(ZEPHYR), app_id="autonomous", type="drive.command", payload={"v_mps": 0.1}))  # 관심 없는 type: 조용히 무시
    handle(_message(topic, vm_id=str(ZEPHYR), app_id="autonomous", type="app.heartbeat", payload={"state": "active", "uptime_s": 12.5}, seq=3))
    beat = table.last(ZEPHYR)
    assert (beat.app_id, beat.state, beat.seq, beat.received_monotonic) == ("autonomous", "active", 3, 100.0)
    table.forget(ZEPHYR)
    handle(_message(topic, vm_id=str(ZEPHYR), app_id="autonomous", type="app.heartbeat", payload={"state": "idle", "uptime_s": 0}, seq=4))  # 정수 0 도 유효
    assert table.last(ZEPHYR).state == "idle"
    table.forget(ZEPHYR)
    for bad in (
        {"state": "active"},  # uptime_s 누락 (계약 6.1 필수)
        {"state": "active", "uptime_s": -1},
        {"state": "active", "uptime_s": float("nan")},
        {"state": "active", "uptime_s": "12"},
        {"state": "active", "uptime_s": True},
        {"state": "flying", "uptime_s": 1},  # 모르는 state
        [],
    ):
        handle(_message(topic, vm_id=str(ZEPHYR), app_id="autonomous", type="app.heartbeat", payload=bad))
    handle(_message(topic, vm_id=str(ZEPHYR), app_id="manual", type="app.heartbeat", payload={"state": "active", "uptime_s": 1}))  # 토픽·봉투 app_id 불일치
    handle(_message(topic, app_id="autonomous", type="app.heartbeat", payload={"state": "active", "uptime_s": 1}))  # vm_id 없음 (VM 이 아닌 발행자)
    assert len(table) == 0


def test_subscribe_liveness_uses_contract_filters_and_link_state_needs_suback():
    client = FakeClient(granted=set())
    subscribe_liveness(client, HeartbeatTable(), AppHeartbeatTable())
    assert [(f, q) for f, _, q in client.subscriptions] == [(HEARTBEAT_FILTER, 1), (APP_EVENTS_FILTER, 0)]
    assert link_state(client) == LinkState(agent=False, app=False)  # 연결은 됐지만 SUBACK 전
    client.granted.add(HEARTBEAT_FILTER)
    assert link_state(client) == LinkState(agent=True, app=False)  # 앱 이벤트 구독만 거부(ACL)
    client.granted.add(APP_EVENTS_FILTER)
    assert link_state(client) == LinkState.up()
    client.connected = False
    assert link_state(client) == LinkState.down()
    assert link_state(object()) == LinkState.down()  # 승인 여부를 모르는 클라이언트는 믿지 않는다


def test_health_publisher_republishes_on_change_or_refresh_and_survives_broker_errors():
    client = FakeClient()
    publisher = HealthPublisher(client, refresh_s=15)
    watcher = Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: 1000.0)
    watcher.tick([_obs()], 1000.0)
    health = watcher.health(DEBIAN)
    assert publisher.publish(health, 1000.0) is True and publisher.publish(health, 1010.0) is False
    topic, message_type, payload, qos, retain = client.published[0]
    assert (topic, message_type, qos, retain) == (health_topic(DEBIAN), HEALTH_TYPE, 1, True)
    assert payload == {"vm_id": str(DEBIAN), "ok": False, "state": "STARTING", "desired_state": "running", "xen_state": "running", "heartbeat_age_s": None, "reason": None, "manager_seq": 1, "lease_s": 45.0}
    assert publisher.publish(health, 1015.0) is True  # 내용이 같아도 refresh 마다 다시 낸다 (받는 쪽 lease 갱신)
    assert client.published[-1][2]["manager_seq"] == 1
    client.fail = True
    crashed = watcher.tick([_obs(state=VmState.crashed)], 1016.0)
    assert crashed and publisher.publish(watcher.health(DEBIAN), 1016.0) is False  # 실패해도 예외 없이 다음 기회에 다시
    client.fail = False
    assert publisher.publish(watcher.health(DEBIAN), 1017.0) is True


def test_health_publisher_clears_retained_for_removed_vm_and_retries_until_delivered():
    client = FakeClient()
    publisher = HealthPublisher(client, refresh_s=15)
    client.fail_clear = True
    assert publisher.clear(DEBIAN) is False
    publisher.retry_clears()
    assert client.cleared == []
    client.fail_clear = False
    publisher.retry_clears()
    assert client.cleared == [health_topic(DEBIAN)]
    publisher.retry_clears()
    assert client.cleared == [health_topic(DEBIAN)]  # 한 번 지워지면 끝


def test_health_publisher_does_not_queue_while_disconnected_and_keeps_clear_pending():
    client = FakeClient(connected=False)
    publisher = HealthPublisher(client, refresh_s=15)
    watcher = Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: 1000.0)
    watcher.tick([_obs()], 1000.0)
    assert publisher.publish(watcher.health(DEBIAN), 1000.0) is False and client.published == []
    assert publisher.clear(DEBIAN) is False and client.cleared == []
    client.connected = True
    publisher.retry_clears()
    assert client.cleared == [health_topic(DEBIAN)]
    assert publisher.publish(watcher.health(DEBIAN), 1001.0) is True


def test_heartbeat_handler_ignores_retained_messages():
    table = HeartbeatTable()
    message = _heartbeat(DEBIAN)
    message.retained = True  # 구독 직후 브로커가 준 옛 값
    heartbeat_handler(table)(message)
    assert table.last(DEBIAN) is None
    message.retained = False
    heartbeat_handler(table)(message)
    assert table.last(DEBIAN) is not None


def test_health_publisher_marks_everything_unavailable_at_shutdown():
    client = FakeClient()
    publisher = HealthPublisher(client, refresh_s=15)
    watcher = Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: 1000.0)
    watcher.tick([_obs()], 1000.0)
    publisher.publish(watcher.health(DEBIAN), 1000.0)
    assert publisher.publish_unavailable(watcher.healths(), 1001.0) == 1
    payload = client.published[-1][2]
    assert (payload["state"], payload["ok"], payload["manager_seq"], payload["reason"]) == ("OBSERVATION_UNAVAILABLE", False, 2, None)


# ---- 루프 ----


def test_worker_queues_judgments_publishes_health_and_skips_failed_observation():
    clock = [1000.0]
    client = FakeClient()
    observations = [[_obs(state=VmState.crashed)]]

    def observer():
        if observations and observations[0] is None:
            observations.pop(0)
            raise PlatformUnavailable("busy")
        return observations[0]

    worker = RecoveryWorker(
        SETTINGS, observer, Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: clock[0]),
        link=lambda: link_state(client), health_publisher=HealthPublisher(client), clock=lambda: clock[0],
        judgments=queue.Queue(maxsize=1),
    )
    judged = worker.run_once()
    assert [j.reason for j in judged] == [FailureReason.DOMAIN_CRASHED]
    assert worker.judgments.get_nowait().vm_id == DEBIAN and worker.judgments.empty()
    assert worker.judgments_total == 1 and worker.judgments_dropped == 0
    assert [p[0] for p in client.published] == [health_topic(DEBIAN)] and client.published[0][2]["state"] == "UNHEALTHY"

    observations.insert(0, None)  # 다음 관측은 플랫폼 잠금 충돌
    assert worker.run_once() == [] and worker.skipped == 1 and worker.ticks == 1
    assert worker.run_once() == [] and worker.ticks == 2  # 같은 장애는 다시 내지 않고 건강 스냅샷도 그대로
    assert len(client.published) == 1
    observations[0] = []  # VM 이 등록에서 사라짐(삭제·보관): retained 건강 스냅샷을 지운다
    assert worker.run_once() == [] and client.cleared == [health_topic(DEBIAN)]


@pytest.mark.parametrize('diagnostic_queue', [False, True])
def test_repeated_closed_episodes_release_diagnostic_copies_but_keep_required_records(tmp_path, diagnostic_queue):
    from dom0_manager.recovery.engine import RecoveryEngine, KEEP_CLOSED
    from dom0_manager.recovery.policy import RecoveryPolicy
    from dom0_manager.recovery.state import RecoveryStore

    class NoPhysicalPlatform:
        def __getattr__(self, name):
            raise AssertionError('policy none must not call platform: ' + name)

    clock, observations = [1000.0], []
    beats = HeartbeatTable()
    watcher = Watcher(SETTINGS, beats, clock=lambda: clock[0])
    store = RecoveryStore(tmp_path/'recoveries.json')
    engine = RecoveryEngine(RecoveryPolicy(policy='none'), NoPhysicalPlatform(), watcher, store,
                            clock=lambda: clock[0], inline=True)
    mirror = queue.Queue(maxsize=1) if diagnostic_queue else None
    worker = RecoveryWorker(SETTINGS, lambda: list(observations), watcher, link=LinkState.down,
                            engine=engine, judgments=mirror, clock=lambda: clock[0])
    try:
        for _ in range(60):
            observations[:] = [_obs(state=VmState.crashed)]
            clock[0] += 1
            assert len(worker.run_once()) == 1
            observations[:] = [replace(_obs(), desired_state='stopped')]
            clock[0] += 1
            assert worker.run_once() == []
        assert worker.judgments_total == 60
        assert len(engine.records()) == len(store.load()) == KEEP_CLOSED
        assert len(engine._skipped) == KEEP_CLOSED
        if mirror is None:
            assert worker.judgments is None and worker.judgments_dropped == 0
        else:
            assert mirror.qsize() == 1 and worker.judgments_dropped == 59
        # 반복 뒤 신선한 정상 신호를 처리한다. 진단 포화가 실행 판정이나 정상 회복을 막지 않는다.
        clock[0] += 1
        beats.record(DEBIAN, 'fixture-boot', 1, clock[0])
        observations[:] = [_obs()]
        assert worker.run_once() == [] and watcher.health(DEBIAN).state == HealthState.HEALTHY
    finally:
        worker.shutdown()


def test_diagnostic_mirror_requires_an_explicit_finite_capacity():
    with pytest.raises(ValueError, match='finite capacity'):
        RecoveryWorker(SETTINGS, lambda: [], Watcher(SETTINGS, HeartbeatTable()),
                       link=LinkState.down, judgments=queue.Queue())


def test_worker_retries_retained_clear_even_when_observation_fails():
    clock = [1000.0]
    client = FakeClient()
    observations = [[_obs()]]

    def observer():
        if observations[0] is None:
            raise PlatformUnavailable("busy")
        return observations[0]

    worker = RecoveryWorker(SETTINGS, observer, Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: clock[0]),
                            link=lambda: link_state(client), health_publisher=HealthPublisher(client), clock=lambda: clock[0])
    worker.run_once()
    client.fail_clear = True
    observations[0] = []  # VM 이 사라졌는데 retained 삭제가 실패
    worker.run_once()
    assert client.cleared == []
    client.fail_clear = False
    observations[0] = None  # 플랫폼 관측 실패가 이어져도 삭제 재시도는 돈다
    assert worker.run_once() == [] and client.cleared == [health_topic(DEBIAN)]


def test_worker_collects_engine_results_even_when_observation_fails():
    clock = [1000.0]
    calls = []
    engine = SimpleNamespace(on_judgment=lambda j, now: calls.append(("judgment", j.vm_id)), tick=lambda now: calls.append(("tick", now)),
                             observation_unavailable=lambda: calls.append(('unavailable',)),
                             collect=lambda now: calls.append(("collect", now)), shutdown=lambda: calls.append(("shutdown",)))

    def observer():
        raise PlatformUnavailable("busy")

    worker = RecoveryWorker(SETTINGS, observer, Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: clock[0]),
                            link=LinkState.up, engine=engine, clock=lambda: clock[0])
    assert worker.run_once() == [] and calls == [('unavailable',), ("collect", 1000.0)]  # 안정 근거를 끊고 원 결과는 회수한다.
    worker.shutdown()
    assert calls[-1] == ("shutdown",)


def test_worker_shutdown_marks_health_unavailable():
    clock = [1000.0]
    client = FakeClient()
    worker = RecoveryWorker(SETTINGS, lambda: [_obs()], Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: clock[0]),
                            link=lambda: link_state(client), health_publisher=HealthPublisher(client), clock=lambda: clock[0])
    worker.run_once()
    assert client.published[-1][2]["state"] == "STARTING"
    worker.shutdown()
    assert client.published[-1][2]["state"] == "OBSERVATION_UNAVAILABLE" and client.published[-1][4] is True


def test_worker_passes_subscription_state_to_watcher(tmp_path):
    clock = [1000.0]
    client = FakeClient(granted=set())  # 연결은 됐지만 ACL 이 구독을 거부
    running = [_obs()]
    table = HeartbeatTable()
    arming = ArmingStore(tmp_path / "armed.json")
    assert arming.save({}, {DEBIAN})  # Worker 재시작 전에 이 VM 의 heartbeat 를 받은 적이 있다
    watcher = Watcher(SETTINGS, table, AppHeartbeatTable(), arming=arming, clock=lambda: clock[0])
    worker = RecoveryWorker(SETTINGS, lambda: running, watcher,
                            link=lambda: link_state(client), health_publisher=HealthPublisher(client), clock=lambda: clock[0])
    for _ in range(40):  # 200초, heartbeat 구독 승인 없음
        clock[0] += 5
        assert worker.run_once() == []
    assert client.published[-1][2]["state"] == HealthState.OBSERVATION_UNAVAILABLE.value
    client.granted.add(HEARTBEAT_FILTER)  # SUBACK 승인
    clock[0] += 5
    assert worker.run_once() == []  # 승인은 봤지만 아직 아무것도 안 옴
    table.record(UUID(int=77), "boot-x", 1, clock[0])  # 다른 VM 의 heartbeat: 브로커가 실제로 전달함을 증명
    judged = []
    for _ in range(7):  # 증명 뒤 30초 창
        clock[0] += 5
        judged += worker.run_once()
    assert [j.reason for j in judged] == [FailureReason.HEARTBEAT_LOST]


def test_worker_run_stops_on_event_and_keeps_monotonic_schedule():
    clock = [0.0]
    sleeps = []
    ticks = []

    class FakeStop:
        def __init__(self):
            self.flag = False

        def set(self):
            self.flag = True

        def is_set(self):
            return self.flag

        def wait(self, delay):
            sleeps.append(delay)
            clock[0] += delay
            return self.flag

    stop = FakeStop()

    def observer():
        ticks.append(clock[0])
        if len(ticks) == 3:
            stop.set()
        return []

    worker = RecoveryWorker(SETTINGS, observer, Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: clock[0]), link=LinkState.up, clock=lambda: clock[0])
    worker.run(stop)
    assert ticks == [0.0, 5.0, 10.0] and sleeps[:2] == [5.0, 5.0]


def test_worker_keeps_health_targets_when_intents_cannot_be_read(tmp_path):
    """의도 조회가 실패해도 등록 삭제로 해석해 retained 건강을 지우지 않는다. 판정은 보류하고 건강은 관측 불가로 낸다."""
    clock = [1000.0]
    manager = FakeManager([_view(DEBIAN, "debian-dev")])
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    client = FakeClient()
    table = HeartbeatTable()
    watcher = Watcher(SETTINGS, table, AppHeartbeatTable(), clock=lambda: clock[0])
    worker = RecoveryWorker(SETTINGS, observer, watcher, link=lambda: link_state(client),
                            health_publisher=HealthPublisher(client), clock=lambda: clock[0])
    table.record(DEBIAN, "boot-1", 1, clock[0])
    assert worker.run_once() == []
    manager.fail = "STORAGE_UNAVAILABLE"
    for _ in range(40):  # 200초: heartbeat 도 없고 의도도 못 읽는다
        clock[0] += 5
        assert worker.run_once() == []
    assert client.cleared == []
    assert client.published[-1][2]["state"] == HealthState.OBSERVATION_UNAVAILABLE.value



@pytest.mark.parametrize("data", [
    {},  # vms 누락
    {"vms": None},
    {"vms": {"debian": "x"}},
    {"vms": [_view(DEBIAN, "debian-dev"), "not-an-object"]},
    {"vms": [_view(DEBIAN, "debian-dev"), {**_view(ZEPHYR, "zephyr-drive-a"), "vm_id": "not-a-uuid"}]},  # 일부 행만 손상
    {"vms": [_view(DEBIAN, "debian-dev"), _view(DEBIAN, "debian-copy")]},  # 중복 vm_id
    {"vms": [{**_view(DEBIAN, "debian-dev"), "desired_state": None}]},
    {"vms": [{**_view(DEBIAN, "debian-dev"), "intent_applicable": "true"}]},
    {"vms": [{**_view(DEBIAN, "debian-dev"), "intent_revision": "2"}]},
    {"vms": [{key: value for key, value in _view(DEBIAN, "debian-dev").items() if key != "lifecycle"}]},
    {"vms": [{key: value for key, value in _view(DEBIAN, "debian-dev").items() if key != "guest_kind"}]},
    {"vms": [_view(DEBIAN, "debian-dev", desired="banana")]},  # 타입은 맞지만 허용값이 아님
    {"vms": [_view(DEBIAN, "debian-dev", lifecycle="BROKEN")]},
    {"vms": [_view(DEBIAN, "debian-dev", lifecycle="RETIRED")]},  # 기본 목록은 REGISTERED 만
    {"vms": [_view(DEBIAN, "debian-dev", kind="windows")]},
    {"vms": [_view(UUID(int=0), "nil")]},
    {"vms": [_view(DEBIAN, "debian-dev", revision=0)]},
    {"vms": [_view(DEBIAN, "debian-dev", desired="unknown", applicable=True)]},  # 적용 가능한데 상태 없음
    {"vms": [_view(DEBIAN, "debian-dev", revision=None, applicable=True)]},  # 적용 가능한데 revision 없음
    {"vms": [_view(DEBIAN, "")]},  # 이름 없음
    {"vms": [{key: value for key, value in _view(DEBIAN, "debian-dev").items() if key != "name"}]},
    {"vms": [_view(ZEPHYR, "zephyr-drive-a", kind="zephyr", template=("", "1.0.0"))]},  # Zephyr 는 템플릿 필수
    {"vms": [{key: value for key, value in _view(ZEPHYR, "zephyr-drive-a", kind="zephyr").items() if key != "template_version"}]},
    {"vms": [_view(DEBIAN, "debian-dev", template=(7, "1.0.0"))]},
])
def test_invalid_intent_response_keeps_the_previous_registrations(data):
    """성공 envelope 라도 목록이 불완전하면 부분 적용하지 않는다. 이전 등록을 유지하고 의도는 모름으로 둔다."""
    manager = FakeManager([_view(DEBIAN, "debian-dev"), _view(ZEPHYR, "zephyr-drive-a", kind="zephyr")])
    clock = [0.0]
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    assert {o.vm_id for o in observer()} == {DEBIAN, ZEPHYR}
    manager.data = data
    clock[0] += 5
    stale = observer()
    assert {o.vm_id for o in stale} == {DEBIAN, ZEPHYR} and all(not o.intent_known for o in stale)
    manager.data = None
    clock[0] += 5
    assert all(o.intent_known for o in observer())


def test_invalid_first_intent_response_is_an_observation_failure():
    manager = FakeManager([])
    manager.data = {"vms": [{"vm_id": "not-a-uuid", "name": "broken"}]}
    with pytest.raises(PlatformUnavailable):
        PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)()


def test_worker_does_not_clear_health_on_a_partial_intent_response():
    clock = [1000.0]
    manager = FakeManager([_view(DEBIAN, "debian-dev"), _view(ZEPHYR, "zephyr-drive-a", kind="zephyr")])
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    client = FakeClient()
    watcher = Watcher(SETTINGS, HeartbeatTable(), AppHeartbeatTable(), clock=lambda: clock[0])
    worker = RecoveryWorker(SETTINGS, observer, watcher, link=lambda: link_state(client),
                            health_publisher=HealthPublisher(client), clock=lambda: clock[0])
    worker.run_once()
    manager.data = {"vms": [_view(DEBIAN, "debian-dev"), {"vm_id": "not-a-uuid"}]}  # ZEPHYR 행이 깨져 빠졌다
    for _ in range(3):
        clock[0] += 5
        worker.run_once()
    assert client.cleared == [] and watcher.health(ZEPHYR) is not None



def test_intent_rows_without_an_applicable_intent_are_valid():
    """명시 의도가 없으면 desired_state=unknown·revision null·applicable=false 다(플랫폼 계약). 손상이 아니다."""
    manager = FakeManager([_view(DEBIAN, "debian-dev", desired="unknown", applicable=False, revision=None),
                           _view(ZEPHYR, "zephyr-drive-a", kind="zephyr", desired="stopped", applicable=False, revision=4)])
    observations = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)()
    assert all(o.intent_known for o in observations) and not any(o.eligible for o in observations)



def test_linux_registration_without_a_template_is_valid():
    """Linux 는 템플릿을 판정에 쓰지 않는다. 템플릿이 없는 등록(null) 하나로 전체 의도 조회를 실패시키지 않는다."""
    manager = FakeManager([{**_view(DEBIAN, "debian-dev"), "template_id": None, "template_version": None}])
    assert [o.intent_known for o in PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)()] == [True]


@pytest.mark.parametrize("change", [
    {"guest_kind": "windows"},
    {"intent_revision": "3"},
    {"desired_state": "banana"},
    {"vm_id": str(ZEPHYR)},  # 다른 VM 의 응답
    {"intent_applicable": True, "lifecycle": "RETIRED"},
])
def test_confirm_rejects_an_invalid_get_and_keeps_the_cache(change):
    manager = FakeManager([_view(DEBIAN, "debian-dev"), _view(ZEPHYR, "zephyr-drive-a", kind="zephyr", template=("zephyr-drive-arm64", "1.0.0"))])
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)
    observer()
    before = {vm_id: dict(intent) for vm_id, intent in observer._intents.items()}  # noqa: SLF001 - 재조회 없이 캐시를 직접 본다
    bad = {**_view(DEBIAN, "debian-dev", desired="stopped", revision=9), **change}
    manager.call = lambda action, spec=None, **kw: {"ok": True, "data": {"vm": bad}}
    with pytest.raises(PlatformUnavailable):
        observer.confirm(DEBIAN)
    assert observer._intents == before  # noqa: SLF001 - 다음 intents 재조회로 덮기 전, 잘못된 get 이 캐시를 바꾸지 않았다


def test_confirm_accepts_a_retired_registration_as_not_eligible():
    manager = FakeManager([_view(DEBIAN, "debian-dev")])
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60)
    observer()
    manager.views[0] = _view(DEBIAN, "debian-dev", lifecycle="RETIRED", applicable=False)
    fresh = observer.confirm(DEBIAN)
    assert fresh.lifecycle == "RETIRED" and not fresh.eligible


def test_invalid_confirm_holds_the_judgment():
    """판정 직전 get 이 잘못된 응답이면 그 주기에는 판정하지 않는다(감시기는 재확인 실패를 보류로 다룬다)."""
    clock = [1000.0]
    manager = FakeManager([_view(DEBIAN, "debian-dev", xen_state="running", domid=1)])  # get 도 xl 과 같은 실행을 본다
    observer = PlatformObserver(manager, Xl(_runner()), intent_refresh_s=60, clock=lambda: clock[0])
    table = HeartbeatTable()
    watcher = Watcher(SETTINGS, table, AppHeartbeatTable(), confirm=observer.confirm, clock=lambda: clock[0])
    client = FakeClient()
    worker = RecoveryWorker(SETTINGS, observer, watcher, link=lambda: link_state(client), clock=lambda: clock[0])
    table.record(UUID(int=77), "boot-x", 1, clock[0])  # 채널 증거
    table.record(DEBIAN, "boot-1", 1, clock[0])
    worker.run_once()
    real_call = manager.call

    def corrupt_get(action, spec=None, **kwargs):
        if action == "get":
            return {"ok": True, "data": {"vm": {**_view(DEBIAN, "debian-dev"), "guest_kind": "zephyr", "template_id": ""}}}
        return real_call(action, spec, **kwargs)

    manager.call = corrupt_get
    judged = []
    for _ in range(20):  # 100초: heartbeat 손실 창을 넘긴다
        clock[0] += 5
        judged += worker.run_once()
    assert judged == []
    manager.call = real_call
    clock[0] += 5
    assert [j.reason for j in worker.run_once()] == [FailureReason.HEARTBEAT_LOST]  # 정상 get 이면 판정
