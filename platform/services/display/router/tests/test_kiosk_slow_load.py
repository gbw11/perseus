"""414 board finding: on a busy dom0 the UI bundle takes well over 15 s to load.

Navigation must not wait for the full page load, the --app navigation of a
domU-served page must not be repeated, and a renderer error must back off
instead of relaunching Chromium every second.
"""
import asyncio
import time

import pytest

from xenova_display import kiosk as kiosk_module
from xenova_display.kiosk import Kiosk
from test_plan_a import BrowserType, Page, plan_a


def started_kiosk(tmp_path, ui_origin="http://192.168.77.20:8086", first_url=""):
    kiosk = Kiosk(plan_a(ui_origin=ui_origin), "service-token", tmp_path/"status.json", allow_virtual=True)
    output = kiosk.config.outputs[0]
    browser = BrowserType(); browser.output = output
    original = browser.launch_persistent_context

    async def launch(profile, **kwargs):
        context = await original(profile, **kwargs)
        context.pages = [Page(first_url)]
        return context
    browser.launch_persistent_context = launch
    return kiosk, output, browser


def cleanup(kiosk):
    for identity in list(kiosk.profiles): kiosk.profiles.pop(identity).cleanup()


@pytest.mark.parametrize("ui_origin", ["http://192.168.77.20:8086", None])
def test_navigation_waits_for_commit_not_full_load(tmp_path, ui_origin):
    async def case():
        kiosk, output, browser = started_kiosk(tmp_path, ui_origin)
        await kiosk.start_output(browser, output)
        page = browser.context.pages[0]
        assert page.urls == [kiosk.config.page_url(output)]
        assert page.waits == [("commit", kiosk_module.NAVIGATION_TIMEOUT_MS)]
        assert kiosk_module.NAVIGATION_TIMEOUT_MS >= 30000
        cleanup(kiosk)
    asyncio.run(case())


def test_domu_page_already_opened_by_app_is_not_loaded_twice(tmp_path):
    async def case():
        target = "http://192.168.77.20:8086/display/mobile"
        kiosk, output, browser = started_kiosk(tmp_path, first_url=target)
        await kiosk.start_output(browser, output)
        assert browser.context.pages[0].urls == []  # the --app navigation is kept
        assert browser.context.posts == []          # still no dom0 feed session
        cleanup(kiosk)
    asyncio.run(case())


def test_dom0_page_is_reloaded_after_its_session_cookie(tmp_path):
    async def case():
        target = "http://127.0.0.1:8082/display/mobile"
        kiosk, output, browser = started_kiosk(tmp_path, ui_origin=None, first_url=target)
        await kiosk.start_output(browser, output)
        assert browser.context.posts and browser.context.pages[0].urls == [target]
        cleanup(kiosk)
    asyncio.run(case())


def test_renderer_error_backs_off_before_relaunching(tmp_path):
    async def case():
        kiosk = Kiosk(plan_a(), "service-token", tmp_path/"status.json", allow_virtual=True)
        output = kiosk.config.outputs[0]
        mapping = {"status": "mapped", "physical_verified": False}
        starts = []

        async def failing_start(_browser, out):
            starts.append(time.monotonic())
            raise TimeoutError("navigation took too long")
        kiosk.start_output = failing_start

        await kiosk._update_output(None, output, mapping)
        state = kiosk.states["monitor-1"]
        assert (state["status"], state["error_type"], state["retry_in_s"]) == ("renderer_error", "TimeoutError", 5)

        for _ in range(5):  # the supervisor loop keeps calling every second
            await kiosk._update_output(None, output, mapping)
        assert len(starts) == 1
        state = kiosk.states["monitor-1"]
        assert state["status"] == "renderer_backoff" and 0 < state["retry_in_s"] <= 5
        assert state["error_type"] == "TimeoutError" and not state["renderer_alive"]

        kiosk.retry_at["monitor-1"] = time.monotonic() - 1  # backoff elapsed
        await kiosk._update_output(None, output, mapping)
        assert len(starts) == 2 and kiosk.states["monitor-1"]["retry_in_s"] == 10

        for failures in range(3, 9):
            kiosk.retry_at["monitor-1"] = time.monotonic() - 1
            await kiosk._update_output(None, output, mapping)
        assert kiosk.states["monitor-1"]["retry_in_s"] == kiosk_module.RESTART_BACKOFF_MAX_S
    asyncio.run(case())


def test_verified_renderer_resets_the_backoff(tmp_path):
    async def case():
        kiosk, output, browser = started_kiosk(tmp_path)
        kiosk.failures["monitor-1"], kiosk.retry_at["monitor-1"] = 4, time.monotonic() - 1
        await kiosk._update_output(browser, output, {"status": "mapped", "physical_verified": True})
        state = kiosk.states["monitor-1"]
        assert state["status"] == "mapped" and state["renderer_alive"] and state["feed_status"] == "not_used"
        assert "monitor-1" not in kiosk.failures and "monitor-1" not in kiosk.retry_at
        cleanup(kiosk)
    asyncio.run(case())
