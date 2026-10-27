"""실제 Worker 파일 저장소의 이력 상실·기록 효과 경계. OS 오류만 주입한다."""

import errno
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dom0_manager.recovery.arming import ArmingStore
from dom0_manager.recovery.model import HealthState
from dom0_manager.recovery.state import RecoveryStore
from test_recovery_engine import REC, pending_event, saved_executing
from test_recovery_watcher import World, ZEPHYR, obs


@pytest.fixture(params=[RecoveryStore, ArmingStore], ids=['recovery', 'arming'])
def store_type(request):
    return request.param


def loaded(store):
    value = store.load()
    if isinstance(store, ArmingStore):
        assert value.history_lost == store.history_lost
        return value.armed
    return value


def test_first_absence_valid_and_blocked_schema_compatibility(tmp_path, store_type):
    path = tmp_path / 'state.json'
    store = store_type(path)
    assert loaded(store) == {} and store.history_lost is False
    values = {REC: saved_executing()} if store_type is RecoveryStore else {ZEPHYR: 'manual'}
    if store_type is RecoveryStore:
        values[REC].pending_events = [pending_event()]
    assert store.save(values) is True
    # 유효 본 파일이 원본이다. 남은 사본·미완료 tmp를 읽거나 승격하지 않는다.
    path.with_suffix('.json.corrupt').write_bytes(b'old evidence')
    path.with_suffix('.json.tmp').write_bytes(b'incomplete staging')
    restarted = store_type(path)
    assert loaded(restarted) == values and restarted.history_lost is False
    blocked = json.loads(path.read_text())
    blocked['history_lost'] = True
    path.write_text(json.dumps(blocked))
    restarted = store_type(path)
    assert loaded(restarted) == values and restarted.history_lost is True


@pytest.mark.parametrize('stage', ['link', 'write', 'fsync', 'replace'])
@pytest.mark.parametrize('code', [errno.ENOSPC, errno.EIO])
def test_corrupt_state_survives_every_failed_protection_write(tmp_path, store_type, stage, code):
    path = tmp_path / 'state.json'
    original = b'{broken'
    path.write_bytes(original)
    store = store_type(path)
    target = 'builtins.open' if stage == 'write' else 'os.' + stage
    with patch(target, side_effect=OSError(code, 'injected ' + stage)):
        assert loaded(store) == {} and store.history_lost
        if stage == 'link':
            assert store.save({}) is False  # 격리 실패를 호출자까지 전달한다.
    assert path.read_bytes() == original
    archive = path.with_suffix('.json.corrupt')
    if stage == 'link':
        assert not archive.exists()
    else:
        assert archive.read_bytes() == original
    for _ in range(2):
        restarted = store_type(path)
        assert loaded(restarted) == {} and restarted.history_lost


@pytest.mark.parametrize('suffix', ['.corrupt', '.tmp'])
def test_missing_main_with_residue_is_blocked_without_rewriting_evidence(tmp_path, store_type, suffix):
    path = tmp_path / 'state.json'
    residue = path.with_suffix('.json' + suffix)
    evidence = b'{"version":1,"history_lost":false}'
    residue.write_bytes(evidence)
    for _ in range(2):
        store = store_type(path)
        assert loaded(store) == {} and store.history_lost
        assert store.save({}) is False
        assert not path.exists() and residue.read_bytes() == evidence


def test_existing_archive_is_never_clobbered(tmp_path, store_type):
    path = tmp_path / 'state.json'
    archive = path.with_suffix('.json.corrupt')
    path.write_bytes(b'new corrupt evidence')
    archive.write_bytes(b'earlier corrupt evidence')
    store = store_type(path)
    assert loaded(store) == {} and store.history_lost
    assert store.save({}) is False
    assert path.read_bytes() == b'new corrupt evidence'
    assert archive.read_bytes() == b'earlier corrupt evidence'


@pytest.mark.parametrize('failure', ['read', 'false-missing', 'residue-stat'])
def test_lookup_errors_are_not_initial_absence(tmp_path, store_type, failure):
    path = tmp_path / 'state.json'
    if failure != 'residue-stat':
        assert store_type(path).save({})
        before = path.read_bytes()
    store = store_type(path)
    if failure == 'residue-stat':
        real = Path.lstat
        def stat(p, *args, **kwargs):
            if p == path.with_suffix('.json.corrupt'):
                raise PermissionError(errno.EACCES, 'injected residue lookup')
            return real(p, *args, **kwargs)
        scope = patch.object(Path, 'lstat', stat)
    else:
        error = PermissionError(errno.EACCES, 'injected read') if failure == 'read' else FileNotFoundError()
        scope = patch.object(Path, 'read_text', side_effect=error)
    with scope:
        assert loaded(store) == {} and store.history_lost
    assert store.save({}) is False
    if failure != 'residue-stat':
        assert path.read_bytes() == before


def test_watcher_receives_arming_save_failure(tmp_path):
    store = ArmingStore(tmp_path / 'armed.json')
    world = World(arming=store)
    world.app_beat(app_id='manual')
    with patch('builtins.open', side_effect=OSError(errno.ENOSPC, 'injected save')):
        world.tick(obs(ZEPHYR, kind='zephyr'))
    assert world.watcher.history_lost and store.history_lost
    other = obs(REC, kind='zephyr')
    world.tick(obs(ZEPHYR, kind='zephyr'), other)
    assert world.health(REC).state == HealthState.OBSERVATION_UNAVAILABLE
