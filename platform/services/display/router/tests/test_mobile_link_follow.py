"""393: the phone listener follows the USB link across cable re-plugs.

A re-plugged phone is a new kernel device (new ifindex) even when the name and the
board address repeat, and SO_BINDTODEVICE keeps the old index. Without following,
the service keeps listening on a device that no longer exists.
"""
import asyncio
import http.client
import itertools
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from xenova_display import mobile_access as mobile
from xenova_display.mobile_access import PhoneLink, Pairing, create_app, follow, make_server, publish_endpoint, read_link
from xenova_display.mobile_network import Endpoint
from xenova_display.mobile_upstream import FeedResponse

ROOT = Path(__file__).resolve().parents[5]
A = Endpoint("usb0", "10.104.83.17", "10.104.83.148", 8084, 5)
A_REPLUGGED = Endpoint("usb0", "10.104.83.17", "10.104.83.148", 8084, 6)
B = Endpoint("usb0", "10.104.83.109", "10.104.83.148", 8084, 7)


class Feed:
    def get(self, path):
        return FeedResponse(200, "text/html", b"<html>synthetic</html>")


@pytest.mark.parametrize("listening,found,action", [
    (True, A, "keep"),
    (True, None, "pause"),
    (False, None, "wait"),
    (False, A, "rebind"),              # back on the same address after a pause
    (True, A_REPLUGGED, "rebind"),     # same address, new device
    (False, A_REPLUGGED, "rebind"),
    (True, B, "new_address"),
    (False, B, "new_address"),
    (True, Endpoint("usb0", "10.104.83.17", "10.104.83.150", 8084, 5), "new_address"),  # phone IP edited
])
def test_follow_keeps_the_session_only_when_the_url_is_unchanged(listening, found, action):
    assert follow(A, listening, found) == action


def test_boundary_checks_the_current_address_not_the_startup_one():
    pairing = Pairing()
    app = create_app(A, Feed(), pairing)
    with TestClient(app, base_url=B.origin, client=(B.phone_ip, 1234), follow_redirects=False) as client:
        assert client.get("/connect").status_code == 403
        app.state.endpoint = B
        assert client.get("/connect").status_code == 200
        assert client.get("/connect", headers={"Host": A.host}).status_code == 403
        assert client.post("/connect", data={"code": "000000"}, headers={"Origin": A.origin}).status_code == 403
        assert pairing.tries == 0


def test_endpoint_file_is_private_replaced_whole_and_removed_without_a_link(tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "RUNTIME", tmp_path)
    publish_endpoint(A)
    path = tmp_path / "endpoint.json"
    assert json.loads(path.read_text())["url"] == "http://10.104.83.17:8084/display/mobile"
    assert path.stat().st_mode & 0o777 == 0o600
    (tmp_path / "endpoint.json.new").write_text("stale from a crash")
    publish_endpoint(B)
    assert json.loads(path.read_text())["url"] == "http://10.104.83.109:8084/display/mobile"
    assert not (tmp_path / "endpoint.json.new").exists()
    publish_endpoint(None)
    assert not path.exists()


@pytest.mark.parametrize("error", [ValueError("down"), FileNotFoundError("usb0"), KeyError("local"),
                                   subprocess.CalledProcessError(1, ["ip"]), subprocess.TimeoutExpired(["ip"], 2)])
def test_unusable_link_reads_as_no_link_instead_of_stopping_the_service(monkeypatch, error):
    monkeypatch.setattr(mobile, "root_config", lambda path: {})
    def resolve(config):
        raise error
    monkeypatch.setattr(mobile, "resolve", resolve)
    assert read_link(Path("/etc/xenova/display-mobile.json")) is None


class FakeServer:
    def __init__(self, servers):
        self.started, self.should_exit, self.sockets = True, False, None
        servers.append(self)

    async def serve(self, sockets):
        self.sockets = sockets
        while not self.should_exit:
            await asyncio.sleep(.005)


def test_replug_same_address_keeps_session_and_new_address_issues_a_new_code(monkeypatch):
    codes = itertools.cycle([111111, 222222, 333333])
    monkeypatch.setattr(mobile.secrets, "randbelow", lambda _: next(codes))
    published_codes, endpoints, servers, sessions = [], [], [], []
    pairing = Pairing(publish=published_codes.append)
    pairing.rotate()
    pairing.session, pairing.session_until = "paired-phone", float("inf")
    app = create_app(A, Feed(), pairing)
    script = [A, None, None, A_REPLUGGED, B]

    def read():
        sessions.append(pairing.session)
        if script:
            return script.pop(0)
        servers[-1].should_exit = True  # finished: stop the last listener
        return B

    link = PhoneLink(app, pairing, A, ("socket", A), read=read, listen=lambda e: ("socket", e),
                     publish=endpoints.append, server=lambda _: FakeServer(servers), recheck=.01, settle=0)
    assert asyncio.run(link.run()) == 0
    assert [s.sockets for s in servers] == [[("socket", A)], [("socket", A_REPLUGGED)], [("socket", B)]]
    assert endpoints == [None, A_REPLUGGED, B]
    assert app.state.endpoint == B and link.endpoint == B
    # Session survived the pause and the same-address re-plug; the new address revoked it.
    assert sessions[:5] == ["paired-phone"] * 5 and pairing.session == ""
    assert published_codes == ["111111", "222222"] and pairing.code == "222222"


def test_listener_bind_failure_waits_and_retries_instead_of_exiting():
    servers, endpoints, attempts = [], [], []
    script = [None, B, B]

    def read():
        if script:
            return script.pop(0)
        servers[-1].should_exit = True
        return B

    def listen(endpoint):
        attempts.append(endpoint)
        if len(attempts) == 1:
            raise OSError("address not ready yet")
        return ("socket", endpoint)

    link = PhoneLink(create_app(A, Feed(), Pairing()), Pairing(), A, ("socket", A), read=read, listen=listen,
                     publish=endpoints.append, server=lambda _: FakeServer(servers), recheck=.01, settle=0)
    assert asyncio.run(link.run()) == 0
    assert attempts == [B, B] and endpoints == [None, None, B]
    assert servers[-1].sockets == [("socket", B)]


def test_kernel_notification_wakes_the_check_without_waiting_for_the_slow_recheck():
    left, right = socket.socketpair()
    left.setblocking(False)
    servers, reads = [], []

    def read():
        reads.append(time.monotonic())
        servers[-1].should_exit = True
        return A

    link = PhoneLink(create_app(A, Feed(), Pairing()), Pairing(), A, ("socket", A), read=read,
                     listen=lambda e: ("socket", e), publish=lambda e: None,
                     server=lambda _: FakeServer(servers), recheck=30, settle=0)

    async def scenario():
        task = asyncio.create_task(link.run(events=left))
        await asyncio.sleep(.05)
        right.send(b"netlink-like notification")
        return await asyncio.wait_for(task, 5)

    started = time.monotonic()
    assert asyncio.run(scenario()) == 0
    assert reads and reads[0] - started < 5
    left.close(); right.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def loopback_listener(endpoint):
    # Same as mobile_network.bind without SO_BINDTODEVICE (needs CAP_NET_RAW).
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((endpoint.local_ip, endpoint.port))
    sock.listen(16)
    sock.setblocking(False)
    return sock


def fetch(port):
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/connect")
        return connection.getresponse().status
    except OSError as error:
        return type(error).__name__


def test_real_uvicorn_moves_to_the_new_address_and_stops_answering_on_the_old_one():
    first = Endpoint("lo", "127.0.0.1", "127.0.0.1", free_port(), 1)
    second = Endpoint("lo", "127.0.0.1", "127.0.0.1", free_port(), 2)
    pairing = Pairing()
    pairing.rotate()
    old_code, current, servers = pairing.code, [first], []

    def server(app):
        servers.append(make_server(app))
        return servers[-1]

    link = PhoneLink(create_app(first, Feed(), pairing), pairing, first, loopback_listener(first),
                     read=lambda: current[0], listen=loopback_listener, publish=lambda e: None,
                     server=server, recheck=.05, settle=0)

    async def scenario():
        task = asyncio.create_task(link.run())
        deadline = time.monotonic() + 10
        while await asyncio.to_thread(fetch, first.port) != 200:
            assert time.monotonic() < deadline
            await asyncio.sleep(.05)
        current[0] = second
        while await asyncio.to_thread(fetch, second.port) != 200:
            assert time.monotonic() < deadline
            await asyncio.sleep(.05)
        old = await asyncio.to_thread(fetch, first.port)
        servers[-1].should_exit = True
        return old, await asyncio.wait_for(task, 10)

    old, result = asyncio.run(scenario())
    assert old == "ConnectionRefusedError" and result == 0
    assert len(servers) == 2 and pairing.code != old_code


def test_operator_rotation_signal_works_while_waiting_for_the_cable(tmp_path):
    # Without the run-wide handler SIGUSR1 would kill the service while no listener runs.
    script = '''import asyncio, os, signal
from xenova_display.mobile_access import Pairing, PhoneLink, create_app
from xenova_display.mobile_network import Endpoint
from xenova_display.mobile_upstream import LocalFeed
async def run():
    endpoint = Endpoint("usb0", "10.104.83.17", "10.104.83.148", 8084, 5)
    pairing = Pairing(); pairing.rotate(); pairing.session = "old"; pairing.session_until = 10**20
    link = PhoneLink(create_app(endpoint, LocalFeed("test-only"), pairing), pairing, endpoint, None,
                     read=lambda: None, recheck=.02, settle=0)
    task = asyncio.create_task(link.run())
    await asyncio.sleep(.1)
    os.kill(os.getpid(), signal.SIGUSR1)
    await asyncio.sleep(.1)
    assert pairing.session == "" and len(pairing.code) == 6 and not task.done()
    task.cancel()
asyncio.run(run())
'''
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10,
                   cwd=ROOT / "platform/services/display/router")
