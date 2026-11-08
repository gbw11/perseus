"""E4 시험 프로필. 실제 Worker/Watcher/Engine/저장소를 사용하고 Xen·통신 경계만 대체한다."""

import errno
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from dom0_manager.errors import DriverTimeout, PlatformUnavailable
from dom0_manager.models import VmState
from dom0_manager.recovery.config import RecoverySettings
from dom0_manager.recovery.engine import RecoveryEngine, KEEP_CLOSED
from dom0_manager.recovery.events import EventLog
from dom0_manager.recovery.heartbeats import HeartbeatTable
from dom0_manager.recovery.model import VmObservation, HealthState
from dom0_manager.recovery.mqtt import LinkState
from dom0_manager.recovery.policy import RecoveryPolicy
from dom0_manager.recovery.state import RecoveryStatus, RecoveryStore
from dom0_manager.recovery.worker import RecoveryWorker
from dom0_manager.recovery.watcher import Watcher
from test_recovery_engine import FakePlatform, HOST, VM, VM2, view, wait_until


# 짧은 국소 회귀용 값이다. 운영 env·unit·배포 기본값에는 공급하지 않는다.
PROFILE = RecoveryPolicy(max_attempts=2, backoff_s=(1,), stable_s=5, vm_max_attempts=3)


class Rig:
    def __init__(self, root, policy=PROFILE, *, inline=True, boot_reader=None):
        self.root, self.policy, self.inline = root, policy, inline
        self.clock, self.seq, self.domid, self.host = [1000.0], 0, 5, HOST
        self.boot_reader = boot_reader or (lambda: self.host)
        self.observations = {}
        self.fail_observation = False
        self.platform = FakePlatform(root/'recoveries.json')
        self.events = EventLog(root/'events.jsonl')
        self.start()

    def start(self):
        self.beats = HeartbeatTable()
        settings = RecoverySettings(interval_s=1, health_refresh_s=3)
        self.watcher = Watcher(settings, self.beats, clock=lambda: self.clock[0])
        self.store = RecoveryStore(self.root/'recoveries.json')
        self.engine = RecoveryEngine(self.policy, self.platform, self.watcher, self.store, inline=self.inline,
                                     clock=lambda: self.clock[0], boot_id=self.boot_reader, on_event=self.events.append,
                                     drive_vm_ids=())
        def observe():
            if self.fail_observation:
                raise PlatformUnavailable('fixture observation unavailable')
            return list(self.observations.values())
        self.worker = RecoveryWorker(settings, observe, self.watcher, link=LinkState.up,
                                     engine=self.engine, clock=lambda: self.clock[0])

    def restart(self, *, host=None, uptime=None):
        self.worker.shutdown()
        if host is not None:
            self.host = host
        if uptime is not None:
            self.clock[0] = uptime
        self.start()

    def observation(self, vm=VM):
        return self.observations.get(vm) or VmObservation(vm, 'fixture', 'linux', 'running', True, 3,
            'REGISTERED', True, VmState.running, 5, execution_id=str(UUID(int=100)))

    def tick(self, seconds=1):
        self.clock[0] += seconds
        for vm, obs in self.observations.items():
            self.platform.views[vm] = {**view(vm), 'domid': obs.domid, 'execution_id': obs.execution_id,
                                      'intent_revision': obs.intent_revision, 'desired_state': obs.desired_state,
                                      'xen_state': obs.xen_state.value, 'host_boot_id': self.host}
        return self.worker.run_once()

    def crash(self, vm=VM):
        self.observations[vm] = replace(self.observation(vm), xen_state=VmState.crashed)
        self.platform.results.append({'attempt': {}, 'original_result': {'execution_id': str(uuid4())}})
        return self.tick()

    def healthy(self, vm=VM, *, beat=True):
        obs = self.observation(vm)
        completed = [r for r in self.engine.records() if r.vm_id == vm and r.execution_id]
        if completed and completed[-1].execution_id != obs.execution_id:
            self.domid += 1
            obs = replace(obs, execution_id=completed[-1].execution_id, domid=self.domid)
        self.observations[vm] = replace(obs, xen_state=VmState.running)
        if beat:
            self.seq += 1
            self.beats.record(vm, 'guest-'+str(obs.domid), self.seq, self.clock[0]+1)
        return self.tick()

    def used(self, vm=VM):
        return self.engine.budget_status(vm)['used']


def test_repeated_episodes_share_vm_budget_and_recover_without_stopping_other_vms(tmp_path):
    r = Rig(tmp_path)
    try:
        for used in range(1, 4):
            r.crash()
            assert r.used() == used
            r.healthy()  # 잠깐 정상: episode는 닫아도 예산은 갱신하지 않는다.
        r.crash()
        held = r.engine.open_for(VM)
        assert held.attempt == 0 and held.request is None
        assert len(r.platform.recover_calls()) == 3
        assert r.engine.budget_status(VM)['hold_reason'] == 'RECOVERY_BUDGET_EXHAUSTED'
        r.tick(100)
        assert len(r.platform.recover_calls()) == 3  # 단순 시간 경과는 재개 조건이 아니다.
        r.crash(VM2)
        assert len(r.platform.recover_calls()) == 4 and r.used(VM2) == 1
        for _ in range(6):
            r.healthy()
        assert r.used() == 0 and r.engine.open_for(VM) is None
        assert r.watcher.health(VM).state == HealthState.HEALTHY
        r.crash()
        assert len(r.platform.recover_calls()) == 5 and r.used() == 1
    finally:
        r.worker.shutdown()


def test_original_attempt_replay_and_worker_restart_charge_only_once(tmp_path):
    r = Rig(tmp_path)
    try:
        r.platform.results = [DriverTimeout('fixture response lost')]
        r.crash()
        original = r.engine.open_for(VM)
        spec = dict(original.request)
        assert original.status == RecoveryStatus.EXECUTING and r.used() == 1
        r.platform.journal[spec['recovery_attempt_id']] = {
            'phase': 'completed', 'result': {'execution_id': str(uuid4())}}
        r.restart()
        r.tick()
        assert r.used() == 1 and len(r.platform.recover_calls()) == 1
        record = r.engine.records()[0]
        assert record.status == RecoveryStatus.COMPLETED and record.request == spec
        assert record.attempt == 1 and str(record.recovery_attempt_id) == spec['recovery_attempt_id']
        assert record.request['deadline_monotonic_ms'] == spec['deadline_monotonic_ms']
    finally:
        r.worker.shutdown()


def test_cold_process_restores_usage_without_new_platform_effect(tmp_path):
    r = Rig(tmp_path)
    try:
        r.crash()
        before = r.store.path.read_bytes()
        script = '''
import json,sys
from pathlib import Path
from uuid import UUID
from dom0_manager.recovery.engine import RecoveryEngine
from dom0_manager.recovery.config import RecoverySettings
from dom0_manager.recovery.policy import RecoveryPolicy
from dom0_manager.recovery.state import RecoveryStore
from dom0_manager.recovery.heartbeats import HeartbeatTable
from dom0_manager.recovery.watcher import Watcher
from dom0_manager.recovery.worker import RecoveryWorker
from dom0_manager.recovery.mqtt import LinkState
class NoEffects:
 def __getattr__(self,name):raise AssertionError(name)
s=RecoverySettings();w=Watcher(s,HeartbeatTable())
e=RecoveryEngine(RecoveryPolicy(max_attempts=2,stable_s=5,vm_max_attempts=3),NoEffects(),w,RecoveryStore(Path(sys.argv[1])),boot_id=lambda:sys.argv[3],inline=True)
worker=RecoveryWorker(s,lambda:[],w,link=LinkState.down,engine=e)
worker.run_once();print(json.dumps(e.budget_status(UUID(sys.argv[2]))));worker.shutdown()
'''
        completed = subprocess.run([sys.executable, '-B', '-c', script, str(r.store.path), str(VM), HOST],
                                   capture_output=True, text=True, timeout=15, check=True)
        assert json.loads(completed.stdout)['used'] == 1
        assert r.store.path.read_bytes() == before
    finally:
        r.worker.shutdown()


def test_cached_heartbeat_and_observation_failure_cannot_renew_budget(tmp_path):
    r = Rig(tmp_path)
    try:
        r.crash(); r.healthy()
        for _ in range(7):
            r.healthy(beat=False)
        assert r.used() == 1 and r.engine.budget_status(VM)['stable_for_s'] == 0
        r.fail_observation = True
        r.tick()
        r.fail_observation = False
        for _ in range(5):
            r.healthy()
        assert r.used() == 1  # 누락 구간 앞의 시간은 이어 붙이지 않는다.
        r.healthy()
        assert r.used() == 0
    finally:
        r.worker.shutdown()


@pytest.mark.parametrize('boundary', ['intent', 'execution', 'worker', 'host'])
def test_stability_restarts_at_context_boundaries_but_usage_survives(tmp_path, boundary):
    r = Rig(tmp_path)
    try:
        r.crash()
        for _ in range(4):
            r.healthy()
        assert r.used() == 1
        if boundary == 'intent':
            r.observations[VM] = replace(r.observations[VM], intent_revision=4)
        elif boundary == 'execution':
            r.observations[VM] = replace(r.observations[VM], domid=90)
        else:
            r.restart(host=str(UUID(int=99)) if boundary == 'host' else None,
                      uptime=1 if boundary == 'host' else None)
        for _ in range(5):
            r.healthy()
        assert r.used() == 1
        r.healthy()
        assert r.used() == 0
    finally:
        r.worker.shutdown()


def test_attempt_and_usage_are_saved_together_and_failed_save_cannot_execute(tmp_path, monkeypatch):
    r = Rig(tmp_path)
    save = r.store.save
    def fail_attempt(records):
        if any(record.status == RecoveryStatus.EXECUTING for record in records.values()):
            return False
        return save(records)
    monkeypatch.setattr(r.store, 'save', fail_attempt)
    try:
        r.crash()
        assert not r.platform.recover_calls() and r.used() == 0
        persisted = json.loads(r.store.path.read_text())
        assert persisted['vm_budget']['used'][str(VM)] == 0
        monkeypatch.setattr(r.store, 'save', save)
        r.tick(PROFILE.transient_retry_s)
        assert len(r.platform.recover_calls()) == 1 and r.used() == 1
        persisted = json.loads(r.store.path.read_text())
        assert persisted['vm_budget']['used'][str(VM)] == 1
        assert persisted['recoveries'][0]['attempt'] == 1
    finally:
        r.worker.shutdown()


def test_pruning_closed_records_does_not_erase_vm_usage(tmp_path):
    r = Rig(tmp_path, replace(PROFILE, vm_max_attempts=KEEP_CLOSED+4))
    try:
        for _ in range(KEEP_CLOSED+4):
            r.crash(); r.healthy()
        assert len(r.engine.records()) == KEEP_CLOSED
        r.restart()
        r.crash()
        assert r.used() == KEEP_CLOSED+4 and len(r.platform.recover_calls()) == KEEP_CLOSED+4
        assert r.engine.budget_status(VM)['hold_reason'] == 'RECOVERY_BUDGET_EXHAUSTED'
    finally:
        r.worker.shutdown()


def test_legacy_usage_is_unknown_until_stable_and_original_records_are_preserved(tmp_path):
    r = Rig(tmp_path)
    try:
        r.crash()
        body = json.loads(r.store.path.read_text())
        old_record = body['recoveries'][0]
        body['version'] = 1; del body['vm_budget']
        r.store.path.write_text(json.dumps(body))
        r.restart()
        assert r.used() is None
        r.crash()
        assert len(r.platform.recover_calls()) == 1
        assert r.engine.budget_status(VM)['hold_reason'] == 'RECOVERY_BUDGET_UNKNOWN'
        assert r.engine.records()[0].to_json() == old_record
        for _ in range(6):
            r.healthy()
        assert r.used() == 0
        r.crash()
        assert len(r.platform.recover_calls()) == 2 and r.used() == 1
    finally:
        r.worker.shutdown()


@pytest.mark.parametrize('bad', [True, -1, '1', 1.5])
def test_corrupt_budget_blocks_recovery_and_preserves_original_evidence(tmp_path, bad):
    r = Rig(tmp_path)
    try:
        r.crash()
        body = json.loads(r.store.path.read_text())
        body['vm_budget']['used'][str(VM)] = bad
        r.store.path.write_text(json.dumps(body))
        original = r.store.path.read_bytes()
        r.restart()
        r.crash()
        assert len(r.platform.recover_calls()) == 1 and r.store.history_lost
        assert r.store.path.with_suffix('.json.corrupt').read_bytes() == original
    finally:
        r.worker.shutdown()


def test_slow_optional_status_sink_cannot_block_budget_and_normal_recovery(tmp_path, monkeypatch):
    from dom0_manager.recovery.status import StatusReporter, DELIVERING
    r = Rig(tmp_path)
    reporter = StatusReporter(tmp_path, policy='restart', broker_configured=True, drive_vm_ids=(), gateway_client=None,
        interval_s=1, connected=lambda:True, links=lambda now:dict(agent=DELIVERING, app=DELIVERING),
        publishers=lambda:dict(agent=True, app=False), intent_known=lambda:True, internal=r.engine.hold_reasons,
        clock=lambda:r.clock[0], boot_id=lambda:HOST)
    r.worker._status = reporter
    entered, release = threading.Event(), threading.Event()
    original = os.replace
    def slow(source, target):
        if Path(target) == reporter.path and not entered.is_set():
            entered.set(); assert release.wait(5)
        return original(source, target)
    monkeypatch.setattr(os, 'replace', slow)
    try:
        r.crash(); assert entered.wait(1)
        for _ in range(26):
            r.healthy()
        assert r.used() == 0 and r.worker.ticks == 27
        assert reporter.sink_snapshot()['pending'] == 1
        release.set()
        wait_until(lambda: reporter.sink_snapshot()['written'] == 2)
        r.crash()
        assert r.used() == 1 and len(r.platform.recover_calls()) == 2
    finally:
        release.set(); r.worker.shutdown()


def test_stable_renewal_is_saved_once_and_not_on_each_healthy_tick(tmp_path, monkeypatch):
    r = Rig(tmp_path)
    r.crash()
    calls = []
    platform_calls = list(r.platform.calls)
    original = os.fsync
    def fsync(fd):
        calls.append(fd)
        return original(fd)
    monkeypatch.setattr(os, 'fsync', fsync)
    try:
        for _ in range(26):
            r.healthy()
        assert r.used() == 0 and len(calls) == 1
        assert r.platform.calls == platform_calls  # 안정 설명을 위한 추가 Manager/Xen 관측은 없다.
    finally:
        r.worker.shutdown()


def test_healthy_observation_jitter_uses_existing_lease_not_publish_interval(tmp_path):
    r = Rig(tmp_path)
    r.watcher._settings = replace(r.watcher._settings, health_refresh_s=1)
    try:
        r.crash()
        for _ in range(6):
            r.clock[0] += .01
            r.healthy()
        assert r.used() == 0  # 최소 재발행 주기와 같은 감시 주기도 정상 지터 때문에 영구 보류되지 않는다.
    finally:
        r.worker.shutdown()


def test_stability_uses_confirmed_intent_instead_of_preconfirm_observation(tmp_path):
    r = Rig(tmp_path)
    try:
        r.crash()
        for _ in range(4):
            r.healthy()
        assert r.used() == 1
        def confirm(vm_id):
            current = replace(r.observations[vm_id], xen_state=VmState.running, intent_revision=4)
            r.observations[vm_id] = current
            return current
        r.watcher._confirm = confirm
        r.crash()
        assert r.watcher.stability_samples()[VM].context[2] == 4
        assert r.used() == 1 and r.engine.budget_status(VM)['stable_for_s'] == 0
        for _ in range(4):
            r.healthy()
        assert r.used() == 1
        r.healthy()
        assert r.used() == 0
    finally:
        r.worker.shutdown()


def test_failed_renewal_does_not_claim_a_durable_budget_reset(tmp_path, monkeypatch):
    r = Rig(tmp_path)
    try:
        r.crash()
        for _ in range(5):
            r.healthy()
        save = r.store.save
        monkeypatch.setattr(r.store, 'save', lambda records: False)
        r.healthy()
        assert r.used() == 1 and not r.engine.budget_status(VM)['durable']
        assert json.loads(r.store.path.read_text())['vm_budget']['used'][str(VM)] == 1
        monkeypatch.setattr(r.store, 'save', save)
        r.healthy()
        assert r.used() == 0 and r.engine.budget_status(VM)['durable']
        r.crash()
        assert len(r.platform.recover_calls()) == 2 and r.used() == 1
    finally:
        r.worker.shutdown()


def test_actual_attempt_fsync_eio_preserves_usage_and_recovers(tmp_path, monkeypatch):
    r = Rig(tmp_path)
    original, failing = os.fsync, [True]
    def fsync(fd):
        path = Path(os.readlink('/proc/self/fd/'+str(fd)))
        if failing[0] and path.name == 'recoveries.json.tmp':
            body = json.loads(path.read_text())
            if any(row['status'] == 'EXECUTING' for row in body['recoveries']):
                raise OSError(errno.EIO, 'fixture required fsync failure')
        return original(fd)
    monkeypatch.setattr(os, 'fsync', fsync)
    try:
        r.crash()
        assert not r.platform.recover_calls() and r.used() == 0
        assert json.loads(r.store.path.read_text())['vm_budget']['used'][str(VM)] == 0
        r.restart()
        assert r.used() == 0 and not r.store.history_lost  # 유효한 본 파일을 읽고 미완료 tmp를 승격하지 않는다.
        failing[0] = False
        r.tick(PROFILE.transient_retry_s)
        assert r.used() == 1 and len(r.platform.recover_calls()) == 1
    finally:
        r.worker.shutdown()


def test_exhaustion_keeps_gateway_protection_and_user_stop_can_settle_it(tmp_path):
    from test_recovery_engine import make, bound_gateway, zephyr_judgment, EXEC, APP
    store = RecoveryStore(tmp_path/'recoveries.json')
    store.budget_tracking = True
    store.vm_budget_used[VM] = PROFILE.vm_max_attempts
    assert store.save({})
    w = make(tmp_path, policy=PROFILE, store=store, drive_vm_ids=[VM], boot_id=lambda:HOST)
    gateway = bound_gateway(w)
    try:
        w.watcher.failing(VM, APP, execution_id=EXEC)
        record = w.engine.on_judgment(zephyr_judgment())
        w.tick()
        key = record.protection['request_id']
        assert gateway.phase(key) == 'PROTECTED' and not w.platform.recover_calls()
        assert record.attempt == 0 and w.engine.budget_status(VM)['used'] == PROFILE.vm_max_attempts
        w.advance(PROFILE.transient_retry_s); w.tick()
        assert gateway.phase(key) == 'PROTECTED' and gateway.count('submit') == 1
        w.watcher.set(VM, HealthState.STOPPED, desired='stopped', execution_id=EXEC)
        w.tick()
        assert record.status == RecoveryStatus.CANCELLED and gateway.phase(key) == 'CANCELLED'
        assert w.engine.budget_status(VM)['used'] == PROFILE.vm_max_attempts
    finally:
        w.engine.shutdown()


def test_context_read_failure_is_held_then_recovers_with_original_cause(tmp_path):
    error = OSError(errno.EIO, 'fixture boot read failure')
    current = [error]
    def read():
        if isinstance(current[0], Exception):
            raise current[0]
        return current[0]
    r = Rig(tmp_path, boot_reader=read)
    try:
        r.crash()
        assert not r.platform.recover_calls() and r.used() == 0
        assert r.engine.budget_status(VM)['hold_reason'] == 'RECOVERY_BUDGET_CONTEXT_UNAVAILABLE'
        assert r.engine._budget_boot_first_error is error
        current[0] = HOST
        r.tick(PROFILE.transient_retry_s)
        assert r.used() == 1 and len(r.platform.recover_calls()) == 1
        assert r.engine._budget_boot_first_error is error
    finally:
        r.worker.shutdown()


def test_unresolved_inflight_attempt_does_not_receive_a_stability_refund(tmp_path):
    r = Rig(tmp_path, inline=False)
    release = threading.Event()
    r.platform.block = release
    try:
        r.crash(); assert r.platform.entered.wait(2)
        for _ in range(10):
            r.healthy()
        assert r.used() == 1 and r.engine.open_for(VM).status == RecoveryStatus.EXECUTING
        release.set()
        wait_until(lambda: all(future.done() for future in r.engine._futures.values()))
        r.engine.collect(r.clock[0])
        assert r.used() == 1 and r.engine.open_for(VM) is None
        for _ in range(6):
            r.healthy()
        assert r.used() == 0 and len(r.platform.recover_calls()) == 1
    finally:
        release.set(); r.worker.shutdown()


def test_qualified_warm_standby_renews_without_synthetic_app_heartbeat_or_work(tmp_path):
    from test_warm_liveness import setup, report
    from test_recovery_watcher import ZEPHYR, SETTINGS
    w, boot, ids, observation = setup()
    observation = replace(observation, execution_id=str(uuid4()))
    store = RecoveryStore(tmp_path/'recoveries.json')
    store.budget_tracking = True
    store.vm_budget_used[ZEPHYR] = 1  # 유효한 이전 시도 누계만 fixture로 공급한다.
    assert store.save({})
    platform = FakePlatform(store.path)
    engine = RecoveryEngine(PROFILE, platform, w.watcher, store, clock=w.clock, boot_id=lambda:HOST, inline=True)
    worker = RecoveryWorker(SETTINGS, lambda:[observation], w.watcher, link=LinkState.up, engine=engine, clock=w.clock)
    try:
        for check in range(1, 8):
            w.clock.now += 1
            for app in ids:
                assert report(w, boot, ids, app, check)
            worker.run_once()
        assert engine.budget_status(ZEPHYR)['used'] == 0
        assert w.apps.last(ZEPHYR) is None and not platform.calls
        assert all(value.progress == 0 for value in w.apps.supervision_pair(ZEPHYR, boot, ids))
    finally:
        worker.shutdown()


def test_duplicate_budget_json_fields_are_corruption_not_a_reset(tmp_path):
    path = tmp_path/'recoveries.json'
    raw = '{"version":2,"history_lost":false,"recoveries":[],"vm_budget":{"baseline_known":true,"used":{"'+str(VM)+'":3,"'+str(VM)+'":0}}}'
    path.write_text(raw)
    store = RecoveryStore(path)
    assert store.load() == {} and store.history_lost
    assert path.with_suffix('.json.corrupt').read_text() == raw


def test_legacy_original_attempt_replay_is_not_blocked_by_unknown_usage(tmp_path):
    r = Rig(tmp_path)
    try:
        r.platform.results = [DriverTimeout('fixture response lost')]
        r.crash()
        spec = dict(r.engine.open_for(VM).request)
        body = json.loads(r.store.path.read_text())
        body['version'] = 1; del body['vm_budget']
        r.store.path.write_text(json.dumps(body))
        r.platform.journal[spec['recovery_attempt_id']] = {'phase':'completed', 'result':{'execution_id':str(uuid4())}}
        r.restart(); r.tick()
        assert r.used() is None and len(r.platform.recover_calls()) == 1
        assert r.engine.records()[0].status == RecoveryStatus.COMPLETED
        assert r.engine.records()[0].request == spec
    finally:
        r.worker.shutdown()


def test_disabling_limit_does_not_erase_saved_usage_or_write_an_old_schema(tmp_path):
    r = Rig(tmp_path)
    try:
        r.crash()
        r.policy = RecoveryPolicy(max_attempts=2)
        r.restart(); r.crash()
        assert r.used() == 2 and not r.engine.budget_status(VM)['configured']
        assert json.loads(r.store.path.read_text())['version'] == 2
        r.policy = PROFILE
        r.restart()
        assert r.used() == 2
    finally:
        r.worker.shutdown()


def test_profile_is_explicit_and_half_configuration_is_rejected():
    assert not RecoveryPolicy.from_env({}).budget_enabled
    assert RecoveryPolicy.from_env({'XENOVA_RECOVERY_STABLE_S':'5','XENOVA_RECOVERY_VM_MAX_ATTEMPTS':'3'}).budget_enabled
    for env in ({'XENOVA_RECOVERY_STABLE_S':'5'}, {'XENOVA_RECOVERY_VM_MAX_ATTEMPTS':'3'},
                {'XENOVA_RECOVERY_STABLE_S':'','XENOVA_RECOVERY_VM_MAX_ATTEMPTS':'3'}):
        with pytest.raises(ValueError):
            RecoveryPolicy.from_env(env)


@pytest.mark.parametrize('stable,limit', [(True,3), (0,3), (-1,3), (float('nan'),3), (float('inf'),3),
                                         (5,True), (5,0), (5,1), (5,3.0), (None,3), (5,None)])
def test_profile_rejects_invalid_explicit_values(stable, limit):
    with pytest.raises(ValueError):
        RecoveryPolicy(max_attempts=2, stable_s=stable, vm_max_attempts=limit)
