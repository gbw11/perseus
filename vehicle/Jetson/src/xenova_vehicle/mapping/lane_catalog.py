"""공통 station의 두 차로를 원 공동 pose와 D 예약 ID에 연결한다.

교차 정책은 caller가 명시하며 점선/빈 공간에서 추론하지 않는다. LANE1은 원
geometry/source digest를 담은 local record다. 그 digest만으로 전체 복원을 하지
않고 실제 LOAD의 참조된 METRIC 상태 page와 provenance를 함께 검산한다.
LANE1 body는 little-endian magic5/side:u8/adjacent:u32/model_revision:u64와
geometry(교차 정책 포함)/source/original StorageContext SHA256 각32B의114B다.
"""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import math
import struct

from .registration import (PoseBox, PointFeature, SegmentFeature, FrozenReference,
    RegistrationProfile, register, transform_point)
from ..perception.inputs import FrameStamp
from ..perception.road import LaneHypothesis, RoadResult
from ..storage.contracts import (StorageContext, StorageJob, CatalogHeadSnapshot,
    CatalogReservation, CommitReceipt, canonical, digest, json_bytes, uint)
from ..storage.geometry_scope import same_catalog_geometry
from ..storage.records import CatalogRecord, CheckpointPayload, LoadedCheckpoint


_RECORD = struct.Struct('<5sBIQ32s32s32s')
_SHAPES = ('divider', 'left_center', 'right_center', 'left_outer', 'right_outer')


def _tree(value):
    return tuple(_tree(v) for v in value) if type(value) is list else value


def _road_fields(road):
    h = replace(road.hypotheses[0], semantic_support=None, semantic_source=None)
    return dict(status=road.status, complete=road.complete, reasons=road.reasons, source_key=road.source_key,
        calibration_qualified=road.calibration_qualified, hypothesis=asdict(h))


def _road_from(fields):
    if type(fields) is not dict or set(fields) != {'status', 'complete', 'reasons', 'source_key',
            'calibration_qualified', 'hypothesis'} or type(fields['hypothesis']) is not dict:
        raise ValueError('exact original lane geometry required')
    h = LaneHypothesis(**{key: _tree(value) for key, value in fields['hypothesis'].items()})
    return RoadResult(fields['status'], (h,), fields['complete'], _tree(fields['reasons']), _tree(fields['source_key']),
        fields['calibration_qualified'])


def _geometry(road, source, crossing):
    if type(road) is not RoadResult or road.source_key != source.stamp.key:
        raise ValueError('road and original source key differ')
    if type(road.complete) is not bool or type(road.calibration_qualified) is not bool:
        raise ValueError('explicit original road qualification required')
    if type(road.hypotheses) is not tuple:
        raise ValueError('original bounded road hypotheses required')
    if not road.complete or road.status not in ('VALID', 'PARTIAL') or len(road.hypotheses) != 1:
        return False
    h = road.hypotheses[0]
    if type(h) is not LaneHypothesis or type(h.station_s) is not tuple:
        raise ValueError('original common station hypothesis required')
    n = len(h.station_s)
    if (type(road.reasons) is not tuple or len(road.reasons) > 16
            or any(type(r) is not str or not r.isascii() or len(r) > 64 for r in road.reasons)
            or type(h.strip_ids) is not tuple or len(h.strip_ids) != 3
            or any(type(ids) is not tuple for ids in h.strip_ids) or sum(map(len, h.strip_ids)) > 32
            or any(type(i) is not int or not 0 <= i < 32 for ids in h.strip_ids for i in ids)
            or type(h.width_intervals_m) is not tuple or len(h.width_intervals_m) != 2):
        raise ValueError('bounded original road roles/intervals required')
    for interval in h.width_intervals_m:
        if (type(interval) is not tuple or len(interval) != 2 or any(type(v) not in (int, float)
                or not math.isfinite(v) or not 0 <= v <= 10 for v in interval) or interval[0] > interval[1]):
            raise ValueError('bounded original width intervals required')
    if (not 2 <= n <= 32 or any(len(getattr(h, name)) != n for name in _SHAPES)
            or len(h.inferred) != n or any(type(v) is not bool for v in h.inferred)
            or type(crossing) is not bytes or len(crossing) != n - 1 or any(v > 1 for v in crossing)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in h.station_s)
            or any(a >= b for a, b in zip(h.station_s, h.station_s[1:]))):
        raise ValueError('bounded common station geometry/crossing required')
    for name in _SHAPES:
        for point in getattr(h, name):
            if (type(point) is not tuple or len(point) != 2 or any(type(v) not in (int, float)
                    or not math.isfinite(v) or abs(v) > 10000 for v in point)):
                raise ValueError('bounded original XY points required')
    for values in zip(h.divider, h.left_outer, h.right_outer, h.left_center, h.right_center):
        divider, left, right, lc, rc = values
        if any(not math.isclose(c[k], (divider[k] + o[k]) / 2, abs_tol=1e-12, rel_tol=1e-12)
                for c, o in ((lc, left), (rc, right)) for k in (0, 1)):
            raise ValueError('centers must remain actual common-station midpoints')
    for i, (a, b) in enumerate(zip(h.divider, h.divider[1:])):
        dx, dy = b[0]-a[0], b[1]-a[1]
        if math.hypot(dx, dy) <= 1e-9 or any((points[i+1][0]-points[i][0])*dx
                + (points[i+1][1]-points[i][1])*dy <= 0 for points in (h.left_center, h.right_center)):
            raise ValueError('both lane centerlines must share forward station direction')
        if (dx*(h.left_outer[i][1]-a[1])-dy*(h.left_outer[i][0]-a[0]) <= 0
                or dx*(h.right_outer[i][1]-a[1])-dy*(h.right_outer[i][0]-a[0]) >= 0):
            raise ValueError('outer/divider role ordering changed')
    for value in (h.error_m, h.yaw_error_rad):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError('finite original geometry bounds required')
    return True


@dataclass(frozen=True)
class LaneSource:
    stamp: FrameStamp
    calibration_id: str
    map_epoch: str
    frame_id: str
    odom_epoch: str
    map_revision: int
    registration_revision: int
    qualified: bool
    input_kind: str = 'LIVE'

    def __post_init__(self):
        if type(self.stamp) is not FrameStamp or type(self.qualified) is not bool:
            raise ValueError('original capture and explicit qualification required')
        digest(self.calibration_id)
        for value in (self.map_epoch, self.frame_id, self.odom_epoch):
            if type(value) is not str or not 1 <= len(value) <= 128 or not value.isascii():
                raise ValueError('original bounded map/odom domain required')
        uint(self.map_revision); uint(self.registration_revision)
        if self.input_kind not in ('LIVE', 'REPLAY', 'SYNTHETIC'):
            raise ValueError('explicit input kind required')

    @property
    def domain(self):
        return self.calibration_id, self.map_epoch, self.frame_id, self.odom_epoch

    @property
    def sha256(self):
        return sha256(canonical(asdict(self))).hexdigest()


@dataclass(frozen=True)
class LaneModelCandidates:
    """stable ID 후보와 원 MAP geometry. 권한/최종 주행 선택은 포함하지 않는다."""
    road: RoadResult
    source: LaneSource
    lane_ids: tuple
    lane_revisions: tuple
    model_revision: int
    crossing: bytes
    provenance_ids: tuple
    original_context_sha256: str

    def __post_init__(self):
        if type(self.source) is not LaneSource or not _geometry(self.road, self.source, self.crossing):
            raise ValueError('complete original two-lane model required')
        for name, maximum in (('lane_ids', (1 << 32) - 1), ('lane_revisions', (1 << 63) - 1),
                ('provenance_ids', (1 << 63) - 1)):
            values = getattr(self, name)
            if type(values) is not tuple or len(values) != 2 or name == 'lane_ids' and len(set(values)) != 2:
                raise ValueError('two explicit lane IDs/revisions/provenance IDs required')
            for value in values: uint(value, maximum, positive=True)
        uint(self.model_revision, positive=True); digest(self.original_context_sha256)

    def fields(self):
        return dict(road=_road_fields(self.road), source=asdict(self.source), lane_ids=self.lane_ids,
            lane_revisions=self.lane_revisions, model_revision=self.model_revision,
            crossing=self.crossing.hex(), provenance_ids=self.provenance_ids,
            original_context_sha256=self.original_context_sha256)

    @classmethod
    def from_fields(cls, fields):
        if type(fields) is not dict or set(fields) != set(cls.__dataclass_fields__):
            raise ValueError('exact lane state fields required')
        values = dict(fields); source = dict(values['source']); stamp = dict(source['stamp'])
        stamp['captured_us'] = _tree(stamp['captured_us']); source['stamp'] = FrameStamp(**stamp)
        values.update(source=LaneSource(**source), road=_road_from(values['road']),
            crossing=bytes.fromhex(values['crossing']))
        for name in ('lane_ids', 'lane_revisions', 'provenance_ids'): values[name] = _tree(values[name])
        return cls(**values)

    def records(self):
        geometry = sha256(canonical((_road_fields(self.road), self.crossing.hex()))).digest()
        return tuple(CatalogRecord('LANE', identity, self.lane_revisions[i], 0, self.provenance_ids[i],
            _RECORD.pack(b'LANE1', i, self.lane_ids[1-i], self.model_revision, geometry,
                bytes.fromhex(self.source.sha256), bytes.fromhex(self.original_context_sha256)))
            for i, identity in enumerate(self.lane_ids))

    def age_us(self, now_us, clock_id):
        return self.source.stamp.age_us(now_us, clock_id)


@dataclass(frozen=True)
class LaneAssociation:
    status: str
    road: RoadResult
    source: LaneSource
    crossing: bytes
    catalog_revision: int
    source_digest: str
    pose: object = None
    position_error_m: float = 0.
    yaw_error_rad: float = 0.
    matches: tuple = ()
    checked_lanes: int = 0
    reasons: tuple = ()


@dataclass(frozen=True)
class LaneAllocationNeed:
    reservation: CatalogReservation
    job_id: str
    source_digest: str
    catalog_revision: int


def _pose_error(prior, points):
    return math.hypot(*prior.widths[:2]) / 2 + 2 * max(math.hypot(*p) for p in points) * math.sin(prior.widths[2] / 4)


def _common_points(road, model, prior, capture):
    """옛 FOV 끝 밖의 미관측 연장을 배제 근거로 쓰지 않는 공통 범위다."""
    current, old = road.hypotheses[0], model.road.hypotheses[0]
    points, segments = [], []
    for side in ('left_center', 'right_center'):
        target = getattr(old, side); dx, dy = target[-1][0] - target[0][0], target[-1][1] - target[0][1]
        length = math.hypot(dx, dy)
        if length <= 1e-9: return (), ()
        axis = dx / length, dy / length
        projected = [p[0] * axis[0] + p[1] * axis[1] for p in target]
        low, high = min(projected) + old.error_m, max(projected) - old.error_m
        selected = []; path = getattr(current, side)
        lengths = [0.]
        for a, b in zip(path, path[1:]): lengths.append(lengths[-1]+math.dist(a, b))
        samples = []
        for i in range(32):
            distance = lengths[-1]*(i+1)/33
            k = next(k for k in range(len(path)-1) if distance <= lengths[k+1])
            weight = (distance-lengths[k])/(lengths[k+1]-lengths[k])
            samples.append(tuple(a+weight*(b-a) for a, b in zip(path[k], path[k+1])))
        for p in samples:
            q = transform_point(p, prior.center); along = q[0] * axis[0] + q[1] * axis[1]
            pad = _pose_error(prior, (p,)) + current.error_m
            if low <= along - pad and along + pad <= high:
                selected.append(PointFeature(p, current.error_m, side, capture, (side,)))
        if len(selected) < 2: return (), ()
        points.extend(selected)
        segments.extend(SegmentFeature(a, b, old.error_m, side, model.source.sha256)
            for a, b in zip(target, target[1:]))
    return tuple(points), tuple(segments)


class LaneCatalog:
    """M owner 하나, lane64개. 검색/할당 대기는 후보만 보류하고 I/O를 하지 않는다.

    RegistrationProfile 기본 수치는 미실측 초기 값이다. 전체 prior가 타당하다는
    caller의 근거가 별도로 필요하며 residual 하나로 그 타당성을 증명하지 않는다.
    """
    def __init__(self, context, *, clock_id, calibration_id, frame_id=None):
        if type(context) is not StorageContext: raise ValueError('original storage context required')
        digest(calibration_id)
        self.context, self.clock_id, self.calibration_id = context, clock_id, calibration_id
        self.frame_id = context.frame_epoch if frame_id is None else frame_id
        for value in (self.clock_id, self.frame_id):
            if type(value) is not str or not 1 <= len(value) <= 128 or not value.isascii():
                raise ValueError('original bounded clock/frame identity required')
        self.revision = 0; self._models = {}; self._heads = {}; self._need = self._need_job = None
        self._range = self._next = self._hwm = self.accepted_range = self._transition = None
        self.latest = None; self._restored_at = None; self._observed = None

    @property
    def available_ids(self): return 0 if self._range is None else self._range.end_exclusive - self._next

    @property
    def models(self): return tuple(self._models.values())

    @property
    def current_association(self): return self._observed

    @property
    def durable_hwm(self): return self._hwm

    @property
    def pending_allocation(self):
        return None if self._need is None else (self._need, self._need_job)

    def _source(self, source, *, historical=False):
        if (type(source) is not LaneSource or source.domain != (self.calibration_id, self.context.map_epoch,
                self.frame_id, self.context.odom_epoch) or not historical and source.stamp.clock_id != self.clock_id):
            raise ValueError('lane source changed original geometry/clock domain')

    def observe(self, road, source, prior, *, prior_qualified, crossing, profile=RegistrationProfile()):
        self._source(source)
        if type(prior) is not PoseBox or type(profile) is not RegistrationProfile or type(prior_qualified) is not bool or profile.outlier_components:
            raise ValueError('joint actual prior and no-outlier bounded profile required')
        valid = _geometry(road, source, crossing)
        if type(crossing) is not bytes: raise ValueError('declared crossing bytes required')
        geometry = _road_fields(road) if valid else (road.status, road.complete, len(road.hypotheses))
        key = sha256(canonical((asdict(source), geometry, crossing.hex()))).hexdigest()
        if valid: road = _road_from(json_bytes(canonical(geometry), 65536))
        def result(status, **kw):
            self._observed = LaneAssociation(status, road, source, crossing, self.revision, key, **kw)
            return self._observed
        previous = self._heads.get(source.stamp.key[:-1])
        if previous is not None:
            sequence, old_key, capture = previous
            if source.stamp.sequence < sequence: return result('STALE')
            if source.stamp.sequence == sequence:
                if old_key is not None and old_key != key: raise ValueError('same capture has conflicting lane geometry/time')
                return result('DUPLICATE')
            if capture is not None and source.stamp.captured_us is not None and source.stamp.captured_us[0] < capture[0]:
                raise ValueError('original lane capture regressed')
        if (not valid or not source.qualified or not road.calibration_qualified or not prior_qualified
                or source.stamp.captured_us is None or self._restored_at is not None and source.stamp.captured_us[0] <= self._restored_at):
            return result('UNKNOWN', reasons=('ORIGINAL_GEOMETRY_OR_PRIOR_UNAVAILABLE',))
        h = road.hypotheses[0]; matches = []; unresolved = False; cells = tests = 0
        for model in self._models.values():
            points, segments = _common_points(road, model, prior, source.sha256)
            if not points: unresolved = True; continue
            if cells >= profile.max_cells or tests >= profile.max_distance_tests:
                unresolved = True; continue
            remaining = replace(profile, max_cells=profile.max_cells-cells, max_distance_tests=profile.max_distance_tests-tests)
            reference = FrozenReference(model.model_revision, self.calibration_id, segments)
            fit = register(points, reference, prior, remaining, calibration_id=self.calibration_id)
            cells += fit.tested_cells; tests += fit.distance_tests
            if fit.feasible: matches.append((model.lane_ids, fit))
        checked = sum(len(m.lane_ids) for m in self._models.values())
        if unresolved: return result('UNKNOWN', matches=tuple(ids for ids, _ in matches), checked_lanes=checked, reasons=('COMMON_COVERAGE_OR_WORK_UNRESOLVED',))
        if len(matches) > 1: return result('AMBIGUOUS', matches=tuple(ids for ids, _ in matches), checked_lanes=checked)
        if matches:
            ids, fit = matches[0]
            if fit.status != 'BOUNDED' or not fit.complete: return result('UNKNOWN', matches=(ids,), checked_lanes=checked)
            pose, position, yaw = fit.representative, fit.position_error_m, fit.yaw_error_rad
        else:
            pose, position, yaw = prior.center, math.hypot(*prior.widths[:2]) / 2, prior.widths[2] / 2
            ids = ()
        radius = max(math.hypot(*p) for p in h.divider + h.left_outer + h.right_outer)
        if h.error_m + position + 2 * radius * math.sin(yaw / 2) > profile.max_position_error_m or h.yaw_error_rad + yaw > profile.max_yaw_error_rad:
            return result('UNKNOWN', checked_lanes=checked, reasons=('GEOMETRY_ENVELOPE_TOO_WIDE',))
        return result('UNIQUE' if ids else 'NEW', pose=pose, position_error_m=position,
            yaw_error_rad=yaw, matches=(ids,) if ids else (), checked_lanes=checked)

    def allocation_need(self, association, head, job, *, reservation_nonce, now_us, count=64):
        if self._transition is not None or association is not self._observed or type(association) is not LaneAssociation or association.status != 'NEW' or association.catalog_revision != self.revision:
            raise ValueError('same current unique new geometry required')
        if self.available_ids >= 2: return None
        if (type(head) is not CatalogHeadSnapshot or type(job) is not StorageJob or job.context != self.context
                or not head.current(job, self.context.sha256, now_us)):
            raise ValueError('actual current D head/job required')
        uint(count, 64, positive=True)
        hwm = next(value for kind, value, _ in head.heads if kind == 'LANE')
        if count < 2 or hwm + count > 1 << 32 or len(self._models) >= 32 or self._hwm is not None and hwm < self._hwm:
            raise ValueError('catalog capacity/HWM exhausted; wrap/reset forbidden')
        reservation = CatalogReservation(self.context.database_uuid, self.context.map_epoch, 'LANE', hwm, hwm,
            hwm+count, reservation_nonce, self.context.mapper_worker, self.context.sha256)
        need = LaneAllocationNeed(reservation, job.job_id, association.source_digest, self.revision)
        if self._need is not None and (self._need != need or self._need_job != job):
            raise ValueError('original lane allocation unresolved')
        self._need, self._need_job = need, job
        return need

    def apply_committed_range(self, need, job, payload, receipt, *, current_context_sha256, now_us):
        if self.accepted_range is not None and self.accepted_range[0] == need and self.accepted_range[1:] == (job, payload, receipt): return False
        if (self._transition is not None or self._need != need or type(need) is not LaneAllocationNeed
                or type(job) is not StorageJob or type(payload) is not CheckpointPayload or type(receipt) is not CommitReceipt
                or job != self._need_job or need.job_id != job.job_id or job.context != self.context or current_context_sha256 != self.context.sha256
                or not receipt.current(job, payload, current_context_sha256, now_us)):
            raise ValueError('original current COMMITTED lane allocation required')
        payload.validate_job(job)
        r = need.reservation
        if r not in payload.evidence.reservations or not r.matches(job) or self._hwm is not None and r.start_inclusive < self._hwm:
            raise ValueError('reservation missing or HWM regressed')
        self._range, self._next, self._hwm = r, r.start_inclusive, r.end_exclusive
        self.accepted_range = need, job, payload, receipt; self._need = self._need_job = None
        return True

    def retire_allocation(self, need, job, payload, receipt):
        if (self._need != need or job != self._need_job or type(payload) is not CheckpointPayload
                or type(receipt) is not CommitReceipt or receipt.status not in ('COMMITTED', 'DUPLICATE', 'FAILED')
                or (receipt.database_uuid, receipt.job_id, receipt.job_digest, receipt.origin_context_sha256)
                != (job.context.database_uuid, job.job_id, job.digest(payload), job.context.sha256)):
            raise ValueError('exact original terminal allocation required')
        payload.validate_job(job)
        if need.reservation not in payload.evidence.reservations: raise ValueError('original reservation missing from terminal job')
        self._need = self._need_job = None

    def cancel_allocation(self, need):
        """미전송 또는 실제 owner 정리 뒤 원 대기만 닫는다. D HWM/번호는 바꾸지 않는다."""
        if type(need) is not LaneAllocationNeed or need is not self._need:
            raise ValueError('cancellation differs from the original lane allocation')
        self._need = self._need_job = None

    def update(self, association, *, provenance_ids):
        if association is not self._observed or type(association) is not LaneAssociation or association.catalog_revision != self.revision:
            raise ValueError('same catalog revision association required')
        self._source(association.source)
        if association.status not in ('NEW', 'UNIQUE') or self._transition is not None: return None
        if association.status == 'NEW':
            if self.available_ids < 2 or len(self._models) >= 32: return None
            ids, revisions = (self._next, self._next+1), (1, 1)
        else:
            if len(association.matches) != 1: raise ValueError('unique actual lane assignment required')
            ids = association.matches[0]; old = self._models[ids]
            revisions = tuple(uint(r+1, positive=True) for r in old.lane_revisions)
        h = association.road.hypotheses[0]; radius = max(math.hypot(*p) for name in _SHAPES for p in getattr(h, name))
        shapes = {name: tuple(transform_point(p, association.pose) for p in getattr(h, name)) for name in _SHAPES}
        mapped = replace(h, **shapes, error_m=h.error_m+association.position_error_m+2*radius*math.sin(association.yaw_error_rad/2),
            yaw_error_rad=h.yaw_error_rad+association.yaw_error_rad, semantic_support=None, semantic_source=None)
        road = replace(association.road, hypotheses=(mapped,), work={})
        revision = uint(self.revision+1, positive=True)
        candidate = LaneModelCandidates(road, association.source, ids, revisions, revision,
            association.crossing, provenance_ids, self.context.sha256)
        scope = association.source.stamp.key[:-1]
        if scope not in self._heads and len(self._heads) >= 16: raise ValueError('source registry exhausted')
        self._models[ids] = candidate; self.revision = revision; self.latest = candidate
        self._heads[scope] = association.source.stamp.sequence, association.source_digest, association.source.stamp.captured_us
        if association.status == 'NEW': self._next += 2
        return candidate

    def replace_publication_context(self, context, *, now_us):
        uint(now_us)
        if not same_catalog_geometry(self.context, context, 'LANE'): raise ValueError('lane geometry owner changed')
        if context == self.context: return self._transition is None
        old = int(json_bytes(self.context.source_binding, 2048)['source_binding_revision'])
        new = int(json_bytes(context.source_binding, 2048)['source_binding_revision'])
        pending = old if self._transition is None else int(json_bytes(self._transition.source_binding, 2048)['source_binding_revision'])
        if new <= old or new < pending or new == pending and context != self._transition: raise ValueError('publication revision regressed/conflicted')
        self._transition = context
        if self._need is not None: return False
        self.context = context; self._transition = None
        return True

    def checkpoint_records(self):
        return tuple(record for model in self._models.values() for record in model.records())

    def originals(self):
        return {identity: model for model in self._models.values() for identity in model.provenance_ids}

    def state_fields(self):
        return dict(profile='lane-catalog-state-1', catalog_revision=self.revision,
            models=[model.fields() for model in self._models.values()])

    def restore(self, loaded, *, now_us, state_key='lane_catalog'):
        """현재 LOAD와 manifest가 참조한 상태 page가 모두 있어야 복원한다. range는 복원하지 않는다."""
        if (type(loaded) is not LoadedCheckpoint
                or not loaded.current(loaded.request, self.context.sha256, now_us)):
            raise ValueError('fresh owner and actual current LOAD required')
        self._restore_catalog(loaded.payload.manifest,loaded.payload.evidence,loaded.heads,
            now_us=now_us,state_key=state_key)

    def restore_complete(self, complete, *, current_scope, now_us, state_key='lane_catalog'):
        """BOOTSTRAP의 원 전체 자료를 새 publication에 연결한다. 원 head SHA/기한은 불변이다."""
        from ..runtime.scene import SceneEvidenceScope
        if (type(current_scope) is not SceneEvidenceScope or current_scope.context!=self.context
                or current_scope.calibration.sha256!=self.calibration_id):
            raise ValueError('actual current registered/calibrated source scope required')
        scope=current_scope.with_complete_history(complete,now_us=now_us)
        _,head=scope.complete_restore_head(complete,now_us=now_us)
        self._restore_catalog(complete.manifest,complete.evidence,head,now_us=now_us,state_key=state_key)

    def _restore_catalog(self,manifest_bytes,evidence,head,*,now_us,state_key):
        from ..storage.mapper_state import decode_mapper_state
        if self._models or self._range is not None or self._need is not None:
            raise ValueError('restore requires a fresh catalog owner')
        manifest = json_bytes(manifest_bytes, 16384)
        state, _ = decode_mapper_state(manifest['scene'], evidence.contexts)
        fields = None if type(state) is not dict else state.get(state_key)
        if type(fields) is not dict or set(fields) != {'profile', 'catalog_revision', 'models'} or fields['profile'] != 'lane-catalog-state-1':
            raise ValueError('referenced original lane catalog state missing')
        uint(fields['catalog_revision']); models = fields['models']
        if type(models) is not list or len(models) > 32: raise ValueError('bounded complete lane state required')
        staged = {}; expected = {}; heads = {}; provenance = {p.provenance_id: p for p in evidence.provenance}
        contexts = {c.sha256 for c in evidence.contexts}
        for value in models:
            model = LaneModelCandidates.from_fields(value); self._source(model.source, historical=True)
            if (model.lane_ids in staged or set(model.lane_ids) & {v for m in staged.values() for v in m.lane_ids}
                    or model.model_revision > fields['catalog_revision']): raise ValueError('duplicate IDs/revision in original lane state')
            for record in model.records():
                p = provenance.get(record.provenance_id)
                if p is None or p.context_sha256 not in contexts: raise ValueError('original lane provenance/context closure missing')
                expected[record.stable_id] = record
            staged[model.lane_ids] = model
            stamp = model.source.stamp; scope = stamp.key[:-1]
            if scope not in heads or stamp.sequence > heads[scope][0]: heads[scope] = stamp.sequence, None, stamp.captured_us
        records = tuple(r for r in evidence.catalog if r.kind == 'LANE')
        if (len(heads) > 16 or len(records) != len(expected) or {r.stable_id for r in records} != set(expected)
                or any(expected.get(r.stable_id) != r for r in records)):
            raise ValueError('LANE1 digest/actual catalog/original geometry mismatch')
        current_hwm = next(value for kind, value, _ in head.heads if kind == 'LANE')
        if current_hwm <= max(expected, default=0): raise ValueError('actual current D head is below issued lane IDs')
        self._models = staged; self._heads = heads; self.revision = fields['catalog_revision']; self._restored_at = now_us
        self._hwm = current_hwm
