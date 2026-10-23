"""장애 판정 규칙 (S15P31E110-124). 가짜 단조 시계로 heartbeat·도메인·의도·관측 경로 조합을 돌린다."""

import json
from dataclasses import replace
from uuid import UUID

import pytest

from dom0_manager.errors import PlatformUnavailable
from dom0_manager.models import VmState
from dom0_manager.recovery.arming import ArmingState, ArmingStore
from dom0_manager.recovery.config import RecoverySettings
from dom0_manager.recovery.heartbeats import AppHeartbeatTable, HeartbeatTable
from dom0_manager.recovery.model import FailureReason, HealthState, VmObservation
from dom0_manager.recovery.mqtt import LinkState
from dom0_manager.recovery.watcher import Watcher

VM = UUID("4be41d5f-b8ef-408b-9709-7fe5ac11231f")
ZEPHYR = UUID("0d950ea2-cc99-49df-9b60-a4ddf2c8e137")
SETTINGS = RecoverySettings(interval_s=5, heartbeat_lost_s=30, app_heartbeat_lost_s=15, boot_grace_s=90, zephyr_boot_grace_s=30, absent_grace_s=60)


def obs(vm_id=VM, *, present=True, state=VmState.running, desired="running", kind="linux", applicable=True, lifecycle="REGISTERED", domid=1, revision=2, expected=None):
    return VmObservation(
        vm_id=vm_id, name="vm", guest_kind=kind, desired_state=desired, intent_applicable=applicable, intent_revision=revision,
        lifecycle=lifecycle, present=present, xen_state=state if present else None, domid=domid if present else None,
        app_heartbeat_expected=expected,
    )


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class World:
    def __init__(self, confirm=None, arming=None):
        self.clock = Clock()
        self.table = HeartbeatTable()
        self.apps = AppHeartbeatTable()
        ids = iter(UUID(int=n) for n in range(1, 100))
        self.watcher = Watcher(SETTINGS, self.table, self.apps, confirm=confirm, arming=arming, clock=self.clock, new_id=lambda: next(ids))
        self.link = LinkState.up()

    def tick(self, *observations):
        return self.watcher.tick(list(observations), self.clock.now, link=self.link)

    def run(self, observation, seconds, step=5):
        """step 초마다 tick 하며 판정을 모은다."""
        out = []
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.clock.now += step
            out += self.tick(observation)
        return out

    def beat(self, vm_id=VM, boot_id="boot-1", seq=1):
        self.table.record(vm_id, boot_id, seq, self.clock.now)

    def app_beat(self, vm_id=ZEPHYR, app_id="autonomous", state="active", boot_id="fw-1", seq=1):
        self.apps.record(vm_id, app_id, state, boot_id, seq, self.clock.now)

    def health(self, vm_id=VM):
        return self.watcher.health(vm_id)

    def prove(self, channel="agent"):
        """경로 증거: 다른 VM 의 신호가 이 채널로 하나 온다 (브로커가 실제로 전달한다는 뜻)."""
        if channel == "agent":
            self.table.record(UUID(int=77), "boot-x", 1, self.clock.now)
        else:
            self.apps.record(UUID(int=78), "autonomous", "active", "fw-x", 1, self.clock.now)


@pytest.fixture
def world():
    return World()


# ---- Linux: agent.heartbeat ----


def test_heartbeat_loss_is_judged_once_after_boot_grace(world):
    world.prove()
    assert world.tick(obs()) == []  # 도메인 처음 봄: boot_grace 시작
    assert world.run(obs(), 85) == []  # 유예 안: heartbeat 가 없어도 판정 없음
    assert world.health().state == HealthState.STARTING  # 유예 중이라도 신호가 없으면 건강하다고 하지 않는다
    world.clock.now += 5  # 90초 경과
    judged = world.tick(obs())
    assert [j.reason for j in judged] == [FailureReason.HEARTBEAT_LOST]
    assert judged[0].heartbeat_age_s is None and judged[0].recovery_id == UUID(int=1)
    assert world.run(obs(), 60) == []  # 같은 장애는 한 번만
    assert world.health().state == HealthState.UNHEALTHY and world.health().reason == FailureReason.HEARTBEAT_LOST


def test_fresh_heartbeats_prevent_judgment_and_loss_is_measured_from_last_beat(world):
    world.tick(obs())
    assert world.health().state == HealthState.STARTING
    for n in range(1, 30):  # 145초 동안 5초마다 heartbeat
        world.clock.now += 5
        world.beat(seq=n)
        assert world.tick(obs()) == [] and world.health().ok is True
    last = world.clock.now
    assert world.run(obs(), 25) == []  # 25초 무수신: 아직
    world.clock.now = last + 30
    judged = world.tick(obs())
    assert [j.reason for j in judged] == [FailureReason.HEARTBEAT_LOST] and judged[0].heartbeat_age_s == 30.0


def test_heartbeat_resume_clears_episode_and_next_failure_gets_new_recovery_id(world):
    world.prove()
    world.tick(obs())
    world.clock.now += 90
    first = world.tick(obs())
    assert first[0].recovery_id == UUID(int=1)
    world.beat(boot_id="boot-2", seq=1)  # 에이전트가 살아남
    assert world.tick(obs()) == [] and world.health().ok is True
    world.clock.now += 30
    second = world.tick(obs())
    assert second[0].reason == FailureReason.HEARTBEAT_LOST and second[0].recovery_id == UUID(int=2)


def test_heartbeat_from_previous_boot_does_not_count_after_restart(world):
    world.tick(obs(domid=1))
    world.clock.now += 100
    world.beat(boot_id="boot-1", seq=20)
    assert world.tick(obs(domid=1)) == []
    world.clock.now += 1
    assert world.tick(obs(domid=2)) == []  # 재시작: 새 domid, 새 boot_grace
    assert world.health().heartbeat_age_s is None and world.health().state == HealthState.STARTING
    assert world.run(obs(domid=2), 85) == []
    world.clock.now += 5
    assert [j.reason for j in world.tick(obs(domid=2))] == [FailureReason.HEARTBEAT_LOST]


# ---- Zephyr: app.heartbeat (D8) ----


def test_zephyr_without_declaration_or_signal_is_never_judged_by_liveness(world):
    z = obs(ZEPHYR, kind="zephyr")
    world.tick(z)
    assert world.run(z, 600) == []
    assert world.health(ZEPHYR).state == HealthState.HEALTHY  # Xen 실행만 근거 (앱 신호를 선언하지도 받지도 않은 펌웨어)


def test_zephyr_app_heartbeat_loss_is_judged_after_zephyr_grace(world):
    z = obs(ZEPHYR, kind="zephyr")
    world.tick(z)
    for n in range(1, 40):  # 39초 동안 1초마다 app.heartbeat
        world.clock.now += 1
        world.app_beat(seq=n)
        assert world.tick(z) == [] and world.health(ZEPHYR).ok is True
    last = world.clock.now
    assert world.run(z, 10, step=1) == []  # 10초 무수신: 아직
    world.clock.now = last + 15
    judged = world.tick(z)
    assert [(j.vm_id, j.reason, j.heartbeat_age_s) for j in judged] == [(ZEPHYR, FailureReason.APP_HEARTBEAT_LOST, 15.0)]
    assert world.run(z, 30, step=1) == []
    world.app_beat(seq=100)  # 앱이 돌아옴
    assert world.tick(z) == [] and world.health(ZEPHYR).ok is True


def test_zephyr_restart_waits_for_app_within_zephyr_grace(world):
    z1, z2 = obs(ZEPHYR, kind="zephyr", domid=5), obs(ZEPHYR, kind="zephyr", domid=6)
    world.tick(z1)
    world.app_beat()
    world.tick(z1)
    world.clock.now += 1
    assert world.tick(z2) == [] and world.health(ZEPHYR).state == HealthState.STARTING  # 재시작: 앱 신호 아직 없음
    assert world.run(z2, 25, step=5) == []
    world.clock.now += 5  # zephyr_boot_grace 30초 경과
    assert [j.reason for j in world.tick(z2)] == [FailureReason.APP_HEARTBEAT_LOST]


def test_declared_app_firmware_is_judged_without_any_prior_signal(world):
    """템플릿 capability 가 app.heartbeat 를 선언하면 신호를 받은 적 없어도 유예 뒤 판정한다. Worker 재시작 직후 앱이 이미 죽어 있어도 놓치지 않는다."""
    world.prove("app")
    z = obs(ZEPHYR, kind="zephyr", expected=True)
    world.tick(z)
    assert world.health(ZEPHYR).state == HealthState.STARTING
    assert world.run(z, 25) == []
    world.clock.now += 5
    assert [j.reason for j in world.tick(z)] == [FailureReason.APP_HEARTBEAT_LOST]


def test_arming_persists_across_watcher_restart(tmp_path):
    store = ArmingStore(tmp_path / "recovery" / "armed.json")
    first = World(arming=store)
    z = obs(ZEPHYR, kind="zephyr")
    first.tick(z)
    first.app_beat(app_id="manual")
    first.tick(z)
    assert json.loads((tmp_path / "recovery" / "armed.json").read_text()) == {"version": 1, "armed": {str(ZEPHYR): "manual"}, "history_lost": False}

    second = World(arming=store)  # Worker 재시작: 앱은 이미 죽어 신호가 없다
    second.prove("app")
    assert second.watcher.armed() == {ZEPHYR: "manual"}
    second.tick(z)
    assert second.health(ZEPHYR).state == HealthState.STARTING  # 선언·armed 된 VM 은 신호 전에 건강하다고 하지 않는다
    assert second.run(z, 25) == []
    second.clock.now += 5
    assert [j.reason for j in second.tick(z)] == [FailureReason.APP_HEARTBEAT_LOST]

    second.tick(obs())  # Zephyr 가 등록에서 사라지면 arming 도 지운다
    assert second.watcher.armed() == {} and json.loads((tmp_path / "recovery" / "armed.json").read_text())["armed"] == {}


def test_arming_store_missing_file_is_empty_and_not_lost(tmp_path):
    assert ArmingStore(tmp_path / "none.json").load() == ArmingState()
    assert ArmingStore(None).load() == ArmingState()


@pytest.mark.parametrize("text", [
    "{not json",
    json.dumps({"not-a-uuid": "x"}),  # 버전 없는 옛 형식
    json.dumps({"version": 1, "armed": {"not-a-uuid": "x"}}),
    json.dumps({"version": 2, "armed": {}}),
    json.dumps({"version": 1, "armed": []}),
    json.dumps({"version": 1, "armed": {str(ZEPHYR): ""}}),
    json.dumps({"version": 1, "armed": {}, "history_lost": "yes"}),
])
def test_corrupt_arming_store_is_set_aside_and_history_lost_persists(tmp_path, text):
    path = tmp_path / "armed.json"
    path.write_text(text, encoding="utf-8")
    store = ArmingStore(path)
    assert store.load() == ArmingState({}, history_lost=True)
    assert (tmp_path / "armed.json.corrupt").read_text(encoding="utf-8") == text  # 깨진 파일은 보존
    assert json.loads(path.read_text()) == {"version": 1, "armed": {}, "history_lost": True}
    store.save({ZEPHYR: "manual"})
    assert ArmingStore(path).load() == ArmingState({ZEPHYR: "manual"}, history_lost=True)  # Worker 재시작으로는 풀리지 않는다
    path.unlink()  # 운영자가 지우면 풀린다
    assert ArmingStore(path).load() == ArmingState()


def test_lost_arming_history_reports_undeclared_zephyr_as_unobservable(tmp_path):
    path = tmp_path / "armed.json"
    path.write_text("{not json", encoding="utf-8")
    world = World(arming=ArmingStore(path))
    assert world.watcher.history_lost is True
    undeclared = obs(ZEPHYR, kind="zephyr", domid=2)  # 템플릿에 capability 없음: 앱 펌웨어인지 starter 인지 모른다
    starter = obs(UUID(int=7), kind="zephyr", expected=False, domid=3)
    declared = obs(UUID(int=8), kind="zephyr", expected=True, domid=4)
    world.beat()
    assert world.tick(obs(), undeclared, starter, declared) == []
    assert world.health(VM).state == HealthState.HEALTHY
    assert world.health(ZEPHYR).state == HealthState.OBSERVATION_UNAVAILABLE  # HEALTHY 로 꾸미지 않는다
    assert world.health(starter.vm_id).state == HealthState.HEALTHY  # starter 로 선언된 VM 은 Xen 상태만
    assert world.health(declared.vm_id).state == HealthState.STARTING
    world.clock.now += 120
    assert world.tick(undeclared, starter) == []  # 대상 여부를 모르는 VM 은 판정하지 않는다
    assert world.health(ZEPHYR).state == HealthState.OBSERVATION_UNAVAILABLE
    world.app_beat(app_id="manual")  # 신호를 받으면 armed 되어 보통의 Zephyr 대상이 된다
    world.tick(undeclared, starter)
    assert world.health(ZEPHYR).state == HealthState.HEALTHY and world.watcher.armed() == {ZEPHYR: "manual"}
    assert json.loads(path.read_text()) == {"version": 1, "armed": {str(ZEPHYR): "manual"}, "history_lost": True}
    world.clock.now += 20
    assert [j.reason for j in world.tick(undeclared, starter)] == [FailureReason.APP_HEARTBEAT_LOST]


def test_invalid_capability_value_makes_zephyr_target_unknown_until_it_signals(world):
    bad = replace(obs(ZEPHYR, kind="zephyr", domid=2), capability_error="template x@1: capability app_heartbeat='false' is not a JSON boolean")
    assert world.tick(bad) == []
    assert world.health(ZEPHYR).state == HealthState.OBSERVATION_UNAVAILABLE
    world.clock.now += 120
    assert world.tick(bad) == []  # Xen 만 보고 HEALTHY 라고도, 앱 신호가 없다고 FAILED 라고도 하지 않는다
    assert world.health(ZEPHYR).state == HealthState.OBSERVATION_UNAVAILABLE
    world.app_beat(app_id="manual")  # 신호를 받으면 관측된 사실이 우선: armed 되어 보통의 Zephyr 대상
    world.tick(bad)
    assert world.health(ZEPHYR).state == HealthState.HEALTHY
    world.clock.now += 20
    assert [j.reason for j in world.tick(bad)] == [FailureReason.APP_HEARTBEAT_LOST]
    other = World()
    assert [j.reason for j in other.tick(replace(bad, xen_state=VmState.crashed))] == [FailureReason.DOMAIN_CRASHED]  # Xen 관측 판정은 계속


def test_watcher_reports_vms_that_left_the_registry(world):
    world.tick(obs(), obs(ZEPHYR, kind="zephyr", domid=2))
    assert world.watcher.removed_vm_ids() == []
    world.tick(obs())
    assert world.watcher.removed_vm_ids() == [ZEPHYR] and world.watcher.removed_vm_ids() == []


def test_linux_vm_ignores_app_heartbeat_and_zephyr_ignores_agent_heartbeat(world):
    z = obs(ZEPHYR, kind="zephyr", domid=2)
    world.tick(obs(), z)
    world.clock.now += 100
    world.app_beat(vm_id=VM)  # Linux VM 이 앱 신호만 보냄: 에이전트 heartbeat 가 없으므로 판정
    world.beat(vm_id=ZEPHYR)  # Zephyr 가 에이전트 heartbeat 를 보낼 리 없지만, 와도 앱 신호 대신 쓰지 않는다
    judged = world.tick(obs(), z)
    assert [(j.vm_id, j.reason) for j in judged] == [(VM, FailureReason.HEARTBEAT_LOST)]
    assert world.health(ZEPHYR).state == HealthState.HEALTHY and world.health(ZEPHYR).heartbeat_age_s is None


# ---- 관측 경로 (필터별 구독 승인) ----


def test_no_liveness_judgment_before_path_granted_and_health_says_unobservable(world):
    world.link = LinkState.down()
    world.tick(obs())
    assert world.run(obs(), 300) == []  # 구독 승인 없이는 heartbeat 손실을 절대 판정하지 않는다
    assert world.health().state == HealthState.OBSERVATION_UNAVAILABLE and world.health().ok is False
    world.link = LinkState.up()
    world.prove()
    assert world.tick(obs()) == []  # 승인 직후: 한 창(30초)을 기다린다
    assert world.run(obs(), 25) == []
    world.clock.now += 5
    assert [j.reason for j in world.tick(obs())] == [FailureReason.HEARTBEAT_LOST]


def test_agent_path_denied_suspends_linux_only_and_app_path_denied_suspends_zephyr_only():
    z = obs(ZEPHYR, kind="zephyr", expected=True, domid=2)
    world = World()
    world.link = LinkState(agent=False, app=True)  # ACL 이 heartbeat 구독만 거부
    world.prove("app")
    judged = []
    for _ in range(21):  # 105초, 두 VM 을 매 주기 함께 관측
        judged += world.tick(obs(), z)
        world.clock.now += 5
    assert [(j.vm_id, j.reason) for j in judged] == [(ZEPHYR, FailureReason.APP_HEARTBEAT_LOST)]  # 앱 경로는 열려 있다
    assert world.health().state == HealthState.OBSERVATION_UNAVAILABLE  # Linux 는 관측 불가

    world2 = World()
    world2.link = LinkState(agent=True, app=False)  # 앱 이벤트 구독만 거부
    world2.prove("agent")
    judged = []
    for _ in range(21):
        judged += world2.tick(obs(), z)
        world2.clock.now += 5
    assert [(j.vm_id, j.reason) for j in judged] == [(VM, FailureReason.HEARTBEAT_LOST)]
    assert world2.health(ZEPHYR).state == HealthState.OBSERVATION_UNAVAILABLE


def test_path_loss_mid_run_suspends_liveness_but_not_xen_judgments(world):
    world.tick(obs())
    world.clock.now += 100
    world.beat()
    assert world.tick(obs()) == []
    world.link = LinkState.down()
    assert world.run(obs(), 120) == []  # 끊긴 동안 heartbeat 가 멈춰도 판정 없음
    assert world.health().state == HealthState.OBSERVATION_UNAVAILABLE
    assert [j.reason for j in world.tick(obs(state=VmState.crashed))] == [FailureReason.DOMAIN_CRASHED]  # Xen 관측은 계속
    world.link = LinkState.up()
    world.tick(obs(domid=2))  # 재시작됨: 같은 episode 가 이어진다 (첫 신호 전)
    assert world.watcher.open_recovery(VM) is not None and world.health().state == HealthState.STARTING
    world.beat(boot_id="boot-2")
    assert world.tick(obs(domid=2)) == [] and world.watcher.open_recovery(VM) is None  # 첫 신호: 건강 → episode 닫힘
    assert world.run(obs(domid=2), 25) == []
    world.clock.now += 5  # 첫 신호 뒤에는 boot grace 없이 창(30초)만 본다
    assert [j.reason for j in world.tick(obs(domid=2))] == [FailureReason.HEARTBEAT_LOST]


# ---- 도메인 상태 ----


def test_crashed_domain_is_judged_immediately_even_for_zephyr(world):
    judged = world.tick(obs(state=VmState.crashed), obs(ZEPHYR, kind="zephyr", state=VmState.crashed, domid=2))
    assert [(j.vm_id, j.reason) for j in judged] == [(VM, FailureReason.DOMAIN_CRASHED), (ZEPHYR, FailureReason.DOMAIN_CRASHED)]
    assert world.health().state == HealthState.UNHEALTHY and world.health().reason == FailureReason.DOMAIN_CRASHED


def test_absent_domain_is_judged_after_grace_and_episode_closes_on_first_signal(world):
    assert world.tick(obs(present=False)) == []
    assert world.health().state == HealthState.STARTING
    assert world.run(obs(present=False), 55) == []
    world.clock.now += 5
    judged = world.tick(obs(present=False))
    assert [j.reason for j in judged] == [FailureReason.DOMAIN_ABSENT] and judged[0].domid is None
    assert world.run(obs(present=False), 30) == []
    assert world.tick(obs(domid=7)) == [] and world.health().state == HealthState.STARTING  # 재시작(새 domid): 신호 대기, episode 유지
    assert world.watcher.open_recovery(VM) is not None
    world.beat(boot_id="boot-7")
    world.tick(obs(domid=7))
    assert world.watcher.open_recovery(VM) is None and world.health().ok is True  # 첫 신호로 닫힘


def test_paused_or_stopping_is_not_a_failure(world):
    for state in (VmState.paused, VmState.stopping):
        world.tick(obs(state=state))
        assert world.run(obs(state=state), 200) == []
        assert world.health().state == HealthState.UNHEALTHY and world.health().reason is None


# ---- episode: 장애 한 건에 판정 하나 ----


def test_reason_change_within_episode_does_not_emit_again(world):
    world.prove()
    world.tick(obs())
    world.clock.now += 90
    first = world.tick(obs())
    assert first[0].reason == FailureReason.HEARTBEAT_LOST
    assert world.run(obs(present=False), 65) == []  # 도메인까지 사라졌지만 같은 장애
    assert world.health().reason == FailureReason.DOMAIN_ABSENT and world.health().state == HealthState.UNHEALTHY
    assert world.run(obs(state=VmState.crashed, domid=9), 10) == []  # crashed 로 바뀌어도 마찬가지
    assert world.health().reason == FailureReason.DOMAIN_CRASHED


# ---- 의도 ----


@pytest.mark.parametrize(
    "observation",
    [
        obs(present=False, desired="stopped"),
        obs(state=VmState.crashed, desired="stopped"),
        obs(present=False, desired="unknown"),
        obs(present=False, applicable=False),
        obs(present=False, lifecycle="RETIRED"),
    ],
)
def test_not_eligible_intent_is_never_judged(world, observation):
    world.tick(observation)
    assert world.run(observation, 300) == []
    assert world.health().state in (HealthState.STOPPED, HealthState.UNHEALTHY)


def test_user_stop_during_episode_clears_it(world):
    world.tick(obs(state=VmState.crashed))
    assert world.health().reason == FailureReason.DOMAIN_CRASHED
    assert world.tick(obs(present=False, desired="stopped")) == []
    assert world.health().reason is None and world.health().state == HealthState.STOPPED


# ---- 판정 직전 재확인 (confirm): 의도와 실행 상태를 다시 읽고 다시 판정 ----


def test_confirm_blocks_judgment_when_intent_changed_or_vm_gone():
    answers = {}

    def confirm(vm_id):
        return answers.get(vm_id)

    world = World(confirm)
    assert world.tick(obs(state=VmState.crashed)) == []  # confirm → None (등록 없음) → 판정 없음
    assert world.watcher.health(VM).state == HealthState.STOPPED and world.watcher.health(VM).reason is None  # 건강도 재확인 결과로
    answers[VM] = obs(state=VmState.crashed, desired="stopped")  # 사용자가 그 사이 stop
    assert world.tick(obs(state=VmState.crashed)) == [] and world.watcher.health(VM).reason is None
    answers[VM] = obs(state=VmState.crashed, revision=7)
    judged = world.tick(obs(state=VmState.crashed))
    assert len(judged) == 1 and judged[0].intent_revision == 7  # 재확인한 값으로 판정


def test_confirm_reevaluates_failure_with_fresh_observation():
    """관측 사이에 VM 이 시작·복구됐으면 판정하지 않고, 사유가 달라졌으면 재확인한 사유로 판정한다."""
    answers = {}
    world = World(lambda vm_id: answers.get(vm_id))
    world.tick(obs(present=False))
    world.clock.now += 60
    answers[VM] = obs(domid=3)  # confirm: 그 사이 시작돼 running
    assert world.tick(obs(present=False)) == [] and world.health().state == HealthState.STARTING  # 재확인 관측으로 건강 계산
    assert world.watcher.open_recovery(VM) is None
    assert world.run(obs(present=False), 55) == []  # 재확인으로 도메인을 봤으니 부재 유예도 그때부터 다시 센다

    answers2 = {}
    world2 = World(lambda vm_id: answers2.get(vm_id))
    world2.tick(obs(present=False))
    world2.clock.now += 60
    answers2[VM] = obs(state=VmState.crashed, domid=3)  # 주기 관측은 absent 였지만 재확인은 crashed
    judged = world2.tick(obs(present=False))
    assert [(j.reason, j.domid, j.xen_state) for j in judged] == [(FailureReason.DOMAIN_CRASHED, 3, VmState.crashed)]
    assert world2.health().reason == FailureReason.DOMAIN_CRASHED and world2.health().xen_state == "crashed"


def test_confirm_failure_retries_next_cycle_without_opening_episode():
    calls = []

    def confirm(vm_id):
        calls.append(vm_id)
        if len(calls) == 1:
            raise PlatformUnavailable("busy")
        return obs(state=VmState.crashed)

    world = World(confirm)
    assert world.tick(obs(state=VmState.crashed)) == []
    judged = world.tick(obs(state=VmState.crashed))
    assert [j.reason for j in judged] == [FailureReason.DOMAIN_CRASHED] and calls == [VM, VM]
    assert world.tick(obs(state=VmState.crashed)) == [] and calls == [VM, VM]  # episode 안에서는 다시 확인하지 않는다


# ---- 건강 스냅샷 ----


def test_health_manager_seq_increments_only_on_change(world):
    world.tick(obs())
    assert world.health().state == HealthState.STARTING and world.health().manager_seq == 1
    world.beat()
    world.tick(obs())
    assert world.health().state == HealthState.HEALTHY and world.health().manager_seq == 2
    world.clock.now += 5
    world.tick(obs())
    assert world.health().manager_seq == 2  # 내용 같음
    world.clock.now += 100
    world.tick(obs())
    assert world.health().state == HealthState.UNHEALTHY and world.health().manager_seq == 3
    assert world.health().payload(lease_s=45)["reason"] == "HEARTBEAT_LOST" and world.health().payload(lease_s=45)["state"] == "UNHEALTHY"


def test_unregistered_vm_is_forgotten(world):
    world.tick(obs(), obs(ZEPHYR, kind="zephyr", domid=2))
    world.beat()
    world.app_beat()
    world.tick(obs(ZEPHYR, kind="zephyr", domid=2))  # VM 이 목록에서 사라짐(보관)
    assert world.health() is None and world.table.last(VM) is None and len(world.watcher.healths()) == 1


# ---- 설정 ----


def test_settings_validation_and_env():
    assert RecoverySettings.from_env({}).heartbeat_lost_s == 30 and RecoverySettings.from_env({}).intent_refresh_s == 60
    s = RecoverySettings.from_env({"XENOVA_RECOVERY_HEARTBEAT_LOST": "15", "XENOVA_RECOVERY_BOOT_GRACE": "20", "XENOVA_RECOVERY_APP_HEARTBEAT_LOST": "3", "XENOVA_RECOVERY_ZEPHYR_BOOT_GRACE": "3", "XENOVA_RECOVERY_BROKER_USERNAME": "dom0-server", "XENOVA_RECOVERY_STATE_DIR": "/tmp/r"})
    assert (s.heartbeat_lost_s, s.boot_grace_s, s.app_heartbeat_lost_s, s.zephyr_boot_grace_s, s.broker_username, s.state_dir) == (15, 20, 3, 3, "dom0-server", "/tmp/r")
    for bad in (
        {"XENOVA_RECOVERY_HEARTBEAT_LOST": "10"},  # 3×주기 미만
        {"XENOVA_RECOVERY_BOOT_GRACE": "10"},  # heartbeat_lost 보다 짧음
        {"XENOVA_RECOVERY_APP_HEARTBEAT_LOST": "2"},
        {"XENOVA_RECOVERY_ZEPHYR_BOOT_GRACE": "5"},
        {"XENOVA_RECOVERY_INTENT_REFRESH": "5"},  # list 를 5초마다 부르지 않는다
        {"XENOVA_RECOVERY_INTERVAL": "0"},
        {"XENOVA_RECOVERY_BROKER_PORT": "70000"},
        {"XENOVA_RECOVERY_LOG_LEVEL": "loud"},
        {"XENOVA_RECOVERY_SOURCE_ID": "dom0/server"},
    ):
        with pytest.raises(ValueError):
            RecoverySettings.from_env(bad)


def test_granted_path_without_any_delivery_never_judges_loss(world):
    """Mosquitto 는 읽기 ACL 에 막힌 구독도 SUBACK 성공으로 답한다. 승인만 보고 '안 온다'를 판정하면 ACL 오설정이 재시작으로 이어진다."""
    world.tick(obs())
    assert world.run(obs(), 200) == []  # 승인은 됐지만 이 채널로 아무것도 안 옴: 에이전트 미기동과 구별 불가 → 판정하지 않는다
    assert world.health().state == HealthState.OBSERVATION_UNAVAILABLE
    world.prove()  # 다른 VM 의 heartbeat 가 오면 경로가 증명된다
    assert [j.reason for j in world.tick(obs())] == [FailureReason.HEARTBEAT_LOST]
