"""실제 Unix 통로의 pending/terminal 회귀 두 개만 확인한다.

송신 대기는 proxy의 Python BlockingIOError 제어 주입이며 커널 EAGAIN 재현이
아니다. 기한용 J clock도 제어한다. 실제 송신 bytes와 peer EOF는 socketpair로
확인한다. Link에는 lease/token handle을 넘기지 않으며 정리 권한은 actor에 남는다.
"""

import errno
import socket
import sys
import unittest
from unittest.mock import patch
import uuid

from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.resident_transport import ResidentLink, TransportError


class SendGate:
    """하나의 제어된 대기 구간 뒤 같은 원 bytes를 실제 socket에 전달한다."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.blocked = True
        self.attempts = []
        self.close_calls = 0

    def __getattr__(self, name):
        return getattr(self.endpoint, name)

    def send(self, raw):
        self.attempts.append(raw)
        if self.blocked:
            raise BlockingIOError(errno.EAGAIN, 'controlled send wait; not actual kernel saturation')
        return self.endpoint.send(raw)

    def close(self):
        self.close_calls += 1
        self.endpoint.close()


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux AF_UNIX/SOCK_SEQPACKET 통로 시험')
class ResidentTransportTests(unittest.TestCase):
    def setUp(self):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        peer = LifecyclePeer(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 'D', 'a' * 64)
        self.gate = SendGate(parent)
        self.sender = ResidentLink(self.gate, peer, 'S', track_sent=True)
        self.receiver = ResidentLink(child, peer, 'D')
        self.addCleanup(self.sender.close)
        self.addCleanup(self.receiver.close)
        self.now = 1000
        clock = patch('xenova_vehicle.runtime.resident_transport.j_us', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def first_pending(self):
        request_id = str(uuid.uuid4())
        packet = self.sender.send('DB_JOB', dict(op='DISCOVER', body=dict(discovery_nonce=str(uuid.uuid4()),
                                  storage_profile_sha256='b' * 64)), domain='STORAGE', revision=0,
                                  request_id=request_id, issued_us=1000, expires_us=2000)
        self.assertIsNotNone(packet)
        self.assertEqual(packet.fields['request_id'], request_id)
        self.assertEqual(packet.fields['issued_j_us'], '1000')
        self.assertEqual(packet.fields['expires_j_us'], '2000')
        return packet

    def test_pending_busy_has_no_new_sequence_and_retries_original_packet(self):
        first = self.first_pending()
        stop_id = str(uuid.uuid4())
        stop_payload = dict(stop_revision='1', reason='normal_shutdown', drain_deadline_j_us='1900')
        rejected = self.sender.send('STOP', stop_payload, request_id=stop_id, issued_us=1100, expires_us=2000)
        self.assertIsNone(rejected)
        self.assertEqual(self.gate.attempts, [first.raw_bytes, first.raw_bytes])
        self.assertEqual(self.sender.take_sent(), ())
        self.assertEqual(self.receiver.poll(), [])
        self.now = 1200
        self.gate.blocked = False
        self.sender.flush()
        received = self.receiver.poll()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].raw_bytes, first.raw_bytes)
        self.assertEqual(received[0].fields['message_sequence'], '1')
        stop = self.sender.send('STOP', stop_payload, request_id=stop_id, issued_us=1100, expires_us=2000)
        self.assertEqual(stop.fields['message_sequence'], '2')
        self.assertEqual(self.receiver.poll()[0].raw_bytes, stop.raw_bytes)
        completed = self.sender.take_sent()
        self.assertEqual(len(completed), 2)
        self.assertIs(completed[0].packet, first)
        self.assertIs(completed[1].packet, stop)

    def test_expired_pending_abort_preserves_packet_and_closes_only_channel(self):
        first = self.first_pending()
        self.now = 2000
        with self.assertRaises(TransportError):
            self.sender.flush()
        self.assertEqual(self.gate.attempts, [first.raw_bytes])
        retained = self.sender.abort()
        self.assertIs(retained, first)
        self.assertEqual(retained.raw_bytes, first.raw_bytes)
        self.assertTrue(self.sender.closed)
        self.assertEqual(self.gate.endpoint.fileno(), -1)
        self.assertEqual(self.gate.close_calls, 1)
        self.sender.flush()
        self.assertIs(self.sender.abort(), first)
        self.assertEqual(self.gate.close_calls, 1)
        self.assertEqual(self.gate.attempts, [first.raw_bytes])
        self.assertEqual(self.sender.take_sent(), ())
        with self.assertRaises(EOFError):
            self.receiver.poll()
