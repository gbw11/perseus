"""자원 guard의 유한 기록 값. OS 관측·저장·정리·생성 허가를 수행하지 않는다.

원 bytes와 출판 canonical bytes를 구별한다. JSON 할당은 raw8192B로 제한하고,
해석 뒤 manifest tree와 exact 문법을 검사한다. native evidence의 bound는 별도다.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID


U64_MAX = 2**64 - 1
ROLES = ('S', 'A', 'M', 'D', 'TRACKER')
PHASES = ('CURRENT', 'RETIRED')
SLOTS = ('RAW_CURRENT', 'RAW_RETIRED', 'DB_COPY_0', 'DB_COPY_1', 'DB_COPY_2', 'DB_COPY_3',
         'RAW_0_WRITABLE', 'RAW_0_READY', 'RAW_1_WRITABLE', 'RAW_1_READY', 'DB_WRITABLE', 'DB_READY')
ROOT_KEYS = frozenset(('version', 'runtime', 'service_instance', 'resource_epoch', 'revision', 'state',
                       'owners', 'cgroup', 'resources', 'intents', 'recovery_episode'))


class ManifestError(ValueError):
    """원 기록의 타입·문법·상한·내부 결박이 맞지 않는다."""


def _fail(message):
    raise ManifestError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _uint(value, *, positive=False, maximum=U64_MAX):
    if type(value) is not int or not (1 if positive else 0) <= value <= maximum:
        _fail('expected bounded exact unsigned integer')
    return value


def _text(value, maximum=192):
    if type(value) is not str or len(value) > maximum or not value.isascii():
        _fail('expected bounded exact ASCII text')
    return value


def _choice(value, choices):
    _text(value)
    if value not in choices:
        _fail('unsupported closed profile value')


def _uuid(value):
    _text(value, 36)
    try:
        if len(value) != 36 or str(UUID(value)) != value:
            _fail('expected canonical UUID')
    except ValueError as error:
        raise ManifestError('expected canonical UUID') from error


def _sha(value):
    _text(value, 64)
    if re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('expected lowercase SHA256')


def _decimal(value, *, positive=False):
    _text(value, 20)
    if re.fullmatch('0|[1-9][0-9]*', value) is None:
        _fail('expected canonical u64 decimal string')
    return _uint(int(value), positive=positive)


def _keys(value, names):
    _exact(value, dict)
    if set(value) != set(names):
        _fail('missing or unknown exact object keys')


@dataclass(frozen=True)
class RuntimeScope:
    """lower가 기록한 runtime 식별값. 숫자의 차이가 backing reset 증명은 아니다."""

    boot_id: str
    pid_ns_inode: int
    mnt_ns_inode: int
    shm_mount_id: int
    shm_dev: int
    shm_root_inode: int
    shm_fs: str

    def __post_init__(self):
        _uuid(self.boot_id)
        for n in ('pid_ns_inode', 'mnt_ns_inode', 'shm_mount_id', 'shm_dev', 'shm_root_inode'):
            _uint(getattr(self, n), positive=True)
        _choice(self.shm_fs, ('tmpfs',))


@dataclass(frozen=True)
class ProcessBirth:
    """같은 boot/PID namespace 안에서 해석할 PID와 kernel start ticks."""

    pid: int
    start_ticks: int

    def __post_init__(self):
        _uint(self.pid, positive=True, maximum=2**32 - 1)
        _uint(self.start_ticks)


@dataclass(frozen=True)
class OwnerRecord:
    """CURRENT/RETIRED는 목록 분류이며 실제 생존 상태가 아니다."""

    role: str
    phase: str
    pid: int | None
    start_ticks: int | None

    def __post_init__(self):
        _choice(self.role, ROLES)
        _choice(self.phase, PHASES)
        if self.role == 'S' and self.phase != 'CURRENT':
            _fail('S has no retired owner slot')
        if (self.pid is None) != (self.start_ticks is None):
            _fail('pending owner birth requires both null values')
        if self.pid is not None:
            ProcessBirth(self.pid, self.start_ticks)

    @property
    def birth(self):
        return None if self.pid is None else ProcessBirth(self.pid, self.start_ticks)


@dataclass(frozen=True)
class CgroupIdentity:
    mode: str
    path: str
    device: int
    inode: int

    def __post_init__(self):
        _choice(self.mode, ('SYSTEMD_V1', 'UNIFIED_V2'))
        _text(self.path, 192)
        if (not self.path.startswith('/') or any(not 32 <= ord(c) <= 126 for c in self.path)
                or any(p in ('.', '..') for p in self.path.split('/'))):
            _fail('expected bounded absolute cgroup path without dot components')
        _uint(self.device)
        _uint(self.inode, positive=True)


def _shm(slot):
    return slot in SLOTS[:6]


def _shm_name(slot, object_id):
    return ('/xnv_raw_' if slot.startswith('RAW') else '/xnv_db_') + object_id


def _name(name):
    _text(name, 128)
    if re.fullmatch('/[A-Za-z0-9_.-]{1,127}', name) is None or name[1:] in ('.', '..'):
        _fail('expected one-slash canonical POSIX name')


@dataclass(frozen=True)
class ResourceRecord:
    """논리 inventory 주소와 기록된 물리 이름. actual existence/layout은 별도다."""

    slot: str
    state: str
    name: str | None
    object_id: str | None
    size_bytes: int | None

    def __post_init__(self):
        _choice(self.slot, SLOTS)
        _choice(self.state, ('NOT_ATTEMPTED', 'INTENT', 'KNOWN', 'UNKNOWN'))
        if self.state != 'KNOWN':
            if self.name is not None or self.object_id is not None or self.size_bytes is not None:
                _fail('only KNOWN inventory carries actual name/object/size')
            return
        _name(self.name)
        if _shm(self.slot):
            _uuid(self.object_id)
            _uint(self.size_bytes, positive=True)
            if self.name != _shm_name(self.slot, self.object_id):
                _fail('SHM name differs from its physical object ID')
        elif self.object_id is not None or self.size_bytes is not None:
            _fail('semaphore inventory has no object ID or size')

    @property
    def kind(self):
        return 'SHM' if _shm(self.slot) else 'SEMAPHORE'


@dataclass(frozen=True)
class ResourceIntent:
    intent_id: str
    slot: str
    operation: str
    planned_name: str | None
    planned_object_id: str | None
    planned_size_bytes: int | None

    def __post_init__(self):
        _uuid(self.intent_id)
        _choice(self.slot, SLOTS)
        _choice(self.operation, ('CREATE',))
        if self.slot == 'RAW_RETIRED':
            _fail('retired inventory is not a creation target')
        if _shm(self.slot):
            _uuid(self.planned_object_id)
            _uint(self.planned_size_bytes, positive=True)
            _name(self.planned_name)
            if self.planned_name != _shm_name(self.slot, self.planned_object_id):
                _fail('planned name differs from planned object')
        elif any(v is not None for v in (self.planned_name, self.planned_object_id, self.planned_size_bytes)):
            _fail('new semaphore name/object/size are not known before its constructor')


@dataclass(frozen=True)
class RecoveryEpisode:
    """전역 group와 A/M/D의 durable 보존값. 증가/rolling/reset을 수행하지 않는다."""

    episode_id: str
    service_attempts: int
    role_attempts: tuple[int, int, int]

    def __post_init__(self):
        _uuid(self.episode_id)
        _uint(self.service_attempts, maximum=5)
        _exact(self.role_attempts, tuple)
        if len(self.role_attempts) != 3:
            _fail('episode requires A/M/D counters')
        for value in self.role_attempts:
            _uint(value, maximum=5)


@dataclass(frozen=True)
class ManifestRef:
    raw_sha256: str
    revision: int
    service_instance: str
    resource_epoch: str
    runtime: RuntimeScope

    def __post_init__(self):
        _sha(self.raw_sha256)
        _uint(self.revision, positive=True)
        _uuid(self.service_instance)
        _uuid(self.resource_epoch)
        _exact(self.runtime, RuntimeScope)


def _depth_before_json(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > 3:
                _fail('manifest container depth exceeds three')
        elif char in ']}':
            depth -= 1
            if depth < 0:
                _fail('unmatched JSON container')


def _pairs(pairs):
    if len(pairs) > 12:
        _fail('manifest object exceeds twelve keys')
    result = {}
    for key, value in pairs:
        _text(key, 32)
        if key in result:
            _fail('duplicate decoded manifest key')
        result[key] = value
    return result


def _json_int(text):
    if len(text) > 20:
        _fail('JSON integer exceeds native width bound')
    return int(text)


def _no_number(text):
    _fail('float and nonfinite values are unsupported')


def _tree(value, depth=1, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 512:
        _fail('manifest tree exceeds node bound')
    kind = type(value)
    if kind is dict or kind is list:
        if depth > 3 or len(value) > (12 if kind is dict else 9):
            _fail('manifest container exceeds its bound')
        if kind is dict:
            for key, child in value.items():
                _text(key, 32)
                _tree(key, depth + 1, budget)
                _tree(child, depth + 1, budget)
        else:
            for child in value:
                _tree(child, depth + 1, budget)
    elif kind is str:
        _text(value)
    elif kind is int:
        if value.bit_length() > 64:
            _fail('manifest native integer exceeds 64 bits')
    elif value is not None:
        _fail('manifest has an unsupported native value')


def _parse(fields):
    _keys(fields, ROOT_KEYS)
    if type(fields['version']) is not int or fields['version'] != 1:
        _fail('unsupported manifest version')
    r = fields['runtime']
    _keys(r, ('boot_id', 'pid_ns_inode', 'mnt_ns_inode', 'shm_mount_id', 'shm_dev', 'shm_root_inode', 'shm_fs'))
    runtime = RuntimeScope(r['boot_id'], *(_decimal(r[n], positive=True) for n in (
        'pid_ns_inode', 'mnt_ns_inode', 'shm_mount_id', 'shm_dev', 'shm_root_inode')), r['shm_fs'])
    _uuid(fields['service_instance'])
    _uuid(fields['resource_epoch'])
    revision = _decimal(fields['revision'], positive=True)
    _choice(fields['state'], ('DIRTY', 'CLEANUP_CANDIDATE', 'CLEAN'))
    _exact(fields['owners'], list)
    owners = []
    for o in fields['owners']:
        _keys(o, ('role', 'phase', 'pid', 'start_ticks'))
        owners.append(OwnerRecord(o['role'], o['phase'], o['pid'],
                                  None if o['start_ticks'] is None else _decimal(o['start_ticks'])))
    if (len({(o.role, o.phase) for o in owners}) != len(owners)
            or len({(o.pid, o.start_ticks) for o in owners if o.pid is not None}) != sum(o.pid is not None for o in owners)):
        _fail('duplicate owner slot or known process birth')
    supervisors = [o for o in owners if o.role == 'S']
    if len(supervisors) != 1 or supervisors[0].pid is None:
        _fail('manifest requires the known CURRENT S birth')
    owners.sort(key=lambda o: (ROLES.index(o.role), PHASES.index(o.phase)))
    c = fields['cgroup']
    _keys(c, ('mode', 'path', 'device', 'inode'))
    cgroup = CgroupIdentity(c['mode'], c['path'], _decimal(c['device']), _decimal(c['inode'], positive=True))
    _keys(fields['resources'], SLOTS)
    resources = []
    for slot in SLOTS:
        v = fields['resources'][slot]
        _keys(v, ('state', 'name', 'object_id', 'size_bytes'))
        resources.append(ResourceRecord(slot, v['state'], v['name'], v['object_id'],
                                        None if v['size_bytes'] is None else _decimal(v['size_bytes'], positive=True)))
    known = [r for r in resources if r.state == 'KNOWN']
    if len({r.name for r in known}) != len(known):
        _fail('duplicate known physical name')
    objects = [r.object_id for r in known if r.object_id is not None]
    if len(set(objects)) != len(objects):
        _fail('duplicate known SHM object')
    _exact(fields['intents'], list)
    if len(fields['intents']) > 1:
        _fail('only one unresolved creation is supported')
    intents = []
    for i in fields['intents']:
        _keys(i, ('intent_id', 'slot', 'operation', 'planned_name', 'planned_object_id', 'planned_size_bytes'))
        intents.append(ResourceIntent(i['intent_id'], i['slot'], i['operation'], i['planned_name'], i['planned_object_id'],
                                      None if i['planned_size_bytes'] is None else _decimal(i['planned_size_bytes'], positive=True)))
    pending = [r for r in resources if r.state in ('INTENT', 'UNKNOWN')]
    if len(pending) != len(intents) or intents and pending[0].slot != intents[0].slot:
        _fail('unresolved resource and creation intent must match exactly')
    if intents and intents[0].planned_name is not None:
        if intents[0].planned_name in {r.name for r in known} or intents[0].planned_object_id in objects:
            _fail('planned SHM name/object conflicts with known inventory')
    e = fields['recovery_episode']
    _keys(e, ('episode_id', 'service_attempts', 'role_attempts'))
    _keys(e['role_attempts'], ('A', 'M', 'D'))
    episode = RecoveryEpisode(e['episode_id'], e['service_attempts'], tuple(e['role_attempts'][r] for r in ('A', 'M', 'D')))
    complete = not pending and all(o.pid is not None for o in owners)
    if fields['state'] == 'CLEAN' and not complete:
        _fail('CLEAN cannot conceal unresolved inventory or owner birth')
    fields['owners'] = [dict(role=o.role, phase=o.phase, pid=o.pid,
                             start_ticks=None if o.start_ticks is None else str(o.start_ticks)) for o in owners]
    return runtime, revision, tuple(owners), cgroup, tuple(resources), tuple(intents), episode, complete


@dataclass(frozen=True, init=False)
class ResourceManifest:
    """원 기록≤8KiB와 유한 typed 값. 문자열/값 검사가 실제 inventory를 보증하지 않는다."""

    raw_bytes: bytes
    raw_sha256: str
    canonical_bytes: bytes
    canonical_sha256: str
    runtime: RuntimeScope
    service_instance: str
    resource_epoch: str
    revision: int
    state: str
    owners: tuple[OwnerRecord, ...]
    cgroup: CgroupIdentity
    resources: tuple[ResourceRecord, ...]
    intents: tuple[ResourceIntent, ...]
    recovery_episode: RecoveryEpisode
    complete_inventory: bool
    ref: ManifestRef

    def __init__(self, raw_bytes):
        _exact(raw_bytes, bytes)
        if not 1 <= len(raw_bytes) <= 8192:
            _fail('manifest raw bytes exceed eight KiB')
        try:
            text = raw_bytes.decode('ascii')
            _depth_before_json(text)
            fields = json.loads(text, object_pairs_hook=_pairs, parse_int=_json_int,
                                parse_float=_no_number, parse_constant=_no_number)
        except ManifestError:
            raise
        except (ValueError, UnicodeError) as error:
            raise ManifestError('invalid ASCII manifest JSON') from error
        _tree(fields)
        runtime, revision, owners, cgroup, resources, intents, episode, complete = _parse(fields)
        canonical = (json.dumps(fields, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')
        if len(canonical) > 8192:
            _fail('canonical manifest exceeds eight KiB')
        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        values = dict(raw_bytes=raw_bytes, raw_sha256=raw_sha, canonical_bytes=canonical,
                      canonical_sha256=hashlib.sha256(canonical).hexdigest(), runtime=runtime,
                      service_instance=fields['service_instance'], resource_epoch=fields['resource_epoch'],
                      revision=revision, state=fields['state'], owners=owners, cgroup=cgroup, resources=resources,
                      intents=intents, recovery_episode=episode, complete_inventory=complete,
                      ref=ManifestRef(raw_sha, revision, fields['service_instance'], fields['resource_epoch'], runtime))
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def fields(self):
        return json.loads(self.canonical_bytes.decode('ascii'))

    @property
    def known_owners(self):
        return tuple(o for o in self.owners if o.pid is not None)

    @property
    def known_resources(self):
        return tuple(r for r in self.resources if r.state == 'KNOWN')

    def resource(self, slot):
        _choice(slot, SLOTS)
        return self.resources[SLOTS.index(slot)]
