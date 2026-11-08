"""실제 OAK를 여는 opt-in pixel probe CLI. 별도 supervisor의 원 실행 기한이 필요하다."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Actual OAK SIM passthrough pixel golden; diagnostic variant only')
    parser.add_argument('--yolop-profile', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--input-format', choices=('NV12', 'BGR888p'), default='NV12')
    args = parser.parse_args()
    from xenova_vehicle.yolop_profile import load_yolop_profile
    from xenova_vehicle.simulation.preprocessing_probe import run_preprocessing_probe
    directory, report = run_preprocessing_probe(load_yolop_profile(args.yolop_profile), args.output_root,
        input_format=args.input_format)
    print(json.dumps(dict(output=str(directory), status=report['status'], exit_code=report['exit_code']), sort_keys=True))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
