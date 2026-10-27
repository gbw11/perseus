"""별도 프로세스와 SIGKILL로 실제 파일 저장·Engine/Watcher 복원을 검증한다."""

import errno
import multiprocessing as mp
import os
from pathlib import Path
import signal
from unittest.mock import patch

import pytest

from dom0_manager.recovery.arming import ArmingStore
from dom0_manager.recovery.model import HealthState
from dom0_manager.recovery.state import RecoveryStore, RecoveryStatus
from test_recovery_engine import REC, judgment, make, pending_event, saved_executing
from test_recovery_watcher import World, ZEPHYR, obs

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX process/OS boundary')


def process(target, *args, killed=False):
    child = mp.get_context('spawn').Process(target=target, args=args)
    child.start()
    child.join(15)
    if child.is_alive():
        child.terminate()
        child.join(2)
        pytest.fail('owned fixture process did not finish')
    assert child.exitcode == (-signal.SIGKILL if killed else 0)


def corrupt_child(path, kind, failure, code):
    store = (RecoveryStore if kind == 'recovery' else ArmingStore)(Path(path))
    target = 'builtins.open' if failure == 'write' else 'os.' + failure
    with patch(target, side_effect=OSError(code, 'injected protection failure')):
        store.load()
    assert store.history_lost


def check_blocked_child(path, kind):
    path = Path(path)
    if kind == 'recovery':
        world = make(path.parent, store=RecoveryStore(path))
        try:
            world.engine.on_judgment(judgment())
            world.tick()
            assert world.store.history_lost
            assert len(world.platform.recover_calls()) == 0
        finally:
            world.engine.shutdown()
    else:
        world = World(arming=ArmingStore(path))
        world.prove('app')
        target = obs(ZEPHYR, kind='zephyr')
        assert world.tick(target) == []
        assert world.run(target, 120) == []
        assert world.watcher.history_lost
        assert world.health(ZEPHYR).state == HealthState.OBSERVATION_UNAVAILABLE


def check_valid_child(path, kind, expected):
    if kind == 'recovery':
        store = RecoveryStore(Path(path))
        assert store.load()[REC].to_json() == expected
    else:
        store = ArmingStore(Path(path))
        assert store.load().armed == {ZEPHYR: 'manual'}
    assert not store.history_lost


@pytest.mark.parametrize('kind', ['recovery', 'arming'])
def test_valid_fresh_process_preserves_original_context(tmp_path, kind):
    path = tmp_path / 'state.json'
    value = saved_executing()
    value.pending_events = [pending_event()]
    if kind == 'recovery':
        assert RecoveryStore(path).save({REC: value})
    else:
        assert ArmingStore(path).save({ZEPHYR: 'manual'})
    process(check_valid_child, str(path), kind, value.to_json())


@pytest.mark.parametrize('kind', ['recovery', 'arming'])
@pytest.mark.parametrize('failure', ['write', 'fsync', 'replace', 'link'])
@pytest.mark.parametrize('code', [errno.ENOSPC, errno.EIO])
def test_protection_failure_and_fresh_process_never_reopens_recovery(tmp_path, kind, failure, code):
    path = tmp_path / ('recoveries.json' if kind == 'recovery' else 'armed.json')
    path.write_bytes(b'{broken')
    process(corrupt_child, str(path), kind, failure, code)
    process(check_blocked_child, str(path), kind)


def protection_cut_child(path, kind, cut):
    path = Path(path)
    store = (RecoveryStore if kind == 'recovery' else ArmingStore)(path)
    real_link, real_replace = os.link, os.replace
    def linked(*args, **kwargs):
        if cut == 'before-link':
            os.kill(os.getpid(), signal.SIGKILL)
        result = real_link(*args, **kwargs)
        if cut == 'after-link':
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    def replaced(*args, **kwargs):
        if cut == 'before-replace':
            os.kill(os.getpid(), signal.SIGKILL)
        result = real_replace(*args, **kwargs)
        if cut == 'after-replace':
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    with patch('os.link', linked), patch('os.replace', replaced):
        store.load()


@pytest.mark.parametrize('kind', ['recovery', 'arming'])
@pytest.mark.parametrize('cut', ['before-link', 'after-link', 'before-replace', 'after-replace'])
def test_killed_protection_keeps_blocking_evidence(tmp_path, kind, cut):
    path = tmp_path / ('recoveries.json' if kind == 'recovery' else 'armed.json')
    path.write_bytes(b'{broken')
    process(protection_cut_child, str(path), kind, cut, killed=True)
    process(check_blocked_child, str(path), kind)


@pytest.mark.parametrize('kind', ['recovery', 'arming'])
@pytest.mark.parametrize('suffix', ['.corrupt', '.tmp'])
def test_previous_implementation_residue_and_restart(tmp_path, kind, suffix):
    path = tmp_path / ('recoveries.json' if kind == 'recovery' else 'armed.json')
    path.with_suffix(path.suffix + suffix).write_bytes(b'{old incomplete evidence')
    process(check_blocked_child, str(path), kind)
    process(check_blocked_child, str(path), kind)


def state_cut_child(path, cut):
    old = RecoveryStore(Path(path)).load()[REC]
    old.status, old.closed_monotonic = RecoveryStatus.COMPLETED, 1000.0
    old.pending_events = [pending_event()]
    real = os.replace
    def replace(*args):
        if cut == 'before':
            os.kill(os.getpid(), signal.SIGKILL)
        real(*args)
        os.kill(os.getpid(), signal.SIGKILL)
    with patch('os.replace', replace):
        RecoveryStore(Path(path)).save({REC: old})


@pytest.mark.parametrize('cut', ['before', 'after'])
def test_state_and_pending_event_remain_one_replace_unit(tmp_path, cut):
    path = tmp_path / 'recoveries.json'
    original = saved_executing()
    assert RecoveryStore(path).save({REC: original})
    process(state_cut_child, str(path), cut, killed=True)
    restored = RecoveryStore(path).load()[REC]
    if cut == 'before':
        assert restored.to_json() == original.to_json()
    else:
        assert restored.status == RecoveryStatus.COMPLETED
        assert restored.pending_events == [pending_event()]
        assert restored.request == original.request
        assert restored.attempt == original.attempt
