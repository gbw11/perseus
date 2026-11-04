from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from xenova_display.gateway import GatewayReader, normalize
from xenova_display.main import create_app
from xenova_display.mqtt import MqttSource
from xenova_display.state import StateStore
from xenova_display.config import DISPLAY_DRIVE_TOPIC, DISPLAY_DRIVE_TYPE
from test_feed import settings, domains, VM1, VM2, VM3, message

HOST = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
EXEC = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def context(**changes):
    return dict(host_boot_id=HOST, owner=VM1, generation=7, mode='AUTO', phase='APPLIED',
                output_permitted=True, storage_uncertain=False,
                targets={vm: dict(execution_id=EXEC, boot_id=HOST, intent_revision=2,
                                 lifecycle_generation=1) for vm in (VM1, VM2)}) | changes


def project(**changes):
    return normalize(context(**changes), host_boot_id=HOST, drive_ids={VM1, VM2})


def gateway_store(settings):
    now = [100.0]
    store = StateStore(replace(settings, drive_transport='gateway'), clock=lambda: now[0])
    store.poll_success(domains())
    return store, now


def test_owner_and_mode_change_independently_without_control_calls(settings):
    store, now = gateway_store(settings)
    store.gateway_success(project(), observed_mono=100)
    assert store.snapshot()['drive']['active_vm_id'] == VM1
    store.gateway_success(project(owner=VM2, generation=8), observed_mono=100)
    assert store.snapshot()['drive']['mode'] == 'AUTO'
    store.gateway_success(project(owner=VM2, generation=9, mode='MANUAL'), observed_mono=100)
    assert store.snapshot()['drive']['active_vm_id'] == VM2
    assert store.snapshot()['drive']['mode'] == 'MANUAL'


@pytest.mark.parametrize('phase,permitted', [('DISARMED', False), ('PENDING', False),
                                           ('PENDING', True), ('APPLIED', False), ('FAILED', False)])
def test_saved_selection_is_not_active_output(phase, permitted):
    result = project(phase=phase, output_permitted=permitted)
    assert result['mode'] == 'AUTO'
    assert result['active_vm_id'] is None
    assert result['authority']['selected_vm_id'] == VM1


def test_uncertainty_and_missing_binding_never_claim_active():
    result = project(storage_uncertain=True)
    assert result['mode'] == 'unknown' and result['active_vm_id'] is None
    assert result['authority']['storage_uncertain'] is True
    result = project(targets={VM1: None, VM2: None})
    assert result['active_vm_id'] is None
    assert project(mode='SAFE_STOP')['active_vm_id'] is None
    assert project(mode='AUTO_READY')['active_vm_id'] is None


@pytest.mark.parametrize('change', [dict(owner=VM3), dict(host_boot_id=EXEC), dict(generation=True),
    dict(generation=0), dict(generation=2**32), dict(mode='OTHER'), dict(phase='NORMAL'),
    dict(output_permitted=None), dict(storage_uncertain=0), dict(targets={VM1: None})])
def test_invalid_or_wrong_host_context_is_rejected(change):
    with pytest.raises(ValueError):
        project(**change)


def test_reader_calls_only_context_and_preserves_request_start(settings):
    client = SimpleNamespace(context=Mock(return_value=context()))
    times = iter([100, 100.3])
    reader = GatewayReader(settings, client=client, clock=lambda: next(times), host_boot_id=HOST)
    value, observed = reader.read()
    assert observed == 100 and value['active_vm_id'] == VM1
    client.context.assert_called_once_with()
    times = iter([100, 101])
    reader.clock = lambda: next(times)
    with pytest.raises(ValueError, match='budget'):
        reader.read()


def test_existing_peer_verified_client_sends_only_context_over_real_uds(settings, tmp_path):
    import os
    import socket
    import threading
    if not hasattr(socket, 'SO_PEERCRED'):
        pytest.skip('Linux peer credential transport required')
    from xenova_gateway.client import GatewayClient, GatewayUnavailable
    from xenova_gateway.ipc_contract import HEADER, decode, encode
    directory = tmp_path/'private'; directory.mkdir(mode=0o700)
    endpoint = directory/'control.sock'
    seen = []
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(endpoint)); endpoint.chmod(0o600)
        listener.listen(1); listener.settimeout(2)
        def serve():
            with listener.accept()[0] as connection:
                connection.settimeout(2)
                data = bytearray()
                while len(data) < 4:
                    data.extend(connection.recv(4-len(data)))
                length = HEADER.unpack(data)[0]
                data = bytearray()
                while len(data) < length:
                    data.extend(connection.recv(length-len(data)))
                seen.append(decode(bytes(data)))
                connection.sendall(encode({'version': 1, 'ok': True, 'data': context()}))
        thread = threading.Thread(target=serve)
        thread.start()
        try:
            client = GatewayClient(endpoint, budget_s=.5, uid=os.geteuid())
            reader = GatewayReader(settings, client=client, host_boot_id=HOST)
            result, _ = reader.read()
            assert result['mode'] == 'AUTO'
        finally:
            thread.join(3)
        assert not thread.is_alive()
        assert seen == [{'version': 1, 'action': 'context'}]
        directory.chmod(0o755)
        with pytest.raises(GatewayUnavailable):
            reader.read()  # Do not loosen the Gateway's existing private permissions.


def test_expiry_failure_recovery_and_mqtt_independence(settings):
    store, now = gateway_store(settings)
    store.gateway_success(project(), observed_mono=99.6)
    drive = store.snapshot()['drive']
    assert drive['source']['age_ms'] >= 399
    assert drive['mode'] == 'AUTO'  # MQTT is disconnected, Gateway is independent.
    now[0] = 102.6
    drive = store.snapshot()['drive']
    assert drive['source']['status'] == 'stale' and drive['authority'] is None
    assert drive['mode'] == 'unknown' and drive['active_vm_id'] is None
    store.gateway_success(project(), observed_mono=102.6)
    store.gateway_failure()
    drive = store.snapshot()['drive']
    assert drive['source']['status'] == 'disconnected' and drive['authority'] is None
    store.gateway_success(project(), observed_mono=102.6)
    assert store.snapshot()['drive']['mode'] == 'AUTO'
    with pytest.raises(ValueError):
        store.gateway_success(project(generation=6), observed_mono=102.6)
    with pytest.raises(ValueError):
        store.gateway_success(project(owner=VM2), observed_mono=102.6)


def test_gateway_mode_excludes_mqtt_control_but_keeps_guest_topics(settings):
    store, _ = gateway_store(settings)
    source = MqttSource(store)
    assert DISPLAY_DRIVE_TOPIC not in source.topics
    assert any('/apps/' in t for t in source.topics)
    env = message(kind=DISPLAY_DRIVE_TYPE, mode='MANUAL', active_vm_id=VM2)
    env.pop('vm_id'); env['source_id'] = 'dom0-gateway'
    assert not store.ingest(DISPLAY_DRIVE_TOPIC, env)
    assert store.snapshot()['drive']['mode'] == 'unknown'


def test_mqtt_mode_remains_default(settings):
    store = StateStore(settings)
    assert DISPLAY_DRIVE_TOPIC in MqttSource(store).topics
    with pytest.raises(ValueError):
        store.gateway_success(project(), observed_mono=0)


class NoMqtt:
    def __init__(self, store): pass
    def start(self): pass
    def stop(self): pass


def test_gateway_observation_reaches_authenticated_rest_ws_and_presentation(settings):
    store, now = gateway_store(settings)
    reader = SimpleNamespace(read=Mock(return_value=(project(phase='DISARMED', output_permitted=False), 100)))
    app = create_app(store.settings, store=store, driver=SimpleNamespace(list_domains=domains),
                     mqtt_factory=NoMqtt, gateway_factory=lambda _: reader)
    headers = {'Authorization': f'Bearer {settings.token}'}
    with TestClient(app) as client:
        # Synchronize with the live startup poll, without assumptions about thread scheduling.
        import time
        deadline = time.monotonic() + 2
        while store.gateway_observation is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert client.get('/api/v1/display/state').status_code == 401
        drive = client.get('/api/v1/display/presentation', headers=headers).json()['state']['drive']
        assert drive['mode'] == 'AUTO' and drive['active_vm_id'] is None
        assert drive['authority']['phase'] == 'DISARMED'
        with client.websocket_connect('/ws/v1/display/state', headers=headers) as ws:
            assert ws.receive_json()['drive']['authority']['selected_vm_id'] == VM1
    assert reader.read.call_count == 1  # No per-client extra Gateway poll.


@pytest.mark.parametrize('changes', [dict(drive_transport='bad'), dict(gateway_budget_s=0),
    dict(gateway_budget_s=float('nan')), dict(gateway_budget_s=2), dict(gateway_socket='relative')])
def test_invalid_gateway_configuration_fails(settings, changes):
    with pytest.raises(ValueError):
        replace(settings, **changes)
