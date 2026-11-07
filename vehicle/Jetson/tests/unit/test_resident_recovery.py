"""선택 회복의 기한·사건 순서 단위. fake reap/ACK 값은 실제 OS 증명이 아니다."""

from collections import deque
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from raw_lease_fixtures import identifier, scope
from resource_guard_fixtures import encode, minimal_fields
from xenova_vehicle.runtime import resident_recovery as recovery
from xenova_vehicle.runtime.raw_leases import Endpoint, RawRecoveryBarrier
from xenova_vehicle.runtime.resource_manifest import ResourceManifest


class Link:
    def __init__(self):
        self.endpoint = object()
        self.closed = False

    def close(self):
        self.closed = True


class Broker:
    def __init__(self, raw_scope):
        self.scope = raw_scope
        self.pause_ready = True
        self.pauses = 0

    def begin_pause(self):
        self.pauses += 1

    def fence(self, now):
        pass

    def recovery_barrier(self, exited):
        return RawRecoveryBarrier(self.scope, Endpoint(identifier(42), identifier(43)),
                                  identifier(44), 'b' * 64, 1500, exited)


class Runtime:
    def __init__(self):
        self.began = 100
        self.startup_deadline = 90_000_100
        self.resident_started_us = 1000
        self.stopping = False
        self.failure = None
        self.log_pending = None
        self.birth_pending = set()
        self.birth_admitting = set()
        self.scope = scope()
        self.binding = SimpleNamespace(sha256='a' * 64)
        self.broker = Broker(self.scope)
        refs = tuple(object() for _ in range(6))
        self.handles = SimpleNamespace(raw=SimpleNamespace(arena_id=self.scope.arena_id),
                                       raw_tokens=((refs[0], refs[1]), (refs[2], refs[3])), db_tokens=refs[4:])
        fields = minimal_fields()
        fields['owners'] = [dict(role=role, phase='CURRENT', pid=100 + i, start_ticks=str(1000 + i))
                            for i, role in enumerate(('S', 'A', 'M', 'D', 'TRACKER'))]
        fields['recovery_episode'].update(service_attempts=1, role_attempts=dict(A=1, M=1, D=1))
        self.manifest = ResourceManifest(encode(fields))
        self.workers = {}
        self.identities = {}
        for i, role in enumerate(('A', 'M', 'D'), 1):
            peer = SimpleNamespace(worker_instance=identifier(10 + i), channel_epoch=identifier(20 + i))
            self.workers[role] = SimpleNamespace(peer=peer, pid=100 + i, link=Link(),
                reaped=False, eof=False, exited_us=None, exitcode=None, bind=object(),
                bind_ack=object(), bootstrap=None, last_heartbeat_us=1000)
            self.identities[role] = peer.worker_instance, peer.channel_epoch
        self.capture_ready_us = self.nn_ready_us = self.storage_ready_us = 1000
        self.last_capture_us = self.last_fresh_parser_us = 1000
        self.parser_deadline = 5000
        self.a_phase = 'STREAMING'
        self.a_phase_deadlines = {}
        self.progress = {('A', 'CAPTURE_PAIR'): {}, ('M', 'PARSER_COMPLETED'): {}}
        self.events = []
        self.writes = []
        self.requests = []
        self.spawns = []
        self.selector = SimpleNamespace(unregister=lambda endpoint: None)

    def event(self, kind, **fields):
        self.events.append((kind, fields))

    def stop(self, reason, error=None):
        self.stopping, self.failure = True, (reason, error)

    def _stop_one(self, host, began, reason):
        pass

    def _request(self, operation, purpose, **kwargs):
        request = identifier(10000 + len(self.requests))
        self.requests.append((operation, purpose, kwargs))
        self.log_pending = request, operation, purpose, kwargs.get('cap_us', recovery.U64_MAX)
        return True

    def _write(self, fields, purpose, **kwargs):
        fields['revision'] = str(self.manifest.revision + 1)
        self.writes.append((json.loads(json.dumps(fields)), purpose))
        return self._request('WRITE', purpose, **kwargs)

    def _spawn(self, role, *, deadline_us):
        self.spawns.append((role, deadline_us))

    def refresh(self, now):
        self.last_capture_us = self.last_fresh_parser_us = now
        for host in self.workers.values():
            host.last_heartbeat_us = now


class ResidentRecoveryTests(unittest.TestCase):
    def test_m_failure_revokes_old_parser_and_suppresses_only_original_host(self):
        r = Runtime()
        coordinator = recovery.ResidentRecovery(r)
        old = r.workers['M']
        with patch.object(recovery, 'j_us', return_value=2000):
            self.assertTrue(coordinator.fail('M', 'injected parser failure'))
        self.assertTrue(coordinator.suppressed('M', old))
        self.assertFalse(coordinator.suppressed('M', SimpleNamespace()))
        self.assertIsNone(r.nn_ready_us)
        self.assertIsNone(r.parser_deadline)
        self.assertIsNone(r.last_fresh_parser_us)
        self.assertNotIn(('M', 'PARSER_COMPLETED'), r.progress)
        self.assertIsNotNone(r.capture_ready_us)
        self.assertFalse(coordinator.fail('M', 'duplicate old EOF'))
        self.assertFalse(r.stopping)
        coordinator.fail('D', 'independent fault')
        self.assertTrue(r.stopping)
        self.assertEqual(coordinator.active.phase, 'FAILED')

    def test_operation_cap_distinguishes_cleanup_new_attempt_and_ready_idle(self):
        r = Runtime()
        coordinator = recovery.ResidentRecovery(r)
        self.assertEqual(coordinator.operation_cap, recovery.U64_MAX)
        r.resident_started_us = None
        self.assertEqual(coordinator.operation_cap, r.startup_deadline)
        with patch.object(recovery, 'j_us', return_value=2000):
            coordinator.fail('M', 'failure')
        self.assertEqual(coordinator.operation_cap, 25_002_000)
        coordinator.active.phase = 'COUNTER'
        coordinator.active.startup_deadline_us = 200_000_000
        self.assertEqual(coordinator.operation_cap, 200_000_000)
        r.stopping = True
        self.assertEqual(coordinator.operation_cap, recovery.U64_MAX)

    def test_reap_pause_and_counter_ack_precede_new_identity_or_spawn(self):
        r = Runtime()
        coordinator = recovery.ResidentRecovery(r)
        old = r.workers['M']
        identity = r.identities['M']
        with patch.object(recovery, 'j_us', return_value=2000):
            coordinator.fail('M', 'failure')
            coordinator.active.paused = object()
            coordinator.step()
        self.assertEqual(coordinator.active.phase, 'DRAIN')
        self.assertEqual(r.writes, [])
        old.reaped, old.eof, old.exited_us, old.exitcode = True, True, 1900, -9
        with patch.object(recovery, 'j_us', return_value=2100):
            coordinator.step()
        self.assertEqual(coordinator.active.phase, 'BACKOFF')
        self.assertNotIn('M', r.workers)
        self.assertTrue(old.link.closed)
        at = coordinator.active.backoff_until_us
        with patch.object(recovery, 'j_us', return_value=at):
            coordinator.step()
        fields, purpose = r.writes[-1]
        self.assertEqual(purpose, 'RECOVERY_COUNTER')
        self.assertEqual(fields['recovery_episode']['service_attempts'], 2)
        self.assertEqual(fields['recovery_episode']['role_attempts'], dict(A=1, M=2, D=1))
        current = next(x for x in fields['owners'] if x['role'] == 'M' and x['phase'] == 'CURRENT')
        retired = next(x for x in fields['owners'] if x['role'] == 'M' and x['phase'] == 'RETIRED')
        self.assertIsNone(current['pid'])
        self.assertEqual(retired['pid'], old.pid)
        self.assertEqual(r.identities['M'], identity)
        self.assertEqual(r.spawns, [])
        request = r.log_pending[0]
        r.manifest = ResourceManifest(encode(fields))
        r.log_pending = None
        with patch.object(recovery, 'j_us', return_value=at + 1):
            coordinator.completed('RECOVERY_COUNTER', SimpleNamespace(request_id=request))
        self.assertNotEqual(r.identities['M'], identity)
        self.assertEqual(r.spawns, [])
        self.assertEqual(coordinator.active.phase, 'NORMALIZE')

    def test_unknown_retired_birth_cannot_be_overwritten(self):
        r = Runtime()
        fields = r.manifest.fields
        fields['owners'].append(dict(role='M', phase='RETIRED', pid=199, start_ticks='1990'))
        r.manifest = ResourceManifest(encode(fields))
        coordinator = recovery.ResidentRecovery(r)
        with patch.object(recovery, 'j_us', return_value=2000):
            coordinator.fail('M', 'failure')
        with self.assertRaises(recovery.RecoveryError):
            coordinator._counter_fields(coordinator.active, 2100)
        coordinator._retired_births['M'] = (199, '1990')
        valid = coordinator._counter_fields(coordinator.active, 2100)
        self.assertEqual(sum(x['role'] == 'M' and x['phase'] == 'RETIRED' for x in valid['owners']), 1)

    def test_already_sent_resume_does_not_expire_the_resumed_state(self):
        r = Runtime()
        coordinator = recovery.ResidentRecovery(r)
        coordinator.active = recovery.RecoveryAttempt('M', ('M',), {'M': r.workers['M']},
            'WAIT_RESUME', 1000, 'fixture', startup_deadline_us=100000,
            resume_draft=dict(issued_us=2000, expires_us=4000), resume_sent_us=2500)
        with patch.object(recovery, 'j_us', return_value=5000):
            coordinator.step()
        self.assertEqual(coordinator.active.phase, 'WAIT_READY')
        self.assertFalse(r.stopping)

    def test_health_requires_continuous_actual_freshness_before_durable_reset(self):
        r = Runtime()
        coordinator = recovery.ResidentRecovery(r)
        began = 10000
        r.refresh(began)
        with patch.object(recovery, 'j_us', return_value=began):
            self.assertFalse(coordinator.healthy())
        r.refresh(began + recovery.HEALTHY_US - 1)
        with patch.object(recovery, 'j_us', return_value=began + recovery.HEALTHY_US - 1):
            self.assertFalse(coordinator.healthy())
        r.refresh(began + recovery.HEALTHY_US)
        with patch.object(recovery, 'j_us', return_value=began + recovery.HEALTHY_US):
            self.assertTrue(coordinator.healthy())
        fields, purpose = r.writes[-1]
        self.assertEqual(purpose, 'RECOVERY_HEALTHY')
        self.assertEqual(fields['recovery_episode']['service_attempts'], 0)
        self.assertEqual(fields['recovery_episode']['role_attempts'], dict(A=0, M=0, D=0))
        self.assertEqual(r.manifest.recovery_episode.service_attempts, 1)


if __name__ == '__main__':
    unittest.main()
