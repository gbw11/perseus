"""실제 S 영상 helper/HTTP/socket의 연결 시험이다. OS worker4개 spawn 증거는 아니다.

Runtime은 명시 partial fixture이고 reaped/EOF/recovery 사실은 입력으로 선언한다.
실제 HTTP thread·socket 소유/반납과 원 typed RAW BIND 문맥을 검사한다. 독립 decode
메서드만 원 baseline JPEG를 native cv2로 해석하며 실제 OAK/NN/FPS 수락은 아니다.
"""

import socket
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

import raw_binding_fixtures as bindings
from xenova_vehicle.runtime.http_video import VideoHttpServer
from xenova_vehicle.runtime.jpeg_stream import BODY_CAP, JpegSender
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer, LifecycleReceiver
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.raw_binding_messages import RawBindContext
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.resident_recovery import ResidentRecovery
from xenova_vehicle.runtime.resident_runtime import _Runtime, _Worker
from test_jpeg_stream import Clock, jpeg


def original_bind(number=0):
    if not number:
        return bindings.bind_message(bindings.context('A', jpeg_enabled=True))
    acquisition = Endpoint(bindings.identifier(500 + number), bindings.identifier(600 + number))
    peer = LifecyclePeer(bindings.identifier(1), acquisition.channel_epoch,
        acquisition.worker_instance, 'A', 'f' * 64)
    context = RawBindContext(peer, acquisition, bindings.profile(jpeg_enabled=True))
    fields = bindings.binding_fields(jpeg_enabled=True)
    fields.update(acquisition_worker=peer.worker_instance, pipeline_instance=bindings.identifier(700 + number),
        capture_epoch=bindings.identifier(800 + number), sdk_bridge_epoch=bindings.identifier(900 + number))
    fields['stream_instances'] = {name: bindings.identifier(1000 + 10 * number + index)
        for index, name in enumerate(fields['stream_instances'])}
    return bindings.bind_message(context, binding=RawBinding(fields), request_number=300 + number)


def worker(original, now):
    host = _Worker(original.context.peer,
        Mock(spec=('terminate', 'kill', 'join', 'close', 'is_alive')),
        Mock(spec=('send', 'close', 'flush', 'poll')),
        LifecycleReceiver(original.context.peer, 'S'), now)
    host.bind = original
    return host


def runtime(original, clock):
    value = _Runtime.__new__(_Runtime)
    # 새 영상 필드는 실제 constructor 기본값에서 시작한다. native 자원/Device를 만들지 않는다.
    value.http_address = None
    value.http = None
    value.http_error = None
    value._video_owner = None
    value.began = clock.value
    value.stopping = False
    value.binding = original.binding
    value.workers = {}
    value.recovery = ResidentRecovery(value)
    value.failure = None
    value.event = Mock()
    return value


class RuntimeVideoWiringTests(unittest.TestCase):
    def until(self, predicate, server=None):
        end = time.monotonic() + 3.
        event = threading.Event()
        while time.monotonic() < end:
            if predicate():
                return
            if server is not None:
                self.assertIsNone(server.failure)
            event.wait(.002)
        self.fail('bounded actual video progress missing: ' + str(None if server is None else server.snapshot()))

    def shutdown_http(self, server):
        server.stop()
        self.assertTrue(server.join(3.), server.snapshot())
        self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)
        self.assertIsNone(server.failure)

    def stop_failed_http(self, server):
        server.stop()
        self.assertTrue(server.join(3.))

    def setup_http(self):
        clock = Clock()
        server = VideoHttpServer('127.0.0.1', 0, clock)
        self.addCleanup(self.shutdown_http, server)
        server.start()
        self.assertTrue(server.wait_ready(2.), server.failure)
        original = original_bind()
        value = runtime(original, clock)
        value.http_address, value.http = server.address, server
        host = worker(original, clock.value)
        value.workers['A'] = host
        return value, host, original, server, clock

    def attach(self, value, host, server, clock):
        writer, reader = socket.socketpair()
        self.addCleanup(writer.close)
        expected = server.snapshot()['counts'].get('channel_replaced', 0) + 1
        value._replace_video_channel(host, reader)
        self.until(lambda: server.snapshot()['counts'].get('channel_replaced', 0) == expected, server)
        self.assertIs(value._video_owner, host)
        return JpegSender(writer, clock)

    def frame(self, original, clock, *, sequence=1, extra=0):
        from test_oak_jpeg import jpeg_bytes
        fields = original.binding.fields
        return jpeg(clock, sequence=sequence, payload=jpeg_bytes(extra_bytes=extra),
            **{key: fields[key] for key in ('service_instance', 'acquisition_worker',
                'pipeline_instance', 'capture_epoch')})

    def deliver(self, sender, frame, server):
        expected = server.snapshot()['counts'].get('frames_received', 0) + 1
        self.assertTrue(sender.offer(frame))
        self.until(lambda: (sender.step(), sender.pending is None)[1], server)
        self.until(lambda: server.snapshot()['counts'].get('frames_received', 0) == expected, server)

    def connect(self, server, path):
        endpoint = socket.create_connection(server.address, timeout=2.)
        self.addCleanup(endpoint.close)
        endpoint.sendall(('GET ' + path + ' HTTP/1.0\r\n\r\n').encode('ascii'))
        return endpoint

    def read_all(self, endpoint):
        chunks = []
        total = 0
        while True:
            try:
                raw = endpoint.recv(65536)
            except ConnectionResetError:
                raw = b''
            if not raw:
                return b''.join(chunks)
            total += len(raw)
            self.assertLessEqual(total, BODY_CAP + 4096)
            chunks.append(raw)

    def assert_core_untouched(self, value, *hosts):
        self.assertIsNone(value.failure)
        self.assertEqual(value.event.mock_calls, [])
        for host in hosts:
            self.assertEqual(host.link.mock_calls, [])
            self.assertEqual(host.process.mock_calls, [])

    def test_actual_runtime_adopts_original_binding_scope_and_serves_original_jpeg(self):
        value, host, original, server, clock = self.setup_http()
        sender = self.attach(value, host, server, clock)
        frame = self.frame(original, clock)
        self.deliver(sender, frame, server)
        response = self.read_all(self.connect(server, '/camera/snapshot.jpg'))
        self.assertTrue(response.startswith(b'HTTP/1.0 200'))
        self.assertEqual(response.split(b'\r\n\r\n', 1)[1], frame.payload)
        self.assertEqual(frame.scope, tuple(original.binding.fields[key] for key in
            ('service_instance', 'acquisition_worker', 'pipeline_instance', 'capture_epoch')))
        self.assertIsNone(value.http_error)
        self.assert_core_untouched(value, host)

    def test_new_a_replacement_returns_old_clients_and_frames_before_new_scope(self):
        value, old, original, server, clock = self.setup_http()
        sender = self.attach(value, old, server, clock)
        frame = self.frame(original, clock)
        self.deliver(sender, frame, server)
        viewer = self.connect(server, '/camera/stream.mjpg')
        seen = b''
        while frame.payload not in seen:
            seen += viewer.recv(4096)
            self.assertLessEqual(len(seen), BODY_CAP + 4096)
        self.until(lambda: server.snapshot()['clients'] == 1, server)
        replacement = original_bind(1)
        new = worker(replacement, clock.value)
        value.binding = replacement.binding
        value.workers['A'] = new
        writer = self.attach(value, new, server, clock)
        self.assertFalse(server.snapshot()['latest'])
        self.assertEqual(server.snapshot()['clients'], 0)
        self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)
        self.read_all(viewer)
        response = self.read_all(self.connect(server, '/camera/snapshot.jpg'))
        self.assertTrue(response.startswith(b'HTTP/1.0 503'))
        clock.value += 200_000
        fresh = self.frame(replacement, clock, extra=8)
        self.deliver(writer, fresh, server)
        response = self.read_all(self.connect(server, '/camera/snapshot.jpg'))
        self.assertEqual(response.split(b'\r\n\r\n', 1)[1], fresh.payload)
        self.assertNotEqual(fresh.payload, frame.payload)
        self.assertNotEqual(fresh.scope, frame.scope)
        self.assert_core_untouched(value, old, new)

    def test_known_a_eof_reaped_or_recovery_fact_detaches_video(self):
        for fact in ('eof', 'reaped', 'recovery'):
            with self.subTest(fact=fact):
                value, host, original, server, clock = self.setup_http()
                sender = self.attach(value, host, server, clock)
                self.deliver(sender, self.frame(original, clock), server)
                if fact == 'recovery':
                    value.recovery.active = NS(old={'A': host})
                else:
                    setattr(host, fact, True)
                value._refresh_video()
                self.assertIsNone(value._video_owner)
                self.until(lambda: not server.snapshot()['video_available'], server)
                self.assertFalse(server.snapshot()['latest'])
                self.assertEqual(server.snapshot()['owned_jpeg_bytes'], 0)
                self.assert_core_untouched(value, host)
                self.shutdown_http(server)

    def test_mapper_or_storage_only_recovery_retains_actual_a_video_channel(self):
        value, host, original, server, clock = self.setup_http()
        sender = self.attach(value, host, server, clock)
        frame = self.frame(original, clock)
        self.deliver(sender, frame, server)
        for role in ('M', 'D'):
            with self.subTest(role=role):
                value.recovery.active = NS(old={role: object()})
                value._refresh_video()
                self.assertIs(value._video_owner, host)
                response = self.read_all(self.connect(server, '/camera/snapshot.jpg'))
                self.assertEqual(response.split(b'\r\n\r\n', 1)[1], frame.payload)
                self.assertEqual(server.snapshot()['counts']['channel_replaced'], 1)
        self.assertEqual(frame.expires_j_us, 1_499_000)
        self.assert_core_untouched(value, host)

    def test_disabled_or_failed_http_returns_fd_without_device_or_core_commands(self):
        clock = Clock()
        original = original_bind()
        disabled = runtime(original, clock)
        host = worker(original, clock.value)
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        disabled._replace_video_channel(host, right)
        self.assertEqual(right.fileno(), -1)
        self.assertIsNone(disabled._video_owner)
        self.assert_core_untouched(disabled, host)

        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(occupied.close)
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        failed = VideoHttpServer(*occupied.getsockname(), clock)
        self.addCleanup(self.stop_failed_http, failed)
        failed.start()
        self.assertFalse(failed.wait_ready(2.))
        self.assertTrue(failed.join(2.))
        self.assertIsNotNone(failed.failure)
        caller, incoming = socket.socketpair()
        self.addCleanup(caller.close)
        self.addCleanup(incoming.close)
        with self.assertRaises(RuntimeError):
            failed.replace_channel(incoming, tuple(original.binding.fields[k] for k in
                ('service_instance', 'acquisition_worker', 'pipeline_instance', 'capture_epoch')))
        self.assertGreaterEqual(incoming.fileno(), 0)
        failed.stop()
        self.assertGreaterEqual(incoming.fileno(), 0)
        incoming.close()
        self.assertEqual(incoming.fileno(), -1)
        self.assertIsNone(failed._command)
        unavailable = runtime(original, clock)
        unavailable.http, unavailable.http_address = failed, occupied.getsockname()
        unavailable.workers['A'] = host
        caller2, incoming2 = socket.socketpair()
        self.addCleanup(caller2.close)
        unavailable._replace_video_channel(host, incoming2)
        self.assertEqual(incoming2.fileno(), -1)
        self.assertEqual(unavailable.http_error, 'RuntimeError')
        self.assertIsNone(unavailable._video_owner)
        self.assert_core_untouched(unavailable, host)

    def test_shutdown_is_not_complete_while_actual_http_thread_is_alive(self):
        value, _, _, server, _ = self.setup_http()
        # 다른 owner 정리가 끝난 국소 단계다. 실제 worker/native 회수 성공을 만들지 않는다.
        value.workers = {}
        value._stop_workers = Mock()
        value.log_pending = None
        value.native_closed = True
        value.db_copy_cleanup = None
        value.cleanup_failed = False
        value.native_started = False
        value.cleanup_attempted = False
        value.candidate_written = True
        value.manifest = None
        value.log_stop_requested = False
        value.log = NS(is_alive=False, request_stop=Mock())
        self.assertIs(value._shutdown(), False)
        self.assertTrue(server.is_alive)
        value.log.request_stop.assert_called_once_with()
        self.shutdown_http(server)
        self.assertIs(value._shutdown(), True)

    def test_baseline_jpeg_fixture_decodes_to_actual_gray640x360(self):
        import cv2
        import numpy as np
        from test_oak_jpeg import jpeg_bytes
        original = jpeg_bytes()
        image = cv2.imdecode(np.frombuffer(original, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(image)
        self.assertEqual(image.shape, (360, 640))
        self.assertEqual(image.dtype, np.dtype('uint8'))
        self.assertTrue(bool(np.all(image == 128)))


if __name__ == '__main__':
    unittest.main()
