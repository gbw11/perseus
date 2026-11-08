"""Optional timing reuses the private frame without renewing source freshness."""
import json
import errno
from unittest.mock import patch

import pytest

from dom0_manager.observations import MAX_BYTES
from dom0_manager.errors import PlatformUnavailable
from test_observations import rig


def test_diagnostics_default_off_keeps_the_existing_frame(rig):
    hub, driver, now = rig
    hub.refresh()
    assert 'diagnostics' not in json.loads(hub.path.read_text())
    assert hub.previous_round is None
    assert driver.calls == 1
    now[0] += 3
    with pytest.raises(PlatformUnavailable):
        hub.reader.read()


def test_two_rounds_separate_query_and_publication_without_extra_reads_or_writes(rig):
    hub, driver, now = rig
    hub.diagnostics = True
    read, publish = driver.list_domains, hub._publish

    def slow_query():
        value = read()
        now[0] += .8
        return value

    def timed_publish(*args, **kwargs):
        publish(*args, **kwargs)
        now[0] += .15

    driver.list_domains = slow_query
    hub._publish = timed_publish
    with patch('dom0_manager.observations.os.replace', wraps=__import__('os').replace) as rename:
        hub.refresh()
        first = json.loads(hub.path.read_text())
        hub.refresh()
        second = json.loads(hub.path.read_text())
    assert driver.calls == 2 and rename.call_count == 2
    assert first['observed_mono'] == 100
    assert first['diagnostics']['previous_round'] is None
    diagnostic = second['diagnostics']
    previous = diagnostic['previous_round']
    assert previous['query_finished_mono'] - previous['query_started_mono'] == pytest.approx(.8)
    assert previous['publish_finished_mono'] - previous['publish_started_mono'] == pytest.approx(.15)
    assert previous['published'] is True
    assert 'previous_round' not in previous  # Constant space, no growing history.
    assert diagnostic['current']['outcome'] == 'ok'
    assert second['observed_mono'] == pytest.approx(100.95)
    assert hub.reader.read()[1] == second['observed_mono']
    now[0] = second['observed_mono'] + 3
    with pytest.raises(PlatformUnavailable):
        hub.reader.read()
    assert list(hub.path.parent.iterdir()) == [hub.path]
    assert hub.path.stat().st_mode & 0o777 == 0o600


def test_query_failure_is_unavailable_and_does_not_export_exception_text(rig):
    hub, driver, now = rig
    hub.diagnostics = True

    def fail():
        now[0] += .4
        raise PlatformUnavailable('sensitive fixture text must never be exported')

    driver.list_domains = fail
    hub.refresh()
    raw = hub.path.read_text()
    frame = json.loads(raw)
    assert 'sensitive fixture' not in raw
    assert frame['status'] == 'unavailable' and frame['domains'] == []
    assert frame['diagnostics']['current']['outcome'] == 'query_error'
    assert frame['diagnostics']['current']['error_kind'] == 'platform_unavailable'
    with pytest.raises(PlatformUnavailable):
        hub.reader.read()


def test_slow_query_retains_original_time_and_records_expiry(rig):
    hub, driver, now = rig
    hub.diagnostics = True
    read = driver.list_domains

    def slow():
        result = read()
        now[0] += 4
        return result

    driver.list_domains = slow
    hub.refresh()
    frame = json.loads(hub.path.read_text())
    assert frame['observed_mono'] == 100
    assert frame['status'] == 'unavailable'
    assert frame['diagnostics']['current']['outcome'] == 'expired'
    assert driver.calls == 1


def test_oversized_optional_metadata_does_not_suppress_the_actual_observation(rig):
    hub, driver, now = rig
    hub._publish(driver.list_domains(), now[0], diagnostic={'padding': 'x' * MAX_BYTES})
    assert 'diagnostics' not in json.loads(hub.path.read_text())
    assert len(hub.path.read_bytes()) <= MAX_BYTES
    assert len(hub.reader.read()[0]) == 1


def test_publication_failure_remains_failure_and_next_round_reports_it(rig):
    hub, _, _ = rig
    hub.diagnostics = True
    with patch('dom0_manager.observations.os.replace', side_effect=OSError('fixture write failed')):
        with pytest.raises(OSError):
            hub.refresh()
    assert hub.previous_round['published'] is False
    hub.refresh()
    assert json.loads(hub.path.read_text())['diagnostics']['previous_round']['published'] is False


def test_optional_clock_failure_falls_back_to_the_original_frame(rig):
    hub, driver, now = rig
    hub.diagnostics = True
    calls = []

    def clock():
        calls.append(None)
        if len(calls) == 3:  # Optional publication-start timestamp only.
            raise RuntimeError('fixture diagnostic clock failed')
        return now[0]

    hub.clock = clock
    hub.refresh()
    assert driver.calls == 1
    assert 'diagnostics' not in json.loads(hub.path.read_text())
    assert len(hub.reader.read()[0]) == 1


@pytest.mark.parametrize('flag,enabled', [(None, False), ('1', True), ('0', False)])
def test_diagnostics_are_enabled_only_by_the_explicit_environment_flag(tmp_path, monkeypatch, flag, enabled):
    from dom0_manager.config import Settings
    from dom0_manager.drivers.mock import MockDriver, MockProvisioner
    from dom0_manager.main import create_app

    monkeypatch.setenv('XENOVA_OBSERVATIONS_PATH', str(tmp_path / 'state.json'))
    if flag is None:
        monkeypatch.delenv('XENOVA_OBSERVATION_DIAGNOSTICS', raising=False)
    else:
        monkeypatch.setenv('XENOVA_OBSERVATION_DIAGNOSTICS', flag)
    app = create_app(Settings(driver='mock'), driver=MockDriver(), provisioner=MockProvisioner())
    assert app.state.observations.diagnostics is enabled


@pytest.mark.parametrize('code,expected', [('OPERATION_IN_PROGRESS', 'OPERATION_IN_PROGRESS'),
                                         ('DEADLINE_EXCEEDED', 'DEADLINE_EXCEEDED'),
                                         ('sensitive-fixture-code', None)])
def test_query_diagnostics_keep_only_known_platform_codes(rig, code, expected):
    hub, driver, _ = rig
    hub.diagnostics = True

    def fail():
        raise PlatformUnavailable('sensitive fixture message', platform_code=code)

    driver.list_domains = fail
    hub.refresh()
    raw = hub.path.read_text()
    assert 'sensitive' not in raw
    assert json.loads(raw)['diagnostics']['current']['platform_code'] == expected


def test_publication_errno_is_retained_without_changing_the_original_failure(rig):
    hub, _, _ = rig
    hub.diagnostics = True
    error = OSError(errno.ENOSPC, 'sensitive fixture path')
    with patch('dom0_manager.observations.os.replace', side_effect=error):
        with pytest.raises(OSError) as caught:
            hub.refresh()
    assert caught.value is error
    hub.refresh()
    raw = hub.path.read_text()
    previous = json.loads(raw)['diagnostics']['previous_round']
    assert previous['publication_error_kind'] == 'os_error'
    assert previous['publication_errno'] == errno.ENOSPC
    assert 'sensitive' not in raw


@pytest.mark.parametrize('problem,reason', [('missing', 'frame_metadata_invalid'),
                                          ('expired', 'frame_expired_or_future')])
def test_verified_frame_rejection_is_distinct_from_a_manager_refusal(rig, problem, reason):
    from test_observations import BOOT, VM

    hub, driver, _ = rig
    hub.diagnostics = True
    driver.name = 'platform'
    view = {'vm_id': str(VM), 'host_boot_id': BOOT, 'observed_monotonic_ms': 96000}
    if problem == 'missing':
        view.pop('observed_monotonic_ms')
    driver._views = lambda: [view]
    hub.refresh()
    current = json.loads(hub.path.read_text())['diagnostics']['current']
    assert current['reason'] == reason
    assert current['platform_code'] is None
    with pytest.raises(PlatformUnavailable):
        hub.reader.read()
