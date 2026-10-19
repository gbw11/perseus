#!/usr/bin/env python3
"""완료 플랫폼 이미지 게시. 기본은 계획만, --execute는 기존 이미지의 독립 사본만 생성한다."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform/first-install'))
from completed_artifacts import main

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('[perseus-publish] ERROR:', error, file=sys.stderr)
        sys.exit(1)
