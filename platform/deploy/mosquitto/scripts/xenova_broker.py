#!/usr/bin/python3
"""dom0 MQTT 계정의 단일 작성자. VM 제어·소비자 설정 적용·암호 회전은 하지 않는다.

병우님의 dom0-broker.sh 계정 운영 진입점을 영구 저장·세대별 게시에 연결한다.
디스크 게시, SIGHUP 요청, 실제 클라이언트 인증은 서로 다른 완료 경계다.
"""
import argparse
from contextlib import contextmanager
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import uuid

DATA = Path('/srv/xenova')
DEVICE = Path('/run/xenova/data-device')
POLICY = Path('/usr/share/xenova/mqtt/policy.json')
if not POLICY.exists():
    POLICY = Path(__file__).resolve().parents[1] / 'etc/policy.json'
NAMES = re.compile(r'[A-Za-z0-9._-]{1,96}\Z')
GENERATION = re.compile(r'generations/[0-9a-f]{32}\Z')


class SetupError(ValueError):
    """비밀값과 외부 명령의 원문 출력을 포함하지 않는 진단."""


class CredentialRevoked(SetupError):
    """명시 폐기된 계정은 자동 공급이 복원할 수 없다."""


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SetupError('duplicate state key')
        value[key] = item
    return value


def read_json(path):
    value = json.loads(path.read_text(), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise SetupError('state JSON must be an object')
    return value


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, content, mode=0o600, gid=0):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), mode)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def private_file(path, mode, gid=0):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != gid or
            stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1):
        raise SetupError('unexpected MQTT file ownership or permissions')


def import_source(source, gid, rules):
    """기존 dom0-broker.sh의 해시·평문을 읽기만 한다. 원본을 이동·변경하지 않는다."""
    source = Path(source)
    if not source.is_absolute():
        raise SetupError('absolute legacy source required')
    info = source.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SetupError('unsafe legacy directory')
    private_file(source / 'passwd.credentials', 0o600)
    # The older installer makes passwd mosquitto-owned 0600 for SIGHUP reads.
    info = (source / 'passwd').lstat()
    try:
        broker_uid = pwd.getpwnam('mosquitto').pw_uid
    except KeyError:
        broker_uid = 0
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or
            info.st_uid not in (0, broker_uid)):
        raise SetupError('unsafe legacy password file')
    hashes = password_lines((source / 'passwd').read_text())
    secrets = {}
    for line in (source / 'passwd.credentials').read_text().splitlines():
        name, sep, value = line.partition(':')
        if not sep or name in secrets or not value or any(c in value for c in '\r\n'):
            raise SetupError('invalid legacy credential file')
        secrets[name] = value
    if set(secrets) != set(hashes):
        raise SetupError('legacy secret/hash set differs; preserve source rather than issue replacement passwords')
    records = {name: profile(name, rules) for name in hashes}
    return {'revision': 0, 'legacy': True, 'profiles': records, 'secrets': secrets, 'hashes': hashes,
            'manifest': {}, 'path': source}


def directory(path, gid):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != gid or
            stat.S_IMODE(info.st_mode) != 0o750):
        raise SetupError('unexpected MQTT directory ownership or permissions')


def mkdir(path, gid):
    path.mkdir(mode=0o750)
    os.chown(path, 0, gid)
    os.chmod(path, 0o750)


def storage(data=DATA, device=DEVICE, mountinfo=Path('/proc/self/mountinfo')):
    """data-prepare가 확정한 writable ext4와 대조하고 rootfs fallback을 거절한다."""
    info, expected = data.lstat(), device.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or
            not stat.S_ISBLK(expected.st_mode) or info.st_dev != expected.st_rdev):
        raise SetupError('verified persistent data device is unavailable')
    rows = [line.split(' - ', 1) for line in mountinfo.read_text().splitlines()
            if line.split(' - ', 1)[0].split()[4] == str(data)]
    if len(rows) != 1:
        raise SetupError('persistent data mount unavailable; no rootfs fallback')
    left, right = (part.split() for part in rows[0])
    if (right[0] != 'ext4' or 'rw' not in left[5].split(',') or
            'rw' not in right[2].split(',') or
            left[2] != f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'):
        raise SetupError('persistent mount changed or is not writable ext4')
    return data / 'mqtt'


def vm_id(value):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise SetupError('canonical nonzero VM UUID required') from None
    if str(parsed) != value or not parsed.int:
        raise SetupError('canonical nonzero VM UUID required')
    return value


def vm_ids(values):
    result = [vm_id(value) for value in values]
    if len(result) != len(set(result)):
        raise SetupError('duplicate VM UUID')
    return sorted(result)


def policy(path=POLICY):
    value = read_json(path)
    if (set(value) != {'schema_version', 'services', 'vm', 'telemetry', 'development_vm_ids'} or
            value['schema_version'] != 1):
        raise SetupError('unsupported account policy')
    for name, entry in value['services'].items():
        if not NAMES.fullmatch(name) or set(entry) != {'default', 'read', 'write'} or type(entry['default']) is not bool:
            raise SetupError('invalid service policy')
    for entry in [*value['services'].values(), value['vm'], value['telemetry']]:
        for access in ('read', 'write'):
            if not isinstance(entry[access], list) or any(not isinstance(t, str) or not t.startswith('xenova/') or
                    any(c.isspace() or ord(c) < 32 for c in t) for t in entry[access]):
                raise SetupError('invalid policy topic')
    vm_ids(value['development_vm_ids'])
    return value


def profile(name, rules):
    if name in rules['services']:
        return 'service'
    if name.startswith('telemetry-'):
        vm_id(name[10:])
        return 'telemetry'
    vm_id(name)
    return 'vm'


def default_accounts(rules, vms=()):
    return {**{name: 'service' for name, entry in rules['services'].items() if entry['default']},
            **{value: 'vm' for value in vms}, **{'telemetry-' + value: 'telemetry' for value in vms}}


def acl(records, rules):
    """전역 pattern 대신 등록된 계정별로 허용 토픽을 펼친다."""
    lines = ['# Generated from policy.json; do not edit account generations.']
    for name, kind in sorted(records.items()):
        if profile(name, rules) != kind:
            raise SetupError('account/profile mismatch')
        entry = rules['services'][name] if kind == 'service' else rules[kind]
        value = name[10:] if kind == 'telemetry' else name
        lines += ['', 'user ' + name]
        for access in ('read', 'write'):
            lines += ['topic ' + access + ' ' + topic.replace('{vm_id}', value) for topic in entry[access]]
    return '\n'.join(lines) + '\n'


def grants(text):
    users, current = {}, None
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        if line.startswith('user '):
            current = line[5:]
            if current in users:
                raise SetupError('duplicate ACL user')
            users[current] = set()
        elif current is not None and line.startswith(('topic read ', 'topic write ')):
            users[current].add(line)
        else:
            raise SetupError('unscoped or unsupported ACL rule')
    return users


def password_lines(text):
    result = {}
    for line in text.splitlines():
        name, sep, hashed = line.partition(':')
        if not sep or not NAMES.fullmatch(name) or name in result or not hashed.startswith(('$7$', '$6$')):
            raise SetupError('invalid password file; never treat it as plaintext')
        result[name] = hashed
    return result


def check_bundle(path, gid, rules, *, legacy=False, allow_policy_change=False):
    directory(path, gid)
    for name, mode, group in [('manifest.json', 0o600, 0), ('credentials.json', 0o600, 0),
                              ('passwd', 0o640, gid), ('acl', 0o640, gid)]:
        private_file(path / name, mode, group)
    manifest = read_json(path / 'manifest.json')
    names = {'credentials.json', 'passwd', 'acl'}
    if not legacy:
        names.add('config.d/accounts.conf')
        directory(path / 'config.d', gid)
        private_file(path / 'config.d/accounts.conf', 0o640, gid)
    if (not isinstance(manifest.get('sha256'), dict) or set(manifest['sha256']) != names or
            any(sha(path / name) != digest for name, digest in manifest['sha256'].items())):
        raise SetupError('MQTT bundle changed; preserve it before investigation')
    if legacy:
        if manifest.get('schema_version') != 1:
            raise SetupError('unsupported legacy manifest')
        vms = vm_ids(manifest['vm_ids'])
        records = {name: profile(name, rules) for name in manifest['accounts']}
        if (len(records) != len(manifest['accounts']) or
                set(n for n, kind in records.items() if kind == 'vm') != set(vms) or
                set(n[10:] for n, kind in records.items() if kind == 'telemetry') != set(vms)):
            raise SetupError('legacy UUID manifest mismatch')
    else:
        if manifest.get('schema_version') != 2 or type(manifest.get('revision')) is not int or manifest['revision'] < 1:
            raise SetupError('invalid account generation')
        records = manifest['profiles']
        revoked=manifest.get('revoked_accounts',[])
        if (not isinstance(revoked,list) or len(set(revoked))!=len(revoked)
                or any(not isinstance(n,str) or not NAMES.fullmatch(n) or n in records for n in revoked)):
            raise SetupError('invalid revoked account fence')
        if not isinstance(records, dict) or any(not isinstance(n, str) or kind not in ('service', 'vm', 'telemetry')
                                                for n, kind in records.items()):
            raise SetupError('invalid account profile manifest')
        if (not allow_policy_change and manifest['policy_sha256'] !=
                hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()):
            raise SetupError('policy update requires explicit account publication')
    secret = read_json(path / 'credentials.json')
    hashes = password_lines((path / 'passwd').read_text())
    if (secret.get('schema_version') != 1 or not isinstance(secret.get('accounts'), dict) or
            set(secret['accounts']) != set(records) or set(hashes) != set(records) or
            any(not isinstance(v, str) or not v or len(v) > 128 or any(ord(c) < 33 or ord(c) > 126 or c in '\\"' for c in v)
                for v in secret['accounts'].values())):
        raise SetupError('credential/hash/account set mismatch')
    policy_matches = legacy or manifest['policy_sha256'] == hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()
    observed_grants = grants((path / 'acl').read_text())
    if (policy_matches and observed_grants != grants(acl(records, rules))) or set(observed_grants) != set(records):
        raise SetupError('ACL/profile mismatch')
    return {'manifest': manifest, 'profiles': records, 'secrets': secret['accounts'], 'hashes': hashes,
            'revision': 0 if legacy else manifest['revision'], 'path': path, 'legacy': legacy,
            'policy_matches': policy_matches}


def load(target, gid, rules, *, allow_initial_pending=False, allow_policy_change=False):
    directory(target, gid)
    current = target / 'current'
    if current.is_symlink():
        name = os.readlink(current)
        if not GENERATION.fullmatch(name):
            raise SetupError('invalid current generation pointer')
        directory(target / 'generations', gid)
        result = check_bundle(target / name, gid, rules, allow_policy_change=allow_policy_change)
        expected = 'password_file ' + str(target / name / 'passwd') + '\nacl_file ' + str(target / name / 'acl') + '\n'
        if (result['path'] / 'config.d/accounts.conf').read_text() != expected:
            raise SetupError('generation configuration is not pinned to its own files')
        return result
    if current.exists():
        raise SetupError('current generation must be a managed symlink')
    if (target / 'published.json').exists():
        raise SetupError('published account pointer is missing; preserve history instead of reinitializing')
    if (target / 'manifest.json').exists():
        return check_bundle(target, gid, rules, legacy=True)
    if allow_initial_pending and (target / 'transaction.json').exists():
        private_file(target / 'transaction.json', 0o600)
        tx = read_json(target / 'transaction.json')
        if tx.get('base_revision') in (-1, 0):
            return None
    raise SetupError('account state is missing or interrupted; refusing fresh credentials')


@contextmanager
def locked(data):
    path = data / '.mqtt-initialize.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise SetupError('unsafe account lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('another account operation is in progress') from None
        yield fd
    finally:
        os.close(fd)


def atomic_json(path, value):
    temporary = path.parent / ('.' + path.name + '-' + uuid.uuid4().hex)
    write(temporary, json.dumps(value, sort_keys=True) + '\n')
    os.replace(temporary, path)
    sync_directory(path.parent)


def hash_new(path, values, gid, *, pass_fds=(), timeout=20):
    """기존 해시는 재생성하지 않고 새 비밀번호도 argv에 넣지 않는다."""
    raw = path / 'new-passwords'
    if not raw.exists():
        write(raw, ''.join(name + ':' + value + '\n' for name, value in sorted(values.items())))
    private_file(raw, 0o600)
    text=raw.read_text()
    if text == ''.join(name+':'+value+'\n' for name,value in sorted(values.items())):
        result = subprocess.run(['mosquitto_passwd', '-H', 'sha512-pbkdf2', '-U', str(raw)],
                                capture_output=True, timeout=timeout, pass_fds=pass_fds)
        if result.returncode:
            raise SetupError('password hashing failed; preserve pending generation')
    else:
        existing=password_lines(text)
        if set(existing)!=set(values) or any(not v.startswith('$7$') for v in existing.values()):
            raise SetupError('interrupted password conversion is not a complete hash set')
    hashed=password_lines(raw.read_text())
    if set(hashed)!=set(values) or any(not v.startswith('$7$') for v in hashed.values()):
        raise SetupError('password conversion did not produce supported hashes')
    with raw.open('rb') as stream:os.fsync(stream.fileno())
    return hashed


def publish(target, old, records, spec, rules, gid, data, *, pass_fds=(), timeout=20):
    """current 포인터로 저장 세대를 확정한다. broker 내부 반영은 별도 경계다."""
    base = old['revision'] if old else -1
    transaction = target / 'transaction.json'
    if transaction.exists():
        private_file(transaction, 0o600)
        tx = read_json(transaction)
        if old and not old['legacy'] and old['manifest']['transaction_id'] == tx['id']:
            tx = None
        elif tx['spec'] != spec or tx['base_revision'] != base:
            raise SetupError('unfinished account operation exists; retry its original command')
    else:
        tx = None
    if tx is None:
        tx = {'id': uuid.uuid4().hex, 'base_revision': base, 'revision': max(1, base + 1), 'spec': spec}
        atomic_json(transaction, tx)
    if (set(tx) != {'id', 'base_revision', 'revision', 'spec'} or
            not isinstance(tx['id'], str) or not re.fullmatch('[0-9a-f]{32}', tx['id']) or
            type(tx['base_revision']) is not int or
            type(tx['revision']) is not int or tx['revision'] != max(1, base + 1)):
        raise SetupError('invalid pending transaction')
    generations = target / 'generations'
    if not generations.exists():
        mkdir(generations, gid)
        sync_directory(target)
    directory(generations, gid)
    final = generations / tx['id']
    stage = target / ('.pending-' + tx['id'])
    if final.exists():
        candidate = check_bundle(final, gid, rules)
        if candidate['manifest']['transaction_id'] != tx['id'] or candidate['profiles'] != records:
            raise SetupError('pending generation does not match original operation')
    else:
        if not stage.exists():
            mkdir(stage, gid)
            sync_directory(target)
        directory(stage, gid)
        credentials = stage / 'credentials.json'
        if not credentials.exists():
            values = {name: old['secrets'][name] if old and name in old['secrets'] else os.urandom(32).hex() for name in records}
            write(credentials, json.dumps({'schema_version': 1, 'accounts': values}, sort_keys=True) + '\n')
        private_file(credentials, 0o600)
        values = read_json(credentials)['accounts']
        if set(values) != set(records) or any(old['secrets'][n] != values[n] for n in records if old and n in old['secrets']):
            raise SetupError('pending credentials changed')
        new = {n: v for n, v in values.items() if not old or n not in old['hashes']}
        hashes = {n: old['hashes'][n] for n in records if old and n in old['hashes']}
        if new:
            hashes.update(hash_new(stage, new, gid,pass_fds=pass_fds,timeout=timeout))
        contents = {'passwd': ''.join(n + ':' + hashes[n] + '\n' for n in sorted(records)),
                    'acl': acl(records, rules)}
        config = stage / 'config.d'
        if not config.exists():
            mkdir(config, gid)
        contents['config.d/accounts.conf'] = 'password_file ' + str(final / 'passwd') + '\nacl_file ' + str(final / 'acl') + '\n'
        for name, content in contents.items():
            file = stage / name
            if not file.exists():
                write(file, content, 0o640, gid)
            elif file.read_text() != content:
                raise SetupError('partial or mismatched pending file; preserve for investigation')
        revoked=set(old['manifest'].get('revoked_accounts',())) if old else set()
        revoked.update(set(old['profiles'])-set(records) if old else ())
        revoked.difference_update(records)
        manifest = {'schema_version': 2, 'revision': tx['revision'], 'transaction_id': tx['id'], 'profiles': records,
                    'revoked_accounts':sorted(revoked),
                    'policy_sha256': hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest(),
                    'sha256': {name: sha(stage / name) for name in ('passwd', 'acl', 'credentials.json', 'config.d/accounts.conf')}}
        if not (stage / 'manifest.json').exists():
            write(stage / 'manifest.json', json.dumps(manifest, sort_keys=True) + '\n')
        if read_json(stage / 'manifest.json') != manifest:
            raise SetupError('pending manifest changed')
        check_bundle(stage, gid, rules)
        for name in (*manifest['sha256'], 'manifest.json'):
            with (stage / name).open('rb') as stream:
                os.fsync(stream.fileno())
        sync_directory(config)
        sync_directory(stage)
        storage(data)
        os.rename(stage, final)
        sync_directory(generations)
        sync_directory(target)
    storage(data)
    temporary = target / ('.current-' + tx['id'])
    if temporary.is_symlink():
        if os.readlink(temporary) != 'generations/' + tx['id']:
            raise SetupError('unexpected pending pointer')
    elif temporary.exists():
        raise SetupError('unexpected pending pointer file')
    else:
        os.symlink('generations/' + tx['id'], temporary)
    os.replace(temporary, target / 'current')
    sync_directory(target)
    return load(target, gid, rules)


def inspect(data=DATA, gid=None, rules=None, runtime=False):
    target = storage(data)
    gid = grp.getgrnam('mosquitto').gr_gid if gid is None else gid
    value = load(target, gid, rules or policy())
    if runtime and value['legacy']:
        raise SetupError('legacy state requires explicit initialize --execute before this configuration starts')
    return value


def change(action, name=None, *, vms=(), accounts=(), execute=False, data=DATA, gid=None, rules=None, source=None,
           legacy_source=Path('/etc/mosquitto'), _private_result=False, _pass_fds=(), _hash_timeout=20):
    rules = rules or policy()
    vms = vm_ids(vms)
    accounts=tuple(sorted(set(accounts)))
    for account in accounts:profile(account,rules)
    if accounts and action!='ensure-accounts':raise SetupError('accounts are only supported by automatic ensure')
    gid = grp.getgrnam('mosquitto').gr_gid if gid is None else gid
    target = storage(data)
    if name is not None:
        kind = profile(name, rules)
    old = load(target, gid, rules, allow_initial_pending=True, allow_policy_change=True) if target.exists() or target.is_symlink() else None
    mounted_old = old
    if old is None and action in ('initialize','ensure-accounts') and (legacy_source / 'passwd').exists():
        raise SetupError('older broker credentials exist; use explicit import-legacy instead of issuing replacements')
    if action == 'import-legacy':
        imported = import_source(source, gid, rules)
        if old is not None:
            if old['legacy'] or any(old['secrets'].get(n) != v for n, v in imported['secrets'].items()) or any(
                    old['hashes'].get(n) != v for n, v in imported['hashes'].items()):
                raise SetupError('destination differs; refusing legacy overwrite')
        else:
            old = imported
    if old and not old['legacy'] and not old['policy_matches'] and action != 'initialize':
        raise SetupError('installed policy changed; review an initialize plan before account changes')
    if old is None and (action not in ('initialize','ensure-accounts') or any(data.glob('.mqtt-initialize-*')) or
                        (not target.exists() and (data / '.mqtt-initialize.lock').exists() and
                         (data / '.mqtt-initialize.lock').stat().st_size)):
        raise SetupError('missing or interrupted state; initialize only on a verified fresh installation')
    records = dict(old['profiles']) if old else default_accounts(rules, vms)
    if action == 'initialize':
        # Existing initialisation never deletes accounts absent from this command.
        records.update(default_accounts(rules, vms))
    elif action == 'import-legacy':
        pass
    elif action == 'add-account':
        records[name] = kind
    elif action == 'ensure-accounts':
        if old and set(accounts)&set(old['manifest'].get('revoked_accounts',())):
            raise CredentialRevoked('revoked account requires explicit operator restoration')
        records.update({account:profile(account,rules) for account in accounts})
    elif action == 'remove-account':
        records.pop(name, None)
    else:
        raise SetupError('unsupported account operation')
    for account, expected in records.items():
        if profile(account, rules) != expected:
            raise SetupError('policy no longer defines an existing account; preserve it for explicit review')
    result = {'configured': old is not None, 'execute_required': not execute,
              'policy_changed': bool(old and not old['legacy'] and not old['policy_matches']),
              'revision': old['revision'] if old else None, 'accounts': sorted(records),
              'added': sorted(set(records) - set(old['profiles'] if old else ())),
              'removed': sorted(set(old['profiles'] if old else ()) - set(records))}
    if not execute:
        return result
    spec = {'action': action, 'name': name, 'vms': vms, 'source': str(source) if source else None}
    if action=='ensure-accounts':spec['accounts']=list(accounts)
    with locked(data) as fd:
        storage(data)
        # Re-read after admission; no overwrite based on a pre-lock snapshot.
        current = load(target, gid, rules, allow_initial_pending=True, allow_policy_change=True) if target.exists() or target.is_symlink() else None
        if (current['revision'] if current else -1) != (mounted_old['revision'] if mounted_old else -1):
            raise SetupError('account revision changed; repeat the plan')
        if not target.exists():
            if action in ('initialize','ensure-accounts') and (legacy_source / 'passwd').exists():
                raise SetupError('older broker credentials appeared; use explicit import-legacy')
            if os.fstat(fd).st_size or any(data.glob('.mqtt-initialize-*')):
                raise SetupError('interrupted initialization; refusing credential reissue')
            os.write(fd, b'mqtt-initialization-started-v2\n')
            os.fsync(fd)
            sync_directory(data)
            mkdir(target, gid)
            sync_directory(data)
        pending = False
        if (target / 'transaction.json').exists():
            tx = read_json(target / 'transaction.json')
            pending = not old or old['legacy'] or old['manifest']['transaction_id'] != tx['id']
        if old and not old['legacy'] and old.get('policy_matches') and records == old['profiles'] and not pending:
            sync_directory(target)
            value = old
        else:
            value = publish(target, old, records, spec, rules, gid, data,
                            pass_fds=tuple(set((*_pass_fds,fd))),timeout=_hash_timeout)
        published = {'generation': value['path'].name, 'revision': value['revision']}
        # A completed publication remains distinguishable from a fresh install
        # even if current is subsequently lost. This fence never issues accounts.
        if (target / 'published.json').exists():
            private_file(target / 'published.json', 0o600)
        if not (target / 'published.json').exists() or read_json(target / 'published.json') != published:
            atomic_json(target / 'published.json', published)
    result.update(configured=True, execute_required=False, revision=value['revision'],
                  reused=bool(old and old['profiles'] == records and not old['legacy']), stored=True)
    return (result,value) if _private_result else result


def ensure_accounts(accounts, **kwargs):
    """특권 공급 adapter용. 원문을 반환하므로 공개 응답/진단에 직렬화하지 않는다."""
    return change('ensure-accounts',accounts=accounts,execute=True,_private_result=True,**kwargs)


def reload_broker():
    result = subprocess.run(['systemctl', 'show', 'mosquitto.service', '-p', 'ActiveState', '--value'],
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise SetupError('accounts stored; broker state could not be queried')
    if result.stdout.strip() in ('inactive', 'failed'):
        return 'next_start'
    if result.stdout.strip() != 'active':
        raise SetupError('accounts stored; broker is changing state; repeat the same command')
    result = subprocess.run(['systemctl', 'reload', 'mosquitto.service'], capture_output=True, timeout=5)
    if result.returncode:
        raise SetupError('accounts stored; reload could not be confirmed; repeat the same command')
    return 'reload_requested'


def export(value, name, form, output, execute=False):
    """계정 하나만 전용 파일로 전달한다. guest 설정과 receipt는 쓰지 않는다."""
    if name not in value['secrets']:
        raise SetupError('account not found')
    output = Path(output)
    if not output.is_absolute() or output.resolve() != output:
        raise SetupError('absolute private output path required')
    parent = output.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077 or output.exists() or output.is_symlink():
        raise SetupError('private root-owned parent and a new output file required')
    password = value['secrets'][name]
    if form == 'agent-env':
        vm_id(name)
        content = f'XENOVA_AGENT_BROKER_USERNAME={name}\nXENOVA_AGENT_BROKER_PASSWORD={password}\n'
    elif form == 'credentials':
        content = json.dumps({'username': name, 'password': password}) + '\n'
    elif form == 'backend-env':
        if name != 'backend':
            raise SetupError('backend export requires backend account')
        content = f'MQTT_USERNAME={name}\nMQTT_PASSWORD={password}\n'
    elif form == 'display-env':
        if name != 'observer' or value['profiles'].get(name) != 'service':
            raise SetupError('display export requires the existing read-only observer account')
        if grants((value['path'] / 'acl').read_text()).get(name) != {'topic read xenova/v1/#'}:
            raise SetupError('observer must have exactly the expected read-only ACL')
        content = ('XENOVA_DISPLAY_MQTT_HOST=127.0.0.1\nXENOVA_DISPLAY_MQTT_PORT=1883\n'
                   f'XENOVA_DISPLAY_MQTT_USERNAME=observer\nXENOVA_DISPLAY_MQTT_PASSWORD={password}\n')
    else:
        expected = 'telemetry' if form == 'telemetry' else 'vm'
        if value['profiles'][name] != expected:
            raise SetupError('Bootstrap export/account profile mismatch')
        own = name[10:] if form == 'telemetry' else name
        content = json.dumps({'vm_id': own, 'broker_ipv4': '192.168.77.1', 'port': 1883,
                              'username': name, 'password': password, 'trusted_internal_plaintext': True}) + '\n'
    if execute:
        write(output, content)
        sync_directory(output.parent)
    return {'exported': execute, 'execute_required': not execute, 'account': name,
            'output': str(output), 'format': form, 'consumer_configuration_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    initial = commands.add_parser('initialize', help='plan or publish default accounts, preserving existing passwords')
    initial.add_argument('--vm', action='append', default=[], metavar='UUID')
    initial.add_argument('--execute', action='store_true')
    for verb in ('add-account', 'remove-account'):
        command = commands.add_parser(verb)
        command.add_argument('name')
        command.add_argument('--execute', action='store_true')
    command = commands.add_parser('import-legacy', help='explicitly copy an older dom0-broker state without rotating credentials')
    command.add_argument('--source', type=Path, default=Path('/etc/mosquitto'))
    command.add_argument('--execute', action='store_true')
    commands.add_parser('list', help='list account names and profiles without secrets')
    check = commands.add_parser('check', help='validate storage and account generation')
    check.add_argument('--runtime', action='store_true', help='require current configuration-compatible generation')
    command = commands.add_parser('export-account')
    command.add_argument('name')
    command.add_argument('--format', choices=('agent-env', 'backend-env', 'display-env', 'credentials', 'drive', 'telemetry'), required=True)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    stored = None
    try:
        if os.geteuid() != 0:
            raise SetupError('run local account operations as root')
        if args.command in ('list', 'check', 'export-account'):
            value = inspect(runtime=getattr(args, 'runtime', False))
            if args.command == 'export-account':
                result = export(value, args.name, args.format, args.output, args.execute)
            else:
                result = {'configured': True, 'revision': value['revision'], 'legacy': value['legacy'],
                          'accounts': sorted(value['profiles']), 'profiles': value['profiles']}
        else:
            result = change(args.command, getattr(args, 'name', None), vms=getattr(args, 'vm', ()),
                            execute=args.execute, source=getattr(args, 'source', None))
            if args.execute:
                stored = result['stored']
                result['broker'] = reload_broker()
                result['client_authentication_verified'] = False
        print(json.dumps(result))
        return 0
    except SetupError as error:
        message = str(error)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        message = 'operation could not be confirmed; preserve current and pending state; retry the same command'
    print(json.dumps({'configured': stored, 'stored': stored, 'error': 'MQTT_SETUP_FAILED', 'message': message}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
