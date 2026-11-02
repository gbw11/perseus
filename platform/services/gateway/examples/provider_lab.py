"""Owned lab: real Gateway actor/Authority/UDS; Xen, apps and output are fixtures.

No broker, physical serial or real /srv state is used. Startup authorization and
short qualification timing are test fixtures, never operating configuration.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import threading
import time
from uuid import uuid4

from xenova_mqtt.drive_binding import BindingCache
from xenova_mqtt.lifecycle import PROFILE
from xenova_gateway.authority import AuthorityStore, GatewayLease
from xenova_gateway.ipc import GatewayServer
from xenova_gateway.machine import Timing, Mode
from xenova_gateway.output_owner import OutputOwner
from xenova_gateway.sink import MockSink
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.warm import WarmDriveMachine

A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
HOST = '11111111-1111-4111-8111-111111111111'
BOOT = '22222222-2222-4222-8222-222222222222'


class Ticket:
    def __init__(self):
        self.envelope = {'message_id': str(uuid4())}
    def status(self, client):
        return 'acknowledged'


class FixtureTransport:
    """Only the MQTT boundary is replaced, not Gateway processing or storage."""
    connected = True
    connections = 1
    def subscribed(self, topic): return True
    def publish_tracked(self, *args, **kwargs): return Ticket()
    def publish(self, *args, **kwargs): pass


class Lab:
    def __init__(self, directory, *, history_policy=None):
        self.root = Path(directory)
        if not self.root.name.startswith('xenova-gateway-lab'):
            raise ValueError('owned xenova-gateway-lab directory required')
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.lease = GatewayLease(self.root/'gateway.lock').acquire()
        self.auth = AuthorityStore(self.root/'authority', HOST, uid=os.geteuid(), mountpoint=None,history_policy=history_policy)
        fresh = not self.auth.marker.exists()
        if fresh:
            self.auth.initialize(A)
            self.auth.update(mode='AUTO', generation=42, phase='DISARMED')
        else:
            self.auth.load()
        self.bindings = BindingCache(HOST, clock=time.monotonic)
        self.executions = {A: str(uuid4()), B: str(uuid4())}
        self.instances = {vm: {app: str(uuid4()) for app in ('autonomous', 'manual')} for vm in (A, B)}
        self.progress = {}; self.checks = {}
        self.ready = {A: True, B: True}
        self.bind()
        self.mock = MockSink(keep=128)
        self.output = OutputOwner(self.mock, command_timeout_s=.1)
        timing = replace(Timing(), ready_hold_s=.01, resume_countdown_s=.02)
        self.runner = VerifiedGatewayRunner(FixtureTransport(), self.output, authority=self.auth,
            bindings=self.bindings, vm_ids=(A, B), readiness_profile='warm-drive-1', timing=timing)
        self.runner._app_filters = []
        self.runner.machine = WarmDriveMachine(vm_ids=(A, B), authority=self.auth,
            bindings=self.bindings, clock=time.monotonic, timing=timing)
        m = self.runner.machine
        self.runner._emit(m.start(time.monotonic()))
        # Only the brand-new lab explicitly exercises the existing resume path.
        # Restart never re-arms a persisted preference.
        if fresh:
            end = time.monotonic()+1
            resumed = False
            while time.monotonic() < end and not m.output_permitted:
                self.feed(); self.runner.run_once(0)
                if not resumed and m._qualified(A, 'autonomous', time.monotonic(), held=True):
                    resumed = m.on_resume(time.monotonic(), 'owned lab',
                        expected_generation=m.generation, message_id=str(uuid4()))
                time.sleep(.005)
            if not m.output_permitted:
                raise RuntimeError('fixture did not arm')
        self.server = GatewayServer(self.runner, self.lease, self.root/'ipc/control.sock', uid=os.geteuid()).start()

    def bind(self):
        now = time.monotonic()
        self.bindings.install(dict(schema_version='1.0-drive-bindings', host_boot_id=HOST,
            observed_monotonic_ms=int(now*1000), expires_monotonic_ms=int(now*1000)+3000,
            vms={vm: dict(status='observed', profile=PROFILE, execution_id=self.executions[vm], boot_id=BOOT,
                app_instance_id=self.instances[vm]['autonomous'], app_instances=self.instances[vm],
                app_state_models={'autonomous':'stateless', 'manual':'stateless'}, domid=i+1,
                xen_unique_id=str(i+1), intent_revision=1, lifecycle_generation=1,
                observed_monotonic_ms=int(now*1000)) for i, vm in enumerate((A, B))}))

    def feed(self):
        self.bind(); m = self.runner.machine; now = time.monotonic()
        for vm in (A, B):
            for app in ('autonomous', 'manual'):
                m.accept_session(vm, BOOT, self.instances[vm][app], now, app=app)
                key = vm, app
                self.checks[key] = self.checks.get(key, 0)+1
                active = vm == m.active_vm and app == m.active_app
                if active: self.progress[key] = self.progress.get(key, 0)+1
                valid = self.ready[vm]
                m.on_lifecycle(app, vm, dict(profile=PROFILE, applied_generation=m.generation,
                    check_seq=str(self.checks[key]), progress_seq=str(self.progress.get(key, 0)),
                    phase='EXECUTING' if active else 'WAITING',
                    readiness=('READY' if active else 'QUALIFIED') if valid else 'NOT_READY',
                    reason=None if valid else 'STATE_PREPARING'), now)
                if active and valid:
                    m.on_command(app, vm, dict(generation=m.generation, v_mps=.1, steer_rad=0), now,
                        dict(message_id=str(uuid4()), vm_id=vm, app_id=app,
                             execution_id=self.executions[vm], boot_id=BOOT,
                             agent_instance_id=self.instances[vm][app], seq=str(self.checks[key]),
                             received_monotonic=now))

    def close(self):
        try: self.server.close()
        finally:
            try: self.runner.shutdown()
            finally: self.lease.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--history-retry-ms',type=int)
    parser.add_argument('--history-retention-ms',type=int)
    args = parser.parse_args(argv)
    from xenova_gateway.history import HistoryPolicy
    policy=None if args.history_retry_ms is None else HistoryPolicy(args.history_retry_ms,args.history_retention_ms)
    lab = Lab(args.directory,history_policy=policy)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: stop.set())
    print(json.dumps({'endpoint':str(lab.server.path), 'uid':os.geteuid(), 'fixture':True}), flush=True)
    try:
        while not stop.is_set():
            lab.feed(); lab.runner.run_once(.002)
    finally:
        lab.close()
        print(json.dumps({'closed':True, 'calls':lab.runner.provider_calls,
                          'io':lab.server.stats, 'watchdog_stops':lab.output.watchdog_stops}), flush=True)


if __name__ == '__main__': main()
