"""첫 설치·실제 boot reset·관리된 full restart를 원 Store와 내구 세대에 연결한다.

같은 boot의 SSH/incomplete catalogue는 승격하지 않는다. 관리된 이전 세대는
원 이름/owner 관측과 고정 실행자 증거를 거쳐 CLEAN을 실제 출판한 뒤 새 DIRTY를
제안한다. 원 관측의 ref를 새 ref로 바꾸거나 소비 시각으로 기한을 갱신하지 않는다.
PUBLISHED 실행 기록은 불변 incarnation anchor다. inventory·episode·counter의 정본은
resource-set.json이며, 후속 WRITE마다 실행 기록을 복제하거나 manager를 호출하지 않는다.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import time
import uuid

from .native_resources import NativeResources
from .procfs_formats import parse_boot_id, parse_proc_stat
from .resource_guard import CgroupObservation, GuardEvidence, RuntimeResetStatement, evaluate_guard
from .resource_manifest import (CgroupIdentity, ManifestRef, ProcessBirth, RecoveryEpisode,
                                ResourceManifest, RuntimeScope, SLOTS)
from .resource_name_observation import ResourceNameObserver
from .resource_store import ResourceStore, StoreAck, StoreLoad
from .runtime_bootstrap import (BootstrapUnavailable, _cgroup_identity, consume_first_install,
                                read_execution_record, write_execution_record)
from .service_scope import SystemdServiceScope, EXECUTION_PROFILE, SLICE_PATH, UNIT, SLICE


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def _same(left, right):
    return _canonical(left) == _canonical(right)


def _record(directory):
    try:
        raw = read_execution_record(directory)
    except FileNotFoundError:
        return None, None
    value = json.loads(raw)
    fields = {'version', 'status', 'source_ref', 'service_instance', 'resource_epoch', 'supervisor',
              'runtime', 'recovery_episode', 'scope', 'admission'}
    if (type(value) is not dict or set(value) != fields or type(value['version']) is not int
            or value['version'] != 1 or value['status'] not in ('PREPARED', 'PUBLISHED')
            or _canonical(value) != raw):
        raise BootstrapUnavailable('execution record is not a supported canonical producer record')
    # version1의 입장 시점 사본 형식만 확인한다. PUBLISHED counter 권한으로 쓰지 않는다.
    _episode(value['recovery_episode'])
    return raw, value


def _check_time(issued_us, expires_us):
    if (type(issued_us) is not int or type(expires_us) is not int
            or not 0 <= issued_us < expires_us <= 2**64 - 1
            or not issued_us <= time.monotonic_ns() // 1000 < expires_us):
        raise BootstrapUnavailable('original admission request lifetime elapsed')


def _precreation(native_owner):
    if type(native_owner) is not NativeResources or native_owner.creation_attempted:
        raise BootstrapUnavailable('runtime admission must precede actual constructor attempts')


def _episode(value):
    if type(value) is not dict or set(value) != {'episode_id', 'service_attempts', 'role_attempts'}:
        raise BootstrapUnavailable('execution recovery episode is not complete')
    return RecoveryEpisode(value['episode_id'], value['service_attempts'], tuple(value['role_attempts']))


def _next_episode(previous):
    if previous.service_attempts >= 5 or any(count >= 5 for count in previous.role_attempts):
        raise BootstrapUnavailable('persistent service/role recovery budget exhausted')
    return RecoveryEpisode(previous.episode_id, previous.service_attempts + 1,
                           tuple(count + 1 for count in previous.role_attempts))


def _new_dirty(guard, resource_epoch, episode, cgroup):
    fields = dict(version=1,
        runtime={key: str(value) if type(value) is int else value for key, value in asdict(guard.runtime).items()},
        service_instance=guard.service_instance, resource_epoch=resource_epoch, revision='1', state='DIRTY',
        owners=[dict(role='S', phase='CURRENT', pid=guard.birth.pid, start_ticks=str(guard.birth.start_ticks))]
               + [dict(role=role, phase='CURRENT', pid=None, start_ticks=None) for role in ('A', 'M', 'D', 'TRACKER')],
        cgroup=dict(mode=cgroup.mode, path=cgroup.path, device=str(cgroup.device), inode=str(cgroup.inode)),
        resources={slot: dict(state='NOT_ATTEMPTED', name=None, object_id=None, size_bytes=None) for slot in SLOTS},
        intents=[], recovery_episode=dict(episode_id=episode.episode_id, service_attempts=episode.service_attempts,
                    role_attempts=dict(zip(('A', 'M', 'D'), episode.role_attempts))))
    return ResourceManifest(_canonical(fields))


def _execution_value(manifest, scope, admission):
    supervisor = next(owner for owner in manifest.owners if owner.role == 'S')
    return dict(version=1, status='PREPARED', source_ref=asdict(manifest.ref),
                service_instance=manifest.service_instance, resource_epoch=manifest.resource_epoch,
                supervisor=asdict(supervisor.birth), runtime=asdict(manifest.runtime),
                recovery_episode=asdict(manifest.recovery_episode), scope=None if scope is None else scope.fields,
                admission=admission)


def _record_manifest(record, manifest):
    """불변 실행 anchor의 범위만 검사한다. PUBLISHED counter 사본은 정본이 아니다."""
    supervisor = next(owner for owner in manifest.owners if owner.role == 'S')
    source = record['source_ref']
    try:
        if (type(source) is not dict or set(source) != {'raw_sha256', 'revision', 'service_instance', 'resource_epoch', 'runtime'}
                or type(source['runtime']) is not dict):
            raise ValueError('unsupported anchor ref fields')
        anchor = ManifestRef(source['raw_sha256'], source['revision'], source['service_instance'],
                             source['resource_epoch'], RuntimeScope(**source['runtime']))
    except (TypeError, ValueError) as error:
        raise BootstrapUnavailable('execution anchor ref is invalid') from error
    if (record['service_instance'] != manifest.service_instance or record['resource_epoch'] != manifest.resource_epoch
            or not _same(record['supervisor'], None if supervisor.birth is None else asdict(supervisor.birth))
            or not _same(record['runtime'], asdict(manifest.runtime))
            or (anchor.service_instance, anchor.resource_epoch, anchor.runtime) != (
                manifest.service_instance, manifest.resource_epoch, manifest.runtime)
            or anchor.revision > manifest.revision
            or anchor.revision == manifest.revision and anchor.raw_sha256 != manifest.raw_sha256):
        raise BootstrapUnavailable('original catalogue and execution record disagree')
    scope = record['scope']
    if scope is not None and (type(scope) is not dict
            or not _same(scope.get('cgroup'), asdict(manifest.cgroup))
            or not _same(scope.get('main_birth'), record['supervisor'])
            or scope.get('boot_id') != manifest.runtime.boot_id):
        raise BootstrapUnavailable('execution anchor scope differs from its resource generation')
    if record['status'] == 'PREPARED' and (anchor != manifest.ref
            or not _same(record['recovery_episode'], asdict(manifest.recovery_episode))):
        raise BootstrapUnavailable('unconfirmed preparation differs from its exact original target')


def _old_execution(record, manifest, current_scope):
    if record is None or record['status'] != 'PUBLISHED' or current_scope is None:
        raise BootstrapUnavailable('same-boot restart requires its published managed execution record')
    _record_manifest(record, manifest)
    supervisor = next(owner for owner in manifest.owners if owner.role == 'S')
    old, current = record['scope'], current_scope.fields
    if (type(old) is not dict or old.get('unit') != UNIT or old.get('slice') != SLICE
            or old.get('execution_profile') != EXECUTION_PROFILE
            or old.get('boot_id') != manifest.runtime.boot_id
            or not _same(old.get('main_birth'), asdict(supervisor.birth))
            or not _same(old.get('cgroup'), asdict(manifest.cgroup))
            or not _same(old.get('manager_birth'), current['manager_birth'])
            or old.get('invocation_id') == current['invocation_id']
            or type(old.get('invocation_id')) is not str or len(old['invocation_id']) != 32
            or any(char not in '0123456789abcdef' for char in old['invocation_id'])
            or old['invocation_id'] == '0' * 32
            or manifest.cgroup != current_scope.cgroup or manifest.cgroup.path != SLICE_PATH):
        raise BootstrapUnavailable('old and current serialized manager executions do not bind the same persistent slice')


def _legacy_names(names, manifest, guard):
    if (names.guard != guard or names.original_load.outcome.manifest.ref != manifest.ref
            or names.original_load.outcome.manifest.raw_bytes != manifest.raw_bytes
            or any(owner.outcome != 'EXITED' for batch in (names.before, names.after) for owner in batch.owners)
            or any(name.outcome != 'ABSENT' for name in names.names)):
        raise BootstrapUnavailable('legacy handover requires original owners EXITED and known names ABSENT at both endpoints')


@dataclass(frozen=True)
class RuntimeAdmission:
    manifest: ResourceManifest
    decision: object
    installation_id: str | None
    proposal: object
    name_observation: object = None
    predecessor_clean_ack: object = None
    prior_name_observation: object = None


def consume_runtime_state(store, observation, *, resource_epoch, request_id, service_scope=None,
                          native_owner=None, issued_us=None, expires_us=None):
    """S-log 원 요청 안에서 admission을 소비하고 같은 원 ID의 fresh DIRTY proposal을 돌려준다.

    현재 실제 이름 관측 뒤 추가 load로 old evidence를 retarget하지 않는다. 이전 CLEAN
    출판은 별도 실제 단계이고, 그 실제 post-load만 fresh proposal의 전신으로 쓴다.
    최종 publish와 publication hook은 S-log가 수행한다.
    """
    if type(store) is not ResourceStore:
        raise BootstrapUnavailable('admission requires the actual owning store')
    _precreation(native_owner)
    _check_time(issued_us, expires_us)
    if service_scope is not None and type(service_scope) is not SystemdServiceScope:
        raise BootstrapUnavailable('unsupported service execution owner')
    old_record_raw, old_record = _record(store.directory)
    kind = observation.latest_load.outcome.kind
    scope = None if service_scope is None else service_scope.observe(observation.guard, native_owner)
    if kind == 'MISSING':
        if old_record is not None:
            raise BootstrapUnavailable('MISSING cannot erase an existing execution history')
        _check_time(issued_us, expires_us)
        first = consume_first_install(store, observation, resource_epoch=resource_epoch, request_id=request_id,
                                      cgroup_identity=None if scope is None else scope.cgroup)
        value = _execution_value(ResourceManifest(first.proposal.target_bytes), scope,
                                dict(kind='FIRST_INSTALL', request_id=request_id, predecessor_ref=None))
        _check_time(issued_us, expires_us)
        write_execution_record(store.directory, value)
        return RuntimeAdmission(first.manifest, first.decision, first.installation_id, first.proposal)
    if kind != 'VALID':
        raise BootstrapUnavailable('missing/corrupt/unknown source cannot authorize runtime resources')
    original = observation.latest_load.outcome.manifest
    legacy_cgroup = prior_names = None
    if (scope is not None and original.runtime == observation.guard.runtime and original.cgroup != scope.cgroup):
        if old_record is not None:
            if old_record['status'] != 'PUBLISHED' or old_record['scope'] is not None:
                raise BootstrapUnavailable('legacy handover cannot discard unconfirmed or managed execution history')
            _record_manifest(old_record, original)
        with service_scope.pin_legacy(original, observation.guard) as pinned:
            prior_names = ResourceNameObserver(store, observation.guard.service_instance).observe()
            _legacy_names(prior_names, original, observation.guard)
            _check_time(issued_us, expires_us)
            legacy_cgroup = pinned.wait_dead(store.directory, request_id=request_id,
                                            issued_us=issued_us, expires_us=expires_us)
            names = ResourceNameObserver(store, observation.guard.service_instance).observe()
            _legacy_names(names, original, observation.guard)
    else:
        names = ResourceNameObserver(store, observation.guard.service_instance).observe()
    manifest = names.original_load.outcome.manifest
    if manifest.ref != original.ref or manifest.raw_bytes != original.raw_bytes:
        raise BootstrapUnavailable('original catalogue changed before admission')
    guard = names.guard
    clean_ack = None
    receipt = names.latest_load
    episode = manifest.recovery_episode
    if manifest.runtime.boot_id != guard.runtime.boot_id:
        with open('/proc/sys/kernel/random/boot_id', 'rb') as source:
            actual_boot = parse_boot_id(source.read(65))
        if actual_boot != guard.runtime.boot_id:
            raise BootstrapUnavailable('actual current boot differs from validated tmpfs runtime')
        if old_record is not None:
            if old_record['status'] == 'PUBLISHED':
                _record_manifest(old_record, manifest)
            else:
                admission = old_record['admission']
                if (type(admission) is not dict or not (
                        _same(old_record['source_ref'], asdict(manifest.ref))
                        or _same(admission.get('predecessor_ref'), asdict(manifest.ref)))):
                    raise BootstrapUnavailable('unconfirmed admission history does not bind this predecessor')
                episode = _episode(old_record['recovery_episode'])
                previous = manifest.recovery_episode
                if (episode.episode_id != previous.episode_id or episode.service_attempts < previous.service_attempts
                        or any(left < right for left, right in zip(episode.role_attempts, previous.role_attempts))):
                    raise BootstrapUnavailable('boot reset cannot discard an unknown recovery episode or attempt')
        proof = dict(kind='OS_BOOT_RESET', old_ref=asdict(manifest.ref), current_runtime=asdict(guard.runtime),
                     source_load_ids=[value.outcome.load_id for value in (names.before.original_load,
                        names.before.latest_load, names.after.original_load, names.after.latest_load)])
        reset = RuntimeResetStatement(manifest.ref, guard, str(uuid.uuid4()), hashlib.sha256(_canonical(proof)).hexdigest())
        decision = evaluate_guard(receipt.outcome, guard, GuardEvidence(reset=reset))
    else:
        if not manifest.complete_inventory or scope is None:
            raise BootstrapUnavailable('same-boot legacy or incomplete execution remains fenced')
        if legacy_cgroup is None:
            _old_execution(old_record, manifest, scope)
            cgroup = CgroupObservation(manifest.ref, guard, manifest.cgroup, 'COMPLETE', (guard.birth,))
        else:
            cgroup = legacy_cgroup
        decision = evaluate_guard(receipt.outcome, guard,
            GuardEvidence(owners=names.after.owners, names=names.names, cgroup=cgroup))
        if decision.kind != 'OLD_CLEAN_ELIGIBLE':
            raise BootstrapUnavailable('old execution is not clean eligible: ' + str(decision.reason))
        if manifest.state != 'CLEAN':
            clean_fields = manifest.fields
            clean_fields.update(state='CLEAN', revision=str(manifest.revision + 1))
            clean_target = ResourceManifest(_canonical(clean_fields))
            _check_time(issued_us, expires_us)
            clean_proposal = store.propose(receipt, clean_target, str(uuid.uuid5(uuid.UUID(request_id), 'predecessor-clean')))
            _check_time(issued_us, expires_us)
            result = store.publish(clean_proposal)
            if result.kind != 'DURABLE':
                raise BootstrapUnavailable('predecessor CLEAN publication remains unconfirmed')
            clean_ack = result.ack
            receipt = store.load()
            if receipt.outcome.kind != 'VALID' or receipt.outcome.manifest.ref != clean_ack.target_ref:
                raise BootstrapUnavailable('actual CLEAN post-load differs from its ACK')
            if old_record is None:
                advanced = _execution_value(clean_target, None,
                    dict(kind='LEGACY_HANDOVER', request_id=request_id, predecessor_ref=asdict(manifest.ref)))
                advanced['status'] = 'PUBLISHED'
                _check_time(issued_us, expires_us)
                write_execution_record(store.directory, advanced)
                old_record_raw = _canonical(advanced)
    if decision.kind not in ('FRESH_RESET_ELIGIBLE', 'OLD_CLEAN_ELIGIBLE'):
        raise BootstrapUnavailable('actual runtime admission was rejected: ' + str(decision.reason))
    if scope is None:
        raw_cgroup = _cgroup_identity()
        cgroup = CgroupIdentity(raw_cgroup['mode'], raw_cgroup['path'], int(raw_cgroup['device']), int(raw_cgroup['inode']))
    else:
        cgroup = scope.cgroup
    target = _new_dirty(guard, resource_epoch, _next_episode(episode), cgroup)
    _check_time(issued_us, expires_us)
    proposal = store.propose(receipt, target, request_id)
    value = _execution_value(ResourceManifest(proposal.target_bytes), scope,
                            dict(kind=decision.kind, request_id=request_id,
                                 predecessor_ref=asdict(proposal.expected.outcome.manifest.ref)))
    _check_time(issued_us, expires_us)
    write_execution_record(store.directory, value, expected=old_record_raw)
    _precreation(native_owner)
    _check_time(issued_us, expires_us)
    return RuntimeAdmission(target, decision, None, proposal, names, clean_ack, prior_names)


def record_runtime_publication(store, ack, receipt, *, service_scope=None):
    """원 ACK/post-load를 실행 anchor에 결박한다. 최초 PREPARED 확정만 sidecar를 쓴다.

    후속 durable inventory/healthy-counter WRITE의 정본은 resource-set.json이다.
    같은 incarnation의 PUBLISHED anchor는 manager helper나 두 번째 fsync 없이 보존한다.
    """
    if type(store) is not ResourceStore or type(ack) is not StoreAck or type(receipt) is not StoreLoad:
        raise BootstrapUnavailable('execution publication requires original typed store results')
    if (receipt.lock_nonce != store.lock_nonce or ack.lock_nonce != store.lock_nonce
            or receipt.outcome.kind != 'VALID' or receipt.outcome.manifest.ref != ack.target_ref
            or receipt.outcome.manifest.raw_bytes != ack.proposal.target_bytes):
        raise BootstrapUnavailable('execution record cannot use a historical or retargeted ACK')
    manifest = receipt.outcome.manifest
    current = parse_proc_stat(_read_self_stat(), os.getpid())
    supervisor = next(owner for owner in manifest.owners if owner.role == 'S')
    if supervisor.birth != ProcessBirth(current.pid, current.start_ticks):
        raise BootstrapUnavailable('published supervisor differs from the actual current process')
    raw, record = _record(store.directory)
    if record is None or (record['service_instance'], record['resource_epoch']) != (manifest.service_instance, manifest.resource_epoch):
        raise BootstrapUnavailable('execution publication has no matching admission preparation')
    _record_manifest(record, manifest)
    if record['status'] == 'PUBLISHED':
        if service_scope is not None:
            if not _same(record['scope'], service_scope.bound_publication_facts(manifest).fields):
                raise BootstrapUnavailable('immutable execution anchor differs from its admitted invocation')
        return
    if service_scope is not None:
        if not _same(record['scope'], service_scope.publication_facts(manifest).fields):
            raise BootstrapUnavailable('prepared execution scope differs from its actual manager invocation')
    value = dict(record, status='PUBLISHED', source_ref=asdict(ack.target_ref),
                 recovery_episode=asdict(manifest.recovery_episode))
    write_execution_record(store.directory, value, expected=raw)


def _read_self_stat():
    with open('/proc/self/stat', 'rb') as source:
        return source.read(4097)
