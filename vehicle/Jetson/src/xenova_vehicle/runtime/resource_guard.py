"""자원 기록과 lower 진술의 순수 적격 판정. 실제 OS proof나 생성 허가가 아니다.

구조/타입 오류는 GuardInputError, 구조가 맞는 다른 scope/대상·누락·모순은 BLOCKED다.
분기마다 필요한 집합을 구별하되 제공된 진술의 결박은 모두 검사한다. 실제 lock,
process/cgroup/name/reset/install 관측과 내구 소비는 후속 adapter의 책임이다.
"""

from dataclasses import dataclass

from .resource_manifest import (
    CgroupIdentity, ManifestError, ManifestRef, OwnerRecord, ProcessBirth, RecoveryEpisode,
    ResourceManifest, ResourceRecord, RuntimeScope, _sha, _uuid,
)


class GuardInputError(ValueError):
    """native 값의 exact 타입/범위/닫힌 형식이 맞지 않는다."""


def _fail(message):
    raise GuardInputError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _scalar(check, value):
    try:
        check(value)
    except ManifestError as error:
        raise GuardInputError('invalid bounded native scalar') from error


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        _fail('unsupported exact closed value')


def _tuple(value, expected, maximum):
    _exact(value, tuple)
    if len(value) > maximum:
        _fail('native evidence tuple exceeds bound')
    for child in value:
        _exact(child, expected)


@dataclass(frozen=True)
class LoadOutcome:
    """d2 읽기의 typed 결과. invalid body에서 scope/episode를 복구하지 않는다."""

    load_id: str
    kind: str
    manifest: ResourceManifest | None = None

    def __post_init__(self):
        _scalar(_uuid, self.load_id)
        _choice(self.kind, ('VALID', 'MISSING', 'CORRUPT', 'IO_ERROR'))
        if self.kind == 'VALID':
            _exact(self.manifest, ResourceManifest)
        elif self.manifest is not None:
            _fail('only VALID load has a trusted parsed manifest')


@dataclass(frozen=True)
class CurrentGuardScope:
    """현재 실제 guard/lock 수명의 외부 식별 진술. 여기서 lock이나 birth를 조회하지 않는다."""

    guard_id: str
    service_instance: str
    runtime: RuntimeScope
    birth: ProcessBirth

    def __post_init__(self):
        _scalar(_uuid, self.guard_id)
        _scalar(_uuid, self.service_instance)
        _exact(self.runtime, RuntimeScope)
        _exact(self.birth, ProcessBirth)


def _claim(ref, guard):
    _exact(ref, ManifestRef)
    _exact(guard, CurrentGuardScope)


@dataclass(frozen=True)
class OwnerObservation:
    ref: ManifestRef
    guard: CurrentGuardScope
    owner: OwnerRecord
    outcome: str

    def __post_init__(self):
        _claim(self.ref, self.guard)
        _exact(self.owner, OwnerRecord)
        if self.owner.pid is None:
            _fail('owner observation needs a known exact birth')
        _choice(self.outcome, ('EXITED', 'ALIVE', 'UNKNOWN'))


@dataclass(frozen=True)
class CgroupObservation:
    """COMPLETE는 원 incarnation을 완전히 조사했다는 lower 진술이며 path 비교의 결과가 아니다."""

    ref: ManifestRef
    guard: CurrentGuardScope
    cgroup: CgroupIdentity
    outcome: str
    members: tuple[ProcessBirth, ...]

    def __post_init__(self):
        _claim(self.ref, self.guard)
        _exact(self.cgroup, CgroupIdentity)
        _choice(self.outcome, ('COMPLETE', 'UNKNOWN'))
        _tuple(self.members, ProcessBirth, 16)
        if len(set(self.members)) != len(self.members):
            _fail('duplicate cgroup member birth')


@dataclass(frozen=True)
class NameObservation:
    ref: ManifestRef
    guard: CurrentGuardScope
    resource: ResourceRecord
    outcome: str

    def __post_init__(self):
        _claim(self.ref, self.guard)
        _exact(self.resource, ResourceRecord)
        if self.resource.state != 'KNOWN':
            _fail('name observation cannot invent an unknown name')
        _choice(self.outcome, ('ABSENT', 'PRESENT', 'UNKNOWN'))


@dataclass(frozen=True)
class LiveIntentStatement:
    ref: ManifestRef
    guard: CurrentGuardScope
    intent_id: str

    def __post_init__(self):
        _claim(self.ref, self.guard)
        _scalar(_uuid, self.intent_id)


@dataclass(frozen=True)
class RuntimeResetStatement:
    """전용 OS_BOOT_RESET 진술. UUID/SHA는 실제 reset의 암호학적 증명이 아니다."""

    ref: ManifestRef
    guard: CurrentGuardScope
    proof_id: str
    proof_sha256: str

    def __post_init__(self):
        _claim(self.ref, self.guard)
        _scalar(_uuid, self.proof_id)
        _scalar(_sha, self.proof_sha256)

    @property
    def kind(self):
        return 'OS_BOOT_RESET'


@dataclass(frozen=True)
class FirstInstallStatement:
    """원 MISSING load와 명시 genesis에 결박된 설치 진술. 사용 이력은 d2가 검사한다."""

    load_id: str
    guard: CurrentGuardScope
    proof_id: str
    proof_sha256: str
    genesis: RecoveryEpisode

    def __post_init__(self):
        _scalar(_uuid, self.load_id)
        _exact(self.guard, CurrentGuardScope)
        _scalar(_uuid, self.proof_id)
        _scalar(_sha, self.proof_sha256)
        _exact(self.genesis, RecoveryEpisode)


@dataclass(frozen=True)
class GuardEvidence:
    """제공된 원 진술의 유한 immutable 집합. 부재한 진술을 성공 값으로 보충하지 않는다."""

    owners: tuple[OwnerObservation, ...] = ()
    cgroup: CgroupObservation | None = None
    names: tuple[NameObservation, ...] = ()
    live: LiveIntentStatement | None = None
    reset: RuntimeResetStatement | None = None
    install: FirstInstallStatement | None = None

    def __post_init__(self):
        _tuple(self.owners, OwnerObservation, 9)
        _tuple(self.names, NameObservation, 12)
        for value, expected in ((self.cgroup, CgroupObservation), (self.live, LiveIntentStatement),
                                (self.reset, RuntimeResetStatement), (self.install, FirstInstallStatement)):
            if value is not None:
                _exact(value, expected)


def _process_key(runtime, birth):
    # mount namespace 변경은 같은 process의 birth를 바꾸지 않는다.
    return runtime.boot_id, runtime.pid_ns_inode, birth.pid, birth.start_ticks


def _bound(manifest, guard, evidence):
    claims = evidence.owners + evidence.names
    for optional in (evidence.cgroup, evidence.live, evidence.reset):
        if optional is not None:
            claims += (optional,)
    if any(c.ref != manifest.ref or c.guard != guard for c in claims):
        return 'EVIDENCE_SCOPE_MISMATCH'
    if evidence.install is not None:
        return 'INSTALL_REQUIRES_MISSING'
    owner_keys, name_keys = set(), set()
    for item in evidence.owners:
        key = item.owner.role, item.owner.phase
        if key in owner_keys or item.owner not in manifest.known_owners:
            return 'OWNER_TARGET_MISMATCH'
        owner_keys.add(key)
        if (item.outcome == 'EXITED' and _process_key(manifest.runtime, item.owner.birth)
                == _process_key(guard.runtime, guard.birth)):
            return 'GUARD_CANNOT_ASSERT_SELF_EXITED'
    for item in evidence.names:
        if item.resource.slot in name_keys or item.resource not in manifest.known_resources:
            return 'NAME_TARGET_MISMATCH'
        name_keys.add(item.resource.slot)
    if evidence.cgroup is not None and evidence.cgroup.cgroup != manifest.cgroup:
        return 'CGROUP_TARGET_MISMATCH'
    if evidence.live is not None:
        if not manifest.intents or evidence.live.intent_id != manifest.intents[0].intent_id:
            return 'INTENT_TARGET_MISMATCH'
    return None


def _judge(load, guard, evidence):
    if load.kind in ('CORRUPT', 'IO_ERROR'):
        return 'BLOCKED', 'LOAD_UNAVAILABLE'
    if load.kind == 'MISSING':
        if evidence.owners or evidence.cgroup is not None or evidence.names or evidence.live is not None or evidence.reset is not None:
            return 'BLOCKED', 'MISSING_HAS_NO_MANIFEST_TARGETS'
        install = evidence.install
        if install is None or install.load_id != load.load_id or install.guard != guard:
            return 'BLOCKED', 'FIRST_INSTALL_EVIDENCE_REQUIRED'
        return 'FIRST_INSTALL_ELIGIBLE', None
    m = load.manifest
    mismatch = _bound(m, guard, evidence)
    if mismatch is not None:
        return 'BLOCKED', mismatch
    if evidence.reset is not None:
        if evidence.live is not None:
            return 'BLOCKED', 'CONFLICTING_ELIGIBILITY_PATHS'
        if m.runtime.boot_id == guard.runtime.boot_id:
            return 'BLOCKED', 'OS_RESET_REQUIRES_DIFFERENT_BOOT'
        return 'FRESH_RESET_ELIGIBLE', None
    if evidence.live is not None:
        supervisor = next(o for o in m.owners if o.role == 'S')
        alive = any(o.owner == supervisor and o.outcome == 'ALIVE' for o in evidence.owners)
        if (m.state == 'DIRTY' and m.resource(m.intents[0].slot).state == 'INTENT'
                and m.runtime == guard.runtime and m.service_instance == guard.service_instance
                and supervisor.birth == guard.birth and alive):
            return 'LIVE_INTENT_PENDING', None
        return 'BLOCKED', 'LIVE_INTENT_CONDITION_UNMET'
    if m.runtime != guard.runtime:
        return 'BLOCKED', 'RUNTIME_RESET_EVIDENCE_REQUIRED'
    if not m.complete_inventory:
        return 'BLOCKED', 'INVENTORY_INCOMPLETE'
    if len(evidence.owners) != len(m.known_owners):
        return 'BLOCKED', 'OWNER_EVIDENCE_INCOMPLETE'
    if any(o.outcome != 'EXITED' for o in evidence.owners):
        return 'BLOCKED', 'OWNER_NOT_EXITED'
    cg = evidence.cgroup
    if cg is None or cg.outcome != 'COMPLETE':
        return 'BLOCKED', 'CGROUP_EVIDENCE_INCOMPLETE'
    if any(member != guard.birth for member in cg.members):
        return 'BLOCKED', 'CGROUP_HAS_OTHER_MEMBERS'
    if len(evidence.names) != len(m.known_resources):
        return 'BLOCKED', 'NAME_EVIDENCE_INCOMPLETE'
    if any(n.outcome != 'ABSENT' for n in evidence.names):
        return 'BLOCKED', 'KNOWN_NAME_NOT_ABSENT'
    return 'OLD_CLEAN_ELIGIBLE', None


@dataclass(frozen=True, init=False)
class GuardDecision:
    """원 입력을 보존한 적격 정보. 직접 constructor도 kind/허가 flag를 받지 않는다."""

    load: LoadOutcome
    guard: CurrentGuardScope
    evidence: GuardEvidence
    kind: str
    reason: str | None
    predecessor: ResourceManifest | None
    episode: RecoveryEpisode | None

    def __init__(self, load, guard, evidence):
        _exact(load, LoadOutcome)
        _exact(guard, CurrentGuardScope)
        _exact(evidence, GuardEvidence)
        kind, reason = _judge(load, guard, evidence)
        predecessor = load.manifest
        episode = (predecessor.recovery_episode if predecessor is not None else
                   evidence.install.genesis if kind == 'FIRST_INSTALL_ELIGIBLE' else None)
        for name, value in (('load', load), ('guard', guard), ('evidence', evidence), ('kind', kind),
                            ('reason', reason), ('predecessor', predecessor), ('episode', episode)):
            object.__setattr__(self, name, value)


def evaluate_guard(load, guard, evidence):
    return GuardDecision(load, guard, evidence)
