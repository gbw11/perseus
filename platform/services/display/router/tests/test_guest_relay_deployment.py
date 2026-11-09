"""relay 정규 recipe와 구형 이미지용 사본이 활성 비밀/다른 display 코드를 건드리지 않는지 확인한다."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from test_mobile_deployment_bundle import stage_image, ROOT, ROUTER

SPEC = importlib.util.spec_from_file_location('relay_package', ROOT / 'platform/scripts/package-guest-display-relay.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_regular_recipe_installs_canonical_enabled_relay_without_active_configuration(tmp_path):
    image = stage_image(tmp_path)
    unit = image / 'usr/lib/systemd/system' / builder.UNIT
    assert unit.read_bytes() == (builder.DEPLOY / builder.UNIT).read_bytes()
    assert unit.stat().st_mode & 0o777 == 0o644
    text = unit.read_text()
    assert 'Requires=xenova-bridge.service' in text
    assert 'After=xenova-bridge.service xenova-display-feed.service' in text
    assert '[Install]\nWantedBy=multi-user.target' in text
    recipe = (ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-display/xenova-display_0.1.0.bb').read_text()
    assert builder.UNIT in recipe.split('SYSTEMD_SERVICE:${PN} = ')[1].split('\n')[0]
    assert 'SYSTEMD_AUTO_ENABLE:${PN} = "enable"' in recipe
    assert (image / 'usr/lib/python3.14/site-packages/xenova_display/guest_frames.py').is_file()
    assert (image / 'usr/share/xenova/display/examples/guest-display-data-only.json.example').is_file()
    assert not (image / 'etc/xenova').exists()


def test_standalone_package_has_only_relay_closure_and_service_scoped_import(tmp_path):
    output = tmp_path / 'bundle'
    result = builder.package(output)
    assert len(result['files']) == 14
    root = output / 'rootfs'
    with tarfile.open(output / 'payload.tar.gz') as archive:
        assert {x.name for x in archive} == {x['path'] for x in result['files']}
        for item in result['files']:
            payload = (root / item['path']).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == item['sha256']
            entry = archive.getmember(item['path'])
            assert entry.isfile() and entry.mode == 0o644 and entry.uid == entry.gid == 0
            assert archive.extractfile(entry).read() == payload
    for item in result['files']:
        assert not item['path'].startswith(('etc/xenova/', 'usr/lib/python'))
    dropin = root / f'etc/systemd/system/{builder.UNIT}.d/10-standalone-runtime.conf'
    assert dropin.read_text() == f'[Service]\nEnvironment=PYTHONPATH={result["runtime"]}\n'
    # No token, service setup or project import is needed to build the payload.
    config = json.loads((root / 'usr/share/xenova/display/examples/guest-display-data-only.json.example').read_text())
    assert config['guest'] is None and config['guest_data_token_file'].endswith('.token')
    with pytest.raises(FileExistsError):
        builder.package(output)


def test_missing_relative_import_is_rejected_before_output_is_created(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / '__init__.py').write_text('from .missing import fake\n')
    monkeypatch.setattr(builder, 'ROUTER', tmp_path)
    (tmp_path / 'xenova_display').symlink_to(source, target_is_directory=True)
    monkeypatch.setattr(builder, 'MODULES', ('__init__',))
    with pytest.raises(ValueError, match='closure is incomplete'):
        builder.package(tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


def test_installed_standalone_entrypoint_imports_outside_development_tree(tmp_path):
    result = builder.package(tmp_path / 'bundle')
    runtime = tmp_path / 'bundle/rootfs' / result['runtime'].lstrip('/')
    # Reuse only externally supplied dependency paths, never the source package.
    environment = {**os.environ, 'PYTHONPATH': str(runtime) + os.pathsep + os.environ.get('PYTHONPATH', ''),
                   'PYTHONDONTWRITEBYTECODE': '1'}
    output = subprocess.run([sys.executable, '-c',
        'import xenova_display.guest_frames as relay; import xenova_display.frame_presenter; '
        'print(relay.__file__)'], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=30, check=True)
    assert str(runtime) in output.stdout and str(ROUTER) not in output.stdout


def test_relay_unit_requires_bridge_and_can_load_in_isolated_systemd_graph(tmp_path):
    executable = shutil.which('systemd-analyze')
    if not executable:
        pytest.skip('systemd-analyze unavailable')
    image = stage_image(tmp_path)
    units = image / 'usr/lib/systemd/system'
    bridge = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-bringup/files/xenova-bridge.service'
    (units / 'xenova-bridge.service').write_text(bridge.read_text().replace('@SBINDIR@', '/usr/sbin'))
    for name in ['sysinit.target', 'basic.target', 'shutdown.target', 'network.target', 'multi-user.target',
                 'systemd-udev-settle.service']:
        (units / name).write_text('[Unit]\nDescription=Isolated dependency\n')
    for name in ['usr/bin/python3', 'usr/sbin/xenova-bridge-up']:
        command = image / name
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text('#!/bin/sh\nexit 0\n')
        command.chmod(0o755)
    args = [executable, 'verify', '--root=' + str(image), builder.UNIT]
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    (units / 'xenova-bridge.service').unlink()
    missing = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert missing.returncode != 0 and 'xenova-bridge.service' in missing.stderr
