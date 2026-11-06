"""실제 카메라가 아닌 spawn 시험 입력. 제품 CLI에는 이 backend를 노출하지 않는다."""

import os
import time

from xenova_vehicle.oak.frames import RawFrame


class FakeSource:
    def __init__(self, profile):
        self.sequence = 0
        self.next_frame = 0

    def start(self, stage):
        stage("DEVICE_BOOT")
        stage("PIPELINE_START")
        print("fixture native stdout must not corrupt the JSON receipt")
        return {"fixture": True, "hardware_verified": False}

    def sample_clock(self):
        before = time.monotonic_ns() // 1000
        return {"j_before_us": before, "sdk_us": before + 5_000_000,
                "j_after_us": time.monotonic_ns() // 1000}

    def poll(self):
        now = time.monotonic()
        if now < self.next_frame:
            return []
        self.next_frame = now + 1 / 15
        self.sequence += 1
        stamp = time.monotonic_ns() // 1000
        return [RawFrame(stream, self.sequence + offset, stamp - 100_000, stamp + 5_000_000,
                         stamp, 640, height, encoding, bytes(size), 1000)
                for stream, offset, height, encoding, size in
                (("rgb_video", 100, 360, "NV12", 345600),
                 ("depth_right", 0, 480, "RAW16_LE", 614400))]

    def close(self):
        pass


class BootHangSource(FakeSource):
    def start(self, stage):
        stage("DEVICE_BOOT")
        time.sleep(60)


class PollHangSource(FakeSource):
    def poll(self):
        time.sleep(60)
        return []


class CloseHangSource(FakeSource):
    def close(self):
        time.sleep(60)


class FailingSource(FakeSource):
    def poll(self):
        raise RuntimeError("연결이 끊겼습니다 " * 2000)


class NoDepthSource(FakeSource):
    def poll(self):
        return super().poll()[:1]


class CrashingSource(FakeSource):
    def poll(self):
        os._exit(7)


class RecordingHangSource(FakeSource):
    def start(self, stage):
        from xenova_vehicle.recording.writer import CaptureWriter
        def blocked_write(*_args):
            time.sleep(60)
        CaptureWriter._write_frame = blocked_write
        return super().start(stage)


class FinalRetrySource(FakeSource):
    def start(self, stage):
        from xenova_vehicle.runtime.channel import ControlChannel
        original = ControlChannel.send
        attempts = 0
        def delayed_final(channel, value):
            nonlocal attempts
            if value.get("kind") == "final":
                attempts += 1
                if attempts < 3:
                    return False
            return original(channel, value)
        ControlChannel.send = delayed_final
        return super().start(stage)


def hang_receipt(*_args):
    time.sleep(60)
