"""원 RGB 추출·오차 분해·점선과 가림 역할의 진단 회귀다.

독립 pinhole 도형은 영상 생성용 oracle이며 core pose/GT 입력은 없다. 실측 보정·
원 NN 실행·운영 주행/시간 수락으로 확대하지 않는다. native 실행은 Nano 담당이다.
"""

from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from xenova_vehicle.perception.calibration import CalibrationProfile, CameraModel, Plane
from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.perception.road import RoadProfile, infer_road
from xenova_vehicle.perception.tape import TapeGraph, TapeStrip
from xenova_vehicle.simulation.road_probe import load_road_sample, probe_road, road_error_diagnostics, road_probe_cases
from xenova_vehicle.simulation.scene import demo_scene


def graph_strip(number, y, a=.2, b=1.4, *, error=.001, start='observed_paint_end', end='observed_paint_end'):
    return TapeStrip(number, ((a,y),(b,y)), (.02,.03), error, start, end,
        point_error_xy=((.001,.001),)*2, segment_error_xy=((.001,.001),))


def original_nv12():
    import cv2
    import numpy as np
    # 독립 top-down oracle: u=320-150y, v=180-150(x-.8). 오차상한을 운영 profile에서 낮추지 않는다.
    camera = CameraModel(640,360,((150.,0.,320.),(0.,150.,180.),(0.,0.,1.)),
        ((0.,-1.,0.),(-1.,0.,0.),(0.,0.,-1.)),(.8,0.,1.),pixel_error=0.)
    right = CameraModel(640,480,((150.,0.,320.),(0.,150.,240.),(0.,0.,1.)),
        camera.rotation,(.8,-.0375,1.),pixel_error=0.)
    calibration = CalibrationProfile(camera,right,Plane(),((-2.,-3.),(6.,-3.),(6.,3.),(-2.,3.)),kind='SYNTHETIC')
    image = np.full((360,640,3),30,np.uint8)
    for y in (-.3,0.,.3):
        cv2.line(image,(round(320-150*y),255),(round(320-150*y),105),(245,245,245),5)
    planar = cv2.cvtColor(image,cv2.COLOR_BGR2YUV_I420).reshape(-1)
    area=640*360
    uv=np.empty(area//2,np.uint8);uv[0::2]=planar[area:area+area//4];uv[1::2]=planar[area+area//4:]
    raw=planar[:area].tobytes()+uv.tobytes()
    stamp=FrameStamp('road-probe-fixture','rgb_video',1,'fixture-original-clock',(3_000_000,3_000_010),3_010_000)
    return PerceptionInput('SYNTHETIC',calibration.sha256,ImageFrame(stamp,640,360,'NV12',raw,0)),calibration


class SimRoadProbeTests(unittest.TestCase):
    def test_real_nv12_extraction_keeps_original_clock_and_returns_no_authority(self):
        observation,calibration=original_nv12()
        result=probe_road(observation,calibration,clock=iter(range(4_000_000,4_000_100)).__next__,
            geometry_expires_us=3_500_000)
        self.assertEqual(result['tape']['status'],'VALID')
        self.assertEqual(result['road']['status'],'VALID')
        self.assertEqual(len(result['tape']['strips']),3)
        self.assertEqual(result['original_rgb']['stamp']['captured_us'],[3_000_000,3_000_010])
        self.assertEqual(result['original_rgb']['payload_sha256'],observation.rgb.sha256)
        self.assertEqual(result['original_geometry_expires_us'],3_500_000)
        self.assertFalse(result['nn_executed']);self.assertFalse(result['nn_requirement_changed'])
        self.assertFalse(result['metric_ready']);self.assertFalse(result['world_valid']);self.assertFalse(result['free_positive'])
        self.assertEqual(result['odometry_sequence_restore'],'NOT_IMPLEMENTED_IN_THIS_PROBE')

    def test_geometry_budget_failure_exposes_exact_terms_without_changing_original_result(self):
        graph=TapeGraph(tuple(graph_strip(i,y,error=.12) for i,y in enumerate((.3,0.,-.3))),
            source_key=('original','rgb','clock',4))
        profile=RoadProfile();result=infer_road(graph,profile)
        self.assertEqual(result.status,'UNKNOWN');self.assertIn('GEOMETRY_ERROR_BUDGET',result.reasons)
        before=result.descriptor()
        diagnostic=road_error_diagnostics(graph,profile,result)
        row=diagnostic['hypotheses'][0]
        self.assertEqual(row['error_terms_m']['chain_tube_max_m'],.12)
        self.assertEqual(row['error_terms_m']['station_error_m'],profile.station_error_m)
        self.assertEqual(row['error_terms_m']['missing_outer_prior_m'],0.)
        self.assertAlmostEqual(row['reconstructed_error_m'],result.hypotheses[0].error_m,places=12)
        self.assertTrue(row['exceeds_geometry_budget'])
        self.assertEqual(row['station_count'],24)
        self.assertEqual(diagnostic['chains'][0]['point_error_xy'],graph.strips[0].point_error_xy)
        self.assertEqual(diagnostic['chains'][0]['segment_error_xy'],graph.strips[0].segment_error_xy)
        self.assertEqual(result.descriptor(),before)

    def test_occluded_tip_does_not_gain_observed_dash_connection(self):
        outers=(graph_strip(0,.3),graph_strip(1,-.3))
        paint=(graph_strip(2,0.,.2,.5),graph_strip(3,0.,.75,1.4))
        profile=RoadProfile()
        ordinary=TapeGraph(outers+paint)
        linked=road_error_diagnostics(ordinary,profile,infer_road(ordinary,profile))
        self.assertEqual(linked['chain_work']['dashed_merges'],1)
        self.assertTrue(any(chain['gaps_s'] for chain in linked['chains']))
        blocked=TapeGraph(outers+(replace(paint[0],end_kind='occluded'),replace(paint[1],start_kind='occluded')))
        censored=road_error_diagnostics(blocked,profile,infer_road(blocked,profile))
        self.assertEqual(censored['chain_work']['dashed_merges'],0)
        self.assertTrue(all(not chain['gaps_s'] for chain in censored['chains']))

    def test_original_packed_loader_keeps_time_and_detects_payload_corruption(self):
        observation,calibration=original_nv12()
        with tempfile.TemporaryDirectory(prefix='road-probe-input-') as temporary:
            root=Path(temporary);case=root/'P01';case.mkdir()
            raw=observation.rgb.payload
            metadata=dict(kind=observation.kind,calibration_id=calibration.sha256,depth=None,depth_fault='NONE',
                rgb=dict(stamp=asdict(observation.rgb.stamp),width=640,height=360,encoding='NV12',
                    exposure_us=0,bytes=len(raw),sha256=sha256(raw).hexdigest()))
            (case/'input.json').write_text(json.dumps(metadata),encoding='utf-8')
            (case/'rgb.nv12').write_bytes(raw)
            (root/'calibration.json').write_text(json.dumps(calibration.to_dict()),encoding='utf-8')
            receipt=dict(rgb_sha256=observation.rgb.sha256,calibration_id=calibration.sha256,
                captured_j_us=['3000000','3000010'],expires_j_us='3500000')
            (case/'capture-receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
            loaded,actual,origin=load_road_sample(case)
            self.assertEqual(loaded,observation);self.assertEqual(actual.sha256,calibration.sha256)
            self.assertEqual(origin['original_geometry_expires_us'],3_500_000)
            self.assertIsNone(loaded.nn);self.assertFalse(hasattr(loaded,'pose'))
            (case/'rgb.nv12').write_bytes(bytes([raw[0]^1])+raw[1:])
            with self.assertRaisesRegex(ValueError,'hash'):load_road_sample(case)

    def test_foreign_depth_or_calibration_cannot_supply_occlusion(self):
        observation,calibration=original_nv12()
        stamp=replace(observation.rgb.stamp,stream_id='depth_right',sequence=2)
        depth=ImageFrame(stamp,640,480,'RAW16_LE',bytes(640*480*2),0)
        with self.assertRaisesRegex(ValueError,'unpaired'):
            probe_road(replace(observation,depth=depth),calibration)
        with self.assertRaisesRegex(ValueError,'calibration'):
            probe_road(replace(observation,calibration_id='f'*64),calibration)

    def test_renderer_only_cases_keep_explicit_dash_lengths_and_box_gt_out_of_input(self):
        scene=demo_scene();before=scene.canonical_bytes
        cases=road_probe_cases(scene)
        self.assertEqual(len(cases),6)
        positive=next(c for c in cases if c.name=='P01-dash-025-025')
        lines=positive.scene.fields['track']['layout']['paint_lines']
        import math
        self.assertAlmostEqual(math.dist(*lines[2]),.25)
        self.assertAlmostEqual(math.dist(lines[2][1],lines[3][0]),.25)
        long_gap=next(c for c in cases if c.name=='P01-long-gap')
        gap_lines=long_gap.scene.fields['track']['layout']['paint_lines']
        self.assertAlmostEqual(math.dist(gap_lines[2][1],gap_lines[3][0]),.60)
        blocked=next(c for c in cases if c.name=='P03-divider-box-occlusion')
        self.assertEqual(len(blocked.scene.fields['track']['layout']['obstacle_seeds']),1)
        self.assertEqual(scene.canonical_bytes,before)
