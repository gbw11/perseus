"""실제 control 원장 전체 읽기·경로 안전·중복 key와 실패 복귀를 작은 fixture로 확인한다."""
import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import first_install_fixture  # fixed local module paths; no board operation
import test_xenova_recovery_foundation as foundation
import xenova_execution as execution
from xenova_runtime import PlatformError


VM = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def store(home, number, key='original-key', *, identity=VM):
    folder = home/'control-requests'
    folder.mkdir(exist_ok=True)
    operation = f'{number:08x}-1111-4111-8111-111111111111'
    request = dict(vm_id=identity, action='stop', force=False)
    value = dict(schema_version='1.0', operation_id=operation, vm_id=identity,
                 idempotency_key=key, request=request, request_hash=execution.fingerprint(request),
                 phase='completed', deadline_monotonic_ms=90000,
                 result=dict(accepted=True), error=None)
    path = folder/(operation+'.json')
    path.write_text(json.dumps(value), encoding='utf8')
    return path, value


@pytest.mark.parametrize('identity', [VM, OTHER])
def test_duplicate_nonnull_key_is_not_a_unique_original_operation(tmp_path, identity):
    first, _ = store(tmp_path, 1)
    second, _ = store(tmp_path, 2, identity=identity)
    before = (first.read_bytes(), second.read_bytes())
    with pytest.raises(execution.ExecutionError) as caught:
        execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'
    assert caught.value.error['message'] == 'duplicate control request key'
    assert before == (first.read_bytes(), second.read_bytes())


@pytest.mark.parametrize('keys', [(None, None), ('first-key', 'second-key'), (None, 'second-key')])
def test_null_keys_and_distinct_keys_preserve_complete_read(tmp_path, keys):
    store(tmp_path, 1, keys[0])
    store(tmp_path, 2, keys[1])
    assert [row['idempotency_key'] for row in execution.read_requests(tmp_path)] == list(keys)


def test_builtin_scan_pins_parent_and_validates_each_open_record(tmp_path):
    paths = [store(tmp_path, i, f'key-{i}')[0] for i in range(1, 5)]
    with patch.object(execution.catalog, 'plain', wraps=execution.catalog.plain) as checks, \
            patch.object(execution, '_read_request_at', wraps=execution._read_request_at) as records:
        rows = execution.read_requests(tmp_path)
    assert len(rows) == len(paths)
    assert [(call.args[0], call.kwargs) for call in checks.call_args_list] == [
        (tmp_path/'control-requests', dict(directory=True))] * 2
    assert [call.args[1] for call in records.call_args_list] == [path.name for path in paths]
    assert len({call.args[0] for call in records.call_args_list}) == 1


def test_first_valid_match_cannot_hide_later_damage(tmp_path):
    store(tmp_path, 1)
    damaged, _ = store(tmp_path, 2, 'other-key')
    damaged.write_text('{incomplete', encoding='utf8')
    with pytest.raises(execution.ExecutionError) as caught:
        execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'


@pytest.mark.parametrize('kind', ['file-link', 'parent-link', 'hardlink', 'fifo', 'directory'])
def test_unsafe_file_or_ancestor_is_refused_before_text_read(tmp_path, kind):
    path, _ = store(tmp_path, 1)
    if kind == 'file-link':
        original = tmp_path/'actual-record'
        path.rename(original)
        path.symlink_to(original)
    elif kind == 'parent-link':
        original = tmp_path/'actual-folder'
        path.parent.rename(original)
        path.parent.symlink_to(original, target_is_directory=True)
    elif kind == 'hardlink':
        os.link(path, tmp_path/'shared-record')
    else:
        path.unlink()
        if kind == 'fifo':
            os.mkfifo(path)
        else:
            path.mkdir()
    with patch.object(Path, 'read_text', side_effect=AssertionError('unsafe path was opened')) as reads:
        with pytest.raises(PlatformError):
            execution.read_record(path)
    assert reads.call_count == 0


def test_execution_read_eio_is_not_absence_and_next_complete_scan_recovers(tmp_path):
    path, original = store(tmp_path, 1)
    with patch.object(execution.os, 'fdopen', side_effect=OSError(errno.EIO, 'synthetic read failure')):
        with pytest.raises(execution.ExecutionError) as caught:
            execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'
    assert caught.value.error['details'] == dict(record=path.name)
    assert execution.read_requests(tmp_path) == [original]


def test_enumerated_disappearance_is_not_a_complete_absence_result(tmp_path):
    path, _ = store(tmp_path, 1)
    def vanished(candidate):
        assert candidate == path
        candidate.unlink()
        return execution.read_record(candidate)
    with pytest.raises(execution.ExecutionError) as caught:
        execution.read_requests(tmp_path, reader=vanished)
    assert caught.value.error['code'] == 'OBSERVATION_UNAVAILABLE'
    assert caught.value.error['message'] == 'enumerated control request disappeared'


def test_incomplete_enumeration_cannot_read_or_report_absence(tmp_path):
    path, _ = store(tmp_path, 1)
    class Entries:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def __iter__(self):
            yield type('Entry', (), dict(name=path.name))()
            raise OSError(errno.EIO, 'synthetic directory failure')
    with patch.object(execution.os, 'scandir', return_value=Entries()), \
            patch.object(execution, 'read_record', wraps=execution.read_record) as reads:
        with pytest.raises(execution.ExecutionError) as caught:
            execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'OBSERVATION_UNAVAILABLE'
    assert reads.call_count == 0


def test_original_deadline_fails_partial_scan_without_rewriting_any_record(tmp_path):
    paths = [store(tmp_path, i, f'key-{i}')[0] for i in range(1, 4)]
    before = [path.read_bytes() for path in paths]
    now = [0.]
    calls = []
    def slow_reader(path):
        calls.append(path)
        value = execution.read_record(path)
        now[0] += 1
        return value
    with pytest.raises(execution.ExecutionError) as caught:
        execution.read_requests(tmp_path, deadline=1.5, clock=lambda: now[0], reader=slow_reader)
    assert caught.value.error['code'] == 'DEADLINE_EXCEEDED'
    assert len(calls) == 2 and [path.read_bytes() for path in paths] == before
    assert len(execution.read_requests(tmp_path, deadline=5, clock=lambda: now[0])) == 3


@pytest.mark.parametrize('field,value', [('request_hash', 'bad'), ('vm_id', OTHER), ('idempotency_key', '')])
def test_invalid_identity_hash_or_key_still_blocks_the_full_scan(tmp_path, field, value):
    path, row = store(tmp_path, 1)
    # vm_id is validated syntactically by the existing reader; operation_id
    # mismatch is the distinct identity corruption checked here.
    if field == 'vm_id':
        field, value = 'operation_id', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    row[field] = value
    path.write_text(json.dumps(row), encoding='utf8')
    with pytest.raises(execution.ExecutionError) as caught:
        execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'


def test_missing_directory_is_readonly_absence(tmp_path):
    assert execution.read_requests(tmp_path) == []
    assert not (tmp_path/'control-requests').exists()


def default_execution(home):
    platform = SimpleNamespace(data=home, runtime=home/'runtime', boot_id_reader=lambda: VM,
                               clock=lambda: 0.)
    return execution.Execution(platform)


@pytest.mark.parametrize('hook', ['explicit', 'module', 'instance', 'class', 'subclass'])
def test_custom_record_reader_is_called_once_per_file_without_fd_bypass(tmp_path, hook):
    paths = [store(tmp_path, i, f'key-{i}')[0] for i in range(1, 4)]
    original = execution.read_record
    calls = []
    def custom(path):
        calls.append(path)
        return original(path)
    owner = default_execution(tmp_path)
    with patch.object(execution, '_read_request_at', side_effect=AssertionError('reader hook bypassed')):
        if hook == 'explicit':
            rows = execution.read_requests(tmp_path, reader=custom)
        elif hook == 'module':
            with patch.object(execution, 'read_record', custom):
                rows = owner.requests()
        elif hook == 'instance':
            owner.read = custom
            rows = owner.requests()
        elif hook == 'class':
            with patch.object(execution.Execution, 'read', lambda owner, path: custom(path)):
                rows = owner.requests()
        else:
            class Custom(execution.Execution):
                def read(self, path):
                    return custom(path)
            rows = Custom(owner.p).requests()
    assert len(rows) == len(paths) and calls == paths


def test_unmodified_execution_reader_uses_scoped_fd_scan(tmp_path):
    path, value = store(tmp_path, 1)
    with patch.object(execution, '_read_request_at', wraps=execution._read_request_at) as reads:
        assert default_execution(tmp_path).requests() == [value]
    assert reads.call_count == 1 and reads.call_args.args[1] == path.name


@pytest.mark.parametrize('kind', ['file-link', 'hardlink', 'fifo', 'directory'])
def test_builtin_fd_reader_refuses_unsafe_record_before_stream_read(tmp_path, kind):
    path, _ = store(tmp_path, 1)
    if kind == 'file-link':
        original = tmp_path/'actual'
        path.rename(original)
        path.symlink_to(original)
    elif kind == 'hardlink':
        os.link(path, tmp_path/'shared')
    else:
        path.unlink()
        os.mkfifo(path) if kind == 'fifo' else path.mkdir()
    with patch.object(execution.os, 'fdopen', side_effect=AssertionError('unsafe record was read')) as opened:
        with pytest.raises(PlatformError):
            execution.read_requests(tmp_path)
    assert opened.call_count == 0


@pytest.mark.parametrize('kind', ['replacement', 'parent-link', 'ancestor-link', 'missing'])
def test_fd_root_replacement_cannot_return_old_directory_results(tmp_path, kind):
    home = tmp_path/'data'
    home.mkdir()
    path, _ = store(home, 1)
    read = execution._read_request_at
    def changed(fd, candidate):
        value = read(fd, candidate)
        if kind == 'ancestor-link':
            moved = tmp_path/'moved-data'
            home.rename(moved)
            home.symlink_to(moved, target_is_directory=True)
        else:
            moved = home/'moved-requests'
            path.parent.rename(moved)
            if kind == 'replacement':
                path.parent.mkdir()
            elif kind == 'parent-link':
                path.parent.symlink_to(moved, target_is_directory=True)
        return value
    with patch.object(execution, '_read_request_at', changed):
        with pytest.raises(PlatformError) as caught:
            execution.read_requests(home)
    if kind in ('replacement', 'missing'):
        assert caught.value.error['code'] == 'OBSERVATION_UNAVAILABLE'


def test_fd_reader_remains_bound_to_original_parent_during_replacement(tmp_path):
    home = tmp_path/'data'
    home.mkdir()
    first, _ = store(home, 1, 'first-key')
    second, value = store(home, 2, 'second-key')
    original = execution._read_request_at
    consumed = []
    def swapped(fd, name):
        result = original(fd, name)
        consumed.append(result['idempotency_key'])
        if name == first.name:
            first.parent.rename(home/'original')
            store(home, 2, 'unrelated-new-directory')
        return result
    with patch.object(execution, '_read_request_at', swapped):
        with pytest.raises(execution.ExecutionError) as caught:
            execution.read_requests(home)
    assert consumed == ['first-key', value['idempotency_key']]
    assert caught.value.error['code'] == 'OBSERVATION_UNAVAILABLE'


def test_fd_enumerated_disappearance_is_not_absence(tmp_path):
    path, _ = store(tmp_path, 1)
    original = execution._read_request_at
    def missing(fd, candidate):
        assert candidate == path.name
        path.unlink()
        return original(fd, candidate)
    with patch.object(execution, '_read_request_at', missing):
        with pytest.raises(execution.ExecutionError) as caught:
            execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'OBSERVATION_UNAVAILABLE'


@pytest.mark.parametrize('failure', ['success', 'read-eio', 'json', 'schema', 'deadline', 'enumeration'])
def test_scoped_fds_are_closed_on_every_exit(tmp_path, failure):
    path, _ = store(tmp_path, 1)
    if failure in ('json', 'schema'):
        path.write_text('{' if failure == 'json' else '{"schema_version":"wrong"}', encoding='utf8')
    opened, live = [], []
    original_open = execution.os.open
    now = [0.]
    def opening(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        current = 0
        for active in set(opened):
            try:
                os.fstat(active)
            except OSError as error:
                assert error.errno == errno.EBADF
            else:
                current += 1
        live.append(current)
        return fd
    reader = execution._read_request_at
    def delayed(fd, candidate):
        value = reader(fd, candidate)
        now[0] = 2.
        return value
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch.object(execution.os, 'open', opening))
        if failure == 'read-eio':
            stack.enter_context(patch.object(execution.os, 'fdopen', side_effect=OSError(errno.EIO, 'read failed')))
        if failure == 'enumeration':
            stack.enter_context(patch.object(execution.os, 'scandir', side_effect=OSError(errno.EIO, 'enumeration failed')))
        if failure == 'deadline':
            stack.enter_context(patch.object(execution, '_read_request_at', delayed))
        if failure == 'success':
            assert len(execution.read_requests(tmp_path)) == 1
        else:
            with pytest.raises(execution.ExecutionError):
                execution.read_requests(tmp_path, deadline=1., clock=lambda: now[0])
    assert opened and max(live) <= 2
    for fd in set(opened):
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize('target', ['root', 'record'])
def test_fd_permission_denial_keeps_error_not_absence(tmp_path, target):
    path, _ = store(tmp_path, 1)
    original = execution.os.open
    def denied(name, *args, **kwargs):
        if (target == 'root' and name == path.parent or
                target == 'record' and name == path.name and kwargs.get('dir_fd') is not None):
            raise PermissionError(errno.EACCES, 'permission denied')
        return original(name, *args, **kwargs)
    with patch.object(execution.os, 'open', denied):
        with pytest.raises(execution.ExecutionError) as caught:
            execution.read_requests(tmp_path)
    assert caught.value.error['code'] == ('OBSERVATION_UNAVAILABLE' if target == 'root' else 'RECONCILE_REQUIRED')


def test_actual_manager_rejects_duplicate_original_before_any_new_effect():
    f = foundation.FoundationTest('runTest')
    f.setUp()
    try:
        first = f.call('start', key='same-original')
        f.ok(first)
        operation = first['operation_id']
        path = f.p.data/'control-requests'/(operation+'.json')
        original = path.read_bytes()
        value = json.loads(original)
        duplicate = dict(value, operation_id='cccccccc-cccc-4ccc-8ccc-cccccccccccc')
        duplicate_path = path.with_name(duplicate['operation_id']+'.json')
        duplicate_path.write_text(json.dumps(duplicate), encoding='utf8')
        intent = (f.p.data/'control'/(f.vm_id+'.json')).read_bytes()
        run = (f.p.runtime/'executions'/(f.vm_id+'.json')).read_bytes()
        physical = len(f.physical('create'))
        f.fails(f.call('start', key='same-original'), 'RECONCILE_REQUIRED')
        assert len(f.physical('create')) == physical
        assert path.read_bytes() == original
        assert (f.p.data/'control'/(f.vm_id+'.json')).read_bytes() == intent
        assert (f.p.runtime/'executions'/(f.vm_id+'.json')).read_bytes() == run
    finally:
        f.doCleanups()
