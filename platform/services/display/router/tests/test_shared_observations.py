from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from xenova_display.mqtt import MqttSource
from xenova_display.main import create_app
from fastapi.testclient import TestClient

from test_feed import app, domains, store, settings


def test_reused_snapshot_keeps_original_age(store):
    store.poll_success(domains(), observed_mono=98.0)
    assert store.snapshot()['vms'][0]['sources']['xen']['age_ms'] == 2000
    store.now[0] = 101.0
    assert store.snapshot()['vms'][0]['sources']['xen']['status'] == 'stale'
    assert not app(store)


def test_restarted_producer_or_execution_clears_old_guest_reports(store):
    rows = [replace(row, observation_id='producer-a:execution-a') for row in domains()]
    store.poll_success(rows)
    assert app(store)
    before = store.snapshot()['vms'][0]['run_id']
    store.poll_success([replace(row, observation_id='producer-b:execution-a') for row in rows])
    vm = store.snapshot()['vms'][0]
    assert vm['run_id'] != before
    assert vm['app_state'] == 'unknown'
    assert app(store, seq=2)  # observer restart must not require a guest reboot
    store.poll_success([replace(row, observation_id='producer-b:execution-b') for row in rows])
    assert store.snapshot()['vms'][0]['app_state'] == 'unknown'
    assert not app(store, seq=3)  # actual changed execution retires the old guest boot


def test_existing_credentials_are_supplied_to_subscription_client(settings):
    configured = replace(settings, mqtt_username='observer', mqtt_password='local-test-secret')
    with patch('xenova_display.mqtt.mqtt.Client') as client:
        source = MqttSource(SimpleNamespace(settings=configured, epoch='local-test'))
        client.return_value.username_pw_set.assert_called_once_with('observer', 'local-test-secret')
        client.return_value.publish.assert_not_called()
        assert all(topic.startswith('xenova/v1/') for topic in source.topics)


@pytest.mark.parametrize('username,password', [('observer', None), (None, 'local-test-secret')])
def test_incomplete_authentication_is_rejected(settings, username, password):
    with pytest.raises(ValueError, match='together'):
        replace(settings, mqtt_username=username, mqtt_password=password)


def test_existing_source_remains_default_until_sharing_is_explicit(settings, monkeypatch):
    monkeypatch.delenv('XENOVA_OBSERVATIONS_PATH', raising=False)
    with patch('xenova_display.main.build_driver') as factory:
        create_app(settings)
        factory.assert_called_once()


def test_explicit_shared_source_never_falls_back_when_file_is_missing(settings, monkeypatch, tmp_path):
    monkeypatch.setenv('XENOVA_OBSERVATIONS_PATH', str(tmp_path/'missing.json'))
    class NoMqtt:
        def __init__(self, store): pass
        def start(self): pass
        def stop(self): pass
    with patch('xenova_display.main.build_driver', side_effect=AssertionError('unexpected Xen fallback')):
        app = create_app(settings, mqtt_factory=NoMqtt)
        with TestClient(app) as client:
            response = client.get('/api/v1/display/state', headers={'Authorization': f'Bearer {settings.token}'})
            assert response.status_code == 200
            assert all(v['sources']['xen']['status'] == 'unknown' for v in response.json()['vms'])
