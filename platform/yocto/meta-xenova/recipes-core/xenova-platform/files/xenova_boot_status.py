"""systemd와 부가 진행 기록을 읽는 부팅 표시 계층. VM 제어·준비 판정은 하지 않는다.

로그인 전 표시는 agetty의 issue/reload에 맡긴다. 직접 VT 출력·전환·입력 처리,
Manager/Xen 조회, 실행 잠금, 정책 변경, readiness 소비를 하지 않는다.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

STAGES = (
    ('storage', 'Storage preparation', 'xenova-data-prepare.service'),
    ('mount', 'VM data mount', 'srv-xenova.mount'),
    ('registration', 'Registration verification/resume', 'xenova-first-boot.service'),
    ('xen', 'Xen control checks', 'xenova-xen-ready.service'),
    ('cpu', 'CPU preparation', 'xenova-cpu-prepare.service'),
    ('bridge', 'Internal bridge service', 'xenova-bridge.service'),
    ('network', 'Internal DHCP service', 'xenova-dhcp.service'),
    ('autostart', 'VM autostart batch', 'xenova-vms.service'),
    ('admission', 'Host admission service', 'xenova-host-admission.service'),
    ('management', 'Local management service', 'xenova-dom0-manager.service'),
)
DEPENDENCIES = ('systemd-remount-fs.service', 'xenstored.service', 'xenconsoled.service',
                'xen-init-dom0.service', 'xen-qemu-dom0-disk-backend.service', 'xenova-vm-shutdown.service')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'Result',
              'ExecMainCode', 'ExecMainStatus', 'InvocationID', 'Job', 'Requires',
              'InactiveExitTimestampMonotonic', 'ActiveEnterTimestampMonotonic')
LIMIT = 131072
DELAY_MS = 20000  # 표시 주의 기준. 실행 timeout·고장 판단 기준이 아니다.


def clean(value, limit=96, ascii_only=False):
    text = str(value) if value is not None else '-'
    text = ''.join(' ' if not c.isprintable() else c for c in text)
    # agetty issue의 백슬래시 치환 문법도 사용자 문자열에 허용하지 않는다.
    text = ' '.join(text.replace('\\', '/').split())
    if ascii_only:
        text = text.encode('ascii', 'replace').decode('ascii')
    return text[:limit] + ('...' if len(text) > limit else '')


def read_json(path):
    """symlink·장치·과대 파일을 읽지 않는다. 부재와 읽기 실패를 구분한다."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None, 'not-regular'
            data = stream.read(LIMIT+1)
        if len(data) > LIMIT:
            return None, 'too-large'
        value = json.loads(data)
        return (value, None) if isinstance(value, dict) else (None, 'invalid-object')
    except FileNotFoundError:
        return None, 'missing'
    except (OSError, ValueError, UnicodeError):
        return None, 'unreadable-or-incomplete'


def systemd_units(names, timeout=2):
    """고정 속성만 한 번 조회한다. stdout·subprocess 예산을 제한한다."""
    argv = ['systemctl', 'show', '--no-pager', '--all',
            '--property='+','.join(PROPERTIES), *names]
    try:
        with os.fdopen(os.memfd_create('xenova-boot-query', os.MFD_CLOEXEC), 'w+b') as output:
            child = subprocess.Popen(argv, stdout=output, stderr=subprocess.DEVNULL,
                                     env={**os.environ, 'LC_ALL': 'C'})
            try:
                rc = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                return {}, 'systemd-query-timeout'
            output.seek(0)
            raw = output.read(LIMIT+1)
        if len(raw) > LIMIT:
            return {}, 'systemd-query-too-large'
        rows = {}
        for block in raw.decode('utf-8', errors='replace').split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            if fields.get('Id') in names:
                rows[fields['Id']] = fields
        # systemctl은 일부 not-found unit 때문에 rc!=0이어도 유효한 행을 반환한다.
        if rows and set(rows) != set(names):
            return rows, 'systemd-query-partial'
        return rows, None if rows else ('systemd-query-failed' if rc else 'systemd-query-empty')
    except OSError:
        return {}, 'systemd-query-unavailable'


def progress(runtime, kind, row, host, now):
    value, error = read_json(Path(runtime)/'boot-progress'/(kind+'.json'))
    if error:
        return None, error
    if (value.get('schema_version') != '1.0' or value.get('host_boot_id') != host or
            not row.get('InvocationID') or value.get('invocation_id') != row['InvocationID']):
        return None, 'stale-host-or-invocation'
    updated = value.get('updated_monotonic_ms')
    if type(updated) is not int or not 0 <= updated <= now:
        return None, 'invalid-monotonic-time'
    started = int(row.get('InactiveExitTimestampMonotonic') or 0)//1000
    if updated < started or not isinstance(value.get('batch_id'), str) or not re.fullmatch(r'[0-9a-f-]{36}', value['batch_id']):
        return None, 'stale-batch'
    return value, None


def failed_requirement(unit, rows):
    """수집한 필수 의존성 안에서만 실패 경로를 찾는다. job 원인을 추측하지 않는다."""
    pending = [(unit, [])]
    visited = set()
    while pending:
        current, chain = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for target in rows.get(current, {}).get('Requires', '').split():
            row = rows.get(target, {})
            if row.get('ActiveState') == 'failed':
                return chain+[target]
            if row.get('ActiveState') == 'inactive':
                pending.append((target, chain+[target]))
    return []


def snapshot(rows, host, now, runtime=Path('/run/xenova'), query_error=None):
    """현재 unit invocation과 일치하는 사실만 표시한다. active는 재검증이 아니다."""
    output = {'host_boot_id': host, 'observed_monotonic_ms': now,
              'stages': [], 'vms': [], 'vm_counts': None, 'issues': []}
    if query_error:
        output['issues'].append(query_error)
    for key, label, unit in STAGES:
        row = rows.get(unit, {})
        active = row.get('ActiveState')
        result = row.get('Result') or 'unknown'
        item = {'key': key, 'label': label, 'unit': unit,
                'invocation_id': row.get('InvocationID') or None,
                'state': 'unknown', 'reason': 'unit observation unavailable'}
        if row.get('LoadState') == 'not-found':
            item.update(state='unavailable', reason='unit not installed')
        elif active == 'failed' or (active == 'inactive' and result not in ('success', 'unknown')):
            item.update(state='failed', reason='systemd result='+clean(result),
                        exit_status=clean(row.get('ExecMainStatus')), exit_code=clean(row.get('ExecMainCode')))
        elif active == 'active':
            item.update(state='complete', reason='unit completed' if row.get('SubState') == 'exited' else 'service active; functionality not probed')
        elif active in ('activating', 'reloading', 'deactivating'):
            start = int(row.get('InactiveExitTimestampMonotonic') or 0)//1000
            elapsed = max(0, now-start) if start else None
            item.update(state='delayed' if elapsed is not None and elapsed >= DELAY_MS else 'progress',
                        reason='systemd '+clean(row.get('SubState') or active), elapsed_ms=elapsed)
        elif active == 'inactive':
            failed = failed_requirement(unit, rows)
            item.update(state='deferred' if failed else 'waiting',
                        reason=('required unit failed: '+', '.join(failed)) if failed else 'not started; wait reason unconfirmed')
        if key in ('storage', 'registration', 'cpu', 'autostart'):
            fact, error = progress(runtime, key, row, host, now)
            item['progress_issue'] = error
            if fact:
                item['batch_id'] = fact['batch_id']
                item['last_change_ms_ago'] = now-fact['updated_monotonic_ms']
                item['phase'] = clean(fact.get('phase'))
                if key == 'registration':
                    item['mode'] = clean(fact.get('mode', 'verification-or-resume'))
                if key == 'cpu' and fact.get('phase') == 'complete' and item['state'] == 'complete':
                    item['reason'] = ('original host affinity restored; preparation not ready' if fact.get('restored') else
                                      'policy unconfigured; compatibility mode' if fact.get('configured') is False else
                                      'host preparation verified by this invocation; current affinity not re-probed')
                if key == 'autostart':
                    missing = fact.get('missing_instances', [])
                    if isinstance(missing, list) and missing:
                        item['reason'] = 'required registered instances missing: '+', '.join(clean(v, 40) for v in missing[:8])
                    values = fact.get('vms')
                    if isinstance(values, list) and len(values) <= 1024:
                        for value in values:
                            if not isinstance(value, dict):
                                continue
                            state = value.get('state')
                            if state not in ('waiting', 'network-prepare', 'execution-start', 'verified', 'excluded', 'failed', 'not-started', 'uncertain'):
                                state = 'unknown'
                            output['vms'].append({k: clean(value.get(k), 128) for k in ('vm_id', 'name', 'internal_name', 'code')} |
                                                 {'state': state, 'selected': value.get('selected') if type(value.get('selected')) is bool else None,
                                                  'os_readiness': 'unobserved', 'app_readiness': 'unconnected'})
                        output['vm_counts'] = {s: sum(v['state'] == s for v in output['vms']) for s in
                                               ('verified', 'excluded', 'failed', 'not-started', 'uncertain')}
                        output['vm_counts']['selected'] = (sum(v['selected'] is True for v in output['vms'])
                                                           if all(v['selected'] is not None for v in output['vms']) else None)
                        if item['state'] in ('failed', 'complete'):
                            for value in output['vms']:
                                if value['state'] in ('waiting', 'network-prepare', 'execution-start'):
                                    value['state'] = 'uncertain'
                            output['vm_counts']['uncertain'] = sum(v['state'] == 'uncertain' for v in output['vms'])
        output['stages'].append(item)
    return output


def history_update(value, previous):
    """같은 boot의 과거 실패는 현재 회복과 구분하여 제한된 수만 보존한다."""
    history = list(previous.get('failure_history', [])) if previous and previous.get('host_boot_id') == value['host_boot_id'] else []
    for row in value['stages']:
        if row['state'] == 'failed':
            failure = {k: row.get(k) for k in ('unit', 'invocation_id', 'reason', 'exit_code', 'exit_status')}
            if failure not in history:
                history.append(failure)
    value['failure_history'] = history[-16:]
    return value


def render(value, *, ascii_only=False, width=100, details=False):
    width = max(40, min(width, 160))
    def line(text):
        # 줄당 출력 제한. 전체 식별자/명령은 details의 별도 줄에서 보존한다.
        return clean(text, width-3, ascii_only)
    lines = ['Xenova boot status (recorded facts; not a readiness gate)' if details else 'Xenova startup',
             ('Observed boot=' if details else 'Boot ')+clean(value['host_boot_id'][:8], 8)+' t='+str(value['observed_monotonic_ms']//1000)+'s']
    def registration(mode):
        return {'existing-install-verification':'existing installation',
                'initial-registration-resume':'initial setup / resume'}.get(mode, mode)
    if not details:
        complete = [r['key'] for r in value['stages'] if r['state'] == 'complete']
        if complete:
            lines.append(line('[ OK ] Services: '+', '.join(complete)))
        waiting = [r['key'] for r in value['stages'] if r['state'] == 'waiting']
        if waiting:
            lines.append(line('[WAIT; reason unconfirmed] '+', '.join(waiting)))
    for row in value['stages']:
        if not details and row['state'] in ('complete', 'unavailable', 'waiting'):
            if row.get('mode'):
                lines.append(line('Registration: '+registration(row['mode'])))
            if row['key'] == 'cpu' and row.get('phase') == 'complete' and row['state'] == 'complete':
                reason=row['reason']
                brief=('not configured (compatibility mode)' if reason.startswith('policy unconfigured;') else
                       'host affinity restored; not prepared' if reason.startswith('original host affinity restored;') else
                       'prepared for this boot' if reason.startswith('host preparation verified by this invocation;') else
                       'preparation status unavailable')
                lines.append(line('CPU policy: '+brief))
            continue
        suffix = ''
        if row.get('elapsed_ms') is not None:
            suffix += ' '+str(row['elapsed_ms']//1000)+'s elapsed'
        if row.get('phase') and (details or row['state'] != 'progress'):
            suffix += ' ['+row['phase']+']'
        if row['state'] == 'delayed':
            suffix = ''
        state='START' if not details and row['state']=='progress' else row['state'].upper()
        lines.append(line('['+state+'] '+row['label']+suffix))
        if row['state'] == 'delayed':
            elapsed = str(row['elapsed_ms']//1000)+'s elapsed'
            changed = '; last change '+str(row['last_change_ms_ago']//1000)+'s ago' if row.get('last_change_ms_ago') is not None else '; progress timestamp unavailable'
            lines.append('  '+elapsed+changed)
            if row.get('phase'):
                lines.append(line('  stage: '+row['phase']))
        if row['state'] not in ('complete', 'unavailable', 'progress') or details:
            lines.append(line('  reason: '+row['reason']))
        if row.get('mode'):
            lines.append(line('  '+(row['mode'] if details else registration(row['mode']))))
        if row['state'] == 'failed' or details:
            lines.append('  unit: '+row['unit'])
            lines.append('  journalctl -b -u '+row['unit']+' --no-pager -n 60')
    if value['vm_counts'] is None:
        lines.append('VM selection/results unavailable for current invocation')
    else:
        c = value['vm_counts']
        denominator = c['selected'] if c['selected'] is not None else 'selection unknown'
        lines.append(f"VM execution verified {c['verified']}/{denominator}; {c['excluded']} excluded")
    for vm in value['vms'][:64]:
        if not details:
            if vm['state'] in ('verified', 'excluded'):
                continue
            # 선택 전 모든 등록 VM을 대기 대상으로 나열하면 로그인 안내가 여러 화면에 걸친다.
            # 선택이 끝나지 않았다는 사실은 분모에 남기고 실제 실패/미확정은 아래에서 보존한다.
            if vm.get('selected') is not True and vm['state'] in ('waiting', 'network-prepare', 'execution-start'):
                continue
        state='STARTING' if not details and vm['state']=='execution-start' else vm['state'].upper()
        lines.append(line('  ['+state+'] '+clean(vm['name'], max(8,width-26), ascii_only)+' ('+vm['vm_id'][:8]+')'))
        if vm['code'] != '-':
            lines.append('    code: '+clean(vm['code'],128,ascii_only))
        if vm['state'] in ('failed', 'not-started', 'uncertain') or details:
            lines.extend(('    UUID: '+vm['vm_id'], '    internal: '+clean(vm['internal_name'], 80, ascii_only)))
            lines.append('    journalctl -b -u xenova-vms.service --no-pager -n 60')
            if re.fullmatch(r'[0-9a-f-]{36}', vm['vm_id']):
                lines.append('    xenova-vm get '+vm['vm_id']+' --json')
    if len(value['vms']) > 64:
        lines.append('  More VMs: xenova-boot-status --json')
    lines.append('Guest OS: unobserved. App readiness: unconnected.')
    for error in value.get('issues', []):
        lines.append(line('Observation: '+error))
    for row in value.get('failure_history', [])[-4:]:
        lines.append(line('Failure history (may be recovered): '+str(row.get('unit'))+' '+str(row.get('reason'))))
    lines.append('Details: xenova-boot-status --details')
    if details:
        lines.append('Display does not gate login. agetty pauses refresh on typed input.')
    return '\n'.join(lines)+'\n'


def publish(path, data, mode=0o644):
    """표시기 소유 파일만 원자적으로 교체한다. 원 journal은 변경하지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.resolve() != path.parent:
        raise OSError('linked display directory')
    name = path.with_name('.'+path.name+'-'+os.urandom(8).hex())
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def collect(runtime, reader=systemd_units, clock=time.monotonic, host=None):
    host = host or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    rows, error = reader([u for _, _, u in STAGES]+list(DEPENDENCIES))
    value = snapshot(rows, host, int(clock()*1000), runtime, error)
    old, _ = read_json(Path(runtime)/'boot-display.json')
    return history_update(value, old)


def refresh(runtime, issue=Path('/run/issue.d/90-xenova.issue'), reload=Path('/run/agetty.reload'), **kwargs):
    previous, _ = read_json(Path(runtime)/'boot-display.json')
    value = collect(runtime, **kwargs)
    publish(Path(runtime)/'boot-display.json', json.dumps(value, ensure_ascii=True)+'\n')
    # 상태 변화에만 안내를 갱신한다. 지연의 경과시간은 30초 간격으로 다시 표시한다.
    text = render(value, ascii_only=True, width=80)
    prior = issue.read_text() if issue.is_file() and not issue.is_symlink() else None
    def signature(v):
        if not v:
            return None
        stable = {k: v.get(k) for k in ('host_boot_id', 'vms', 'vm_counts', 'issues', 'failure_history')}
        stable['stages'] = [{k: x for k, x in row.items() if k not in ('elapsed_ms', 'last_change_ms_ago')} |
                            ({'delay_bucket': row.get('elapsed_ms', 0)//30000} if row['state'] == 'delayed' else {})
                            for row in v['stages']]
        return stable
    if text != prior and (prior is None or signature(previous) != signature(value)):
        publish(issue, text)
        fd = os.open(reload, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('invalid agetty reload trigger')
            os.utime(fd, None)
        finally:
            os.close(fd)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='display snapshot JSON (not a Manager envelope)')
    parser.add_argument('--details', action='store_true', help='include unit and VM identifiers')
    parser.add_argument('--ascii', action='store_true', help='use ASCII-safe names for text consoles')
    parser.add_argument('--watch', action='store_true', help='service mode: update agetty issue until boot settles or 600s')
    args = parser.parse_args(argv)
    runtime = Path('/run/xenova')
    if args.watch:
        end = time.monotonic()+600
        while time.monotonic() < end:
            value = refresh(runtime)
            # 표시 관측의 종료이며 VM/app의 준비를 기다리거나 성공으로 바꾸지 않는다.
            if all(s['state'] not in ('waiting', 'progress', 'delayed') for s in value['stages']):
                break
            time.sleep(5)
        return 0
    value = collect(runtime)
    print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else
          render(value, ascii_only=args.ascii, details=args.details), end='\n' if args.json else '')
    return 1 if value['issues'] else 0


def entrypoint():
    try:
        return main()
    except (OSError, ValueError, TypeError) as error:
        print('xenova-boot-status: observation/display unavailable ('+type(error).__name__+')', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
