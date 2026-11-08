"""실제 Pi clock owner의 Scene 의미 변경과 원 시각 수명 회귀.

source counter를 전송자가 임의로 만들지 않고 실제 owner에서 전진시킨다.
명시한 probe fixture를 사용하며 broker·장치·권위 파일을 생성하지 않는다.
"""

from hashlib import sha256
import unittest

from xenova_drive_world import schema,encode,decode
from xenova_drive_world.mapper_clock import MapperClockError,U63_MAX,build_probe,candidate_body
from test_mapper_uplink import owner,exchange,reply_for,uid


def meaning(value): return sha256(value.encode('ascii')).digest()


class MapperSceneRevisionTests(unittest.TestCase):
    def ready(self):
        clock=owner()
        self.assertIsNone(exchange(clock,1000000)[0])
        lease=exchange(clock,1200000)[0]
        self.assertIsNotNone(lease)
        return clock,lease

    def test_meaning_change_fences_old_probe_without_reissuing_clock(self):
        clock,lease=self.ready()
        old=clock.plan(1400000,uid(20));wire=build_probe(old,1400100)
        raw_reply=encode(reply_for(old,wire))
        previous,streak,sequence,last_plan=clock._previous,clock._streak,clock.sequence,clock.last_plan_us
        self.assertEqual(clock.advance_scene_context(meaning('new-scene')),2)
        self.assertIsNone(clock.pending)
        self.assertIs(clock._lease,lease)
        self.assertEqual((clock._previous,clock._streak,clock.sequence,clock.last_plan_us),
                         (previous,streak,sequence,last_plan))
        self.assertEqual(clock.time_lease_revision,1)
        self.assertEqual(decode(lease.trust_bytes,schema.WorldTrust).expires_pi_us,2200100)
        self.assertEqual(lease.source_revision,1)
        with self.assertRaisesRegex(MapperClockError,'CLOCK_NOT_ORIGINAL_PLAN'):
            clock.accept(old,candidate_body(wire,raw_reply,1400500),1400500)

    def test_duplicate_does_not_cancel_new_probe_or_advance_revisions(self):
        clock,original=self.ready();key=meaning('new-scene')
        clock.advance_scene_context(key)
        plan=clock.plan(1400000,uid(20))
        self.assertEqual(plan.admission_revision,2)
        self.assertEqual(plan.prior_trust_bytes,original.trust_bytes)
        self.assertEqual(clock.advance_scene_context(key),2)
        self.assertIs(clock.pending,plan)
        wire=build_probe(plan,1400100)
        renewed=clock.accept(plan,candidate_body(wire,encode(reply_for(plan,wire)),1400500),1400500)
        self.assertEqual((renewed.source_revision,renewed.time_lease_revision),(2,2))
        self.assertEqual(decode(original.trust_bytes,schema.WorldTrust).expires_pi_us,2200100)
        self.assertEqual(clock.advance_scene_context(meaning('restore-request')),3)
        self.assertIs(clock._lease,renewed)
        self.assertEqual(clock.time_lease_revision,2)

    def test_malformed_key_cannot_change_any_current_clock_state(self):
        for key in (None,True,'x'*32,b'',bytes(31),bytes(33),bytearray(32)):
            clock,lease=self.ready()
            with self.subTest(key_type=type(key).__name__),self.assertRaisesRegex(MapperClockError,'CLOCK_SCENE_CONTEXT_KEY'):
                clock.advance_scene_context(key)
            self.assertEqual(clock.source_revision,1)
            self.assertIs(clock._lease,lease)

    def test_revision_overflow_denies_new_admission_without_mutating_old_lease(self):
        clock,lease=self.ready();clock.source_revision=U63_MAX
        clock.plan(1400000,uid(20))
        with self.assertRaisesRegex(MapperClockError,'CLOCK_SOURCE_REVISION_EXHAUSTED'):
            clock.advance_scene_context(meaning('new-scene'))
        self.assertEqual(clock.source_revision,U63_MAX)
        self.assertEqual(clock.time_lease_revision,1)
        self.assertIs(clock._lease,lease)
        self.assertIsNone(clock.pending)
        self.assertIsNone(clock.trust(1400500))
        self.assertIsNone(clock.plan(1600000,uid(20)))
