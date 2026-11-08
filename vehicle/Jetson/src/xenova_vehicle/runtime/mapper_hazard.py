"""고정 좌표 grid의 실제 occupied 변화만 세는 단일 compute owner.

snapshot 만료/UNKNOWN은 장애물 제거가 아니다. 새 source의 실제 RAW FREE만 제거할
수 있다. 후보의 revoke_required는 caller가 즉시 Scene.revoke에 전달해야 한다.
이 모듈은 시각·scene 권한을 만들지 않으며 atlas/rolling ROI 복원은 지원하지 않는다.
"""

from dataclasses import dataclass, replace
from threading import get_ident

from .mapper_mapping import MappingFrameDraft
from .map_checkpoint import FrameEvidence, SourceComplete
from ..mapping.local_map import EvidenceGrid, GridSpec, OCCUPIED, FREE
from ..perception.pipeline import PerceptionResult
from ..storage.contracts import uint
from ..storage.geometry_scope import CatalogGeometryScope


@dataclass(frozen=True, slots=True)
class HazardSnapshot:
    """원 occupied bitset과 source HWM. revision은 grid revision/현재성/권한이 아니다."""
    revision: int
    geometry: tuple
    spec: GridSpec
    occupied: bytes
    sources: tuple


@dataclass(frozen=True, slots=True)
class HazardDraft:
    original: object
    ready: bool
    reason: str
    snapshot: HazardSnapshot | None
    changed: bool
    source_advanced: bool
    revoke_required: bool
    witness: tuple | None = None

    @property
    def revision(self):
        return None if self.snapshot is None else self.snapshot.revision


class MapperHazard:
    """prepare/accept/cancel만 소유한다. 취소해도 이미 필요한 scene 철회는 되돌리지 않는다."""
    def __init__(self, *, complete=None):
        if complete is not None and type(complete) is not SourceComplete:
            raise ValueError('actual complete source or explicit cold source required')
        self._thread = get_ident()
        self._accepted = self._staged = None
        self._complete = complete
        self._revision_floor = 0
        self._visible = None

    def _owner(self):
        if get_ident() != self._thread: raise RuntimeError('hazard belongs to the original compute owner')

    @property
    def snapshot(self):
        self._owner()
        return self._visible if self._visible is not None else self._accepted

    def require_restore(self, complete):
        """실제 완성 source가 있는 새 M은 EMPTY=1로 우회하지 않는다. 현재 복원은 NOT_READY다."""
        self._owner()
        if type(complete) is not SourceComplete or self._staged is not None or self._accepted is not None:
            raise ValueError('fresh hazard owner and actual original complete source required')
        if self._complete is not None and self._complete is not complete:
            raise ValueError('original complete source changed')
        self._complete = complete

    def restore_complete(self, complete):
        """원 CP hazard/selected tiles→fixed RAW state 대응 검산 전에는 역사 baseline을 발급하지 않는다."""
        self.require_restore(complete)
        return self._reject(complete, 'RESTORE_NOT_READY')

    def _reject(self, original, reason):
        candidate = HazardDraft(original, False, reason, self.snapshot, False, False, True)
        self._staged = candidate
        return candidate

    def prepare(self, mapping, *, now_us):
        """원 실제 처리시각을 받는다. unsafe 후보는 반드시 caller가 즉시 scene 철회해야 한다."""
        self._owner()
        if self._staged is not None: raise ValueError('one original hazard candidate is unresolved')
        if self._complete is not None: return self._reject(mapping, 'RESTORE_NOT_READY')
        try:
            uint(now_us)
            if (type(mapping) is not MappingFrameDraft or not mapping.ready or type(mapping.result) is not PerceptionResult
                    or type(mapping.frame) is not FrameEvidence or not mapping.original_job.fresh(now_us)):
                return self._reject(mapping, 'MAPPING_NOT_READY')
            result, frame, pipeline = mapping.result, mapping.frame, mapping.pipeline
            grid = pipeline.grid
            if (type(grid) is not EvidenceGrid or grid.kind != 'LIVE' or frame.observation.kind != 'LIVE'
                    or frame.scope.context != mapping.context or frame.scope.calibration != pipeline.calibration
                    or mapping.original_job.source_generation != frame.scope.raw_binding.sha256
                    or result.summary.get('kind') != 'LIVE' or result.summary.get('quarantined') is not False
                    or not frame.accepted_us <= result.summary['now_us'] <= now_us
                    or result.map_snapshot['revision'] != grid.revision or result.map_snapshot['spec'] != grid.spec
                    or result.map_snapshot['frame_id'] != grid.frame_id or result.map_snapshot['kind'] != grid.kind
                    or result.map_snapshot['clock_id'] != grid.clock_id or result.map_snapshot['calibration_id'] != grid.calibration_id
                    or result.summary.get('rgb_source_key') != frame.observation.rgb.stamp.key
                    or grid.spec.width*grid.spec.height > 4096):
                return self._reject(mapping, 'GRID_NOT_READY')
            raw = frame.scope.raw_binding.fields
            geometry = (CatalogGeometryScope.from_context(mapping.context, 'SLOT').bytes,
                frame.scope.method_manifest_sha256, grid.frame_id, grid.calibration_id, grid.clock_id, grid.kind, grid.spec)
            if (grid.frame_id != 'odom:'+mapping.context.odom_epoch or grid.calibration_id != pipeline.calibration.sha256
                    or grid.clock_id != raw['clock_epoch']):
                return self._reject(mapping, 'GEOMETRY_NOT_READY')
            previous = self.snapshot
            if previous is not None and previous.geometry != geometry:
                return self._reject(mapping, 'GEOMETRY_CHANGED')
            stamps = (frame.observation.rgb.stamp, frame.observation.depth.stamp)
            flags = (result.summary.get('rgb_updated'), result.summary.get('depth_updated'))
            gates = (pipeline.rgb_gate, pipeline.depth_gate)
            sources = []; advanced = []
            for i, (stamp, flag, gate, name) in enumerate(zip(stamps, flags, gates, ('rgb_video', 'depth_right'))):
                if (type(flag) is not bool or stamp.captured_us is None or stamp.available_us > now_us
                        or stamp.key[:3] != (raw['acquisition_worker'], raw['stream_instances'][name], raw['clock_epoch'])):
                    return self._reject(mapping, 'SOURCE_NOT_READY')
                head = gate._heads.get(stamp.key[:3])
                if head is None or head[0] < stamp.sequence or (head[0] == stamp.sequence and head[2] != stamp.captured_us):
                    return self._reject(mapping, 'SOURCE_GATE_NOT_READY')
                old = None if previous is None else previous.sources[i]
                if old is not None and old.key == stamp.key and old != stamp:
                    return self._reject(mapping, 'SOURCE_CONFLICT')
                is_new = flag and (old is None or stamp.sequence > old.sequence)
                if flag and old is not None and stamp.sequence < old.sequence:
                    return self._reject(mapping, 'SOURCE_REGRESSED')
                if is_new and head[0] != stamp.sequence:
                    return self._reject(mapping, 'SOURCE_GATE_NOT_READY')
                if old is None and not is_new:
                    return self._reject(mapping, 'NO_INITIAL_SOURCE_PROGRESS')
                advanced.append(is_new); sources.append(stamp if is_new else old)
            import numpy as np
            state = grid.state
            if (type(state) is not np.ndarray or state.dtype != np.dtype('u1')
                    or state.shape != (grid.spec.height, grid.spec.width) or bool(np.any(state > OCCUPIED))):
                return self._reject(mapping, 'RAW_STATE_NOT_READY')
            old_bits = (np.zeros(state.size, dtype=bool) if previous is None else
                np.unpackbits(np.frombuffer(previous.occupied, dtype=np.uint8), bitorder='little', count=state.size).astype(bool))
            flat = state.reshape(-1)
            fresh_times = tuple(stamp.captured_us[0] for stamp, fresh in zip(stamps, advanced) if fresh)
            # RAW FREE의 원 source 시각이 실제 새 관측이어야 한다. 시간 경과/UNKNOWN은 남긴다.
            clear = ((flat == FREE) & grid.time_known.reshape(-1)
                & np.isin(grid.observed_us.reshape(-1), np.asarray(fresh_times, dtype=np.uint64))) if fresh_times else np.zeros(state.size, dtype=bool)
            bits = (old_bits & ~clear) | (flat == OCCUPIED)
            changed = not bool(np.array_equal(bits, old_bits))
            progressed = any(advanced)
            if changed and not progressed: return self._reject(mapping, 'RAW_CHANGED_WITHOUT_SOURCE_PROGRESS')
            if previous is not None and not progressed:
                candidate = HazardDraft(mapping, True, 'NO_SOURCE_PROGRESS', previous, False, False, False)
            else:
                revision = max(self._revision_floor, 1 if previous is None else previous.revision) + int(changed)
                uint(revision, positive=True)
                snapshot = HazardSnapshot(revision, geometry, grid.spec,
                    np.packbits(bits, bitorder='little').tobytes(), tuple(sources))
                witness = (mapping.original_job.job_id, *stamps)
                candidate = HazardDraft(mapping, True, 'CHANGED' if changed else 'UNCHANGED', snapshot,
                    changed, progressed, changed, witness)
                # Scene가 이미 본 철회 사건 번호는 취소해도 소모한다. 채택한 occupied/source는
                # 그대로 두고 계산 단계에서 counter만 올린 사본을 만들어 최종 cancel은 참조만 지운다.
                self._revision_floor = revision
                if self._accepted is not None and self._accepted.revision < revision:
                    self._visible = replace(self._accepted, revision=revision)
            self._staged = candidate
            return candidate
        except (ValueError, KeyError, AttributeError):
            return self._reject(mapping, 'INPUT_NOT_READY')

    def accept(self, candidate):
        """최종 원 mapping 채택 뒤 호출한다. NOT_READY는 원 baseline을 바꾸지 않는다."""
        self._owner()
        if candidate is not self._staged: raise ValueError('exact original hazard candidate required')
        self._staged = None
        if not candidate.ready: return False
        self._accepted = candidate.snapshot
        self._visible = None
        return True

    def cancel(self, candidate):
        self._owner()
        if candidate is not self._staged: raise ValueError('exact original hazard candidate required')
        self._staged = None
        return candidate.revoke_required
