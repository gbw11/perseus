"""SDK 없는 YOLOP spawn 시험 입력. 실제 장치/모델의 정확도를 증명하지 않는다."""

import time

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.yolop_frames import NNInputStamp, NNPacket, RuntimeTensor


def packet(sequence, device, sdk, received, context, *, golden=True):
    tensors = (
        RuntimeTensor("det_out", 0, 0x321, 3, (1, 6300, 6), (75600, 12, 2), 0),
        RuntimeTensor("drive_area_seg", 0, 0x4321, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 75648),
        RuntimeTensor("lane_line_seg", 0, 0x4321, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 485248),
    )
    return NNPacket("nn_output", sequence, device, sdk, received, bytes(894848), tensors,
                    context, {"performed": True, "matched": golden, "fixture": True})


class FakeYolopSource:
    def __init__(self, profile, *, context_key):
        self.context_key = context_key
        self.sequence = 0
        self.next_frame = 0

    def start(self, stage):
        stage("ASSET_CHECK")
        stage("DEVICE_BOOT")
        stage("PIPELINE_START")
        return {"fixture": True, "hardware_verified": False, "pipeline_start_count": 1,
                "blob_registration_count": 1, "nn_context_key": self.context_key}

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
        received = time.monotonic_ns() // 1000
        device = received - 100_000
        sdk = received + 5_000_000
        result = [RawFrame("rgb_video", self.sequence, device, sdk, received, 640, 360,
                           "NV12", bytes(345600), 1000),
                  RawFrame("depth_right", self.sequence + 100, device + 500, sdk + 500,
                           received + 1000, 640, 480, "RAW16_LE", bytes(614400), 1000)]
        if self.sequence % 3 == 1:
            result += [NNInputStamp("nn_input", self.sequence, device, sdk, received,
                                    320, 320, "BGR888p", self.context_key),
                       packet(self.sequence, device, sdk, received, self.context_key)]
        return result

    def close(self):
        pass


class NoNnSource(FakeYolopSource):
    def poll(self):
        return [x for x in super().poll() if isinstance(x, RawFrame)]


class NoPassthroughSource(FakeYolopSource):
    def poll(self):
        return [x for x in super().poll() if not isinstance(x, NNInputStamp)]


class NnStopsSource(FakeYolopSource):
    def poll(self):
        result = super().poll()
        return result if self.sequence <= 4 else [x for x in result if isinstance(x, RawFrame)]


class BadGoldenSource(FakeYolopSource):
    def poll(self):
        from dataclasses import replace
        return [replace(x, golden={"performed": True, "matched": False}) if isinstance(x, NNPacket)
                else x for x in super().poll()]


class BadLayoutSource(FakeYolopSource):
    def poll(self):
        raise ValueError("fixture: empty runtime strides rejected")

    def take_failure_diagnostic(self):
        return {"metadata": {"fixture": True, "tensors": [{"name": "det_out", "strides": []}]},
                "payloads": {"nn-buffer.bin": b"invalid-layout-fixture"}}


class BadAssetSource(FakeYolopSource):
    def start(self, stage):
        stage("ASSET_CHECK")
        raise ValueError("fixture: pinned blob SHA256 mismatch")


class NoDepthSource(FakeYolopSource):
    def poll(self):
        return [x for x in super().poll() if x.stream != "depth_right"]


class CloseHangSource(FakeYolopSource):
    def close(self):
        time.sleep(60)


class CloseSignalSource(FakeYolopSource):
    def __init__(self, profile, *, context_key):
        super().__init__(profile, context_key=context_key)
        self.closing_marker = profile.asset_dir

    def close(self):
        self.closing_marker.write_text("closing", encoding="ascii")
        time.sleep(1)


class PairingStopsSource(FakeYolopSource):
    def poll(self):
        from dataclasses import replace
        result = super().poll()
        return [replace(x, sequence=x.sequence + 1000)
                if self.sequence > 4 and isinstance(x, NNInputStamp) else x for x in result]


class SlowIntermittentNnSource(FakeYolopSource):
    def poll(self):
        result = super().poll()
        if self.sequence % 6 != 1 or self.sequence == 13:
            return [x for x in result if isinstance(x, RawFrame)]
        return result
