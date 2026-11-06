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
        stage("DRAINING")
        if writer is not None:
            try:
                writer_stats = writer.close(timeout_s=min(2.0, profile.close_timeout_s))
                if writer_stats.get("error") or not writer_stats.get("writer_exit_confirmed"):
                    error = error or writer_stats.get("error") or "recording writer did not exit"
            except Exception as exc:
                error = error or f"recording close: {type(exc).__name__}: {exc}"[:768]
        stage("CLOSING")
        if source is not None:
            try:
                source.close()
                close_confirmed = True
            except Exception as exc:
                error = error or f"device close: {type(exc).__name__}: {exc}"[:768]
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
    """한 번의 유한capture. 새 output만 생성하고 강제 회수·불완전 기록은 성공으로 세지 않는다."""
    if not isinstance(duration_s, (float, int)) or isinstance(duration_s, bool) \
            or not 0.1 <= duration_s <= 300:
        raise ValueError("duration must be finite and between 0.1 and 300 seconds")
    output = Path(output).expanduser().absolute()
    profile_bytes = _json_bytes(profile.to_dict())
    context = {name: str(uuid.uuid4()) for name in ("service_instance", "acquisition_worker",
               "pipeline_instance", "capture_epoch", "sdk_bridge_epoch", "clock_epoch")}
    context["stream_instances"] = {s: str(uuid.uuid4()) for s in ("rgb_video", "depth_right")}
    context["graph_profile_sha256"] = hashlib.sha256(profile_bytes).hexdigest()
    context["device_id"] = profile.device_mxid
    report = {"schema_version": 1, "scope": "rgb-native-depth-bench", "complete": False,
              "exit_code": 1, "reason": "starting", "context": context,
              "requested_duration_s": duration_s, "output": str(output),
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "process_exit_confirmed": False, "forced_termination": False,
              "metric_ready": False, "runtime_readiness": "not_evaluated"}
    parent_socket, child_socket = control_pair()
    channel = ControlChannel(parent_socket)
    process = multiprocessing.get_context("spawn").Process(
        name="xenova-acquisition", target=acquisition_worker,
        args=(child_socket, profile, duration_s, str(output), context, source_factory), daemon=False)
    began = time.monotonic()
    startup_deadline = began + profile.startup_timeout_s
    global_deadline = startup_deadline + duration_s + profile.close_timeout_s + 2.0
    stop_started = None
    stop_delivered = False
    capture_started = None
    last_status = None
    final_status = None
    parent_reason = None
    requested_signal = None
    writer_progress = 0
    writer_pending_since = None
    old_handlers = {}
    started = False
    error = None

    def on_signal(number, _frame):
        nonlocal requested_signal
        requested_signal = number

    try:
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                old_handlers[number] = signal.signal(number, on_signal)
        reservation = write_receipt(output, report, reserve=True)
        report["output_reservation"] = reservation
        if not reservation["ok"]:
            report["reason"] = "output_reservation_failed"
            report["error"] = reservation.get("error", "cannot reserve output")
            report["exit_code"] = 2 if reservation.get("error_type") == "FileExistsError" else 1
            return report
        if requested_signal:
            parent_reason = "interrupted" if requested_signal == signal.SIGINT else "terminated"
            raise RuntimeError("capture cancelled before acquisition startup")
        began = time.monotonic()
        startup_deadline = began + profile.startup_timeout_s
        global_deadline = startup_deadline + duration_s + profile.close_timeout_s + 2.0
        process.start()
        started = True
        child_socket.close()
        report["worker_pid"] = process.pid
        while True:
            try:
                messages = channel.receive()
            except EOFError:
                messages = []
            for message in messages:
                if message.get("kind") not in ("status", "final"):
                    raise ValueError("invalid acquisition control message")
                last_status = message
                if message["phase"] == "CAPTURING" and capture_started is None:
                    capture_started = message["phase_since_j_us"] / 1_000_000
                if message.get("kind") == "final":
                    final_status = message
            now = time.monotonic()
            if not process.is_alive():
                # 자식 exit 직전 송신한 마지막packet을 유한하게 회수한다.
                try:
                    for message in channel.receive():
                        if message.get("kind") == "final":
                            final_status = last_status = message
                except EOFError:
                    pass
                break
            reason = None
            if requested_signal:
                reason = "interrupted" if requested_signal == signal.SIGINT else "terminated"
            elif now >= global_deadline:
                reason = "global_timeout"
            elif capture_started is None and now >= startup_deadline:
                reason = "startup_timeout"
            elif last_status and last_status["phase"] in ("DEVICE_BOOT", "PIPELINE_START") \
                    and j_us() - last_status["phase_since_j_us"] >= 30_000_000:
                reason = "native_stage_timeout"
            elif last_status and last_status["phase"] == "CAPTURING":
                if capture_started is not None and now >= capture_started + duration_s:
                    reason = "duration_elapsed"
                elif j_us() - last_status["last_progress_j_us"] > profile.stall_timeout_s * 1_000_000:
                    reason = "capture_stalled"
                recording = last_status.get("recording", {})
                if recording.get("pending_frames", 0):
                    written = recording.get("written_frames", 0)
                    if writer_pending_since is None or written > writer_progress:
                        writer_pending_since = now
                        writer_progress = written
                    if now - writer_pending_since >= 5.0:
                        reason = "recording_stalled"
                else:
                    writer_pending_since = None
            elif last_status and last_status["phase"] in ("DRAINING", "CLOSING", "CLOSED", "FAILED"):
                reason = "worker_finishing"
            if reason and stop_started is None:
                stop_started = now
                parent_reason = reason
            if stop_started is not None:
                if not stop_delivered and parent_reason != "worker_finishing":
                    try:
                        stop_delivered = channel.send({"kind": "stop", "reason": parent_reason})
                    except OSError:
                        stop_delivered = True
                if now - stop_started >= profile.close_timeout_s:
                    break
            time.sleep(0.01)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:768]
        parent_reason = "supervisor_error"
    finally:
        child_socket.close()
        if started:
            _terminate(process, report)
        else:
            report["process_exit_confirmed"] = True
            report["worker_exit_code"] = None
        channel.close()
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
        if report["process_exit_confirmed"]:
            process.close()
    report["elapsed_s"] = round(time.monotonic() - began, 6)
    report["last_worker_status"] = final_status or last_status
    report["error"] = error or (final_status or {}).get("error")
    report["reason"] = (parent_reason if parent_reason not in (None, "worker_finishing")
                        else (final_status or {}).get("reason", "worker_exited_without_result"))
    if final_status:
        observations = final_status["observations"]
        recording = final_status["recording"]
        success = (parent_reason in (None, "duration_elapsed", "worker_finishing")
                   and final_status["reason"] == "duration_elapsed" and not final_status["error"]
                   and final_status["device_close_confirmed"] and recording.get("writer_exit_confirmed")
                   and not recording.get("error") and recording.get("recorded_candidate_pairs", 0) > 0
                   and all(recording.get("per_stream_counts", {}).get(s, 0) > 0
                           for s in ("rgb_video", "depth_right"))
                   and not report["forced_termination"] and report["process_exit_confirmed"]
                   and report["worker_exit_code"] == 0)
        report["complete"] = bool(success)
        if success:
            report["reason"] = "duration_elapsed"
            report["exit_code"] = 0
        elif not final_status["error"] and not recording.get("recorded_candidate_pairs", 0):
            report["error"] = "no synchronized RGB/native depth candidate pair recorded"
    if requested_signal:
        report["exit_code"] = 128 + requested_signal
    if report["process_exit_confirmed"]:
        receipt = write_receipt(output, report)
    else:
        receipt = {"ok": False, "error": "acquisition owner not reaped; final file I/O withheld"}
    report["final_receipt"] = receipt
    if not receipt["ok"]:
        report["complete"] = False
        report["exit_code"] = 1
        report["error"] = f"cannot finalize capture receipt: {receipt.get('error', 'unknown')}"[:768]
    return report
