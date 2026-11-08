"""원 RGB/ClockEvidence에서 actual JPEG socketpair까지의 영상 전용 수명.

독립 software JPEG/센서 metadata fixture이며 실물 동기·노출/readout 보정이나
metric/NN/주행 준비를 승인하지 않는다. SDK/장치·대형 raw arena는 열지 않는다.
"""

from copy import deepcopy
from dataclasses import replace
import gc
import socket
import unittest
import weakref
from zlib import crc32

from raw_binding_fixtures import binding_fields
from test_oak_jpeg import jpeg_bytes
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.jpeg import JpegPacket
from xenova_vehicle.oak.observations import ClockEvidence
from xenova_vehicle.runtime.jpeg_stream import EncodedJpeg,JpegReceiver,TRANSFER_US
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.resident_video import AcquisitionVideo


BASE_J=3_000_000
BASE_SDK=1_000_000
RGB_BYTES=bytes(640*360*3//2)


class Clock:
    def __init__(self):self.value=BASE_J+20_000
    def __call__(self):return self.value


def observation(*,index=0,exposure_us=30_000):
    """독립 SDK→J 원 bracket에 실제 양수 노출을 적용할 입력이다."""
    delta=index*1_000
    rgb=RawFrame('rgb_video',10+index,500_000+delta,BASE_SDK+10_000+delta,
        BASE_J+10_010+delta,640,360,'NV12',RGB_BYTES,exposure_us)
    evidence=ClockEvidence()
    evidence.update(dict(j_before_us=BASE_J,sdk_us=BASE_SDK,j_after_us=BASE_J+4))
    return rgb,evidence.describe(rgb)


def jpeg_packet(rgb,*,received_j_us=None):
    return JpegPacket('jpeg',rgb.sequence,rgb.device_us,rgb.sdk_us,
        BASE_J+15_000 if received_j_us is None else received_j_us,640,360,jpeg_bytes())


class ResidentVideoTests(unittest.TestCase):
    def pair(self):
        clock=Clock()
        binding=RawBinding(binding_fields(jpeg_enabled=True))
        left,right=socket.socketpair()
        self.addCleanup(left.close);self.addCleanup(right.close)
        video=AcquisitionVideo(binding,left,clock=clock)
        receiver=JpegReceiver(right,video.scope,clock=clock)
        self.addCleanup(video.close);self.addCleanup(receiver.close)
        return video,receiver,clock,binding

    def receive(self,video,receiver):
        for _ in range(32):
            video.step()
            frame=receiver.step()
            if frame is not None:return frame
        self.fail('original bounded JPEG did not cross actual socketpair')

    def test_original_rgb_bridge_exposure_scope_crc_and_owned_jpeg_round_trip(self):
        video,receiver,clock,binding=self.pair()
        rgb,original_clock=observation()
        preserved=deepcopy(original_clock)
        self.assertFalse(original_clock['metric_time_qualified'])
        self.assertTrue(video.observe_rgb(rgb,original_clock))
        packet=jpeg_packet(rgb)
        self.assertTrue(video.offer_jpeg(packet))
        pending=video.sender.pending
        self.assertIs(type(pending),EncodedJpeg)
        self.assertIs(pending.payload,packet.payload)
        fields=pending.fields
        lower=BASE_J+9_999-rgb.exposure_us
        upper=BASE_J+10_005
        self.assertEqual(fields['capture_j_lower_us'],str(lower))
        self.assertEqual(fields['capture_j_upper_us'],str(upper))
        self.assertEqual(fields['expires_j_us'],str(lower+500_000))
        self.assertEqual(fields['completed_j_us'],str(clock.value))
        self.assertNotEqual(fields['completed_j_us'],str(packet.received_j_us))
        self.assertEqual(fields['frame_sequence'],str(rgb.sequence))
        for key in ('service_instance','acquisition_worker','pipeline_instance','capture_epoch'):
            self.assertEqual(fields[key],binding.fields[key])
        self.assertEqual((fields['width'],fields['height'],fields['encoding']),(640,360,'MJPEG'))
        self.assertEqual(fields['payload_length'],len(packet.payload))
        self.assertEqual(fields['crc32'],crc32(packet.payload))
        self.assertNotIn('metric_ready',fields)
        self.assertEqual(original_clock,preserved)
        # metadata 입력을 나중에 바꿔도 이미 소유한 원 header/전송 E는 바뀌지 않는다.
        original_clock['bridge_sample']['j_before_us']='0'
        original_clock['sdk_host_timestamp_j_bracket_us'][0]='0'
        clock.value+=1_000
        received=self.receive(video,receiver)
        self.assertEqual(received.fields,fields)
        self.assertEqual(received.payload,packet.payload)
        self.assertEqual(received.header_bytes,pending.header_bytes)
        self.assertIsNone(video.sender.pending)
        self.assertEqual(video.snapshot()['counts']['offered'],1)

    def test_one_original_completed_time_and_busy_drop_cannot_be_reissued_after_send(self):
        video,receiver,clock,_=self.pair()
        first,first_clock=observation()
        self.assertTrue(video.observe_rgb(first,first_clock))
        first_packet=jpeg_packet(first)
        self.assertTrue(video.offer_jpeg(first_packet))
        original=video.sender.pending
        completed,expiry=original.completed_j_us,original.expires_j_us
        second,second_clock=observation(index=1)
        clock.value+=1_000
        self.assertTrue(video.observe_rgb(second,second_clock))
        second_packet=jpeg_packet(second,received_j_us=BASE_J+16_000)
        self.assertFalse(video.offer_jpeg(second_packet))
        self.assertEqual(video.snapshot()['counts']['busy'],1)
        self.assertIs(video.sender.pending,original)
        self.assertEqual((original.completed_j_us,original.expires_j_us),(completed,expiry))
        self.assertEqual(self.receive(video,receiver).fields,original.fields)
        clock.value+=1_000
        self.assertFalse(video.offer_jpeg(second_packet))
        self.assertFalse(video.offer_jpeg(first_packet))
        self.assertEqual(video.snapshot()['counts']['duplicate'],2)
        self.assertEqual(video.snapshot()['counts']['offered'],1)
        self.assertIsNone(video.sender.pending)
        self.assertIsNone(receiver.step())
        third,third_clock=observation(index=2)
        self.assertTrue(video.observe_rgb(third,third_clock))
        self.assertTrue(video.offer_jpeg(jpeg_packet(third,received_j_us=BASE_J+17_000)))
        received=self.receive(video,receiver)
        self.assertEqual(received.sequence,third.sequence)
        self.assertEqual(received.completed_j_us,clock.value)
        self.assertEqual(original.completed_j_us,completed)

    def test_metadata_ring_eight_evicts_old_key_and_retains_no_frame_or_raw_payload(self):
        video,receiver,clock,_=self.pair()
        first,first_clock=observation()
        first_packet=jpeg_packet(first)
        frame_ref=weakref.ref(first)
        self.assertTrue(video.observe_rgb(first,first_clock))
        del first,first_clock
        gc.collect()
        self.assertIsNone(frame_ref())
        for index in range(1,9):
            rgb,clock_fields=observation(index=index)
            clock.value=BASE_J+20_000+index*1_000
            self.assertTrue(video.observe_rgb(rgb,clock_fields))
        self.assertEqual(video.snapshot()['metadata_count'],8)
        # 이 private 관측은 cap/alias 계약만 검사한다. 제품 원 sensor 입력은 위 public API다.
        self.assertEqual(len(video._rgbs),8)
        self.assertTrue(all(type(key) is tuple and len(key)==3 and all(type(x) is int for x in key)
            and type(row) is tuple and len(row)==3 and all(type(x) is int for x in row)
            for key,row in video._rgbs.items()))
        self.assertFalse(video.offer_jpeg(first_packet))
        self.assertEqual(video.snapshot()['counts']['missing_rgb'],1)
        self.assertIsNone(video.sender.pending)
        self.assertTrue(video.offer_jpeg(jpeg_packet(rgb,received_j_us=clock.value-1)))
        self.assertEqual(self.receive(video,receiver).sequence,rgb.sequence)

    def test_jpeg_requires_exact_sequence_device_sdk_and_never_invents_missing_rgb(self):
        for changed in ('sequence','device_us','sdk_us','missing'):
            with self.subTest(changed=changed):
                video,receiver,clock,_=self.pair()
                rgb,clock_fields=observation()
                if changed!='missing':self.assertTrue(video.observe_rgb(rgb,clock_fields))
                packet=jpeg_packet(rgb)
                if changed!='missing':packet=replace(packet,**{changed:getattr(packet,changed)+1})
                self.assertFalse(video.offer_jpeg(packet))
                self.assertEqual(video.snapshot()['counts']['missing_rgb'],1)
                self.assertIsNone(video.sender.pending)
                video.step()
                self.assertIsNone(receiver.step())
                self.assertFalse(video.snapshot()['channel_closed'])
                self.assertIsNone(video.snapshot()['failure'])

    def test_original_capture_half_open_expiry_and_future_copy_time_do_not_refresh(self):
        video,_,clock,_=self.pair()
        rgb,clock_fields=observation()
        self.assertTrue(video.observe_rgb(rgb,clock_fields))
        expiry=int(clock_fields['sdk_host_timestamp_j_bracket_us'][0])-rgb.exposure_us+500_000
        packet=jpeg_packet(rgb)
        clock.value=expiry
        self.assertFalse(video.offer_jpeg(packet))
        self.assertIsNone(video.sender.pending)
        clock.value+=1
        self.assertFalse(video.offer_jpeg(packet))
        self.assertEqual(video.snapshot()['counts']['offered'],0)
        self.assertEqual(video.snapshot()['counts']['duplicate'],1)
        other,_,fresh_clock,_=self.pair()
        rgb,clock_fields=observation()
        fresh_clock.value=expiry
        self.assertFalse(other.observe_rgb(rgb,clock_fields))
        self.assertEqual(other.snapshot()['metadata_count'],0)
        self.assertEqual(other.snapshot()['counts']['expired'],1)
        future,_,future_clock,_=self.pair()
        self.assertTrue(future.observe_rgb(rgb,clock_fields))
        self.assertFalse(future.offer_jpeg(jpeg_packet(rgb,received_j_us=future_clock.value+1)))
        self.assertEqual(future.snapshot()['counts']['expired'],1)
        self.assertIsNone(future.sender.pending)

    def test_original_bridge_interval_and_exposure_mismatch_remain_video_not_ready(self):
        for changed in ('interval','bridge','missing_bridge','future_bridge','no_exposure','zero_exposure','shape'):
            with self.subTest(changed=changed):
                video,_,clock,_=self.pair()
                rgb,clock_fields=observation()
                if changed=='interval':
                    clock_fields['sdk_host_timestamp_j_bracket_us'][0]=str(int(clock_fields['sdk_host_timestamp_j_bracket_us'][0])+1)
                elif changed=='bridge':
                    clock_fields['bridge_sample']['sdk_us']=str(BASE_SDK+1)
                elif changed=='missing_bridge':clock_fields['bridge_sample']=None
                elif changed=='future_bridge':clock_fields['bridge_sample']['j_after_us']=str(clock.value+1)
                elif changed=='no_exposure':rgb=replace(rgb,exposure_us=None)
                elif changed=='zero_exposure':rgb=replace(rgb,exposure_us=0)
                elif changed=='shape':rgb=replace(rgb,width=320,payload=bytes(320*360*3//2))
                self.assertFalse(video.observe_rgb(rgb,clock_fields))
                self.assertEqual(video.snapshot()['metadata_count'],0)
                self.assertEqual(video.snapshot()['counts']['invalid'],1)
                self.assertFalse(video.offer_jpeg(jpeg_packet(rgb)))
                self.assertIsNone(video.sender.pending)
                self.assertIsNone(video.snapshot()['failure'])
        binding=RawBinding(binding_fields(jpeg_enabled=False))
        left,right=socket.socketpair()
        self.addCleanup(left.close);self.addCleanup(right.close)
        with self.assertRaises(ValueError):AcquisitionVideo(binding,left,clock=Clock())

    def test_actual_peer_close_is_video_only_failure_and_rgb_metadata_can_continue(self):
        video,receiver,clock,_=self.pair()
        rgb,clock_fields=observation()
        self.assertTrue(video.observe_rgb(rgb,clock_fields))
        self.assertTrue(video.offer_jpeg(jpeg_packet(rgb)))
        receiver.close()
        # 실제 socket peer close를 sender가 JpegStreamError로 분류한다. A로 전파하면 결함이다.
        video.step()
        snapshot=video.snapshot()
        self.assertTrue(snapshot['channel_closed'])
        self.assertEqual(snapshot['counts']['transport_failed'],1)
        self.assertEqual(snapshot['failure'],'JpegStreamError')
        self.assertIsNone(video.sender.pending)
        rgb,clock_fields=observation(index=1)
        clock.value+=1_000
        self.assertTrue(video.observe_rgb(rgb,clock_fields))
        self.assertEqual(video.snapshot()['counts']['rgb'],2)
        video.step()
        self.assertEqual(video.snapshot()['counts']['transport_failed'],1)

    def test_pending_transfer_expiry_isolated_without_repackaging_same_source(self):
        video,_,clock,_=self.pair()
        rgb,clock_fields=observation()
        self.assertTrue(video.observe_rgb(rgb,clock_fields))
        packet=jpeg_packet(rgb)
        self.assertTrue(video.offer_jpeg(packet))
        pending=video.sender.pending
        completed,expiry=pending.completed_j_us,pending.expires_j_us
        clock.value=completed+TRANSFER_US
        video.step()
        self.assertTrue(video.snapshot()['channel_closed'])
        self.assertEqual(video.snapshot()['counts']['transport_failed'],1)
        self.assertIsNone(video.sender.pending)
        self.assertFalse(video.offer_jpeg(packet))
        self.assertEqual(video.snapshot()['counts']['duplicate'],1)
        self.assertEqual((pending.completed_j_us,pending.expires_j_us),(completed,expiry))
        self.assertEqual(video.snapshot()['counts']['offered'],1)
