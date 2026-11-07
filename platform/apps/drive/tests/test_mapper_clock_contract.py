"""Pi가 수락한 원 J/Pi trust를 다음 probe에 반환하는 append 계약의 크기/거절 경계."""

import unittest
from xenova_drive_world import schema,encode,decode


BOOT='11111111-1111-4111-8111-111111111111'
PEER='22222222-2222-4222-8222-222222222222'
CLOCK='33333333-3333-4333-8333-333333333333'


def probe():
    value=schema.MapperClockProbe(major=1,vehicle='v'*64,device='oak-front',pi_host_boot=BOOT,
        pi_client_instance=PEER,probe_seq=1,nonce=b'n'*16,pi_send_us=1000,
        expected_mapper_boot=BOOT,expected_mapper_instance=PEER,expected_clock_epoch=CLOCK,admission_revision=1)
    value.mapper_trust.CopyFrom(schema.WorldTrust(vehicle=value.vehicle,mapper_boot=BOOT,mapper_instance=PEER,
        map_epoch=BOOT,frame_epoch=PEER,odom_epoch=CLOCK,configuration='c'*64,calibration='a'*64,
        primitive='b'*64,clock_epoch=CLOCK,lower_offset_us=-100,upper_offset_us=100,
        expires_pi_us=1000000,max_error_us=200))
    return value


class MapperClockContractTests(unittest.TestCase):
    def test_returned_original_trust_exceeds_old_cap_and_roundtrips_without_changing_expiry(self):
        original=probe();wire=encode(original)
        self.assertGreater(len(wire),512)
        self.assertLessEqual(len(wire),1024)
        self.assertEqual(decode(wire,schema.MapperClockProbe),original)
        self.assertEqual(original.mapper_trust.expires_pi_us,1000000)

    def test_initial_no_trust_probe_does_not_need_admission(self):
        original=probe();original.ClearField('mapper_trust');original.admission_revision=0
        self.assertFalse(decode(encode(original),schema.MapperClockProbe).HasField('mapper_trust'))

    def test_returned_trust_rejects_wrong_vehicle_epoch_admission_and_unbounded_offset(self):
        for change in ('vehicle','epoch','admission','width'):
            value=probe()
            if change=='vehicle':value.mapper_trust.vehicle='other'
            elif change=='epoch':value.mapper_trust.clock_epoch=BOOT
            elif change=='admission':value.admission_revision=0
            else:value.mapper_trust.upper_offset_us=10000
            with self.subTest(change=change),self.assertRaises(ValueError):encode(value)


if __name__=='__main__':unittest.main()
