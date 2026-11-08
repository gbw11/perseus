"""실제 LIVE sensor admission·SQLite·Scene·World producer의 retained 연결 회귀.

보정/차선/정합/whole-cell FREE는 기존 fixture와 같은 명시 software 기하 사실이다.
실물 영상·측정 보정·Pi 운영 승인은 시험하지 않는다. LIVE 입력을 다른 kind에서
바꾸지 않으며 실제 원 Vehicle/clock/DB UUID/내구 range/receipt/lifecycle를 소비한다.
"""

from base64 import b64encode
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import unittest
from uuid import UUID,uuid4

import test_mapper_mapping as mapping_fixture
import test_world_adapter as road_fixture
from xenova_drive_world import schema,encode,decode
from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.lane_catalog import LaneCatalog,LaneSource
from xenova_vehicle.mapping.local_map import EvidenceGrid,GridSpec,UNKNOWN,FREE
from xenova_vehicle.mapping.registration import FrozenReference,PoseBox,RegistrationResult,SegmentFeature
from xenova_vehicle.mapping.road_basis import RoadBasis
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.inputs import FrameStamp,ImageFrame,PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline,PerceptionResult,PipelineProfile
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.runtime.map_checkpoint import FrameEvidence,_pipeline_state
from xenova_vehicle.runtime.mapper_catalogs import CatalogDraft,CatalogView,MappingState
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_hazard import MapperHazard
from xenova_vehicle.runtime.mapper_mapping import MappingFrameDraft
from xenova_vehicle.runtime.mapper_scene import SceneCoordinator
from xenova_vehicle.runtime.mapper_world import build_mapping_world
from xenova_vehicle.runtime.parking_bridge import ParkingBridge
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.scene import SceneEvidenceScope,SurveyProfile
from xenova_vehicle.runtime.source_identity import scene_ref_fields
from xenova_vehicle.storage.contracts import StorageJob,canonical,json_bytes
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CELL,PROVENANCE,CheckpointPayload,EvidenceTable,ProvenanceRecord,TileVersion


def uid():return str(uuid4())


@unittest.skipUnless(os.name=='posix','실제 임시 SQLite/writer lock은 대상 POSIX에서 검증한다')
class MapperRetainedWorldTests(unittest.TestCase):
    cell=(4,7)

    def setUp(self):
        import numpy as np
        self.m=mapping_fixture.MapperMappingTests(methodName='runTest');self.addCleanup(self.m.doCleanups);self.m.setUp()
        self.m.grant();self.f=self.m.fixture;self.base=self.m.now;self.context=self.m.context
        self.calibration=self.f.calibration;self.assertEqual(self.calibration.kind,'LIVE')
        self.collector=self.m.mapping.collector;self.method=self.m.profile.sha256
        profile=PipelineProfile(grid=GridSpec(20,20,.1,(-.2,-.7)),free_ttl_us=51_000)
        self.pipeline=PerceptionPipeline(self.calibration,profile)
        self.pipeline.grid=EvidenceGrid(profile.grid,frame_id='odom:'+self.context.odom_epoch,
            calibration_id=self.calibration.sha256,clock_id=self.f.raw.fields['clock_epoch'],kind='LIVE',free_ttl_us=51_000)
        domain=np.ones((20,20),dtype=bool);self.pipeline.grid.set_domain(domain,domain)
        # 실제 RGB parent→frozen geometry→새 REGISTRATION 계보를 먼저 만든다.
        warm=self.frame(self.context,11,0,revision=0)
        self.collector.observe_result(warm.frame,warm.result,self.pipeline,raw_pose=warm.inputs.pose)
        self.initial=self.frame(self.context,12,1000,revision=1,free=True)
        self.collector.observe_result(self.initial.frame,self.initial.result,self.pipeline,raw_pose=self.initial.inputs.pose)
        self.hazard=MapperHazard()
        self.survey_profile=SurveyProfile('d'*64,1,4096,5000,.1,.1,.1,50_000,.1,.1,55_000,'LIVE')
        self.scene=SceneCoordinator(self.context,self.initial.inputs.source,self.survey_profile,
            gateway_instance=self.f.fixture.gateway,evidence_scope=self.initial.frame.scope)
        grant=self.grant();self.assertTrue(self.scene.maintenance(encode(grant),self.initial.inputs.clock_lease,now_j_us=self.m.now))
        hazard=self.hazard.prepare(self.initial,now_us=self.m.now);self.assertTrue(hazard.ready,hazard.reason)
        scene=self.scene.prepare_frame(self.initial,self.initial.result,self.f.state,world_sequence=1,
            hazard_revision=hazard.revision,now_j_us=self.m.now)
        self.assertTrue(scene.observed,scene.reason);self.assertIsNotNone(scene.station)
        self.hazard.accept(hazard);self.scene.accept(scene,now_j_us=self.m.now)
        self.freeze(grant)
        binding=json_bytes(self.context.source_binding,2048)
        binding.update(scene_admission_ref=scene_ref_fields(self.admitted.scene),source_binding_revision='2')
        self.context=replace(self.context,source_binding=canonical(binding));self.m.port.publish(self.context)
        scope=SceneEvidenceScope(self.context,self.f.raw,self.calibration,self.method)
        self.scene.rebind_scope(scope);self.collector.rebind_scope(scope)
        self.current=self.frame(self.context,13,5000,revision=2)
        self.collector.observe_result(self.current.frame,self.current.result,self.pipeline,raw_pose=self.current.inputs.pose)
        self.current,self.state,self.basis=self.catalog(self.current)
        self.hazard_draft=self.hazard.prepare(self.current,now_us=self.m.now)
        self.assertTrue(self.hazard_draft.ready,self.hazard_draft.reason)
        self.scene_draft=self.scene.prepare_frame(self.current,self.current.result,self.f.state,world_sequence=2,
            hazard_revision=self.hazard_draft.revision,now_j_us=self.m.now)
        self.assertTrue(self.scene_draft.observed,self.scene_draft.reason)
        self.assertTrue(self.scene_draft.scene.eligible)

    def frame(self,context,sequence,offset,*,revision,free=False):
        at=self.base+offset;self.m.now=at
        capture=tuple(value+offset for value in self.f.stamp.captured_us)
        raw=self.f.raw.fields;stamp=FrameStamp(raw['acquisition_worker'],raw['stream_instances']['rgb_video'],sequence,
            raw['clock_epoch'],capture,at)
        snapshot=self.f.snapshot();snapshot.update(publication=json_bytes(context.source_binding,2048),
            storage_context=context.fields,context_sha256=context.sha256)
        inputs=self.f.state.read(canonical(snapshot),stamp,now_us=at)
        self.assertIsNotNone(inputs.pose,inputs.reasons)
        rgb=RawFrame('rgb_video',sequence,400000+offset,500000+offset,at,640,360,'NV12',bytes(640*360*3//2),0)
        depth=RawFrame('depth_right',sequence,400001+offset,500001+offset,at,640,480,'RAW16_LE',bytes(640*480*2),0)
        depth_stamp=replace(stamp,stream_id=raw['stream_instances']['depth_right'])
        observation=PerceptionInput('LIVE',self.calibration.sha256,ImageFrame(stamp,640,360,'NV12',rgb.payload),
            ImageFrame(depth_stamp,640,480,'RAW16_LE',depth.payload))
        clocks={name:dict(bridge_sample=dict(j_before_us=str(at),sdk_us=str(at),j_after_us=str(at)),
            sdk_host_timestamp_j_bracket_us=list(map(str,capture))) for name in ('rgb_video','depth_right')}
        sample=CaptureSample(rgb,depth,None,None,clocks)
        item=MapperInput(uid(),self.f.raw.sha256,sample,at+100_000,context_bytes=canonical(snapshot))
        bracket=self.f.state.original_pose_bracket(inputs.pose);parents=[];contexts={}
        for row in bracket.originals:
            record,source_context=self.collector.vehicle_admission_parent(row,sensor_state=self.f.state)
            parents.append(record);contexts[source_context.sha256]=source_context
        scope=SceneEvidenceScope(context,self.f.raw,self.calibration,self.method)
        frame=FrameEvidence.from_capture(scope,observation,sample,pi_interval=inputs.clock_lease.j_interval_to_pi(capture,at),
            observation_sequence=sequence,parents=tuple(parents),contexts=tuple(contexts.values()),clock_lease=inputs.clock_lease,
            now_us=at,reference_vehicle=inputs.vehicle,admission=self.f.state.sensors,registration_valid_for_us=100_000,
            pose_bracket=bracket,sensor_state=self.f.state)
        capture_id=sha256(canonical(stamp.key)).hexdigest()
        self.pipeline.reference=FrozenReference(revision,self.calibration.sha256,
            (SegmentFeature((0.,0.),(1.,0.),.001,'paint',capture_id),))
        fitted=replace(inputs.pose,sample=replace(inputs.pose.sample,x=inputs.pose.sample.x+.05,
            position_error_m=.002,yaw_error_rad=.002))
        fit=None
        if revision:
            self.pipeline._alignment=MapOdomAlignment.from_match(inputs.pose,fitted,observed_j_interval=capture,
                registration_revision=revision,valid_for_us=100_000,position_drift_mps=.001,yaw_drift_rps=.001)
            point=(fitted.sample.x,fitted.sample.y,fitted.sample.yaw)
            fit=RegistrationResult('BOUNDED',point,.002,.002,
                (PoseBox((point[0]-.001,point[0]+.001),(point[1]-.001,point[1]+.001),(point[2]-.001,point[2]+.001)),),
                revision-1,1,1,True)
        else:self.pipeline._alignment=None
        if free:
            self.assertEqual(self.pipeline.grid.add_free((self.cell,),depth_stamp,view_id='EXPLICIT_SOFTWARE_CELL_CLEARANCE',
                origin_xy=(inputs.pose.sample.x,inputs.pose.sample.y),certificate_complete=True),1)
        rgb_new=self.pipeline.rgb_gate.accept(stamp,sha256(rgb.payload).hexdigest())
        depth_new=self.pipeline.depth_gate.accept(depth_stamp,sha256(depth.payload).hexdigest())
        road=replace(road_fixture.road(),source_key=stamp.key)
        summary=dict(kind='LIVE',calibration_id=self.calibration.sha256,now_us=at,rgb_source_key=stamp.key,
            rgb_updated=rgb_new,depth_updated=depth_new,localization_qualified=bool(revision),quarantined=False,
            alignment_metadata=None if not revision else self.pipeline._alignment.fields(),reference_revision=revision,
            mapped_pose=list((inputs.pose.sample.x,inputs.pose.sample.y,inputs.pose.sample.yaw) if not revision else fit.representative))
        result=PerceptionResult(summary,TapeGraph((),source_key=stamp.key),road,None,fit,
            self.pipeline.grid.snapshot(at,clock_id=stamp.clock_id),None)
        return MappingFrameDraft(item,context,self.pipeline,self.collector,inputs,frame,None,0,True,'READY',result=result)

    def grant(self):
        source=self.initial.inputs.source;lease=self.initial.inputs.clock_lease
        grant=schema.SceneGrant(major=1,vehicle=source['vehicle'],pi_host_boot=lease.pi_host_boot,
            gateway_instance=self.f.fixture.gateway,source_admission_revision=lease.source_revision,mapper_device=source['device'],
            issued_pi_us=1_080_000,valid_until_pi_us=1_080_000+14_400_000_000,lifecycle_state=0,request_nonce=UUID(uid()).bytes,
            survey_deadline_pi_us=181_080_000,max_observation_stations=24,purpose=0,lifecycle_revision=1,
            frozen_survey_revision=0,frozen_hazard_revision=0,
            **{name:source[name] for name in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')})
        grant.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(uid()).bytes,admission_revision=1,
            survey_id=UUID(uid()).bytes,survey_revision=1,policy=1,coverage_profile_sha256=bytes.fromhex('d'*64),
            allow_restore_after_mapper_restart=True))
        return grant

    def freeze(self,grant):
        frame=self.initial.frame;collector=self.collector
        context=frame.scope.context_record('METRIC');parents=tuple(record.provenance_id for record in frame.parents)
        parents=(*parents,collector._alignment_record[1]);identity=collector._allocate(parents)
        mask=2
        for parent in parents:mask|=collector._records[parent].source_mask
        key=frame.raw_keys[1];raw=struct.pack('<16sQQ',UUID(key[0]).bytes,key[1],key[2])
        record=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),mask,2,1,len(parents),
            UUID(self.context.pi_host_boot).bytes,*frame.pi_interval,frame.observation_sequence,
            *(parents+(0,)*(4-len(parents))),raw.ljust(128,b'\0'),0,bytes(16)))
        self.free_record=record
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0);body=bytearray(unknown*4096)
        col,row=self.cell;CELL.pack_into(body,(row*64+col)*64,0,1,mask,0.,.1,.01,identity,*frame.pi_interval,
            frame.observation_sequence,self.pipeline.grid.revision,0)
        tile=TileVersion(self.context.map_epoch,0,0,1,bytes(body));refs=(tile.reference,)
        pending=decode(encode(grant),schema.SceneGrant);pending.lifecycle_state=1;pending.lifecycle_revision=2
        pending.request_nonce=UUID(uid()).bytes;pending.survey_deadline_pi_us=1_500_000
        self.m.now+=20
        self.assertTrue(self.scene.maintenance(encode(pending),frame.clock_lease,now_j_us=self.m.now))
        job=StorageJob(self.context,uid(),self.m.now,self.m.now+100_000,str(UUID(bytes=pending.request_nonce)))
        descriptor=self.scene.freeze_scene(job,current_tile_references=refs,current_hazard_revision=1,now_j_us=self.m.now)
        state=_pipeline_state(self.pipeline,self.context)
        state.update(tile_revision_highwater=1,reference_provenance=dict(collector._reference_geometry),
            alignment_provenance=collector._alignment_record[1],alignment_clock=collector._alignment_clock)
        metadata,pages=encode_mapper_state(state)
        contexts=dict(collector._contexts);contexts[context.sha256]=context;contexts.update((page.sha256,page) for page in pages)
        records=dict(collector._records);records[identity]=record
        evidence=EvidenceTable(tuple(records[key] for key in sorted(records)),tuple(contexts[key] for key in sorted(contexts)))
        self.request=CheckpointRequest(job,CheckpointPayload.build(job,(tile,),evidence,scene=dict(descriptor,mapper_state=metadata),
            tile_references=refs,durable_cutoff_pi_us=max(row.pi_upper for row in records.values())))
        self.scene.adopt_freeze(self.request,current_tile_references=refs,current_hazard_revision=1,now_j_us=self.m.now,
            all_tiles=(tile,),mapper_state=metadata)
        self.m.now+=10;self.receipt=self.m.store.commit(job,self.request.payload,current_context_sha256=self.context.sha256)
        self.assertEqual(self.receipt.status,'COMMITTED')
        self.assertTrue(self.scene.complete_freeze(self.request,self.receipt,now_j_us=self.m.now+10))
        admitted=decode(encode(pending),schema.SceneGrant);summary=decode(self.scene.scene._frozen.summary,schema.SurveySummary)
        admitted.lifecycle_state=2;admitted.lifecycle_revision=3;admitted.frozen_evidence_sha256=summary.evidence_sha256
        admitted.frozen_survey_revision=summary.survey_revision;admitted.frozen_hazard_revision=summary.hazard_revision
        self.m.now+=20;self.assertTrue(self.scene.maintenance(encode(admitted),frame.clock_lease,now_j_us=self.m.now))
        self.assertTrue(self.scene.scene.eligible);self.admitted=admitted

    def catalog(self,draft):
        inputs=draft.inputs;stamp=draft.frame.observation.rgb.stamp;grid=self.pipeline.grid
        lane=LaneCatalog(self.context,clock_id=stamp.clock_id,calibration_id=self.calibration.sha256,frame_id=grid.frame_id)
        source=LaneSource(stamp,self.calibration.sha256,self.context.map_epoch,grid.frame_id,self.context.odom_epoch,
            grid.revision,self.pipeline._alignment.registration_revision,True,'LIVE')
        fit=draft.result.registration;x,y,yaw=fit.representative
        prior=PoseBox((x-.002,x+.002),(y-.002,y+.002),(yaw-.002,yaw+.002))
        observed=lane.observe(draft.result.road,source,prior,prior_qualified=True,crossing=b'\0')
        self.assertEqual(observed.status,'NEW')
        job=StorageJob(self.context,uid(),self.m.now,self.m.now+100_000)
        head=self.m.store.catalog_heads(job,current_context_sha256=self.context.sha256)
        need=lane.allocation_need(observed,head,job,reservation_nonce=uid(),now_us=self.m.now,count=64)
        slot_hwm=next(hwm for kind,hwm,_ in head.heads if kind=='SLOT')
        payload=CheckpointPayload.build(job,evidence=EvidenceTable(reservations=(need.reservation,),
            catalog_highwaters=(need.reservation.end_exclusive,slot_hwm)),
            intent='CATALOG_RESERVATION')
        receipt=self.m.store.commit(job,payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        lane.apply_committed_range(need,job,payload,receipt,current_context_sha256=self.context.sha256,now_us=self.m.now)
        capture=sha256(canonical(stamp.key)).hexdigest();root=self.collector._reference_geometry[capture]
        model=lane.update(observed,provenance_ids=(root,root));self.assertEqual(model.lane_ids,(1,2))
        basis_owner=RoadBasis();basis=basis_owner.observe(lane,model,current_stamp=stamp,resolution_m=grid.spec.resolution_m,
            now_us=self.m.now,max_age_us=draft.original_job.expires_us-stamp.captured_us[0])
        self.assertEqual(basis.status,'READY')
        parking=ParkingBridge(self.context,clock_id=stamp.clock_id,map_frame_id=grid.frame_id,body=self.calibration.body,
            method_manifest_sha256=self.method,input_kind='LIVE',calibration_profile=self.calibration)
        state=MappingState(lane,parking,EvidenceTable(),CatalogView(observed.status,model,None,road_basis=basis),basis_owner)
        catalog=CatalogDraft(draft.original_job,self.context,0,state)
        return replace(draft,catalog_draft=catalog),state,basis

    def build(self,*,scene=True,**changes):
        draft=self.current;inputs=draft.inputs
        prepared=max(self.m.now,self.scene_draft.prepared_j_us)
        arguments=dict(context=self.context,source=inputs.source,calibration=self.calibration,sensors=self.f.state.sensors,
            current_vehicle=inputs.vehicle,clock_lease=inputs.clock_lease,sequence=2,completed_j_us=prepared+10,
            published_j_us=prepared+20,scene_draft=self.scene_draft if scene else None,
            hazard_draft=self.hazard_draft if scene else None)
        arguments.update(changes)
        return build_mapping_world(draft,self.state,self.basis,**arguments)

    def export(self,built):
        target=os.environ.get('XENOVA_RETAINED_WORLD_EXPORT')
        if not target:return
        def blob(raw):return dict(base64=b64encode(raw).decode('ascii'),sha256=sha256(raw).hexdigest(),length=len(raw))
        world=built.message();inputs=self.current.inputs
        fields=dict(profile='jetson-retained-world-producer-vector-1',world=blob(built.payload),
            scene_grant=blob(self.scene_draft.scene.grant_bytes),clock_trust=blob(inputs.clock_lease.trust_bytes),
            clock_probe=blob(inputs.clock_lease.raw_probe_bytes),vehicle_sample=blob(inputs.vehicle.wire),
            source=inputs.source,storage_context=self.context.fields,scene_ref=scene_ref_fields(world.scene),
            times=dict(observed_us=world.observed_us,completed_us=world.completed_us,published_us=world.published_us,
                captured_us=list(self.current.frame.observation.capture_interval),
                published_pi_interval=list(inputs.clock_lease.j_interval_to_pi((world.published_us,world.published_us),world.published_us))),
            checkpoint=dict(job=self.request.job.fields,receipt=self.receipt.fields,payload_sha256=self.request.payload.sha256),
            calibration=self.calibration.to_dict(),assumptions='SOFTWARE_GEOMETRY_CALIBRATION_CLEARANCE_ONLY_NO_PHYSICAL_ACCEPTANCE')
        with Path(target).open('x',encoding='utf-8',newline='\n') as output:
            json.dump(fields,output,sort_keys=True,separators=(',',':'));output.write('\n')

    def test_actual_live_freeze_commit_admitted_source_and_world_preserve_original_free_age(self):
        import numpy as np
        grid=self.pipeline.grid;before={name:getattr(grid,name).tobytes() for name in ('state','observed_us','time_known','support','permission')}
        col,row=self.cell
        self.assertEqual(int(self.current.result.map_snapshot['arrays']['state'][row,col]),UNKNOWN)
        self.assertEqual(int(grid.state[row,col]),FREE)
        built=self.build();self.assertIsNotNone(built)
        value=built.message();index=row*grid.spec.width+col
        self.assertEqual(value.occupancy[index],0);self.assertTrue(value.cell_flags[index]&2)
        self.assertEqual(value.scene,self.admitted.scene);self.assertEqual(tuple(lane.lane_id for lane in value.lane_model.lanes),(1,2))
        pi_upper=self.current.inputs.clock_lease.j_interval_to_pi((value.published_us,value.published_us),value.published_us)[1]
        self.assertGreaterEqual(struct.unpack_from('<I',value.age_le32,index*4)[0],(pi_upper-self.free_record.pi_lower+999)//1000)
        self.assertEqual({name:getattr(grid,name).tobytes() for name in before},before)
        self.assertEqual(self.receipt.job_digest,self.request.digest)
        self.assertEqual(self.m.db.metadata['database_uuid'],self.request.job.context.database_uuid)
        self.assertFalse(built.world_valid or built.live_authority or built.metric_ready)
        self.assertEqual(np.count_nonzero(np.frombuffer(value.cell_flags,dtype=np.uint8)&2),1)
        self.export(built)

    def test_original_scene_expiry_and_foreign_current_source_cannot_publish_retained(self):
        self.assertIsNotNone(self.build())
        expires=self.scene_draft.expires_j_us
        self.assertLess(expires,self.pipeline._alignment.observed_j_interval[0]+self.pipeline._alignment.valid_for_us)
        self.assertIsNone(self.build(published_j_us=expires))
        live=self.build(scene=False,published_j_us=expires)
        self.assertIsNotNone(live);self.assertFalse(live.message().HasField('scene'))
        self.assertIsNone(self.build(source=dict(self.current.inputs.source,mapper_instance=uid())))

    def test_actual_revoke_and_new_hit_hazard_remove_retained_eligibility(self):
        self.assertIsNotNone(self.build())
        self.hazard.accept(self.hazard_draft);self.scene.accept(self.scene_draft,now_j_us=self.m.now+30)
        self.scene.revoke('HAZARD',now_j_us=self.m.now+40)
        self.hazard_draft=self.hazard.prepare(self.current,now_us=self.m.now+50)
        self.scene_draft=self.scene.prepare_frame(self.current,self.current.result,self.f.state,world_sequence=3,
            hazard_revision=self.hazard_draft.revision,now_j_us=self.m.now+50)
        self.assertFalse(self.scene_draft.scene.eligible)
        live=self.build();self.assertIsNotNone(live);self.assertFalse(live.message().HasField('scene'))
        self.assertFalse(any(flags&2 for flags in live.message().cell_flags))
        self.hazard.cancel(self.hazard_draft);self.scene.cancel(self.scene_draft)
        new=self.frame(self.context,14,6000,revision=3)
        x,y,x1,y1=self.pipeline.grid.spec.bounds(*self.cell)
        self.pipeline.grid.add_hits(((x+.01,y+.01,x1-.01,y1-.01),),new.frame.observation.depth.stamp,
            view_id='actual-new-software-hit',origin_xy=(new.inputs.pose.sample.x,new.inputs.pose.sample.y))
        result=replace(new.result,map_snapshot=self.pipeline.grid.snapshot(self.m.now,clock_id=self.f.raw.fields['clock_epoch']))
        new=replace(new,result=result);hazard=self.hazard.prepare(new,now_us=self.m.now)
        self.assertTrue(hazard.ready,hazard.reason);self.assertTrue(hazard.changed and hazard.revoke_required)
        self.assertGreater(hazard.revision,self.admitted.frozen_hazard_revision)
        self.scene.hazard(hazard.revision,now_j_us=self.m.now)
        self.assertFalse(self.scene.scene.eligible)


if __name__=='__main__':unittest.main()
