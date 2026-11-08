"""실제 seqpacket 역압에서 RELEASE/PARSER_STARTED의 CPU 진입 경계를 검사한다.

kernel 송신 buffer를 채워 실제 EAGAIN을 만들며 SDK·카메라·SHM·token은 쓰지 않는다.
actor의 HELLO/BIND 기동은 합성 등록값으로 생략한다. CPU marker는 helper 뒤의
decode/core 진입을 대신하며, 실제 모델 실행·native view 반환 검증을 주장하지 않는다.
"""

from collections import deque
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from slot_message_fixtures import limits, make, reader_context
from xenova_vehicle.runtime import resident_workers as workers
from xenova_vehicle.runtime.resident_transport import ResidentLink, j_us
from xenova_vehicle.runtime.slot_cleanup import CleanupTimeLimits, ReleaseMessage


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux AF_UNIX/SOCK_SEQPACKET 역압 시험')
class ResidentReleaseTests(unittest.TestCase):
    def setUp(self):
        self.left, self.right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(self.left.close)
        self.addCleanup(self.right.close)
        self.left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        self.left.setblocking(False)
        self.right.setblocking(False)
        context = reader_context()
        selected = limits()
        offer = make('OFFER', context, limits=selected, issued=1000, expires=1500)
        request = make('ACQUIRE', context, offer, limits=selected, issued=1100, expires=1250)
        self.acquired = make('ACQUIRED', context, request, limits=selected, issued=1110, expires=1250)
        peer = context.bind.context.peer
        self.actor = workers._Actor.__new__(workers._Actor)
        self.actor.boot = SimpleNamespace(peer=peer)
        self.actor.link = ResidentLink(self.left, peer, 'M', track_sent=True)
        self.actor.bind = context.bind
        self.actor.ack = None
        self.actor.ack_sent = None
        self.actor.last_filled_sent = None
        self.actor.last_actual_sent = None
        self.actor.stop = None
        self.actor.deferred = deque()
        self.actor.state = 'WAIT_CLOCK'
        self.actor.last_progress = None
        self.actor.progress_count = 0
        self.server = ResidentLink(self.right, peer, 'S')
        self.addCleanup(self.actor.link.close)
        self.addCleanup(self.server.close)

    def saturate(self):
        """작은 packet의 actual EAGAIN을 확인해 더 큰 제어 packet의 역압을 준비한다."""
        # 직접 넣은 filler는 protocol packet이 아니다. 반대편은 raw recv로 시험분만 회수한다.
        self.fill_count = 0
        for _ in range(1024):
            try:
                self.left.send(b'x' * 64)
            except BlockingIOError:
                self.assertGreater(self.fill_count, 0)
                return
            self.fill_count += 1
        self.fail('bounded preparation did not reach actual kernel EAGAIN')

    def release(self):
        completed = j_us()
        payload = self.acquired.payload
        payload['op'] = 'RELEASE'
        packet = self.actor.required_slot(payload, request_id=self.acquired.request_id,
            issued=completed, expires=completed + 2_000_000)
        message = ReleaseMessage(packet, self.acquired, limits=CleanupTimeLimits(2_000_000))
        return packet, message

    def drain_raw(self):
        result = []
        for _ in range(1024):
            try:
                result.append(self.right.recv(4096))
            except BlockingIOError:
                return result
        self.fail('bounded raw receive did not drain owned test packets')

    def test_queued_release_blocks_cpu_until_full_send_and_preserves_other_slot_fifo(self):
        self.saturate()
        packet, message = self.release()
        self.assertEqual(self.actor.link.take_sent(), ())
        self.assertIsNone(self.actor.last_actual_sent)
        now = j_us()
        incoming = tuple(self.server.send('SLOT', self.acquired.parent.parent.payload,
            domain='RAW', revision=self.actor.bind.binding_revision, issued_us=now,
            expires_us=now + 2_000_000) for _ in range(2))
        entered, started = threading.Event(), threading.Event()
        results, errors = [], []

        def consume_after_send():
            started.set()
            try:
                results.append(self.actor.await_sent(packet))
                entered.set()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=consume_after_send)
        thread.start()
        received = []
        try:
            self.assertTrue(started.wait(1))
            self.assertFalse(entered.wait(.03), 'queued-but-unsent release allowed CPU entry')
            self.assertEqual(self.actor.link.counts['sent'], 0)
            received.extend(self.drain_raw())
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, [type(error).__name__ for error in errors])
            self.assertTrue(entered.is_set())
            received.extend(self.drain_raw())
            self.assertIn(packet.raw_bytes, received)
            self.assertEqual(sum(raw == packet.raw_bytes for raw in received), 1)
            self.assertIs(results[0].packet, packet)
            self.assertLess(results[0].completed_us, message.expires_us)
            self.assertEqual(tuple(p.raw_bytes for p in self.actor.deferred), tuple(p.raw_bytes for p in incoming))
        finally:
            self.actor.link.close()
            self.server.close()
            thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_stop_while_release_is_unsent_prevents_cpu_without_reissuing_cleanup(self):
        self.saturate()
        packet, _ = self.release()
        now = j_us()
        stop = self.server.send('STOP', dict(stop_revision='1', reason='test_stop',
            drain_deadline_j_us=str(now + 500_000)), issued_us=now, expires_us=now + 1_000_000)
        cpu = []
        with self.assertRaises(workers._StopWork):
            self.actor.await_sent(packet)
            cpu.append('entered')
        self.assertFalse(cpu)
        self.assertEqual(self.actor.stop.packet.raw_bytes, stop.raw_bytes)
        self.assertIs(self.actor.link.abort(), packet)

    def test_original_cleanup_expiry_prevents_flush_and_cpu_without_new_deadline(self):
        self.saturate()
        packet, message = self.release()
        original = packet.raw_bytes
        cpu = []
        with patch.object(workers, 'j_us', return_value=message.expires_us), \
                patch.object(self.actor.link, 'flush', wraps=self.actor.link.flush) as flush:
            with self.assertRaises(TimeoutError):
                self.actor.await_sent(packet)
                cpu.append('entered')
            flush.assert_not_called()
        self.assertFalse(cpu)
        self.assertIs(self.actor.link.abort(), packet)
        self.assertEqual(packet.raw_bytes, original)
        self.assertEqual(int(packet.fields['expires_j_us']), message.expires_us)

    def test_peer_close_while_unsent_prevents_cpu(self):
        self.saturate()
        packet, _ = self.release()
        self.server.close()
        cpu = []
        # unread outgoing filler가 있는 실제 Unix close는 EOF 또는 reset으로 관측될 수 있다.
        with self.assertRaises((EOFError, OSError)):
            self.actor.await_sent(packet)
            cpu.append('entered')
        self.assertFalse(cpu)

    def test_parser_start_original_500ms_cap_is_not_renewed_while_unsent(self):
        self.saturate()
        started = j_us()
        packet = self.actor.progress('PARSER_STARTED', 7, completed=started)
        original = packet.raw_bytes
        self.assertIsNone(self.actor.last_actual_sent)
        cpu = []
        with patch.object(workers, 'j_us', return_value=started + 500_000):
            with self.assertRaises(TimeoutError):
                self.actor.await_sent(packet, cap_us=started + 500_000)
                cpu.append('entered')
        self.assertFalse(cpu)
        self.assertIs(self.actor.link.abort(), packet)
        self.assertEqual(packet.raw_bytes, original)
        self.assertEqual(packet.payload['completed_j_us'], str(started))

    def test_already_sent_parser_start_retains_completion_after_progress_drain(self):
        started = j_us()
        packet = self.actor.progress('PARSER_STARTED', 7, completed=started)
        self.assertEqual(self.actor.link.take_sent(), ())
        actual = self.actor.await_sent(packet, cap_us=started + 500_000)
        self.assertIs(actual.packet, packet)
        self.assertLess(actual.completed_us, started + 500_000)
        self.assertEqual(self.right.recv(4096), packet.raw_bytes)


if __name__ == '__main__':
    unittest.main()
