"""Sharing reads must not weaken fresh control validation or hide stale failures."""
from dataclasses import replace
import json
import os
from pathlib import Path
import threading
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from dom0_manager.config import Settings
from dom0_manager.drivers.base import Domain
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.errors import PlatformUnavailable
from dom0_manager.main import create_app
from dom0_manager.models import VmState
from dom0_manager.observations import ObservationFile, ObservationHub
from dom0_manager.service import VmService

BOOT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
VM = UUID('11111111-1111-4111-8111-111111111111')


@pytest.fixture
def rig(tmp_path):
    now = [100.0]
    class Driver:
        name = 'mock'
        calls = 0
        fail = False
        def list_domains(self):
            self.calls += 1
            if self.fail:
                raise RuntimeError('fixture failure')
            return [Domain(VM, VmState.running, 1, 16, 2, 'drive-a', 'execution-a')]
        def get_domain(self, vm_id):
            raise PlatformUnavailable('fresh control lookup failed')
    driver = Driver()
    hub = ObservationHub(driver, tmp_path / 'state.json', clock=lambda: now[0], boot_id=BOOT)
    return hub, driver, now


def test_many_consumers_share_one_query_but_get_still_checks_real_source(rig):
    hub, driver, _ = rig
    hub.refresh()
    service = VmService(driver, None, Settings(driver='mock'))
    service.observations = hub
    for _ in range(30):
        assert service.list()[0].vm_id == VM
        assert hub.reader.read()[0][0].observation_id.endswith(':execution-a')
    assert driver.calls == 1
    with pytest.raises(PlatformUnavailable, match='fresh control'):
        service.get(VM)


def test_expiry_and_failed_refresh_never_return_old_success(rig):
    hub, driver, now = rig
    hub.refresh()
    now[0] += 3
    with pytest.raises(PlatformUnavailable): hub.list_domains()
    now[0] += 1
    hub.refresh()
    driver.fail = True
    hub.refresh()
    with pytest.raises(PlatformUnavailable): hub.list_domains()


@pytest.mark.parametrize('change', ['boot', 'future', 'duplicate', 'dom0', 'malformed', 'permissions', 'symlink'])
def test_untrusted_or_invalid_snapshot_is_unavailable(rig, change):
    hub, _, _ = rig
    hub.refresh()
    value = json.loads(hub.path.read_text())
    if change == 'boot': value['host_boot_id'] = str(VM)
    if change == 'future': value['observed_mono'] = 101.0
    if change == 'duplicate': value['domains'] *= 2
    if change == 'dom0': value['domains'][0]['vm_id'] = str(UUID(int=0))
    if change == 'malformed': value['domains'][0]['domid'] = True
    hub.path.write_text(json.dumps(value))
    if change == 'permissions': hub.path.chmod(0o644)
    if change == 'symlink':
        target = hub.path.with_suffix('.other')
        hub.path.rename(target)
        hub.path.symlink_to(target)
    with pytest.raises(PlatformUnavailable): hub.list_domains()


def test_shutdown_invalidates_and_late_query_cannot_republish(rig):
    hub, driver, _ = rig
    hub.refresh()
    entered, release = threading.Event(), threading.Event()
    original = driver.list_domains
    def blocked():
        entered.set()
        assert release.wait(2)
        return original()
    driver.list_domains = blocked
    thread = threading.Thread(target=hub.refresh)
    thread.start()
    assert entered.wait(2)
    hub.stop()
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    with pytest.raises(PlatformUnavailable): hub.list_domains()


def test_slow_query_does_not_gain_new_freshness(rig):
    hub, driver, now = rig
    original = driver.list_domains
    def slow():
        rows = original()
        now[0] += 4
        return rows
    driver.list_domains = slow
    hub.refresh()
    with pytest.raises(PlatformUnavailable): hub.list_domains()


def test_optional_lifespan_no_new_public_route(tmp_path, monkeypatch):
    path = tmp_path / 'runtime.json'
    monkeypatch.setenv('XENOVA_OBSERVATIONS_PATH', str(path))
    app = create_app(Settings(driver='mock'), driver=MockDriver(), provisioner=MockProvisioner())
    with TestClient(app):
        assert app.state.observations.thread.is_alive()
        assert '/api/v1/observations' not in app.openapi()['paths']
    assert json.loads(path.read_text())['status'] == 'unavailable'


@pytest.mark.parametrize('bad', [None, 'old', 'wrong_boot', 'future', 'missing_time', 'missing_identity'])
def test_platform_reuses_exact_verified_list_without_requery(rig, bad):
    hub, driver, now = rig
    driver.name = 'platform'
    view = {'vm_id': str(VM), 'domid': 2, 'xen_state': 'running',
            'configuration': {'vcpus': 1, 'memory_mib': 16}, 'name': 'drive-a',
            'host_boot_id': str(VM) if bad == 'wrong_boot' else BOOT,
            'observed_monotonic_ms': 99000 if bad == 'old' else 108000 if bad == 'future' else 107000,
            'xen_unique_id': 'xen-incarnation-a'}
    if bad == 'missing_time': view.pop('observed_monotonic_ms')
    if bad == 'missing_identity': view.pop('xen_unique_id')
    class Manager:
        calls = []
        def call(self, action, spec):
            raise AssertionError('display must not issue another list/get/snapshot')
    driver.manager = Manager()
    def views():
        now[0] = 107.0  # the query waited 7s; platform observation is newer
        return [view]
    driver._views = views
    hub.refresh()
    if bad is not None:
        with pytest.raises(PlatformUnavailable): hub.list_domains()
    else:
        rows, observed = hub.reader.read()
        assert observed == 107.0
        assert rows[0].observation_id.endswith(':xen-incarnation-a')
        assert driver.calls == 0


def test_query_runtime_does_not_add_an_extra_second_of_stale_window(rig):
    hub, driver, now = rig
    driver.name = 'platform'
    view = {'vm_id': str(VM), 'domid': 2, 'xen_state': 'running',
            'configuration': {'vcpus': 1, 'memory_mib': 16}, 'name': 'drive-a',
            'host_boot_id': BOOT, 'xen_unique_id': 'same-run'}
    calls = []
    def views():
        now[0] += 1.6
        if calls:
            # The previous verified frame must still be fresh immediately
            # before the next query publishes, not expire due to idle sleep.
            hub.reader.read()
        calls.append(now[0])
        return [{**view, 'observed_monotonic_ms': int((now[0]-.6)*1000)}]
    driver._views = views
    class Manager:
        def call(self, action, spec):
            raise AssertionError('extra lookup')
    driver.manager = Manager()
    class StopAfterTwoFrames:
        waits = 0
        def is_set(self): return self.waits >= 2
        def wait(self, delay):
            now[0] += delay
            self.waits += 1
    hub.stop_event = StopAfterTwoFrames()
    hub._run()
    assert len(calls) == 2
    assert hub.reader.read()[0][0].vm_id == VM
