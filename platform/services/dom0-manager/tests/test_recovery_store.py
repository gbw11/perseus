"""실제 Worker 파일 저장소의 이력 상실·기록 효과 경계. OS 오류만 주입한다."""

import builtins
import errno
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from dom0_manager.recovery.arming import ArmingStore
from dom0_manager.recovery.events import EventLog
from dom0_manager.recovery.model import HealthState
from dom0_manager.recovery.state import RecoveryStore
from test_recovery_engine import REC, make, judgment, pending_event, saved_executing
from test_recovery_events import event
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


class FailingAppend:
    """파일은 실제로 쓰고 append 경계의 지정 지점만 실패시킨다."""
    def __init__(self, stream, mode):
        self.stream, self.mode = stream, mode

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, payload):
        if self.mode == 'before':
            raise OSError(errno.EIO, 'injected before write')
        cut = len(payload) // 2
        if self.mode == 'utf8':
            cut = payload.index('한'.encode()) + 1
        count = self.stream.write(payload[:cut])
        self.stream.flush()
        if self.mode == 'short':
            return count
        raise OSError(errno.EIO, 'injected partial write')


@pytest.mark.parametrize('mode', ['before', 'partial', 'utf8', 'short', 'fsync'])
def test_append_unconfirmed_effect_same_id_retry_and_gap(tmp_path, mode):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    value = event(1, 'recovery.failed')
    value['error']['message'] = '한글 오류'
    real = builtins.open
    def opened(p, *args, **kwargs):
        stream = real(p, *args, **kwargs)
        return FailingAppend(stream, mode) if Path(p) == path and args[0] == 'a+b' else stream
    scope = patch('os.fsync', side_effect=OSError(errno.EIO, 'injected fsync')) if mode == 'fsync' else patch('builtins.open', opened)
    with scope:
        assert log.append(value) is False
    before = path.read_bytes()
    assert log.append(value) is True
    assert path.read_bytes().startswith(before)  # 부가 기록 원문을 자르지 않는다.
    assert log.events() == [value]
    page = log.after(None, 10)
    assert page['events'] == [value] and page['gap'] == (mode in ('partial', 'utf8', 'short'))
    if mode == 'fsync':
        assert len(path.read_bytes().splitlines()) == 2
    log._compact()
    assert log.after(None, 10)['gap'] == page['gap']


def test_engine_retains_pending_on_append_sync_failure_then_retries_same_content(tmp_path):
    outbox = EventLog(tmp_path / 'recovery-events.jsonl')
    world = make(tmp_path, outbox=outbox)
    world.engine.on_judgment(judgment())
    real = outbox.append
    def unconfirmed(value):
        with patch('os.fsync', side_effect=OSError(errno.EIO, 'injected append fsync')):
            return real(value)
    with patch.object(outbox, 'append', unconfirmed):
        world.tick()
    saved = RecoveryStore(world.store.path).load()[REC]
    original = saved.to_json()
    assert [e['type'] for e in saved.pending_events] == ['recovery.started', 'recovery.completed']
    assert len(world.platform.recover_calls()) == 1
    restarted = make(tmp_path, outbox=EventLog(outbox.path), id_start=500)
    restarted.tick()
    assert restarted.platform.recover_calls() == []
    assert restarted.record().attempt == original['attempt']
    assert restarted.record().request == original['request']
    assert restarted.record().recovery_attempt_id == saved.recovery_attempt_id
    assert restarted.record().pending_events == []
    assert EventLog(outbox.path).events() == original['pending_events']


def test_production_sink_confirms_local_record_not_puback(tmp_path, monkeypatch):
    from dom0_manager import config
    from dom0_manager.recovery import __main__ as entry
    from dom0_manager.recovery.config import RecoverySettings

    class Captured(Exception):
        pass

    captured = {}
    def engine(*args, on_event, **kwargs):
        captured['sink'] = on_event
        raise Captured
    publish = Mock(side_effect=RuntimeError('injected MQTT submission failure'))
    client = SimpleNamespace(start=lambda **kw: False, publish=publish)
    monkeypatch.delenv('XENOVA_RECOVERY_DRIVE_CONFIG', raising=False)
    monkeypatch.setattr(entry.RecoverySettings, 'from_env', lambda: RecoverySettings(state_dir=str(tmp_path)))
    settings = config.Settings.from_env({'XENOVA_DRIVER': 'platform'})
    monkeypatch.setattr(entry.Settings, 'from_env', lambda: settings)
    monkeypatch.setattr(entry, 'acquire', lambda *a: 0)
    monkeypatch.setattr(entry, 'load_manager', lambda *a, **kw: SimpleNamespace(data=tmp_path))
    monkeypatch.setattr(entry, '_xl', lambda *a: None)
    monkeypatch.setattr(entry, 'PlatformObserver', lambda *a, **kw: SimpleNamespace(confirm=lambda *a: None))
    monkeypatch.setattr(entry, 'build_client', lambda *a: client)
    monkeypatch.setattr(entry, 'subscribe_liveness', lambda *a, **kw: None)
    monkeypatch.setattr(entry, 'RecoveryEngine', engine)
    with pytest.raises(Captured):
        entry.main()  # 실제 entrypoint가 Engine에 전달하는 sink를 확인한다.
    sink = captured['sink']
    value = event(1)
    with patch('os.fsync', side_effect=OSError(errno.EIO, 'injected local sync')):
        assert sink(value) is False
    publish.assert_not_called()
    assert sink(value) is True
    assert EventLog(tmp_path / 'recovery-events.jsonl').events() == [value]
    assert publish.call_args.kwargs == {'qos': 1, 'retain': False, 'wait_s': 0}
