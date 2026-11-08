"""localhost 실제 selector/thread/stream의 유한 HTTP 영상 경계를 검사한다.

JPEG 본문은 opaque software bytes이며 보정/SDK/물리 FPS 수락이 아니다.
명시 논리 시계는 원 deadline 경계를 재현하고 TCP/UDS I/O 자체는 실제로 수행한다.
"""

import socket
import threading
import time
import unittest
from unittest.mock import patch
from uuid import UUID

from xenova_vehicle.runtime.http_video import VideoHttpServer, JPEG_OWNED_CAP
from xenova_vehicle.runtime.jpeg_stream import BODY_CAP, JpegSender
from test_jpeg_stream import Clock, SCOPE, jpeg, wire


class HttpVideoTests(unittest.TestCase):
    def server(self):
        clock = Clock()
        server = VideoHttpServer('127.0.0.1', 0, clock)
        self.addCleanup(self.shutdown, server)
        server.start()
        self.assertTrue(server.wait_ready(2.), server.failure)
        self.assertTrue(server.is_alive)
        self.assertFalse(server._thread.daemon)
        return server, clock

    def shutdown(self, server):
        server.stop()
        self.assertTrue(server.join(3.), server.snapshot())
        self.assertIsNone(server.failure)
        self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)
        self.assertEqual(server.snapshot()['viewer_inflight'], 0)

    def until(self, predicate, *, server=None, timeout=3.):
        end = time.monotonic() + timeout
        event = threading.Event()
        while time.monotonic() < end:
            if predicate():
                return
            if server is not None:
                self.assertIsNone(server.failure)
            event.wait(.002)
        self.fail('bounded socket/selector progress missing: ' + str(None if server is None else server.snapshot()))

    def channel(self, server, clock, scope=SCOPE):
        writer, reader = socket.socketpair()
        self.addCleanup(writer.close)
        server.replace_channel(reader, scope)
        self.until(lambda: server.snapshot()['video_available'], server=server)
        return JpegSender(writer, clock)

    def deliver(self, server, sender, frame):
        expected = server.snapshot()['counts'].get('frames_received', 0) + 1
        self.assertTrue(sender.offer(frame))
        self.until(lambda: (sender.step(), sender.pending is None)[1], server=server)
        self.until(lambda: server.snapshot()['counts'].get('frames_received', 0) >= expected, server=server)

    def client(self, server, path=None, *, small_receive=False):
        endpoint = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if small_receive:
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        endpoint.settimeout(2.)
        endpoint.connect(server.address)
        self.addCleanup(endpoint.close)
        if path is not None:
            endpoint.sendall(('GET ' + path + ' HTTP/1.0\r\n\r\n').encode('ascii'))
        return endpoint

    def response(self, endpoint):
        parts = []
        total = 0
        while True:
            try:
                raw = endpoint.recv(65536)
            except ConnectionResetError:
                return b''.join(parts)
            if not raw:
                return b''.join(parts)
            parts.append(raw)
            total += len(raw)
            self.assertLessEqual(total, BODY_CAP + 4096)

    def test_optional_channel_is_503_and_only_two_legacy_get_routes_exist(self):
        server, _ = self.server()
        response = self.response(self.client(server, '/camera/snapshot.jpg'))
        self.assertTrue(response.startswith(b'HTTP/1.0 503'))
        response = self.response(self.client(server, '/status'))
        self.assertTrue(response.startswith(b'HTTP/1.0 404'))
        response = self.response(self.client(server, '/camera/stream.mjpg'))
        self.assertTrue(response.startswith(b'HTTP/1.0 503'))

    def test_actual_snapshot_and_two_multipart_frames_share_original_payload(self):
        server, clock = self.server()
        sender = self.channel(server, clock)
        first = jpeg(clock, payload=b'\xff\xd8first-original\xff\xd9')
        self.deliver(server, sender, first)
        response = self.response(self.client(server, '/camera/snapshot.jpg'))
        self.assertEqual(response.split(b'\r\n\r\n', 1)[1], first.payload)
        self.assertIn(b'Cache-Control: no-store', response)
        viewer = self.client(server, '/camera/stream.mjpg')
        data = b''
        while first.payload not in data:
            data += viewer.recv(4096)
            self.assertLess(len(data), 8192)
        self.assertIn(b'multipart/x-mixed-replace; boundary=xenova-frame', data)
        clock.value += 200_000
        second = jpeg(clock, sequence=2, payload=b'\xff\xd8second-original\xff\xd9')
        self.deliver(server, sender, second)
        while second.payload not in data:
            data += viewer.recv(4096)
            self.assertLess(len(data), 16384)
        self.assertEqual(data.count(b'HTTP/1.0 200'), 1)
        self.assertLessEqual(server.snapshot()['counts']['owned_peak'], JPEG_OWNED_CAP)
        clock.value = second.expires_j_us
        self.until(lambda: not server.snapshot()['latest'], server=server)
        self.assertTrue(self.response(self.client(server, '/camera/snapshot.jpg')).startswith(b'HTTP/1.0 503'))

    def test_two_partial_requests_and_third_viewer_are_bounded(self):
        server, clock = self.server()
        first, second = self.client(server), self.client(server)
        first.sendall(b'GET /camera/')
        second.sendall(b'GET /camera/')
        self.until(lambda: server.snapshot()['clients'] == 2, server=server)
        busy = self.response(self.client(server, '/camera/snapshot.jpg'))
        self.assertTrue(not busy or busy.startswith(b'HTTP/1.0 503'))
        clock.value += 250_000
        self.until(lambda: server.snapshot()['clients'] == 0, server=server)
        self.assertEqual(first.recv(1), b'')
        self.assertEqual(second.recv(1), b'')
        self.assertEqual(server.snapshot()['counts']['busy_clients'], 1)
        expected = server.snapshot()['counts']['clients_closed'] + 1
        oversized = self.client(server)
        oversized.sendall(b'GET /camera/snapshot.jpg HTTP/1.0\r\nX: ' + b'X' * 4096 + b'\r\n\r\n')
        self.until(lambda: server.snapshot()['counts'].get('clients_closed', 0) >= expected, server=server)
        self.assertEqual(self.response(oversized), b'')

    def test_snapshot_reconnect_and_channel_replace_do_not_refund_two_send_slots(self):
        server, clock = self.server()
        sender = self.channel(server, clock)
        original = jpeg(clock)
        original_expiry = original.expires_j_us
        self.deliver(server, sender, original)

        def snapshot(status, payload=None):
            response = self.response(self.client(server, '/camera/snapshot.jpg'))
            self.assertTrue(response.startswith(b'HTTP/1.0 ' + status), response[:100])
            if payload is not None:
                self.assertEqual(response.split(b'\r\n\r\n', 1)[1], payload)

        snapshot(b'200', original.payload)
        snapshot(b'200', original.payload)
        snapshot(b'503')
        clock.value += 199_999
        snapshot(b'503')
        clock.value += 1
        snapshot(b'200', original.payload)
        snapshot(b'200', original.payload)
        self.assertEqual(original.expires_j_us, original_expiry)

        writer, reader = socket.socketpair()
        self.addCleanup(writer.close)
        server.replace_channel(reader, SCOPE)
        self.until(lambda: server.snapshot()['counts'].get('channel_replaced') == 2, server=server)
        fresh = jpeg(clock, sequence=2, payload=b'\xff\xd8new-channel\xff\xd9')
        self.deliver(server, JpegSender(writer, clock), fresh)
        snapshot(b'503')
        clock.value += 200_000
        snapshot(b'200', fresh.payload)
        self.assertEqual(fresh.expires_j_us, int(fresh.fields['capture_j_lower_us']) + 500_000)

    def test_partial_ingress_times_out_without_more_read_events_and_drops_only_video(self):
        server, clock = self.server()
        writer, reader = socket.socketpair()
        self.addCleanup(writer.close)
        server.replace_channel(reader, SCOPE)
        self.until(lambda: server.snapshot()['video_available'], server=server)
        frame = jpeg(clock, payload=b'X' * 4096)
        original = wire(frame)
        writer.sendall(original[:8 + len(frame.header_bytes) + 7])
        self.until(lambda: server.snapshot()['ingress_bytes'] == len(frame.payload), server=server)
        clock.value += 250_000
        self.until(lambda: not server.snapshot()['video_available'], server=server)
        self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)
        self.assertTrue(server.is_alive)
        self.assertIsNone(server.failure)
        self.assertTrue(self.response(self.client(server, '/camera/snapshot.jpg')).startswith(b'HTTP/1.0 503'))

    def test_crc_fault_and_replacement_clear_old_scope_without_core_restart(self):
        server, clock = self.server()
        writer, reader = socket.socketpair()
        self.addCleanup(writer.close)
        server.replace_channel(reader, SCOPE)
        self.until(lambda: server.snapshot()['video_available'], server=server)
        frame = jpeg(clock)
        writer.sendall(wire(frame, payload=b'X' * len(frame.payload)))
        self.until(lambda: server.snapshot()['counts'].get('video_faults') == 1, server=server)
        self.assertFalse(server.snapshot()['latest'])
        self.assertIsNone(server.failure)
        sender = self.channel(server, clock)
        self.deliver(server, sender, frame)
        new_writer, new_reader = socket.socketpair()
        self.addCleanup(new_writer.close)
        new_scope = (SCOPE[0], str(UUID(int=99)), SCOPE[2], SCOPE[3])
        server.replace_channel(new_reader, new_scope)
        self.until(lambda: server.snapshot()['counts'].get('channel_replaced', 0) == 3, server=server)
        self.assertFalse(server.snapshot()['latest'])
        self.assertTrue(self.response(self.client(server, '/camera/snapshot.jpg')).startswith(b'HTTP/1.0 503'))
        fields = frame.fields
        fields['acquisition_worker'] = new_scope[1]
        from xenova_vehicle.runtime.jpeg_stream import EncodedJpeg
        replacement = EncodedJpeg(fields, frame.payload)
        self.deliver(server, JpegSender(new_writer, clock), replacement)
        self.assertTrue(self.response(self.client(server, '/camera/snapshot.jpg')).endswith(frame.payload))
        new_writer.close()
        self.until(lambda: not server.snapshot()['video_available'], server=server)
        self.assertFalse(server.snapshot()['latest'])
        self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)
        self.assertTrue(server.is_alive)

    def test_actual_tcp_backpressure_releases_both_viewer_refs_at_original_write_cap(self):
        server, clock = self.server()
        sender = self.channel(server, clock)
        self.deliver(server, sender, jpeg(clock, payload=b'J' * BODY_CAP))
        actual_send = socket.socket.send

        def bounded_actual_send(endpoint, data, *args, **kwargs):
            if threading.current_thread().name == 'S-http' and endpoint.family == socket.AF_INET:
                endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
            return actual_send(endpoint, data, *args, **kwargs)

        # 원 실제 send를 유지하고 kernel buffer만 fixture에서 줄여 실제 EAGAIN을 만든다.
        with patch.object(socket.socket, 'send', bounded_actual_send):
            self.client(server, '/camera/stream.mjpg', small_receive=True)
            self.client(server, '/camera/stream.mjpg', small_receive=True)
            self.until(lambda: server.snapshot()['viewer_inflight'] == 2, server=server)
            clock.value += 250_000
            self.until(lambda: server.snapshot()['clients'] == 0, server=server)
        self.assertGreaterEqual(server.snapshot()['counts']['viewer_frames_released'], 2)
        self.assertLessEqual(server.snapshot()['counts']['owned_peak'], JPEG_OWNED_CAP)
        server.stop()
        self.assertTrue(server.join(3.))
        self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)

    def test_stop_before_start_returns_pending_channel_and_wakeup_fds(self):
        clock = Clock()
        writer, reader = socket.socketpair()
        self.addCleanup(writer.close)
        server = VideoHttpServer('127.0.0.1', 0, clock)
        server.replace_channel(reader, SCOPE)
        server.stop()
        self.assertTrue(server.join(0))
        self.assertEqual(reader.fileno(), -1)
        self.assertEqual(server._wake_read.fileno(), -1)
        self.assertEqual(server._wake_write.fileno(), -1)


if __name__ == '__main__':
    unittest.main()
