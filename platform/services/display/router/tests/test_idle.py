import subprocess
import pytest
from xenova_display.idle import X11IdleGuard


class FakeXset:
    def __init__(self, supported=True):
        self.timeout, self.cycle, self.blank, self.enabled = 600, 600, True, True
        self.supported = supported
        self.calls = []

    def __call__(self, args, **kwargs):
        a = args[3:]
        self.calls.append(a)
        if a == ["q"]:
            text = f"prefer blanking: {'yes' if self.blank else 'no'} allow exposures: yes\ntimeout: {self.timeout} cycle: {self.cycle}"
            if self.supported:
                text += f"\nStandby: 600 Suspend: 600 Off: 600\nDPMS is {'Enabled' if self.enabled else 'Disabled'}"
            return subprocess.CompletedProcess(args, 0, text, "")
        if a == ["s", "off"]: self.timeout = 0
        elif a == ["s", "noblank"]: self.blank = False
        elif a == ["s", "blank"]: self.blank = True
        elif a[0] == "s": self.timeout, self.cycle = map(int, a[1:])
        elif a == ["-dpms"]: self.enabled = False
        elif a == ["+dpms"]: self.enabled = True
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.mark.parametrize("supported", [True, False])
def test_idle_settings_are_disabled_then_restored(supported):
    runner = FakeXset(supported)
    guard = X11IdleGuard(":5", runner)
    before = guard.settings()
    current = guard.enable()
    assert current["timeout"] == 0 and current["blank"] is False
    if supported: assert current["dpms"]["enabled"] is False
    guard.restore()
    assert guard.settings() == before
    if not supported: assert not any("dpms" in " ".join(c) for c in runner.calls)


def test_failed_apply_restores_preexisting_settings():
    runner = FakeXset()
    def fail(args, **kwargs):
        if args[3:] == ["s", "noblank"]:
            raise subprocess.CalledProcessError(1, args)
        return runner(args, **kwargs)
    guard = X11IdleGuard(":5", fail)
    before = guard.settings()
    with pytest.raises(subprocess.CalledProcessError): guard.enable()
    assert guard.settings() == before


def test_kiosk_cancellation_restores_idle_settings(monkeypatch):
    import asyncio
    from xenova_display.kiosk import Kiosk
    from xenova_display.outputs import RuntimeConfig
    calls = []
    class Guard:
        def __init__(self, display): pass
        def enable(self): calls.append("enable")
        def restore(self): calls.append("restore")
    monkeypatch.setattr("xenova_display.idle.X11IdleGuard", Guard)
    config = RuntimeConfig.model_validate({"backend": "virtual_x11", "outputs": [
        {"output_id": "monitor-1", "connector": "one", "x": 0},
        {"output_id": "monitor-2", "connector": "two", "x": 1280}]})
    kiosk = Kiosk(config, "test-only", "/unused", allow_virtual=True, prevent_idle_blank=True)
    async def check():
        ready = asyncio.Event()
        async def run():
            ready.set()
            await asyncio.Future()
        kiosk._run = run
        task = asyncio.create_task(kiosk.run())
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(check())
    assert calls == ["enable", "restore"]
