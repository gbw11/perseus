"""검증한 파일 입력을 원 수신 순서로 전달한다. 카메라·운영 broker를 열지 않는다."""

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import signal
import threading
import time
import uuid

from .reader import DatasetError, DatasetReader
from .timeline import ReplayCancelled, ReplayTimeline

_MAX_TRACE_BYTES = 96 * 1024**2


class _ReplaySignal(BaseException):
    def __init__(self, number):
        self.number = number


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _error_text(exc) -> str:
    # 손상된 JSON의 lone surrogate도 터미널과 최종 receipt에 안전하게 남긴다.
    return f"{type(exc).__name__}: {exc}"[:1024].encode("utf-8", "backslashreplace").decode("utf-8")


def _write_result(output: Path, report: dict, *, initial=False) -> None:
    target = output / "replay-result.json"
    if initial:
        with target.open("xb") as stream:
            stream.write(_json_bytes(report))
        return
    temporary = output / "replay-result.json.tmp"
    with temporary.open("xb") as stream:
        stream.write(_json_bytes(report))
    temporary.replace(target)


def run_replay(dataset: Path, *, output: Path | None = None,
               expected_profile: dict | None = None, allow_incomplete: bool = False,
               speed: float = 1.0, consumer=None, clock=time.monotonic,
               sleeper=time.sleep, cancelled=lambda: False) -> dict:
    """전체 사전 검증 뒤 RawFrame+원 metadata를 consumer에 한 번씩 전달한다.

    speed=1은 원 received_j_us의 상대 간격, 0은 무대기다. 늦어도 원 프레임을
    버리거나 시각/ID를 갱신하지 않는다. consumer는 ReplaySample을 받으며,
    예외가 나면 재생을 중단한다. 결과의 replay_completed와 source_complete는
    별개다. output은 진단 trace 전용 새 디렉터리이며 source dataset을 수정하지 않는다.
    """
    timeline = ReplayTimeline(speed, clock=clock, sleeper=sleeper, cancelled=cancelled)
    if consumer is not None and not callable(consumer):
        raise ValueError("replay consumer must be callable")
    dataset = Path(dataset).expanduser().resolve()
    if output is not None:
        output = Path(output).expanduser().resolve()
        if output == dataset or output.is_relative_to(dataset) or dataset.is_relative_to(output):
            raise ValueError("replay output must be separate from the source dataset")
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"schema_version": 1, "scope": "offline-replay", "input_kind": "REPLAY",
              "replay_instance": str(uuid.uuid4()), "dataset": str(dataset),
              "output": str(output) if output is not None else None,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "speed": float(speed), "allow_incomplete": allow_incomplete,
              "replay_completed": False, "source_complete": None,
              "source_validation": None, "source_context": None,
              "replayed_frames": 0, "per_stream_counts": {"rgb_video": 0, "depth_right": 0},
              "trace_bytes": 0, "max_lateness_us": 0, "last_source_received_j_us": None,
              "live_authority": False, "metric_ready": False,
              "exit_code": 1, "error": None, "phase": "VALIDATING"}
    if output is not None:
        _write_result(output, report, initial=True)
    handlers = {}
    trace = None
    started = time.monotonic()

    def on_signal(number, _frame):
        raise _ReplaySignal(number)

    try:
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                handlers[number] = signal.signal(number, on_signal)
        if cancelled():
            raise ReplayCancelled("replay cancelled before validation")
        reader = DatasetReader(dataset, allow_incomplete=allow_incomplete,
                               expected_profile=expected_profile)
        report["source_validation"] = reader.validate()
        report["source_complete"] = report["source_validation"]["source_complete"]
        report["source_context"] = reader.manifest["context"]
        report["phase"] = "REPLAYING"
        if output is not None:
            trace = (output / "events.jsonl").open("xb", buffering=0)
        for sample in reader.frames():
            timing = timeline.wait(sample.frame.received_j_us)
            original_metadata = copy.deepcopy(sample.metadata) if trace is not None else None
            if consumer is not None:
                consumer(sample)
            if trace is not None:
                event = {"schema_version": 1, "input_kind": "REPLAY",
                         "replay_instance": report["replay_instance"], "ordinal": sample.ordinal,
                         "delivery": timing, "source": original_metadata,
                         "live_authority": False}
                line = _json_bytes(event)
                if len(line) > 40 * 1024 or report["trace_bytes"] + len(line) > _MAX_TRACE_BYTES:
                    raise ValueError("replay diagnostic trace exceeds its byte limit")
                view = memoryview(line)
                while view:
                    count = trace.write(view)
                    if not count:
                        raise OSError("replay trace write made no progress")
                    report["trace_bytes"] += count
                    view = view[count:]
            report["replayed_frames"] += 1
            report["per_stream_counts"][sample.frame.stream] += 1
            report["last_source_received_j_us"] = str(sample.frame.received_j_us)
            report["max_lateness_us"] = max(report["max_lateness_us"], timing["lateness_us"])
        if cancelled():
            raise ReplayCancelled("replay cancelled")
        if report["replayed_frames"] != report["source_validation"]["frame_count"]:
            raise DatasetError("replay frame count differs from validated index")
        report.update(replay_completed=True, exit_code=0, phase="FINISHED")
    except _ReplaySignal as exc:
        report.update(exit_code=128 + exc.number, error="replay interrupted", phase="CANCELLED")
    except (KeyboardInterrupt, ReplayCancelled):
        report.update(exit_code=130, error="replay cancelled", phase="CANCELLED")
    except Exception as exc:
        report.update(error=_error_text(exc), phase="FAILED")
    finally:
        if trace is not None:
            try:
                trace.close()
            except OSError as exc:
                report.update(replay_completed=False, exit_code=1,
                              error=_error_text(exc), phase="FAILED")
        for number, handler in handlers.items():
            signal.signal(number, handler)
    report["elapsed_s"] = round(time.monotonic() - started, 6)
    if output is not None:
        try:
            _write_result(output, report)
        except OSError as exc:
            report.update(replay_completed=False, exit_code=1,
                          error=_error_text(exc), phase="FAILED")
    return report
