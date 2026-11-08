"""An older binding failure must not erase a newer paired observation."""
import json
import threading
import time
from unittest.mock import patch

import pytest

from test_xenova_batch_read_queries import pair, batch_commands
import xenova_drive_binding as binding
from xenova_execution import ExecutionError


@pytest.mark.parametrize('older_path', ['single', 'pair'])
@pytest.mark.parametrize('code', ['DEADLINE_EXCEEDED', 'TARGET_CHANGED'])
def test_delayed_binding_failure_keeps_newer_paired_success(pair, older_path, code):
    now = [time.monotonic()]
    pair.p.clock = lambda: now[0]
    records = list(pair.p.records(validate_assets=False).values())
    identities = tuple(record['uuid'] for record in records)
    entered, release = threading.Event(), threading.Event()
    failures = []
    command = pair.p.command
    batch_command, calls = batch_commands(pair)

    def fail_older():
        entered.set()
        if not release.wait(5):
            raise RuntimeError('older binding observation barrier timed out')
        raise ExecutionError(code, 'injected delayed binding failure')

    def single_command(args, **kwargs):
        if (threading.current_thread().name == 'older-binding'
                and args[0] == 'xenova-domain-identity'):
            fail_older()
        return command(args, **kwargs)

    def paired_command(args, deadline):
        if (threading.current_thread().name == 'older-binding'
                and args[0] == '/usr/libexec/xenova-domain-identities-native'):
            fail_older()
        return batch_command(args, deadline)

    def older_observation():
        try:
            if older_path == 'single':
                pair.p.execution().observe(records[0], now[0] + 5, identity_only=True)
            else:
                binding.observe_pair(pair.p, identities, timeout_s=5)
        except ExecutionError as error:
            failures.append(error.error['code'])

    with (patch.object(pair.p, 'command', single_command),
          patch.object(pair.p, 'timed_command', paired_command)):
        worker = threading.Thread(target=older_observation, name='older-binding')
        worker.start()
        try:
            assert entered.wait(5)
            # Observation journals order at millisecond precision. Avoid
            # depending on scheduler timing to make the newer read distinct.
            now[0] += .01
            new_calls_start = len(calls)
            rows = binding.observe_pair(pair.p, identities, timeout_s=5)
            new_calls = calls[new_calls_start:]
            paths = {identity: pair.p.runtime / 'observations' / (identity + '.json')
                     for identity in identities}
            saved = {identity: path.read_bytes() for identity, path in paths.items()}
            assert all(row['status'] == 'observed' for row in rows.values())
        finally:
            release.set()
            worker.join(5)

    assert not worker.is_alive()
    assert failures == [code]  # The failed caller still gets its original error.
    for identity, path in paths.items():
        assert path.read_bytes() == saved[identity]
        snapshot = pair.p.execution().snapshot(identity, 5000)
        assert snapshot['last_error'] is None
        assert json.loads(saved[identity])['last_success']['monotonic_ms'] == rows[identity]['observed_monotonic_ms']
    # The successful pair still shares two Xen reads and two native batches.
    assert sum(args == ['xl', 'list', '-v'] for args in new_calls) == 2
    assert sum(args[0] == '/usr/libexec/xenova-domain-identities-native' for args in new_calls) == 2
