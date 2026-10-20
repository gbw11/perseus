#!/usr/bin/env python3
"""템플릿 카탈로그를 검증해 보관 입력을 출력한다. 기본 VM의 수와 독립적이며 복사·조립은 하지 않는다."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'platform/first-install'))
spec = importlib.util.spec_from_file_location('xenova_template_assembly', ROOT / 'platform/first-install/assemble.py')
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def resolve(release_root, catalog):
    root = Path(release_root)
    if not root.is_absolute() or root.resolve() != root or root.is_symlink():
        raise ValueError('canonical release root required')
    from xenova_catalog import DEFAULT_IDS
    entries = catalog.get('templates', [])
    if (catalog.get('schema') != 1 or len(entries) != 3
            or {e.get('template_id') for e in entries} != set(DEFAULT_IDS.values())):
        raise ValueError('one current default per template ID required; no duplicate catalog')
    result = {'linux_templates': [], 'rtos_templates': []}
    for entry in entries:
        relative = Path(entry['directory'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('template path escapes release root')
        if DEFAULT_IDS.get(entry.get('id')) != entry['template_id']:
            raise ValueError('normalized default template identity mismatch')
        kind = 'zephyr' if entry['template_id'] == 'zephyr-xenvm-starter-arm64' else 'linux'
        if entry.get('guest_kind') != kind:
            raise ValueError('default template kind mismatch')
        directory = root / relative
        if directory.resolve() != directory or directory.is_symlink():
            raise ValueError('template path contains links')
        if kind == 'linux':
            assembly.template_info(directory, entry['id'], entry['manifest_sha256'])
            meta = json.loads((directory / 'template.json').read_text())
            from xenova_catalog import deployment_version
            if meta.get('platform') != 'xenova' or deployment_version(meta) != entry['version']:
                raise ValueError('default Linux template must use the selected Xenova preparation/version')
            result['linux_templates'].append({'template_id': entry['template_id'], 'template_version': entry['version'],
                                              'template_dir': str(directory),
                                              'template_manifest_sha256': entry['manifest_sha256']})
        else:
            info = assembly.rtos.info(directory, entry['manifest_sha256'])
            if info['id'] != entry['id'] or info['metadata'].get('template_version',info['metadata'].get('version')) != entry['version']:
                raise ValueError('default Starter identity/version mismatch')
            result['rtos_templates'].append({'template_dir': str(directory),
                                             'template_manifest_sha256': entry['manifest_sha256']})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root', required=True, type=Path)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'platform/guests/default-templates.lock.json')
    args = parser.parse_args(argv)
    print(json.dumps(resolve(args.release_root, json.loads(args.catalog.read_text())), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc))
