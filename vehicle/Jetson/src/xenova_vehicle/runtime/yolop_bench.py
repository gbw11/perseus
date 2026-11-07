"""YOLOP 진단 벤치의 A worker. 큰 bytes와 SDK는 이 자식에만 존재한다."""

from collections import deque
import hashlib
import os
from pathlib import Path
import signal
import socket
import threading
import time

from .capture import _json_bytes, _short_text, j_us
from .channel import ControlChannel


def _rss():
    """Linux 실제 현재/고수위 RSS와 thread 수만 읽는다. 다른 OS는 미측정이다."""
    try:
        fields = {}
        with open("/proc/self/status", encoding="ascii") as source:
            for line in source:
                key, _, value = line.partition(":")
                if key in ("VmRSS", "VmHWM", "Threads"):
                    fields[key] = int(value.strip().split()[0])
        return fields
    except (OSError, ValueError):
        return {}


def yolop_worker(endpoint, profile, duration_s, output, context, source_factory=None):
    """하나의 graph를 유지하며 유한 표본만 저장한다. NN_READY/metric 권한을 만들지 않는다."""
    fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        if fd > 2:
            os.close(fd)
    from ..oak.frames import RawFrame
    from ..oak.observations import ClockEvidence, ObservationTracker
    from ..oak.yolop_frames import NNInputStamp, NNPacket
    from ..oak.yolop_association import YolopAssociation
    from ..recording.yolop_diagnostics import YolopDiagnosticWriter

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    channel = ControlChannel(endpoint)
    context_key = hashlib.sha256(_json_bytes(context)).hexdigest()
    association = YolopAssociation(context_key, nn_timeout_us=500_000)
    observations = ObservationTracker(profile.sync_tolerance_us)
    bridge = ClockEvidence()
    source = writer = None
    phase = "STARTING"
    since = progress = j_us()
    stream_progress = dict.fromkeys(context["stream_instances"])
    counts = dict.fromkeys(stream_progress, 0)
    probe_counts = dict.fromkeys(stream_progress, 0)
    late = dict.fromkeys(stream_progress, 0)
    prestart = dict.fromkeys(stream_progress, 0)
    depth_ring = deque(maxlen=8)
    rgb_clock = {}
    attempt_deadline = int(context["startup_deadline_j_us"]) / 1_000_000
    phase_deadline = attempt_deadline
    error = None
    reason = "failed"
    closed = peer_gone = False
    max_poll_us = max_age_us = 0
    max_steady_poll_us = 0
    validation_total_us = validation_max_us = validation_elements = 0
    capture_started_j = capture_ended_j = None
    arrival_lag_min = arrival_lag_max = None
    writer_stats = {}
    probe = None
    probe_packet = None
    setup = None
    last_match_progress = None
    last_match_source_key = None

    def snapshot(final=False):
        nonlocal peer_gone
        stats = association.snapshot()
        value = {"kind": "final" if final else "status", "phase": phase,
                 "phase_since_j_us": since, "last_progress_j_us": progress,
                 "last_stream_progress_j_us": dict(stream_progress),
                 "observations": observations.snapshot(), "recording": writer_stats,
                 "nn_bench": {"matched": stats.get("matched", 0), "association": stats,
                              "last_match_progress_j_us": last_match_progress,
                              "last_match_source_key": last_match_source_key,
                              "received": counts, "startup_received": probe_counts,
                              "prestart_dropped": prestart, "age_rejected": late,
                              "max_capture_age_upper_us": max_age_us,
                              "max_poll_us": max_poll_us, "max_steady_poll_us": max_steady_poll_us,
                              "validation_total_us": validation_total_us,
                              "validation_max_us": validation_max_us,
                              "validation_elements": validation_elements,
                              "capture_elapsed_us": ((capture_ended_j or j_us()) - capture_started_j
                                                     if capture_started_j is not None else None),
                              "arrival_difference_min_us": arrival_lag_min,
                              "arrival_difference_max_us": arrival_lag_max, "rss": _rss()},
                 "nn_ready": False, "metric_ready": False,
                 "device_close_confirmed": closed, "reason": reason,
                 "error": _short_text(error) if error else None}
        try:
            return channel.send(value)
        except (BrokenPipeError, ConnectionError, OSError, ValueError):
            peer_gone = True
            return False

    def stage(name):
        nonlocal phase, since, phase_deadline
        now = time.monotonic()
        if name not in ("DRAINING", "CLOSING", "CLOSED", "FAILED") and phase != "CAPTURING" \
                and (now >= attempt_deadline or now >= phase_deadline):
            raise TimeoutError("original startup/stage deadline expired before next state")
        phase, since = name, j_us()
        cap = {"ASSET_CHECK": 15, "DEVICE_BOOT": 30, "PIPELINE_START": 30,
               "NN_LAYOUT_PROBE": profile.nn_first_timeout_s, "RECORDING_SETUP": 15}.get(name)
        phase_deadline = min(attempt_deadline, now + cap) if cap is not None else attempt_deadline
        snapshot()

    def stopped():
        nonlocal peer_gone
        try:
            return next((m.get("reason", "stopped") for m in channel.receive() if m.get("kind") == "stop"), None)
        except (EOFError, BrokenPipeError, ConnectionError, OSError):
            peer_gone = True
            return "parent_disconnected"

    def poll():
        nonlocal max_poll_us, max_steady_poll_us
        begin = j_us()
        items = source.poll()
        elapsed = j_us() - begin
        max_poll_us = max(max_poll_us, elapsed)
        if phase == "CAPTURING":
            max_steady_poll_us = max(max_steady_poll_us, elapsed)
        if len(items) > 16 or sum(len(getattr(item, "payload", b"")) for item in items) > 4 * 1024**2:
            raise ValueError("YOLOP poll exceeds its message/copy budget")
        return items

    try:
        if source_factory is None:
            from ..oak.yolop_device import YolopOakSource
            source_factory = YolopOakSource
        source = source_factory(profile, context_key=context_key)
        setup = source.start(stage)
        # 최초 SDK accessor golden 비용을 정상 capture500ms 감시와 구분한다.
        stage("NN_LAYOUT_PROBE")
        probe_deadline = time.monotonic() + profile.nn_first_timeout_s
        while probe is None:
            stop = stopped()
            if stop:
                reason = "stopped"
                break
            if time.monotonic() >= probe_deadline:
                raise TimeoutError("no initial NN layout/golden within original probe deadline")
            items = poll()
            if time.monotonic() >= probe_deadline:
                raise TimeoutError("initial NN layout/golden completed after original deadline")
            for item in items:
                probe_counts[item.stream] += 1
                if isinstance(item, NNPacket) and probe is None:
                    probe_packet = item
                    probe = item.descriptor()
                    golden = probe.get("golden") or {}
                    if golden.get("matched") is not True:
                        raise ValueError("first NN SDK accessor golden comparison failed or missing")
            if probe is None:
                time.sleep(0.005)
        if probe is not None and reason != "stopped":
            stage("RECORDING_SETUP")
            bridge.update(source.sample_clock())
            barrier = bridge.sample["sdk_us"]
            manifest = {"schema_version": 1, "scope": "yolop-independent-diagnostics",
                        "not_capture_v1": True, "context": context, "profile": profile.to_dict(),
                        "setup": setup, "first_nn_layout_probe": probe,
                        "nn_context_key": context_key, "nn_ready": False, "metric_ready": False,
                        "pixel_equivalence": "not_evaluated"}
            writer = YolopDiagnosticWriter(Path(output), manifest, sample_limit=profile.diagnostic_samples,
                                            max_bytes=profile.max_bytes, min_free_bytes=profile.min_free_bytes)
            writer.start()
            started = time.monotonic()
            stage("CAPTURING")
            capture_started_j = since
            started = since / 1_000_000
            next_clock = next_status = started
            reason = "duration_elapsed"
            while time.monotonic() - started < duration_s:
                stop = stopped()
                if stop:
                    reason = "duration_elapsed" if stop == "duration_elapsed" else "stopped"
                    if peer_gone:
                        reason = "parent_disconnected"
                    break
                now = time.monotonic()
                if now >= next_clock:
                    bridge.update(source.sample_clock())
                    next_clock = now + 0.2
                items = poll()
                for item in items:
                    if item.sdk_us < barrier:
                        prestart[item.stream] += 1
                        continue
                    counts[item.stream] += 1
                    if isinstance(item, NNPacket):
                        validation_total_us += item.validation_elapsed_us
                        validation_max_us = max(validation_max_us, item.validation_elapsed_us)
                        validation_elements += item.validation_elements
                    clock = bridge.describe(item)
                    age = item.received_j_us - int(clock["sdk_host_timestamp_j_bracket_us"][0])
                    max_age_us = max(max_age_us, age)
                    if age < 0 or age > 500_000:
                        late[item.stream] += 1
                        continue
                    if isinstance(item, RawFrame):
                        observations.observe(item)
                        if item.stream == "depth_right":
                            depth_ring.append(item)
                        else:
                            rgb_clock[(item.sequence, item.device_us)] = clock
                            while len(rgb_clock) > 8:
                                del rgb_clock[next(iter(rgb_clock))]
                    stream_progress[item.stream] = item.received_j_us
                    progress = j_us()
                    for match in association.observe(item, context_key=context_key):
                        source_key = [context["stream_instances"]["rgb_video"],
                                      str(match.rgb.sequence), str(match.rgb.device_us)]
                        if source_key == last_match_source_key:
                            raise ValueError("duplicate original input cannot renew NN pairing progress")
                        last_match_progress = match.packet.received_j_us
                        last_match_source_key = source_key
                        arrival_lag_min = (match.arrival_difference_us if arrival_lag_min is None
                                           else min(arrival_lag_min, match.arrival_difference_us))
                        arrival_lag_max = (match.arrival_difference_us if arrival_lag_max is None
                                           else max(arrival_lag_max, match.arrival_difference_us))
                        nearest = min(depth_ring, key=lambda d: abs(d.sdk_us - match.rgb.sdk_us), default=None)
                        if nearest is None or abs(nearest.sdk_us - match.rgb.sdk_us) > profile.sync_tolerance_us:
                            continue
                        packet = match.packet
                        metadata = {"schema_version": 1, "scope": "yolop-independent-diagnostic-sample",
                                    "context": context, "nn_context_key": context_key,
                                    "rgb": match.rgb.descriptor(), "depth": nearest.descriptor(),
                                    "nn_input": match.input.descriptor(), "nn_output": packet.descriptor(),
                                    "rgb_source_key": source_key,
                                    "capture_clock": rgb_clock[(match.rgb.sequence, match.rgb.device_us)],
                                    "source_timestamp_delta_us": match.source_timestamp_delta_us,
                                    "arrival_difference_us": match.arrival_difference_us,
                                    "depth_candidate_sdk_delta_us": nearest.sdk_us - match.rgb.sdk_us,
                                    "qualification": "timestamp_candidate_only; not metric", "metric_ready": False}
                        payloads = {"rgb.nv12": match.rgb.payload, "depth.raw16le": nearest.payload,
                                    "nn-buffer.bin": packet.payload}
                        payloads.update({name + ".fp16": packet.layer_bytes(name)
                                         for name in ("det_out", "drive_area_seg", "lane_line_seg")})
                        writer.submit(metadata, payloads)
                association.expire(j_us())
                writer_stats = writer.snapshot()
                if writer_stats["error"]:
                    raise RuntimeError(writer_stats["error"])
                if now >= next_status:
                    snapshot()
                    next_status = now + 0.1
                if not items:
                    time.sleep(0.005)
    except Exception as exc:
        error = _short_text(f"{type(exc).__name__}: {exc}")
        reason = "worker_error"
        try:
            failure = getattr(source, "take_failure_diagnostic", lambda: None)()
            if failure is None and probe_packet is not None and writer is None:
                failure = {"metadata": {"first_nn_layout_probe": probe},
                           "payloads": {"nn-buffer.bin": probe_packet.payload}}
                failure["payloads"].update({name + ".fp16": probe_packet.layer_bytes(name)
                                            for name in ("det_out", "drive_area_seg", "lane_line_seg")})
            if failure is not None:
                if writer is None:
                    writer = YolopDiagnosticWriter(Path(output), {
                        "schema_version": 1, "scope": "yolop-failure-diagnostics",
                        "not_capture_v1": True, "context": context, "profile": profile.to_dict(),
                        "setup": setup, "nn_ready": False, "metric_ready": False},
                        sample_limit=profile.diagnostic_samples, max_bytes=profile.max_bytes,
                        min_free_bytes=profile.min_free_bytes)
                    writer.start()
                writer.submit_failure({"schema_version": 1, "scope": "yolop-failure-diagnostics",
                                       "context": context, "nn_context_key": context_key,
                                       "error": error, "failed_phase": phase,
                                       "raw_unvalidated": True, "evidence": failure["metadata"],
                                       "nn_ready": False, "metric_ready": False}, failure["payloads"])
        except Exception as diagnostic_exc:
            error = _short_text(error + f"; diagnostic: {type(diagnostic_exc).__name__}")
    finally:
        if capture_started_j is not None:
            capture_ended_j = j_us()
        deadline = time.monotonic() + profile.close_timeout_s
        stage("DRAINING")
        if writer is not None:
            try:
                writer_stats = writer.close(0)
            except Exception as exc:
                error = error or _short_text(f"diagnostic drain: {type(exc).__name__}: {exc}")
        stage("CLOSING")
        if source is not None:
            try:
                source.close()
                closed = True
            except Exception as exc:
                error = error or _short_text(f"device close: {type(exc).__name__}: {exc}")
        if writer is not None:
            try:
                writer_stats = writer.close(max(0, deadline - time.monotonic()))
                if not writer_stats["writer_exit_confirmed"] or writer_stats["error"]:
                    error = error or writer_stats["error"] or "diagnostic writer did not exit"
            except Exception as exc:
                error = error or _short_text(f"diagnostic close: {type(exc).__name__}: {exc}")
        phase, since = ("CLOSED" if closed else "FAILED"), j_us()
        until = time.monotonic() + 0.1
        while not peer_gone and time.monotonic() < until:
            if snapshot(final=True):
                break
            time.sleep(0.001)
        try:
            channel.endpoint.shutdown(socket.SHUT_WR)
            until = time.monotonic() + 0.05
            while time.monotonic() < until:
                channel.receive()
                time.sleep(0.001)
        except (OSError, EOFError, ValueError):
            pass
        channel.close()


def run_yolop_bench(profile, duration_s, output, *, source_factory=None):
    """별도 진단 결과만 만든다. 기존 capture/replay version1이 아니다."""
    from .bench_supervisor import supervise_bench
    return supervise_bench(profile, duration_s, output, worker_target=yolop_worker,
                           source_factory=source_factory, yolop=True)
