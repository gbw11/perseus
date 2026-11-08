"""실제 Bridge method/originals의 좁은 Scene 의미 검사.

등록자는 같은 compute owner가 보유한 실제 ParkingBridge.method를 제공한다.
등록된 정책 원문과 명시된 full scope만 비교하며 저장 SHA 자체를 승인으로 보지 않는다.
반환 mask는 원 raw key의 종류다. freshness/HISTORY/양성 FREE/Scene 권위는 caller가
기존 closure 정책으로 따로 검사한다. module import에서는 Scene을 import하지 않는다.
"""

from dataclasses import asdict, dataclass, fields
import struct
from uuid import UUID

from ..mapping.parking_catalog import AssociationProfile
from ..perception.calibration import BodyModel
from ..perception.inputs import FrameStamp
from ..perception.parking import OccupancyEvidence, ParkingProfile, SlotSource
from ..storage.contracts import canonical, decimal, digest, json_bytes
from ..storage.geometry_scope import same_catalog_geometry
from ..storage.records import ContextRecord, EvidenceTable, PROVENANCE, ProvenanceRecord


class ParkingSceneError(ValueError):
    """원 문맥·정책·source·부모가 명시된 parking 프로필과 맞지 않는다."""


METHOD_KEYS = frozenset(('profile', 'method_manifest_sha256', 'storage_context_sha256',
    'calibration_profile_sha256', 'vehicle_bundle_sha256', 'coordinate', 'map_frame_id',
    'body', 'geometry_profile', 'association_profile'))
ORIGINAL_KEYS = frozenset(('profile', 'method_context_sha256', 'storage_context_sha256',
                           'catalog_revision', 'sources', 'entries'))


@dataclass(frozen=True, init=False)
class ApprovedParkingMethods:
    actual_method: ContextRecord
    scope: object
    prototype: bytes

    def __new__(cls, *args, **kwargs):
        raise ParkingSceneError('actual validated Bridge method registration required')


@dataclass(frozen=True, init=False)
class ParkingRecordSources:
    scope: object
    actual_mask: int
    wire_mask: int
    profile: str
    method: ContextRecord

    def __new__(cls, *args, **kwargs):
        raise ParkingSceneError('actual parking record semantic validation required')


def _make(cls, **values):
    result = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def _scopes(value):
    from .scene import SceneEvidenceScope
    if (type(value) is not tuple or not 1 <= len(value) <= 65
            or any(type(scope) is not SceneEvidenceScope for scope in value)):
        raise ParkingSceneError('bounded actual current/history SceneEvidenceScope tuple required')
    return value


def _typed(cls, value, tuple_fields=()):
    names = {item.name for item in fields(cls) if item.init}
    if type(value) is not dict or set(value) != names:
        raise ParkingSceneError('exact typed parking profile/source fields required')
    own = dict(value)
    for name in tuple_fields:
        if type(own[name]) is not list:
            raise ParkingSceneError('original canonical tuple field must be a JSON array')
        own[name] = tuple(own[name])
    return cls(**own)


def _method(context, scopes):
    if type(context) is not ContextRecord:
        raise ParkingSceneError('actual immutable method ContextRecord required')
    value = json_bytes(context.payload, 4096)
    body = value['body']
    if value['kind'] != 'METRIC' or set(body) != METHOD_KEYS or body['profile'] != 'parking-method-1':
        raise ParkingSceneError('exact parking-method-1 grammar required')
    for name in ('method_manifest_sha256', 'storage_context_sha256', 'calibration_profile_sha256', 'vehicle_bundle_sha256'):
        digest(body[name])
    candidates = [scope for scope in scopes if scope.context.sha256 == body['storage_context_sha256']]
    if not candidates:
        raise ParkingSceneError('method requires an explicit original full StorageContext')
    scope = candidates[0]
    if any((candidate.context != scope.context or candidate.raw_binding != scope.raw_binding
            or candidate.calibration != scope.calibration) for candidate in candidates[1:]):
        raise ParkingSceneError('ambiguous original method scope')
    if (body['method_manifest_sha256'] != scope.method_manifest_sha256
            or body['calibration_profile_sha256'] != scope.calibration.sha256
            or body['vehicle_bundle_sha256'] != scope.context.calibration
            or body['vehicle_bundle_sha256'] != scope.calibration.vehicle_bundle_sha256
            or body['coordinate'] != 'MAP_X_FORWARD_Y_LEFT_REAR_AXLE'
            or body['map_frame_id'] != 'odom:' + scope.context.odom_epoch):
        raise ParkingSceneError('method asset/calibration/map coordinate differs from its original scope')
    body_model = _typed(BodyModel, body['body'])
    geometry = _typed(ParkingProfile, body['geometry_profile'], ('width_m', 'depth_m'))
    association = _typed(AssociationProfile, body['association_profile'], ('pose_steps',))
    if (body_model != scope.calibration.body or canonical(asdict(body_model)) != canonical(body['body'])
            or canonical(asdict(geometry)) != canonical(body['geometry_profile'])
            or canonical(asdict(association)) != canonical(body['association_profile'])):
        raise ParkingSceneError('method typed geometry/body/association is not canonical')
    return scope, canonical({name: item for name, item in body.items() if name != 'storage_context_sha256'})


def validate_parking_method(actual_method, scopes):
    """실제 Bridge method를 등록한다. 다른 역사 method는 같은 정책 prototype이어야 한다.

    이 입력은 caller의 실제 producer port다. 임의 저장 method를 이 함수에 재등록하여
    자기 자신을 승인하는 데 사용하면 안 된다. 정책 default를 채우지 않는다.
    """
    try:
        scope, prototype = _method(actual_method, _scopes(scopes))
        return _make(ApprovedParkingMethods, actual_method=actual_method, scope=scope, prototype=prototype)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ParkingSceneError(str(error)) from error


def _approved_method(method, scopes, approved):
    scope, prototype = _method(method, scopes)
    if prototype != approved.prototype:
        raise ParkingSceneError('stored method differs from the actual approved Bridge policy')
    return scope


def _source(value, scope):
    if type(value) is not dict or set(value) != {item.name for item in fields(SlotSource)}:
        raise ParkingSceneError('exact original SlotSource required')
    stamp = dict(value['stamp']) if type(value['stamp']) is dict else None
    if stamp is None or set(stamp) != {item.name for item in fields(FrameStamp)}:
        raise ParkingSceneError('exact original source FrameStamp required')
    if stamp['captured_us'] is not None:
        if type(stamp['captured_us']) is not list:
            raise ParkingSceneError('canonical original capture interval array required')
        stamp['captured_us'] = tuple(stamp['captured_us'])
    own = dict(value); own['stamp'] = FrameStamp(**stamp)
    source = SlotSource(**own)
    raw = scope.raw_binding.fields
    if (not source.qualified or source.input_kind != scope.calibration.kind
            or source.calibration_id != scope.calibration.sha256 or source.map_epoch != scope.context.map_epoch
            or source.frame_id != 'odom:' + scope.context.odom_epoch or source.odom_epoch != scope.context.odom_epoch
            or source.stamp.key[:3] != (raw['acquisition_worker'], raw['stream_instances']['rgb_video'], raw['clock_epoch'])
            or source.stamp.captured_us is None):
        raise ParkingSceneError('original slot source differs from its full camera/map/clock scope')
    return source


def _raw_masks(record, scope):
    data = PROVENANCE.unpack(record.payload); kind, count = data[2:4]
    if kind not in (3, 4):
        raise ParkingSceneError('parking method supports only original GEOMETRY/AGGREGATE records')
    streams = scope.raw_binding.fields['stream_instances']
    known = {UUID(streams['rgb_video']).bytes: (1, 2), UUID(streams['depth_right']).bytes: (2, 1)}
    for name in ('nn_input0', 'nn_output0'):
        if name in streams:
            known[UUID(streams[name]).bytes] = (4, 0)
    known[UUID(scope.context.geometry_origin_fields['device_session']).bytes] = (16, 0)
    for _, leaf, instance, _ in scope._tof:
        if UUID(instance).bytes in known:
            raise ParkingSceneError('sensor instance collides with another registered raw source')
        known[UUID(instance).bytes] = (8, 4 << leaf.sensor_id)
    actual = wire = 0
    for index in range(count):
        instance, _, _ = struct.unpack_from('<16sQQ', data[-3], index * 32)
        if instance not in known:
            raise ParkingSceneError('parking raw key is outside original camera/ToF/Pi inventory')
        bit, sensor = known[instance]; actual |= bit; wire |= sensor
    if kind == 3 and not actual & 1:
        raise ParkingSceneError('original parking geometry requires its named RGB raw key')
    if record.source_mask & 32:
        if kind != 4 or not record.parents:
            raise ParkingSceneError('retained aggregate requires actual parent closure')
        actual |= 32
    return actual, wire


def _originals(record, context, contexts, records, scopes, approved):
    value = json_bytes(context.payload, 4096); body = value['body']
    if value['kind'] != 'METRIC' or set(body) != ORIGINAL_KEYS or body['profile'] != 'parking-originals-1':
        raise ParkingSceneError('exact parking-originals-1 grammar required')
    digest(body['method_context_sha256']); digest(body['storage_context_sha256'])
    decimal(body['catalog_revision'], positive=True)
    method = contexts.get(body['method_context_sha256'])
    if method is None:
        raise ParkingSceneError('original method ContextRecord dependency is missing')
    scope = _approved_method(method, scopes, approved)
    if scope.context.sha256 != body['storage_context_sha256']:
        raise ParkingSceneError('originals and method refer to different full StorageContexts')
    if type(body['sources']) is not list or type(body['entries']) is not list:
        raise ParkingSceneError('original source/entry arrays required')
    sources = {}
    for item in body['sources']:
        if type(item) is not dict or set(item) != {'sha256', 'fields'}:
            raise ParkingSceneError('exact original source entry required')
        digest(item['sha256']); source = _source(item['fields'], scope)
        if source.sha256 != item['sha256'] or source.sha256 in sources:
            raise ParkingSceneError('original SlotSource SHA differs or repeats')
        sources[source.sha256] = source
    selected = None; ids = set()
    for entry in body['entries']:
        if type(entry) is not dict or set(entry) != {'provenance_id', 'geometry_source', 'occupancy'}:
            raise ParkingSceneError('exact original catalog entry required')
        identity = decimal(entry['provenance_id'], positive=True)
        if identity in ids or entry['geometry_source'] not in sources:
            raise ParkingSceneError('duplicate provenance entry or missing geometry source')
        ids.add(identity)
        occupancy = entry['occupancy']
        if occupancy is not None:
            if (type(occupancy) is not dict or set(occupancy) != {'source', 'fields'}
                    or occupancy['source'] not in sources):
                raise ParkingSceneError('original occupancy source is missing')
            names = {item.name for item in fields(OccupancyEvidence) if item.init} - {'source'}
            if type(occupancy['fields']) is not dict or set(occupancy['fields']) != names:
                raise ParkingSceneError('exact typed original occupancy fields required')
            own = dict(occupancy['fields'])
            if type(own['required_region']) is not list:
                raise ParkingSceneError('original occupancy region array required')
            own['required_region'] = tuple(tuple(point) for point in own['required_region'])
            OccupancyEvidence(source=sources[occupancy['source']], **own)
        # 사용하지 않는 옛 group entry가 존재할 수 있다. 현재 root만 존재/연결을 요구한다.
        if identity == record.provenance_id:
            selected = sources[entry['geometry_source']]
    data = PROVENANCE.unpack(record.payload)
    if selected is None or data[2:4] != (4, 0) or not record.parents:
        raise ParkingSceneError('originals requires its own derived aggregate and actual parents')
    parents = [records[parent] for parent in record.parents]
    mask = 0; matched = False
    for parent in parents:
        mask |= parent.source_mask
        values = PROVENANCE.unpack(parent.payload)
        if values[2] == 3:
            parent_context = contexts[parent.context_sha256]
            parent_scope = _approved_method(parent_context, scopes, approved)
            for index in range(values[3]):
                instance, sequence, _ = struct.unpack_from('<16sQQ', values[-3], index * 32)
                if (instance, sequence) == (UUID(selected.stamp.stream_id).bytes, selected.stamp.sequence):
                    if (parent_scope.raw_binding.sha256 != scope.raw_binding.sha256
                            or not same_catalog_geometry(parent_scope.context, scope.context, 'SLOT')):
                        raise ParkingSceneError('geometry parent belongs to another original full source scope')
                    matched = True
    if (record.source_mask != mask or record.pi_lower != min(parent.pi_lower for parent in parents)
            or record.pi_upper != max(parent.pi_upper for parent in parents)
            or record.observation_sequence != max(parent.observation_sequence for parent in parents)
            or not matched):
        raise ParkingSceneError('aggregate mask/time/sequence or original geometry parent differs')
    return scope, method


def record_sources(record, context_record, *, evidence, scopes, approved_methods):
    """원 root와 전체 부모를 검산하고 root 자체의 실제 raw mask만 반환한다.

    부모 mask를 root의 direct sensor mask로 합치지 않는다. 따라서 originals는 항상
    (0,0)이며 caller가 원 parent를 FRESH/HISTORY 역할로 계속 방문해야 한다.
    """
    try:
        scopes = _scopes(scopes)
        if (type(record) is not ProvenanceRecord or type(context_record) is not ContextRecord
                or type(evidence) is not EvidenceTable or type(approved_methods) is not ApprovedParkingMethods):
            raise ParkingSceneError('actual record/context/evidence/approved method registry required')
        records = {item.provenance_id: item for item in evidence.provenance}
        contexts = {item.sha256: item for item in evidence.contexts}
        if records.get(record.provenance_id) != record or contexts.get(record.context_sha256) != context_record:
            raise ParkingSceneError('original root/context is outside actual evidence')
        needed = set(); pending = [record.provenance_id]
        while pending:
            identity = pending.pop()
            if identity in needed:
                continue
            row = records.get(identity)
            if row is None or row.context_sha256 not in contexts:
                raise ParkingSceneError('original parent/context closure is incomplete')
            needed.add(identity); pending.extend(row.parents)
        result = None
        for identity in sorted(needed):
            row = records[identity]; context = contexts[row.context_sha256]
            value = json_bytes(context.payload, 4096); profile = value['body'].get('profile')
            if profile == 'parking-method-1':
                scope = _approved_method(context, scopes, approved_methods)
                actual, wire = _raw_masks(row, scope); method = context
            elif profile == 'parking-originals-1':
                scope, method = _originals(row, context, contexts, records, scopes, approved_methods)
                actual = wire = 0
            elif profile == 'scene-source-context-1':
                matched = None
                for original in scopes:
                    try:
                        original_scope, _ = original._record_scope(context)
                        masks = original.record_sources(row, context)
                    except ValueError:
                        continue
                    matched = original_scope, masks; break
                if matched is None:
                    raise ParkingSceneError('parent source is outside known original semantic scopes')
                scope, (actual, wire) = matched; method = None
            else:
                raise ParkingSceneError('unsupported parking parent semantic profile')
            if PROVENANCE.unpack(row.payload)[5] != UUID(scope.context.pi_host_boot).bytes:
                raise ParkingSceneError('original parking parent Pi host differs')
            available = actual
            for parent in row.parents:
                available |= records[parent].source_mask
            if row.source_mask & available != row.source_mask:
                raise ParkingSceneError('parking mask lacks an actual named raw/parent witness')
            if identity == record.provenance_id:
                if profile not in ('parking-method-1', 'parking-originals-1'):
                    raise ParkingSceneError('parking consumer requires a known parking root profile')
                result = _make(ParkingRecordSources, scope=scope, actual_mask=actual,
                               wire_mask=wire, profile=profile, method=method)
        return result
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ParkingSceneError(str(error)) from error
