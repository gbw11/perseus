import asyncio
from contextlib import suppress
import json
from types import SimpleNamespace
import time

import pytest
from xenova_display.kiosk import Kiosk
from test_outputs import config


@pytest.mark.parametrize('operation', ['start', 'close', 'verify'])
def test_slow_output_does_not_stop_other_output_observations(tmp_path, operation):
    async def case():
        path = tmp_path/'runtime.json'
        kiosk = Kiosk(config(), 'test-only', path, allow_virtual=True)
        entered, release = asyncio.Event(), asyncio.Event()
        checks = {'monitor-1': 0, 'monitor-2': 0}
        mapping = {o.output_id: {'status': 'mapped', 'physical_verified': False}
                   for o in kiosk.config.outputs}
        if operation == 'close': mapping['monitor-1']['status'] = 'disconnected'
        kiosk.mapping, kiosk.mapping_at = mapping, time.monotonic()
        kiosk.layout.reconcile = lambda: {k: dict(v) for k,v in mapping.items()}

        class Page:
            def is_closed(self): return False

        class CDP:
            def __init__(self, output): self.output = output
            async def send(self, _):
                if self.output.output_id == 'monitor-1' and operation == 'verify':
                    entered.set(); await release.wait()
                checks[self.output.output_id] += 1
                return {'bounds': dict(left=self.output.x, top=self.output.y,
                                      width=self.output.width, height=self.output.height)}
            async def detach(self): pass

        class Context:
            def __init__(self, output):
                self.output = output; self.request = self
            async def new_cdp_session(self, _): return CDP(self.output)
            async def get(self, *_a, **_k): return SimpleNamespace(status=503)

        async def start(_browser, output):
            if output.output_id == 'monitor-1' and operation == 'start':
                entered.set(); await release.wait()
            kiosk.pages[output.output_id] = Page()
            kiosk.contexts[output.output_id] = Context(output)
            kiosk.renewed[output.output_id] = time.monotonic()

        async def close(identity):
            if identity == 'monitor-1' and operation == 'close':
                entered.set(); await release.wait()
            kiosk.pages.pop(identity, None)
            kiosk.contexts.pop(identity, None)

        kiosk.start_output, kiosk.close_output = start, close
        # The opposite output already exists, as on the physical hotplug test.
        for o in kiosk.config.outputs:
            if o.output_id == 'monitor-2' or operation == 'verify':
                kiosk.pages[o.output_id] = Page(); kiosk.contexts[o.output_id] = Context(o)
                kiosk.renewed[o.output_id] = time.monotonic()
        task = asyncio.create_task(kiosk._supervise(None, interval=.01))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            deadline = time.monotonic()+2
            first = None
            while time.monotonic() < deadline:
                data = json.loads(path.read_text())
                if data['outputs']['monitor-2'].get('renderer_alive'):
                    if first is None: first = data['outputs']['monitor-2']['renderer_verified_monotonic']
                    elif data['outputs']['monitor-2']['renderer_verified_monotonic'] > first and checks['monitor-2'] >= 4:
                        break
                await asyncio.sleep(.01)
            else: pytest.fail('healthy output stopped reporting while opposite operation blocked')
            assert not release.is_set()
            assert time.monotonic()-data['updated_monotonic'] < .2
            assert data['outputs']['monitor-2']['status'] == 'mapped'
            if operation != 'verify':
                assert not data['outputs']['monitor-1']['renderer_alive']
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task
    asyncio.run(case())


def test_fresh_status_file_does_not_extend_stale_renderer_or_geometry(tmp_path):
    kiosk = Kiosk(config(), 'test-only', tmp_path/'runtime', allow_virtual=True)
    now = time.monotonic()
    kiosk.mapping_at = now
    kiosk.mapping = {o.output_id: {'status': 'mapped', 'physical_verified': True}
                     for o in kiosk.config.outputs}
    kiosk.states = {
        'monitor-1': {'status': 'mapped', 'renderer_alive': True, 'renderer_verified_monotonic': now-7},
        'monitor-2': {'status': 'mapped', 'renderer_alive': True, 'renderer_verified_monotonic': now},
    }
    data = kiosk._runtime_status(now)
    assert data['updated_monotonic'] == now
    assert data['outputs']['monitor-1']['status'] == 'renderer_stale'
    assert not data['outputs']['monitor-1']['renderer_alive']
    assert data['outputs']['monitor-2']['status'] == 'mapped'
    kiosk.mapping['monitor-2']['status'] = 'disconnected'
    assert not kiosk._runtime_status(now)['outputs']['monitor-2']['renderer_alive']
    kiosk.mapping_at = now-7
    assert all(o['status'] == 'layout_stale' for o in kiosk._runtime_status(now)['outputs'].values())
