"""고정 service 안에서 실제 OAK·worker 장애를 순차 주입하는 명시 실기 harness.

기본 test discovery로 실행하지 않는다. production runtime을 mock하지 않고 실제
S event/timer에 시험용 관찰과 자식 signal만 추가한다. 별도 thread는 만들지 않는다.
최종 결과 파일은 S가 살아 있을 때 발행한다. 외부 시험은 service의 실제 종료를
기다린 뒤 별도 observer로 S/tracker birth와 known 이름을 검사해야 한다.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
from unittest.mock import patch

from xenova_vehicle.runtime.resident_runtime import _Runtime, run_runtime
from xenova_vehicle.runtime.resident_storage import StorageProfile
from xenova_vehicle.runtime.service_scope import SystemdServiceScope
from xenova_vehicle.yolop_profile import load_yolop_profile


def _bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def _sample(runtime, event):
    processes = {}
    targets = dict(S=os.getpid(), **{role: host.pid for role, host in runtime.workers.items()})
    for role, pid in targets.items():
        try:
            status = Path('/proc/' + str(pid) + '/status').read_bytes().splitlines()
            value = {row.split(b':', 1)[0].decode(): row.split(b':', 1)[1].strip().decode()
                     for row in status if row.startswith((b'VmRSS:', b'Threads:'))}
            value['fd_count'] = len(os.listdir('/proc/' + str(pid) + '/fd'))
            value['pid'] = pid
            processes[role] = value
        except FileNotFoundError:
            processes[role] = dict(pid=pid, outcome='EXITED_DURING_SAMPLE')
    manifest = runtime.manifest
    return dict(event=event, sampled_j_us=str(time.monotonic_ns() // 1000), processes=processes,
        service_instance=runtime.service, resource_epoch=runtime.epoch,
        raw_arena=None if runtime.scope is None else runtime.scope.arena_id,
        raw_binding=None if runtime.binding is None else runtime.binding.sha256,
        clock_epoch=runtime.clock_epoch,
        tokens={} if manifest is None else {r.slot: r.name for r in manifest.known_resources if r.slot.endswith(('READY', 'WRITABLE'))},
        retired_raw=None if manifest is None else manifest.resource('RAW_RETIRED').state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--control', required=True, type=Path)
    args = parser.parse_args()
    mode = json.loads(args.control.read_text(encoding='ascii'))['mode']
    if mode not in ('RECOVERY', 'NORMAL', 'FORCE_S', 'AFTER_FORCE_S'):
        raise ValueError('unsupported explicitly armed hardware scenario')
    state = dict(stage=0, due_us=None, samples=[], signals=[], spontaneous_recoveries=[])
    original_event, original_timers = _Runtime.event, _Runtime._timers

    def event(runtime, kind, **fields):
        original_event(runtime, kind, **fields)
        now = time.monotonic_ns() // 1000
        if kind in ('RESIDENT_LOCAL_READY', 'RECOVERY_COMPLETE', 'RECOVERY_EPISODE_RESET'):
            runtime.output.joinpath('hardware-live.json').write_bytes(_bytes(dict(
                event=kind, service_instance=runtime.service, observed_j_us=str(now))))
        if kind == 'RESIDENT_LOCAL_READY':
            state['samples'].append(_sample(runtime, kind))
            state['due_us'] = now + 2_000_000
        elif kind == 'RECOVERY_COMPLETE':
            state['samples'].append(_sample(runtime, kind))
            receipt = runtime.recovery.receipts[-1]
            injected = state['signals'][-1] if len(state['signals']) > state['stage'] else None
            if injected is None:
                state['spontaneous_recoveries'].append(receipt['operation_id'])
                return
            expected_kind = {'M': 'M', 'A': 'AM', 'D': 'D'}[injected['role']]
            old = receipt['old'].get(injected['role'])
            if receipt['kind'] != expected_kind or old is None or old['pid'] != injected['pid']:
                runtime.stop('hardware_injected_recovery_mismatch', RuntimeError('recovery does not match the injected owner'))
                return
            state['stage'] += 1
            state['due_us'] = now + 2_000_000 if state['stage'] < 3 else None
        elif kind == 'RECOVERY_EPISODE_RESET' and mode == 'RECOVERY' and state['stage'] == 3:
            state['samples'].append(_sample(runtime, kind))
            runtime.stop('hardware_scenario_completed')

    def timers(runtime):
        original_timers(runtime)
        now = time.monotonic_ns() // 1000
        if runtime.stopping or runtime.recovery.active is not None or state['due_us'] is None or now < state['due_us']:
            return
        if mode == 'FORCE_S':
            # 시험 다음 invocation은 새 권한을 받지 않고 실제 old-resource guard부터 통과한다.
            with args.control.open('wb') as target:
                target.write(_bytes(dict(mode='AFTER_FORCE_S', forced_service=runtime.service,
                                         forced_pid=os.getpid(), forced_j_us=str(now))))
                target.flush()
                os.fsync(target.fileno())
            os.kill(os.getpid(), signal.SIGKILL)
        if mode != 'RECOVERY' or state['stage'] >= 3:
            state['due_us'] = None
            return
        role, number = (('M', signal.SIGKILL), ('A', signal.SIGSTOP), ('D', signal.SIGKILL))[state['stage']]
        host = runtime.workers[role]
        if host.reaped or host.exited_us is not None:
            raise RuntimeError('scenario target exited before the original injected signal')
        state['signals'].append(dict(role=role, pid=host.pid, worker_instance=host.peer.worker_instance,
                                     signal=number, issued_j_us=str(now)))
        os.kill(host.pid, number)
        state['due_us'] = None

    profile = load_yolop_profile(args.profile)
    storage = StorageProfile(str(args.database.absolute()), 'EXISTING_ONLY')
    with patch.object(_Runtime, 'event', event), patch.object(_Runtime, '_timers', timers):
        result = run_runtime(profile, args.state_dir, storage, args.output_root,
            duration_s=None if mode in ('RECOVERY', 'NORMAL', 'FORCE_S') else 3,
            service_scope=SystemdServiceScope(), output_root=True)
    args.output_root.joinpath(result['service_instance'], 'hardware-scenario.json').write_bytes(
        _bytes(dict(mode=mode, observations=state, complete=result['complete'], runtime_exit=result['exit_code'])))
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False), flush=True)
    if result.get('requires_service_reap'):
        os._exit(result['exit_code'])
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
