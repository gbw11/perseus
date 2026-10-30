"""SerialSink 의 포트 열기 경로 시험 (장치 경로·socket:// TCP)."""

from __future__ import annotations

import socket
import sys
import time
import types

import pytest

from xenova_gateway.frame import DriveFrame, StopFrame, encode
from xenova_gateway.sink import SerialSink, SinkError


class _FakeSerial:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_serial(monkeypatch):
    calls: list[tuple[str, dict]] = []
    module = types.ModuleType("serial")

    def serial_for_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeSerial()

    def _serial_ctor(*_a, **_k):  # 직접 생성자를 쓰면 URL 을 못 연다
        raise AssertionError("serial.Serial 대신 serial_for_url 을 써야 한다")

    module.serial_for_url = serial_for_url
    module.Serial = _serial_ctor
    monkeypatch.setitem(sys.modules, "serial", module)
    return calls


@pytest.mark.parametrize("port", ["/dev/ttyUSB0", "socket://192.168.0.10:7600"])
def test_serial_sink_opens_port_with_serial_for_url(fake_serial, port):
    sink = SerialSink(port, baudrate=115200)
    assert fake_serial == [(port, {"baudrate": 115200, "timeout": 0, "write_timeout": 0.005})]
    frame = DriveFrame(3, 1, 0.5, -0.2)
    assert sink.write(frame, 0.0) == len(encode(frame))
    assert sink._serial.written == [encode(frame)]
    sink.close()
    assert sink._serial.closed


def test_serial_sink_open_failure_is_sink_error(monkeypatch):
    module = types.ModuleType("serial")

    def serial_for_url(url, **kwargs):
        raise OSError("connection refused")

    module.serial_for_url = serial_for_url
    monkeypatch.setitem(sys.modules, "serial", module)
    with pytest.raises(SinkError, match="socket://127.0.0.1:1"):
        SerialSink("socket://127.0.0.1:1")


def _listen() -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(5)
    return srv


def test_socket_url_delivers_encoded_frames_over_tcp():
    pytest.importorskip("serial")
    srv = _listen()
    port = srv.getsockname()[1]
    try:
        sink = SerialSink(f"socket://127.0.0.1:{port}")
        conn, _ = srv.accept()
        frames = [DriveFrame(5, 1, 0.75, 0.1), DriveFrame(5, 2, -0.3, -0.25), StopFrame(5, 3, 1)]
        expected = b"".join(encode(f) for f in frames)
        for i, f in enumerate(frames):
            assert sink.write(f, float(i)) == len(encode(f))
        conn.settimeout(5)
        got = b""
        while len(got) < len(expected):
            chunk = conn.recv(4096)
            assert chunk, "서버가 프레임을 다 받기 전에 연결이 닫혔다"
            got += chunk
        assert got == expected
        sink.close()
        conn.close()
    finally:
        srv.close()


def test_socket_url_connect_failure_is_sink_error():
    pytest.importorskip("serial")
    srv = _listen()
    port = srv.getsockname()[1]
    srv.close()  # 듣는 쪽이 없는 포트
    with pytest.raises(SinkError):
        SerialSink(f"socket://127.0.0.1:{port}")


def test_socket_url_peer_close_becomes_sink_error():
    pytest.importorskip("serial")
    srv = _listen()
    port = srv.getsockname()[1]
    try:
        sink = SerialSink(f"socket://127.0.0.1:{port}")
        conn, _ = srv.accept()
        conn.close()  # 수신 쪽이 끊긴다 → 이어지는 쓰기가 RST/EPIPE 로 실패해야 한다
        frame = DriveFrame(1, 1, 0.0, 0.0)
        with pytest.raises(SinkError):
            for _ in range(200):
                sink.write(frame, 0.0)
                time.sleep(0.01)
        assert sink.failures == 1
        sink.close()
    finally:
        srv.close()
