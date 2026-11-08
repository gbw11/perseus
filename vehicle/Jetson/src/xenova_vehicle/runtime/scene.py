"""Mapper의 실제 조사/동결/복원 사실. Pi scene 사용권이나 이동 허가는 발급하지 않는다.

원 sensor/pose/version 사본과 D transaction receipt를 함께 검사한다. 호출자는 실제
등록 source, 승인 profile, 현재 hazard revision을 공급한다. 이 값 검사는 센서 물리
수락이나 SQLite 실행을 대신하지 않는다. 지도 자체는 소유하지 않아 철회 때 지우지 않는다.
"""

from dataclasses import dataclass
from hashlib import sha256
import math
import struct
from uuid import UUID

from ..mapping.odometry import PoseEstimate, PoseSample
from ..mapping.alignment import MapOdomAlignment
from ..mapping.registration import RegistrationResult
from ..perception.inputs import FrameStamp
from ..storage.contracts import (StorageContext, StorageJob, CommitReceipt, canonical,
                                json_bytes, uint, digest)
from ..storage.port import CheckpointRequest
from ..storage.records import (CheckpointPayload, LoadedCheckpoint, EvidenceTable,
                               TileVersion, ContextRecord, CELL, PROVENANCE, REGISTRATION)
from ..storage.geometry_scope import PiGeometryOrigin, CatalogGeometryScope, same_catalog_geometry
from ..perception.calibration import CalibrationProfile
from .raw_binding import RawBinding


SURVEY_US = 180_000_000
SCENE_US = 14_400_000_000
OPERATOR_US = 10_000_000
SOURCE_KEYS = frozenset(('vehicle', 'device', 'mapper_boot', 'mapper_instance', 'clock_epoch',
    'map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive'))


class SceneError(ValueError):
    """원 scene/source/관측/내구 사실이 현재 작업과 맞지 않는다."""


def _codec():
    from xenova_drive_world import schema, encode, decode
    return schema, encode, decode


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise SceneError('nonnegative finite scene bound required')
    return value


@dataclass(frozen=True)
class SurveyProfile:
    """설치에서 승인하는 값. station 기준이나 sensor 존재를 operator가 바꾸지 않는다."""
    coverage_profile_sha256: str
    required_sensor_mask: int
    atlas_cell_capacity: int
    station_window_us: int
    station_distance_m: float
    station_motion_m: float
    station_yaw_rad: float
    max_pose_gap_us: int
    max_anchor_position_m: float
    max_anchor_heading_rad: float
    observation_max_age_us: int
    input_kind: str

    def __post_init__(self):
        digest(self.coverage_profile_sha256)
        uint(self.required_sensor_mask, 15, positive=True)
        for name in ('atlas_cell_capacity', 'station_window_us', 'max_pose_gap_us', 'observation_max_age_us'):
            uint(getattr(self, name), positive=True)
        for name in ('station_distance_m', 'station_motion_m', 'station_yaw_rad',
                     'max_anchor_position_m', 'max_anchor_heading_rad'):
            _number(getattr(self, name))
        if self.input_kind not in ('LIVE', 'SYNTHETIC') or not self.station_distance_m:
            raise SceneError('explicit source kind/station separation required')


@dataclass(frozen=True)
class SurveyObservation:
    """원 capture 구간과 같은 시각/보정의 실제 pose·정합 결과. READY bool은 없다."""
    stamp: FrameStamp
    pose: PoseEstimate
    registration: RegistrationResult
    world_sequence: int
    hazard_revision: int
    input_kind: str
    raw_pose: PoseEstimate
    accepted_alignment: MapOdomAlignment

    def __post_init__(self):
        if (type(self.stamp) is not FrameStamp or type(self.pose) is not PoseEstimate
                or type(self.registration) is not RegistrationResult
                or type(self.raw_pose) is not PoseEstimate
                or type(self.accepted_alignment) is not MapOdomAlignment):
            raise SceneError('exact original capture/pose/registration required')
        uint(self.world_sequence, positive=True); uint(self.hazard_revision, positive=True)
        if self.input_kind not in ('LIVE', 'SYNTHETIC'):
            raise SceneError('replay is not a new survey observation')


@dataclass(frozen=True)
class FrozenSurvey:
    request: CheckpointRequest
    summary: bytes
    hazard_revision: int
    grant_revision: int
    receipt: CommitReceipt | None = None

    @property
    def checkpoint_sha256(self):return self.request.payload.sha256

    @property
    def manifest(self):return self.request.payload.manifest


@dataclass(frozen=True)
class RestoredSurvey:
    """원 전체 CP와 실제 LOAD material을 함께 보존한다. 페이지를 원 요청으로 재포장하지 않는다."""
    complete: 'SourceComplete'
    summary: bytes
    hazard_revision: int
    grant_revision: int

    @property
    def receipt(self):return self.complete.original_commit.receipt

    @property
    def checkpoint_sha256(self):return self.complete.original_commit.payload_sha256(self.complete.manifest)

    @property
    def manifest(self):return self.complete.manifest


@dataclass(frozen=True,init=False)
class SceneEvidenceScope:
    """실제 승인 asset/origin과 context bytes를 대조하는 유한 의미 소비자.

    source owner가 가진 원 RawBinding/CalibrationProfile/PiGeometryOrigin을 받는다.
    tof_sources는 (외부 leaf 이름, actual TofLeaf, 실제 sensor_instance, generation)다.
    historical_scopes는 caller가 명시적으로 선택한 원 scope만 허용한다. 단순 bool이나
    context SHA만으로 다른 source를 승인하지 않는다. 알려지지 않은 옛 method body는
    그대로 NOT_READY이며 기존 ParkingBridge body도 자동 승인하지 않는다.
    """
    context: StorageContext
    raw_binding: RawBinding
    calibration: CalibrationProfile
    method_manifest_sha256: str
    _tof: tuple
    _historical: tuple
    _parking_methods: object

    def __init__(self,context,raw_binding,calibration,method_manifest_sha256,*,tof_sources=(),historical_scopes=()):
        if (type(context) is not StorageContext or type(raw_binding) is not RawBinding
                or type(calibration) is not CalibrationProfile or context.geometry_origin is None):
            raise SceneError('EVIDENCE_SCOPE_NOT_READY: actual raw/calibration/Pi origin required')
        digest(method_manifest_sha256)
        geometry=CatalogGeometryScope.from_context(context,'SLOT')
        if (geometry.fields['raw_binding_sha256']!=raw_binding.sha256
                or raw_binding.fields['calibration_bundle']!=calibration.sha256
                or calibration.vehicle_bundle_sha256!=context.calibration or not calibration.free_qualified):
            raise SceneError('approved raw/local/vehicle calibration relation differs')
        if (type(tof_sources) is not tuple or len(tof_sources)>2
                or type(historical_scopes) is not tuple or len(historical_scopes)>64
                or any(type(item) is not SceneEvidenceScope for item in historical_scopes)):
            raise SceneError('bounded actual source scope inventory required')
        from ..perception.tof import TofLeaf
        external=calibration.leaf_hashes['external']; sensors=set()
        for item in tof_sources:
            if type(item) is not tuple or len(item)!=4: raise SceneError('exact ToF source inventory required')
            name,leaf,instance,generation=item
            if (type(name) is not str or type(leaf) is not TofLeaf or external.get(name)!=leaf.sha256
                    or leaf.sensor_id in sensors or not all((leaf.extrinsics_qualified,leaf.first_surface_qualified,
                                                           leaf.coverage_qualified,leaf.time_qualified))):
                raise SceneError('actual approved ToF leaf/configuration required')
            if type(instance) is not str or str(UUID(instance))!=instance or not UUID(instance).int:
                raise SceneError('actual sensor incarnation required')
            uint(generation,positive=True); sensors.add(leaf.sensor_id)
        for old in historical_scopes:
            if (old.calibration.sha256!=calibration.sha256 or old.method_manifest_sha256!=method_manifest_sha256
                    or old.context.geometry_origin!=context.geometry_origin or any(getattr(old.context,name)!=getattr(context,name)
                        for name in ('database_uuid','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive_sha256'))
                    or any(old.raw_binding.fields[name]!=raw_binding.fields[name] for name in
                        ('device_id','model_id','parser_id','graph_profile_sha256','calibration_bundle','format_table_sha256'))):
                raise SceneError('historical source geometry/asset/Pi session differs')
        for name,value in dict(context=context,raw_binding=raw_binding,calibration=calibration,
                method_manifest_sha256=method_manifest_sha256,_tof=tof_sources,_historical=historical_scopes,
                _parking_methods=None).items():
            object.__setattr__(self,name,value)

    def with_parking_method(self,actual_method):
        """실제 Bridge가 쓰는 원 method를 고정한다. hash만으로 과거 알고리즘을 승인하지 않는다."""
        from copy import copy
        from .parking_scene import validate_parking_method
        try:approved=validate_parking_method(actual_method,(self,*self._historical))
        except ValueError as error:raise SceneError(str(error)) from error
        result=copy(self);object.__setattr__(result,'_parking_methods',approved)
        return result

    def context_record(self,kind,*,sensor_id=None):
        """원 source collector용 local context. wire나 측정/사용권 승인 API가 아니다."""
        if kind not in ('RAW','METRIC','TOF'): raise SceneError('unsupported scene evidence kind')
        leaf_data=None
        if kind=='TOF':
            selected=next((item for item in self._tof if item[1].sensor_id==sensor_id),None)
            if selected is None: raise SceneError('actual sensor source has not been collected/approved')
            name,leaf,instance,generation=selected
            leaf_data=dict(name=name,sensor_id=leaf.sensor_id,sensor_instance=instance,sensor_generation=generation,
                leaf_sha256=leaf.sha256,sensor_config_revision=leaf.sensor_config_revision,
                driver_profile_sha256=leaf.driver_profile_sha256,distance_convention=leaf.distance_convention)
        elif sensor_id is not None: raise SceneError('camera/metric context has no ToF sensor')
        return ContextRecord(canonical(dict(kind=kind,body=dict(profile='scene-source-context-1',
            storage_context=self.context.fields,raw_binding=self.raw_binding.fields,
            calibration_profile_sha256=self.calibration.sha256,vehicle_bundle_sha256=self.context.calibration,
            method_manifest_sha256=self.method_manifest_sha256,coordinate='MAP_X_FORWARD_Y_LEFT_REAR_AXLE',
            input_kind=self.calibration.kind,tof=leaf_data))))

    def _record_scope(self,context_record,*,record=None,evidence=None):
        value=json_bytes(context_record.payload,4096); body=value['body']
        if body.get('profile') in ('parking-method-1','parking-originals-1'):
            from .parking_scene import record_sources
            try:checked=record_sources(record,context_record,evidence=evidence,
                scopes=(self,*self._historical),approved_methods=self._parking_methods)
            except ValueError as error:raise SceneError(str(error)) from error
            return checked.scope,value
        expected={'profile','storage_context','raw_binding','calibration_profile_sha256','vehicle_bundle_sha256',
                  'method_manifest_sha256','coordinate','input_kind','tof'}
        if set(body)!=expected or body['profile']!='scene-source-context-1':
            raise SceneError('EVIDENCE_CONTEXT_NOT_READY: unknown RAW/METRIC/TOF semantic profile')
        if body['tof'] is not None and type(body['tof']) is not dict:
            raise SceneError('exact original ToF source descriptor required')
        original=StorageContext.from_fields(body['storage_context']); raw=RawBinding(body['raw_binding'])
        for scope in (self,*self._historical):
            if raw.sha256!=scope.raw_binding.sha256 or not same_catalog_geometry(original,scope.context,'SLOT'): continue
            sensor=None if body['tof'] is None else body['tof'].get('sensor_id')
            try:prototype=json_bytes(scope.context_record(value['kind'],sensor_id=sensor).payload,4096)
            except SceneError:continue
            # scene-only context/revision 교체는 geometry scope에서 이미 비교했다.
            prototype['body']['storage_context']=body['storage_context']
            if prototype!=value: continue
            return scope,value
        raise SceneError('original raw/geometry origin/asset/ToF leaf is outside approved source scopes')

    def complete_restore_head(self,complete,*,now_us):
        """완성 사본과 현재 등록 M의 관계를 검사하고 마지막 원 request/head를 반환한다.

        BOOTSTRAP의 provisional SHA를 현재 publication SHA로 바꾸지 않는다. 원
        각 page의 수락 사실과 마지막 원 기한을 검사하며 scene/clock/이동 승인은 없다.
        """
        from .map_checkpoint import SourceComplete
        from ..storage.contracts import LoadRequest,CatalogHeadSnapshot,selected_checkpoint_reference
        uint(now_us)
        if (type(complete) is not SourceComplete or type(complete.evidence) is not EvidenceTable
                or type(complete.original_job) is not StorageJob):
            raise SceneError('actual complete decoded page source required')
        reference=selected_checkpoint_reference(complete.checkpoint_ref)
        manifest=json_bytes(complete.manifest,16384);old=complete.original_job.context;current=self.context
        fields=('database_uuid','map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256')
        if (complete.original_job.job_id!=reference['checkpoint_job_id']
                or len(complete.manifest)!=reference['manifest_length']
                or sha256(complete.manifest).hexdigest()!=reference['manifest_sha256']
                or manifest.get('profile')!='xenova-checkpoint-1' or manifest.get('intent')!='CHECKPOINT'
                or manifest.get('context_sha256')!=old.sha256
                or manifest.get('catalog_sha256')!=sha256(complete.evidence.catalog_bytes).hexdigest()
                or any(getattr(old,name)!=getattr(current,name) for name in fields)
                or old.geometry_origin is None or old.geometry_origin!=current.geometry_origin
                or reference['origin_mapper_instance']!=old.origin_mapper_instance
                or any(reference[name]!=getattr(old,name) for name in fields if name!='database_uuid')):
            raise SceneError('selected complete DB/map/Pi/asset context differs from current scope')
        refs=manifest.get('tiles')
        if (type(refs) is not list or len(refs)!=reference['tile_count'] or len(refs)>128
                or type(complete.tiles) is not tuple or any(type(tile) is not TileVersion for tile in complete.tiles)
                or tuple(tile.reference for tile in complete.tiles)!=tuple(tuple(item) for item in refs)):
            raise SceneError('complete source is missing original ordered tiles')
        from ..storage.mapper_state import decode_mapper_state
        state,_=decode_mapper_state(manifest.get('scene'),complete.evidence.contexts)
        if complete.state!=(None if state is None else canonical(state)):
            raise SceneError('complete mapper state differs from referenced original pages')
        records={r.provenance_id:r for r in complete.evidence.provenance}
        contexts={c.sha256:c for c in complete.evidence.contexts}
        for record in records.values():
            if (record.context_sha256 not in contexts or any(p not in records for p in record.parents)
                    or PROVENANCE.unpack(record.payload)[5]!=UUID(current.pi_host_boot).bytes):
                raise SceneError('complete original provenance/context/Pi closure missing')
        receipts=complete.page_receipts;count=max(1,(len(refs)+31)//32)
        if type(receipts) is not tuple or len(receipts)!=count or count>4:
            raise SceneError('actual complete page receipts are missing')
        normalized=None;previous=0;seen=set();last_heads=None
        publication=json_bytes(current.source_binding,2048)
        if publication.get('service_instance')!=self.raw_binding.fields['service_instance']:
            raise SceneError('current publication differs from actual registered service')
        for index,receipt in enumerate(receipts):
            if type(receipt) is not tuple or len(receipt)!=4:raise SceneError('exact original page receipt required')
            request,completed,accepted,head=receipt
            if type(request) is not LoadRequest or type(head) is not CatalogHeadSnapshot:
                raise SceneError('actual original LOAD request/head required')
            uint(completed);uint(accepted)
            context=request.job.context;selection=json_bytes(request.selection,512)
            if (request.checkpoint_ref!=complete.checkpoint_ref or request.job.job_id in seen
                    or selection!={'kind':'CHECKPOINT_PAGE','cursor':index*32}
                    or (context.mapper_worker,context.origin_mapper_instance)!=(current.mapper_worker,current.origin_mapper_instance)
                    or any(getattr(context,name)!=getattr(current,name) for name in fields)
                    or not request.job.issued_us<=completed<=accepted<=now_us or accepted<previous
                    or not head.current(request.job,context.sha256,accepted)):
                raise SceneError('page receipt changed original selected source/M/clock/lifetime')
            if request.purpose=='CURRENT_MAP':
                if context!=current:raise SceneError('CURRENT_MAP requires original full publication context')
                scope=('CURRENT_MAP',context.bytes)
            elif request.purpose=='BOOTSTRAP_RESTORE':
                source=json_bytes(context.source_binding,2048)
                names={'storage_binding','mapper_worker','origin_mapper_instance','load_purpose','checkpoint_ref','load_selection'}
                if (set(source)!=names or source['load_purpose']!='BOOTSTRAP_RESTORE' or context.geometry_origin is not None
                        or source['mapper_worker']!=current.mapper_worker or source['origin_mapper_instance']!=current.origin_mapper_instance
                        or source['checkpoint_ref']!=reference or source['load_selection']!=selection):
                    raise SceneError('original provisional page context changed')
                binding=source['storage_binding']
                if (type(binding) is not dict or set(binding)!={'service_instance','storage_worker','storage_profile_sha256','database_uuid','db_schema_version'}
                        or binding['service_instance']!=publication['service_instance'] or binding['database_uuid']!=current.database_uuid
                        or type(binding['db_schema_version']) is not int or binding['db_schema_version']!=1):
                    raise SceneError('original provisional storage binding changed')
                scope=('BOOTSTRAP_RESTORE',canonical(binding))
            else:raise SceneError('unsupported original restore purpose')
            if normalized is not None and scope!=normalized:raise SceneError('page source scope changed')
            if last_heads is not None and any(a[1]<b[1] or a[2]<b[2] for a,b in zip(head.heads,last_heads)):
                raise SceneError('actual durable catalog heads regressed across pages')
            normalized=scope;last_heads=head.heads;previous=accepted;seen.add(request.job.job_id)
        request,_,_,head=receipts[-1]
        if not head.current(request.job,request.job.context.sha256,now_us):
            raise SceneError('last original restore request/head expired')
        for record in complete.evidence.catalog:
            if record.stable_id>=dict((kind,hwm) for kind,hwm,_ in head.heads)[record.kind]:
                raise SceneError('actual final head is below original issued catalog IDs')
        return request,head

    def with_complete_history(self,complete,*,now_us):
        """실제 참조된 source 문맥만 역사 의미로 수락한다. 현재 scene/metric 권위는 아니다."""
        self.complete_restore_head(complete,now_us=now_us)
        by_sha={item.sha256:item for item in complete.evidence.contexts}
        referenced={record.context_sha256 for record in complete.evidence.provenance}
        historical=list(self._historical);keys={(item.context.bytes,item.raw_binding.sha256,canonical(item._tof_fields())) for item in historical}
        for key in sorted(referenced):
            record=by_sha[key];value=json_bytes(record.payload,4096);body=value['body']
            if body.get('profile')!='scene-source-context-1':continue
            try:self._record_scope(record)
            except SceneError:pass
            else:continue
            expected={'profile','storage_context','raw_binding','calibration_profile_sha256','vehicle_bundle_sha256',
                'method_manifest_sha256','coordinate','input_kind','tof'}
            if (set(body)!=expected or body['calibration_profile_sha256']!=self.calibration.sha256
                    or body['vehicle_bundle_sha256']!=self.context.calibration
                    or body['method_manifest_sha256']!=self.method_manifest_sha256
                    or body['input_kind']!=self.calibration.kind or body['coordinate']!='MAP_X_FORWARD_Y_LEFT_REAR_AXLE'):
                raise SceneError('historical source method/local calibration is not approved')
            context=StorageContext.from_fields(body['storage_context']);raw=RawBinding(body['raw_binding']);tof=()
            if body['tof'] is not None:
                stored=body['tof']
                if type(stored) is not dict or set(stored)!={'name','sensor_id','sensor_instance','sensor_generation',
                        'leaf_sha256','sensor_config_revision','driver_profile_sha256','distance_convention'}:
                    raise SceneError('exact historical ToF source descriptor required')
                selected=next((item for item in self._tof if item[0]==stored['name'] and item[1].sensor_id==stored['sensor_id']),None)
                if selected is None:raise SceneError('actual approved ToF leaf is unavailable')
                name,leaf,_,_=selected
                if (stored['leaf_sha256'],stored['sensor_config_revision'],stored['driver_profile_sha256'],stored['distance_convention'])!=(
                        leaf.sha256,leaf.sensor_config_revision,leaf.driver_profile_sha256,leaf.distance_convention):
                    raise SceneError('historical ToF leaf/configuration differs from approved asset')
                tof=((name,leaf,stored['sensor_instance'],stored['sensor_generation']),)
            old=SceneEvidenceScope(context,raw,self.calibration,self.method_manifest_sha256,tof_sources=tof)
            # 기존 constructor가 실제 origin/asset 호환성을 모두 검사한다.
            SceneEvidenceScope(self.context,self.raw_binding,self.calibration,self.method_manifest_sha256,
                tof_sources=self._tof,historical_scopes=(old,))
            prototype=json_bytes(old.context_record(value['kind'],sensor_id=None if not tof else tof[0][1].sensor_id).payload,4096)
            if prototype!=value:raise SceneError('historical source context is not its original exact grammar')
            marker=(old.context.bytes,old.raw_binding.sha256,canonical(old._tof_fields()))
            if marker not in keys:
                if len(historical)>=64:raise SceneError('historical source scope capacity exceeded')
                historical.append(old);keys.add(marker)
        result=SceneEvidenceScope(self.context,self.raw_binding,self.calibration,self.method_manifest_sha256,
            tof_sources=self._tof,historical_scopes=tuple(historical))
        if self._parking_methods is not None:
            object.__setattr__(result,'_parking_methods',self._parking_methods)
        for record in complete.evidence.provenance:
            context=by_sha[record.context_sha256]
            if json_bytes(context.payload,4096)['body'].get('profile')=='scene-source-context-1':
                result.record_sources(record,context)
        return result

    def _tof_fields(self):
        return tuple((name,leaf.sha256,instance,generation) for name,leaf,instance,generation in self._tof)

    def record_sources(self,record,context_record,*,evidence=None):
        """원 raw key의 실제 stream/session을 source mask와 대조한다."""
        profile=json_bytes(context_record.payload,4096)['body'].get('profile')
        if profile in ('parking-method-1','parking-originals-1'):
            from .parking_scene import record_sources
            try:checked=record_sources(record,context_record,evidence=evidence,
                scopes=(self,*self._historical),approved_methods=self._parking_methods)
            except ValueError as error:raise SceneError(str(error)) from error
            return checked.actual_mask,checked.wire_mask
        scope,value=self._record_scope(context_record); kind=value['kind']; body=value['body']
        data=PROVENANCE.unpack(record.payload); record_kind=data[2]; raw_count=data[3]
        raw=data[-3]; streams=scope.raw_binding.fields['stream_instances']
        origin=PiGeometryOrigin.from_fields(scope.context.geometry_origin_fields)
        if record_kind==5:
            reg=REGISTRATION.unpack(raw)
            if (kind!='METRIC' or not record.parents or reg[2:5]!=(UUID(scope.context.map_epoch).bytes,
                    UUID(scope.context.frame_epoch).bytes,UUID(scope.context.odom_epoch).bytes)
                    or reg[11]!=UUID(scope.context.origin_mapper_instance).bytes):
                raise SceneError('registration origin/map differs from its actual source scope')
            return 0,0
        actual_mask=wire_mask=0
        for at in range(raw_count):
            instance,_,_=struct.unpack_from('<16sQQ',raw,at*32)
            if kind=='TOF':
                leaf=body['tof']
                if instance!=UUID(leaf['sensor_instance']).bytes: raise SceneError('foreign ToF incarnation raw key')
                actual_mask|=8; wire_mask|=4<<leaf['sensor_id']; continue
            if instance==UUID(streams['depth_right']).bytes: actual_mask|=2; wire_mask|=1
            elif instance==UUID(streams['rgb_video']).bytes: actual_mask|=1; wire_mask|=2
            elif instance in tuple(UUID(streams[name]).bytes for name in ('nn_input0','nn_output0') if name in streams): actual_mask|=4
            elif kind=='METRIC' and instance==UUID(origin.device_session).bytes: actual_mask|=16
            else: raise SceneError('raw key is not an actual registered camera/encoder stream')
        if record.source_mask&32:
            if record_kind!=4 or not record.parents: raise SceneError('retained evidence requires original parent closure')
            actual_mask|=32
        return actual_mask,wire_mask


class MapperScene:
    """M 단일 compute owner. bounded station24/동결시도2/현재 사본1만 보유한다.

    receive_grant는 Pi clock lease의 현재 source를 검사한다. prepare_freeze가 반환한
    원 CheckpointRequest를 기존 D mailbox에 제출하고 실제 완료만 complete_freeze로
    돌려준다. 수집/hazard 업데이트는 그 사이에도 계속 호출해야 한다.
    """

    def __init__(self, context, source, profile, *, gateway_instance, evidence_scope=None):
        if type(context) is not StorageContext or type(profile) is not SurveyProfile:
            raise SceneError('original storage context/approved survey profile required')
        if type(source) is not dict or set(source) != SOURCE_KEYS:
            raise SceneError('exact registered mapper source required')
        self._source_bytes = canonical(source)
        expected = (context.origin_mapper_instance, context.map_epoch, context.frame_epoch, context.odom_epoch,
                    context.configuration, context.calibration, context.primitive_sha256)
        if tuple(source[name] for name in ('mapper_instance', 'map_epoch', 'frame_epoch', 'odom_epoch',
                'configuration', 'calibration', 'primitive')) != expected:
            raise SceneError('storage/publication source mismatch')
        if type(gateway_instance) is not str or str(UUID(gateway_instance)) != gateway_instance or not UUID(gateway_instance).int:
            raise SceneError('registered Gateway incarnation required')
        if evidence_scope is not None and (type(evidence_scope) is not SceneEvidenceScope or evidence_scope.context!=context):
            raise SceneError('exact current evidence semantic scope required')
        if evidence_scope is not None:
            origin=PiGeometryOrigin.from_fields(context.geometry_origin_fields)
            if (origin.vehicle!=source['vehicle'] or origin.gateway_instance!=gateway_instance
                    or evidence_scope.calibration.kind!=profile.input_kind):
                raise SceneError('actual Pi origin/source domain differs from survey')
        self.context, self.profile, self.gateway_instance = context, profile, gateway_instance
        self.evidence_scope=evidence_scope
        self._grant = self._lease = self._frozen = self._summary = None
        self._stations = (); self._last_pose = None; self._travel = 0.; self._attempts = 0
        self._station_windows = ()
        self._observation = None; self._begin_pi = None; self._started_j = None
        self._validation_start_j = None
        self._hazard = 0; self._blocked = 'NO_SCENE'; self._eligible = False

    @property
    def eligible(self): return self._eligible

    @property
    def source(self): return json_bytes(self._source_bytes,2048)

    @property
    def blocked_reason(self): return self._blocked

    @property
    def grant_bytes(self): return self._grant

    def renew_clock(self, lease, *, now_j_us):
        """같은 source의 실제 lease만 교체한다. scene/관측/동결 기한은 불변이다."""
        from xenova_drive_world.mapper_clock import ClockLease
        if type(lease) is not ClockLease or lease.source != self.source or lease.pi_host_boot != self.context.pi_host_boot:
            self.revoke('SOURCE'); raise SceneError('clock source changed')
        s, _, decode = _codec(); trust = decode(lease.trust_bytes,s.WorldTrust); uint(now_j_us)
        if now_j_us+trust.upper_offset_us>=lease.expires_pi_us: raise SceneError('expired original clock lease')
        self._lease=lease

    def revoke(self, reason='SOURCE', *, now_j_us=None):
        """실제 철회사건의 완료시각만 새로 기록한다. 원 관측 age/증거는 갱신하지 않는다."""
        if now_j_us is not None:uint(now_j_us)
        durable=self._frozen is not None and self._frozen.receipt is not None
        previous=self._summary if self._summary is not None else (None if self._frozen is None else self._frozen.summary)
        self._eligible = False; self._blocked = reason; self._summary = None; self._frozen = None
        if previous is not None and reason!='NEW_SCENE':
            s, encode, decode = _codec(); invalid=decode(previous,s.SurveySummary); invalid.status=3
            if now_j_us is not None:
                if now_j_us<invalid.completed_peer_us:raise SceneError('negative event completion regressed')
                invalid.completed_peer_us=now_j_us
            if not durable:invalid.evidence_flags &= ~16
            invalid.failure_flags={'SOURCE':1,'CLOCK':2,'CALIBRATION':4,'ALIGNMENT':8,'STORAGE':16,
                'RESOURCE':32,'HAZARD':64,'TIMEOUT':128}.get(reason,1)
            self._summary=encode(invalid)

    def _now(self, now):
        uint(now)
        if self._grant is None or self._lease is None: raise SceneError('no scene grant/clock')
        s, _, decode = _codec(); grant = decode(self._grant, s.SceneGrant)
        trust = decode(self._lease.trust_bytes, s.WorldTrust)
        if now + trust.upper_offset_us >= min(grant.valid_until_pi_us, self._lease.expires_pi_us):
            self.revoke('TIMEOUT'); raise SceneError('scene or original clock lease expired')
        return grant, trust

    def receive_grant(self, raw, lease, *, now_j_us):
        from xenova_drive_world.mapper_clock import ClockLease
        s, encode, decode = _codec(); grant = decode(raw, s.SceneGrant)
        if type(lease) is not ClockLease or lease.source != self.source or lease.pi_host_boot != self.context.pi_host_boot:
            self.revoke('SOURCE'); raise SceneError('scene requires current original source clock lease')
        for name in ('vehicle', 'map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive'):
            if getattr(grant, name) != self.source[name]:
                self.revoke('SOURCE'); raise SceneError('scene source mismatch')
        if (grant.mapper_device != self.source['device'] or grant.pi_host_boot != self.context.pi_host_boot
                or grant.gateway_instance != self.gateway_instance or grant.source_admission_revision != lease.source_revision
                or grant.scene.coverage_profile_sha256.hex() != self.profile.coverage_profile_sha256):
            self.revoke('SOURCE'); raise SceneError('scene profile/Pi origin mismatch')
        uint(now_j_us)
        trust = decode(lease.trust_bytes, s.WorldTrust)
        if now_j_us + trust.upper_offset_us >= min(grant.valid_until_pi_us, lease.expires_pi_us):
            raise SceneError('expired scene/clock')
        prior = None if self._grant is None else decode(self._grant, s.SceneGrant)
        same = prior is not None and prior.scene.admission_id == grant.scene.admission_id
        if same:
            if (grant.lifecycle_revision < prior.lifecycle_revision or
                    grant.scene.admission_revision < prior.scene.admission_revision): return False
            if grant.lifecycle_revision == prior.lifecycle_revision:
                if encode(grant) != self._grant: raise SceneError('same scene revision changed bytes')
                self._lease = lease; return True
            if (grant.issued_pi_us != prior.issued_pi_us or grant.valid_until_pi_us != prior.valid_until_pi_us
                    or grant.scene.survey_id != prior.scene.survey_id):
                raise SceneError('scene renewal cannot change original survey/expiry')
        elif not (grant.lifecycle_state == 0 and grant.purpose == 0 or grant.lifecycle_state == 1
                  and grant.purpose == 1 and grant.scene.allow_restore_after_mapper_restart):
            raise SceneError('new publication requires SURVEYING or explicit RESTORE_CHECK')
        if not same:
            self.revoke('NEW_SCENE'); self._stations = (); self._station_windows = (); self._last_pose = None; self._travel = 0.; self._attempts = 0
            self._begin_pi = grant.issued_pi_us; self._started_j = None; self._observation = None
        if grant.purpose == 1 and (not same or grant.request_nonce != prior.request_nonce):
            self._validation_start_j = now_j_us; self._stations = (); self._station_windows = (); self._last_pose = None
        if same and grant.request_nonce != prior.request_nonce:
            self._summary = None; self._frozen = None
        self._grant, self._lease = encode(grant), lease
        if grant.lifecycle_state in (3, 4): self.revoke('REVOKED',now_j_us=now_j_us); return True
        if grant.lifecycle_state == 0:
            self._eligible = False; self._blocked = 'SURVEYING'
        elif grant.lifecycle_state == 1:
            self._eligible = False; self._blocked = 'FREEZE_PENDING'
        else:
            frozen = self._frozen
            expected = grant.restore_checkpoint_sha256 if grant.purpose else grant.frozen_evidence_sha256
            if (frozen is None or frozen.receipt is None or frozen.checkpoint_sha256 != expected.hex()
                    or frozen.hazard_revision != grant.frozen_hazard_revision):
                self.revoke('FROZEN_REFERENCE'); return False
            summary = decode(frozen.summary, s.SurveySummary)
            if (summary.survey_revision != grant.frozen_survey_revision or
                    summary.request_nonce != grant.request_nonce or self._hazard != frozen.hazard_revision):
                self.revoke('FROZEN_REFERENCE'); return False
            summary.status = 2; self._summary = encode(summary); self._eligible = True; self._blocked = None
        return True

    def observe(self, observation, *, now_j_us):
        if type(observation) is not SurveyObservation: raise SceneError('original observation required')
        grant, trust = self._now(now_j_us); p = self.profile; sample = observation.pose.sample
        registration = observation.registration; stamp = observation.stamp
        raw,alignment=observation.raw_pose,observation.accepted_alignment
        scope=self.evidence_scope
        if scope is None:raise SceneError('actual approved local/vehicle calibration relation required')
        if (observation.input_kind != p.input_kind or type(sample) is not PoseSample
                or sample.origin != ('ODOMETRY' if p.input_kind == 'LIVE' else 'SYNTHETIC_ODOMETRY')
                or sample.epoch != self.context.odom_epoch or sample.calibration_id != scope.calibration.sha256
                or scope.calibration.vehicle_bundle_sha256 != self.context.calibration
                or stamp.key[:3] != (scope.raw_binding.fields['acquisition_worker'],
                    scope.raw_binding.fields['stream_instances']['rgb_video'],self.source['clock_epoch'])
                or stamp.clock_id != self.source['clock_epoch'] or sample.clock_id != stamp.clock_id
                or stamp.captured_us is None or observation.pose.captured_us != raw.captured_us
                or raw.bracket_us != observation.pose.bracket_us
                or raw.sample.context != sample.context or raw.sample.at_us != sample.at_us
                or not raw.bracket_us[0] <= raw.captured_us[0] <= stamp.captured_us[0]
                    <= stamp.captured_us[1] <= raw.captured_us[1] <= raw.bracket_us[1]):
            raise SceneError('observation time/pose/source provenance mismatch')
        lo, hi = raw.captured_us
        if (now_j_us < stamp.available_us or now_j_us-lo > p.observation_max_age_us or
                (not grant.purpose and (lo+trust.lower_offset_us < self._begin_pi
                    or hi+trust.upper_offset_us > self._begin_pi+SURVEY_US)) or
                (grant.purpose and (self._validation_start_j is None or lo < self._validation_start_j
                    or hi+trust.upper_offset_us >= grant.survey_deadline_pi_us))):
            raise SceneError('original survey observation outside collection/freshness window')
        if (registration.status != 'BOUNDED' or registration.complete is not True or not registration.feasible
                or registration.representative is None or type(registration.reference_revision) is not int
                or registration.reference_revision < 0
                or _number(registration.position_error_m) > p.max_anchor_position_m
                or _number(registration.yaw_error_rad) > p.max_anchor_heading_rad):
            raise SceneError('unique bounded alignment required')
        if (not alignment.valid_at(now_j_us) or alignment.observed_j_interval!=raw.captured_us
                or alignment.registration_revision!=registration.reference_revision+1
                or (sample.x,sample.y,sample.yaw)!=registration.representative
                or (sample.position_error_m,sample.yaw_error_rad)!=(registration.position_error_m,registration.yaw_error_rad)
                or alignment!=MapOdomAlignment.from_match(raw,observation.pose,
                    observed_j_interval=raw.captured_us,registration_revision=alignment.registration_revision,
                    valid_for_us=alignment.valid_for_us,position_drift_mps=alignment.position_drift_mps,
                    yaw_drift_rps=alignment.yaw_drift_rps)):
            raise SceneError('original raw capture/read reference/accepted alignment differ')
        if self._observation is not None:
            previous=self._observation
            if (stamp.key[:3]==previous.stamp.key[:3] and stamp.sequence<=previous.stamp.sequence
                    or observation.world_sequence <= self._observation.world_sequence
                    or lo < self._observation.raw_pose.captured_us[0]): return False
        self.hazard(observation.hazard_revision,now_j_us=now_j_us)
        if self._last_pose is not None:
            old = self._last_pose
            if not old.at_us < sample.at_us <= old.at_us+p.max_pose_gap_us:
                self.revoke('SOURCE'); raise SceneError('survey pose stream gap/order')
            self._travel += math.hypot(sample.x-old.x, sample.y-old.y)+sample.position_error_m+old.position_error_m
            if self._travel > 10.:
                self.revoke('RESOURCE'); raise SceneError('survey supervised travel exceeds10m')
        self._last_pose = sample; self._observation = observation
        if self._started_j is None: self._started_j = lo
        if grant.lifecycle_state == 0:
            s, encode, _ = _codec()
            self._summary = encode(s.SurveySummary(major=1,request_nonce=grant.request_nonce,
                survey_id=grant.scene.survey_id,survey_revision=grant.scene.survey_revision,status=0,
                world_sequence=observation.world_sequence,evidence_sequence=observation.world_sequence,
                started_peer_us=self._started_j,last_observed_peer_us=(lo+hi)//2,completed_peer_us=now_j_us,
                positive_cell_count=0,observed_slot_count=0,required_sensor_mask=p.required_sensor_mask,
                valid_sensor_mask=0,evidence_sha256=bytes(32),coverage_profile_sha256=grant.scene.coverage_profile_sha256,
                observation_station_count=len(self._stations),hazard_revision=self._hazard,
                alignment_revision=alignment.registration_revision,anchor_position_bound_m=registration.position_error_m,
                anchor_heading_bound_rad=registration.yaw_error_rad,evidence_flags=4,failure_flags=0,
                timestamp_error_us=(hi-lo+1)//2))
        return True

    def station(self, poses, *, now_j_us):
        self._now(now_j_us); p = self.profile
        # 운영 경로는 원 SensorState 등록 객체를 재검산하는 typed witness만 받는다.
        from .mapper_scene import SceneStationWitness
        if type(poses) is SceneStationWitness:
            poses.validate(self._observation,self.context,p,now_j_us)
            last=poses.map_pose.sample
            return self._station_record(last,poses.window_fields,p)
        if p.input_kind!='SYNTHETIC':raise SceneError('LIVE station requires original registered Vehicle window')
        if (type(poses) is not tuple or not 2 <= len(poses) <= 256
                or any(type(item) is not PoseSample for item in poses) or self._observation is None):
            raise SceneError('bounded actual stationary pose window required')
        last = poses[-1]
        if last != self._observation.pose.sample or last.at_us-poses[0].at_us < p.station_window_us:
            raise SceneError('station requires current whole stationary window')
        if self._validation_start_j is not None and poses[0].at_us < self._validation_start_j:
            raise SceneError('restore stationary window predates actual validation start')
        for index, pose in enumerate(poses):
            if (pose.context != last.context or index and not 0 < pose.at_us-poses[index-1].at_us <= p.max_pose_gap_us
                    or math.hypot(pose.x-last.x, pose.y-last.y)+pose.position_error_m+last.position_error_m > p.station_motion_m
                    or abs(math.remainder(pose.yaw-last.yaw, 2*math.pi))+pose.yaw_error_rad+last.yaw_error_rad > p.station_yaw_rad):
                raise SceneError('station window motion/error/context not bounded')
        fields=dict(started_us=poses[0].at_us,completed_us=last.at_us,sample_count=len(poses),
            x_bounds=(min(p.x-p.position_error_m for p in poses),max(p.x+p.position_error_m for p in poses)),
            y_bounds=(min(p.y-p.position_error_m for p in poses),max(p.y+p.position_error_m for p in poses)),
            yaw_about_last=(min(math.remainder(p.yaw-last.yaw,2*math.pi)-p.yaw_error_rad for p in poses),
                            max(math.remainder(p.yaw-last.yaw,2*math.pi)+p.yaw_error_rad for p in poses)))
        return self._station_record(last,fields,p)

    def _station_record(self,last,fields,p):
        if self._validation_start_j is not None and fields['started_us']<self._validation_start_j:
            raise SceneError('restore stationary window predates validation start')
        if any(math.hypot(last.x-old.x,last.y-old.y)-last.position_error_m-old.position_error_m<p.station_distance_m
               for old in self._stations):return False
        if len(self._stations)==24:
            self.revoke('RESOURCE');raise SceneError('station capacity24 exceeded')
        self._station_windows+=(fields,);self._stations+=(last,)
        if self._summary is not None:
            s,encode,decode=_codec();summary=decode(self._summary,s.SurveySummary)
            if summary.status==0:
                summary.observation_station_count=len(self._stations);self._summary=encode(summary)
        return True

    def hazard(self, revision, *, now_j_us=None):
        uint(revision, positive=True)
        if revision < self._hazard: raise SceneError('hazard revision regressed')
        if self._frozen is not None and revision != self._frozen.hazard_revision:self.revoke('HAZARD',now_j_us=now_j_us)
        self._hazard = revision

    def _inspect(self, payload, *, begin_pi, end_pi, all_tiles=None):
        if self.evidence_scope is None: raise SceneError('EVIDENCE_SCOPE_NOT_READY: collector source facts are missing')
        if type(payload) is not CheckpointPayload:raise SceneError('actual immutable checkpoint payload required')
        tiles=payload.tiles if all_tiles is None else all_tiles
        if (type(payload) is not CheckpointPayload or type(tiles) is not tuple or not 0<len(tiles)<=128
                or any(type(tile) is not TileVersion for tile in tiles)
                or tuple(sorted(tile.reference for tile in tiles))!=payload.tile_references):
            raise SceneError('freeze requires complete copied ROI, not hash-only tile references')
        records = {record.provenance_id: record for record in payload.evidence.provenance}
        contexts = {item.sha256: item for item in payload.evidence.contexts}
        positive = valid_mask = 0; visited = set(); actual_masks={};parsed={};geometry_groups={}
        for identity in sorted(records):
            record=records[identity];data=PROVENANCE.unpack(record.payload);parsed[identity]=data
            # 실제 reference 결합 aggregate는 GEOMETRY 사본들만 묶는다. 일반 aggregate의
            # mask가 RGB를 포함한다는 이유로 역사 기하 예외를 주지 않는다.
            geometry_groups[identity]=(data[2]==3 or data[2]==4 and bool(record.parents)
                and all(geometry_groups.get(parent,False) for parent in record.parents))

        def details(identity):
            record=records.get(identity)
            if record is None or record.context_sha256 not in contexts:raise SceneError('missing actual provenance/context closure')
            data=parsed[identity]
            if data[5]!=UUID(self.context.pi_host_boot).bytes or record.pi_upper>end_pi:
                raise SceneError('foreign/future original survey provenance')
            if identity not in actual_masks:
                actual_masks[identity]=self.evidence_scope.record_sources(record,contexts[record.context_sha256],evidence=payload.evidence)
            return record,data,actual_masks[identity]

        def odom_parent(identity):
            record,data,_=details(identity)
            return data[2] in (0,4) and record.source_mask==16

        def closure(identity,role='FRESH'):
            nonlocal valid_mask
            pending = [(identity,role)]
            while pending:
                identity,role=pending.pop();key=(identity,role)
                if key in visited:continue
                record,data,(actual,wire)=details(identity);kind,raw_count=data[2:4]
                if role in ('FRESH','CURRENT_RGB'):
                    if record.pi_lower<begin_pi:raise SceneError('old survey clearance/catalog support cannot be renewed')
                    valid_mask|=wire
                if role=='ODOM':
                    if (record.source_mask!=16 or (kind==0 and (actual!=16 or raw_count!=1 or record.parents))
                            or kind not in (0,4) or kind==4 and (actual or raw_count or not record.parents)):
                        raise SceneError('historical support is not closed original PI_ODOM lineage')
                    pending.extend((parent,'ODOM') for parent in record.parents)
                elif role=='CURRENT_RGB':
                    if kind!=0 or actual!=1 or raw_count!=1:
                        raise SceneError('current registration requires original RGB SENSOR proof')
                    pending.extend((parent,'ODOM') for parent in record.parents)
                elif role=='HISTORY':
                    if record.source_mask&~17 or actual&~17 or kind not in (0,3,4,5):
                        raise SceneError('historical geometry contains clearance or unsupported sensor evidence')
                    if kind==0:
                        if actual==16:
                            pending.append((identity,'ODOM'))
                        elif actual==1 and raw_count==1:
                            pending.extend((parent,'ODOM') for parent in record.parents)
                        else:raise SceneError('historical geometry requires actual RGB or PI_ODOM source')
                    elif kind==3:
                        if actual!=1 or raw_count!=1:raise SceneError('historical GEOMETRY requires original RGB source')
                        for parent in record.parents:
                            _,parent_data,_=details(parent)
                            if parent_data[2]==5:pending.append((parent,'HISTORY'))
                            elif odom_parent(parent):pending.append((parent,'ODOM'))
                            else:raise SceneError('historical GEOMETRY parent is not registration/odom support')
                    elif kind==4:
                        if actual or raw_count or not geometry_groups[identity]:
                            raise SceneError('generic aggregate cannot become historical geometry')
                        pending.extend((parent,'HISTORY') for parent in record.parents)
                    else:
                        # REGISTRATION의 현재 RGB와 이전 frozen geometry는 다른 역할이다.
                        # 이전 기하만 붙여 새로운 정합을 주장할 수 없다.
                        current_rgb=[];anchor=False
                        registration_scope,_=self.evidence_scope._record_scope(contexts[record.context_sha256],
                            record=record,evidence=payload.evidence)
                        for parent in record.parents:
                            previous,parent_data,(parent_actual,_)=details(parent)
                            same_capture=(previous.pi_lower,previous.pi_upper,previous.observation_sequence)==(
                                record.pi_lower,record.pi_upper,record.observation_sequence)
                            if parent_data[2]==0 and parent_actual==1 and parent_data[3]==1 and same_capture:
                                rgb_scope,_=self.evidence_scope._record_scope(contexts[previous.context_sha256],
                                    record=previous,evidence=payload.evidence)
                                if (rgb_scope.raw_binding.sha256!=registration_scope.raw_binding.sha256
                                        or not same_catalog_geometry(rgb_scope.context,registration_scope.context,'SLOT')):
                                    raise SceneError('REGISTRATION current RGB belongs to a different original source')
                                current_rgb.append(parent);pending.append((parent,'HISTORY'))
                            elif odom_parent(parent):pending.append((parent,'ODOM'))
                            elif geometry_groups[parent]:anchor=True;pending.append((parent,'HISTORY'))
                            else:raise SceneError('REGISTRATION parent changed original current RGB/static geometry role')
                        if not current_rgb or not anchor:
                            raise SceneError('REGISTRATION lacks its own original RGB or previous frozen geometry')
                        # 역사 노드는 자체로 mask에 기여하지 않는다. 새 정합의 실제 현재 RGB만
                        # 별도 fresh 역할로 다시 검산하며 원 interval/순번을 유지한다.
                        if record.pi_lower>=begin_pi:
                            pending.extend((parent,'CURRENT_RGB') for parent in current_rgb)
                else:
                    for parent in record.parents:
                        _,parent_data,_=details(parent)
                        if kind in (1,2,3) and parent_data[2] in (3,5):
                            pending.append((parent,'HISTORY'))
                        elif kind in (1,2,3) and odom_parent(parent):pending.append((parent,'ODOM'))
                        else:pending.append((parent,'FRESH'))
                visited.add(key)
        for tile in tiles:
            if tile.map_epoch != self.context.map_epoch: raise SceneError('foreign tile version')
            for cell in CELL.iter_unpack(tile.payload):
                occupancy, flags, mask, _, _, _, identity, lower, upper, sequence, _, _ = cell
                if not identity: continue
                record,data,(actual,_)=details(identity)
                if occupancy==0 and flags&1 and not flags&4:
                    if data[2]!=2 or not actual&10:
                        raise SceneError('positive FREE requires its original direct depth/ToF clearance record')
                closure(identity)
                if (mask & record.source_mask != mask or (lower,upper,sequence) != (record.pi_lower,record.pi_upper,record.observation_sequence)):
                    raise SceneError('cell differs from immutable original provenance')
                if occupancy == 0 and flags & 1 and not flags & 4: positive += 1
        for entry in payload.evidence.catalog: closure(entry.provenance_id)
        for identity in sorted({identity for identity,_ in visited}):
            record=records[identity]; available=actual_masks[identity][0]
            for parent in record.parents: available|=records[parent].source_mask
            if record.source_mask&available!=record.source_mask:
                raise SceneError('provenance source mask has no actual named raw/parent witness')
        if not 0 < positive <= self.profile.atlas_cell_capacity or valid_mask & self.profile.required_sensor_mask != self.profile.required_sensor_mask:
            raise SceneError('positive cells/required sensors not actually evidenced')
        slots = sum(item.kind == 'SLOT' for item in payload.evidence.catalog)
        if slots > 64: raise SceneError('summary slot capacity exceeded')
        return positive, valid_mask, slots

    def freeze_scene(self, job, *, current_tile_references, current_hazard_revision, now_j_us):
        """원 operator job의 순수 descriptor다. CP 생성/offer/ID 소비는 하지 않는다."""
        grant, trust = self._now(now_j_us)
        if (grant.lifecycle_state != 1 or grant.purpose or type(job) is not StorageJob or job.context != self.context
                or job.scene_commit_nonce != str(UUID(bytes=grant.request_nonce)) or not job.current(self.context.sha256,now_j_us)
                or self._observation is None or not self._stations or self._attempts == 2):
            raise SceneError('original freeze job/current survey/station/cap required')
        if (now_j_us+trust.upper_offset_us >= grant.survey_deadline_pi_us
                or job.expires_us+trust.upper_offset_us>min(grant.survey_deadline_pi_us,
                    grant.valid_until_pi_us,self._lease.expires_pi_us)):
            raise SceneError('original job must fit the unchanged operator/source deadline')
        if self._frozen is not None and self._frozen.receipt is None: raise SceneError('one original freeze is still pending')
        observation = self._observation
        if (type(current_tile_references) is not tuple or len(current_tile_references)>128
                or current_tile_references!=tuple(sorted(current_tile_references))
                or len({item[:2] for item in current_tile_references})!=len(current_tile_references)
                or type(current_hazard_revision) is not int or current_hazard_revision!=self._hazard
                or observation.hazard_revision!=self._hazard):
            raise SceneError('atlas version capture changed or observation predates current hazard')
        if now_j_us-observation.raw_pose.captured_us[0]>self.profile.observation_max_age_us:
            raise SceneError('original frozen observation expired')
        scene = dict(profile='controlled-survey-1',scene_commit_nonce=job.scene_commit_nonce,
            admission_id=grant.scene.admission_id.hex(),survey_id=grant.scene.survey_id.hex(),
            survey_revision=grant.scene.survey_revision,begin_pi_us=self._begin_pi,
            collection_end_pi_us=self._begin_pi+SURVEY_US,coverage_profile_sha256=self.profile.coverage_profile_sha256,
            hazard_revision=self._hazard,alignment_revision=observation.accepted_alignment.registration_revision,
            stations_sha256=sha256(canonical([(p.at_us,p.x,p.y,p.yaw,p.position_error_m,p.yaw_error_rad) for p in self._stations])).hexdigest(),
            stations=[(p.at_us,p.x,p.y,p.yaw,p.position_error_m,p.yaw_error_rad) for p in self._stations],
            station_windows=self._station_windows,
            observation_station_count=len(self._stations),base_evidence_sha256=None)
        return json_bytes(canonical(scene),16384)

    def adopt_freeze(self,request,*,current_tile_references,current_hazard_revision,now_j_us,
                     all_tiles=None,mapper_state=None):
        """Catalog가 이미 고정한 동일 원 CP만 수락한다. 요청을 재생성하지 않는다.

        all_tiles는 실제 snapshot의 full128 사본이며 wire copy32 cap과 별개다.
        추가 META는 snapshot의 원 ref를 별도 전달할 때만 허용한다. 실패한 미게시
        Catalog pending의 terminal 정리는 호출자 Mapping owner가 수행해야 한다.
        """
        if type(request) is not CheckpointRequest:raise SceneError('exact original checkpoint request required')
        scene=self.freeze_scene(request.job,current_tile_references=current_tile_references,
            current_hazard_revision=current_hazard_revision,now_j_us=now_j_us)
        if mapper_state is not None:
            from ..storage.mapper_state import decode_mapper_state
            scene['mapper_state']=mapper_state
            decode_mapper_state(scene,request.payload.evidence.contexts)
        manifest=json_bytes(request.payload.manifest,16384)
        if (manifest['scene']!=json_bytes(canonical(scene),16384) or request.payload.intent!='CHECKPOINT'
                or request.payload.tile_references!=current_tile_references):
            raise SceneError('final original checkpoint changed descriptor/META/full tile references')
        request.payload.validate_job(request.job)
        positive,valid_mask,slots=self._inspect(request.payload,begin_pi=self._begin_pi,
            end_pi=self._begin_pi+SURVEY_US,all_tiles=all_tiles)
        grant,_=self._now(now_j_us);observation=self._observation;payload=request.payload
        self._attempts += 1
        s, encode, _ = _codec(); lo,hi = observation.raw_pose.captured_us
        summary = s.SurveySummary(major=1,request_nonce=grant.request_nonce,survey_id=grant.scene.survey_id,
            survey_revision=grant.scene.survey_revision,status=1,world_sequence=observation.world_sequence,
            evidence_sequence=observation.world_sequence,started_peer_us=self._started_j,last_observed_peer_us=(lo+hi)//2,
            completed_peer_us=now_j_us,positive_cell_count=positive,observed_slot_count=slots,
            required_sensor_mask=self.profile.required_sensor_mask,valid_sensor_mask=valid_mask,
            evidence_sha256=bytes.fromhex(payload.sha256),coverage_profile_sha256=grant.scene.coverage_profile_sha256,
            observation_station_count=len(self._stations),hazard_revision=self._hazard,
            alignment_revision=observation.accepted_alignment.registration_revision,
            anchor_position_bound_m=observation.registration.position_error_m,
            anchor_heading_bound_rad=observation.registration.yaw_error_rad,evidence_flags=31,failure_flags=0,
            timestamp_error_us=(hi-lo+1)//2)
        self._frozen = FrozenSurvey(request,encode(summary),self._hazard,grant.lifecycle_revision)
        self._summary = None; self._eligible = False; self._blocked = 'FREEZE_PENDING'; return request

    def prepare_freeze(self,job,tiles,evidence,*,current_tile_references,current_hazard_revision,now_j_us,
                       mapper_state=None):
        """독립 사용 wrapper. 실제 Mapping 통합은 descriptor/adopt로 CP를 한 번만 만든다."""
        scene=self.freeze_scene(job,current_tile_references=current_tile_references,
            current_hazard_revision=current_hazard_revision,now_j_us=now_j_us)
        if mapper_state is not None:scene['mapper_state']=mapper_state
        _,trust=self._now(now_j_us)
        payload=CheckpointPayload.build(job,tiles,evidence,scene=scene,tile_references=current_tile_references,
            durable_cutoff_pi_us=self._observation.raw_pose.captured_us[1]+trust.upper_offset_us)
        return self.adopt_freeze(CheckpointRequest(job,payload),current_tile_references=current_tile_references,
            current_hazard_revision=current_hazard_revision,now_j_us=now_j_us,mapper_state=mapper_state)

    def complete_freeze(self, request, receipt, *, now_j_us):
        grant, trust = self._now(now_j_us); frozen = self._frozen
        s, encode, decode = _codec()
        original_summary=None if frozen is None else decode(frozen.summary,s.SurveySummary)
        if (type(frozen) is not FrozenSurvey or request is not frozen.request or type(receipt) is not CommitReceipt
                or self._blocked != 'FREEZE_PENDING' or self._hazard != frozen.hazard_revision
                or frozen.grant_revision != grant.lifecycle_revision
                or now_j_us+trust.upper_offset_us >= grant.survey_deadline_pi_us
                or now_j_us-(original_summary.last_observed_peer_us-original_summary.timestamp_error_us)>self.profile.observation_max_age_us
                or not receipt.current(request.job,request.payload,self.context.sha256,now_j_us)):
            return False
        summary = original_summary
        summary.completed_peer_us = now_j_us
        self._summary = encode(summary)
        self._frozen = FrozenSurvey(request,self._summary,frozen.hazard_revision,frozen.grant_revision,receipt)
        return True

    def restore_ready(self, loaded, original_receipt, observation, *, now_j_us):
        """단일 페이지 호환 포트다. 원 commit descriptor와 전체 material을 반드시 요구한다."""
        from .map_checkpoint import CheckpointPages
        if (type(loaded) is not LoadedCheckpoint or loaded.original_commit is None
                or loaded.original_commit.receipt!=original_receipt):
            raise SceneError('actual LOAD with its original checkpoint commit descriptor required')
        pages=CheckpointPages(loaded.checkpoint_ref,loaded.request.job.context)
        complete=pages.accept(loaded,now_us=now_j_us)
        if complete is None:raise SceneError('all original selected checkpoint pages required')
        return self.restore_complete(complete,observation,now_j_us=now_j_us)

    def restore_complete(self, complete, observation, *, now_j_us):
        """전체 실제 LOAD·원 transaction·새 관측을 결박한다. 원 CP/셀의 시각은 갱신하지 않는다."""
        from .map_checkpoint import SourceComplete
        grant, trust = self._now(now_j_us)
        if (grant.purpose != 1 or grant.lifecycle_state != 1 or not grant.scene.allow_restore_after_mapper_restart
                or type(complete) is not SourceComplete or complete.original_commit is None
                or self.evidence_scope is None):
            raise SceneError('actual complete LOAD with original checkpoint commit descriptor required')
        self.evidence_scope.complete_restore_head(complete,now_us=now_j_us)
        original_sha=complete.original_commit.validate(complete.original_job,complete.manifest,complete.checkpoint_ref)
        if original_sha!=grant.restore_checkpoint_sha256.hex():
            raise SceneError('restore grant selected a different original full checkpoint SHA')
        old=complete.original_job;receipt=complete.original_commit.receipt
        if any(getattr(old.context,name) != getattr(self.context,name) for name in
               ('database_uuid','map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256')):
            raise SceneError('restore cannot cross Pi/odom/calibration/map identity')
        scene = json_bytes(complete.manifest,16384)['scene']
        if (type(scene) is not dict or scene.get('survey_id') != grant.scene.survey_id.hex()
                or scene.get('survey_revision') != grant.frozen_survey_revision
                or scene.get('admission_id') != grant.scene.admission_id.hex()
                or scene.get('coverage_profile_sha256') != self.profile.coverage_profile_sha256
                or original_sha != grant.frozen_evidence_sha256.hex()
                   and scene.get('base_evidence_sha256') != grant.frozen_evidence_sha256.hex()):
            raise SceneError('checkpoint does not close over original frozen base')
        # 이 사본의 SHA는 원 CP SHA가 아니다. inspect만 기존 bounded payload 포트로 전달한다.
        inspection=CheckpointPayload(complete.manifest,complete.tiles[:32],complete.evidence)
        positive, mask, slots = self._inspect(inspection,begin_pi=scene['begin_pi_us'],
            end_pi=scene['collection_end_pi_us'],all_tiles=complete.tiles)
        self.observe(observation,now_j_us=now_j_us)
        if self._observation is not observation:
            raise SceneError('restore requires the exact accepted current observation')
        if (not self._stations or self._hazard != grant.frozen_hazard_revision
                or now_j_us+trust.upper_offset_us >= grant.survey_deadline_pi_us):
            raise SceneError('restore validation changed hazard/deadline')
        s, encode, _ = _codec(); lo,hi = observation.raw_pose.captured_us
        summary = s.SurveySummary(major=1,request_nonce=grant.request_nonce,survey_id=grant.scene.survey_id,
            survey_revision=grant.frozen_survey_revision,status=4,world_sequence=observation.world_sequence,
            evidence_sequence=observation.world_sequence,started_peer_us=lo,last_observed_peer_us=(lo+hi)//2,
            completed_peer_us=now_j_us,positive_cell_count=positive,observed_slot_count=slots,
            required_sensor_mask=self.profile.required_sensor_mask,valid_sensor_mask=mask,
            evidence_sha256=bytes.fromhex(original_sha),coverage_profile_sha256=grant.scene.coverage_profile_sha256,
            observation_station_count=scene['observation_station_count'],hazard_revision=self._hazard,
            alignment_revision=observation.accepted_alignment.registration_revision,
            anchor_position_bound_m=observation.registration.position_error_m,
            anchor_heading_bound_rad=observation.registration.yaw_error_rad,evidence_flags=63,failure_flags=0,
            timestamp_error_us=(hi-lo+1)//2)
        self._summary = encode(summary)
        self._frozen = RestoredSurvey(complete,self._summary,self._hazard,grant.lifecycle_revision)
        return self._summary

    def summary_bytes(self, *, now_j_us):
        self._now(now_j_us)
        return self._summary
