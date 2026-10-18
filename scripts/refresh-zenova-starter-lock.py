#!/usr/bin/env python3
"""명시한 새 앱 버전과 실제 Starter 소스 해시로 수정 가능한 lock만 갱신한다. binary는 건드리지 않는다."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('zenova_starter_source_tools', ROOT / 'scripts/build-zenova-starter.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


def updated_lock(app, version):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', version):
        raise ValueError('explicit application SemVer required')
    old = json.loads((app.parent / 'starter.lock.json').read_text())
    files = builder.fingerprint(app)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if files != old['app_files'] and version == old['version']:
        raise ValueError('changed Starter source requires a new application version')
    return {**old, 'id': 'zenova-zephyr-starter-' + version, 'version': version,
            'app_files': files, 'app_source_sha256': digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    app = ROOT / 'platform/guests/zephyr/starter-app'
    meta = updated_lock(app, args.version)
    print(json.dumps({'writes_source_lock': args.execute, 'metadata': meta}, indent=2))
    if args.execute:
        target = app.parent / 'starter.lock.json'
        stage = target.with_suffix('.json.tmp')
        stage.write_text(json.dumps(meta, indent=2) + '\n')
        stage.replace(target)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
