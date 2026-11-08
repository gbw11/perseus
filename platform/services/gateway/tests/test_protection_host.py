"""S15P31E110-346 platform decision: only the current host boot's protection is active."""
from types import SimpleNamespace
from uuid import uuid4

from xenova_gateway.authority import AuthorityStore
from xenova_gateway.history import HistoryPolicy, candidate

OLD = '11111111-1111-4111-8111-111111111111'
NEW = '22222222-2222-4222-8222-222222222222'
B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def key(host):
    return f'g1:{host}:1:{uuid4()}'


def row(host, request_id, phase='PROTECTED', vm=B):
    return dict(request_id=request_id, host_boot_id=host, phase=phase,
                spec=dict(target_vm=vm, host_boot_id=host, deadline_ms=10_000))


def store(host, rows):
    return SimpleNamespace(host=host, value={'protections': rows, 'transitions': [], 'phase': 'APPLIED'},
                           history_policy=HistoryPolicy(100, 100))


def test_previous_host_protection_does_not_block_reselection():
    current = store(NEW, [row(OLD, key(OLD))])
    assert not AuthorityStore.protected(current, B)
    current.value['protections'].append(row(NEW, key(NEW)))
    assert AuthorityStore.protected(current, B)


def test_previous_host_active_protection_becomes_collectable_history():
    old, new = key(OLD), key(NEW)
    kept = candidate(store(NEW, [row(OLD, old), row(NEW, new)]), now_ms=1)['protections']
    assert [r['request_id'] for r in kept] == [new]


def test_current_host_active_protection_is_never_collected():
    new = key(NEW)
    kept = candidate(store(NEW, [row(NEW, new)]), now_ms=10**9)['protections']
    assert [r['request_id'] for r in kept] == [new]
