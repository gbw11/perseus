"""M-compute의 채택 catalog와 잠정 관측, 원 D 작업을 분리한다.

prepare는 유한 owner container만 복제하고 외부 요청을 내보내지 않는다. accept는
이미 최종 검사를 통과한 참조 하나를 바꾼다. maintenance만 같은 compute thread에서
큰 encode/완료 소비와 원 mailbox offer를 수행한다. actor·token·SQL·선택권은 없다.
"""

from copy import copy
from dataclasses import dataclass, replace
import threading
from uuid import UUID

from .mapper_compute import MapperInput
from .mapper_storage import MapperStorageMailbox, StorageAbort
from .parking_bridge import ParkingBridge, ParkingNotReady, MetricEvidence, WireProvenanceGrant
from ..mapping.lane_catalog import LaneCatalog, LaneSource
from ..mapping.local_map import GridSpec
from ..mapping.odometry import PoseEstimate
from ..mapping.registration import PoseBox, RegistrationProfile
from ..mapping.road_basis import RoadBasis
from ..mapping.parking_catalog import AssociationProfile
from ..perception.calibration import BodyModel
from ..perception.inputs import FrameStamp
from ..perception.parking import ParkingAisle, ParkingProfile
from ..perception.pipeline import PerceptionResult
from ..storage.contracts import (StorageContext, StorageJob, CatalogHeadSnapshot, CommitReceipt,
    ProvenanceRange, LoadRequest, canonical, json_bytes, uint)
from ..storage.port import CheckpointRequest
from ..storage.records import EvidenceTable, CheckpointPayload, LoadedCheckpoint, PROVENANCE
from ..storage.mapper_state import decode_mapper_state,mapper_state_roots


class CatalogNotReady(RuntimeError):
    """원 시각/ID/provenance/head 또는 D terminal이 아직 준비되지 않았다."""


def _fork(owner):
    # 이 owner의 dict 값은 frozen source/track/request 또는 bytes다. mailbox/lock/
    # grid 배열은 owner에 없으며 원 request is identity를 복제하지 않는다.
    result = copy(owner)
    for name, value in vars(owner).items():
        if type(value) is dict: setattr(result, name, dict(value))
    return result


def _merge_evidence(*values):
    records = {}; contexts = {}
    for value in values:
        if type(value) is not EvidenceTable: raise ValueError('actual immutable parent evidence required')
        for record in value.provenance:
            if record.provenance_id in records and records[record.provenance_id] != record:
                raise ValueError('original immutable provenance conflict')
            records[record.provenance_id] = record
        for context in value.contexts: contexts[context.sha256] = context
    return EvidenceTable(tuple(records[k] for k in sorted(records)), tuple(contexts[k] for k in sorted(contexts)))


def _closure(evidence, roots, context):
    records = {r.provenance_id: r for r in evidence.provenance}
    contexts = {c.sha256: c for c in evidence.contexts}; kept = {}; pending = list(roots)
    while pending:
        identity = pending.pop()
        if identity in kept: continue
        record = records.get(identity)
        if record is None or record.context_sha256 not in contexts:
            raise CatalogNotReady('original collector parent closure missing')
        if PROVENANCE.unpack(record.payload)[5] != UUID(context.pi_host_boot).bytes:
            raise ValueError('catalog parent belongs to another original Pi host')
        kept[identity] = record; pending.extend(record.parents)
    required = {r.context_sha256 for r in kept.values()}
    return EvidenceTable(tuple(kept[k] for k in sorted(kept)), tuple(contexts[k] for k in sorted(required)))


@dataclass(frozen=True)
class CatalogView:
    lane_status: str
    lane: object
    parking: object
    reasons: tuple = ()
    road_basis: object = None

    def summary(self):
        return dict(lane_status=self.lane_status, lane_ids=[] if self.lane is None else list(self.lane.lane_ids),
            parking_candidates=0 if self.parking is None else self.parking.total_tracks_in_roi,
            reasons=list(self.reasons[:8]),road_basis_status='NOT_READY' if self.road_basis is None else self.road_basis.status,
            road_reference_id=None if self.road_basis is None else self.road_basis.reference_id,
            road_reference_revision=None if self.road_basis is None else self.road_basis.reference_revision,
            metric_ready=False, live_authority=False)


@dataclass(frozen=True)
class MappingState:
    lane: LaneCatalog
    parking: ParkingBridge
    evidence: EvidenceTable
    view: CatalogView
    road_basis: RoadBasis
    road_basis_material: bytes | None = None

    def fork(self):
        parking = _fork(self.parking); parking.catalog = _fork(self.parking.catalog)
        basis=copy(self.road_basis);basis._events=list(self.road_basis._events)
        return replace(self,lane=_fork(self.lane),parking=parking,road_basis=basis)


def _waiting_basis(state,reason='CURRENT_LANE_REQUIRED'):
    """옛 prefix/source는 남기고 현재 association 부재를 readonly view에 명시한다."""
    if state.road_basis_material is not None:return None
    return replace(state.road_basis.snapshot(),status='WAIT_CURRENT',reason=reason)


def _basis_roots(state):
    roots=set(state.road_basis.originals())
    if state.road_basis_material is not None:
        roots.update(mapper_state_roots({'road_basis':json_bytes(state.road_basis_material,512*1024)}))
    return roots


@dataclass(frozen=True)
class CatalogDraft:
    original_job: MapperInput
    context: StorageContext
    accepted_revision: int
    state: MappingState

    @property
    def view(self): return self.state.view


@dataclass(frozen=True)
class CatalogPending:
    role: str
    request: object
    need: object = None


@dataclass(frozen=True)
class CatalogTerminal:
    """한 번도 offer하지 않은 로컬 원 작업의 철회다. wire RESULT/native 반환이 아니다."""
    request: object
    reason: str
    observed_us: int


class MapperCatalogs:
    """단일 compute owner. aisle_lane_indices는 설치한 lane-role 0/1이며 ego 선택이 아니다.

    crossing은 명시 bytes 또는 (road,stamp)->bytes 정책이다. None이면 UNCONFIGURED.
    D job/nonce는 caller가 원 기한과 함께 제공하며 이 객체는 ID/시각을 생성하지 않는다.
    """
    def __init__(self, context, *, mailbox, clock_id, map_frame_id, body,
            method_manifest_sha256, input_kind, calibration_profile=None, parking_aisles=(),
            aisle_lane_indices=None, crossing=None, lane_profile=RegistrationProfile(),
            parking_profile=ParkingProfile(), association_profile=AssociationProfile()):
        if type(context) is not StorageContext or type(mailbox) is not MapperStorageMailbox or mailbox.context_sha256 != context.sha256:
            raise ValueError('same original context/mailbox required')
        if type(body) is not BodyModel or type(lane_profile) is not RegistrationProfile:
            raise ValueError('explicit body/registration policy required')
        if (type(parking_aisles) is not tuple or len(parking_aisles) > 4
                or any(type(a) is not ParkingAisle for a in parking_aisles)):
            raise ValueError('at most four declared parking aisles required')
        if aisle_lane_indices is not None and (type(aisle_lane_indices) is not tuple
                or len(aisle_lane_indices) != len(parking_aisles)
                or any(type(i) is not int or i not in (0, 1) for i in aisle_lane_indices)):
            raise ValueError('explicit aisle-to-common-lane roles required')
        if crossing is not None and type(crossing) is not bytes and not callable(crossing):
            raise ValueError('explicit crossing bytes/policy required')
        bridge = ParkingBridge(context, clock_id=clock_id, map_frame_id=map_frame_id,
            body=body, method_manifest_sha256=method_manifest_sha256, input_kind=input_kind,
            calibration_profile=calibration_profile, profile=parking_profile, association_profile=association_profile)
        lane = LaneCatalog(context, clock_id=clock_id, calibration_id=bridge.local_calibration_id, frame_id=map_frame_id)
        self.mailbox = mailbox; self._thread = threading.get_ident(); self._revision = 0
        basis=RoadBasis()
        self._accepted = MappingState(lane, bridge, EvidenceTable(), CatalogView('UNAVAILABLE', None, None,road_basis=basis.snapshot()),basis)
        self._staged = self._pending = self._transition = self._transition_state = None
        self._queued = self._head = self._delivery = self._terminal = None
        self._submitted = False
        self._crossing, self._lane_profile = crossing, lane_profile
        self._aisles, self._roles = parking_aisles, aisle_lane_indices

    def _owner(self):
        if threading.get_ident() != self._thread: raise RuntimeError('catalogs belong to the original compute owner')

    @property
    def context(self): return self._accepted.lane.context

    @property
    def view(self): return self._accepted.view

    @property
    def pending(self): return self._pending

    @property
    def basis_snapshot(self):
        """마지막 채택 frame의 gated 참조다. 현재 age/World/scene 사용권은 caller가 별도 검산한다."""
        return self._accepted.view.road_basis

    @staticmethod
    def metric_from_frame(frame):
        """이미 수락된 실제 FrameEvidence의 원 값만 옮긴다. 새 clock/부모/ID는 만들지 않는다."""
        from .map_checkpoint import FrameEvidence
        if type(frame) is not FrameEvidence or frame.source_kind != 'CAMERA':
            raise ValueError('actual camera FrameEvidence required')
        stamps = (frame.observation.rgb.stamp,) + (() if frame.observation.depth is None else (frame.observation.depth.stamp,))
        mask = 1 | (2 if frame.observation.depth is not None else 0)
        for parent in frame.parents: mask |= parent.source_mask
        return MetricEvidence(tuple(s.key for s in stamps), frame.scope.context.pi_host_boot,
            frame.pi_interval, frame.observation_sequence, mask, frame.raw_keys,
            tuple(parent.provenance_id for parent in frame.parents))

    def prepare(self, original_job, result, *, context, stamp, capture_prior, registration_revision,
            prior_qualified, metric_evidence, lane_provenance_ids, base_evidence, now_us, roi_bounds):
        self._owner(); uint(now_us)
        if self._staged is not None or self._transition is not None:
            raise CatalogNotReady('original draft/publication transition is unresolved')
        if (type(original_job) is not MapperInput or type(result) is not PerceptionResult
                or type(stamp) is not FrameStamp or type(context) is not StorageContext
                or context != self.context or now_us >= original_job.expires_us
                or original_job.sample.rgb.sequence != stamp.sequence):
            raise ValueError('original current mapper job/source/context required')
        raw_context = json_bytes(original_job.context_bytes, 65536)
        if raw_context.get('context_sha256') != context.sha256 or raw_context.get('storage_context') != context.fields:
            raise ValueError('catalog context differs from original mapper job bytes')
        summary, snapshot = result.summary, result.map_snapshot
        if (type(summary) is not dict or type(snapshot) is not dict or type(prior_qualified) is not bool
                or tuple(summary.get('rgb_source_key', ())) != stamp.key or summary.get('rgb_captured_us') != stamp.captured_us
                or stamp.clock_id != self._accepted.lane.clock_id or stamp.available_us > now_us
                or summary.get('calibration_id') != self._accepted.lane.calibration_id
                or summary.get('kind') != self._accepted.parking._kind
                or not stamp.available_us <= summary.get('now_us', -1) <= now_us):
            raise ValueError('original perception/capture/map source changed')
        uint(registration_revision)
        if type(capture_prior) is PoseEstimate: capture_prior = capture_prior.prior
        if type(capture_prior) is not PoseBox: raise ValueError('actual joint capture prior required')
        state = self._accepted.fork(); reasons = []; lane_model = None; parking_publication = None
        if summary.get('quarantined') is True:
            state = replace(state, view=CatalogView('UNKNOWN', None, None, ('MAP_QUARANTINED',),
                _waiting_basis(state,'MAP_QUARANTINED')))
            self._staged = CatalogDraft(original_job, context, self._revision, state)
            return self._staged
        if state.road_basis_material is not None:
            # 부분 LOAD의 원 lane/basis 조합도 함께 고정한다. 새 lane만 바꿔 옛 prefix를 손상시키지 않는다.
            state=replace(state,view=CatalogView('UNKNOWN',None,None,('ROAD_BASIS_COMPLETE_REQUIRED',),None))
            self._staged=CatalogDraft(original_job,context,self._revision,state)
            return self._staged
        if (snapshot.get('frame_id') != state.lane.frame_id or snapshot.get('calibration_id') != state.lane.calibration_id
                or snapshot.get('clock_id') != state.lane.clock_id or snapshot.get('kind') != state.parking._kind):
            raise ValueError('original metric map domain changed')
        if type(metric_evidence) is not MetricEvidence or stamp.key not in metric_evidence.frame_keys or metric_evidence.pi_host_boot != context.pi_host_boot:
            raise ValueError('original metric capture/Pi evidence required')
        merged = _merge_evidence(state.evidence, base_evidence)
        parent_roots = set(metric_evidence.parent_ids)
        if type(lane_provenance_ids) is not tuple or len(lane_provenance_ids) != 2:
            raise ValueError('two actual collector lane provenance references required')
        for identity in lane_provenance_ids:
            uint(identity, positive=True); parent_roots.add(identity)
        checked = _closure(merged, parent_roots, context)
        by_id = {r.provenance_id: r for r in checked.provenance}
        for identity in lane_provenance_ids:
            record = by_id[identity]
            if (not record.source_mask & 1 or record.observation_sequence != metric_evidence.observation_sequence
                    or record.pi_lower > metric_evidence.pi_interval[0] or record.pi_upper < metric_evidence.pi_interval[1]):
                raise ValueError('lane provenance is not the original full RGB capture')
        if self._crossing is None:
            lane_status = 'UNCONFIGURED'; reasons.append('CROSSING_POLICY_UNCONFIGURED')
        elif result.road is None:
            lane_status = 'UNKNOWN'; reasons.append('ROAD_UNAVAILABLE')
        else:
            crossing = self._crossing(result.road, stamp) if callable(self._crossing) else self._crossing
            source = LaneSource(stamp, state.lane.calibration_id, context.map_epoch, state.lane.frame_id,
                context.odom_epoch, result.map_snapshot['revision'], registration_revision,
                result.summary.get('geometric_model_qualified') is True and prior_qualified
                and (state.parking.calibration_profile is None or state.parking.calibration_profile.metric_qualified),
                state.parking._kind)
            proof = state.lane.observe(result.road, source, capture_prior,
                prior_qualified=prior_qualified, crossing=crossing, profile=self._lane_profile)
            lane_status = 'AMBIGUOUS' if result.road.complete and result.road.status == 'AMBIGUOUS' else proof.status
            lane_model = state.lane.update(proof, provenance_ids=lane_provenance_ids)
            if proof.status == 'NEW' and lane_model is None: reasons.append('WAIT_COMMITTED_LANE_RANGE')
        if lane_model is None or stamp.captured_us is None:
            basis_snapshot=_waiting_basis(state)
        else:
            spec=snapshot.get('spec')
            if type(spec) is not GridSpec:raise ValueError('actual map snapshot grid resolution required for road basis')
            basis_snapshot=state.road_basis.observe(state.lane,lane_model,current_stamp=stamp,resolution_m=spec.resolution_m,
                now_us=now_us,max_age_us=original_job.expires_us-stamp.captured_us[0])
        if lane_model is not None and self._aisles and self._roles is not None:
            aisles = []
            for aisle, index in zip(self._aisles, self._roles):
                identity = lane_model.lane_ids[index]
                if aisle.approach_lane_id not in (0, identity): raise ValueError('declared aisle names another stable lane')
                aisles.append(replace(aisle, approach_lane_id=identity))
            update = state.parking.process(result, stamp=stamp, capture_prior=capture_prior,
                aisles=tuple(aisles), registration_revision=registration_revision, prior_qualified=prior_qualified,
                now_us=now_us, metric_evidence=metric_evidence)
            reasons.extend(update.reasons)
            if state.parking.catalog.revision:
                parking_publication = state.parking.publication_candidates(now_us=now_us, roi_bounds=roi_bounds)
        elif self._aisles:
            reasons.append('WAIT_CURRENT_LANE_IDS' if self._roles is not None else 'PARKING_LANE_ROLES_UNCONFIGURED')
            if state.parking.catalog.tracks:
                state.parking.refresh_occupancy(result.map_snapshot, snapshot_us=result.summary['now_us'],
                    now_us=now_us, metric_evidence=metric_evidence)
        # 오래된 source의 부모도 보존하되 catalog/현재 관측에 닿지 않는 metadata는 남기지 않는다.
        roots = set(state.lane.originals()) | parent_roots
        roots.update(_basis_roots(state))
        roots.update(t.provenance_id for t in state.parking.catalog.tracks)
        for links in state.parking._links.values(): roots.update(links)
        combined = _merge_evidence(merged, EvidenceTable(tuple(state.parking._records.values()),
            tuple(state.parking._contexts.values())))
        retained = _closure(combined, roots, context)
        view = CatalogView(lane_status, lane_model, parking_publication, tuple(dict.fromkeys(reasons)),basis_snapshot)
        state = replace(state,evidence=retained,view=view)
        self._staged = CatalogDraft(original_job, context, self._revision, state)
        return self._staged

    def accept(self, draft):
        """condition lock 안의 cheap 참조 교환. 현재성/원 expiry 최종 판정은 compute가 이미 했다."""
        if draft is not self._staged or draft.accepted_revision != self._revision:
            raise ValueError('not the original staged catalog draft')
        self._accepted = draft.state; self._revision += 1; self._staged = None

    def discard(self, draft):
        self._owner()
        if draft is not self._staged: raise ValueError('not the original staged catalog draft')
        self._staged = None

    def observe_heads(self, head, job, *, now_us):
        self._owner()
        if self._staged is not None or self._transition is not None: raise CatalogNotReady('draft/context transition active')
        self._accepted.parking.observe_heads(head, job, now_us=now_us)
        self._head = head, job

    def queue_load(self, request):
        self._owner()
        if type(request) is not LoadRequest or request.job.context != self.context:
            raise ValueError('original current LOAD request required')
        if self._pending is not None or self.mailbox.main_peek() is not None: return False
        if self._queued is not None and self._queued.request is not request: return False
        self._queued = CatalogPending('LOAD', request)
        return True

    def _consume(self, delivery, now_us):
        pending = self._pending
        if pending is None or delivery.request is not pending.request: return False
        self._delivery = delivery
        state = self._transition_state or self._accepted
        result = delivery.admitted_result; request = pending.request
        if type(delivery.result) is StorageAbort:
            if pending.role == 'LANE_RANGE': state.lane.cancel_allocation(pending.need)
            elif pending.role in ('SLOT_RANGE', 'PARKING_PROVENANCE', 'CHECKPOINT'):
                state.parking.retire_storage_abort(request, delivery.result, now_us=now_us)
        elif pending.role == 'LANE_RANGE':
            if type(result) is not CommitReceipt or result.status in ('UNKNOWN', 'ABSENT'): return True
            if delivery.current and self._transition is None:
                state.lane.apply_committed_range(pending.need, request.job, request.payload, result,
                    current_context_sha256=self.context.sha256, now_us=now_us)
                known = state.parking._head_values
                if known is not None:
                    # 실제 COMMITTED 예약의 끝은 header의 내구 하한이다. 새 head/job
                    # 증명을 만들거나 다음 CAS 예약의 head로 재포장하지 않는다.
                    values = max(known[0], pending.need.reservation.end_exclusive), known[1]
                    state.parking._head_values = state.parking._head_floor = values
            else: state.lane.retire_allocation(pending.need, request.job, request.payload, result)
        elif pending.role == 'SLOT_RANGE':
            if (not delivery.current and self._transition is None and type(result) is CommitReceipt
                    and result.current(request.job, request.payload, self.context.sha256, now_us)):
                self._delivery = delivery; return True
            state.parking.accept_range(request, result, now_us=now_us)
            if type(result) is not CommitReceipt or result.status in ('UNKNOWN', 'ABSENT'): return True
        elif pending.role == 'PARKING_PROVENANCE':
            if delivery.current and self._transition is None:
                if type(result) is ProvenanceRange: state.parking.accept_provenance(request, result, now_us=now_us)
                else: state.parking.accept_wire_grant(request, result, now_us=now_us)
            elif type(result) is ProvenanceRange:
                if state.parking.catalog.publication_transition is None: return True
                state.parking.accept_provenance(request, result, now_us=now_us)
            elif type(result) is WireProvenanceGrant:
                if state.parking.catalog.publication_transition is None: return True
                state.parking.accept_wire_grant(request, result, now_us=now_us)
            else:
                if delivery.result.expires_us <= now_us:
                    self._delivery = delivery; return True
                if not state.parking.retire_provenance_wire(request, delivery.result, now_us=now_us): return True
        elif pending.role == 'CHECKPOINT':
            if (not delivery.current and self._transition is None and type(result) is CommitReceipt
                    and result.current(request.job, request.payload, self.context.sha256, now_us)):
                self._delivery = delivery; return True
            state.parking.accept_checkpoint(request, result, now_us=now_us)
            if type(result) is not CommitReceipt or result.status in ('UNKNOWN', 'ABSENT'): return True
        elif pending.role == 'LOAD':
            if delivery.current and self._transition is None and type(result) is LoadedCheckpoint:
                self.observe_heads(result.heads, request.job, now_us=now_us)
        self._delivery = delivery; self._pending = None; self._submitted = False
        self._head = None if pending.role.endswith('_RANGE') else self._head
        return True

    def _unoffered_terminal(self, pending, reason, now_us):
        state = self._transition_state or self._accepted
        if pending.role == 'LANE_RANGE': state.lane.cancel_allocation(pending.need)
        elif pending.role in ('SLOT_RANGE', 'PARKING_PROVENANCE', 'CHECKPOINT'):
            state.parking.retire_storage_abort(pending.request, StorageAbort(reason), now_us=now_us)
        self._terminal = CatalogTerminal(pending.request, reason, now_us)

    def maintenance(self, *, now_us, head=None, head_job=None, reservation_nonce=None,
            provenance_job=None, provenance_nonce=None):
        """최종 채택 뒤 원 요청만 직렬 offer한다. 반환값은 작은 상태이며 D 성공은 아니다."""
        self._owner(); uint(now_us)
        if self._staged is not None: raise CatalogNotReady('geometry draft is not terminal')
        current_sha = self.context.sha256 if self._transition is None else self._transition.sha256
        self.mailbox.compute_encode_pending(current_context_sha256=current_sha, now_us=now_us)
        if self._pending is not None:
            if not self._submitted and now_us >= self._pending.request.job.expires_us:
                self._unoffered_terminal(self._pending, 'EXPIRED', now_us)
                self._pending = None
                return 'UNSTARTED_EXPIRED'
            peek = self.mailbox.main_peek()
            if peek is not None and peek.request is self._pending.request:
                delivery = self.mailbox.compute_take(current_context_sha256=current_sha, now_us=now_us)
                if delivery is not None: self._consume(delivery, now_us)
            if self._pending is not None:
                self._submitted |= self.mailbox.compute_offer(self._pending.request, now_us=now_us)
                return 'WAIT_ORIGINAL_D_JOB'
        if self._transition is not None:
            return 'WAIT_CONTEXT' if not self._finish_transition(now_us) else 'CONTEXT_CHANGED'
        if head is not None: self.observe_heads(head, head_job, now_us=now_us)
        if self.mailbox.main_peek() is not None: return 'WAIT_OTHER_D_JOB'
        state = self._accepted
        if self._queued is not None:
            self._pending, self._queued = self._queued, None
        elif state.lane._observed is not None and state.lane._observed.status == 'NEW' and state.lane.available_ids < 2:
            if self._head is None or reservation_nonce is None: return 'NEEDS_CURRENT_HEAD'
            heads, job = self._head
            need = state.lane.allocation_need(state.lane._observed, heads, job,
                reservation_nonce=reservation_nonce, now_us=now_us)
            evidence = EvidenceTable(reservations=(need.reservation,),
                catalog_highwaters=(need.reservation.end_exclusive, heads.heads[1][1]))
            self._pending = CatalogPending('LANE_RANGE', CheckpointRequest(job,
                CheckpointPayload.build(job, evidence=evidence, intent='CATALOG_RESERVATION')), need)
        elif (state.parking.last is not None and state.parking.last.allocation_wait
                and len(state.parking.last.association.births) > state.parking.catalog.available_ids):
            if self._head is None or reservation_nonce is None: return 'NEEDS_CURRENT_HEAD'
            heads, job = self._head
            request = state.parking.prepare_range(heads, job, reservation_nonce, now_us=now_us)
            if request is not None: self._pending = CatalogPending('SLOT_RANGE', request)
        elif (state.parking.last is not None and state.parking.last.provenance_wait
                and (state.parking._provenance is None
                     or state.parking._next_provenance >= state.parking._provenance.end_exclusive)):
            if provenance_job is None or provenance_nonce is None: return 'NEEDS_EXCLUSIVE_PARKING_PROVENANCE'
            request = state.parking.prepare_provenance(provenance_job, provenance_nonce)
            if request is not None: self._pending = CatalogPending('PARKING_PROVENANCE', request)
        if self._pending is None: return 'IDLE'
        offered = self.mailbox.compute_offer(self._pending.request, now_us=now_us)
        self._submitted = offered
        return 'OFFERED' if offered else 'WAIT_MAILBOX'

    def take_delivery(self):
        self._owner(); result, self._delivery = self._delivery, None
        return result

    def take_terminal(self):
        self._owner(); result, self._terminal = self._terminal, None
        return result

    def replace_publication_context(self, context, *, now_us):
        self._owner()
        if self._staged is not None: raise CatalogNotReady('original geometry draft unresolved')
        if context == self.context and self._transition is None: return True
        state = self._accepted.fork() if self._transition_state is None else self._transition_state
        lane = state.lane.replace_publication_context(context, now_us=now_us)
        parking = state.parking.replace_publication_context(context, now_us=now_us)
        self._transition, self._transition_state = context, state
        self.mailbox.fence(context.sha256, now_us=now_us)
        self._head = None
        if self._queued is not None:
            self._unoffered_terminal(self._queued, 'SOURCE_REPLACED', now_us); self._queued = None
        if self._pending is not None and not self._submitted:
            self._unoffered_terminal(self._pending, 'SOURCE_REPLACED', now_us); self._pending = None
        return self._finish_transition(now_us) if lane and parking and self._pending is None else False

    def _finish_transition(self, now_us):
        state = self._transition_state; context = self._transition
        if self._pending is not None: return False
        if not state.lane.replace_publication_context(context, now_us=now_us): return False
        if not state.parking.replace_publication_context(context, now_us=now_us): return False
        self._accepted = replace(state, view=CatalogView('WAIT_CURRENT_OBSERVATION', None, None,
            road_basis=_waiting_basis(state,'PUBLICATION_CHANGED')))
        self._transition = self._transition_state = None; self._revision += 1
        return True

    def prepare_checkpoint(self, job, *, base_evidence, now_us, tiles=(), tile_references=None,
            durable_cutoff_pi_us, scene=None):
        """최종 채택 뒤 실제 collector 사본을 합친 원 CP다. root는 이 최종 request를 track한다."""
        self._owner()
        if (self._staged is not None or self._pending is not None or self._transition is not None
                or self._queued is not None or self.mailbox.main_peek() is not None):
            raise CatalogNotReady('one original catalog/storage operation is unresolved')
        state = self._accepted.fork()
        merged = _merge_evidence(state.evidence, base_evidence)
        if state.parking._head_values is None: raise CatalogNotReady('actual current catalog heads required')
        evidence = EvidenceTable(merged.provenance, merged.contexts, state.lane.checkpoint_records(),
            catalog_highwaters=state.parking._head_values)
        request = state.parking.build_checkpoint(job, now_us=now_us, tiles=tiles, tile_references=tile_references,
            base_evidence=evidence, durable_cutoff_pi_us=durable_cutoff_pi_us, scene=scene)
        self._accepted = state; self._revision += 1
        self._queued = CatalogPending('CHECKPOINT', request)
        return request

    def cancel_unoffered_checkpoint(self,request,*,now_us):
        """아직 mailbox에 내보내지 않은 원 CP만 철회한다. ID/원 version을 되돌리지 않는다."""
        self._owner();uint(now_us)
        queued=self._queued
        if (queued is None or queued.role!='CHECKPOINT' or queued.request is not request
                or self._pending is not None or self._submitted or self.mailbox.main_peek() is not None):
            raise CatalogNotReady('exact unpublished checkpoint and idle mailbox required')
        if not self._accepted.parking.retire_storage_abort(request,StorageAbort('CANCELLED'),now_us=now_us):
            raise CatalogNotReady('original unpublished parking checkpoint could not retire')
        self._queued=None;self._terminal=CatalogTerminal(request,'UNPUBLISHED_CANCELLED',now_us)

    def state_fields(self):
        self._owner()
        state=self._accepted
        return {'lane_catalog':state.lane.state_fields(),'road_basis':state.road_basis.state_fields()
            if state.road_basis_material is None else json_bytes(state.road_basis_material,512*1024)}

    def restore_loaded(self, loaded, *, now_us):
        """현재 LOAD의 catalog만 복원한다. map 전체 COMPLETE/scene/현재 관측 승인은 별도다."""
        self._owner()
        if (self._staged is not None or self._pending is not None or self._queued is not None
                or self._transition is not None or self._accepted.lane.revision or self._accepted.parking.catalog.revision
                or type(loaded) is not LoadedCheckpoint or not loaded.current(loaded.request, self.context.sha256, now_us)):
            raise CatalogNotReady('fresh catalog owner and actual current decoded LOAD required')
        state = self._accepted.fork()
        if any(r.kind == 'LANE' for r in loaded.payload.evidence.catalog):
            state.lane.restore(loaded, now_us=now_us)
        state.parking.restore(loaded, now_us=now_us)
        metadata,_=decode_mapper_state(json_bytes(loaded.payload.manifest,16384)['scene'],loaded.payload.evidence.contexts)
        material=None
        if metadata is not None and 'road_basis' in metadata:
            material=canonical(metadata['road_basis'])
            roots=mapper_state_roots({'road_basis':json_bytes(material,512*1024)})
            _closure(loaded.payload.evidence,roots,self.context)
        state=replace(state,road_basis_material=material)
        evidence = _merge_evidence(loaded.payload.evidence)
        self._accepted = replace(state,evidence=evidence,view=CatalogView('WAIT_CURRENT_OBSERVATION',None,None,
            () if material is None else ('ROAD_BASIS_COMPLETE_REQUIRED',),
            road_basis=_waiting_basis(state,'ROAD_BASIS_COMPLETE_REQUIRED' if material is not None else 'CURRENT_LANE_REQUIRED')))
        self._head = loaded.heads, loaded.request.job
        self._revision += 1

    def restore_complete(self,complete,*,current_scope,now_us):
        """완성 BOOTSTRAP의 역사 scope/catalog를 연결한다. 이후 FrameEvidence용 scope를 반환한다."""
        self._owner()
        if (self._staged is not None or self._pending is not None or self._queued is not None
                or self._transition is not None or self._accepted.lane.revision or self._accepted.parking.catalog.revision):
            raise CatalogNotReady('fresh catalog owner required for complete bootstrap restore')
        from .scene import SceneEvidenceScope
        if type(current_scope) is not SceneEvidenceScope or current_scope.context!=self.context:
            raise CatalogNotReady('actual current publication source scope required')
        scope=current_scope.with_complete_history(complete,now_us=now_us)
        state=self._accepted.fork()
        if any(r.kind=='LANE' for r in complete.evidence.catalog):
            state.lane.restore_complete(complete,current_scope=scope,now_us=now_us)
        state.parking.restore_complete(complete,current_scope=scope,now_us=now_us)
        metadata=None if complete.state is None else json_bytes(complete.state,512*1024)
        if metadata is not None and 'road_basis' in metadata:
            state.road_basis.restore(complete,state.lane,evidence_scope=scope,now_us=now_us)
        self._accepted=replace(state,evidence=_merge_evidence(complete.evidence),road_basis_material=None,
            view=CatalogView('WAIT_CURRENT_OBSERVATION',None,None,
                road_basis=_waiting_basis(state,'RESTORED_REOBSERVATION_REQUIRED')))
        self._head=None;self._revision+=1
        return scope
