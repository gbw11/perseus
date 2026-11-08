"""기동 실패의 실제 socket/thread 정리와 synthetic spawn 소유 기록을 검사한다.

FakeProcess의 PID/exit 표시는 실제 child 종료 증명이 아니다. socketpair와 제어된
non-daemon thread만 실제 생성하며 카메라·Paho·SQLite·SHM·fork는 실행하지 않는다.
"""

from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from xenova_vehicle.runtime import channel, data_channel, resident_runtime


def uid(n): return '40000000-0000-4000-8000-%012x' % n


class Selector:
    def __init__(self, fail=False): self.fail = fail; self.closed = False
    def register(self, *args):
        if self.fail: raise OSError('controlled selector failure')
    def unregister(self, *args): pass
    def close(self): self.closed = True


class Process:
    def __init__(self, mode='normal'):
        self.mode = mode; self.pid = None; self.starts = self.closes = 0; self.sentinel = object()
    def start(self):
        self.starts += 1
        if self.mode == 'reject': raise OSError('controlled pre-Popen failure')
        self.pid = 321
        if self.mode == 'started-error': raise OSError('controlled error after simulated PID')
    def close(self): self.closes += 1


def runtime_for_spawn(process, *, mapper=False, selector_fail=False):
    value = resident_runtime._Runtime.__new__(resident_runtime._Runtime)
    value.recovery = SimpleNamespace(operation_cap=10**18)
    value.service = uid(1); value.identities = {'D': (uid(2), uid(3)), 'M': (uid(4), uid(5))}
    value.artifacts = dict(release_sha256='a' * 64, manifests={})
    value.output = Path('/unused-test-output')
    value.storage_profile = value.storage_recovery = value.profile = value.scope = value.raw_profile = None
    value.handles = SimpleNamespace(db_copy=object(), db_tokens=(), raw_tokens=())
    value.jio_profile = SimpleNamespace(source_policy=object(), device='oak-test',clock_policy=object()) if mapper else None
    value.context = SimpleNamespace(Process=Mock(return_value=process))
    value.selector = Selector(selector_fail); value.workers = {'A': SimpleNamespace(bind=object())}
    value.binding = None; value.jio = None; value.manifest = SimpleNamespace(runtime=SimpleNamespace(boot_id=uid(6)))
    value.clock_epoch = uid(7); value.source_revision = 0; value.birth_pending = set(); value.failure = None
    value.storage_material = lambda: None; value.event = Mock()
    return value


class ThreadOwner:
    """기동 오류 주입 때도 실제 thread 참조를 보유하는 작은 fixture다. MQTT owner가 아니다."""
    instances = []
    fail_after_start = False
    def __init__(self, _):
        self.event = threading.Event(); self.thread = None; self.stops = 0; self.joins = 0
        self.instances.append(self)
    def start(self):
        self.thread = threading.Thread(target=self.event.wait, daemon=False); self.thread.start()
        if self.fail_after_start: raise RuntimeError('controlled JIO start failure')
        return self
    def stop(self): self.stops += 1; self.event.set()
    def join(self, timeout):
        self.joins += 1
        if self.thread is not None: self.thread.join(timeout)
        return self.thread is None or not self.thread.is_alive()


class LogOwner:
    def __init__(self, *, start_error=None):
        self.error = start_error; self.event = threading.Event(); self.thread = None
        self.stop_calls = self.close_calls = 0; self.start_calls = 0
    def start(self):
        self.start_calls += 1
        if self.error is not None: raise self.error
        self.thread = threading.Thread(target=self.event.wait, daemon=False); self.thread.start()
    def request_stop(self): self.stop_calls += 1; self.event.set()
    @property
    def is_alive(self): return self.thread is not None and self.thread.is_alive()
    def close(self, timeout):
        self.close_calls += 1; self.request_stop()
        if self.thread is not None: self.thread.join(timeout)


@unittest.skipUnless(hasattr(socket, 'SOCK_SEQPACKET'), '실제 Linux socketpair 정리 확인 필요')
class ResidentStartupCleanupTests(unittest.TestCase):
    def pair(self):
        pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        for endpoint in pair: self.addCleanup(endpoint.close)
        return pair

    def test_control_pair_second_endpoint_setup_failure_closes_both_actual_fds(self):
        pair = self.pair()
        class Wrapped:
            def __init__(self, endpoint, fail): self.endpoint = endpoint; self.fail = fail; self.closes = 0
            def setblocking(self, flag):
                if self.fail: raise OSError('controlled setblocking failure')
                self.endpoint.setblocking(flag)
            def close(self): self.closes += 1; self.endpoint.close()
        wrapped = (Wrapped(pair[0], False), Wrapped(pair[1], True))
        with patch.object(channel.socket, 'socketpair', return_value=wrapped):
            with self.assertRaises(OSError): channel.control_pair()
        self.assertEqual([p.fileno() for p in pair], [-1, -1])
        self.assertEqual([p.closes for p in wrapped], [1, 1])

    def test_data_pair_partial_socket_option_failure_closes_both_actual_fds(self):
        pair = self.pair()
        class Wrapped:
            def __init__(self, endpoint, fail): self.endpoint = endpoint; self.fail = fail
            def setblocking(self, flag): self.endpoint.setblocking(flag)
            def setsockopt(self, *args):
                if self.fail: raise OSError('controlled data option failure')
                self.endpoint.setsockopt(*args)
            def close(self): self.endpoint.close()
        with patch.object(data_channel.socket, 'socketpair', return_value=(Wrapped(pair[0], False), Wrapped(pair[1], True))):
            with self.assertRaises(OSError): data_channel.data_pair()
        self.assertEqual([p.fileno() for p in pair], [-1, -1])

    def test_second_pair_acquisition_failure_closes_existing_control_pair_before_spawn(self):
        process = Process(); runtime = runtime_for_spawn(process, mapper=True); pair = self.pair()
        with patch.object(resident_runtime, 'control_pair', return_value=pair), \
                patch.object(data_channel, 'data_pair', side_effect=OSError('controlled FD limit')):
            with self.assertRaises(OSError): runtime._spawn('M')
        self.assertEqual([p.fileno() for p in pair], [-1, -1])
        self.assertNotIn('M', runtime.workers); self.assertEqual(process.starts, 0)

    def test_source_constructor_failure_closes_all_four_fds_before_process_creation(self):
        from xenova_vehicle.runtime import source_runtime
        process = Process(); runtime = runtime_for_spawn(process, mapper=True)
        control, data = self.pair(), self.pair()
        with patch.object(resident_runtime, 'control_pair', return_value=control), \
                patch.object(data_channel, 'data_pair', return_value=data), \
                patch.object(resident_runtime, 'WorkerBoot', side_effect=lambda *a, **kw: SimpleNamespace()), \
                patch.object(source_runtime, 'SupervisorSource', side_effect=ValueError('controlled source construction')):
            with self.assertRaises(ValueError): runtime._spawn('M')
        self.assertEqual([p.fileno() for p in control + data], [-1] * 4)
        runtime.context.Process.assert_not_called()

    def test_link_construction_failure_happens_before_process_start_and_closes_fds(self):
        process = Process(); runtime = runtime_for_spawn(process); pair = self.pair()
        with patch.object(resident_runtime, 'control_pair', return_value=pair), \
                patch.object(resident_runtime, 'StorageBoot', return_value=object()), \
                patch.object(resident_runtime, 'ResidentLink', side_effect=ValueError('controlled link construction')):
            with self.assertRaises(ValueError): runtime._spawn('D')
        self.assertEqual(process.starts, 0); self.assertEqual(process.closes, 1)
        self.assertEqual([p.fileno() for p in pair], [-1, -1])

    def test_start_failure_without_pid_closes_parent_and_child_without_claiming_exit(self):
        process = Process('reject'); runtime = runtime_for_spawn(process); pair = self.pair()
        with patch.object(resident_runtime, 'control_pair', return_value=pair), \
                patch.object(resident_runtime, 'StorageBoot', return_value=object()):
            with self.assertRaises(OSError): runtime._spawn('D')
        self.assertEqual([p.fileno() for p in pair], [-1, -1])
        self.assertNotIn('D', runtime.workers); self.assertEqual(process.closes, 1)

    def test_post_start_registration_failure_preserves_tracked_owner_and_actual_reap_responsibility(self):
        process = Process(); runtime = runtime_for_spawn(process, selector_fail=True); pair = self.pair()
        with patch.object(resident_runtime, 'control_pair', return_value=pair), \
                patch.object(resident_runtime, 'StorageBoot', return_value=object()):
            with self.assertRaises(OSError): runtime._spawn('D')
        host = runtime.workers['D']
        self.assertIs(host.process, process); self.assertEqual(host.pid, 321)
        self.assertFalse(host.reaped); self.assertIsNone(host.exitcode)
        self.assertIn('D', runtime.birth_pending); self.assertEqual(process.closes, 0)
        self.assertGreaterEqual(pair[0].fileno(), 0); self.assertEqual(pair[1].fileno(), -1)

    def test_start_error_with_a_reported_pid_preserves_owner_instead_of_closing_live_parent(self):
        process = Process('started-error'); runtime = runtime_for_spawn(process); pair = self.pair()
        with patch.object(resident_runtime, 'control_pair', return_value=pair), \
                patch.object(resident_runtime, 'StorageBoot', return_value=object()):
            with self.assertRaises(OSError): runtime._spawn('D')
        self.assertFalse(runtime.workers['D'].reaped); self.assertEqual(process.closes, 0)
        self.assertGreaterEqual(pair[0].fileno(), 0); self.assertEqual(pair[1].fileno(), -1)

    def run_initial_failure(self, *, log_error=None, request_error=None, jio_start_error=False):
        runtime = resident_runtime._Runtime.__new__(resident_runtime._Runtime)
        runtime.jio_profile = object(); runtime.jio = None; runtime.workers = {}; runtime.failure = None
        runtime.selector = Selector(); runtime.log = LogOwner(start_error=log_error)
        runtime._request = Mock(side_effect=request_error)
        ThreadOwner.instances = []; ThreadOwner.fail_after_start = jio_start_error
        def cleanup():
            for owner in ThreadOwner.instances: owner.stop(); owner.join(1)
            runtime.log.request_stop(); runtime.log.close(1)
            ThreadOwner.fail_after_start = False
        self.addCleanup(cleanup)
        from xenova_vehicle.transport import jetson_jio
        with patch.object(jetson_jio, 'JetsonJio', ThreadOwner):
            with self.assertRaises(RuntimeError) as caught: runtime.run(lambda: False)
        self.assertTrue(runtime.selector.closed)
        self.assertTrue(ThreadOwner.instances)
        self.assertTrue(all(not owner.thread.is_alive() for owner in ThreadOwner.instances))
        self.assertFalse(runtime.log.is_alive)
        return runtime, caught.exception

    def test_log_start_failure_still_stops_and_joins_the_actual_non_daemon_jio_fixture(self):
        error = RuntimeError('controlled log startup failure')
        runtime, caught = self.run_initial_failure(log_error=error)
        self.assertIs(caught, error); runtime._request.assert_not_called()

    def test_prepare_rejection_stops_both_actual_threads_and_preserves_original_failure(self):
        error = RuntimeError('controlled initial PREPARE rejection')
        runtime, caught = self.run_initial_failure(request_error=error)
        self.assertIs(caught, error); self.assertGreaterEqual(runtime.log.stop_calls, 1)

    def test_jio_start_error_after_thread_creation_retains_reference_for_real_stop_join(self):
        runtime, _ = self.run_initial_failure(jio_start_error=True)
        self.assertEqual(runtime.log.start_calls, 0)

    def test_only_current_m_source_is_fenced_before_endpoint_close_and_repeated_close_is_noop(self):
        events = []; runtime = resident_runtime._Runtime.__new__(resident_runtime._Runtime)
        runtime.jio = SimpleNamespace(fence_source=lambda: events.append('FENCE'))
        runtime.selector = Selector(); runtime.source_revision = 0
        def host(name):
            source = SimpleNamespace(owner=SimpleNamespace(revision=1), link=SimpleNamespace(endpoint=object()),
                close=lambda: events.append(name))
            return SimpleNamespace(source=source)
        old, current = host('OLD_CLOSE'), host('CURRENT_CLOSE'); runtime.workers = {'M': current}
        runtime._close_source(old)
        self.assertEqual(events, ['OLD_CLOSE'])
        runtime._close_source(current); runtime._close_source(current)
        self.assertEqual(events, ['OLD_CLOSE', 'FENCE', 'CURRENT_CLOSE'])


if __name__ == '__main__': unittest.main()
