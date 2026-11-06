"""벤치 capture의 부모 감시와 단일 A 소유자. broker·지도·주행 권한은 다루지 않는다."""

from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import threading
import time
import uuid

from .channel import ControlChannel, control_pair
from .receipts import write_receipt


def j_us() -> int:
    return time.monotonic_ns() // 1000


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("ascii")


def _short_text(value) -> str:
    return str(value).encode("ascii", "backslashreplace").decode("ascii")[:512]


def acquisition_worker(endpoint, profile, duration_s, output, context, source_factory=None):
    """spawn 자식. Device·raw bytes·파일writer는 이 process 밖으로 나가지 않는다."""
    # SDK의 native stdout/stderr가 부모의 JSON 결과를 깨뜨리지 않게 한다.
    null_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
    finally:
        if null_fd > 2:
            os.close(null_fd)
    from ..oak.observations import ClockEvidence, ObservationTracker
    from ..recording.writer import CaptureWriter

    channel = ControlChannel(endpoint)
    if threading.current_thread() is threading.main_thread():
        # 첫 Ctrl+C는 부모가 cooperative stop으로 전달한다. TERM/KILL은 강제 회수에 남긴다.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    tracker = ObservationTracker(profile.sync_tolerance_us)
    bridge = ClockEvidence()
    source = writer = None
    phase = "STARTING"
    phase_since = j_us()
    progress = j_us()
    stream_progress = {stream: None for stream in ("rgb_video", "depth_right")}
    prestart_drops = 0
    error = None
    reason = "failed"
    writer_stats = {}
    close_confirmed = False
    peer_gone = False

    def status(final=False):
        nonlocal peer_gone
        recording = dict(writer_stats)
        if recording.get("error"):
            recording["error"] = _short_text(recording["error"])
        value = {"kind": "final" if final else "status", "phase": phase,
                 "phase_since_j_us": phase_since, "last_progress_j_us": progress,
                 "last_stream_progress_j_us": dict(stream_progress),
                 "observations": tracker.snapshot(), "recording": recording,
                 "prestart_dropped_frames": prestart_drops,
                 "error": _short_text(error) if error else None,
                 "reason": reason, "device_close_confirmed": close_confirmed}
        try:
            return channel.send(value)
        except (BrokenPipeError, ConnectionError, OSError, ValueError):
            peer_gone = True
            return False

    def stage(value):
        nonlocal phase, phase_since
        phase, phase_since = value, j_us()
        status()

    def stopped():
        nonlocal peer_gone
        try:
            return next((m.get("reason", "stopped") for m in channel.receive()
                         if m.get("kind") == "stop"), None)
        except (EOFError, BrokenPipeError, ConnectionError, OSError):
            peer_gone = True
            return "parent_disconnected"

    try:
        if source_factory is None:
            from ..oak.device import OakSource
            source_factory = OakSource
        source = source_factory(profile)
        setup = source.start(stage)
        if stopped():
            reason = "stopped"
        else:
            stage("RECORDING_SETUP")
            sample = source.sample_clock()
            bridge.update(sample)
            barrier_sdk_us = sample["sdk_us"]
            manifest = {"schema_version": 1, "scope": "rgb-native-depth-bench",
                        "context": context, "profile": profile.to_dict(), "setup": setup,
                        "nn_requirement": "ABSENT", "metric_ready": False,
                        "clock_qualification": "SDK-host/J bracket only; OAK error bound unvalidated"}
            writer = CaptureWriter(Path(output), manifest, profile.max_bytes, profile.min_free_bytes)
            writer.start()
            started = time.monotonic()
            progress = j_us()
            stage("CAPTURING")
            next_clock = started + 0.2
            next_status = started
            reason = "duration_elapsed"
            while time.monotonic() - started < duration_s:
                stop_reason = stopped()
                if stop_reason:
                    reason = "duration_elapsed" if stop_reason == "duration_elapsed" else "stopped"
                    if peer_gone:
                        reason = "parent_disconnected"
                    break
                now = time.monotonic()
                if now >= next_clock:
                    bridge.update(source.sample_clock())
                    next_clock = now + 0.2
                frames = source.poll()
                if len(frames) > 16 or sum(len(f.payload) for f in frames) > 4 * 1024**2:
                    raise ValueError("source poll exceeds its message/copy budget")
                for frame in frames:
                    if frame.sdk_us < barrier_sdk_us:
                        prestart_drops += 1
                        continue
                    pair = tracker.observe(frame)
                    provenance = {"capture_epoch": context["capture_epoch"],
                                  "stream_instance": context["stream_instances"][frame.stream],
                                  "source_key": [context["stream_instances"][frame.stream],
                                                 str(frame.sequence), str(frame.device_us)],
                                  "graph_profile_sha256": context["graph_profile_sha256"],
                                  "sdk_bridge_epoch": context["sdk_bridge_epoch"],
                                  "clock_evidence": bridge.describe(frame),
                                  "candidate_pair": pair}
                    writer.submit(frame, provenance)
                    progress = j_us()
                    stream_progress[frame.stream] = frame.received_j_us
                writer_stats = writer.snapshot()
                if writer_stats.get("error"):
                    raise RuntimeError(writer_stats["error"])
                if now >= next_status:
                    status()
                    next_status = now + 0.1
                if not frames:
                    time.sleep(0.005)
    except Exception as exc:
        error = _short_text(f"{type(exc).__name__}: {exc}")
        reason = "worker_error"
    finally:
        close_deadline = time.monotonic() + profile.close_timeout_s
        stage("DRAINING")
        if writer is not None:
            try:
                # 원본은 소유한 bytes이므로 writer drain과 Device close를 병행할 수 있다.
                writer_stats = writer.close(timeout_s=0)
            except Exception as exc:
                error = error or f"recording close: {type(exc).__name__}: {exc}"[:768]
        stage("CLOSING")
        if source is not None:
            try:
                source.close()
                close_confirmed = True
            except Exception as exc:
                error = error or f"device close: {type(exc).__name__}: {exc}"[:768]
        if writer is not None:
            try:
                remaining = max(0.0, close_deadline - time.monotonic())
                writer_stats = writer.close(timeout_s=remaining)
                if writer_stats.get("error") or not writer_stats.get("writer_exit_confirmed"):
                    error = error or writer_stats.get("error") or "recording writer did not exit"
            except Exception as exc:
                error = error or f"recording close: {type(exc).__name__}: {exc}"[:768]
        phase, phase_since = ("CLOSED" if close_confirmed else "FAILED"), j_us()
        flush_deadline = time.monotonic() + 0.1
        while time.monotonic() < flush_deadline and not peer_gone:
            if status(final=True):
                break
            time.sleep(0.001)
        try:
            channel.endpoint.shutdown(socket.SHUT_WR)
            # 늦은 stop을 읽지 않고 TCP를 닫으면 Windows 대체 통로에서 final이 유실될 수 있다.
            drain_until = time.monotonic() + 0.05
            while time.monotonic() < drain_until:
                channel.receive()
                time.sleep(0.001)
        except (OSError, EOFError, ValueError):
            pass
        channel.close()


def _terminate(worker, report):
    """실제 종료를 확인하기 전 재기동하지 않는다. 이 capture는 자동 재시도하지 않는다."""
    if worker.is_alive():
        report["forced_termination"] = True
        worker.terminate()
        worker.join(timeout=1.0)
    if worker.is_alive():
        worker.kill()
        worker.join(timeout=1.0)
    report["process_exit_confirmed"] = not worker.is_alive()
    report["worker_exit_code"] = worker.exitcode


def run_capture(profile, duration_s: float, output: Path, *, source_factory=None) -> dict:
    """기존 no-NN version1 벤치를 공통 감시기로 실행한다. 형식과 수락은 유지한다."""
    from .bench_supervisor import supervise_bench
    return supervise_bench(profile, duration_s, output, worker_target=acquisition_worker,
                           source_factory=source_factory)
