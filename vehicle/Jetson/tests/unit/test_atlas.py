"""원 block의 유한 소유권/후보/lease와 data peak를 검사한다.

본문은 codec를 통과했다고 가정한 opaque software bytes다. SQL receipt·지도/시각
수락은 이 pool의 책임이 아니며 mark_clean 호출은 검산된 caller 사건을 주입한다.
"""

from dataclasses import replace
import gc
from threading import Lock
import unittest
from unittest.mock import patch
import weakref

from xenova_vehicle.mapping.atlas import AtlasPool,AtlasUpdate,AtlasError,AtlasResource,TILE_BYTES


def body(value):return bytes((value,))*TILE_BYTES


def update(owner,kind,key,value):
    return AtlasUpdate(kind,key,body(value),owner.current(kind,key))


def install(owner,*updates):
    delta=owner.prepare(tuple(updates));owner.accept(delta);owner.drain_retired();return delta.refs


class AtlasPoolTests(unittest.TestCase):
    def test_final_swap_retains_last_original_payload_and_charges_until_outside_lock_drain(self):
        owner=AtlasPool(reserved_workspace_bytes=0,max_versions=2,max_data_bytes=2*TILE_BYTES)
        original,=install(owner,update(owner,'LIVE64',(0,0),1))
        final_lock=Lock();finalized=[]
        # bytes는 weakref가 없으므로 그 본문을 소유한 실제 마지막 _Block 해제를 관측한다.
        old_block=weakref.ref(owner._state.blocks[original.serial])
        weakref.finalize(old_block(),lambda:finalized.append(final_lock.locked()))
        delta=owner.prepare((update(owner,'LIVE64',(0,0),2),))
        with final_lock:
            owner.accept(delta)
            self.assertEqual(finalized,[])
        self.assertIsNotNone(old_block())
        self.assertEqual((owner.stats()['versions'],owner.stats()['retired_versions'],owner.stats()['charged_bytes']),
            (2,1,2*TILE_BYTES))
        self.assertNotIn(original,owner.retained_refs())
        with self.assertRaises(AtlasError):owner.lease((original,),purpose='READ')
        self.assertEqual(owner.coalesce_unleased(),())
        with self.assertRaisesRegex(AtlasResource,'VERSIONS'):owner.ensure_capacity(1)
        self.assertTrue(owner.drain_retired())
        self.assertEqual(finalized,[False]);self.assertIsNone(old_block())
        self.assertEqual((owner.stats()['versions'],owner.stats()['retired_bytes'],owner.stats()['charged_bytes']),
            (1,0,TILE_BYTES))
        self.assertIs(owner.current('LIVE64',(0,0)),delta.refs[0])
        owner.ensure_capacity(1);self.assertFalse(owner.drain_retired())

    def test_weak_owner_pins_are_bounded_idempotent_and_gc_only_queues_until_compute_drain(self):
        class Owner:pass
        owner=AtlasPool(reserved_workspace_bytes=0)
        original,=install(owner,update(owner,'EVIDENCE64',(0,0),1))
        newer,=install(owner,update(owner,'EVIDENCE64',(1,0),2))
        holders=[Owner() for _ in range(16)]
        first=owner.retain_owner(holders[0],(original,newer))
        self.assertIs(owner.retain_owner(holders[0],(newer,original)),first)
        with self.assertRaises(AtlasError):owner.retain_owner(holders[0],(original,))
        for holder in holders[1:]:owner.retain_owner(holder,(original,))
        del holder
        with self.assertRaisesRegex(AtlasResource,'OWNER_PINS'):owner.retain_owner(Owner(),(original,))
        self.assertEqual(owner.stats()['owner_pins'],16);self.assertEqual(owner.stats()['versions'],2)
        generation=owner.generation
        del holders;gc.collect()
        self.assertEqual(owner.stats()['owner_pins'],16);self.assertEqual(owner.generation,generation)
        released=owner.drain_owner_releases()
        self.assertEqual(len(released),16);self.assertEqual(owner.stats()['owner_pins'],0)
        self.assertEqual(owner.stats()['dirty_evidence_versions'],2)

    def test_full_immutable_updates_and_original_delta_cancel_do_not_mutate_current(self):
        owner=AtlasPool(reserved_workspace_bytes=0)
        original,=install(owner,update(owner,'LIVE64',(-1,0),1))
        with self.assertRaises(AtlasError):
            owner.prepare((AtlasUpdate('LIVE64',(-1,0),body(2),replace(original)),))
        with self.assertRaises(AtlasError):
            owner.prepare((AtlasUpdate('LIVE64',(-1,0),bytearray(body(2)),original),))
        delta=owner.prepare((update(owner,'LIVE64',(-1,0),2),))
        self.assertIs(owner.current('LIVE64',(-1,0)),original)
        self.assertFalse(hasattr(delta,'payload'));self.assertFalse(hasattr(original,'payload'))
        self.assertEqual(owner.stats()['charged_bytes'],2*TILE_BYTES)
        with self.assertRaises(AtlasError):owner.cancel(replace(delta))
        owner.cancel(delta)
        self.assertEqual(owner.stats()['versions'],1);self.assertEqual(owner.stats()['staged_versions'],0)
        self.assertIs(owner.current('LIVE64',(-1,0)),original)
        delta=owner.prepare((update(owner,'LIVE64',(-1,0),3),))
        with patch('xenova_vehicle.mapping.atlas.sha256',side_effect=AssertionError('no final hash')):
            owner.accept(delta)
        self.assertIs(owner.current('LIVE64',(-1,0)),delta.refs[0]);self.assertEqual(owner.stats()['versions'],2)
        owner.drain_retired();self.assertEqual(owner.stats()['versions'],1)
        lease=owner.lease(delta.refs,purpose='READ')
        self.assertEqual(owner.payload(delta.refs[0],lease=lease),body(3));owner.release(lease)
        with self.assertRaises(AtlasError):owner.payload(delta.refs[0],lease=lease)

    def test_shared_checkpoint_frozen_original_survives_cow_and_old_receipt_cleans_only_old(self):
        owner=AtlasPool(reserved_workspace_bytes=0)
        original,=install(owner,update(owner,'EVIDENCE64',(0,0),1))
        checkpoint=owner.lease((original,),purpose='CHECKPOINT');frozen=owner.lease((original,),purpose='FROZEN')
        self.assertEqual((owner.stats()['versions'],owner.stats()['frozen_refs'],owner.stats()['dirty_versions']),(1,1,1))
        newer,=install(owner,update(owner,'EVIDENCE64',(0,0),2))
        self.assertIsNot(original,newer);self.assertEqual(owner.stats()['versions'],2)
        self.assertEqual(owner.payload(original,lease=checkpoint),body(1))
        self.assertEqual(owner.stats()['dirty_versions'],2)
        owner.mark_clean((original,))
        self.assertEqual(owner.stats()['dirty_versions'],1);self.assertIs(owner.current('EVIDENCE64',(0,0)),newer)
        owner.release(checkpoint);owner.release(frozen)
        with self.assertRaises(AtlasResource):owner.evict_clean(((0,0),))
        owner.mark_clean((newer,));removed=owner.evict_clean(((0,0),))
        self.assertEqual({r.serial for r in removed},{original.serial,newer.serial})
        self.assertEqual((owner.stats()['versions'],owner.stats()['charged_bytes']),(0,0))

    def test_unleased_full_dirty_replacement_coalesces_but_pending_original_does_not(self):
        owner=AtlasPool(reserved_workspace_bytes=0,max_dirty=1)
        first,=install(owner,update(owner,'EVIDENCE64',(0,0),1))
        second,=install(owner,update(owner,'EVIDENCE64',(0,0),2))
        self.assertGreater(second.serial,first.serial)
        self.assertEqual((owner.stats()['versions'],owner.stats()['dirty_versions']),(1,1))
        checkpoint=owner.lease((second,),purpose='CHECKPOINT')
        with self.assertRaisesRegex(AtlasResource,'DIRTY_VERSIONS'):
            owner.prepare((update(owner,'EVIDENCE64',(0,0),3),))
        self.assertIs(owner.current('EVIDENCE64',(0,0)),second)
        self.assertEqual(owner.payload(second,lease=checkpoint),body(2));self.assertEqual(owner.stats()['staged_versions'],0)
        owner.mark_clean((second,))
        third,=install(owner,update(owner,'EVIDENCE64',(0,0),3))
        self.assertIs(owner.current('EVIDENCE64',(0,0)),third);self.assertEqual(owner.stats()['versions'],2)
        owner.release(checkpoint)

    def test_logical_negative_key_evicts_pair_only_when_clean_and_unleased(self):
        owner=AtlasPool(reserved_workspace_bytes=0,max_live_keys=1,max_versions=4)
        live,evidence=install(owner,update(owner,'LIVE64',(-1,-2),1),update(owner,'EVIDENCE64',(-1,-2),2))
        self.assertEqual((owner.stats()['live_versions'],owner.stats()['evidence_versions'],owner.stats()['dirty_versions']),(1,1,1))
        with self.assertRaises(AtlasResource):owner.evict_clean(((-1,-2),))
        owner.mark_clean((evidence,));frozen=owner.lease((evidence,),purpose='FROZEN')
        updates=(update(owner,'LIVE64',(0,-2),3),update(owner,'EVIDENCE64',(0,-2),4))
        with self.assertRaisesRegex(AtlasResource,'NO_CLEAN_UNLEASED_KEY'):owner.prepare(updates)
        self.assertIs(owner.current('LIVE64',(-1,-2)),live)
        owner.release(frozen);delta=owner.prepare(updates)
        self.assertEqual(delta.evicted_keys,((-1,-2),));owner.accept(delta)
        self.assertIsNone(owner.current('LIVE64',(-1,-2)));self.assertIsNone(owner.current('EVIDENCE64',(-1,-2)))
        self.assertEqual(owner.stats()['versions'],4);owner.drain_retired()
        self.assertEqual(owner.stats()['live_keys'],1);self.assertEqual(owner.stats()['versions'],2)
        with self.assertRaises(AtlasError):owner.mark_clean((evidence,))

    def test_lease_change_fences_prepared_candidate_without_mutating_old_data(self):
        owner=AtlasPool(reserved_workspace_bytes=0)
        original,=install(owner,update(owner,'LIVE64',(0,0),1))
        delta=owner.prepare((update(owner,'LIVE64',(0,0),2),))
        read=owner.lease((original,),purpose='READ')
        with self.assertRaisesRegex(AtlasError,'fenced'):owner.accept(delta)
        self.assertIs(owner.current('LIVE64',(0,0)),original)
        self.assertEqual(owner.payload(original,lease=read),body(1))
        owner.cancel(delta);owner.release(read)
        other=AtlasPool(reserved_workspace_bytes=0)
        with self.assertRaises(AtlasError):other.lease((original,),purpose='READ')
        with self.assertRaises(AtlasError):owner.lease((original,),purpose='FROZEN')

    def test_reserved_workspace_and_staged_peak_count_both_block_kinds_before_eviction(self):
        owner=AtlasPool(reserved_workspace_bytes=TILE_BYTES,max_versions=3,max_data_bytes=3*TILE_BYTES)
        install(owner,update(owner,'LIVE64',(0,0),1))
        delta=owner.prepare((update(owner,'EVIDENCE64',(0,0),2),))
        self.assertEqual(owner.stats()['charged_bytes'],3*TILE_BYTES)
        self.assertEqual((owner.stats()['versions'],owner.stats()['staged_versions']),(1,1))
        owner.accept(delta)
        with self.assertRaisesRegex(AtlasResource,'DATA_BYTES'):owner.ensure_capacity(1)
        with self.assertRaisesRegex(AtlasResource,'VERSIONS'):owner.ensure_capacity(2)
        with self.assertRaises(AtlasError):AtlasPool(reserved_workspace_bytes=0,max_versions=321)
        with self.assertRaises(AtlasError):AtlasPool(reserved_workspace_bytes=0,max_live_keys=129)
