"""410 Plan B: display-domU frame sender and the real dom0 receiver over a local socket."""
import http.client
import json
import shutil
import threading
import time

import pytest

from guest_jpeg import fake_jpeg
from xenova_display.guest_frames import GuestFrameReceiver, GuestSource
from xenova_display.guest_sender import (CaptureWorker, LatestFrame, bind_address, capture_command,
                                         read_token, serve)

TOKEN = "guest-stream-token"


def wait_until(check, seconds=5):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if check(): return True
        time.sleep(.05)
    return False


def test_token_file_must_be_private(tmp_path):
    path = tmp_path/"token"
    path.write_text(TOKEN+"\n")
    path.chmod(0o644)
    with pytest.raises(ValueError): read_token(path)
    path.chmod(0o600)
    assert read_token(path) == TOKEN
    path.write_text("")
    with pytest.raises(ValueError): read_token(path)


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "127.0.0.1", "not-an-ip"])
def test_sender_binds_only_to_a_private_guest_address(host):
    with pytest.raises(ValueError): bind_address(host)
    assert bind_address("10.10.0.13") == "10.10.0.13"
    assert bind_address("127.0.0.1", allow_loopback=True) == "127.0.0.1"


def test_capture_command_is_bounded_and_marks_synthetic_source():
    assert "x11grab" in capture_command("x11", display=":0")
    assert "lavfi" in capture_command("testsrc")
    for bad in [dict(source="x11", display="remote:0"), dict(source="file"),
                dict(source="x11", width=4000), dict(source="x11", fps=60)]:
        with pytest.raises(ValueError): capture_command(**bad)


@pytest.fixture
def sender():
    latest = LatestFrame()
    server = serve("127.0.0.1", 0, TOKEN, latest, source="testsrc")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
    thread.start()
    yield latest, server.server_address[1]
    server.stopping.set()
    server.shutdown()
    server.server_close()


def request(port, path, token=TOKEN):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {})
    return connection, connection.getresponse()


def test_sender_requires_token_and_reports_synthetic_status(sender):
    latest, port = sender
    for token in (None, "wrong"):
        connection, response = request(port, "/status", token)
        assert response.status == 401
        connection.close()
    latest.publish(fake_jpeg())
    connection, response = request(port, "/status")
    body = json.loads(response.read())
    connection.close()
    assert body["status"] == "live" and body["synthetic"] is True and body["rendered_by"] == "domU"
    assert TOKEN not in json.dumps(body)


def test_sender_limits_concurrent_streams(sender):
    latest, port = sender
    latest.publish(fake_jpeg())
    opened = [request(port, "/frames.mjpeg") for _ in range(2)]
    assert all(response.status == 200 for _, response in opened)
    connection, response = request(port, "/frames.mjpeg")
    assert response.status == 503
    for c, _ in [*opened, (connection, response)]: c.close()


def test_real_receiver_pulls_guest_frames_end_to_end(sender):
    latest, port = sender
    source = GuestSource(host="127.0.0.1", port=port, allow_loopback=True, max_fps=15)
    receiver = GuestFrameReceiver(source, TOKEN)
    receiver.start()
    stop = threading.Event()
    def produce():
        n = 0
        while not stop.is_set():
            latest.publish(fake_jpeg(tag=f"frame-{n}".encode()))
            n += 1
            time.sleep(.1)
    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    try:
        assert wait_until(lambda: receiver.snapshot()[0]["frame_sequence"] >= 3)
        metadata, frame = receiver.snapshot()
        assert metadata["status"] == "live" and b"frame-" in frame
        # The guest stops rendering: dom0 must stop presenting its last frame as live.
        stop.set()
        assert wait_until(lambda: receiver.snapshot()[0]["status"] == "unavailable", 4)
    finally:
        stop.set()
        receiver.stop()


def test_real_receiver_reports_wrong_guest_token(sender):
    _, port = sender
    receiver = GuestFrameReceiver(GuestSource(host="127.0.0.1", port=port, allow_loopback=True), "wrong-token")
    assert receiver.session() is False
    assert receiver.snapshot()[0]["error"] == "guest_auth_failed"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_test_pattern_produces_bounded_frames_of_the_configured_size():
    latest = LatestFrame()
    worker = CaptureWorker(capture_command("testsrc", width=640, height=480, fps=5), latest, width=640, height=480)
    worker.start()
    try:
        assert wait_until(lambda: latest.status()["frame_sequence"] >= 2, 15)
        assert latest.status()["status"] == "live"
    finally:
        worker.stop()
