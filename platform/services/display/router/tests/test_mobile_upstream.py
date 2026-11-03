from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from xenova_display.mobile_upstream import FeedUnavailable, LocalFeed, MAX_BODY

TOKEN = "test-only-internal-token"


@contextmanager
def server(status=200, body=b"test", content_type="text/html"):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, dict(self.headers)))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Set-Cookie", "not-for-phone=internal")
            self.send_header("Location", "/do-not-follow")
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self, *args): pass
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try: yield LocalFeed(TOKEN, instance.server_port), calls
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(2)


@pytest.mark.parametrize("path", ["/display/mobile", "/api/v1/display/presentation", "/assets/index-test.js", "/assets/logo-test.png"])
def test_only_allowed_reads_reach_the_existing_feed(path, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://invalid-proxy:1")
    with server() as (feed, calls):
        response = feed.get(path)
        assert response.status == 200 and response.body == b"test"
        assert response.content_type == "text/html"
        assert not hasattr(response, "headers")
        assert TOKEN not in repr(feed)
    assert len(calls) == 1 and calls[0][0:2] == ("GET", path)
    assert calls[0][2]["Authorization"] == f"Bearer {TOKEN}"
    assert "Cookie" not in calls[0][2]


@pytest.mark.parametrize("path", [
    "/api/v1/display/session", "/api/v1/display/state", "/api/v1/display/selection",
    "/api/v1/display/outputs/monitor-1/ack", "/ws/v1/display/state", "/display/monitor-1",
    "/api/v1/vms", "http://outside.invalid/", "//outside.invalid/assets/x.js",
    "/assets/../secret.js", "/assets/%2e%2e/secret.js", "/assets/x.js?token=bad", "/assets/source.map",
])
def test_control_and_arbitrary_paths_never_create_a_connection(path):
    def forbidden(*a, **k): raise AssertionError("network must not be opened")
    with pytest.raises(ValueError): LocalFeed(TOKEN).get(path, connect=forbidden)


@pytest.mark.parametrize("status,code", [(401, "DISPLAY_FEED_AUTHENTICATION_FAILED"), (403, "DISPLAY_FEED_AUTHENTICATION_FAILED"), (302, "DISPLAY_FEED_UNAVAILABLE"), (503, "DISPLAY_FEED_UNAVAILABLE")])
def test_failures_are_fixed_codes_without_redirects_or_secret_response(status, code):
    with server(status=status, body=TOKEN.encode()) as (feed, calls):
        with pytest.raises(FeedUnavailable) as error: feed.get("/display/mobile")
        assert error.value.code == code and TOKEN not in str(error.value)
    assert len(calls) == 1


def test_not_found_remains_not_found_and_no_stale_success_is_cached():
    with server(status=404) as (feed, calls):
        assert feed.get("/assets/missing.js").status == 404
    with server(status=503) as (feed, calls):
        with pytest.raises(FeedUnavailable): feed.get("/api/v1/display/presentation")


def test_oversized_response_is_rejected():
    with server(body=b"x" * (MAX_BODY + 1)) as (feed, _):
        with pytest.raises(FeedUnavailable, match="TOO_LARGE"):
            feed.get("/assets/oversized.js")


def test_timeout_and_connection_failure_do_not_expose_the_exception_or_token():
    def failure(*args, **kwargs):
        assert args[0] == "127.0.0.1" and kwargs["timeout"] == 2
        class Connection:
            def request(self, *a, **k): raise TimeoutError(TOKEN)
            def close(self): pass
        return Connection()
    with pytest.raises(FeedUnavailable) as error:
        LocalFeed(TOKEN).get("/display/mobile", connect=failure)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("token", ["", "  ", "bad\nheader", "bad\rheader", "bad\0header"])
def test_empty_or_invalid_credentials_are_rejected(token):
    with pytest.raises(ValueError): LocalFeed(token)
