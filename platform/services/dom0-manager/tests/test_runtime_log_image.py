"""Exercise the image install fragment and the packaged collector/API path on Linux."""
import configparser
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from dom0_manager.config import Settings
from dom0_manager.main import create_app


ROOT = Path(__file__).resolve().parents[4]
RECIPE = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-dom0-manager'
APPEND = RECIPE / 'xenova-dom0-manager_%.bbappend'
FILES = RECIPE / 'files/runtime-logs'
SERVICES = ROOT / 'platform/services/dom0-manager'
UNIT = 'xenova-runtime-log-collector.service'
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux image installation/process semantics')


def ini(path):
    result = configparser.ConfigParser(interpolation=None, strict=False)
    result.read(path)
    return result


@pytest.fixture
def image(tmp_path):
    """Execute the actual append install body, without emulating BitBake parsing."""
    stage, unpack = tmp_path / 'image', tmp_path / 'unpack'
    unpack.mkdir()
    text = APPEND.read_text()
    names = re.findall(r'file://([^"\s]+)', text)
    for name in names:
        shutil.copyfile(FILES / name, unpack / name)
    values = {'D': str(stage), 'S': str(unpack), 'bindir': '/usr/bin',
              'sysconfdir': '/etc', 'datadir': '/usr/share',
              'systemd_system_unitdir': '/usr/lib/systemd/system'}
    body = re.search(r'do_install:append\(\) \{\n(.*?)\n\}', text, re.S).group(1)
    for key, value in values.items():
        body = body.replace('${' + key + '}', value)
    assert '${' not in body, 'Unhandled BitBake variable in isolated install test'
    subprocess.run(['sh', '-eu', '-c', body], check=True, capture_output=True, text=True)
    return stage


def test_image_installs_one_collector_with_shared_private_configuration(image):
    units = image / 'usr/lib/systemd/system'
    collector = ini(units / UNIT)
    manager = ini(units / 'xenova-dom0-manager.service.d/30-runtime-logs.conf')
    config = image / collector['Service']['EnvironmentFile'].lstrip('/')
    assert manager['Service']['EnvironmentFile'] == collector['Service']['EnvironmentFile']
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert config.read_text().splitlines()[-1] == 'XENOVA_RUNTIME_LOG_DB=/var/lib/xenova-runtime-logs/logs.sqlite3'
    assert 'PYTHONPATH=' not in config.read_text() and 'TOKEN=' not in config.read_text()
    assert '@PYTHON@' not in (units / UNIT).read_text()
    assert collector['Service']['ExecStart'].startswith('/usr/bin/python3 -m dom0_manager.log_collector ')
    assert collector['Service']['StateDirectory'] == 'xenova-runtime-logs'
    assert collector['Service']['RestrictAddressFamilies'] == 'AF_UNIX'
    assert collector['Install']['WantedBy'] == 'multi-user.target'
    assert manager['Unit']['Wants'] == UNIT and manager['Unit']['After'] == UNIT
    for relationship in ('Requires', 'BindsTo', 'PartOf'):
        assert relationship not in manager['Unit'] and relationship not in collector['Unit']
    assert sorted(p.name for p in units.glob('*.service')) == [UNIT]
    assert not list(image.rglob('*ingest*.service')) and not list(image.rglob('*shipper*.service'))
    append = APPEND.read_text()
    assert 'SYSTEMD_SERVICE:${PN}:append = " ' + UNIT + '"' in append
    assert 'RDEPENDS:${PN}:append = " systemd"' in append
    base = (RECIPE / 'xenova-dom0-manager_0.1.0.bb').read_text()
    assert 'SYSTEMD_AUTO_ENABLE:${PN} = "enable"' in base


def test_installed_units_pass_systemd_verification(image):
    executable = shutil.which('systemd-analyze')
    if not executable:
        pytest.skip('systemd-analyze unavailable')
    units = image / 'usr/lib/systemd/system'
    # Offline verification needs only executable/boot target stubs, no root or
    # systemd daemon. Include the real Manager unit to verify its installed drop-in.
    python = image / 'usr/bin/python3'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nexit 0\n')
    python.chmod(0o755)
    shutil.copyfile(RECIPE / 'files/xenova-dom0-manager.service', units / 'xenova-dom0-manager.service')
    for target in ['sysinit.target', 'basic.target', 'shutdown.target', 'network.target', 'multi-user.target']:
        (units / target).write_text('[Unit]\nDescription=Offline verification target\n')
    # Before/After-only dependencies need no VM services running.
    result = subprocess.run([executable, 'verify', '--root=' + str(image),
                             UNIT, 'xenova-dom0-manager.service'], capture_output=True, text=True, timeout=15,
                            env={**os.environ, 'SYSTEMD_LOG_LEVEL': 'warning'})
    assert result.returncode == 0, result.stderr


def wait_for(check):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    pytest.fail('Packaged collector did not deliver expected local records')


def test_packaged_collector_to_authenticated_api_and_restart(image, tmp_path):
    collector = ini(image / 'usr/lib/systemd/system' / UNIT)
    config_path = image / collector['Service']['EnvironmentFile'].lstrip('/')
    # Relocate only the packaged storage path for this unprivileged integration test.
    db = tmp_path / 'state/logs.sqlite3'
    config_path.write_text(config_path.read_text().replace('/var/lib/xenova-runtime-logs/logs.sqlite3', str(db)))
    packaged_env = dict(line.split('=', 1) for line in config_path.read_text().splitlines() if line and not line.startswith('#'))
    settings = Settings.from_env({**packaged_env, 'XENOVA_SERVICE_TOKEN': 'image-test-key', 'XENOVA_DRIVER': 'mock'})
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    calls = tmp_path / 'journal-arguments.jsonl'
    reader = fake_bin / 'journalctl'
    reader.write_text('#!' + sys.executable + '\n' + '''import json,os,sys,time
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['IMAGE_JOURNAL_CALLS']).open('a') as f:f.write(json.dumps(args)+'\\n')
row={'_BOOT_ID':'image-boot','__REALTIME_TIMESTAMP':str(int(time.time()*1000000)),
     '_SYSTEMD_UNIT':'xenova-dom0-manager.service','PRIORITY':'6',
     '__CURSOR':'image-cursor1','MESSAGE':'image test password=synthetic-secret'}
print(json.dumps(row),flush=True)
if '--after-cursor' in args:
 row.update({'__CURSOR':'image-cursor2','MESSAGE':'after restart'})
 print(json.dumps(row),flush=True)
row.update({'_SYSTEMD_UNIT':'unregistered.service','__CURSOR':'ignored','MESSAGE':'must not collect'})
print(json.dumps(row),flush=True)
while True:time.sleep(.1)
''')
    reader.chmod(0o755)
    command = shlex.split(collector['Service']['ExecStart'])
    command[0] = sys.executable
    environment = {**os.environ, **packaged_env, 'PYTHONPATH': str(SERVICES),
                   'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'],
                   'IMAGE_JOURNAL_CALLS': str(calls)}

    def rows(count):
        if not db.exists():
            return False
        try:
            with sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True) as connection:
                return connection.execute('SELECT count(*) FROM logs').fetchone()[0] == count
        except sqlite3.OperationalError:
            return False

    with TestClient(create_app(settings)) as client:
        headers = {'Authorization': 'Bearer image-test-key'}
        assert client.get('/api/v1/logs', headers=headers).status_code == 503
        assert not db.exists(), 'Read API must not create a fake healthy DB'
        epochs = []
        for expected in (1, 2):
            process = subprocess.Popen(command, cwd=SERVICES, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                wait_for(lambda: rows(expected))
                assert process.poll() is None
                assert client.get('/api/v1/logs').status_code == 401
                response = client.get('/api/v1/logs', headers=headers)
                assert response.status_code == 200
                body = response.json()
                epochs.append(body['epoch'])
                assert len(body['items']) == expected
                assert body['items'][0]['message'] == 'image test password=[REDACTED]'
                assert all(item['component'] == 'xenova-dom0-manager.service' for item in body['items'])
                sources = client.get('/api/v1/logs/sources', headers=headers).json()['items']
                assert sources[0]['source_id'] == 'dom0' and sources[0]['state'] == 'connected'
                assert 'xenova-gateway.service' in sources[0]['components']
            finally:
                if process.poll() is None:
                    process.terminate()
                _, error = process.communicate(timeout=8)
            assert process.returncode == 0, error
            assert client.get('/api/v1/logs/sources', headers=headers).json()['items'][0]['state'] == 'disconnected'
            assert client.get('/api/v1/logs', headers=headers).status_code == 200
        assert epochs[0] == epochs[1]
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(invocations) == 2
    assert '--after-cursor' not in invocations[0]
    assert invocations[1][invocations[1].index('--after-cursor') + 1] == 'image-cursor1'
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
