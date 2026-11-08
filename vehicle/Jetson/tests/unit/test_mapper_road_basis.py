"""실제 Catalog prepare/accept/discard와 RoadBasis의 현재 관측 경계를 검사한다.

range/clock/기하는 명시 software fixture다. 새 SQL·NativeResources나 시뮬레이션을
실행하지 않으며 이 경로의 READY는 World/scene/추진 허가가 아니다.
"""

from dataclasses import replace
import unittest

import test_mapper_catalogs as catalog_fixture
import test_road_basis as basis_fixture
import test_lane_catalog as lane_fixture
from test_parking_catalog import publication_context
from xenova_vehicle.mapping.road_basis import RoadBasis
from xenova_vehicle.runtime.mapper_catalogs import MapperCatalogs
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.storage.records import CheckpointPayload,LoadedCheckpoint


class MapperRoadBasisTests(unittest.TestCase):
    def fixture(self):
        f=catalog_fixture.MapperCatalogsTests(methodName='runTest');f.setUp()
        f.owner=f.make_owner(aisles=(),roles=())
        return f

    def ready(self,f):
        # lane 전용 최초 예약만 한다. parking range/provenance를 공유하거나 여섯 단계 준비를 반복하지 않는다.
        first,now=f.accept_frame()
        self.assertEqual(first.view.road_basis.status,'WAIT_CURRENT')
        head,job=f.heads(now)
        f.owner.maintenance(now_us=now,head=head,head_job=job,reservation_nonce=catalog_fixture.uid(501))
        f.complete(now)
        current,now=f.accept_frame()
        self.assertEqual(current.view.road_basis.status,'READY')
        return current,now

    def test_current_update_forks_events_and_keeps_original_prefix_proof_after_lane_replacement(self):
        f=self.fixture();initial,_=self.ready(f);owner=f.owner
        old=owner._accepted.road_basis;first=owner.basis_snapshot;origin_root=first.roots[0]
        job,result,args=f.frame();draft=owner.prepare(job,result,**args)
        self.assertIsNot(draft.state.road_basis,old)
        self.assertIsNot(draft.state.road_basis._events,old._events)
        self.assertIs(draft.state.road_basis._events[0][0],old._events[0][0])
        self.assertIs(owner.basis_snapshot,first);self.assertIsNot(draft.view.road_basis.latest_lane_source,first.latest_lane_source)
        self.assertEqual(draft.view.road_basis.points_f32,first.points_f32)
        self.assertIsNone(f.mailbox.main_peek())
        owner.discard(draft);self.assertIs(owner._accepted.road_basis,old)
        job,result,args=f.frame();draft=owner.prepare(job,result,**args);owner.accept(draft)
        current=owner.basis_snapshot
        self.assertEqual(current.status,'READY');self.assertEqual(current.reference_id,first.reference_id)
        self.assertEqual(current.points_f32,first.points_f32)
        self.assertEqual(current.origin_source.stamp,initial.view.lane.source.stamp)
        self.assertEqual(current.latest_lane_source.stamp,args['stamp'])
        self.assertNotIn(origin_root,owner._accepted.lane.originals())
        self.assertIn(origin_root,{r.provenance_id for r in owner._accepted.evidence.provenance})
        self.assertIn(origin_root,owner._accepted.road_basis.originals())
        fields=owner.state_fields()['road_basis']
        self.assertEqual(fields['reference_revision'],first.reference_revision)
        self.assertEqual(fields['points_f32'],first.points_f32.hex())
        self.assertFalse(owner.view.summary()['metric_ready'] or owner.view.summary()['live_authority'])

    def test_unknown_and_quarantine_never_expose_old_ready_and_discard_preserves_accepted_view(self):
        f=self.fixture();self.ready(f);owner=f.owner;original=owner.basis_snapshot
        job,result,args=f.frame(ambiguous=True);draft=owner.prepare(job,result,**args)
        self.assertEqual(draft.view.road_basis.status,'WAIT_CURRENT')
        self.assertEqual(draft.view.road_basis.points_f32,original.points_f32)
        self.assertIs(owner.basis_snapshot,original)
        owner.discard(draft);self.assertIs(owner.basis_snapshot,original)
        job,result,args=f.frame(ambiguous=True);draft=owner.prepare(job,result,**args);owner.accept(draft)
        self.assertEqual(owner.basis_snapshot.status,'WAIT_CURRENT');self.assertIsNone(owner.view.lane)
        self.assertIs(owner._accepted.lane.latest.source,original.latest_lane_source)
        job,result,args=f.frame();result=replace(result,summary=dict(result.summary,quarantined=True))
        draft=owner.prepare(job,result,**args);owner.accept(draft)
        self.assertEqual(owner.basis_snapshot.status,'WAIT_CURRENT')
        self.assertEqual(owner.basis_snapshot.reason,'MAP_QUARANTINED')
        self.assertEqual(owner.basis_snapshot.reference_id,original.reference_id)
        self.assertEqual(owner.basis_snapshot.origin_source.stamp,original.origin_source.stamp)

    def test_scene_only_transition_keeps_basis_id_prefix_age_and_waits_for_actual_new_model(self):
        f=self.fixture();_,now=self.ready(f);owner=f.owner;original=owner.basis_snapshot
        old_model=owner._accepted.road_basis._events[0][0]
        changed=publication_context(owner.context,revision=2,scene=dict(admission_id=catalog_fixture.uid(502),revision='1'))
        self.assertTrue(owner.replace_publication_context(changed,now_us=now+1))
        waiting=owner.basis_snapshot
        self.assertEqual(waiting.status,'WAIT_CURRENT');self.assertEqual(waiting.reference_id,original.reference_id)
        self.assertEqual(waiting.points_f32,original.points_f32)
        self.assertEqual(waiting.origin_source.stamp.captured_us,original.origin_source.stamp.captured_us)
        job,result,args=f.frame();draft=owner.prepare(job,result,**args);owner.accept(draft)
        fresh=owner.basis_snapshot
        self.assertEqual(fresh.status,'READY');self.assertEqual(fresh.reference_id,original.reference_id)
        self.assertEqual(fresh.points_f32,original.points_f32)
        self.assertIs(owner._accepted.road_basis._events[0][0],old_model)
        self.assertEqual(owner._accepted.road_basis._latest.original_context_sha256,changed.sha256)
        self.assertNotEqual(old_model.original_context_sha256,changed.sha256)

    def test_complete_restore_waits_and_current_map_basis_is_preserved_as_unapproved_material(self):
        catalog,scope,raw=basis_fixture.scoped_catalog()
        model=basis_fixture.update(catalog,raw=raw);basis=RoadBasis();basis_fixture.consume(basis,catalog,model)
        complete,_=basis_fixture.restore_fixture(basis,catalog,scope)
        def owner():
            return MapperCatalogs(catalog.context,mailbox=MapperStorageMailbox(catalog.context.sha256),
                clock_id=catalog.clock_id,map_frame_id=catalog.frame_id,body=scope.calibration.body,
                method_manifest_sha256=scope.method_manifest_sha256,input_kind=scope.calibration.kind,
                calibration_profile=scope.calibration,parking_aisles=())
        restored=owner();restored.restore_complete(complete,current_scope=scope,now_us=1_190_020)
        self.assertEqual(restored.basis_snapshot.status,'WAIT_CURRENT')
        self.assertEqual(restored.basis_snapshot.reference_id,basis.snapshot().reference_id)
        self.assertEqual(restored.basis_snapshot.points_f32,basis.snapshot().points_f32)
        self.assertEqual(canonical(restored.state_fields()['road_basis']),canonical(basis.state_fields()))
        request,completed,_,head=complete.page_receipts[-1]
        loaded=LoadedCheckpoint(request,complete.original_job,complete.checkpoint_ref,
            CheckpointPayload(complete.manifest,complete.tiles,complete.evidence),head,completed)
        partial=owner();partial.restore_loaded(loaded,now_us=1_190_020)
        self.assertIsNone(partial.basis_snapshot)
        self.assertEqual(partial.view.summary()['road_basis_status'],'NOT_READY')
        self.assertIsNone(partial.view.summary()['road_reference_revision'])
        self.assertIn('ROAD_BASIS_COMPLETE_REQUIRED',partial.view.reasons)
        self.assertEqual(partial._accepted.road_basis._events,[])
        self.assertEqual(canonical(partial.state_fields()['road_basis']),canonical(basis.state_fields()))
        legacy=owner();legacy.restore_loaded(lane_fixture.loaded_state(catalog),now_us=1_200_000)
        self.assertEqual(legacy.basis_snapshot.status,'WAIT_CURRENT')
        self.assertIsNone(legacy._accepted.road_basis_material)
        self.assertIsNone(legacy.view.lane)
