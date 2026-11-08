"""A delayed binding read failure must not replace a later verified success."""
import threading
import time
from unittest.mock import patch

import pytest

from test_xenova_batch_read_queries import pair
from xenova_execution import ExecutionError


@pytest.mark.parametrize('code', ['DEADLINE_EXCEEDED', 'TARGET_CHANGED'])
def test_delayed_identity_failure_preserves_later_verified_success(pair, code):
    pair.p.clock = time.monotonic
    record = next(iter(pair.p.records(validate_assets=False).values()))
    identity = record['uuid']
    entered, release = threading.Event(), threading.Event()
    failures = []
    command = pair.p.command

    def blocked(args, **kwargs):
        if threading.current_thread().name == 'old-binding-read' and args[0] == 'xenova-domain-identity':
            entered.set()
            if not release.wait(5):
                raise RuntimeError('binding test barrier timed out')
            raise ExecutionError(code, 'injected delayed binding observation failure')
        return command(args, **kwargs)

    def observe():
        try:
            pair.p.execution().observe(record, pair.p.clock() + 5, identity_only=True)
        except ExecutionError as error:
            failures.append(error.error['code'])

    with patch.object(pair.p, 'command', blocked):
        worker = threading.Thread(target=observe, name='old-binding-read')
        worker.start()
        try:
            assert entered.wait(5)
            response = pair.m.fork().call('get', {'vm_id': identity}, budget_ms=5000)
            assert response['ok'], response
            path = pair.p.runtime / 'observations' / (identity + '.json')
            success = path.read_bytes()
        finally:
            release.set()
            worker.join(5)
    assert not worker.is_alive()
    assert failures == [code]  # The failing caller still receives its failure.
    assert path.read_bytes() == success
    assert pair.p.execution().snapshot(identity, 5000)['last_error'] is None


@pytest.mark.parametrize('code', ['DEADLINE_EXCEEDED', 'TARGET_CHANGED'])
def test_identity_failure_after_prior_success_still_revokes_snapshot(pair, code):
    pair.p.clock = time.monotonic
    record = next(iter(pair.p.records(validate_assets=False).values()))
    identity = record['uuid']
    response = pair.m.fork().call('get', {'vm_id': identity}, budget_ms=5000)
    assert response['ok'], response
    command = pair.p.command

    def failed(args, **kwargs):
        if args[0] == 'xenova-domain-identity':
            raise ExecutionError(code, 'injected current binding failure')
        return command(args, **kwargs)

    with patch.object(pair.p, 'command', failed), pytest.raises(ExecutionError) as caught:
        pair.p.execution().observe(record, pair.p.clock() + 5, identity_only=True)
    assert caught.value.error['code'] == code
    snapshot = pair.p.execution().snapshot(identity, 5000)
    assert snapshot['unknown'] and snapshot['current'] is None
    assert snapshot['last_error']['code'] == code


def test_delayed_identity_failure_does_not_erase_newer_failure(pair):
    now = [time.monotonic()]
    pair.p.clock = lambda: now[0]
    record = next(iter(pair.p.records(validate_assets=False).values()))
    identity = record['uuid']
    entered, release = threading.Event(), threading.Event()
    failures = []
    command = pair.p.command

    def failed(args, **kwargs):
        if args[0] == 'xenova-domain-identity':
            if threading.current_thread().name == 'old-binding-read':
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('binding test barrier timed out')
                raise ExecutionError('DEADLINE_EXCEEDED', 'older binding failure')
            raise ExecutionError('TARGET_CHANGED', 'newer binding failure')
        return command(args, **kwargs)

    def observe():
        try:
            pair.p.execution().observe(record, pair.p.clock() + 5, identity_only=True)
        except ExecutionError as error:
            failures.append(error.error['code'])

    with patch.object(pair.p, 'command', failed):
        worker = threading.Thread(target=observe, name='old-binding-read')
        worker.start()
        try:
            assert entered.wait(5)
            # Journal clocks have millisecond precision. Make the later
            # failure strictly later, rather than relying on thread timing
            # to cross a millisecond boundary on a fast local fixture.
            now[0] += .01
            with pytest.raises(ExecutionError):
                pair.p.execution().observe(record, pair.p.clock() + 5, identity_only=True)
            path = pair.p.runtime / 'observations' / (identity + '.json')
            newer = path.read_bytes()
        finally:
            release.set()
            worker.join(5)
    assert not worker.is_alive()
    assert failures == ['DEADLINE_EXCEEDED']
    assert path.read_bytes() == newer
    assert pair.p.execution().snapshot(identity, 5000)['last_error']['code'] == 'TARGET_CHANGED'
