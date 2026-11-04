"""Combined native recipe/349/351 installation, without BitBake or host services."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[5]
RECIPE = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-display'
ROUTER = ROOT / 'platform/services/display/router'
CHECKER = ROOT / 'platform/scripts/check-mobile-display-rootfs.py'
spec = importlib.util.spec_from_file_location('mobile_bundle_check', CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux image installation semantics')


def stage_image(folder, built_web=None):
    stage, unpack = folder / 'root', folder / 'unpack'
    unpack.mkdir(parents=True)
    web_input = folder / 'web-input'
    web = web_input / 'display-web'
    if built_web:
        shutil.copytree(built_web, web)
    else:
        (web / 'assets').mkdir(parents=True)
        (web / 'index.html').write_text('<div id="root"></div><script type="module" src="/assets/index-test.js"></script>')
        (web / 'assets/index-test.js').write_text('const route="/display/mobile";import("./MobilePage-test.js");')
        (web / 'assets/MobilePage-test.js').write_text('export const fixture="synthetic built asset";')
    definitions = [RECIPE / 'xenova-display_0.1.0.bb', RECIPE / 'xenova-display_0.1.0.bbappend', RECIPE / 'xenova-display_%.bbappend']
    texts = [p.read_text() for p in definitions]
    search = [ROUTER, ROUTER / 'deploy', ROUTER / 'deploy/native', ROUTER / 'examples', web_input, RECIPE / 'files']
    for subdir in ('mobile', 'mobile-access'):
        search.insert(0, ROUTER / 'deploy' / subdir)
    for name in dict.fromkeys(re.findall(r'file://([^"\s]+)', '\n'.join(texts))):
        candidates = [p / name for p in search if (p / name).exists()]
        assert candidates, f'Missing SRC_URI artifact {name}'
        source = candidates[0]
        if source.is_dir():
            shutil.copytree(source, unpack / name)
        else:
            shutil.copyfile(source, unpack / name)
    commands = []
    for text in texts:
        match = re.search(r'do_install(?::append)?\(\) \{(.*?)\n\}', text, re.S)
        assert match
        commands.append(match.group(1))
    tools = folder / 'tools'
    tools.mkdir()
    # The recipe's only privileged operation is package-account chown. Do not
    # change host users/ownership; skip only that operation inside this test root.
    chown = tools / 'chown'
    chown.write_text('#!/bin/sh\ncase "$2" in "$D"/*) exit 0 ;; *) exit 2 ;; esac\n')
    chown.chmod(0o755)
    environment = {**os.environ, 'D': str(stage), 'S': str(unpack),
        'PYTHON_SITEPACKAGES_DIR': '/usr/lib/python3.14/site-packages',
        'systemd_system_unitdir': '/usr/lib/systemd/system', 'nonarch_libdir': '/usr/lib',
        'sysconfdir': '/etc', 'datadir': '/usr/share',
        'PATH': str(tools) + os.pathsep + os.environ['PATH']}
    command = 'bbfatal() { exit 2; }\n' + '\n'.join(commands)
    subprocess.run(['sh', '-eu', '-c', command], check=True, env=environment, capture_output=True, text=True, timeout=15)
    return stage


@pytest.fixture
def image(tmp_path):
    return stage_image(tmp_path)


def test_combined_install_exposes_mobile_without_changing_boot_or_private_configuration(image):
    result = checker.inspect(image)
    assert result['artifact_layout_valid'], result
    assert not result['runtime_ready'] and not result['physical_phone_verified'] and not result['usb_verified']
    assert result['assets']['referenced_asset_count'] == 2
    assert not (image / 'etc/xenova/display-mobile-first').exists()
    assert (image / 'usr/share/xenova/display/examples/qemu-bindings.json').is_file()
    assert (image / 'usr/lib/systemd/user/xenova-display-stream.service').is_file()


@pytest.mark.parametrize('broken', ['missing-lazy-chunk', 'missing-backtick-chunk', 'old-bundle', 'external-symlink', 'escaping-reference'])
def test_index_html_alone_does_not_prove_a_usable_mobile_bundle(image, tmp_path, broken):
    web = image / 'usr/share/xenova/display/web'
    chunk = web / 'assets/MobilePage-test.js'
    if broken == 'missing-lazy-chunk':
        chunk.unlink()
    elif broken == 'missing-backtick-chunk':
        (web / 'assets/index-test.js').write_text('const route="/display/mobile";import(`./MobilePage-test.js`);')
        chunk.unlink()
    elif broken == 'old-bundle':
        (web / 'assets/index-test.js').write_text('const route="/display/monitor-1";')
    elif broken == 'external-symlink':
        outside = tmp_path / 'outside.js'
        outside.write_text('export const route="/display/mobile";')
        chunk.unlink()
        chunk.symlink_to(outside)
    else:
        (web / 'assets/index-test.js').write_text('const route="/display/mobile";import("./../../outside.js");')
    result = checker.inspect(image)
    assert (web / 'index.html').is_file()
    assert not result['artifact_layout_valid'] and not result['checks']['compiled_mobile_assets_complete']


@pytest.mark.parametrize('change', ['access-needs-hdmi', 'feed-owned-by-hdmi', 'active-phone-config', 'automatic-phone-enable', 'external-access-unit'])
def test_phone_artifacts_do_not_silently_activate_or_couple_to_hdmi(image, change):
    units = image / 'usr/lib/systemd/system'
    if change == 'access-needs-hdmi':
        path = units / 'xenova-display-mobile.service'
        path.write_text(path.read_text().replace('After=network.target', 'After=xenova-display-hdmi.service network.target'))
    elif change == 'feed-owned-by-hdmi':
        path = units / 'xenova-display-feed.service'
        path.write_text(path.read_text().replace('[Unit]', '[Unit]\nPartOf=xenova-display.target'))
    elif change == 'active-phone-config':
        path = image / 'etc/xenova/display.env'
        path.parent.mkdir(parents=True)
        path.write_text('XENOVA_DISPLAY_TOKEN=synthetic-private-value')
    elif change == 'automatic-phone-enable':
        link = image / 'etc/systemd/system/multi-user.target.wants/xenova-display-mobile.service'
        link.parent.mkdir(parents=True)
        link.symlink_to('/usr/lib/systemd/system/xenova-display-mobile.service')
    else:
        path = units / 'xenova-display-mobile.service'
        outside = image.parent / 'external-mobile.service'
        outside.write_text(path.read_text())
        path.unlink()
        path.symlink_to(outside)
    result = checker.inspect(image)
    assert not result['artifact_layout_valid']
    assert 'synthetic-private-value' not in json.dumps(result)


def test_installed_unit_graph_passes_actual_systemd_loader(image):
    executable = shutil.which('systemd-analyze')
    if not executable:
        pytest.skip('systemd-analyze unavailable')
    units = image / 'usr/lib/systemd/system'
    for name in ['sysinit.target', 'basic.target', 'shutdown.target', 'network.target', 'multi-user.target']:
        (units / name).write_text('[Unit]\nDescription=Isolated target\n')
    python = image / 'usr/bin/python3'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nexit 0\n')
    python.chmod(0o755)
    args = [executable, 'verify', '--root=' + str(image), 'xenova-display.target',
            'xenova-display-feed.service', 'xenova-display-hdmi.service', 'xenova-display-mobile.service']
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_offline_cli_reads_artifacts_and_refuses_incomplete_image(image):
    before = {str(p.relative_to(image)): p.read_bytes() for p in image.rglob('*') if p.is_file()}
    args = [sys.executable, str(CHECKER), '--root', str(image)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)['artifact_layout_valid']
    assert before == {str(p.relative_to(image)): p.read_bytes() for p in image.rglob('*') if p.is_file()}
    (image / 'usr/lib/systemd/system/xenova-display-mobile.service').unlink()
    rejected = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert rejected.returncode == 1 and not json.loads(rejected.stdout)['artifact_layout_valid']
