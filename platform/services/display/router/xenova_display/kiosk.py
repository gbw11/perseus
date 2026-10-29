"""Local renderer supervisor. Remote streaming is an independent process."""
import argparse
import asyncio
import json
import os
import signal
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from .outputs import RuntimeConfig, X11Layout


def write_status(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        json.dump(value, file)
        name = file.name
    os.replace(name, path)


class Kiosk:
    def __init__(self, config, token, status_path, *, allow_virtual=False, chromium_path=None, test_no_sandbox=False, prevent_idle_blank=False, shared_browser=False):
        if not token: raise ValueError("XENOVA_DISPLAY_TOKEN required")
        if test_no_sandbox and config.backend != "virtual_x11":
            raise ValueError("sandbox bypass is restricted to virtual test mode")
        self.config, self.token, self.status_path = config, token, status_path
        self.layout = X11Layout(config, allow_virtual=allow_virtual)
        self.chromium_path, self.test_no_sandbox = chromium_path, test_no_sandbox
        self.prevent_idle_blank = prevent_idle_blank
        self.shared_browser = shared_browser
        self.browser = None
        self.epoch = str(uuid4())
        self.contexts, self.pages, self.renewed = {}, {}, {}
        self.profiles = {}
        self.states = {}

    async def start_output(self, browser_type, output):
        args = ["--ozone-platform=x11", "--force-device-scale-factor=1", "--no-first-run",
                f"--window-position={output.x},{output.y}", f"--window-size={output.width},{output.height}",
                "--disable-translate", "--disable-features=Translate", "--lang=ko-KR",
                f"--app={self.config.feed_origin}/display/{output.output_id}"]
        if self.config.backend == "x11": args.append("--kiosk")
        if self.test_no_sandbox: args.append("--no-sandbox")
        # Chromium never receives the service token via argv, extra headers or page JS.
        env = {k: v for k, v in os.environ.items() if not (k.startswith("XENOVA_") and ("TOKEN" in k or "PASSWORD" in k))}
        env["DISPLAY"] = self.config.display
        if self.shared_browser:
            # Share Chromium's browser/GPU overhead, not cookies or output windows.
            # Every output still owns an independent context and CDP window mapping.
            if self.browser is None or not self.browser.is_connected():
                common = [arg for arg in args if not arg.startswith(("--app=", "--window-position=", "--window-size="))
                          and arg != "--kiosk"]
                self.browser = await browser_type.launch(headless=False,
                    executable_path=self.chromium_path, args=common, env=env,
                    chromium_sandbox=not self.test_no_sandbox)
            context = await self.browser.new_context(no_viewport=True, locale="ko-KR")
        else:
            profile = tempfile.TemporaryDirectory(prefix="xenova-kiosk-")
            self.profiles[output.output_id] = profile
            default = Path(profile.name)/"Default"
            default.mkdir()
            (default/"Preferences").write_text(json.dumps({"translate":{"enabled":False},
                "intl":{"accept_languages":"ko-KR,ko"}}),encoding="utf-8")
            context = await browser_type.launch_persistent_context(profile.name, headless=False, no_viewport=True, locale="ko-KR",
                        executable_path=self.chromium_path, args=args, env=env,
                        ignore_default_args=["about:blank"],
                        chromium_sandbox=not self.test_no_sandbox)
        self.contexts[output.output_id] = context
        await self.renew(context)
        page = context.pages[0] if context.pages else await context.new_page()
        self.pages[output.output_id] = page
        for other in list(context.pages):
            if other is not page: await other.close()
        await page.goto(f"{self.config.feed_origin}/display/{output.output_id}", timeout=15000)
        cdp = await context.new_cdp_session(page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState":"normal"}})
        await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {
            "left": output.x, "top": output.y, "width": output.width, "height": output.height}})
        if self.config.backend == "x11":
            await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState":"fullscreen"}})
        await cdp.detach()
        self.renewed[output.output_id] = time.monotonic()

    async def renew(self, context):
        response = await context.request.post(f"{self.config.feed_origin}/api/v1/display/session",
            headers={"Authorization": f"Bearer {self.token}"}, timeout=3000)
        if response.status != 204: raise RuntimeError("display session bootstrap failed")

    async def close_output(self, output_id):
        context = self.contexts.pop(output_id, None)
        self.pages.pop(output_id, None)
        if context:
            try: await context.close()
            except Exception: pass
        profile = self.profiles.pop(output_id, None)
        if profile: profile.cleanup()

    async def run(self):
        from .idle import X11IdleGuard
        guard = X11IdleGuard(self.config.display) if self.prevent_idle_blank else None
        try:
            if guard: await asyncio.to_thread(guard.enable)
            await self._run()
        finally:
            if guard: await asyncio.to_thread(guard.restore)

    async def _run(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            try:
                while True:
                    try: mapping = await asyncio.to_thread(self.layout.reconcile)
                    except Exception:
                        mapping = {o.output_id: {"status": "layout_error", "physical_verified": False} for o in self.config.outputs}
                    for output in self.config.outputs:
                        identity = output.output_id
                        state = {**mapping[identity], "connector": output.connector,
                            "rectangle": [output.x, output.y, output.width, output.height], "renderer_alive": False,
                            "applied_revision": None}
                        try:
                            page = self.pages.get(identity)
                            if state["status"] != "mapped":
                                await self.close_output(identity)
                            else:
                                if page is None or page.is_closed():
                                    await self.close_output(identity)
                                    await self.start_output(playwright.chromium, output)
                                    page = self.pages[identity]
                                state["renderer_alive"] = not page.is_closed()
                                cdp = await self.contexts[identity].new_cdp_session(page)
                                window = await cdp.send("Browser.getWindowForTarget")
                                await cdp.detach()
                                bounds = window["bounds"]
                                if tuple(bounds[k] for k in ("left","top","width","height")) != (output.x,output.y,output.width,output.height):
                                    raise RuntimeError("browser is outside configured output")
                                try:
                                    if time.monotonic() - self.renewed[identity] > 3600:
                                        await self.renew(self.contexts[identity])
                                        self.renewed[identity] = time.monotonic()
                                    response = await self.contexts[identity].request.get(
                                        f"{self.config.feed_origin}/api/v1/display/presentation", timeout=2500)
                                    if response.status == 401:
                                        await self.renew(self.contexts[identity])
                                        self.renewed[identity] = time.monotonic()
                                        response = await self.contexts[identity].request.get(
                                            f"{self.config.feed_origin}/api/v1/display/presentation", timeout=2500)
                                    if response.status == 200:
                                        p = await response.json()
                                        selected = next(o for o in p["outputs"] if o["output_id"] == identity)
                                        ack = selected.get("renderer_ack")
                                        state["desired_render_revision"] = p["render_revision"]
                                        state["render_revision"] = ack["render_revision"] if ack else None
                                        if ack:
                                            state["dom_revision"] = ack["revision"]
                                            if state["physical_verified"]: state["applied_revision"] = ack["revision"]
                                    else: state["feed_status"] = "unavailable"
                                except Exception:
                                    # Keep the window and its explicit stale-data UI when the local feed is down.
                                    state["feed_status"] = "unavailable"
                        except Exception as error:
                            state["status"] = "renderer_error"
                            state["error_type"] = type(error).__name__
                            await self.close_output(identity)
                        self.states[identity] = state
                    write_status(self.status_path, {"schema_version": 1, "layout_epoch": self.epoch,
                        "backend": self.config.backend, "updated_monotonic": time.monotonic(), "outputs": self.states})
                    await asyncio.sleep(1)
            finally:
                for identity in set(self.contexts) | set(self.profiles): await self.close_output(identity)
                if self.browser:
                    try: await self.browser.close()
                    finally: self.browser = None
                write_status(self.status_path, {"schema_version": 1, "layout_epoch": self.epoch,
                    "backend": self.config.backend, "updated_monotonic": time.monotonic(), "outputs": {}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--status", default="/run/xenova/display-runtime.json")
    parser.add_argument("--allow-virtual", action="store_true")
    parser.add_argument("--test-no-sandbox", action="store_true")
    parser.add_argument("--chromium")
    parser.add_argument("--prevent-idle-blank", action="store_true", help="dedicated X11 session only")
    parser.add_argument("--shared-browser", action="store_true", help="one Chromium process with isolated output contexts")
    args = parser.parse_args()
    kiosk = Kiosk(RuntimeConfig.load(args.config), os.environ.get("XENOVA_DISPLAY_TOKEN"), args.status,
                  allow_virtual=args.allow_virtual, chromium_path=args.chromium, test_no_sandbox=args.test_no_sandbox, prevent_idle_blank=args.prevent_idle_blank, shared_browser=args.shared_browser)
    async def serve():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await kiosk.run()
        except asyncio.CancelledError:
            pass
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
    try: asyncio.run(serve())
    except KeyboardInterrupt: pass
