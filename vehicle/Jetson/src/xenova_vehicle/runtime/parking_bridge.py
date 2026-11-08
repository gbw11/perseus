"""M의 실제 인지 결과와 D의 주차 catalog 사본을 연결한다.

파일/SQL/소켓/fsync·thread는 열지 않는다. 원 pose/clock/Pi interval/부모 근거는
caller가 제공한다. 시각0·임의 ID·scene/최종 목표·추진 허가는 만들지 않는다.
"""

from dataclasses import asdict, dataclass, field, fields, replace
from hashlib import sha256
import math
import struct
from uuid import UUID

from ..mapping.parking_catalog import AssociationProfile, SlotCatalog
from ..mapping.registration import PoseBox
from ..perception.calibration import BodyModel, CalibrationProfile
from ..perception.inputs import FrameStamp
from ..perception.parking import (ParkingProfile, ParkingAisle, SlotSource, SlotGeometryResult,
    OccupancyEvidence, VALID, PARTIAL, AMBIGUOUS, UNKNOWN, CLEAR_REGION, GEOMETRY, AMBIGUOUS_REASON,
    SOURCE_UNAVAILABLE, ClearanceMap, extract_slots, evaluate_occupancy, to_map, goal_candidates, polygons_touch)
from ..storage.contracts import (StorageContext, StorageJob, CatalogHeadSnapshot, CommitReceipt,
    ProvenanceRange, ProvenanceRequest, canonical, json_bytes, uint, uuid, digest)
from ..storage.port import CheckpointRequest
from ..storage.records import (ContextRecord, ProvenanceRecord, EvidenceTable, CheckpointPayload,
    LoadedCheckpoint, PROVENANCE)


class ParkingNotReady(RuntimeError):
    """원 pose/저장 범위/parent closure나 유한 사본 공간이 아직 준비되지 않았다."""


def _context_closure_keys(contexts, required):
    """직접 source와 알려진 originals→method edge만 보존한다. asset SHA는 edge가 아니다."""
    required = set(required)
    for key in tuple(required):
        context = contexts.get(key)
        if context is None or context.sha256 != key:
            raise ParkingNotReady('original context closure is missing')
        value = json_bytes(context.payload, 4096); body = value['body']
        if body.get('profile') != 'parking-originals-1':
            continue
        names = {'profile', 'method_context_sha256', 'storage_context_sha256',
            'catalog_revision', 'sources', 'entries'}
        if (value['kind'] != 'METRIC' or set(body) != names
                or type(body['sources']) is not list or type(body['entries']) is not list):
            raise ParkingNotReady('invalid exact parking originals context')
        method_sha = digest(body['method_context_sha256'])
        storage_sha = digest(body['storage_context_sha256'])
        method = contexts.get(method_sha)
        if method is None or method.sha256 != method_sha:
            raise ParkingNotReady('original method context closure is missing')
        original = json_bytes(method.payload, 4096); method_body = original['body']
        method_names = {'profile', 'method_manifest_sha256', 'storage_context_sha256',
            'calibration_profile_sha256', 'vehicle_bundle_sha256', 'coordinate',
            'map_frame_id', 'body', 'geometry_profile', 'association_profile'}
        if (original['kind'] != 'METRIC' or set(method_body) != method_names
                or method_body['profile'] != 'parking-method-1'
                or method_body['storage_context_sha256'] != storage_sha):
            raise ParkingNotReady('original method context does not match originals')
        # method에는 추가 ContextRecord edge가 없다. 과거 원문을 현재 method로 바꾸지 않는다.
        required.add(method_sha)
    return required


@dataclass(frozen=True)
class MetricEvidence:
    """실제 원 입력 key/부모와 이미 환산한 Pi 구간. constructor 자체는 시계 승인이 아니다."""

    frame_keys: tuple
    pi_host_boot: str
    pi_interval: tuple
    observation_sequence: int
    source_mask: int
    raw_keys: tuple = ()
    parent_ids: tuple = ()

    def __post_init__(self):
        uuid(self.pi_host_boot); uint(self.observation_sequence, positive=True)
        if type(self.frame_keys) is not tuple or not 1 <= len(self.frame_keys) <= 4 or any(type(k) is not tuple or len(k) != 4 for k in self.frame_keys):
            raise ValueError('bounded original stream keys required')
        if type(self.pi_interval) is not tuple or len(self.pi_interval) != 2:
            raise ValueError('original Pi capture interval required')
        for v in self.pi_interval:
            uint(v)
        if self.pi_interval[0] > self.pi_interval[1] or type(self.source_mask) is not int or not 1 <= self.source_mask <= 63:
            raise ValueError('invalid original Pi/source interval')
        if (type(self.raw_keys) is not tuple or len(self.raw_keys) > 4 or type(self.parent_ids) is not tuple
                or len(self.parent_ids) > 4 or not self.raw_keys and not self.parent_ids):
            raise ValueError('actual raw keys or parent evidence required')
        for instance, sequence, device_us in self.raw_keys:
            uuid(instance); uint(sequence); uint(device_us)
        for identity in self.parent_ids:
            uint(identity, positive=True)
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError('duplicate provenance parents')


@dataclass(frozen=True)
class ParkingUpdate:
    geometry: SlotGeometryResult | None
    association: object
    geometry_updated: bool
    occupancy_updated: bool
    allocation_wait: bool
    provenance_wait: bool
    reasons: tuple
    map_revision: int


@dataclass(frozen=True)
class WireProvenanceGrant:
    """실제 RANGE_RESULT 수신 근거다. wire에 없는 SQL commit stamp는 담지 않는다."""

    request: ProvenanceRequest
    start_inclusive: int
    end_exclusive: int
    highwater_after: int
    reply_bytes: bytes
    issued_us: int
    expires_us: int
    received_us: int

    def __post_init__(self):
        from .local_envelope import decode_structure
        from .storage_messages import validate_body
        if type(self.request) is not ProvenanceRequest:
            raise ValueError('original range request required')
        for value in (self.start_inclusive, self.end_exclusive, self.highwater_after, self.issued_us, self.expires_us, self.received_us):
            uint(value)
        if not 0 < self.start_inclusive < self.end_exclusive == self.highwater_after or self.end_exclusive - self.start_inclusive != self.request.block_count:
            raise ValueError('invalid original wire range bounds')
        packet = decode_structure(self.reply_bytes); fields = packet.fields; payload = fields['payload']
        if (fields['kind'] != 'DB_JOB' or fields['binding_domain'] != 'STORAGE' or type(payload) is not dict
                or set(payload) != {'op', 'body'} or payload.get('op') != 'RANGE_RESULT'):
            raise ValueError('original RANGE_RESULT bytes required')
        body = payload['body']; validate_body('RANGE_RESULT', body)
        if body['result'] not in ('GRANTED', 'REPLAY'):
            raise ValueError('unconfirmed wire result has no usable range')
        if (body['database_uuid'], body['mapper_worker'], body['reservation_nonce'], int(body['start_inclusive']),
                int(body['end_exclusive']), int(body['highwater_after'])) != (self.request.job.context.database_uuid,
                self.request.job.context.mapper_worker, self.request.reservation_nonce, self.start_inclusive, self.end_exclusive, self.highwater_after):
            raise ValueError('wire range values differ from original bytes/request')
        if (int(fields['issued_j_us']), int(fields['expires_j_us'])) != (self.issued_us, self.expires_us):
            raise ValueError('range lifetime differs from original wire bytes')

    def current(self, request, context_sha256, now_us):
        return (request is self.request and request.job.current(context_sha256, now_us)
            and request.job.issued_us <= self.issued_us <= self.received_us <= now_us < self.expires_us <= request.job.expires_us)


def provenance_wire_grant(request, message, *, now_us):
    """main의 peer/sequence admission 뒤 호출하는 순수 값 변환이다. bridge를 참조하지 않는다."""
    from .storage_messages import DbMessage, validate_body
    if type(request) is not ProvenanceRequest or type(message) is not DbMessage or message.op != 'RANGE_RESULT':
        raise ValueError('original request and admitted RANGE_RESULT required')
    body = message.body; validate_body(message.op, body); context = request.job.context
    if message.packet.fields['payload'] != dict(op=message.op, body=body):
        raise ValueError('typed range body differs from original packet bytes')
    if (body['database_uuid'], body['mapper_worker'], body['reservation_nonce']) != (
            context.database_uuid, context.mapper_worker, request.reservation_nonce):
        raise ValueError('range result belongs to a different owner/request')
    if body['result'] not in ('GRANTED', 'REPLAY'):
        raise ParkingNotReady('original range result is not confirmed')
    start, end, head = (int(body[k]) for k in ('start_inclusive', 'end_exclusive', 'highwater_after'))
    grant = WireProvenanceGrant(request, start, end, head, message.packet.raw_bytes,
        message.issued_us, message.expires_us, now_us)
    if not grant.current(request, context.sha256, now_us):
        raise ValueError('RANGE_RESULT renewed or exceeded the original request lifetime')
    return grant


@dataclass(frozen=True)
class ParkingPublication:
    """공용 protobuf 사본의 bytes. ROI 상한/모호함은 meta에 남고 권한은 없다."""

    slots: tuple
    meta: bytes
    total_tracks_in_roi: int
    input_kind: str
    source_clock: str
    context: StorageContext
    live_authority: bool = field(default=False, init=False)

    def apply(self, template):
        from xenova_drive_world import schema, encode, decode
        if type(template) is not schema.WorldSnapshot or template.required_features not in (11, 15):
            raise ValueError('original supported Lane/Localization world template required')
        for name, expected in (('map_epoch', self.context.map_epoch), ('frame_epoch', self.context.frame_epoch),
                ('odom_epoch', self.context.odom_epoch), ('calibration', self.context.calibration),
                ('configuration', self.context.configuration), ('primitive', self.context.primitive_sha256),
                ('clock_epoch', self.source_clock)):
            if getattr(template, name) != expected:
                raise ValueError('parking/world original source context mismatch')
        value = schema.WorldSnapshot(); value.CopyFrom(template)
        value.required_features = 15; value.minor = max(2, value.minor)
        del value.parking_slots[:]
        for raw in self.slots:
            value.parking_slots.add().ParseFromString(raw)
        value.parking_set.ParseFromString(self.meta)
        return decode(encode(value))


def _source_fields(source):
    return asdict(source)


def _source_from(value):
    if type(value) is not dict or set(value) != {f.name for f in fields(SlotSource)}:
        raise ValueError('original slot source fields missing')
    original = dict(value); stamp = original['stamp']
    if type(stamp) is not dict or set(stamp) != {f.name for f in fields(FrameStamp)}:
        raise ValueError('original FrameStamp fields missing')
    stamp = dict(stamp)
    if stamp['captured_us'] is not None:
        stamp['captured_us'] = tuple(stamp['captured_us'])
    original['stamp'] = FrameStamp(**stamp)
    return SlotSource(**original)


def _occupancy_fields(value):
    if value is None:
        return None
    raw = asdict(value); raw.pop('source'); raw.pop('feasible')
    return dict(source=value.source.sha256, fields=raw)


def _occupancy_from(value, sources):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {'source', 'fields'} or value['source'] not in sources:
        raise ValueError('original occupancy source missing')
    raw = value['fields']; expected = {f.name for f in fields(OccupancyEvidence) if f.init} - {'source'}
    if type(raw) is not dict or set(raw) != expected:
        raise ValueError('original occupancy fields missing')
    raw = dict(raw); raw['required_region'] = tuple(tuple(p) for p in raw['required_region'])
    return OccupancyEvidence(source=sources[value['source']], **raw)


def _context_group(method_sha, storage_sha, entries, catalog_revision):
    sources = {}
    for _, track in entries:
        sources[track.candidate.source.sha256] = _source_fields(track.candidate.source)
        if track.occupancy is not None:
            sources[track.occupancy.source.sha256] = _source_fields(track.occupancy.source)
    body = dict(profile='parking-originals-1', method_context_sha256=method_sha, storage_context_sha256=storage_sha,
        catalog_revision=str(catalog_revision),
        sources=[dict(sha256=key, fields=sources[key]) for key in sorted(sources)],
        entries=[dict(provenance_id=str(identity), geometry_source=track.candidate.source.sha256,
                      occupancy=_occupancy_fields(track.occupancy)) for identity, track in entries])
    return ContextRecord(canonical(dict(kind='METRIC', body=body)))


def _record(identity, context, evidence, *, parents=None, kind=3):
    parents = evidence.parent_ids if parents is None else parents
    if any(p >= identity for p in parents):
        raise ParkingNotReady('new provenance must follow every original parent ID')
    raw = b''.join(struct.pack('<16sQQ', UUID(instance).bytes, sequence, device_us) for instance, sequence, device_us in evidence.raw_keys)
    return ProvenanceRecord(identity, PROVENANCE.pack(bytes.fromhex(context.sha256), evidence.source_mask, kind,
        len(evidence.raw_keys), len(parents), UUID(evidence.pi_host_boot).bytes, *evidence.pi_interval,
        evidence.observation_sequence, *(parents + (0,) * (4 - len(parents))), raw.ljust(128, b'\0'), 0, bytes(16)))


def _f32(value, *, upper=False):
    v = struct.unpack('<f', struct.pack('<f', value))[0]
    if upper and v < value:
        v = struct.unpack('<f', struct.pack('<I', struct.unpack('<I', struct.pack('<f', v))[0] + 1))[0]
    return v


def _f32_down(value):
    v = _f32(value)
    if v > value:
        v = struct.unpack('<f', struct.pack('<I', struct.unpack('<I', struct.pack('<f', v))[0] - 1))[0]
    return v


def _map_source_mask(values, evidence):
    for value in values:
        if value.state != UNKNOWN:
            required = (2 if value.source_kinds & 1 else 0) | (8 if value.source_kinds & 2 else 0)
            if evidence.source_mask & required != required:
                raise ValueError('metric provenance omits an actual map sensor source')


def _in_roi(candidate, bounds):
    x0, y0, x1, y1 = bounds; e = candidate.position_bound_m
    return polygons_touch(candidate.corners, ((x0 - e, y0 - e), (x1 + e, y0 - e),
        (x1 + e, y1 + e), (x0 - e, y1 + e)))


class ParkingBridge:
    """M incarnation당 한 owner. range는 이 객체 전용 예약이며 다른 allocator와 공유하지 않는다."""

    def __init__(self, context, *, clock_id, map_frame_id, body, method_manifest_sha256, input_kind,
                 profile=ParkingProfile(), association_profile=AssociationProfile(), calibration_profile=None):
        if type(context) is not StorageContext or type(body) is not BodyModel or type(profile) is not ParkingProfile or type(association_profile) is not AssociationProfile:
            raise ValueError('exact context/body/profiles required')
        digest(method_manifest_sha256)
        if input_kind not in ('LIVE', 'SYNTHETIC', 'REPLAY'):
            raise ValueError('explicit original input kind required')
        if calibration_profile is None:
            if input_kind == 'LIVE':
                raise ValueError('live parking requires the pinned local/vehicle calibration relation')
            local_calibration_id = context.calibration
        else:
            if (type(calibration_profile) is not CalibrationProfile
                    or calibration_profile.vehicle_bundle_sha256 != context.calibration
                    or calibration_profile.body != body):
                raise ValueError('local profile/body differs from the original vehicle calibration bundle')
            local_calibration_id = calibration_profile.sha256
        self.context, self.clock_id, self.body = context, clock_id, body
        self.calibration_profile = calibration_profile
        self.local_calibration_id = local_calibration_id
        self.profile, self.association_profile = profile, association_profile
        self.catalog = SlotCatalog(context, clock_id=clock_id, frame_id=map_frame_id, calibration_id=local_calibration_id)
        self.method = ContextRecord(canonical(dict(kind='METRIC', body=dict(profile='parking-method-1',
            method_manifest_sha256=method_manifest_sha256, storage_context_sha256=context.sha256,
            calibration_profile_sha256=local_calibration_id, vehicle_bundle_sha256=context.calibration,
            coordinate='MAP_X_FORWARD_Y_LEFT_REAR_AXLE', map_frame_id=map_frame_id,
            body=asdict(body), geometry_profile=asdict(profile), association_profile=asdict(association_profile)))))
        self.last = None; self._last_batch = None; self._pending_range = None; self._range_done = None
        self._pending_provenance = None; self._provenance = None; self._next_provenance = None
        self._provenance_highwater = None
        self._last_provenance_nonce = None; self._pending_checkpoint = None; self.last_commit = None
        self._head_values = self._head_floor = None
        self._records = {}; self._contexts = {self.method.sha256: self.method}
        self._links = {}; self._kind = input_kind; self._volatile = {}
        self._applied_rgb = None; self._applied_geometry = None; self._applied_prior = None; self._applied_aisles = None
        self.context_transition_wait_reason = None

    def replace_publication_context(self, new_context, *, now_us):
        """동일 M의 scene-only full context를 바꾼다. 미해결 D 원문은 실제 정리 뒤에만 넘긴다.

        기존 ID/provenance range의 원 수락 근거·cursor, map source/관측 age와 last_commit
        원문은 보존한다. last_commit이 새 context/freeze의 current receipt가 되는 것은 아니다.
        """
        from ..storage.geometry_scope import same_catalog_geometry
        from ..storage.contracts import decimal
        uint(now_us)
        if type(new_context) is not StorageContext or not same_catalog_geometry(self.context, new_context, 'SLOT'):
            raise ValueError('publication transition changed M/raw/actual origin/map/calibration')
        if new_context == self.context:
            ready = self.catalog.publication_transition is None
            if ready:self.context_transition_wait_reason = None
            return ready
        before = json_bytes(self.context.source_binding, 2048)
        after = json_bytes(new_context.source_binding, 2048)
        if decimal(after['source_binding_revision'], positive=True) <= decimal(before['source_binding_revision'], positive=True):
            raise ValueError('old publication revision cannot become current again')
        self.catalog.begin_publication_context(new_context, now_us=now_us)
        if self._pending_checkpoint is not None or self._pending_range is not None or self._pending_provenance is not None:
            self.context_transition_wait_reason = 'PENDING_ORIGINAL_D_JOB'
            return False
        value = json_bytes(self.method.payload, 4096)
        value['body']['storage_context_sha256'] = new_context.sha256
        method = ContextRecord(canonical(value))
        contexts = dict(self._contexts); contexts[method.sha256] = method
        required = {record.context_sha256 for record in self._records.values()} | {method.sha256}
        required = _context_closure_keys(contexts, required)
        if len(required) > 64:
            self.context_transition_wait_reason = 'ORIGINAL_CONTEXT_CLOSURE_CAP'
            return False
        if not self.catalog.replace_publication_context(new_context, now_us=now_us):
            self.context_transition_wait_reason = 'PENDING_ORIGINAL_CATALOG_RANGE'
            return False
        self.context = new_context; self.method = method
        self._contexts = {key: contexts[key] for key in required}
        # 수락한 range/HWM은 catalog에 보존된다. 이전 full context에 결박한 조회를
        # 새 CP의 current head로 승격하지 않고 새 실제 조회를 기다린다.
        self._head_values = None
        self.context_transition_wait_reason = None
        return True

    def summary(self):
        """일반 mapper result용 작은 사본이다. checkpoint/배열/catalog 참조를 넣지 않는다."""
        value = self.last
        return dict(status='UNAVAILABLE' if value is None or value.association is None else value.association.status,
            geometry_candidates=0 if value is None or value.geometry is None else len(value.geometry.candidates),
            tracks=len(self.catalog.tracks), catalog_revision=self.catalog.revision,
            allocation_wait=False if value is None else value.allocation_wait,
            provenance_wait=False if value is None else value.provenance_wait,
            reasons=[] if value is None else list(value.reasons[:8]),
            storage_pending=self._pending_checkpoint is not None, input_kind=self._kind,
            metric_ready=False, live_authority=False)

    def _prune(self):
        self._contexts[self.method.sha256] = self.method
        roots = {t.provenance_id for t in self.catalog.tracks}
        for links in self._links.values():
            roots.update(links)
        kept = {}; pending = list(roots)
        while pending:
            identity = pending.pop()
            if identity in kept or identity not in self._records:
                continue
            if len(kept) >= 16384:
                raise ParkingNotReady('current catalog parent closure exceeds16384 records')
            kept[identity] = self._records[identity]; pending.extend(kept[identity].parents)
        self._records = kept
        needed = {p.context_sha256 for p in kept.values()} | {self.method.sha256}
        needed = _context_closure_keys(self._contexts, needed)
        if len(needed) > 64:
            raise ParkingNotReady('original context closure exceeds64 records')
        self._contexts = {k: v for k, v in self._contexts.items() if k in needed}

    def prepare_provenance(self, job, reservation_nonce, *, block_count=1 << 20):
        if self.catalog.publication_transition is not None:
            raise ParkingNotReady('original publication transition is awaiting terminal cleanup')
        if type(job) is not StorageJob or job.context != self.context:
            raise ValueError('original current storage job required')
        if self._provenance is not None and self._next_provenance < self._provenance.end_exclusive:
            return None
        request = ProvenanceRequest(job, reservation_nonce, block_count)
        if self._pending_provenance is not None and self._pending_provenance != request:
            raise ParkingNotReady('original provenance request is unresolved')
        self._pending_provenance = request
        return request

    def accept_provenance(self, request, result, *, now_us):
        uint(now_us)
        if (type(request) is not ProvenanceRequest or type(result) is not ProvenanceRange
                or request.job.context != self.context or result.reservation_nonce != request.reservation_nonce):
            raise ValueError('original exclusive provenance request/result mismatch')
        if self._last_provenance_nonce == result.reservation_nonce:
            return False
        if self.catalog.publication_transition is not None:
            if (request != self._pending_provenance or result.original_job != request.job
                    or result.end_exclusive - result.start_inclusive != request.block_count
                    or result.committed_j_us > now_us):
                raise ValueError('historical provenance result lost its original pending identity')
            self._provenance_highwater = max(self._provenance_highwater or 0, result.end_exclusive)
            self._pending_provenance = None
            return False
        if (request != self._pending_provenance or result.end_exclusive - result.start_inclusive != request.block_count
                or not result.current(request.job, self.context.sha256, now_us)):
            raise ValueError('historical/expired provenance result is not a new allocation')
        if self._provenance_highwater is not None and result.start_inclusive < self._provenance_highwater:
            raise ValueError('old provenance range cannot be reused after a newer range')
        self._provenance = result; self._next_provenance = result.start_inclusive
        self._provenance_highwater = result.end_exclusive
        self._last_provenance_nonce = result.reservation_nonce; self._pending_provenance = None
        return True

    def accept_provenance_wire(self, request, message, *, now_us):
        grant = provenance_wire_grant(request, message, now_us=now_us)
        return self.accept_wire_grant(request, grant, now_us=now_us)

    def accept_wire_grant(self, request, grant, *, now_us):
        uint(now_us)
        if (type(request) is ProvenanceRequest and type(grant) is WireProvenanceGrant
                and request.job.context == self.context and self.catalog.publication_transition is not None):
            if request is not self._pending_provenance or grant.request is not request or grant.received_us > now_us:
                raise ValueError('historical wire range lost its original pending identity')
            self._provenance_highwater = max(self._provenance_highwater or 0, grant.end_exclusive)
            self._pending_provenance = None
            return False
        if type(grant) is not WireProvenanceGrant or request.job.context != self.context or not grant.current(request, self.context.sha256, now_us):
            raise ValueError('original current wire provenance grant required')
        if self._last_provenance_nonce == request.reservation_nonce:
            return False
        if request is not self._pending_provenance:
            raise ValueError('wire range belongs to another original pending request')
        if self._provenance_highwater is not None and grant.start_inclusive < self._provenance_highwater:
            raise ValueError('historical RANGE_RESULT cannot rewind the original cursor')
        self._provenance = grant; self._next_provenance = grant.start_inclusive
        self._provenance_highwater = grant.end_exclusive
        self._last_provenance_nonce = request.reservation_nonce; self._pending_provenance = None
        return True

    def retire_provenance_wire(self, request, message, *, now_us):
        """현재 D의 역사 조회 결과로 원 대기만 정리한다. 옛 range는 발행하지 않고 건너뛴다."""
        from .storage_messages import DbMessage, validate_body
        uint(now_us)
        if request is not self._pending_provenance or request.job.context != self.context or type(message) is not DbMessage or message.op != 'RANGE_RESULT':
            raise ValueError('original unresolved provenance request required')
        body = message.body; validate_body(message.op, body)
        if (message.packet.fields['payload'] != dict(op=message.op, body=body)
                or (body['database_uuid'], body['mapper_worker'], body['reservation_nonce']) != (
                    self.context.database_uuid, self.context.mapper_worker, request.reservation_nonce)
                or not request.job.issued_us <= message.issued_us <= now_us < message.expires_us):
            raise ValueError('historical range lookup lost its original identity/transport')
        if body['result'] == 'UNKNOWN':
            return False
        if body['result'] in ('GRANTED', 'REPLAY'):
            start, end = int(body['start_inclusive']), int(body['end_exclusive'])
            if end - start != request.block_count:
                raise ValueError('historical range changed its original count')
            # 현재 head/grant가 아니라 재사용을 금지할 하한만 보존한다.
            self._provenance_highwater = max(self._provenance_highwater or 0, end)
        self._pending_provenance = None
        return True

    def _ids(self, count):
        if count == 0:
            return ()
        if self._provenance is None or self._next_provenance + count > self._provenance.end_exclusive:
            raise ParkingNotReady('exclusive committed provenance range is not ready')
        return tuple(range(self._next_provenance, self._next_provenance + count))

    def _frame_id(self, evidence):
        identity = self._ids(1)[0]
        if any(p >= identity for p in evidence.parent_ids):
            # 다른 M 하위 producer가 더 뒤의 블록에서 parent를 발급했으면 남은 작은
            # 번호는 건너뛴다. 같은 block을 되감거나 parent 순서를 비틀지 않는다.
            self._next_provenance = self._provenance.end_exclusive
            raise ParkingNotReady('a later committed range must follow the actual parent IDs')
        return identity

    def observe_heads(self, head, job, *, now_us):
        if type(head) is not CatalogHeadSnapshot or type(job) is not StorageJob or job.context != self.context or not head.current(job, self.context.sha256, now_us):
            raise ValueError('actual current D heads/request required')
        values = tuple(hwm for _, hwm, _ in head.heads)
        if self._head_floor is not None and any(a < b for a, b in zip(values, self._head_floor)):
            raise ValueError('current durable catalog heads regressed')
        self._head_values = self._head_floor = values

    def prepare_range(self, head, job, reservation_nonce, *, now_us, count=64):
        if self.catalog.publication_transition is not None:
            raise ParkingNotReady('original publication transition is awaiting terminal cleanup')
        self.observe_heads(head, job, now_us=now_us)
        if self._pending_range is not None:
            return self._pending_range[1]
        if self.last is None or not self.last.allocation_wait or self.last.geometry is None or self.last.association is None:
            return None
        need = self.catalog.allocation_need(self.last.geometry, self.last.association, head, job,
            reservation_nonce=reservation_nonce, now_us=now_us, count=count)
        if need is None:
            return None
        evidence = EvidenceTable(reservations=(need.reservation,),
            catalog_highwaters=(self._head_values[0], need.reservation.end_exclusive))
        request = CheckpointRequest(job, CheckpointPayload.build(job, evidence=evidence, intent='CATALOG_RESERVATION'))
        self._pending_range = need, request
        return request

    def accept_range(self, request, receipt, *, now_us):
        uint(now_us)
        if self._range_done == (request, receipt):
            return False
        if self._pending_range is None or request is not self._pending_range[1]:
            raise ValueError('range completion belongs to another original request')
        need, original = self._pending_range
        if type(receipt) is not CommitReceipt:
            raise ValueError('exact original range receipt required')
        if (receipt.database_uuid, receipt.job_id, receipt.job_digest, receipt.origin_context_sha256) != (
                self.context.database_uuid, original.job.job_id, original.digest, self.context.sha256):
            raise ValueError('range receipt original identity mismatch')
        if receipt.status in ('UNKNOWN', 'ABSENT'):
            return False
        if receipt.committed_j_us is not None and receipt.committed_j_us > now_us:
            raise ValueError('future commit is not a completed original operation')
        if self.catalog.publication_transition is not None:
            self.catalog.retire_allocation(need, original.job, original.payload, receipt)
            self._pending_range = None; self._range_done = request, receipt
            return False
        if not receipt.current(original.job, original.payload, self.context.sha256, now_us):
            self.catalog.retire_allocation(need, original.job, original.payload, receipt)
            self._pending_range = None; self._range_done = request, receipt
            return False
        accepted = self.catalog.apply_committed_range(need, original.job, original.payload, receipt,
            current_context_sha256=self.context.sha256, now_us=now_us)
        if accepted:
            self._head_values = self._head_values[0], need.reservation.end_exclusive
            self._head_floor = self._head_values
            self._pending_range = None; self._range_done = request, receipt
        return accepted

    def process(self, result, *, stamp, capture_prior, aisles, registration_revision, prior_qualified,
                now_us, metric_evidence=None, source_kinds=1):
        """실제 PerceptionResult를 소비한다. I/O 대기 대신 allocation/provenance wait를 반환한다."""
        from ..perception.pipeline import PerceptionResult
        if (type(result) is not PerceptionResult or type(stamp) is not FrameStamp or type(capture_prior) is not PoseBox
                or type(aisles) is not tuple or not 1 <= len(aisles) <= 4 or any(type(a) is not ParkingAisle for a in aisles)
                or type(prior_qualified) is not bool):
            raise ValueError('exact perception result/original capture prior/declared roles required')
        uint(now_us); uint(registration_revision)
        summary, snapshot = result.summary, result.map_snapshot
        if snapshot.get('frame_id') != self.catalog.frame_id and summary.get('quarantined') is True:
            self.last = ParkingUpdate(None, None, False, False, False, False, ('MAP_QUARANTINED',), snapshot['revision'])
            return self.last
        if (type(summary) is not dict or type(snapshot) is not dict or stamp.clock_id != self.clock_id
                or tuple(summary.get('rgb_source_key', ())) != stamp.key
                or summary.get('calibration_id') != self.local_calibration_id or snapshot.get('frame_id') != self.catalog.frame_id
                or summary.get('now_us', now_us + 1) > now_us or stamp.available_us > summary.get('now_us', -1)
                or summary.get('rgb_captured_us') != stamp.captured_us):
            raise ValueError('original source/map/clock differs from current storage context')
        if summary.get('kind') != self._kind or snapshot.get('kind') != self._kind:
            raise ValueError('physical/replay/synthetic map domains must not be mixed')
        if result.tape is None or result.tape.source_key != stamp.key:
            self.last = ParkingUpdate(None, None, False, False, False, False, ('TAPE_SOURCE_UNAVAILABLE',), snapshot['revision'])
            return self.last
        source = SlotSource(stamp, self.local_calibration_id, self.context.map_epoch, self.catalog.frame_id,
            self.context.odom_epoch, snapshot['revision'], registration_revision,
            summary.get('geometric_model_qualified') is True and prior_qualified
            and (self.calibration_profile is None or self.calibration_profile.metric_qualified), input_kind=self._kind)
        rows = []; checked = pairs = 0; complete = True; reasons = []
        for aisle in aisles:
            remaining = self.profile.hypothesis_cap - checked
            if remaining <= 0:
                complete = False; reasons.append('DECLARED_AISLE_WORK_CAP'); break
            value = extract_slots(result.tape, aisle, source=source, body=self.body,
                profile=replace(self.profile, hypothesis_cap=remaining))
            base = len(rows)
            rows.extend(replace(c, local_id=base + i) for i, c in enumerate(value.candidates))
            checked += value.hypotheses_checked; pairs += value.pair_count
            complete &= value.complete; reasons.extend(value.reasons)
        conflicting = set()
        for i, a in enumerate(rows):
            for j, b in enumerate(rows[:i]):
                if a.corners and b.corners and polygons_touch(a.corners, b.corners, interior=True):
                    conflicting.update((i, j))
        for i in conflicting:
            rows[i] = replace(rows[i], geometry_state=AMBIGUOUS, reasons=rows[i].reasons + ('DECLARED_ROLE_AMBIGUITY',))
        state = 'UNKNOWN' if not complete else 'AMBIGUOUS' if conflicting else 'PARTIAL' if any(c.geometry_state != VALID for c in rows) else 'VALID' if rows else 'EMPTY'
        geometry = SlotGeometryResult(tuple(rows), source, complete, state, tuple(reasons), checked, pairs)
        cached_geometry = self._applied_rgb == stamp.key
        if cached_geometry:
            if capture_prior != self._applied_prior or aisles != self._applied_aisles:
                raise ValueError('same capture cannot acquire a new pose/role interpretation')
            geometry = self._applied_geometry
        association = self.catalog.associate(geometry, capture_prior, prior_qualified=prior_qualified, profile=self.association_profile)
        allocation_wait = len(association.births) > self.catalog.available_ids
        new_geometry = False; new_occupancy = False; provenance_wait = False
        self._last_batch = geometry
        can_update = not cached_geometry and association.complete and association.status in ('NEW', 'UNIQUE') and not allocation_wait
        if can_update and metric_evidence is not None:
            if type(metric_evidence) is not MetricEvidence or stamp.key not in metric_evidence.frame_keys or metric_evidence.pi_host_boot != self.context.pi_host_boot:
                raise ValueError('metric evidence differs from original RGB/Pi source')
            try:
                identity = self._frame_id(metric_evidence)
                record = _record(identity, self.method, metric_evidence)
                pose = association.assignments[0].pose_envelope
                clearance = ClearanceMap.from_snapshot(snapshot, snapshot_us=summary['now_us'], map_epoch=self.context.map_epoch,
                    odom_epoch=self.context.odom_epoch, source_kinds=source_kinds)
                occupancy = tuple((c.local_id, evaluate_occupancy(to_map(c, pose.center,
                    position_error_m=math.hypot(*pose.widths[:2]) / 2, yaw_error_rad=pose.widths[2] / 2),
                    clearance, now_us=now_us, profile=self.profile, clock_id=self.clock_id)) for c in geometry.candidates)
                _map_source_mask((v for _, v in occupancy), metric_evidence)
                previous = {t.slot_id: t for t in self.catalog.tracks}
                new_geometry = self.catalog.update(geometry, association,
                    provenance_ids={c.local_id: identity for c in geometry.candidates}, occupancy=occupancy)
                if new_geometry:
                    self._next_provenance += 1; self._records[identity] = record
                    for track in self.catalog.tracks:
                        if track.candidate.source.key == source.key:
                            self._volatile.pop(track.slot_id, None)
                            old = previous.get(track.slot_id); old_links = self._links.get(track.slot_id, ())
                            occupancy_parent = old_links[1] if old is not None and track.occupancy is old.occupancy and len(old_links) > 1 else identity
                            self._links[track.slot_id] = identity, occupancy_parent
                    new_occupancy = any(t.occupancy is not previous[t.slot_id].occupancy for t in self.catalog.tracks if t.slot_id in previous)
                    self._applied_rgb, self._applied_geometry = stamp.key, geometry
                    self._applied_prior, self._applied_aisles = capture_prior, aisles
            except ParkingNotReady:
                provenance_wait = True
        elif can_update:
            provenance_wait = True
        if self.catalog.tracks and not new_geometry:
            changed, waiting = self.refresh_occupancy(snapshot, snapshot_us=summary['now_us'], now_us=now_us,
                metric_evidence=metric_evidence, source_kinds=source_kinds)
            new_occupancy |= changed; provenance_wait |= waiting
        if allocation_wait:
            reasons.append('WAIT_COMMITTED_SLOT_RANGE')
        if provenance_wait:
            reasons.append('WAIT_ORIGINAL_METRIC_PROVENANCE')
        self.last = ParkingUpdate(geometry, association, new_geometry, new_occupancy, allocation_wait, provenance_wait,
            tuple(reasons), snapshot['revision'])
        self._prune()
        return self.last

    def refresh_occupancy(self, snapshot, *, snapshot_us, now_us, metric_evidence=None, source_kinds=1):
        """depth/map만 바뀐 경우다. 보수 hazard는 allocation/fsync를 기다리지 않는다."""
        if type(snapshot) is not dict or snapshot.get('kind') != self._kind:
            raise ValueError('original map input kind required')
        clearance = ClearanceMap.from_snapshot(snapshot, snapshot_us=snapshot_us, map_epoch=self.context.map_epoch,
            odom_epoch=self.context.odom_epoch, source_kinds=source_kinds)
        spec = clearance.spec; lo = spec.bounds(0, 0); hi = spec.bounds(spec.width - 1, spec.height - 1)
        tracks = tuple(t for t in self.catalog.tracks if _in_roi(t.candidate, (lo[0], lo[1], hi[2], hi[3])))
        if len(tracks) > 8:
            return False, True
        values = {t.slot_id: evaluate_occupancy(t.candidate, clearance, now_us=now_us,
            profile=self.profile, clock_id=self.clock_id) for t in tracks}
        for identifier, value in values.items():
            if value.state != CLEAR_REGION:
                self._volatile[identifier] = value
        if not values:
            return False, False
        if metric_evidence is None:
            return False, True
        if type(metric_evidence) is not MetricEvidence or metric_evidence.pi_host_boot != self.context.pi_host_boot:
            raise ValueError('original map metric evidence required')
        _map_source_mask(values.values(), metric_evidence)
        try:
            identity = self._frame_id(metric_evidence); record = _record(identity, self.method, metric_evidence, kind=4)
        except ParkingNotReady:
            return False, True
        if any(not self._links.get(identifier) for identifier in values):
            return False, True
        changed = self.catalog.update_occupancy(values, provenance_ids={identifier: identity for identifier in values})
        if changed:
            self._next_provenance += 1; self._records[identity] = record
            for track in self.catalog.tracks:
                if track.provenance_id == identity:
                    geometry_parent = self._links.get(track.slot_id, ())[0]
                    self._links[track.slot_id] = geometry_parent, identity
                    self._volatile.pop(track.slot_id, None)
            self._prune()
        return changed, False

    def build_checkpoint(self, job, *, now_us, tiles=(), tile_references=None, base_evidence=EvidenceTable(),
                         durable_cutoff_pi_us, scene=None):
        """원 catalog tree를 checkpoint 시점에만 묶는다. 원 source/관측 age는 바꾸지 않는다."""
        if self._pending_checkpoint is not None:
            return self._pending_checkpoint
        if self.catalog.publication_transition is not None:
            raise ParkingNotReady('original publication transition is awaiting terminal cleanup')
        if type(job) is not StorageJob or job.context != self.context or not job.current(self.context.sha256, now_us):
            raise ValueError('current original checkpoint job required')
        if type(base_evidence) is not EvidenceTable or self._head_values is None:
            raise ParkingNotReady('actual current heads and source evidence copy required')
        tracks = self.catalog.tracks; planned_revision = self.catalog.revision + 1
        identities = self._ids(len(tracks)); groups = []; current = []
        for identity, track in zip(identities, tracks):
            trial = current + [(identity, track)]
            try:
                _context_group(self.method.sha256, self.context.sha256, trial, planned_revision)
            except ValueError:
                if not current:
                    raise ParkingNotReady('one original catalog tree exceeds the4KiB context cap')
                groups.append(current); current = [(identity, track)]
                _context_group(self.method.sha256, self.context.sha256, current, planned_revision)
            else:
                current = trial
        if current:
            groups.append(current)
        contexts = dict(self._contexts); contexts.update({c.sha256: c for c in base_evidence.contexts})
        records = {p.provenance_id: p for p in base_evidence.provenance}; records.update(self._records)
        aggregate = {}; sources_by_new_id = {}
        for group in groups:
            context = _context_group(self.method.sha256, self.context.sha256, group, planned_revision); contexts[context.sha256] = context
            for identity, track in group:
                parents = tuple(dict.fromkeys(self._links.get(track.slot_id, (track.provenance_id,))))
                if not parents or len(parents) > 4 or any(p not in records for p in parents):
                    raise ParkingNotReady('actual original metric parent records are missing')
                original = [records[p] for p in parents]
                if any(PROVENANCE.unpack(p.payload)[5] != UUID(self.context.pi_host_boot).bytes for p in original):
                    raise ParkingNotReady('cannot merge different Pi clock epochs into one checkpoint proof')
                # source mask는 실제 부모의 union이며 새 sensor bit를 발급하지 않는다.
                mask = 0
                for p in original:
                    mask |= p.source_mask
                evidence = MetricEvidence((track.candidate.source.stamp.key,), self.context.pi_host_boot,
                    (min(p.pi_lower for p in original), max(p.pi_upper for p in original)),
                    max(p.observation_sequence for p in original), mask, parent_ids=parents)
                aggregate[identity] = _record(identity, context, evidence, parents=parents, kind=4)
                sources_by_new_id[track.slot_id] = identity
        records.update(aggregate)
        roots = set(aggregate) | {p.provenance_id for p in base_evidence.provenance}
        roots.update(c.provenance_id for c in base_evidence.catalog if c.kind != 'SLOT')
        for tile in tiles:
            roots.update(tile.provenance_ids)
        kept = {}; pending = list(roots)
        while pending:
            identity = pending.pop()
            if identity in kept or identity not in records:
                continue
            if len(kept) >= 16384:
                raise ParkingNotReady('checkpoint parent closure exceeds16384 records')
            kept[identity] = records[identity]; pending.extend(records[identity].parents)
        records = kept
        needed_contexts = {p.context_sha256 for p in records.values()}
        if scene is not None:
            from ..storage.mapper_state import decode_mapper_state
            _, state_pages = decode_mapper_state(scene, tuple(contexts.values()))
            needed_contexts.update(page.sha256 for page in state_pages)
        needed_contexts = _context_closure_keys(contexts, needed_contexts)
        if len(needed_contexts) > 64:
            raise ParkingNotReady('checkpoint context closure exceeds64 records')
        contexts = {k: v for k, v in contexts.items() if k in needed_contexts}
        if any(PROVENANCE.unpack(p.payload)[5] != UUID(self.context.pi_host_boot).bytes for p in records.values()):
            raise ParkingNotReady('checkpoint cutoff cannot merge different original Pi clock epochs')
        if records and durable_cutoff_pi_us < max(p.pi_upper for p in records.values()):
            raise ValueError('original durable cutoff precedes included evidence')
        proposed = tuple(replace(t, geometry_revision=t.geometry_revision + 1,
            provenance_id=sources_by_new_id[t.slot_id]) for t in tracks)
        from ..storage.records import CatalogRecord
        catalog = tuple(CatalogRecord('SLOT', t.slot_id, t.geometry_revision, t.occupancy_revision,
            t.provenance_id, t.payload_bytes()) for t in proposed) + tuple(c for c in base_evidence.catalog if c.kind != 'SLOT')
        evidence = EvidenceTable(tuple(records[k] for k in sorted(records)), tuple(contexts[k] for k in sorted(contexts)),
            catalog, catalog_highwaters=self._head_values)
        request = CheckpointRequest(job, CheckpointPayload.build(job, tiles, evidence,
            tile_references=tile_references, scene=scene, durable_cutoff_pi_us=durable_cutoff_pi_us))
        # 준비한 immutable key는 local에서도 건너뛰지 않고 보존한다. sensor progress는 없다.
        if tracks:
            self.catalog.repack_provenance(sources_by_new_id); self._next_provenance += len(identities)
        self._records.update(aggregate); self._contexts = contexts
        self._pending_checkpoint = request
        self._prune()
        return request

    def accept_checkpoint(self, request, receipt, *, now_us):
        uint(now_us)
        if request is not self._pending_checkpoint or type(receipt) is not CommitReceipt:
            raise ValueError('completion belongs to a different original checkpoint')
        if (receipt.database_uuid, receipt.job_id, receipt.job_digest, receipt.origin_context_sha256) != (
                self.context.database_uuid, request.job.job_id, request.digest, self.context.sha256):
            raise ValueError('checkpoint completion original identity mismatch')
        if self.catalog.publication_transition is not None:
            if receipt.committed_j_us is not None and receipt.committed_j_us > now_us:
                raise ValueError('future commit is not a completed original operation')
            if receipt.status in ('COMMITTED', 'DUPLICATE', 'FAILED'):
                self._pending_checkpoint = None
            return False
        if not receipt.current(request.job, request.payload, self.context.sha256, now_us):
            if receipt.status in ('COMMITTED', 'DUPLICATE', 'FAILED'):
                self._pending_checkpoint = None
            return False
        self.last_commit = receipt; self._pending_checkpoint = None
        return True

    def retire_storage_abort(self, request, abort, *, now_us):
        """main의 미시작/실제 정리 통지로 원 대기만 닫는다. UNKNOWN 대신 쓰는 API는 아니다.

        caller는 실제 dispatch/copy/owner 수명 정리를 먼저 끝낸다. 이 함수는 그 OS
        사실을 생산하거나 COMMITTED/범위/현재 성공을 발급하지 않는다.
        """
        from .mapper_storage import StorageAbort
        uint(now_us)
        if (type(abort) is not StorageAbort or type(request) not in (CheckpointRequest, ProvenanceRequest)
                or request.job.context != self.context):
            raise ValueError('original main cleanup notification required')
        if abort.reason == 'EXPIRED' and now_us < request.job.expires_us:
            raise ValueError('original operation has not expired')
        if request is self._pending_provenance:
            self._pending_provenance = None
        elif self._pending_range is not None and request is self._pending_range[1]:
            self.catalog.cancel_allocation(self._pending_range[0]); self._pending_range = None
        elif request is self._pending_checkpoint:
            self._pending_checkpoint = None
        else:
            raise ValueError('cleanup belongs to another original pending request')
        return True

    def restore(self, loaded, *, now_us):
        if type(loaded) is not LoadedCheckpoint or not loaded.current(loaded.request, self.context.sha256, now_us):
            raise ValueError('actual current LOAD request/result required')
        self._restore_catalog(loaded.payload.evidence,loaded.original_job,now_us=now_us)
        self.observe_heads(loaded.heads, loaded.request.job, now_us=now_us)

    def restore_complete(self,complete,*,current_scope,now_us):
        """전체 BOOTSTRAP 자료의 issued ID만 복원한다. provisional head를 재포장하지 않는다."""
        from .scene import SceneEvidenceScope
        if (type(current_scope) is not SceneEvidenceScope or current_scope.context!=self.context
                or current_scope.calibration.sha256!=self.local_calibration_id
                or current_scope.method_manifest_sha256!=json_bytes(self.method.payload,4096)['body']['method_manifest_sha256']):
            raise ParkingNotReady('actual current source/method/local calibration scope required')
        scope=current_scope.with_complete_history(complete,now_us=now_us)
        _,head=scope.complete_restore_head(complete,now_us=now_us)
        self._restore_catalog(complete.evidence,complete.original_job,now_us=now_us)
        # 설명용 내구 하한이다. 새 publication job의 current head나 range 발행권이 아니다.
        self._head_values=self._head_floor=tuple(hwm for _,hwm,_ in head.heads)

    def _restore_catalog(self,evidence,original_job,*,now_us):
        if self.catalog.tracks or self._pending_range is not None or self._pending_provenance is not None or self._pending_checkpoint is not None:
            raise ParkingNotReady('restore requires a fresh catalog owner')
        originals = {}; contexts = {c.sha256: c for c in evidence.contexts}
        records = {p.provenance_id: p for p in evidence.provenance}
        selected = tuple(c for c in evidence.catalog if c.kind == 'SLOT')
        needed = {c.provenance_id for c in selected}; revisions = set()
        for c in evidence.contexts:
            value = json_bytes(c.payload, 4096); body = value['body']
            if value['kind'] != 'METRIC' or body.get('profile') != 'parking-originals-1':
                continue
            if set(body) != {'profile', 'method_context_sha256', 'storage_context_sha256', 'catalog_revision', 'sources', 'entries'}:
                raise ValueError('original parking context grammar mismatch')
            method = contexts.get(body['method_context_sha256'])
            if method is None:
                raise ValueError('original method context closure missing')
            method_fields = json_bytes(method.payload, 4096)
            current_method = json_bytes(self.method.payload, 4096)
            for key in ('method_manifest_sha256', 'body', 'geometry_profile', 'association_profile', 'coordinate',
                        'calibration_profile_sha256', 'vehicle_bundle_sha256'):
                if method_fields['kind'] != 'METRIC' or method_fields['body'].get(key) != current_method['body'][key]:
                    raise ParkingNotReady('original parking method/profile is not supported by this owner')
            sources = {}
            for entry in body['sources']:
                if type(entry) is not dict or set(entry) != {'sha256', 'fields'}:
                    raise ValueError('original source entry grammar mismatch')
                source = _source_from(entry['fields'])
                if source.input_kind != self._kind:
                    raise ValueError('restored input kind differs from current physical/simulation domain')
                if source.sha256 != entry['sha256'] or source.sha256 in sources:
                    raise ValueError('original source SHA/identity mismatch')
                sources[source.sha256] = source
            for entry in body['entries']:
                if type(entry) is not dict or set(entry) != {'provenance_id', 'geometry_source', 'occupancy'}:
                    raise ValueError('original catalog entry grammar mismatch')
                identity = int(entry['provenance_id'])
                if str(identity) != entry['provenance_id']:
                    raise ValueError('noncanonical original provenance ID')
                uint(identity, positive=True)
                if identity not in needed:
                    continue
                if identity not in records or records[identity].context_sha256 != c.sha256 or identity in originals:
                    raise ValueError('catalog provenance/context linkage mismatch')
                originals[identity] = sources[entry['geometry_source']], _occupancy_from(entry['occupancy'], sources)
                if body['storage_context_sha256'] != original_job.context.sha256:
                    raise ValueError('original catalog context differs from committed job')
                revision = int(body['catalog_revision'])
                if str(revision) != body['catalog_revision']:
                    raise ValueError('noncanonical original catalog revision')
                uint(revision, positive=True); revisions.add(revision)
        if not needed <= set(originals):
            raise ValueError('LOAD has no original source/occupancy tree for catalog')
        if selected:
            if len(revisions) != 1:
                raise ValueError('selected catalog has mixed original revisions')
            self.catalog.restore(selected, {p: originals[p] for p in needed}, catalog_revision=revisions.pop(), restored_at_us=now_us)
        self._records = records; self._contexts.update(contexts)
        self._links = {c.stable_id: records[c.provenance_id].parents for c in selected}

    def publication_candidates(self, *, now_us, roi_bounds):
        from xenova_drive_world import schema
        uint(now_us)
        if self.catalog.revision == 0:
            raise ParkingNotReady('no original catalog evidence revision is available')
        if type(roi_bounds) is not tuple or len(roi_bounds) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in roi_bounds):
            raise ValueError('explicit bounded map ROI required')
        x0, y0, x1, y1 = roi_bounds
        if x0 >= x1 or y0 >= y1:
            raise ValueError('reversed ROI')
        tracks = tuple(t for t in self.catalog.tracks if _in_roi(t.candidate, roi_bounds))
        unresolved = (self.last is None or self.last.association is None or not self.last.association.complete
            or self.last.association.status not in ('NEW', 'UNIQUE') or self.last.allocation_wait or self.last.provenance_wait)
        limited = len(tracks) > 8
        complete = not limited and not unresolved
        slots = []
        if not limited:
            for track in tracks:
                c = track.candidate; partial = unresolved or not c.approach_lane_id
                proof = self._records.get(track.provenance_id)
                if proof is None:
                    raise ParkingNotReady('original slot evidence sequence is unavailable')
                state = AMBIGUOUS if unresolved else PARTIAL if partial else c.geometry_state
                value = schema.ParkingSlot(slot_id=track.slot_id, geometry_revision=track.geometry_revision,
                    geometry_state=state, occupancy_state=UNKNOWN, entry_edge=0,
                    position_bound_m=_f32(c.position_bound_m, upper=True), heading_bound_rad=_f32(c.heading_bound_rad, upper=True),
                    geometry_age_ms=track.geometry_age_ms(now_us, self.clock_id), clearance_age_ms=(1 << 32) - 1,
                    evidence_sequence=proof.observation_sequence, geometry_complete=state == VALID,
                    occupancy_reason=AMBIGUOUS_REASON if unresolved else GEOMETRY, source_kinds=0,
                    occupancy_revision=track.occupancy_revision,
                    usable_width_m=_f32_down(math.dist(c.usable_polygon[0], c.usable_polygon[1])),
                    usable_depth_m=_f32_down(math.dist(c.usable_polygon[0], c.usable_polygon[3])),
                    approach_lane_id=c.approach_lane_id, goal_mask=0)
                rounding = 0.
                for p in c.usable_polygon:
                    point = value.corners.add(x=p[0], y=p[1]); rounding = max(rounding, math.dist(p, (point.x, point.y)))
                value.position_bound_m = _f32(c.position_bound_m + rounding, upper=True)
                volatile = self._volatile.get(track.slot_id)
                occupancy = (replace(track, occupancy=volatile) if volatile is not None else track).occupancy_at(now_us, self.clock_id, profile=self.profile)
                if occupancy is not None:
                    value.source_kinds = occupancy.source_kinds
                    value.clearance_age_ms = occupancy.clearance_age_ms
                if not partial and occupancy is not None:
                    value.occupancy_state = occupancy.state; value.occupancy_reason = occupancy.reason
                if state == VALID:
                    for seed in goal_candidates(c, body=self.body, profile=self.profile):
                        goal = value.reverse_goal if seed.direction == 'REVERSE' else value.forward_goal
                        goal.x, goal.y, goal.yaw = seed.pose
                        value.goal_mask |= 1 if seed.direction == 'REVERSE' else 2
                        value.position_bound_m = _f32(max(value.position_bound_m, seed.position_bound_m + math.dist(seed.pose[:2], (goal.x, goal.y))), upper=True)
                        value.heading_bound_rad = _f32(max(value.heading_bound_rad, seed.heading_bound_rad + abs(goal.yaw - seed.pose[2])), upper=True)
                raw = value.SerializeToString(deterministic=True)
                if len(raw) > 256:
                    raise ParkingNotReady('parking candidate exceeds256-byte profile')
                slots.append(raw)
        meta = schema.ParkingSetMeta(revision=self.catalog.revision, complete=complete,
            track_count_in_roi=len(tracks) if len(tracks) <= 64 else 0, capacity_limited=limited,
            evidence_sequence=max((self._records[t.provenance_id].observation_sequence for t in tracks), default=0),
            state=0 if len(tracks) > 64 else 2 if not complete else 1)
        return ParkingPublication(tuple(slots), meta.SerializeToString(deterministic=True), len(tracks), self._kind, self.clock_id, self.context)
