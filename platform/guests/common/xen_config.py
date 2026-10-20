"""Render Linux xl templates from resolved instance metadata.

No registry, disk writes, resource allocation or Xen calls. This is an internal
platform helper, not the Spring/FastAPI request validator.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
from string import Template
import sys


MODULE_DIR = Path(__file__).resolve().parent
TEMPLATE_ROOT = (MODULE_DIR.parent if MODULE_DIR.name == 'common'
                 and MODULE_DIR.parent.name == 'guests' else MODULE_DIR / 'xen-templates')
PUBLIC_OS = {'ubuntu-24.04-arm64': 'ubuntu', 'debian-13-arm64': 'debian'}
SOURCE_OS = {'ubuntu-24.04-20260926': 'ubuntu', 'debian-13-20261015': 'debian'}
ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
MAC = re.compile(r'02(?::[0-9a-f]{2}){5}\Z')
PATH_PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')


class ConfigError(ValueError):
    """Instance metadata or the installed cfg template is invalid."""


def _integer(value, field, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(f'{field} must be an integer in {low}..{high}')
    return value


def _os(record):
    source_id = record.get('template')
    if not isinstance(source_id, str):
        raise ConfigError('source template must be a string')
    source = SOURCE_OS.get(source_id)
    public = record.get('template_id')
    if public is None:
        if source is None:
            raise ConfigError('unknown Linux source; explicit supported template_id required')
        # Do not invent a deployment ID/version for legacy instances.
        return source
    if not isinstance(public, str) or public not in PUBLIC_OS:
        raise ConfigError('unsupported Linux template_id')
    selected = PUBLIC_OS[public]
    if source is not None and selected != source:
        raise ConfigError('template_id and source template disagree on Linux OS')
    return selected


def render_config(record, *, instance_root):
    """Return cfg using the existing id/name/uuid and per-instance boot paths.

    The caller resolves catalog/version and checks actual boot assets. Resource
    bounds preserve the internal platform's 128..8192 MiB range, independently
    of catalog selection. No file existence is asserted here.
    """
    if not isinstance(record, dict):
        raise ConfigError('instance must be an object')
    if record.get('guest_kind', 'linux') != 'linux':
        raise ConfigError('Linux cfg renderer cannot render firmware guests')
    for field in ('id', 'name'):
        if not isinstance(record.get(field), str) or not ID.fullmatch(record[field]):
            raise ConfigError(f'invalid instance {field}')
    identity = record.get('uuid')
    if (not isinstance(identity, str)
            or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', identity)):
        raise ConfigError('uuid must be a canonical lowercase UUID')
    if not isinstance(record.get('mac'), str) or not MAC.fullmatch(record['mac']):
        raise ConfigError('mac must match the platform 02:xx:xx:xx:xx:xx format')
    root = str(instance_root)
    path = PurePosixPath(root)
    if (not path.is_absolute() or str(path) != root or root == '/'
            or any(not PATH_PART.fullmatch(part) for part in path.parts[1:])):
        raise ConfigError('instance_root must be a canonical safe dom0 POSIX path')
    base = f"{root}/{record['id']}"
    values = {
        'xen_name': record['name'], 'vm_id': identity,
        'vcpus': _integer(record.get('vcpus'), 'vcpus', 1, 4),
        'memory_mib': _integer(record.get('memory_mib'), 'memory_mib', 128, 8192),
        'kernel_path': f'{base}/Image', 'initrd_path': f'{base}/initrd',
        'disk_path': f'{base}/root.raw', 'mac': record['mac'], 'bridge_name': 'xenbr0',
    }
    try:
        source = (TEMPLATE_ROOT / _os(record) / 'xen.cfg.in').read_text(encoding='utf-8')
        return Template(source).substitute(values)
    except (OSError, KeyError, ValueError) as exc:
        raise ConfigError(f'cannot render Linux cfg: {exc}') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description='Print cfg from resolved metadata; never start a VM')
    parser.add_argument('spec', type=Path, help='JSON with instance and instance_root (not an HTTP request)')
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.spec.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'instance', 'instance_root'}:
            raise ConfigError('input must contain exactly instance and instance_root')
        rendered = render_config(data['instance'], instance_root=data['instance_root'])
    except (OSError, ValueError) as exc:
        print(f'ConfigError: {exc}', file=sys.stderr)
        return 2
    sys.stdout.write(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
