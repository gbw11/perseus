"""WebSocket 인증, VM 분리, 연결 경쟁, disconnect 및 콘솔 종료 검증."""
import asyncio
from dataclasses import replace
import threading
import sys
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dom0_manager.config import Settings
from dom0_manager.console import ConsoleError, ConsoleExited
from dom0_manager.drivers.base import Domain
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.errors import DriverTimeout, VMNotFound
from dom0_manager.main import create_app
from dom0_manager.models import VmState

VM_A = UUID('11111111-1111-4111-8111-111111111111')
VM_B = UUID('22222222-2222-4222-8222-222222222222')
AUTH = {'Authorization': 'Bearer test-console-token'}
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux dom0 console locks')


class LiveDriver(MockDriver):
    name = 'xen'

    def __init__(self):
        super().__init__()
        self.domains = {
            VM_A: Domain(VM_A, VmState.running, 1, 512, 5, 'debian-dev'),
            VM_B: Domain(VM_B, VmState.running, 1, 16, 7, 'zephyr-dev'),
        }
        self.failure = None

    def get_domain(self, vm_id):
        if self.failure:
            raise self.failure
        if vm_id not in self.domains:
            raise VMNotFound('not found')
        return self.domains[vm_id]


class FakeSession:
    def __init__(self, domid, on_open=None):
        self.domid = domid
        self.queue = asyncio.Queue()
        self.closed = threading.Event()
        self.written = []
        self.on_open = on_open

    async def __aenter__(self):
        if self.on_open:
            self.on_open()
        await self.queue.put(b'READY:' + str(self.domid).encode())
        return self

    async def __aexit__(self, *exc):
        self.closed.set()

    async def read(self):
        data = await self.queue.get()
        if isinstance(data, Exception):
            raise data
        return data

    async def write(self, data):
        self.written.append(data)
        if data == b'EXIT':
            await self.queue.put(ConsoleExited(0))
        elif data == b'FAIL':
            await self.queue.put(ConsoleExited(1))
        elif data == b'STALL':
            raise TimeoutError
        else:
            await self.queue.put(data)


@pytest.fixture
def setup(tmp_path):
    driver = LiveDriver()
    settings = Settings(driver='xen', service_token='test-console-token',
                        console_enabled=True, console_lock_dir=str(tmp_path))
    app = create_app(settings, driver=driver, provisioner=MockProvisioner())
    sessions = []

    def factory(domid):
        session = FakeSession(domid)
        sessions.append(session)
        return session

    app.state.console_factory = factory
    with TestClient(app) as client:
        yield client, driver, sessions


def path(vm_id=VM_A):
    return f'/api/v1/vms/{vm_id}/console'


def closed(ws, code):
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_bytes()
    assert exc.value.code == code


def test_binary_text_and_control_bytes_are_relayed(setup):
    client, _, sessions = setup
    with client.websocket_connect(path(), headers=AUTH) as ws:
        assert ws.receive_bytes() == b'READY:5'
        ws.send_bytes(b'\x03\x1b[A\r\xff')
        assert ws.receive_bytes() == b'\x03\x1b[A\r\xff'
        ws.send_text('한글\r')
        assert ws.receive_bytes() == '한글\r'.encode()
    assert sessions[0].closed.wait(2)


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'}])
def test_auth_before_console_spawn(setup, headers):
    client, _, sessions = setup
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path() + '?token=test-console-token', headers=headers):
            pass
    assert exc.value.code == 4401
    assert not sessions


@pytest.mark.parametrize('identifier,code', [
    ('not-a-uuid', 4400), (UUID(int=0), 4400), (UUID(int=42), 4404),
])
def test_invalid_or_unavailable_vm_never_spawns(setup, identifier, code):
    client, _, sessions = setup
    with client.websocket_connect(path(identifier), headers=AUTH) as ws:
        closed(ws, code)
    assert not sessions


def test_stopping_vm_rejected(setup):
    client, driver, sessions = setup
    driver.domains[VM_A] = replace(driver.domains[VM_A], state=VmState.stopping)
    with client.websocket_connect(path(), headers=AUTH) as ws:
        closed(ws, 4409)
    assert not sessions


def test_duplicate_rejected_and_reconnect_after_disconnect(setup):
    client, _, sessions = setup
    with client.websocket_connect(path(), headers=AUTH) as first:
        assert first.receive_bytes() == b'READY:5'
        with client.websocket_connect(path(), headers=AUTH) as second:
            closed(second, 4409)
        assert len(sessions) == 1
        first.send_bytes(b'still alive')
        assert first.receive_bytes() == b'still alive'
    assert sessions[0].closed.wait(2)
    with client.websocket_connect(path(), headers=AUTH) as second:
        assert second.receive_bytes() == b'READY:5'


def test_distinct_vm_streams_do_not_mix(setup):
    client, _, _ = setup
    with client.websocket_connect(path(), headers=AUTH) as first:
        with client.websocket_connect(path(VM_B), headers=AUTH) as second:
            assert first.receive_bytes() == b'READY:5'
            assert second.receive_bytes() == b'READY:7'
            first.send_text('A')
            second.send_text('B')
            assert first.receive_bytes() == b'A'
            assert second.receive_bytes() == b'B'


def test_disconnect_control_acknowledges_cleanup_before_immediate_reconnect(setup):
    client, _, sessions = setup
    for _ in range(5):
        with client.websocket_connect(path(), headers=AUTH) as ws:
            ws.receive_bytes()
            ws.send_text('{"type":"disconnect"}')
            closed(ws, 1000)
            assert sessions[-1].closed.is_set()
            assert not sessions[-1].written


@pytest.mark.parametrize('command,code', [(b'EXIT', 1000), (b'FAIL', 1011), (b'STALL', 1011)])
def test_console_exit_or_write_timeout_closes_peer(setup, command, code):
    client, _, sessions = setup
    with client.websocket_connect(path(), headers=AUTH) as ws:
        ws.receive_bytes()
        ws.send_bytes(command)
        closed(ws, code)
    assert sessions[0].closed.wait(2)


@pytest.mark.parametrize('payload', [b'x' * 65537, '한' * 22000])
def test_oversized_input_not_written(setup, payload):
    client, _, sessions = setup
    with client.websocket_connect(path(), headers=AUTH) as ws:
        ws.receive_bytes()
        if isinstance(payload, bytes):
            ws.send_bytes(payload)
        else:
            ws.send_text(payload)
        closed(ws, 1009)
    assert not sessions[0].written
    assert sessions[0].closed.wait(2)


def test_vm_change_during_open_does_not_receive_input(setup):
    client, driver, _ = setup

    def change():
        driver.domains[VM_A] = replace(driver.domains[VM_A], domid=99)

    session = FakeSession(5, on_open=change)
    client.app.state.console_factory = lambda _: session
    with client.websocket_connect(path(), headers=AUTH) as ws:
        closed(ws, 4409)
    assert session.closed.wait(2)
    assert not session.written


def test_monitor_closes_on_vm_disappearance(setup):
    client, driver, sessions = setup
    with client.websocket_connect(path(), headers=AUTH) as ws:
        ws.receive_bytes()
        del driver.domains[VM_A]
        closed(ws, 4404)
    assert sessions[0].closed.wait(2)


def test_monitor_closes_on_vm_restart(setup):
    client, driver, sessions = setup
    with client.websocket_connect(path(), headers=AUTH) as ws:
        ws.receive_bytes()
        driver.domains[VM_A] = replace(driver.domains[VM_A], domid=99)
        closed(ws, 4409)
    assert sessions[0].closed.wait(2)


def test_launch_failure_releases_lease(setup):
    client, _, _ = setup
    factory = client.app.state.console_factory

    def fail(_):
        raise ConsoleError('cannot spawn')

    client.app.state.console_factory = fail
    with client.websocket_connect(path(), headers=AUTH) as ws:
        closed(ws, 1011)
    client.app.state.console_factory = factory
    with client.websocket_connect(path(), headers=AUTH) as ws:
        assert ws.receive_bytes() == b'READY:5'


def test_maximum_size_input_is_allowed(setup):
    client, _, _ = setup
    with client.websocket_connect(path(), headers=AUTH) as ws:
        ws.receive_bytes()
        ws.send_bytes(b'x' * 65536)
        assert ws.receive_bytes() == b'x' * 65536


def test_driver_timeout_does_not_expose_detail(setup):
    client, driver, sessions = setup
    driver.failure = DriverTimeout('sensitive detail')
    with client.websocket_connect(path(), headers=AUTH) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
        assert exc.value.code == 1011
        assert 'sensitive' not in exc.value.reason
    assert not sessions


def test_cross_worker_lock_rejects_second_owner(tmp_path):
    from dom0_manager.api.console import claim_console, ConsoleBusy
    with claim_console(str(tmp_path), VM_A):
        with pytest.raises(ConsoleBusy):
            with claim_console(str(tmp_path), VM_A):
                pass
        with claim_console(str(tmp_path), VM_B):
            pass
    with claim_console(str(tmp_path), VM_A):
        pass


def test_console_disabled_by_default():
    app = create_app(Settings())
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path(), headers=AUTH):
            pass


def test_console_configuration_requires_token_and_local_xen():
    with pytest.raises(ValueError):
        Settings(console_enabled=True)
    with pytest.raises(ValueError):
        Settings(console_enabled=True, service_token='test', xl_ssh_target='remote')
    assert Settings.from_env({'XENOVA_CONSOLE_ENABLED': 'true', 'XENOVA_SERVICE_TOKEN': 'test'}).console_enabled
