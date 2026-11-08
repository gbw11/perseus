"""NN 없는 offline tape/road 진단 진입점. 운영 REQUIRED gate를 변경하지 않는다."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--original-case-directory', help='원 대표01 P01 input/packed/receipt 디렉터리')
    args = parser.parse_args()
    from xenova_vehicle.simulation.profile import DevelopmentProfile
    from xenova_vehicle.simulation.scene import Scene
    from xenova_vehicle.simulation.road_probe import run_road_probe
    profile = DevelopmentProfile.from_bytes(Path(args.profile).read_bytes())
    scene = Scene(Path(args.scene).read_bytes())
    result = run_road_probe(profile, scene, args.output_root,
        original_case_directory=args.original_case_directory)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
