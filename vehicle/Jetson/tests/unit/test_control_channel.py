"""제어 channel의 packet 상한과 backpressure·부분 읽기 경계를 검증한다."""

import socket
import struct
import unittest

from xenova_vehicle.runtime.channel import ControlChannel, control_pair


class ChannelTests(unittest.TestCase):
    def channels(self, stream=False):
        if stream:
            sockets = socket.socketpair()
            for endpoint in sockets:
                endpoint.setblocking(False)
        else:
            sockets = control_pair()
        a, b = (ControlChannel(s) for s in sockets)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, b

    def test_control_roundtrip_and_eof(self):
        a, b = self.channels()
        a.send({"kind": "stop", "reason": "duration_elapsed"})
        self.assertEqual(b.receive()[0]["reason"], "duration_elapsed")
        a.close()
        with self.assertRaises(EOFError):
            b.receive()

    def test_oversized_and_nonfinite_control_are_rejected(self):
        a, _ = self.channels()
        for value in ({"x": "x" * 4096}, {"x": float("nan")}):
            with self.assertRaises(ValueError):
                a.send(value)

    def test_stream_partial_frame_and_multiple_messages(self):
        a, b = self.channels(stream=True)
        raw = b'{"kind":"status"}'
        packet = struct.pack("<I", len(raw)) + raw
        a.endpoint.send(packet[:6])
        self.assertEqual(b.receive(), [])
        a.endpoint.send(packet[6:] + packet)
        self.assertEqual(len(b.receive()), 2)

    def test_bad_stream_length_is_rejected(self):
        a, b = self.channels(stream=True)
        a.endpoint.send(struct.pack("<I", 4097))
        with self.assertRaises(ValueError):
            b.receive()

    def test_receive_does_not_process_unbounded_small_packets(self):
        a, b = self.channels(stream=True)
        raw = b'{}'
        a.endpoint.send((struct.pack("<I", len(raw)) + raw) * 40)
        self.assertEqual(len(b.receive()), 16)
        self.assertEqual(len(b.receive()), 16)
        self.assertEqual(len(b.receive()), 8)
