"""가벼운 CLI 분기. 개별 명령의 의존성은 해당 명령에서 가져온다."""

import argparse
import json
import math

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


def main(argv: list[str] | None = None) -> int:
    """CLI를 실행하고 종료 코드를 반환한다. argparse 사용 오류는 2다."""
    parser = argparse.ArgumentParser(prog="xenova-vehicle")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="inspect the target environment and enumerate OAK devices")
    doctor.add_argument("--json", action="store_true", help="print one JSON report to stdout")
    doctor.add_argument("--timeout", type=_timeout, default=10.0,
                        help="total native probe budget in seconds, default: 10 (0.1..30)")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor_command(args)
    parser.error("unsupported command")
