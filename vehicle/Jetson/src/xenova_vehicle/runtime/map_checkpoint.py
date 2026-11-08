"""실제 cell mutation/provenance를 SQLite copy로 묶고 완전 LOAD를 정적 자료로 복원한다.

compute owner 전용이며 I/O·fsync·native token·scene/추진 권한은 소유하지 않는다.
원 source가 없는 셀을 current RGB/revision으로 채우지 않는다. 옛 FREE는 live가 아니다.
"""

from base64 import b64decode,b64encode
from dataclasses import asdict,dataclass,replace
from hashlib import sha256
import math
import struct
from uuid import UUID

from ..mapping.local_map import EvidenceGrid,GridMutation,GridSpec,SourceGate,FREE,OCCUPIED
from ..mapping.odometry import PoseEstimate
from ..mapping.registration import FrozenReference,SegmentFeature
from ..perception.inputs import PerceptionInput
from ..perception.pipeline import MapEvidenceUpdate,PerceptionPipeline
from ..storage.contracts import (StorageContext,StorageJob,ProvenanceRange,PersistenceError,
    canonical,digest,json_bytes,uint,selected_checkpoint_reference)
from ..storage.records import (CELL,PROVENANCE,CheckpointPayload,ContextRecord,EvidenceTable,
    LoadedCheckpoint,ProvenanceRecord,TileVersion)
from ..storage.mapper_state import encode_mapper_state,decode_mapper_state,mapper_state_roots
from ..storage.port import CheckpointRequest
from .scene import SceneEvidenceScope


class MapCheckpointNotReady(PersistenceError):pass


class MapRestoreNotReady(MapCheckpointNotReady):
    """저장 자료의 COMPLETE와 현재 map 좌표 복원 가능 여부를 구분한다."""


def _interval(value):
    if type(value) is not tuple or len(value)!=2:raise MapCheckpointNotReady('original interval required')
    for endpoint in value:uint(endpoint)
    if value[0]>value[1]:raise MapCheckpointNotReady('reversed original interval')
    return value


def _pack_array(value):
    return dict(dtype=value.dtype.str,shape=list(value.shape),raw=b64encode(value.tobytes()).decode('ascii'))


def _array(value,dtype,shape):
    import numpy as np
    if type(value) is not dict or set(value)!= {'dtype','shape','raw'} or value['shape']!=list(shape) or value['dtype']!=np.dtype(dtype).str:
        raise MapCheckpointNotReady('original mapper array descriptor mismatch')
    try:raw=b64decode(value['raw'],validate=True)
    except (ValueError,TypeError) as error:raise MapCheckpointNotReady('invalid mapper array encoding') from error
    if len(raw)!=math.prod(shape)*np.dtype(dtype).itemsize or b64encode(raw).decode('ascii')!=value['raw']:
        raise MapCheckpointNotReady('mapper array length/canonical encoding mismatch')
    return np.frombuffer(raw,dtype=dtype).reshape(shape)


def _gate_fields(gate):
    return dict(capacity=gate.capacity,heads=[[*scope,sequence,key,None if interval is None else list(interval)]
        for scope,(sequence,key,interval) in sorted(gate._heads.items())])


def _gate(value,raw):
    if type(value) is not dict or set(value)!= {'capacity','heads'} or type(value['heads']) is not list:
        raise MapCheckpointNotReady('exact source gate state required')
    result=SourceGate(value['capacity'])
    if len(value['heads'])>result.capacity:raise MapCheckpointNotReady('source gate capacity mismatch')
    known=set();streams=set(raw.fields['stream_instances'].values());source=raw.fields['acquisition_worker']
    for row in value['heads']:
        if type(row) is not list or len(row)!=6:raise MapCheckpointNotReady('source gate row shape')
        a,b,c,sequence,key,interval=row
        if any(type(v) is not str or not 1<=len(v)<=128 or any(not 33<=ord(ch)<=126 for ch in v) for v in (a,b,c)):
            raise MapCheckpointNotReady('original bounded source gate identity required')
        uint(sequence,(1<<64)-1);digest(key)
        if interval is not None:interval=_interval(tuple(interval))
        scope=(a,b,c)
        if scope in known:raise MapCheckpointNotReady('duplicate original source gate scope')
        known.add(scope)
        # 다른 실제 A/clock의 HWM은 보존된 META 안에만 남기며 새 stream으로 이식하지 않는다.
        if a==source and (b in streams or b.startswith('NN:') and b[3:] in streams) and c==raw.fields['clock_epoch']:
            result._heads[scope]=(sequence,key,interval)
    return result


def _pipeline_state(pipeline,context):
    import numpy as np
    grid=pipeline.grid
    arrays={name:_pack_array(getattr(grid,name)) for name in ('state','observed_us','time_known','support','permission',
        'conflict','hit_origin','unordered_hit','hit_available_us')}
    reference=None if pipeline.reference is None else asdict(pipeline.reference)
    window=getattr(grid,'rolling_window',None)
    labels=sorted(set(grid.hit_view.values()) if window is None else {str(value) for value in grid.hit_view.values()})
    if len(labels)>64:raise MapCheckpointNotReady('original hit-view inventory exceeds state capacity')
    view_codes=np.zeros(grid.state.shape,dtype=np.uint8);lookup={label:index+1 for index,label in enumerate(labels)}
    for index,view in grid.hit_view.items():view_codes.reshape(-1)[index]=lookup[view if window is None else str(view)]
    state=dict(profile='mapper-pipeline-state-1',storage_context=context.fields,
        calibration_id=pipeline.calibration.sha256,pipeline_profile_sha256=sha256(canonical(asdict(pipeline.profile))).hexdigest(),
        clock_id=grid.clock_id,kind=grid.kind,frame_id=grid.frame_id,
        spec=asdict(grid.spec),free_ttl_us=grid.free_ttl_us,occupied_ttl_us=grid.occupied_ttl_us,
        independent_view_m=grid.independent_view_m,revision=grid.revision,
        applied_hits=grid.applied_hits,applied_free=grid.applied_free,arrays=arrays,
        hit_views=dict(labels=labels,codes=_pack_array(view_codes)),
        gates={name:_gate_fields(getattr(pipeline,name)) for name in ('rgb_gate','depth_gate','nn_gate')},
        map_from_odom_identity=bool(pipeline._alignment is None and not pipeline._restore_pending),
        alignment=None if pipeline._alignment is None else pipeline._alignment.fields(),reference=reference)
    if window is not None:
        from ..mapping.map_lattice import atlas_state_fields
        try:state['atlas']=atlas_state_fields(window,getattr(pipeline,'_rolling_policy',None))
        except ValueError as error:raise MapCheckpointNotReady('explicit original rolling policy/window required') from error
    return state


CLOCK_LEASE_FIELDS=frozenset(('trust_bytes','source_revision','time_lease_revision','completed_pi_us','probe_pi_send_us',
    'basis_pi_us','pi_support_lower_us','expires_pi_us','max_offset_width_us','raw_probe_bytes','lookback_us','lease_us'))


def _alignment_clock(frame):
    from xenova_drive_world.mapper_clock import ClockLease
    lease=frame.clock_lease
    if type(lease) is not ClockLease:raise MapCheckpointNotReady('original alignment ClockLease required')
    fields={name:getattr(lease,name) for name in CLOCK_LEASE_FIELDS}
    for name in ('trust_bytes','raw_probe_bytes'):fields[name]=b64encode(fields[name]).decode('ascii')
    return dict(profile='alignment-clock-source-1',lease=fields,pi_interval=frame.pi_interval,accepted_j_us=frame.accepted_us)


def _verify_alignment_clock(value,alignment,record,context):
    """원 당시 clock만 재구성해 J/Pi 관계를 검산한다. 현재 lease/시각으로 바꾸지 않는다."""
    from xenova_drive_world.mapper_clock import ClockLease
    if type(value) is not dict or set(value)!= {'profile','lease','pi_interval','accepted_j_us'} or value['profile']!='alignment-clock-source-1':
        raise MapRestoreNotReady('exact original alignment clock evidence required')
    fields=value['lease']
    if type(fields) is not dict or set(fields)!=CLOCK_LEASE_FIELDS:raise MapRestoreNotReady('original alignment lease fields differ')
    fields=dict(fields)
    for name in ('trust_bytes','raw_probe_bytes'):
        if type(fields[name]) is not str:raise MapRestoreNotReady('original alignment clock bytes required')
        try:raw=b64decode(fields[name],validate=True)
        except (ValueError,TypeError) as error:raise MapRestoreNotReady('invalid original alignment clock bytes') from error
        if b64encode(raw).decode('ascii')!=fields[name]:raise MapRestoreNotReady('noncanonical original clock bytes')
        fields[name]=raw
    lease=ClockLease(**fields);pi_interval=_interval(tuple(value['pi_interval']));uint(value['accepted_j_us'])
    lo,hi=lease.pi_interval_to_j(pi_interval,value['accepted_j_us']);observed=alignment.observed_j_interval
    if (not lo<=observed[0]<=observed[1]<=hi or pi_interval!=(record.pi_lower,record.pi_upper)
            or lease.pi_host_boot!=context.pi_host_boot or lease.source['clock_epoch']!=alignment.context[1]
            or lease.source['vehicle']!=context.geometry_origin_fields['vehicle']
            or any(lease.source[name]!=getattr(context,field) for name,field in (('mapper_instance','origin_mapper_instance'),
                ('map_epoch','map_epoch'),('frame_epoch','frame_epoch'),('odom_epoch','odom_epoch'),('configuration','configuration'),
                ('calibration','calibration'),('primitive','primitive_sha256')))):
        raise MapRestoreNotReady('original alignment J/Pi interval/source differs from REGISTRATION')


def _reference_vehicle(scope,vehicle,admission,valid_for,now_us):
    if vehicle is None and admission is None and valid_for is None:return
    from ..transport.sensor_samples import VehicleEvidence,SensorSamples,FrozenSensorSamples
    if type(vehicle) is not VehicleEvidence or type(admission) not in (SensorSamples,FrozenSensorSamples) or not admission.current_vehicle(vehicle,now_us):
        raise MapCheckpointNotReady('actual current reference Vehicle/admission required')
    origin=scope.context.geometry_origin_fields;message=vehicle.message()
    if ((message.host_boot,message.gateway_instance,message.pi_session,message.device_session,message.odom_epoch,message.calibration,message.vehicle)
            !=tuple(origin[name] for name in ('pi_host_boot','gateway_instance','pi_session','device_session','odom_epoch','calibration','vehicle'))
            or admission.context.calibration.sha256!=scope.calibration.sha256
            or admission.context.source['clock_epoch']!=scope.raw_binding.fields['clock_epoch']):
        raise MapCheckpointNotReady('reference Vehicle origin/clock/calibration differs from scope')
    uint(valid_for,0xffffffff,positive=True)


@dataclass(frozen=True)
class FrameEvidence:
    """실제 capture와 승인 scope·원 Pi/J 구간·이미 존재하는 odom/정합 parent의 결합.

    from_capture는 P0의 원 payload/seq/device time을 검사한다. parents는 실제 수집한
    PI_ODOM/REGISTRATION DAG이며 새 ID를 이름만으로 발급하는 입력을 허용하지 않는다.
    """
    scope: SceneEvidenceScope
    observation: PerceptionInput
    pi_interval: tuple
    raw_keys: tuple
    observation_sequence: int
    parents: tuple
    contexts: tuple
    clock_lease: object
    accepted_us: int
    ancestry: tuple = ()
    source_kind: str = 'CAMERA'
    sensor_id: int | None = None
    reference_vehicle: object = None
    registration_valid_for_us: int | None = None

    @classmethod
    def from_capture(cls,scope,observation,sample,*,pi_interval,observation_sequence,
                     parents,contexts,clock_lease,now_us,ancestry=(),reference_vehicle=None,admission=None,registration_valid_for_us=None,
                     pose_bracket=None,sensor_state=None):
        from .resident_samples import CaptureSample
        from ..oak.frames import RawFrame
        from xenova_drive_world.mapper_clock import ClockLease
        if type(scope) is not SceneEvidenceScope or type(observation) is not PerceptionInput or type(sample) is not CaptureSample:
            raise MapCheckpointNotReady('actual scope/input/P0 capture required')
        if observation.kind!='LIVE' or observation.calibration_id!=scope.calibration.sha256 or observation.capture_interval is None:
            raise MapCheckpointNotReady('physical capture timing/calibration not admitted')
        pi_interval=_interval(pi_interval);uint(now_us);uint(observation_sequence,positive=True)
        raw=scope.raw_binding.fields;keys=[]
        for frame,image,name in ((sample.rgb,observation.rgb,'rgb_video'),(sample.depth,observation.depth,'depth_right')):
            if type(frame) is not RawFrame:raise MapCheckpointNotReady('original owned RawFrame required')
            frame.validate()
            if (image is None or frame.payload!=image.payload or frame.sequence!=image.stamp.sequence
                    or image.stamp.key[:3]!=(raw['acquisition_worker'],raw['stream_instances'][name],raw['clock_epoch'])
                    or frame.encoding!=image.encoding or (frame.width,frame.height)!=(image.width,image.height)):
                raise MapCheckpointNotReady('original P0 image/source changed before collection')
            keys.append((raw['stream_instances'][name],frame.sequence,frame.device_us))
        if type(clock_lease) is not ClockLease:raise MapCheckpointNotReady('original clock lease required')
        source=clock_lease.source;context=scope.context
        if (clock_lease.pi_host_boot!=context.pi_host_boot or source['vehicle']!=context.geometry_origin_fields['vehicle']
                or any(source[name]!=getattr(context,field) for name,field in
                (('mapper_instance','origin_mapper_instance'),('map_epoch','map_epoch'),('frame_epoch','frame_epoch'),('odom_epoch','odom_epoch'),
                 ('configuration','configuration'),('calibration','calibration'),('primitive','primitive_sha256')))
                or source['clock_epoch']!=raw['clock_epoch']):
            raise MapCheckpointNotReady('original clock lease source differs from mapper capture')
        lo,hi=clock_lease.pi_interval_to_j(pi_interval,now_us);a,b=observation.capture_interval
        if not lo<=a<=b<=hi:raise MapCheckpointNotReady('original Pi interval does not cover full capture')
        if (type(parents) is not tuple or not 1<=len(parents)<=4 or any(type(v) is not ProvenanceRecord for v in parents)
                or type(contexts) is not tuple or any(type(v) is not ContextRecord for v in contexts)
                or not any(v.source_mask&16 for v in parents)):
            raise MapCheckpointNotReady('actual odom/registration parent records and contexts required')
        by_sha={item.sha256:item for item in contexts}
        if type(ancestry) is not tuple or len(ancestry)>16384 or any(type(v) is not ProvenanceRecord for v in ancestry):
            raise MapCheckpointNotReady('bounded original parent ancestry required')
        known={item.provenance_id:item for item in (*parents,*ancestry)}
        for parent in (*parents,*ancestry):
            if parent.context_sha256 not in by_sha:raise MapCheckpointNotReady('parent context closure missing')
            if any(identity not in known for identity in parent.parents):raise MapCheckpointNotReady('original parent DAG is incomplete')
            if PROVENANCE.unpack(parent.payload)[5]!=UUID(context.pi_host_boot).bytes:raise MapCheckpointNotReady('foreign original parent Pi host')
            scope.record_sources(parent,by_sha[parent.context_sha256])
        if pose_bracket is not None or sensor_state is not None:
            from .mapper_sensor_state import MapperSensorState,SensorFrameSnapshot,VehicleBracket
            if (type(sensor_state) not in (MapperSensorState,SensorFrameSnapshot) or type(pose_bracket) is not VehicleBracket
                    or sensor_state.original_pose_bracket(pose_bracket.pose) is not pose_bracket
                    or pose_bracket.pose.captured_us!=observation.capture_interval
                    or pose_bracket.pose.sample.context[:3]!=(context.odom_epoch,raw['clock_epoch'],scope.calibration.sha256)):
                raise MapCheckpointNotReady('actual full capture Vehicle bracket required')
            for original in pose_bracket.originals:
                evidence=sensor_state.validate_vehicle_admission(original)
                raw_key=struct.pack('<16sQQ',UUID(evidence.source_key[0]).bytes,*evidence.source_key[1:])
                matched=False
                for parent in known.values():
                    data=PROVENANCE.unpack(parent.payload)
                    if (data[2]==0 and data[1]&16 and data[6:9]==(*evidence.pi_interval,evidence.anchor.sequence)
                            and any(data[-3][index:index+32]==raw_key for index in range(0,data[3]*32,32))):matched=True
                if not matched:raise MapCheckpointNotReady('original interpolation endpoint PI_ODOM proof missing')
        _reference_vehicle(scope,reference_vehicle,admission,registration_valid_for_us,now_us)
        return cls(scope,observation,pi_interval,tuple(keys),observation_sequence,parents,contexts,clock_lease,now_us,ancestry,
            reference_vehicle=reference_vehicle,registration_valid_for_us=registration_valid_for_us)

    @classmethod
    def from_tof(cls,scope,evidence,*,admission,parents,contexts,observation_sequence,now_us,ancestry=()):
        from ..transport.sensor_samples import TofEvidence,SensorSamples,FrozenSensorSamples
        if (type(scope) is not SceneEvidenceScope or type(evidence) is not TofEvidence or type(admission) not in (SensorSamples,FrozenSensorSamples)
                or not admission.current_tof(evidence,now_us) or evidence.calibration_profile_sha256!=scope.calibration.sha256
                or evidence.odom_epoch!=scope.context.odom_epoch or evidence.stamp.captured_us is None):
            raise MapCheckpointNotReady('actual admitted ToF source/clock required')
        context=scope.context_record('TOF',sensor_id=evidence.sensor_id)
        leaf=json_bytes(context.payload,4096)['body']['tof']
        if (leaf['sensor_instance'],leaf['sensor_generation'],leaf['leaf_sha256'])!=(evidence.sensor_instance,evidence.sensor_generation,evidence.leaf_sha256):
            raise MapCheckpointNotReady('ToF reset/leaf differs from original scope')
        if (type(parents) is not tuple or not 1<=len(parents)<=4 or any(type(v) is not ProvenanceRecord for v in parents)
                or not any(v.source_mask&16 for v in parents) or type(contexts) is not tuple
                or any(type(v) is not ContextRecord for v in contexts)
                or type(ancestry) is not tuple or len(ancestry)>16384 or any(type(v) is not ProvenanceRecord for v in ancestry)):
            raise MapCheckpointNotReady('original ToF pose parent closure required')
        known={item.provenance_id:item for item in (*parents,*ancestry)};by_sha={item.sha256:item for item in contexts}
        for parent in known.values():
            if parent.context_sha256 not in by_sha or any(identity not in known for identity in parent.parents):raise MapCheckpointNotReady('ToF parent closure missing')
            if PROVENANCE.unpack(parent.payload)[5]!=UUID(scope.context.pi_host_boot).bytes:raise MapCheckpointNotReady('foreign ToF pose parent Pi host')
            scope.record_sources(parent,by_sha[parent.context_sha256])
        uint(observation_sequence,positive=True)
        return cls(scope,evidence,evidence.pi_interval,(evidence.raw_key,),observation_sequence,parents,contexts,
            evidence.clock_lease,now_us,ancestry,'TOF',evidence.sensor_id)


@dataclass(frozen=True)
class MapSnapshot:
    """동일 version capture의 원 사본. all_tiles와 D에 보내는32개 이하 copy를 구분한다."""
    job: StorageJob
    tiles: tuple
    all_tiles: tuple
    evidence: EvidenceTable
    mapper_state: bytes
    tile_references: tuple
    durable_cutoff_pi_us: int
    generation: int
    grid_revision: int
    atlas_snapshot: object=None
    atlas_owner: object=None

    @property
    def mapper_state_ref(self):return json_bytes(self.mapper_state,8192)

    def full_tiles(self):
        """rolling 본문은 원 freeze lease 안에서 잠시만 빌린다. 전체 atlas를 상주 사본으로 두지 않는다."""
        return self.all_tiles if self.atlas_owner is None else tuple(self.atlas_owner.iter_tiles(self.atlas_snapshot))


class MapCheckpointCollector:
    """한 M의 전용 내구 provenance range와 실제 셀 witness만 보존한다. allocator 대기는 없다."""

    def __init__(self,scope,grant,*,now_us):
        from .parking_bridge import WireProvenanceGrant
        if type(scope) is not SceneEvidenceScope:raise MapCheckpointNotReady('actual scene evidence scope required')
        if type(grant) is ProvenanceRange:
            job=grant.original_job;current=grant.current(job,scope.context.sha256,now_us)
        elif type(grant) is WireProvenanceGrant:
            job=grant.request.job;current=grant.current(grant.request,scope.context.sha256,now_us)
        else:raise MapCheckpointNotReady('original committed exclusive provenance grant required')
        if not current or job.context!=scope.context:raise MapCheckpointNotReady('historical range cannot issue new map provenance')
        self.scope,self.grant=scope,grant;self._next=grant.start_inclusive
        self._cells={};self._records={};self._contexts={};self._unresolved=set();self._last=None;self._grid=None
        self._rgb_refs={};self._vehicle_refs={};self._alignment_record=None;self._frame_cache=None
        self._propagation_anchor_record=None
        self._reference_geometry={}
        self._alignment_clock=None
        self._durable_tiles={};self._durable_catalog=();self._tile_revision_head=0;self._pending=None;self._retained=None
        self._draft_snapshot=None;self._generation=0
        self._durable_highwaters=(1,1);self._subsystem_state={}
        self._atlas_proof=None;self._atlas_owner=None;self._atlas_checkpoint_snapshot=None

    def reframe_atlas_proof(self,proof):
        """root가 먼저 고정한 후보 grid/window에 원 proof를 묶는다. pool/본문은 복사하지 않는다."""
        from .atlas_proof import AtlasProofWindow
        grid=self._grid
        if (type(proof) is not AtlasProofWindow or type(grid) is not EvidenceGrid
                or grid.rolling_window!=proof.window or grid.spec!=proof.window.spec):
            raise MapCheckpointNotReady('actual candidate rolling grid and matching proof window required')
        cells={};unresolved=set()
        for key,raw in proof.cells.items():
            index=proof.window.global_to_local(*key)
            if index is None:raise MapCheckpointNotReady('proof cell is outside original rolling window')
            # 원 EVIDENCE는 helper에 남긴다. LIVE가 없는 복원/eviction 셀을 현재 관측으로 올리지 않는다.
            if CELL.unpack(raw)[6] and int(grid.state.reshape(-1)[index])!=0:cells[index]=raw
            if proof.is_unresolved(*key):unresolved.add(index)
        changed=proof is not self._atlas_proof
        self._atlas_proof=proof;self._cells=cells;self._unresolved=unresolved
        if changed:self._generation+=1

    def _allocate(self,keep=()):
        if len(self._records)>=16384:self._trim(keep)
        if self._next>=self.grant.end_exclusive or len(self._records)>=16384:raise MapCheckpointNotReady('map provenance capacity exhausted')
        value=self._next;self._next+=1;return value

    def _pin_atlas_owner(self,owner):
        from ..mapping.atlas import AtlasResource
        from .mapper_atlas import MapperAtlasNotReady
        try:self._atlas_owner.retain_checkpoint_owner(self._atlas_checkpoint_snapshot,owner)
        except (AtlasResource,MapperAtlasNotReady) as error:
            raise MapCheckpointNotReady('original atlas payload owner cannot be retained') from error

    def _trim(self,keep=()):
        pending=list(keep)+list(self._rgb_refs.values())+[CELL.unpack(raw)[6] for raw in self._cells.values() if CELL.unpack(raw)[6]]
        if self._atlas_owner is not None:
            roots=self._atlas_owner.provenance_roots()
            if type(roots) is not tuple or len(roots)>16384:raise MapCheckpointNotReady('bounded original atlas proof roots required')
            for identity in roots:
                uint(identity)
                if identity:pending.append(identity)
        pending.extend(self._vehicle_refs.values())
        pending.extend(mapper_state_roots(self._subsystem_state))
        pending.extend(self._reference_geometry.values())
        for tile in self._durable_tiles.values():
            if type(tile) is not TileVersion:
                raise MapCheckpointNotReady('durable tile provenance roots unavailable for trimming')
            pending.extend(tile.provenance_ids)
        pending.extend(record.provenance_id for record in self._durable_catalog)
        if self._alignment_record is not None:pending.append(self._alignment_record[1])
        if self._propagation_anchor_record is not None:pending.append(self._propagation_anchor_record[1])
        records={}
        while pending:
            identity=pending.pop()
            if identity in records:continue
            record=self._records.get(identity)
            if record is None:raise MapCheckpointNotReady('live map parent closure missing')
            records[identity]=record;pending.extend(record.parents)
        self._records=records;self._contexts={record.context_sha256:self._contexts[record.context_sha256] for record in records.values()}

    def _record(self,frame,parents,mask,kind,raw_keys=(),pi_interval=None):
        context=self.scope.context_record('METRIC');self._contexts[context.sha256]=context
        identity=self._allocate(parents)
        self._contexts[context.sha256]=context
        if any(parent>=identity for parent in parents):raise MapCheckpointNotReady('original parents must precede derived record')
        raw=b''.join(struct.pack('<16sQQ',UUID(key[0]).bytes,key[1],key[2]) for key in raw_keys)
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),mask,kind,len(raw_keys),len(parents),
            UUID(self.scope.context.pi_host_boot).bytes,*(frame.pi_interval if pi_interval is None else pi_interval),frame.observation_sequence,
            *(parents+(0,)*(4-len(parents))),raw.ljust(128,b'\0'),0,bytes(16)))
        self._records[identity]=record;return identity

    def _require_propagation_ancestor(self,roots,identity):
        pending=list(roots);visited=set()
        while pending:
            parent=pending.pop()
            if parent in visited:continue
            record=self._records.get(parent)
            if record is None:raise MapCheckpointNotReady('original propagation anchor parent closure missing')
            visited.add(parent)
            if len(visited)>16384:raise MapCheckpointNotReady('original propagation anchor closure exceeds capacity')
            pending.extend(record.parents)
        if identity not in visited:raise MapCheckpointNotReady('original reference closure lost propagation anchor')

    def _frame_roots(self,frame,pipeline,raw_pose=None):
        alignment=None if pipeline is None else pipeline._alignment
        alignment_key=None if alignment is None else sha256(canonical(alignment.fields())).hexdigest()
        anchor=None if pipeline is None else pipeline._propagation_anchor
        anchor_key=None if anchor is None else sha256(canonical(anchor.fields())).hexdigest()
        pin=self._propagation_anchor_record
        if alignment is not None:
            if anchor_key is None:raise MapCheckpointNotReady('actual propagation anchor is missing')
            if pin is None:
                if anchor_key!=alignment_key or self._alignment_record is not None:
                    raise MapCheckpointNotReady('historical propagation anchor has no original registration pin')
            else:
                if (type(pin) is not tuple or len(pin)!=3 or type(pin[0]) is not str
                        or type(pin[1]) is not int or not 0<pin[1]<2**63 or type(pin[2]) is not str):
                    raise MapCheckpointNotReady('immutable original propagation anchor registration pin required')
                record=self._records.get(pin[1])
                if (pin[0]!=anchor_key or record is None or record.provenance_id!=pin[1]
                        or PROVENANCE.unpack(record.payload)[2]!=5 or sha256(record.payload).hexdigest()!=pin[2]):
                    raise MapCheckpointNotReady('original propagation anchor registration pin changed')
                if self._alignment_record is None or self._alignment_record[0]!=alignment_key:
                    # 기존 GEOMETRY→REGISTRATION 경로만 검산한다. 새 부모나 시각을 발급하지 않는다.
                    self._require_propagation_ancestor(self._reference_geometry.values(),pin[1])
        parents=tuple(item.provenance_id for item in frame.parents)
        for item in frame.contexts:self._contexts[item.sha256]=item
        for item in (*frame.parents,*frame.ancestry):
            if item.provenance_id in self._records and self._records[item.provenance_id]!=item:raise MapCheckpointNotReady('immutable original frame parent conflict')
            self._records[item.provenance_id]=item
        if self._frame_cache is not None and self._frame_cache[0] is frame and self._frame_cache[1]==alignment_key:
            return self._frame_cache[2]
        if len(parents)>2:
            mask=0
            for identity in parents:mask|=self._records[identity].source_mask
            interval=(min(self._records[i].pi_lower for i in parents),max(self._records[i].pi_upper for i in parents))
            parents=(self._record(frame,parents,mask,4,pi_interval=interval),)
        rgb_id=None
        if frame.source_kind=='CAMERA':
            capture=sha256(canonical(frame.observation.rgb.stamp.key)).hexdigest();rgb_id=self._rgb_refs.get(capture)
            if rgb_id is None:
                mask=1
                for identity in parents:mask|=self._records[identity].source_mask
                rgb_id=self._record(frame,parents,mask,0,(frame.raw_keys[0],));self._rgb_refs[capture]=rgb_id
            needed=set() if pipeline is None or pipeline.reference is None else {s.capture_id for s in pipeline.reference.segments}
            if len(self._rgb_refs)>128:self._rgb_refs={key:value for key,value in self._rgb_refs.items() if key in needed or key==capture}
        if alignment is not None:
            if self._alignment_record is not None and self._alignment_record[0]==alignment_key:
                parents=tuple(dict.fromkeys((*parents,self._alignment_record[1])))
            else:
                from ..storage.records import REGISTRATION
                from ..mapping.registration import transform_point,wrap_yaw
                reference=pipeline.reference
                if (rgb_id is None or reference is None or reference.revision<=0 or not reference.segments
                        or any(segment.capture_id!=capture for segment in reference.segments)
                        or frame.reference_vehicle is None or frame.registration_valid_for_us is None
                        or type(raw_pose) is not PoseEstimate or alignment.context!=raw_pose.sample.context):
                    raise MapCheckpointNotReady('original accepted registration/reference Vehicle/config evidence missing')
                if alignment.has_metadata and (alignment.observed_j_interval!=frame.observation.capture_interval
                        or alignment.registration_revision!=reference.revision or alignment.valid_for_us!=frame.registration_valid_for_us):
                    raise MapCheckpointNotReady('alignment metadata differs from original accepted frame/revision/config')
                vehicle=frame.reference_vehicle;anchor=vehicle.anchor;position=anchor.odom_pose
                x,y=transform_point(position[:2],alignment.pose);mapped=(x,y,wrap_yaw(position[2]+alignment.pose[2]))
                transform=tuple(struct.unpack('<f',struct.pack('<f',v))[0] for v in alignment.pose)
                position32=tuple(struct.unpack('<f',struct.pack('<f',v))[0] for v in position)
                mapped32=tuple(struct.unpack('<f',struct.pack('<f',v))[0] for v in mapped)
                heading=(alignment.yaw_error_rad+vehicle.pose_sample.yaw_error_rad+abs(transform[2]-alignment.pose[2])
                    +abs(position32[2]-position[2])+abs(mapped32[2]-mapped[2]))
                pos_error=(alignment.position_error_m+vehicle.pose_sample.position_error_m
                    +math.hypot(transform[0]-alignment.pose[0],transform[1]-alignment.pose[1])
                    +math.hypot(position32[0]-position[0],position32[1]-position[1])
                    +math.hypot(mapped32[0]-mapped[0],mapped32[1]-mapped[1])
                    +2*math.hypot(*position[:2])*math.sin(min(math.pi,heading)/2))
                revision=alignment.registration_revision if alignment.has_metadata else reference.revision
                validity=alignment.valid_for_us if alignment.has_metadata else frame.registration_valid_for_us
                body=REGISTRATION.pack(anchor.sequence,revision,UUID(self.scope.context.map_epoch).bytes,
                    UUID(self.scope.context.frame_epoch).bytes,UUID(self.scope.context.odom_epoch).bytes,*transform,
                    _f32(pos_error,True),_f32(heading,True),validity,
                    UUID(self.scope.context.origin_mapper_instance).bytes,*position32,*mapped32)
                actual_parents=tuple(dict.fromkeys((*parents,rgb_id)));mask=0
                anchors=tuple(dict.fromkeys(self._reference_geometry.values()))
                while len(anchors)>1:
                    combined=[]
                    for at in range(0,len(anchors),4):
                        group=anchors[at:at+4];source_mask=0
                        for identity in group:source_mask|=self._records[identity].source_mask
                        interval=(min(self._records[i].pi_lower for i in group),max(self._records[i].pi_upper for i in group))
                        combined.append(self._record(frame,group,source_mask,4,pi_interval=interval))
                    anchors=tuple(combined)
                if not anchors:raise MapCheckpointNotReady('original frozen reference provenance was not collected')
                actual_parents=tuple(dict.fromkeys((*actual_parents,*anchors)))
                for identity in actual_parents:mask|=self._records[identity].source_mask
                context=self.scope.context_record('METRIC');identity=self._allocate(actual_parents)
                record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),mask,5,0,len(actual_parents),
                    UUID(self.scope.context.pi_host_boot).bytes,*frame.pi_interval,frame.observation_sequence,
                    *(actual_parents+(0,)*(4-len(actual_parents))),body,0,bytes(16)))
                self._contexts[context.sha256]=context;self._records[identity]=record;self._alignment_record=(alignment_key,identity)
                if pin is None:self._propagation_anchor_record=(anchor_key,identity,sha256(record.payload).hexdigest())
                self._alignment_clock=_alignment_clock(frame) if alignment.has_metadata else None
                parents=tuple(dict.fromkeys((*parents,identity)))
        self._frame_cache=(frame,alignment_key,parents)
        return parents

    def vehicle_parent(self,vehicle,*,admission,now_us):
        """실제 수락 Vehicle 원본으로 PI_ODOM SENSOR parent를 같은 내구 range에서 만든다."""
        from ..transport.sensor_samples import VehicleEvidence,SensorSamples,FrozenSensorSamples
        if (type(vehicle) is not VehicleEvidence or type(admission) not in (SensorSamples,FrozenSensorSamples) or not admission.current_vehicle(vehicle,now_us)
                or admission.context.calibration.sha256!=self.scope.calibration.sha256
                or admission.context.source['odom_epoch']!=self.scope.context.odom_epoch
                or admission.context.device_session!=self.scope.context.geometry_origin_fields['device_session']):
            raise MapCheckpointNotReady('actual admitted Vehicle parent required')
        message=vehicle.message();origin=self.scope.context.geometry_origin_fields
        if (message.host_boot,message.gateway_instance,message.pi_session,message.vehicle)!=(origin['pi_host_boot'],origin['gateway_instance'],origin['pi_session'],origin['vehicle']):
            raise MapCheckpointNotReady('Vehicle parent changed original Pi/Gateway/session')
        context=self.scope.context_record('METRIC');identity=self._allocate();message=vehicle.message()
        raw=struct.pack('<16sQQ',UUID(message.device_session).bytes,message.source_sample,message.source_us)
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),16,0,1,0,
            UUID(self.scope.context.pi_host_boot).bytes,*vehicle.pi_interval,message.sequence,0,0,0,0,raw.ljust(128,b'\0'),0,bytes(16)))
        self.scope.record_sources(record,context);self._records[identity]=record;self._contexts[context.sha256]=context
        return record,context

    def vehicle_admission_parent(self,original,*,sensor_state):
        """원 수락 당시의 보간 endpoint다. historical sample을 현재 latest로 승격하지 않는다."""
        from .mapper_sensor_state import MapperSensorState,SensorFrameSnapshot
        if type(sensor_state) not in (MapperSensorState,SensorFrameSnapshot):raise MapCheckpointNotReady('original sensor owner required')
        vehicle=sensor_state.validate_vehicle_admission(original);message=vehicle.message()
        origin=self.scope.context.geometry_origin_fields
        if (sensor_state.calibration.sha256!=self.scope.calibration.sha256
                or vehicle.pose_sample.context[:3]!=(self.scope.context.odom_epoch,self.scope.raw_binding.fields['clock_epoch'],self.scope.calibration.sha256)
                or tuple(getattr(message,name) for name in ('vehicle','host_boot','gateway_instance','device_session','pi_session','odom_epoch','calibration'))
                    !=tuple(origin[name] for name in ('vehicle','pi_host_boot','gateway_instance','device_session','pi_session','odom_epoch','calibration'))):
            raise MapCheckpointNotReady('original Vehicle admission has a different geometry/Pi source')
        key=vehicle.wire_sha256;identity=self._vehicle_refs.get(key)
        context=self.scope.context_record('METRIC')
        if identity is not None:
            record=self._records[identity]
            return record,self._contexts[record.context_sha256]
        identity=self._allocate();raw=struct.pack('<16sQQ',UUID(message.device_session).bytes,message.source_sample,message.source_us)
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),16,0,1,0,
            UUID(self.scope.context.pi_host_boot).bytes,*vehicle.pi_interval,message.sequence,0,0,0,0,raw.ljust(128,b'\0'),0,bytes(16)))
        self.scope.record_sources(record,context);self._records[identity]=record;self._contexts[context.sha256]=context
        if len(self._vehicle_refs)>=256:self._vehicle_refs.pop(next(iter(self._vehicle_refs)))
        self._vehicle_refs[key]=identity
        return record,context

    def frame_observer(self,frame,*,pipeline=None):
        """frame=None도 실제 mutation을 무효화해 옛 witness가 새 상태를 가장하지 못하게 한다."""
        if frame is not None and (type(frame) is not FrameEvidence or frame.scope.context!=self.scope.context):
            raise MapCheckpointNotReady('frame source differs from current collector context')
        if pipeline is not None and type(pipeline) is not PerceptionPipeline:raise MapCheckpointNotReady('actual pipeline owner required')
        return lambda update:self._observe(frame,update,pipeline=pipeline)

    def rebind_scope(self,scope):
        """동일 M의 승인된 geometry scope만 유지한다. 과거 range/job/record bytes는 바꾸지 않는다."""
        from ..storage.geometry_scope import same_catalog_geometry
        if type(scope) is not SceneEvidenceScope or not same_catalog_geometry(self.scope.context,scope.context,'SLOT'):
            raise MapCheckpointNotReady('map collector cannot cross actual geometry/mapper/Pi origin')
        self.scope=scope;self._frame_cache=None
        self._generation+=1

    def retain_complete(self,complete,pipeline,*,selected_bootstrap):
        """actor가 받은 원 최신 COMPLETE bootstrap을 실제 LOAD 자료와 대조해 정적 baseline으로 둔다.

        arbitrary historical LOAD를 현재 tile head로 채택하지 않는다. native/source binding
        admission과 old writer quiescence는 이 함수 호출 전 기존 actor가 담당한다.
        """
        if (type(complete) is not SourceComplete or type(selected_bootstrap) is not dict
                or selected_bootstrap.get('status')!='COMPLETE' or selected_bootstrap.get('database_uuid')!=self.scope.context.database_uuid
                or selected_bootstrap.get('selected_checkpoint')!=json_bytes(complete.checkpoint_ref,2048)):
            raise MapRestoreNotReady('actual selected COMPLETE bootstrap and decoded pages required')
        if self._cells or self._pending is not None:raise MapRestoreNotReady('retained baseline requires a fresh collector')
        restore_pipeline(pipeline,complete,self.scope)
        state=json_bytes(complete.state,512*1024)
        self._retained=complete;self._grid=pipeline.grid;self._durable_tiles={(tile.tile_x,tile.tile_y):tile for tile in complete.tiles}
        self._durable_catalog=complete.evidence.catalog;self._records={p.provenance_id:p for p in complete.evidence.provenance}
        self._durable_highwaters=complete.evidence.catalog_highwaters
        self._subsystem_state={key:state[key] for key in ('lane_catalog','parking_catalog','road_basis') if key in state}
        used={p.context_sha256 for p in self._records.values()}
        self._contexts={c.sha256:c for c in complete.evidence.contexts if c.sha256 in used}
        self._reference_geometry=dict(state['reference_provenance']);self._rgb_refs={};self._vehicle_refs={};self._alignment_record=None
        self._propagation_anchor_record=None
        self._alignment_clock=None
        head=state['tile_revision_highwater'];uint(head)
        self._tile_revision_head=max(head,max((t.revision for t in complete.tiles),default=0))
        self._generation+=1
        return complete

    def accept_commit(self,request,receipt,*,now_us):
        """현재 원 receipt만 durable baseline을 전진시킨다. 새 RAM witness/age는 지우지 않는다."""
        from ..storage.contracts import CommitReceipt
        if request is not self._pending or type(receipt) is not CommitReceipt:
            raise MapCheckpointNotReady('original selected map checkpoint/receipt required')
        if not receipt.current(request.job,request.payload,self.scope.context.sha256,now_us):return False
        if self._atlas_checkpoint_snapshot is not None:
            self._atlas_owner.complete_checkpoint(self._atlas_checkpoint_snapshot)
            self._atlas_checkpoint_snapshot=None
            self._durable_tiles={reference[:2]:reference for reference in request.payload.tile_references}
        else:
            for tile in request.payload.tiles:self._durable_tiles[(tile.tile_x,tile.tile_y)]=tile
        self._durable_catalog=request.payload.evidence.catalog;self._pending=None
        self._durable_highwaters=request.payload.evidence.catalog_highwaters
        state,_=decode_mapper_state(json_bytes(request.payload.manifest,16384)['scene'],request.payload.evidence.contexts)
        self._subsystem_state={key:state[key] for key in ('lane_catalog','parking_catalog','road_basis') if key in state}
        self._draft_snapshot=None
        return True

    def retire_checkpoint(self,request):
        """원 actor가 terminal/history로 정리한 요청만 해제한다. durable refs/HWM을 발급하지 않는다."""
        if request is not self._pending:raise MapCheckpointNotReady('original pending map checkpoint required')
        if self._atlas_checkpoint_snapshot is not None:
            self._atlas_owner.retire_checkpoint(self._atlas_checkpoint_snapshot)
            self._atlas_checkpoint_snapshot=None
        self._pending=self._draft_snapshot=None

    def _copy_tiles(self,grid,now_us,include_unchanged):
        if getattr(grid,'rolling_window',None) is not None:
            from .mapper_atlas import MapperAtlas,MapperAtlasNotReady
            from ..mapping.atlas import AtlasResource
            if type(self._atlas_owner) is not MapperAtlas:
                raise MapCheckpointNotReady('rolling atlas checkpoint snapshot port is not connected')
            try:snapshot=self._atlas_owner.checkpoint_snapshot(allow_clean=True)
            except (MapperAtlasNotReady,AtlasResource) as error:
                raise MapCheckpointNotReady('original atlas checkpoint is not ready') from error
            self._atlas_checkpoint_snapshot=snapshot
            return snapshot.tiles,(),snapshot.tile_references
        snapshot=grid.snapshot(now_us,clock_id=grid.clock_id);copied=[]
        all_tiles=dict(self._durable_tiles)
        empty=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        for ty in range((grid.spec.height+63)//64 if grid.revision else 0):
            for tx in range((grid.spec.width+63)//64):
                baseline=self._durable_tiles.get((tx,ty));body=bytearray(empty*4096 if baseline is None else baseline.payload)
                if baseline is not None:
                    for index,values in enumerate(CELL.iter_unpack(baseline.payload)):
                        if values[6]:
                            fields=list(values);fields[1]|=6;CELL.pack_into(body,index*64,*fields)
                for row in range(ty*64,min(grid.spec.height,(ty+1)*64)):
                    for col in range(tx*64,min(grid.spec.width,(tx+1)*64)):
                        raw=self._cells.get(row*grid.spec.width+col)
                        if raw is None:continue
                        if snapshot['arrays']['state'][row,col]==0:
                            values=list(CELL.unpack(raw));values[1]|=4;raw=CELL.pack(*values)
                        offset=((row%64)*64+col%64)*64;body[offset:offset+64]=raw
                payload=bytes(body)
                if baseline is not None and payload==baseline.payload:
                    if include_unchanged:copied.append(baseline)
                    continue
                self._tile_revision_head=max(self._tile_revision_head+1,grid.revision)
                tile=TileVersion(self.scope.context.map_epoch,tx,ty,self._tile_revision_head,payload)
                copied.append(tile);all_tiles[(tx,ty)]=tile
        values=tuple(all_tiles[key] for key in sorted(all_tiles))
        if len(values)>128 or len(copied)>32:raise MapCheckpointNotReady('actual map/copy tile capacity exceeded')
        return tuple(copied),values,tuple(tile.reference for tile in values)

    def observe_result(self,frame,result,pipeline,*,raw_pose=None):
        """mutation이0인 실제 RGB/reference 결과도 원 raw key와 연결한다. 현재 시각을 새로 찍지 않는다."""
        from ..perception.pipeline import PerceptionResult
        if (type(frame) is not FrameEvidence or frame.source_kind!='CAMERA' or type(result) is not PerceptionResult
                or type(pipeline) is not PerceptionPipeline or result.summary['rgb_source_key']!=frame.observation.rgb.stamp.key
                or result.summary['calibration_id']!=frame.scope.calibration.sha256 or result.summary['quarantined']):
            raise MapCheckpointNotReady('actual current source pipeline result required')
        if pipeline.grid.frame_id!='odom:'+self.scope.context.odom_epoch:raise MapCheckpointNotReady('frame-local result is not a metric map')
        if not result.summary['rgb_updated']:return False
        self._grid=pipeline.grid;self._frame_roots(frame,pipeline,raw_pose);self._last=frame
        if pipeline.reference is not None:
            needed={segment.capture_id for segment in pipeline.reference.segments}
            capture=sha256(canonical(frame.observation.rgb.stamp.key)).hexdigest()
            if capture in needed and capture not in self._reference_geometry:
                parents=self._frame_roots(frame,pipeline,raw_pose);mask=1
                for identity in parents:mask|=self._records[identity].source_mask
                self._reference_geometry[capture]=self._record(frame,parents,mask,3,(frame.raw_keys[0],))
            if not needed.issubset(self._reference_geometry):raise MapCheckpointNotReady('original RGB reference source was not collected')
            self._reference_geometry={key:self._reference_geometry[key] for key in needed}
            self._rgb_refs={key:value for key,value in self._rgb_refs.items() if key in needed}
        else:
            capture=sha256(canonical(frame.observation.rgb.stamp.key)).hexdigest()
            self._rgb_refs={capture:self._rgb_refs[capture]}
        self._generation+=1
        return True

    def observe_tof(self,frame,grid,mutation,result,pose,*,raw_pose=None,pipeline=None,certificate=None,cell_bounds=None,targets=()):
        """실제 ToF fusion owner가 GridMutation과 그 원 zone 결과를 그대로 전달한다."""
        if type(frame) is not FrameEvidence or frame.source_kind!='TOF' or result.stamp is not frame.observation.stamp:
            raise MapCheckpointNotReady('original ToF result/mutation required')
        self._observe(frame,MapEvidenceUpdate(grid,mutation,frame.observation,result,raw_pose,pose,result.patches,
            certificate,cell_bounds,targets),pipeline=pipeline)

    def _observe(self,frame,update,*,pipeline=None):
        import numpy as np
        if type(update) is not MapEvidenceUpdate or type(update.mutation) is not GridMutation:
            raise MapCheckpointNotReady('actual typed cell mutation required')
        delta=update.mutation;grid=update.grid
        if grid.frame_id!='odom:'+self.scope.context.odom_epoch:return
        if self._grid is not None and self._grid is not grid:raise MapCheckpointNotReady('collector cannot silently replace its grid')
        window=getattr(grid,'rolling_window',None);proof=self._atlas_proof
        if window is not None:
            if (proof is None or proof.window!=window or grid.spec!=window.spec
                    or delta.window_offset_xy!=window.offset_xy or delta.shape!=(window.height,window.width)):
                raise MapCheckpointNotReady('mutation changed original rolling window/proof address')
        elif delta.window_offset_xy is not None or proof is not None:
            raise MapCheckpointNotReady('rolling proof cannot be applied to an unbound fixed grid')
        def unresolved(indices):
            self._unresolved.update(indices)
            if proof is not None:
                for index in indices:
                    row,col=divmod(index,delta.shape[1])
                    proof.mark_unresolved(delta.window_offset_xy[0]+col,delta.window_offset_xy[1]+row)
        self._grid=grid
        self._generation+=1
        affected=np.flatnonzero(np.frombuffer(delta.affected_mask,dtype=np.bool_)).tolist()
        written=np.flatnonzero(np.frombuffer(delta.source_written_mask,dtype=np.bool_)).tolist()
        stamp=None if frame is None else frame.observation.stamp if frame.source_kind=='TOF' else frame.observation.depth.stamp
        metric=getattr(update.depth,'metric_qualified',getattr(update.depth,'mapping_ready',False))
        full=None if frame is None else stamp.captured_us if frame.source_kind=='TOF' else frame.observation.capture_interval
        if (frame is None or update.observation is not frame.observation or update.pose is None
                or delta.stamp is not stamp or delta.stamp.captured_us is None
                or not metric or type(update.pose) is not PoseEstimate or update.pose.captured_us!=full
                or update.pose.sample.context[:3]!=(self.scope.context.odom_epoch,grid.clock_id,self.scope.calibration.sha256)
                or update.pose.sample.origin!='ODOMETRY'
                or not update.pose.bracket_us[0]<=full[0]<=full[1]<=update.pose.bracket_us[1]
                or grid.clock_id!=self.scope.raw_binding.fields['clock_epoch']):
            unresolved(affected);return
        if (type(update.raw_pose) is not PoseEstimate or pipeline is None and update.raw_pose!=update.pose
                or pipeline is not None and pipeline.grid is not grid):
            unresolved(affected);return
        unresolved(set(affected)-set(written))
        if not written:return
        if delta.kind=='FREE' and (update.certificate is None or not update.certificate.free
                or not update.certificate.source_keys or any(key!=delta.stamp.key for key in update.certificate.source_keys)):
            unresolved(written);return
        try:parents=self._frame_roots(frame,pipeline,update.raw_pose)
        except MapCheckpointNotReady:unresolved(written);return
        for item in frame.contexts:self._contexts[item.sha256]=item
        for item in (*frame.parents,*frame.ancestry):
            old=self._records.get(item.provenance_id)
            if old is not None and old!=item:raise MapCheckpointNotReady('immutable original parent conflict')
            self._records[item.provenance_id]=item
        identity=self._allocate(tuple(dict.fromkeys((*parents,*(item.provenance_id for item in frame.ancestry)))))
        if any(parent>=identity for parent in parents):raise MapCheckpointNotReady('map range must follow original parent records')
        context=self.scope.context_record('TOF',sensor_id=frame.sensor_id) if frame.source_kind=='TOF' else self.scope.context_record('METRIC')
        self._contexts[context.sha256]=context
        key=frame.raw_keys[-1];raw=struct.pack('<16sQQ',UUID(key[0]).bytes,key[1],key[2])
        mask=8 if frame.source_kind=='TOF' else 2
        for parent in parents:mask|=self._records[parent].source_mask
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),mask,1 if delta.kind=='HIT' else 2,
            1,len(parents),UUID(self.scope.context.pi_host_boot).bytes,*frame.pi_interval,frame.observation_sequence,
            *(parents+(0,)*(4-len(parents))),raw.ljust(128,b'\0'),0,bytes(16)))
        self._records[identity]=record;self.scope.record_sources(record,context)
        if delta.kind=='HIT':
            from ..perception.geometry import Interval,plane_height_bounds
            lower=math.inf;upper=-math.inf;extent=radius=0.
            for box in update.depth.hit_boxes:
                ground=plane_height_bounds(Interval(box.lower[0],box.upper[0]),Interval(box.lower[1],box.upper[1]),self.scope.calibration.plane)
                if ground is None:unresolved(written);return
                lower=min(lower,box.lower[2]-ground.hi);upper=max(upper,box.upper[2]-ground.lo)
                extent=max(extent,math.hypot(box.upper[0]-box.lower[0],box.upper[1]-box.lower[1]))
                radius=max(radius,math.hypot(max(abs(box.lower[0]),abs(box.upper[0])),max(abs(box.lower[1]),abs(box.upper[1]))))
            if not math.isfinite(lower+upper):unresolved(written);return
        else:
            # 실제 검산한 opaque core의 높이 band다. hit의 실측 높이라고 해석하지 않는다.
            targets=getattr(update,'targets',())
            if not targets:unresolved(written);return
            lower=min(t.base_height_m[0] for t in targets);upper=max(t.base_height_m[1]+t.core_height_m for t in targets);extent=0.
            radius=max(math.hypot(x,y) for x in update.cell_bounds[::2] for y in update.cell_bounds[1::2])
        original=update.raw_pose if type(update.raw_pose) is PoseEstimate else update.pose
        bound=max(update.pose.sample.position_error_m,original.sample.position_error_m)+extent+2*radius*math.sin(
            min(math.pi,max(update.pose.sample.yaw_error_rad,original.sample.yaw_error_rad))/2)
        hmin,hmax,pad=_f32(lower,False),_f32(upper,True),_f32(bound,True)
        cell=CELL.pack(100 if delta.kind=='HIT' else 0,1,mask,hmin,hmax,pad,identity,*frame.pi_interval,
            frame.observation_sequence,grid.revision,0)
        for index in written:
            if proof is not None:
                row,col=divmod(index,delta.shape[1]);x=delta.window_offset_xy[0]+col;y=delta.window_offset_xy[1]+row
                proof.write(x,y,cell);proof.clear_unresolved(x,y)
            self._cells[index]=cell;self._unresolved.discard(index)
        self._last=frame

    def snapshot(self,pipeline,job,*,now_us,catalog_evidence=None,subsystem_state=None,include_unchanged=False):
        """실제 RAM 사본만 고정한다. scene owner가 같은 사본으로 원 최종 manifest를 만들 수 있다."""
        import numpy as np
        if (type(pipeline) is not PerceptionPipeline or type(job) is not StorageJob or job.context!=self.scope.context
                or not job.current(self.scope.context.sha256,now_us) or pipeline.grid is not self._grid or self._last is None):
            raise MapCheckpointNotReady('actual current pipeline/job/source witness required')
        if self._pending is not None:
            raise MapCheckpointNotReady('one original map checkpoint is still pending')
        if self._draft_snapshot is not None:
            if self._draft_snapshot.job==job:return self._draft_snapshot
            raise MapCheckpointNotReady('one original map version capture is still pinned')
        if type(include_unchanged) is not bool:raise MapCheckpointNotReady('explicit copy policy required')
        grid=pipeline.grid
        if self._atlas_proof is not None and self._atlas_proof.unresolved:
            raise MapCheckpointNotReady('global atlas cell proof is unresolved outside or inside current ROI')
        if self._unresolved or any(index not in self._cells for index in np.flatnonzero(grid.state.reshape(-1)!=0)):
            raise MapCheckpointNotReady('unknown/untimed/blocked cell witness prevents complete checkpoint')
        for index,raw in self._cells.items():
            state=int(grid.state.reshape(-1)[index]);wire=CELL.unpack(raw)[0]
            if state not in (FREE,OCCUPIED) or wire!=(0 if state==FREE else 100):
                raise MapCheckpointNotReady('actual cell state differs from original applied witness')
        rolling=getattr(grid,'rolling_window',None) is not None
        if rolling:
            if self._atlas_owner is None:raise MapCheckpointNotReady('original rolling atlas owner required')
            self._tile_revision_head=max(self._tile_revision_head,self._atlas_owner.tile_revision_highwater)
            tiles=all_tiles=references=None
        else:tiles,all_tiles,references=self._copy_tiles(grid,now_us,include_unchanged)
        state=_pipeline_state(pipeline,self.scope.context);state['tile_revision_highwater']=self._tile_revision_head
        needed=set() if pipeline.reference is None else {segment.capture_id for segment in pipeline.reference.segments}
        if not needed.issubset(self._reference_geometry):raise MapCheckpointNotReady('original reference RGB proof was not collected')
        state['reference_provenance']={key:self._reference_geometry[key] for key in sorted(needed)}
        if pipeline._alignment is not None and self._alignment_record is None:raise MapCheckpointNotReady('original alignment provenance missing')
        state['alignment_provenance']=None if pipeline._alignment is None else self._alignment_record[1]
        if pipeline._alignment is not None and pipeline._alignment.has_metadata:
            if self._alignment_clock is None:raise MapCheckpointNotReady('original alignment clock evidence was not captured')
            state['alignment_clock']=self._alignment_clock
        state.update(self._subsystem_state)
        if subsystem_state is not None:
            if type(subsystem_state) is not dict or set(subsystem_state)-{'lane_catalog','parking_catalog','road_basis','hazard'}:
                raise MapCheckpointNotReady('unknown mapper subsystem state')
            for key,value in subsystem_state.items():
                if type(value) is not dict or value.get('profile')!={'lane_catalog':'lane-catalog-state-1',
                        'parking_catalog':'parking-catalog-state-1','road_basis':'road-basis-1','hazard':'mapper-hazard-state-1'}[key]:
                    raise MapCheckpointNotReady('unsupported original subsystem profile')
                state[key]=value
        metadata,pages=encode_mapper_state(state)
        contexts=dict(self._contexts);contexts.update((item.sha256,item) for item in pages)
        records=dict(self._records)
        base=EvidenceTable(catalog=self._durable_catalog,catalog_highwaters=self._durable_highwaters) if catalog_evidence is None else catalog_evidence
        if type(base) is not EvidenceTable:raise MapCheckpointNotReady('actual immutable catalog evidence required')
        for item in base.provenance:
            if item.provenance_id in records and records[item.provenance_id]!=item:raise MapCheckpointNotReady('catalog/map provenance conflict')
            records[item.provenance_id]=item
        contexts.update((item.sha256,item) for item in base.contexts)
        evidence=EvidenceTable(tuple(records[k] for k in sorted(records)),tuple(contexts[k] for k in sorted(contexts)),
            base.catalog,base.reservations,base.catalog_highwaters)
        cutoff=max((record.pi_upper for record in records.values()),default=self._last.pi_interval[1])
        # rolling lease는 큰 META/증거 검산이 끝난 뒤에만 획득한다. 실패한 준비가 pool을 잡지 않는다.
        if rolling:tiles,all_tiles,references=self._copy_tiles(grid,now_us,include_unchanged)
        snapshot=MapSnapshot(job,tiles,all_tiles,evidence,canonical(metadata),references,cutoff,self._generation,grid.revision,
            self._atlas_checkpoint_snapshot,self._atlas_owner if rolling else None)
        if rolling:
            try:self._pin_atlas_owner(snapshot)
            except BaseException:
                self._atlas_owner.retire_checkpoint(self._atlas_checkpoint_snapshot)
                self._atlas_checkpoint_snapshot=None
                raise
        self._draft_snapshot=snapshot
        return self._draft_snapshot

    def current_tile_references(self,pipeline,snapshot):
        """현재 owner의 실제 generation/revision이 변하면 옛 사본을 최신 참조로 반환하지 않는다."""
        if (snapshot is not self._draft_snapshot or pipeline.grid is not self._grid or self._generation!=snapshot.generation
                or pipeline.grid.revision!=snapshot.grid_revision):raise MapCheckpointNotReady('actual map changed after original version capture')
        return snapshot.tile_references

    def track_checkpoint(self,request,*,catalog_extension=False):
        """명시 catalog 확장은 원 map 사본을 exact subset으로만 보존한다. D range 검사를 대신하지 않는다."""
        snapshot=self._draft_snapshot
        if (snapshot is None or self._pending is not None or type(request) is not CheckpointRequest or request.job!=snapshot.job
                or type(catalog_extension) is not bool or request.payload.tiles!=snapshot.tiles
                or request.payload.tile_references!=snapshot.tile_references):
            raise MapCheckpointNotReady('final checkpoint changed the original map/source version capture')
        if catalog_extension:_catalog_extension(snapshot.evidence,request.payload.evidence)
        elif request.payload.evidence.bytes!=snapshot.evidence.bytes:
            raise MapCheckpointNotReady('final checkpoint changed the original map/source evidence')
        manifest=json_bytes(request.payload.manifest,16384)
        if (manifest['scene'] is None or manifest['scene'].get('mapper_state')!=snapshot.mapper_state_ref
                or manifest['durable_cutoff_pi_us']<snapshot.durable_cutoff_pi_us
                or catalog_extension and manifest['durable_cutoff_pi_us']!=snapshot.durable_cutoff_pi_us):
            raise MapCheckpointNotReady('final checkpoint lost original mapper state/source cutoff')
        if self._atlas_checkpoint_snapshot is not None:
            self._pin_atlas_owner(request)
        self._pending=request
        return self._pending

    def discard_snapshot(self,snapshot):
        if snapshot is not self._draft_snapshot or self._pending is not None:raise MapCheckpointNotReady('original unpublished version capture required')
        if self._atlas_checkpoint_snapshot is not None:
            self._atlas_owner.retire_checkpoint(self._atlas_checkpoint_snapshot)
            self._atlas_checkpoint_snapshot=None
        self._draft_snapshot=None

    def capture(self,pipeline,job,*,now_us,scene=None,catalog_evidence=None,subsystem_state=None):
        """일반 CP의 최종 원 request. SQL/fsync를 기다리거나 dirty를 clean으로 만들지 않는다."""
        if self._pending is not None and self._pending.job==job:return self._pending
        preserved={} if scene is None else dict(scene)
        if 'mapper_state' in preserved:raise MapCheckpointNotReady('mapper-state metadata cannot replace a different original snapshot')
        snapshot=self.snapshot(pipeline,job,now_us=now_us,catalog_evidence=catalog_evidence,subsystem_state=subsystem_state)
        preserved['mapper_state']=snapshot.mapper_state_ref
        try:
            payload=CheckpointPayload.build(job,snapshot.tiles,snapshot.evidence,tile_references=snapshot.tile_references,
                scene=preserved,durable_cutoff_pi_us=snapshot.durable_cutoff_pi_us)
            return self.track_checkpoint(CheckpointRequest(job,payload))
        except BaseException:
            if self._pending is None and self._draft_snapshot is snapshot:self.discard_snapshot(snapshot)
            raise


def _catalog_extension(original,expanded):
    """추가 proof/context는 실제 catalog root closure에만 속해야 한다. 원 map proof는 불변이다."""
    if type(original) is not EvidenceTable or type(expanded) is not EvidenceTable:
        raise MapCheckpointNotReady('actual bounded catalog evidence required')
    before={item.provenance_id:item for item in original.provenance};after={item.provenance_id:item for item in expanded.provenance}
    old_contexts={item.sha256:item for item in original.contexts};contexts={item.sha256:item for item in expanded.contexts}
    if (any(after.get(key)!=value for key,value in before.items()) or any(contexts.get(key)!=value for key,value in old_contexts.items())
            or expanded.reservations!=original.reservations
            or any(new<old for new,old in zip(expanded.catalog_highwaters,original.catalog_highwaters))):
        raise MapCheckpointNotReady('catalog extension changed original proof/context/reservation/HWM')
    catalog={(item.kind,item.stable_id):item for item in expanded.catalog}
    for old in original.catalog:
        new=catalog.get((old.kind,old.stable_id))
        if (new is None or new.geometry_revision<old.geometry_revision or new.occupancy_revision<old.occupancy_revision
                or (new.geometry_revision,new.occupancy_revision)==(old.geometry_revision,old.occupancy_revision) and new!=old):
            raise MapCheckpointNotReady('catalog extension deleted or rewrote an original catalog version')
    pending=[item.provenance_id for item in expanded.catalog];visited=set()
    while pending:
        identity=pending.pop()
        if identity in visited:continue
        record=after.get(identity)
        if record is None or record.context_sha256 not in contexts:raise MapCheckpointNotReady('catalog extension parent/context closure missing')
        visited.add(identity);pending.extend(record.parents)
    added=set(after)-set(before)
    if not added.issubset(visited) or set(contexts)-set(old_contexts)-{after[key].context_sha256 for key in visited}:
        raise MapCheckpointNotReady('catalog extension contains unrelated source proof/context')


def _f32(value,upper):
    import numpy as np
    if not math.isfinite(value):raise MapCheckpointNotReady('finite actual geometry bound required')
    result=np.float32(value)
    if upper and float(result)<value:result=np.nextafter(result,np.float32(math.inf))
    elif not upper and float(result)>value:result=np.nextafter(result,np.float32(-math.inf))
    if not np.isfinite(result):raise MapCheckpointNotReady('geometry exceeds CELL64 finite range')
    return float(result)


@dataclass(frozen=True,init=False)
class SourceComplete:
    """실제 모든 decoded LOAD 페이지에서만 생성한다. scene/현재 FREE 사용권은 없다."""
    checkpoint_ref: bytes
    original_job: StorageJob
    manifest: bytes
    tiles: tuple
    evidence: EvidenceTable
    state: bytes | None
    page_receipts: tuple
    original_commit: object = None

    def __new__(cls):raise MapCheckpointNotReady('SourceComplete requires actual decoded complete LOAD pages')

    def current_head(self,context_sha256,now_us):
        """마지막 실제 LOAD head의 원 기한만 검사한다. 완료 자료로 새 head/기한을 만들지 않는다."""
        request,_,_,head=self.page_receipts[-1]
        return head if head.current(request.job,context_sha256,now_us) else None

    @classmethod
    def _assembled(cls,ref,job,manifest,tiles,evidence,state,receipts,original_commit=None):
        if original_commit is not None:
            from ..storage.records import CheckpointCommitOrigin
            if type(original_commit) is not CheckpointCommitOrigin:
                raise MapCheckpointNotReady('exact original checkpoint commit descriptor required')
            original_commit.validate(job,manifest,ref)
        result=object.__new__(cls)
        for name,value in dict(checkpoint_ref=ref,original_job=job,manifest=manifest,tiles=tiles,evidence=evidence,
                state=None if state is None else canonical(state),page_receipts=receipts,
                original_commit=original_commit).items():object.__setattr__(result,name,value)
        return result


def _load_scope(context,reference,selection=None):
    """명시 BOOTSTRAP local6만 selection을 분리한다. 나머지 원 문맥은 전부 비교한다."""
    source=json_bytes(context.source_binding,2048)
    names={'storage_binding','mapper_worker','origin_mapper_instance','load_purpose','checkpoint_ref','load_selection'}
    if set(source)!=names:return 'CURRENT_MAP',context.bytes
    if (source['load_purpose']!='BOOTSTRAP_RESTORE' or source['checkpoint_ref']!=json_bytes(reference,2048)
            or source['mapper_worker']!=context.mapper_worker or source['origin_mapper_instance']!=context.origin_mapper_instance
            or context.geometry_origin is not None or selection is not None and source['load_selection']!=selection):
        raise MapCheckpointNotReady('original provisional LOAD context/selection differs')
    binding=source['storage_binding']
    if (type(binding) is not dict or set(binding)!= {'service_instance','storage_worker','storage_profile_sha256','database_uuid','db_schema_version'}
            or binding['database_uuid']!=context.database_uuid or binding['db_schema_version']!=1):
        raise MapCheckpointNotReady('original provisional storage binding differs')
    fields={name:getattr(context,name) for name in ('database_uuid','mapper_worker','origin_mapper_instance','map_epoch',
        'frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256')}
    return 'BOOTSTRAP_RESTORE',canonical(dict(profile='bootstrap-load-scope-1',storage_binding=binding,
        context=fields,checkpoint_ref=source['checkpoint_ref']))


class CheckpointPages:
    """선택한 원 ref의 최대128 tile/16384 proof/64 context를 실제 페이지 순서로 모은다."""

    def __init__(self,checkpoint_ref,current_context):
        ref=selected_checkpoint_reference(checkpoint_ref)
        if type(current_context) is not StorageContext or any(ref[name]!=getattr(current_context,field) for name,field in
                (('map_epoch','map_epoch'),('frame_epoch','frame_epoch'),('odom_epoch','odom_epoch'),('pi_host_boot','pi_host_boot'),
                 ('configuration','configuration'),('calibration','calibration'),('primitive_sha256','primitive_sha256'))):
            raise MapCheckpointNotReady('original selected ref/current restore context required')
        self.ref,self.context=checkpoint_ref,current_context;self._next=0;self._pages=[];self._tiles={};self._records={};self._contexts={}
        self._mode,self._load_scope=_load_scope(current_context,checkpoint_ref)
        self._manifest=self._original=self._catalog=self._original_commit=None;self._complete=None

    def accept(self,loaded,*,now_us):
        if self._complete is not None:raise MapCheckpointNotReady('complete source cannot consume another page')
        if (type(loaded) is not LoadedCheckpoint or loaded.checkpoint_ref!=self.ref
                or not loaded.current(loaded.request,loaded.request.job.context.sha256,now_us)):
            raise MapCheckpointNotReady('actual current decoded original LOAD required')
        selection=json_bytes(loaded.request.selection,512)
        mode,scope=_load_scope(loaded.request.job.context,self.ref,selection)
        if mode!=self._mode or scope!=self._load_scope or loaded.request.purpose!=mode:
            raise MapCheckpointNotReady('LOAD page changed original registered mapper/storage/source scope')
        if selection!={'kind':'CHECKPOINT_PAGE','cursor':self._next}:raise MapCheckpointNotReady('original complete page order/selection mismatch')
        payload=loaded.payload;refs=payload.tile_references
        selected=json_bytes(self.ref,2048)
        if (payload.intent!='CHECKPOINT' or loaded.original_job.context.database_uuid!=self.context.database_uuid
                or len(payload.manifest)!=selected['manifest_length'] or len(refs)!=selected['tile_count']
                or any(selected[name]!=getattr(loaded.original_job.context,field) for name,field in
                    (('map_epoch','map_epoch'),('frame_epoch','frame_epoch'),('odom_epoch','odom_epoch'),('pi_host_boot','pi_host_boot'),
                     ('configuration','configuration'),('calibration','calibration'),('primitive_sha256','primitive_sha256'),('origin_mapper_instance','origin_mapper_instance')))
                or tuple(t.reference for t in payload.tiles)!=refs[self._next:self._next+32]
                or self._manifest is not None and (payload.manifest!=self._manifest or loaded.original_job!=self._original
                    or payload.evidence.catalog_bytes!=self._catalog or loaded.original_commit!=self._original_commit)):
            raise MapCheckpointNotReady('page differs from selected complete manifest/catalog/original job')
        records=dict(self._records);contexts=dict(self._contexts);tiles=dict(self._tiles)
        for item in payload.evidence.provenance:
            if item.provenance_id in records and records[item.provenance_id]!=item:raise MapCheckpointNotReady('immutable page provenance conflict')
            records[item.provenance_id]=item
        for item in payload.evidence.contexts:
            if item.sha256 in contexts and contexts[item.sha256]!=item:raise MapCheckpointNotReady('immutable page context conflict')
            contexts[item.sha256]=item
        for tile in payload.tiles:
            if tile.reference in tiles:raise MapCheckpointNotReady('duplicate actual tile page')
            tiles[tile.reference]=tile
        if len(records)>16384 or len(contexts)>64 or len(tiles)>128:raise MapCheckpointNotReady('full LOAD closure exceeds actual M budget')
        roots={item.provenance_id for item in payload.evidence.catalog}
        state,_=decode_mapper_state(json_bytes(payload.manifest,16384)['scene'],payload.evidence.contexts)
        roots.update(mapper_state_roots(state))
        for tile in tiles.values():roots.update(tile.provenance_ids)
        for identity in tuple(roots):
            if identity not in records:raise MapCheckpointNotReady('actual tile/catalog proof missing')
        for record in records.values():
            if record.context_sha256 not in contexts or any(parent not in records for parent in record.parents):
                raise MapCheckpointNotReady('actual provenance/context page closure incomplete')
        for tile in payload.tiles:
            for cell in CELL.iter_unpack(tile.payload):
                if cell[6]:
                    record=records[cell[6]]
                    if cell[2]&record.source_mask!=cell[2] or cell[7:10]!=(record.pi_lower,record.pi_upper,record.observation_sequence):
                        raise MapCheckpointNotReady('loaded cell differs from actual immutable source/time')
        self._manifest,self._original,self._catalog=payload.manifest,loaded.original_job,payload.evidence.catalog_bytes
        self._original_commit=loaded.original_commit
        self._records,self._contexts,self._tiles=records,contexts,tiles
        self._next+=len(payload.tiles);self._pages.append((loaded.request,loaded.completed_us,now_us,loaded.heads))
        if self._next!=len(refs):return None
        evidence=EvidenceTable(tuple(records[k] for k in sorted(records)),tuple(contexts[k] for k in sorted(contexts)),
            payload.evidence.catalog,payload.evidence.reservations,payload.evidence.catalog_highwaters)
        state,_=decode_mapper_state(json_bytes(payload.manifest,16384)['scene'],evidence.contexts)
        self._complete=SourceComplete._assembled(self.ref,self._original,self._manifest,tuple(tiles[r] for r in refs),
            evidence,state,tuple(self._pages),self._original_commit)
        return self._complete


def restore_pipeline(pipeline,complete,current_scope):
    """완전 검증된 자료에서 정적 reference/HWM만 복원하고 현재 live grid는 UNKNOWN으로 둔다.

    반환 SourceComplete가 원 retained atlas/배열/clock을 보존한다. 새 관측 정합 전 alignment,
    motion/use권·live FREE를 복원하지 않는다. 원 META가 없는 legacy는 명시 NOT_READY다.
    reference의 old4 필드는 UNSPECIFIED로 읽으며 nonidentity map의 재정합 근거로
    승격하지 않는다. new5의 feature_policy만 원 생산자의 명시 의미로 보존한다.
    """
    from ..mapping.alignment import MapOdomAlignment
    if (type(pipeline) is not PerceptionPipeline or type(complete) is not SourceComplete or type(current_scope) is not SceneEvidenceScope
            or pipeline.calibration.sha256!=current_scope.calibration.sha256):
        raise MapCheckpointNotReady('actual complete state/approved current pipeline required')
    if complete.state is None:raise MapRestoreNotReady('storage COMPLETE has no supported original mapper state')
    already=getattr(pipeline,'_restored_checkpoint_ref',None)==complete.checkpoint_ref
    if pipeline.grid is not None and not pipeline.grid.frame_id.startswith('frame:') and not already:
        raise MapRestoreNotReady('cannot replace an existing metric map with historical data')
    state=json_bytes(complete.state,512*1024);current=current_scope.context
    fields={'profile','storage_context','calibration_id','pipeline_profile_sha256','clock_id','kind','frame_id','spec',
        'free_ttl_us','occupied_ttl_us','independent_view_m','revision','applied_hits','applied_free','arrays','hit_views','gates','alignment','reference',
        'reference_provenance','alignment_provenance'}
    fields.add('map_from_odom_identity')
    fields.add('tile_revision_highwater')
    if type(state) is not dict or set(state)-{'lane_catalog','parking_catalog','road_basis','alignment_clock','atlas','hazard'}!=fields:raise MapCheckpointNotReady('unsupported exact mapper pipeline state')
    context=StorageContext.from_fields(state['storage_context'])
    if (state['profile']!='mapper-pipeline-state-1' or context!=complete.original_job.context
            or any(getattr(context,name)!=getattr(current,name) for name in ('database_uuid','map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256'))
            or context.geometry_origin!=current.geometry_origin or state['calibration_id']!=pipeline.calibration.sha256
            or state['kind']!='LIVE'):
        raise MapCheckpointNotReady('historical geometry/Pi/calibration context cannot be rebased')
    if state['pipeline_profile_sha256']!=sha256(canonical(asdict(pipeline.profile))).hexdigest():
        raise MapCheckpointNotReady('original accepted pipeline profile changed')
    uint(state['tile_revision_highwater'])
    if any(tile.revision>state['tile_revision_highwater'] for tile in complete.tiles):raise MapRestoreNotReady('original tile head regressed below manifest versions')
    by_id={v.provenance_id:v for v in complete.evidence.provenance};contexts={v.sha256:v for v in complete.evidence.contexts}
    pending=list(mapper_state_roots(state));visited=set()
    for tile in complete.tiles:pending.extend(tile.provenance_ids)
    while pending:
        identity=pending.pop()
        if identity in visited:continue
        if identity not in by_id:raise MapRestoreNotReady('original map provenance closure missing')
        record=by_id[identity];visited.add(identity);pending.extend(record.parents)
        actual,_=current_scope.record_sources(record,contexts[record.context_sha256])
        if PROVENANCE.unpack(record.payload)[5]!=UUID(current.pi_host_boot).bytes:raise MapRestoreNotReady('foreign original map Pi host')
        for parent in record.parents:actual|=by_id[parent].source_mask
        if record.source_mask&actual!=record.source_mask:raise MapRestoreNotReady('original map source mask lacks actual raw/parent witness')
    spec=dict(state['spec']);spec['origin_xy']=tuple(spec['origin_xy']);spec=GridSpec(**spec)
    window=None
    if 'atlas' in state:
        from ..mapping.map_lattice import MapLattice,read_atlas_window
        policy=getattr(pipeline,'_rolling_policy',None)
        if policy is None:raise MapRestoreNotReady('rolling checkpoint requires actual configured rolling policy')
        try:window=read_atlas_window(state['atlas'],spec,policy=policy)
        except ValueError as error:raise MapRestoreNotReady('original atlas window/policy metadata differs') from error
        initial=pipeline.profile.grid
        if (window.lattice!=MapLattice.from_spec(initial) or (spec.width,spec.height)!=(initial.width,initial.height)):
            raise MapRestoreNotReady('original atlas anchor/resolution/shape differs from initial profile')
    if (window is None and spec!=pipeline.profile.grid) or state['frame_id']!='odom:'+current.odom_epoch:
        raise MapCheckpointNotReady('original map spec/frame differs from active pipeline')
    shape=(spec.height,spec.width)
    if set(state['arrays'])!={'state','observed_us','time_known','support','permission','conflict','unordered_hit','hit_available_us','hit_origin'}:
        raise MapCheckpointNotReady('unknown original mapper array')
    for name,dtype,dims in (('state','u1',shape),('observed_us','<u8',shape),('time_known','?',shape),
            ('support','?',shape),('permission','?',shape),('conflict','?',shape),('unordered_hit','?',shape),
            ('hit_available_us','<u8',shape),('hit_origin','<f8',(*shape,2))):
        array=_array(state['arrays'][name],dtype,dims)
        if name=='state' and (array>2).any():raise MapCheckpointNotReady('invalid original grid state')
        if name=='hit_origin' and not __import__('numpy').isfinite(array).all():raise MapCheckpointNotReady('invalid original hit origin')
    views=state['hit_views']
    if (type(views) is not dict or set(views)!= {'labels','codes'} or type(views['labels']) is not list
            or len(views['labels'])>64 or any(type(v) is not str or not 1<=len(v)<=128 for v in views['labels'])
            or len(set(views['labels']))!=len(views['labels'])
            or (_array(views['codes'],'u1',shape)>len(views['labels'])).any()):
        raise MapCheckpointNotReady('original bounded hit view state mismatch')
    gates={name:_gate(state['gates'][name],current_scope.raw_binding) for name in ('rgb_gate','depth_gate','nn_gate')}
    reference=None
    if state['reference'] is not None:
        ref=state['reference'];old_fields={'revision','calibration_id','segments','shared_error_m'}
        if (type(ref) is not dict or set(ref) not in (old_fields,old_fields|{'feature_policy'})
                or type(ref['segments']) is not list or len(ref['segments'])>64
                or any(type(v) is not dict or set(v)!={'a','b','error_m','component','capture_id'} for v in ref['segments'])):
            raise MapCheckpointNotReady('unsupported exact original reference fields')
        try:
            reference=FrozenReference(ref['revision'],ref['calibration_id'],
                tuple(SegmentFeature(tuple(v['a']),tuple(v['b']),v['error_m'],v['component'],v['capture_id']) for v in ref['segments']),
                ref['shared_error_m'],ref.get('feature_policy','UNSPECIFIED'))
        except (TypeError,ValueError) as error:
            raise MapCheckpointNotReady('invalid original reference feature metadata') from error
        if reference.calibration_id!=pipeline.calibration.sha256:raise MapCheckpointNotReady('original reference calibration mismatch')
        if {segment.capture_id for segment in reference.segments}!=set(state['reference_provenance']):
            raise MapRestoreNotReady('original reference geometry/source roots differ')
        for capture,identity in state['reference_provenance'].items():
            record=by_id[identity];data=PROVENANCE.unpack(record.payload);body=json_bytes(contexts[record.context_sha256].payload,4096)['body']
            raw=body['raw_binding'];matched=False
            for index in range(data[3]):
                instance,sequence,_=struct.unpack_from('<16sQQ',data[-3],index*32)
                if instance==UUID(raw['stream_instances']['rgb_video']).bytes:
                    key=(raw['acquisition_worker'],str(UUID(bytes=instance)),raw['clock_epoch'],sequence)
                    matched|=sha256(canonical(key)).hexdigest()==capture
            if not matched:raise MapRestoreNotReady('reference hash has no actual original RGB source')
    if state['alignment'] is not None:
        old=MapOdomAlignment.from_fields(state['alignment'])
        if (old.context[:3]!=(current.odom_epoch,state['clock_id'],pipeline.calibration.sha256) or len(old.context)!=4
                or len(old.pose)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in (*old.pose,old.position_error_m,old.yaw_error_rad))
                or old.position_error_m<0 or old.yaw_error_rad<0):raise MapCheckpointNotReady('old alignment context mismatch')
        from ..storage.records import REGISTRATION
        identity=state['alignment_provenance']
        if identity is None or identity not in by_id or PROVENANCE.unpack(by_id[identity].payload)[2]!=5:
            raise MapRestoreNotReady('original accepted alignment provenance missing')
        registration=REGISTRATION.unpack(PROVENANCE.unpack(by_id[identity].payload)[-3])
        transform=tuple(struct.unpack('<f',struct.pack('<f',v))[0] for v in old.pose)
        if registration[5:8]!=transform or registration[8]<old.position_error_m or registration[9]<old.yaw_error_rad:
            raise MapRestoreNotReady('stored alignment differs from its original accepted registration')
        if old.has_metadata:
            if registration[1]!=old.registration_revision or registration[10]!=old.valid_for_us:
                raise MapRestoreNotReady('original registration revision/validity differs from metadata')
            _verify_alignment_clock(state.get('alignment_clock'),old,by_id[identity],context)
        elif 'alignment_clock' in state:
            raise MapRestoreNotReady('legacy alignment cannot gain a new clock/time/drift witness')
    elif 'alignment_clock' in state:
        raise MapRestoreNotReady('clock witness has no original accepted alignment')
    if type(state['map_from_odom_identity']) is not bool:raise MapRestoreNotReady('explicit original map coordinate relationship missing')
    identity=state['map_from_odom_identity'] and state['alignment'] is None
    if not identity and (reference is None or not reference.segments):
        raise MapRestoreNotReady('storage COMPLETE has no reference for actual nonidentity map relocalization')
    if not identity and reference.feature_policy!='PHYSICAL_STRIP_ENDPOINTS_V1':
        raise MapRestoreNotReady('original nonidentity reference endpoint policy is unspecified')
    if already:
        if pipeline.grid.clock_id!=current_scope.raw_binding.fields['clock_epoch']:
            raise MapRestoreNotReady('current restored source clock changed')
        if window is not None and pipeline.grid.rolling_window!=window:
            raise MapRestoreNotReady('current restored atlas window changed')
        return complete
    grid=EvidenceGrid(spec,frame_id=state['frame_id'],calibration_id=pipeline.calibration.sha256,
        clock_id=current_scope.raw_binding.fields['clock_epoch'],kind='LIVE',free_ttl_us=pipeline.profile.free_ttl_us,
        occupied_ttl_us=state['occupied_ttl_us'],independent_view_m=state['independent_view_m'])
    # 원 revision은 보존하지만 관측 시각을 새 J now로 발급하지 않는다. 현재 state는 전부 UNKNOWN이다.
    for name in ('revision','applied_hits','applied_free'):uint(state[name])
    grid.revision=state['revision'];grid.applied_hits=state['applied_hits'];grid.applied_free=state['applied_free']
    if window is not None:
        from ..mapping.rolling_grid import configure_grid
        configure_grid(grid,window)
    pipeline.grid=grid;pipeline.reference=reference;pipeline._alignment=None;pipeline._propagation_anchor=None;pipeline._free_scheduler=None
    pipeline._restore_pending=not identity
    pipeline._context=('LIVE',pipeline.calibration.sha256,grid.clock_id,grid.frame_id)
    pipeline._restored_checkpoint_ref=complete.checkpoint_ref
    for name,gate in gates.items():setattr(pipeline,name,gate)
    return complete
