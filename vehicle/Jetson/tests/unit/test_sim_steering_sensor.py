"""가상 현재 servo 측정의 원본·오차·packet 결합을 검사한다. 실물 HOLDREADY 수락은 아니다.

기본 선택은 원 wheel/actual-angle literal과 struct projection을 쓴 의미 단위 시험이다.
마지막 클래스만 coherent B08 codec/odom을 쓰며 Device clock 입구는 명시 software leaf다.
"""

from dataclasses import FrozenInstanceError,replace
from hashlib import sha256
import importlib
import json
import math
import os
import unittest
from uuid import UUID

from test_simulation_feedback import ProjectedCodec
from xenova_vehicle.simulation.feedback import WheelSample
from xenova_vehicle.simulation.steering_sensor import VirtualSteeringSensorProfile,VirtualSteeringSensor


PI=str(UUID(int=1));DEVICE=str(UUID(int=2));EPOCH=str(UUID(int=3))


def profile(**changes):
    values=dict(sensor_id='virtual-current-servo-1',quantization_rad=.0001,angle_error_rad=.0002,
        timestamp_error_us=1,max_rate_rps=5.)
    values.update(changes)
    return VirtualSteeringSensorProfile(**values)


def sensor(leaf=None):return VirtualSteeringSensor(profile() if leaf is None else leaf,pi_session=PI,device_session=DEVICE)


def wheel(seq=1,stamp=1_000_020):
    # 원 estimated 값은 넓은 오차를 가진 별도 입력이다. measured 값으로 덮어쓰지 않는다.
    return WheelSample(seq,stamp,13,17,0,500)


class VirtualSteeringSensorTests(unittest.TestCase):
    def test_leaf_requires_explicit_identity_and_errors_with_immutable_virtual_provenance(self):
        leaf=profile();fields=leaf.fields
        self.assertEqual(leaf.source_kind,'SIMULATED_MEASURED_STEERING')
        self.assertFalse(leaf.physical_qualified)
        self.assertEqual(fields['steering_quality_bit'],2)
        self.assertEqual(leaf.sha256,sha256(leaf.canonical_bytes).hexdigest())
        fields['quantization_rad']=1.
        self.assertEqual(leaf.quantization_rad,.0001)
        self.assertNotEqual(leaf.sha256,profile(sensor_id='another-virtual-sensor').sha256)
        with self.assertRaises(FrozenInstanceError):leaf.angle_error_rad=0.
        with self.assertRaises(TypeError):VirtualSteeringSensorProfile('missing-model')
        for changes in (dict(sensor_id=''),dict(quantization_rad=0.),dict(angle_error_rad=-1.),
                dict(timestamp_error_us=True),dict(timestamp_error_us=-1),dict(max_rate_rps=0.),
                dict(quantization_rad=float('nan')),dict(angle_error_rad=float('inf'))):
            with self.subTest(changes=changes),self.assertRaises(ValueError):profile(**changes)

    def test_actual_angle_quantization_time_and_wire_error_are_outward_and_positive(self):
        owner=sensor();actual=.12345
        measurement=owner.sample(actual,source_sample=1,source_us=100)
        facts=measurement.fields;model=facts['error_model']
        self.assertEqual(measurement.actual_steering_rad,actual)
        self.assertEqual(measurement.steering_mrad,round(measurement.steering_rad*1000))
        self.assertGreater(measurement.steering_error_mrad,0)
        self.assertGreaterEqual(model['quantization_bound_rad'],.0001/2)
        self.assertGreaterEqual(model['time_error_rad'],5/1_000_000)
        self.assertGreaterEqual(measurement.steering_error_mrad/1000,
            math.fsum((model['angle_error_rad'],model['quantization_bound_rad'],
                       model['time_error_rad'],model['wire_rounding_error_rad'])))
        self.assertGreaterEqual(measurement.steering_error_mrad/1000,
            abs(actual-measurement.steering_mrad/1000)+.0002+5/1_000_000)
        self.assertEqual((facts['source_sample'],facts['source_us']),('1','100'))
        self.assertEqual(facts['source_kind'],'SIMULATED_MEASURED_STEERING')
        self.assertFalse(measurement.physical_qualified)
        self.assertEqual(facts['sensor_profile_sha256'],owner.profile.sha256)
        self.assertNotIn('target',facts)
        facts['error_model']['angle_error_rad']=100.
        self.assertEqual(measurement.fields['error_model']['angle_error_rad'],.0002)
        with self.assertRaises(FrozenInstanceError):measurement.steering_mrad=0
        with self.assertRaises(TypeError):owner.sample(target_steering_rad=.5,source_sample=2,source_us=101)

    def test_composition_requires_original_same_sample_time_and_keeps_estimated_wheel_unchanged(self):
        owner=sensor();original=wheel()
        measured=owner.sample(.2134,source_sample=original.sample_seq,source_us=original.source_us)
        combined=owner.combine(original,measured)
        self.assertIs(combined.wheel_sample,original);self.assertIs(combined.measurement,measured)
        self.assertIs(owner.combine(original,measured),combined)
        self.assertEqual((combined.source_sample,combined.sample_seq,combined.source_us),(1,1,1_000_020))
        self.assertEqual((combined.left_ticks,combined.right_ticks),(13,17))
        self.assertEqual(combined.quality,3);self.assertEqual(original.quality,5)
        self.assertEqual(original.steering_mrad,0)
        self.assertEqual(original.steering_error_mrad,500)
        self.assertEqual(combined.steering_mrad,measured.steering_mrad)
        self.assertNotEqual(combined.steering_mrad,original.steering_mrad)
        facts=json.loads(combined.provenance_bytes)
        self.assertEqual(facts['wheel']['original_quality'],5)
        self.assertEqual(facts['measurement']['actual_steering_rad'],.2134)
        self.assertFalse(facts['physical_qualified'])
        for other in (replace(original,source_us=1_000_021),replace(original,sample_seq=2)):
            with self.assertRaises(ValueError):owner.combine(other,measured)
        for other in (replace(original),replace(original,left_ticks=99)):
            with self.assertRaises(ValueError):owner.combine(other,measured)
        foreign=sensor().sample(.2134,source_sample=1,source_us=1_000_020)
        with self.assertRaises(ValueError):owner.combine(original,foreign)

    def test_clock_and_sensor_projection_use_measured_fields_without_retiming_or_source_alias(self):
        owner=sensor();original=wheel();measurement=owner.sample(.1234,source_sample=1,source_us=original.source_us)
        combined=owner.combine(original,measurement)
        probe=ProjectedCodec.PROBE3.pack(UUID(PI).bytes,UUID(DEVICE).bytes,1,500_000)
        wire=combined.clock_reply(ProjectedCodec,probe,received_device_us=1_000_010,sent_device_us=1_000_030)
        row=ProjectedCodec.CLOCK3.unpack(wire[2:])
        self.assertEqual(row[:10],(UUID(PI).bytes,UUID(DEVICE).bytes,1,500_000,1_000_010,1_000_020,1_000_030,1,13,17))
        self.assertEqual(row[10:],(3,measurement.steering_mrad,measurement.steering_error_mrad))
        self.assertIs(combined.clock_reply(ProjectedCodec,probe,received_device_us=1_000_010,sent_device_us=1_000_030),wire)
        with self.assertRaises(ValueError):combined.clock_reply(ProjectedCodec,probe,received_device_us=1_000_010,sent_device_us=1_000_031)
        packet=combined.sensor_wire(ProjectedCodec,pi_session=PI,device_session=DEVICE,clock_epoch=EPOCH,clock_sequence=7)
        row=ProjectedCodec.SENSOR3.unpack(packet[2:])
        self.assertEqual(row,(UUID(PI).bytes,UUID(DEVICE).bytes,1,1_000_020,13,17,3,
            measurement.steering_mrad,measurement.steering_error_mrad,UUID(EPOCH).bytes,7))
        with self.assertRaises(ValueError):combined.sensor_wire(ProjectedCodec,pi_session=str(UUID(int=9)),device_session=DEVICE,clock_epoch=EPOCH,clock_sequence=7)
        wrong=ProjectedCodec.PROBE3.pack(UUID(PI).bytes,UUID(int=8).bytes,2,500_100)
        with self.assertRaises(ValueError):combined.clock_reply(ProjectedCodec,wrong,received_device_us=1_000_010,sent_device_us=1_000_030)

    def test_new_measurement_keeps_original_reply_retry_but_old_sensor_is_not_current(self):
        owner=sensor();first_wheel=wheel();first=owner.combine(first_wheel,
            owner.sample(.1,source_sample=1,source_us=first_wheel.source_us))
        probe=ProjectedCodec.PROBE3.pack(UUID(PI).bytes,UUID(DEVICE).bytes,1,500_000)
        raw=first.clock_reply(ProjectedCodec,probe,received_device_us=1_000_010,sent_device_us=1_000_030)
        next_wheel=wheel(2,1_000_120);second=owner.combine(next_wheel,
            owner.sample(.2,source_sample=2,source_us=next_wheel.source_us))
        self.assertIs(first.clock_reply(ProjectedCodec,probe,received_device_us=1_000_010,sent_device_us=1_000_030),raw)
        with self.assertRaises(ValueError):first.sensor_wire(ProjectedCodec,pi_session=PI,device_session=DEVICE,clock_epoch=EPOCH,clock_sequence=7)
        next_probe=ProjectedCodec.PROBE3.pack(UUID(PI).bytes,UUID(DEVICE).bytes,2,500_100)
        wire=second.clock_reply(ProjectedCodec,next_probe,received_device_us=1_000_110,sent_device_us=1_000_130)
        self.assertEqual(ProjectedCodec.CLOCK3.unpack(wire[2:])[5],1_000_120)
        self.assertEqual(first.wheel_sample.quality,5)

    def test_invalid_regressed_conflicting_and_unrepresentable_samples_do_not_create_measurements(self):
        owner=sensor();first=owner.sample(0.,source_sample=1,source_us=100)
        self.assertIs(owner.sample(0.,source_sample=1,source_us=100),first)
        for angle,seq,stamp in ((.1,1,100),(0.,1,101),(0.,2,99),(True,2,101),
                (1.5,2,101),(float('nan'),2,101),(0.,True,101),(0.,2,0)):
            with self.subTest(angle=angle,seq=seq,stamp=stamp),self.assertRaises(ValueError):
                owner.sample(angle,source_sample=seq,source_us=stamp)
        for leaf in (profile(quantization_rad=1e-300),profile(angle_error_rad=70.),profile(angle_error_rad=1e308)):
            with self.subTest(leaf=leaf),self.assertRaises(ValueError):sensor(leaf).sample(.2,source_sample=1,source_us=100)
        ideal=sensor(profile(angle_error_rad=0.,timestamp_error_us=0))
        self.assertGreater(ideal.sample(0.,source_sample=1,source_us=100).steering_error_mrad,0)


@unittest.skipUnless(os.environ.get('XENOVA_TEST_PAIRED_GATEWAY') == '1',
    'NOT_SELECTED: explicit coherent Gateway/common requires XENOVA_TEST_PAIRED_GATEWAY=1')
class PairedMeasuredSteeringCodecTests(unittest.TestCase):
    """전체 B08 공통/codec 선택 전용이며 HOLDREADY FSM의 실제 수락을 시험하지 않는다."""
    def test_actual_codec_and_mapped_gateway_keep_virtual_measured_value_and_positive_error(self):
        feedback=importlib.import_module('xenova_gateway.feedback3')
        framing=importlib.import_module('xenova_gateway.feedback')
        odometry=importlib.import_module('xenova_gateway.odometry')
        config=odometry.OdometryConfig(vehicle='virtual-bench',calibration='a'*64,metres_per_tick=.001,
            track_width_m=.18,wheelbase_m=.2,maximum_speed_mps=2.,maximum_clock_error_us=1000,
            maximum_sample_age_us=200_000,sample_period_ms=20,encoder_bits=32,position_error_m=.001,
            heading_error_rad=.01,distance_error_ratio=.01,heading_error_per_m=.01)
        odom=odometry.VehicleOdometry(config,str(UUID(int=7)));odom.device_session(DEVICE);owner=sensor()
        result=None
        for seq,stamp,ticks,angle in ((1,1_000_000,0,0.),(2,1_100_000,100,.05)):
            original=WheelSample(seq,stamp,ticks,ticks,0,500)
            measured=owner.sample(angle,source_sample=seq,source_us=stamp)
            combined=owner.combine(original,measured)
            wire=combined.sensor_wire(feedback,pi_session=PI,device_session=DEVICE,clock_epoch=EPOCH,clock_sequence=1)
            kind,payload=framing.unpack(wire);self.assertEqual(kind,feedback.SENSOR)
            pi,device,sample,source,left,right,quality,steer,error,_,_=feedback.SENSOR3.unpack(payload)
            result=odom.observe_mapped(str(UUID(bytes=pi)),str(UUID(bytes=device)),source,sample,left,right,
                quality,steer,error,stamp+990,stamp+1010)
        self.assertEqual(result['measured_steering_rad'],.05)
        self.assertNotIn('estimated_steering_rad',result)
        self.assertGreater(result['steering_error_rad'],0)
        self.assertEqual(combined.measurement.source_kind,'SIMULATED_MEASURED_STEERING')
        self.assertFalse(combined.physical_qualified)


if __name__=='__main__':unittest.main()
