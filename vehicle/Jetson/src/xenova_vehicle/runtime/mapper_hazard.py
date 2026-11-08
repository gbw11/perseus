"""고정 좌표 grid의 실제 occupied 변화만 세는 단일 compute owner.

snapshot 만료/UNKNOWN은 장애물 제거가 아니다. 새 source의 실제 RAW FREE만 제거할
수 있다. 후보의 revoke_required는 caller가 즉시 Scene.revoke에 전달해야 한다.
rolling에서는 창 밖 occupied도 전역 bitset에 남긴다. 시각·scene 권한이나 역사적
atlas 복원 승인을 만들지 않는다.
"""

from base64 import b64encode
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from threading import get_ident

from .mapper_mapping import MappingFrameDraft
from .map_checkpoint import FrameEvidence, SourceComplete
from ..mapping.local_map import EvidenceGrid, GridSpec, OCCUPIED, FREE
from ..mapping.map_lattice import GridWindow, MapLattice, read_atlas_window
from ..perception.pipeline import PerceptionResult
from ..storage.contracts import StorageContext, canonical, json_bytes, uint, digest
from ..storage.geometry_scope import CatalogGeometryScope
from ..storage.mapper_state import validate_hazard_state


@dataclass(frozen=True, slots=True)
class HazardSnapshot:
    """원 occupied bitset과 source HWM. revision은 grid revision/현재성/권한이 아니다."""
    revision: int
    geometry: tuple
    spec: GridSpec
    occupied: bytes
    sources: tuple
    global_occupied: tuple = ()
    window: GridWindow | None = None

    def __post_init__(self):
        if self.window is None:
            if self.global_occupied != (): raise ValueError('fixed hazard cannot carry rolling tile bits')
            return
        if (type(self.window) is not GridWindow or self.spec != self.window.spec
                or type(self.global_occupied) is not tuple or len(self.global_occupied) > 128):
            raise ValueError('bounded original rolling hazard window required')
        seen = set()
        for entry in self.global_occupied:
            if type(entry) is not tuple or len(entry) != 2: raise ValueError('original global tile/bitset pair required')
            key, body = entry
            if (type(key) is not tuple or len(key) != 2 or any(type(v) is not int or not -(1<<31) <= v < 1<<31 for v in key)
                    or key in seen or type(body) is not bytes or len(body) != 512):
                raise ValueError('unique int32 tile key and immutable512-byte bitset required')
            seen.add(key)


def _rolling_clear(mapping, grid, window, clear, np):
    """같은 J의 다른 관측 FREE를 새 제거로 쓰지 않는다. 원 CELL/provenance가 없으면 유지한다."""
    from .atlas_proof import AtlasProofWindow
    from .map_checkpoint import MapCheckpointCollector
    from ..storage.records import CELL, PROVENANCE, ProvenanceRecord
    result = np.zeros(clear.shape, dtype=bool)
    if not bool(clear.any()): return result
    collector = mapping.collector
    proof = None if collector is None else getattr(collector, '_atlas_proof', None)
    if (type(collector) is not MapCheckpointCollector or type(proof) is not AtlasProofWindow
            or proof.window != window or collector.scope.context != mapping.context):
        return result
    frame = mapping.frame
    expected_context = frame.scope.context_record('METRIC').sha256
    accepted = {}
    for index in np.flatnonzero(clear).tolist():
        x, y = window.local_to_global(index)
        values = CELL.unpack(proof.read(x, y))
        if (proof.is_unresolved(x, y) or values[0] != 0 or values[1] != 1 or not values[2] & 2 or not values[6]
                or values[7:10] != (*frame.pi_interval, frame.observation_sequence)):
            continue
        identity = values[6]
        record = collector._records.get(identity)
        if identity not in accepted:
            context = None if record is None else collector._contexts.get(record.context_sha256)
            valid = (type(record) is ProvenanceRecord and record.provenance_id == identity and record.context_sha256 == expected_context
                and context is not None and record.observation_sequence == frame.observation_sequence
                and (record.pi_lower, record.pi_upper) == frame.pi_interval and PROVENANCE.unpack(record.payload)[2] == 2)
            if valid:
                try:
                    actual_mask, _ = frame.scope.record_sources(record, context)
                    valid = bool(actual_mask & 2)
                except ValueError: valid = False
            accepted[identity] = valid
        if accepted[identity] and values[2] & record.source_mask == values[2]: result[index] = True
    return result


def _rolling_bits(mapping, grid, window, previous, clear, np):
    """본문은 최대4개 현재 tile만 분리한다. 나머지 global bitset bytes는 그대로 공유한다."""
    stored = {} if previous is None else dict(previous.global_occupied)
    updates = {}; old_roi = np.zeros(grid.state.shape, dtype=bool)
    pieces = window.pieces()
    for piece in pieces:
        body = stored.get((piece.tile_x, piece.tile_y), bytes(512))
        bits = np.unpackbits(np.frombuffer(body, dtype=np.uint8), bitorder='little').reshape(64, 64)
        roi = (slice(piece.roi_row, piece.roi_row+piece.height), slice(piece.roi_col, piece.roi_col+piece.width))
        tile = (slice(piece.tile_row, piece.tile_row+piece.height), slice(piece.tile_col, piece.tile_col+piece.width))
        old_roi[roi] = bits[tile]
    clear = _rolling_clear(mapping, grid, window, clear & old_roi.reshape(-1), np).reshape(grid.state.shape)
    current = (old_roi & ~clear) | (grid.state == OCCUPIED)
    for piece in pieces:
        key = piece.tile_x, piece.tile_y
        old = stored.get(key, bytes(512))
        bits = np.unpackbits(np.frombuffer(old, dtype=np.uint8), bitorder='little').reshape(64, 64)
        roi = (slice(piece.roi_row, piece.roi_row+piece.height), slice(piece.roi_col, piece.roi_col+piece.width))
        tile = (slice(piece.tile_row, piece.tile_row+piece.height), slice(piece.tile_col, piece.tile_col+piece.width))
        if np.array_equal(bits[tile], current[roi]): continue
        bits[tile] = current[roi]
        updates[key] = np.packbits(bits.reshape(-1), bitorder='little').tobytes() if bool(bits.any()) else None
    keys = set(stored)
    for key, body in updates.items():
        if body is None: keys.discard(key)
        else: keys.add(key)
    if len(keys) > 128: raise ValueError('global occupied tile cap128 exceeded')
    for key, body in updates.items():
        if body is None: stored.pop(key, None)
        else: stored[key] = body
    return current.reshape(-1), tuple((key, stored[key]) for key in sorted(stored)), bool(updates)


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


@dataclass(frozen=True, slots=True)
class _RetainedHazard:
    """현재 관측이 없는 원 내구 baseline. old source/J/clock은 실행 상태에 넣지 않는다."""
    revision: int
    geometry_scope: bytes
    method_sha256: str
    frame_id: str
    calibration_id: str
    spec: GridSpec
    window: GridWindow | None
    tiles: tuple


def _fixed_tile(snapshot):
    body=bytearray(512)
    for index in range(snapshot.spec.width*snapshot.spec.height):
        if snapshot.occupied[index//8] & (1 << (index%8)):
            row,col=divmod(index,snapshot.spec.width);position=row*64+col
            body[position//8] |= 1 << (position%8)
    return (((0,0),bytes(body)),) if any(body) else ()


def _retained_snapshot(retained,grid,geometry):
    """원 bitset의 현재 창만 투영한다. source None은 내부 계산용이며 외부 snapshot이 아니다."""
    window=grid.rolling_window;stored=dict(retained.tiles)
    bits=bytearray((grid.spec.width*grid.spec.height+7)//8)
    for index in range(grid.spec.width*grid.spec.height):
        row,col=divmod(index,grid.spec.width)
        if window is None:key,position=(0,0),row*64+col
        else:
            x=window.offset_xy[0]+col;y=window.offset_xy[1]+row
            tx,c=divmod(x,64);ty,r=divmod(y,64);key,position=(tx,ty),r*64+c
        body=stored.get(key)
        if body is not None and body[position//8] & (1 << (position%8)):
            bits[index//8] |= 1 << (index%8)
    return HazardSnapshot(retained.revision,geometry,grid.spec,bytes(bits),(None,None),
        retained.tiles if window is not None else (),window)


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
        self._retained = None

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

    def state_fields(self,pipeline,context,method_manifest_sha256):
        """같은 accepted MAP의 원 occupied/floor만 고정한다. CP queue/인코딩은 호출자 책임이다."""
        self._owner();digest(method_manifest_sha256)
        from ..perception.pipeline import PerceptionPipeline
        snapshot=self.snapshot
        if (type(pipeline) is not PerceptionPipeline or type(context) is not StorageContext
                or snapshot is None or self._staged is not None):
            raise ValueError('accepted hazard and actual current pipeline/context required')
        grid=pipeline.grid;window=grid.rolling_window
        geometry=(CatalogGeometryScope.from_context(context,'SLOT').bytes,method_manifest_sha256,
            grid.frame_id,grid.calibration_id,grid.clock_id,grid.kind,
            grid.spec if window is None else ('ROLLING',window.lattice))
        if (snapshot.geometry!=geometry or snapshot.spec!=grid.spec or snapshot.window!=window
                or grid.calibration_id!=pipeline.calibration.sha256 or grid.frame_id!='odom:'+context.odom_epoch
                or grid.kind!='LIVE' or not 2<=grid.spec.width<=64 or not 2<=grid.spec.height<=64):
            raise ValueError('hazard export differs from accepted map/method/lattice')
        tiles=_fixed_tile(snapshot) if window is None else snapshot.global_occupied
        value=dict(profile='mapper-hazard-state-1',revision=max(self._revision_floor,snapshot.revision),
            method_manifest_sha256=method_manifest_sha256,
            tiles=[[x,y,b64encode(body).decode('ascii')] for (x,y),body in tiles])
        outer=dict(profile='mapper-pipeline-state-1',spec=asdict(grid.spec))
        if window is not None:
            from ..mapping.map_lattice import atlas_state_fields
            outer['atlas']=atlas_state_fields(window,pipeline._rolling_policy)
        validate_hazard_state(value,state=json_bytes(canonical(outer),512*1024))
        return {'hazard':value}

    def restore_complete(self, complete, *, pipeline, scope, now_us):
        """원 CP bitset을 보관한다. 첫 실제 두 source 진전 전에는 current snapshot이 없다."""
        self._owner();uint(now_us)
        from .scene import SceneEvidenceScope
        from ..perception.pipeline import PerceptionPipeline
        if (type(complete) is not SourceComplete or type(scope) is not SceneEvidenceScope or type(pipeline) is not PerceptionPipeline
                or getattr(pipeline,'_restored_checkpoint_ref',None)!=complete.checkpoint_ref):
            raise ValueError('actual complete-restored pipeline and approved scope required')
        if self._retained is not None and self._complete is complete:
            current=CatalogGeometryScope.from_context(scope.context,'SLOT').bytes
            if (self._retained.geometry_scope!=current or self._retained.method_sha256!=scope.method_manifest_sha256
                    or self._retained.calibration_id!=pipeline.calibration.sha256):
                raise ValueError('retained restore owner scope changed')
            return True
        self.require_restore(complete)
        scope.complete_restore_head(complete,now_us=now_us)
        if complete.state is None:return False
        state=json_bytes(complete.state,512*1024)
        if 'hazard' not in state:return False
        revision,method,tiles=validate_hazard_state(state['hazard'],state=state)
        old=StorageContext.from_fields(state['storage_context']);grid=pipeline.grid
        if (old!=complete.original_job.context or scope.calibration!=pipeline.calibration
                or method!=scope.method_manifest_sha256 or state['kind']!='LIVE' or grid.kind!='LIVE'
                or state['calibration_id']!=pipeline.calibration.sha256 or grid.calibration_id!=state['calibration_id']
                or state['frame_id']!='odom:'+scope.context.odom_epoch or grid.frame_id!=state['frame_id']
                or state['pipeline_profile_sha256']!=sha256(canonical(asdict(pipeline.profile))).hexdigest()):
            raise ValueError('retained hazard map/calibration/method/profile differs')
        spec=dict(state['spec']);spec['origin_xy']=tuple(spec['origin_xy']);spec=GridSpec(**spec)
        window=None
        if 'atlas' in state:
            window=read_atlas_window(state['atlas'],spec,policy=pipeline._rolling_policy)
            if window.lattice!=MapLattice.from_spec(pipeline.profile.grid) or grid.rolling_window!=window:
                raise ValueError('retained hazard immutable rolling lattice differs')
        elif grid.rolling_window is not None or spec!=pipeline.profile.grid:
            raise ValueError('retained fixed hazard grid differs')
        if grid.spec!=spec or any(bool(getattr(grid,name).any()) for name in
                ('state','observed_us','time_known','unordered_hit','hit_available_us','hit_origin','conflict')) or grid.hit_view:
            raise ValueError('restored LIVE must remain unknown with no current measurements')
        scene=json_bytes(complete.manifest,16384)['scene']
        if type(scene) is dict and 'hazard_revision' in scene and scene['hazard_revision']!=revision:
            raise ValueError('original frozen scene and hazard revision differ')
        retained=_RetainedHazard(revision,CatalogGeometryScope.from_context(scope.context,'SLOT').bytes,
            method,state['frame_id'],state['calibration_id'],spec,window,tiles)
        self._retained=retained;self._revision_floor=revision
        # complete와 baseline은 owner 수명 동안 유지한다. final accept lock에서 큰 사본을 해제하지 않는다.
        return True

    def _reject(self, original, reason):
        candidate = HazardDraft(original, False, reason, self.snapshot, False, False, True)
        self._staged = candidate
        return candidate

    def prepare(self, mapping, *, now_us):
        """원 실제 처리시각을 받는다. unsafe 후보는 반드시 caller가 즉시 scene 철회해야 한다."""
        self._owner()
        if self._staged is not None: raise ValueError('one original hazard candidate is unresolved')
        if self._complete is not None and self._retained is None: return self._reject(mapping, 'RESTORE_NOT_READY')
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
            window = grid.rolling_window
            if window is not None and (type(window) is not GridWindow or window.spec != grid.spec):
                return self._reject(mapping, 'ROLLING_WINDOW_NOT_READY')
            geometry = (CatalogGeometryScope.from_context(mapping.context, 'SLOT').bytes,
                frame.scope.method_manifest_sha256, grid.frame_id, grid.calibration_id, grid.clock_id, grid.kind,
                grid.spec if window is None else ('ROLLING', window.lattice))
            if (grid.frame_id != 'odom:'+mapping.context.odom_epoch or grid.calibration_id != pipeline.calibration.sha256
                    or grid.clock_id != raw['clock_epoch']):
                return self._reject(mapping, 'GEOMETRY_NOT_READY')
            previous = self.snapshot
            binding_retained=previous is None and self._retained is not None
            if binding_retained:
                retained=self._retained
                if (geometry[0]!=retained.geometry_scope or frame.scope.method_manifest_sha256!=retained.method_sha256 or grid.frame_id!=retained.frame_id
                        or grid.calibration_id!=retained.calibration_id
                        or (window is None)!=(retained.window is None)
                        or (grid.spec!=retained.spec if window is None else window.lattice!=retained.window.lattice)):
                    return self._reject(mapping,'RETAINED_GEOMETRY_CHANGED')
                previous=_retained_snapshot(retained,grid,geometry)
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
            if binding_retained and not all(advanced):return self._reject(mapping,'NO_INITIAL_SOURCE_PROGRESS')
            import numpy as np
            state = grid.state
            if (type(state) is not np.ndarray or state.dtype != np.dtype('u1')
                    or state.shape != (grid.spec.height, grid.spec.width) or bool(np.any(state > OCCUPIED))):
                return self._reject(mapping, 'RAW_STATE_NOT_READY')
            flat = state.reshape(-1)
            fresh_times = tuple(stamp.captured_us[0] for stamp, fresh in zip(stamps, advanced) if fresh)
            # RAW FREE의 원 source 시각이 실제 새 관측이어야 한다. 시간 경과/UNKNOWN은 남긴다.
            clear = ((flat == FREE) & grid.time_known.reshape(-1)
                & np.isin(grid.observed_us.reshape(-1), np.asarray(fresh_times, dtype=np.uint64))) if fresh_times else np.zeros(state.size, dtype=bool)
            global_bits = ()
            if window is None:
                old_bits = (np.zeros(state.size, dtype=bool) if previous is None else
                    np.unpackbits(np.frombuffer(previous.occupied, dtype=np.uint8), bitorder='little', count=state.size).astype(bool))
                bits = (old_bits & ~clear) | (flat == OCCUPIED)
                changed = not bool(np.array_equal(bits, old_bits))
            else:
                bits, global_bits, changed = _rolling_bits(mapping, grid, window, previous, clear, np)
            progressed = any(advanced)
            if changed and not progressed: return self._reject(mapping, 'RAW_CHANGED_WITHOUT_SOURCE_PROGRESS')
            if previous is not None and not progressed and (window is None or previous.window == window):
                candidate = HazardDraft(mapping, True, 'NO_SOURCE_PROGRESS', previous, False, False, False)
            else:
                revision = max(self._revision_floor, 1 if previous is None else previous.revision) + int(changed)
                uint(revision, positive=True)
                snapshot = HazardSnapshot(revision, geometry, grid.spec,
                    np.packbits(bits, bitorder='little').tobytes(), tuple(sources), global_bits, window)
                witness = (mapping.original_job.job_id, *stamps) if progressed else None
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
