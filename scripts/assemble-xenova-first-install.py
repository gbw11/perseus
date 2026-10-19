#!/usr/bin/env python3
"""공통 SD 설치 이미지의 계획·명시적 조립·읽기 전용 결과 검증 진입점."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform/first-install'))
from assemble import main

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as error:
        print('[xenova-assemble] ERROR:', error, file=sys.stderr)
        sys.exit(1)
