"""Prepare board-local Worker and non-actuating factory Gateway settings."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def install_if_missing(path, content):
    """Publish a private file without replacing an operator's configuration."""
    from xenova_broker import sync_directory
    from xenova_provisioning import private

    fd, name = tempfile.mkstemp(prefix='.recovery-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(name)
    private(path)
    sync_directory(path.parent)


def factory_drive(data, runtime):
    from xenova_guest_profile import validate
    from xenova_provisioning import credential_file, private
    from xenova_vm import load_records, guest_kind

    state_path = data/'.first-install-state.json'
    if not state_path.exists():
        return None
    private(state_path)
    state = json.loads(state_path.read_text())
    if state.get('schema') != 1 or state.get('phase') != 'complete':
        raise ValueError('completed factory registration required')
    records = load_records(data, validate_assets=False)
    identities = state['identities']
    roles = ('zephyr-drive-a', 'zephyr-drive-b')
    if any(role not in identities or role not in records for role in roles):
        return None

    vm_ids = []
    credentials = {}
    models = None
    for role in roles:
        record = records[role]
        identity = record['uuid']
        if identity != identities[role]['uuid'] or guest_kind(record) != 'zephyr':
            raise ValueError('factory drive role identity changed')
        if 'provisioning_profile' not in record:
            return None
        profile = validate(record['provisioning_profile'], guest_kind='zephyr')
        apps = profile['apps']
        if ('drive' not in profile['channels'] or profile['signals']['app_heartbeat'] is not True
                or set(apps) != {'autonomous', 'manual'}
                or any(app['standby_progress'] != 'idle' or app['model'] is None for app in apps.values())):
            return None
        selected = {name: app['model'] for name, app in apps.items()}
        if models is not None and selected != models:
            raise ValueError('factory drive application models differ')
        models = selected
        vm_ids.append(identity)
        credentials[identity] = {channel: str(credential_file(data, identity, channel))
                                 for channel in ('drive', 'telemetry')}
    if len(set(vm_ids)) != 2:
        raise ValueError('factory drive roles must be distinct')
    return {'schema_version': '1.0-drive-rejoin-config', 'readiness_profile': 'warm-drive-1',
            'app_state_models': models, 'vm_ids': vm_ids, 'credentials': credentials,
            'binding_file': str(runtime/'drive-bindings.json'),
            'authority_file': str(data/'drive-control/control.json'),
            'receipt_dir': str(data/'drive-rejoin')}


def configure(data, runtime, etc):
    from xenova_broker import inspect, render_environment, service_environment
    from xenova_provisioning import private, put
    from xenova_vm import storage_check
    from xenova_runtime import ensure_runtime

    storage_check(data)
    ensure_runtime(runtime)
    for directory in (runtime, etc):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('private configuration requires a root-controlled directory')
    worker_env = etc/'recovery-worker.env'
    accounts = None
    if os.path.lexists(worker_env):
        private(worker_env)
    else:
        accounts = inspect(data=data, runtime=True)
        install_if_missing(worker_env, render_environment(service_environment(accounts, 'dom0-server')))

    # These are generated only from the current registry. A removed role or an
    # operator override must not make old factory configuration reappear.
    worker_drive = runtime/'recovery-drive.env'
    gateway_env = runtime/'gateway-factory.env'
    drive_path = runtime/'drive-rejoin-config.json'
    operator_gateway = os.path.lexists(etc/'gateway.env')
    drive = None if operator_gateway else factory_drive(data, runtime)
    if drive is None:
        for path in (worker_drive, gateway_env, drive_path):
            path.unlink(missing_ok=True)
        return None
    put(drive_path, json.dumps(drive, sort_keys=True)+'\n')
    put(worker_drive, render_environment({'XENOVA_RECOVERY_DRIVE_CONFIG': str(drive_path)}))

    accounts = accounts or inspect(data=data, runtime=True)
    settings = service_environment(accounts, 'gateway')
    settings.update(XENOVA_GATEWAY_PROFILE='warm', XENOVA_GATEWAY_SINK='mock',
                    XENOVA_GATEWAY_VM_A=drive['vm_ids'][0], XENOVA_GATEWAY_VM_B=drive['vm_ids'][1],
                    XENOVA_GATEWAY_BINDING_FILE=drive['binding_file'],
                    XENOVA_GATEWAY_AUTHORITY_DIR=str(data/'drive-control'))
    put(gateway_env, render_environment(settings))
    return settings


def start_factory_gateway(settings, data, runtime, etc):
    """Only fresh registration may initialize missing Authority automatically."""
    if (os.path.lexists(etc/'gateway.env')
            or not Path('/usr/lib/systemd/system/xenova-gateway.service').is_file()):
        return
    authority = data/'drive-control'
    if not os.path.lexists(authority):
        from xenova_provisioning import private
        marker = runtime/'install-ready.json'
        private(marker)
        ready = json.loads(marker.read_text())
        state = json.loads((data/'.first-install-state.json').read_text())
        if (ready.get('fresh_registration') is not True or ready.get('factory_id') != state['factory_id']
                or ready.get('dom0_machine_id') != state['dom0_machine_id']):
            print('factory Gateway awaits explicit first Authority initialization')
            return
        # Consume this boot's first-registration evidence before launching the
        # existing initializer. A missing directory after this is an operator case.
        attempt = runtime/'gateway-first-init-attempt'
        fd = os.open(attempt, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
        os.close(fd)
        subprocess.run([sys.executable, '-m', 'xenova_gateway', '--initialize-control-state'],
                       env={**os.environ, **settings}, check=True, timeout=8)
    subprocess.run(['/usr/bin/systemctl', '--no-block', 'start', 'xenova-gateway.service'],
                   check=True, timeout=5)


def main():
    sys.path.insert(0, '/usr/lib/xenova')
    data, runtime, etc = Path('/srv/xenova'), Path('/run/xenova'), Path('/etc/xenova')
    try:
        settings = configure(data, runtime, etc)
        if settings is not None:
            start_factory_gateway(settings, data, runtime, etc)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'recovery configuration unavailable: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
