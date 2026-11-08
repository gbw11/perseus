"""410 Plan B: dom0 receives frames rendered by the display domU and never trusts the guest."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from guest_jpeg import fake_jpeg
from xenova_display.capture_limits import MAX_JPEG
from xenova_display.guest_frames import (GuestFrameReceiver, GuestSource, create_guest_data_app,
                                         create_relay_app)
from xenova_display.mjpeg import JpegParser, jpeg_dimensions
from xenova_display.mobile_upstream import FeedResponse, FeedUnavailable
from xenova_display.stream import FrameSignal

TOKEN = "guest-stream-token"


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class FakeResponse:
    def __init__(self, status=200, content_type="multipart/x-mixed-replace; boundary=frame", chunks=(), end=None):
        self.status, self.content_type, self.chunks, self.end = status, content_type, list(chunks), end
    def getheader(self, name, default=None):
        return self.content_type if name.lower() == "content-type" else default
    def read1(self, _size):
        if self.chunks:
            chunk = self.chunks.pop(0)
            if isinstance(chunk, BaseException): raise chunk
            return chunk
        if self.end is not None: raise self.end
        return b""


class FakeConnection:
    def __init__(self, response=None, refuse=None):
        self.response, self.refuse, self.requests, self.closed = response, refuse, [], False
    def __call__(self, host, port, timeout):
        if self.refuse: raise self.refuse
        self.target = (host, port, timeout)
        return self
    def request(self, method, path, headers): self.requests.append((method, path, headers))
    def getresponse(self): return self.response
    def close(self): self.closed = True


def receiver(connection, clock=None, **source):
    return GuestFrameReceiver(GuestSource(host="10.10.0.13", **source), TOKEN, connect=connection,
                              clock=clock or Clock())


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "224.0.0.1", "127.0.0.1"])
def test_guest_source_must_be_a_concrete_private_xen_address(host):
    with pytest.raises(ValueError): GuestSource(host=host)
    assert GuestSource(host="127.0.0.1", allow_loopback=True).host.is_loopback


def test_jpeg_dimensions_reads_sof_and_rejects_malformed_bytes():
    frame = fake_jpeg(1280, 720, pad=10)
    assert jpeg_dimensions(frame) == (1280, 720)
    assert jpeg_dimensions(frame[:-2]) is None                       # no EOI
    assert jpeg_dimensions(b"\xff\xd8\xff\xda\x00\x02\xff\xd9") is None  # scan before size
    assert jpeg_dimensions(b"\xff\xd8\xff\xc0\xff\xff\xff\xd9") is None  # length past buffer
    assert jpeg_dimensions(b"not a jpeg") is None
    assert jpeg_dimensions(fake_jpeg(0, 720)) is None


def test_parser_counts_frames_dropped_for_size():
    parser = JpegParser()
    assert parser.push(fake_jpeg(pad=MAX_JPEG)) == []
    assert parser.discarded == 1
    assert parser.push(fake_jpeg()) == [fake_jpeg()]


def test_valid_guest_frames_become_live_and_token_only_goes_upstream():
    clock = Clock()
    frame = fake_jpeg(tag=b"first")
    connection = FakeConnection(FakeResponse(chunks=[frame[:30], frame[30:]]))
    r = receiver(connection, clock)
    assert r.session() is True
    assert connection.target == ("10.10.0.13", 8090, 3)
    assert connection.requests == [("GET", "/frames.mjpeg", {"Authorization": f"Bearer {TOKEN}"})]
    assert connection.closed
    # The stream ended, so the last frame is withdrawn rather than shown as live.
    metadata, shown = r.snapshot()
    assert shown is None and metadata["status"] == "unavailable"
    assert metadata["error"] == "guest_stream_closed" and metadata["frame_sequence"] == 1
    assert TOKEN not in repr(metadata)


def test_live_frame_expires_without_new_guest_frames():
    clock = Clock()
    r = receiver(FakeConnection(), clock, stale_ms=2000)
    r._accept(fake_jpeg(tag=b"live"))
    metadata, frame = r.snapshot()
    assert metadata["status"] == "live" and frame.endswith(b"\xff\xd9")
    clock.now += 2.5
    metadata, frame = r.snapshot()
    assert frame is None and metadata["status"] == "unavailable" and metadata["error"] == "frame_stale"


def test_wrong_size_and_flooding_frames_are_not_shown():
    clock = Clock()
    good = fake_jpeg(tag=b"good")
    chunks = [fake_jpeg(1920, 1080), good, good]  # second good frame arrives at the same instant
    r = receiver(FakeConnection(FakeResponse(chunks=chunks, end=TimeoutError())), clock)
    assert r.session() is True
    metadata, _ = r.snapshot()
    assert metadata["frames_rejected"] == 1 and metadata["frames_dropped"] == 1
    assert metadata["frame_sequence"] == 1
    assert metadata["error"] == "guest_stream_stalled"


@pytest.mark.parametrize("response,refuse,code", [
    (FakeResponse(status=401), None, "guest_auth_failed"),
    (FakeResponse(content_type="text/html"), None, "guest_protocol_error"),
    (FakeResponse(status=500), None, "guest_protocol_error"),
    (None, ConnectionRefusedError(), "guest_unreachable"),
])
def test_connection_failures_have_fixed_codes(response, refuse, code):
    r = receiver(FakeConnection(response, refuse))
    assert r.session() is False
    metadata, frame = r.snapshot()
    assert frame is None and metadata["status"] == "unavailable" and metadata["error"] == code


def test_relay_requires_auth_and_does_not_echo_tokens():
    r = receiver(FakeConnection())
    app = create_relay_app(r, "relay-token", start_receiver=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/guest-display").status_code == 401
        assert client.get("/streams/display.mjpeg").status_code == 401
        assert client.post("/auth", json={"token": "wrong"}).status_code == 401
        response = client.get("/api/v1/guest-display", headers={"Authorization": "Bearer relay-token"})
        assert response.status_code == 200 and response.json()["rendered_by"] == "domU"
        assert "relay-token" not in response.text and TOKEN not in response.text
        assert response.headers["cache-control"] == "no-store"


def test_relay_stream_sends_only_new_live_frames_and_rechecks_auth():
    async def case():
        r = receiver(FakeConnection())
        r.changed = FrameSignal(asyncio.get_running_loop())
        app = create_relay_app(r, "relay-token", start_receiver=False)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/streams/display.mjpeg")
        class Request:
            headers = {"authorization": "Bearer relay-token"}
            cookies = {}
            async def is_disconnected(self): return False
        request = Request()
        r._accept(fake_jpeg(tag=b"first"))
        stream = (await endpoint(request)).body_iterator
        assert b"first" in await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(.05)
        assert not waiting.done()  # same frame is not resent
        r._accept(fake_jpeg(tag=b"second"))
        assert b"second" in await asyncio.wait_for(waiting, 1)
        request.headers = {}
        r._accept(fake_jpeg(tag=b"private"))
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
    asyncio.run(case())


class FakeFeed:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.paths = result, error, []
    def get(self, path):
        self.paths.append(path)
        if self.error: raise self.error
        return self.result


def test_guest_data_is_one_read_only_path_with_its_own_token():
    feed = FakeFeed(FeedResponse(200, "application/json", b'{"type":"display.presentation"}'))
    app = create_guest_data_app(feed, "guest-data-token")
    good = {"Authorization": "Bearer guest-data-token"}
    with TestClient(app) as client:
        assert client.get("/api/v1/display/presentation").status_code == 401
        assert client.get("/api/v1/display/presentation", headers={"Authorization": "Bearer service"}).status_code == 401
        for method, path in [("GET", "/api/v1/display/state"), ("GET", "/display/mobile"),
                             ("POST", "/api/v1/display/presentation"),
                             ("GET", "/api/v1/display/presentation?x=1")]:
            assert client.request(method, path, headers=good).status_code == 404
        response = client.get("/api/v1/display/presentation", headers=good)
        assert response.status_code == 200 and response.json() == {"type": "display.presentation"}
        assert "set-cookie" not in response.headers and response.headers["cache-control"] == "no-store"
    assert feed.paths == ["/api/v1/display/presentation"]


def test_relay_config_example_is_valid_and_guest_data_stays_on_the_bridge():
    from pathlib import Path
    from xenova_display.guest_frames import GuestRelayConfig
    example = Path(__file__).resolve().parents[1]/"deploy/guest-display/guest-display.json.example"
    config = GuestRelayConfig.model_validate_json(example.read_text(encoding="utf-8"))
    assert str(config.guest.host) == "192.168.77.20" and str(config.data_bind) == "192.168.77.1"
    raw = config.model_dump(mode="json")
    for change in ({"data_bind": "0.0.0.0"}, {"data_bind": "127.0.0.1"}, {"data_bind": "8.8.8.8"},
                   {"data_port": 8084}, {"relay_token_file": "relative.token"}):
        with pytest.raises(ValueError):
            GuestRelayConfig.model_validate({**raw, **change})


@pytest.mark.parametrize("feed,code", [
    (FakeFeed(error=FeedUnavailable("DISPLAY_FEED_AUTHENTICATION_FAILED")), "DISPLAY_FEED_AUTHENTICATION_FAILED"),
    (FakeFeed(FeedResponse(200, "text/html", b"<html>")), "DISPLAY_FEED_UNAVAILABLE"),
    (FakeFeed(FeedResponse(404, "application/json", b"{}")), "DISPLAY_FEED_UNAVAILABLE"),
])
def test_guest_data_failures_are_fixed_codes(feed, code):
    with TestClient(create_guest_data_app(feed, "guest-data-token")) as client:
        response = client.get("/api/v1/display/presentation", headers={"Authorization": "Bearer guest-data-token"})
    assert response.status_code == 503 and response.json() == {"code": code}
