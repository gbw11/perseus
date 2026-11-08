"""freeze producer의 원 lease 기한과 게시 전 job 보존을 실제 Scene에 대조한다.

원 Scene/clock/증거는 기존 명시 software fixture다. snapshot·queue 준비 leaf만
유한 대역이며 실제 Scene validator, CheckpointRequest와 storage mailbox를 쓴다.
SQL/native 완료나 새 센서 관측·실물 시계 수락을 주장하지 않는다.
"""

from dataclasses import replace
import os
from types import SimpleNamespace
import unittest
from uuid import UUID

import test_mapper_scene as scene_fixture
from xenova_drive_world import decode,encode,schema
from xenova_vehicle.runtime.map_checkpoint import MapCheckpointNotReady
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload


class _CollectorLeaf:
    def __init__(self,fixture,metadata):
        self.fixture=fixture;self.metadata=metadata;self.jobs=[];self.discarded=[]
        self._draft_snapshot=None;self.fail_before_descriptor=False

    def snapshot(self,pipeline,job,**kwargs):
        self.jobs.append(job)
        self._draft_snapshot=SimpleNamespace(atlas_owner=None,mapper_state_ref=self.metadata,
            full_tiles=lambda:self.fixture.tiles)
        return self._draft_snapshot

    def current_tile_references(self,pipeline,snapshot):
        if self.fail_before_descriptor:raise MapCheckpointNotReady('EXPLICIT_UNPUBLISHED_PREPARE_FAILURE')
        return self.fixture.refs

    def discard_snapshot(self,snapshot):
        if snapshot is not self._draft_snapshot:raise AssertionError('wrong original snapshot')
        self.discarded.append(snapshot);self._draft_snapshot=None


@unittest.skipUnless(os.name=='posix','원 Scene fixture의 임시 SQLite/lock은 POSIX에서 검사한다')
class MapperSceneFreezeDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.fixture=scene_fixture.MapperSceneCheckpointTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        fixture=self.fixture;self.now=fixture.f.now;self.coordinator=fixture.coordinator
        grant=decode(self.coordinator.scene.grant_bytes,schema.SceneGrant)
        grant.lifecycle_revision+=1;grant.survey_deadline_pi_us=self.now+3_000_000
        self.assertTrue(self.coordinator.maintenance(encode(grant),fixture.f.clock,now_j_us=self.now))
        self.original_grant=self.coordinator.scene.grant_bytes
        metadata,pages=encode_mapper_state(dict(profile='explicit-survey-metadata-fixture',original=[1,2,3]))
        self.evidence=replace(fixture.evidence,contexts=(*fixture.evidence.contexts,*pages))
        self.collector=_CollectorLeaf(fixture,metadata)
        self.mailbox=MapperStorageMailbox(fixture.f.context.sha256);self.queued=[]
        self.owner=SimpleNamespace(_context=fixture.f.context,_revision=7,_job_lifetime=5_000_000,
            _pipeline=object(),pending=None,mailbox=self.mailbox,collector=self.collector,
            catalogs=SimpleNamespace(pending=None,_queued=None,state_fields=lambda:{}),
            _head_ready=True,_checkpoint_ref=None,queue_checkpoint=self.queue)
        self.processor=ResidentMapperProcessor(None,fixture.f.raw,clock=lambda:self.now)
        self.processor._mapping=self.owner;self.processor._scene=self.coordinator
        self.processor._config=SimpleNamespace(sha256='e'*64)
        self.processor._hazard=SimpleNamespace(snapshot=SimpleNamespace(revision=1),state_fields=lambda *_:{})

    def queue(self,job,*,now_us,scene,subsystem_state):
        request=CheckpointRequest(job,CheckpointPayload.build(job,self.fixture.tiles,self.evidence,
            scene=dict(scene,mapper_state=self.collector.metadata),tile_references=self.fixture.refs,
            durable_cutoff_pi_us=1010002))
        if not self.mailbox.compute_offer(request,now_us=now_us):raise AssertionError('original queue rejected')
        self.queued.append(request)
        return request

    def test_source_lease_caps_original_job_and_actual_queue_acceptance_is_once(self):
        self.processor._queue_scene_freeze(self.now)
        self.assertEqual(len(self.queued),1)
        request=self.queued[0];grant=decode(self.original_grant,schema.SceneGrant)
        trust=decode(self.coordinator.scene._lease.trust_bytes,schema.WorldTrust)
        expected=min(self.now+5_000_000,grant.survey_deadline_pi_us-trust.upper_offset_us,
            grant.valid_until_pi_us-trust.upper_offset_us,self.coordinator.scene._lease.expires_pi_us-trust.upper_offset_us)
        self.assertEqual((request.job.issued_us,request.job.expires_us),(self.now,expected))
        self.assertLess(expected,self.now+5_000_000)
        self.assertEqual(request.job.scene_commit_nonce,str(UUID(bytes=grant.request_nonce)))
        self.assertIs(self.mailbox.main_peek().request,request)
        self.assertIs(self.coordinator._freeze_request,request)
        self.assertIs(self.processor._scene_freeze_request,request)
        self.assertIsNone(self.processor._scene_freeze_prequeue)
        self.assertIsNone(self.coordinator.scene._frozen.receipt)
        self.assertFalse(self.coordinator.scene.eligible)
        self.processor._queue_scene_freeze(self.now+1)
        self.assertEqual(len(self.queued),1)

    def test_unpublished_failure_returns_snapshot_and_retries_exact_job_without_new_issued_or_expiry(self):
        self.collector.fail_before_descriptor=True
        self.processor._queue_scene_freeze(self.now)
        original=self.collector.jobs[-1]
        self.assertEqual(self.queued,[]);self.assertIsNone(self.mailbox.main_peek())
        self.assertIsNone(self.coordinator._freeze_job)
        self.assertIsNone(self.processor._scene_freeze_attempt)
        self.assertEqual(len(self.collector.discarded),1)
        self.assertIs(self.processor._scene_freeze_prequeue[2],original)
        self.now+=10;self.collector.fail_before_descriptor=False
        self.processor._queue_scene_freeze(self.now)
        self.assertEqual(len(self.queued),1)
        self.assertIs(self.queued[0].job,original)
        self.assertIs(self.collector.jobs[0],self.collector.jobs[1])
        self.assertLess(original.issued_us,self.now)

    def test_expired_original_job_is_not_reminted_by_same_grant_clock_renewal(self):
        self.collector.fail_before_descriptor=True
        self.processor._queue_scene_freeze(self.now);original=self.collector.jobs[-1]
        self.now=original.expires_us
        old=self.coordinator.scene._lease;trust=decode(old.trust_bytes,schema.WorldTrust)
        trust.expires_pi_us+=500_000
        basis=old.basis_pi_us+500_000
        probe=decode(old.raw_probe_bytes,schema.MapperClockProbe)
        probe.pi_send_us=basis;probe.probe_seq+=1;probe.nonce=UUID(int=90).bytes;probe.mapper_trust.CopyFrom(trust)
        renewed=replace(old,trust_bytes=encode(trust),raw_probe_bytes=encode(probe),expires_pi_us=trust.expires_pi_us,
            basis_pi_us=basis,probe_pi_send_us=basis,pi_support_lower_us=max(0,basis-old.lookback_us),
            completed_pi_us=basis+10_000,time_lease_revision=old.time_lease_revision+1)
        self.assertTrue(self.coordinator.maintenance(self.original_grant,renewed,now_j_us=self.now))
        self.collector.fail_before_descriptor=False
        for _ in range(2):
            self.processor._queue_scene_freeze(self.now);self.now+=1
        self.assertEqual(self.processor._scene_status,'SCENE_FREEZE_EXPIRED')
        self.assertIs(self.processor._scene_freeze_prequeue[2],original)
        self.assertEqual(len(self.collector.jobs),1)
        self.assertEqual(self.queued,[]);self.assertIsNone(self.mailbox.main_peek())
        self.assertEqual(self.coordinator.scene.grant_bytes,self.original_grant)

    def test_new_accepted_revision_does_not_repackage_an_unpublished_original_job(self):
        self.collector.fail_before_descriptor=True
        self.processor._queue_scene_freeze(self.now);original=self.collector.jobs[-1]
        self.owner._revision+=1;self.collector.fail_before_descriptor=False;self.now+=1
        self.processor._queue_scene_freeze(self.now)
        self.assertEqual(self.processor._scene_status,'SCENE_FREEZE_INPUT_CHANGED')
        self.assertIs(self.processor._scene_freeze_prequeue[2],original)
        self.assertEqual(len(self.collector.jobs),1)
        self.assertEqual(self.queued,[]);self.assertIsNone(self.mailbox.main_peek())
