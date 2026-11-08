"""실제 socketpair의 bounded JPEG framing/원 기한을 검사한다. 영상 decode 시험은 아니다."""

import json
import socket
import struct
import unittest
from uuid import UUID
import zlib

from xenova_vehicle.runtime.jpeg_stream import (BODY_CAP, EncodedJpeg, JpegError,
    JpegReceiver, JpegSender, JpegStreamError, TRANSFER_US)


SCOPE = tuple(str(UUID(int=n)) for n in (1, 2, 3, 4))


class Clock:
    def __init__(self):
        self.value = 1_000_000

    def __call__(self):
        return self.value


def jpeg(clock, *, sequence=1, payload=b'\xff\xd8original\xff\xd9', **changes):
    fields = dict(version=1, service_instance=SCOPE[0], acquisition_worker=SCOPE[1],
        pipeline_instance=SCOPE[2], capture_epoch=SCOPE[3], frame_sequence=str(sequence),
        capture_j_lower_us=str(clock.value - 1000), capture_j_upper_us=str(clock.value - 500),
        completed_j_us=str(clock.value), expires_j_us=str(clock.value + 499_000),
        width=640, height=360, encoding='MJPEG', payload_length=len(payload), crc32=zlib.crc32(payload))
    fields.update(changes)
    return EncodedJpeg(fields, payload)


def wire(frame, *, header=None, payload=None):
    header = frame.header_bytes if header is None else header
    payload = frame.payload if payload is None else payload
    return struct.pack('<II', len(header), len(payload)) + header + payload


class JpegStreamTests(unittest.TestCase):
    def pair(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        return left, right

    def test_exact_header_crc_scope_and_original_immutable_bytes(self):
        clock = Clock()
        payload = b'\xff\xd8owned\xff\xd9'
        frame = jpeg(clock, payload=payload)
        self.assertIs(frame.payload, payload)
        self.assertEqual(frame.scope, SCOPE)
        self.assertEqual(frame.sequence, 1)
        fields = frame.fields
        fields['expires_j_us'] = '0'
        self.assertEqual(frame.expires_j_us, 1_499_000)
        self.assertEqual(json.loads(frame.header_bytes), frame.fields)
        for changes in ({'version': True}, {'width': 320}, {'encoding': 'JPEG'},
                {'frame_sequence': '01'}, {'completed_j_us': '1499000'},
                {'expires_j_us': '1500001'}, {'crc32': 0}, {'extra': None}):
            with self.subTest(changes=changes), self.assertRaises(JpegError):
                jpeg(clock, **changes)

    def test_actual_socketpair_round_trip_and_single_inflight_without_retime(self):
        clock = Clock()
        left, right = self.pair()
        sender, receiver = JpegSender(left, clock), JpegReceiver(right, SCOPE, clock)
        frame = jpeg(clock, payload=b'j' * BODY_CAP)
        self.assertTrue(sender.offer(frame))
        self.assertFalse(sender.offer(jpeg(clock, sequence=2)))
        sent = received = None
        for _ in range(100):
            sent = sender.step() or sent
            received = receiver.step() or received
            if sent is not None and received is not None:
                break
        self.assertIs(sent, frame)
        self.assertEqual(received.fields, frame.fields)
        self.assertEqual(received.payload, frame.payload)
        self.assertEqual(received.header_bytes, frame.header_bytes)
        self.assertEqual(receiver.buffered_bytes, 0)
        self.assertIsNone(sender.pending)
        callbacks = []
        self.assertTrue(sender.offer(frame))
        for _ in range(100):
            sender.step()
            self.assertIsNone(receiver.step(before_complete=lambda: callbacks.append(True)))
            if sender.pending is None and receiver.buffered_bytes == 0:
                break
        self.assertEqual(callbacks, [])
        self.assertEqual(frame.expires_j_us, received.expires_j_us)

    def test_partial_prefix_header_and_body_keep_one_original_deadline(self):
        clock = Clock()
        left, right = self.pair()
        receiver = JpegReceiver(right, SCOPE, clock)
        frame = jpeg(clock)
        original = wire(frame)
        left.sendall(original[:3])
        self.assertIsNone(receiver.step())
        clock.value += TRANSFER_US - 1
        left.sendall(original[3:8 + len(frame.header_bytes) + 1])
        self.assertIsNone(receiver.step())
        self.assertEqual(receiver.buffered_bytes, len(frame.payload))
        clock.value += 1
        with self.assertRaises(JpegStreamError):
            receiver.step()
        self.assertTrue(receiver.closed)
        self.assertEqual(receiver.buffered_bytes, 0)

    def test_original_completed_time_counts_kernel_buffer_wait(self):
        for delta in (TRANSFER_US - 1, TRANSFER_US):
            with self.subTest(delta=delta):
                clock = Clock()
                frame = jpeg(clock)
                left, right = self.pair()
                receiver = JpegReceiver(right, SCOPE, clock)
                left.sendall(wire(frame))
                clock.value += delta
                if delta < TRANSFER_US:
                    self.assertEqual(receiver.step().payload, frame.payload)
                else:
                    with self.assertRaises(JpegStreamError):
                        receiver.step()
                    self.assertTrue(receiver.closed)
                    sender_left, sender_right = self.pair()
                    self.assertFalse(JpegSender(sender_left, clock).offer(frame))

    def test_oversize_is_rejected_before_body_allocation(self):
        for header_size, body_size in ((1025, 1), (1, BODY_CAP + 1), (0, 1), (1, 0)):
            with self.subTest(sizes=(header_size, body_size)):
                clock = Clock()
                left, right = self.pair()
                receiver = JpegReceiver(right, SCOPE, clock)
                left.sendall(struct.pack('<II', header_size, body_size))
                with self.assertRaises(JpegStreamError):
                    receiver.step()
                self.assertTrue(receiver.closed)
                self.assertEqual(receiver.buffered_bytes, 0)

    def test_crc_duplicate_header_and_foreign_scope_close_video_only(self):
        clock = Clock()
        frame = jpeg(clock)
        bad = frame.fields
        bad['acquisition_worker'] = str(UUID(int=99))
        headers = (frame.header_bytes.replace(b'"version":1', b'"version":1,"version":1'),
            json.dumps(bad).encode(), frame.header_bytes)
        for index, header in enumerate(headers):
            with self.subTest(case=index):
                left, right = self.pair()
                receiver = JpegReceiver(right, SCOPE, clock)
                payload = b'X' * len(frame.payload) if index == 2 else frame.payload
                left.sendall(wire(frame, header=header, payload=payload))
                with self.assertRaises(JpegStreamError):
                    receiver.step()
                self.assertTrue(receiver.closed)
                self.assertEqual(receiver.buffered_bytes, 0)

    def test_actual_send_backpressure_expires_without_accepting_next_frame(self):
        clock = Clock()
        left, right = self.pair()
        left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        sender = JpegSender(left, clock)
        frame = jpeg(clock, payload=b'j' * BODY_CAP)
        self.assertTrue(sender.offer(frame))
        for _ in range(20):
            self.assertIsNone(sender.step())
        self.assertIs(sender.pending, frame)
        self.assertFalse(sender.offer(jpeg(clock, sequence=2)))
        clock.value += TRANSFER_US
        with self.assertRaises(JpegStreamError):
            sender.step()
        self.assertTrue(sender.closed)
        self.assertIsNone(sender.pending)
        self.assertFalse(right._closed)

    def test_eof_after_partial_does_not_retain_ingress(self):
        clock = Clock()
        left, right = self.pair()
        receiver = JpegReceiver(right, SCOPE, clock)
        left.sendall(b'\x01\x00')
        left.close()
        with self.assertRaises(JpegStreamError):
            receiver.step()
        self.assertTrue(receiver.closed)
        self.assertEqual(receiver.buffered_bytes, 0)


if __name__ == '__main__':
    unittest.main()
