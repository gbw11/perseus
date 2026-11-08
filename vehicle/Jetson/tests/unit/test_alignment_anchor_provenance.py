"""원 BOUNDED anchor REG의 동일성과 CP closure를 검산하는 software 시험이다.

기존 작은 capture/registration 경계 fixture와 실제 SQLite CP/LOAD를 재사용한다.
관측 geometry fixture는 실차 정확도나 Scene/이동 권한 수락을 뜻하지 않는다.
"""

from dataclasses import replace
from hashlib import sha256
import os
import unittest

from xenova_vehicle.runtime.map_checkpoint import MapCheckpointCollector,MapCheckpointNotReady,CheckpointPages
from xenova_vehicle.runtime.mapper_mapping import _fork_collector
from xenova_vehicle.storage.contracts import LoadRequest,canonical,json_bytes
from xenova_vehicle.storage.records import PROVENANCE

import test_alignment_flow as alignment_fixture
import test_map_checkpoint as checkpoint_fixture


@unittest.skipUnless(os.name=='posix','실제 D writer lock/SQLite는 대상 POSIX에서 검사한다')
class AlignmentAnchorProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=checkpoint_fixture.MapCheckpointTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        limits=dict(localization_valid_for_us=800_000,localization_position_drift_mps=.001,
            localization_yaw_drift_rps=.001)
        self.restored,self.pipeline,self.execute,self.next_frame=self.fixture._restored_nonidentity_fixture(
            raw_x=.11,position_error=.012,spacing_us=200_000,localization_limits=limits)
        self.collector=self.fixture.collector

    @staticmethod
    def closure(records,roots):
        pending=list(roots);seen=set()
        while pending:
            identity=pending.pop()
            if identity in seen:continue
            seen.add(identity);pending.extend(records[identity].parents)
        return seen

    def next_result(self):
        observation,pose,frame=self.next_frame(3,.012)
        graph=alignment_fixture.AlignmentFlowTests(methodName='runTest').graph(alignment_fixture.CORNER_GT,.1)
        result=self.execute(self.pipeline,observation,pose,graph)
        self.assertEqual(result.registration.status,'BOUNDED')
        return result,pose,frame

    def test_actual_anchor_remains_original_ancestor_with_four_parent_limit(self):
        pin=self.collector._propagation_anchor_record;anchor=self.pipeline._propagation_anchor
        original=self.collector._records[pin[1]]
        self.assertEqual(pin,(sha256(canonical(anchor.fields())).hexdigest(),self.collector._alignment_record[1],
            sha256(original.payload).hexdigest()))
        result,pose,frame=self.next_result()
        # 원 이전 PI_ODOM도 함께 전달해 실제 4-parent 경계를 검사한다. 새 표본/시각은 만들지 않는다.
        earlier=next(record for record in self.collector._records.values()
            if PROVENANCE.unpack(record.payload)[2]==0 and record.source_mask==16
            and record.provenance_id!=frame.parents[0].provenance_id)
        frame=replace(frame,parents=frame.parents+(earlier,))
        self.collector.observe_result(frame,result,self.pipeline,raw_pose=pose)
        current=self.collector._records[self.collector._alignment_record[1]]
        self.assertEqual(len(current.parents),4)
        self.assertNotIn(pin[1],current.parents)
        self.assertIn(pin[1],self.closure(self.collector._records,current.parents))
        self.assertEqual(self.collector._propagation_anchor_record,pin)
        self.assertIs(self.pipeline._propagation_anchor,anchor)
        self.assertIs(self.collector._records[pin[1]],original)
        self.assertTrue(all(len(record.parents)<=4 for record in self.collector._records.values()))

    def test_historical_missing_or_foreign_pin_cannot_mint_a_current_root(self):
        pin=self.collector._propagation_anchor_record
        result,pose,frame=self.next_result()
        self.collector.observe_result(frame,result,self.pipeline,raw_pose=pose)
        current_id=self.collector._alignment_record[1]
        self.assertNotEqual(current_id,pin[1])
        for bad in (None,('0'*64,pin[1],pin[2]),(pin[0],self.fixture.grant.end_exclusive,pin[2]),
                (pin[0],current_id,pin[2])):
            with self.subTest(pin=bad):
                self.collector._propagation_anchor_record=bad
                cursor=self.collector._next;records=dict(self.collector._records)
                with self.assertRaises(MapCheckpointNotReady):
                    self.collector.observe_result(frame,result,self.pipeline,raw_pose=pose)
                self.assertEqual(self.collector._next,cursor)
                self.assertEqual(self.collector._records,records)
        self.collector._propagation_anchor_record=pin
        original=self.collector._records[pin[1]]
        fields=list(PROVENANCE.unpack(original.payload));fields[8]+=1
        self.collector._records[pin[1]]=replace(original,payload=PROVENANCE.pack(*fields))
        cursor=self.collector._next
        with self.assertRaisesRegex(MapCheckpointNotReady,'registration pin changed'):
            self.collector._frame_roots(frame,self.pipeline,pose)
        self.assertEqual(self.collector._next,cursor)
        self.collector._records[pin[1]]=original
        parents=self.collector._frame_roots(frame,self.pipeline,pose)
        self.assertIn(pin[1],self.closure(self.collector._records,parents))
        self.assertIs(self.collector._records[pin[1]],original)

    def test_reference_closure_missing_original_anchor_is_not_repaired(self):
        pin=self.collector._propagation_anchor_record
        seed=next(record for record in self.collector._records.values()
            if PROVENANCE.unpack(record.payload)[2]==3
            and pin[1] not in self.closure(self.collector._records,(record.provenance_id,)))
        result,pose,frame=self.next_result()
        self.collector._reference_geometry={next(iter(self.collector._reference_geometry)):seed.provenance_id}
        cursor=self.collector._next
        with self.assertRaisesRegex(MapCheckpointNotReady,'reference closure lost propagation anchor'):
            self.collector.observe_result(frame,result,self.pipeline,raw_pose=pose)
        self.assertEqual(self.collector._next,cursor)
        self.assertEqual(self.collector._propagation_anchor_record,pin)

    def test_fork_and_trim_preserve_the_immutable_anchor_pin_and_its_original_closure(self):
        pin=self.collector._propagation_anchor_record;original=self.collector._records[pin[1]]
        fork=_fork_collector(self.collector,self.pipeline)
        self.assertIs(fork._propagation_anchor_record,pin)
        self.assertIsNot(fork._records,self.collector._records)
        # 다른 root 없이도 anchor 자체가 trim root임을 분리해 검사한다.
        for name in ('_cells','_rgb_refs','_vehicle_refs','_reference_geometry','_durable_tiles','_subsystem_state'):
            setattr(fork,name,{})
        fork._durable_catalog=();fork._alignment_record=None;fork._atlas_owner=None
        fork._trim()
        self.assertIs(fork._records[pin[1]],original)
        self.assertEqual(set(fork._records),self.closure(self.collector._records,(pin[1],)))
        self.assertEqual(self.collector._propagation_anchor_record,pin)
        self.assertTrue(self.collector._reference_geometry)

    def test_actual_complete_keeps_old_proof_but_fresh_restore_has_no_propagation_anchor(self):
        f=self.fixture;ref=canonical(f.store.latest_complete(expires_us=f.sensor.now+5_000_000))
        request=LoadRequest(f.job(),ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        complete=CheckpointPages(ref,f.context).accept(f.store.load(request,current_context_sha256=f.context.sha256),now_us=f.sensor.now)
        old_pin=self.collector._propagation_anchor_record
        self.assertIn(old_pin[1],{record.provenance_id for record in complete.evidence.provenance})
        state=json_bytes(complete.state,512*1024)
        self.assertNotIn('propagation_anchor',state)
        self.assertNotIn('propagation_anchor_provenance',state)
        self.assertIsNone(self.restored._alignment)
        self.assertIsNone(self.restored._propagation_anchor)
        grant=f.store.reserve_provenance(f.job(),checkpoint_fixture.uid(),block_count=256,current_context_sha256=f.context.sha256)
        fresh=MapCheckpointCollector(f.scope,grant,now_us=f.sensor.now)
        fresh._propagation_anchor_record=old_pin
        selected=dict(status='COMPLETE',database_uuid=f.context.database_uuid,selected_checkpoint=json_bytes(ref,2048))
        fresh.retain_complete(complete,self.restored,selected_bootstrap=selected)
        self.assertIsNone(fresh._propagation_anchor_record)
        self.assertIsNone(self.restored._propagation_anchor)
        self.assertIn(old_pin[1],fresh._records)
