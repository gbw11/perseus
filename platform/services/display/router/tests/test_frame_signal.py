import asyncio
from xenova_display.stream import FrameSignal, create_stream_app
from test_outputs import config


def test_capture_thread_notification_wakes_all_receivers_and_is_not_consumed():
    async def case():
        signal = FrameSignal(asyncio.get_running_loop())
        subscribed = signal.event
        first = asyncio.create_task(subscribed.wait())
        second = asyncio.create_task(subscribed.wait())
        await asyncio.to_thread(signal.notify)
        assert await asyncio.wait_for(first, 1)
        assert await asyncio.wait_for(second, 1)
        # A later client waits for the next frame instead of spinning on an old one.
        following = signal.event
        assert following is not subscribed and not following.is_set()
        await asyncio.to_thread(signal.notify)
        assert await asyncio.wait_for(following.wait(), 1)
    asyncio.run(case())


def test_publish_after_subscription_but_before_wait_does_not_lose_wakeup():
    async def case():
        signal = FrameSignal(asyncio.get_running_loop())
        subscribed = signal.event
        signal.notify()
        await asyncio.sleep(0)
        assert await asyncio.wait_for(subscribed.wait(), 1)
    asyncio.run(case())


def test_stream_broadcast_rechecks_invalid_frames_and_authentication(tmp_path):
    async def case():
        class FakeCapture:
            def __init__(self, *_):
                self.changed = FrameSignal(asyncio.get_running_loop())
                self.sequence, self.frame = 1, b"first"
            def start(self): pass
            def stop(self): pass
            def snapshot(self):
                return {"capture_epoch":"epoch", "frame_sequence":self.sequence}, self.frame
            def publish(self, frame):
                self.frame, self.sequence = frame, self.sequence+1
                self.changed.notify()
        class Request:
            headers = {"authorization":"Bearer test-key"}
            cookies = {}
            async def is_disconnected(self): return False
        app = create_stream_app(config(), tmp_path/"unused", "test-key",
                                allow_virtual=True, capture_factory=FakeCapture)
        endpoint = next(r.endpoint for r in app.routes if r.path == "/streams/{output_id}.mjpeg")
        request = Request()
        a = (await endpoint("monitor-1", request)).body_iterator
        b = (await endpoint("monitor-1", request)).body_iterator
        assert b"first" in await anext(a)
        assert b"first" in await anext(b)
        waiting = [asyncio.create_task(anext(a)), asyncio.create_task(anext(b))]
        capture = app.state.captures["monitor-1"]
        capture.publish(None)
        await asyncio.sleep(.01)
        assert all(not task.done() for task in waiting)
        capture.publish(b"current")
        for task in waiting: assert b"current" in await asyncio.wait_for(task, 1)
        # Frame notifications must not authorize a previously authenticated client.
        request.headers = {}
        capture.publish(b"private")
        try:
            await asyncio.wait_for(anext(a), 1)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("frame leaked after authentication was removed")
        await b.aclose()
    asyncio.run(case())


def test_notification_after_loop_close_is_safe():
    loop = asyncio.new_event_loop()
    signal = FrameSignal(loop)
    loop.close()
    signal.notify()
