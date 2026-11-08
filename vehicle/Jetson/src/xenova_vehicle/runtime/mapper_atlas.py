"""pipeline 밖 단일 compute owner의 atlas/원 TileVersion metadata 연결.

현재 ROI의 변경 block만 COW하며 원 CELL/Pi/sequence와 별도 tile revision HWM을
보존한다. DB/clock/Scene 권한은 발급하지 않는다. CP 완료는 원 receipt를 검산한
caller만 호출하며 release 자체는 COMMITTED가 아니다. bootstrap은 실제 전체 LOAD
검증을 재사용하고 원 SourceComplete의 본문 소유를 M owner 수명 동안 보수적으로 유지한다.
"""

from contextlib import contextmanager
from dataclasses import dataclass,field
import struct
from threading import get_ident
from weakref import ref as weakref

from ..mapping.atlas import AtlasPool,AtlasUpdate,AtlasRef,AtlasError,AtlasResource,MAX_DATA_BYTES
from ..mapping.local_map import GridSpec,EvidenceGrid
from ..mapping.map_lattice import GridWindow,RollingPolicy
from ..mapping.rolling_grid import pack_live_tiles,configure_grid
from ..storage.contracts import uuid,uint,json_bytes,StorageContext
from ..storage.records import TileVersion,LoadedCheckpoint
from ..storage.port import CheckpointRequest
from .atlas_proof import AtlasProofWindow
from .atlas_archive import SceneAtlasArchive


class MapperAtlasNotReady(RuntimeError):
    """원 window/proof/ref 또는 CP 수명이 아직 준비되지 않았다."""
    def __init__(self,reason,*,needs_load=()):
        super().__init__(reason);self.needs_load=needs_load


@dataclass(frozen=True,slots=True)
class AtlasTileMetadata:
    """최대320개 block의 원 wire reference와 compact root vector. 본문은 없다."""
    pool_ref: AtlasRef
    revision: int
    sha256: str
    crc32: int
    root_bytes: bytes

    @property
    def reference(self):return (*self.pool_ref.key,self.revision,self.sha256)

    def iter_roots(self):return (item[0] for item in struct.iter_unpack('<Q',self.root_bytes))


@dataclass(frozen=True,slots=True)
class AtlasInstallReceipt:
    """원 alias 본문과 설치 peak 회계다. 새 commit/clock/scene 영수증이 아니다."""
    input_tile_count: int
    input_body_bytes: int
    aliased_install_bytes: int
    reserved_workspace_bytes: int
    peak_body_and_workspace_bytes: int
    tile_revision_highwater: int


@dataclass(frozen=True)
class AtlasFrameDelta:
    generation: int
    window: GridWindow
    changed_keys: tuple
    tile_references: tuple
    _owner: object=field(repr=False,compare=False)


@dataclass(frozen=True)
class AtlasCheckpointSnapshot:
    generation: int
    tile_references: tuple
    copied_references: tuple
    _reader: object=field(repr=False,compare=False)

    @property
    def tiles(self):
        owner=self._reader()
        if owner is None:raise MapperAtlasNotReady('original atlas owner is gone')
        return owner._snapshot_tiles(self)

    @property
    def checkpoint_lease(self):
        owner=self._reader()
        if owner is None:raise MapperAtlasNotReady('original atlas owner is gone')
        owner._snapshot_tiles(self);return owner._checkpoint.checkpoint_lease

    @property
    def frozen_lease(self):
        owner=self._reader()
        if owner is None:raise MapperAtlasNotReady('original atlas owner is gone')
        owner._snapshot_tiles(self);return owner._checkpoint.frozen_lease


@dataclass(frozen=True)
class _Frame:
    handle: AtlasFrameDelta
    pool_delta: object
    pool_generation: int
    metadata: dict
    roots: tuple
    unresolved: dict
    selected: dict
    retiring: tuple


@dataclass(frozen=True)
class _Checkpoint:
    handle: AtlasCheckpointSnapshot
    copies: tuple
    copy_refs: tuple
    full_refs: tuple
    checkpoint_lease: object
    frozen_lease: object


def _root_union(metadata):
    # compact vector 최대7MiB + 고정320 metadata/원ID tuple 예산1MiB다.
    if len(metadata)>320 or sum(len(item.root_bytes) for item in metadata.values())>7*1024*1024:
        raise AtlasResource('ROOT_METADATA_BYTES')
    roots=set()
    for item in metadata.values():
        roots.update(value for value in item.iter_roots() if value)
        if len(roots)>16384:raise AtlasResource('PROVENANCE_ROOTS')
    return tuple(sorted(roots))


class MapperAtlas:
    """pool은 deepcopy하지 않는다. 후보 metadata만 얕게 분리하고 final에 함께 바꾼다.

    reserved_workspace_bytes에는 실제 동시 ROI와 LIVE/proof pack 임시 배열을 포함해야
    한다. 밖으로 빌린 TileVersion/bytes는 READ/CP lease가 끝난 뒤 보관하지 않는다.
    """
    def __init__(self,map_epoch,initial_spec,rolling_policy,*,reserved_workspace_bytes,tile_revision_highwater=0):
        uuid(map_epoch);uint(tile_revision_highwater)
        if type(initial_spec) is not GridSpec or type(rolling_policy) is not RollingPolicy:
            raise ValueError('original GridSpec and explicit rolling policy required')
        self.window=GridWindow.from_spec(initial_spec);rolling_policy.check_window(self.window)
        self.map_epoch,self.policy=map_epoch,rolling_policy;self.pool=AtlasPool(reserved_workspace_bytes=reserved_workspace_bytes)
        self._thread=get_ident();self._token=object();self._generation=0;self._tile_hwm=tile_revision_highwater
        self._metadata={};self._selected={};self._roots=();self._unresolved={};self._frame=self._checkpoint=self._retiring=None
        self._installing=self._restore_failed=False;self._bootstrap_owner=self._bootstrap_pin=None;self._install_receipt=None
        self._archives={}

    @classmethod
    def from_complete(cls,complete,*,pipeline,current_scope,selected_bootstrap,now_us,reserved_workspace_bytes):
        """실제 검산된 원 전체 LOAD만 fresh owner에 설치한다. 부분 owner는 반환하지 않는다.

        원 SourceComplete의 body를 그대로 공유한다. 그 owner를 강하게 보존하고 weak
        pin도 등록하므로 독립 page/collector alias가 있어도 bootstrap data를 감춰 회수하지
        않는다. LIVE는 설치하지 않으며 실제 복원 pipeline도 UNKNOWN/zero-J여야 한다.
        """
        from .map_checkpoint import SourceComplete,restore_pipeline
        from .scene import SceneEvidenceScope
        from ..perception.pipeline import PerceptionPipeline
        from ..mapping.map_lattice import read_atlas_window
        uint(now_us)
        if (type(complete) is not SourceComplete or type(current_scope) is not SceneEvidenceScope
                or type(pipeline) is not PerceptionPipeline or type(selected_bootstrap) is not dict
                or selected_bootstrap.get('status')!='COMPLETE'
                or selected_bootstrap.get('database_uuid')!=current_scope.context.database_uuid
                or selected_bootstrap.get('selected_checkpoint')!=json_bytes(complete.checkpoint_ref,2048)):
            raise MapperAtlasNotReady('actual selected COMPLETE bootstrap and original decoded pages required')
        if type(complete.tiles) is not tuple or len(complete.tiles)>128 or any(type(tile) is not TileVersion for tile in complete.tiles):
            raise MapperAtlasNotReady('original complete tile inventory required')
        if (type(reserved_workspace_bytes) is not int or not 0<=reserved_workspace_bytes<=MAX_DATA_BYTES):
            raise AtlasResource('RESTORE_WORKSPACE')
        input_bytes=sum(len(tile.payload) for tile in complete.tiles)
        if input_bytes+reserved_workspace_bytes>MAX_DATA_BYTES:raise AtlasResource('RESTORE_INPUT_PEAK')
        scope=current_scope.with_complete_history(complete,now_us=now_us)
        restore_pipeline(pipeline,complete,scope)
        state=json_bytes(complete.state,512*1024);policy=pipeline._rolling_policy
        if type(policy) is not RollingPolicy:raise MapperAtlasNotReady('actual configured rolling policy required')
        initial=pipeline.profile.grid
        window=GridWindow.from_spec(initial) if 'atlas' not in state else read_atlas_window(state['atlas'],pipeline.grid.spec,policy=policy)
        policy.check_window(window)
        if window.lattice!=GridWindow.from_spec(initial).lattice or pipeline.grid.spec!=window.spec:
            raise MapperAtlasNotReady('original fixed lattice/restore window differs')
        grid=pipeline.grid
        if (grid.state.any() or grid.time_known.any() or grid.observed_us.any() or grid.hit_available_us.any()
                or grid.hit_origin.any() or grid.unordered_hit.any() or grid.conflict.any() or grid.hit_view):
            raise MapperAtlasNotReady('bootstrap requires the actual UNKNOWN zero-J restored live grid')
        head=state['tile_revision_highwater'];uint(head)
        owner=cls(current_scope.context.map_epoch,initial,policy,reserved_workspace_bytes=reserved_workspace_bytes,
            tile_revision_highwater=head)
        owner._installing=True
        try:
            owner.window=window
            if grid.rolling_window is None:configure_grid(grid,window)
            elif grid.rolling_window!=window:raise MapperAtlasNotReady('original restored rolling window changed')
            all_refs=[]
            for start in range(0,len(complete.tiles),32):
                originals=complete.tiles[start:start+32]
                if any(tile.map_epoch!=owner.map_epoch or tile.revision>head for tile in originals):
                    raise MapperAtlasNotReady('original map/revision exceeds preserved tile head')
                delta=owner.pool.prepare(tuple(AtlasUpdate('EVIDENCE64',(tile.tile_x,tile.tile_y),tile.payload,None) for tile in originals))
                owner.pool.accept(delta);refs=delta.refs
                lease=owner.pool.lease(refs,purpose='READ')
                try:
                    for original,reference in zip(originals,refs):
                        if owner.pool.payload(reference,lease=lease) is not original.payload or reference.sha256!=original.sha256:
                            raise MapperAtlasNotReady('installed tile changed original body ownership/hash')
                        metadata=AtlasTileMetadata(reference,original.revision,original.sha256,original.crc32,
                            b''.join(struct.pack('<Q',value) for value in original.provenance_ids))
                        owner._metadata[reference.serial]=metadata;owner._selected[reference.key]=metadata
                finally:owner.pool.release(lease)
                owner.pool.mark_clean(refs);all_refs.extend(refs)
            if tuple(sorted(item.reference for item in owner._selected.values()))!=tuple(tile.reference for tile in complete.tiles):
                raise MapperAtlasNotReady('installed original complete reference set changed')
            owner._roots=_root_union(owner._metadata)
            owner._bootstrap_pin=owner.pool.retain_owner(complete,tuple(all_refs))
            owner._bootstrap_owner=complete
            owner._install_receipt=AtlasInstallReceipt(len(complete.tiles),input_bytes,input_bytes,reserved_workspace_bytes,
                input_bytes+reserved_workspace_bytes,head)
            owner._installing=False;return owner
        except BaseException:
            owner._restore_failed=True;owner._installing=False
            raise

    def _owner(self):
        if get_ident()!=self._thread:raise RuntimeError('mapper atlas belongs to the original compute owner')
        if self._installing or self._restore_failed:raise MapperAtlasNotReady('RESTORE_NOT_READY')

    @property
    def tile_revision_highwater(self):
        self._owner();return self._tile_hwm

    @property
    def install_receipt(self):
        self._owner();return self._install_receipt

    def stats(self):
        self._owner();result=self.pool.stats();receipt=self._install_receipt
        result.update(selected_refs=len(self._selected),tile_revision_highwater=self._tile_hwm,
            retiring_metadata=self._retiring is not None,
            bootstrap_retained=self._bootstrap_owner is not None,
            bootstrap_input_body_bytes=0 if receipt is None else receipt.input_body_bytes,
            bootstrap_install_peak_bytes=0 if receipt is None else receipt.peak_body_and_workspace_bytes)
        return result

    @property
    def unresolved(self):
        from types import MappingProxyType
        self._owner()
        return MappingProxyType(self._unresolved)

    def provenance_roots(self):
        """현재와 원 CP/frozen/read로 보존된 모든 EVIDENCE의 원ID다. 새 번호는 없다."""
        self._owner();return self._roots

    @property
    def needs_load(self):
        """eviction으로 본문이 없는 원 selected refs다. 부재를 새 UNKNOWN으로 만들지 않는다."""
        self._owner()
        return tuple((key,item.reference) for key,item in sorted(self._selected.items())
            if self.pool.current('EVIDENCE64',key) is None)

    def metadata(self):
        self._owner();return tuple(self._metadata[key] for key in sorted(self._metadata))

    def _window(self,window):
        if (type(window) is not GridWindow or window.lattice!=self.window.lattice
                or (window.width,window.height)!=(self.window.width,self.window.height)):
            raise MapperAtlasNotReady('original fixed lattice and ROI dimensions required')
        self.policy.check_window(window)

    @contextmanager
    def read_tiles(self,window):
        """원 READ lease 안의 현재 ROI만 빌린다. reader는 context 밖에서 사용할 수 없다."""
        self._owner();self._window(window)
        keys=tuple((piece.tile_x,piece.tile_y) for piece in window.pieces())
        missing=tuple(item for item in self.needs_load if item[0] in keys)
        if missing:raise MapperAtlasNotReady('EVIDENCE_LOAD_REQUIRED',needs_load=missing)
        refs=tuple(original for key in keys for kind in ('LIVE64','EVIDENCE64')
            if (original:=self.pool.current(kind,key)) is not None)
        lease=None if not refs else self.pool.lease(refs,purpose='READ')
        live={};evidence={};active=True
        try:
            for original in refs:
                body=self.pool.payload(original,lease=lease)
                (live if original.kind=='LIVE64' else evidence)[original.key]=body
            def reader(tx,ty):
                if not active:raise MapperAtlasNotReady('original READ lease has ended')
                if (tx,ty) not in keys:raise MapperAtlasNotReady('tile is outside the original READ window')
                return evidence.get((tx,ty))
            yield live,reader
        finally:
            active=False;live.clear();evidence.clear()
            if lease is not None:self.pool.release(lease)

    def _next_revision(self):
        if self._tile_hwm>=(1<<63)-1:raise AtlasResource('TILE_REVISION_EXHAUSTED')
        self._tile_hwm+=1;return self._tile_hwm

    def install_loaded_tile(self,loaded,*,context,now_us):
        """원 CURRENT_MAP/TILE LOAD만 누락된 selected key에 재설치한다. I/O는 하지 않는다.

        caller의 원 peer/job admission 뒤 actual LoadedCheckpoint를 받는다. 본문/원
        revision/roots를 바꾸지 않고 실제 current LOAD를 근거로 그 ref만 cache clean으로
        설치한다. LIVE/새 시각/odom/source/Scene 자격을 만들거나 최신 자료를 덮지 않는다.
        """
        self._owner();uint(now_us)
        if self._frame is not None or self._checkpoint is not None:
            raise MapperAtlasNotReady('original frame/checkpoint blocks TILE installation')
        if (type(loaded) is not LoadedCheckpoint or type(context) is not StorageContext
                or loaded.request.purpose!='CURRENT_MAP' or loaded.request.job.context!=context
                or not loaded.current(loaded.request,context.sha256,now_us)):
            raise MapperAtlasNotReady('actual current original CURRENT_MAP TILE LOAD required')
        original=loaded.original_job.context
        if (context.map_epoch!=self.map_epoch or original.database_uuid!=context.database_uuid
                or original.geometry_origin!=context.geometry_origin
                or any(getattr(original,key)!=getattr(context,key) for key in
                    ('map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256'))
                or len(loaded.payload.tiles)!=1):
            raise MapperAtlasNotReady('TILE LOAD changed original DB/map/geometry source')
        tile=loaded.payload.tiles[0];key=(tile.tile_x,tile.tile_y);expected=self._selected.get(key)
        selection=json_bytes(loaded.request.selection,512)
        if (expected is None or tile.map_epoch!=self.map_epoch or tile.reference!=expected.reference or tile.crc32!=expected.crc32
                or tile.reference not in loaded.payload.tile_references
                or selection!=dict(kind='TILE',map_epoch=self.map_epoch,tile_x=key[0],tile_y=key[1],tile_revision=str(expected.revision))
                or b''.join(struct.pack('<Q',value) for value in tile.provenance_ids)!=expected.root_bytes
                or tile.revision>self._tile_hwm):
            raise MapperAtlasNotReady('TILE LOAD differs from original selected reference/root vector')
        if self.pool.current('EVIDENCE64',key) is not None or self.pool.current('LIVE64',key) is not None:
            raise MapperAtlasNotReady('TILE LOAD cannot overwrite an actual current block')
        if not any(item[0]==key and item[1]==expected.reference for item in self.needs_load):
            raise MapperAtlasNotReady('original selected key does not require this LOAD')
        self.drain_retired()
        delta=None
        try:
            delta=self.pool.prepare((AtlasUpdate('EVIDENCE64',key,tile.payload,None),))
            reference=delta.refs[0]
            if reference.sha256!=expected.sha256:raise MapperAtlasNotReady('original loaded body SHA differs from selected metadata')
            replacement=AtlasTileMetadata(reference,expected.revision,expected.sha256,expected.crc32,expected.root_bytes)
            selected=dict(self._selected);selected[key]=replacement
            keep={item.serial for item in self.pool.retained_refs('EVIDENCE64',delta=delta)}|{item.pool_ref.serial for item in selected.values()}
            metadata={serial:item for serial,item in self._metadata.items() if serial in keep}
            metadata[reference.serial]=replacement;roots=_root_union(metadata)
            self.pool.preflight_owner(loaded,(reference,),delta=delta)
            self.pool.validate(delta);self.pool.accept(delta);delta=None
            self.pool.retain_owner(loaded,(reference,))
            self.pool.mark_clean((reference,))
            self._metadata=metadata;self._selected=selected;self._roots=roots;self._generation+=1
            return replacement
        except BaseException:
            if delta is not None:self.pool.cancel(delta)
            raise

    def prepare_frame(self,grid,proof_window):
        """실제 후보 grid/proof의 현재 창 최대4tile, 변경 block 최대8개만 준비한다."""
        self._owner()
        if self._frame is not None:raise MapperAtlasNotReady('one original atlas frame is unresolved')
        self.drain_retired()
        if type(grid) is not EvidenceGrid or type(proof_window) is not AtlasProofWindow:
            raise ValueError('actual EvidenceGrid and AtlasProofWindow required')
        window=proof_window.window;self._window(window)
        if grid.rolling_window!=window or grid.spec!=window.spec:raise MapperAtlasNotReady('grid/proof window mismatch')
        updates=[];evidence_tiles={}
        # pack 배열/본문 생성 중 peak는 ctor의 명시 workspace 예약을 사용한다.
        with self.read_tiles(window) as (live,reader):
            live_packed=pack_live_tiles(grid,live);proof_packed=proof_window.packed_tiles(reader)
            for key,body in live_packed:
                old=self.pool.current('LIVE64',key)
                if old is None or body!=live[key]:updates.append(AtlasUpdate('LIVE64',key,body,old))
            for key,body in proof_packed.items():
                old=self.pool.current('EVIDENCE64',key);baseline=reader(*key)
                if old is None or body!=baseline:
                    tile=TileVersion(self.map_epoch,*key,self._next_revision(),body)
                    evidence_tiles[key]=tile;updates.append(AtlasUpdate('EVIDENCE64',key,body,old))
        pool_delta=None
        try:
            if updates:pool_delta=self.pool.prepare(tuple(updates))
            retained=self.pool.retained_refs('EVIDENCE64',delta=pool_delta)
            selected=dict(self._selected);new_metadata={}
            new_refs=() if pool_delta is None else pool_delta.refs
            for original in new_refs:
                if original.kind!='EVIDENCE64':continue
                tile=evidence_tiles[original.key]
                if tile.sha256!=original.sha256:raise MapperAtlasNotReady('pool/storage original body SHA differs')
                new_metadata[original.serial]=AtlasTileMetadata(original,tile.revision,tile.sha256,tile.crc32,
                    b''.join(struct.pack('<Q',value) for value in tile.provenance_ids))
                selected[original.key]=new_metadata[original.serial]
            if len(selected)>128:raise AtlasResource('SELECTED_TILE_REFS')
            keep={original.serial for original in retained}|{item.pool_ref.serial for item in selected.values()}
            metadata={key:item for key,item in self._metadata.items() if key in keep};metadata.update(new_metadata)
            if any(original.serial not in metadata for original in retained):raise MapperAtlasNotReady('original EVIDENCE metadata missing')
            roots=_root_union(metadata)
            references=tuple(sorted(item.reference for item in selected.values()))
            handle=AtlasFrameDelta(self._generation,window,tuple(sorted({update.key for update in updates})),references,self._token)
            retiring=(self._metadata,self._roots,self._unresolved,self._selected)
            self._frame=_Frame(handle,pool_delta,self.pool.generation,metadata,roots,dict(proof_window.unresolved),selected,retiring)
            return handle
        except BaseException:
            if pool_delta is not None:self.pool.cancel(pool_delta)
            raise

    def accept_frame(self,delta):
        """caller의 원 source/기한 검산 뒤 작은 원 ref/generation 검사와 swap만 한다."""
        self._owner();frame=self._frame
        if frame is None or self._retiring is not None or delta is not frame.handle or delta.generation!=self._generation:
            raise MapperAtlasNotReady('original atlas frame was fenced')
        if frame.pool_delta is not None:self.pool.validate(frame.pool_delta)
        elif self.pool.generation!=frame.pool_generation:raise MapperAtlasNotReady('original pool generation changed')
        if frame.pool_delta is not None:self.pool.accept(frame.pool_delta)
        self._retiring=frame.retiring
        self.window=delta.window;self._metadata=frame.metadata;self._roots=frame.roots;self._unresolved=frame.unresolved
        self._selected=frame.selected
        self._frame=None;self._generation+=1

    def cancel_frame(self,delta):
        self._owner()
        if self._frame is None or delta is not self._frame.handle:raise MapperAtlasNotReady('original atlas frame required')
        if self._frame.pool_delta is not None:self.pool.cancel(self._frame.pool_delta)
        self._frame=None

    def _refresh_metadata(self):
        keep={original.serial for original in self.pool.retained_refs('EVIDENCE64')}|{item.pool_ref.serial for item in self._selected.values()}
        self._metadata={key:item for key,item in self._metadata.items() if key in keep};self._roots=_root_union(self._metadata)

    def drain_retired(self):
        """adoption lock 밖의 compute 유지/prepare에서 이전 본문과 root 참조를 해제한다."""
        self._owner()
        if self._frame is not None:
            if self._retiring is not None:raise MapperAtlasNotReady('original frame blocks retiring metadata release')
            return False
        retired=self._retiring
        released=self.pool.drain_retired()
        self._retiring=None
        if released or retired is not None:self._refresh_metadata()
        return released or retired is not None

    def _tile(self,original,lease):
        metadata=self._metadata.get(original.serial)
        if metadata is None:raise MapperAtlasNotReady('original selected tile metadata missing')
        tile=TileVersion(self.map_epoch,*original.key,metadata.revision,self.pool.payload(original,lease=lease))
        if tile.reference!=metadata.reference:raise MapperAtlasNotReady('original immutable TileVersion differs')
        return tile

    def checkpoint_snapshot(self,*,allow_clean=False):
        """actual dirty 사본과 full ref를 원 lease로 고정한다. clean/완료를 발급하지 않는다."""
        self._owner()
        if type(allow_clean) is not bool:raise ValueError('explicit metadata-only checkpoint policy required')
        if self._frame is not None or self._checkpoint is not None:raise MapperAtlasNotReady('one original frame/checkpoint is unresolved')
        self.drain_retired()
        if self._unresolved:raise MapperAtlasNotReady('UNRESOLVED_PROOF')
        if self.needs_load:raise MapperAtlasNotReady('EVIDENCE_LOAD_REQUIRED',needs_load=self.needs_load)
        self.pool.coalesce_unleased();self._refresh_metadata()
        dirty=self.pool.dirty_refs();full=self.pool.current_refs('EVIDENCE64')
        if {original.key for original in full}!=set(self._selected):raise MapperAtlasNotReady('selected directory differs from actual current EVIDENCE')
        if not dirty and not allow_clean:raise MapperAtlasNotReady('NO_DIRTY_EVIDENCE')
        if len(dirty)>32 or len(full)>128:raise AtlasResource('CHECKPOINT_CAPACITY')
        if any(not any(original is selected for selected in full) for original in dirty):
            raise MapperAtlasNotReady('dirty original is not a current complete selected tile')
        checkpoint=frozen=None
        try:
            if dirty:checkpoint=self.pool.lease(dirty,purpose='CHECKPOINT')
            if full:frozen=self.pool.lease(full,purpose='FROZEN')
            copies=tuple(self._tile(original,checkpoint) for original in dirty)
            references=tuple(sorted(self._metadata[original.serial].reference for original in full))
            handle=AtlasCheckpointSnapshot(self._generation,references,tuple(tile.reference for tile in copies),weakref(self))
            self._checkpoint=_Checkpoint(handle,copies,dirty,full,checkpoint,frozen);return handle
        except BaseException:
            if frozen is not None:self.pool.release(frozen)
            if checkpoint is not None:self.pool.release(checkpoint)
            raise

    def _snapshot_tiles(self,snapshot):
        self._owner()
        if self._checkpoint is None or snapshot is not self._checkpoint.handle:raise MapperAtlasNotReady('original checkpoint lease has ended')
        if self._checkpoint.checkpoint_lease is not None:
            self.pool.payload(self._checkpoint.copy_refs[0],lease=self._checkpoint.checkpoint_lease)
        return self._checkpoint.copies

    def retain_checkpoint_owner(self,snapshot,original_request):
        """원 request/MapSnapshot의 dirty 본문 보유를 lease 이후에도 계수한다.

        내용이 같은 별도 bytes 사본은 pool ref의 소유권으로 숨길 수 없다. request의
        실제 tile payload가 원 snapshot bytes 객체일 때만 원 block을 pin한다.
        """
        from .map_checkpoint import MapSnapshot
        self._owner();copies=self._snapshot_tiles(snapshot)
        if type(original_request) is CheckpointRequest:
            tiles=original_request.payload.tiles;references=original_request.payload.tile_references
        elif type(original_request) is MapSnapshot:
            if original_request.all_tiles:raise MapperAtlasNotReady('rolling MapSnapshot must use lazy full tiles')
            tiles=original_request.tiles;references=original_request.tile_references
        else:raise MapperAtlasNotReady('original immutable checkpoint request or MapSnapshot required')
        if (original_request.job.context.map_epoch!=self.map_epoch or references!=snapshot.tile_references
                or len(tiles)!=len(copies)
                or any(actual.reference!=expected.reference or actual.payload is not expected.payload
                    for actual,expected in zip(tiles,copies))):
            raise MapperAtlasNotReady('original checkpoint request changed the retained tile body ownership')
        return self.pool.retain_owner(original_request,self._checkpoint.copy_refs)

    def _register_archive(self,job,checkpoint_sha256,checkpoint_digest,manifest_sha256,checkpoint_ref,refs):
        if self._frame is not None:raise MapperAtlasNotReady('archive registration must precede original frame staging')
        if not refs or len(refs)>128:raise MapperAtlasNotReady('nonempty original scene archive permits at most128 tiles')
        available={original.serial:original for original in self.pool.retained_refs('EVIDENCE64')}
        if any(available.get(original.serial) is not original or (item:=self._metadata.get(original.serial)) is None
                or item.pool_ref is not original for original in refs):
            raise MapperAtlasNotReady('original retained archive refs/metadata unavailable')
        refs=tuple(sorted(refs,key=lambda original:self._metadata[original.serial].reference))
        references=tuple(self._metadata[original.serial].reference for original in refs)
        self._archives={key:value for key,value in self._archives.items() if value() is not None}
        key=(job.job_id,checkpoint_digest,checkpoint_ref)
        known=self._archives.get(key);known=None if known is None else known()
        if known is not None:
            if (known.tile_references!=references or known.checkpoint_job!=job or known.checkpoint_sha256!=checkpoint_sha256
                    or known.manifest_sha256!=manifest_sha256 or len(known.pool_refs)!=len(refs)
                    or any(a is not b for a,b in zip(known.pool_refs,refs))):
                raise MapperAtlasNotReady('same original scene archive changed source or refs')
            return known
        if len(self._archives)>=16:raise AtlasResource('SCENE_ARCHIVES')
        archive=SceneAtlasArchive._prepared(map_epoch=self.map_epoch,lattice=self.window.lattice,checkpoint_job=job,
            checkpoint_sha256=checkpoint_sha256,checkpoint_digest=checkpoint_digest,manifest_sha256=manifest_sha256,
            checkpoint_ref=checkpoint_ref,tile_references=references,pool_refs=refs,owner=self._token)
        pin=self.pool.retain_owner(archive,refs)
        object.__setattr__(archive,'pin',pin);self._archives[key]=weakref(archive)
        return archive

    def retain_scene(self,snapshot,actual_request):
        """활성 원 CP/FROZEN의 full refs를 독립 carrier로 pin한다. commit/Scene 승인은 없다."""
        self._owner()
        if self._frame is not None:raise MapperAtlasNotReady('archive registration must precede original frame staging')
        copies=self._snapshot_tiles(snapshot)
        if (type(actual_request) is not CheckpointRequest or actual_request.job.context.map_epoch!=self.map_epoch
                or actual_request.payload.tile_references!=snapshot.tile_references
                or len(actual_request.payload.tiles)!=len(copies)
                or any(a.reference!=b.reference or a.payload is not b.payload for a,b in zip(actual_request.payload.tiles,copies))):
            raise MapperAtlasNotReady('actual original checkpoint request/copy ownership required')
        if self._checkpoint.frozen_lease is None:raise MapperAtlasNotReady('original full FROZEN lease required')
        refs=self._checkpoint.full_refs
        for original in refs:self.pool.payload(original,lease=self._checkpoint.frozen_lease)
        from hashlib import sha256
        return self._register_archive(actual_request.job,actual_request.payload.sha256,actual_request.digest,
            sha256(actual_request.payload.manifest).hexdigest(),None,refs)

    def archive_from_complete(self,complete,*,current_scope,now_us):
        """검산된 원 bootstrap Complete의 옛 pinned refs만 사용한다. current ref를 대입하지 않는다."""
        from .map_checkpoint import SourceComplete
        from .scene import SceneEvidenceScope
        from hashlib import sha256
        self._owner();uint(now_us)
        if self._frame is not None:raise MapperAtlasNotReady('archive registration must precede original frame staging')
        if (type(complete) is not SourceComplete or complete is not self._bootstrap_owner
                or type(current_scope) is not SceneEvidenceScope or current_scope.context.map_epoch!=self.map_epoch
                or complete.original_commit is None or self._bootstrap_pin is None):
            raise MapperAtlasNotReady('actual installed original complete/commit/bootstrap pin required')
        current_scope.complete_restore_head(complete,now_us=now_us)
        checkpoint_sha256=complete.original_commit.validate(complete.original_job,complete.manifest,complete.checkpoint_ref)
        refs=self._bootstrap_pin.refs;originals={tile.reference:tile for tile in complete.tiles}
        if len(refs)!=len(originals):raise MapperAtlasNotReady('original complete bootstrap inventory differs')
        try:
            with self.pool.borrow_owner(complete,self._bootstrap_pin,refs) as reader:
                for original in refs:
                    item=self._metadata.get(original.serial)
                    tile=None if item is None else originals.get(item.reference)
                    if (tile is None or item.pool_ref is not original or item.crc32!=tile.crc32
                            or tuple(item.iter_roots())!=tile.provenance_ids or reader(original) is not tile.payload):
                        raise MapperAtlasNotReady('original complete body/ref/root ownership differs')
        except AtlasError as error:raise MapperAtlasNotReady('original complete bootstrap pin unavailable') from error
        return self._register_archive(complete.original_job,checkpoint_sha256,complete.original_commit.receipt.job_digest,
            sha256(complete.manifest).hexdigest(),complete.checkpoint_ref,refs)

    @contextmanager
    def read_archive(self,archive,window):
        """등록된 원 full archive 중 현재 창 최대4tile만 빌린다. staged generation을 바꾸지 않는다."""
        self._owner();self._window(window)
        if (type(archive) is not SceneAtlasArchive or archive._owner is not self._token
                or archive.map_epoch!=self.map_epoch or archive.lattice!=window.lattice):
            raise MapperAtlasNotReady('original scene archive of this map/lattice required')
        entries={original.key:original for original in archive.pool_refs}
        keys=tuple((piece.tile_x,piece.tile_y) for piece in window.pieces())
        refs=tuple(entries[key] for key in keys if key in entries);active=True;thread=self._thread
        try:
            with self.pool.borrow_owner(archive,archive.pin,refs) as fetch:
                def reader(tx,ty):
                    if get_ident()!=thread:raise RuntimeError('archive read belongs to its original compute owner')
                    if not active:raise MapperAtlasNotReady('original archive read has ended')
                    key=(tx,ty)
                    if type(tx) is not int or type(ty) is not int or key not in keys:
                        raise MapperAtlasNotReady('tile is outside original archive read window')
                    original=entries.get(key)
                    return None if original is None else fetch(original)
                yield reader
        except AtlasError as error:raise MapperAtlasNotReady('original registered archive pin/ref unavailable') from error
        finally:active=False

    def drain_owner_releases(self):
        """GC callback은 token만 남긴다. 실제 pool 회수/원 roots 갱신은 compute에서 한다."""
        self._owner();released=self.pool.drain_owner_releases()
        if self._frame is None:
            self.drain_retired()
            self.pool.coalesce_unleased();self._refresh_metadata()
        return released

    def iter_tiles(self,snapshot):
        """Scene full128 검사도 원 FROZEN lease에서만 한다. 밖에 본문을 보관하지 않는다."""
        self._snapshot_tiles(snapshot);original=self._checkpoint
        for reference in original.full_refs:
            self._snapshot_tiles(snapshot);yield self._tile(reference,original.frozen_lease)

    def _finish_checkpoint(self,snapshot,clean):
        self._snapshot_tiles(snapshot);original=self._checkpoint
        if clean and original.copy_refs:self.pool.mark_clean(original.copy_refs)
        if original.checkpoint_lease is not None:self.pool.release(original.checkpoint_lease)
        if original.frozen_lease is not None:self.pool.release(original.frozen_lease)
        self._checkpoint=None

    def complete_checkpoint(self,snapshot):
        """실제 원 COMMITTED를 검산한 caller만 호출한다. 최신 B를 A로 clean 처리하지 않는다."""
        self._owner();self._finish_checkpoint(snapshot,True)

    def retire_checkpoint(self,snapshot):
        """실제 terminal/lease 수습 후 반납한다. UNKNOWN을 시간만 보고 수습하지 않는다."""
        self._owner();self._finish_checkpoint(snapshot,False)
