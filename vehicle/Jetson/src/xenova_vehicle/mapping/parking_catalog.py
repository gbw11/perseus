"""주차 관측의 공동 가능한 대응과 D가 예약한 stable ID를 관리한다.

catalog 전체 gate를 끝내기 전에 nearest8을 고르지 않는다. 독립 매칭 점수나
최저 loss는 ID 증명이 아니다. SQLite/파일/대기/랜덤 ID·최종 주차 선택은 없다.
"""

from collections import deque
from dataclasses import dataclass, replace
import hashlib
import itertools
import json
import math
import struct

from .registration import PoseBox, transform_point
from ..perception.parking import (SlotCandidate, SlotGeometryResult, SlotSource, OccupancyEvidence,
    VALID, UNKNOWN, CLEAR_REGION, STALE, SOURCE_UNAVAILABLE, ParkingProfile, polygons_touch, to_map)


MAX_SLOT_ID = (1 << 32) - 1
CATALOG_CAP = 256
_PAYLOAD = struct.Struct('<4sBBBBI10d4f6d32s32s')


def _canonical(value):
    from ..storage.contracts import canonical
    return canonical(value)


def _digest_batch(batch):
    return hashlib.sha256(_canonical((batch.source.sha256, batch.complete, batch.status,
        tuple((s.local_id, s.geometry_sha256, s.coordinates) for s in batch.candidates)))).hexdigest()


def _angle(value):
    return (value + math.pi) % (2 * math.pi) - math.pi


def _occupancy_fact(value):
    if value is None:
        return None
    return (value.state, value.reason, value.source.domain[:4], value.clock_id, value.map_revision, value.observed_lower_us,
        value.source_kinds, value.checked_cells, value.hit_cells, value.unknown_cells, value.required_region, value.qualified)


def _f32_bound(value, *, upper):
    rounded = struct.unpack('<f', struct.pack('<f', value))[0]
    if (rounded < value if upper else rounded > value):
        bits = struct.unpack('<I', struct.pack('<f', rounded))[0]
        rounded = struct.unpack('<f', struct.pack('<I', bits + (1 if upper else -1)))[0]
    return rounded


def _mid(candidate):
    return candidate.entry_midpoint


def _radius(candidate):
    return max(math.hypot(*p) for p in candidate.corners or candidate.partial_points)


def _pose_error(box, candidate):
    return math.hypot(box.widths[0], box.widths[1]) / 2 + 2 * _radius(candidate) * math.sin(min(math.pi, box.widths[2] / 2) / 2)


@dataclass(frozen=True)
class AssociationProfile:
    """미실측 geometric gate다. cap은 설계 상한이며 줄인 검색은 UNKNOWN으로 남긴다."""

    residual_m: float = .01
    max_position_error_m: float = .05
    max_yaw_error_rad: float = .10
    pose_steps: tuple = (.02, .02, .02)
    max_pose_cells: int = 4096
    max_distance_tests: int = 1 << 22
    max_assignments: int = 40320

    def __post_init__(self):
        for name in ('residual_m', 'max_position_error_m', 'max_yaw_error_rad'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('bounded association geometry budget required')
        if type(self.pose_steps) is not tuple or len(self.pose_steps) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 1 for v in self.pose_steps):
            raise ValueError('three positive finite pose split steps required')
        for name, maximum in (('max_pose_cells', 4096), ('max_distance_tests', 1 << 22), ('max_assignments', 40320)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError('association work cap exceeds the design profile')


@dataclass(frozen=True)
class SlotTrack:
    slot_id: int
    geometry_revision: int
    occupancy_revision: int
    candidate: SlotCandidate
    occupancy: OccupancyEvidence | None
    provenance_id: int
    restored: bool = False
    restore_barrier_us: int | None = None

    def __post_init__(self):
        for name, maximum in (('slot_id', MAX_SLOT_ID), ('geometry_revision', (1 << 63) - 1),
                              ('occupancy_revision', (1 << 63) - 1), ('provenance_id', (1 << 63) - 1)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError('positive stable ID/revision/provenance required')
        if type(self.candidate) is not SlotCandidate or self.candidate.coordinates != 'MAP':
            raise ValueError('catalog geometry must be in its original map frame')
        if self.occupancy is not None and (type(self.occupancy) is not OccupancyEvidence or self.occupancy.source.domain[:4] != self.candidate.source.domain[:4]):
            raise ValueError('occupancy must retain the same map/calibration/odom domain and its own original clock')
        if type(self.restored) is not bool:
            raise ValueError('restored flag must be explicit')
        if self.restore_barrier_us is not None and (type(self.restore_barrier_us) is not int
                or not 0 <= self.restore_barrier_us < 1 << 63 or not self.restored):
            raise ValueError('restore barrier belongs only to an explicitly restored track')

    @property
    def occupancy_state(self):
        """저장 상태/restore fence다. 현재 wire 후보에는 occupancy_at의 원 age 검사가 필요하다."""
        if self.occupancy is None or self.restored and self.occupancy.state == 2:
            return UNKNOWN
        return self.occupancy.state

    def geometry_age_ms(self, now_us, clock_id):
        age = self.candidate.source.stamp.age_us(now_us, clock_id)
        return (1 << 32) - 1 if age is None else min((1 << 32) - 1, (age + 999) // 1000)

    def occupancy_at(self, now_us, clock_id, *, profile=ParkingProfile()):
        """원 map lower time으로만 만료시킨다. restore/시계 변경이 FREE를 갱신하지 않는다."""
        if type(now_us) is not int or not 0 <= now_us < 1 << 63 or type(profile) is not ParkingProfile:
            raise ValueError('bounded current clock/profile required')
        value = self.occupancy
        if value is None:
            return None
        if (clock_id != value.clock_id or value.observed_lower_us is None or not value.qualified
                or now_us < value.evaluated_us or self.restored and value.state == CLEAR_REGION):
            return replace(value, state=UNKNOWN, reason=SOURCE_UNAVAILABLE, qualified=False)
        age = now_us - value.observed_lower_us
        limit = profile.free_age_us if value.state == CLEAR_REGION else profile.hit_age_us
        if age >= limit:
            return replace(value, state=UNKNOWN, reason=STALE, clearance_age_ms=min((1 << 32) - 1, (age + 999) // 1000),
                evaluated_us=now_us, qualified=False)
        return replace(value, clearance_age_ms=min((1 << 32) - 1, (age + 999) // 1000), evaluated_us=now_us)

    @property
    def occupancy_sha256(self):
        value = self.occupancy
        if value is None:
            return hashlib.sha256(b'no-clearance-evidence').hexdigest()
        from dataclasses import asdict
        return hashlib.sha256(_canonical(asdict(value))).hexdigest()

    def payload_bytes(self):
        """D CatalogRecord의 opaque payload. 원 source/근거 tree는 provenance closure가 보존한다."""
        c = self.candidate
        if len(c.corners) != 4:
            raise ValueError('only complete mapped geometry is catalogued')
        values = tuple(v for p in c.corners for v in p) + (c.position_bound_m, c.heading_bound_rad)
        intervals = tuple(_f32_bound(v, upper=bool(i % 2)) for i, v in enumerate(c.width_interval_m + c.depth_interval_m))
        usable = tuple(v for p in (c.usable_polygon[0], c.usable_polygon[1], c.usable_polygon[3]) for v in p)
        raw = _PAYLOAD.pack(b'PS11', c.geometry_state, UNKNOWN if self.occupancy is None else self.occupancy.state,
            0 if self.occupancy is None else self.occupancy.reason,
            0 if self.occupancy is None else self.occupancy.source_kinds, c.approach_lane_id,
            *values, *intervals, *usable, bytes.fromhex(c.source.sha256), bytes.fromhex(self.occupancy_sha256))
        if len(raw) > 224:
            raise ValueError('catalog payload exceeds the D record profile')
        return raw


@dataclass(frozen=True)
class FeasibleAssignment:
    pairs: tuple
    pose_envelope: PoseBox
    pose_cells: int


@dataclass(frozen=True)
class AssociationResult:
    status: str
    complete: bool
    assignments: tuple
    births: tuple
    candidate_ids: tuple
    reasons: tuple
    source_digest: str
    catalog_revision: int
    catalog_checked: int
    distance_tests: int
    assignments_checked: int


def _gate(observation, track, box, profile):
    target = track.candidate
    if observation.geometry_state != VALID or target.geometry_state != VALID:
        return True
    margin = observation.position_bound_m + target.position_bound_m + profile.residual_m
    gamma = _pose_error(box, observation)
    if any(math.dist(transform_point(p, box.center), q) > gamma + margin
           for p, q in zip(observation.corners, target.corners)):
        return False
    observed_yaw = math.atan2(observation.inward[1], observation.inward[0]) + box.center[2]
    target_yaw = math.atan2(target.inward[1], target.inward[0])
    allowance = box.widths[2] / 2 + observation.heading_bound_rad + target.heading_bound_rad
    return abs(_angle(observed_yaw - target_yaw)) <= allowance + 1e-12


def _spatially_disjoint(observation, track, prior, profile):
    """크기/role 차이만으로 기존 칸을 새 birth라고 하지 않는다."""
    a = tuple(transform_point(p, prior.center) for p in observation.usable_polygon)
    b = track.candidate.usable_polygon
    margin = _pose_error(prior, observation) + observation.position_bound_m + track.candidate.position_bound_m + profile.residual_m
    for polygon in (a, b):
        for p, q in zip(polygon, polygon[1:] + polygon[:1]):
            dx, dy = q[0] - p[0], q[1] - p[1]; length = math.hypot(dx, dy)
            if not length:
                continue
            normal = -dy / length, dx / length
            first = [x * normal[0] + y * normal[1] for x, y in a]
            second = [x * normal[0] + y * normal[1] for x, y in b]
            if min(second) - max(first) > margin or min(first) - max(second) > margin:
                return True
    return False


def _hull(cells):
    return PoseBox(*tuple((min(getattr(c, key)[0] for c in cells), max(getattr(c, key)[1] for c in cells)) for key in ('x', 'y', 'yaw')))


def associate_slots(batch, tracks, prior, *, catalog_revision=0, prior_qualified=False, profile=AssociationProfile()):
    """전체 catalog gate 뒤 모든 기존 대응을 공동 pose에서 검사한다.

    새 birth는 기존 어느 칸과도 공간상 대응할 수 없을 때만 분리한다. 그 뒤 기존
    대응의 permutation 상한만 8!이다. partial/new/null 혼합 전체가 8!이라고 하지 않는다.
    """
    if (type(batch) is not SlotGeometryResult or type(tracks) is not tuple or len(tracks) > CATALOG_CAP
            or any(type(t) is not SlotTrack for t in tracks) or type(prior) is not PoseBox
            or type(profile) is not AssociationProfile or type(prior_qualified) is not bool):
        raise ValueError('exact bounded batch/catalog/prior/profile required')
    if len({t.slot_id for t in tracks}) != len(tracks):
        raise ValueError('duplicate catalog stable ID')
    if type(catalog_revision) is not int or catalog_revision < 0:
        raise ValueError('invalid catalog revision')
    digest = _digest_batch(batch)
    def result(status, complete, assignments=(), births=(), ids=(), reasons=(), checked=0, tests=0, attempts=0):
        return AssociationResult(status, complete, tuple(assignments), tuple(births), tuple(ids), tuple(reasons),
            digest, catalog_revision, checked, tests, attempts)
    if not prior_qualified or not batch.source.qualified or batch.source.stamp.captured_us is None:
        return result('UNKNOWN', False, reasons=('UNQUALIFIED_CAPTURE_POSE',))
    if not batch.complete or len(batch.candidates) > 8:
        return result('UNKNOWN', False, reasons=('INCOMPLETE_OR_OBSERVATION_CAP',))
    if any(c.source != batch.source or c.coordinates != 'BASE' for c in batch.candidates):
        raise ValueError('association requires one original BASE observation batch')
    if any(t.candidate.source.domain[:4] != batch.source.domain[:4] or t.candidate.source.input_kind != batch.source.input_kind for t in tracks):
        raise ValueError('catalog map/calibration/odom domain mismatch')
    if any(c.geometry_state != VALID or not c.calibration_qualified for c in batch.candidates):
        return result('UNKNOWN', False, reasons=('PARTIAL_OR_AMBIGUOUS_GEOMETRY',))
    if not batch.candidates:
        return result('UNIQUE', True, assignments=(FeasibleAssignment((), prior, 1),), checked=len(tracks))
    gates = []; births = []; tested = 0; spatial_unknown = False
    for observation in batch.candidates:
        choices = []
        for track in tracks:
            if tested + 4 > profile.max_distance_tests:
                pending_ids = tuple((c.local_id, tuple(t.slot_id for t in tracks)) for c in batch.candidates)
                return result('UNKNOWN', False, ids=pending_ids, reasons=('CATALOG_GATE_WORK_CAP',),
                    checked=0, tests=tested)
            tested += 4
            if _gate(observation, track, prior, profile):
                choices.append(track.slot_id)
        gates.append((observation.local_id, tuple(choices)))
        if not choices:
            if tested + 9 * len(tracks) > profile.max_distance_tests:
                return result('UNKNOWN', False, ids=gates, reasons=('BIRTH_GATE_WORK_CAP',), checked=0, tests=tested)
            tested += 9 * len(tracks)
            if all(_spatially_disjoint(observation, track, prior, profile) for track in tracks):
                births.append(observation.local_id)
            else:
                spatial_unknown = True
    remaining = tuple(sorted({v for _, ids in gates for v in ids}))
    checked = len(tracks)
    if spatial_unknown:
        return result('UNKNOWN', False, ids=gates, reasons=('EXISTING_VERSUS_NEW_UNRESOLVED',), checked=checked, tests=tested)
    if len(remaining) > 8:
        return result('UNKNOWN', False, ids=gates, reasons=('SURVIVING_CATALOG_CAP',), checked=checked, tests=tested)
    matched = [c for c in batch.candidates if c.local_id not in births]
    # 서로 겹친 새 구획을 별도 ID로 굳히지 않는다.
    for i, c in enumerate(batch.candidates):
        if c.local_id in births and any(polygons_touch(c.corners, d.corners, interior=True)
                                      for d in batch.candidates[:i] if d.local_id in births):
            return result('AMBIGUOUS', True, ids=gates, reasons=('OVERLAPPING_BIRTHS',), checked=checked, tests=tested)
    if not matched:
        if max(_pose_error(prior, c) + c.position_bound_m for c in batch.candidates) > profile.max_position_error_m or prior.widths[2] / 2 > profile.max_yaw_error_rad:
            return result('UNKNOWN', False, ids=gates, reasons=('BIRTH_POSE_ENVELOPE_TOO_WIDE',), checked=checked, tests=tested)
        return result('NEW', True, assignments=(FeasibleAssignment((), prior, 1),), births=births, ids=gates, checked=checked, tests=tested)
    by_id = {t.slot_id: t for t in tracks}; choices = dict(gates)
    feasible = []; attempts = cells_tested = 0; limited = False
    for permutation in itertools.permutations(remaining, len(matched)):
        if any(identifier not in choices[obs.local_id] for obs, identifier in zip(matched, permutation)):
            continue
        if attempts >= profile.max_assignments:
            limited = True; break
        attempts += 1
        pairs = tuple((obs.local_id, identifier) for obs, identifier in zip(matched, permutation))
        pending = deque((prior,)); leaves = []
        while pending:
            if cells_tested >= profile.max_pose_cells or tested + len(matched) * 4 > profile.max_distance_tests:
                limited = True; leaves.extend(pending); pending.clear(); break
            cell = pending.popleft(); cells_tested += 1; tested += len(matched) * 4
            if any(not _gate(obs, by_id[identifier], cell, profile) for obs, identifier in zip(matched, permutation)):
                continue
            if all(w <= step for w, step in zip(cell.widths, profile.pose_steps)):
                leaves.append(cell)
            else:
                split = cell.split(profile.pose_steps)
                if split is None:
                    leaves.append(cell)
                else:
                    pending.extend(split)
        if leaves:
            feasible.append(FeasibleAssignment(pairs, _hull(leaves), len(leaves)))
        if limited:
            break
    if limited:
        return result('UNKNOWN', False, feasible, births, gates, ('ASSOCIATION_WORK_CAP',), checked, tested, attempts)
    if not feasible:
        return result('INCONSISTENT', True, ids=gates, reasons=('NO_COMMON_POSE_ASSIGNMENT',), checked=checked, tests=tested, attempts=attempts)
    if len(feasible) > 1:
        return result('AMBIGUOUS', True, feasible, births, gates, ('MULTIPLE_JOINT_ASSIGNMENTS',), checked, tested, attempts)
    pose = feasible[0].pose_envelope
    all_obs = tuple(batch.candidates)
    error = max(_pose_error(pose, c) + c.position_bound_m for c in all_obs)
    if error > profile.max_position_error_m or pose.widths[2] / 2 > profile.max_yaw_error_rad:
        return result('UNKNOWN', True, feasible, births, gates, ('GEOMETRY_ENVELOPE_TOO_WIDE',), checked, tested, attempts)
    return result('UNIQUE', True, feasible, births, gates, checked=checked, tests=tested, attempts=attempts)


@dataclass(frozen=True)
class AllocationNeed:
    reservation: object
    job_id: str
    source_digest: str
    catalog_revision: int
    required_count: int


@dataclass(frozen=True)
class AcceptedSlotRange:
    """최초 current 검사를 통과한 원 범위 근거다. scene 전환 때 원문을 다시 쓰지 않는다."""
    need: AllocationNeed
    job: object
    payload: object
    receipt: object


class SlotCatalog:
    """M 한 owner의 유한 catalog. D의 원 COMMITTED 범위를 쓰며 reset/ID 재사용은 없다."""

    def __init__(self, context, *, clock_id, frame_id=None, calibration_id=None):
        from ..storage.contracts import StorageContext
        if type(context) is not StorageContext or type(clock_id) is not str or not 1 <= len(clock_id) <= 128:
            raise ValueError('exact D context and original clock required')
        self.context, self.clock_id = context, clock_id
        self.calibration_id = context.calibration if calibration_id is None else calibration_id
        if (type(self.calibration_id) is not str or len(self.calibration_id) != 64
                or any(c not in '0123456789abcdef' for c in self.calibration_id)):
            raise ValueError('original local calibration profile SHA required')
        self.frame_id = context.frame_epoch if frame_id is None else frame_id
        if type(self.frame_id) is not str or not 1 <= len(self.frame_id) <= 128:
            raise ValueError('original map frame identity required')
        self._tracks = {}; self.revision = 0; self._heads = {}; self._need = None
        self._range = None; self._next = None; self._hwm = None; self._range_job = None
        self._accepted_range = None
        self._transition_target = None

    @property
    def tracks(self):
        return tuple(self._tracks[key] for key in sorted(self._tracks))

    @property
    def durable_hwm(self):
        return self._hwm

    @property
    def available_ids(self):
        return 0 if self._range is None else self._range.end_exclusive - self._next

    @property
    def accepted_range(self):
        return self._accepted_range

    @property
    def publication_transition(self):
        return self._transition_target

    def begin_publication_context(self, new_context, *, now_us):
        """실제 새 S 문맥을 대기시킨 뒤 old 미수락 예약의 신규 발급을 먼저 봉인한다."""
        from ..storage.contracts import StorageContext, json_bytes, decimal, uint
        from ..storage.geometry_scope import same_catalog_geometry
        uint(now_us)
        if type(new_context) is not StorageContext or not same_catalog_geometry(self.context, new_context, 'SLOT'):
            raise ValueError('publication transition changed the original catalog geometry owner')
        if new_context == self.context:
            return self._transition_target is None
        revision = decimal(json_bytes(new_context.source_binding, 2048)['source_binding_revision'], positive=True)
        original = decimal(json_bytes(self.context.source_binding, 2048)['source_binding_revision'], positive=True)
        if revision <= original:
            raise ValueError('publication transition must not replay an old source revision')
        if self._transition_target is not None:
            pending = decimal(json_bytes(self._transition_target.source_binding, 2048)['source_binding_revision'], positive=True)
            if revision < pending or revision == pending and new_context != self._transition_target:
                raise ValueError('pending publication transition changed or regressed')
        self._transition_target = new_context
        return True

    def replace_publication_context(self, new_context, *, now_us):
        """같은 M/실제 origin의 scene-only 전환이다. 미해결 원 예약은 False(WAIT)다.

        caller의 현재 S binding 수락은 외부 책임이다. 이미 수락한 range/cursor만
        유지하며 이 비교로 old receipt를 새 context에서 최초 수락하지 않는다.
        """
        if not self.begin_publication_context(new_context, now_us=now_us):
            return False
        if new_context == self.context:
            return True
        if self._need is not None:
            return False
        self.context = new_context; self._transition_target = None
        return True

    def _source(self, batch):
        if type(batch) is not SlotGeometryResult:
            raise ValueError('original slot geometry batch required')
        expected = (self.calibration_id, self.context.map_epoch, self.frame_id, self.context.odom_epoch, self.clock_id)
        if batch.source.domain != expected:
            raise ValueError('catalog observation context changed')

    def associate(self, batch, prior, *, prior_qualified, profile=AssociationProfile()):
        self._source(batch)
        return associate_slots(batch, self.tracks, prior, catalog_revision=self.revision,
            prior_qualified=prior_qualified, profile=profile)

    def allocation_need(self, batch, association, head, job, *, reservation_nonce, now_us, count=64):
        from ..storage.contracts import CatalogHeadSnapshot, CatalogReservation, StorageJob
        self._source(batch)
        if self._transition_target is not None:
            raise ValueError('pending publication transition cannot start a new allocation job')
        if (type(association) is not AssociationResult or association.source_digest != _digest_batch(batch)
                or association.catalog_revision != self.revision or not association.complete or association.status not in ('NEW', 'UNIQUE')):
            raise ValueError('allocation requires the same complete unique source/revision')
        if not association.births:
            return None
        if type(head) is not CatalogHeadSnapshot or type(job) is not StorageJob or job.context != self.context or not head.current(job, self.context.sha256, now_us):
            raise ValueError('current actual D head/job context required')
        if self._range is not None and self._next + len(association.births) <= self._range.end_exclusive:
            return None
        if len(self._tracks) + len(association.births) > CATALOG_CAP:
            raise ValueError('catalog full; new map epoch/admission required')
        if type(count) is not int or not len(association.births) <= count <= 256:
            raise ValueError('bounded explicit reservation count required')
        hwm = next(hwm for kind, hwm, _ in head.heads if kind == 'SLOT')
        if hwm + count > 1 << 32:
            raise ValueError('fixed32 ID exhausted; wrap/reset forbidden')
        reservation = CatalogReservation(self.context.database_uuid, self.context.map_epoch, 'SLOT', hwm, hwm, hwm + count,
            reservation_nonce, self.context.mapper_worker, self.context.sha256)
        need = AllocationNeed(reservation, job.job_id, _digest_batch(batch), self.revision, len(association.births))
        if self._need is not None and self._need != need:
            raise ValueError('original allocation job is still unresolved')
        self._need = need
        return need

    def apply_committed_range(self, need, job, payload, receipt, *, current_context_sha256, now_us):
        """이것은 allocation 완료다. 옛 source를 새 geometry/관측 완료로 세지 않는다."""
        from ..storage.contracts import CommitReceipt, StorageJob
        from ..storage.records import CheckpointPayload
        if (type(need) is not AllocationNeed or type(job) is not StorageJob or type(receipt) is not CommitReceipt
                or type(payload) is not CheckpointPayload
                or need.job_id != job.job_id or job.context != self.context or current_context_sha256 != self.context.sha256):
            raise ValueError('original allocation/job/current context mismatch')
        if self._range_job == (job.job_id, receipt.job_digest):
            return False
        if self._transition_target is not None:
            raise ValueError('old unaccepted receipt is historical after publication transition began')
        if self._need != need or not receipt.current(job, payload, current_context_sha256, now_us):
            raise ValueError('only original current COMMITTED result can supply a range')
        payload.validate_job(job)
        reservations = payload.evidence.reservations
        if type(reservations) is not tuple or need.reservation not in reservations or not need.reservation.matches(job):
            raise ValueError('committed payload does not contain the original immutable reservation')
        r = need.reservation
        if self._hwm is not None and r.start_inclusive < self._hwm:
            raise ValueError('catalog HWM cannot move backwards or reuse old IDs')
        self._range = r; self._next = r.start_inclusive; self._hwm = r.end_exclusive
        self._range_job = job.job_id, receipt.job_digest; self._need = None
        self._accepted_range = AcceptedSlotRange(need, job, payload, receipt)
        return True

    def retire_allocation(self, need, job, payload, receipt):
        """알려진 원 결과만 정리한다. historical COMMITTED의 번호를 새 발행권으로 쓰지 않는다."""
        from ..storage.contracts import CommitReceipt
        if (self._need != need or type(receipt) is not CommitReceipt
                or receipt.status not in ('COMMITTED', 'DUPLICATE', 'FAILED')
                or (receipt.database_uuid, receipt.job_id, receipt.job_digest, receipt.origin_context_sha256)
                != (self.context.database_uuid, job.job_id, job.digest(payload), self.context.sha256)):
            raise ValueError('unknown/mismatched allocation result cannot release the original job')
        self._need = None

    def cancel_allocation(self, need):
        """미전송/실제 owner 정리를 확인한 caller가 원 대기만 닫는다. 번호는 발급하지 않는다."""
        if need is not self._need:
            raise ValueError('cancellation differs from the original allocation')
        self._need = None

    def update(self, batch, association, *, provenance_ids, occupancy=()):
        """단일 source를 한 번 반영한다. 공간/ID 모호함 또는 range 대기면 변화 없이 False다."""
        self._source(batch)
        if type(association) is not AssociationResult or association.source_digest != _digest_batch(batch):
            raise ValueError('association belongs to a different original source')
        scope = batch.source.stamp.key[:-1]; sequence = batch.source.stamp.sequence
        digest = _digest_batch(batch); previous = self._heads.get(scope)
        if previous is not None:
            old_sequence, old_digest, old_capture = previous
            if sequence < old_sequence:
                return False
            if sequence == old_sequence:
                if old_digest is not None and (digest != old_digest or batch.source.stamp.captured_us != old_capture):
                    raise ValueError('same original source key has conflicting geometry/time')
                return False
            if old_capture is not None and batch.source.stamp.captured_us is not None and batch.source.stamp.captured_us[0] < old_capture[0]:
                raise ValueError('capture time moved backwards')
        elif len(self._heads) >= 16:
            raise ValueError('source registry full; explicit source replacement required')
        if association.catalog_revision != self.revision:
            raise ValueError('association belongs to a different catalog revision')
        if not association.complete or association.status not in ('NEW', 'UNIQUE') or len(association.assignments) != 1:
            return False
        count = len(association.births)
        if count and (self._range is None or self._next + count > self._range.end_exclusive):
            return False
        if len(self._tracks) + count > CATALOG_CAP:
            return False
        if type(provenance_ids) is not dict or set(provenance_ids) != {c.local_id for c in batch.candidates}:
            raise ValueError('each original candidate needs its committed provenance ID')
        by_occupancy = dict(occupancy)
        if len(by_occupancy) != len(occupancy) or set(by_occupancy) - set(provenance_ids):
            raise ValueError('occupancy keys must identify the original candidate set')
        mapped = dict(association.assignments[0].pairs); staged = dict(self._tracks); next_id = self._next
        pose = association.assignments[0].pose_envelope
        for candidate in batch.candidates:
            identifier = mapped.get(candidate.local_id)
            if identifier is None:
                if candidate.local_id not in association.births:
                    raise ValueError('unexplained observation cannot acquire an ID')
                identifier = next_id; next_id += 1
            original = staged.get(identifier)
            transformed = to_map(candidate, pose.center, position_error_m=math.hypot(*pose.widths[:2]) / 2, yaw_error_rad=pose.widths[2] / 2)
            # 같은 capture를 반복 투표해 bound를 줄이지 않고 새 관측의 선언 오차도 축소하지 않는다.
            if original is not None:
                transformed = replace(transformed, position_bound_m=max(original.candidate.position_bound_m, transformed.position_bound_m),
                    heading_bound_rad=max(original.candidate.heading_bound_rad, transformed.heading_bound_rad))
            observed = by_occupancy.get(candidate.local_id)
            if observed is not None and (type(observed) is not OccupancyEvidence or observed.source != batch.source):
                raise ValueError('occupancy belongs to another original source')
            if observed is not None and observed.state == CLEAR_REGION and observed.source_kinds & 4:
                raise ValueError('retained-static clearance requires the separate scene admission flow')
            if original is not None and (observed is None or original.occupancy is not None
                    and observed.clock_id == original.occupancy.clock_id and observed.map_revision < original.occupancy.map_revision):
                observed = original.occupancy
            previous_occupancy = None if original is None else original.occupancy
            if (observed is not None and previous_occupancy is not None
                    and observed.map_revision == previous_occupancy.map_revision
                    and observed.clock_id == previous_occupancy.clock_id
                    and observed.required_region == previous_occupancy.required_region
                    and observed.state == previous_occupancy.state
                    and observed.observed_lower_us is not None and previous_occupancy.observed_lower_us is not None):
                lower = min(observed.observed_lower_us, previous_occupancy.observed_lower_us)
                age = min((1 << 32) - 1, (observed.evaluated_us - lower + 999) // 1000)
                observed = replace(observed, observed_lower_us=lower, clearance_age_ms=age)
            if previous_occupancy is not None and _occupancy_fact(observed) == _occupancy_fact(previous_occupancy):
                observed = previous_occupancy
            # 새 capture의 원 source/provenance도 불변 record의 내용이다. 모양이 같아도
            # 기존 (geometry,occupancy) key에 다른 source bytes를 덮어쓰지 않는다.
            geometry_revision = 1 if original is None else original.geometry_revision + 1
            occupancy_revision = 1 if original is None else original.occupancy_revision
            restored = original is not None and original.restored
            barrier = None if original is None else original.restore_barrier_us
            if (restored and barrier is not None and observed is not None and observed.state == CLEAR_REGION
                    and observed.clock_id == self.clock_id and observed.observed_lower_us is not None
                    and observed.observed_lower_us > barrier):
                restored = False; barrier = None
            proposed = SlotTrack(identifier, geometry_revision, occupancy_revision, transformed, observed,
                provenance_ids[candidate.local_id], restored=restored, restore_barrier_us=barrier)
            if original is not None and _occupancy_fact(proposed.occupancy) != _occupancy_fact(original.occupancy):
                proposed = replace(proposed, occupancy_revision=occupancy_revision + 1)
            staged[identifier] = proposed
        if self.revision == (1 << 63) - 1:
            raise ValueError('catalog revision exhausted')
        self._tracks = staged; self._next = next_id; self.revision += 1
        self._heads[scope] = sequence, digest, batch.source.stamp.captured_us
        return True

    def update_occupancy(self, values, *, provenance_ids):
        """새 공간 근거만 갱신한다. cached RGB를 새 geometry 관측으로 세지 않는다."""
        if type(values) is not dict or type(provenance_ids) is not dict or set(values) != set(provenance_ids):
            raise ValueError('exact slot/evidence/provenance mapping required')
        staged = dict(self._tracks); changed = False
        for identifier, value in values.items():
            original = staged.get(identifier)
            if original is None or type(value) is not OccupancyEvidence or value.source != original.candidate.source or value.clock_id != self.clock_id:
                raise ValueError('occupancy belongs to a different original slot/map clock')
            if value.state == CLEAR_REGION and value.source_kinds & 4:
                raise ValueError('scene admission is separate from live occupancy')
            old = original.occupancy
            if old is not None and old.clock_id == value.clock_id:
                if value.map_revision < old.map_revision:
                    continue
                if (value.map_revision == old.map_revision and value.required_region == old.required_region
                        and value.state == old.state and value.observed_lower_us is not None and old.observed_lower_us is not None):
                    lower = min(value.observed_lower_us, old.observed_lower_us)
                    value = replace(value, observed_lower_us=lower,
                        clearance_age_ms=min((1 << 32) - 1, (value.evaluated_us - lower + 999) // 1000))
            if _occupancy_fact(value) == _occupancy_fact(old):
                continue
            restored, barrier = original.restored, original.restore_barrier_us
            if restored and barrier is not None and value.state == CLEAR_REGION and value.observed_lower_us is not None and value.observed_lower_us > barrier:
                restored = False; barrier = None
            staged[identifier] = replace(original, occupancy=value, occupancy_revision=original.occupancy_revision + 1,
                provenance_id=provenance_ids[identifier], restored=restored, restore_barrier_us=barrier)
            changed = True
        if changed:
            if self.revision == (1 << 63) - 1:
                raise ValueError('catalog revision exhausted')
            self._tracks = staged; self.revision += 1
        return changed

    def repack_provenance(self, provenance_ids):
        """checkpoint metadata의 불변 key를 예약한다. 원 관측시각/geometry vote는 바꾸지 않는다."""
        if type(provenance_ids) is not dict or set(provenance_ids) != set(self._tracks):
            raise ValueError('complete catalog provenance repack required')
        staged = {identifier: replace(track, geometry_revision=track.geometry_revision + 1,
                    provenance_id=provenance_ids[identifier]) for identifier, track in self._tracks.items()}
        if self.revision == (1 << 63) - 1:
            raise ValueError('catalog revision exhausted')
        self._tracks = staged; self.revision += 1
        return self.checkpoint_records()

    def checkpoint_records(self):
        from ..storage.records import CatalogRecord
        return tuple(CatalogRecord('SLOT', t.slot_id, t.geometry_revision, t.occupancy_revision,
            t.provenance_id, t.payload_bytes()) for t in self.tracks)

    def restore(self, records, originals, *, catalog_revision, restored_at_us):
        """원 checkpoint와 provenance closure를 함께 복원한다. 미사용 예약 번호는 재활용하지 않는다."""
        from ..storage.records import CatalogRecord
        if self._tracks or self._range is not None or self._need is not None:
            raise ValueError('restore requires a fresh catalog owner')
        if type(records) is not tuple or len(records) > CATALOG_CAP or type(originals) is not dict or len(originals) > CATALOG_CAP:
            raise ValueError('bounded complete records and original provenance required')
        if type(catalog_revision) is not int or not 0 <= catalog_revision < 1 << 63:
            raise ValueError('original checkpoint catalog revision required')
        if type(restored_at_us) is not int or not 0 <= restored_at_us < 1 << 63:
            raise ValueError('actual restore barrier in the current original clock required')
        staged = {}
        for record in records:
            if type(record) is not CatalogRecord or record.kind != 'SLOT' or record.stable_id in staged:
                raise ValueError('unexpected/duplicate catalog record')
            original = originals.get(record.provenance_id)
            if type(original) is not tuple or len(original) != 2:
                raise ValueError('original geometry/occupancy provenance is missing')
            source, occupancy = original
            if type(source) is not SlotSource or source.domain[:4] != (self.calibration_id,
                    self.context.map_epoch, self.frame_id, self.context.odom_epoch):
                raise ValueError('restored source domain differs from original map')
            if len(record.payload) != _PAYLOAD.size:
                raise ValueError('unknown parking payload profile')
            values = _PAYLOAD.unpack(record.payload)
            magic, geometry, state, reason, kinds, lane = values[:6]
            numeric = values[6:-2]; source_sha, occupancy_sha = values[-2:]
            if (magic != b'PS11' or geometry != VALID or state not in (0, 1, 2) or reason not in range(8)
                    or kinds & ~7 or any(not math.isfinite(v) for v in numeric)
                    or source_sha.hex() != source.sha256):
                raise ValueError('parking payload/source grammar mismatch')
            corners = tuple(tuple(numeric[i:i + 2]) for i in range(0, 8, 2))
            p0, p1, p3 = (tuple(numeric[i:i + 2]) for i in (14, 16, 18))
            p2 = p1[0] + p3[0] - p0[0], p1[1] + p3[1] - p0[1]
            length = math.dist(p0, p3)
            if length <= 0:
                raise ValueError('restored usable depth is degenerate')
            inward = (p3[0] - p0[0]) / length, (p3[1] - p0[1]) / length
            candidate = SlotCandidate(0, (), geometry, corners, (), corners[:2], inward,
                tuple(numeric[10:12]), tuple(numeric[12:14]), (p0, p1, p2, p3), numeric[8], numeric[9],
                source, (), 'restored_committed', lane, calibration_qualified=False, coordinates='MAP')
            track = SlotTrack(record.stable_id, record.geometry_revision, record.occupancy_revision,
                candidate, occupancy, record.provenance_id, restored=True, restore_barrier_us=restored_at_us)
            if (occupancy_sha.hex() != track.occupancy_sha256 or state != (UNKNOWN if occupancy is None else occupancy.state)
                    or reason != (0 if occupancy is None else occupancy.reason) or kinds != (0 if occupancy is None else occupancy.source_kinds)):
                raise ValueError('restored occupancy does not match its original provenance')
            if track.payload_bytes() != record.payload:
                raise ValueError('catalog payload/provenance closure mismatch')
            staged[track.slot_id] = track
        heads = {}
        for track in staged.values():
            stamp = track.candidate.source.stamp; scope = stamp.key[:-1]
            previous = heads.get(scope)
            if previous is None or stamp.sequence > previous[0]:
                heads[scope] = stamp.sequence, None, stamp.captured_us
        if len(heads) > 16:
            raise ValueError('restored source registry exceeds the bounded profile')
        self._tracks = staged; self.revision = catalog_revision; self._heads = heads
        # 과거 range/HWM은 현재 worker의 발행권이 아니다.
        self._range = self._next = self._hwm = None
