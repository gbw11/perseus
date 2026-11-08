"""가벼운 CLI 분기. 개별 명령의 의존성은 해당 명령에서 가져온다."""

import argparse
import json
import math
import os
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


def _http_host(value: str) -> str:
    import ipaddress
    try:
        if str(ipaddress.IPv4Address(value)) != value:
            raise ValueError('noncanonical IPv4')
    except ValueError as error:
        raise argparse.ArgumentTypeError('HTTP host must be a numeric IPv4 address') from error
    return value


def _http_port(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise argparse.ArgumentTypeError('HTTP port must be in1..65535')
    return int(value)


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


def _runtime_init_command(args: argparse.Namespace) -> int:
    from .runtime.runtime_bootstrap import initialize_runtime_state

    try:
        report = initialize_runtime_state(args.state_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"runtime-init: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, allow_nan=False) if args.json
          else f"Initialized protected runtime state: {args.state_dir}")
    return 0


def _perception_bench_command(args: argparse.Namespace) -> int:
    from .perception.bench import run_perception_bench

    try:
        report=run_perception_bench(args.output,source=args.source,dataset=args.dataset,
            calibration_path=args.calibration,frames=args.frames,oracle_pose=args.oracle_pose,
            allow_incomplete=args.allow_incomplete)
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'perception-bench: {exc}',file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report,ensure_ascii=True,allow_nan=False))
    else:
        print(f"Perception diagnostics {'completed' if report['complete'] else 'incomplete'}: {report['output']}")
        print('Diagnostic results only; metric/world/control authority remains false')
    return report['exit_code']


def _runtime_command(args: argparse.Namespace) -> int:
    from .yolop_profile import load_yolop_profile
    from .runtime.resident_runtime import run_runtime
    from .runtime.resident_storage import StorageProfile

    try:
        if args.http_port is not None and args.http_host is None:
            raise ValueError('--http-port requires explicit --http-host')
        http_address=None if args.http_host is None else (args.http_host,args.http_port or 8080)
        profile = load_yolop_profile(args.profile)
        storage = StorageProfile(str(args.database.expanduser().absolute()), args.database_open_policy)
        service_scope = None
        if args.service_scope is not None:
            from .runtime.service_scope import SystemdServiceScope
            service_scope = SystemdServiceScope()
        perception_bytes=None
        if args.perception_profile is not None:
            with args.perception_profile.open('rb') as source:
                perception_bytes=source.read(65537)
            if not 1<=len(perception_bytes)<=65536:
                raise ValueError('perception calibration must fit in 64KiB')
        jio_profile=None
        if args.jio_profile is not None:
            from .transport.jio_profile import JetsonJioProfile
            jio_profile=JetsonJioProfile.load(args.jio_profile)
        mapper_bytes=None
        if args.mapper_profile is not None:
            with args.mapper_profile.open('rb') as source:mapper_bytes=source.read(65537)
            if not 1<=len(mapper_bytes)<=65536:raise ValueError('mapper configuration must fit in64KiB')
        report = run_runtime(profile, args.state_dir, storage, args.output or args.output_root,
            duration_s=args.duration_s, service_scope=service_scope, output_root=args.output_root is not None,
            perception_profile_bytes=perception_bytes,jio_profile=jio_profile,mapper_profile_bytes=mapper_bytes,
            http_address=http_address)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"runtime: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    else:
        print(f"Resident runtime {'completed' if report['complete'] else 'incomplete'}: {report['reason']}")
        print(f"Capture: {report['capture_ready']}; parser: {report['nn_ready']}; storage: {report['storage_ready']}")
        print(f"Metric/world: waiting; output: {args.output}")
        if report.get('error'):
            print(f"Error: {report['error']}")
    if report.get('requires_service_reap'):
        # 미회수 owner를 CPython의 암묵 join으로 계속 기다리지 않고 외부 service manager에 넘긴다.
        # resource 기록은 DIRTY로 남기고 다음 guard가 미종료·잔존 이름을 다시 확인한다.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(report['exit_code'])
    return report['exit_code']


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
    runtime_init = commands.add_parser("runtime-init", help="explicitly initialize a new protected runtime state directory")
    runtime_init.add_argument("--state-dir", type=Path, required=True, help="new directory; existing state is never overwritten")
    runtime_init.add_argument("--json", action="store_true", help="print the installation record")
    runtime = commands.add_parser("runtime", help="run resident OAK acquisition, parser and storage; metric/world wait for external inputs")
    runtime.add_argument("--profile", type=Path, required=True, help="explicit REQUIRED YOLOP graph/asset profile")
    runtime.add_argument("--state-dir", type=Path, required=True, help="protected state created by runtime-init")
    runtime.add_argument("--database", type=Path, required=True, help="explicit SQLite path owned by D")
    runtime.add_argument("--database-open-policy", choices=("EXISTING_ONLY", "CREATE_IF_ABSENT"), default="EXISTING_ONLY",
                         help="existing database by default; explicit first creation is opt-in")
    runtime_output = runtime.add_mutually_exclusive_group(required=True)
    runtime_output.add_argument("--output", type=Path, help="new runtime diagnostic directory")
    runtime_output.add_argument("--output-root", type=Path, help="existing managed root; create one private directory per service UUID")
    runtime.add_argument("--service-scope", choices=("systemd",), help="require actual fixed systemd service/slice execution and restart guard")
    runtime.add_argument("--duration-s", type=_duration, default=None, help="optional 0.1..300 second residence after local readiness; default until stop")
    runtime.add_argument("--json", action="store_true", help="print one final normal-flow receipt")
    runtime.add_argument('--perception-profile',type=Path,help='explicit physical/provisional calibration for owned-copy local-map processing')
    runtime.add_argument('--jio-profile',type=Path,help='optional owner-only MQTT/source policy profile for original Pi inputs and world publication')
    runtime.add_argument('--mapper-profile',type=Path,help='optional explicit local mapper schema1 for capture timing, sensors, geometry and stopping bounds')
    runtime.add_argument('--http-host',type=_http_host,help='opt-in numeric IPv4 listen address for read-only OAK JPEG; default off')
    runtime.add_argument('--http-port',type=_http_port,default=None,help='HTTP port1..65535; requires --http-host, default8080')
    perception=commands.add_parser('perception-bench',help='offline sensor-to-local-map diagnostics; no control authority')
    perception.add_argument('--source',choices=('synthetic','yolop-replay'),default='synthetic',help='explicit sensor provenance')
    perception.add_argument('--dataset',type=Path,help='existing paired synthetic or validated YOLOP diagnostic recording')
    perception.add_argument('--calibration',type=Path,help='external calibration bundle; required for physical replay')
    perception.add_argument('--output',type=Path,required=True,help='new diagnostic directory; never overwrite')
    perception.add_argument('--frames',type=int,default=4,help='bounded frames to generate/process, 1..256')
    perception.add_argument('--oracle-pose',action='store_true',help='explicit synthetic evaluation oracle; never physical odom acceptance')
    perception.add_argument('--allow-incomplete',action='store_true',help='allow verified indexed prefix; never corrupted indexed payload')
    perception.add_argument('--json',action='store_true',help='print one final diagnostic report')
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
    if args.command == "runtime-init":
        return _runtime_init_command(args)
    if args.command == "runtime":
        return _runtime_command(args)
    if args.command == 'perception-bench':
        return _perception_bench_command(args)
    parser.error("unsupported command")
