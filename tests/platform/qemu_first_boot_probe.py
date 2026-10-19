#!/usr/bin/env python3
"""Opt-in QEMU integration probe of existing Linux provisioning, not agent acceptance."""
import argparse
import getpass
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

LAB = Path('/home/ssafy/xenova-qemu-28')
SSH = ['ssh', '-F', str(LAB / 'keys/ssh_config'), '-o', 'ConnectTimeout=8']
CODE = r'''import hashlib,json,pathlib,socket,subprocess
p=pathlib.Path
def read(n): return p(n).read_text().strip()
def cmd(*a): return subprocess.check_output(a,text=True).strip()
print(json.dumps({'hostname':socket.gethostname(),'instance_id':read('/etc/xenova/instance-id'),
'machine_id':read('/etc/machine-id'),'boot_id':read('/proc/sys/kernel/random/boot_id'),
'ssh_key':hashlib.sha256(p('/etc/ssh/ssh_host_ed25519_key.pub').read_bytes()).hexdigest(),
'addresses':json.loads(cmd('ip','-j','-4','addr')),'ssh':cmd('systemctl','is-active','ssh'),
'identity_complete_exists':p('/etc/xenova/identity-complete.json').exists(),
'deployment_seed_exists':p('/etc/xenova/deployment-seed.json').exists(),
'marker':p('/root/jira27-probe').read_text() if p('/root/jira27-probe').exists() else None}))
'''


def dom0(code, timeout=1800):
    return subprocess.check_output(SSH + ['-o', 'BatchMode=yes', 'xenova-qemu-dom0',
        'python3 -c ' + shlex.quote(code)], text=True, timeout=timeout)


def management(action, spec, key=None):
    code = ("import sys,json;sys.path.insert(0,'/usr/lib/xenova');"
            "from xenova_management import Manager;"
            f"print(json.dumps(Manager().call({action!r},{spec!r},execute=True,idempotency_key={key!r})))")
    deadline = time.monotonic() + 1800
    while True:
        result = json.loads(dom0(code))
        if result['ok']:
            return result
        error = result.get('error') or {}
        if (action == 'create' and key and error.get('code') == 'OPERATION_IN_PROGRESS'
                and error.get('retryable') and time.monotonic() < deadline):
            print('Creation is still owned by the previous request; waiting for its lock...', flush=True)
            time.sleep(30)
            continue
        raise RuntimeError(json.dumps(result))


def records():
    return json.loads(dom0("import sys,json;sys.path.insert(0,'/usr/lib/xenova');"
                           "from xenova_vm import Platform;print(json.dumps(Platform().records()))"))


def guest(target, password, mark=False):
    import pexpect
    code = CODE
    if mark:
        code = ("from pathlib import Path;p=Path('/root/jira27-probe');"
                "p.write_text('preserve across reboot\\n') if not p.exists() else None\n") + code
    child = pexpect.spawn(SSH[0], SSH[1:] + ['-J', 'xenova-qemu-dom0',
        '-o', 'StrictHostKeyChecking=accept-new', '-o', 'PreferredAuthentications=password',
        '-o', 'PubkeyAuthentication=no', 'root@' + target, 'python3 -c ' + shlex.quote(code)],
        encoding='utf-8', timeout=35, echo=False)
    try:
        if child.expect([r'[Pp]assword:', pexpect.EOF]) != 0:
            raise RuntimeError('SSH not ready: ' + child.before)
        child.sendline(password)
        if child.expect([pexpect.EOF, r'[Pp]assword:']) != 0:
            raise PermissionError('documented credential rejected; no retry')
        output = child.before
        child.close()
        if child.exitstatus != 0:
            raise RuntimeError(output)
        return next(json.loads(line) for line in output.splitlines() if line.startswith('{'))
    finally:
        child.close()


def wait_guest(target, password, previous_boot=None, mark=False):
    import pexpect
    deadline = time.monotonic() + 180
    while True:
        try:
            value = guest(target, password, mark)
            if previous_boot is None or value['boot_id'] != previous_boot:
                return value
        except (RuntimeError, StopIteration, pexpect.ExceptionPexpect):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError('guest SSH/new boot ID not observed within 180s')
        time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume-token', help='resume an owned failed run with its saved lab evidence')
    parser.add_argument('--address', default='192.168.77.30', help='unused lab DHCP reservation in .2..99')
    parser.add_argument('--observe-after-reboot', type=Path,
                        help='observe an already-issued reboot from a failed owned evidence file; no second reboot')
    args = parser.parse_args()
    if not args.execute:
        print('Plan: create a new Debian probe (.30), inspect, reboot, compare, stop and retire. '
              'Existing three VMs stay unchanged. Agent and seed path are outside this probe.')
        return
    if sys.platform != 'linux' or not (LAB / 'keys/ssh_config').is_file():
        raise ValueError('run in the existing WSL QEMU lab')
    address = args.address
    if not re.fullmatch(r'192\.168\.77\.(?:[2-9]|[1-9][0-9])', address):
        raise ValueError('address must be in the lab .2..99 subnet')
    dom0("import pathlib,socket;assert socket.gethostname()=='xenova-qemu-dom0';"
         "assert pathlib.Path('/usr/lib/xenova/qemu_lab_ready.py').is_file()")
    dom0("import shutil;required=['cp','sha256sum','tune2fs','e2fsck','losetup','mount',"
         "'umount','chroot','ssh-keygen'];missing=[n for n in required if not shutil.which(n)];"
         "assert not missing, 'QEMU dom0 dependency missing: '+repr(missing)")
    token = args.resume_token or uuid.uuid4().hex[:10]
    if not re.fullmatch('[0-9a-f]{10}', token):
        raise ValueError('invalid probe token')
    name = 'firstboot-' + token
    prior = None
    if args.resume_token:
        prior_path = args.observe_after_reboot or LAB / 'logs' / ('jira27-' + token + '.json')
        if prior_path.parent.resolve() != LAB / 'logs' or prior_path.is_symlink():
            raise ValueError('linked evidence refused')
        prior = json.loads(prior_path.read_text())
        if (prior.get('name') != name or prior.get('passed') is not False
                or prior.get('address', '192.168.77.30') != address):
            raise ValueError('resume requires a failed owned probe record')
    elif args.observe_after_reboot:
        raise ValueError('observation requires --resume-token and owned evidence')
    before = records()
    probe_before = before.get(name)
    if prior:
        before.pop(name, None)
    if args.observe_after_reboot and (not probe_before or not prior.get('first')
            or probe_before['uuid'] != prior.get('record', {}).get('uuid')):
        raise ValueError('observation requires the same registered UUID and first boot snapshot')
    if set(before) != {'debian-dev', 'zephyr-dev', 'zephyr-backup'}:
        raise ValueError('unexpected registration set: resolve before testing')
    if any(r.get('ipv4') == address for r in before.values()):
        raise ValueError('probe IP already allocated')
    password = getpass.getpass('Documented lab guest password (memory only): ')
    result = {'scope': 'generic ARM QEMU; normal instance offline provisioning + DHCP + reboot',
              'name': name, 'address': address, 'created_at_unix': time.time(), 'agent': 'NOT_TESTED',
              'factory_seed_first_boot': 'NOT_TESTED', 'passed': False}
    suffix = '-resume-' + uuid.uuid4().hex[:6] if prior else ''
    output = LAB / 'logs' / ('jira27-' + token + suffix + '.json')
    identity = None
    try:
        print('Checking existing Debian identity...', flush=True)
        result['baseline_guest'] = guest('192.168.77.20', password)
        if not args.observe_after_reboot:
            print('Creating isolated probe ' + name + ' (TCG image verification can take several minutes)...', flush=True)
            management('create', {'name': name, 'template_id': 'debian-13-arm64',
                'template_version': '1.0.0', 'vcpus': 1, 'memory_mib': 512,
                'ipv4': address}, 'jira27-' + token)
        record = records()[name]
        identity = record['uuid']
        result['record'] = record
        if args.observe_after_reboot:
            result['source_evidence'] = str(args.observe_after_reboot)
            result['control_plane_reboot_error'] = prior.get('error')
            first = result['first'] = prior['first']
        else:
            print('Starting probe...', flush=True)
            management('start', {'vm_id': identity})
            first = result['first'] = wait_guest(address, password, mark=True)
            print('First boot observed; rebooting probe...', flush=True)
            management('reboot', {'vm_id': identity, 'force': False})
        second = result['after_reboot'] = wait_guest(address, password, first['boot_id'])
        result['checks'] = {
            'hostname': first['hostname'] == second['hostname'] == name,
            'vm_uuid': first['instance_id'] == second['instance_id'] == identity,
            'dhcp': all(address in [a['local'] for i in v['addresses'] for a in i['addr_info']]
                        for v in (first, second)),
            'reboot_observed': first['boot_id'] != second['boot_id'],
            'persistent_user_file': first['marker'] == second['marker'] == 'preserve across reboot\n',
            'ssh_active': first['ssh'] == second['ssh'] == 'active',
        }
        for field in ('machine_id', 'instance_id', 'ssh_key'):
            result['checks']['preserve_' + field] = first[field] == second[field]
            result['checks']['independent_' + field] = first[field] != result['baseline_guest'][field]
        result['provisioning_checks_passed'] = all(result['checks'].values())
        result['passed'] = result['provisioning_checks_passed'] and not result.get('control_plane_reboot_error')
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if identity:
            try:
                result['stop'] = management('stop', {'vm_id': identity, 'force': False})
                result['retire'] = management('retire', {'vm_id': identity}, 'jira27-retire-' + token)
            except Exception as error:
                result['cleanup_error'] = str(error)
                result['passed'] = False
        try:
            result['original_records_unchanged'] = records() == before
            result['passed'] = result['passed'] and result['original_records_unchanged']
        except Exception as error:
            result['final_state_error'] = str(error)
            result['passed'] = False
        with output.open('x') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({'passed': result['passed'], 'evidence': str(output),
                          'error': result.get('error'), 'checks': result.get('checks')}, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
