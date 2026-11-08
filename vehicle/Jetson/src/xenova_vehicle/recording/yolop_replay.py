"""선택 YOLOP 진단 표본을 원 가용 순서로 전달한다. 장치·모델·운영 권한은 만들지 않는다."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import threading
import time
import uuid

from .reader import DatasetError
from .timeline import ReplayCancelled, ReplayTimeline
from .yolop_reader import IncompleteDiagnosticError, YolopDiagnosticReader


_RESULT_LIMIT = 64 * 1024
_EVENT_LIMIT = 256 * 1024
_TRACE_LIMIT = 2 * 1024**2


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii") + b"\n"


def _error(exc):
    return f"{type(exc).__name__}: {exc}"[:1024].encode("ascii", "backslashreplace").decode()


def _write_result(output, report, *, initial=False):
    data = _encoded(report)
    if len(data) > _RESULT_LIMIT:
        raise ValueError("YOLOP replay result exceeds 64KiB")
    final = output / "yolop-replay-result.json"
    path = final if initial else output / "yolop-replay-result.json.tmp"
    with path.open("xb") as target:
        if target.write(data) != len(data):
            raise OSError("short YOLOP replay receipt write")
    if not initial:
        path.replace(final)


def run_yolop_replay(dataset: Path, *, output: Path | None = None,
                     allow_incomplete: bool = False, speed: float = 1.0,
                     consumer=None, clock=time.monotonic, sleeper=time.sleep,
                     cancelled=lambda: False) -> dict:
    """전체 preflight 뒤 소유 bytes와 원 descriptor를 consumer에 전달한다.

    입력은 연속 NN 기록이 아닌 최대8개 선택 표본이다. speed는 원 네 stream의
    received J 최댓값인 availability 간격에 적용한다. 원 촬영/수신 stamp는 바꾸지
    않는다. output은 metadata/hash trace만 저장하며 실제 bytes는 API consumer가 받는다.
    source_complete와 replay_completed는 별개다. 사용자 취소와 신호는 최종 결과
    확정 전에 반영하며, 그 뒤 신호는 too_late로 기록하고 확정 결과를 유지한다.
    """
    if type(allow_incomplete) is not bool:
        raise ValueError("allow_incomplete must be boolean")
    if consumer is not None and not callable(consumer):
        raise ValueError("replay consumer must be callable")
    winner = {}
    late = {str(signal.SIGINT.value): 0, str(signal.SIGTERM.value): 0}

    def stopped():
        return winner.get("decision", ("finish", None))[0] == "abort" or cancelled()

    timeline = ReplayTimeline(speed, clock=clock, sleeper=sleeper, cancelled=stopped)
    if not callable(cancelled):
        raise TypeError("cancelled must be callable")
    dataset = Path(dataset).expanduser().absolute()
    if output is not None:
        # 원 입력은 reader가 symlink를 검사하므로 resolve한 값으로 바꾸지 않는다.
        try:
            source_target = dataset.resolve()
            output = Path(output).expanduser().resolve()
        except RuntimeError as exc:
            raise ValueError("diagnostic input/output path cannot be resolved") from exc
        if output == source_target or output.is_relative_to(source_target) or source_target.is_relative_to(output):
            raise ValueError("replay output must be separate from the source diagnostics")
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {"schema_version": 1, "scope": "yolop-selected-diagnostic-replay",
              "input_kind": "REPLAY", "selection_kind": "sparse_independent_samples",
              "continuous_nn_recording": False, "replay_instance": str(uuid.uuid4()),
              "dataset": str(dataset), "output": str(output) if output is not None else None,
              "started_utc": datetime.now(timezone.utc).isoformat(), "speed": float(speed),
              "allow_incomplete": allow_incomplete, "source_complete": None,
              "source_validation": None, "source_context": None,
              "replay_completed": False, "replayed_samples": 0, "consumer_calls": 0,
              "trace_events": 0, "trace_bytes": 0, "max_lateness_us": 0,
              "last_source_availability_j_us": None, "live_authority": False,
              "nn_ready": False, "metric_ready": False, "phase": "VALIDATING",
              "exit_code": 1, "error": None, "error_kind": None}
    handlers = {}
    trace = None
    began = time.monotonic()

    def on_signal(number, _frame):
        candidate = ("abort", number)
        if winner.setdefault("decision", candidate) is not candidate:
            late[str(number)] = min(1_000_000, late[str(number)] + 1)

    def check_cancelled():
        if stopped():
            raise ReplayCancelled("YOLOP diagnostic replay cancelled")

    try:
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                handlers[number] = signal.signal(number, on_signal)
        if output is not None:
            _write_result(output, report, initial=True)
        try:
            check_cancelled()
            reader = YolopDiagnosticReader(dataset, allow_incomplete=allow_incomplete)
            report["source_validation"] = reader.validate()
            report["source_complete"] = report["source_validation"]["source_complete"]
            report["source_context"] = deepcopy(reader.manifest["context"])
            check_cancelled()
            report["phase"] = "REPLAYING"
            if output is not None:
                trace = (output / "events.jsonl").open("xb", buffering=0)
            for sample in reader.samples():
                timing = timeline.wait(sample.availability_j_us)
                check_cancelled()
                # consumer가 metadata 사본을 수정해도 trace는 원 검증 자료를 유지한다.
                event = {"schema_version": 1, "scope": report["scope"], "input_kind": "REPLAY",
                         "replay_instance": report["replay_instance"], "ordinal": sample.ordinal,
                         "source_availability_j_us": str(sample.availability_j_us),
                         "source": deepcopy(sample.metadata), "delivery": timing,
                         "depth_time_qualification": sample.depth_time_qualification,
                         "payloads": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                      for name, data in sample.payloads.items()},
                         "live_authority": False, "nn_ready": False, "metric_ready": False}
                if consumer is not None:
                    report["consumer_calls"] += 1
                    consumer(sample)
                report["replayed_samples"] += 1
                report["last_source_availability_j_us"] = str(sample.availability_j_us)
                report["max_lateness_us"] = max(report["max_lateness_us"], timing["lateness_us"])
                if trace is not None:
                    line = _encoded(event)
                    if len(line) > _EVENT_LIMIT or report["trace_bytes"] + len(line) > _TRACE_LIMIT:
                        raise ValueError("YOLOP replay trace exceeds its byte limit")
                    if trace.write(line) != len(line):
                        raise OSError("short YOLOP replay trace write")
                    report["trace_bytes"] += len(line)
                    report["trace_events"] += 1
            check_cancelled()
            if report["replayed_samples"] != report["source_validation"]["sample_count"]:
                raise DatasetError("delivered sample count differs from the validated selection")
            report.update(replay_completed=True, exit_code=0, phase="FINISHED")
        except IncompleteDiagnosticError as exc:
            report.update(source_complete=False, error=_error(exc), error_kind="incomplete_not_allowed", phase="FAILED")
        except ReplayCancelled as exc:
            number = winner.setdefault("decision", ("abort", signal.SIGINT))[1]
            report.update(exit_code=128 + number, error=_error(exc), error_kind="cancelled", phase="CANCELLED")
        except DatasetError as exc:
            report.update(error=_error(exc), error_kind="invalid_diagnostics", phase="FAILED")
        except Exception as exc:
            report.update(error=_error(exc), error_kind="replay_error", phase="FAILED")
        finally:
            if trace is not None:
                try:
                    trace.close()
                except OSError as exc:
                    report.update(replay_completed=False, exit_code=1, error=_error(exc),
                                  error_kind="output_io", phase="FAILED")
        # 결과 확정 전 처리된 신호를 덮지 않는다. 이후 신호는 진단으로만 남긴다.
        try:
            if cancelled():
                winner.setdefault("decision", ("abort", signal.SIGINT))
        except Exception as exc:
            report.update(replay_completed=False, exit_code=1, error=_error(exc),
                          error_kind="cancel_callback", phase="FAILED")
        decision = winner.setdefault("decision", ("finish", None))
        if decision[0] == "abort":
            report.update(replay_completed=False, exit_code=128 + decision[1],
                          error="YOLOP diagnostic replay interrupted", error_kind="cancelled", phase="CANCELLED")
        report["elapsed_s"] = round(time.monotonic() - began, 6)
        if output is not None:
            try:
                _write_result(output, report)
            except (OSError, ValueError) as exc:
                report.update(replay_completed=False, exit_code=1, error=_error(exc),
                              error_kind="output_io", phase="FAILED")
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    report["finalization"] = {"decision": decision[0], "too_late_signals": late}
    return report
