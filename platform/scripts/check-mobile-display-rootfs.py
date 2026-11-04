#!/usr/bin/env python3
"""Read-only, offline check of a fresh image's mobile display artifacts.

No sockets, subprocesses, environment loading, service changes or credentials.
This is an artifact check, not USB/runtime/phone readiness or BitBake evaluation.
"""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re


REFERENCES = re.compile(r'''["'`]((?:\./|/?assets/)[A-Za-z0-9_./-]+\.(?:js|css|png|jpe?g|webp|svg|woff2?|ttf))["'`]''')


def inside(root, relative):
    path = (root / relative.lstrip('/')).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Artifact reference escapes the supplied root')
    return path


def unit(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Regular bounded vendor unit required')
    result, section = {}, ''
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        elif '=' in line:
            key, value = line.split('=', 1)
            result.setdefault((section, key), []).append(value)
    return result


def values(fields, section, key):
    return ' '.join(fields.get((section, key), []))


def web_assets(web):
    class Entries(HTMLParser):
        def __init__(self):
            super().__init__()
            self.paths = []
            self.scripts = 0

        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            if tag == 'script' and attrs.get('src'):
                self.paths.append(attrs['src'])
                self.scripts += 1
            if tag == 'link' and attrs.get('rel') == 'stylesheet' and attrs.get('href'):
                self.paths.append(attrs['href'])

    parser = Entries()
    index = inside(web, 'index.html')
    if index.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Compiled HTML outside bounds')
    parser.feed(index.read_text())
    if not parser.scripts or not parser.paths:
        raise ValueError('No compiled script entry')
    pending = []
    for name in parser.paths:
        if not re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(?:js|css)', name):
            raise ValueError('Unexpected compiled entry URL')
        pending.append(inside(web, name))
    visited, mobile = set(), False
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024 or len(visited) >= 256:
            raise ValueError('Compiled asset missing or outside bounds')
        visited.add(path)
        if path.suffix not in ('.js', '.css'):
            continue
        text = path.read_text()
        mobile |= path.suffix == '.js' and '/display/mobile' in text
        for reference in REFERENCES.findall(text):
            target = inside(web, reference) if reference.startswith(('/', 'assets/')) else (path.parent / reference).resolve()
            if not target.is_relative_to(web):
                raise ValueError('Compiled dependency escapes web root')
            pending.append(target)
    return {'mobile_route_present': mobile, 'referenced_asset_count': len(visited)}


def inspect(root):
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Image root directory required')
    checks, detail = {}, {}
    report = {'schema_version': 1, 'read_only': True, 'checks': checks,
              'physical_phone_verified': False, 'usb_verified': False,
              'runtime_ready': False}
    try:
        directories = {inside(root, name) for name in ('usr/lib/systemd/system', 'lib/systemd/system')
                       if inside(root, name).is_dir()}
        selected = [p for p in directories if (p / 'xenova-display-feed.service').is_file()]
        if len(selected) != 1:
            raise ValueError('Expected one vendor system unit directory')
        units = selected[0]
        feed = unit(units / 'xenova-display-feed.service')
        access = unit(units / 'xenova-display-mobile.service')
        hdmi = unit(units / 'xenova-display-hdmi.service')
        command = values(feed, 'Service', 'ExecStart')
        checks['single_loopback_feed'] = 'uvicorn xenova_display.main:create_app' in command and all(
            re.search(r'(?:^|\s)' + re.escape(flag) + r'\s+' + re.escape(value) + r'(?:\s|$)', command)
            for flag, value in (('--host', '127.0.0.1'), ('--port', '8082'), ('--workers', '1')))
        checks['feed_independent_of_hdmi'] = 'xenova-display.target' not in values(feed, 'Unit', 'PartOf').split()
        checks['shared_private_environment'] = (values(feed, 'Service', 'EnvironmentFile')
            == values(access, 'Service', 'EnvironmentFile') == '/etc/xenova/display.env')
        checks['installed_web_root_matches_feed'] = 'XENOVA_DISPLAY_WEB_ROOT=/usr/share/xenova/display/web' in values(feed, 'Service', 'Environment').split()
        checks['access_entry_installed'] = values(access, 'Service', 'ExecStart') == '/usr/bin/python3 -m xenova_display.mobile_access --config /etc/xenova/display-mobile.json'
        dependencies = ' '.join(values(access, 'Unit', key) for key in ('Wants', 'Requires', 'After', 'BindsTo', 'PartOf'))
        checks['access_independent_of_graphics'] = ('xenova-display-feed.service' in dependencies.split()
            and not any(word in dependencies for word in ('hdmi', 'graphics', 'kiosk', 'stream', 'display.target')))
        checks['hdmi_opt_in_guard'] = (values(unit(units / 'xenova-display-hdmi.service.d/50-mobile-first.conf'), 'Unit', 'ConditionPathExists')
            == '!/etc/xenova/display-mobile-first' and 'native_session' in values(hdmi, 'Service', 'ExecStart'))
        checks['phone_boot_not_enabled'] = not any(
            list(folder.glob('*.wants/xenova-display-mobile.service')) for folder in
            [units, inside(root, 'etc/systemd/system')])
    except (OSError, ValueError, UnicodeError):
        checks['native_unit_bundle_available'] = False
    modules = []
    for base in ('usr/lib', 'usr/lib64'):
        modules.extend(inside(root, base).glob('python*/site-packages/xenova_display'))
    modules = sorted(set(modules))
    checks['one_installed_python_package'] = len(modules) == 1
    checks['mobile_python_modules_installed'] = len(modules) == 1 and all(
        inside(root, str((modules[0] / name).relative_to(root))).is_file()
        for name in ('main.py', 'mobile_access.py', 'mobile_network.py', 'mobile_upstream.py', 'mobile_preflight.py'))
    checks['operator_guides_installed'] = all(inside(root, name).is_file() for name in (
        'usr/share/xenova/display/mobile/mobile-first.README.md',
        'usr/share/xenova/display/mobile/mobile-deployment.README.md',
        'usr/share/xenova/display/mobile-access/mobile-access.README.md'))
    checks['private_config_not_seeded'] = not any(inside(root, name).exists() for name in (
        'etc/xenova/display.env', 'etc/xenova/display-bindings.json',
        'etc/xenova/display-mobile.json', 'etc/xenova/display-mobile-first'))
    try:
        detail.update(web_assets(inside(root, 'usr/share/xenova/display/web')))
        checks['compiled_mobile_assets_complete'] = detail['mobile_route_present']
    except (OSError, ValueError, UnicodeError):
        checks['compiled_mobile_assets_complete'] = False
    report.update({'assets': detail, 'artifact_layout_valid': bool(checks) and all(checks.values()),
                   'next_step': 'BitBake/package QA, private runtime configuration and actual USB/phone acceptance remain required'})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Fresh extracted image rootfs, not a live deployment')
    args = parser.parse_args()
    try:
        report = inspect(args.root)
    except (OSError, ValueError):
        report = {'read_only': True, 'artifact_layout_valid': False, 'error': 'Image root unavailable or unsafe'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['artifact_layout_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
