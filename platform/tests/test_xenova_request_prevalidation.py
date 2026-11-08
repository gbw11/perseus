"""요청 안의 bounded 사전 검증과 잠금 안의 새 전체 읽기를 실제 파일로 대조한다."""
import copy
import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import first_install_fixture
import test_xenova_recovery_foundation as foundation
from test_xenova_control_request_scan import store, default_execution
import xenova_execution as execution
import xenova_management as management


def original_hint(home):
    owner = default_execution(home)
    return owner, owner._prevalidate_requests(100.)


def test_equal_current_raw_avoids_only_locked_decode_hash_and_consumes_entries(tmp_path):
    expected = [store(tmp_path, i, f'key-{i}')[1] for i in (1, 2)]
    with patch.object(execution.json, 'loads', wraps=json.loads) as decode, \
            patch.object(execution, 'fingerprint', wraps=execution.fingerprint) as digest:
        fds = len(os.listdir('/proc/self/fd'))
        owner, hint = original_hint(tmp_path)
        assert len(os.listdir('/proc/self/fd')) == fds
        assert (decode.call_count, digest.call_count) == (2, 2)
        result = owner._requests_prevalidated(hint, deadline=100.)
        assert result == expected and hint[1] == {}
        assert (decode.call_count, digest.call_count) == (2, 2)
        result[0]['request']['action'] = 'destroy'
        assert owner._requests_prevalidated(hint, deadline=100.) == expected
        assert (decode.call_count, digest.call_count) == (4, 4)


@pytest.mark.parametrize('kind', ['atomic', 'same-size-inplace'])
def test_current_phase_change_is_freshly_decoded(tmp_path, kind):
    path, value = store(tmp_path, 1)
    owner, hint = original_hint(tmp_path)
    changed = {**value, 'phase':'uncertain'}
    body = json.dumps(changed)
    assert len(body.encode()) == path.stat().st_size
    if kind == 'atomic':
        temporary = path.with_suffix('.next')
        temporary.write_text(body)
        temporary.replace(path)
    else:
        path.write_text(body)
    with patch.object(execution, 'fingerprint', wraps=execution.fingerprint) as digest:
        assert owner._requests_prevalidated(hint, deadline=100.) == [changed]
        assert digest.call_count == 1


@pytest.mark.parametrize('kind', ['duplicate', 'invalid-json', 'symlink', 'fifo'])
def test_new_or_changed_bad_record_blocks_full_scan_then_recovers(tmp_path, kind):
    _, first = store(tmp_path, 1, 'first')
    owner, hint = original_hint(tmp_path)
    path, second = store(tmp_path, 2, 'first' if kind == 'duplicate' else 'second')
    if kind == 'invalid-json':
        path.write_text('{')
    elif kind == 'symlink':
        actual = tmp_path/'actual'
        path.rename(actual)
        path.symlink_to(actual)
    elif kind == 'fifo':
        path.unlink()
        os.mkfifo(path)
    with pytest.raises(execution.PlatformError):
        owner._requests_prevalidated(hint, deadline=100.)
    path.unlink()
    _, second = store(tmp_path, 2, 'second')
    assert owner._requests_prevalidated(hint, deadline=100.) == [first, second]


def test_prefetch_cap_is_not_a_limit_or_an_early_unique_result(tmp_path):
    expected = [store(tmp_path, i, f'key-{i}')[1] for i in range(1, 131)]
    with patch.object(execution, 'fingerprint', wraps=execution.fingerprint) as digest:
        owner, hint = original_hint(tmp_path)
        assert len(hint[1]) == 128 and digest.call_count == 128
        assert sum(len(v[0]) for v in hint[1].values()) <= 1048576
        assert owner._requests_prevalidated(hint, deadline=100.) == expected
        assert digest.call_count == 130


def test_unpinned_core_ignores_hint_and_preserves_every_custom_reader_call(tmp_path):
    path, value = store(tmp_path, 1)
    owner, hint = original_hint(tmp_path)
    seen = []
    original = execution.read_record
    def custom(candidate):
        assert isinstance(candidate, Path)
        seen.append(candidate)
        return original(candidate)
    with patch.object(execution, '_request_raw_at', side_effect=AssertionError('custom reader bypassed')):
        assert execution._scan_requests(tmp_path, reader=custom, prevalidated=hint[1]) == [value]
        with patch.object(execution, 'read_record', side_effect=custom):
            assert execution._scan_requests(tmp_path, prevalidated=hint[1]) == [value]
    assert seen == [path, path]


def test_junk_prefix_bounds_prework_but_never_the_current_full_scan(tmp_path):
    path, value = store(tmp_path, 1)
    names = [f'junk-{i}.pending' for i in range(128)] + [path.name]
    for name in names[:-1]:
        path.with_name(name).write_text('fixture')
    class Entries:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def __iter__(self):
            for name in names:
                yield SimpleNamespace(name=name)
    with patch.object(execution.os, 'scandir', return_value=Entries()), \
            patch.object(execution, '_request_raw_at', wraps=execution._request_raw_at) as reads:
        owner, hint = original_hint(tmp_path)
        assert hint[1] == {} and reads.call_count == 0
        assert owner._requests_prevalidated(hint, deadline=100.) == [value]
        assert reads.call_count == 1


def test_oversize_prefetch_reads_one_extra_byte_and_falls_back(tmp_path):
    path, value = store(tmp_path, 1)
    value['padding'] = 'x' * 1048576
    path.write_text(json.dumps(value))
    observed = []
    original = execution._request_raw_at
    def read(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append((args, len(result[0])))
        return result
    with patch.object(execution, '_request_raw_at', read):
        owner, hint = original_hint(tmp_path)
    assert hint[1] == {} and len(observed) == 1 and observed[0][1] == 1048577
    assert owner._requests_prevalidated(hint, deadline=100.) == [value]


@pytest.mark.parametrize('kind', ['read', 'instance', 'class', 'module', 'subclass'])
def test_custom_reader_before_or_after_prefetch_keeps_original_path_hook(tmp_path, kind):
    path, value = store(tmp_path, 1)
    owner, hint = original_hint(tmp_path)
    seen = []
    original = execution.read_record
    def read(candidate):
        assert isinstance(candidate, Path)
        seen.append(candidate)
        return original(candidate)
    if kind == 'subclass':
        class Custom(execution.Execution):
            def read(self, candidate):
                return read(candidate)
        owner = Custom(owner.p)
        context = patch.object(execution, '_read_request_at', side_effect=AssertionError('custom reader bypassed'))
    elif kind in ('read', 'instance'):
        context = patch.object(owner, 'read', side_effect=read)
    elif kind == 'class':
        context = patch.object(execution.Execution, 'read', side_effect=read)
    else:
        context = patch.object(execution, 'read_record', side_effect=read)
    with context:
        assert owner._prevalidate_requests(100.) is None
        assert seen == []
        assert owner._requests_prevalidated(hint, deadline=100.) == [value]
    assert seen == [path]


def test_same_raw_with_different_actual_encoding_is_not_reused(tmp_path):
    _, value = store(tmp_path, 1)
    owner, hint = original_hint(tmp_path)
    original = execution._request_raw_at
    def changed(*args, **kwargs):
        raw, _ = original(*args, **kwargs)
        return raw, 'ascii'
    with patch.object(execution, '_request_raw_at', changed), \
            patch.object(execution, 'fingerprint', wraps=execution.fingerprint) as digest:
        assert owner._requests_prevalidated(hint, deadline=100.) == [value]
        assert digest.call_count == 1


def test_crlf_text_and_empty_prefetch_still_use_authoritative_current_scan(tmp_path):
    owner = default_execution(tmp_path)
    hint = owner._prevalidate_requests(100.)
    path, value = store(tmp_path, 1)
    path.write_bytes(json.dumps(value, indent=2).replace('\n', '\r\n').encode())
    assert owner._requests_prevalidated(hint, deadline=100.) == [value]


@pytest.mark.parametrize('where', ['prework', 'locked'])
def test_read_eio_keeps_original_error_and_all_fds_close(tmp_path, where):
    path, value = store(tmp_path, 1)
    owner = default_execution(tmp_path)
    hint = None if where == 'prework' else owner._prevalidate_requests(100.)
    before = len(os.listdir('/proc/self/fd'))
    with patch.object(execution.os, 'fdopen', side_effect=OSError(errno.EIO, 'synthetic read failure')):
        with pytest.raises(execution.ExecutionError) as caught:
            owner._prevalidate_requests(100.) if where == 'prework' else owner._requests_prevalidated(hint, deadline=100.)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'
    assert caught.value.error['details'] == {'record':path.name}
    assert len(os.listdir('/proc/self/fd')) == before
    assert owner._requests_prevalidated(hint, deadline=100.) == [value]


def test_prework_deadline_is_call_deadline_not_renewed(tmp_path):
    store(tmp_path, 1)
    owner = default_execution(tmp_path)
    owner.p.clock = lambda: 2.
    with pytest.raises(execution.ExecutionError) as caught:
        owner._prevalidate_requests(1.)
    assert caught.value.error['code'] == 'DEADLINE_EXCEEDED'


def test_locked_deadline_still_checks_fresh_cached_read_and_recovers(tmp_path):
    _, value = store(tmp_path, 1)
    owner, hint = original_hint(tmp_path)
    now = [0.]
    owner.p.clock = lambda: now[0]
    original = execution._request_raw_at
    def delayed(*args, **kwargs):
        raw = original(*args, **kwargs)
        now[0] = 2.
        return raw
    with patch.object(execution, '_request_raw_at', delayed):
        with pytest.raises(execution.ExecutionError) as caught:
            owner._requests_prevalidated(hint, deadline=1.)
    assert caught.value.error['code'] == 'DEADLINE_EXCEEDED'
    assert owner._requests_prevalidated(hint, deadline=100.) == [value]


def test_parent_replacement_during_locked_hint_scan_still_fails(tmp_path):
    path, _ = store(tmp_path, 1)
    owner, hint = original_hint(tmp_path)
    original = execution._request_raw_at
    def moved(*args, **kwargs):
        value = original(*args, **kwargs)
        path.parent.rename(tmp_path/'old-requests')
        path.parent.mkdir()
        return value
    with patch.object(execution, '_request_raw_at', moved):
        with pytest.raises(execution.ExecutionError) as caught:
            owner._requests_prevalidated(hint, deadline=100.)
    assert caught.value.error['code'] == 'OBSERVATION_UNAVAILABLE'


def test_prework_corruption_has_no_effect_and_next_same_key_can_accept():
    fixture = foundation.FoundationTest('runTest')
    fixture.setUp()
    try:
        path, value = store(fixture.p.data, 999, 'unrelated', identity=fixture.vm_id)
        path.write_text('{')
        before = {p:p.read_bytes() for p in path.parent.iterdir()}
        with patch.object(management, 'short_lock', side_effect=AssertionError('admission before failed prework')):
            response = fixture.m.call('stop', {'vm_id':fixture.vm_id}, execute=True, idempotency_key='same-recovery-key')
        assert response['error']['code'] == 'RECONCILE_REQUIRED' and response.get('operation_id') is None
        assert {p:p.read_bytes() for p in path.parent.iterdir()} == before
        replacement = path.with_suffix('.next')
        replacement.write_text(json.dumps(value))
        replacement.replace(path)
        response = fixture.m.call('stop', {'vm_id':fixture.vm_id}, execute=True, idempotency_key='same-recovery-key')
        assert response['ok'], response
    finally:
        fixture.doCleanups()


def test_prework_cost_precedes_single_admission_window_but_not_original_deadline():
    fixture = foundation.FoundationTest('runTest')
    fixture.setUp()
    try:
        windows, operation_deadlines = [], []
        original_prefetch = execution._prefetch_requests
        original_lock = management.short_lock
        original_accept = execution.Execution._accept_locked
        def slow_prefetch(*args, **kwargs):
            result = original_prefetch(*args, **kwargs)
            fixture.ticks += 3.
            return result
        def locking(*args, **kwargs):
            windows.append(kwargs['deadline'])
            return original_lock(*args, **kwargs)
        def accept(*args, **kwargs):
            operation_deadlines.append(kwargs['deadline'])
            return original_accept(*args, **kwargs)
        with patch.object(execution, '_prefetch_requests', side_effect=slow_prefetch) as prework, \
                patch.object(management, 'short_lock', side_effect=locking), \
                patch.object(execution.Execution, '_accept_locked', autospec=True, side_effect=accept):
            response = fixture.m.call('stop', {'vm_id':fixture.vm_id}, execute=True,
                                      idempotency_key='original-deadline', budget_ms=90000)
        assert response['ok'], response
        assert prework.call_count == 1 and windows == [5.]
        assert operation_deadlines and all(value == 90. for value in operation_deadlines)
        assert fixture.m.last_trace['summary']['execution.stop_prevalidation']['inclusive_seconds'] == 3.
        assert fixture.m.last_trace['elapsed_seconds'] >= 3.
    finally:
        fixture.doCleanups()
