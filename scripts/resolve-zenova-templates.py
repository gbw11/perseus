#!/usr/bin/env python3
"""현재 기본 카탈로그 3개만 검증해 조립 config의 guest 참조를 출력한다. 복사·조립은 하지 않는다."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/first-install'))
spec = importlib.util.spec_from_file_location('zenova_template_assembly', ROOT / 'platform/first-install/assemble.py')
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def resolve(release_root, catalog):
    root = Path(release_root)
    if not root.is_absolute() or root.resolve() != root or root.is_symlink():
        raise ValueError('canonical release root required')
    entries = catalog.get('templates', [])
    if catalog.get('schema') != 1 or len(entries) != 3 or {e['vm_id'] for e in entries} != {'ubuntu-dev', 'debian-dev', 'zephyr-dev'}:
        raise ValueError('one current default per VM required; no legacy compatibility catalog')
    defaults = json.loads((assembly.FILES / 'defaults.json').read_text())
    selected = {e['id']: e['template'] for e in defaults['instances'] + defaults['firmware_instances']}
    result = {'guests': [], 'rtos_templates': []}
    for entry in entries:
        relative = Path(entry['directory'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('template path escapes release root')
        if entry.get('id') != selected[entry['vm_id']] or entry.get('guest_kind') != ('zephyr' if entry['vm_id']=='zephyr-dev' else 'linux'):
            raise ValueError('default template/VM pairing differs from deployment defaults')
        directory = root / relative
        if directory.resolve() != directory or directory.is_symlink():
            raise ValueError('template path contains links')
        if entry['guest_kind'] == 'linux':
            info = assembly.template_info(directory, entry['id'], entry['manifest_sha256'])
            meta = json.loads((directory / 'template.json').read_text())
            if meta.get('platform') != 'zenova' or meta.get('version') != entry['version']:
                raise ValueError('default Linux template must use the selected Zenova preparation/version')
            result['guests'].append({'id': entry['vm_id'], 'template_dir': str(directory),
                                    'template_manifest_sha256': entry['manifest_sha256']})
        elif entry['guest_kind'] == 'zephyr':
            info = assembly.rtos.info(directory, entry['manifest_sha256'])
            if info['id'] != entry['id'] or info['metadata']['version'] != entry['version']:
                raise ValueError('default Starter identity/version mismatch')
            result['rtos_templates'].append({'template_dir': str(directory),
                                            'template_manifest_sha256': entry['manifest_sha256']})
        else:
            raise ValueError('unsupported default template kind')
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
