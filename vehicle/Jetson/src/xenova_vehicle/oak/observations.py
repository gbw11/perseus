"""벤치 수집의 순서·동기 후보와 SDK/J 시계 근거. metric 준비 판정은 하지 않는다."""

from collections import deque

from .frames import RawFrame


class ObservationTracker:
    """원 순번/시각을 검증하고 최대8개/500ms의 metadata만 보유한다."""

    def __init__(self, tolerance_us: int = 10_000):
        self.tolerance_us = tolerance_us
        self.last: dict[str, dict] = {}
        self.pending = {name: deque() for name in ("rgb_video", "depth_right")}
        self.received = dict.fromkeys(self.pending, 0)
        self.sequence_gaps = dict.fromkeys(self.pending, 0)
        self.unmatched = dict.fromkeys(self.pending, 0)
        self.candidate_pairs = 0

    def observe(self, frame: RawFrame) -> dict | None:
        frame.validate()
        expected = (640, 360) if frame.stream == "rgb_video" else (640, 480)
        if (frame.width, frame.height) != expected:
            raise ValueError("frame dimensions differ from the fixed bench profile")
        previous = self.last.get(frame.stream)
        if previous is not None:
            if (frame.sequence <= previous["sequence"] or frame.sdk_us <= previous["sdk_us"]
                    or frame.device_us <= previous["device_us"]
                    or frame.received_j_us < previous["received_j_us"]):
                raise ValueError(f"non-increasing source sequence/time: {frame.stream}")
            self.sequence_gaps[frame.stream] += frame.sequence - previous["sequence"] - 1
        # payload를 유지하지 않고 순서 검사에 필요한 작은 metadata만 남긴다.
        self.last[frame.stream] = {"sequence": frame.sequence, "sdk_us": frame.sdk_us,
                                  "device_us": frame.device_us,
                                  "received_j_us": frame.received_j_us}
        self.received[frame.stream] += 1
        for stream, ring in self.pending.items():
            while ring and frame.received_j_us - ring[0]["received_j_us"] > 500_000:
                ring.popleft()
                self.unmatched[stream] += 1
        other = "depth_right" if frame.stream == "rgb_video" else "rgb_video"
        candidates = self.pending[other]
        nearest = min(candidates, key=lambda item: abs(item["sdk_us"] - frame.sdk_us),
                      default=None)
        current = {"sequence": frame.sequence, "sdk_us": frame.sdk_us,
                   "device_us": frame.device_us, "received_j_us": frame.received_j_us}
        if nearest is not None and abs(nearest["sdk_us"] - frame.sdk_us) <= self.tolerance_us:
            candidates.remove(nearest)
            self.candidate_pairs += 1
            return {"other_stream": other, "other_sequence": str(nearest["sequence"]),
                    "other_device_timestamp_us": str(nearest["device_us"]),
                    "sdk_timestamp_delta_us": frame.sdk_us - nearest["sdk_us"],
                    "qualification": "timestamp_candidate_only"}
        ring = self.pending[frame.stream]
        if len(ring) == 8:
            ring.popleft()
            self.unmatched[frame.stream] += 1
        ring.append(current)
        return None

    def snapshot(self) -> dict:
        return {"received_frames": dict(self.received), "sequence_gaps": dict(self.sequence_gaps),
                "candidate_pairs": self.candidate_pairs,
                "unmatched_frames": {s: self.unmatched[s] + len(r) for s, r in self.pending.items()},
                "bench_warmup_30_pairs": self.candidate_pairs >= 30,
                "metric_ready": False}


class ClockEvidence:
    """SDK host clock→J bracket만 보존한다. OAK sync/readout 오차 bound는 미수락이다."""

    def __init__(self):
        self.sample: dict | None = None

    def update(self, sample: dict) -> None:
        if set(sample) != {"j_before_us", "sdk_us", "j_after_us"}:
            raise ValueError("invalid SDK/J clock sample fields")
        if any(type(v) is not int or not 0 <= v < 2**64 for v in sample.values()):
            raise ValueError("invalid SDK/J clock sample values")
        if sample["j_before_us"] > sample["j_after_us"]:
            raise ValueError("reversed SDK/J clock bracket")
        if self.sample and (sample["j_before_us"] < self.sample["j_after_us"]
                            or sample["sdk_us"] <= self.sample["sdk_us"]):
            raise ValueError("SDK/J clock moved backwards")
        self.sample = dict(sample)

    def describe(self, frame: RawFrame) -> dict:
        if self.sample is None:
            raise ValueError("missing SDK/J clock sample")
        sample = self.sample
        if not 0 <= frame.received_j_us - sample["j_after_us"] <= 1_000_000:
            raise ValueError("expired SDK/J clock sample")
        # 양 끝의1us는 정수us 변환의 양자화 여유이며 OAK 동기 정확도와 다르다.
        lower = sample["j_before_us"] - sample["sdk_us"] - 1
        upper = sample["j_after_us"] - sample["sdk_us"] + 1
        return {"bridge_sample": {k: str(v) for k, v in sample.items()},
                "sdk_host_timestamp_j_bracket_us": [str(frame.sdk_us + lower),
                                                     str(frame.sdk_us + upper)],
                "oak_sync_error_bound_us": None, "readout_error_bound_us": None,
                "metric_time_qualified": False}
