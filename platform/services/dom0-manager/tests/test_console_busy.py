"""Bounded revalidation must never relay through an uncertain VM execution."""
import asyncio
from dataclasses import replace
from uuid import UUID

import pytest

from dom0_manager.api import console as api
from dom0_manager.drivers.base import Domain
from dom0_manager.errors import PlatformUnavailable
from dom0_manager.models import VmState

VM = UUID('11111111-1111-4111-8111-111111111111')
DOMAIN = Domain(VM, VmState.running, 1, 512, 5, 'guest', observation_id='execution-one')


class Peer:
    def __init__(self):
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()

    async def receive(self):
        return await self.input.get()

    async def send_bytes(self, data):
        await self.output.put(data)


class Session:
    def __init__(self):
        self.output = asyncio.Queue()
        self.written = []

    async def read(self):
        return await self.output.get()

    async def write(self, data):
        self.written.append(data)
        await self.output.put(data)


def retryable_busy(legacy=False):
    error = PlatformUnavailable('busy', platform_code='OPERATION_IN_PROGRESS', retryable=True)
    if legacy:
        del error.retryable  # Installed old APIs have no such field at all.
    return error


@pytest.mark.parametrize('code', [None, 'OBSERVATION_UNAVAILABLE', 'RECONCILE_REQUIRED', 'STORAGE_UNAVAILABLE'])
def test_legacy_errors_without_retryable_do_not_make_other_codes_retryable(code):
    error = PlatformUnavailable('legacy', platform_code=code)
    del error.retryable
    assert not api.retryable_console_busy(error)


def prepare(monkeypatch, outcome=DOMAIN, *, legacy=False):
    """The second call can only happen after the relay cleared its I/O gate."""
    retry_entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def validate(*_):
        calls.append(True)
        if len(calls) == 1:
            raise retryable_busy(legacy)
        retry_entered.set()
        await release.wait()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(api, 'live_domain', validate)
    monkeypatch.setattr(api, 'WATCH_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(api, 'BUSY_RECHECK_DELAY_SECONDS', 0.01)
    return retry_entered, release, calls


@pytest.mark.parametrize('legacy', [False, True])
def test_busy_pauses_both_directions_until_same_execution_is_verified(monkeypatch, caplog, legacy):
    caplog.set_level('INFO', logger='dom0_manager.api.console')
    async def scenario():
        entered, release, _ = prepare(monkeypatch, legacy=legacy)
        peer, session = Peer(), Session()
        task = asyncio.create_task(api.relay(peer, session, None, VM, 5, observation_id='execution-one'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await peer.input.put({'type': 'websocket.receive', 'bytes': b'held-input'})
            await session.output.put(b'held-output')
            await asyncio.sleep(0)
            assert not session.written
            assert peer.output.empty()
            release.set()
            frames = [await asyncio.wait_for(peer.output.get(), 1) for _ in range(2)]
            assert set(frames) == {b'held-input', b'held-output'}
            assert session.written == [b'held-input']  # Once, only within this live connection.
            await peer.input.put({'type': 'websocket.receive', 'text': '{"type":"disconnect"}'})
            await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert 'console VM validation paused' in caplog.text
    assert 'console VM validation resumed' in caplog.text
    assert 'held-input' not in caplog.text
    assert 'held-output' not in caplog.text


@pytest.mark.parametrize('changed', [
    replace(DOMAIN, domid=99),
    replace(DOMAIN, observation_id='execution-two'),
    replace(DOMAIN, vm_id=UUID('22222222-2222-4222-8222-222222222222')),
])
def test_changed_execution_never_receives_held_input_or_exposes_held_output(monkeypatch, changed):
    async def scenario():
        entered, release, _ = prepare(monkeypatch, changed)
        peer, session = Peer(), Session()
        task = asyncio.create_task(api.relay(peer, session, None, VM, 5, observation_id='execution-one'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await peer.input.put({'type': 'websocket.receive', 'bytes': b'never-replay'})
            await session.output.put(b'do-not-show')
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(api.EndRelay) as exc:
                await asyncio.wait_for(task, 1)
            assert exc.value.code == 4409
            assert not session.written
            assert peer.output.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_busy_deadline_cancels_held_frames_and_all_relay_tasks(monkeypatch):
    async def scenario():
        entered, _, _ = prepare(monkeypatch)
        monkeypatch.setattr(api, 'BUSY_RECHECK_SECONDS', 0.2)
        peer, session = Peer(), Session()
        task = asyncio.create_task(api.relay(peer, session, None, VM, 5, observation_id='execution-one'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await peer.input.put({'type': 'websocket.receive', 'bytes': b'never-replay'})
            await session.output.put(b'do-not-show')
            with pytest.raises(api.EndRelay) as exc:
                await asyncio.wait_for(task, 1)
            assert exc.value.code == 1011
            assert not session.written
            assert peer.output.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('code,retryable', [
    ('OPERATION_IN_PROGRESS', False), ('OPERATION_IN_PROGRESS', None),
    ('OBSERVATION_UNAVAILABLE', True), ('RECONCILE_REQUIRED', True),
])
def test_other_or_unconfirmed_errors_do_not_gain_a_retry_window(monkeypatch, code, retryable):
    async def scenario():
        calls = []
        async def validate(*_):
            calls.append(True)
            raise PlatformUnavailable('not retryable here', platform_code=code, retryable=retryable)
        monkeypatch.setattr(api, 'live_domain', validate)
        monkeypatch.setattr(api, 'WATCH_INTERVAL_SECONDS', 0.01)
        with pytest.raises(PlatformUnavailable):
            await asyncio.wait_for(api.relay(Peer(), Session(), None, VM, 5), 1)
        assert len(calls) == 1
    asyncio.run(scenario())


def test_explicit_disconnect_without_pending_input_still_cleans_up_during_busy(monkeypatch):
    async def scenario():
        entered, _, _ = prepare(monkeypatch)
        peer, session = Peer(), Session()
        task = asyncio.create_task(api.relay(peer, session, None, VM, 5, observation_id='execution-one'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await peer.input.put({'type': 'websocket.receive', 'text': '{"type":"disconnect"}'})
            await asyncio.wait_for(task, 1)
            assert not session.written
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
