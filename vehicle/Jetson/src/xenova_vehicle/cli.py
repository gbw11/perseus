"""가벼운 CLI 분기. 개별 명령의 의존성은 해당 명령에서 가져온다."""

import argparse
import json
import math
from pathlib import Path
import sys

from . import __version__


def _timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(seconds) or not 0.1 <= seconds <= 30:
        raise argparse.ArgumentTypeError("timeout must be between 0.1 and 30 seconds")
    return seconds


def _doctor_command(args: argparse.Namespace) -> int:
    from .doctor import diagnose, render_text

    report = diagnose(args.timeout)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        print(render_text(report))
    return report["exit_code"]


def _duration(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("duration must be a number") from exc
    if not math.isfinite(seconds) or not 0.1 <= seconds <= 300:
        raise argparse.ArgumentTypeError("duration must be between 0.1 and 300 seconds")
    return seconds


def _capture_command(args: argparse.Namespace) -> int:
    from .capture_profile import load_profile
    from .runtime.capture import run_capture

    try:
        profile = load_profile(args.profile)
        report = run_capture(profile, args.duration_s, args.output)
    except (OSError, ValueError) as exc:
        print(f"capture: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        result = "completed" if report["complete"] else "incomplete"
        print(f"Capture {result}: {report['reason']}\nOutput: {report['output']}")
        if report.get("error"):
            print(f"Error: {report['error']}")
        print(f"Acquisition exit confirmed: {report['process_exit_confirmed']}; "
              f"forced termination: {report['forced_termination']}")
    return report["exit_code"]


def _speed(value: str) -> float:
    try:
        speed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("speed must be a number") from exc
    if not math.isfinite(speed) or not 0 <= speed <= 16:
        raise argparse.ArgumentTypeError("speed must be between 0 and 16; 0 means no waiting")
    return speed


def _yolop_bench_command(args: argparse.Namespace) -> int:
    from .yolop_profile import load_yolop_profile
    from .runtime.yolop_bench import run_yolop_bench

    try:
        profile = load_yolop_profile(args.profile)
        report = run_yolop_bench(profile, args.duration_s, args.output)
    except (OSError, ValueError) as exc:
        print(f"yolop-bench: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        print(f"YOLOP bench {'completed' if report['complete'] else 'incomplete'}: {report['reason']}")
        print(f"Output: {report['output']}")
        if report.get("error"):
            print(f"Error: {report['error']}")
    return report["exit_code"]


def _replay_command(args: argparse.Namespace) -> int:
    from .capture_profile import load_profile
    from .recording.replay import run_replay

    try:
        profile = load_profile(args.profile).to_dict() if args.profile is not None else None
        report = run_replay(args.dataset, output=args.output, expected_profile=profile,
                            speed=args.speed, allow_incomplete=args.allow_incomplete)
    except (OSError, ValueError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        print(f"Replay {'finished' if report['replay_completed'] else 'incomplete'}: "
              f"{report['replayed_frames']} frames; source_complete={report['source_complete']}")
        if report.get("error"):
            print(f"Error: {report['error']}")
        print(f"Output: {report['output']}")
    return report["exit_code"]


def _yolop_replay_command(args: argparse.Namespace) -> int:
    from .recording.yolop_replay import run_yolop_replay

    try:
        report = run_yolop_replay(args.dataset, output=args.output, speed=args.speed,
                                  allow_incomplete=args.allow_incomplete)
    except (OSError, ValueError) as exc:
        print(f"yolop-replay: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        print(f"YOLOP diagnostic replay {'finished' if report['replay_completed'] else 'incomplete'}: "
              f"{report['replayed_samples']} selected samples; source_complete={report['source_complete']}")
        if report.get("error"):
            print(f"Error: {report['error']}")
        print(f"Output: {report['output']}")
    return report["exit_code"]


def main(argv: list[str] | None = None) -> int:
    """CLI를 실행하고 종료 코드를 반환한다. argparse 사용 오류는 2다."""
    parser = argparse.ArgumentParser(prog="xenova-vehicle")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="inspect the target environment and enumerate OAK devices")
    doctor.add_argument("--json", action="store_true", help="print one JSON report to stdout")
    doctor.add_argument("--timeout", type=_timeout, default=10.0,
                        help="total native probe budget in seconds, default: 10 (0.1..30)")
    capture = commands.add_parser("capture", help="record a bounded RGB/native-depth bench capture")
    capture.add_argument("--profile", type=Path, required=True, help="explicit OAK no-NN bench JSON")
    capture.add_argument("--duration-s", type=_duration, default=30.0,
                         help="recording duration after startup, default: 30 (0.1..300)")
    capture.add_argument("--output", type=Path, required=True, help="new dataset directory; never overwrite")
    capture.add_argument("--json", action="store_true", help="print one final JSON receipt to stdout")
    yolop = commands.add_parser("yolop-bench", help="bounded RGB/native-depth/YOLOP diagnostics; not capture v1")
    yolop.add_argument("--profile", type=Path, required=True, help="explicit no-JPEG YOLOP bench JSON")
    yolop.add_argument("--duration-s", type=_duration, default=30.0, help="bench duration after startup (0.1..300)")
    yolop.add_argument("--output", type=Path, required=True, help="new diagnostic directory; never overwrite")
    yolop.add_argument("--json", action="store_true", help="print one final bench receipt")
    replay = commands.add_parser("replay", help="validate and replay recorded frames without a camera")
    replay.add_argument("--dataset", type=Path, required=True, help="version1 capture directory")
    replay.add_argument("--output", type=Path, required=True, help="new diagnostic trace directory")
    replay.add_argument("--profile", type=Path, help="optionally require the exact embedded capture profile")
    replay.add_argument("--speed", type=_speed, default=1.0,
                        help="original receive-time rate, default: 1; 0 means no waiting (0..16)")
    replay.add_argument("--allow-incomplete", action="store_true",
                        help="explicitly allow verified indexed frames from an incomplete capture")
    replay.add_argument("--json", action="store_true", help="print one final JSON replay report")
    yolop_replay = commands.add_parser("yolop-replay", help="validate/replay sparse YOLOP diagnostic samples offline")
    yolop_replay.add_argument("--dataset", type=Path, required=True, help="S02 YOLOP diagnostic directory; not capture v1")
    yolop_replay.add_argument("--output", type=Path, required=True, help="new metadata/hash trace directory")
    yolop_replay.add_argument("--speed", type=_speed, default=1.0, help="original bundle availability rate; 0 means no waiting")
    yolop_replay.add_argument("--allow-incomplete", action="store_true", help="allow an intact indexed prefix; never skip corrupt indexed samples")
    yolop_replay.add_argument("--json", action="store_true", help="print one final diagnostic replay receipt")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor_command(args)
    if args.command == "capture":
        return _capture_command(args)
    if args.command == "yolop-bench":
        return _yolop_bench_command(args)
    if args.command == "replay":
        return _replay_command(args)
    if args.command == "yolop-replay":
        return _yolop_replay_command(args)
    parser.error("unsupported command")
