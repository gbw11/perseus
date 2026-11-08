"""Actual cache publication races; broker is replaced only by callback payloads.

Event/trace gates belong to these tests, not the product. Their timeouts bound
test cleanup and are neither production budgets nor latency guarantees.
"""
from contextlib import contextmanager
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from xenova_mqtt import Identity, XenovaClient, topics
from xenova_mqtt import envelope
from xenova_mqtt import drive_binding as product
from xenova_mqtt.drive_binding import BindingCache


def uid(number):
    return f'{number:08x}-1111-4111-8111-111111111111'


VM, HOST, BOOT = uid(1), uid(2), uid(3)


def binding(version=1, observed=100000):
    return dict(schema_version='1.0-drive-bindings', host_boot_id=HOST,
        observed_monotonic_ms=observed, expires_monotonic_ms=observed + 3000,
        vms={VM: dict(status='observed', profile='warm-drive-1', version=version,
            execution_id=uid(20 + version), boot_id=BOOT, app_instance_id=uid(30 + version),
            app_instances={'autonomous': uid(40 + version), 'manual': uid(50 + version)},
            app_status={'autonomous': 'observed', 'manual': 'observed'},
            app_state_models={'autonomous': 'stateful', 'manual': 'stateless'},
            intent_revision=version, lifecycle_generation=version, domid=version,
            xen_unique_id=str(version), observed_monotonic_ms=observed)})


def invoke_thread(callback):
    done, output, errors = threading.Event(), [], []

    def run():
        try:
            output.append(callback())
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, done, output, errors


def joined(call):
    thread, done, output, errors = call
    thread.join(3)
    assert not thread.is_alive() and done.is_set()
    assert not errors, errors
    return output


@contextmanager
def paused_install(monkeypatch, cache, value):
    entered, release = threading.Event(), threading.Event()
    original = product.json.dumps

    def encode(candidate, *args, **kwargs):
        if candidate is value:
            assert cache._lock.locked()  # Existing writer/clone ownership remains.
            entered.set()
            assert release.wait(3), 'test did not release writer'
        return original(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(product.json, 'dumps', encode)
        call = invoke_thread(lambda: cache.install(value))
        try:
            assert entered.wait(2)
            yield release
        finally:
            release.set()
            joined(call)


@pytest.mark.parametrize('name', ['current', 'observation_status'])
def test_readers_complete_while_writer_clone_is_paused(monkeypatch, name):
    cache = BindingCache(HOST, clock=lambda: 100.)
    cache.install(binding())
    with paused_install(monkeypatch, cache, binding(2)) as release:
        call = invoke_thread(lambda: getattr(cache, name)(VM))
        try:
            assert call[1].wait(.5), 'reader waited for unrelated writer clone'
            value = joined(call)[0]
            assert value['version'] == 1 if name == 'current' else value == 'observed'
        finally:
            release.set()
            joined(call)
    assert cache.current(VM)['version'] == 2


@pytest.mark.parametrize('name, local', [('current', 'v'), ('observation_status', 'value')])
def test_captured_snapshot_and_concurrent_invalidation_are_linearizable(name, local):
    cache = BindingCache(HOST, clock=lambda: 100.)
    cache.install(binding())
    captured, resume = threading.Event(), threading.Event()
    code = getattr(BindingCache, name).__code__

    def read():
        def trace(frame, event, arg):
            if frame.f_code is code and event == 'line' and local in frame.f_locals and not captured.is_set():
                captured.set()
                assert resume.wait(3)
            return trace
        sys.settrace(trace)
        try:
            return getattr(cache, name)(VM)
        finally:
            sys.settrace(None)

    reader = invoke_thread(read)
    invalidator = None
    try:
        assert captured.wait(2)
        invalidator = invoke_thread(cache.invalidate)
        assert invalidator[1].wait(.5), 'invalidation waited for a captured reader'
        joined(invalidator)
        assert cache.current(VM) is None
        assert cache.observation_status(VM) == 'unavailable'
    finally:
        resume.set()
        if invalidator is not None:
            joined(invalidator)
        old = joined(reader)[0]
    assert old['version'] == 1 if name == 'current' else old == 'observed'
    cache.install(binding(2))
    assert cache.current(VM)['version'] == 2


def test_writer_clone_and_invalidation_keep_original_serial_order(monkeypatch):
    cache = BindingCache(HOST, clock=lambda: 100.)
    cache.install(binding())
    invalidator = None
    with paused_install(monkeypatch, cache, binding(2)) as release:
        invalidator = invoke_thread(cache.invalidate)
        assert not invalidator[1].wait(.05), 'invalidator bypassed writer ownership'
        release.set()
    joined(invalidator)
    assert cache.current(VM) is None and cache.error == 'BINDING_UNAVAILABLE'
    cache.install(binding(3))
    assert cache.current(VM)['version'] == 3 and cache.error is None


def test_original_input_and_returned_known_maps_cannot_mutate_publication():
    cache = BindingCache(HOST, clock=lambda: 100.)
    value = binding()
    cache.install(value)
    value['vms'][VM]['app_instances']['autonomous'] = uid(99)
    row = cache.current(VM)
    row['intent_revision'] = 99
    row['app_instances']['autonomous'] = uid(98)
    row['app_status']['autonomous'] = 'unavailable'
    row['app_state_models']['autonomous'] = 'stateless'
    current = cache.current(VM)
    assert current['intent_revision'] == 1
    assert current['app_instances']['autonomous'] == uid(41)
    assert current['app_status']['autonomous'] == 'observed'
    assert current['app_state_models']['autonomous'] == 'stateful'


@pytest.mark.parametrize('now, present', [(0., True), (3., True), (3.001, False), (-.001, False)])
def test_explicit_zero_and_original_inclusive_ttl_boundaries(now, present):
    calls = []
    cache = BindingCache(HOST, clock=lambda: calls.append(1) or 0.)
    cache.install(binding(observed=0))
    assert (cache.current(VM, now) is not None) is present
    assert cache.observation_status(VM, now) == ('observed' if present else 'unavailable')
    assert calls == []
    cache.current(VM)
    cache.observation_status(VM)
    assert calls == [1, 1]


def test_row_expiry_status_and_per_app_guards_remain_distinct():
    cache = BindingCache(HOST)
    value = binding(observed=1000)
    value['vms'][VM]['observed_monotonic_ms'] = 0
    cache.install(value)
    assert cache.current(VM, 3.) is not None
    assert cache.current(VM, 3.001) is None
    assert cache.observation_status(VM, 3.001) == 'observed'
    value = binding()
    value['vms'][VM]['app_status']['autonomous'] = 'provisioning'
    cache.install(value)
    assert not cache.accepts(VM, BOOT, uid(41), 100., app='autonomous')
    assert cache.accepts(VM, BOOT, uid(51), 100., app='manual')
    assert not cache.accepts(VM, uid(99), uid(51), 100., app='manual')
    with pytest.raises(ValueError):
        cache.install({**value, 'host_boot_id': uid(99)})
    assert cache.current(VM, 100.) is None and cache.error == 'BINDING_UNAVAILABLE'


def test_clone_exception_keeps_original_failure_and_recovers(monkeypatch):
    cache = BindingCache(HOST, clock=lambda: 100.)
    cache.install(binding())
    next_value = binding(2)
    failure = RuntimeError('local clone fixture failure')
    original = product.json.dumps

    def fail(value, *args, **kwargs):
        if value is next_value:
            raise failure
        return original(value, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(product.json, 'dumps', fail)
        with pytest.raises(RuntimeError) as raised:
            cache.install(next_value)
        assert raised.value is failure and not cache._lock.locked()
        assert cache.current(VM)['version'] == 1 and cache.error is None
    cache.install(next_value)
    assert cache.current(VM)['version'] == 2


def test_common_callback_does_not_wait_on_clone_and_keeps_binding_seq_guards(monkeypatch):
    cache = BindingCache(HOST, clock=lambda: 100.)
    cache.install(binding())
    client = XenovaClient(Identity(source_id='dom0-server', boot_id=BOOT, agent_instance_id=uid(90)))
    client.set_receive_guard(cache.guard((VM,)))
    topic = topics.vm_app_events(VM, 'autonomous')
    received = []
    client.subscribe(topic, received.append, qos=0)

    def deliver(instance, seq):
        body = envelope.build(Identity(vm_id=VM, app_id='autonomous', boot_id=BOOT, agent_instance_id=instance),
            'drive.command', dict(generation=2, v_mps=.1, steer_rad=0), seq)
        client._on_message(client._paho, None, SimpleNamespace(topic=topic, payload=json.dumps(body).encode(), retain=False))

    with paused_install(monkeypatch, cache, binding(2)) as release:
        call = invoke_thread(lambda: deliver(uid(41), 1))
        try:
            assert call[1].wait(.5), 'callback waited on writer clone'
            joined(call)
            assert len(received) == 1
        finally:
            release.set()
            joined(call)
    deliver(uid(41), 999)
    assert len(received) == 1
    deliver(uid(42), 1)
    assert len(received) == 2 and received[-1].envelope['seq'] == 1
    cache.invalidate()
    deliver(uid(42), 2)
    assert len(received) == 2
    cache.install(binding(2))
    deliver(uid(42), 2)
    assert len(received) == 3 and received[-1].envelope['seq'] == 2


def test_repeated_publication_never_mixes_execution_or_known_maps():
    cache = BindingCache(HOST, clock=lambda: 100.)
    cache.install(binding())
    release, stop = threading.Event(), threading.Event()

    def read():
        count = 0
        assert release.wait(2)
        while not stop.is_set() or count < 100:
            row = cache.current(VM)
            if row is not None:
                version = row['version']
                assert row['execution_id'] == uid(20 + version)
                assert row['app_instances'] == {'autonomous': uid(40 + version), 'manual': uid(50 + version)}
                assert row['intent_revision'] == row['lifecycle_generation'] == row['domid'] == version
            count += 1
            time.sleep(0)
        return count

    readers = [invoke_thread(read) for _ in range(2)]
    release.set()
    try:
        for version in range(2, 102):
            if version % 7 == 0:
                cache.invalidate()
            cache.install(binding(version))
            time.sleep(0)
    finally:
        stop.set()
        for reader in readers:
            assert joined(reader)[0] >= 100
