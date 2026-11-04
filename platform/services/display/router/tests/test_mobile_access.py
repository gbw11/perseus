import itertools
import json

from fastapi.testclient import TestClient
import pytest

from xenova_display.mobile_access import COOKIE, CODE_SECONDS, SESSION_SECONDS, Pairing, create_app
from xenova_display.mobile_network import Endpoint
from xenova_display.mobile_upstream import FeedResponse, FeedUnavailable


@pytest.fixture
def link(monkeypatch):
    from xenova_display import mobile_access
    codes = itertools.cycle([123456, 654321])
    monkeypatch.setattr(mobile_access.secrets, "randbelow", lambda _: next(codes))
    clock, published, calls = [100.0], [], []
    pairing = Pairing(clock=lambda: clock[0], publish=published.append)
    pairing.rotate()
    class Feed:
        failing = False
        def get(self, path):
            calls.append(path)
            if self.failing: raise FeedUnavailable()
            return FeedResponse(200, "application/json" if path.endswith("presentation") else "text/html", b'{"test":"synthetic"}')
    endpoint = Endpoint("usb-test", "192.168.42.2", "192.168.42.129", 8084)
    feed = Feed()
    app = create_app(endpoint, feed, pairing)
    with TestClient(app, base_url=endpoint.origin, client=(endpoint.phone_ip, 1234), follow_redirects=False) as client:
        yield client, endpoint, pairing, feed, clock, published, calls


def pair(link, code="123456"):
    client, endpoint, *_ = link
    return client.post("/connect", data={"code": code}, headers={"Origin": endpoint.origin})


def test_first_open_requires_pairing_and_does_not_expose_data(link):
    client, _, _, _, _, _, calls = link
    response = client.get("/display/mobile")
    assert response.status_code == 303 and response.headers["location"] == "/connect"
    page = client.get("/connect")
    assert page.status_code == 200 and 'name="code"' in page.text and "123456" not in page.text
    assert client.get("/api/v1/display/presentation").status_code == 401
    assert client.get("/assets/index-test.js").status_code == 401
    assert not calls


def test_single_use_code_issues_http_only_cookie_for_read_only_paths(link):
    client, _, pairing, _, _, published, calls = link
    response = pair(link)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and f"Max-Age={SESSION_SECONDS}" in cookie
    assert published == ["123456", None]
    assert pairing.code == ""
    assert client.get("/display/mobile").status_code == 200
    assert client.get("/api/v1/display/presentation").status_code == 200
    assert pair(link).status_code == 401
    assert calls == ["/display/mobile", "/api/v1/display/presentation"]


def test_wrong_origin_or_missing_origin_does_not_consume_a_code(link):
    client, _, pairing, *_ = link
    assert client.post("/connect", data={"code": "123456"}).status_code == 403
    assert client.post("/connect", data={"code": "123456"}, headers={"Origin": "http://other.invalid"}).status_code == 403
    assert pairing.tries == 0 and pair(link).status_code == 303


@pytest.mark.parametrize("path", ["/api/v1/display/state", "/api/v1/display/session", "/api/v1/display/selection", "/api/v1/display/outputs/monitor-1/ack", "/api/v1/vms", "/display/monitor-1", "/ws/v1/display/state", "/docs", "/openapi.json"])
def test_internal_and_control_paths_never_reach_feed_even_when_paired(link, path):
    client, endpoint, _, _, _, _, calls = link
    pair(link)
    assert client.get(path).status_code == 404
    assert client.post(path, headers={"Origin": endpoint.origin}).status_code == 405
    assert not calls


@pytest.mark.parametrize("host", ["outside.invalid", "127.0.0.1:8084", "192.168.42.2:8082"])
def test_unexpected_host_is_rejected(link, host):
    client, *_ = link
    assert client.get("/connect", headers={"Host": host}).status_code == 403


def test_forwarded_header_does_not_allow_other_peer(link):
    _, endpoint, pairing, feed, *_ = link
    with TestClient(create_app(endpoint, feed, pairing), base_url=endpoint.origin, client=("192.168.30.158", 1234)) as other:
        assert other.get("/connect", headers={"X-Forwarded-For": endpoint.phone_ip}).status_code == 403


def test_five_wrong_attempts_lock_the_code_until_operator_rotates(link):
    client, _, pairing, *_ = link
    for _ in range(5): assert pair(link, "000000").status_code == 401
    assert pair(link).status_code == 401
    pairing.rotate()
    assert pair(link, "654321").status_code == 303


def test_expiry_and_rotation_never_reuse_previous_sessions(link):
    client, _, pairing, _, clock, _, calls = link
    clock[0] += CODE_SECONDS
    assert pair(link).status_code == 401
    pairing.rotate()
    assert pair(link, "654321").status_code == 303
    old_session = client.cookies.get(COOKIE)
    clock[0] += SESSION_SECONDS
    assert client.get("/api/v1/display/presentation").status_code == 401
    assert client.get("/display/mobile").headers["location"] == "/connect"
    pairing.rotate()
    assert pair(link).status_code == 303
    assert old_session != client.cookies.get(COOKIE)
    pairing.rotate()
    assert client.get("/api/v1/display/presentation").status_code == 401


def test_feed_loss_is_not_cached_and_new_report_can_resume(link):
    client, _, _, feed, *_ = link
    pair(link)
    assert client.get("/api/v1/display/presentation").status_code == 200
    feed.failing = True
    failed = client.get("/api/v1/display/presentation")
    assert failed.status_code == 503 and failed.json()["code"] == "DISPLAY_FEED_UNAVAILABLE"
    waiting = client.get("/display/mobile")
    assert waiting.status_code == 503 and "화면 준비 중" in waiting.text
    assert 'href="/display/mobile"' in waiting.text and 'name="code"' not in waiting.text
    feed.failing = False
    assert client.get("/api/v1/display/presentation").status_code == 200


def test_query_and_encoded_paths_cannot_forward_extra_information(link):
    client, _, _, _, _, _, calls = link
    pair(link)
    assert client.get("/api/v1/display/presentation?token=test").status_code == 404
    assert client.get("/assets/%69ndex-test.js").status_code == 404
    assert not calls


def test_body_is_small_and_not_json_or_a_get_query_code(link):
    client, endpoint, _, _, _, _, calls = link
    assert client.get("/connect?code=123456").status_code == 200
    assert client.get("/api/v1/display/presentation").status_code == 401
    headers = {"Origin": endpoint.origin, "Content-Type": "application/x-www-form-urlencoded"}
    assert client.post("/connect", content=b"code=" + b"1" * 100, headers=headers).status_code == 413
    assert client.post("/connect", json={"code": "123456"}, headers={"Origin": endpoint.origin}).status_code == 415
    assert not calls


def test_every_response_prevents_cache_framing_and_referrer_leak(link):
    client, *_ = link
    for path in ("/connect", "/display/mobile", "/api/v1/display/presentation"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
