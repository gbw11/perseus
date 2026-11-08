"""YOLOP 원 입력·출력·RGB의 유한 association. depth sequence를 사용하지 않는다.

동일 source binding 안의 ColorCamera sequence/device timestamp와 원 SDK stamp를
대조한다. 수신 J 시각의 ring/arrival 차이는 capture age나 추론 시간의 증명이
아니며, SDK→J capture interval 검사는 외부 ClockEvidence의 책임이다.
"""

from collections import OrderedDict
from dataclasses import dataclass

from .frames import RawFrame
from .yolop_frames import NNInputStamp, NNPacket, _context, _u64


_STREAMS = ("rgb_video", "nn_input", "nn_output")


@dataclass(frozen=True)
class NNMatch:
    rgb: RawFrame
    input: NNInputStamp
    packet: NNPacket
    source_timestamp_delta_us: int
    arrival_difference_us: int

    def descriptor(self) -> dict:
        return {
            "rgb": self.rgb.descriptor(), "input": self.input.descriptor(),
            "packet": self.packet.descriptor(),
            "source_timestamp_delta_us": self.source_timestamp_delta_us,
            "arrival_difference_us": self.arrival_difference_us,
            "arrival_difference_basis": "NN output receive J minus original RGB receive J; not inference latency",
            "source_identity": "same binding and exact ColorCamera sequence/device timestamp",
            "capture_age_qualified": False, "metric_ready": False,
        }


class YolopAssociation:
    """각 stream 최대8개/500ms. epoch 교체는 새 instance로 시작한다.

    순서 역전은 서로 다른 stream 사이에서 허용한다. 같은 stream의 source
    sequence/device/SDK 시각 역전·duplicate는 거절해 만료된 입력의 재포장을 막는다.
    NN timeout은 별도이며 실제 ring 수명은 min(timeout,500ms)보다 길어지지 않는다.
    """

    def __init__(self, context_key: str, nn_timeout_us: int = 500_000, *,
                 capacity: int = 8, max_age_us: int = 500_000):
        _context(context_key)
        if type(capacity) is not int or not 1 <= capacity <= 8:
            raise ValueError("association capacity must be in 1..8")
        if type(max_age_us) is not int or not 0 < max_age_us <= 500_000:
            raise ValueError("association ring age must be in 1..500000us")
        if type(nn_timeout_us) is not int or not 0 < nn_timeout_us <= 30_000_000:
            raise ValueError("invalid NN association timeout")
        self.context_key = context_key
        self.capacity = capacity
        self.max_age_us = max_age_us
        self.nn_timeout_us = nn_timeout_us
        self.pending = {stream: OrderedDict() for stream in _STREAMS}
        self.received = dict.fromkeys(_STREAMS, 0)
        self.dropped = dict.fromkeys(_STREAMS, 0)
        self.reasons = {}
        self.matched = 0
        self.depth_ignored = 0
        self.validation_total_us = 0
        self.validation_max_us = 0
        self.validation_elements = 0
        self._last = {}
        self._now_j_us = 0
        self._expired_rgb = OrderedDict()

    def _drop(self, stream, key, reason):
        item = self.pending[stream].pop(key, None)
        if item is not None:
            self.dropped[stream] += 1
            self.reasons[reason] = self.reasons.get(reason, 0) + 1
            if stream == "nn_output":
                for other, missing in (("nn_input", "outputs_without_pending_input"),
                                       ("rgb_video", "outputs_without_pending_rgb")):
                    if key not in self.pending[other]:
                        self.reasons[missing] = self.reasons.get(missing, 0) + 1
        return item

    def _reject(self, stream, reason):
        self.dropped[stream] += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def _remember_expired_rgb(self, key, item):
        self._expired_rgb[key] = item.received_j_us
        while len(self._expired_rgb) > self.capacity:
            self._expired_rgb.popitem(last=False)

    def expire(self, now_j_us: int) -> None:
        """무입력 tick에서도 원 RGB 수신 J를 기준으로 만료한다."""
        _u64(now_j_us, "association now J")
        if now_j_us < self._now_j_us:
            raise ValueError("association now J moved backwards")
        self._now_j_us = now_j_us
        rgb = self.pending["rgb_video"]
        for key, item in list(rgb.items()):
            if now_j_us - item.received_j_us > self.max_age_us:
                self._remember_expired_rgb(key, item)
                self._drop("rgb_video", key, "expired_rgb")
        for stream in ("nn_output", "nn_input"):
            for key, item in list(self.pending[stream].items()):
                original = rgb.get(key)
                anchor = (original.received_j_us if original is not None
                          else self._expired_rgb.get(key, item.received_j_us))
                late = (stream == "nn_output" and original is not None
                        and item.received_j_us - original.received_j_us > self.nn_timeout_us)
                if now_j_us - anchor > self.max_age_us:
                    self._drop(stream, key, "expired_" + stream)
                elif late or (stream == "nn_output" and original is not None
                              and now_j_us - anchor > self.nn_timeout_us):
                    self._drop(stream, key, "late_nn_result")

    def observe(self, item, *, context_key: str | None = None) -> list[NNMatch]:
        if isinstance(item, RawFrame):
            item.validate()
            if item.stream == "depth_right":
                self.depth_ignored += 1
                return []
            if (item.width, item.height) != (640, 360):
                raise ValueError("association RGB must be the original 640x360 video")
            stream = "rgb_video"
            actual_context = context_key
        elif isinstance(item, NNInputStamp):
            item.validate()
            stream = "nn_input"
            actual_context = item.context_key
        elif isinstance(item, NNPacket):
            item.validate()
            stream = "nn_output"
            actual_context = item.context_key
            self.validation_total_us += item.validation_elapsed_us
            self.validation_max_us = max(self.validation_max_us, item.validation_elapsed_us)
            self.validation_elements += item.validation_elements
        else:
            raise TypeError("unsupported YOLOP association item")
        self.received[stream] += 1
        if (actual_context != self.context_key
                or context_key is not None and context_key != self.context_key):
            self._reject(stream, "context_mismatch")
            return []
        # 다른 stream의 늦게 전달된 사본은 J를 역전시키지 않고 원 수신 J를 보존한다.
        self.expire(max(self._now_j_us, item.received_j_us))
        current = (item.sequence, item.device_us, item.sdk_us, item.received_j_us)
        previous = self._last.get(stream)
        if previous is not None and (any(a <= b for a, b in zip(current[:3], previous[:3]))
                                     or current[3] < previous[3]):
            self._reject(stream, "duplicate_or_source_reversal")
            return []
        self._last[stream] = current
        key = (item.sequence, item.device_us)
        ring = self.pending[stream]
        if len(ring) >= self.capacity:
            old_key = next(iter(ring))
            old = self._drop(stream, old_key, "capacity_" + stream)
            if stream == "rgb_video":
                self._remember_expired_rgb(old_key, old)
        ring[key] = item
        self.expire(self._now_j_us)
        matches = []
        for candidate in list(self.pending["nn_output"]):
            rgb = self.pending["rgb_video"].get(candidate)
            stamp = self.pending["nn_input"].get(candidate)
            packet = self.pending["nn_output"].get(candidate)
            if rgb is None or stamp is None or packet is None:
                continue
            # no-arg NNData/ImgFrame Buffer stamp는 고정 SDK END와 동일하다.
            # 불일치 때 inference-completion stamp로 고쳐 붙이지 않는다.
            if not (packet.sdk_us == stamp.sdk_us == rgb.sdk_us):
                self._drop("nn_output", candidate, "source_timestamp_mismatch")
                continue
            if (self._now_j_us - rgb.received_j_us > self.max_age_us
                    or packet.received_j_us - rgb.received_j_us > self.nn_timeout_us):
                self._drop("nn_output", candidate, "late_nn_result")
                continue
            for name in _STREAMS:
                self.pending[name].pop(candidate)
            self.matched += 1
            matches.append(NNMatch(rgb, stamp, packet, packet.sdk_us - stamp.sdk_us,
                                   packet.received_j_us - rgb.received_j_us))
        return matches

    def snapshot(self) -> dict:
        payload_bytes = sum(len(item.payload) for stream in ("rgb_video", "nn_output")
                            for item in self.pending[stream].values())
        return {
            "context_key": self.context_key, "received": dict(self.received),
            "matched": self.matched, "pending": {s: len(r) for s, r in self.pending.items()},
            "dropped": dict(self.dropped), "reasons": dict(self.reasons),
            "unmatched_nn_outputs": self.received["nn_output"] - self.matched
                                     - len(self.pending["nn_output"]),
            "depth_frames_ignored": self.depth_ignored,
            "retained_payload_bytes": payload_bytes,
            "retained_payload_bound_bytes": self.capacity * (1_048_576 + 345600),
            "capacity_per_stream": self.capacity, "ring_max_age_us": self.max_age_us,
            "nn_timeout_us": self.nn_timeout_us,
            "validation_total_us": self.validation_total_us,
            "validation_max_us": self.validation_max_us,
            "validation_elements": self.validation_elements,
            "reason_counts_are_nonexclusive": True,
            "capture_age_qualified": False, "metric_ready": False,
        }
