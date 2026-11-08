"""벤치 공통 spawn 감시·원 기한·실종료. SDK/원본 payload를 가져오지 않는다."""

from datetime import datetime, timezone
import hashlib
import multiprocessing
from pathlib import Path
import signal
import threading
import time
import uuid

from .channel import ControlChannel, control_pair
from .receipts import write_receipt
from .capture import _json_bytes, _terminate, j_us


def supervise_bench(profile, duration_s: float, output: Path, *, worker_target,
                    source_factory=None, yolop=False) -> dict:
    """기존 capture와 YOLOP 벤치의 공통 부모 감시. SDK/원본 payload는 자식에만 둔다."""
    decision = {}
    late_signals = {str(signal.SIGINT.value): 0, str(signal.SIGTERM.value): 0}
    old_handlers = {}

    def on_signal(number, _frame):
        candidate = ("abort", number)
        if decision.setdefault("final", candidate) is not candidate:
            late_signals[str(number)] = min(1_000_000, late_signals[str(number)] + 1)

    def decide_final_signal():
        # CPython3.11의 단일 setdefault가 handler의 ABORT와 COMMIT 중 먼저 온 결정을 고정한다.
        return decision.setdefault("final", ("commit", None))[1]

    try:
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                old_handlers[number] = signal.signal(number, on_signal)
        return _supervise_bench(profile, duration_s, output, worker_target=worker_target,
                                source_factory=source_factory, yolop=yolop,
                                stop_signal=lambda: decision.get("final", ("commit", None))[1],
                                decide_final_signal=decide_final_signal, late_signals=late_signals)
    finally:
        # acquisition뿐 아니라 마지막 receipt 자식의 실종료까지 신호를 소유한다.
        for number, handler in old_handlers.items():
            signal.signal(number, handler)


def _supervise_bench(profile, duration_s, output, *, worker_target,
                     source_factory, yolop, stop_signal, decide_final_signal, late_signals):
    scope = "yolop-rgb-native-depth-bench" if yolop else "rgb-native-depth-bench"
    result_name = "yolop-bench-result.json" if yolop else "capture-result.json"
    if not isinstance(duration_s, (float, int)) or isinstance(duration_s, bool) \
            or not 0.1 <= duration_s <= 300:
        raise ValueError("duration must be finite and between 0.1 and 300 seconds")
    output = Path(output).expanduser().absolute()
    profile_bytes = _json_bytes(profile.to_dict())
    context = {name: str(uuid.uuid4()) for name in ("service_instance", "acquisition_worker",
               "pipeline_instance", "capture_epoch", "sdk_bridge_epoch", "clock_epoch")}
    context["stream_instances"] = {s: str(uuid.uuid4()) for s in (("rgb_video", "depth_right", "nn_input", "nn_output")
                                             if yolop else ("rgb_video", "depth_right"))}
    context["graph_profile_sha256"] = hashlib.sha256(profile_bytes).hexdigest()
    context["device_id"] = profile.device_mxid
    report = {"schema_version": 1, "scope": scope, "complete": False,
              "exit_code": 1, "reason": "starting", "context": context,
              "requested_duration_s": duration_s, "output": str(output),
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "process_exit_confirmed": False, "forced_termination": False,
              "metric_ready": False, "runtime_readiness": "not_evaluated"}
    parent_socket, child_socket = control_pair()
    channel = ControlChannel(parent_socket)
    process = multiprocessing.get_context("spawn").Process(
        name="xenova-acquisition", target=worker_target,
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
    fault_streams = []
    writer_progress = 0
    writer_pending_since = None
    started = False
    error = None

    try:
        reservation = write_receipt(output, report, reserve=True, filename=result_name)
        report["output_reservation"] = reservation
        if not reservation["ok"]:
            report["reason"] = "output_reservation_failed"
            report["error"] = reservation.get("error", "cannot reserve output")
            report["exit_code"] = 2 if reservation.get("error_type") == "FileExistsError" else 1
            return report
        if stop_signal():
            parent_reason = "interrupted" if stop_signal() == signal.SIGINT else "terminated"
            raise RuntimeError("capture cancelled before acquisition startup")
        began = time.monotonic()
        startup_deadline = began + profile.startup_timeout_s
        global_deadline = startup_deadline + duration_s + profile.close_timeout_s + 2.0
        if yolop:
            context["startup_deadline_j_us"] = str(int(startup_deadline * 1_000_000))
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
            if stop_signal():
                reason = "interrupted" if stop_signal() == signal.SIGINT else "terminated"
            elif now >= global_deadline:
                reason = "global_timeout"
            elif capture_started is None and now >= startup_deadline:
                reason = "startup_timeout"
            elif last_status and last_status["phase"] in ("ASSET_CHECK", "DEVICE_BOOT", "PIPELINE_START", "RECORDING_SETUP", "NN_LAYOUT_PROBE") \
                    and j_us() - last_status["phase_since_j_us"] >= (
                        min(profile.nn_first_timeout_s, 15) * 1_000_000 if last_status["phase"] == "NN_LAYOUT_PROBE"
                        else 15_000_000 if last_status["phase"] in ("ASSET_CHECK", "RECORDING_SETUP") else 30_000_000):
                reason = "native_stage_timeout"
            elif last_status and last_status["phase"] == "CAPTURING":
                if capture_started is not None and now >= capture_started + duration_s:
                    reason = "duration_elapsed"
                else:
                    current_j_us = j_us()
                    per_stream = last_status["last_stream_progress_j_us"]
                    stalled = [stream for stream, stamp in per_stream.items()
                               if stamp is not None
                               and current_j_us - stamp > (profile.nn_stall_timeout_s if yolop and stream.startswith("nn_")
                                                         else profile.stall_timeout_s) * 1_000_000]
                    # 첫 입력 대기는 원 startup/capture 기한에 묶고 상태 통지로 연장하지 않는다.
                    first_frame_deadline = min(startup_deadline, capture_started + 15.0)
                    missing = [stream for stream, stamp in per_stream.items() if stamp is None
                               and (not yolop or not stream.startswith("nn_"))]
                    nn_missing = [stream for stream, stamp in per_stream.items()
                                  if yolop and stream.startswith("nn_") and stamp is None]
                    if stalled:
                        reason = "capture_stalled"
                        if stop_started is None:
                            fault_streams = stalled
                    elif missing and now >= first_frame_deadline:
                        reason = "capture_warmup_timeout"
                        if stop_started is None:
                            fault_streams = missing
                    elif nn_missing and now >= min(startup_deadline, capture_started + profile.nn_first_timeout_s):
                        reason = "nn_first_result_timeout"
                        if stop_started is None:
                            fault_streams = nn_missing
                    elif yolop and not last_status.get("nn_bench", {}).get("matched", 0) \
                            and now >= min(startup_deadline, capture_started + profile.nn_first_timeout_s):
                        reason = "nn_association_timeout"
                    elif yolop and last_status.get("nn_bench", {}).get("matched", 0):
                        matched_j = last_status["nn_bench"].get("last_match_progress_j_us")
                        if matched_j is None or current_j_us - matched_j > profile.nn_stall_timeout_s * 1_000_000:
                            reason = "nn_association_stalled"
                            if stop_started is None:
                                fault_streams = ["nn_pairing"]
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
        if report["process_exit_confirmed"]:
            process.close()
    report["elapsed_s"] = round(time.monotonic() - began, 6)
    report["last_worker_status"] = final_status or last_status
    report["fault_streams"] = fault_streams
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
        if yolop:
            nn_bench = final_status.get("nn_bench", {})
            success = (parent_reason in (None, "duration_elapsed", "worker_finishing")
                       and final_status["reason"] == "duration_elapsed" and not final_status["error"]
                       and final_status["device_close_confirmed"] and recording.get("writer_exit_confirmed")
                       and not recording.get("error") and recording.get("saved_samples", 0) > 0
                       and nn_bench.get("matched", 0) > 0
                       and all(observations.get("received_frames", {}).get(s, 0) > 0
                               for s in ("rgb_video", "depth_right"))
                       and not report["forced_termination"] and report["process_exit_confirmed"]
                       and report["worker_exit_code"] == 0)
            report.update(nn_ready=False, metric_ready=False, runtime_readiness="not_evaluated")
        report["complete"] = bool(success)
        if success:
            report["reason"] = "duration_elapsed"
            report["exit_code"] = 0
        elif yolop and not final_status["error"]:
            report["error"] = "no complete saved diagnostic sample with fresh NN/input/RGB association"
        elif not final_status["error"] and not recording.get("recorded_candidate_pairs", 0):
            report["error"] = "no synchronized RGB/native depth candidate pair recorded"
    def apply_signal():
        if stop_signal():
            report["complete"] = False
            report["reason"] = "interrupted" if stop_signal() == signal.SIGINT else "terminated"
            report["exit_code"] = 128 + stop_signal()

    apply_signal()
    # COMMIT/ABORT 결정이 최종화의 선형화점이다. 이후 신호는 결과를 뒤집지 않는다.
    def decide_receipt():
        number = decide_final_signal()
        if number is not None:
            apply_signal()
            return {"kind": "abort", "override": {name: report[name]
                    for name in ("complete", "reason", "exit_code")}}
        return {"kind": "commit"}

    # 기존 5초 I/O+2초 회수 총상한을 유지하며 재시도하지 않는다.
    final_deadline = time.monotonic() + 7.0
    if report["process_exit_confirmed"]:
        receipt = write_receipt(output, report, filename=result_name,
                                deadline=final_deadline, decide=decide_receipt)
    else:
        receipt = {"ok": False, "error": "acquisition owner not reaped; final file I/O withheld"}
    receipt = {**receipt, "deadline_j_us": str(int(final_deadline * 1_000_000)),
               "too_late_signals": late_signals,
               "disk_consistency": "confirmed" if receipt["ok"] else "unknown"}
    report["final_receipt"] = receipt
    if not receipt["ok"]:
        report["complete"] = False
        report["exit_code"] = 1
        report["reason"] = "final_receipt_failed"
        report["error"] = f"cannot finalize capture receipt: {receipt.get('error', 'unknown')}"[:768]
    return report
