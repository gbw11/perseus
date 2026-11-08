"""명시적 profile/OAK만 여는 S23 대표 장면 진단 CLI. 운영 boot에서는 실행하지 않는다."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Actual host-fed OAK YOLOP / SYNTHETIC frame-local diagnostics')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--yolop-profile', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--warmup-frames', type=int, choices=(0, 1, 2), default=0)
    args = parser.parse_args()
    from xenova_vehicle.simulation.profile import DevelopmentProfile
    from xenova_vehicle.simulation.scene import Scene
    from xenova_vehicle.simulation.representative import run_representative
    from xenova_vehicle.yolop_profile import load_yolop_profile
    with args.profile.open('rb') as stream:
        profile = DevelopmentProfile.from_bytes(stream.read(16_385))
    with args.scene.open('rb') as stream:
        scene = Scene(stream.read(65_537))
    directory, report = run_representative(profile, scene, load_yolop_profile(args.yolop_profile),
        args.output_root, warmup_frames=args.warmup_frames)
    print(json.dumps(dict(output=str(directory), status=report['status'], exit_code=report['exit_code']), sort_keys=True))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
