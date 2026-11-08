"""Original full-checkpoint receipts across four actual SQLite LOAD pages.

Synthetic sensor/pose values exercise Scene semantics; this does not run OAK, MQTT,
the resident service, Pi/C consumers or vehicle motion.
"""
from dataclasses import replace
import os
import unittest
from uuid import UUID

import test_scene as fixture
from xenova_drive_world import schema,encode,decode
from xenova_vehicle.runtime.map_checkpoint import CheckpointPages,MapCheckpointNotReady
from xenova_vehicle.runtime.scene import MapperScene,RestoredSurvey,SceneError
from xenova_vehicle.storage.contracts import StorageJob,LoadRequest,canonical
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload,ContextRecord,TileVersion,CELL


@unittest.skipUnless(os.name=='posix','actual SQLite writer requires POSIX')
class CompleteSceneRestoreTests(unittest.TestCase):
    def setUp(self):
        f=fixture.MapperSceneTests(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups)
        self.f=f
        original,evidence=f.copies()
        self.tiles=tuple(TileVersion(f.context.map_epoch,index-64,0,index+1,original[0].payload)
            for index in range(128))
        selected=()
        for batch in range(3):
            copies=self.tiles[batch*32:(batch+1)*32];selected+=tuple(t.reference for t in copies)
            job=StorageJob(f.context,fixture.uid(200+batch),f.now,1_500_000)
            payload=CheckpointPayload.build(job,copies,evidence,tile_references=selected,durable_cutoff_pi_us=1010002)
            self.assertEqual(f.store.commit(job,payload,current_context_sha256=f.context.sha256).status,'COMMITTED')
        grant=schema.SceneGrant();grant.CopyFrom(f.grant)
        grant.lifecycle_state=1;grant.lifecycle_revision=2;grant.request_nonce=UUID(fixture.uid(232)).bytes
        grant.survey_deadline_pi_us=1_500_000
        f.mapper.receive_grant(encode(grant),f.clock,now_j_us=f.now)
        job=StorageJob(f.context,fixture.uid(233),f.now,1_500_000,fixture.uid(232))
        selected=tuple(t.reference for t in self.tiles)
        scene=f.mapper.freeze_scene(job,current_tile_references=selected,current_hazard_revision=1,now_j_us=f.now)
        # Unreferenced metadata belongs to the original transaction but not page closure.
        extra=ContextRecord(canonical(dict(kind='RAW',body=dict(profile='unreferenced-test-metadata',value=1))))
        evidence=replace(evidence,contexts=(*evidence.contexts,extra))
        payload=CheckpointPayload.build(job,self.tiles[96:],evidence,tile_references=selected,
            scene=scene,durable_cutoff_pi_us=1010002)
        self.request=CheckpointRequest(job,payload)
        f.mapper.adopt_freeze(self.request,current_tile_references=selected,current_hazard_revision=1,
            now_j_us=f.now,all_tiles=self.tiles)
        f.now+=20
        self.receipt=f.store.commit(job,payload,current_context_sha256=f.context.sha256)
        self.assertTrue(f.mapper.complete_freeze(self.request,self.receipt,now_j_us=f.now+10))
        self.reference=canonical(f.store.latest_complete(expires_us=f.now+500_000))
        self.pages=[]
        for index in range(4):
            load=LoadRequest(StorageJob(f.context,fixture.uid(240+index),f.now,f.now+500_000),
                self.reference,canonical(dict(kind='CHECKPOINT_PAGE',cursor=index*32)))
            self.pages.append(f.store.load(load,current_context_sha256=f.context.sha256))
        self.grant=schema.SceneGrant();self.grant.CopyFrom(grant)
        self.grant.lifecycle_revision=3;self.grant.purpose=1;self.grant.request_nonce=UUID(fixture.uid(251)).bytes
        self.grant.frozen_evidence_sha256=bytes.fromhex(payload.sha256)
        self.grant.frozen_survey_revision=1;self.grant.frozen_hazard_revision=1
        self.grant.restore_checkpoint_sha256=bytes.fromhex(payload.sha256)

    def complete(self,pages=None):
        loader=CheckpointPages(self.reference,self.f.context);result=None
        for page in self.pages if pages is None else pages:
            result=loader.accept(page,now_us=self.f.now+10)
        return result

    def mapper(self,grant=None):
        f=self.f
        mapper=MapperScene(f.context,fixture.source(),f.profile,gateway_instance=fixture.uid(4),evidence_scope=f.scope)
        mapper.receive_grant(encode(self.grant if grant is None else grant),f.clock,now_j_us=f.now+20)
        current=fixture.observation(2,1020200,calibration_id=f.calibration.sha256)
        mapper.observe(current,now_j_us=1020220);f.station(mapper,current)
        return mapper,current

    def test_full_128_actual_pages_preserve_original_delta_receipt_and_restore_exact_original_sha(self):
        complete=self.complete();self.assertEqual(len(complete.tiles),128)
        self.assertEqual(complete.original_commit.receipt,self.receipt)
        self.assertEqual(complete.original_commit.copy_tiles,tuple(t.reference for t in self.request.payload.tiles))
        original=complete.original_commit.validate(complete.original_job,complete.manifest,complete.checkpoint_ref)
        self.assertEqual(original,self.request.payload.sha256)
        self.assertNotEqual(original,self.pages[0].payload.sha256)
        mapper,current=self.mapper()
        raw=mapper.restore_complete(complete,current,now_j_us=1020240)
        summary=decode(raw,schema.SurveySummary)
        self.assertEqual((summary.status,summary.positive_cell_count,summary.evidence_flags),(4,128,63))
        self.assertEqual(summary.evidence_sha256.hex(),original)
        self.assertFalse(mapper.eligible)
        self.assertIs(type(mapper._frozen),RestoredSurvey)
        self.assertIs(mapper._frozen.complete,complete)
        for before,after in zip(self.tiles,complete.tiles):
            self.assertEqual(after.payload,before.payload)
            self.assertEqual(CELL.unpack_from(after.payload)[7:10],(1010000,1010002,1))

    def test_page_sha_cannot_replace_original_selected_checkpoint_sha(self):
        value=schema.SceneGrant();value.CopyFrom(self.grant)
        value.restore_checkpoint_sha256=bytes.fromhex(self.pages[0].payload.sha256)
        mapper,current=self.mapper(value)
        with self.assertRaisesRegex(SceneError,'original full checkpoint SHA'):
            mapper.restore_complete(self.complete(),current,now_j_us=1020240)
        self.assertFalse(mapper.eligible);self.assertIsNone(mapper._frozen)

    def test_older_observation_cannot_replace_the_exact_accepted_restore_capture(self):
        mapper,older=self.mapper()
        newer=fixture.observation(3,1020300,calibration_id=self.f.calibration.sha256)
        self.assertTrue(mapper.observe(newer,now_j_us=1020320))
        with self.assertRaisesRegex(SceneError,'exact accepted current observation'):
            mapper.restore_complete(self.complete(),older,now_j_us=1020340)
        self.assertIs(mapper._observation,newer)
        self.assertIsNone(mapper._frozen);self.assertFalse(mapper.eligible)

    def test_missing_or_mixed_original_commit_is_not_a_restored_scene(self):
        legacy=[replace(page,original_commit=None) for page in self.pages]
        complete=self.complete(legacy)
        mapper,current=self.mapper()
        with self.assertRaisesRegex(SceneError,'original checkpoint commit descriptor'):
            mapper.restore_complete(complete,current,now_j_us=1020240)
        loader=CheckpointPages(self.reference,self.f.context)
        self.assertIsNone(loader.accept(self.pages[0],now_us=self.f.now+10))
        with self.assertRaises(MapCheckpointNotReady):
            loader.accept(legacy[1],now_us=self.f.now+10)
        self.assertEqual(loader._next,32)
