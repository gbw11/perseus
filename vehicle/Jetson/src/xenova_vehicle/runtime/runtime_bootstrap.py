"""명시 설치 기록의 실제 생성·한 번 소비. MISSING을 생성 허가로 바꾸지 않는다.

설치 명령은 새 보호 directory만 만든다. 상주 S-log는 원 MISSING 조회와 현재
runtime에 맞는 READY 기록을 CONSUMED로 내구 저장한 뒤 초기 DIRTY를 제안한다.
소비 뒤 실패한 설치를 자동 복원하거나 과거 catalogue/cgroup을 승인하지 않는다.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import stat
import uuid

from .process_observation import ObservationBatch, ProcessObserver
from .resource_guard import FirstInstallStatement, GuardDecision, GuardEvidence, evaluate_guard
from .resource_manifest import RecoveryEpisode, ResourceManifest, SLOTS
from .resource_store import ResourceStore, StoreProposal


MARKER = 'bootstrap-admission.json'
MARKER_CAP = 4096


class BootstrapUnavailable(RuntimeError):
    """설치·소비·과거 자원 상태가 불명이다. named 자원을 만들지 않는다."""


def _bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def _identity(value):
    return value.st_dev, value.st_ino


class _Directory:
    def __init__(self, path):
        self.path = str(Path(path).expanduser().absolute())
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self.original = os.fstat(self.fd)
            self.check()
        except BaseException:
            os.close(self.fd)
            raise

    def check(self):
        opened = os.fstat(self.fd)
        named = os.stat(self.path, follow_symlinks=False)
        if (not stat.S_ISDIR(named.st_mode) or opened.st_uid != os.geteuid()
                or opened.st_mode & 0o022 or _identity(opened) != _identity(named)
                or _identity(opened) != _identity(self.original)):
            raise BootstrapUnavailable('protected installation directory changed')

    def read(self):
        descriptor = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise BootstrapUnavailable('installation marker is not an owned protected regular file')
            raw = bytearray()
            while len(raw) <= MARKER_CAP:
                part = os.read(descriptor, MARKER_CAP + 1 - len(raw))
                if not part:
                    break
                raw.extend(part)
            after = os.fstat(descriptor)
            named = os.stat(MARKER, dir_fd=self.fd, follow_symlinks=False)
            fingerprint = lambda s: (_identity(s), s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)
            if len(raw) > MARKER_CAP or fingerprint(before) != fingerprint(after) or fingerprint(before) != fingerprint(named):
                raise BootstrapUnavailable('installation marker changed during read')
            self.check()
            return bytes(raw), before
        finally:
            os.close(descriptor)

    def write(self, value, *, expected=None):
        raw = _bytes(value)
        if len(raw) > MARKER_CAP:
            raise BootstrapUnavailable('installation marker exceeds its bound')
        name = MARKER if expected is None else '.bootstrap.' + str(uuid.uuid4()) + '.tmp'
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=self.fd)
        owned = None
        try:
            owned = os.fstat(descriptor)
            position = 0
            while position < len(raw):
                count = os.write(descriptor, raw[position:])
                if count <= 0:
                    raise BootstrapUnavailable('installation write made no progress')
                position += count
            os.fsync(descriptor)
            self.check()
            if expected is not None:
                current, _ = self.read()
                if current != expected:
                    raise BootstrapUnavailable('installation consumption predecessor changed')
                os.replace(name, MARKER, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(descriptor)
            if expected is not None:
                try:
                    current = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if owned is not None and _identity(current) == _identity(owned):
                        os.unlink(name, dir_fd=self.fd)

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)


def initialize_runtime_state(directory):
    """명시 설치 명령 전용. 기존 directory는 변경하지 않으며 IPC를 만들지 않는다.

    동기 filesystem 작업이므로 상주 S-main에서 호출하지 않는다. 실패한 새 설치
    directory도 덮거나 지우지 않으며 운영자가 원 실패를 확인할 수 있게 남긴다.
    """
    path = Path(directory).expanduser().absolute()
    os.mkdir(path, 0o700)
    root = _Directory(path)
    try:
        installation = str(uuid.uuid4())
        with ResourceStore(str(path)) as store:
            observed = ProcessObserver(store, installation).observe()
            if observed.original_load.outcome.kind != 'MISSING' or observed.latest_load.outcome.kind != 'MISSING':
                raise BootstrapUnavailable('new installation requires two actual MISSING reads')
            value = dict(version=1, state='READY', installation_id=installation,
                         installation_runtime=asdict(observed.guard.runtime),
                         directory=dict(device=root.original.st_dev, inode=root.original.st_ino, uid=os.geteuid()),
                         genesis=dict(episode_id=str(uuid.uuid4()), service_attempts=0, role_attempts=dict(A=0, M=0, D=0)),
                         consumed_by=None, target_ref=None)
            root.write(value)
            return value
    finally:
        root.close()


def _cgroup_identity():
    with open('/proc/self/cgroup', 'rb') as source:
        raw = source.read(16_385)
    if len(raw) > 16_384:
        raise BootstrapUnavailable('current cgroup membership exceeds its bound')
    rows = [line.decode('ascii').split(':', 2) for line in raw.splitlines()]
    selected = [row[2] for row in rows if len(row) == 3 and row[1] == 'name=systemd']
    mode, mount = 'SYSTEMD_V1', '/sys/fs/cgroup/systemd'
    if not selected:
        selected = [row[2] for row in rows if len(row) == 3 and row[:2] == ['0', '']]
        mode, mount = 'UNIFIED_V2', '/sys/fs/cgroup'
    if len(selected) != 1 or not selected[0].startswith('/') or any(p in ('.', '..') for p in selected[0].split('/')):
        raise BootstrapUnavailable('current cgroup membership is not unambiguous')
    descriptor = os.open(mount + selected[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        value = os.fstat(descriptor)
        return dict(mode=mode, path=selected[0], device=str(value.st_dev), inode=str(value.st_ino))
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class BootstrapAdmission:
    manifest: ResourceManifest
    decision: GuardDecision
    installation_id: str
    proposal: StoreProposal


def consume_first_install(store, observation, *, resource_epoch, request_id):
    """S-log에서 원 조회와 실제 설치 기록을 한 번 소비하고 초기 DIRTY target을 돌려준다.

    실제 owner/latest 제안을 먼저 결박한 뒤 marker를 소비한다. 반환한 동일 원
    proposal의 publish 내구 ACK 이후에만 후속 intent를 진행한다. 별도 directory를
    받지 않으며 실패/만료한 pending을 새 proposal로 우회하지 않는다.
    """
    if type(store) is not ResourceStore or type(observation) is not ObservationBatch:
        raise BootstrapUnavailable('bootstrap requires the actual owning store and observation')
    if observation.original_load.outcome.kind != 'MISSING' or observation.latest_load.outcome.kind != 'MISSING':
        raise BootstrapUnavailable('existing catalogue requires old-resource recovery admission')
    root = _Directory(store.directory)
    try:
        original, _ = root.read()
        value = json.loads(original)
        expected_keys = {'version', 'state', 'installation_id', 'installation_runtime', 'directory', 'genesis', 'consumed_by', 'target_ref'}
        if (type(value) is not dict or set(value) != expected_keys or _bytes(value) != original
                or type(value['version']) is not int or value['version'] != 1
                or value['state'] != 'READY' or value['consumed_by'] is not None or value['target_ref'] is not None
                or _bytes(value['installation_runtime']) != _bytes(asdict(observation.guard.runtime))
                or _bytes(value['directory']) != _bytes(dict(device=root.original.st_dev, inode=root.original.st_ino, uid=os.geteuid()))):
            raise BootstrapUnavailable('installation record is consumed, mismatched or unsupported')
        if str(uuid.UUID(value['installation_id'])) != value['installation_id'] or str(uuid.UUID(resource_epoch)) != resource_epoch:
            raise BootstrapUnavailable('installation and resource epoch must be canonical UUIDs')
        genesis_fields = value['genesis']
        if (type(genesis_fields) is not dict or set(genesis_fields) != {'episode_id', 'service_attempts', 'role_attempts'}
                or type(genesis_fields['role_attempts']) is not dict or set(genesis_fields['role_attempts']) != {'A', 'M', 'D'}):
            raise BootstrapUnavailable('installation genesis has unsupported fields')
        genesis = RecoveryEpisode(genesis_fields['episode_id'], genesis_fields['service_attempts'],
                                  tuple(genesis_fields['role_attempts'][r] for r in ('A', 'M', 'D')))
        if genesis.service_attempts != 0 or genesis.role_attempts != (0, 0, 0):
            raise BootstrapUnavailable('first installation genesis was already used')
        fields = dict(version=1,
            runtime={key: str(v) if type(v) is int else v for key, v in asdict(observation.guard.runtime).items()},
            service_instance=observation.guard.service_instance, resource_epoch=resource_epoch, revision='1', state='DIRTY',
            owners=[dict(role='S', phase='CURRENT', pid=observation.guard.birth.pid, start_ticks=str(observation.guard.birth.start_ticks))]
                   + [dict(role=role, phase='CURRENT', pid=None, start_ticks=None) for role in ('A', 'M', 'D', 'TRACKER')],
            cgroup=_cgroup_identity(),
            resources={slot: dict(state='NOT_ATTEMPTED', name=None, object_id=None, size_bytes=None) for slot in SLOTS},
            intents=[], recovery_episode=dict(episode_id=genesis.episode_id, service_attempts=1, role_attempts=dict(A=1, M=1, D=1)))
        target = ResourceManifest(_bytes(fields))
        proposal = store.propose(observation.latest_load, target, request_id)
        consumed = dict(value, state='CONSUMED', consumed_by=dict(service_instance=observation.guard.service_instance,
                        lock_nonce=observation.latest_load.lock_nonce, load_id=observation.latest_load.outcome.load_id,
                        resource_epoch=resource_epoch), target_ref=asdict(proposal.target_ref))
        root.write(consumed, expected=original)
        import hashlib
        statement = FirstInstallStatement(observation.latest_load.outcome.load_id, observation.guard,
                                         value['installation_id'], hashlib.sha256(_bytes(consumed)).hexdigest(), genesis)
        decision = evaluate_guard(observation.latest_load.outcome, observation.guard, GuardEvidence(install=statement))
        if decision.kind != 'FIRST_INSTALL_ELIGIBLE':
            raise BootstrapUnavailable('actual first installation was not admitted')
        return BootstrapAdmission(target, decision, value['installation_id'], proposal)
    finally:
        root.close()
