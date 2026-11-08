"""관측 divider prefix/현재 association/원 LOAD 복원의 합성 정책 시험.

D head/COMMITTED·sensor qualification은 명시한 software fixture다. 실제 내구성,
물리 보정·clock·주행 성공의 증거는 아니며 GT 중심선을 제품에 주입하지 않는다.
"""

from dataclasses import replace
from hashlib import sha256
import math
import struct
import unittest
from uuid import UUID

import test_lane_catalog as lanes
import test_depth_evidence as depths
import raw_binding_fixtures as raw_fixture
from xenova_vehicle.mapping.lane_catalog import LaneCatalog,LaneSource
from xenova_vehicle.mapping.registration import RegistrationProfile
from xenova_vehicle.mapping.road_basis import RoadBasis,POINT
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.runtime.map_checkpoint import CheckpointPages
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.scene import SceneEvidenceScope
from xenova_vehicle.runtime.storage_copy import CopyLease,OwnedCopy,encode_load
from xenova_vehicle.storage.contracts import StorageContext,StorageJob,LoadRequest,CatalogHeadSnapshot,canonical,json_bytes
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.records import EvidenceTable,ProvenanceRecord,PROVENANCE,CheckpointPayload,LoadedCheckpoint


def observed(catalog,sequence=1,*,points=None,prior=lanes.FIXED,profile=None,raw=None,captured=None):
    road,old=lanes.observed(catalog,sequence,captured=captured)
    stamp=old.stamp if raw is None else FrameStamp(raw.fields['acquisition_worker'],raw.fields['stream_instances']['rgb_video'],
        sequence,catalog.clock_id,old.stamp.captured_us,old.stamp.available_us)
    source=replace(old,stamp=stamp,calibration_id=catalog.calibration_id)
    road=replace(road,source_key=stamp.key)
    if points is not None:
        points=tuple(points);directions=tuple((b[0]-a[0],b[1]-a[1]) for a,b in zip(points,points[1:]))
        normals=tuple((-dy/math.hypot(dx,dy),dx/math.hypot(dx,dy)) for dx,dy in directions)
        normals+=normals[-1:]
        left=tuple((p[0]+.3*n[0],p[1]+.3*n[1]) for p,n in zip(points,normals))
        right=tuple((p[0]-.3*n[0],p[1]-.3*n[1]) for p,n in zip(points,normals))
        h=replace(road.hypotheses[0],station_s=tuple(float(i) for i in range(len(points))),divider=points,
            left_outer=left,right_outer=right,left_center=tuple(((p[0]+q[0])/2,(p[1]+q[1])/2) for p,q in zip(points,left)),
            right_center=tuple(((p[0]+q[0])/2,(p[1]+q[1])/2) for p,q in zip(points,right)),inferred=(False,)*len(points))
        road=replace(road,hypotheses=(h,))
    kwargs={} if profile is None else dict(profile=profile)
    association=catalog.observe(road,source,prior,prior_qualified=True,crossing=bytes(len(road.hypotheses[0].divider)-1),**kwargs)
    return association


def update(catalog,sequence=1,**kwargs):
    association=observed(catalog,sequence,**kwargs)
    if association.status=='NEW':
        need,job,payload,receipt=lanes.reserve(catalog,association)
        if need is not None:catalog.apply_committed_range(need,job,payload,receipt,
            current_context_sha256=catalog.context.sha256,now_us=1_100_100)
    model=catalog.update(association,provenance_ids=(100+sequence*2-1,100+sequence*2))
    if model is None:raise AssertionError('software fixture needs actual accepted current model: '+association.status)
    return model


def consume(owner,catalog,model,*,resolution=.02,stamp=None,now=None):
    return owner.observe(catalog,model,current_stamp=model.source.stamp if stamp is None else stamp,
        resolution_m=resolution,now_us=model.source.stamp.available_us if now is None else now,max_age_us=500_000)


def restore_fixture(basis,catalog,scope,*,state=None):
    stored=basis.state_fields() if state is None else state
    ref,pages=encode_mapper_state(dict(lane_catalog=catalog.state_fields(),road_basis=stored))
    context=scope.context_record('METRIC');records=[]
    for identity,model in basis.originals().items():
        stamp=model.source.stamp
        key=struct.pack('<16sQQ',UUID(scope.raw_binding.fields['stream_instances']['rgb_video']).bytes,stamp.sequence,stamp.captured_us[0])
        records.append(ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),1,3,1,0,
            UUID(catalog.context.pi_host_boot).bytes,*stamp.captured_us,stamp.sequence,0,0,0,0,key.ljust(128,b'\0'),0,bytes(16))))
    evidence=EvidenceTable(tuple(records),(context,)+pages,catalog.checkpoint_records(),catalog_highwaters=(catalog.durable_hwm,1))
    cp=StorageJob(catalog.context,lanes.uid(300),1_150_000,2_150_000)
    payload=CheckpointPayload.build(cp,evidence=evidence,scene=dict(mapper_state=ref),
        durable_cutoff_pi_us=max((record.pi_upper for record in records),default=1_000_010))
    selected=dict(checkpoint_job_id=cp.job_id,durable_revision='1',manifest_sha256=sha256(payload.manifest).hexdigest(),
        manifest_length=len(payload.manifest),tile_count=0,map_epoch=catalog.context.map_epoch,frame_epoch=catalog.context.frame_epoch,
        odom_epoch=catalog.context.odom_epoch,pi_host_boot=catalog.context.pi_host_boot,configuration=catalog.context.configuration,
        calibration=catalog.context.calibration,primitive_sha256=catalog.context.primitive_sha256,origin_mapper_instance=catalog.context.origin_mapper_instance)
    job=StorageJob(catalog.context,lanes.uid(301),1_190_000,2_190_000)
    request=LoadRequest(job,canonical(selected),canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
    heads=CatalogHeadSnapshot(job.job_id,catalog.context.sha256,catalog.context.database_uuid,catalog.context.map_epoch,
        (('LANE',catalog.durable_hwm,1),('SLOT',1,0)),1_190_000,job.expires_us)
    loaded=LoadedCheckpoint(request,cp,request.checkpoint_ref,payload,heads,1_190_010)
    writer=CopyLease(scope.raw_binding.fields['service_instance'],lanes.uid(302),1,Endpoint(lanes.uid(303),lanes.uid(304)),
        Endpoint(lanes.uid(305),catalog.context.mapper_worker),lanes.uid(306),raw_binding_sha256=scope.raw_binding.sha256,
        publication_binding_sha256=sha256(catalog.context.source_binding).hexdigest())
    encoded=encode_load(loaded,writer);reader=replace(writer,read_token=lanes.uid(307))
    ready=dict(encoded.ready,copy_lease=reader.fields)
    decoded=OwnedCopy(reader,canonical(ready),encoded.evidence,encoded.descriptors,encoded.manifest,encoded.tiles).decode()
    complete=CheckpointPages(request.checkpoint_ref,catalog.context).accept(decoded,now_us=1_190_020)
    restored=LaneCatalog(catalog.context,clock_id=catalog.clock_id,calibration_id=catalog.calibration_id,frame_id=catalog.frame_id)
    restored.restore(decoded,now_us=1_190_020)
    return complete,restored


def scoped_catalog():
    fields=depths.profile(6).to_dict();base=lanes.owner().context;fields['vehicle_bundle_sha256']=base.calibration
    calibration=CalibrationProfile.from_dict(fields)
    raw_fields=raw_fixture.binding_fields(nn_enabled=False);raw_fields['calibration_bundle']=calibration.sha256
    raw=RawBinding(raw_fields);binding=json_bytes(base.source_binding,2048)
    binding.update(raw_binding_sha256=raw.sha256,service_instance=raw.fields['service_instance'])
    context=replace(base,source_binding=canonical(binding))
    catalog=LaneCatalog(context,clock_id=raw.fields['clock_epoch'],calibration_id=calibration.sha256)
    return catalog,SceneEvidenceScope(context,raw,calibration,'e'*64),raw


class RoadBasisTests(unittest.TestCase):
    def test_current_d_issued_prefix_float32_and_append_keep_original_id_bytes_and_time(self):
        catalog=lanes.owner();model=update(catalog,points=((.1,0.),(1.1,0.)))
        owner=RoadBasis();first=consume(owner,catalog,model)
        self.assertEqual(first.status,'READY');self.assertEqual(first.reference_revision,1)
        self.assertLessEqual(len(first.reference_id),64)
        self.assertEqual(first.reference_id,'rb1:'+catalog.context.map_epoch+':00000001:00000002')
        for source,stored,error in zip(model.road.hypotheses[0].divider,first.points,first.point_error_m):
            self.assertGreaterEqual(error,model.road.hypotheses[0].error_m+math.dist(source,stored))
        newer=update(catalog,2,points=((.1,0.),(1.1,0.)),prior=lanes.PoseBox((.05,.05),(0.,0.),(0.,0.)))
        second=consume(owner,catalog,newer)
        self.assertEqual(second.reference_revision,2);self.assertEqual(second.reference_id,first.reference_id)
        self.assertEqual(second.points_f32[:len(first.points_f32)],first.points_f32)
        self.assertEqual(second.origin_source.stamp.captured_us,first.origin_source.stamp.captured_us)
        self.assertEqual(second.latest_model_revision,newer.model_revision)
        self.assertNotEqual(second.latest_lane_source.stamp.captured_us,second.origin_source.stamp.captured_us)
        before=second.points_f32
        noisy=update(catalog,3,points=((.1,0.),(1.1,0.)),prior=lanes.PoseBox((.051,.051),(0.,0.),(0.,0.)))
        same=consume(owner,catalog,noisy)
        self.assertEqual((same.reference_revision,same.points_f32),(2,before))
        self.assertEqual(same.latest_model_revision,3);self.assertEqual(len(owner.roots),6)

    def test_old_latest_after_unknown_wrong_frame_and_stale_source_are_not_current(self):
        catalog=lanes.owner();model=update(catalog);owner=RoadBasis();original=consume(owner,catalog,model)
        later=observed(catalog,2);road=replace(later.road,complete=False)
        catalog.observe(road,later.source,lanes.FIXED,prior_qualified=True,crossing=b'\0')
        self.assertIs(catalog.latest,model)
        for stamp in (model.source.stamp,later.source.stamp):
            decision=consume(owner,catalog,model,stamp=stamp,now=later.source.stamp.available_us)
            self.assertEqual(decision.status,'WAIT_CURRENT');self.assertEqual(decision.points_f32,original.points_f32)
        other=lanes.owner();current=update(other)
        decision=consume(RoadBasis(),other,current,now=current.source.stamp.available_us+500_001)
        self.assertEqual(decision.status,'WAIT_CURRENT');self.assertEqual(decision.points_f32,b'')
        unowned=replace(current)
        self.assertEqual(consume(RoadBasis(),other,unowned).status,'WAIT_CURRENT')
        wrong_clock=replace(current.source.stamp,clock_id='different-original-clock')
        self.assertEqual(consume(RoadBasis(),other,current,stamp=wrong_clock).status,'WAIT_CURRENT')

    def test_possible_disconnected_seams_and_reversed_direction_hold_without_new_id(self):
        for points in (((.05,0.),(.35,0.),(.35,.8),(.75,.8),(.75,0.),(1.2,0.)),((1.,0.),(0.,0.))):
            catalog=lanes.owner();first=update(catalog);owner=RoadBasis();original=consume(owner,catalog,first)
            # 큰 residual은 이 반례용 명시 값이다. 실제 운용 설정의 승인이 아니다.
            current=update(catalog,2,points=points,profile=RegistrationProfile(residual_m=2.))
            decision=consume(owner,catalog,current)
            self.assertEqual(decision.status,'SECTION_HANDOFF_REQUIRED')
            self.assertIn(decision.reason,('AMBIGUOUS_OR_MISSING_OVERLAP','DIRECTION_UNRESOLVED'))
            self.assertEqual((decision.reference_id,decision.reference_revision,decision.points_f32),
                             (original.reference_id,original.reference_revision,original.points_f32))
            restored_view=consume(owner,catalog,current)
            self.assertEqual(restored_view.status,'SECTION_HANDOFF_REQUIRED')

    def test_64_point_cap_preserves_entire_prefix_and_cannot_decimate_or_reset(self):
        catalog=lanes.owner();owner=RoadBasis()
        snapshots=[]
        for sequence,start in enumerate((0,16,32,48),1):
            current=update(catalog,sequence,points=tuple((float(i),0.) for i in range(start,start+32)))
            snapshots.append(consume(owner,catalog,current))
        self.assertEqual([len(s.points) for s in snapshots[:3]],[32,48,64])
        held=snapshots[-1];self.assertEqual(held.status,'SECTION_HANDOFF_REQUIRED');self.assertEqual(held.reason,'POINT_CAP')
        self.assertEqual(held.points_f32,snapshots[-2].points_f32);self.assertEqual(held.reference_revision,3)
        self.assertEqual(held.reference_id,snapshots[0].reference_id)

    def test_actual_complete_state_restore_preserves_bytes_roots_and_requires_new_observation(self):
        catalog,scope,raw=scoped_catalog();model=update(catalog,raw=raw)
        owner=RoadBasis();consume(owner,catalog,model)
        newer=update(catalog,2,raw=raw,prior=lanes.PoseBox((.05,.05),(0.,0.),(0.,0.)))
        original=consume(owner,catalog,newer)
        complete,restored_catalog=restore_fixture(owner,catalog,scope)
        restored=RoadBasis();view=restored.restore(complete,restored_catalog,evidence_scope=scope,now_us=1_190_020)
        self.assertEqual(view.status,'WAIT_CURRENT');self.assertEqual(view.reason,'RESTORED_REOBSERVATION_REQUIRED')
        self.assertEqual(view.points_f32,original.points_f32);self.assertEqual(view.origin_source,original.origin_source)
        self.assertEqual(restored.state_fields(),owner.state_fields());self.assertEqual(restored.roots,owner.roots)
        old=restored_catalog.models[0]
        self.assertEqual(consume(restored,restored_catalog,old,now=1_190_020).status,'WAIT_CURRENT')
        with self.assertRaises(ValueError):RoadBasis().restore(complete,restored_catalog,evidence_scope=True,now_us=1_190_020)
        for mutation in ('prefix','revision','root','unknown','scope'):
            state=owner.state_fields()
            if mutation=='prefix':state['points_f32']=POINT.pack(.25,0.).hex()+state['points_f32'][POINT.size*2:]
            elif mutation=='revision':state['reference_revision']+=1
            elif mutation=='root':state['events'][0]['model']['provenance_ids']=[777,778]
            elif mutation=='unknown':state['unknown']=True
            else:state['geometry_scope']['frame_epoch']=lanes.uid(999)
            # 원 LOAD metadata 검산이 먼저 거절해도 손상된 state를 owner에 복원하지 않는다.
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                wrong,checked=restore_fixture(owner,catalog,scope,state=state)
                RoadBasis().restore(wrong,checked,evidence_scope=scope,now_us=1_190_020)

    def test_closed_road_never_initializes_reference_or_hides_handoff(self):
        catalog=lanes.owner();model=update(catalog,points=((0.,0.),(1.,0.),(1.,1.),(0.,1.),(0.,0.)))
        owner=RoadBasis();view=consume(owner,catalog,model)
        self.assertEqual(view.status,'SECTION_HANDOFF_REQUIRED');self.assertEqual(view.points_f32,b'')
        self.assertIsNone(view.reference_id);self.assertEqual(view.reference_revision,0)
